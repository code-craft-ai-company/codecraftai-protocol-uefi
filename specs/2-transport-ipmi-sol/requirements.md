# Requirements — IPMI-SOL Transport

## Introduction

The IPMI-SOL Transport feature delivers a layer-2 byte-level transport that
reaches a UEFI target's console over IPMI Serial-Over-LAN.  It is the
out-of-band equivalent of the local serial transport: once the SOL payload
is activated, bytes flow to/from the target's physical UART just as they
would over a directly-attached cable, but the wire is IPMI over LAN rather
than an RS-232 pair or a TCP serial concentrator.

The transport implements the `IUefiTransport` protocol defined in
`1-interfaces`, consumes `UefiAspects` / `UefiCmdAspects` from `1-aspects`,
and reuses the read-until / prompt-detection machinery of
`2-transport-serial` via *composition* — a `UefiSerialTransport`-style
"byte pipe" role is presented by a session object wrapping either `pyghmi`
or a `subprocess`-driven `ipmitool sol activate`.  No byte-level code is
reimplemented here; this feature is primarily the SOL *session lifecycle*
and the `ipmitool`/`pyghmi` abstraction layer.

This feature has dependencies on `1-aspects`, `1-interfaces`,
`1-exceptions`, `1-credentials`, and `2-transport-serial` (for the
read-until stack reuse).  It is pre-boot capable: like the serial
transport, IPMI-SOL reaches the target's console before any OS is running.

## Requirements

### Requirement 1 — Backend Selection: pyghmi vs. ipmitool

**User Story:** As a test author, I want the transport to pick the best
available SOL backend without me having to install both and choose, so that
my test runs work in minimal CI images and in rich developer laptops alike.

#### Acceptance Criteria

1. WHEN the caller constructs
   `UefiIpmiSolTransport(ipmi_session=<pyghmi session>, ...)` THEN the
   system SHALL use the `pyghmi`-based backend.
2. WHEN the caller constructs
   `UefiIpmiSolTransport(credential=<UefiIpmiCredential>, ...)` without a
   `pyghmi` session THEN the system SHALL use the `ipmitool`-subprocess
   backend.
3. IF both `ipmi_session` and `credential` are passed THEN the system SHALL
   raise `ConfigurationError` with `details` naming both fields and
   indicating only one is permitted.
4. IF neither is passed THEN the system SHALL raise `ConfigurationError`.
5. WHEN the `ipmitool`-backend is selected and the `ipmitool` binary is
   not on `PATH` THEN `connect()` SHALL raise `UefiTransportError` with
   `details` including the `PATH` searched.

### Requirement 2 — Evict-Existing Session Policy

**User Story:** As a test author, I want fine control over whether my
transport kicks an existing SOL session off the target, so that I do not
accidentally evict a colleague who is already debugging.

#### Acceptance Criteria

1. WHEN the caller omits `evict_existing` THEN the system SHALL default to
   `False`.
2. WHEN `evict_existing == False` and the target reports an existing SOL
   session at `connect()` THEN the system SHALL raise
   `UefiTransportError` with `details` identifying the current session
   holder (user / source IP) if IPMI reports them.
3. WHEN `evict_existing == True` and an existing session is reported THEN
   the system SHALL issue `sol deactivate` first and THEN attempt to
   activate.
4. IF `sol deactivate` returns a `Deactivation failed` error THEN the
   system SHALL raise `UefiTransportError` whose `details` contain the raw
   tool output and SHALL NOT retry.

### Requirement 3 — Connection Retry Bounded by Aspects

**User Story:** As a test author, I want SOL connection retries to obey the
aspects contract, so that BMC boot-time or transient network failures do
not immediately fail my test.

#### Acceptance Criteria

1. WHEN `connect()` is invoked with `aspects.action_pattern ==
   ActionPattern.DO_UNTIL_SUCCESS` THEN the system SHALL retry SOL
   activation every `aspects.connection_interval` until success or
   `aspects.connection_timeout` elapses.
2. IF a soft failure occurs whose exception type is in
   `aspects.allowed_connection_exceptions` THEN the system SHALL retry per
   the action pattern.
3. IF a soft failure occurs whose exception type is not in
   `aspects.allowed_connection_exceptions` THEN the system SHALL re-raise
   immediately.
4. WHEN `aspects.must_connect == True` THEN a connection failure SHALL
   raise `ConnectionError` immediately regardless of action pattern.
5. IF `aspects.action_pattern == ActionPattern.DO_WHILE_SUCCESS` THEN
   `connect()` SHALL raise `SemanticError`.
6. WHEN `aspects.action_pattern == ActionPattern.SINGLE_CONNECTED_CALL` and
   the transport is already connected THEN `connect()` SHALL return
   without re-activating.

### Requirement 4 — Shared Read-Until Behavior With Serial Transport

**User Story:** As a prompt-synchronizing channel implementer, I want
`read_until` semantics identical to the serial transport, so that I do not
branch on transport-kind when matching shell prompts.

#### Acceptance Criteria

1. WHEN `read_until(pattern, timeout, aspects=None)` is invoked THEN the
   system SHALL delegate to the same `read_until` loop used by
   `UefiSerialTransport` (imported from `2-transport-serial`).
2. WHEN `pattern` is not observed within `timeout` THEN the system SHALL
   raise `TimeoutError` whose instance has `.partial: bytes` containing
   every byte observed during the call.
3. WHEN line-ending normalization is configured THEN the system SHALL apply
   it identically to the serial transport.
4. IF the underlying SOL session drops mid-read THEN the system SHALL raise
   `ConnectionError` with `details` naming the target and bytes seen.

### Requirement 5 — Line-Ending Normalization

**User Story:** As a test author, I want the same normalization choices
available over SOL as over serial.

#### Acceptance Criteria

1. WHEN the caller omits `line_ending_normalization` THEN the system SHALL
   default to `"crlf_to_lf"`.
2. WHEN the caller passes `"cr_to_lf"` THEN the system SHALL convert
   standalone CR to LF.
3. WHEN the caller passes `"none"` THEN the system SHALL pass inbound bytes
   through unchanged.
4. WHEN `send(data)` is invoked THEN the system SHALL NOT normalize outbound
   bytes — send is literal.

### Requirement 6 — Implements IUefiTransport

**User Story:** As a layer-3 integrator, I want the IPMI-SOL transport to
be structurally interchangeable with the other layer-2 transports.

#### Acceptance Criteria

1. WHEN a `UefiIpmiSolTransport` instance is checked against
   `IUefiTransport` via `isinstance` THEN the system SHALL return `True`.
2. WHEN `transport_kind` is read THEN the system SHALL return
   `"ipmi-sol"`.
3. WHEN `IUefiTransport.connect` / `disconnect` / `send` / `recv` /
   `read_until` / `is_connected` are invoked THEN the system SHALL honor
   the method signatures declared on `IUefiTransport`.

### Requirement 7 — Clean Deactivation on Close

**User Story:** As a test author, I want my SOL session to deactivate
cleanly on exit so that the next run (or operator) can acquire the
payload without manually clearing it.

#### Acceptance Criteria

1. WHEN the transport is used as `with UefiIpmiSolTransport(...) as t:`
   THEN the system SHALL call `connect()` on entry and `disconnect()` on
   exit.
2. WHEN `disconnect()` is invoked THEN the system SHALL issue
   `sol deactivate` (or `pyghmi`'s equivalent) and SHALL wait up to
   `aspects.completion_interval` seconds for the deactivation to
   acknowledge.
3. WHEN `disconnect()` is invoked on an already-closed transport THEN the
   system SHALL be a no-op and SHALL NOT raise.
4. IF deactivation fails THEN the system SHALL log the raw tool output at
   `WARNING` and SHALL NOT re-raise from `disconnect()`.

### Requirement 8 — Surface IPMI Errors Verbatim

**User Story:** As an operator triaging a failing SOL bring-up, I want the
raw `ipmitool` or `pyghmi` error output to be on the raised exception, so
that I do not have to re-run with verbose logging to understand what
happened.

#### Acceptance Criteria

1. WHEN any `ipmitool` subprocess returns a non-zero status THEN the system
   SHALL capture stdout and stderr and SHALL include them in the raised
   `UefiTransportError`'s `details`.
2. WHEN `pyghmi` raises an exception THEN the system SHALL wrap it in
   `UefiTransportError` with the original chained via `from`.
3. WHEN `details` contain credential material (e.g., the `-P password` on
   an `ipmitool` command line THAT the transport built itself) THEN the
   system SHALL redact it before inclusion.

### Requirement 9 — Aspects=None Passthrough

**User Story:** As a caller of transport methods, I want to omit `aspects`
and have the transport fall back to the instance default.

#### Acceptance Criteria

1. WHEN any public method is invoked with `aspects=None` THEN the system
   SHALL use the aspects supplied at construction.
2. IF no aspects object was supplied at construction and `aspects=None` is
   passed THEN the system SHALL raise `ConfigurationError`.

### Requirement 10 — Action-Pattern Coverage

**User Story:** As a test author, I want every action pattern I use
elsewhere in the stack to also work for the IPMI-SOL transport.

#### Acceptance Criteria

1. WHEN `connect(aspects=ActionPattern.SINGLE_CALL)` is invoked THEN the
   system SHALL perform exactly one activation attempt.
2. WHEN `connect(aspects=ActionPattern.SINGLE_CONNECTED_CALL)` is invoked
   THEN the system SHALL skip activation when already connected.
3. WHEN `connect(aspects=ActionPattern.DO_UNTIL_SUCCESS)` is invoked THEN
   the system SHALL retry until the connection-timeout deadline.
4. WHEN `connect(aspects=ActionPattern.DO_UNTIL_CONNECTION_FAILURE)` is
   invoked on an already-connected transport THEN the system SHALL poll
   session liveness every `aspects.connection_interval` until a failure is
   observed and SHALL raise.
5. IF `aspects.action_pattern == ActionPattern.DO_WHILE_SUCCESS` THEN
   `connect()` SHALL raise `SemanticError`.

### Requirement 11 — Diagnostics and Logging

**User Story:** As an operator triaging failures, I want every activation,
deactivation, eviction, and IPMI error to emit a structured log entry, so
that run logs are greppable.

#### Acceptance Criteria

1. WHEN `connect()` begins THEN the system SHALL emit `INFO` with the BMC
   host, the backend (`pyghmi` / `ipmitool`), and whether
   `evict_existing` is set.
2. WHEN `connect()` succeeds THEN the system SHALL emit `INFO` with the
   attempt count and elapsed time.
3. WHEN an existing session is detected with `evict_existing=False` THEN
   the system SHALL emit `WARNING` with the session holder details if
   available.
4. WHEN `sol deactivate` returns `Deactivation failed` THEN the system
   SHALL emit `ERROR` with the raw output.
5. WHEN `disconnect()` completes THEN the system SHALL emit `DEBUG`
   with session duration and approximate bytes exchanged.
6. WHEN any BMC command line is logged THEN the system SHALL redact
   `-P <password>` / `--password=<value>` before emission.
