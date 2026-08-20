# Requirements — UefiBase

## Introduction

`UefiBase` is the shared-state / shared-behavior base class for
`UefiAgent` and `UefiSession`.  It owns the credentials, default aspects,
host / transport configuration, and the library's generic action-pattern
engine.  It is the direct analog of `SshBase`
(`/Users/myron/repos/codecraftai-protocol-ssh/source/packages/ccai/protocols/ssh/sshbase.py`)
and must implement every one of the five `ActionPattern` values declared
by `1-aspects`, wrap every inner call in a `MonitoredScope`, and drive
retry loops with a `TimeoutContext` from
`ccai.waiting.waitmodel.TimeoutContext`.

`UefiBase` additionally implements `IUefiSystemContext`, the library's
UEFI-flavored extension of the shared `ISystemContext` interface.  This
makes any `UefiAgent` or `UefiSession` usable as a system-context
parameter to higher-layer capability modules.

This feature is the heart of the measured-interop contract.  Every
layer-5 capability calls through the `_run_cmd`-style helpers defined
here; every retry, every timeout, every connection-loss allowance flows
through this module.

## Requirements

### Requirement 1 — Instance Variable Initialization In `__init__`

**User Story:** As a maintainer, I want every instance variable of
`UefiBase` initialized in `__init__`, so that there are no lazy attribute
surprises during debugging.

#### Acceptance Criteria

1. WHEN `UefiBase.__init__` returns THEN every attribute referenced by
   any method of the class SHALL already be set, including:
   `_host`, `_primary_credential`, `_transport_kind`, `_jump`,
   `_called_id`, `_target_tag`, `_aspects`, `_logger`,
   `_client_factory`, `_user_lookup_table`, `_group_lookup_table`.
2. IF any method introduces a new instance attribute outside `__init__`
   THEN the code review SHALL reject it; tests SHALL assert that the
   set of attributes after `__init__` is identical to the set after any
   public method call.
3. WHEN `__init__` runs THEN the system SHALL NOT perform network I/O
   — construction is free of side effects beyond field assignment.

### Requirement 2 — Action Pattern Engine Implements All Five Patterns

**User Story:** As a test author, I want `UefiBase._run_cmd` to implement
every `ActionPattern` value declared in `1-aspects`, so that every
layer-5 capability can choose its retry semantics freely.

#### Acceptance Criteria

1. WHEN `aspects.action_pattern == ActionPattern.SINGLE_CALL` THEN the
   system SHALL run the command exactly once and return its result.
2. WHEN `aspects.action_pattern == ActionPattern.DO_UNTIL_SUCCESS` THEN
   the system SHALL run the command repeatedly until the status matches
   `aspects.expected_status` or `aspects.completion_timeout` elapses.
3. WHEN `aspects.action_pattern == ActionPattern.DO_WHILE_SUCCESS` THEN
   the system SHALL run the command repeatedly while the status matches
   `aspects.expected_status` and stop either when the status diverges
   or `aspects.completion_timeout` elapses.
4. WHEN `aspects.action_pattern == ActionPattern.SINGLE_CONNECTED_CALL`
   THEN the system SHALL run the command exactly once on the caller-
   supplied channel without internal reconnect attempts.
5. WHEN `aspects.action_pattern ==
   ActionPattern.DO_UNTIL_CONNECTION_FAILURE` THEN the system SHALL
   loop the command until the underlying connection raises a
   `ConnectionError` from `aspects.allowed_connection_exceptions`.
6. IF `aspects.action_pattern` is an unknown value THEN the system
   SHALL raise `SemanticError` mirroring
   `sshbase.py:628–630`.
7. WHERE `UefiBase` is subclassed by `UefiSession` THEN
   `SINGLE_CONNECTED_CALL` and `DO_UNTIL_CONNECTION_FAILURE` SHALL be
   supported and the other three SHALL NOT be raised by the session's
   public `run_cmd` when those patterns are passed.
8. WHERE `UefiBase` is subclassed by `UefiAgent` THEN `SINGLE_CALL`,
   `DO_UNTIL_SUCCESS`, and `DO_WHILE_SUCCESS` SHALL be supported
   exactly as in `SshBase._run_cmd` (sshbase.py:542–623) and
   `SINGLE_CONNECTED_CALL` / `DO_UNTIL_CONNECTION_FAILURE` SHALL raise
   `SemanticError`.

### Requirement 3 — MonitoredScope Wraps Every Inner Call

**User Story:** As an operator, I want every retry iteration's inner
call wrapped in a `MonitoredScope`, so that hung threads can be
detected and reported.

#### Acceptance Criteria

1. WHEN `_run_cmd` dispatches the inner call under `SINGLE_CALL` THEN
   the system SHALL wrap the call in a `MonitoredScope` labelled
   `"RUNCMD-SINGULAR"` with `notify_delay=aspects.monitor_delay`.
2. WHEN `_run_cmd` dispatches the inner call under `DO_UNTIL_SUCCESS`
   THEN the system SHALL wrap the call in a `MonitoredScope` labelled
   `"RUNCMD-DO_UNTIL_SUCCESS"`.
3. WHEN `_run_cmd` dispatches the inner call under `DO_WHILE_SUCCESS`
   THEN the system SHALL wrap the call in a `MonitoredScope` labelled
   `"RUNCMD-DO_WHILE_SUCCESS"`.
4. WHEN `_run_cmd` dispatches the inner call under
   `SINGLE_CONNECTED_CALL` THEN the system SHALL wrap the call in a
   `MonitoredScope` labelled `"RUNCMD-SINGLE_CONNECTED_CALL"`.
5. WHEN `_run_cmd` dispatches the inner call under
   `DO_UNTIL_CONNECTION_FAILURE` THEN the system SHALL wrap the call
   in a `MonitoredScope` labelled `"RUNCMD-DO_UNTIL_CONNECTION_FAILURE"`.
6. WHEN a `MonitoredScope` fires THEN the system SHALL attach the
   current thread id, name, and the command being run to the monitor
   message exactly as `sshbase.py:539` does.

### Requirement 4 — TimeoutContext Drives Completion Loops

**User Story:** As a test author, I want `DO_UNTIL_SUCCESS` and
`DO_WHILE_SUCCESS` to use the `TimeoutContext` pattern for deadline
tracking, so that the "final attempt" semantics are consistent with the
SSH library.

#### Acceptance Criteria

1. WHEN `_run_cmd` begins a completion loop THEN the system SHALL
   create a `TimeoutContext(aspects.completion_timeout,
   aspects.completion_interval)` and call `mark_begin()`.
2. WHEN the loop body completes without success THEN the system SHALL
   check `completion_toctx.final_attempt`; if `True`, raise the
   `TimeoutContext.create_timeout(...)` error.
3. WHEN the loop body completes without success AND
   `completion_toctx.should_continue()` is `False` THEN the system
   SHALL call `completion_toctx.mark_final_attempt()`.
4. WHEN the loop sleeps between attempts THEN the system SHALL sleep
   for `completion_toctx.interval` seconds.

### Requirement 5 — `verify_connectivity` Default

**User Story:** As a test author, I want a default `verify_connectivity`
method that uses a harmless echo, so that reconnect-verify workflows
work out-of-the-box.

#### Acceptance Criteria

1. WHEN `verify_connectivity()` is called on a `UefiShellClient`-backed
   base THEN the system SHALL dispatch the UEFI Shell command
   `echo Hello` and return `True` iff the response contains `"Hello"`.
2. WHEN `verify_connectivity()` is called on a
   `UefiRedfishClient`-backed base THEN the system SHALL GET
   `/redfish/v1/` and return `True` iff the response is HTTP 200.
3. WHEN `verify_connectivity()` is called on a
   `UefiHostOsClient`-backed base THEN the system SHALL dispatch
   `echo Hello` via the composed `SshAgent` and return `True` iff the
   stdout strip equals `"Hello"`, mirroring `SshBase.verify_connectivity`
   (sshbase.py:258–271).
4. IF the connection is down AND the underlying client raises
   `ConnectionError` THEN the system SHALL return `False`.

### Requirement 6 — `AbstractMethodError` On Override-Intended Methods

**User Story:** As a maintainer, I want methods intended for override to
raise `AbstractMethodError` when the base class's method is called, so
that forgetting to override is detectable at call time.

#### Acceptance Criteria

1. WHEN `reboot(aspects=None)` is called on a `UefiBase` instance THEN
   the system SHALL raise `AbstractMethodError` naming the derived
   class, mirroring `sshbase.py:234–242`.
2. WHEN `run_cmd(command, ...)` is called on a `UefiBase` instance
   (not a subclass) THEN the system SHALL raise `AbstractMethodError`,
   mirroring `sshbase.py:244–256`.
3. WHEN `run_redfish(verb, path, ...)` is called on a `UefiBase` that
   does not have a Redfish-capable client THEN the system SHALL raise
   `AbstractMethodError`.
4. WHEN `run_variable(name, vendor_guid, ...)` is called on a
   `UefiBase` without a capable client THEN the system SHALL raise
   `AbstractMethodError`.

### Requirement 7 — `IUefiSystemContext` Compliance

**User Story:** As a layer-5 capability author, I want `UefiBase` to
satisfy `IUefiSystemContext`, so that capabilities accept any
`UefiAgent` or `UefiSession` as a system-context argument.

#### Acceptance Criteria

1. WHEN `isinstance(base_instance, IUefiSystemContext)` is evaluated
   THEN the system SHALL return `True` (via `@runtime_checkable`
   protocol dispatch).
2. WHEN the capabilities access `system_context.host`,
   `system_context.firmware_vendor`,
   `system_context.firmware_revision`, or
   `system_context.secure_boot_state` THEN the system SHALL provide
   those accessors from `UefiBase`.
3. WHERE `firmware_vendor` is not yet resolved THEN the system SHALL
   fetch it on first access and cache the result; subsequent reads
   SHALL return the cached value.

### Requirement 8 — `LoggingPattern`-Driven Result Logging

**User Story:** As a test author, I want command-result logging to be
controlled by `aspects.logging_pattern`, so that my log volume matches
the signal I want.

#### Acceptance Criteria

1. WHEN `_log_command_result` is called with
   `LoggingPattern.ALL_RESULTS` THEN the system SHALL log both
   success and failure cases.
2. WHEN called with `LoggingPattern.SUCCESS_ONLY` THEN the system
   SHALL log only matches to `expected_status`.
3. WHEN called with `LoggingPattern.FAILURE_ONLY` THEN the system
   SHALL log only non-matches to `expected_status`.
4. WHERE `expected_status` is an `int` THEN the system SHALL compare
   with `==`; where it is a sequence, SHALL compare with `in`.
5. WHERE `_target_tag` is set THEN the system SHALL include it in
   every logged line per `sshbase.py:483–486`.

### Requirement 9 — Aspects None-Passthrough At Every Entry Point

**User Story:** As a test author, I want every public method that
accepts an `aspects` parameter to treat `None` as "use the instance
default", so that call sites stay concise.

#### Acceptance Criteria

1. WHEN any public method with an `aspects` parameter is called with
   `aspects=None` THEN the system SHALL resolve via
   `resolve_aspects(None, self._aspects)`.
2. WHEN `aspects` is passed non-None THEN the system SHALL use it for
   the duration of that call only — instance default SHALL be
   unchanged.

### Requirement 10 — Allowed-Connection-Exception Handling

**User Story:** As a test author, I want transient connection
exceptions declared on aspects to be tolerated inside the action-pattern
engine, so that flaky networks do not abort every retry loop.

#### Acceptance Criteria

1. WHEN the inner call raises an exception whose type is in
   `aspects.allowed_connection_exceptions` THEN the system SHALL treat
   it as a transient failure and continue the loop (for
   `DO_UNTIL_SUCCESS` / `DO_WHILE_SUCCESS` /
   `DO_UNTIL_CONNECTION_FAILURE`).
2. WHEN the inner call raises a type NOT in
   `aspects.allowed_connection_exceptions` THEN the system SHALL
   re-raise it.
3. WHEN operating under `DO_UNTIL_CONNECTION_FAILURE` AND an allowed
   exception is raised THEN the system SHALL return (loop exit condition
   met) rather than retry.
4. WHEN `aspects.must_connect` is `True` AND the inner call raises
   `ConnectionError` THEN the system SHALL NOT retry; it SHALL
   re-raise immediately.

### Requirement 11 — `_target_tag` Formation

**User Story:** As an operator reading logs from multiple targets, I
want each log line to carry a target-identifying prefix, so that I can
filter by target even when multiple agents log concurrently.

#### Acceptance Criteria

1. WHEN `__init__` is called with `called_id=None` THEN the system
   SHALL set `_target_tag = host`.
2. WHEN `called_id` is provided THEN the system SHALL set
   `_target_tag = "[{called_id}]"`, mirroring `sshbase.py:113–115`.
3. WHEN `_log_command_result` logs THEN the system SHALL prepend
   `_target_tag` to the log line.

### Requirement 12 — Credential Validation

**User Story:** As a library author, I want `UefiBase` to reject
invalid credentials at construction time, so that broken specs are
caught early.

#### Acceptance Criteria

1. WHEN `__init__` is called with a `primary_credential` that is not
   an instance of a registered `UefiCredential` type (or `SshCredential`
   for the SSH-host transport kind) THEN the system SHALL raise
   `ConfigurationError`.
2. WHEN the credential type does not match `transport_kind` (e.g.,
   passing a `UefiRedfishCredential` with `transport_kind="serial"`)
   THEN the system SHALL raise `ConfigurationError` with `details`
   naming both the expected and received credential type.
3. WHERE the transport kind requires additional credential fields
   (e.g., Redfish requires URL) THEN the system SHALL call
   `credential.validate()` and re-raise any `ConfigurationError` with
   a wrapped message.

### Requirement 13 — `_run_cmd` Result Contract

**User Story:** As a layer-5 capability author, I want `_run_cmd` to
return a `(status, stdout, stderr)` three-tuple even when the underlying
transport is Redfish, so that my capability code does not branch on
transport.

#### Acceptance Criteria

1. WHEN `_run_cmd` returns for any `action_pattern` THEN the return
   value SHALL be `tuple[int, str, str]` matching
   `sshbase.py:501–632`.
2. WHERE the underlying transport is Redfish THEN the Redfish status
   code SHALL be mapped to `status`, the response body to `stdout`,
   and any structured error payload to `stderr`.
3. WHERE the underlying transport is SSH-host THEN the SSH three-tuple
   SHALL pass through unchanged.
4. WHERE the underlying transport is serial or IPMI-SOL THEN the
   parsed shell status SHALL be mapped to `status` and the captured
   text to `stdout`; `stderr` SHALL be an empty string by default.
