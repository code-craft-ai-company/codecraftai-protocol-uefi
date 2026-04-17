# Requirements — Firmware Capsules

## Introduction

The Firmware Capsules feature builds, delivers, and verifies UEFI firmware-
update capsules as defined in UEFI Specification §8.5.3 (UpdateCapsule
runtime service), §8.5.5 (Delivery of Capsules via File on Mass Storage
Device), §8.5.6 (Delivery of Capsules via Persisted Across Reset variables),
and §23 (Firmware Management Protocol).  It delivers three delivery methods
and three independently testable classes:

* `CapsuleBuilder` — Assembles an `EFI_CAPSULE_HEADER`-conformant capsule
  with an optional FMP payload wrapper per §23.3.
* `CapsuleDelivery` — Sends the capsule to the target via one of:
  * **OS-runtime** — `/dev/efi_capsule_loader` with `fwupdmgr` fallback.
  * **Mass storage** — Drops the capsule into `/EFI/UpdateCapsule/` on the
    ESP per §8.5.5.
  * **Redfish** — `UpdateService.SimpleUpdate` or the multipart push
    endpoint.
* `CapsuleReport` — Polls `EFI_CAPSULE_REPORT_GUID` variables after the
  next boot to fetch per-capsule outcome per §8.5.6.

Payload signing is strictly delegated to callers.  This feature never loads
private keys from disk, never invokes a signing command, and never embeds
signatures.  Callers pass a signer callable or a pre-signed payload; the
library wraps it into the capsule envelope and delivers it.  That boundary
is the cornerstone of the feature's security contract.

Measured-interop timeouts apply to the Redfish delivery task completion and
to the post-reboot report polling.

## Requirements

### Requirement 1 — Capsule Header Conformance

**User Story:** As a firmware engineer, I want to build an
`EFI_CAPSULE_HEADER` that conforms to §8.5.3, so that my capsule is
accepted by the firmware's UpdateCapsule path.

#### Acceptance Criteria

1. WHEN `CapsuleBuilder(header=header, payload=payload).build()` is invoked
   THEN the system SHALL produce a byte string whose first 28 bytes form a
   valid `EFI_CAPSULE_HEADER` with correct `CapsuleImageSize` equal to the
   total length and `HeaderSize` equal to 28 (or the caller-overridden
   value when a vendor payload precedes the data).
2. WHEN the caller passes a `CapsuleGuid` to `CapsuleBuilder` THEN the
   system SHALL place it into bytes 0–15 of the header in little-endian
   mixed form per §2.3.1.
3. WHEN the caller sets `persist_across_reset=True` THEN the system SHALL
   set `CAPSULE_FLAGS_PERSIST_ACROSS_RESET` (0x00010000) in the `Flags`
   field.
4. WHEN the caller sets `populate_system_table=True` THEN the system SHALL
   set `CAPSULE_FLAGS_POPULATE_SYSTEM_TABLE` (0x00020000) in the `Flags`
   field and SHALL also set persist-across-reset since the spec requires
   both together.
5. WHEN the caller sets `initiate_reset=True` THEN the system SHALL set
   `CAPSULE_FLAGS_INITIATE_RESET` (0x00040000).
6. IF the payload byte length exceeds `0xFFFFFFFF` THEN the system SHALL
   raise `UefiCapsuleError` with `details` naming the computed size.

### Requirement 2 — FMP Payload Wrapping (§23.3)

**User Story:** As a firmware engineer, I want to wrap an FMP payload
header around my vendor binary so that the capsule is consumable by the
firmware's Firmware Management Protocol.

#### Acceptance Criteria

1. WHEN the caller passes an `FmpCapsulePayload` to the builder THEN the
   system SHALL emit the `EFI_FIRMWARE_MANAGEMENT_CAPSULE_HEADER` per
   §23.3 immediately after the capsule header.
2. WHEN the FMP header declares `EmbeddedDriverCount` and
   `PayloadItemCount` THEN the system SHALL emit that many offsets in the
   following offset array.
3. WHEN the caller passes an optional `ItemOffsetList` override THEN the
   system SHALL use it verbatim rather than computing offsets.
4. IF the total size computed from the offsets disagrees with the builder's
   computed total THEN the system SHALL raise `UefiCapsuleError`.

### Requirement 3 — OS Runtime Delivery

**User Story:** As a test author, I want to deliver a capsule via the
running operating system's capsule interface, so that I do not need to
reboot into Shell for simple capsule tests.

#### Acceptance Criteria

1. WHEN `CapsuleDelivery.deliver_via_os(capsule, aspects)` is invoked on a
   Linux target with `/dev/efi_capsule_loader` present THEN the system
   SHALL `write()` the capsule bytes to that character device.
2. WHEN `/dev/efi_capsule_loader` is absent and `fwupdmgr` is installed
   THEN the system SHALL fall back to `fwupdmgr install --allow-reinstall`.
3. WHEN both capsule interfaces are absent THEN the system SHALL raise
   `UefiCapsuleError` with `details` listing the probed paths and tools.
4. IF the kernel returns `EINVAL` from the capsule write THEN the system
   SHALL raise `UefiCapsuleError` with `details` containing the error.
5. WHEN the write succeeds THEN the system SHALL emit a structured log
   entry at channel `uefi.capsule.os` with the capsule GUID and size.

### Requirement 4 — Mass-Storage Delivery (§8.5.5)

**User Story:** As a test author, I want to stage a capsule on the ESP and
let the firmware pick it up on next boot, so that I can test capsule
delivery in environments where the OS has no capsule interface.

#### Acceptance Criteria

1. WHEN `CapsuleDelivery.deliver_via_mass_storage(capsule, aspects)` is
   invoked THEN the system SHALL push the capsule bytes to
   `/EFI/UpdateCapsule/<name>.cap` on the ESP using the
   `EspFileProvider` from 5-esp-file-ops.
2. WHEN the capsule is being staged THEN the system SHALL also set the
   `EFI_OS_INDICATIONS_FILE_CAPSULE_DELIVERY_SUPPORTED` bit in
   `OsIndications` per §8.5.5 (via 5-variable-services).
3. IF the ESP's `/EFI/UpdateCapsule/` directory does not exist THEN the
   system SHALL create it before the push.
4. WHEN the staged capsule name is omitted THEN the system SHALL default
   to `auto-<guid>-<sha256[0:16]>.cap` for deterministic reproducibility.

### Requirement 5 — Redfish Delivery

**User Story:** As a test author running against a headless BMC, I want to
deliver a capsule via Redfish without touching the OS, so that bare-metal
firmware updates are reproducible.

#### Acceptance Criteria

1. WHEN `CapsuleDelivery.deliver_via_redfish(capsule, aspects)` is invoked
   THEN the system SHALL POST a multipart payload to
   `/redfish/v1/UpdateService/update-multipart` using the capsule bytes as
   the binary part.
2. WHEN the BMC does not advertise the multipart endpoint THEN the system
   SHALL fall back to `UpdateService.SimpleUpdate` with a `TransferProtocol`
   of `"HTTP"` pointing at a caller-supplied URL.
3. WHEN the BMC responds `202 Accepted` with a `Location:` TaskMonitor THEN
   the system SHALL poll the task every `aspects.completion_interval`
   until completion or `aspects.completion_timeout` elapses.
4. IF the task's `TaskState` is a failure terminal state (`Exception`,
   `Killed`, `Cancelled`) THEN the system SHALL raise `UefiCapsuleError`
   with `details` containing the `Messages[]` array.
5. WHEN the caller supplies neither a bundled SimpleUpdate URL nor a
   reachable multipart endpoint THEN the system SHALL raise
   `ConfigurationError`.

### Requirement 6 — Post-Boot Capsule Report (§8.5.6)

**User Story:** As a test author, I want to read back the per-capsule
outcome recorded by the firmware into `EFI_CAPSULE_REPORT_GUID` variables,
so that my test knows whether the firmware accepted the capsule.

#### Acceptance Criteria

1. WHEN `CapsuleReport.collect(aspects)` is invoked after a reset THEN the
   system SHALL enumerate variables under `EFI_CAPSULE_REPORT_GUID`
   (3B8C8162-188C-46A4-AEC9-BE43F1D65697) and decode them per §8.5.6.
2. WHEN every expected variable is present THEN the system SHALL return a
   list of `CapsuleReportRecord` objects in variable-name order.
3. IF the `MaxReports` variable (`EFI_CAPSULE_REPORT_GUID`) is present THEN
   the enumeration SHALL respect that upper bound.
4. IF the `aspects.action_pattern` is `DO_UNTIL_SUCCESS` and no reports
   exist THEN the system SHALL retry every `completion_interval` until
   at least one record appears or `completion_timeout` elapses.
5. WHEN a record's `CapsuleStatus` indicates failure THEN the returned
   `CapsuleReportRecord.is_success` property SHALL be `False`.

### Requirement 7 — Capsule Report Filtering

**User Story:** As a test author, I want to ask for the report for a
specific capsule GUID so that I do not have to sift through records from
prior runs.

#### Acceptance Criteria

1. WHEN `CapsuleReport.latest_for(capsule_guid, aspects)` is invoked THEN
   the system SHALL return the most recent `CapsuleReportRecord` whose
   embedded capsule GUID equals `capsule_guid`.
2. IF no matching record exists THEN the system SHALL return `None`.
3. WHEN multiple matches exist THEN the system SHALL return the record
   with the numerically largest variable-name suffix.

### Requirement 8 — Signing Is Caller-Supplied

**User Story:** As a security-conscious integrator, I want to keep my
signing keys out of the interop library entirely, so that the library's
compromise cannot expose a signing key.

#### Acceptance Criteria

1. WHEN `CapsuleBuilder(sign=None)` is invoked THEN the system SHALL emit
   an unsigned capsule body.
2. WHEN `CapsuleBuilder(sign=signer_callable)` is invoked THEN the system
   SHALL call `signer_callable(payload_bytes) -> bytes` and embed the
   result in the capsule per the selected wrapping mode.
3. IF `signer_callable` raises THEN the system SHALL re-raise without
   wrapping, preserving the original stack trace.
4. WHEN the builder needs a signed FMP authentication header THEN the
   system SHALL construct the `EFI_FIRMWARE_IMAGE_AUTHENTICATION`
   structure by calling the signer only, never by loading a key file.
5. WHEN `sign` is provided THEN the system SHALL NOT read or write any
   filesystem path for keying material.

### Requirement 9 — SINGLE_CALL Delivery Pattern

**User Story:** As a test author, I want the default delivery action to be
a single invocation, so that transient failures are surfaced directly.

#### Acceptance Criteria

1. WHEN `aspects.action_pattern == ActionPattern.SINGLE_CALL` THEN
   `CapsuleDelivery.deliver_via_os`, `deliver_via_mass_storage`, and
   `deliver_via_redfish` SHALL each execute their delivery step exactly
   once and propagate exceptions unchanged.

### Requirement 10 — SINGLE_CONNECTED_CALL Delivery Pattern

**User Story:** As a test author, I want to ensure the transport is live
before I attempt delivery, so that I see a clean `ConnectionError` rather
than a partial write.

#### Acceptance Criteria

1. WHEN `aspects.action_pattern == ActionPattern.SINGLE_CONNECTED_CALL`
   THEN the system SHALL verify transport connectivity before delivery.
2. IF connectivity verification fails within `aspects.connection_timeout`
   THEN the system SHALL raise `ConnectionError` without attempting
   delivery.

### Requirement 11 — DO_UNTIL_SUCCESS Delivery Pattern

**User Story:** As a test author, I want Redfish delivery retries bounded
by `completion_timeout`, so that flaky BMCs do not block my run forever.

#### Acceptance Criteria

1. WHEN `aspects.action_pattern == ActionPattern.DO_UNTIL_SUCCESS` THEN the
   system SHALL retry the delivery every `aspects.completion_interval`
   until success or `aspects.completion_timeout` elapses.
2. WHEN a retry attempt raises an exception in
   `aspects.allowed_connection_exceptions` THEN the system SHALL retry.
3. IF the timeout elapses THEN the system SHALL raise `TimeoutError` with
   the last failure in `details`.

### Requirement 12 — Default Aspects Passthrough

**User Story:** As a test author, I want to omit `aspects` on any public
method and fall back to the instance default.

#### Acceptance Criteria

1. WHEN any public method is called with `aspects=None` THEN the system
   SHALL resolve via `resolve_aspects`.
2. IF no instance default is set THEN the system SHALL raise
   `ConfigurationError`.

### Requirement 13 — Independently Testable Classes

**User Story:** As a maintainer, I want `CapsuleBuilder`,
`CapsuleDelivery`, and `CapsuleReport` to be usable and testable in
isolation, so that build-only tests do not need delivery infrastructure
and vice versa.

#### Acceptance Criteria

1. WHEN a test imports `CapsuleBuilder` alone THEN the system SHALL NOT
   require a `UefiClient` instance.
2. WHEN a test imports `CapsuleDelivery` alone THEN the system SHALL NOT
   require key material.
3. WHEN a test imports `CapsuleReport` alone THEN the system SHALL NOT
   require a payload.

### Requirement 14 — Deterministic Capsule Hashing

**User Story:** As a test author, I want a deterministic SHA-256 of the
built capsule so that I can correlate logs and reports across the
workflow.

#### Acceptance Criteria

1. WHEN `CapsuleBuilder.build()` returns THEN the result SHALL expose a
   `.sha256` attribute equal to the SHA-256 of the emitted bytes.
2. WHEN a capsule is delivered THEN the delivery log line SHALL include
   the `sha256` digest.
3. WHEN a report is fetched THEN the record SHALL include the fetched
   capsule GUID; if the firmware additionally reports a hash, the record
   SHALL include it.
