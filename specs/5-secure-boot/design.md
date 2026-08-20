# Design — Secure Boot

## Overview

The Secure Boot feature delivers two classes plus a codec:

* `SecureBootInspector` — Read-only state and database enumeration.
  Returns `SecureBootState` and iterators of `Signature` records.
* `SecureBootEnroller` — Authenticated-variable writes for PK, KEK, db,
  dbx, dbt, dbr.  Handles `EFI_VARIABLE_AUTHENTICATION_2` assembly,
  SetupMode gating, operator acknowledgement, and TimeStamp
  monotonicity.
* `SignatureListCodec` — Pure-bytes encoder/decoder for EFI Signature
  Lists (ESL) and Signature Database (EFI_SIGNATURE_DATA).

The feature is strict about key material: the library delegates all
private-key-touching operations to a caller-supplied signer callable, and
the marshalling of ASN.1 / PKCS#7 structures is delegated to
`cryptography.hazmat`.  Under normal configuration the library performs no
filesystem I/O on key paths.

## Architecture

```
ccai.protocols.uefi.secureboot/
    __init__.py                # re-exports public surface
    state.py                   # SecureBootState dataclass
    signatures.py              # Signature, SignatureType, SignatureListCodec
    inspector.py               # SecureBootInspector
    enroller.py                # SecureBootEnroller
    authdesc.py                # EFI_VARIABLE_AUTHENTICATION_2 marshalling
    monotonicity.py            # TimeStamp monotonicity tracker
    audit.py                   # Operator-acknowledgement audit logger
```

Dependency direction:

```
enroller.py ──▶ inspector.py (for SetupMode check)
enroller.py ──▶ authdesc.py
enroller.py ──▶ monotonicity.py
enroller.py ──▶ audit.py
inspector.py ──▶ signatures.py
inspector.py ──▶ state.py
signatures.py, state.py, authdesc.py, monotonicity.py, audit.py ──▶ 1-helpers
```

## Components and Interfaces

### `SecureBootState` (state.py)

```python
@dataclass(frozen=True)
class SecureBootState:
    setup_mode: int
    audit_mode: int
    deployed_mode: int
    secure_boot: int

    @property
    def is_setup_mode(self) -> bool:
        rtnval = self.setup_mode == 1
        return rtnval

    @property
    def is_deployed(self) -> bool:
        rtnval = self.deployed_mode == 1
        return rtnval

    @property
    def is_enforcing(self) -> bool:
        rtnval = self.secure_boot == 1
        return rtnval
```

### `Signature` / `SignatureType` (signatures.py)

```python
class SignatureType(Enum):
    EFI_CERT_SHA256_GUID = uuid.UUID("C1C41626-504C-4092-ACA9-41F936934328")
    EFI_CERT_RSA2048_GUID = uuid.UUID("3C5766E8-269C-4E34-AA14-ED776E85B3B6")
    EFI_CERT_X509_GUID = uuid.UUID("A5C059A1-94E4-4AA7-87B5-AB155C2BF072")
    EFI_CERT_X509_SHA256_GUID = uuid.UUID("3BD2A492-96C0-4079-B420-FCF98EF103ED")
    EFI_CERT_X509_SHA384_GUID = uuid.UUID("7076876E-80C2-4EE6-AAD2-28B349A6865B")
    EFI_CERT_X509_SHA512_GUID = uuid.UUID("446DBF63-2502-4CDA-BCFA-2465D2B0FE9D")
    UNKNOWN = uuid.UUID("00000000-0000-0000-0000-000000000000")


@dataclass(frozen=True)
class Signature:
    owner: uuid.UUID
    signature_type: SignatureType
    payload: bytes
    raw_guid: uuid.UUID          # populated when signature_type == UNKNOWN
```

### `SignatureListCodec` (signatures.py)

```python
class SignatureListCodec:
    @staticmethod
    def encode(signatures: Sequence[Signature]) -> bytes: ...

    @staticmethod
    def decode(buf: bytes) -> list[Signature]: ...
```

### `SecureBootInspector` (inspector.py)

```python
class SecureBootInspector:
    """
        Read-only Secure Boot state + signature-database enumerator.

        :param variables: VariableStore from 5-variable-services.
        :param default_aspects: Instance default for None passthrough.
    """

    def __init__(
        self,
        variables: "VariableStore",
        default_aspects: Optional["UefiCmdAspects"] = None,
    ) -> None: ...

    def state(
        self,
        aspects: Optional["UefiCmdAspects"] = None,
    ) -> SecureBootState: ...

    def enumerate(
        self,
        database: str,
        aspects: Optional["UefiCmdAspects"] = None,
    ) -> Iterator[Signature]: ...
```

### `SecureBootEnroller` (enroller.py)

```python
class SecureBootEnroller:
    """
        Authenticated-variable writer for PK, KEK, db, dbx, dbt, dbr.

        :param variables: VariableStore from 5-variable-services.
        :param inspector: SecureBootInspector used for SetupMode checks.
        :param default_aspects: Instance default for None passthrough.
        :param audit_logger: Optional pre-built audit logger (see audit.py).
    """

    def __init__(
        self,
        variables: "VariableStore",
        inspector: SecureBootInspector,
        default_aspects: Optional["UefiCmdAspects"] = None,
        audit_logger: Optional["AuditLogger"] = None,
    ) -> None: ...

    def enroll(
        self,
        database: str,
        payload: bytes,
        signer: Callable[[bytes], bytes],
        timestamp: Optional[datetime] = None,
        replace: bool = False,
        force: bool = False,
        acknowledgement: Optional[Mapping[str, str]] = None,
        aspects: Optional["UefiCmdAspects"] = None,
    ) -> None: ...

    def enroll_pre_signed(
        self,
        database: str,
        descriptor: bytes,
        force: bool = False,
        acknowledgement: Optional[Mapping[str, str]] = None,
        aspects: Optional["UefiCmdAspects"] = None,
    ) -> None: ...
```

### `EFI_VARIABLE_AUTHENTICATION_2` Marshalling (authdesc.py)

```python
def build_auth2_descriptor(
    variable_name: str,
    vendor_guid: uuid.UUID,
    attributes: int,
    timestamp: datetime,
    new_data: bytes,
    signer: Callable[[bytes], bytes],
) -> bytes:
    """
        Build an EFI_VARIABLE_AUTHENTICATION_2 descriptor per §8.2.5.

        The TBS (to-be-signed) bytes are:
            UCS-2(variable_name) || vendor_guid || attributes_u32 ||
            EFI_TIME(timestamp) || new_data

        The signer callable receives the TBS bytes and returns a
        detached PKCS#7 signature block.  The returned descriptor is:
            EFI_TIME(timestamp) || WIN_CERTIFICATE_UEFI_GUID(PKCS7)
    """
```

### Monotonicity Tracker (monotonicity.py)

```python
class TimeStampMonotonicityTracker:
    def __init__(self) -> None:
        self._last: dict[tuple[str, uuid.UUID], datetime] = {}
        return

    def check_and_record(
        self,
        variable_name: str,
        vendor_guid: uuid.UUID,
        timestamp: datetime,
    ) -> None: ...
```

### Audit Logger (audit.py)

```python
class AuditLogger:
    def __init__(
        self,
        logger: Optional[logging.Logger] = None,
    ) -> None: ...

    def record_force_enroll(
        self,
        database: str,
        payload_sha256: str,
        operator: str,
        ticket: str,
    ) -> None: ...
```

## Data Models

### `EFI_VARIABLE_AUTHENTICATION_2` layout (§8.2.5)

| Offset | Size | Field          | Notes                                  |
|--------|------|----------------|----------------------------------------|
| 0x00   | 16   | TimeStamp      | EFI_TIME with Pad1/Pad2/Nanosecond=0   |
| 0x10   | var  | AuthInfo       | WIN_CERTIFICATE_UEFI_GUID wrapper      |

### `WIN_CERTIFICATE_UEFI_GUID` (§32)

| Offset | Size | Field           |
|--------|------|-----------------|
| 0x00   | 4    | Length          |
| 0x04   | 2    | Revision (0x0200) |
| 0x06   | 2    | CertificateType (0x0EF1) |
| 0x08   | 16   | CertType (EFI_CERT_TYPE_PKCS7_GUID) |
| 0x18   | N    | CertData (PKCS7 SignedData) |

### `EFI_SIGNATURE_LIST` (§32.4.1)

| Offset | Size | Field              |
|--------|------|--------------------|
| 0x00   | 16   | SignatureType      |
| 0x10   | 4    | SignatureListSize  |
| 0x14   | 4    | SignatureHeaderSize|
| 0x18   | 4    | SignatureSize      |
| 0x1C   | S-H  | SignatureHeader    |
| 0x1C+H | N*Sig| Signatures[]       |

### `EFI_SIGNATURE_DATA` (§32.4.1)

| Offset | Size | Field              |
|--------|------|--------------------|
| 0x00   | 16   | SignatureOwner     |
| 0x10   | var  | SignatureData      |

### `EFI_TIME` (§8.3)

| Offset | Size | Field       |
|--------|------|-------------|
| 0x00   | 2    | Year        |
| 0x02   | 1    | Month       |
| 0x03   | 1    | Day         |
| 0x04   | 1    | Hour        |
| 0x05   | 1    | Minute      |
| 0x06   | 1    | Second      |
| 0x07   | 1    | Pad1        |
| 0x08   | 4    | Nanosecond  |
| 0x0C   | 2    | TimeZone    |
| 0x0E   | 1    | Daylight    |
| 0x0F   | 1    | Pad2        |

## Measured Interop Contract

Consumed `UefiCmdAspects` fields:

* `action_pattern` — Inspection methods support `SINGLE_CALL`,
  `SINGLE_CONNECTED_CALL`, `DO_UNTIL_SUCCESS`.  Enrollment methods
  support only `SINGLE_CALL` and `SINGLE_CONNECTED_CALL`; passing
  `DO_UNTIL_SUCCESS` or `DO_WHILE_SUCCESS` raises `SemanticError`.
* `completion_timeout` / `completion_interval` — Bound inspection
  retries; unused during enrollment.
* `connection_timeout` / `connection_interval` — Used by
  `SINGLE_CONNECTED_CALL` connectivity checks.
* `allowed_connection_exceptions` — Inspection retry eligibility.
* `must_connect` — Forces `ConnectionError` on connectivity failure
  rather than retry.
* `logger` / `logging_pattern` — Channels
  `uefi.secureboot.inspect`, `uefi.secureboot.enroll`,
  `uefi.secureboot.audit`.

## Error Handling

| Condition                                                 | Exception             | `details`                                   |
|-----------------------------------------------------------|-----------------------|---------------------------------------------|
| Required SB variable missing                              | `UefiSecureBootError` | missing variable name                       |
| ESL size mismatch                                         | `UefiSecureBootError` | offsets, sizes                              |
| Unknown database name                                     | `ConfigurationError`  | offending name, allowed set                 |
| `force=True` without valid ack dict                       | `ConfigurationError`  | present keys, required keys                 |
| Enrollment outside SetupMode without `force=True`         | `UefiSecureBootError` | current SetupMode                           |
| Signer returned non-bytes or empty                        | `UefiSecureBootError` | return type                                 |
| TimeStamp not strictly monotone                           | `UefiSecureBootError` | prior timestamp, new timestamp              |
| Firmware `EFI_SECURITY_VIOLATION`                         | `UefiSecureBootError` | EFI status code                             |
| `DO_UNTIL_SUCCESS` passed to `enroll`                     | `SemanticError`       | method name                                 |
| `aspects=None` without instance default                   | `ConfigurationError`  | method name                                 |

## Logging

* `uefi.secureboot.inspect` — One line per `state()` call with
  `{setup_mode, audit_mode, deployed_mode, secure_boot}`.  One line per
  `enumerate()` call with `{database, record_count}`.
* `uefi.secureboot.enroll` — One line per enrollment with
  `{database, payload_sha256, setup_mode_at_call, force, timestamp}`.
  Never includes signature bytes, never includes payload bytes.
* `uefi.secureboot.audit` — One line per `force=True` path with
  `{database, payload_sha256, operator, ticket, timestamp,
  setup_mode_at_call}`.

## Security Considerations

### Key-handling boundary

* Signing is delegated to a caller-supplied callable
  `Callable[[bytes], bytes]`.  The library never reads a PEM, DER, PKCS#8,
  PKCS#12, or keystore file for signing material.  Every code path that
  produces a signature calls the caller's callable with the TBS bytes
  and embeds the returned blob into the auth descriptor.
* `cryptography.hazmat` is used only for non-secret operations:
  ASN.1 DER assembly of the outer structure, checksum calculation, and
  certificate inspection when the caller passes a certificate chain to
  include alongside the signature.  Nothing in the library's default
  configuration loads a private key.
* The `enroll_pre_signed(descriptor, ...)` method lets callers perform
  signing out-of-process (HSM, remote signer) and hand the final
  descriptor in unchanged.  The library only verifies structural
  invariants (length fields, magic values) before issuing the write.

### Authentication-descriptor invariants

* `TimeStamp` fields have `Pad1`, `Pad2`, and `Nanosecond` zeroed per
  §8.2.5.  The library rejects caller-supplied `EFI_TIME` values that
  violate this.
* `WIN_CERTIFICATE_UEFI_GUID` has `Revision == 0x0200` and
  `CertificateType == 0x0EF1`.  The library rejects other values.
* `CertType` in the auth descriptor is
  `EFI_CERT_TYPE_PKCS7_GUID` (`4AAFD29D-68DF-49EE-8AA9-347D375665A7`).
  Other values are rejected.
* The library does not verify the signature itself — that is the
  firmware's responsibility.  It verifies only that the descriptor is
  well-formed and that `TimeStamp` monotonicity is honored.

### Operator-acknowledgement flow

Enrollment outside SetupMode (`SetupMode == 0`) requires both:

1. `force=True` passed explicitly — default is `False`, so unattended
   automation that forgets to declare intent silently fails rather than
   silently succeeds.
2. An `acknowledgement` dict with exactly the keys `{"operator",
   "ticket"}` where both values are non-empty strings.  Any other keys,
   any missing keys, or any empty value raises `ConfigurationError`
   *before* the firmware is contacted.

The acknowledgement is then logged at the `uefi.secureboot.audit` channel
along with the database name, payload SHA-256, and wall-clock timestamp.
The payload itself, the signature bytes, and the raw descriptor are
never logged.

For `dbx` append-writes, the `replace=True` path is also gated behind
the same `force=True` + acknowledgement — replacing a `dbx` is a
platform-trust event, not a routine operation.

### Append-only dbx

* `dbx` writes default to `APPEND_WRITE` (0x00000040) in the attribute
  mask so new revocations layer onto the existing list without
  overwriting.
* Full-replace `dbx` writes require `replace=True` *plus* the SetupMode
  guard (or `force=True` + acknowledgement) *plus* an explicit
  replacement-ack log entry.

### TimeStamp monotonicity

* The in-memory monotonicity tracker prevents the library from emitting
  a write whose `TimeStamp` is equal to or less than a prior write's
  `TimeStamp` for the same (VariableName, VendorGuid).
* The tracker is process-local — it does not persist across runs.
  Callers who want cross-run monotonicity must seed the tracker from
  their own store.  The library exposes
  `SecureBootEnroller.seed_monotonicity(mapping)` for this.

### Sensitive-data logging hygiene

* Payload bytes: never logged.
* Signer input (TBS): never logged.
* Signer output (PKCS#7): never logged.
* Certificate chain bytes when passed by the caller: never logged.
* SHA-256 of payload: logged once, used for correlation.

## Testing Strategy

### Inspector tests

* `test_state.py` — Captured fixtures with different SetupMode /
  DeployedMode combinations, assert `SecureBootState` boolean
  properties.
* `test_enumerate.py` — Captured `db` / `dbx` ESL fixtures, assert
  expected record count and signature types.
* `test_enumerate_malformed.py` — Corrupted ESL blobs raise
  `UefiSecureBootError`.

### Enroller tests

* `test_enroll_setup_mode.py` — Enrollment succeeds when `SetupMode == 1`
  without `force=True`.
* `test_enroll_deployed_mode_refuses.py` — Without `force=True`,
  enrollment outside SetupMode raises `UefiSecureBootError`.
* `test_enroll_force_ack.py` — `force=True` without a valid
  acknowledgement dict raises `ConfigurationError`; with a valid ack,
  the audit log receives the expected record.
* `test_enroll_monotonicity.py` — Second write with equal or lesser
  `TimeStamp` raises `UefiSecureBootError`.
* `test_enroll_pre_signed.py` — Pre-signed descriptors flow through
  without re-signing; structural invariants enforced.
* `test_dbx_append_only.py` — `APPEND_WRITE` attribute set by default;
  `replace=True` requires force + ack.
* `test_enroll_signer_bad_return.py` — Non-bytes / empty signer output
  raises `UefiSecureBootError`.

### Action-pattern tests

* `test_inspect_action_patterns.py` — All three supported patterns.
* `test_enroll_action_patterns.py` — `DO_UNTIL_SUCCESS` raises
  `SemanticError`.

### Security-boundary tests

* `test_no_key_material_read.py` — pytest hook asserts no `open()` under
  `**/keys/**`, `**/.ssh/**`, `**/pki/**`, `**/*.pem`, `**/*.p12`
  during any test in this feature's suite.
* `test_no_openssl_subprocess.py` — pytest hook asserts no subprocess
  invocation of `openssl`, `sbsign`, `gpg`, `pkcs11-tool`.
* `test_logs_exclude_signer_io.py` — Collected log stream contains no
  bytes from signer input/output.

### Codec tests

* `test_signature_list_roundtrip.py` — Property-based round-trip using
  `hypothesis` strategies for signature-type mixes.

### Coverage target

95% line, 90% branch.

## Dependencies on Other Features

* **1-aspects** — `UefiCmdAspects`, `resolve_aspects`.
* **1-constants-and-types** — `EFI_IMAGE_SECURITY_DATABASE_GUID`,
  `EFI_GLOBAL_VARIABLE_GUID`, signature-type GUIDs, attribute flags.
* **1-exceptions** — `UefiSecureBootError`, `ConfigurationError`,
  `SemanticError`.
* **1-helpers** — UCS-2 encoder, `datetime ↔ EFI_TIME` converters.
* **5-variable-services** — `VariableStore.get_variable`,
  `set_variable`, `enumerate`.
* **3-client** — Transport routing.
* Third-party: `cryptography.hazmat.primitives` for DER/ASN.1
  marshalling of the outer PKCS#7 wrapper (never for signing).

Consumers: **6-secure-boot-enrollment** composes this feature with the
reboot workflow.
