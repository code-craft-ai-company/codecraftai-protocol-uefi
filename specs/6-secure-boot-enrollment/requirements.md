# Requirements — Secure Boot Enrollment Workflow

## Introduction

The Secure Boot Enrollment workflow composes `5-secure-boot` and
`5-variable-services` into a single call that drives the target into
SetupMode, enrolls the Platform Key (PK), Key Exchange Key (KEK), signature
database (db), and forbidden signature database (dbx) in the order required
by the UEFI specification, verifies that each write took effect by
re-reading the variable and comparing contents, and leaves the target in
User Mode with Secure Boot enabled.

Enrollment is a multi-step, not-safely-partial operation: a partial
enrollment may leave the target unable to boot or in an ambiguous security
state.  For this reason the workflow refuses to operate unless
`aspects.must_connect=True`, and it also exposes a dry-run mode that
reports exactly what would be written (which variables, in what order, with
what payload digests) without writing anything.

This workflow is layer-6 and is the highest-risk operation in the library.
Relevant UEFI spec sections are §32 (Secure Boot and Driver Signing), §8.2
(Variable Services), and §8.2.5 (Authenticated Variables).

## Requirements

### Requirement 1 — Single-Call Enrollment Entry Point

**User Story:** As a test author, I want one call that enrolls the entire
Secure Boot key hierarchy, so that my security test does not reinvent the
enrollment sequence.

#### Acceptance Criteria

1. WHEN the caller invokes `secure_boot_enrollment(agent, bundle,
   aspects=...)` THEN the system SHALL enter SetupMode (verify
   `SetupMode==1`), enroll PK/KEK/db/dbx in the spec-required order per
   UEFI §32, verify each write, and exit SetupMode by enrolling PK.
2. WHEN the workflow completes successfully THEN the system SHALL return a
   `SecureBootEnrollmentReport` with `succeeded=True`.
3. WHEN any verification fails THEN the system SHALL raise
   `UefiSecureBootError` and attach the partial enrollment report to
   `details`.

### Requirement 2 — Must-Connect Enforcement

**User Story:** As a safety-conscious operator, I want the workflow to
refuse to run unless I have explicitly acknowledged that connection
failures are fatal, so that a partial enrollment cannot happen.

#### Acceptance Criteria

1. IF `aspects.must_connect is not True` THEN the system SHALL raise
   `ConfigurationError` with `details=["secure boot enrollment requires
   aspects.must_connect=True", "partial enrollment is not safe"]` before
   any variable is read or written.
2. WHEN `aspects.must_connect is True` THEN the system SHALL proceed.
3. WHEN any connection failure occurs mid-workflow THEN the system SHALL
   raise `ConnectionError` immediately without retry, per `must_connect`
   semantics.

### Requirement 3 — SetupMode Precondition

**User Story:** As a test author, I want the workflow to verify the
target is in SetupMode before attempting writes, so that an already-enrolled
target does not cause an authenticated-write failure halfway through.

#### Acceptance Criteria

1. WHEN the workflow begins THEN the system SHALL read `SetupMode`,
   `AuditMode`, `DeployedMode`, and `SecureBoot` variables per UEFI spec
   §32.3.
2. IF `SetupMode != 1` THEN the system SHALL raise
   `UefiSecureBootError` with `details` listing all four state variables.
3. WHERE `aspects.allow_user_mode_exit=True` AND `DeployedMode == 0` the
   system SHALL attempt a PK-delete-then-reenter-SetupMode sequence using
   the current PK's authenticated-write key to transition to SetupMode
   before enrollment.
4. IF `DeployedMode == 1` THEN the system SHALL raise
   `UefiSecureBootError` unconditionally — exit from DeployedMode requires
   platform-specific mechanisms outside this workflow's scope.

### Requirement 4 — Enrollment Order

**User Story:** As a test author, I want the workflow to enroll keys in
the order mandated by the spec, so that I do not have to remember "dbx
before db, KEK before PK, and always PK last" myself.

#### Acceptance Criteria

1. WHEN the workflow writes enrollments THEN the system SHALL write in the
   order: `dbx`, `db`, `KEK`, `PK` per UEFI spec §32.
2. WHEN `dbx` contains multiple entries THEN the system SHALL append each
   entry using the `APPEND_WRITE` attribute per UEFI spec §8.2.5.
3. WHEN enrolling PK THEN the system SHALL use
   `TIME_BASED_AUTHENTICATED_WRITE_ACCESS` with a monotonically-increasing
   `EFI_TIME` payload per UEFI spec §8.2.5.
4. IF `bundle.pk is None` THEN the system SHALL raise
   `ConfigurationError` — PK is required to exit SetupMode.

### Requirement 5 — Per-Write Verification

**User Story:** As a test author, I want each enrollment verified by
re-reading the variable and comparing contents, so that a silent accept
cannot masquerade as a successful write.

#### Acceptance Criteria

1. WHEN each of `dbx`, `db`, `KEK`, `PK` is written THEN the system SHALL
   immediately re-read the variable via `5-variable-services`.
2. WHEN re-reading THEN the system SHALL compare the returned signature
   list against the expected payload by signature-entry content (not raw
   bytes, to accommodate firmware reserialization).
3. IF any re-read does not contain every expected entry THEN the system
   SHALL raise `UefiSecureBootError` naming the missing entries.
4. WHEN the verification passes THEN the system SHALL record the
   verified variable in `report.writes_verified`.

### Requirement 6 — Dry-Run Mode

**User Story:** As a test author doing a staging pass, I want a dry-run
mode that reports what would be written without writing, so that I can
validate my bundle before running for real.

#### Acceptance Criteria

1. WHEN `aspects.dry_run=True` THEN the system SHALL read the current
   SecureBoot state, parse and validate `bundle`, compute the payload
   digests of each variable that would be written, and return a report
   with `dry_run=True`.
2. WHEN dry-run is active THEN the system SHALL NOT call any setvar or
   authenticated-write operation.
3. WHEN dry-run is active AND any precondition fails (not in SetupMode,
   bundle invalid) THEN the system SHALL raise the same error as
   non-dry-run mode.

### Requirement 7 — Bundle Shape

**User Story:** As a test author, I want a structured bundle type for the
keys and signatures I am enrolling, so that my test is explicit about what
is going on the target.

#### Acceptance Criteria

1. WHEN the caller constructs `SecureBootBundle(pk=..., kek=[...], db=[...],
   dbx=[...])` THEN the system SHALL accept it.
2. IF `pk` is not exactly one signature list entry THEN the system SHALL
   raise `ConfigurationError` — PK is a single key per UEFI spec §32.
3. WHEN each entry is passed THEN the system SHALL accept either a
   pre-wrapped `EFI_VARIABLE_AUTHENTICATION_2` descriptor or an unwrapped
   `(signer, certificate, payload)` triple and SHALL assemble the
   descriptor internally via `5-secure-boot`.
4. IF the bundle is empty (no pk, no kek, no db, no dbx) THEN the system
   SHALL raise `ConfigurationError`.

### Requirement 8 — Exit SetupMode Via PK Enrollment

**User Story:** As a test author, I want the workflow to leave the target
in User Mode with Secure Boot enabled, so that a subsequent signed-image
test has meaningful preconditions.

#### Acceptance Criteria

1. WHEN PK is enrolled AND verification succeeds THEN the system SHALL
   re-read `SetupMode` and confirm it transitions to `0`.
2. WHEN `SetupMode` is `0` THEN the system SHALL re-read `SecureBoot` and
   confirm it is `1` per UEFI spec §32.3.
3. IF `SetupMode` remains `1` after PK enrollment OR `SecureBoot` is `0`
   THEN the system SHALL raise `UefiSecureBootError` with `details` listing
   the observed state.

### Requirement 9 — Structured Report

**User Story:** As a test author, I want a structured report with every
action the workflow took, so that my test framework can attach it to the
run record.

#### Acceptance Criteria

1. WHEN the workflow completes THEN the report SHALL carry:
   `succeeded: bool`, `dry_run: bool`, `writes_planned: list[VariableWrite]`,
   `writes_verified: list[VariableWrite]`, `setup_mode_before: int`,
   `setup_mode_after: int`, `secure_boot_before: int`,
   `secure_boot_after: int`, `deployed_mode_before: int`, `started_at:
   datetime`.
2. WHEN serialized via `as_dict()` THEN the report SHALL be JSON-safe;
   raw payloads SHALL be represented by SHA-256 hex digests, not inlined
   bytes.

### Requirement 10 — Aspects Delegation

**User Story:** As a test author, I want the workflow's retry and timeout
behavior controlled by the same aspects object I already use, so that
configuration is not duplicated.

#### Acceptance Criteria

1. WHEN the caller passes `aspects` THEN the system SHALL forward it to
   `5-secure-boot` and `5-variable-services` unchanged.
2. IF `aspects is None` THEN the system SHALL resolve from the agent
   default via `resolve_aspects`.
3. WHEN forwarding THEN the workflow SHALL enforce `must_connect=True` on
   every constituent call (layering an override on the forwarded aspects
   if needed).

### Requirement 11 — Action Pattern Support

**User Story:** As a test author, I want to pick an appropriate retry
pattern, knowing the workflow has strict must-connect semantics.

#### Acceptance Criteria

1. WHEN `aspects.action_pattern == ActionPattern.SINGLE_CALL` THEN the
   system SHALL attempt the workflow once.
2. WHEN `aspects.action_pattern == ActionPattern.SINGLE_CONNECTED_CALL`
   THEN the system SHALL attempt the workflow once within a single
   connected session.
3. WHEN `aspects.action_pattern == ActionPattern.DO_UNTIL_SUCCESS` THEN
   the system SHALL raise `SemanticError` — partial enrollment must not
   be retried blindly.
4. WHEN `aspects.action_pattern == ActionPattern.DO_WHILE_SUCCESS` THEN
   the system SHALL raise `SemanticError`.
5. WHEN `aspects.action_pattern ==
   ActionPattern.DO_UNTIL_CONNECTION_FAILURE` THEN the system SHALL raise
   `SemanticError`.

### Requirement 12 — Integration Against OVMF With Test Certificates

**User Story:** As a maintainer, I want an integration test against OVMF
with prebuilt test certificates, so that I catch regressions.

#### Acceptance Criteria

1. WHEN the test suite runs with `AUTOMOJO_OVMF_TEST=1` and
   `AUTOMOJO_SECURE_BOOT_FIXTURES` pointing at a directory of
   PK/KEK/db/dbx test certificates THEN the system SHALL drive a full
   enrollment against an OVMF VM started in SetupMode.
2. WHEN the enrollment completes THEN the system SHALL assert
   `report.setup_mode_after == 0` and `report.secure_boot_after == 1`.
3. WHEN the dbx corpus is non-empty THEN the system SHALL assert each
   dbx entry appears in the post-enrollment re-read.
4. IF the OVMF fixture is unavailable THEN the test SHALL be skipped
   with a clear message.
