# Requirements — Capsule Update Workflow

## Introduction

The Capsule Update Workflow composes `5-firmware-capsules`,
`6-reboot-and-verify`, and `5-system-tables` into one test-author-friendly
call: deliver a capsule to the target, reboot, read back the capsule report,
and verify that the corresponding ESRT entry advanced.  The workflow exists
because a correct firmware-update test otherwise requires five to eight
distinct API calls and careful state-carry-forward between them — easy to
get wrong, and the kind of boilerplate that degrades test readability.

This workflow fails fast when preconditions do not hold — most importantly,
when the target does not expose an ESRT, capsule-report verification would
be impossible and the workflow refuses to proceed.  On success, it emits a
`CapsuleUpdateReport` containing the input capsule's SHA-256, the
firmware-reported status GUID from the Capsule Report variable, and
ESRT-before and ESRT-after snapshots for side-by-side comparison.

This workflow is layer-6 and delegates per-call behavior to the aspects it
receives, forwarding them to each constituent feature.  Relevant UEFI spec
sections are §8.5.3 (UpdateCapsule), §8.5.5 (Delivery of Capsules), §8.5.6
(Capsule Report Variable), and §23.4 (EFI System Resource Table).

## Requirements

### Requirement 1 — Single-Call Capsule Update Entry Point

**User Story:** As a test author, I want one call that drives a full
capsule-update cycle and returns a structured report, so that my firmware
test fits in a few lines.

#### Acceptance Criteria

1. WHEN the caller invokes `capsule_update_workflow(agent, capsule_bytes,
   aspects=...)` THEN the system SHALL compute the capsule's SHA-256,
   snapshot ESRT, deliver the capsule, reboot, read the capsule report, and
   snapshot ESRT again.
2. WHEN the workflow completes successfully THEN the system SHALL return a
   `CapsuleUpdateReport` with `succeeded=True`.
3. WHEN the workflow fails at any stage THEN the system SHALL raise the
   appropriate error and attach the partial report to the exception's
   `details` list.

### Requirement 2 — ESRT Precondition Gate

**User Story:** As a test author, I want the workflow to refuse to run when
the target cannot report capsule outcomes, so that I do not mis-attribute
a silent failure to a "successful" update.

#### Acceptance Criteria

1. WHEN the workflow begins THEN the system SHALL read ESRT via
   `5-system-tables` per UEFI spec §23.4.
2. IF ESRT is absent or empty THEN the system SHALL raise
   `UefiCapsuleError` with `details` naming the failed precondition.
3. WHERE the caller passes `require_esrt=False` the system SHALL skip the
   ESRT precondition check and SHALL set
   `report.esrt_before=None` and `report.esrt_after=None`.

### Requirement 3 — Capsule Identity Hashing

**User Story:** As a maintainer auditing a test run, I want the input
capsule's SHA-256 in the result, so that I can tie a pass/fail outcome to
a specific binary.

#### Acceptance Criteria

1. WHEN the workflow begins THEN the system SHALL compute the SHA-256 of
   the input capsule bytes and store it on the report as
   `input_capsule_sha256` (lowercase hex, no prefix).
2. WHEN the caller passes a path instead of bytes THEN the system SHALL
   load the file and hash it before delivery.
3. IF the input is neither `bytes` nor a path-like THEN the system SHALL
   raise `ConfigurationError` naming the received type.

### Requirement 4 — Delivery Mode Selection

**User Story:** As a test author, I want to choose between runtime-service
delivery, mass-storage delivery, and Redfish delivery, so that my test
matches the target's capabilities.

#### Acceptance Criteria

1. WHEN the caller omits `delivery_mode` THEN the system SHALL default to
   `CapsuleDeliveryMode.RUNTIME_SERVICE` per UEFI spec §8.5.3.
2. WHEN `delivery_mode == CapsuleDeliveryMode.MASS_STORAGE` THEN the
   system SHALL stage the capsule on the ESP per UEFI spec §8.5.5.
3. WHEN `delivery_mode == CapsuleDeliveryMode.REDFISH` THEN the system
   SHALL upload via Redfish `UpdateService` multipart.
4. IF the selected delivery mode is not supported by the active transport
   THEN the system SHALL raise `ConfigurationError` with `details` naming
   transport kind and requested mode.

### Requirement 5 — Reboot and Capsule-Report Readback

**User Story:** As a test author, I want the workflow to reboot on my
behalf and read the firmware's capsule report, so that my test does not
need to compose the intermediate primitives.

#### Acceptance Criteria

1. WHEN the capsule has been delivered THEN the system SHALL invoke
   `6-reboot-and-verify` with `wait_for_shell=True`.
2. WHEN the post-reboot shell is available THEN the system SHALL enumerate
   Capsule Report variables per UEFI spec §8.5.6 and capture the status
   GUID of the capsule corresponding to the input SHA-256.
3. IF no matching Capsule Report variable is found within
   `aspects.completion_timeout` THEN the system SHALL raise
   `UefiCapsuleError` with `details` listing the variables observed.
4. WHEN the status GUID is read THEN the system SHALL store it on the
   report as `reported_status_guid: uuid.UUID`.

### Requirement 6 — ESRT Advancement Verification

**User Story:** As a test author, I want a clear pass/fail on whether the
ESRT entry for the updated firmware actually advanced, so that I catch
silent no-ops where the capsule was accepted but nothing changed.

#### Acceptance Criteria

1. WHEN the post-reboot ESRT snapshot is available THEN the system SHALL
   diff it against the pre-reboot ESRT snapshot by
   `(firmware_class_guid, firmware_version)` tuples.
2. IF no ESRT entry with a matching `firmware_class_guid` shows a higher
   `firmware_version` post-reboot THEN the system SHALL set
   `report.esrt_advanced=False`.
3. IF at least one matching entry advanced THEN the system SHALL set
   `report.esrt_advanced=True`.
4. WHERE `aspects.must_advance=True` the system SHALL raise
   `UefiCapsuleError` when `esrt_advanced is False`.

### Requirement 7 — Structured Report Output

**User Story:** As a test author, I want a structured result, so that my
framework can attach the full context to the test-run report.

#### Acceptance Criteria

1. WHEN the workflow completes THEN the report SHALL carry:
   `succeeded: bool`, `input_capsule_sha256: str`,
   `delivery_mode: CapsuleDeliveryMode`, `reported_status_guid:
   Optional[uuid.UUID]`, `esrt_before: Optional[EsrtSnapshot]`,
   `esrt_after: Optional[EsrtSnapshot]`, `esrt_advanced: bool`,
   `reboot_report: RebootAndVerifyReport`, and `started_at: datetime`.
2. WHEN the report is serialized via `as_dict()` THEN the system SHALL
   produce a JSON-safe dict suitable for test-artifact attachment.

### Requirement 8 — Safety Gates Before Destructive Write

**User Story:** As a safety-conscious operator, I want preflight checks
before the capsule is actually delivered, so that obvious failures are
caught before I reboot the target.

#### Acceptance Criteria

1. WHEN the workflow begins THEN the system SHALL verify
   `OsIndicationsSupported` reports capsule support per UEFI spec §8.5.1
   before any delivery attempt.
2. IF the capsule header is malformed (wrong magic, truncated) THEN the
   system SHALL raise `UefiCapsuleError` before any delivery is attempted.
3. IF the target reports insufficient NV storage to stage the capsule (via
   `QueryVariableInfo`) THEN the system SHALL raise `UefiCapsuleError` with
   `details` naming the free-space shortfall.
4. WHERE `aspects.dry_run=True` the system SHALL perform all preflight
   checks AND produce a report but SHALL NOT deliver the capsule or
   reboot, setting `report.dry_run=True`.

### Requirement 9 — Aspects Delegation

**User Story:** As a test author, I want the workflow's retry and timeout
behavior controlled by a single aspects object, so that I do not maintain
multiple configurations.

#### Acceptance Criteria

1. WHEN the caller passes `aspects` THEN the system SHALL forward it to
   `5-firmware-capsules`, `6-reboot-and-verify`, and `5-system-tables`
   unchanged.
2. IF `aspects is None` THEN the system SHALL resolve from the agent's
   default via `resolve_aspects`.
3. WHEN `aspects.must_connect=True` is set THEN the workflow SHALL treat
   every connection failure as fatal (no silent retry).

### Requirement 10 — Action Pattern Support

**User Story:** As a test author, I want to pick the retry semantics for a
full capsule update cycle, so that my firmware test can be appropriately
resilient.

#### Acceptance Criteria

1. WHEN `aspects.action_pattern == ActionPattern.SINGLE_CALL` THEN the
   system SHALL attempt the workflow once.
2. WHEN `aspects.action_pattern == ActionPattern.DO_UNTIL_SUCCESS` THEN
   the system SHALL retry the workflow up to `aspects.completion_timeout`
   on transient delivery failures only.
3. WHEN `aspects.action_pattern == ActionPattern.SINGLE_CONNECTED_CALL`
   THEN the system SHALL raise `SemanticError`.
4. WHEN `aspects.action_pattern == ActionPattern.DO_WHILE_SUCCESS` THEN
   the system SHALL raise `SemanticError`.
5. WHEN `aspects.action_pattern ==
   ActionPattern.DO_UNTIL_CONNECTION_FAILURE` THEN the system SHALL raise
   `SemanticError`.

### Requirement 11 — Integration Against OVMF With Test-Signed Capsule

**User Story:** As a maintainer, I want an integration test against OVMF
with a test-signed capsule, so that I catch regressions in the full
workflow.

#### Acceptance Criteria

1. WHEN the test suite runs with `AUTOMOJO_OVMF_TEST=1` and
   `AUTOMOJO_CAPSULE_FIXTURE` set to a test-signed capsule THEN the
   system SHALL execute the full workflow against an OVMF VM.
2. WHEN the integration test completes THEN the system SHALL assert
   `report.esrt_advanced is True` and `report.reported_status_guid` equals
   `EFI_CAPSULE_REPORT_STATUS_SUCCESS_GUID`.
3. IF the capsule fixture or OVMF environment is unavailable THEN the
   test SHALL be skipped with a clear message.

### Requirement 12 — Dry-Run Reporting

**User Story:** As a test author doing a staging pass, I want a dry-run
mode that runs every preflight and reports what would be delivered without
actually rebooting the target, so that I can validate my test scaffolding
safely.

#### Acceptance Criteria

1. WHEN `aspects.dry_run=True` THEN the system SHALL compute the capsule
   hash, run every preflight check, and return a report with
   `dry_run=True`, `delivery_mode` as configured, `esrt_before` populated,
   and `esrt_after=None`.
2. WHEN dry-run is active THEN the system SHALL NOT call `UpdateCapsule`,
   SHALL NOT stage a capsule file on the ESP, and SHALL NOT reboot.
3. WHEN dry-run is active AND a preflight check fails THEN the system
   SHALL raise the same error it would raise in non-dry-run mode.
