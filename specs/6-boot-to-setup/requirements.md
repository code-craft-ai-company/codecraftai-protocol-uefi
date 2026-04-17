# Requirements — Boot To Setup Workflow

## Introduction

The Boot To Setup workflow drives the target directly into its firmware
Setup UI by setting the `EFI_OS_INDICATIONS_BOOT_TO_FW_UI` bit in
`OsIndications`, issuing a reset, and then exposing the HII navigator from
`5-hii-forms` so a test can programmatically interact with the Setup
screens.  On workflow exit, the original `OsIndications` value is
restored atomically so subsequent boots behave normally.

Before setting the bit, the workflow confirms the firmware advertises
`BOOT_TO_FW_UI` support via `OsIndicationsSupported`, failing fast if the
target cannot honor the request.  This avoids the frustrating case of
issuing a reset that ignores the bit and boots normally, leaving the test
thinking it is in Setup when it is not.

This workflow is layer-6 and delegates aspects to `5-variable-services`,
`6-reboot-and-verify`, and `5-hii-forms`.  Relevant UEFI spec sections
are §8.2 (OsIndications, OsIndicationsSupported) and §8.5.1 (ResetSystem).

## Requirements

### Requirement 1 — Single-Call Entry Point

**User Story:** As a test author, I want one call that boots the target
into Setup and hands me a HII navigator, so that my BIOS-configuration
test does not reinvent the plumbing.

#### Acceptance Criteria

1. WHEN the caller invokes `boot_to_setup(agent, aspects=...)` THEN the
   system SHALL verify `BOOT_TO_FW_UI` support, set the bit in
   `OsIndications`, reboot, and return a `BootToSetupContext` exposing a
   `HiiFormNavigator`.
2. WHEN the caller uses the returned context as a context manager and
   exits THEN the system SHALL restore the prior `OsIndications` value
   and reboot back to normal boot.
3. WHEN the workflow fails at any stage THEN the system SHALL raise the
   appropriate error and attach the partial `BootToSetupReport` to
   `details`.

### Requirement 2 — Firmware Capability Probe

**User Story:** As a test author, I want the workflow to verify that the
firmware actually supports boot-to-Setup, so that I get a crisp
`UefiCapabilityError` instead of a confusing silent normal boot.

#### Acceptance Criteria

1. WHEN the workflow begins THEN the system SHALL read
   `OsIndicationsSupported` from the `EFI_GLOBAL_VARIABLE_GUID` namespace
   per UEFI spec §8.2.
2. IF the `EFI_OS_INDICATIONS_BOOT_TO_FW_UI` bit is not set in
   `OsIndicationsSupported` THEN the system SHALL raise
   `UefiCapabilityError` with `details` listing the observed bitmask in
   hex.
3. WHEN the bit is set THEN the system SHALL proceed to the set-and-reset
   phase.

### Requirement 3 — Atomic OsIndications Mutation

**User Story:** As a test author, I want the workflow to set the
BOOT_TO_FW_UI bit without clobbering other bits the OS may have set, so
that OS-level features (recovery, capsule request) are not disrupted.

#### Acceptance Criteria

1. WHEN setting `OsIndications` THEN the system SHALL read the current
   value first, record it on the report as `os_indications_prior`, and
   write back `prior | EFI_OS_INDICATIONS_BOOT_TO_FW_UI`.
2. IF `OsIndications` does not exist yet THEN the system SHALL treat the
   prior value as `0` and write `EFI_OS_INDICATIONS_BOOT_TO_FW_UI`.
3. WHEN restoring `OsIndications` on context exit THEN the system SHALL
   write exactly `os_indications_prior` — not `current & ~BOOT_TO_FW_UI`,
   since firmware may have set additional bits during the Setup session.

### Requirement 4 — Reset To Setup

**User Story:** As a test author, I want the reset triggered
automatically after the bit is set, so that the Setup screen is actually
visible when the call returns.

#### Acceptance Criteria

1. WHEN `OsIndications` has been updated THEN the system SHALL invoke
   `6-reboot-and-verify` with `wait_for_shell=False` because the shell
   is not available inside the Setup UI.
2. WHEN the reboot completes THEN the system SHALL probe the transport
   for Setup-screen readiness per transport (e.g., detecting the vendor's
   Setup-banner pattern on serial) and SHALL populate
   `report.setup_ready_at: datetime`.
3. IF the Setup banner is not observed within
   `aspects.setup_ready_timeout` (default 120s) THEN the system SHALL
   raise `UefiShellError` with `details` listing the captured console
   bytes so far.

### Requirement 5 — HII Navigator Exposure

**User Story:** As a test author, I want a ready-to-use HII navigator so
that my BIOS-configuration test body is focused on what to configure, not
how to get there.

#### Acceptance Criteria

1. WHEN the Setup banner is detected THEN the system SHALL construct a
   `HiiFormNavigator` from `5-hii-forms` bound to the agent's current
   transport.
2. WHEN the returned `BootToSetupContext` is entered THEN the system
   SHALL expose `context.navigator` of type `HiiFormNavigator`.
3. IF `5-hii-forms` is not available for the active transport THEN the
   system SHALL raise `ConfigurationError` listing transport and the
   unsupported feature.

### Requirement 6 — Restore-On-Exit

**User Story:** As a test author, I want `OsIndications` restored
automatically when my test finishes, so that my next test run does not
mysteriously land in Setup.

#### Acceptance Criteria

1. WHEN `BootToSetupContext.__exit__` is called THEN the system SHALL
   navigate the HII navigator to the "Save and Exit" screen or
   equivalent.
2. WHEN the Setup session ends THEN the system SHALL read `OsIndications`
   and compute the target value as `current_after_setup &
   ~EFI_OS_INDICATIONS_BOOT_TO_FW_UI | os_indications_prior &
   EFI_OS_INDICATIONS_BOOT_TO_FW_UI` so that the original bit's
   state is restored without touching bits the firmware may have modified.
3. WHEN the restore write completes THEN the system SHALL invoke
   `6-reboot-and-verify` with `wait_for_shell=True` so the caller is
   returned to a known-good shell state.
4. IF the restore write fails THEN the system SHALL raise
   `UefiVariableError` with `details` listing the observed and target
   `OsIndications` values.

### Requirement 7 — Structured Report

**User Story:** As a test author, I want a structured report of what the
workflow did, so that my test framework can attach it.

#### Acceptance Criteria

1. WHEN the workflow completes THEN the report SHALL carry:
   `succeeded: bool`, `os_indications_supported: int`,
   `os_indications_prior: int`, `os_indications_written: int`,
   `os_indications_restored: Optional[int]`, `setup_ready_at:
   Optional[datetime]`, `reboot_to_setup_report:
   Optional[RebootAndVerifyReport]`, `reboot_to_normal_report:
   Optional[RebootAndVerifyReport]`, `started_at: datetime`.
2. WHEN serialized via `as_dict()` THEN the report SHALL be JSON-safe.

### Requirement 8 — Aspects Delegation

**User Story:** As a test author, I want one aspects object controlling
both reboots and the HII interactions.

#### Acceptance Criteria

1. WHEN the caller passes `aspects` THEN the system SHALL forward it to
   `5-variable-services`, `6-reboot-and-verify`, and `5-hii-forms`
   unchanged.
2. IF `aspects is None` THEN the system SHALL resolve from the agent
   default via `resolve_aspects`.
3. WHEN `aspects.must_connect=True` THEN the workflow SHALL treat every
   connection failure as fatal for the two reboot steps.

### Requirement 9 — Action Pattern Support

**User Story:** As a test author, I want to pick the retry semantics,
knowing that the workflow crosses two reboot boundaries.

#### Acceptance Criteria

1. WHEN `aspects.action_pattern == ActionPattern.SINGLE_CALL` THEN the
   system SHALL attempt the workflow once.
2. WHEN `aspects.action_pattern == ActionPattern.DO_UNTIL_SUCCESS` THEN
   the system SHALL retry the entry phase only; the exit phase is never
   retried once inside Setup.
3. WHEN `aspects.action_pattern == ActionPattern.SINGLE_CONNECTED_CALL`
   THEN the system SHALL raise `SemanticError` — the workflow crosses
   reset boundaries.
4. WHEN `aspects.action_pattern == ActionPattern.DO_WHILE_SUCCESS` THEN
   the system SHALL raise `SemanticError`.
5. WHEN `aspects.action_pattern ==
   ActionPattern.DO_UNTIL_CONNECTION_FAILURE` THEN the system SHALL raise
   `SemanticError`.

### Requirement 10 — Integration Against OVMF

**User Story:** As a maintainer, I want an integration test that proves
the workflow works end-to-end against a real UEFI firmware.

#### Acceptance Criteria

1. WHEN the test suite runs with `AUTOMOJO_OVMF_TEST=1` THEN the system
   SHALL boot an OVMF VM that advertises `BOOT_TO_FW_UI` support and
   drive the full round-trip.
2. WHEN the integration test completes THEN the system SHALL assert
   `report.setup_ready_at is not None`, the Setup navigator could read
   at least one form, and `os_indications_restored == os_indications_prior`.
3. IF the OVMF fixture does not advertise `BOOT_TO_FW_UI` THEN the test
   SHALL be skipped with a clear message.

### Requirement 11 — Aspects-None Passthrough

**User Story:** As a consumer, I want to omit `aspects` and fall back to
the agent default.

#### Acceptance Criteria

1. WHEN the caller invokes `boot_to_setup(agent)` with no `aspects` THEN
   the system SHALL resolve via `resolve_aspects(None, agent.aspects)`.
2. IF both are `None` THEN the system SHALL raise `ConfigurationError`.
