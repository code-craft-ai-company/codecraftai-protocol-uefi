# Design — Serial Transport

## Overview

`UefiSerialTransport` is a layer-2 byte-level transport that reaches the UEFI
console over either a locally-attached serial device or a network-wrapped
serial concentrator.  It implements `IUefiTransport` (see
`1-interfaces`) and honors the measured-interop contract defined by
`UefiAspects` / `UefiCmdAspects` (see `1-aspects`).

The implementation is deliberately thin.  It delegates link-layer framing to
`pyserial` for local devices, uses a raw `socket` for `socket://` endpoints,
and layers a minimal telnet IAC-stripping adapter on top of `socket` for
`telnet://` endpoints.  A single `_read_buffer: bytearray` owned by the
transport is the only place raw device bytes live before they become the
output of `read_until` / `recv`.

The design is modeled on `codecraftai-protocol-ssh`'s
`sshconnection.SshConnection` pattern — the transport is responsible for
*establishing* and *tearing down* the byte pipe, while all retry semantics are
driven externally by the `aspects` object.  No decorator-based retry is used
(per the coding standards in `/Users/myron/repos/CLAUDE.md`).

## Architecture

```
ccai.protocols.uefi.transport.serial/
    __init__.py             # re-exports UefiSerialTransport
    serialtransport.py      # UefiSerialTransport (IUefiTransport)
    endpoints.py            # endpoint scheme parser + factory
    local_backend.py        # pyserial-based byte pipe
    socket_backend.py       # raw TCP byte pipe
    telnet_backend.py       # telnet-IAC-stripping TCP byte pipe
    readuntil.py            # read_until loop shared by backends
    termios_snapshot.py     # capture/restore for local devices
```

Dependency direction (nothing imports upward):

```
serialtransport.py ──▶ endpoints.py
                   ──▶ local_backend.py   ──▶ termios_snapshot.py
                   ──▶ socket_backend.py
                   ──▶ telnet_backend.py  ──▶ socket_backend.py
                   ──▶ readuntil.py
```

## Components and Interfaces

### `UefiSerialTransport` (serialtransport.py)

Public constructor:

```python
class UefiSerialTransport(IUefiTransport):
    def __init__(
        self,
        endpoint: str,
        baud: int = 115200,
        parity: str = "N",
        stop_bits: int = 1,
        flow_control: str = "none",
        line_ending_normalization: str = "crlf_to_lf",
        aspects: Optional[UefiAspects] = None,
    ) -> None:
        super().__init__()
        self._endpoint = endpoint
        self._baud = baud
        self._parity = parity
        self._stop_bits = stop_bits
        self._flow_control = flow_control
        self._line_ending_normalization = line_ending_normalization
        self._aspects = aspects if aspects is not None else DEFAULT_ASPECTS
        self._backend: Optional[_SerialBackend] = None
        self._read_buffer = bytearray()
        self._termios_snapshot: Optional[TermiosSnapshot] = None
        self._connected = False
        self._reconnect_count = 0
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
        rtnval = "serial"
        return rtnval
    def __enter__(self) -> "UefiSerialTransport": ...
    def __exit__(self, exc_type, exc_val, exc_tb) -> None: ...
```

### `_SerialBackend` (abstract)

A tiny internal abstract base that the three backends (`local`, `socket`,
`telnet`) share.  Methods: `open()`, `close()`, `read(max_bytes, timeout)
-> bytes`, `write(data) -> int`, `is_open` property.  The transport never
calls `pyserial` or `socket` directly — it only talks to a backend.

### Connection Loop (serialtransport.py)

The connection loop mirrors the shape used in
`sshconnection.SshConnection.connect` (bounded by
`connection_timeout` / `connection_interval`, catching only exception types
listed in `allowed_connection_exceptions`):

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
            self._open_backend()
            self._connected = True
            return
        except Exception as xcpt:
            last_exc = xcpt
            if not isinstance(xcpt, tuple(effective.allowed_connection_exceptions)):
                raise
            if effective.must_connect:
                raise ConnectionError(...) from xcpt
            if effective.action_pattern == ActionPattern.SINGLE_CALL:
                raise
            if time.monotonic() >= deadline:
                err_msg_lines = [...]  # per multiline-error-message standard
                err_msg = os.linesep.join(err_msg_lines)
                raise TimeoutError(err_msg) from last_exc

        time.sleep(effective.connection_interval)
```

### `read_until` (readuntil.py)

```python
def read_until(
    backend: _SerialBackend,
    read_buffer: bytearray,
    pattern: Union[bytes, re.Pattern[bytes]],
    timeout: float,
    aspects: UefiAspects,
    normalize: Callable[[bytes], bytes],
    logger: logging.Logger,
) -> bytes:
    deadline = time.monotonic() + timeout
    last_progress = time.monotonic()

    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            err = TimeoutError(...)
            err.partial = bytes(read_buffer)
            raise err

        chunk = backend.read(4096, min(remaining, aspects.inactivity_interval))
        if len(chunk) > 0:
            read_buffer.extend(normalize(chunk))
            last_progress = time.monotonic()

        match_end = _find_match_end(read_buffer, pattern)
        if match_end is not None:
            out = bytes(read_buffer[:match_end])
            del read_buffer[:match_end]
            return out

        if time.monotonic() - last_progress >= aspects.inactivity_interval:
            logger.debug("read_until: no bytes in last %0.3fs", aspects.inactivity_interval)
            last_progress = time.monotonic()
```

### `TermiosSnapshot` (termios_snapshot.py)

```python
@dataclass(frozen=True)
class TermiosSnapshot:
    fd: int
    iflag: int
    oflag: int
    cflag: int
    lflag: int
    ispeed: int
    ospeed: int
    cc: tuple

def capture_termios(fd: int) -> TermiosSnapshot: ...
def restore_termios(snapshot: TermiosSnapshot) -> None: ...
```

Only used by `local_backend.py`; the socket/telnet backends skip it.

## Data Models

### Endpoint Scheme Table

| Scheme      | Backend         | Example                        |
|-------------|-----------------|--------------------------------|
| (bare path) | local_backend   | `/dev/ttyUSB0`, `COM3`         |
| `socket://` | socket_backend  | `socket://concentrator:7001`   |
| `telnet://` | telnet_backend  | `telnet://concentrator:7001`   |

### Line-Ending Normalization Values

| Value          | Inbound Transform           | Outbound |
|----------------|-----------------------------|----------|
| `"crlf_to_lf"` | `\r\n` → `\n`               | literal  |
| `"cr_to_lf"`   | lone `\r` → `\n`            | literal  |
| `"none"`       | pass-through                | literal  |

## Measured Interop Contract

`UefiSerialTransport` consumes the following fields from `UefiAspects`:

| Field                             | Consumed By                         |
|-----------------------------------|-------------------------------------|
| `action_pattern`                  | `connect()` retry-loop shape        |
| `connection_timeout`              | `connect()` overall deadline        |
| `connection_interval`             | `connect()` sleep between attempts  |
| `allowed_connection_exceptions`   | `connect()` soft-failure filter     |
| `must_connect`                    | `connect()` hard-fail switch        |
| `inactivity_interval`             | `read_until()` progress-log cadence |
| `inactivity_timeout`              | reserved for layer 3 consumers      |
| `completion_timeout`              | not directly consumed; forwarded    |
| `completion_interval`             | not directly consumed; forwarded    |
| `logger`                          | all log emission                    |
| `logging_pattern`                 | forwarded; not acted on here        |

Supported `ActionPattern` values on `connect()`:

* `SINGLE_CALL` — one attempt, raise on failure.
* `SINGLE_CONNECTED_CALL` — no-op if already connected; otherwise one attempt.
* `DO_UNTIL_SUCCESS` — retry while `allowed_connection_exceptions` matches,
  bounded by `connection_timeout` / `connection_interval`.
* `DO_UNTIL_CONNECTION_FAILURE` — wait on an established connection until it
  breaks, used by liveness probes.
* `DO_WHILE_SUCCESS` — raises `SemanticError` (not meaningful here).

Supported `LoggingPattern` values are forwarded unchanged; this transport
does not short-circuit on them, but its log emission respects `ALL_RESULTS`
as the default.

## Error Handling

| Condition                                           | Exception             | Included in `details`                 |
|-----------------------------------------------------|-----------------------|---------------------------------------|
| Unknown endpoint scheme                             | `ConfigurationError`  | scheme, accepted schemes              |
| Link-layer params passed on network endpoint        | `ConfigurationError`  | passed fields, endpoint scheme        |
| `connection_timeout` elapsed                        | `TimeoutError`        | endpoint, attempts, elapsed, last exc |
| `must_connect=True` + connect failure               | `ConnectionError`     | endpoint, last exc                    |
| `read_until` pattern not observed                   | `TimeoutError`        | pattern, timeout, `.partial` length   |
| Connection drops mid-`read_until`                   | `ConnectionError`     | endpoint, bytes seen                  |
| Unsupported `ActionPattern` on `connect()`          | `SemanticError`       | pattern name                          |
| `termios` restore failure                           | (logged WARNING only) | n/a — suppressed                      |
| Soft-failure exception not in `allowed_connection_exceptions` | re-raised  | n/a                                   |

`SemanticError` escapes unchanged per the coding standards — never caught.

## Logging

All log lines emitted by this feature route to channel
`uefi.transport.serial`:

* `INFO` — connect begin / connect success / disconnect.
* `WARNING` — soft-failure retries, `read_until` timeout, termios-restore
  failure.
* `DEBUG` — `read_until` inactivity heartbeats, send/recv byte counts at
  `len > 0`, backend open/close detail.

No log line ever embeds credential material (there is none in this feature).

## Security Considerations

* Local serial devices on POSIX require group `dialout` (or equivalent) —
  the transport does not elevate privileges.  `PermissionError` bubbles to
  the caller.
* Network-wrapped serial (`socket://`, `telnet://`) is unencrypted.  The
  transport does not tunnel TLS.  Callers who require encrypted serial reach
  must front the concentrator with an SSH tunnel or VPN and point the
  transport at the tunneled endpoint.
* Telnet IAC negotiation bytes are stripped from the inbound stream to
  prevent a malicious concentrator from injecting pseudo-commands into the
  Shell prompt, but the transport neither negotiates nor honors options.

## Testing Strategy

### Unit tests (`test_serialtransport.py`)

* Endpoint scheme dispatch — `/dev/...`, `COM*`, `socket://`, `telnet://`,
  unknown.
* Connect happy path with mocked backend.
* Connect retry path — `allowed_connection_exceptions` accepts and rejects
  exception types.
* Connect `must_connect=True` raises immediately.
* Connect timeout — deadline reached, `TimeoutError` raised with expected
  `details`.
* `SINGLE_CONNECTED_CALL` skips a second connect when already connected.
* `DO_WHILE_SUCCESS` raises `SemanticError`.
* Context-manager entry/exit calls connect/close.

### Unit tests (`test_readuntil.py`)

* Match on `bytes` pattern across a chunk boundary.
* Match on compiled regex across a chunk boundary.
* Timeout raises `TimeoutError` with populated `.partial`.
* Inactivity heartbeat emits a DEBUG log every `inactivity_interval`.

### Unit tests (`test_normalization.py`)

* CRLF→LF normalization.
* CR→LF normalization.
* Passthrough.
* Chunk-boundary CR handling (no double-translation).

### Integration tests (POSIX only)

* PTY-backed pseudo-serial pair — `pty.openpty()` provides a real local
  device pair; test round-trips.
* Socat-backed `socket://` loopback — spawn a socat in a fixture, point the
  transport at it, round-trip bytes.

### Fault-injection tests

* Flapping connection — backend raises `ConnectionResetError` every other
  attempt; `DO_UNTIL_SUCCESS` eventually succeeds.
* Truncated reads — backend delivers 1-byte chunks; `read_until` still
  converges.
* CR-only line endings — firmware emits only `\r`; `cr_to_lf` normalization
  produces clean lines.

### Coverage target

95% line coverage for this feature (the `socket_backend.py` and
`telnet_backend.py` modules have small OS-dependent branches where full
coverage is impractical without real hardware).

## Dependencies on Other Features

* **1-aspects** — `UefiAspects`, `ActionPattern`, `LoggingPattern`,
  `resolve_aspects`, `DEFAULT_ASPECTS`.
* **1-exceptions** — `UefiTransportError`, `ConfigurationError` (shared),
  `SemanticError` (shared).
* **1-interfaces** — `IUefiTransport`.
* **1-logging** — `get_uefi_logger("uefi.transport.serial")`.
* **1-constants-and-types** — accepted-scheme literals, accepted parity
  literals.

## Transport Matrix

| Capability                         | Serial                                        |
|------------------------------------|-----------------------------------------------|
| Pre-boot (no OS required)          | Yes                                           |
| Post-boot (OS running)             | Yes (same console)                            |
| UEFI variable read                 | Yes (via Shell `dmpstore`)                    |
| UEFI variable write                | Yes (via Shell `setvar`)                      |
| Capsule delivery                   | No (no direct capsule path over console)      |
| Boot-option management             | Yes (via Shell `bcfg`)                        |
| ESP file operations                | Limited (via Shell `cp`/`ls`, small files)    |
| System reset                       | Yes (via Shell `reset`)                       |
| Firmware setup navigation          | Yes (HII screen-scrape, see `5-hii-forms`)    |
| Required operator credentials      | Physical / concentrator access                |
| Bandwidth                          | Low (baud-limited)                            |
