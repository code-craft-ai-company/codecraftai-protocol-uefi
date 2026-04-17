# Requirements — UefiConnection

## Introduction

`UefiConnection` wraps the low-level transport handle (a serial port, a
`requests.Session` for Redfish, an IPMI-SOL stream, or a composed `SshAgent`)
in a single lifecycle-managed object that can connect, disconnect, and
reconnect under caller-controlled aspects.  Every state transition is
emitted as a structured log event on the `uefi.transport` channel so that
test operators can reconstruct the sequence of connection events from a
log tail.

Reconnection logic is bounded by the `connection_timeout`,
`connection_interval`, and `allowed_connection_exceptions` triad on
`UefiAspects` — identical to the behavior of `SshBase._create_client`
(`sshbase.py:288–375`).  Reconnects preserve the original credential and
aspects so callers never have to rebuild state.  A monotonically increasing
`reconnect_count` is exposed for diagnostics and test assertions.

This feature is foundational to layer-4 session behavior, which uses
`UefiConnection.reconnect()` as part of the
`DO_UNTIL_CONNECTION_FAILURE` action pattern.

## Requirements

### Requirement 1 — Lifecycle Primitives

**User Story:** As a library author, I want a uniform
`connect/disconnect/reconnect` API across every transport kind, so that
higher layers do not branch on transport type to manage lifecycle.

#### Acceptance Criteria

1. WHEN `connection.connect()` is called on a disconnected connection THEN
   the system SHALL establish the underlying transport and set
   `connection.connected` to `True`.
2. WHEN `connection.disconnect()` is called on a connected connection THEN
   the system SHALL tear down the underlying transport and set
   `connection.connected` to `False`.
3. WHEN `connection.reconnect()` is called THEN the system SHALL
   disconnect if currently connected and then call `connect()` bounded by
   the aspects-supplied connection timeout and interval.
4. IF `connection.connect()` is called on an already-connected connection
   THEN the system SHALL log `DEBUG` at `uefi.transport` and return
   without error.
5. IF `connection.disconnect()` is called on an already-disconnected
   connection THEN the system SHALL return without error.

### Requirement 2 — `connected` Is A Property

**User Story:** As a test author, I want `connected` to be a property so
that my code reads naturally and is guaranteed to be a cheap data
accessor.

#### Acceptance Criteria

1. WHEN `connection.connected` is read THEN the system SHALL return a
   `bool` drawn from internal state without performing I/O.
2. IF `connection.connected` is assigned THEN the system SHALL raise
   `AttributeError` — it is read-only.
3. WHERE `connected` is implemented THEN the system SHALL NOT perform
   SSH calls, thread proxying, or blocking on sync primitives inside the
   accessor (per `/Users/myron/repos/CLAUDE.md` property rules).

### Requirement 3 — Reconnect Preserves Credential And Aspects

**User Story:** As a test author driving long-running sessions, I want
reconnect attempts to use the exact same credential and aspects I passed
at construction, so that I never have to rebuild connection state.

#### Acceptance Criteria

1. WHEN `reconnect()` is called THEN the system SHALL use the original
   credential passed to the constructor and not any subsequently-mutated
   reference.
2. WHEN `reconnect()` is called THEN the system SHALL use the aspects
   object attached at construction and not the current
   `DEFAULT_ASPECTS` module default.
3. WHERE the caller has frozen the aspects at construction time THEN the
   system SHALL respect the frozen values for every reconnect attempt.

### Requirement 4 — Reconnect Count Exposure

**User Story:** As a test author, I want a reliable counter of
reconnection attempts, so that my diagnostics can assert how many
transient failures occurred during a test.

#### Acceptance Criteria

1. WHEN the connection is first constructed THEN `connection.reconnect_count`
   SHALL be `0`.
2. WHEN `reconnect()` succeeds THEN the system SHALL increment
   `reconnect_count` by exactly `1`.
3. WHEN `reconnect()` raises `ConnectionError` after exhausting the
   connection timeout THEN the system SHALL NOT increment
   `reconnect_count`.
4. WHEN `reconnect_count` is read THEN the system SHALL return a copy or
   immutable int; any mutation attempt SHALL raise `AttributeError`.

### Requirement 5 — Connection Timeout And Interval From Aspects

**User Story:** As a test author, I want connection retry behavior bounded
by `aspects.connection_timeout` and paced by `aspects.connection_interval`,
so that I have a single control surface for connection persistence.

#### Acceptance Criteria

1. WHEN `connect()` or `reconnect()` begins THEN the system SHALL compute
   an end time as `begin + aspects.connection_timeout` seconds.
2. WHILE the current time is before the end time AND the underlying
   transport raises an allowed connection exception THEN the system SHALL
   sleep `aspects.connection_interval` seconds and retry.
3. IF the underlying transport raises a non-allowed exception THEN the
   system SHALL re-raise immediately without retry.
4. IF the end time elapses without success THEN the system SHALL raise
   `ConnectionError` with `details` including the host, transport kind,
   begin/end timestamps, and the chained last exception.

### Requirement 6 — Allowed Connection Exceptions

**User Story:** As a test author, I want to declare which connection
exceptions are transient, so that the connection does not silently
swallow a real failure.

#### Acceptance Criteria

1. WHEN `aspects.allowed_connection_exceptions` is empty THEN any
   exception raised by the underlying transport SHALL be re-raised
   immediately.
2. WHEN a raised exception's type is in
   `aspects.allowed_connection_exceptions` THEN the system SHALL treat it
   as transient and retry.
3. IF a raised exception is a subclass of a listed type THEN the system
   SHALL treat it as transient (per the SSH library's
   `type(cerr) in allowed_connection_exceptions` check at
   `sshbase.py:358`, extended here with `isinstance`-based matching — see
   Design).

### Requirement 7 — Structured Log Events On Every Transition

**User Story:** As an operator, I want a structured log event on every
connect, disconnect, and reconnect transition, so that I can reconstruct
the connection timeline from a log tail.

#### Acceptance Criteria

1. WHEN `connect()` succeeds THEN the system SHALL emit an `INFO`-level
   event under `uefi.transport` with fields `host`, `transport_kind`,
   and `elapsed_ms`.
2. WHEN `disconnect()` completes THEN the system SHALL emit an `INFO`
   event under `uefi.transport` with fields `host` and `transport_kind`.
3. WHEN `reconnect()` succeeds THEN the system SHALL emit an `INFO`
   event under `uefi.transport` with fields `host`, `transport_kind`,
   `reconnect_count`, `elapsed_ms`.
4. WHEN any retry attempt fails with an allowed exception THEN the
   system SHALL emit a `WARNING` event with `attempt_number`,
   `exception_type`, `exception_repr`, `remaining_ms`.
5. WHEN a terminal connection failure occurs THEN the system SHALL emit
   an `ERROR` event with the full exception chain.

### Requirement 8 — Context Manager Protocol

**User Story:** As a test author, I want `UefiConnection` to behave as a
context manager, so that I can use it in `with` blocks with guaranteed
cleanup.

#### Acceptance Criteria

1. WHEN `with UefiConnection(...) as conn:` is entered THEN the system
   SHALL call `connect()` before yielding the instance.
2. WHEN the `with` block exits (normally or by exception) THEN the
   system SHALL call `disconnect()`.
3. WHEN the `with` block raises THEN the system SHALL NOT suppress the
   exception.

### Requirement 9 — Must-Connect Semantics

**User Story:** As a test author performing a destructive operation, I
want to be able to declare that connection is mandatory, so that a
transient failure is raised rather than retried.

#### Acceptance Criteria

1. WHEN `aspects.must_connect` is `True` AND the first connect attempt
   raises `ConnectionError` THEN the system SHALL re-raise immediately
   without entering the retry loop.
2. WHEN `aspects.must_connect` is `False` (the default) THEN the system
   SHALL apply the full retry-until-timeout behavior described in
   Requirement 5.

### Requirement 10 — Transport-Kind Dispatch

**User Story:** As a library author, I want one `UefiConnection` class
that dispatches to the right transport at construction time, so that
callers have a single entry point.

#### Acceptance Criteria

1. WHEN `UefiConnection(transport=UefiSerialTransport(...))` is
   constructed THEN the system SHALL accept it and route connect/
   disconnect/reconnect to the serial transport.
2. WHEN `UefiConnection(transport=UefiRedfishTransport(...))` is
   constructed THEN the system SHALL route to the Redfish transport.
3. WHEN `UefiConnection(transport=UefiSshHostTransport(...))` is
   constructed THEN the system SHALL route to the composed `SshAgent`.
4. IF the `transport` argument is not an `IUefiTransport` THEN the
   system SHALL raise `ConfigurationError`.

### Requirement 11 — Aspects None-Passthrough

**User Story:** As a test author, I want to pass `aspects=None` to any
method that takes an `aspects` override, so that the instance default
is used transparently.

#### Acceptance Criteria

1. WHEN `reconnect(aspects=None)` is called THEN the system SHALL
   resolve via `resolve_aspects(None, instance_default)`.
2. WHEN `reconnect(aspects=...)` is called with non-None THEN the
   system SHALL use the passed aspects for the duration of the
   reconnect only — it SHALL NOT mutate the stored instance default.
