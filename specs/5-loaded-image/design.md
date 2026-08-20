# Design — Loaded Image

## Overview

The Loaded Image feature delivers a hand-rolled PE/COFF parser tailored to
UEFI executables plus a small helper for reading
`EFI_LOADED_IMAGE_PROTOCOL.FilePath` through a transport.  The parser is
strictly read-only, strictly pure-Python, and has no dependency on
`pefile` or any other third-party PE tooling — every offset and every
field definition is traceable to the MSFT PE/COFF specification and, for
UEFI-specific fields, to UEFI Specification §9.1 and §2.1.

The public surface is small:

* `LoadedImage` — The root parser.  Built via `from_bytes(buf)` or
  `from_path(path)`.
* `CoffFileHeader` — COFF file header as a typed dataclass.
* `PeOptionalHeader` — PE optional header (covers both PE32 and PE32+).
* `CoffSection` — One record per COFF section.
* `DataDirectoryEntry` — One record per PE data directory.
* `AttributeCertificate` — One record per entry in the security data
  directory; carries extracted DER PKCS#7 bytes.
* `Machine`, `Subsystem`, `Characteristics`, `DllCharacteristics` — named
  enums / flag records.
* `LoadedImageProtocolReader` — Helper that queries the UEFI Boot
  Services through a `UefiShellClient` to pull back the `FilePath` bytes.

## Architecture

```
ccai.protocols.uefi.loadedimage/
    __init__.py              # re-exports public surface
    image.py                 # LoadedImage root parser
    headers.py               # CoffFileHeader, PeOptionalHeader, DataDirectoryEntry
    sections.py              # CoffSection
    certs.py                 # AttributeCertificate, PKCS#7 extraction
    enums.py                 # Machine, Subsystem, Characteristics,
                             # DllCharacteristics
    protocol_reader.py       # LoadedImageProtocolReader
```

Dependency direction:

```
image.py ──▶ headers.py, sections.py, certs.py, enums.py
protocol_reader.py ──▶ 3-client (UefiShellClient), 1-aspects
image.py imports NOTHING from protocol_reader.py
```

## Components and Interfaces

### `LoadedImage` (image.py)

```python
class LoadedImage:
    """
        Parser for a UEFI PE32/PE32+ image.

        :param raw_bytes: The full image bytes.
        :param file_header: Parsed COFF file header.
        :param optional_header: Parsed PE optional header.
        :param sections_tuple: Parsed sections in file order.
        :param attribute_certificates_tuple: Parsed security-directory entries.
    """

    def __init__(
        self,
        raw_bytes: bytes,
        file_header: "CoffFileHeader",
        optional_header: "PeOptionalHeader",
        sections_tuple: tuple["CoffSection", ...],
        attribute_certificates_tuple: tuple["AttributeCertificate", ...],
    ) -> None: ...

    @classmethod
    def from_bytes(cls, buf: bytes) -> "LoadedImage": ...

    @classmethod
    def from_path(cls, path: str) -> "LoadedImage": ...

    @property
    def machine(self) -> "Machine": ...

    @property
    def pe_plus(self) -> bool:
        rtnval = self.optional_header.magic == 0x20B
        return rtnval

    @property
    def subsystem(self) -> "Subsystem": ...

    @property
    def raw_subsystem(self) -> int: ...

    @property
    def sha256(self) -> str: ...

    def sections(self) -> Iterator["CoffSection"]: ...

    def authenticode_signatures(self) -> list["AttributeCertificate"]: ...

    def data_directory(self, name: str) -> "DataDirectoryEntry": ...
```

### `CoffFileHeader` (headers.py)

```python
@dataclass(frozen=True)
class CoffFileHeader:
    machine: int
    number_of_sections: int
    time_date_stamp: int
    pointer_to_symbol_table: int
    number_of_symbols: int
    size_of_optional_header: int
    characteristics: "Characteristics"
```

### `PeOptionalHeader` (headers.py)

```python
@dataclass(frozen=True)
class PeOptionalHeader:
    magic: int                                      # 0x10B or 0x20B
    major_linker_version: int
    minor_linker_version: int
    size_of_code: int
    size_of_initialized_data: int
    size_of_uninitialized_data: int
    address_of_entry_point: int
    base_of_code: int
    base_of_data: Optional[int]                     # PE32 only
    image_base: int                                 # 32 or 64 bits
    section_alignment: int
    file_alignment: int
    major_operating_system_version: int
    minor_operating_system_version: int
    major_image_version: int
    minor_image_version: int
    major_subsystem_version: int
    minor_subsystem_version: int
    size_of_image: int
    size_of_headers: int
    checksum: int
    subsystem: int
    dll_characteristics: "DllCharacteristics"
    size_of_stack_reserve: int
    size_of_stack_commit: int
    size_of_heap_reserve: int
    size_of_heap_commit: int
    loader_flags: int
    number_of_rva_and_sizes: int
    data_directories: tuple["DataDirectoryEntry", ...]
```

### `DataDirectoryEntry` (headers.py)

```python
@dataclass(frozen=True)
class DataDirectoryEntry:
    virtual_address: int
    size: int
```

### `CoffSection` (sections.py)

```python
@dataclass(frozen=True)
class CoffSection:
    name: str
    virtual_size: int
    virtual_address: int
    size_of_raw_data: int
    pointer_to_raw_data: int
    pointer_to_relocations: int
    pointer_to_line_numbers: int
    number_of_relocations: int
    number_of_line_numbers: int
    characteristics: int
    raw_bytes: bytes
```

### `AttributeCertificate` (certs.py)

```python
@dataclass(frozen=True)
class AttributeCertificate:
    length: int                 # dwLength
    revision: int               # wRevision
    certificate_type: int       # wCertificateType
    raw_bytes: bytes            # The full structure (header + bCertificate)
    pkcs7_der: Optional[bytes]  # Populated when revision==0x0200 and
                                # certificate_type==0x0002 (PKCS_SIGNED_DATA)
```

### `LoadedImageProtocolReader` (protocol_reader.py)

```python
class LoadedImageProtocolReader:
    """
        Reads EFI_LOADED_IMAGE_PROTOCOL.FilePath for a currently-loaded
        image handle by running the UEFI Shell 'dh' command.
    """

    def __init__(
        self,
        client: "UefiShellClient",
        default_aspects: Optional["UefiCmdAspects"] = None,
    ) -> None: ...

    def device_path(
        self,
        handle: int,
        aspects: Optional["UefiCmdAspects"] = None,
    ) -> bytes: ...
```

### Enums (enums.py)

```python
class Machine(IntEnum):
    UNKNOWN = 0x0000
    I386 = 0x014C
    X64 = 0x8664
    AARCH64 = 0xAA64
    RISCV64 = 0x5064


class Subsystem(IntEnum):
    UNKNOWN = 0
    EFI_APPLICATION = 10
    EFI_BOOT_SERVICE_DRIVER = 11
    EFI_RUNTIME_DRIVER = 12
    EFI_ROM = 13


@dataclass(frozen=True)
class Characteristics:
    raw: int

    @property
    def is_executable(self) -> bool:
        rtnval = (self.raw & 0x0002) != 0
        return rtnval

    @property
    def is_32bit_machine(self) -> bool:
        rtnval = (self.raw & 0x0100) != 0
        return rtnval

    @property
    def is_dll(self) -> bool:
        rtnval = (self.raw & 0x2000) != 0
        return rtnval

    @property
    def is_large_address_aware(self) -> bool:
        rtnval = (self.raw & 0x0020) != 0
        return rtnval


@dataclass(frozen=True)
class DllCharacteristics:
    raw: int

    @property
    def high_entropy_va(self) -> bool:
        rtnval = (self.raw & 0x0020) != 0
        return rtnval

    @property
    def dynamic_base(self) -> bool:
        rtnval = (self.raw & 0x0040) != 0
        return rtnval

    @property
    def nx_compat(self) -> bool:
        rtnval = (self.raw & 0x0100) != 0
        return rtnval

    @property
    def no_seh(self) -> bool:
        rtnval = (self.raw & 0x0400) != 0
        return rtnval

    @property
    def no_bind(self) -> bool:
        rtnval = (self.raw & 0x0800) != 0
        return rtnval
```

## Data Models

### High-level layout of a UEFI PE/COFF image

```
+-----------------------------------+  offset 0
| DOS stub ("MZ" ...)               |
|   e_lfanew at offset 0x3C  ──────────┐
+-----------------------------------+  │
|               (padding)           |  │
+-----------------------------------+  │
| PE signature "PE\0\0"             | <┘ file offset = e_lfanew
+-----------------------------------+
| IMAGE_FILE_HEADER  (20 bytes)     |
+-----------------------------------+
| IMAGE_OPTIONAL_HEADER             |
|   (224 bytes PE32  / 240 PE32+)   |
|   + data_directory[NumberOfRvaAndSizes]
+-----------------------------------+
| Section[0] .. Section[N-1]        |  each 40 bytes
+-----------------------------------+
|   (Section raw-data regions,      |
|    referenced by PointerToRawData)|
+-----------------------------------+
|   (Security directory — attribute |
|    certificate table, 8-byte      |
|    aligned, outside section map)  |
+-----------------------------------+
```

### IMAGE_DOS_HEADER critical fields

| Offset | Size | Field     | Required value   |
|--------|------|-----------|------------------|
| 0x00   | 2    | e_magic   | `0x5A4D` ("MZ")  |
| 0x3C   | 4    | e_lfanew  | Offset of "PE\0\0" |

### IMAGE_FILE_HEADER (COFF, 20 bytes)

| Offset | Size | Field                   |
|--------|------|-------------------------|
| 0x00   | 2    | Machine                 |
| 0x02   | 2    | NumberOfSections        |
| 0x04   | 4    | TimeDateStamp           |
| 0x08   | 4    | PointerToSymbolTable    |
| 0x0C   | 4    | NumberOfSymbols         |
| 0x10   | 2    | SizeOfOptionalHeader    |
| 0x12   | 2    | Characteristics         |

### IMAGE_OPTIONAL_HEADER64 (PE32+) critical offsets

| Offset | Size | Field                     |
|--------|------|---------------------------|
| 0x00   | 2    | Magic (0x20B)             |
| 0x10   | 4    | AddressOfEntryPoint       |
| 0x18   | 8    | ImageBase                 |
| 0x20   | 4    | SectionAlignment          |
| 0x24   | 4    | FileAlignment             |
| 0x38   | 4    | SizeOfImage               |
| 0x3C   | 4    | SizeOfHeaders             |
| 0x44   | 2    | Subsystem                 |
| 0x46   | 2    | DllCharacteristics        |
| 0x6C   | 4    | NumberOfRvaAndSizes       |
| 0x70   | 8*N  | DataDirectories           |

### IMAGE_SECTION_HEADER (40 bytes each)

| Offset | Size | Field                 |
|--------|------|-----------------------|
| 0x00   | 8    | Name                  |
| 0x08   | 4    | VirtualSize           |
| 0x0C   | 4    | VirtualAddress        |
| 0x10   | 4    | SizeOfRawData         |
| 0x14   | 4    | PointerToRawData      |
| 0x18   | 4    | PointerToRelocations  |
| 0x1C   | 4    | PointerToLineNumbers  |
| 0x20   | 2    | NumberOfRelocations   |
| 0x22   | 2    | NumberOfLineNumbers   |
| 0x24   | 4    | Characteristics       |

### WIN_CERTIFICATE (attribute cert, 8-byte aligned)

| Offset | Size | Field                     |
|--------|------|---------------------------|
| 0x00   | 4    | dwLength                  |
| 0x04   | 2    | wRevision (0x0200)        |
| 0x06   | 2    | wCertificateType (0x0002 = PKCS_SIGNED_DATA) |
| 0x08   | var  | bCertificate (PKCS#7 DER) |

### PE image subsystem mapping to UEFI §2.1

| Value | Meaning                              | UEFI semantic       |
|-------|--------------------------------------|---------------------|
| 10    | IMAGE_SUBSYSTEM_EFI_APPLICATION      | UEFI application    |
| 11    | IMAGE_SUBSYSTEM_EFI_BOOT_SERVICE_DRIVER | Boot services driver |
| 12    | IMAGE_SUBSYSTEM_EFI_RUNTIME_DRIVER   | Runtime driver      |
| 13    | IMAGE_SUBSYSTEM_EFI_ROM              | EFI ROM image       |

## Measured Interop Contract

The `LoadedImage` parser itself is synchronous and does not consume
aspects.  `LoadedImageProtocolReader` consumes the following
`UefiCmdAspects` fields:

* `action_pattern` — Supported: `SINGLE_CALL`, `SINGLE_CONNECTED_CALL`,
  `DO_UNTIL_SUCCESS`.  Other patterns raise `SemanticError`.
* `completion_timeout` / `completion_interval` — Bound the retry loop.
* `connection_timeout` / `connection_interval` — Used by
  `SINGLE_CONNECTED_CALL` connectivity check.
* `allowed_connection_exceptions` — Retry eligibility.
* `must_connect` — Forces `ConnectionError` on connectivity failure.
* `logger` / `logging_pattern` — Channel
  `uefi.loadedimage.protocol_reader`.

## Error Handling

| Condition                                         | Exception            | `details`                                   |
|---------------------------------------------------|----------------------|---------------------------------------------|
| Missing `MZ` DOS signature                        | `UefiError`          | observed prefix bytes                       |
| Missing `PE\0\0` signature                        | `UefiError`          | offset, observed bytes                      |
| Optional-header magic neither 0x10B nor 0x20B     | `UefiError`          | observed magic                              |
| Offset + size exceeds buffer                      | `UefiError`          | field name, offset, buffer length           |
| `NumberOfRvaAndSizes > 16`                        | `UefiError`          | observed value                              |
| `NumberOfSections > 96`                           | `UefiError`          | observed value                              |
| Section raw-data extent exceeds buffer            | `UefiError`          | section name, extent, buffer length         |
| Security directory extent exceeds buffer          | `UefiError`          | VA, size, buffer length                     |
| Unknown data-directory name (accessor)            | `ConfigurationError` | requested name, allowed set                 |
| Unsupported action pattern (protocol reader)      | `SemanticError`      | requested pattern                           |
| Boot Services unavailable                         | `UefiError`          | transport, reason                           |

## Logging

* Channel `uefi.loadedimage.parse` — one line per successful parse with
  `{path_or_len, machine, subsystem, pe_plus, section_count, signature_count, sha256}`.
* Channel `uefi.loadedimage.protocol_reader` — one line per
  `device_path` call with `{handle, bytes}`.

No log line ever includes the raw image bytes, raw section bytes, or
raw certificate bytes.

## Security Considerations

* **Read-only.** The package exposes no writer, no re-signer, no
  section-injector.  A test is added that scans the package for method
  names beginning with `write_`, `rewrite_`, or `re_sign` and fails if
  any are found.
* **Bounded offsets.** Every field read is bounds-checked against the
  buffer length.  A malicious or corrupt image cannot cause an
  unbounded read or an uncaught `struct.error`.
* **Attribute-certificate trust.** The parser extracts PKCS#7 DER bytes
  but does *not* verify them.  Verification is the caller's
  responsibility (and typically the firmware's at load time).  The
  parser does not implicitly trust the signature structure.
* **No network, no subprocess.** The parser operates purely on bytes.
  Test fixtures include a check that no socket or subprocess is opened
  during any parser test.
* **Fixture integrity.** The test suite includes a SHA-256 manifest of
  each fixture so fixture corruption in CI surfaces immediately.

## Testing Strategy

### Parser unit tests

* `test_mz_pe_signatures.py` — MZ / PE signature validation.
* `test_machine_decode.py` — X64, AARCH64, RISCV64, I386 parameterized.
* `test_optional_header.py` — PE32 vs PE32+ field decoding.
* `test_sections.py` — Section name decoding including `/N` string-table
  path.
* `test_data_directories.py` — Named accessor, empty entries.
* `test_subsystem_enum.py` — All four named subsystems plus UNKNOWN.
* `test_characteristics.py` — Each named flag.
* `test_dll_characteristics.py` — Each named flag.
* `test_bounds.py` — Malformed, truncated, oversized fields each raise
  `UefiError`.

### Authenticode tests

* `test_authenticode_extract.py` — Extract PKCS#7 from a signed
  `shim.efi`.
* `test_authenticode_multi.py` — Image with multiple certificates.
* `test_authenticode_unsigned.py` — Unsigned image returns empty list.

### Protocol reader tests

* `test_protocol_reader_action_patterns.py` — All three supported
  patterns; unsupported raise `SemanticError`.
* `test_protocol_reader_dh_parse.py` — Mocked `dh` output produces
  expected device-path bytes.

### Fixture coverage

`tests/fixtures/loadedimage/` contains:

* `shim.efi` — Upstream Linux shim.
* `grubx64.efi` — Upstream GRUB.
* `uefi-rust-app.efi` — Minimal `uefi` crate hello-world compiled for
  `x86_64-unknown-uefi`.

Each fixture has an assertion against machine, subsystem, and
signature count.

### Read-only enforcement

* `test_no_write_methods.py` — Static scan for `write_`, `rewrite_`,
  `re_sign` method names.
* `test_no_filesystem_writes.py` — pytest hook asserts no fixture file
  is written during the suite.
* `test_no_network.py` — socket / subprocess audit hook.

### Coverage target

95% line, 90% branch.

## Dependencies on Other Features

* **1-aspects** — `UefiCmdAspects`, `resolve_aspects` (for the protocol
  reader only).
* **1-exceptions** — `UefiError`, `ConfigurationError`, `SemanticError`.
* **1-helpers** — Little-endian int unpackers.
* **3-client** — `UefiShellClient` used by `LoadedImageProtocolReader`.
* **5-device-path-codec** — Optional consumer that renders the device
  path bytes returned by the protocol reader.

No third-party PE/COFF library dependency — no `pefile`, no `lief`.

Consumers: any feature that needs to validate or inspect loaded images,
including future Authenticode-verification workflows.
