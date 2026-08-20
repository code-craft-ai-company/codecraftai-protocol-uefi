# Requirements — UefiChannel

## Introduction

`UefiChannel` represents a single logical command channel between the library
and the target UEFI entity.  For shell-style transports (serial, IPMI-SOL)
it is a PTY-like pipe; for Redfish it is a dedicated `requests.Session`
with scoped headers; for SSH-to-host it is an `SshChannel` forwarded
through composition.  Every subclass exposes an identical public API —
`send`, `recv`, `recv_until`, `close` — so layer-4 consumers never branch
on transport kind.

Channels are short-lived by design.  The layer-4 action-pattern engine
(`4-base._run_cmd`) opens a channel, issues the command, reads the
response, and closes the channel per `ActionPattern`.  Multiplexing onto a
single transport requires explicit opt-in at client construction time
(see `3-client`).

The channel subclasses re-use buffering and prompt-detection logic already
proven in `SshChannel`
(`/Users/myron/repos/codecraftai-protocol-ssh/source/packages/ccai/protocols/ssh/sshconnection.py`
lines 14–159) and the serial read-until primitives from `2-transport-serial`.

## Requirements

### Requirement 1 — Unified API Across Subclasses

**User Story:** As a layer-4 consumer, I want `send`, `recv`, `recv_until`,
and `close` to have identical signatures across every channel subclass,
so that my dispatch code is transport-agnostic.

#### Acceptance Criteria

1. WHEN any `UefiChannel` subclass is inspected THEN the system SHALL
   expose `send(data: bytes) -> int`, `recv(size: int, timeout: float)
   -> bytes`, `recv_until(pattern, timeout: float) -> bytes`, and
   `close() -> None` with identical signatures.
2. WHEN `send(b"")` is called THEN the system SHALL return `0` without
   touching the transport.
3. WHEN `recv(size=0, ...)` is called THEN the system SHALL return `b""`
   without touching the transport.

### Requirement 2 — `recv_until` Pattern Types

**User Story:** As a test author, I want to use either literal bytes or
a compiled regex as the `recv_until` terminator, so that prompt
detection handles both simple and vendor-specific prompts.

#### Acceptance Criteria

1. WHEN `recv_until(pattern=b"Shell> ", timeout=t)` is called THEN the
   system SHALL treat the pattern as a literal byte sequence and return
   when that sequence is observed in the cumulative buffer.
2. WHEN `recv_until(pattern=re.compile(rb"Shell[0-9]*> "), timeout=t)`
   is called THEN the system SHALL apply `pattern.search` to the
   cumulative buffer and return when a match is found.
3. IF `pattern` is neither `bytes` nor a compiled `re.Pattern[bytes]`
   THEN the system SHALL raise `ConfigurationError`.
4. WHEN `recv_until` returns THEN the returned bytes SHALL include the
   terminator and any bytes read after it up to the end of the last
   `recv` chunk.

### Requirement 3 — `recv_until` Timeout Behavior

**User Story:** As a test author, I want `recv_until` to bound its
wait by a caller-supplied timeout and surface any partial bytes
collected, so that diagnostics are available on prompt failures.

#### Acceptance Criteria

1. WHEN the cumulative wait time reaches `timeout` without the pattern
   being seen THEN the system SHALL raise `TimeoutError`.
2. WHEN the `TimeoutError` is raised THEN the exception SHALL carry the
   partial bytes collected on a `.partial` attribute (or in
   `args[1]`) so callers can inspect what arrived.
3. WHERE `aspects.inactivity_interval` is known to the channel THEN the
   system SHALL sleep that interval between polls when the transport
   returns zero bytes.
4. IF `timeout <= 0` THEN the system SHALL raise `ConfigurationError`.

### Requirement 4 — Close Never Raises

**User Story:** As a test author, I want `channel.close()` in a
`finally` block to never mask the original exception, so that test
failure diagnostics remain accurate.

#### Acceptance Criteria

1. WHEN `close()` is called for the first time THEN the system SHALL
   tear down the underlying framing object (PTY handle, HTTP session,
   SSH sub-channel).
2. WHEN `close()` is called a second or subsequent time THEN the
   system SHALL return without error.
3. IF the underlying close raises an exception THEN the system SHALL
   log the exception at `WARNING` under `uefi.transport` and suppress
   it.
4. WHEN `close()` is called on an open channel THEN the system SHALL
   set `channel.closed` to `True` before returning.

### Requirement 5 — `UefiShellChannel` Send/Recv Semantics

**User Story:** As a layer-4 consumer using a serial transport, I want
the shell channel to frame bytes exactly as written (no LF/CRLF
normalization on `send`), so that commands reach the target byte-for-byte.

#### Acceptance Criteria

1. WHEN `UefiShellChannel.send(data)` is called THEN the system SHALL
   write `data` to the underlying serial/IPMI-SOL transport without
   normalization.
2. WHEN `UefiShellChannel.recv(size, timeout)` is called THEN the
   system SHALL return up to `size` bytes, blocking at most `timeout`
   seconds.
3. WHERE the transport returns CR-only or CRLF line endings THEN the
   system SHALL preserve them exactly.

### Requirement 6 — `UefiRedfishChannel` Scoped Session

**User Story:** As a layer-4 consumer using the Redfish transport, I
want each channel to carry its own headers (auth token, OData-Version)
without leaking them to other channels on the same transport.

#### Acceptance Criteria

1. WHEN `UefiRedfishChannel` is constructed THEN the system SHALL
   attach a dedicated `requests.Session` or a header-scoped adapter
   over a shared session.
2. WHEN a header is set on this channel THEN the system SHALL NOT
   mutate headers on any other channel.
3. WHEN the channel is closed THEN the system SHALL close the scoped
   session (if dedicated) or detach the header scope (if shared).
4. WHEN `send` is invoked with an HTTP verb/path/body tuple THEN the
   system SHALL dispatch through the scoped session and return the raw
   response bytes.

### Requirement 7 — `UefiHostOsChannel` Forwarded SshChannel

**User Story:** As a layer-4 consumer using the SSH-to-host transport,
I want the host-OS channel to be a thin wrapper around `SshChannel`, so
that I inherit the battle-tested buffering of the SSH library.

#### Acceptance Criteria

1. WHEN `UefiHostOsChannel` is constructed THEN the system SHALL
   obtain an `SshChannel` from the composed `SshAgent` and hold it by
   reference.
2. WHEN `send`, `recv`, or `recv_until` is called THEN the system SHALL
   forward to the wrapped `SshChannel`'s analogous method.
3. WHEN `close()` is called THEN the system SHALL close the wrapped
   `SshChannel` and suppress any `Exception` with a WARNING log.

### Requirement 8 — Chunk Size From Aspects

**User Story:** As a test author running over a slow transport, I
want to control the `recv` chunk size per-call, so that buffering
tradeoffs match my target's latency.

#### Acceptance Criteria

1. WHEN `recv(size=N, ...)` is called THEN the system SHALL read at most
   `N` bytes from the transport in a single call.
2. WHEN `recv_until(pattern, timeout)` is called without a chunk size
   override THEN the system SHALL read chunks of
   `aspects.inactivity_chunk_size` (default 4096) per poll.
3. IF `size <= 0` for `recv` THEN the system SHALL raise
   `ConfigurationError`.

### Requirement 9 — Inactivity Timeout Flow

**User Story:** As a test author, I want `recv_until` to honor the
inactivity-timeout values carried on aspects, so that channels never
hang indefinitely.

#### Acceptance Criteria

1. WHEN the channel's aspects provide `inactivity_timeout` THEN that
   value SHALL be used as the default wait-bound when the caller does
   not pass `timeout` explicitly.
2. WHEN `aspects.inactivity_interval` is set THEN the system SHALL
   sleep that interval between zero-byte polls inside `recv_until`.
3. WHEN `recv_until` exceeds `aspects.inactivity_timeout` even when
   some bytes were read THEN the system SHALL still raise
   `TimeoutError`.

### Requirement 10 — Channel Closed State Is Observable

**User Story:** As a layer-4 consumer, I want a reliable way to tell
whether a channel is closed without calling `close()`, so that I can
gate send/recv operations accordingly.

#### Acceptance Criteria

1. WHEN `channel.closed` is read THEN the system SHALL return a `bool`
   without performing I/O.
2. WHEN `send` is called on a closed channel THEN the system SHALL
   raise `ConnectionError`.
3. WHEN `recv` or `recv_until` is called on a closed channel with an
   empty buffer THEN the system SHALL return `b""` (for `recv`) or
   raise `ConnectionError` (for `recv_until`).

### Requirement 11 — Logging On Abnormal Events

**User Story:** As an operator, I want abnormal channel events to be
logged to `uefi.transport`, so that I can trace channel failures.

#### Acceptance Criteria

1. WHEN `close()` suppresses an exception THEN the system SHALL emit a
   WARNING log at `uefi.transport` with the exception repr and the
   channel id.
2. WHEN `recv_until` times out THEN the system SHALL emit a WARNING
   log with the partial byte count and elapsed time before raising.
3. WHEN `send` fails with a connection error THEN the system SHALL emit
   an ERROR log and re-raise.
