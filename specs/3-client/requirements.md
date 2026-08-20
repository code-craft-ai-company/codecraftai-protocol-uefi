# Requirements — UefiClient

## Introduction

The `UefiClient` feature provides the per-transport protocol-framing client that
sits on top of a layer-2 `IUefiTransport` and turns transport-level byte / HTTP
primitives into a uniform command-dispatch API.  A `UefiClient` is the object a
layer-4 `UefiBase`/`UefiAgent`/`UefiSession` uses when it actually needs to
issue work against the target — the layer-4 classes decide *when* and *how many
times* to issue it (through `ActionPattern`), and the client decides *how* a
single issuance is framed over its particular transport.

There are three concrete clients — `UefiShellClient` (serial / IPMI-SOL),
`UefiRedfishClient`, and `UefiHostOsClient` (SSH-to-host) — all deriving from
an abstract `UefiClient` base.  Each subclass declares exactly which
`ActionPattern` values it supports; unsupported patterns raise `SemanticError`
so misuse is loud.  Channels are short-lived; multiplexing requires explicit
caller opt-in.

This feature is the direct analog of `SshClient`
(`/Users/myron/repos/codecraftai-protocol-ssh/source/packages/ccai/protocols/ssh/sshclient.py`)
with extra subclass-level specialization to cover the three very different
transports supported by this library.

## Requirements

### Requirement 1 — Abstract Base With Three Concrete Subclasses

**User Story:** As a library author, I want a single abstract `UefiClient`
contract that three transport-specific subclasses satisfy, so that layer-4
code never branches on transport kind.

#### Acceptance Criteria

1. WHEN `UefiClient` is imported THEN the system SHALL expose an abstract
   class with abstract methods `open_channel`, `execute`, and `close`.
2. WHEN `UefiShellClient`, `UefiRedfishClient`, or `UefiHostOsClient` is
   instantiated THEN the system SHALL produce an instance whose method
   resolution order includes `UefiClient`.
3. IF a caller instantiates the abstract `UefiClient` directly THEN the
   system SHALL raise `TypeError` with a message naming the concrete
   subclasses.
4. WHERE a subclass omits an implementation of any abstract method THEN the
   system SHALL raise `AbstractMethodError` when that method is called.

### Requirement 2 — Channel Opening With Explicit Multiplex Opt-In

**User Story:** As a test author, I want every `open_channel()` call to
return a fresh channel unless I explicitly opt into multiplexing, so that
concurrent callers never collide on a shared transport by accident.

#### Acceptance Criteria

1. WHEN the caller invokes `client.open_channel()` with no arguments THEN
   the system SHALL return a new `UefiChannel` instance distinct from any
   previously returned channel.
2. WHEN the caller invokes `client.open_channel(multiplex=True)` AND the
   underlying transport supports multiplexing THEN the system SHALL return
   a channel that shares the transport with existing channels.
3. IF `multiplex=True` is passed to a subclass whose transport does not
   support multiplexing (e.g., `UefiShellClient` over serial) THEN the
   system SHALL raise `SemanticError` naming the transport and explaining
   the limitation.
4. WHEN `open_channel()` is called on a closed client THEN the system
   SHALL raise `ConnectionError`.

### Requirement 3 — Execute Honors Every `UefiCmdAspects` Field

**User Story:** As a test author, I want `execute(command, aspects)` to
respect every field on the passed aspects object, so that my measured-interop
contract flows all the way down from the call site to the wire.

#### Acceptance Criteria

1. WHEN the caller invokes `client.execute(command, aspects=None)` THEN the
   system SHALL resolve `aspects` against the client's instance default via
   `resolve_aspects()`.
2. WHEN `aspects.inactivity_timeout` is set THEN the system SHALL bound every
   transport `recv` call by that value.
3. WHEN `aspects.response_encoding` is set THEN the system SHALL decode the
   response bytes using that codec.
4. WHEN `aspects.prompt_pattern` is set AND the subclass is `UefiShellClient`
   THEN the system SHALL use that pattern to detect end-of-command.
5. WHEN `aspects.expected_status` is set THEN the system SHALL compare the
   returned status against it and record the result for layer-4 logging.
6. IF `aspects.action_pattern` is not in the subclass's supported pattern
   set THEN the system SHALL raise `SemanticError` before any bytes are sent.

### Requirement 4 — Fail Fast On Connection Loss Mid-Execute

**User Story:** As a test author, I want the client to surface connection
loss immediately rather than silently retrying, so that layer-4 retry
semantics remain the single source of truth for persistence.

#### Acceptance Criteria

1. WHEN the underlying transport raises `ConnectionError` during
   `execute(...)` THEN the system SHALL re-raise `ConnectionError` without
   any internal retry.
2. WHEN the underlying transport raises `TimeoutError` during an
   inactivity-timeout window THEN the system SHALL re-raise `TimeoutError`
   without any internal retry.
3. WHERE the caller has passed `aspects.allowed_connection_exceptions` THEN
   the system SHALL NOT consume those exceptions at this layer; they are
   consumed by layer-4 `_run_cmd`.
4. WHEN a mid-execute connection loss occurs THEN the system SHALL close any
   open channel associated with the failed operation before re-raising.

### Requirement 5 — `UefiShellClient` Action Pattern Support

**User Story:** As a test author using a serial or IPMI-SOL transport, I
want to know exactly which action patterns the shell client supports, so
that I never accidentally ship a spec that cannot be honored.

#### Acceptance Criteria

1. WHEN `UefiShellClient.supported_action_patterns` is read THEN the system
   SHALL return a frozenset containing `SINGLE_CALL`, `DO_UNTIL_SUCCESS`,
   and `DO_WHILE_SUCCESS`.
2. IF the caller passes `aspects.action_pattern ==
   ActionPattern.SINGLE_CONNECTED_CALL` or `DO_UNTIL_CONNECTION_FAILURE` to
   `UefiShellClient.execute` THEN the system SHALL raise `SemanticError`.
3. WHEN `execute` is called under `SINGLE_CALL` THEN the system SHALL send
   the command, read until prompt, and return exactly once.

### Requirement 6 — `UefiRedfishClient` Action Pattern Support

**User Story:** As a test author using the Redfish transport, I want the
client to support long-running task polling via the measured-interop
pattern, so that capsule uploads and boot-option writes can be driven
declaratively.

#### Acceptance Criteria

1. WHEN `UefiRedfishClient.supported_action_patterns` is read THEN the
   system SHALL return a frozenset containing `SINGLE_CALL`,
   `DO_UNTIL_SUCCESS`, and `DO_WHILE_SUCCESS`.
2. IF the caller passes `SINGLE_CONNECTED_CALL` or
   `DO_UNTIL_CONNECTION_FAILURE` THEN the system SHALL raise
   `SemanticError`.
3. WHEN Redfish returns `202 Accepted` with a TaskMonitor URI THEN the
   system SHALL poll that URI every `aspects.completion_interval` until the
   Task completes or `aspects.completion_timeout` elapses.

### Requirement 7 — `UefiHostOsClient` Action Pattern Support

**User Story:** As a test author using the SSH-to-host transport, I want
the host-OS client to expose the full set of shell-like patterns supported
by the underlying SSH stack, so that my existing SSH idioms carry over.

#### Acceptance Criteria

1. WHEN `UefiHostOsClient.supported_action_patterns` is read THEN the
   system SHALL return a frozenset containing `SINGLE_CALL`,
   `DO_UNTIL_SUCCESS`, and `DO_WHILE_SUCCESS`.
2. IF the caller passes `SINGLE_CONNECTED_CALL` or
   `DO_UNTIL_CONNECTION_FAILURE` THEN the system SHALL raise
   `SemanticError` mirroring `SshBase._run_cmd`
   (sshbase.py:625–627).
3. WHEN `execute` runs a command THEN the system SHALL dispatch via the
   composed `SshAgent.run_cmd` and return the three-tuple
   `(status, stdout, stderr)` unchanged.

### Requirement 8 — Close Is Idempotent And Never Raises

**User Story:** As a test author, I want `client.close()` to be safe to
call multiple times and never raise, so that cleanup in `finally` blocks
cannot mask the original exception.

#### Acceptance Criteria

1. WHEN `client.close()` is called for the first time THEN the system
   SHALL close every open channel and then close the underlying transport.
2. WHEN `client.close()` is called a second or subsequent time THEN the
   system SHALL return without error.
3. IF a channel `close()` raises an exception internally THEN the system
   SHALL log the exception at WARNING under `uefi.transport` and continue.
4. IF the transport `close()` raises an exception THEN the system SHALL
   log the exception at WARNING under `uefi.transport` and suppress it.

### Requirement 9 — Aspects Passthrough And Defaulting

**User Story:** As a test author, I want every client method that accepts
an `aspects` parameter to treat `None` as "use the instance default," so
that I am never forced to repeat my preset at every call site.

#### Acceptance Criteria

1. WHEN any public method is called with `aspects=None` THEN the system
   SHALL resolve to the client's instance default via `resolve_aspects()`.
2. WHEN a non-None `aspects` is passed THEN the system SHALL use it and
   NOT fall back to the instance default for any field.
3. IF neither a passed aspects nor an instance default is available THEN
   the system SHALL raise `ConfigurationError` mirroring
   `resolve_aspects(None, None)`.

### Requirement 10 — Structured Logging Of Dispatch Events

**User Story:** As an operator, I want every command dispatch to emit a
structured event to the `uefi.transport` channel, so that I can correlate
wire activity with test steps in post-mortems.

#### Acceptance Criteria

1. WHEN `execute` begins THEN the system SHALL log a `DEBUG`-level event
   containing the command, the `ActionPattern`, and the resolved aspects
   summary.
2. WHEN `execute` completes THEN the system SHALL log the status and
   elapsed wall-clock milliseconds.
3. WHERE `aspects.logging_pattern == LoggingPattern.FAILURE_ONLY` THEN the
   system SHALL suppress the success-case log events but SHALL always emit
   the dispatch-begin event.

### Requirement 11 — Subclass Construction Validates Transport

**User Story:** As a library author, I want each client subclass to refuse
construction with a transport of the wrong kind, so that mis-wiring is
caught at construction time rather than first call.

#### Acceptance Criteria

1. WHEN `UefiShellClient` is constructed with a `UefiSerialTransport` or
   `UefiIpmiSolTransport` THEN the system SHALL accept it.
2. IF `UefiShellClient` is constructed with any other transport type THEN
   the system SHALL raise `ConfigurationError` with `details` naming the
   expected and received transport kinds.
3. WHEN `UefiRedfishClient` is constructed with a `UefiRedfishTransport`
   THEN the system SHALL accept it.
4. WHEN `UefiHostOsClient` is constructed with a `UefiSshHostTransport`
   THEN the system SHALL accept it.

### Requirement 12 — Context Manager Protocol

**User Story:** As a test author, I want to use a client in a `with`
block, so that teardown happens automatically even on exception.

#### Acceptance Criteria

1. WHEN a client is used as `with UefiClient(...) as c:` THEN the system
   SHALL call `c.close()` on exit regardless of whether the block raised.
2. WHEN the `with` block raises THEN the system SHALL NOT suppress the
   exception.
