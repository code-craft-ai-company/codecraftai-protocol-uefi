# Design — IPMI-SOL Transport

## Overview

`UefiIpmiSolTransport` is a layer-2 byte-level transport that reaches a
UEFI target's console over IPMI Serial-Over-LAN.  Once the SOL payload is
activated, the transport is functionally indistinguishable from
`UefiSerialTransport`: callers get the same `read_until`, the same
line-ending normalization, and the same `IUefiTransport` surface.  What
differs is the session lifecycle — SOL requires IPMI activation and
deactivation around the byte pipe, and must cope with the target's rule
that only one SOL session may be active at a time.

The transport is built on two interchangeable backends:

* **`pyghmi` backend** — used when the caller supplies a pre-built
  `pyghmi.ipmi.Session`.  No subprocess is spawned.
* **`ipmitool` subprocess backend** — used when the caller supplies only
  `UefiIpmiCredential`.  Spawns `ipmitool sol activate` and pumps its
  stdio.

Both backends expose an internal `_SolBackend` interface that looks like
the `_SerialBackend` interface from `2-transport-serial` — the transport
composes the same `read_until` loop on top.

All retry semantics are driven by the caller-supplied `UefiAspects` — no
decorator-based retry is used (per the coding standards in
`/Users/myron/repos/CLAUDE.md`).

## Architecture

```
automojo.protocols.uefi.transport.ipmisol/
    __init__.py              # re-exports UefiIpmiSolTransport
    ipmisoltransport.py      # UefiIpmiSolTransport (IUefiTransport)
    pyghmi_backend.py        # _SolBackend using pyghmi
    ipmitool_backend.py      # _SolBackend using subprocess
    solactivation.py         # activate / deactivate / probe-existing
    cmdredact.py             # redact -P/--password from logged argv
```

Dependency direction (nothing imports upward):

```
ipmisoltransport.py ──▶ solactivation.py
                    ──▶ pyghmi_backend.py
                    ──▶ ipmitool_backend.py  ──▶ cmdredact.py
                    ──▶ (reused) 2-transport-serial.readuntil
                    ──▶ (reused) 2-transport-serial.normalize
```

The transport imports `read_until` and the `normalize(mode)` factory from
`2-transport-serial`.  No byte-level code is reimplemented here.

## Components and Interfaces

### `UefiIpmiSolTransport` (ipmisoltransport.py)

Public constructor:

```python
class UefiIpmiSolTransport(IUefiTransport):
    def __init__(
        self,
        bmc_host: str,
        credential: Optional[UefiIpmiCredential] = None,
        ipmi_session: Optional["pyghmi.ipmi.Session"] = None,
        interface: str = "lanplus",
        evict_existing: bool = False,
        line_ending_normalization: str = "crlf_to_lf",
        aspects: Optional[UefiAspects] = None,
    ) -> None:
        super().__init__()
        if credential is not None and ipmi_session is not None:
            err_msg = "Pass exactly one of credential or ipmi_session, not both."
            raise ConfigurationError(err_msg)
        if credential is None and ipmi_session is None:
            err_msg = "Pass exactly one of credential or ipmi_session."
            raise ConfigurationError(err_msg)

        self._bmc_host = bmc_host
        self._credential = credential
        self._ipmi_session = ipmi_session
        self._interface = interface
        self._evict_existing = evict_existing
        self._line_ending_normalization = line_ending_normalization
        self._aspects = aspects if aspects is not None else DEFAULT_ASPECTS
        self._backend: Optional[_SolBackend] = None
        self._read_buffer = bytearray()
        self._connected = False
        self._reconnect_count = 0
        self._session_start_ts: Optional[float] = None
        self._bytes_in = 0
        self._bytes_out = 0
        return
```

Public methods (satisfying `IUefiTransport`):

```python
    def connect(self, aspects: Optional[UefiAspects] = None) -> None: ...
    def disconnect(self) -> None: ...
    def send(self, data: bytes) -> int: ...
    def recv(self, max_bytes: int, timeout: float) -> bytes: ...
    def read_until(
        self,
        pattern: Union[bytes, re.Pattern[bytes]],
        timeout: float,
        aspects: Optional[UefiAspects] = None,
    ) -> bytes: ...
    @property
    def is_connected(self) -> bool: ...
    @property
    def transport_kind(self) -> str:
        rtnval = "ipmi-sol"
        return rtnval
    def __enter__(self) -> "UefiIpmiSolTransport": ...
    def __exit__(self, exc_type, exc_val, exc_tb) -> None: ...
```

### `_SolBackend` (abstract)

Internal ABC, mirroring `_SerialBackend` from `2-transport-serial`.
Methods: `activate()`, `deactivate()`, `read(max_bytes, timeout) -> bytes`,
`write(data) -> int`, `is_open` property.  The transport never calls
`pyghmi` or `subprocess` directly — it only talks to a backend.

### `pyghmi_backend.py`

```python
class PyghmiSolBackend(_SolBackend):
    def __init__(self, session: "pyghmi.ipmi.Session") -> None: ...
    def activate(self) -> None: ...        # session.activate_sol(callback)
    def deactivate(self) -> None: ...      # session.deactivate_sol()
    def read(self, max_bytes: int, timeout: float) -> bytes: ...
    def write(self, data: bytes) -> int: ...
```

Buffers inbound bytes through a threadsafe queue fed by `pyghmi`'s
callback.

### `ipmitool_backend.py`

```python
class IpmitoolSolBackend(_SolBackend):
    def __init__(
        self,
        bmc_host: str,
        credential: UefiIpmiCredential,
        interface: str,
    ) -> None: ...
    def activate(self) -> None: ...         # popen `ipmitool sol activate`
    def deactivate(self) -> None: ...       # popen `ipmitool sol deactivate`; wait
    def read(self, max_bytes: int, timeout: float) -> bytes: ...
    def write(self, data: bytes) -> int: ...
```

Subprocess is spawned with argv assembled via:

```python
argv = [
    "ipmitool",
    "-I", self._interface,
    "-H", self._bmc_host,
    "-U", self._credential.username,
    "-P", self._credential.password,
    "sol", "activate",
]
```

Log emission of `argv` goes through `cmdredact.redact_argv(argv)` which
replaces the value after `-P` / `--password=` with `"***REDACTED***"`.

### `solactivation.py`

Higher-level helpers that work on whichever backend is active:

```python
@dataclass(frozen=True)
class ExistingSessionInfo:
    user: Optional[str]
    source_ip: Optional[str]
    raw_output: str


def probe_existing_session(
    backend_or_argv: Union[_SolBackend, list[str]],
    logger: logging.Logger,
) -> Optional[ExistingSessionInfo]: ...


def force_deactivate(
    backend_or_argv: Union[_SolBackend, list[str]],
    logger: logging.Logger,
) -> None: ...


DEACTIVATION_FAILED_SENTINEL = "Deactivation failed"
```

### Connection Loop

Mirrors the shape of `UefiSerialTransport.connect()`:

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
            self._backend = self._build_backend()
            existing = probe_existing_session(self._backend, self._logger)
            if existing is not None:
                if self._evict_existing:
                    force_deactivate(self._backend, self._logger)
                else:
                    err_msg_lines = [...]
                    err_msg = os.linesep.join(err_msg_lines)
                    raise UefiTransportError(err_msg, details=[...])

            self._backend.activate()
            self._session_start_ts = time.monotonic()
            self._connected = True
            return
        except Exception as xcpt:
            last_exc = xcpt
            if DEACTIVATION_FAILED_SENTINEL in str(xcpt):
                # Never retry "Deactivation failed"; it needs operator intervention.
                raise UefiTransportError(...) from xcpt
            if not isinstance(xcpt, tuple(effective.allowed_connection_exceptions)):
                raise
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

### `cmdredact.py`

```python
REDACTED = "***REDACTED***"

def redact_argv(argv: list[str]) -> list[str]:
    out: list[str] = []
    skip_next = False
    for tok in argv:
        if skip_next:
            out.append(REDACTED)
            skip_next = False
            continue
        if tok == "-P":
            out.append(tok)
            skip_next = True
            continue
        if tok.startswith("--password="):
            out.append("--password=" + REDACTED)
            continue
        out.append(tok)
    return out
```

## Data Models

### Backend Selection

| Constructor args                        | Backend             |
|-----------------------------------------|---------------------|
| `ipmi_session=<pyghmi>`                 | `PyghmiSolBackend`  |
| `credential=<UefiIpmiCredential>`       | `IpmitoolSolBackend`|
| both / neither                          | `ConfigurationError`|

### `ExistingSessionInfo`

| Field         | Type              | Source                                |
|---------------|-------------------|---------------------------------------|
| `user`        | `Optional[str]`   | parsed from `sol info` output if any  |
| `source_ip`   | `Optional[str]`   | parsed from `sol info` output if any  |
| `raw_output`  | `str`             | verbatim tool output                  |

## Measured Interop Contract

`UefiIpmiSolTransport` consumes the following fields from `UefiAspects`:

| Field                             | Consumed By                                 |
|-----------------------------------|---------------------------------------------|
| `action_pattern`                  | `connect()` retry-loop shape                |
| `connection_timeout`              | `connect()` overall deadline                |
| `connection_interval`             | `connect()` sleep between attempts          |
| `allowed_connection_exceptions`   | `connect()` soft-failure filter             |
| `must_connect`                    | `connect()` hard-fail switch                |
| `completion_timeout`              | `disconnect()` deactivation deadline        |
| `completion_interval`             | `disconnect()` poll for deactivation ack    |
| `inactivity_interval`             | `read_until()` progress-log cadence         |
| `inactivity_timeout`              | reserved for layer 3 consumers              |
| `logger`                          | all log emission                            |
| `logging_pattern`                 | forwarded; not acted on here                |

Supported `ActionPattern` values on `connect()`:

* `SINGLE_CALL`, `SINGLE_CONNECTED_CALL`, `DO_UNTIL_SUCCESS`,
  `DO_UNTIL_CONNECTION_FAILURE`.
* `DO_WHILE_SUCCESS` — raises `SemanticError`.

`LoggingPattern.ALL_RESULTS` / `FAILURE_ONLY` / `SUCCESS_ONLY` are
forwarded unchanged.

## Error Handling

| Condition                                                     | Exception              | Included in `details`                              |
|---------------------------------------------------------------|------------------------|----------------------------------------------------|
| Both `ipmi_session` and `credential` passed                   | `ConfigurationError`   | passed field names                                 |
| Neither `ipmi_session` nor `credential` passed                | `ConfigurationError`   | field names accepted                               |
| `ipmitool` binary not on PATH                                 | `UefiTransportError`   | searched PATH                                      |
| Existing SOL session + `evict_existing=False`                 | `UefiTransportError`   | session holder (user / IP) if available            |
| `sol deactivate` returns "Deactivation failed"                | `UefiTransportError`   | raw tool output, redacted argv                     |
| `connection_timeout` elapsed                                  | `TimeoutError`         | BMC host, attempts, elapsed, last exc              |
| `must_connect=True` + connect failure                         | `ConnectionError`      | BMC host, last exc                                 |
| `read_until` pattern not observed                             | `TimeoutError`         | pattern, timeout, `.partial` length                |
| SOL session drops mid-`read_until`                            | `ConnectionError`      | BMC host, bytes seen                               |
| Unsupported `ActionPattern` on `connect()`                    | `SemanticError`        | pattern name                                       |
| Any non-zero `ipmitool` exit                                  | `UefiTransportError`   | argv (redacted), stdout, stderr                    |
| Any `pyghmi` exception                                        | `UefiTransportError`   | chained via `from`                                 |

`SemanticError` escapes unchanged per the coding standards.

## Logging

All log lines route to channel `uefi.transport.ipmisol`:

* `INFO` — connect begin / connect success / disconnect / backend
  selection.
* `WARNING` — existing-session detection when `evict_existing=False` is
  about to raise, `ipmitool` stderr noise.
* `ERROR` — `Deactivation failed` captured at activation time.
* `DEBUG` — read/write byte counts, `read_until` inactivity heartbeats,
  subprocess argv (after `redact_argv`).

`ipmitool` command lines are **always** passed through `redact_argv`
before any log emission.  `pyghmi` sessions never contain plain-text
credentials in their repr, so no extra redaction is required there.

## Security Considerations

* IPMI-over-LAN is an under-audited protocol historically; the transport
  defaults `interface="lanplus"` (IPMI v2.0 with RAKP) so that plaintext
  IPMI v1.5 is never selected implicitly.
* Subprocess argv passes `-P password` on the command line — this is
  readable via `/proc/<pid>/cmdline` for the duration of the process.
  This is an `ipmitool` limitation, not ours; callers who need stricter
  secrecy should use `pyghmi` which negotiates in-session.
* Credential redaction applies only to argv logging.  `UefiIpmiCredential`
  itself redacts on `__repr__` (enforced by `1-credentials`).
* `evict_existing=True` is destructive to a concurrent operator;
  `connect()` emits an `INFO` log naming `evict_existing=True` so that it
  appears in every run log.

## Testing Strategy

### Unit tests (`test_ipmisoltransport.py`)

* Backend selection — pyghmi / ipmitool / both / neither.
* `evict_existing=False` with existing session raises
  `UefiTransportError` with holder details.
* `evict_existing=True` issues deactivate then activate.
* `Deactivation failed` surfaces as `UefiTransportError`; not retried.
* `connect()` honors every `ActionPattern`; `DO_WHILE_SUCCESS` raises
  `SemanticError`.
* Context-manager entry/exit calls connect/disconnect.

### Unit tests (`test_ipmitool_backend.py`)

* Argv assembly for lanplus / lan interfaces.
* Non-zero exit captured in `UefiTransportError`.
* Missing binary raises `UefiTransportError`.
* Read/write round trip using mocked `subprocess.Popen`.

### Unit tests (`test_pyghmi_backend.py`)

* Activate pumps callback → internal queue → `read()` returns bytes.
* `pyghmi` exceptions wrap into `UefiTransportError`.

### Unit tests (`test_cmdredact.py`)

* `-P <pass>` → `-P ***REDACTED***`.
* `--password=<pass>` → `--password=***REDACTED***`.
* Non-credential tokens pass through unchanged.
* Final token is `-P` (no value): redaction still leaves argv well-formed.

### Unit tests (`test_solactivation.py`)

* `probe_existing_session` parses `sol info` output for user / source IP.
* `force_deactivate` surfaces "Deactivation failed".

### Integration tests

* Gated by `AUTOMOJO_IPMI_HOST` env var.
* Round-trip: activate → send `\r\n` → read prompt → deactivate.
* Eviction race: second `connect()` with `evict_existing=True` wins.

### Coverage target

95% line coverage.  Real-hardware branches in `pyghmi` that require BMC
firmware behavior are exempted.

## Dependencies on Other Features

* **1-aspects** — `UefiAspects`, `ActionPattern`, `resolve_aspects`,
  `DEFAULT_ASPECTS`.
* **1-credentials** — `UefiIpmiCredential`.
* **1-exceptions** — `UefiTransportError`, `ConfigurationError` (shared),
  `SemanticError` (shared).
* **1-interfaces** — `IUefiTransport`.
* **1-logging** — `get_uefi_logger("uefi.transport.ipmisol")`.
* **2-transport-serial** — `read_until` loop and `normalize(mode)` factory,
  reused via composition (not inheritance).

External (one of):

* `pyghmi` — when the caller supplies a pre-built session.
* `ipmitool` binary on `PATH` — when the caller supplies only a credential.

## Transport Matrix

| Capability                         | IPMI-SOL                                          |
|------------------------------------|---------------------------------------------------|
| Pre-boot (no OS required)          | Yes                                               |
| Post-boot (OS running)             | Yes (same console)                                |
| UEFI variable read                 | Yes (via Shell `dmpstore`)                        |
| UEFI variable write                | Yes (via Shell `setvar`)                          |
| Capsule delivery                   | No (no direct capsule path over SOL)              |
| Boot-option management             | Yes (via Shell `bcfg`)                            |
| ESP file operations                | Limited (via Shell `cp`/`ls`, small files)        |
| System reset                       | Yes (via Shell `reset`; separately via IPMI chassis) |
| Firmware setup navigation          | Yes (HII screen-scrape, see `5-hii-forms`)        |
| Required operator credentials      | BMC IPMI credential                               |
| Bandwidth                          | Low (baud-limited, ~57600–115200)                 |
