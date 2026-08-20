# Design — ESP File Operations

## Overview

The ESP File Operations feature delivers a single facade, `EspFileProvider`,
that exposes the `SshAgent`-shaped file API (`file_push`, `file_pull`,
`file_exists`, `directory_exists`, `directory`, `directory_tree`) across
three transports:

* **UEFI Shell** — Uses the Shell built-in commands `cp`, `ls`, `map`, and
  reads/writes files through the active Simple File System per §13.3.
* **SSH-to-host** — Addresses the OS-mounted ESP (typically `/boot/efi`)
  through an existing `SshAgent` session.
* **Redfish** — Uses VirtualMedia uploads through the BMC for firmware
  staging and read-back.

The facade owns path normalization (UEFI `\`-separated convention vs. POSIX),
chunked streaming, retry semantics driven by `UefiAspects`, and the cross-
transport error taxonomy.  Callers never branch on transport kind.

## Architecture

```
ccai.protocols.uefi.esp/
    __init__.py                 # re-exports EspFileProvider, EspDirEntry,
                                # EspDirTree, EspFileAttributes
    provider.py                 # EspFileProvider facade (dispatches to backends)
    paths.py                    # UEFI <-> POSIX path normalization helpers
    models.py                   # EspDirEntry, EspDirTree, EspFileAttributes
    backends/
        __init__.py
        base.py                 # AbstractEspFileBackend
        shell.py                # UEFI Shell backend
        sshhost.py              # SSH-to-host backend
        redfish.py              # Redfish VirtualMedia backend
```

Dependency direction (lower imports upward is forbidden):

```
backends/shell.py ──▶ provider.py ──▶ paths.py
backends/sshhost.py ──▶                models.py
backends/redfish.py ──▶                1-aspects
                                       1-exceptions
                                       1-logging
                                       3-client (for the active UefiClient)
                                       4-agent  (for an SshAgent instance — composition only)
```

## Components and Interfaces

### `EspFileProvider` (provider.py)

```python
class EspFileProvider:
    """
        Transport-agnostic file-operations facade over the EFI System Partition.

        :param client: The already-constructed UefiClient that owns the active
                       transport.
        :param default_aspects: The instance default UefiCmdAspects used when a
                                caller passes aspects=None.
    """

    def __init__(
        self,
        client: "UefiClient",
        default_aspects: Optional["UefiCmdAspects"] = None,
    ) -> None:
        self._client = client
        self._default_aspects = default_aspects
        self._backend = self._resolve_backend(client=client)
        return

    def file_push(
        self,
        localpath: str,
        remotepath: str,
        aspects: Optional["UefiCmdAspects"] = None,
    ) -> None:
        """
            Push a local file to the remote ESP.

            :param localpath: Absolute path to the source file on the test harness.
            :param remotepath: Destination path on the ESP (UEFI or POSIX form).
            :param aspects: Optional per-call aspects override.

            :raises UefiError: When the underlying backend reports a file-system
                               or path error.
            :raises ConnectionError: When the transport cannot be reached and
                                     must_connect is True.
        """
        resolved = resolve_aspects(passed=aspects, instance_default=self._default_aspects)
        normalized = self._backend.normalize(remotepath=remotepath)
        self._run_with_pattern(
            aspects=resolved,
            operation=lambda: self._backend.file_push(
                localpath=localpath,
                remotepath=normalized,
                aspects=resolved,
            ),
        )
        return

    def file_pull(
        self,
        remotepath: str,
        localpath: str,
        aspects: Optional["UefiCmdAspects"] = None,
    ) -> None: ...

    def file_exists(
        self,
        remotepath: str,
        aspects: Optional["UefiCmdAspects"] = None,
    ) -> bool: ...

    def directory_exists(
        self,
        remotepath: str,
        aspects: Optional["UefiCmdAspects"] = None,
    ) -> bool: ...

    def directory(
        self,
        remotepath: str,
        aspects: Optional["UefiCmdAspects"] = None,
    ) -> list["EspDirEntry"]: ...

    def directory_tree(
        self,
        remotepath: str,
        aspects: Optional["UefiCmdAspects"] = None,
    ) -> "EspDirTree": ...
```

### `AbstractEspFileBackend` (backends/base.py)

```python
class AbstractEspFileBackend:
    """
        Abstract contract each transport-specific backend implements.

        The backend is responsible for path normalization native to its
        transport, raw I/O, and translation of transport-specific errors to
        UefiError.
    """

    def normalize(self, remotepath: str) -> str:
        raise AbstractMethodError()

    def file_push(
        self,
        localpath: str,
        remotepath: str,
        aspects: "UefiCmdAspects",
    ) -> None:
        raise AbstractMethodError()

    def file_pull(
        self,
        remotepath: str,
        localpath: str,
        aspects: "UefiCmdAspects",
    ) -> None:
        raise AbstractMethodError()

    def file_exists(
        self,
        remotepath: str,
        aspects: "UefiCmdAspects",
    ) -> bool:
        raise AbstractMethodError()

    def directory_exists(
        self,
        remotepath: str,
        aspects: "UefiCmdAspects",
    ) -> bool:
        raise AbstractMethodError()

    def directory(
        self,
        remotepath: str,
        aspects: "UefiCmdAspects",
    ) -> list["EspDirEntry"]:
        raise AbstractMethodError()

    def directory_tree(
        self,
        remotepath: str,
        aspects: "UefiCmdAspects",
    ) -> "EspDirTree":
        raise AbstractMethodError()
```

### `ShellEspBackend` (backends/shell.py)

Issues `cp`, `ls`, `mkdir`, and `rm` via a `UefiShellClient`.  Large-file
pushes are staged into a temporary Redfish-uploaded buffer only when the
serial link is too slow (`aspects.chunk_size < 4096` is rejected because
XMODEM-style serial transfer is out of scope for v1).  Read-back after
write uses `ls -sfo` parsing to validate the byte length.

### `SshHostEspBackend` (backends/sshhost.py)

Composes an `SshAgent` instance.  Probes the ESP mount point at construction
time by reading `/proc/mounts` and picking the first `vfat` mount whose
source device has a GPT type GUID of `C12A7328-F81F-11D2-BA4B-00A0C93EC93B`
(§5.3.3).  Reuses `SshAgent.file_push` / `SshAgent.file_pull` /
`SshAgent.file_exists` / `SshAgent.directory` / `SshAgent.directory_tree` by
composition — *not* inheritance.

### `RedfishEspBackend` (backends/redfish.py)

Delivers files via VirtualMedia `InsertMedia` multipart POST and retrieves
them through the `TransferProtocolType="HTTP"` URI returned in the
VirtualMedia resource.  Falls back to the Redfish UpdateService when
VirtualMedia is unavailable.  Polls `TaskMonitor` URIs for `202 Accepted`
responses.

### `EspDirEntry` / `EspDirTree` / `EspFileAttributes` (models.py)

```python
@dataclass(frozen=True)
class EspFileAttributes:
    read_only: bool
    hidden: bool
    system: bool
    archive: bool
    directory: bool


@dataclass(frozen=True)
class EspDirEntry:
    name: str
    is_dir: bool
    size: int
    mtime: datetime
    attributes: EspFileAttributes


@dataclass(frozen=True)
class EspDirTree:
    path: str
    entries: tuple[EspDirEntry, ...]
    subtrees: tuple["EspDirTree", ...]
```

### Path Helpers (paths.py)

```python
def uefi_to_posix(uefipath: str, esp_mount: str) -> str: ...
def posix_to_uefi(posixpath: str, esp_mount: str, map_alias: str = "FS0:") -> str: ...
def normalize_uefi_path(path: str, default_alias: str = "FS0:") -> str: ...
def split_uefi_path(path: str) -> tuple[str, list[str]]: ...  # (alias, segments)
```

Each helper is a pure function with zero I/O.

## Data Models

### `EspFileAttributes` bit layout (UEFI §13.4)

| Bit | Attribute         | Meaning                                  |
|-----|-------------------|------------------------------------------|
| 0   | EFI_FILE_READ_ONLY| File cannot be opened for write.         |
| 1   | EFI_FILE_HIDDEN   | File is hidden.                          |
| 2   | EFI_FILE_SYSTEM   | File is a system file.                   |
| 3   | (reserved)        | Must be zero.                            |
| 4   | EFI_FILE_DIRECTORY| File is a directory.                     |
| 5   | EFI_FILE_ARCHIVE  | Archive bit.                             |

### Multipart upload body (Redfish)

| Part index | Content-Type              | Payload                                   |
|------------|---------------------------|-------------------------------------------|
| 0          | application/json          | `{"Targets":[],"@Redfish.OperationApplyTime":"Immediate"}` |
| 1          | application/octet-stream  | Raw file bytes                            |

## Measured Interop Contract

`EspFileProvider` consumes the following `UefiCmdAspects` fields from every
caller-facing method:

* `action_pattern` — Supported: `SINGLE_CALL`, `SINGLE_CONNECTED_CALL`,
  `DO_UNTIL_SUCCESS`.  `DO_WHILE_SUCCESS` and
  `DO_UNTIL_CONNECTION_FAILURE` are **not** supported and raise
  `SemanticError`.
* `completion_timeout` — Upper bound on retry loop wall-clock time and on
  Redfish TaskMonitor polling.
* `completion_interval` — Retry spacing and TaskMonitor poll spacing.
* `connection_timeout` / `connection_interval` — Used when the underlying
  transport needs to (re)connect before I/O.
* `inactivity_timeout` — Enforced on streaming reads/writes that make no
  progress.
* `allowed_connection_exceptions` — Exceptions retried under
  `DO_UNTIL_SUCCESS`.
* `allowed_error_codes` — UEFI Shell exit codes treated as success (rarely
  used but honored).
* `must_connect` — When `True`, surfaces `ConnectionError` rather than
  retrying.
* `chunk_size` — Bytes per streamed I/O chunk (backend-specific: serialized
  over the shell, HTTP body chunk for Redfish, SFTP block for SSH-to-host).
  Must be in `[512, 16777216]`; default `65536`.
* `logger` — Logger used by this feature at channels `uefi.esp.shell`,
  `uefi.esp.sshhost`, `uefi.esp.redfish`.
* `logging_pattern` — Controls whether per-attempt success/failure lines are
  emitted.

## Error Handling

| Condition                                          | Exception            | Included in `details`                         |
|----------------------------------------------------|----------------------|-----------------------------------------------|
| Path outside ESP mount (SSH-to-host)               | `UefiError`          | input path, resolved path                     |
| Path with `..` after expansion                     | `UefiError`          | input path, normalized form                   |
| ESP mount not found (SSH-to-host)                  | `UefiTransportError` | probed candidates, `mount` output             |
| Shell reports "Access Denied"                      | `UefiError`          | firmware text, attempted path                 |
| POSIX `EACCES`                                     | `UefiError`          | path, `errno`                                 |
| Redfish `403 Forbidden`                            | `UefiError`          | Redfish `Messages[]`                          |
| Redfish task failed                                | `UefiError`          | TaskState, Messages[]                         |
| Byte-length mismatch after push                    | `UefiError`          | expected, observed                            |
| `chunk_size` out of range                          | `ConfigurationError` | offending value                               |
| Unsupported action pattern                         | `SemanticError`      | requested pattern                             |
| `aspects=None` and no instance default             | `ConfigurationError` | method name                                   |

## Logging

Per-call log lines at channel `uefi.esp.*` respect `logging_pattern`:

* `ALL_RESULTS` — one line per attempt with `{operation, path, bytes, duration_ms, status}`.
* `FAILURE_ONLY` — only failing attempts logged.
* `SUCCESS_ONLY` — only succeeding attempts logged.

Chunked pushes emit a single summary line at completion; per-chunk logging
is suppressed to avoid flooding.

## Security Considerations

* Path traversal — `..` expansion is rejected *after* normalization.
  Attacker-controlled remote path inputs cannot escape the ESP mount.
* Redfish credential handling — The Redfish backend never writes secrets to
  disk; session tokens live only in memory on the `UefiRedfishCredential`
  object and are never logged.
* TLS verification — Redfish backend defaults to
  `verify=True`.  Lab overrides must be explicit on the credential.
* SSH-to-host privileges — The OS mount of the ESP is read-only for
  unprivileged users on many distributions.  The backend raises a clear
  `UefiError` rather than attempting silent `sudo` escalation.
* Firmware file-level security — §13.5 attributes (read-only, hidden,
  system) are surfaced to the caller rather than masked.

## Testing Strategy

### Unit tests

* `test_paths.py` — Round-trip `uefi_to_posix` / `posix_to_uefi` for a
  fixtures list including spaces, mixed case, trailing separators, and
  `FS#:` alias variations.
* `test_provider_dispatch.py` — With mocked backends, verify the facade
  routes to the correct backend per `client.transport_kind`.
* `test_action_patterns.py` — Assert `SINGLE_CALL` runs once,
  `SINGLE_CONNECTED_CALL` verifies connectivity first, `DO_UNTIL_SUCCESS`
  retries on allowed exceptions, and unsupported patterns raise
  `SemanticError`.
* `test_aspects_none.py` — Passing `aspects=None` substitutes the instance
  default.
* `test_attributes.py` — `EspFileAttributes` bit decoding matches §13.4.

### Backend tests

* `test_shell_backend.py` — Mocked `UefiShellClient.execute` returning
  captured `cp` / `ls` transcripts drives every public method.
* `test_sshhost_backend.py` — Mocked `SshAgent` verifies POSIX translation,
  mount probing, and `EACCES` propagation.
* `test_redfish_backend.py` — `responses`-library fake BMC verifies
  multipart assembly, TaskMonitor polling, 202/401 paths, and 403 error
  translation.

### Integration tests

* `test_large_file_stream.py` — Push a 64 MiB random file through each
  backend and verify SHA-256 round-trip.  Parametrized across
  `chunk_size = [4096, 65536, 1048576]`.

### Coverage target

95% line, 90% branch for the package.

## Dependencies on Other Features

* **1-aspects** — `UefiCmdAspects`, `ActionPattern`, `LoggingPattern`,
  `resolve_aspects`.
* **1-constants-and-types** — UEFI GPT type GUID, volume-alias defaults.
* **1-exceptions** — `UefiError`, `UefiTransportError`,
  `ConfigurationError`.
* **1-helpers** — Nothing directly, but chunked-hash helpers may be used
  for streaming verification.
* **1-logging** — `uefi.esp.*` channels.
* **3-client** — The `UefiClient` passed to the facade constructor exposes
  `transport_kind` and `execute`.
* **4-agent** — The SSH-to-host backend reuses `SshAgent.file_push` etc. by
  composition.
* **2-transport-redfish** — Provides the Redfish session used by the
  Redfish backend.

This feature is consumed by **6-esp-content-sync** and
**5-firmware-capsules** (for mass-storage capsule delivery per §8.5.5).
