# Tasks — Loaded Image

- [ ] 1. Create package skeleton at
      `source/packages/automojo/protocols/uefi/loadedimage/`.
  - [ ] 1.1 Add `__init__.py` re-exporting `LoadedImage`,
        `CoffFileHeader`, `PeOptionalHeader`, `DataDirectoryEntry`,
        `CoffSection`, `AttributeCertificate`, `Machine`, `Subsystem`,
        `Characteristics`, `DllCharacteristics`,
        `LoadedImageProtocolReader`.
  - [ ] 1.2 Document the read-only contract in the module docstring.
  - References: Requirement 11.

- [ ] 2. Implement `enums.py`.
  - [ ] 2.1 Author `Machine(IntEnum)` with `I386`, `X64`, `AARCH64`,
        `RISCV64`, and `UNKNOWN` members.
  - [ ] 2.2 Author `Subsystem(IntEnum)` with `EFI_APPLICATION`,
        `EFI_BOOT_SERVICE_DRIVER`, `EFI_RUNTIME_DRIVER`, `EFI_ROM`,
        `UNKNOWN`.
  - [ ] 2.3 Author `Characteristics` and `DllCharacteristics` frozen
        dataclasses with named boolean properties per the design.
  - [ ] 2.4 Add `test_machine_decode.py`, `test_subsystem_enum.py`,
        `test_characteristics.py`, `test_dll_characteristics.py`.
  - References: Requirement 1, Requirement 5, Requirement 10.

- [ ] 3. Implement `headers.py`.
  - [ ] 3.1 Author `DataDirectoryEntry` (frozen dataclass).
  - [ ] 3.2 Author `CoffFileHeader` (frozen dataclass) with a
        `from_bytes(buf, offset)` helper performing bounds checks.
  - [ ] 3.3 Author `PeOptionalHeader` (frozen dataclass) covering PE32
        and PE32+ variants via `from_bytes(buf, offset)`.
  - [ ] 3.4 Enforce `NumberOfRvaAndSizes <= 16` and reject unknown
        magics with `UefiError`.
  - [ ] 3.5 Add `test_optional_header.py` and `test_data_directories.py`.
  - References: Requirement 2, Requirement 6, Requirement 9.

- [ ] 4. Implement `sections.py`.
  - [ ] 4.1 Author `CoffSection` (frozen dataclass).
  - [ ] 4.2 Author `parse_sections(buf, section_table_offset, count,
        string_table_offset)` that resolves `/N` names via the COFF
        string table.
  - [ ] 4.3 Bounds-check `pointer_to_raw_data + size_of_raw_data`
        against buffer length.
  - [ ] 4.4 Add `test_sections.py`.
  - References: Requirement 3.

- [ ] 5. Implement `certs.py`.
  - [ ] 5.1 Author `AttributeCertificate` (frozen dataclass).
  - [ ] 5.2 Author `parse_attribute_certificates(buf, security_directory)`
        iterating the 8-byte-aligned table per PE/COFF spec.
  - [ ] 5.3 Populate `pkcs7_der` when revision == 0x0200 and
        certificate_type == 0x0002.
  - [ ] 5.4 Tolerate other revision values (surface them but leave
        `pkcs7_der = None`).
  - [ ] 5.5 Add `test_authenticode_extract.py`,
        `test_authenticode_multi.py`, `test_authenticode_unsigned.py`.
  - References: Requirement 4.

- [ ] 6. Implement `image.py` (root parser).
  - [ ] 6.1 Author `LoadedImage.__init__` per the design signature.
  - [ ] 6.2 Implement `from_bytes(buf)` — validate DOS stub, read
        `e_lfanew`, validate `PE\0\0`, dispatch to header parsers, parse
        sections, parse attribute certificates.
  - [ ] 6.3 Implement `from_path(path)` by reading bytes and delegating
        to `from_bytes`.
  - [ ] 6.4 Implement `sections()` generator.
  - [ ] 6.5 Implement `authenticode_signatures()` returning non-empty
        entries.
  - [ ] 6.6 Implement `data_directory(name)` with the named set from
        Requirement 6.3.
  - [ ] 6.7 Implement `machine`, `subsystem`, `raw_subsystem`,
        `pe_plus`, `sha256`, `raw_bytes` properties.
  - [ ] 6.8 Add `test_mz_pe_signatures.py` and `test_bounds.py`.
  - References: Requirements 1, 2, 3, 4, 5, 6, 8, 9, 12.

- [ ] 7. Implement `protocol_reader.py`.
  - [ ] 7.1 Author `LoadedImageProtocolReader.__init__` taking a
        `UefiShellClient` and `default_aspects`.
  - [ ] 7.2 Implement `device_path(handle, aspects)` by issuing the
        UEFI Shell `dh -d <handle>` command and parsing the
        `EFI_LOADED_IMAGE_PROTOCOL` line.
  - [ ] 7.3 Implement `_run_with_pattern` supporting `SINGLE_CALL`,
        `SINGLE_CONNECTED_CALL`, `DO_UNTIL_SUCCESS`; reject other
        patterns with `SemanticError`.
  - [ ] 7.4 Translate Boot-Services-unavailable conditions into
        `UefiError` with transport details.
  - [ ] 7.5 Add `test_protocol_reader_dh_parse.py` and
        `test_protocol_reader_action_patterns.py`.
  - References: Requirement 7.

- [ ] 8. Collect fixtures.
  - [ ] 8.1 Add `tests/fixtures/loadedimage/shim.efi` (upstream binary).
  - [ ] 8.2 Add `tests/fixtures/loadedimage/grubx64.efi`.
  - [ ] 8.3 Add `tests/fixtures/loadedimage/uefi-rust-app.efi` — build
        a minimal `uefi` crate hello-world and commit the artifact.
  - [ ] 8.4 Add a SHA-256 manifest file `fixtures.sha256.txt` and a
        `test_fixture_integrity.py` that verifies the manifest.
  - References: Requirement 13.

- [ ] 9. Author read-only enforcement tests.
  - [ ] 9.1 `test_no_write_methods.py` — static scan for `write_`,
        `rewrite_`, `re_sign` method names in the package.
  - [ ] 9.2 `test_no_filesystem_writes.py` — pytest hook asserts no
        fixture file is opened for writing during the suite.
  - [ ] 9.3 `test_no_network.py` — socket / subprocess audit hook.
  - References: Requirement 11.

- [ ] 10. Author aspects-none passthrough test for the protocol reader.
  - [ ] 10.1 `test_protocol_reader_aspects_none.py` — `aspects=None`
        substitutes the instance default; missing default raises
        `ConfigurationError`.
  - References: Requirement 7.

- [ ] 11. Author round-trip byte tests.
  - [ ] 11.1 `test_raw_bytes_identity.py` — `LoadedImage.from_bytes(buf)
        .raw_bytes == buf`.
  - [ ] 11.2 `test_sha256.py` — digest matches `hashlib.sha256(buf)`.
  - References: Requirement 12.

- [ ] 12. Integration tests.
  - [ ] 12.1 Parse each fixture image and assert machine, subsystem,
        section count, and signature count match committed expected
        values.
  - [ ] 12.2 Parse each fixture image twice and assert output equality.
  - References: Requirement 13.

- [ ] 13. Documentation.
  - [ ] 13.1 Usage guide at `userguide/50-05-uefi-loaded-image.rst`
        with parser examples, subsystem enum reference, and
        Authenticode-extraction walkthrough.
  - [ ] 13.2 Cross-link from `AGENTS.md` Features section.
  - References: Introduction.

- [ ] 14. Coverage gate.
  - [ ] 14.1 Configure pytest-cov to enforce 95% line / 90% branch
        coverage for the package.
  - [ ] 14.2 CI step fails on drop below the threshold.
  - References: Testing Strategy.
