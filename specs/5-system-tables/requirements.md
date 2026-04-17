# Requirements — System Tables Readout

## Introduction

The System Tables feature provides post-boot readout and parsing of the UEFI
System Table and its adjacent tables: the EFI System Resource Table (ESRT)
defined in §23.4, the SMBIOS table referenced from the System Table, the
ACPI RSDP / XSDT / MADT tables, and the `EFI_MEMORY_ATTRIBUTES_TABLE`
defined in §4.6.4.  Each parser exposes a `from_bytes(buf)` classmethod and
an iterator over child entries so callers can work with typed dataclasses
rather than raw binary.

The feature sources its raw data from one of three runtime locations based
on the active transport:

* **SSH-to-host** — The Linux `/sys/firmware/efi/` virtual filesystem,
  including `/sys/firmware/efi/esrt/`, `/sys/firmware/dmi/tables/DMI`, and
  `/sys/firmware/acpi/tables/`.
* **UEFI Shell** — The Shell commands `memmap`, `smbiosview`, and
  `dmpstore`.
* **Redfish** — The `Bios`, `Chassis`, and `UpdateService/FirmwareInventory`
  resources of a BMC.

The transport is selected at runtime by the caller; each parser operates on
raw bytes and is agnostic to how those bytes were fetched.  The ESRT parser
additionally cross-references each entry against the Firmware Management
Protocol image-info reported via Redfish or OS tools, so callers can verify
that firmware components reported by the ESRT match components reported by
the platform's updater.  This feature references UEFI Specification §4.6
(Configuration Tables), §4.6.4 (Memory Attributes Table), and §23.4
(System Resource Table).

## Requirements

### Requirement 1 — `from_bytes` Classmethod on Every Parser

**User Story:** As a test author, I want every table parser to accept raw
bytes via a uniform classmethod, so that I can hand-feed fixtures or tap
different transports without each parser exposing its own constructor shape.

#### Acceptance Criteria

1. WHEN `EsrtTable.from_bytes(buf)` is invoked with a well-formed ESRT
   payload per §23.4 THEN the system SHALL return an `EsrtTable` instance.
2. WHEN `SmbiosTable.from_bytes(buf)` is invoked with a well-formed SMBIOS
   table blob THEN the system SHALL return an `SmbiosTable` instance.
3. WHEN `AcpiRsdp.from_bytes(buf)` / `AcpiXsdt.from_bytes(buf)` /
   `AcpiMadt.from_bytes(buf)` are invoked with well-formed ACPI payloads
   THEN the system SHALL return the corresponding typed instance.
4. WHEN `MemoryAttributesTable.from_bytes(buf)` is invoked with a well-formed
   §4.6.4 payload THEN the system SHALL return a `MemoryAttributesTable`
   instance.
5. IF any `from_bytes` is called with bytes shorter than the fixed-size
   header THEN the system SHALL raise `UefiError` with `details` listing
   expected and observed byte counts.
6. IF any `from_bytes` encounters a signature or version mismatch THEN the
   system SHALL raise `UefiError` with `details` listing expected and
   observed values in hexadecimal.

### Requirement 2 — Iterable Child Entries

**User Story:** As a test author, I want each table to expose its child
entries as an iterator of typed dataclasses, so that I can use list
comprehensions and filters without parsing offsets myself.

#### Acceptance Criteria

1. WHEN `EsrtTable.entries()` is iterated THEN the system SHALL yield
   `EsrtEntry` objects in table order.
2. WHEN `SmbiosTable.structures()` is iterated THEN the system SHALL yield
   `SmbiosStructure` objects in table order.
3. WHEN `AcpiXsdt.entries()` is iterated THEN the system SHALL yield
   `AcpiTableHeader` references in descriptor order.
4. WHEN `AcpiMadt.controllers()` is iterated THEN the system SHALL yield
   typed `MadtController` subclasses per ACPI type code.
5. WHEN `MemoryAttributesTable.descriptors()` is iterated THEN the system
   SHALL yield `EfiMemoryDescriptor` objects in table order.
6. WHEN any iterator is consumed twice THEN the system SHALL produce the
   same sequence each time.

### Requirement 3 — Runtime Transport Source Selection

**User Story:** As a test author, I want the system-tables loader to pick
the right transport source automatically based on the active client, so
that my calls stay identical whether I am running pre-boot (Shell), in-OS
(SSH-to-host), or out-of-band (Redfish).

#### Acceptance Criteria

1. WHEN `SystemTablesLoader(client=shell_client).load_esrt(aspects)` is
   invoked THEN the system SHALL populate the ESRT from the UEFI Shell
   `memmap` or `dmpstore SystemResourceTable` output.
2. WHEN `SystemTablesLoader(client=ssh_client).load_esrt(aspects)` is
   invoked THEN the system SHALL populate the ESRT from
   `/sys/firmware/efi/esrt/entries/entry*/`.
3. WHEN `SystemTablesLoader(client=redfish_client).load_esrt(aspects)` is
   invoked THEN the system SHALL populate the ESRT from the
   `/redfish/v1/UpdateService/FirmwareInventory` collection.
4. IF the selected transport cannot provide the requested table THEN the
   system SHALL raise `UefiError` with `details` identifying the transport
   and the missing source.

### Requirement 4 — ESRT Cross-Reference Against FMP

**User Story:** As a firmware engineer, I want to know when the ESRT's
reported components do not agree with the Firmware Management Protocol
image info, so that I catch misreports before they surface as failed
capsule updates.

#### Acceptance Criteria

1. WHEN `EsrtTable.cross_reference(fmp_images)` is invoked THEN the system
   SHALL return an `EsrtCrossReference` listing matched, missing, and extra
   firmware component GUIDs.
2. WHEN every ESRT entry has a matching FMP image by `FwClass` GUID THEN
   the returned cross-reference's `.is_consistent` property SHALL be `True`.
3. IF any ESRT entry's `FwVersion` differs from the corresponding FMP
   image's `Version` THEN the returned cross-reference SHALL list that
   entry under `.version_mismatches`.
4. WHEN the caller supplies an empty FMP image list THEN the cross-reference
   SHALL return all ESRT entries under `.missing_in_fmp` without raising.

### Requirement 5 — Memory Attributes Table Decoding

**User Story:** As a firmware engineer, I want to decode the memory
attributes table and inspect per-region attributes such as `EFI_MEMORY_XP`
and `EFI_MEMORY_RO`, so that I can validate platform page-protection
posture per §4.6.4.

#### Acceptance Criteria

1. WHEN `MemoryAttributesTable.descriptors()` is iterated THEN each
   `EfiMemoryDescriptor` SHALL expose `type`, `physical_start`,
   `virtual_start`, `number_of_pages`, `attribute`.
2. WHEN an `attribute` field has bit 0x2000 set THEN the `xp` property SHALL
   be `True`.
3. WHEN an `attribute` field has bit 0x20000 set THEN the `ro` property
   SHALL be `True`.
4. WHEN the header's `Version` field is not `0x00000001` THEN the system
   SHALL raise `UefiError`.

### Requirement 6 — SMBIOS Structure Enumeration

**User Story:** As a test author, I want to enumerate SMBIOS structures by
type code and index into their string tables without writing a parser, so
that I can assert BIOS vendor, baseboard info, and processor info values.

#### Acceptance Criteria

1. WHEN `SmbiosTable.structures()` iterates THEN each `SmbiosStructure`
   SHALL expose `type`, `length`, `handle`, `formatted_area`, and
   `strings: tuple[str, ...]`.
2. WHEN the formatted area of a structure is shorter than `length` THEN the
   system SHALL raise `UefiError`.
3. WHEN the string table is not terminated by a double-null THEN the system
   SHALL raise `UefiError`.
4. WHEN type 0 (BIOS Information) is present THEN `SmbiosTable.bios_info`
   SHALL return a `SmbiosType0` dataclass view.

### Requirement 7 — ACPI MADT Controller Dispatch

**User Story:** As a test author, I want MADT controllers returned as the
correct typed subclass per the ACPI-defined `Type` field, so that I do not
have to switch on raw integers.

#### Acceptance Criteria

1. WHEN MADT type `0x00` appears THEN `controllers()` SHALL yield a
   `LocalApic` instance.
2. WHEN MADT type `0x01` appears THEN `controllers()` SHALL yield an
   `IoApic` instance.
3. WHEN MADT type `0x0B` appears THEN `controllers()` SHALL yield a
   `Gicc` instance.
4. WHEN MADT type `0x0C` appears THEN `controllers()` SHALL yield a
   `Gicd` instance.
5. WHEN a type is not recognized THEN `controllers()` SHALL yield a
   `UnknownMadtController` instance carrying the raw bytes.

### Requirement 8 — SINGLE_CALL Action Pattern

**User Story:** As a test author, I want a one-shot table fetch to be the
default, so that diagnostic loads do not retry silently.

#### Acceptance Criteria

1. WHEN `aspects.action_pattern == ActionPattern.SINGLE_CALL` THEN the
   system SHALL fetch the underlying bytes exactly once and SHALL raise any
   transport exception directly.

### Requirement 9 — SINGLE_CONNECTED_CALL Action Pattern

**User Story:** As a test author, I want to ensure the transport is live
before system tables are read, so that disconnected reads do not return
stale cached bytes.

#### Acceptance Criteria

1. WHEN `aspects.action_pattern == ActionPattern.SINGLE_CONNECTED_CALL` THEN
   the system SHALL verify transport connectivity via
   `client.verify_connectivity(aspects)` before fetching table bytes.
2. IF connectivity verification fails THEN the system SHALL raise
   `ConnectionError` without fetching.

### Requirement 10 — DO_UNTIL_SUCCESS Action Pattern

**User Story:** As a test author, I want transient BMC errors during
`/sys/firmware/efi/esrt/` reads to retry within a bounded window.

#### Acceptance Criteria

1. WHEN `aspects.action_pattern == ActionPattern.DO_UNTIL_SUCCESS` THEN the
   system SHALL retry the fetch every `aspects.completion_interval` until
   it succeeds or `aspects.completion_timeout` elapses.
2. IF a retry-eligible exception (member of
   `aspects.allowed_connection_exceptions`) is raised THEN the system SHALL
   retry rather than propagate.
3. IF the timeout elapses THEN the system SHALL raise `TimeoutError` with
   the last failure in `details`.

### Requirement 11 — Default Aspects Passthrough

**User Story:** As a test author, I want to omit `aspects` and fall back to
the loader instance default.

#### Acceptance Criteria

1. WHEN any public method is called with `aspects=None` THEN the system
   SHALL resolve to the instance default via `resolve_aspects`.
2. IF no instance default is configured THEN the system SHALL raise
   `ConfigurationError`.

### Requirement 12 — Pure-Bytes Parsers Are Transport-Free

**User Story:** As a maintainer, I want the per-table parser classes to be
transport-independent, so that the tests can use captured byte fixtures
without setting up a transport stack.

#### Acceptance Criteria

1. WHEN any `from_bytes` classmethod is invoked THEN the system SHALL NOT
   open a connection, socket, or file handle.
2. WHEN any iterator on a parsed table is consumed THEN the system SHALL
   NOT perform I/O.
