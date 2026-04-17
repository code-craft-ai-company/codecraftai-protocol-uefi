# Design — UefiConnection

## Overview

`UefiConnection` is the lifecycle-managed wrapper around a single underlying
transport handle.  Its responsibilities are bounded: it connects,
disconnects, reconnects, and emits structured log events — nothing else.
It does not frame commands (that is `UefiClient`), does not retry commands
(that is `UefiBase`'s action-pattern engine), and does not own channels
(that is `UefiChannel`).

The class is modeled on the connection-establishment loop inside
`SshBase._create_client` at
`/Users/myron/repos/automojo-protocol-ssh/source/packages/automojo/protocols/ssh/sshbase.py`
lines 288–375.  The notable enhancements are:

* **Explicit reconnection API.**  The SSH library relies on reconstructing
  an `SshClient` every time; `UefiConnection` exposes `reconnect()` as a
  first-class primitive and counts attempts on `reconnect_count`.
* **Structured log events.**  Every transition emits a JSON-shaped event
  on `uefi.transport` instead of ad-hoc `logger.error` calls.
* **Subclass-based `isinstance` match for allowed exceptions.**  The SSH
  library uses `type(cerr) in allowed_connection_exceptions`, which rejects
  subclasses; we use `isinstance(cerr, tuple(allowed))` so that
  `ConnectionResetError` is covered when the caller lists
  `ConnectionError`.

## Architecture

```
automojo.protocols.uefi.connection/
    __init__.py         # re-exports UefiConnection
    ueficonnection.py   # the class
    events.py           # ConnectionEvent, ReconnectEvent structured types
```

Dependency direction:

```
ueficonnection.py ──▶ automojo.protocols.uefi.aspects
                  ──▶ automojo.protocols.uefi.logging
                  ──▶ automojo.protocols.uefi.interfaces  # IUefiTransport
                  ──▶ automojo.protocols.uefi.exceptions
events.py         ──▶ (stdlib only)
```

## Components and Interfaces

### `UefiConnection` (ueficonnection.py)

```python
class UefiConnection:
    """
        Lifecycle-managed wrapper around an ``IUefiTransport`` handle.
        Carries reconnection logic bounded by aspects and emits
        structured log events on every transition.
    """

    def __init__(
        self,
        transport: IUefiTransport,
        aspects: UefiAspects = DEFAULT_ASPECTS,
        host: Optional[str] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        if not isinstance(transport, IUefiTransport):
            err_msg_lines = [
                "UefiConnection requires an IUefiTransport instance.",
                f"RECEIVED: {type(transport).__name__}",
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg) from None
        self._transport = transport
        self._aspects = aspects
        self._host = host if host is not None else getattr(transport, "host", "<unknown>")
        self._logger = logger if logger is not None else get_uefi_logger("uefi.transport")
        self._connected = False
        self._reconnect_count = 0
        self._last_connect_begin: Optional[datetime] = None
        self._last_connect_end: Optional[datetime] = None
        return

    @property
    def aspects(self) -> UefiAspects:
        return self._aspects

    @property
    def host(self) -> str:
        return self._host

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def reconnect_count(self) -> int:
        return self._reconnect_count

    @property
    def transport(self) -> IUefiTransport:
        return self._transport

    @property
    def transport_kind(self) -> str:
        return self._transport.transport_kind

    def connect(self, aspects: Optional[UefiAspects] = None) -> None:
        if self._connected:
            self._logger.debug("uefi.transport: connect() called on already-connected %s", self._host)
            return
        resolved = resolve_aspects(aspects, self._aspects)
        self._do_connect_with_retry(resolved=resolved)
        return

    def disconnect(self) -> None:
        if not self._connected:
            return
        try:
            self._transport.disconnect()
        except BaseException as cerr:
            self._logger.warning(
                "uefi.transport: disconnect raised and was suppressed: host=%s err=%r",
                self._host, cerr,
            )
        self._connected = False
        self._logger.info(
            "uefi.transport: disconnected host=%s transport_kind=%s",
            self._host, self.transport_kind,
        )
        return

    def reconnect(self, aspects: Optional[UefiAspects] = None) -> None:
        resolved = resolve_aspects(aspects, self._aspects)
        begin = datetime.now()
        self.disconnect()
        self._do_connect_with_retry(resolved=resolved)
        self._reconnect_count += 1
        elapsed_ms = int((datetime.now() - begin).total_seconds() * 1000)
        self._logger.info(
            "uefi.transport: reconnected host=%s transport_kind=%s reconnect_count=%d elapsed_ms=%d",
            self._host, self.transport_kind, self._reconnect_count, elapsed_ms,
        )
        return

    def __enter__(self) -> "UefiConnection":
        self.connect()
        return self

    def __exit__(self, ex_type, ex_val, ex_tb) -> bool:
        self.disconnect()
        return False

    def _do_connect_with_retry(self, resolved: UefiAspects) -> None:
        begin_time = datetime.now()
        self._last_connect_begin = begin_time
        end_time = begin_time + timedelta(seconds=resolved.connection_timeout)
        connection_interval = resolved.connection_interval
        allowed = tuple(resolved.allowed_connection_exceptions)
        must_connect = resolved.must_connect

        attempt = 0
        last_exception: Optional[BaseException] = None
        while True:
            attempt += 1
            try:
                self._transport.connect()
                self._connected = True
                self._last_connect_end = datetime.now()
                elapsed_ms = int((self._last_connect_end - begin_time).total_seconds() * 1000)
                self._logger.info(
                    "uefi.transport: connected host=%s transport_kind=%s elapsed_ms=%d attempts=%d",
                    self._host, self.transport_kind, elapsed_ms, attempt,
                )
                return
            except BaseException as cerr:
                last_exception = cerr
                now_time = datetime.now()
                remaining_ms = int((end_time - now_time).total_seconds() * 1000)

                if must_connect:
                    self._logger.error(
                        "uefi.transport: must_connect=True; raising immediately host=%s err=%r",
                        self._host, cerr,
                    )
                    raise ConnectionError(str(cerr)) from cerr

                if len(allowed) == 0 or not isinstance(cerr, allowed):
                    self._logger.error(
                        "uefi.transport: non-allowed connection exception host=%s err=%r",
                        self._host, cerr,
                    )
                    raise

                self._logger.warning(
                    "uefi.transport: transient connect failure attempt=%d host=%s err=%r remaining_ms=%d",
                    attempt, self._host, cerr, remaining_ms,
                )

                if now_time >= end_time:
                    err_msg_lines = [
                        "UefiConnection failed to connect within the configured timeout.",
                        f"HOST: {self._host}",
                        f"TRANSPORT: {self.transport_kind}",
                        f"BEGIN: {begin_time.isoformat()}",
                        f"END: {end_time.isoformat()}",
                        f"ATTEMPTS: {attempt}",
                        f"LAST_ERR: {cerr!r}",
                    ]
                    err_msg = os.linesep.join(err_msg_lines)
                    self._logger.error(
                        "uefi.transport: terminal connect failure host=%s attempts=%d",
                        self._host, attempt,
                    )
                    raise ConnectionError(err_msg) from last_exception

                time.sleep(connection_interval)
```

### `ConnectionEvent` / `ReconnectEvent` (events.py)

```python
@dataclass(frozen=True, slots=True)
class ConnectionEvent:
    event_type: str            # "connect" | "disconnect" | "reconnect"
    host: str
    transport_kind: str
    elapsed_ms: int
    attempts: int
    reconnect_count: int
    outcome: str               # "success" | "failure"
    error_repr: Optional[str] = None
```

Produced by the logger via a custom `Formatter` when the handler opts in;
used as a structured-logging contract.  The connection class emits ordinary
`LogRecord` calls; translation to `ConnectionEvent` is done by the
sink.  This keeps the class free of JSON-formatting concerns.

## Data Models

The connection itself has only instance-state scalars — no persistent
records.  `ConnectionEvent` is the only data class.  `reconnect_count` is
an `int` counter; the `_last_connect_begin` / `_last_connect_end` pair is
`Optional[datetime]` for diagnostics.  All instance variables are
initialized in `__init__` per
`/Users/myron/repos/CLAUDE.md` standards — no lazy attributes.

## Measured Interop Contract

This feature consumes the following `UefiAspects` fields:

* `connection_timeout` — end-time for the retry loop.
* `connection_interval` — sleep between failed attempts.
* `allowed_connection_exceptions` — tolerance list for transient failures.
* `must_connect` — if `True`, the retry loop is skipped and the first
  failure propagates as `ConnectionError`.
* `logger` — not directly consumed; `UefiConnection` obtains its own
  `uefi.transport` logger. Caller-supplied loggers are honored via the
  optional `logger` constructor parameter.

The feature does NOT consume `completion_timeout`, `completion_interval`,
`inactivity_timeout`, `inactivity_interval`, `monitor_delay`,
`logging_pattern`, `action_pattern`, or the `UefiCmdAspects`-specific
fields (`expected_status`, `prompt_pattern`, `response_encoding`, `user`,
`transport_kind`).  These are consumed by `3-client` and `4-base`.

`ActionPattern`s supported: none directly.  `UefiConnection` is a
lifecycle primitive; action patterns are layered on top by
`4-base._run_cmd`.  `UefiSession` invokes `reconnect()` from within its
`DO_UNTIL_CONNECTION_FAILURE` loop — but that is at layer 4.

## Error Handling

| Condition                                       | Exception            | Included in `details`           |
|-------------------------------------------------|----------------------|---------------------------------|
| `transport` is not an `IUefiTransport`          | `ConfigurationError` | received type                   |
| Connect exhausts `connection_timeout`           | `ConnectionError`    | host, transport_kind, begin/end, attempts, last exception |
| Non-allowed exception during connect            | passthrough          | (re-raised unchanged)           |
| `must_connect=True` and first connect fails     | `ConnectionError`    | chained from original           |
| Assignment to read-only property                | `AttributeError`     | property name                   |
| Disconnect raises underneath                    | suppressed + WARN    | logged, not raised              |

`SemanticError` is never raised by this feature.

## Logging

All output routes through `get_uefi_logger("uefi.transport")`.

| Event                      | Level   | Fields                                                                      |
|----------------------------|---------|-----------------------------------------------------------------------------|
| connect.success            | INFO    | host, transport_kind, elapsed_ms, attempts                                  |
| connect.attempt_failure    | WARNING | host, attempt_number, exception_type, exception_repr, remaining_ms          |
| connect.terminal_failure   | ERROR   | host, transport_kind, attempts, exception_chain                             |
| connect.nonallowed_failure | ERROR   | host, exception_type, exception_repr                                        |
| disconnect.success         | INFO    | host, transport_kind                                                        |
| disconnect.suppressed      | WARNING | host, exception_repr                                                        |
| reconnect.success          | INFO    | host, transport_kind, reconnect_count, elapsed_ms                           |

## Security Considerations

* The connection object stores a transport, which may hold credentials.
  `__repr__` redacts any attribute named `password`, `token`, `keyraw`,
  `keypasswd`, or `*_secret`.
* Log events never include credential values — only host, transport kind,
  timings, and exception reprs.  Transports are expected to scrub their
  own `repr` for credential fields.

## Testing Strategy

### Unit tests (`test_ueficonnection_lifecycle.py`)

* `connect` on a disconnected connection sets `connected=True` and emits
  the expected INFO event.
* `connect` on an already-connected connection is a no-op returning
  cleanly with a DEBUG log.
* `disconnect` on a connected connection sets `connected=False` and emits
  the expected INFO event.
* `disconnect` on an already-disconnected connection is a no-op.
* `reconnect` disconnects then connects and increments `reconnect_count`.
* `connected` is a property; assignment raises `AttributeError`.
* `reconnect_count` is read-only; assignment raises `AttributeError`.

### Unit tests (`test_ueficonnection_retry.py`)

* With `allowed_connection_exceptions=(ConnectionError,)` and a transport
  that raises once then succeeds, `connect()` returns without error and
  emits one WARNING event.
* With empty `allowed_connection_exceptions`, the first exception
  propagates immediately.
* A subclass exception (e.g., `ConnectionResetError` vs.
  `ConnectionError`) is matched by `isinstance`.
* A transport that raises continuously for the entire
  `connection_timeout` window results in a terminal `ConnectionError` with
  the last exception chained via `__cause__`.
* `must_connect=True` skips the retry loop on the first failure.

### Unit tests (`test_ueficonnection_aspects.py`)

* `reconnect(aspects=None)` resolves to the instance default.
* `reconnect(aspects=X)` uses `X` for that call only and does not mutate
  the instance default.
* Passed aspects override `connection_timeout` and `connection_interval`
  for the duration of the call.

### Integration tests

* With a mocked `UefiSerialTransport`, exercise connect / disconnect /
  reconnect / reconnect_count transitions.
* With a mocked `UefiRedfishTransport`, exercise the same.
* With a mocked `UefiSshHostTransport`, verify the composed `SshAgent`
  `_create_client` is invoked and its exceptions propagate per the
  allowed-exceptions rule.
* Coverage target: 95% line coverage.

## Dependencies on Other Features

* `1-aspects` — `UefiAspects`, `DEFAULT_ASPECTS`, `resolve_aspects`.
* `1-exceptions` — `ConfigurationError`, `UefiTransportError`.
* `1-interfaces` — `IUefiTransport`.
* `1-logging` — `get_uefi_logger("uefi.transport")`.
* `2-transport-serial`, `2-transport-redfish`, `2-transport-ssh-host`,
  `2-transport-ipmi-sol` — the concrete `IUefiTransport`s this
  connection wraps.
