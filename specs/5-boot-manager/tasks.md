# Tasks — Boot Manager

- [ ] 1. Create package skeleton at
      `source/packages/ccai/protocols/uefi/bootmanager/`.
  - [ ] 1.1 Add `__init__.py` re-exporting `BootManager`, `BootOption`,
        `LoadOptionAttributes`, `pack_u16_array`, `unpack_u16_array`.
  - [ ] 1.2 Add a module-level docstring citing UEFI §3, §3.1.3, §3.3.
  - References: Requirement 1.

- [ ] 2. Implement `attributes.py`.
  - [ ] 2.1 Author `LoadOptionAttributes(IntFlag)` per UEFI §3.1.3
        with `LOAD_OPTION_ACTIVE`, `LOAD_OPTION_FORCE_RECONNECT`,
        `LOAD_OPTION_HIDDEN`, `LOAD_OPTION_CATEGORY_BOOT`,
        `LOAD_OPTION_CATEGORY_APP`, and the `CATEGORY_MASK` constant.
  - [ ] 2.2 Author `category_of(attrs)` helper.
  - [ ] 2.3 Add `test_attributes.py` covering every member value and
        `category_of` edge cases.
  - References: Requirement 3.1, Requirement 13.

- [ ] 3. Implement `order_codec.py`.
  - [ ] 3.1 Author `pack_u16_array(values)` and `unpack_u16_array(buf)`.
  - [ ] 3.2 Add tests for empty buffer, single element, 255 elements,
        out-of-range value, odd-length buffer.
  - References: Requirement 8.

- [ ] 4. Implement `load_option.py` decode path.
  - [ ] 4.1 Author `BootOption` frozen dataclass with fields
        `attributes`, `description`, `file_path_bytes`, `optional_data`.
  - [ ] 4.2 Implement `BootOption.decode(buf)` per UEFI §3.1.3 layout.
  - [ ] 4.3 Implement `_find_ucs2_null_terminator(buf, start)` helper.
  - [ ] 4.4 Add tests for short buffer, missing null terminator,
        over-declared FilePathListLength.
  - References: Requirement 3.

- [ ] 5. Implement `load_option.py` encode path and round-trip.
  - [ ] 5.1 Implement `BootOption.encode()`.
  - [ ] 5.2 Implement non-BMP character rejection raising `UefiError`.
  - [ ] 5.3 Collect decode fixtures corpus from real Windows Boot
        Manager, shim, GRUB, rEFInd, UEFI Shell, HTTP-boot,
        NVMe-over-Fabric captures.
  - [ ] 5.4 Add parametrized round-trip tests over every fixture.
  - References: Requirement 4.

- [ ] 6. Implement `load_option.py` factory classmethods.
  - [ ] 6.1 Implement `BootOption.for_file_path(description,
        volume_alias, path, optional_data, attributes)` constructing
        device-path bytes via 5-device-path-codec.
  - [ ] 6.2 Implement `BootOption.for_http_boot(description, uri,
        optional_data, attributes)` constructing URI + end-of-hardware
        nodes via 5-device-path-codec.
  - [ ] 6.3 Emit `uefi.boot` `WARNING` when `attributes=0`.
  - [ ] 6.4 Add tests that encode/decode every factory output losslessly.
  - References: Requirement 5.

- [ ] 7. Implement `render.py`.
  - [ ] 7.1 Author `BootOption.render()` producing multi-line human-
        readable text.
  - [ ] 7.2 Deferred-import 5-device-path-codec; on ImportError, fall
        back to a hex-dump of `file_path_bytes`.
  - [ ] 7.3 On codec raising on an unknown node type, substitute the
        generic `Path(type,subtype,data)` form and do not raise.
  - [ ] 7.4 Add tests covering the three rendering paths.
  - References: Requirement 13.

- [ ] 8. Implement `manager.py` Boot#### CRUD.
  - [ ] 8.1 Author `BootManager(store, aspects)`.
  - [ ] 8.2 Implement `read_boot_option`, `write_boot_option`,
        `delete_boot_option` with `Boot{option_id:04X}` name assembly.
  - [ ] 8.3 Enforce `0x0000 <= option_id <= 0xFFFF` raising
        `ConfigurationError`.
  - [ ] 8.4 Use `NON_VOLATILE | BOOTSERVICE_ACCESS | RUNTIME_ACCESS`
        attributes on write.
  - [ ] 8.5 Add tests with a fake `VariableStore`.
  - References: Requirement 7.

- [ ] 9. Implement `BootOrder` CRUD and atomic update.
  - [ ] 9.1 Implement `read_boot_order` / `write_boot_order` via the
        order codec.
  - [ ] 9.2 Implement `update_boot_order(transform, aspects)` as an
        optimistic read-modify-write loop bounded by
        `aspects.completion_timeout` / `aspects.completion_interval`.
  - [ ] 9.3 Implement transform-output validation that raises
        `UefiVariableError` on unknown option IDs.
  - [ ] 9.4 Add tests for convergence, concurrent-change retry,
        timeout, and unknown-ID rejection.
  - References: Requirement 8.

- [ ] 10. Implement `BootNext`, `BootCurrent`, `Timeout`.
  - [ ] 10.1 Implement `read_boot_next`, `set_boot_next`,
        `clear_boot_next` with existence verification.
  - [ ] 10.2 Implement `read_boot_current` with Redfish mapping
        fall-back path raising `UefiTransportError` when unmappable.
  - [ ] 10.3 Implement `read_timeout`, `write_timeout`,
        `delete_timeout` with 0..65535 validation.
  - [ ] 10.4 Add tests covering every happy and error path.
  - References: Requirement 9, Requirement 10, Requirement 12.

- [ ] 11. Implement `Driver####` and `SysPrep####` parity.
  - [ ] 11.1 Author `read_driver_option`, `write_driver_option`,
        `delete_driver_option` delegating to the same internal
        `_read_option(prefix, id)` helper.
  - [ ] 11.2 Author `read_driver_order`, `write_driver_order`,
        `update_driver_order`.
  - [ ] 11.3 Author `read_sysprep_option`, `write_sysprep_option`,
        `read_sysprep_order`, `write_sysprep_order`.
  - [ ] 11.4 Add parametrized tests by variable-name prefix.
  - References: Requirement 11.

- [ ] 12. Implement `iter_boot_options`.
  - [ ] 12.1 Author generator that uses
        `VariableStore.iter_variables(name_filter="Boot[0-9A-F][0-9A-F][0-9A-F][0-9A-F]")`.
  - [ ] 12.2 Decode each; on failure emit `WARNING` on `uefi.boot`
        and skip.
  - [ ] 12.3 Yield in sorted option-ID order.
  - [ ] 12.4 Add tests for mixed valid/invalid entries and early close.
  - References: Requirement 14.

- [ ] 13. Action-pattern targeted tests.
  - [ ] 13.1 Add `test_action_patterns.py`.
  - [ ] 13.2 Verify `SINGLE_CALL`, `SINGLE_CONNECTED_CALL`,
        `DO_UNTIL_SUCCESS` for every read and write op.
  - [ ] 13.3 Verify `DO_WHILE_SUCCESS` raises `SemanticError` on every
        write.
  - [ ] 13.4 Verify `DO_UNTIL_CONNECTION_FAILURE` raises
        `SemanticError`.
  - [ ] 13.5 Verify `aspects=None` resolves from `BootManager._aspects`.
  - References: Requirement 1, Requirement 2.

- [ ] 14. Integration against OVMF.
  - [ ] 14.1 Author a QEMU/OVMF integration test that reads default
        `BootOrder`, adds a UEFI Shell `Boot####` entry, sets
        `BootNext`, cold-resets, and verifies `BootCurrent`.
  - [ ] 14.2 Gate the test on an `CCAI_OVMF_AVAILABLE` env var.
  - References: Requirement 7, Requirement 8, Requirement 9,
    Requirement 10.

- [ ] 15. Documentation.
  - [ ] 15.1 Author a usage guide at
        `userguide/50-03-boot-manager.rst` showing CRUD, atomic
        `BootOrder` update, and one-shot `BootNext` patterns.
  - [ ] 15.2 Cross-link from `AGENTS.md` Features section.
  - References: Introduction.
