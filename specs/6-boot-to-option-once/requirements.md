# Requirements — Boot To Option Once Workflow

## Introduction

The Boot To Option Once workflow boots a specified UEFI boot option
exactly once without changing `BootOrder`, returning to the normal boot
sequence afterwards.  It does so by writing `BootNext` for the target
option, issuing a reset, and then verifying on the next boot that
`BootCurrent` reflects the option the test requested.  This is the
standard mechanism for scheduled-shell-entry tests, one-shot HTTP-boot
verifications, and for validating that a newly-installed `Boot####`
entry works before promoting it into `BootOrder`.

The workflow also enforces the firmware's spec-mandated behavior that
`BootNext` is automatically cleared after the next boot (UEFI §3.1.5): if
`BootNext` is still observed to contain the target option after boot,
the workflow raises `UefiVariableError` because the firmware is
noncompliant and the test's one-shot assumption has been violated.

This workflow is layer-6 and composes `5-variable-services`,
`5-boot-manager`, and `6-reboot-and-verify`.  Relevant UEFI spec sections
are §3.1.5 (Boot Manager Processing of `BootNext`), §3.3 (Globally
Defined Variables), and §8.5.1 (ResetSystem).

## Requirements

### Requirement 1 — Single-Call Boot-Next Entry Point

**User Story:** As a test author, I want one call that boots a specific
option exactly once, so that my one-shot boot test is a single line.

#### Acceptance Criteria

1. WHEN the caller invokes `boot_to_option_once(agent, option_id,
   aspects=...)` THEN the system SHALL verify `Boot{option_id:04X}`
   exists, write `BootNext = option_id`, reboot, and verify
   `BootCurrent` post-reset matches.
2. WHEN the workflow completes successfully THEN the system SHALL return
   a `BootToOptionOnceReport` with `succeeded=True`.
3. WHEN the workflow fails THEN the system SHALL raise the specific
   error and attach the partial report to `details`.

### Requirement 2 — Boot Option Existence Check

**User Story:** As a test author, I want the workflow to verify the
target option exists before writing `BootNext`, so that I do not reboot
into a broken `BootNext` that the firmware will silently ignore.

#### Acceptance Criteria

1. WHEN the workflow begins THEN the system SHALL read
   `Boot{option_id:04X}` via `5-variable-services` / `5-boot-manager` per
   UEFI spec §3.1.3.
2. IF the variable does not exist THEN the system SHALL raise
   `UefiVariableError` with `details=["Boot{option_id:04X} not present
   on target"]`.
3. WHEN the variable exists but cannot be decoded as `EFI_LOAD_OPTION`
   THEN the system SHALL raise `UefiVariableError` with the decode
   error in `details`.
4. WHERE `aspects.skip_decode=True` the system SHALL skip the decode
   step but still require the variable's presence.

### Requirement 3 — BootOrder Preservation

**User Story:** As a test author, I want BootOrder left unchanged by the
workflow, so that my test does not disrupt the rest of the lab's boot
sequencing assumptions.

#### Acceptance Criteria

1. WHEN the workflow begins THEN the system SHALL read `BootOrder` and
   record it on the report as `boot_order_before`.
2. WHEN the workflow completes THEN the system SHALL re-read `BootOrder`
   and verify it matches `boot_order_before` byte-for-byte.
3. IF `BootOrder` changed THEN the system SHALL raise
   `UefiVariableError` with both values in `details`.

### Requirement 4 — BootNext Write

**User Story:** As a test author, I want the workflow to write `BootNext`
with the correct attributes, so that my one-shot request is honored
across a reset.

#### Acceptance Criteria

1. WHEN writing `BootNext` THEN the system SHALL write a 2-byte
   little-endian `UINT16` per UEFI spec §3.3.
2. WHEN writing `BootNext` THEN the system SHALL use attributes
   `NON_VOLATILE | BOOTSERVICE_ACCESS | RUNTIME_ACCESS`.
3. WHEN a pre-existing `BootNext` is present THEN the system SHALL
   record its value as `boot_next_before` on the report and overwrite it.
4. IF writing `BootNext` fails THEN the system SHALL raise
   `UefiVariableError` with the `efi_status` captured in the exception.

### Requirement 5 — Reboot and BootCurrent Verification

**User Story:** As a test author, I want the workflow to reboot and
confirm `BootCurrent` post-reset reflects the requested option, so that
I have proof the firmware honored the one-shot request.

#### Acceptance Criteria

1. WHEN `BootNext` has been written THEN the system SHALL invoke
   `6-reboot-and-verify` with `wait_for_shell=True`.
2. WHEN the post-reboot shell is reachable THEN the system SHALL read
   `BootCurrent` per UEFI spec §3.3.
3. IF `BootCurrent != option_id` THEN the system SHALL raise
   `UefiVariableError` with both values and the captured reboot console
   in `details`.
4. WHEN `BootCurrent == option_id` THEN the system SHALL record it on
   the report as `boot_current_after`.

### Requirement 6 — BootNext Auto-Clear Verification

**User Story:** As a test author, I want the workflow to verify that the
firmware cleared `BootNext` automatically, per UEFI spec §3.1.5, so that
a noncompliant firmware does not leave my target in a permanent one-shot
loop.

#### Acceptance Criteria

1. WHEN post-reboot checks begin THEN the system SHALL attempt to read
   `BootNext` via `5-variable-services`.
2. WHEN `BootNext` does not exist (expected per UEFI §3.1.5) THEN the
   system SHALL record `boot_next_cleared=True` on the report.
3. IF `BootNext` still exists AND its value equals `option_id` THEN the
   system SHALL raise `UefiVariableError` with `details=["firmware did
   not clear BootNext after one-shot boot (UEFI §3.1.5 violation)",
   "option_id: ...", "BootNext observed: ..."]`.
4. WHERE `BootNext` exists but its value differs from `option_id` the
   system SHALL record `boot_next_cleared=False`,
   `boot_next_after=<observed>` and raise `UefiVariableError` with the
   observed value.

### Requirement 7 — Cleanup On Failure

**User Story:** As a test author, I want the workflow to clean up
`BootNext` if the workflow fails before the reboot, so that a later
normal boot is not accidentally redirected.

#### Acceptance Criteria

1. IF the workflow fails AFTER writing `BootNext` but BEFORE the reboot
   THEN the system SHALL attempt to delete `BootNext` before raising.
2. IF the cleanup-delete fails THEN the system SHALL append the cleanup
   failure to the primary exception's `details` but SHALL still raise the
   primary exception.
3. WHEN the workflow fails AFTER the reboot THEN cleanup is not required
   because the firmware is spec-required to have cleared `BootNext`
   itself.

### Requirement 8 — Structured Report

**User Story:** As a test author, I want a structured report, so that my
test framework can attach it to the run record.

#### Acceptance Criteria

1. WHEN the workflow completes THEN the report SHALL carry:
   `succeeded: bool`, `option_id: int`, `boot_order_before: bytes`,
   `boot_next_before: Optional[int]`, `boot_current_after: Optional[int]`,
   `boot_next_after: Optional[int]`, `boot_next_cleared: bool`,
   `reboot_report: Optional[RebootAndVerifyReport]`, `started_at:
   datetime`.
2. WHEN serialized via `as_dict()` THEN the report SHALL be JSON-safe.

### Requirement 9 — Aspects Delegation

**User Story:** As a test author, I want the workflow's retry and timeout
behavior controlled by the aspects object I already use.

#### Acceptance Criteria

1. WHEN the caller passes `aspects` THEN the system SHALL forward it to
   `5-variable-services`, `5-boot-manager`, and `6-reboot-and-verify`
   unchanged.
2. IF `aspects is None` THEN the system SHALL resolve from the agent
   default via `resolve_aspects`.
3. WHEN `aspects.must_connect=True` THEN the workflow SHALL treat every
   connection failure as fatal.

### Requirement 10 — Action Pattern Support

**User Story:** As a test author, I want to pick the retry semantics for
a reboot-crossing workflow.

#### Acceptance Criteria

1. WHEN `aspects.action_pattern == ActionPattern.SINGLE_CALL` THEN the
   system SHALL attempt the workflow once.
2. WHEN `aspects.action_pattern == ActionPattern.DO_UNTIL_SUCCESS` THEN
   the system SHALL retry the full workflow up to
   `aspects.completion_timeout`.
3. WHEN `aspects.action_pattern == ActionPattern.SINGLE_CONNECTED_CALL`
   THEN the system SHALL raise `SemanticError`.
4. WHEN `aspects.action_pattern == ActionPattern.DO_WHILE_SUCCESS` THEN
   the system SHALL raise `SemanticError`.
5. WHEN `aspects.action_pattern ==
   ActionPattern.DO_UNTIL_CONNECTION_FAILURE` THEN the system SHALL raise
   `SemanticError`.

### Requirement 11 — Integration Against OVMF With Shell Boot Option

**User Story:** As a maintainer, I want an integration test that proves
the workflow works against a real UEFI firmware with a known Shell boot
option.

#### Acceptance Criteria

1. WHEN the test suite runs with `CCAI_OVMF_TEST=1` THEN the system
   SHALL boot an OVMF VM that has a known UEFI Shell `Boot####` entry
   and drive the workflow against that option.
2. WHEN the integration test completes THEN the system SHALL assert
   `report.boot_current_after == option_id` AND
   `report.boot_next_cleared is True` AND
   `report.boot_order_before` was preserved.
3. IF the OVMF fixture or Shell boot option is unavailable THEN the
   test SHALL be skipped with a clear message.

### Requirement 12 — Aspects-None Passthrough

**User Story:** As a consumer, I want to omit `aspects` and fall back to
the agent default.

#### Acceptance Criteria

1. WHEN the caller invokes `boot_to_option_once(agent, option_id)` with
   no `aspects` argument THEN the system SHALL resolve via
   `resolve_aspects(None, agent.aspects)`.
2. IF both are `None` THEN the system SHALL raise `ConfigurationError`.
