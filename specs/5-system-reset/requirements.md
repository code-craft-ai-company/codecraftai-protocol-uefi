# Requirements — System Reset

## Introduction

The System Reset feature provides reset and power control with
reconnect-verify semantics over any of the supported transports.  It
implements the four reset kinds defined by UEFI §8.5.1 — cold reset, warm
reset, shutdown, and platform-specific reset — and delegates to the
appropriate mechanism per transport: the UEFI Shell `reset` command over
serial / IPMI-SOL, the `EFI_RUNTIME_SERVICES.ResetSystem` runtime service
via an OS shim on SSH-to-host, and the Redfish `ComputerSystem.Reset`
action over Redfish.

Every reset call is followed by a reconnect-verify loop modeled directly
on `SshAgent.reboot()` at
`/Users/myron/repos/codecraftai-protocol-ssh/source/packages/ccai/protocols/ssh/sshagent.py:132-185`.
The loop consumes `aspects.completion_timeout`,
`aspects.completion_interval`, and `aspects.connection_interval`, and uses
the agent's `verify_connectivity()` primitive as the verification command.
Targets that do not come back within the timeout cause a
`ResetFailedError` to be raised.

Reset reason and platform-specific reset data are exposed as optional
parameters, per UEFI §8.5.1's `ResetData` mechanism, so callers can pass
firmware-specific reset subtypes (e.g., `EFI_RESET_NOTIFICATION_PROTOCOL`
data or a platform OEM reset GUID).

## Requirements

### Requirement 1 — Reset Kind Enumeration

**User Story:** As a test author, I want a typed enumeration of reset
kinds so that the API is self-documenting and IDE-autocomplete-friendly.

#### Acceptance Criteria

1. WHEN `ResetKind` is imported THEN the system SHALL expose exactly the
   members `COLD`, `WARM`, `SHUTDOWN`, and `PLATFORM_SPECIFIC` per UEFI
   §8.5.1.
2. WHEN a caller passes a `ResetKind` value THEN the system SHALL dispatch
   to the correct transport-level implementation without caller-visible
   string conversions.
3. IF a caller passes a non-`ResetKind` value as `kind` THEN the system
   SHALL raise `ConfigurationError` with `details` naming the observed
   type.

### Requirement 2 — Cold Reset

**User Story:** As a test author, I want to issue a cold reset so that the
firmware reinitializes as if the system had just been powered on.

#### Acceptance Criteria

1. WHEN `reset(kind=ResetKind.COLD, aspects)` is called THEN the system
   SHALL invoke the transport-specific cold-reset mechanism per UEFI
   §8.5.1 (Shell `reset -c`, Redfish `ResetType: "ForceRestart"`, or an
   OS-mediated `ResetSystem(EfiResetCold, ...)`).
2. WHEN the cold reset is accepted by the firmware THEN the system SHALL
   begin the reconnect-verify loop.
3. IF the reset command itself fails with a non-connection error THEN the
   system SHALL raise `UefiError` with `details` naming the transport and
   the failing command output.

### Requirement 3 — Warm Reset

**User Story:** As a test author, I want to issue a warm reset so that
volatile state is preserved where the platform supports it.

#### Acceptance Criteria

1. WHEN `reset(kind=ResetKind.WARM, aspects)` is called THEN the system
   SHALL invoke Shell `reset -w`, Redfish `ResetType: "GracefulRestart"`,
   or `ResetSystem(EfiResetWarm, ...)` per transport.
2. WHEN the firmware does not support warm reset distinct from cold THEN
   the system SHALL log an INFO-level note on channel `uefi.reset` and
   proceed; no automatic downgrade to cold.

### Requirement 4 — Shutdown

**User Story:** As a test author, I want to issue an orderly shutdown
(power off) so that I can validate post-shutdown behavior (e.g., IPMI
power status).

#### Acceptance Criteria

1. WHEN `reset(kind=ResetKind.SHUTDOWN, aspects)` is called THEN the
   system SHALL invoke Shell `reset -s`, Redfish `ResetType:
   "GracefulShutdown"`, or `ResetSystem(EfiResetShutdown, ...)` per
   transport.
2. WHEN shutdown is requested THEN the system SHALL NOT execute the
   reconnect-verify loop (target is expected to stay down).
3. WHEN shutdown is requested THEN the system SHALL return as soon as the
   transport confirms the target has powered off, subject to
   `aspects.completion_timeout`.
4. IF the target remains reachable after `aspects.completion_timeout`
   THEN the system SHALL raise `ResetFailedError` with `details` naming
   the last-observed verification evidence.

### Requirement 5 — Platform-Specific Reset

**User Story:** As a platform engineer, I want to invoke a
platform-specific reset subtype with a reset GUID and opaque data so
that I can exercise OEM reset paths per UEFI §8.5.1.

#### Acceptance Criteria

1. WHEN `reset(kind=ResetKind.PLATFORM_SPECIFIC, aspects, reset_subtype_guid=..., reset_data=...)`
   is called THEN the system SHALL pack the GUID as the first 16 bytes of
   `ResetData` followed by the caller-supplied bytes.
2. IF `kind == ResetKind.PLATFORM_SPECIFIC` and `reset_subtype_guid` is
   `None` THEN the system SHALL raise `ConfigurationError` with
   `details` explaining that a platform-specific subtype requires a
   GUID per UEFI §8.5.1.
3. WHERE the transport is Redfish THEN the system SHALL map platform-
   specific reset to the Redfish `ResetType` literal the caller provides
   via `redfish_reset_type`; if the caller omits it THEN the system
   SHALL raise `ConfigurationError`.

### Requirement 6 — Reconnect-Verify Loop

**User Story:** As a test author, I want the reset call to block until
the target returns so that my next test step can assume the system is
responsive.

#### Acceptance Criteria

1. WHEN the reset command has been issued for a non-shutdown kind THEN
   the system SHALL sleep `aspects.monitor_delay` (default 30s) to allow
   the firmware to tear down.
2. WHILE the reconnect-verify loop is running the system SHALL attempt
   `verify_connectivity()` every `aspects.connection_interval` seconds.
3. WHEN `verify_connectivity()` returns success THEN the system SHALL
   exit the loop and return to the caller.
4. IF `aspects.completion_timeout` elapses without a successful
   verification THEN the system SHALL raise `ResetFailedError` with
   `details` listing the reset kind, timeout, last verification error,
   and transport kind.
5. WHEN a `verify_connectivity()` call raises an exception listed in
   `aspects.allowed_connection_exceptions` THEN the system SHALL treat
   it as a "not yet back" signal and continue polling.
6. IF a `verify_connectivity()` call raises an exception NOT listed in
   `aspects.allowed_connection_exceptions` THEN the system SHALL raise
   it immediately, aborting the loop.

### Requirement 7 — Default Completion Timeout

**User Story:** As a test author, I want a sensible default timeout for
`reset()` so that I do not have to specify one for typical servers.

#### Acceptance Criteria

1. WHEN `reset()` is called with `aspects=None` and the owning agent's
   default aspects has `completion_timeout` unset THEN the system SHALL
   apply an effective `completion_timeout` of 300 seconds, matching
   `SshAgent.reboot()`'s default.
2. WHEN `reset()` is called with an explicit `aspects.completion_timeout`
   THEN the system SHALL honor the caller-supplied value exactly, with
   no floor or ceiling enforcement.

### Requirement 8 — Reset Reason Capture

**User Story:** As an operator, I want an optional `reason` parameter so
that resets in logs are attributable.

#### Acceptance Criteria

1. WHEN `reset(..., reason="post-capsule validation")` is called THEN
   the system SHALL log an INFO-level line on channel `uefi.reset`
   including the reason, the reset kind, and the target host.
2. WHEN `reason` is omitted THEN the system SHALL log only the reset
   kind and the target host.
3. WHEN the transport is Redfish THEN the system SHALL include `reason`
   (truncated to 256 chars) in the body payload under an
   `@Message.ExtendedInfo` entry if the BMC accepts custom extended
   info; otherwise it SHALL log locally only.

### Requirement 9 — SINGLE_CALL Action Pattern

**User Story:** As a test author, I want a one-shot reset behavior when I
am confident the target will accept the reset command on the first try.

#### Acceptance Criteria

1. WHEN `reset(..., aspects)` is called with
   `aspects.action_pattern == ActionPattern.SINGLE_CALL` THEN the system
   SHALL issue the reset command exactly once.
2. IF the command itself fails THEN the system SHALL raise without
   retrying the command.
3. WHEN the command succeeds THEN the reconnect-verify loop SHALL still
   run (the loop is part of the reset contract, not a retry of the
   command).

### Requirement 10 — SINGLE_CONNECTED_CALL Action Pattern

**User Story:** As a test author operating over a flaky management
network, I want connection-class errors at command issue time to be
retried while application-level errors fail fast.

#### Acceptance Criteria

1. WHEN `reset(..., aspects)` is called with
   `aspects.action_pattern == ActionPattern.SINGLE_CONNECTED_CALL` THEN
   the system SHALL retry only exceptions listed in
   `aspects.allowed_connection_exceptions` when issuing the reset
   command.
2. IF the firmware returns a non-connection application error THEN the
   system SHALL raise immediately.

### Requirement 11 — DO_UNTIL_SUCCESS Action Pattern

**User Story:** As a test author exercising a firmware that occasionally
rejects reset commands under load, I want the reset command itself to
be retried until accepted.

#### Acceptance Criteria

1. WHEN `reset(..., aspects)` is called with
   `aspects.action_pattern == ActionPattern.DO_UNTIL_SUCCESS` THEN the
   system SHALL retry the reset command every
   `aspects.completion_interval` until it is accepted or
   `aspects.completion_timeout` elapses.
2. WHEN the command is ultimately accepted THEN the reconnect-verify
   loop SHALL run with a *fresh* budget equal to
   `aspects.completion_timeout` (the budgets are not shared).

### Requirement 12 — Aspects=None Passthrough

**User Story:** As a caller, I want to omit `aspects` and have the method
use the owning `UefiAgent`/`UefiSession` default.

#### Acceptance Criteria

1. WHEN `reset()` is called with `aspects=None` THEN the system SHALL
   call `resolve_aspects(None, self._aspects)` and use the returned
   instance.
2. IF both arguments are `None` THEN the system SHALL raise
   `ConfigurationError`.

### Requirement 13 — Logging Channel and Reset Events

**User Story:** As an operator, I want reset events to be attributable
and structured.

#### Acceptance Criteria

1. WHEN a reset is issued THEN the system SHALL emit a structured log
   event on channel `uefi.reset` at INFO carrying `host`, `kind`,
   `reason`, and `transport`.
2. WHEN the reconnect-verify loop succeeds THEN the system SHALL emit an
   INFO-level event carrying `elapsed_seconds` and `attempt_count`.
3. WHEN the reconnect-verify loop fails THEN the system SHALL emit an
   ERROR-level event carrying `elapsed_seconds`, `attempt_count`, and
   `last_error` before raising.

### Requirement 14 — `ResetFailedError` Exception Type

**User Story:** As a caller, I want a dedicated exception type for
reset-verify failure so that I can distinguish reset problems from other
`UefiError` subclasses.

#### Acceptance Criteria

1. WHEN reconnect-verify fails THEN the system SHALL raise
   `ResetFailedError` which is a subclass of `UefiError`.
2. WHEN `ResetFailedError` is constructed THEN it SHALL accept the
   kwargs `host`, `kind`, `elapsed_seconds`, `attempt_count`,
   `last_error`, and `transport` and expose them as attributes.
3. WHEN `ResetFailedError.__str__` is called THEN the system SHALL
   produce a multiline message with each field on its own line per the
   multiline-error-message coding standard.
