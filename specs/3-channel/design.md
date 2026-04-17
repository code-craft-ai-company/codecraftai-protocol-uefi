# Design — UefiChannel

## Overview

`UefiChannel` is the unified command-channel abstraction consumed by layer-4
`UefiBase._run_cmd` and by `UefiClient._execute_impl` in layer 3.  Its
purpose is to turn a transport's native read/write primitives into a
protocol-agnostic `send` / `recv` / `recv_until` / `close` surface with
identical signatures across three very different transports.

The class hierarchy is:

* `UefiChannel` — abstract base; defines the public API and shared
  `recv_until` loop with pattern-or-regex detection.
* `UefiShellChannel` — wraps a serial or IPMI-SOL byte stream.
* `UefiRedfishChannel` — wraps a scoped `requests.Session` (or a shared
  session with a scoped-header adapter).
* `UefiHostOsChannel` — wraps an `SshChannel` from the composed
  `SshAgent`; forwards every method to the underlying SSH channel
  (`sshconnection.py:14–159`).

Close never raises.  Underlying-close exceptions are logged at WARNING
on the `uefi.transport` channel and swallowed, per requirement.

## Architecture

```
automojo.protocols.uefi.channel/
    __init__.py                # re-exports public surface
    uefichannel.py             # UefiChannel abstract base
    uefishellchannel.py        # Shell (serial / IPMI-SOL)
    uefiredfishchannel.py      # Redfish
    uefihostoschannel.py       # SSH-to-host
```

Dependency direction:

```
uefishellchannel.py  ─┐
uefiredfishchannel.py ─┼─▶ uefichannel.py ──▶ automojo.protocols.uefi.aspects
uefihostoschannel.py ─┘                    ──▶ automojo.protocols.uefi.logging
                                           ──▶ automojo.protocols.uefi.exceptions
```

## Components and Interfaces

### `UefiChannel` (uefichannel.py)

```python
class UefiChannel:
    """
        Abstract single logical command channel.  Subclasses implement the
        transport-specific primitives ``_send_impl``, ``_recv_impl``,
        ``_close_impl``.  The shared ``recv_until`` polling loop lives
        here and consumes aspects timeouts.
    """

    def __init__(
        self,
        aspects: UefiCmdAspects = DEFAULT_CMD_ASPECTS,
        logger: Optional[logging.Logger] = None,
        channel_id: Optional[str] = None,
    ) -> None:
        self._aspects = aspects
        self._logger = logger if logger is not None else get_uefi_logger("uefi.transport")
        self._channel_id = channel_id if channel_id is not None else uuid.uuid4().hex[:8]
        self._closed = False
        self._buffer = bytearray()
        return

    @property
    def aspects(self) -> UefiCmdAspects:
        return self._aspects

    @property
    def channel_id(self) -> str:
        return self._channel_id

    @property
    def closed(self) -> bool:
        return self._closed

    def send(self, data: bytes) -> int:
        if self._closed:
            err_msg = f"Cannot send on closed channel id={self._channel_id}."
            raise ConnectionError(err_msg)
        if len(data) == 0:
            return 0
        try:
            written = self._send_impl(data)
        except BaseException as cerr:
            self._logger.error(
                "uefi.transport: send failed channel=%s err=%r",
                self._channel_id, cerr,
            )
            raise
        return written

    def recv(self, size: int, timeout: float) -> bytes:
        if size <= 0:
            if size == 0:
                return b""
            err_msg = f"recv: size must be > 0; got {size}."
            raise ConfigurationError(err_msg) from None
        if self._closed and len(self._buffer) == 0:
            return b""
        if len(self._buffer) > 0:
            chunk = bytes(self._buffer[:size])
            del self._buffer[:size]
            return chunk
        chunk = self._recv_impl(size=size, timeout=timeout)
        return chunk

    def recv_until(
        self,
        pattern: Union[bytes, re.Pattern[bytes]],
        timeout: Optional[float] = None,
    ) -> bytes:
        effective_timeout = timeout if timeout is not None else self._aspects.inactivity_timeout
        if effective_timeout <= 0:
            err_msg = f"recv_until: timeout must be > 0; got {effective_timeout}."
            raise ConfigurationError(err_msg) from None

        matcher = self._compile_matcher(pattern)

        begin = time.monotonic()
        deadline = begin + effective_timeout
        chunk_size = getattr(self._aspects, "inactivity_chunk_size", 4096)
        inactivity_interval = self._aspects.inactivity_interval

        while True:
            match_end = matcher(self._buffer)
            if match_end is not None:
                collected = bytes(self._buffer[:match_end])
                del self._buffer[:match_end]
                return collected

            if self._closed:
                err_msg = (
                    f"recv_until: channel closed before pattern observed; id={self._channel_id}; "
                    f"partial_bytes={len(self._buffer)}."
                )
                raise ConnectionError(err_msg) from None

            now = time.monotonic()
            if now >= deadline:
                elapsed = now - begin
                partial = bytes(self._buffer)
                self._logger.warning(
                    "uefi.transport: recv_until timeout channel=%s elapsed=%.2fs partial_bytes=%d",
                    self._channel_id, elapsed, len(partial),
                )
                toerr = TimeoutError(
                    f"recv_until: pattern not observed in {elapsed:.2f}s; partial={len(partial)} bytes."
                )
                toerr.partial = partial
                raise toerr

            remaining = deadline - now
            chunk = self._recv_impl(size=chunk_size, timeout=min(remaining, inactivity_interval))
            if len(chunk) == 0:
                time.sleep(inactivity_interval)
                continue
            self._buffer.extend(chunk)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._close_impl()
        except BaseException as cerr:
            self._logger.warning(
                "uefi.transport: channel close suppressed channel=%s err=%r",
                self._channel_id, cerr,
            )
        return

    def _compile_matcher(self, pattern) -> Callable[[bytearray], Optional[int]]:
        if isinstance(pattern, (bytes, bytearray, memoryview)):
            needle = bytes(pattern)
            def literal_matcher(buf: bytearray) -> Optional[int]:
                idx = buf.find(needle)
                if idx < 0:
                    return None
                return idx + len(needle)
            return literal_matcher
        if isinstance(pattern, re.Pattern):
            if not isinstance(pattern.pattern, (bytes, bytearray)):
                err_msg = "recv_until regex pattern must be bytes-typed."
                raise ConfigurationError(err_msg) from None
            def regex_matcher(buf: bytearray) -> Optional[int]:
                m = pattern.search(bytes(buf))
                if m is None:
                    return None
                return m.end()
            return regex_matcher
        err_msg = f"recv_until: pattern must be bytes or re.Pattern[bytes]; got {type(pattern).__name__}."
        raise ConfigurationError(err_msg) from None

    def _send_impl(self, data: bytes) -> int:
        raise AbstractMethodError(
            "UefiChannel._send_impl must be overloaded by '%s'." % type(self).__name__
        ) from None

    def _recv_impl(self, size: int, timeout: float) -> bytes:
        raise AbstractMethodError(
            "UefiChannel._recv_impl must be overloaded by '%s'." % type(self).__name__
        ) from None

    def _close_impl(self) -> None:
        raise AbstractMethodError(
            "UefiChannel._close_impl must be overloaded by '%s'." % type(self).__name__
        ) from None
```

### `UefiShellChannel` (uefishellchannel.py)

```python
class UefiShellChannel(UefiChannel):
    """
        Byte-stream channel for serial and IPMI-SOL transports.
        Pass-through send/recv, no CR/LF normalization.
    """

    def __init__(self, transport: IUefiTransport, aspects: UefiCmdAspects = DEFAULT_CMD_ASPECTS, **kw) -> None:
        super().__init__(aspects=aspects, **kw)
        self._transport = transport
        return

    def _send_impl(self, data: bytes) -> int:
        written = self._transport.write(data)
        return written

    def _recv_impl(self, size: int, timeout: float) -> bytes:
        chunk = self._transport.read(size=size, timeout=timeout)
        return chunk

    def _close_impl(self) -> None:
        # Channel close does NOT close the transport.  That is a client
        # responsibility.  Only clear our buffer.
        self._buffer = bytearray()
        return
```

### `UefiRedfishChannel` (uefiredfishchannel.py)

```python
class UefiRedfishChannel(UefiChannel):
    """
        Redfish-scoped channel carrying a dedicated requests.Session (or
        a header-scoped adapter over a shared session).  Implements an
        HTTP-oriented send interface (``send_http``) in addition to the
        bytes-oriented ``send``/``recv`` for protocol uniformity.
    """

    def __init__(self, transport: "UefiRedfishTransport", aspects: UefiCmdAspects = DEFAULT_CMD_ASPECTS,
                 shared_session: bool = False, **kw) -> None:
        super().__init__(aspects=aspects, **kw)
        self._transport = transport
        self._shared_session = shared_session
        if shared_session:
            self._session = transport.shared_session
            self._headers: dict[str, str] = dict(transport.default_headers)
        else:
            self._session = transport.build_session()
            self._headers = dict(transport.default_headers)
        self._last_response_bytes: bytes = b""
        return

    def send_http(self, verb: str, path: str, json_body=None, aspects: Optional[UefiCmdAspects] = None):
        effective = resolve_aspects(aspects, self._aspects)
        url = self._transport.absolute_url(path)
        response = self._session.request(
            method=verb, url=url, json=json_body,
            headers=self._headers, timeout=effective.completion_timeout,
            verify=self._transport.verify_tls,
        )
        self._last_response_bytes = response.content
        return response

    def poll_task(self, task_uri: str, aspects: UefiCmdAspects):
        # Polling bounded by aspects.completion_timeout and
        # aspects.completion_interval; returns the final task dict.
        ...

    def _send_impl(self, data: bytes) -> int:
        # Bytes-oriented fallback: send a raw POST to the default
        # Redfish action URL with ``data`` as the body.
        raise SemanticError(
            "UefiRedfishChannel does not support byte-level send; use send_http instead."
        ) from None

    def _recv_impl(self, size: int, timeout: float) -> bytes:
        if len(self._last_response_bytes) == 0:
            return b""
        chunk = self._last_response_bytes[:size]
        self._last_response_bytes = self._last_response_bytes[size:]
        return chunk

    def _close_impl(self) -> None:
        if not self._shared_session:
            try:
                self._session.close()
            except BaseException:
                raise
        return
```

### `UefiHostOsChannel` (uefihostoschannel.py)

```python
class UefiHostOsChannel(UefiChannel):
    """
        Thin forwarder over an ``SshChannel`` obtained from the composed
        ``SshAgent``.  Inherits the battle-tested buffering of
        ``automojo-protocol-ssh`` (sshconnection.py:14–159).
    """

    def __init__(self, transport: "UefiSshHostTransport", aspects: UefiCmdAspects = DEFAULT_CMD_ASPECTS,
                 multiplex: bool = False, **kw) -> None:
        super().__init__(aspects=aspects, **kw)
        self._transport = transport
        self._multiplex = multiplex
        self._ssh_channel: Optional[SshChannel] = None
        return

    def ensure_open(self) -> None:
        if self._ssh_channel is None:
            self._ssh_channel = self._transport.ssh_agent._create_client().connection.open_channel("session")
        return

    def _send_impl(self, data: bytes) -> int:
        self.ensure_open()
        self._ssh_channel.send(data)
        return len(data)

    def _recv_impl(self, size: int, timeout: float) -> bytes:
        self.ensure_open()
        chunk = self._ssh_channel.recv(size=size, timeout=timeout)
        return chunk

    def _close_impl(self) -> None:
        if self._ssh_channel is not None:
            try:
                self._ssh_channel.close()
            except BaseException:
                raise
            self._ssh_channel = None
        return
```

## Data Models

The channel classes carry no persistent data.  `self._buffer` is a
`bytearray` holding unread bytes between `_recv_impl` calls.  `channel_id`
is an 8-character hex tag derived from a `uuid4` for log correlation.
All instance variables are initialized in `__init__` per
`/Users/myron/repos/CLAUDE.md` standards.

## Measured Interop Contract

This feature consumes the following `UefiCmdAspects` fields:

* `inactivity_timeout` — default bound on `recv_until` when caller passes
  `timeout=None`.
* `inactivity_interval` — sleep-between-polls inside `recv_until`.
* `inactivity_chunk_size` (new field; defaulted to 4096) — transport read
  chunk size.
* `response_encoding` — **not consumed** directly here; decoding is the
  client's responsibility.
* `logger` — not used (channels use their own `uefi.transport` logger).

The feature does NOT consume `completion_timeout`, `completion_interval`,
`connection_timeout`, `connection_interval`,
`allowed_connection_exceptions`, `must_connect`, `action_pattern`,
`logging_pattern`, `expected_status`, or `prompt_pattern`.  Those flow
through aspects to higher layers (client, base).

`ActionPattern`s supported: none.  Channels are single-issue — they do
not loop.  Action patterns are applied by layer-4
`UefiBase._run_cmd` which opens and closes channels per iteration.

## Error Handling

| Condition                                                  | Exception            | Included in `details`           |
|------------------------------------------------------------|----------------------|---------------------------------|
| Send on closed channel                                     | `ConnectionError`    | channel_id                      |
| `recv_until` `timeout <= 0`                                | `ConfigurationError` | value                           |
| `recv` `size <= 0` (and not 0)                             | `ConfigurationError` | value                           |
| `recv_until` pattern not bytes or `re.Pattern[bytes]`      | `ConfigurationError` | received type                   |
| `recv_until` deadline exceeded                             | `TimeoutError`       | partial bytes on `.partial`     |
| `recv_until` channel closed before pattern                 | `ConnectionError`    | channel_id, partial byte count  |
| Abstract method invoked on base                            | `AbstractMethodError`| subclass name                   |
| `_close_impl` raises                                       | suppressed + WARNING | channel_id, exception           |
| `send` raises on underlying transport                      | passthrough + ERROR  | channel_id                      |
| `UefiRedfishChannel._send_impl` invoked                    | `SemanticError`      | guidance to use `send_http`     |

## Logging

Channels emit to `get_uefi_logger("uefi.transport")`:

| Event                  | Level   | Fields                                         |
|------------------------|---------|------------------------------------------------|
| channel.send.error     | ERROR   | channel_id, exception                          |
| channel.recv_until.timeout | WARNING | channel_id, elapsed, partial_bytes         |
| channel.close.suppressed   | WARNING | channel_id, exception                      |

Success-case send/recv is NOT logged — the volume would be prohibitive
and layer-4 already logs command-level results.

## Security Considerations

* Channels do not hold credentials themselves; credentials live on the
  transport/session they wrap.  Channel `__repr__` shows only
  `channel_id`, `closed`, and `aspects` reference — never credential
  material.
* Buffered bytes may contain secrets (a `Sessions` response includes an
  auth token).  The channel's logging never dumps buffer contents; only
  lengths and counts.

## Testing Strategy

### Unit tests (`test_uefichannel_base.py`)

* `send(b"")` returns `0` without touching transport.
* `recv(0, t)` returns `b""` without touching transport.
* `recv` reads from internal buffer before transport when buffer is
  non-empty.
* `recv_until` with literal bytes returns when pattern observed;
  returned bytes include the pattern.
* `recv_until` with a compiled regex returns on match.
* `recv_until` raises `ConfigurationError` for `timeout<=0`.
* `recv_until` raises `ConfigurationError` for non-bytes / non-regex
  pattern.
* `recv_until` raises `TimeoutError` with `.partial` attribute on
  deadline expiry.
* `close()` is idempotent.
* `close()` suppresses underlying `_close_impl` exceptions with a
  WARNING log.
* Abstract methods raise `AbstractMethodError` when called on the base.

### Unit tests (`test_uefishellchannel.py`)

* `send` passes bytes through without CR/LF normalization.
* `recv` returns exactly what the fake serial transport returned.
* Close clears buffer but does not close the transport.

### Unit tests (`test_uefiredfishchannel.py`)

* Dedicated-session mode closes its session on `close()`.
* Shared-session mode detaches headers without closing the session.
* Headers set on one channel do not leak to another channel.
* `send_http` uses `aspects.completion_timeout` as the request timeout.
* `_send_impl(b"...")` raises `SemanticError`.

### Unit tests (`test_uefihostoschannel.py`)

* `send`/`recv` forward to the wrapped `SshChannel`.
* `close()` closes the wrapped `SshChannel` and suppresses any raise
  with a WARNING log.
* `ensure_open` opens the sub-channel exactly once on first use.

### Integration tests

* Prompt detection over a `pty`-backed fake serial — `Shell>` terminator
  observed after streamed response.
* Inactivity-timeout test using a fake transport that stalls forever —
  expect `TimeoutError` with `.partial`.
* Large-response streaming under `UefiHostOsChannel` — verify 1 MiB of
  output arrives without truncation.
* Coverage target: 95% line coverage.

## Dependencies on Other Features

* `1-aspects` — `UefiCmdAspects`, `resolve_aspects`,
  `DEFAULT_CMD_ASPECTS`.
* `1-exceptions` — `UefiError`, `AbstractMethodError`.
* `1-interfaces` — `IUefiTransport`.
* `1-logging` — `get_uefi_logger("uefi.transport")`.
* `2-transport-serial`, `2-transport-ipmi-sol`, `2-transport-redfish`,
  `2-transport-ssh-host` — the concrete transports wrapped.
* `3-connection` — peer feature; not imported, but the connection's
  lifecycle is the precondition for a channel being usable.
