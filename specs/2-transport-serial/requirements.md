# Requirements — Serial Transport

## Introduction

The Serial Transport feature delivers a layer-2 byte-level transport that
reaches a UEFI target's console over a locally-attached serial device
(`/dev/ttyUSB*`, `/dev/tty.usbserial-*`, `COM*`) or a remote network-serial
concentrator exposed as `socket://host:port` or `telnet://host:port`.  It is
the primary pre-boot transport for the library: it works before any operating
system is running, which the Redfish and SSH-to-host transports cannot claim.

The transport implements the `IUefiTransport` protocol defined in
`1-interfaces` and consumes the measured-interop `UefiAspects` /
`UefiCmdAspects` objects defined in `1-aspects`.  It exposes a cooked
`read_until(pattern, timeout)` primitive used by layer-3 channels for UEFI
Shell prompt synchronization, and it normalizes CR / LF / CRLF line endings
per caller preference.  Connection attempts, disconnect recovery, and
terminal-settings restoration are driven entirely by the aspects contract —
there are no hidden hard-coded waits and no behavior-modifying decorators.

This feature has no dependencies outside of layer 1 and is a prerequisite for
`3-client`, `3-connection`, `3-channel`, and any layer-5 feature that wants
pre-boot reach (`5-shell-commands`, `5-variable-services` via Shell,
`5-boot-manager` via Shell, `5-system-reset` via Shell).

## Requirements

### Requirement 1 — Local and Remote Serial Endpoints

**User Story:** As a test author, I want a single transport class that speaks
both local serial ports and TCP-wrapped remote serial concentrators, so that
my tests look identical whether the lab is on my desk or on the other side of
a network serial switch.

#### Acceptance Criteria

1. WHEN the caller constructs `UefiSerialTransport(endpoint="/dev/ttyUSB0", ...)`
   THEN the system SHALL open the named POSIX character device via `pyserial`.
2. WHEN the caller constructs `UefiSerialTransport(endpoint="COM3", ...)` on
   Windows THEN the system SHALL open the named Windows serial device via
   `pyserial`.
3. WHEN the caller constructs `UefiSerialTransport(endpoint="socket://concentrator:7001", ...)`
   THEN the system SHALL open a raw TCP socket to the named host and port.
4. WHEN the caller constructs `UefiSerialTransport(endpoint="telnet://concentrator:7001", ...)`
   THEN the system SHALL open a telnet-framed TCP connection and SHALL strip
   IAC negotiation bytes from the incoming byte stream.
5. IF the caller passes an `endpoint` string with an unrecognized scheme THEN
   the system SHALL raise `ConfigurationError` with `details` naming the
   offending scheme and the set of accepted schemes.

### Requirement 2 — Connection Retry Bounded by Aspects

**User Story:** As a test author, I want connection attempts to obey the
aspects contract I already use for SSH, so that I do not have to learn a new
retry model for serial.

#### Acceptance Criteria

1. WHEN `connect()` is invoked with `aspects.action_pattern ==
   ActionPattern.SINGLE_CALL` THEN the system SHALL attempt the connection
   exactly once and SHALL raise on failure.
2. WHEN `connect()` is invoked with `aspects.action_pattern ==
   ActionPattern.DO_UNTIL_SUCCESS` THEN the system SHALL retry connection
   attempts every `aspects.connection_interval` seconds until either
   succeeding or `aspects.connection_timeout` elapses.
3. IF connection fails with an exception type present in
   `aspects.allowed_connection_exceptions` THEN the system SHALL treat the
   attempt as a soft failure and SHALL retry per the action pattern.
4. IF connection fails with an exception type absent from
   `aspects.allowed_connection_exceptions` THEN the system SHALL re-raise the
   exception immediately without further retries.
5. WHEN `aspects.connection_timeout` elapses before a successful connection
   THEN the system SHALL raise `TimeoutError` with `details` naming the
   endpoint, the elapsed time, the attempt count, and the last caught
   exception.
6. WHEN `aspects.must_connect == True` and a connection failure occurs THEN
   the system SHALL raise `ConnectionError` immediately regardless of action
   pattern.

### Requirement 3 — Baud, Parity, Stop-Bits, and Flow Control

**User Story:** As a test author, I want to configure link-layer parameters
because different silicon vendors ship different default UART settings, so
that my transport works across every board I need to support.

#### Acceptance Criteria

1. WHEN the caller omits `baud` THEN the system SHALL default to 115200.
2. WHEN the caller passes `baud=9600` (or any other integer in the pyserial
   accepted set) THEN the system SHALL configure the link at that rate.
3. WHEN the caller omits `parity` THEN the system SHALL default to `"N"`
   (none).
4. WHEN the caller passes `parity` as one of `"N"`, `"E"`, `"O"`, `"M"`, `"S"`
   THEN the system SHALL configure the link accordingly.
5. WHEN the caller omits `stop_bits` THEN the system SHALL default to `1`.
6. WHEN the caller omits `flow_control` THEN the system SHALL default to
   `"none"`; other accepted values are `"rtscts"`, `"xonxoff"`, and
   `"dsrdtr"`.
7. IF the endpoint scheme is `socket://` or `telnet://` and the caller passes
   link-layer parameters THEN the system SHALL raise `ConfigurationError`
   with `details` indicating link-layer parameters are not configurable over
   network-wrapped serial.

### Requirement 4 — Line-Ending Normalization

**User Story:** As a test author, I want the transport to normalize the mix
of CR, LF, and CRLF that different firmwares emit, so that my prompt regexes
do not need per-vendor variants.

#### Acceptance Criteria

1. WHEN the caller omits `line_ending_normalization` THEN the system SHALL
   default to `"crlf_to_lf"`.
2. WHEN `line_ending_normalization == "crlf_to_lf"` THEN the system SHALL
   collapse every `b"\r\n"` read from the device into `b"\n"`.
3. WHEN `line_ending_normalization == "cr_to_lf"` THEN the system SHALL
   convert every standalone `b"\r"` to `b"\n"`.
4. WHEN `line_ending_normalization == "none"` THEN the system SHALL pass
   incoming bytes through unchanged.
5. WHEN the caller sends data via `write(data)` THEN the system SHALL NOT
   normalize outbound bytes — send is literal.

### Requirement 5 — Cooked read_until

**User Story:** As a layer-3 channel implementer, I want a `read_until`
primitive that either delivers the matched bytes or reports exactly how much
it saw before giving up, so that I can surface useful diagnostics on timeout.

#### Acceptance Criteria

1. WHEN `read_until(pattern, timeout)` is called with `pattern` as `bytes`
   THEN the system SHALL return the accumulated bytes from the start of the
   call up to and including the first occurrence of `pattern`.
2. WHEN `read_until(pattern, timeout)` is called with `pattern` as a compiled
   `re.Pattern[bytes]` THEN the system SHALL return the accumulated bytes up
   to and including the end of the first match.
3. WHEN `pattern` is not observed within `timeout` seconds THEN the system
   SHALL raise `TimeoutError` whose instance has a `.partial: bytes`
   attribute containing every byte collected during the call.
4. WHILE `read_until` is blocked waiting for data the system SHALL honor an
   inactivity sub-timeout of `aspects.inactivity_interval` and SHALL log a
   `DEBUG`-level progress line every `aspects.inactivity_interval` seconds
   when no bytes arrive.
5. IF the underlying connection drops mid-read THEN the system SHALL raise
   `ConnectionError` with `details` naming the endpoint and the number of
   bytes observed before the drop.

### Requirement 6 — Context Manager and Terminal Settings Restoration

**User Story:** As a test author, I want to use the transport in a `with`
block and have the serial device's terminal settings restored on exit, so
that my tests do not leave the device in a mis-configured state for the next
session.

#### Acceptance Criteria

1. WHEN the transport is used as `with UefiSerialTransport(...) as t:` THEN
   the system SHALL call `connect()` on entry and `close()` on exit.
2. WHEN `close()` is invoked on a local-serial endpoint THEN the system
   SHALL restore the `termios` attributes captured at `connect()` time.
3. WHEN `close()` is invoked on a `socket://` or `telnet://` endpoint THEN
   the system SHALL cleanly shut down the socket (`SHUT_RDWR` then `close`).
4. WHEN `close()` is invoked on an already-closed transport THEN the system
   SHALL be a no-op and SHALL NOT raise.
5. IF `close()` encounters an exception while restoring terminal settings
   THEN the system SHALL log it at `WARNING` under channel
   `uefi.transport.serial` and SHALL NOT re-raise.

### Requirement 7 — Aspects=None Passthrough

**User Story:** As a caller of `connect()` and `read_until()`, I want to omit
`aspects` and have the transport fall back to its instance default, so that
call sites stay concise when I have no per-call tweak.

#### Acceptance Criteria

1. WHEN `connect(aspects=None)` is invoked THEN the system SHALL use the
   aspects object supplied at construction time.
2. WHEN `read_until(pattern, timeout, aspects=None)` is invoked THEN the
   system SHALL use the aspects object supplied at construction time.
3. IF no aspects object was supplied at construction time and `aspects=None`
   is passed to a public method THEN the system SHALL raise
   `ConfigurationError`.

### Requirement 8 — Action-Pattern Coverage

**User Story:** As a test author, I want every action pattern I use
elsewhere in the stack to also work for the serial transport, so that I can
pick the same retry semantics across all transports.

#### Acceptance Criteria

1. WHEN `connect(aspects=ActionPattern.SINGLE_CALL)` is invoked THEN the
   system SHALL perform exactly one attempt.
2. WHEN `connect(aspects=ActionPattern.SINGLE_CONNECTED_CALL)` is invoked
   THEN the system SHALL treat an already-connected transport as a no-op
   success and SHALL otherwise perform exactly one connection attempt.
3. WHEN `connect(aspects=ActionPattern.DO_UNTIL_SUCCESS)` is invoked THEN the
   system SHALL retry until success or `connection_timeout` elapses.
4. WHEN `connect(aspects=ActionPattern.DO_UNTIL_CONNECTION_FAILURE)` is
   invoked on an already-connected transport THEN the system SHALL monitor
   connection liveness every `aspects.connection_interval` until a failure
   is observed, then SHALL raise the first observed exception.
5. IF `aspects.action_pattern == ActionPattern.DO_WHILE_SUCCESS` THEN the
   system SHALL raise `SemanticError` because that pattern is not meaningful
   for connection establishment.

### Requirement 9 — Implements IUefiTransport

**User Story:** As a layer-3 integrator, I want the serial transport to be
structurally interchangeable with every other layer-2 transport, so that
layer 3 does not need transport-specific branches.

#### Acceptance Criteria

1. WHEN a `UefiSerialTransport` instance is checked against `IUefiTransport`
   via `isinstance` THEN the system SHALL return `True`.
2. WHEN `IUefiTransport.connect`, `disconnect`, `send`, `recv`, `recv_until`,
   `is_connected` are invoked on a `UefiSerialTransport` THEN the system
   SHALL honor the method signatures declared on `IUefiTransport`.
3. WHEN `IUefiTransport.transport_kind` is read THEN the system SHALL return
   the string literal `"serial"`.

### Requirement 10 — Diagnostics and Logging

**User Story:** As an operator triaging a failing test run, I want every
connect, disconnect, read timeout, and terminal-settings restoration to emit
a structured log entry under a predictable channel, so that I can grep the
run log without scraping stack traces.

#### Acceptance Criteria

1. WHEN `connect()` begins THEN the system SHALL emit an `INFO` log to
   channel `uefi.transport.serial` containing the endpoint and baud.
2. WHEN `connect()` succeeds THEN the system SHALL emit an `INFO` log with
   the attempt count and total elapsed time.
3. WHEN `connect()` retries after a soft failure THEN the system SHALL emit a
   `WARNING` log identifying the caught exception.
4. WHEN `read_until()` raises `TimeoutError` THEN the system SHALL emit a
   `WARNING` log with the pattern, timeout, and the length of `.partial`.
5. WHEN `close()` completes THEN the system SHALL emit a `DEBUG` log.
