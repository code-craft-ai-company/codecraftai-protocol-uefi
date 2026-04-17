# Tasks — System Tables Readout

- [ ] 1. Create package skeleton at
      `source/packages/automojo/protocols/uefi/systables/`.
  - [ ] 1.1 Add `__init__.py` re-exporting `SystemTablesLoader`,
        `EsrtTable`, `EsrtEntry`, `EsrtCrossReference`, `SmbiosTable`,
        `SmbiosStructure`, `AcpiRsdp`, `AcpiXsdt`, `AcpiMadt`,
        `MemoryAttributesTable`, `EfiMemoryDescriptor`.
  - [ ] 1.2 Add `sources/` and `parsers/` subpackages with `__init__.py`
        files.
  - References: Requirement 1.

- [ ] 2. Implement the ESRT parser in `parsers/esrt.py`.
  - [ ] 2.1 Author `EsrtEntry` (frozen dataclass) with the seven fields
        from §23.4.
  - [ ] 2.2 Author `EsrtTable` with `HEADER_SIZE`, `ENTRY_SIZE` class
        constants.
  - [ ] 2.3 Implement `from_bytes(buf)` with bounds checks, signature
        validation (None — ESRT has only a version), and version == 1
        check.
  - [ ] 2.4 Implement `entries()` generator.
  - [ ] 2.5 Implement `cross_reference(fmp_images)` returning an
        `EsrtCrossReference`.
  - [ ] 2.6 Add `test_esrt_parse.py` and `test_esrt_crossref.py`.
  - References: Requirement 1.1, Requirement 2.1, Requirement 4.

- [ ] 3. Implement the SMBIOS parser in `parsers/smbios.py`.
  - [ ] 3.1 Author `SmbiosStructure` (frozen dataclass) with `type`,
        `length`, `handle`, `formatted_area: bytes`, `strings: tuple[str, ...]`.
  - [ ] 3.2 Author `SmbiosTable.from_bytes(buf)` handling the 2.x
        double-NUL-terminated string table.
  - [ ] 3.3 Author `SmbiosTable.structures()` iterator.
  - [ ] 3.4 Author `SmbiosType0` dataclass view for BIOS Information.
  - [ ] 3.5 Add `test_smbios_parse.py` using captured `dmidecode` fixtures.
  - References: Requirement 1.2, Requirement 2.2, Requirement 6.

- [ ] 4. Implement the ACPI parsers in `parsers/acpi.py`.
  - [ ] 4.1 Author `AcpiTableHeader` dataclass covering the 36-byte
        header common to SDTs.
  - [ ] 4.2 Author `AcpiRsdp.from_bytes(buf)` validating both checksums.
  - [ ] 4.3 Author `AcpiXsdt` walking the 64-bit pointer array.
  - [ ] 4.4 Author `AcpiMadt` with a `(type_code → class)` registry
        covering `LocalApic`, `IoApic`, `Gicc`, `Gicd`, and
        `UnknownMadtController`.
  - [ ] 4.5 Add `test_acpi_parse.py` using OVMF captures.
  - References: Requirement 1.3, Requirement 2.3, Requirement 2.4,
        Requirement 7.

- [ ] 5. Implement the Memory Attributes Table parser in
      `parsers/matt.py`.
  - [ ] 5.1 Author `EfiMemoryDescriptor` with `xp` and `ro` properties.
  - [ ] 5.2 Author `MemoryAttributesTable.from_bytes(buf)` validating
        `Version == 1`.
  - [ ] 5.3 Author `descriptors()` iterator.
  - [ ] 5.4 Add `test_matt_parse.py`.
  - References: Requirement 1.4, Requirement 2.5, Requirement 5.

- [ ] 6. Implement `AbstractTableSource` in `sources/base.py`.
  - [ ] 6.1 Define the six `fetch_*` abstract methods.
  - [ ] 6.2 Raise `AbstractMethodError` from each method body.
  - References: Requirement 3.

- [ ] 7. Implement `ShellTableSource` in `sources/shell.py`.
  - [ ] 7.1 Implement `fetch_esrt` via UEFI Shell `dmpstore
        SystemResourceTable` (or equivalent).
  - [ ] 7.2 Implement `fetch_smbios` via `smbiosview`.
  - [ ] 7.3 Implement `fetch_acpi_*` via memory-map reads.
  - [ ] 7.4 Translate missing sources into `UefiError`.
  - References: Requirement 3.1.

- [ ] 8. Implement `SshHostTableSource` in `sources/sshhost.py`.
  - [ ] 8.1 Read `/sys/firmware/efi/esrt/` recursively and reassemble
        the packed representation.
  - [ ] 8.2 Read `/sys/firmware/dmi/tables/DMI` for SMBIOS.
  - [ ] 8.3 Read `/sys/firmware/acpi/tables/*` for ACPI.
  - [ ] 8.4 Translate `EACCES` / `ENOENT` into `UefiError`.
  - References: Requirement 3.2.

- [ ] 9. Implement `RedfishTableSource` in `sources/redfish.py`.
  - [ ] 9.1 Implement `fetch_esrt` by querying
        `/redfish/v1/UpdateService/FirmwareInventory` and reassembling an
        ESRT-equivalent byte blob.
  - [ ] 9.2 Implement `fetch_smbios` via the `Chassis` resource where
        exposed.
  - [ ] 9.3 Implement `fetch_acpi_*` — raise `UefiError` where the BMC
        does not expose them (Redfish cannot always provide raw ACPI).
  - References: Requirement 3.3, Requirement 3.4.

- [ ] 10. Implement `SystemTablesLoader` in `loader.py`.
  - [ ] 10.1 Author `__init__` that selects the source per
        `client.transport_kind`.
  - [ ] 10.2 Author `_run_with_pattern` dispatcher for `SINGLE_CALL`,
        `SINGLE_CONNECTED_CALL`, `DO_UNTIL_SUCCESS`.
  - [ ] 10.3 Implement each `load_*` method wiring source bytes into the
        corresponding parser.
  - [ ] 10.4 Emit structured logs at `uefi.systables` per `logging_pattern`.
  - References: Requirements 3, 8, 9, 10, 11, 12.

- [ ] 11. Author action-pattern tests.
  - [ ] 11.1 `test_loader_single_call.py`.
  - [ ] 11.2 `test_loader_single_connected_call.py`.
  - [ ] 11.3 `test_loader_do_until_success.py`.
  - [ ] 11.4 `test_loader_unsupported_patterns.py`.
  - References: Requirements 8, 9, 10.

- [ ] 12. Author aspects-none passthrough tests.
  - [ ] 12.1 `test_loader_aspects_none.py`.
  - References: Requirement 11.

- [ ] 13. Collect fixtures.
  - [ ] 13.1 Capture `/sys/firmware/efi/esrt/` from a Linux host.
  - [ ] 13.2 Capture SMBIOS via `dmidecode --dump-bin`.
  - [ ] 13.3 Capture ACPI via `acpidump -b`.
  - [ ] 13.4 Add a minimal OVMF capture for portability.
  - References: Testing Strategy.

- [ ] 14. Author documentation and coverage gate.
  - [ ] 14.1 Usage guide at `userguide/50-02-uefi-system-tables.rst`.
  - [ ] 14.2 pytest-cov gate at 95% for the package.
  - References: Introduction, Testing Strategy.
