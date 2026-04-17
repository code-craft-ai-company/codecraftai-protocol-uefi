# Design — SSH-to-Host Transport

## Overview

`UefiSshHostTransport` is a layer-2 transport that reaches a UEFI target by
SSHing into a *booted* operating system on the target and running OS-side
UEFI tooling (`efibootmgr`, `efivar`, `fwupdmgr`, direct reads/writes of
`/sys/firmware/efi/efivars/`).  It implements `IUefiTransport` (see
`1-interfaces`) and honors the measured-interop contract defined by
`UefiAspects` (see `1-aspects`).

The transport is deliberately a thin veneer over an existing
`automojo-protocol-ssh` `SshAgent`, acquired via *composition*.  The caller
constructs the `SshAgent` themselves with whatever credential, jump-host,
key-file, and retry behavior they already need, and hands that agent to this
transport.  This transport does not own the SSH lifecycle — it only adds:

* UEFI capability probing at `connect()` time, yielding a `.capabilities`
  dict.
* A `require(capability_name)` helper that raises a distribution-aware
  `UefiTransportError` when the needed tool is missing.
* A `run_cmd(...)` wrapper that adds an `as_root` parameter and a
  non-interactive sudo policy.
* `IUefiTransport` conformance so layer 3 can treat it interchangeably with
  `UefiSerialTransport` and `UefiRedfishTransport`.

No decorator-based retry is used (per the coding standards in
`/Users/myron/repos/CLAUDE.md`); all retry is driven by the caller-supplied
`aspects`.

## Architecture

```
automojo.protocols.uefi.transport.sshhost/
    __init__.py            # re-exports UefiSshHostTransport, HostCmdResult
    sshhosttransport.py    # UefiSshHostTransport (IUefiTransport)
    capabilityprobe.py     # connect-time probe logic
    sudopolicy.py          # as_root prefixing + prompt detection
    distrohint.py          # /etc/os-release → install-command hint
    efivarfsio.py          # read_efivar / write_efivar helpers
    result.py              # HostCmdResult dataclass
```

Dependency direction (nothing imports upward):

```
sshhosttransport.py ──▶ capabilityprobe.py
                    ──▶ sudopolicy.py
                    ──▶ distrohint.py     (capabilityprobe also uses)
                    ──▶ efivarfsio.py     ──▶ sudopolicy.py
                    ──▶ result.py
```

## Components and Interfaces

### `UefiSshHostTransport` (sshhosttransport.py)

Public constructor:

```python
class UefiSshHostTransport(IUefiTransport):
    def __init__(
        self,
        ssh_agent: "SshAgent",
        aspects: Optional[UefiAspects] = None,
    ) -> None:
        super().__init__()
        if not isinstance(ssh_agent, SshAgent):
            err_msg = f"ssh_agent must be an SshAgent; got {type(ssh_agent).__name__}."
            raise ConfigurationError(err_msg)
        self._ssh_agent = ssh_agent
        self._aspects = aspects if aspects is not None else DEFAULT_ASPECTS
        self._capabilities: dict[str, bool] = {}
        self._distro_hint: Optional[str] = None
        self._effective_user: Optional[str] = None
        self._connected = False
        self._reconnect_count = 0
        self._run_cmd_count = 0
        return
```

Public methods (satisfying `IUefiTransport`):

```python
    def connect(self, aspects: Optional[UefiAspects] = None) -> None: ...
    def disconnect(self) -> None: ...
    def run_cmd(
        self,
        command: str,
        aspects: Optional[UefiCmdAspects] = None,
        as_root: bool = False,
    ) -> HostCmdResult: ...
    def file_pull(self, remote_path: str, local_path: str, aspects: Optional[UefiAspects] = None) -> None: ...
    def file_push(self, local_path: str, remote_path: str, aspects: Optional[UefiAspects] = None) -> None: ...
    def read_efivar(self, name: str, vendor_guid: str, aspects: Optional[UefiAspects] = None) -> bytes: ...
    def write_efivar(self, name: str, vendor_guid: str, payload: bytes, aspects: Optional[UefiAspects] = None) -> None: ...
    def require(self, capability_name: str) -> None: ...
    @property
    def capabilities(self) -> Mapping[str, bool]: ...
    @property
    def is_connected(self) -> bool: ...
    @property
    def transport_kind(self) -> str:
        rtnval = "ssh-host"
        return rtnval
```

Byte-pipe primitives (`send`, `recv`, `read_until`) raise `SemanticError`
because they are not meaningful for a per-call exec transport.

### `capabilityprobe.py`

```python
PROBE_COMMANDS: dict[str, str] = {
    "has_efibootmgr": "command -v efibootmgr",
    "has_efivar": "command -v efivar",
    "has_fwupd": "command -v fwupdmgr",
    "has_sysfs_efi": "test -d /sys/firmware/efi",
    "has_efivarfs_ro": "test -r /sys/firmware/efi/efivars/.",
    "has_efivarfs_rw": "test -w /sys/firmware/efi/efivars/.",
}


def probe_capabilities(
    ssh_agent: "SshAgent",
    aspects: UefiCmdAspects,
    logger: logging.Logger,
) -> dict[str, bool]:
    caps: dict[str, bool] = {}
    for flag, probe in PROBE_COMMANDS.items():
        try:
            result = ssh_agent.run_cmd(probe, aspects=aspects)
            caps[flag] = (result.status == 0)
        except Exception as xcpt:
            logger.warning("probe '%s' failed: %s", flag, xcpt)
            caps[flag] = False
    return caps
```

The probe uses `UefiCmdAspects(action_pattern=SINGLE_CALL,
completion_timeout=10, expected_status=(0, 1))` so that `command -v`
returning non-zero (binary missing) is *not* a retryable failure.

### `sudopolicy.py`

```python
SUDO_PROMPT_SENTINELS = (
    "a password is required",
    "a terminal is required",
    "sudo: a password is required",
)

def apply_sudo(command: str, as_root: bool, effective_user: Optional[str]) -> str:
    if not as_root:
        return command
    if effective_user == "root":
        return command
    rtnval = f"sudo -n -- {command}"
    return rtnval


def detect_sudo_prompt(stderr: str) -> bool:
    lowered = stderr.lower()
    for sentinel in SUDO_PROMPT_SENTINELS:
        if sentinel in lowered:
            return True
    return False
```

When a prompt is detected, the transport raises `ConfigurationError` with
details including the host, the command (credential-shaped tokens
redacted), and a sudoers remediation hint.

### `distrohint.py`

```python
DISTRO_INSTALL_TEMPLATES: dict[str, str] = {
    "ubuntu": "sudo apt-get install {pkg}",
    "debian": "sudo apt-get install {pkg}",
    "fedora": "sudo dnf install {pkg}",
    "rhel":   "sudo dnf install {pkg}",
    "centos": "sudo dnf install {pkg}",
    "arch":   "sudo pacman -S {pkg}",
    "opensuse-leap":   "sudo zypper install {pkg}",
    "opensuse-tumbleweed": "sudo zypper install {pkg}",
}

CAPABILITY_PACKAGE: dict[str, str] = {
    "has_efibootmgr": "efibootmgr",
    "has_efivar": "efivar",
    "has_fwupd": "fwupd",
}

def fetch_distro_id(ssh_agent: "SshAgent") -> Optional[str]: ...
def install_hint(distro_id: Optional[str], capability: str) -> str: ...
```

### `efivarfsio.py`

```python
def read_efivar(
    transport: "UefiSshHostTransport",
    name: str,
    vendor_guid: str,
    aspects: UefiCmdAspects,
) -> bytes:
    path = f"/sys/firmware/efi/efivars/{name}-{vendor_guid}"
    cmd = f"cat -- {shlex.quote(path)} | base64"
    result = transport.run_cmd(cmd, aspects=aspects, as_root=False)
    if result.status != 0:
        err_msg_lines = [...]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiVariableError(err_msg, name=name, vendor_guid=vendor_guid)
    rtnval = base64.b64decode(result.stdout)
    return rtnval


def write_efivar(
    transport: "UefiSshHostTransport",
    name: str,
    vendor_guid: str,
    payload: bytes,
    aspects: UefiCmdAspects,
) -> None:
    transport.require("has_efivarfs_rw")
    path = f"/sys/firmware/efi/efivars/{name}-{vendor_guid}"
    # chattr -i to clear the immutable bit if set; then write via tee
    clear_immutable = f"chattr -i -- {shlex.quote(path)} 2>/dev/null || true"
    transport.run_cmd(clear_immutable, aspects=aspects, as_root=True)

    encoded = base64.b64encode(payload).decode("ascii")
    write_cmd = f"printf %s {shlex.quote(encoded)} | base64 -d | tee -- {shlex.quote(path)} >/dev/null"
    result = transport.run_cmd(write_cmd, aspects=aspects, as_root=True)
    if result.status != 0:
        err_msg_lines = [...]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiVariableError(err_msg, name=name, vendor_guid=vendor_guid)
    return
```

### `HostCmdResult` (result.py)

```python
@dataclass(frozen=True)
class HostCmdResult:
    command: str
    status: int
    stdout: str
    stderr: str
    duration_s: float
    was_root: bool
```

### Connection Loop

Mirrors `sshconnection.SshConnection.connect()` in shape.  Since this
transport does not open its own SSH session, `connect()` is a thin wrapper
around `ssh_agent.verify_connectivity()` plus capability probing:

```python
def connect(self, aspects: Optional[UefiAspects] = None) -> None:
    effective = resolve_aspects(aspects, self._aspects)
    if effective.action_pattern == ActionPattern.DO_WHILE_SUCCESS:
        err_msg = "DO_WHILE_SUCCESS is not meaningful for connect()."
        raise SemanticError(err_msg)

    if effective.action_pattern == ActionPattern.SINGLE_CONNECTED_CALL and self._connected:
        return

    deadline = time.monotonic() + effective.connection_timeout
    attempt_count = 0
    last_exc: Optional[BaseException] = None
    while True:
        attempt_count += 1
        try:
            self._ssh_agent.verify_connectivity()
            self._capabilities = probe_capabilities(self._ssh_agent, ..., self._logger)
            self._distro_hint = fetch_distro_id(self._ssh_agent)
            self._effective_user = self._ssh_agent.run_cmd("id -un").stdout.strip()
            self._connected = True
            return
        except Exception as xcpt:
            last_exc = xcpt
            if not isinstance(xcpt, tuple(effective.allowed_connection_exceptions)):
                raise UefiTransportError(...) from xcpt
            if effective.must_connect:
                raise ConnectionError(...) from xcpt
            if effective.action_pattern == ActionPattern.SINGLE_CALL:
                raise
            if time.monotonic() >= deadline:
                err_msg_lines = [...]
                err_msg = os.linesep.join(err_msg_lines)
                raise TimeoutError(err_msg) from last_exc
        time.sleep(effective.connection_interval)
```

## Data Models

### Capability Keys

| Key                 | Meaning                                               |
|---------------------|-------------------------------------------------------|
| `has_efibootmgr`    | `efibootmgr` binary in PATH                           |
| `has_efivar`        | `efivar` binary in PATH                               |
| `has_fwupd`         | `fwupdmgr` binary in PATH                             |
| `has_sysfs_efi`     | `/sys/firmware/efi/` directory exists                 |
| `has_efivarfs_ro`   | `/sys/firmware/efi/efivars/` readable by current user |
| `has_efivarfs_rw`   | `/sys/firmware/efi/efivars/` writable by current user |

### Distribution-to-Package-Manager Map

| `/etc/os-release` `ID`    | Install command template                 |
|---------------------------|------------------------------------------|
| `ubuntu`, `debian`        | `sudo apt-get install {pkg}`             |
| `fedora`, `rhel`, `centos`| `sudo dnf install {pkg}`                 |
| `arch`                    | `sudo pacman -S {pkg}`                   |
| `opensuse-leap`, `opensuse-tumbleweed` | `sudo zypper install {pkg}` |
| (unknown)                 | `install '{pkg}' using your distribution's package manager` |

## Measured Interop Contract

`UefiSshHostTransport` consumes the following fields from `UefiAspects` /
`UefiCmdAspects`:

| Field                             | Consumed By                                       |
|-----------------------------------|---------------------------------------------------|
| `action_pattern`                  | `connect()` retry-loop shape                      |
| `connection_timeout`              | `connect()` overall deadline                      |
| `connection_interval`             | `connect()` sleep between attempts                |
| `allowed_connection_exceptions`   | `connect()` soft-failure filter                   |
| `must_connect`                    | `connect()` hard-fail switch                      |
| `completion_timeout`              | per-probe `run_cmd` deadline                      |
| `completion_interval`             | forwarded to `SshAgent.run_cmd`                   |
| `inactivity_timeout`              | forwarded to `SshAgent.run_cmd`                   |
| `inactivity_interval`             | forwarded to `SshAgent.run_cmd`                   |
| `expected_status` (Cmd)           | forwarded to `SshAgent.run_cmd`                   |
| `prompt_pattern` (Cmd)            | unused — SSH exec is not prompt-oriented          |
| `transport_kind` (Cmd)            | validated against `"ssh-host"` if not None        |
| `logger`                          | all log emission                                  |
| `logging_pattern`                 | forwarded; acted on by `SshAgent.run_cmd`         |

Supported `ActionPattern` values on `connect()`:

* `SINGLE_CALL`, `SINGLE_CONNECTED_CALL`, `DO_UNTIL_SUCCESS`,
  `DO_UNTIL_CONNECTION_FAILURE`.
* `DO_WHILE_SUCCESS` — raises `SemanticError`.

`run_cmd` itself does not loop; it forwards all aspects-controlled retry to
the underlying `SshAgent.run_cmd`, which itself supports the full set of
action patterns via the SSH library.

## Error Handling

| Condition                                                   | Exception              | Included in `details`                         |
|-------------------------------------------------------------|------------------------|-----------------------------------------------|
| Non-`SshAgent` passed as `ssh_agent`                        | `ConfigurationError`   | passed type                                   |
| `ssh_agent.verify_connectivity()` fails                     | `UefiTransportError`   | host, underlying exception                    |
| `connection_timeout` elapsed                                | `TimeoutError`         | host, attempts, elapsed, last exc             |
| `must_connect=True` + connect failure                       | `ConnectionError`      | host, last exc                                |
| `require(capability)` when capability is `False`            | `UefiTransportError`   | capability, package, distro-aware install cmd |
| `require(capability)` with unknown capability name          | `SemanticError`        | capability, known keys                        |
| Sudo password prompt detected                               | `ConfigurationError`   | host, command (redacted), sudoers hint        |
| `read_efivar` on non-existent variable                      | `UefiVariableError`    | name, vendor_guid, stderr                     |
| `write_efivar` without `has_efivarfs_rw`                    | `UefiTransportError`   | capability, install hint                      |
| `send`/`recv`/`read_until` called on exec transport         | `SemanticError`        | method name                                   |

`SemanticError` escapes unchanged per the coding standards — never caught.

## Logging

All log lines route to channel `uefi.transport.sshhost`:

* `INFO` — connect begin, connect success, capability dict, disconnect.
* `WARNING` — soft-failure retries, capability-probe non-absence errors,
  `require()` refusals.
* `ERROR` — sudo password prompt detected.
* `DEBUG` — every `run_cmd` call with status and duration.

No log line ever embeds credential material (the `SshAgent` owns its own
credentials and handles its own redaction).

## Security Considerations

* The transport never opens its own paramiko session — every byte on the
  wire flows through the caller-supplied `SshAgent`, so the caller's key
  handling and jump-host policy apply unchanged.
* Sudo escalation is strictly non-interactive (`sudo -n`).  If sudoers
  requires a password, the transport refuses cleanly rather than blocking
  forever on an invisible prompt.
* `write_efivar` uses `chattr -i` to clear the immutable bit when present;
  this requires root, which is why `as_root=True` is mandatory there.
* Command strings passed to `run_cmd` are quoted with `shlex.quote` where
  the transport builds them (efivarfs paths, probe commands).  Callers
  passing raw commands are responsible for their own quoting.
* The capability-probe commands are deterministic constants — there is no
  user input that flows into `PROBE_COMMANDS`.

## Testing Strategy

### Unit tests (`test_sshhosttransport.py`)

* Construction rejects non-`SshAgent`.
* `connect()` populates `.capabilities` from mocked `SshAgent.run_cmd`
  return values.
* `require()` returns on `True`, raises with install hint on `False`,
  raises `SemanticError` on unknown key.
* `run_cmd(as_root=True, ...)` prefixes `sudo -n --`; skips prefix when
  effective user is `root`.
* Sudo-prompt detection raises `ConfigurationError` with host and command.
* `connect()` honors every `ActionPattern`.

### Unit tests (`test_capabilityprobe.py`)

* All six probes pass → all flags `True`.
* `efibootmgr` absent → `has_efibootmgr=False`, no raise.
* Probe raises unrelated exception → flag `False`, `WARNING` logged.

### Unit tests (`test_sudopolicy.py`)

* `apply_sudo` correctness for all four branches.
* `detect_sudo_prompt` true on each sentinel and false otherwise.

### Unit tests (`test_distrohint.py`)

* All mapped distro IDs render the right template.
* Unknown / missing ID falls back to generic message.

### Unit tests (`test_efivarfsio.py`)

* `read_efivar` round-trip using mocked `run_cmd` that returns base64 of a
  known payload.
* `write_efivar` requires `has_efivarfs_rw` and issues the expected
  `chattr -i` + `tee` pair.
* `read_efivar` on non-zero status raises `UefiVariableError` with
  `name`/`vendor_guid` populated.

### Integration tests

* Runnable against a disposable Linux VM when env var
  `AUTOMOJO_SSH_HOST_FIXTURE` is set; otherwise skipped.
* Round-trip: connect → probe → `efibootmgr -v` → parse → disconnect.
* Sudo-prompt detection against a sudoers-misconfigured fixture.

### Coverage target

95% line coverage for this feature.

## Dependencies on Other Features

* **1-aspects** — `UefiAspects`, `UefiCmdAspects`, `ActionPattern`,
  `resolve_aspects`, `DEFAULT_ASPECTS`.
* **1-exceptions** — `UefiTransportError`, `UefiVariableError`,
  `ConfigurationError` (shared), `SemanticError` (shared).
* **1-interfaces** — `IUefiTransport`.
* **1-logging** — `get_uefi_logger("uefi.transport.sshhost")`.
* **1-constants-and-types** — capability keys, distro templates.

External:

* `automojo-protocol-ssh` — `SshAgent`, `SshCredential`, `SshCmdAspects`.

## Transport Matrix

| Capability                         | SSH-to-Host                                        |
|------------------------------------|----------------------------------------------------|
| Pre-boot (no OS required)          | No (requires a booted OS with sshd)                |
| Post-boot (OS running)             | Yes                                                |
| UEFI variable read                 | Yes (efivarfs + `efivar`)                          |
| UEFI variable write                | Yes (efivarfs + `chattr -i` + `tee`, requires root)|
| Capsule delivery                   | Yes (`/dev/efi_capsule_loader` + `fwupdmgr`)       |
| Boot-option management             | Yes (`efibootmgr`)                                 |
| ESP file operations                | Yes (ordinary OS file API on the mounted ESP)      |
| System reset                       | Yes (`systemctl reboot`, `systemctl poweroff`)     |
| Firmware setup navigation          | No (pre-boot only, OS is already up)               |
| Required operator credentials      | SSH credential with optional sudo                  |
| Bandwidth                          | High (SSH over LAN)                                |
