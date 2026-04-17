# Requirements — Reboot and Verify Workflow

## Introduction

The Reboot and Verify workflow provides a single, test-author-friendly call
that composes `5-system-reset`, `4-agent.verify_connectivity()`, and a
state-carry-forward step so a test can say "reboot the target and come back
ready to issue commands" in one line.  The workflow wraps the lower-level
reset and connectivity primitives, captures any `dmesg`-like console noise
emitted during the reset (useful for triage when the target misbehaves), and
returns a structured result object suitable for attachment to a test report.

This workflow is layer-6 and delegates every per-call behavior decision to
the aspects it receives, forwarding them to its constituent layer-5 features.
It also introduces a `wait_for_shell: bool = True` flag that lets tests opt
for a shallow "ICMP-up" check instead of a full UEFI Shell verification —
useful when the follow-up step will itself re-establish a richer session.

This feature consumes `5-system-reset`, `4-agent`, and `1-logging`.  It is
the bottom of the layer-6 stack — the capsule-update, boot-to-setup, and
boot-to-option-once workflows all build on it.  Relevant UEFI spec sections
are §8.5.1 (`ResetSystem`) and §3.1 (Boot Manager, for the post-reset shell
verification).

## Requirements

### Requirement 1 — Single-Call Reboot-and-Verify Entry Point

**User Story:** As a test author, I want one call that resets the target and
returns only once I can issue a shell command, so that my test body stays
focused on the intent of the test.

#### Acceptance Criteria

1. WHEN the caller invokes `reboot_and_verify(agent, aspects=...)` THEN the
   system SHALL issue a reset via `5-system-reset`, wait for transport
   return, and verify UEFI Shell liveness before returning.
2. WHEN the reset completes successfully and the shell responds THEN the
   system SHALL return a `RebootAndVerifyReport` with `succeeded=True`.
3. IF the shell does not respond within
   `aspects.completion_timeout` THEN the system SHALL raise `ResetFailedError`
   with `details` listing the last observed transport state.
4. WHILE the reset is in progress the system SHALL capture all console
   bytes received on the transport into a buffer and attach them to the
   report as `console_capture` per UEFI spec §8.5.1.

### Requirement 2 — Wait-For-Shell Flag

**User Story:** As a test author, I want to opt out of the full shell
verification when my next step will re-open its own richer session, so that
I do not pay for a redundant shell probe.

#### Acceptance Criteria

1. WHEN `wait_for_shell=True` (default) THEN the system SHALL call
   `agent.verify_connectivity()` before returning.
2. WHEN `wait_for_shell=False` THEN the system SHALL return as soon as the
   target responds to an ICMP echo or equivalent transport-level liveness
   probe.
3. IF the transport does not expose an ICMP probe AND `wait_for_shell=False`
   THEN the system SHALL raise `ConfigurationError` naming the transport
   kind and the unsupported flag combination.
4. WHERE `wait_for_shell=False` the `RebootAndVerifyReport.shell_verified`
   field SHALL be `False` and `icmp_verified` SHALL be `True`.

### Requirement 3 — Console Noise Capture for Triage

**User Story:** As a test author debugging a flaky reset, I want any
firmware-emitted console output during reset attached to the result, so that
I can triage without re-running.

#### Acceptance Criteria

1. WHILE the reset is in progress the system SHALL collect every byte
   received on the transport into an in-memory buffer.
2. WHEN `reboot_and_verify` returns THEN the system SHALL include a
   `console_capture: bytes` field on the report.
3. IF the captured buffer exceeds `aspects.max_capture_bytes` (default 1 MiB)
   THEN the system SHALL truncate from the start and set
   `report.console_capture_truncated=True`.
4. WHERE the transport is Redfish (no raw console) the system SHALL set
   `console_capture=b""` and `console_capture_truncated=False` without
   raising.

### Requirement 4 — State Carry-Forward

**User Story:** As a test author, I want the agent's session-level state
(current map alias, working directory) to survive the reboot, so that my
next command runs in the same environment as before.

#### Acceptance Criteria

1. WHEN the reset is initiated THEN the system SHALL snapshot the agent's
   carry-forward state (map alias, working directory, environment variables).
2. WHEN the post-reset shell verification succeeds THEN the system SHALL
   replay the snapshot onto the new channel before returning.
3. IF replay of any element of the snapshot fails THEN the system SHALL
   raise `UefiShellError` with `details` naming the failed element and
   the partial state restored so far.
4. WHERE `wait_for_shell=False` the system SHALL skip state replay and
   SHALL set `report.state_replayed=False`.

### Requirement 5 — Aspects Delegation to Layer-5 Features

**User Story:** As a test author, I want the workflow's retry and timeout
behavior to be controlled by the same aspects object I already use, so that
I do not maintain two separate configurations.

#### Acceptance Criteria

1. WHEN the caller passes `aspects` THEN the system SHALL forward it to
   `5-system-reset.reset()` unchanged.
2. WHEN the caller passes `aspects` THEN the system SHALL forward it to
   `agent.verify_connectivity()` unchanged.
3. IF `aspects is None` THEN the system SHALL use the agent's
   `instance_default_aspects` via `resolve_aspects`.
4. WHERE the caller passes a `UefiAspects` (not `UefiCmdAspects`) the
   system SHALL accept it and promote to `UefiCmdAspects` internally using
   `merge` semantics for the shell-verification call.

### Requirement 6 — Action Pattern Support

**User Story:** As a test author, I want to choose between fast-fail,
retry-until-success, and do-until-connection-failure patterns for the
reboot-and-verify call, so that my test is appropriately resilient.

#### Acceptance Criteria

1. WHEN `aspects.action_pattern == ActionPattern.SINGLE_CALL` THEN the
   system SHALL attempt the workflow once and propagate any error without
   retry.
2. WHEN `aspects.action_pattern == ActionPattern.DO_UNTIL_SUCCESS` THEN the
   system SHALL retry the workflow every `aspects.completion_interval` until
   success or `aspects.completion_timeout` elapses.
3. WHEN `aspects.action_pattern == ActionPattern.SINGLE_CONNECTED_CALL` THEN
   the system SHALL raise `SemanticError` because a reboot cannot complete
   within a single live connection.
4. WHEN `aspects.action_pattern == ActionPattern.DO_WHILE_SUCCESS` THEN the
   system SHALL raise `SemanticError` — looping while success is not a
   meaningful semantic for a destructive reset.
5. WHEN `aspects.action_pattern == ActionPattern.DO_UNTIL_CONNECTION_FAILURE`
   THEN the system SHALL raise `SemanticError` — the workflow intentionally
   crosses a connection-failure boundary.

### Requirement 7 — Reset Kind Selection

**User Story:** As a test author, I want to choose cold reset vs. warm reset
vs. shutdown-then-start, so that my test covers the correct reset type per
UEFI spec §8.5.1.

#### Acceptance Criteria

1. WHEN the caller omits `reset_kind` THEN the system SHALL default to
   `ResetKind.COLD`.
2. WHEN `reset_kind == ResetKind.WARM` THEN the system SHALL request a warm
   reset via `5-system-reset` per UEFI spec §8.5.1.
3. WHEN `reset_kind == ResetKind.SHUTDOWN` THEN the system SHALL request a
   shutdown followed by a power-on sequence via `5-system-reset`.
4. IF the transport cannot honor the requested `reset_kind` THEN the system
   SHALL raise `ConfigurationError` with the transport kind and the
   unsupported reset kind in `details`.

### Requirement 8 — Result Object Shape

**User Story:** As a test author, I want a structured result, so that my
test framework can attach the full context to the run report.

#### Acceptance Criteria

1. WHEN `reboot_and_verify` returns successfully THEN the report SHALL
   carry: `succeeded: bool`, `reset_kind: ResetKind`,
   `reset_duration_seconds: float`, `icmp_verified: bool`,
   `shell_verified: bool`, `state_replayed: bool`, `console_capture: bytes`,
   `console_capture_truncated: bool`, `attempts: int`, and
   `started_at: datetime`.
2. WHEN the workflow fails THEN the report SHALL still be populated as far
   as the workflow progressed and SHALL be attached to the raised
   exception's `details` list.
3. WHERE the report is serialized via `as_dict()` the
   `console_capture` field SHALL be rendered as a base64-encoded string to
   keep the dict JSON-safe.

### Requirement 9 — Integration Against QEMU/OVMF

**User Story:** As a maintainer, I want an integration test that exercises
the workflow against a real UEFI firmware, so that I can catch regressions
before they hit users.

#### Acceptance Criteria

1. WHEN the test suite runs with `AUTOMOJO_OVMF_TEST=1` THEN the system
   SHALL boot a QEMU VM with OVMF firmware and exercise a cold reset with
   `wait_for_shell=True`.
2. WHEN the OVMF integration test completes THEN the system SHALL assert
   that `report.shell_verified is True` and `report.state_replayed is True`.
3. IF `AUTOMOJO_OVMF_TEST` is not set THEN the integration test SHALL be
   skipped with a clear message rather than failing.

### Requirement 10 — Aspects-None Default Resolution

**User Story:** As a consumer, I want to omit `aspects` entirely and fall
back to the agent's configured default, so that simple tests are simple.

#### Acceptance Criteria

1. WHEN the caller invokes `reboot_and_verify(agent)` with no `aspects`
   argument THEN the system SHALL resolve aspects via
   `resolve_aspects(None, agent.aspects)`.
2. IF the agent has no default aspects THEN the system SHALL raise
   `ConfigurationError` naming the missing default.
