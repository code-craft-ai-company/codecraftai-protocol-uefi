# Design — UefiClient

## Overview

The `UefiClient` feature delivers the protocol-framing layer that sits on top
of layer-2 transports and under layer-4 `UefiBase`.  It is organized as one
abstract base class plus three concrete subclasses, each specializing the
framing logic for a single transport family.  The abstract base class is
modeled closely on `SshClient`
(`/Users/myron/repos/automojo-protocol-ssh/source/packages/automojo/protocols/ssh/sshclient.py`),
extended with explicit `supported_action_patterns` declaration so that
layer-4 can validate action-pattern compatibility before any bytes are sent.

Clients hold a reference to their transport, a default `UefiCmdAspects`, and
a `logging.Logger` routed to the `uefi.transport` channel.  Channels are
short-lived; the default for `open_channel()` is a fresh channel per call.
Multiplexing requires explicit opt-in and is supported only where the
transport supports it (Redfish sessions, SSH multiplexed channels) — never
on serial or IPMI-SOL.

On any mid-execute connection loss the client re-raises `ConnectionError`
immediately.  Retry is the sole responsibility of layer-4's
`ActionPattern` engine (see `4-base`).  This matches the SSH library's
division of labor exactly (`sshbase.py:347–368`).

## Architecture

```
automojo.protocols.uefi.client/
    __init__.py              # re-exports public surface
    ueficlient.py            # UefiClient abstract base
    uefishellclient.py       # UefiShellClient (serial / IPMI-SOL)
    uefiredfishclient.py     # UefiRedfishClient (Redfish HTTP)
    uefihostosclient.py      # UefiHostOsClient (SSH-to-host composition)
```

Dependency direction (nothing imports upward):

```
uefishellclient.py  ─┐
uefiredfishclient.py ─┼─▶ ueficlient.py ──▶ automojo.protocols.uefi.aspects
uefihostosclient.py ─┘                  ──▶ automojo.protocols.uefi.channel
                                        ──▶ automojo.protocols.uefi.transports.*
                                        ──▶ automojo.protocols.uefi.logging
```

Each concrete subclass lives in its own module so that tests for one
subclass do not have to import the others (Redfish pulls `requests`,
SSH-host pulls `automojo-protocol-ssh`; keeping them separate keeps
import cost proportional to the transport the caller actually uses).

## Components and Interfaces

### `UefiClient` (ueficlient.py)

```python
class UefiClient:
    """
        Abstract per-transport protocol client.  Subclasses MUST set
        ``supported_action_patterns`` and implement ``_open_channel_impl``,
        ``_execute_impl``, and ``_close_impl``.
    """

    supported_action_patterns: frozenset[ActionPattern] = frozenset()

    def __init__(
        self,
        transport: IUefiTransport,
        aspects: UefiCmdAspects = DEFAULT_CMD_ASPECTS,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._transport = transport
        self._aspects = aspects
        self._logger = logger if logger is not None else get_uefi_logger("uefi.transport")
        self._channels: list[UefiChannel] = []
        self._closed = False
        self._validate_transport()
        return

    @property
    def aspects(self) -> UefiCmdAspects:
        return self._aspects

    @property
    def transport(self) -> IUefiTransport:
        return self._transport

    @property
    def closed(self) -> bool:
        return self._closed

    def open_channel(self, multiplex: bool = False) -> UefiChannel:
        if self._closed:
            err_msg = "Cannot open_channel on a closed UefiClient."
            raise ConnectionError(err_msg)
        channel = self._open_channel_impl(multiplex=multiplex)
        self._channels.append(channel)
        return channel

    def execute(
        self,
        command: str,
        aspects: Optional[UefiCmdAspects] = None,
    ) -> UefiExecuteResult:
        resolved = resolve_aspects(aspects, self._aspects)
        self._check_action_pattern(resolved.action_pattern)
        result = self._execute_impl(command=command, aspects=resolved)
        return result

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for chan in list(self._channels):
            try:
                chan.close()
            except BaseException as cerr:
                self._logger.warning(
                    "uefi.transport: channel close raised during client.close: %s",
                    cerr,
                )
        try:
            self._close_impl()
        except BaseException as cerr:
            self._logger.warning(
                "uefi.transport: transport close raised during client.close: %s",
                cerr,
            )
        return

    def __enter__(self) -> "UefiClient":
        return self

    def __exit__(self, ex_type, ex_val, ex_tb) -> bool:
        self.close()
        return False

    def _check_action_pattern(self, pattern: ActionPattern) -> None:
        if pattern not in self.supported_action_patterns:
            err_msg_lines = [
                "Unsupported ActionPattern for this client.",
                f"CLIENT: {type(self).__name__}",
                f"PATTERN: {pattern.name}",
                f"SUPPORTED: {sorted(p.name for p in self.supported_action_patterns)}",
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise SemanticError(err_msg) from None
        return

    def _validate_transport(self) -> None:
        raise AbstractMethodError(
            "UefiClient._validate_transport must be overloaded by '%s'." % type(self).__name__
        ) from None

    def _open_channel_impl(self, multiplex: bool) -> UefiChannel:
        raise AbstractMethodError(
            "UefiClient._open_channel_impl must be overloaded by '%s'." % type(self).__name__
        ) from None

    def _execute_impl(self, command: str, aspects: UefiCmdAspects) -> UefiExecuteResult:
        raise AbstractMethodError(
            "UefiClient._execute_impl must be overloaded by '%s'." % type(self).__name__
        ) from None

    def _close_impl(self) -> None:
        raise AbstractMethodError(
            "UefiClient._close_impl must be overloaded by '%s'." % type(self).__name__
        ) from None
```

### `UefiShellClient` (uefishellclient.py)

```python
class UefiShellClient(UefiClient):
    """
        Client for serial and IPMI-SOL transports.  Performs prompt
        synchronization on every execute and treats the transport as a
        single-channel pipe — multiplex=True is a SemanticError.
    """

    supported_action_patterns = frozenset({
        ActionPattern.SINGLE_CALL,
        ActionPattern.DO_UNTIL_SUCCESS,
        ActionPattern.DO_WHILE_SUCCESS,
    })

    def _validate_transport(self) -> None:
        allowed = (UefiSerialTransport, UefiIpmiSolTransport)
        if not isinstance(self._transport, allowed):
            err_msg_lines = [
                "UefiShellClient requires a serial or IPMI-SOL transport.",
                f"EXPECTED: {[t.__name__ for t in allowed]}",
                f"RECEIVED: {type(self._transport).__name__}",
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg) from None
        return

    def _open_channel_impl(self, multiplex: bool) -> UefiChannel:
        if multiplex:
            err_msg = (
                "UefiShellClient does not support multiplexing over a serial "
                "or IPMI-SOL transport.  Open a second transport instead."
            )
            raise SemanticError(err_msg) from None
        channel = UefiShellChannel(transport=self._transport, aspects=self._aspects)
        return channel

    def _execute_impl(self, command: str, aspects: UefiCmdAspects) -> UefiExecuteResult:
        channel = self.open_channel()
        try:
            channel.send((command + os.linesep).encode(aspects.response_encoding))
            raw = channel.recv_until(
                pattern=aspects.prompt_pattern,
                timeout=aspects.inactivity_timeout,
            )
            status, stdout, stderr = self._parse_shell_response(raw, aspects)
            result = UefiExecuteResult(status=status, stdout=stdout, stderr=stderr)
        finally:
            channel.close()
        return result
```

### `UefiRedfishClient` (uefiredfishclient.py)

```python
class UefiRedfishClient(UefiClient):
    """
        Client for the Redfish HTTP transport.  ``execute`` accepts a
        command string of the form ``"VERB path [ json_body ]"`` and
        dispatches through the composed ``UefiRedfishTransport``.  Long-
        running tasks (202 Accepted) are polled under
        ``aspects.completion_*``.
    """

    supported_action_patterns = frozenset({
        ActionPattern.SINGLE_CALL,
        ActionPattern.DO_UNTIL_SUCCESS,
        ActionPattern.DO_WHILE_SUCCESS,
    })

    def _validate_transport(self) -> None:
        if not isinstance(self._transport, UefiRedfishTransport):
            err_msg_lines = [
                "UefiRedfishClient requires a UefiRedfishTransport.",
                f"RECEIVED: {type(self._transport).__name__}",
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg) from None
        return

    def _open_channel_impl(self, multiplex: bool) -> UefiChannel:
        channel = UefiRedfishChannel(
            transport=self._transport,
            aspects=self._aspects,
            shared_session=multiplex,
        )
        return channel

    def _execute_impl(self, command: str, aspects: UefiCmdAspects) -> UefiExecuteResult:
        channel = self.open_channel()
        try:
            verb, path, body = self._parse_redfish_command(command)
            response = channel.send_http(verb=verb, path=path, json_body=body, aspects=aspects)
            if response.status_code == 202:
                task_uri = response.headers["Location"]
                final = channel.poll_task(task_uri=task_uri, aspects=aspects)
                result = UefiExecuteResult.from_redfish_task(final)
            else:
                result = UefiExecuteResult.from_http_response(response)
        finally:
            channel.close()
        return result
```

### `UefiHostOsClient` (uefihostosclient.py)

```python
class UefiHostOsClient(UefiClient):
    """
        Client for the SSH-to-host transport.  Composes over an
        ``SshAgent`` and forwards command dispatch to it.  Mirrors the
        division of labor in ``SshClient`` — this class does not open
        paramiko-level sessions directly.
    """

    supported_action_patterns = frozenset({
        ActionPattern.SINGLE_CALL,
        ActionPattern.DO_UNTIL_SUCCESS,
        ActionPattern.DO_WHILE_SUCCESS,
    })

    def _validate_transport(self) -> None:
        if not isinstance(self._transport, UefiSshHostTransport):
            err_msg_lines = [
                "UefiHostOsClient requires a UefiSshHostTransport.",
                f"RECEIVED: {type(self._transport).__name__}",
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg) from None
        return

    def _open_channel_impl(self, multiplex: bool) -> UefiChannel:
        channel = UefiHostOsChannel(
            transport=self._transport,
            aspects=self._aspects,
            multiplex=multiplex,
        )
        return channel

    def _execute_impl(self, command: str, aspects: UefiCmdAspects) -> UefiExecuteResult:
        ssh_agent = self._transport.ssh_agent
        status, stdout, stderr = ssh_agent.run_cmd(command=command, aspects=aspects)
        result = UefiExecuteResult(status=status, stdout=stdout, stderr=stderr)
        return result
```

## Data Models

### `UefiExecuteResult`

```python
@dataclass(frozen=True, slots=True)
class UefiExecuteResult:
    status: int
    stdout: str
    stderr: str
    raw_bytes: Optional[bytes] = None
    elapsed_ms: Optional[int] = None
```

Exposed as the return type of `UefiClient.execute`.  Layer-4 converts this to
the `(status, stdout, stderr)` three-tuple expected by SSH-style callers.

### Internal Parse Outputs

Each subclass has a private parser — `_parse_shell_response`,
`_parse_redfish_command`, etc. — that returns plain tuples.  No shared
superclass data types beyond `UefiExecuteResult`.

## Measured Interop Contract

This feature *consumes* the following `UefiCmdAspects` fields:

* `action_pattern` — validated against `supported_action_patterns`; unsupported
  values raise `SemanticError` before any bytes move.
* `inactivity_timeout`, `inactivity_interval` — forwarded to channel
  `recv_until` calls.
* `completion_timeout`, `completion_interval` — used by `UefiRedfishClient`
  when polling TaskMonitor URIs.
* `prompt_pattern` — used by `UefiShellClient._execute_impl` as the
  `recv_until` terminator.
* `response_encoding` — used by all subclasses to decode bytes to str.
* `expected_status` — recorded on `UefiExecuteResult` for layer-4 logging.
* `logging_pattern` — consulted before emitting dispatch log events.
* `logger` — used to emit all `uefi.transport` events for this client.
* `allowed_connection_exceptions` — **not consumed** at this layer; layer-4
  handles retry/allowlisting.
* `must_connect` — **not consumed** at this layer.

`ActionPattern`s supported by subclass:

| Subclass              | SINGLE_CALL | DO_UNTIL_SUCCESS | DO_WHILE_SUCCESS | SINGLE_CONNECTED_CALL | DO_UNTIL_CONNECTION_FAILURE |
|-----------------------|:-----------:|:----------------:|:----------------:|:---------------------:|:---------------------------:|
| `UefiShellClient`     | yes         | yes              | yes              | SemanticError         | SemanticError               |
| `UefiRedfishClient`   | yes         | yes              | yes              | SemanticError         | SemanticError               |
| `UefiHostOsClient`    | yes         | yes              | yes              | SemanticError         | SemanticError               |

The two "connected" patterns are handled at layer-4 `UefiSession` only,
mirroring the SSH library (`sshbase.py:625–627`).

## Error Handling

| Condition                                                  | Exception            | Included in `details`           |
|------------------------------------------------------------|----------------------|---------------------------------|
| Client used after close                                    | `ConnectionError`    | client type, last-close time    |
| `multiplex=True` on non-multiplex transport                | `SemanticError`      | transport kind                  |
| Unsupported `ActionPattern` for subclass                   | `SemanticError`      | subclass, pattern, supported    |
| Wrong transport type at construction                       | `ConfigurationError` | expected, received              |
| Mid-execute connection loss                                | `ConnectionError`    | raw exception chained via `__cause__` |
| Mid-execute inactivity timeout                             | `TimeoutError`       | partial bytes, elapsed          |
| Abstract method invoked on base                            | `AbstractMethodError`| subclass name                   |
| `aspects` resolution with both None                        | `ConfigurationError` | caller frame                    |

`SemanticError` is intentionally raised at this layer for misuse — it is
*never* caught anywhere in the library, matching `/Users/myron/repos/CLAUDE.md`
guidance.

## Logging

All log output routes through `get_uefi_logger("uefi.transport")`.  Structured
fields emitted on each event:

| Event                | Level  | Fields                                                    |
|----------------------|--------|-----------------------------------------------------------|
| `dispatch.begin`     | DEBUG  | client, command (redacted), action_pattern, logging_pattern |
| `dispatch.complete`  | INFO   | client, status, elapsed_ms                                |
| `dispatch.error`     | ERROR  | client, command (redacted), exception_type, elapsed_ms    |
| `channel.opened`     | DEBUG  | client, channel_id                                        |
| `channel.close.warn` | WARNING| client, channel_id, exception                             |
| `client.close.warn`  | WARNING| client, exception                                         |

`aspects.logging_pattern == FAILURE_ONLY` suppresses `dispatch.complete` on
success but always emits `dispatch.error`.  `SUCCESS_ONLY` suppresses
`dispatch.error` only at INFO — ERROR is always emitted.

## Security Considerations

* Commands and response bodies may contain secrets (e.g., Redfish
  credentials in a `Sessions` POST body).  Log output passes every message
  through a redaction helper that masks values of known secret keys
  (`password`, `token`, `X-Auth-Token`).
* TLS verification is a transport-layer concern; clients never loosen it.
* `UefiHostOsClient` inherits SSH credential-handling discipline from the
  composed `SshAgent`.

## Testing Strategy

### Unit tests (`test_ueficlient_base.py`)

* Abstract methods raise `AbstractMethodError` when called on the base.
* `_check_action_pattern` raises `SemanticError` for each pattern not in
  a contrived `supported_action_patterns`.
* `close()` is idempotent.
* `close()` suppresses channel / transport close exceptions with a WARNING
  log.
* Context-manager protocol closes on exception.

### Unit tests (`test_uefishellclient.py`)

* Construction with `UefiSerialTransport` and `UefiIpmiSolTransport`
  succeeds.
* Construction with any other transport raises `ConfigurationError`.
* `execute` with each supported `ActionPattern`.
* `execute` with `SINGLE_CONNECTED_CALL` raises `SemanticError`.
* `execute` with `DO_UNTIL_CONNECTION_FAILURE` raises `SemanticError`.
* `open_channel(multiplex=True)` raises `SemanticError`.
* Prompt sync uses `aspects.prompt_pattern`.
* Response decoded with `aspects.response_encoding`.

### Unit tests (`test_uefiredfishclient.py`)

* Construction with `UefiRedfishTransport` succeeds; others raise.
* `execute` with each supported `ActionPattern`.
* `execute` with `SINGLE_CONNECTED_CALL` raises `SemanticError`.
* `202`-Accepted path polls TaskMonitor until completion.
* Poll bounded by `aspects.completion_timeout`.
* Multiplex channel shares the underlying HTTP session.

### Unit tests (`test_uefihostosclient.py`)

* Construction with `UefiSshHostTransport` succeeds; others raise.
* `execute` forwards to composed `SshAgent.run_cmd` and returns the
  three-tuple.
* `execute` with `SINGLE_CONNECTED_CALL` raises `SemanticError`.

### Integration tests

* One action-pattern-matrix test per subclass exercising `SINGLE_CALL`,
  `DO_UNTIL_SUCCESS`, and `DO_WHILE_SUCCESS`.
* Fault injection: transport raises `ConnectionError` mid-execute;
  verify client re-raises and closes the channel.
* Coverage target: 95% line coverage across the client package.

## Dependencies on Other Features

* `1-aspects` — `UefiCmdAspects`, `ActionPattern`, `LoggingPattern`,
  `resolve_aspects`, `DEFAULT_CMD_ASPECTS`.
* `1-exceptions` — `UefiError`, `UefiTransportError`.
* `1-logging` — `get_uefi_logger("uefi.transport")`.
* `1-interfaces` — `IUefiTransport`.
* `2-transport-serial`, `2-transport-ipmi-sol`, `2-transport-redfish`,
  `2-transport-ssh-host` — the concrete transports consumed.
* `3-channel` — `UefiChannel` and its subclasses.
* `3-connection` — `UefiConnection`, used by shell / Redfish clients for
  lifecycle management.
