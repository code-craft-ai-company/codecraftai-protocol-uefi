# Design — Firmware Capsules

## Overview

The Firmware Capsules feature splits capsule lifecycle into three
independently-testable classes:

* `CapsuleBuilder` — Pure-bytes assembler.  Produces capsule bytes from a
  header spec, an optional FMP wrapping, a raw payload, and an optional
  caller-supplied signer callable.  No I/O, no transport, no key material.
* `CapsuleDelivery` — Transport-aware delivery.  Owns three methods
  (`deliver_via_os`, `deliver_via_mass_storage`, `deliver_via_redfish`)
  that carry capsule bytes to the target by the caller-chosen mechanism.
  Respects the measured-interop aspects contract.
* `CapsuleReport` — Post-boot readout.  Enumerates
  `EFI_CAPSULE_REPORT_GUID` variables via 5-variable-services and decodes
  them per §8.5.6.

The separation is strict: `CapsuleBuilder` imports nothing from transport
or delivery code; `CapsuleDelivery` never imports from `CapsuleBuilder`
beyond the dataclass result type; `CapsuleReport` knows nothing about
either.  This is what makes the feature unit-testable without OVMF or a
BMC.

## Architecture

```
ccai.protocols.uefi.capsules/
    __init__.py               # re-exports public surface
    builder.py                # CapsuleBuilder, BuiltCapsule, CapsuleGuid
    fmp.py                    # FmpCapsulePayload, FmpImageAuthHeader helpers
    flags.py                  # CAPSULE_FLAGS_* constants
    delivery.py               # CapsuleDelivery facade (dispatches by mode)
    backends/
        __init__.py
        os_runtime.py         # /dev/efi_capsule_loader + fwupdmgr
        mass_storage.py       # ESP-based delivery per §8.5.5
        redfish.py            # UpdateService multipart / SimpleUpdate
    report.py                 # CapsuleReport, CapsuleReportRecord
```

## Components and Interfaces

### `CapsuleBuilder` (builder.py)

```python
@dataclass(frozen=True)
class BuiltCapsule:
    bytes_: bytes
    sha256: str
    capsule_guid: uuid.UUID
    capsule_image_size: int


class CapsuleBuilder:
    """
        Pure-bytes builder for an EFI_CAPSULE_HEADER-conformant capsule.

        :param capsule_guid: The CapsuleGuid for bytes 0..15 of the header.
        :param payload: Raw bytes of the capsule body.
        :param persist_across_reset: Sets CAPSULE_FLAGS_PERSIST_ACROSS_RESET.
        :param populate_system_table: Sets
                                      CAPSULE_FLAGS_POPULATE_SYSTEM_TABLE.
        :param initiate_reset: Sets CAPSULE_FLAGS_INITIATE_RESET.
        :param fmp: Optional FmpCapsulePayload to wrap per §23.3.
        :param sign: Optional callable `(payload: bytes) -> bytes` that
                     returns the serialized signature block.  None produces
                     an unsigned capsule.
    """

    def __init__(
        self,
        capsule_guid: uuid.UUID,
        payload: bytes,
        persist_across_reset: bool = False,
        populate_system_table: bool = False,
        initiate_reset: bool = False,
        fmp: Optional["FmpCapsulePayload"] = None,
        sign: Optional[Callable[[bytes], bytes]] = None,
    ) -> None: ...

    def build(self) -> BuiltCapsule: ...
```

### `FmpCapsulePayload` (fmp.py)

```python
@dataclass(frozen=True)
class FmpCapsulePayload:
    version: int                       # 0x00000001
    embedded_driver_count: int
    payload_item_count: int
    items: tuple[bytes, ...]           # raw item payloads
    item_offset_override: Optional[tuple[int, ...]] = None
```

### `CapsuleDelivery` (delivery.py)

```python
class CapsuleDelivery:
    """
        Transport-aware delivery of a BuiltCapsule to a UEFI target.

        :param client: The active UefiClient for OS / Redfish routing.
        :param esp: Optional EspFileProvider for mass-storage delivery.
        :param variables: Optional VariableStore for OsIndications toggling
                          during mass-storage delivery.
        :param default_aspects: Instance default for None passthrough.
    """

    def __init__(
        self,
        client: "UefiClient",
        esp: Optional["EspFileProvider"] = None,
        variables: Optional["VariableStore"] = None,
        default_aspects: Optional["UefiCmdAspects"] = None,
    ) -> None: ...

    def deliver_via_os(
        self,
        capsule: "BuiltCapsule",
        aspects: Optional["UefiCmdAspects"] = None,
    ) -> None: ...

    def deliver_via_mass_storage(
        self,
        capsule: "BuiltCapsule",
        name: Optional[str] = None,
        aspects: Optional["UefiCmdAspects"] = None,
    ) -> None: ...

    def deliver_via_redfish(
        self,
        capsule: "BuiltCapsule",
        simple_update_url: Optional[str] = None,
        aspects: Optional["UefiCmdAspects"] = None,
    ) -> None: ...
```

### `CapsuleReport` (report.py)

```python
@dataclass(frozen=True)
class CapsuleReportRecord:
    variable_name: str
    capsule_guid: uuid.UUID
    capsule_status: int
    size_in_bytes: int
    capsule_processed: bool
    reset_required: bool
    raw: bytes

    @property
    def is_success(self) -> bool:
        rtnval = self.capsule_status == 0
        return rtnval


class CapsuleReport:
    """
        Post-boot reader for EFI_CAPSULE_REPORT_GUID-scoped variables.

        :param variables: VariableStore from 5-variable-services.
        :param default_aspects: Instance default for None passthrough.
    """

    def __init__(
        self,
        variables: "VariableStore",
        default_aspects: Optional["UefiCmdAspects"] = None,
    ) -> None: ...

    def collect(
        self,
        aspects: Optional["UefiCmdAspects"] = None,
    ) -> list[CapsuleReportRecord]: ...

    def latest_for(
        self,
        capsule_guid: uuid.UUID,
        aspects: Optional["UefiCmdAspects"] = None,
    ) -> Optional[CapsuleReportRecord]: ...
```

## Data Models

### `EFI_CAPSULE_HEADER` (§8.5.3)

| Offset | Size | Field              | Meaning                                 |
|--------|------|--------------------|-----------------------------------------|
| 0x00   | 16   | CapsuleGuid        | Mixed-endian EFI_GUID                   |
| 0x10   | 4    | HeaderSize         | ≥ 28; typically 28                      |
| 0x14   | 4    | Flags              | CAPSULE_FLAGS_* bitfield                |
| 0x18   | 4    | CapsuleImageSize   | Total capsule size including header     |

### Capsule flags

| Bit       | Name                                     | Meaning                          |
|-----------|------------------------------------------|----------------------------------|
| 0x00010000| CAPSULE_FLAGS_PERSIST_ACROSS_RESET       | Persist across reset             |
| 0x00020000| CAPSULE_FLAGS_POPULATE_SYSTEM_TABLE      | Place in system config table     |
| 0x00040000| CAPSULE_FLAGS_INITIATE_RESET             | Firmware resets automatically    |

### `EFI_FIRMWARE_MANAGEMENT_CAPSULE_HEADER` (§23.3)

| Offset | Size | Field                 |
|--------|------|-----------------------|
| 0x00   | 4    | Version (= 1)         |
| 0x04   | 2    | EmbeddedDriverCount   |
| 0x06   | 2    | PayloadItemCount      |
| 0x08   | 4*N  | ItemOffsetList[N]     |

### `EFI_CAPSULE_RESULT_VARIABLE_HEADER` (§8.5.6)

| Offset | Size | Field              |
|--------|------|--------------------|
| 0x00   | 4    | VariableTotalSize  |
| 0x04   | 4    | Reserved           |
| 0x08   | 16   | CapsuleGuid        |
| 0x18   | 16   | CapsuleProcessed   |
| ...    | ...  | Additional status per §8.5.6 |

### `CapsuleReportRecord` derivation

Records are decoded from `CapsuleMax`, `CapsuleLast`, and `Capsule####`
variables scoped to `EFI_CAPSULE_REPORT_GUID`
(`3B8C8162-188C-46A4-AEC9-BE43F1D65697`).

## Measured Interop Contract

Consumed `UefiCmdAspects` fields:

* `action_pattern` — `CapsuleDelivery` supports `SINGLE_CALL`,
  `SINGLE_CONNECTED_CALL`, and `DO_UNTIL_SUCCESS`.  `CapsuleReport.collect`
  additionally supports `DO_UNTIL_SUCCESS` for the "wait for a report to
  appear" case.  `DO_WHILE_SUCCESS` and
  `DO_UNTIL_CONNECTION_FAILURE` are not supported — raise `SemanticError`.
* `completion_timeout` — Upper bound on Redfish TaskMonitor polling and
  on report-appearance polling after a reboot.
* `completion_interval` — Poll spacing for both of the above.
* `connection_timeout` / `connection_interval` — Used by
  `SINGLE_CONNECTED_CALL` connectivity checks.
* `inactivity_timeout` — Enforced on mass-storage ESP streaming writes.
* `allowed_connection_exceptions` — Exceptions retried under
  `DO_UNTIL_SUCCESS`.
* `must_connect` — When `True`, Redfish delivery raises `ConnectionError`
  on a connectivity failure rather than retrying.
* `chunk_size` — Forwarded to `EspFileProvider` for mass-storage delivery.
* `logger` / `logging_pattern` — Channels `uefi.capsule.builder`,
  `uefi.capsule.os`, `uefi.capsule.massstorage`, `uefi.capsule.redfish`,
  `uefi.capsule.report`.

`CapsuleBuilder` is synchronous and does not consume aspects.

## Error Handling

| Condition                                                 | Exception            | `details`                                   |
|-----------------------------------------------------------|----------------------|---------------------------------------------|
| Payload too large for `CapsuleImageSize` u32              | `UefiCapsuleError`   | computed total                              |
| FMP offset/size inconsistency                             | `UefiCapsuleError`   | offsets, totals                             |
| OS runtime interface missing                              | `UefiCapsuleError`   | probed paths, installed tools               |
| `/dev/efi_capsule_loader` returns `EINVAL`                | `UefiCapsuleError`   | `errno`, capsule header bytes (hex-dump)    |
| Mass-storage directory creation failure                   | `UefiCapsuleError`   | path, underlying error                      |
| Redfish TaskState terminal failure                        | `UefiCapsuleError`   | TaskState, Messages[]                       |
| Redfish multipart not advertised, no fallback URL         | `ConfigurationError` | `UpdateService` capabilities                |
| Signer callable raises                                    | (propagated)         | untouched                                   |
| `aspects=None` without instance default                   | `ConfigurationError` | method name                                 |
| Unsupported action pattern                                | `SemanticError`      | requested pattern                           |
| Post-boot report not present within timeout               | `TimeoutError`       | polling duration, observed variable list    |

## Logging

* `uefi.capsule.builder` — One line per `build()` call with
  `{guid, size, flags, signed: bool, sha256}`.
* `uefi.capsule.os` — One line per OS delivery with
  `{guid, sha256, mode: "capsule_loader|fwupd"}`.
* `uefi.capsule.massstorage` — One line per ESP push with
  `{guid, path, sha256, size}`.
* `uefi.capsule.redfish` — One line per task completion with
  `{guid, sha256, task_uri, task_state, duration_ms}`.
* `uefi.capsule.report` — One line per `collect()` call with
  `{record_count, success_count, failure_count}`.

Signer callables are never logged.  Signature bytes are never logged.

## Security Considerations

This section is load-bearing.  Capsule signing is the most security-
sensitive operation in the library, and this feature is written explicitly
to avoid owning that responsibility.

### Key-handling boundary

* The library never reads private keys from disk.  There is no code path
  that accepts a PEM file, key-ring handle, PKCS#11 URI, or environment
  variable containing a private key.
* The library never shells out to a signing tool.  There is no code path
  that invokes `openssl`, `sbsign`, `gpg`, `pkcs11-tool`, or equivalent.
* The library accepts only a signer *callable* from the caller, whose
  signature is `Callable[[bytes], bytes]`.  The callable takes a
  to-be-signed (TBS) payload and returns a pre-computed signature block
  (PKCS#7 / CMS SignedData for FMP, or the caller's chosen format).  This
  keeps private-key material inside the caller's process/HSM.
* When `sign=None`, the builder emits an unsigned capsule.  Callers who
  want a signed artifact *must* pass a callable explicitly.
* The builder asserts the callable was called with the exact TBS bytes it
  was given and rejects callables that return an empty or obviously-wrong
  (length 0, type not `bytes`) result.

### Authentication-descriptor invariants

* The `EFI_FIRMWARE_IMAGE_AUTHENTICATION` structure built around the
  signer's output is assembled by the library, but the signature block
  itself is opaque.  The library verifies only structural invariants:
  non-zero signature length, well-formed `WIN_CERTIFICATE_UEFI_GUID`
  wrapper.
* The `TimeStamp` field of the authentication descriptor is derived from
  the builder's `datetime.utcnow()` at build time, not from a caller-
  controlled wall clock.  This matches §8.2.5 semantics where TimeStamp
  serves as a monotonic replay guard.
* When the caller supplies `TimeStamp` explicitly, the builder compares
  against the last-seen `TimeStamp` for the corresponding `FwClass` GUID
  (tracked in an in-memory map) and raises `UefiCapsuleError` if the new
  value is not strictly greater.

### Operator-acknowledgement boundary

Capsule delivery is *not* gated behind an operator acknowledgement in this
feature — unlike 5-secure-boot, which has explicit `force=True` +
acknowledgement semantics.  The rationale:

* Capsule delivery cannot enroll keys into the platform.  The worst a
  malicious capsule can do is be rejected by firmware authentication.
* The authentication responsibility is the firmware's, not the library's.

If a workflow layer wants to add operator ack, it does so in 6-layer
code — never inside this feature.

### Logging hygiene

* Capsule payload bytes are never logged, even at `DEBUG`.
* The builder's summary log omits the signature block entirely.
* Redfish response bodies are logged at `DEBUG` only and are truncated to
  the first 512 bytes.

### Authorization expectations

* OS-runtime delivery requires root on the target.  The backend expects
  the `SshAgent` to have been opened with a credential that can escalate;
  a non-root failure surfaces as a clean `UefiCapsuleError` rather than a
  silent retry.
* Redfish delivery requires Administrator-level credentials per
  `UefiRedfishCredential`.  The library does not downgrade to a less-
  privileged credential on 403 — it surfaces the error.

## Testing Strategy

### Builder tests

* `test_builder_header.py` — Header offsets, flags, total-size invariants.
* `test_builder_fmp.py` — FMP wrapping, offset arrays, override path.
* `test_builder_signer.py` — Signer callable is invoked with the exact
  TBS payload; returns are placed into the auth descriptor; `None`
  produces unsigned output; raising signers propagate.
* `test_builder_size_limits.py` — Oversize payload raises
  `UefiCapsuleError`.
* `test_builder_no_key_material.py` — pytest hook asserts no file under
  `tests/fixtures/keys/` is read during a builder run.

### Delivery tests

* `test_delivery_os.py` — Mocks `/dev/efi_capsule_loader` writes; asserts
  fallback to `fwupdmgr` when device missing.
* `test_delivery_mass_storage.py` — Mocked `EspFileProvider` verifies path
  assembly and `OsIndications` toggling.
* `test_delivery_redfish.py` — `responses`-library fake BMC covers
  multipart path, SimpleUpdate fallback, TaskMonitor polling, terminal
  failure states.
* `test_delivery_action_patterns.py` — `SINGLE_CALL`,
  `SINGLE_CONNECTED_CALL`, `DO_UNTIL_SUCCESS`, and rejection of other
  patterns.

### Report tests

* `test_report_collect.py` — Enumerate variables, decode records, respect
  `MaxReports`.
* `test_report_latest_for.py` — Filter-by-GUID semantics.
* `test_report_do_until_success.py` — Retry-until-present polling.

### Integration tests

* `test_edk2_vector.py` — Build a capsule matching a known-good vector
  from the EDK2 FmpDevicePkg tests and byte-compare.
* `test_redfish_multipart_upload.py` — End-to-end multipart upload against
  the fake BMC.

### Coverage target

95% line coverage, 90% branch.

## Dependencies on Other Features

* **1-aspects** — `UefiCmdAspects`, `resolve_aspects`.
* **1-constants-and-types** — `EFI_CAPSULE_REPORT_GUID`, capsule flag
  constants, `CAPSULE_HEADER_SIZE`.
* **1-exceptions** — `UefiCapsuleError`, `ConfigurationError`.
* **1-helpers** — GUID packers, little-endian int packers.
* **3-client** — `UefiClient.transport_kind` for delivery routing.
* **5-esp-file-ops** — `EspFileProvider` for mass-storage delivery.
* **5-variable-services** — `VariableStore` for `OsIndications` toggling
  (mass-storage path) and `EFI_CAPSULE_REPORT_GUID` enumeration (report
  path).
* **2-transport-redfish** — Redfish session used by Redfish delivery.
* **2-transport-ssh-host** — SSH session used by OS-runtime delivery.

Consumers: **6-capsule-update-workflow** composes builder + delivery +
report + reboot.
