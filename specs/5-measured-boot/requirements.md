# Requirements — Measured Boot

## Introduction

The Measured Boot feature reads and validates the TCG 2.0 event log recorded
by UEFI firmware during the Static Root of Trust for Measurement (SRTM) boot
flow and cross-checks the replayed hash chain against the current TPM 2.0
Platform Configuration Register (PCR) banks.  It parses the TCG_PCR_EVENT2
binary structure defined by the TCG PC Client Platform Firmware Profile and
produces typed `PcrEvent` records suitable for integrity attestation, boot
forensics, and secure-boot-enforcement regression tests.

The feature supports two data flows: (a) post-boot readout of the kernel's
preserved event log via `/sys/kernel/security/tpm0/binary_bios_measurements`
over the SSH-to-host transport, and (b) live PCR readout via the `tpm2-tools`
suite (`tpm2_pcrread`).  It references UEFI specification §38 (TCG platform
interface) and the TCG EFI Protocol Specification.  It depends on the layer-1
`UefiError` hierarchy, the layer-1 helpers (big/little-endian integer
packers), and the measured-interop aspects contract carried by
`UefiCmdAspects`.

This feature is a read-only analyzer.  It does not extend PCRs, does not
issue attestation quotes, and does not manage signing keys.  Those concerns
belong to higher-layer workflow features.

## Requirements

### Requirement 1 — TCG 2.0 Event Log Parsing

**User Story:** As a platform security engineer, I want to parse the TCG 2.0
event log into typed `PcrEvent` records so that I can audit every
measurement the firmware recorded during boot.

#### Acceptance Criteria

1. WHEN `parse_event_log(buf: bytes)` is called with a TCG 2.0 event log
   beginning with the `EFI_SPEC_ID_EVENT_STRUCT` specification ID event
   THEN the system SHALL yield a generator of `PcrEvent` records per
   UEFI spec §38.4.1.
2. WHEN the parser encounters the first event (always TCG 1.2 format per
   the TCG PC Client spec) THEN the system SHALL decode it as a
   `TCG_PCR_EVENT` (SHA-1 only) and all subsequent events as
   `TCG_PCR_EVENT2`.
3. WHEN a `TCG_PCR_EVENT2.Digests` field carries multiple algorithm entries
   (SHA-1, SHA-256, SHA-384, SHA-512, SM3_256) THEN the system SHALL surface
   each digest keyed by its `TPM_ALG_ID` in a `dict[int, bytes]` on the
   `PcrEvent.digests` field.
4. IF the event log is truncated mid-event THEN the system SHALL raise
   `UefiError` with `details` naming the byte offset, the partial field
   being decoded, and the number of successfully-parsed events.
5. IF the specification ID event declares an unsupported log version THEN
   the system SHALL raise `UefiError` with `details` naming the declared
   version and the versions this parser supports.
6. WHERE the event type is one of the well-known UEFI event types
   (`EV_EFI_VARIABLE_DRIVER_CONFIG`, `EV_EFI_BOOT_SERVICES_APPLICATION`,
   `EV_EFI_GPT_EVENT`, `EV_EFI_PLATFORM_FIRMWARE_BLOB2`, etc. per UEFI §38.4.1)
   THEN the system SHALL expose a decoded `event_data` sub-record typed for
   that event type.

### Requirement 2 — PCR Readback

**User Story:** As a platform security engineer, I want to read the current
PCR values from the target's TPM 2.0 device so that I can verify the
firmware's measurements match what the kernel sees.

#### Acceptance Criteria

1. WHEN `read_pcrs(banks: Sequence[str], aspects)` is called THEN the system
   SHALL execute `tpm2_pcrread` through the SSH-to-host transport and return
   a `dict[str, dict[int, bytes]]` mapping algorithm name → PCR index →
   digest bytes.
2. WHEN `banks` is omitted THEN the system SHALL default to reading
   `("sha256",)` because SHA-256 is the baseline algorithm required by the
   TCG PC Client Platform TPM Profile.
3. IF `tpm2_pcrread` is not installed on the target THEN the system SHALL
   raise `UefiError` with `details` listing the missing binary and the
   transport-probe evidence.
4. IF `tpm2_pcrread` exits with a non-zero status not listed in
   `aspects.allowed_error_codes` THEN the system SHALL raise
   `UefiShellError` carrying `command`, `status`, `stdout`, `stderr`.
5. WHERE the caller requests a bank the TPM does not implement THEN the
   system SHALL raise `UefiError` with `details` listing the implemented
   banks reported by `tpm2_getcap pcrs`.

### Requirement 3 — Log-Against-PCR Validation

**User Story:** As a platform security engineer, I want to reconstruct the
PCR values by replaying the event log so that I can detect measurement log
tampering or undisclosed extensions.

#### Acceptance Criteria

1. WHEN `validate_log_against_pcrs(events, pcrs)` is called THEN the system
   SHALL fold each event's digest into an accumulator per PCR using the
   Extend operation (`PCR_new = Hash(PCR_old || digest)`) per TCG §3.4.
2. WHEN every replayed PCR accumulator matches the live PCR value THEN the
   system SHALL return a `ValidationReport` with `success=True` and an
   empty `mismatches` list.
3. IF any replayed PCR accumulator differs from the live value THEN the
   system SHALL raise `UefiError` with `details` containing a per-PCR diff
   table naming the PCR index, algorithm, expected digest, and computed
   digest.
4. WHERE the caller opts in via `strict_algorithms=True` THEN the system
   SHALL require every bank present in the live PCRs to also appear in every
   event's `digests` dict; missing entries raise `UefiError`.
5. WHERE the caller passes `strict_algorithms=False` (default) THEN the
   system SHALL skip banks that are not universally present across all
   events and note the skip in the returned report.

### Requirement 4 — Event Log Source Selection

**User Story:** As an integrator, I want to read the event log from either
the kernel's securityfs file or a caller-supplied buffer so that I can
exercise this feature in unit tests and in production with the same API.

#### Acceptance Criteria

1. WHEN `load_event_log_from_host(aspects)` is called THEN the system SHALL
   read `/sys/kernel/security/tpm0/binary_bios_measurements` over the
   SSH-to-host transport and return the raw bytes.
2. IF the securityfs file is not present THEN the system SHALL raise
   `UefiError` with `details` explaining that `securityfs` must be mounted
   and the IMA/TPM kernel subsystem enabled.
3. WHEN `parse_event_log(buf)` is called directly with caller-supplied
   bytes THEN the system SHALL parse without any transport interaction.
4. WHERE the caller supplies a path override via the `path` parameter THEN
   the system SHALL read that path instead of the default securityfs path.

### Requirement 5 — SINGLE_CALL Action Pattern

**User Story:** As a test author, I want a one-shot event-log read with no
retry so that fast-fail semantics are available when I know the target
state.

#### Acceptance Criteria

1. WHEN `read_pcrs(..., aspects)` is called with
   `aspects.action_pattern == ActionPattern.SINGLE_CALL` THEN the system
   SHALL execute the read exactly once and return the first result.
2. IF the single call fails with a non-allowed error code THEN the system
   SHALL raise immediately without retry.

### Requirement 6 — DO_UNTIL_SUCCESS Action Pattern

**User Story:** As a test author running right after a reboot, I want the
log reader to poll until the TPM becomes available so that my test does
not flake on boot races.

#### Acceptance Criteria

1. WHEN `read_pcrs(..., aspects)` is called with
   `aspects.action_pattern == ActionPattern.DO_UNTIL_SUCCESS` THEN the
   system SHALL poll every `aspects.completion_interval` until success or
   `aspects.completion_timeout` elapses.
2. IF `aspects.completion_timeout` elapses without a successful read THEN
   the system SHALL raise `TimeoutError` with `details` summarizing the
   last-observed error.

### Requirement 7 — SINGLE_CONNECTED_CALL Action Pattern

**User Story:** As a test author, I want a single call that treats
connection failures as retryable per aspects but treats application
failures as fatal, so that I can ride out BMC flaps without hiding
firmware bugs.

#### Acceptance Criteria

1. WHEN `read_pcrs(..., aspects)` is called with
   `aspects.action_pattern == ActionPattern.SINGLE_CONNECTED_CALL` THEN
   the system SHALL retry only connection-class exceptions listed in
   `aspects.allowed_connection_exceptions`.
2. IF the operation succeeds on a retry attempt THEN the system SHALL log
   the retry count under channel `uefi.measured` at INFO.

### Requirement 8 — Aspects=None Passthrough

**User Story:** As a caller, I want to omit `aspects` and have the method
use the owning `UefiAgent`/`UefiSession` default, so that my call sites are
short.

#### Acceptance Criteria

1. WHEN any public method is called with `aspects=None` THEN the system
   SHALL call `resolve_aspects(None, self._aspects)` and use the returned
   instance.
2. IF both the passed `aspects` and the instance default are `None` THEN
   the system SHALL raise `ConfigurationError` per the measured-interop
   contract.

### Requirement 9 — Well-Known Event Type Decoders

**User Story:** As a test author, I want well-known UEFI event payloads
decoded into named fields so I do not re-implement decoders per test.

#### Acceptance Criteria

1. WHEN an event of type `EV_EFI_VARIABLE_DRIVER_CONFIG` is parsed THEN
   the system SHALL decode it per UEFI §38.4.1 as a
   `UefiVariableEventData(variable_name, vendor_guid, unicode_name, variable_data)`.
2. WHEN an event of type `EV_EFI_BOOT_SERVICES_APPLICATION` is parsed THEN
   the system SHALL decode it per UEFI §38.4.1 as a
   `UefiImageLoadEventData(image_location, image_length, image_link_address, device_path)`.
3. WHEN an event of type `EV_EFI_GPT_EVENT` is parsed THEN the system
   SHALL decode it per UEFI §38.4.1 into a `GptEventData` dataclass that
   references the GPT parser from 5-gpt-mbr-parser via an opaque bytes
   passthrough.
4. WHEN an event of type `EV_EFI_PLATFORM_FIRMWARE_BLOB2` is parsed THEN
   the system SHALL decode it per UEFI §38.4.1 as a
   `FirmwareBlob2EventData(descriptor, base_address, length)`.
5. WHERE the event type is not one of the well-known types THEN the system
   SHALL expose the raw `event_data: bytes` without attempting to decode
   it; callers who need custom event types may parse `event_data` directly.

### Requirement 10 — Specification ID Event Validation

**User Story:** As a platform security engineer, I want strict validation
of the specification ID event so that malformed logs are rejected before
any downstream interpretation.

#### Acceptance Criteria

1. WHEN the first event is parsed THEN the system SHALL verify its
   `Signature` field equals `"Spec ID Event03"` per the TCG PC Client
   spec.
2. IF the `Signature` field does not match THEN the system SHALL raise
   `UefiError` with `details` naming the observed signature.
3. WHEN the specification ID event reports a digest-sizes vector THEN the
   system SHALL store it and use it to size every subsequent
   `TCG_PCR_EVENT2.Digests` entry.
4. IF a subsequent event's digest length does not match the declared size
   for that algorithm THEN the system SHALL raise `UefiError` with
   `details` naming the event index, algorithm ID, expected size, and
   observed size.

### Requirement 11 — Logging Channel

**User Story:** As an operator, I want measured-boot activity to route
through a named logging channel so I can filter it independently from
other UEFI activity.

#### Acceptance Criteria

1. WHEN any measured-boot API emits a log line THEN the system SHALL emit
   it on the `uefi.measured` channel.
2. WHEN `aspects.logging_pattern == LoggingPattern.FAILURE_ONLY` THEN
   successful parses and reads SHALL NOT emit INFO-level log lines.
3. WHEN `aspects.logging_pattern == LoggingPattern.SUCCESS_ONLY` THEN
   recoverable failures SHALL NOT emit WARNING-level log lines.

### Requirement 12 — Read-Only Contract

**User Story:** As a platform security engineer, I want the measured-boot
feature to be strictly read-only so that running it in production never
changes target state.

#### Acceptance Criteria

1. WHILE any measured-boot API is executing the system SHALL NOT invoke
   `tpm2_pcrextend`, `tpm2_pcrreset`, or any TPM command that mutates PCR
   state.
2. WHILE any measured-boot API is executing the system SHALL NOT write to
   `/sys/kernel/security/tpm0/*`.
3. IF a caller attempts to instantiate a write-oriented helper on this
   module THEN the system SHALL raise `SemanticError` because write
   operations are out of scope.
