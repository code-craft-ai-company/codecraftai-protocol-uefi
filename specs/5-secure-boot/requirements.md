# Requirements — Secure Boot

## Introduction

The Secure Boot feature provides read-only inspection of the platform
Secure Boot posture plus controlled enrollment of the Platform Key (PK),
Key Exchange Key (KEK), and image signature databases (`db`, `dbx`, `dbt`,
`dbr`).  It issues authenticated-variable writes using the
`EFI_VARIABLE_AUTHENTICATION_2` descriptor with time-based signing per
UEFI Specification §32 (Secure Boot and Driver Signing) and §8.2.5
(Time-Based Authenticated Variables).

Inspection surfaces `SetupMode`, `AuditMode`, `DeployedMode`, and
`SecureBoot` in a single `SecureBootState` dataclass.  Each signature
database is enumerated as a stream of typed `Signature` records that
expose `owner`, `signature_type`, and `payload`.

Enrollment is deliberately restrictive.  When `SetupMode == 0` the
feature refuses to operate unless the caller explicitly opts in with
`force=True` and a structured acknowledgement dictionary
`{"operator": str, "ticket": str}`.  That acknowledgement is captured in
the audit log and is not optional — the intent is to make unattended
automation conspicuous when it crosses a platform-trust boundary.

Signing is delegated.  Callers supply a signer callable or a pre-signed
authentication descriptor.  The library never reads a private key from
disk by default; it leans on `cryptography.hazmat` only to marshal the
authentication descriptor from caller-supplied key material or a caller-
supplied PKCS#7 blob.  `dbx` append-only semantics are honored via the
`APPEND_WRITE` attribute.

## Requirements

### Requirement 1 — `SecureBootState` Inspection

**User Story:** As a test author, I want a single dataclass summarizing
the platform Secure Boot posture, so that my tests can assert state with
one call.

#### Acceptance Criteria

1. WHEN `SecureBootInspector.state(aspects)` is invoked THEN the system
   SHALL read `SetupMode`, `AuditMode`, `DeployedMode`, `SecureBoot`
   variables and return a `SecureBootState` dataclass with fields
   `setup_mode: int`, `audit_mode: int`, `deployed_mode: int`,
   `secure_boot: int`.
2. WHEN `SetupMode == 1` THEN the returned `SecureBootState.is_setup_mode`
   SHALL be `True`.
3. WHEN `DeployedMode == 1` THEN the returned
   `SecureBootState.is_deployed` SHALL be `True`.
4. WHEN `SecureBoot == 1` THEN the returned
   `SecureBootState.is_enforcing` SHALL be `True`.
5. IF any required variable is missing THEN the system SHALL raise
   `UefiSecureBootError` with `details` naming the missing variable.

### Requirement 2 — Signature Database Enumeration

**User Story:** As a test author, I want each signature database
enumerated as typed records so that I can assert membership without
parsing ESL blobs.

#### Acceptance Criteria

1. WHEN `SecureBootInspector.enumerate("db", aspects)` is invoked THEN
   the system SHALL return an iterator of `Signature` records each
   exposing `owner: uuid.UUID`, `signature_type: SignatureType`, and
   `payload: bytes`.
2. WHEN `SecureBootInspector.enumerate(name, aspects)` is invoked for
   `name` in `{"PK", "KEK", "db", "dbx", "dbt", "dbr"}` THEN the system
   SHALL enumerate each respective database.
3. IF `name` is not one of the six recognized databases THEN the system
   SHALL raise `ConfigurationError`.
4. WHEN a database is empty THEN the iterator SHALL yield zero records
   without raising.
5. WHEN an ESL (Efi Signature List) has a non-standard `SignatureSize`
   that does not evenly divide `SignatureListSize - SignatureHeaderSize`
   THEN the system SHALL raise `UefiSecureBootError` citing the offsets.

### Requirement 3 — Authenticated-Variable Write

**User Story:** As a security engineer, I want to enroll a key by issuing
an authenticated `SetVariable` with an `EFI_VARIABLE_AUTHENTICATION_2`
descriptor, so that enrollment follows the §8.2.5 / §32 contract.

#### Acceptance Criteria

1. WHEN `SecureBootEnroller.enroll(database, payload, signer, aspects)`
   is invoked THEN the system SHALL build an
   `EFI_VARIABLE_AUTHENTICATION_2` descriptor containing the current UTC
   time in `EFI_TIME` form followed by the caller's PKCS#7 signature of
   the concatenation `(VariableName || VendorGuid || Attributes ||
   TimeStamp || NewData)` per §8.2.5.
2. WHEN the caller supplies a pre-signed `AuthDescriptor` via
   `enroll_pre_signed(...)` THEN the system SHALL use it verbatim without
   re-signing.
3. WHEN the write issues `TIME_BASED_AUTHENTICATED_WRITE_ACCESS` THEN the
   system SHALL include the `TIME_BASED_AUTHENTICATED_WRITE_ACCESS`
   attribute flag in the variable write per §8.2.
4. IF `TimeStamp` is not strictly greater than the last-seen `TimeStamp`
   for the target variable (tracked in an in-memory monotonicity log) THEN
   the system SHALL raise `UefiSecureBootError`.
5. WHEN the firmware rejects the write with `EFI_SECURITY_VIOLATION` THEN
   the system SHALL raise `UefiSecureBootError` with `details` citing the
   firmware return status.

### Requirement 4 — `dbx` Append-Only Semantics

**User Story:** As a security engineer, I want `dbx` entries to be
appended rather than overwritten, so that the revocation list grows
monotonically.

#### Acceptance Criteria

1. WHEN `SecureBootEnroller.enroll("dbx", payload, signer, aspects)` is
   invoked THEN the system SHALL set the `APPEND_WRITE` (0x00000040)
   variable attribute.
2. WHEN the caller passes `replace=True` to the `dbx` write THEN the
   system SHALL raise `UefiSecureBootError` unless the caller also passes
   `force=True` with an acknowledgement dict.
3. WHEN `APPEND_WRITE` is set THEN the system SHALL NOT perform a prior
   `GetVariable` + merge — it SHALL let the firmware merge.

### Requirement 5 — SetupMode Guard

**User Story:** As a platform owner, I want enrollment refused outside
SetupMode unless an operator explicitly acknowledges the risk, so that
unattended automation cannot silently mutate the trust store.

#### Acceptance Criteria

1. WHEN `SecureBootEnroller.enroll(...)` is invoked and
   `SecureBootInspector.state().setup_mode == 0` THEN the system SHALL
   raise `UefiSecureBootError` unless `force=True` is passed and a valid
   acknowledgement dict is supplied.
2. WHEN `force=True` is passed THEN the system SHALL require the
   acknowledgement dict to contain exactly the keys `operator: str` and
   `ticket: str`, both non-empty.
3. IF `force=True` is passed without a valid acknowledgement dict THEN
   the system SHALL raise `ConfigurationError`.
4. WHEN the `force=True` path is taken THEN the system SHALL emit an
   audit log entry at channel `uefi.secureboot.audit` with the operator,
   ticket, database name, payload SHA-256, and timestamp.
5. WHEN `setup_mode == 1` THEN the system SHALL NOT require `force=True`.

### Requirement 6 — Signer Delegation

**User Story:** As a security-conscious integrator, I want to keep
private keys outside the interop library, so that a library compromise
cannot exfiltrate key material.

#### Acceptance Criteria

1. WHEN `SecureBootEnroller.enroll(signer=callable, ...)` is invoked
   THEN the system SHALL call `callable(tbs: bytes) -> bytes` to obtain
   the PKCS#7 signature.
2. WHEN `signer=None` THEN the system SHALL require
   `pre_signed_descriptor` as an alternative or raise
   `ConfigurationError`.
3. WHEN the library is invoked with its default configuration THEN the
   system SHALL NOT open, read, or write any file path containing a
   private key.
4. WHEN the signer callable returns a non-`bytes` or empty value THEN
   the system SHALL raise `UefiSecureBootError`.
5. WHEN the library marshals the auth descriptor it SHALL delegate
   cryptographic primitives (ASN.1 encoding, SignerInfo layout) to
   `cryptography.hazmat` — it SHALL NOT re-implement ASN.1.

### Requirement 7 — Time-Based Monotonicity

**User Story:** As a security engineer, I want the library to refuse to
issue an authenticated write whose `TimeStamp` would be equal to or less
than a prior write's `TimeStamp` for the same variable, so that replay
is impossible.

#### Acceptance Criteria

1. WHEN the first write for a variable is issued THEN the system SHALL
   record the `TimeStamp` in an in-memory monotonicity log keyed by
   `(VariableName, VendorGuid)`.
2. WHEN a subsequent write for the same key uses `TimeStamp` less than
   or equal to the recorded value THEN the system SHALL raise
   `UefiSecureBootError` without contacting the firmware.
3. WHEN the caller omits `TimeStamp` THEN the system SHALL substitute
   the current UTC time with sub-second precision set to zero per the
   §8.2.5 EFI_TIME contract.

### Requirement 8 — SINGLE_CALL Action Pattern

**User Story:** As a test author, I want inspection and enrollment to
default to a single attempt.

#### Acceptance Criteria

1. WHEN `aspects.action_pattern == ActionPattern.SINGLE_CALL` THEN the
   system SHALL invoke the backend exactly once and propagate exceptions.

### Requirement 9 — SINGLE_CONNECTED_CALL Action Pattern

**User Story:** As a test author, I want to verify transport connectivity
before enrollment begins.

#### Acceptance Criteria

1. WHEN `aspects.action_pattern == ActionPattern.SINGLE_CONNECTED_CALL`
   THEN the system SHALL verify connectivity before the write.
2. IF verification fails within `connection_timeout` THEN the system
   SHALL raise `ConnectionError` without writing.

### Requirement 10 — DO_UNTIL_SUCCESS Action Pattern

**User Story:** As a test author, I want transient transport errors
during inspection reads to be retried within a bounded window — I do
*not* want enrollment writes to retry automatically.

#### Acceptance Criteria

1. WHEN `aspects.action_pattern == ActionPattern.DO_UNTIL_SUCCESS` is
   passed to `SecureBootInspector.state()` or
   `SecureBootInspector.enumerate()` THEN the system SHALL retry the
   read on `allowed_connection_exceptions` every
   `completion_interval` until success or `completion_timeout`.
2. WHEN `aspects.action_pattern == ActionPattern.DO_UNTIL_SUCCESS` is
   passed to `SecureBootEnroller.enroll(...)` THEN the system SHALL
   raise `SemanticError` — writes must not be retried silently.

### Requirement 11 — Default Aspects Passthrough

**User Story:** As a test author, I want to omit `aspects` and fall back
to the instance default.

#### Acceptance Criteria

1. WHEN any public method is called with `aspects=None` THEN the system
   SHALL resolve via `resolve_aspects`.
2. IF no instance default is set THEN the system SHALL raise
   `ConfigurationError`.

### Requirement 12 — Logging Hygiene

**User Story:** As a security engineer, I want key material and signature
bytes to never appear in logs, so that log shipping cannot leak secrets.

#### Acceptance Criteria

1. WHEN any public method logs at any level THEN the system SHALL NOT
   emit the signer callable's input or output bytes.
2. WHEN enrollment succeeds THEN the audit log SHALL include only
   `{database, payload_sha256, timestamp, operator, ticket}`.
3. WHEN enrollment fails THEN the failure log SHALL include the
   firmware error status but SHALL NOT include the PKCS#7 bytes.

### Requirement 13 — Symmetric Decode / Encode

**User Story:** As a test author, I want `Signature` records round-
trippable through the ESL codec so I can build payloads by composition.

#### Acceptance Criteria

1. WHEN `SignatureListCodec.encode(signatures)` is invoked and the result
   is decoded via `SignatureListCodec.decode(...)` THEN the resulting
   list SHALL equal the original element-wise.
2. WHEN encoding a mixed-type signature set THEN the system SHALL emit
   one ESL per `SignatureType`.

### Requirement 14 — Recognized Signature Types

**User Story:** As a test author, I want the common signature types from
§32.4.1 recognized as named enum members.

#### Acceptance Criteria

1. WHEN `SignatureType` is imported THEN the system SHALL expose named
   members including `EFI_CERT_SHA256_GUID`, `EFI_CERT_RSA2048_GUID`,
   `EFI_CERT_X509_GUID`, `EFI_CERT_X509_SHA256_GUID`,
   `EFI_CERT_X509_SHA384_GUID`, `EFI_CERT_X509_SHA512_GUID`.
2. WHEN an unknown signature-type GUID is encountered during enumeration
   THEN the system SHALL return a `SignatureType.UNKNOWN` member whose
   `.raw_guid` attribute is populated.
