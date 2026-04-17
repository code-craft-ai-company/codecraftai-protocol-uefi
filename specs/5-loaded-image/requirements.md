# Requirements — Loaded Image

## Introduction

The Loaded Image feature provides read-only parsers for UEFI PE/COFF
images (`.efi` executables) and for the metadata that the
`EFI_LOADED_IMAGE_PROTOCOL` exposes at runtime.  It parses PE32+ images
(`IMAGE_FILE_MACHINE_X64`, `IMAGE_FILE_MACHINE_AARCH64`,
`IMAGE_FILE_MACHINE_RISCV64`) and classic PE32 images, enumerates COFF
sections, extracts `WIN_CERTIFICATE` attribute-certificate directory
entries containing embedded Authenticode signatures, and surfaces the PE
subsystem field as a named enum whose values align with the UEFI
specification's image subsystem codes.

The feature's design goals are:

* **Read-only.** No image writer, no re-signer, no section injector in
  v1.  Writing UEFI binaries is a separate concern.
* **Dependency-light.** No `pefile` dependency.  The parser is written
  directly against the PE/COFF layout so the library's install footprint
  stays minimal and so the parser can be audited against the MSFT
  PE/COFF specification line-by-line.
* **Typed.** Every output is a frozen dataclass with Sphinx-style
  docstrings; callers never have to interpret raw bytes.
* **UEFI-aware.** The feature cross-references PE data directories
  against the UEFI Specification §9.1 (LoadImage format) and §2.1
  (EFI image machine types) rather than limiting itself to PE/COFF as a
  generic format.

The feature references UEFI Specification §9.1 (LoadImage) and §2.1
(EFI Image Types).

## Requirements

### Requirement 1 — PE32+ and PE32 Support

**User Story:** As a test author, I want to parse any UEFI PE image I
encounter — whether produced by EDK2, by the Linux `shim` project, by
GRUB, by Windows Boot Manager, or by a Rust UEFI crate — regardless of
whether the image is PE32 or PE32+.

#### Acceptance Criteria

1. WHEN `LoadedImage.from_path("shim.efi")` is invoked on a PE32+ image
   whose `Machine` is `IMAGE_FILE_MACHINE_X64` (0x8664) THEN the system
   SHALL return a `LoadedImage` with `.pe_plus == True` and
   `.machine == Machine.X64`.
2. WHEN `from_path` is invoked on an `IMAGE_FILE_MACHINE_AARCH64`
   (0xAA64) image THEN the system SHALL return
   `.machine == Machine.AARCH64`.
3. WHEN `from_path` is invoked on an `IMAGE_FILE_MACHINE_RISCV64`
   (0x5064) image THEN the system SHALL return
   `.machine == Machine.RISCV64`.
4. WHEN `from_path` is invoked on a PE32 (`IMAGE_FILE_MACHINE_I386`,
   0x014C) image THEN the system SHALL return `.pe_plus == False` and
   `.machine == Machine.I386`.
5. IF the input does not begin with the `MZ` DOS stub THEN the system
   SHALL raise `UefiError` with `details` naming the offending byte
   prefix.
6. IF the PE signature at the address in `e_lfanew` is not `PE\0\0`
   (`0x50450000`) THEN the system SHALL raise `UefiError` citing the
   observed signature bytes.

### Requirement 2 — Header Inspection

**User Story:** As a test author, I want structured access to the COFF
file header and the PE optional header, so that my assertions do not
have to parse offsets.

#### Acceptance Criteria

1. WHEN `LoadedImage.file_header` is accessed THEN the system SHALL
   return a `CoffFileHeader` dataclass with `machine`, `number_of_sections`,
   `time_date_stamp`, `pointer_to_symbol_table`, `number_of_symbols`,
   `size_of_optional_header`, `characteristics`.
2. WHEN `LoadedImage.optional_header` is accessed THEN the system SHALL
   return a `PeOptionalHeader` dataclass covering both PE32 and PE32+
   variants, with `.magic`, `.major_linker_version`, `.address_of_entry_point`,
   `.image_base`, `.section_alignment`, `.file_alignment`,
   `.size_of_image`, `.size_of_headers`, `.subsystem`, `.dll_characteristics`,
   and `.data_directories: tuple[DataDirectoryEntry, ...]`.
3. WHEN the optional header is PE32+ THEN the system SHALL decode
   `image_base` as 64-bit; when PE32, as 32-bit.
4. IF the optional-header magic is neither `0x10B` (PE32) nor `0x20B`
   (PE32+) THEN the system SHALL raise `UefiError`.

### Requirement 3 — Section Enumeration

**User Story:** As a test author, I want each COFF section as a typed
record, so that I can iterate, filter, and slice the raw bytes per
section.

#### Acceptance Criteria

1. WHEN `LoadedImage.sections()` is iterated THEN the system SHALL
   yield `CoffSection` records in file order, each exposing `name`
   (decoded from its 8-byte NUL-padded field, or resolved from the
   string table when prefixed with `/`), `virtual_size`, `virtual_address`,
   `size_of_raw_data`, `pointer_to_raw_data`, `characteristics`,
   `raw_bytes`.
2. WHEN a section's `pointer_to_raw_data + size_of_raw_data` exceeds the
   file length THEN the system SHALL raise `UefiError` with `details`
   naming the section and the computed extent.
3. WHEN a section name starts with `/N` THEN the system SHALL resolve
   it via the COFF string table.
4. WHEN `raw_bytes` is accessed THEN the system SHALL return
   `bytes(size_of_raw_data)` sliced from the backing buffer — no
   decompression, no relocation applied.

### Requirement 4 — Authenticode Signature Extraction

**User Story:** As a security engineer, I want the embedded Authenticode
signature(s) of a signed `.efi` as DER-encoded PKCS#7 blobs, so that I
can verify, inspect, or diff them without writing a PE walker.

#### Acceptance Criteria

1. WHEN `LoadedImage.authenticode_signatures()` is invoked THEN the
   system SHALL return a `list[AttributeCertificate]` drawn from the
   `IMAGE_DIRECTORY_ENTRY_SECURITY` (index 4) data directory entry.
2. WHEN the security directory's `VirtualAddress` is 0 or its `Size` is
   0 THEN the system SHALL return an empty list.
3. WHEN the security directory points beyond the file length THEN the
   system SHALL raise `UefiError`.
4. WHEN an `AttributeCertificate` has
   `wCertificateType == WIN_CERT_TYPE_PKCS_SIGNED_DATA` (0x0002) THEN
   the system SHALL expose `.pkcs7_der` as DER-encoded PKCS#7 bytes
   starting at offset 8 of the certificate structure.
5. WHEN an `AttributeCertificate` has `wRevision` other than `0x0200`
   THEN the system SHALL still return it but with
   `.pkcs7_der == None`.
6. WHEN the security directory contains multiple `AttributeCertificate`
   entries THEN the system SHALL return each in file order, honoring
   the 8-byte alignment required by the PE/COFF spec.

### Requirement 5 — Subsystem Enum

**User Story:** As a test author, I want the PE subsystem field surfaced
as a named enum, so that my assertions read `EFI_APPLICATION` rather
than `10`.

#### Acceptance Criteria

1. WHEN the optional header's `Subsystem == 10` THEN
   `LoadedImage.subsystem` SHALL equal `Subsystem.EFI_APPLICATION`.
2. WHEN `Subsystem == 11` THEN `LoadedImage.subsystem` SHALL equal
   `Subsystem.EFI_BOOT_SERVICE_DRIVER`.
3. WHEN `Subsystem == 12` THEN `LoadedImage.subsystem` SHALL equal
   `Subsystem.EFI_RUNTIME_DRIVER`.
4. WHEN `Subsystem == 13` THEN `LoadedImage.subsystem` SHALL equal
   `Subsystem.EFI_ROM`.
5. WHEN `Subsystem` is an unrecognized value THEN
   `LoadedImage.subsystem` SHALL equal `Subsystem.UNKNOWN` and
   `LoadedImage.raw_subsystem` SHALL expose the numeric value.

### Requirement 6 — Data Directories

**User Story:** As a test author, I want each PE data directory as a
typed record, so that my tests can assert the presence or absence of
Debug, Resource, and TLS directories.

#### Acceptance Criteria

1. WHEN `LoadedImage.optional_header.data_directories` is accessed THEN
   the system SHALL return exactly `NumberOfRvaAndSizes` entries, each
   with `virtual_address: int` and `size: int`.
2. WHEN a data directory entry has `size == 0` THEN the record SHALL be
   present but with both fields zero.
3. WHEN the named accessor `LoadedImage.data_directory(name)` is invoked
   with a recognized name (`EXPORT`, `IMPORT`, `RESOURCE`, `EXCEPTION`,
   `SECURITY`, `BASERELOC`, `DEBUG`, `ARCHITECTURE`, `GLOBALPTR`,
   `TLS`, `LOAD_CONFIG`, `BOUND_IMPORT`, `IAT`, `DELAY_IMPORT`,
   `CLR_RUNTIME_HEADER`, `RESERVED`) THEN the system SHALL return the
   corresponding entry.
4. IF `name` is not recognized THEN the system SHALL raise
   `ConfigurationError`.

### Requirement 7 — Device Path from Loaded Image Protocol

**User Story:** As a test author running post-boot, I want to ask the
firmware for the device path of a currently-loaded image, so that I can
correlate a loaded driver with its source.

#### Acceptance Criteria

1. WHEN `LoadedImageProtocolReader.device_path(handle, aspects)` is
   invoked via a transport that exposes the UEFI Boot Services (Shell
   `dh` command) THEN the system SHALL return the device path bytes
   reported by the `EFI_LOADED_IMAGE_PROTOCOL.FilePath` field.
2. WHEN the transport cannot reach the Boot Services (e.g., the OS is
   booted and Boot Services have been exited) THEN the system SHALL
   raise `UefiError` with `details` naming the transport and reason.
3. WHEN the caller also has a `DevicePathCodec` from 5-device-path-codec
   THEN the result SHALL be passable to that codec for rendering.

### Requirement 8 — Fixtures-Only Parser

**User Story:** As a maintainer, I want the parser to operate against
raw bytes without any transport dependency, so that the test suite can
run against fixtures without network or SSH.

#### Acceptance Criteria

1. WHEN `LoadedImage.from_bytes(buf)` is invoked THEN the system SHALL
   parse the image entirely from the byte buffer without any I/O.
2. WHEN `LoadedImage.from_path(path)` is invoked THEN the system SHALL
   open the file, read it into bytes, and delegate to `from_bytes`.
3. WHEN the parser is running THEN the system SHALL NOT open a network
   socket or an SSH channel.

### Requirement 9 — Bounded-Buffer Safety

**User Story:** As a maintainer, I want the parser to reject malformed
or truncated images with a clear error rather than with an opaque
`IndexError` or `struct.error`.

#### Acceptance Criteria

1. WHEN an offset plus its fixed-size read would exceed the input
   buffer length THEN the system SHALL raise `UefiError` with
   `details` naming the field, the offset, and the buffer length.
2. WHEN `NumberOfRvaAndSizes` exceeds 16 (the PE maximum) THEN the
   system SHALL raise `UefiError`.
3. WHEN `NumberOfSections` exceeds 96 (PE/COFF upper bound) THEN the
   system SHALL raise `UefiError`.
4. WHEN the optional-header size does not match the magic + declared
   data-directory count THEN the system SHALL raise `UefiError`.

### Requirement 10 — Characteristics and DLL Characteristics Decoding

**User Story:** As a test author, I want flag bitmasks decoded into
named booleans, so that my assertions read clearly.

#### Acceptance Criteria

1. WHEN `CoffFileHeader.characteristics` is accessed THEN the system
   SHALL expose named booleans for `.is_executable`,
   `.is_32bit_machine`, `.is_dll`, `.is_large_address_aware`.
2. WHEN `PeOptionalHeader.dll_characteristics` is accessed THEN the
   system SHALL expose named booleans for `.dynamic_base`, `.nx_compat`,
   `.no_seh`, `.no_bind`, `.high_entropy_va`.
3. WHEN a flag is not set THEN the corresponding boolean SHALL be
   `False`.

### Requirement 11 — Read-Only Contract

**User Story:** As a platform owner, I want the library to be unable to
re-sign or rewrite UEFI binaries, so that misuse risk is bounded by
design.

#### Acceptance Criteria

1. WHEN the package is imported THEN the system SHALL NOT expose any
   public method named with prefix `write_`, `rewrite_`, or `re_sign`.
2. WHEN the package is imported THEN the system SHALL NOT invoke any
   `open(..., "w")`, `open(..., "wb")`, or `os.write`.
3. WHEN tests run THEN the system SHALL pass a fixture that asserts no
   writes to fixture files occurred.

### Requirement 12 — Round-Tripping Bytes

**User Story:** As a maintainer, I want the parser to expose the raw
image bytes it parsed, so that callers can reuse them without reading
the file twice.

#### Acceptance Criteria

1. WHEN `LoadedImage.raw_bytes` is accessed THEN the system SHALL
   return the original byte buffer passed to `from_bytes` (or read by
   `from_path`) unchanged.
2. WHEN `LoadedImage.sha256` is accessed THEN the system SHALL return
   the SHA-256 hexdigest of `raw_bytes`.

### Requirement 13 — Fixtures Coverage

**User Story:** As a maintainer, I want the test corpus to cover the
real-world diversity of UEFI images, so that the parser is exercised
against images produced by different toolchains.

#### Acceptance Criteria

1. WHEN the test suite runs THEN it SHALL include `shim.efi`,
   `grubx64.efi`, and a minimal Rust-built UEFI application under
   `tests/fixtures/loadedimage/`.
2. WHEN a fixture is added THEN the suite SHALL include at least one
   parser assertion against it (machine, subsystem, signature count).
