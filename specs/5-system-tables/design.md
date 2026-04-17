# Design — System Tables Readout

## Overview

The System Tables feature delivers a collection of pure-bytes parsers for
UEFI System Table adjacent structures, plus a `SystemTablesLoader` that
selects the correct transport-backed source for the raw bytes and feeds
them into the parsers.  Parsers and loader are separate concerns: parsers
can be used against captured fixtures with zero transport setup, and the
loader is the glue that wires parsers to the active `UefiClient`.

Parsers implemented:

* `EsrtTable` — UEFI §23.4 EFI System Resource Table.
* `SmbiosTable` — DMTF SMBIOS reference via EFI Configuration Table.
* `AcpiRsdp`, `AcpiXsdt`, `AcpiMadt` — ACPI 6.x (core subset sufficient for
  firmware testing).
* `MemoryAttributesTable` — UEFI §4.6.4.

## Architecture

```
automojo.protocols.uefi.systables/
    __init__.py               # re-exports public surface
    loader.py                 # SystemTablesLoader (transport-dispatched)
    sources/
        __init__.py
        base.py               # AbstractTableSource
        shell.py              # ShellTableSource (memmap, dmpstore, smbiosview)
        sshhost.py            # SshHostTableSource (/sys/firmware/efi/...)
        redfish.py            # RedfishTableSource
    parsers/
        __init__.py
        esrt.py               # EsrtTable, EsrtEntry, EsrtCrossReference
        smbios.py             # SmbiosTable, SmbiosStructure, SmbiosType0, ...
        acpi.py               # AcpiRsdp, AcpiXsdt, AcpiMadt, MADT controllers
        matt.py               # MemoryAttributesTable, EfiMemoryDescriptor
```

Dependency direction:

```
loader.py ──▶ sources/*.py ──▶ (transport clients from layer 3)
loader.py ──▶ parsers/*.py ──▶ 1-helpers, 1-exceptions
parsers/*.py imports NOTHING from sources/*.py
```

## Components and Interfaces

### `SystemTablesLoader` (loader.py)

```python
class SystemTablesLoader:
    """
        Loader that fetches raw bytes for UEFI System Table adjacent tables
        and feeds them into the corresponding parser.

        :param client: The active UefiClient used to reach the target.
        :param default_aspects: Instance default UefiCmdAspects for None
                                passthrough.
    """

    def __init__(
        self,
        client: "UefiClient",
        default_aspects: Optional["UefiCmdAspects"] = None,
    ) -> None:
        self._client = client
        self._default_aspects = default_aspects
        self._source = self._resolve_source(client=client)
        return

    def load_esrt(
        self,
        aspects: Optional["UefiCmdAspects"] = None,
    ) -> "EsrtTable":
        resolved = resolve_aspects(passed=aspects, instance_default=self._default_aspects)
        raw = self._run_with_pattern(
            aspects=resolved,
            operation=lambda: self._source.fetch_esrt(aspects=resolved),
        )
        table = EsrtTable.from_bytes(buf=raw)
        return table

    def load_smbios(
        self,
        aspects: Optional["UefiCmdAspects"] = None,
    ) -> "SmbiosTable": ...

    def load_acpi_rsdp(
        self,
        aspects: Optional["UefiCmdAspects"] = None,
    ) -> "AcpiRsdp": ...

    def load_acpi_xsdt(
        self,
        aspects: Optional["UefiCmdAspects"] = None,
    ) -> "AcpiXsdt": ...

    def load_acpi_madt(
        self,
        aspects: Optional["UefiCmdAspects"] = None,
    ) -> "AcpiMadt": ...

    def load_memory_attributes(
        self,
        aspects: Optional["UefiCmdAspects"] = None,
    ) -> "MemoryAttributesTable": ...
```

### `AbstractTableSource` (sources/base.py)

```python
class AbstractTableSource:
    def fetch_esrt(self, aspects: "UefiCmdAspects") -> bytes: ...
    def fetch_smbios(self, aspects: "UefiCmdAspects") -> bytes: ...
    def fetch_acpi_rsdp(self, aspects: "UefiCmdAspects") -> bytes: ...
    def fetch_acpi_xsdt(self, aspects: "UefiCmdAspects") -> bytes: ...
    def fetch_acpi_madt(self, aspects: "UefiCmdAspects") -> bytes: ...
    def fetch_memory_attributes(self, aspects: "UefiCmdAspects") -> bytes: ...
```

### `EsrtTable` (parsers/esrt.py)

```python
@dataclass(frozen=True)
class EsrtEntry:
    fw_class: uuid.UUID
    fw_type: int
    fw_version: int
    lowest_supported_fw_version: int
    capsule_flags: int
    last_attempt_version: int
    last_attempt_status: int


class EsrtTable:
    HEADER_SIZE: Final[int] = 0x20
    ENTRY_SIZE: Final[int] = 0x28

    def __init__(
        self,
        fw_resource_count: int,
        fw_resource_count_max: int,
        fw_resource_version: int,
        entries: tuple[EsrtEntry, ...],
    ) -> None:
        self._fw_resource_count = fw_resource_count
        self._fw_resource_count_max = fw_resource_count_max
        self._fw_resource_version = fw_resource_version
        self._entries = entries
        return

    @classmethod
    def from_bytes(cls, buf: bytes) -> "EsrtTable": ...

    def entries(self) -> Iterator[EsrtEntry]:
        for e in self._entries:
            yield e
        return

    def cross_reference(
        self,
        fmp_images: Sequence["FmpImageInfo"],
    ) -> "EsrtCrossReference": ...
```

### `SmbiosTable` / `SmbiosStructure` (parsers/smbios.py)

Implements the SMBIOS table-walker.  Each structure has a fixed-size
formatted-area header + a variable-length string table terminated by a
double NUL.  `strings` are decoded via latin-1 since SMBIOS does not
mandate UTF-8.

### `AcpiRsdp` / `AcpiXsdt` / `AcpiMadt` (parsers/acpi.py)

`AcpiRsdp` validates both checksums (v1 and v2).  `AcpiXsdt` walks the
64-bit pointer array.  `AcpiMadt` dispatches controller entries to typed
subclasses via a `(type_code → class)` registry defined at module scope.

### `MemoryAttributesTable` (parsers/matt.py)

```python
@dataclass(frozen=True)
class EfiMemoryDescriptor:
    type: int
    physical_start: int
    virtual_start: int
    number_of_pages: int
    attribute: int

    @property
    def xp(self) -> bool:
        rtnval = (self.attribute & 0x0000_0000_0000_2000) != 0
        return rtnval

    @property
    def ro(self) -> bool:
        rtnval = (self.attribute & 0x0000_0000_0002_0000) != 0
        return rtnval
```

## Data Models

### EFI System Resource Table header (§23.4)

| Offset | Size | Field                  | Notes                  |
|--------|------|------------------------|------------------------|
| 0x00   | 4    | FwResourceCount        | Number of entries      |
| 0x04   | 4    | FwResourceCountMax     | Allocation ceiling     |
| 0x08   | 8    | FwResourceVersion      | Must be 1              |
| 0x10   | 16   | (reserved)             | Zero                   |

### EFI System Resource Entry (§23.4)

| Offset | Size | Field                       |
|--------|------|-----------------------------|
| 0x00   | 16   | FwClass (EFI_GUID)          |
| 0x10   | 4    | FwType                      |
| 0x14   | 4    | FwVersion                   |
| 0x18   | 4    | LowestSupportedFwVersion    |
| 0x1C   | 4    | CapsuleFlags                |
| 0x20   | 4    | LastAttemptVersion          |
| 0x24   | 4    | LastAttemptStatus           |

### Memory Attributes Table header (§4.6.4)

| Offset | Size | Field              |
|--------|------|--------------------|
| 0x00   | 4    | Version (=1)       |
| 0x04   | 4    | NumberOfEntries    |
| 0x08   | 4    | DescriptorSize     |
| 0x0C   | 4    | Reserved           |

### EFI_MEMORY_DESCRIPTOR (§7.2)

| Offset | Size | Field            |
|--------|------|------------------|
| 0x00   | 4    | Type             |
| 0x04   | 4    | (padding)        |
| 0x08   | 8    | PhysicalStart    |
| 0x10   | 8    | VirtualStart     |
| 0x18   | 8    | NumberOfPages    |
| 0x20   | 8    | Attribute        |

### SMBIOS structure header

| Offset | Size | Field          |
|--------|------|----------------|
| 0x00   | 1    | Type           |
| 0x01   | 1    | Length         |
| 0x02   | 2    | Handle         |
| 0x04   | N    | FormattedArea  |
| ...    | M    | StringTable (double-NUL terminated) |

### ACPI RSDP v2

| Offset | Size | Field              |
|--------|------|--------------------|
| 0x00   | 8    | Signature "RSD PTR " |
| 0x08   | 1    | Checksum v1        |
| 0x09   | 6    | OEMID              |
| 0x0F   | 1    | Revision           |
| 0x10   | 4    | RsdtAddress        |
| 0x14   | 4    | Length             |
| 0x18   | 8    | XsdtAddress        |
| 0x20   | 1    | Extended Checksum  |
| 0x21   | 3    | Reserved           |

## Measured Interop Contract

`SystemTablesLoader` consumes the following `UefiCmdAspects` fields:

* `action_pattern` — Supported: `SINGLE_CALL`, `SINGLE_CONNECTED_CALL`,
  `DO_UNTIL_SUCCESS`.  Other patterns raise `SemanticError`.
* `completion_timeout` / `completion_interval` — Bound the retry loop.
* `connection_timeout` / `connection_interval` — Used by the
  `SINGLE_CONNECTED_CALL` connectivity check.
* `allowed_connection_exceptions` — Exceptions eligible for retry under
  `DO_UNTIL_SUCCESS`.
* `must_connect` — When `True`, connection failures raise immediately
  rather than being retried.
* `logger` / `logging_pattern` — Structured logging under
  `uefi.systables`.

The parsers themselves **do not** consume aspects — they operate on raw
bytes and are synchronous pure functions.

## Error Handling

| Condition                                    | Exception            | `details`                                   |
|----------------------------------------------|----------------------|---------------------------------------------|
| Buffer shorter than header                   | `UefiError`          | expected header size, actual buffer length  |
| Signature mismatch                           | `UefiError`          | expected signature, observed signature      |
| Version mismatch (ESRT, MAT, RSDP)           | `UefiError`          | field name, expected, observed              |
| SMBIOS string table missing double NUL       | `UefiError`          | structure type, handle, offset              |
| Transport cannot supply source               | `UefiError`          | transport name, missing source path         |
| ACPI checksum mismatch                       | `UefiError`          | computed, stored                            |
| Unsupported action pattern                   | `SemanticError`      | requested pattern                           |
| Retry timeout exhausted                      | `TimeoutError`       | last exception                              |
| `aspects=None` without instance default      | `ConfigurationError` | method name                                 |

## Logging

Channel `uefi.systables` emits one summary line per load call:
`{"op": "load_esrt", "transport": "...", "bytes": N, "entries": K,
"duration_ms": ...}`.  On cross-reference, a second line reports
`{matched, missing_in_fmp, missing_in_esrt, version_mismatches}`.

## Security Considerations

* Parser robustness — All offset arithmetic is bounds-checked.  No
  `struct.unpack_from` call proceeds without asserting the slice is at
  least the expected length.  A malicious or corrupt fixture cannot cause
  an unbounded read.
* Reading `/sys/firmware/efi/` requires root on many distributions — the
  SSH-to-host source surfaces `EACCES` as a clear `UefiError`.
* Cross-reference does not trust FMP inputs; if two FMP images share a
  `FwClass` GUID the cross-reference raises `UefiError`.

## Testing Strategy

### Parser unit tests

* `test_esrt_parse.py` — Captured `/sys/firmware/efi/esrt/` fixtures from
  a representative Linux host.
* `test_smbios_parse.py` — Fixtures including well-known structures
  (BIOS Information, System, Baseboard, Processor).
* `test_acpi_parse.py` — OVMF-captured RSDP, XSDT, MADT.
* `test_matt_parse.py` — Fixtures with at least one `xp` region and one
  `ro` region.
* `test_bounds.py` — Truncated-buffer inputs each raise `UefiError`.

### Cross-reference tests

* `test_esrt_crossref.py` — Matched, missing, extra, and version-mismatch
  permutations.

### Loader tests

* `test_loader_dispatch.py` — Loader routes to correct source per
  `client.transport_kind`.
* `test_loader_action_patterns.py` — `SINGLE_CALL`, `SINGLE_CONNECTED_CALL`,
  `DO_UNTIL_SUCCESS`, and rejection of other patterns.

### Fixtures

`tests/fixtures/systables/` — Captured byte blobs from a Linux host under
`/sys/firmware/efi/esrt/`, SMBIOS via `dmidecode --dump-bin`, ACPI via
`acpidump -b`.

### Coverage target

95% line for both parsers and loader.

## Dependencies on Other Features

* **1-aspects** — `UefiCmdAspects`, `resolve_aspects`.
* **1-constants-and-types** — Well-known table GUIDs
  (`EFI_SYSTEM_RESOURCE_TABLE_GUID`, `SMBIOS_TABLE_GUID`,
  `ACPI_20_TABLE_GUID`, `EFI_MEMORY_ATTRIBUTES_TABLE_GUID`).
* **1-exceptions** — `UefiError`.
* **1-helpers** — `guid_from_bytes`, little-endian int unpackers.
* **3-client** — `UefiClient.transport_kind` for source selection.
* **2-transport-redfish** — Provides the Redfish session used by
  `RedfishTableSource`.
* **2-transport-ssh-host** — Provides the OS-side file access used by
  `SshHostTableSource`.

Consumers: **5-firmware-capsules** (ESRT snapshots before/after), and
**6-capsule-update-workflow** (pre-flight ESRT presence check).
