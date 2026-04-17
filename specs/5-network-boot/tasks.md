# Tasks — Network Boot

- [ ] 1. Create package skeleton at
      `source/packages/automojo/protocols/uefi/netboot/`.
  - [ ] 1.1 Add `__init__.py` exporting `NetworkBoot`,
        `build_http_boot_option`, `build_pxe_boot_option_ipv4`,
        `build_pxe_boot_option_ipv6`.
  - [ ] 1.2 Module docstring summarizing §24.3 / §24.7 scope.
  - References: Introduction.

- [ ] 2. Implement `options.py`.
  - [ ] 2.1 Author `LoadOptionAttrFlags(IntFlag)` with
        `LOAD_OPTION_ACTIVE`, `LOAD_OPTION_FORCE_RECONNECT`,
        `LOAD_OPTION_HIDDEN`, `LOAD_OPTION_CATEGORY_APP`,
        `LOAD_OPTION_CATEGORY_BOOT` per UEFI §3.1.3.
  - [ ] 2.2 Author `_assemble_attributes(hidden, category_app)` helper
        combining flags.
  - References: Requirement 11.

- [ ] 3. Implement `httpbuilder.py`.
  - [ ] 3.1 Author `_validate_uri(uri)` rejecting non-str and
        non-`http/https` schemes.
  - [ ] 3.2 Author `_validate_mac(mac_address)` enforcing 6-byte length.
  - [ ] 3.3 Author `_build_http_boot_option(...)` assembling MAC
        (optional) + URI + end-of-path nodes into a `DevicePath` and
        returning a `BootOption`.
  - References: Requirement 1.

- [ ] 4. Implement `pxebuilder.py`.
  - [ ] 4.1 Author `_build_pxe_boot_option_ipv4(...)` with zero-IP default
        and explicit-IP packing in big-endian order.
  - [ ] 4.2 Author `_build_pxe_boot_option_ipv6(...)` with zero-IP default
        and explicit-IP 16-byte packing.
  - [ ] 4.3 Validate IPv4 / IPv6 address lengths and raise
        `ConfigurationError` with details on mismatch.
  - References: Requirement 2, Requirement 3.

- [ ] 5. Implement `installer.py`.
  - [ ] 5.1 Author `_install_option(...)` handling slot allocation,
        occupied-slot detection, write via 5-boot-manager, and optional
        `BootOrder` append.
  - [ ] 5.2 Author `_trigger_one_shot(...)` with pre-write existence
        check and optional readback verify.
  - [ ] 5.3 Route all aspects through to 5-boot-manager.
  - References: Requirement 4, Requirement 5.

- [ ] 6. Implement `netbootfacade.py`.
  - [ ] 6.1 Author the `NetworkBoot` class wiring agent + boot-manager +
        builders + installer.
  - [ ] 6.2 Every public method calls `resolve_aspects` at the top.
  - [ ] 6.3 Raise `SemanticError` for `DO_WHILE_SUCCESS` and
        `DO_UNTIL_CONNECTION_FAILURE` on install-type calls.
  - References: Requirement 7, Requirement 8, Requirement 9, Requirement 10.

- [ ] 7. Unit tests — `test_httpbuilder.py`.
  - [ ] 7.1 `http://` and `https://` URIs accepted.
  - [ ] 7.2 Non-string URI raises `ConfigurationError`.
  - [ ] 7.3 Unsupported scheme raises `ConfigurationError`.
  - [ ] 7.4 MAC prefix present iff `mac_address` given.
  - [ ] 7.5 Attribute flag combinations (`hidden`, `category_app`).
  - [ ] 7.6 `optional_data` bytes appended verbatim.
  - References: Requirement 1, Requirement 11.

- [ ] 8. Unit tests — `test_pxebuilder.py`.
  - [ ] 8.1 IPv4 zero-IP default produces all-zero bytes.
  - [ ] 8.2 IPv4 explicit IP packed big-endian.
  - [ ] 8.3 IPv6 zero-IP default produces all-zero bytes.
  - [ ] 8.4 IPv6 explicit IP packed in network-byte order.
  - [ ] 8.5 MAC length validation.
  - References: Requirement 2, Requirement 3.

- [ ] 9. Round-trip test (`test_roundtrip.py`).
  - [ ] 9.1 For each builder: build → encode → decode → equal.
  - [ ] 9.2 For each builder: build → encode → decode → `to_text` →
        `from_text` → encode → equal.
  - [ ] 9.3 Include at least one example per builder in a fixtures file
        matching the text form produced by
        `ConvertDevicePathToText()` from EDK2.
  - References: Requirement 6.

- [ ] 10. Installer tests (`test_installer.py`).
  - [ ] 10.1 `install(slot=None)` calls `allocate_free_slot` and returns
        the slot.
  - [ ] 10.2 `install` into occupied slot raises `UefiVariableError`.
  - [ ] 10.3 `append_to_boot_order=True` calls the boot-manager method.
  - [ ] 10.4 `append_to_boot_order=False` does not.
  - [ ] 10.5 `trigger_one_shot` with missing slot raises.
  - [ ] 10.6 `trigger_one_shot(verify=True)` detects readback mismatch.
  - References: Requirement 4, Requirement 5.

- [ ] 11. Action-pattern tests (`test_actionpatterns.py`).
  - [ ] 11.1 `SINGLE_CALL` performs one write.
  - [ ] 11.2 `SINGLE_CONNECTED_CALL` retries connection-class
        exceptions.
  - [ ] 11.3 `DO_UNTIL_SUCCESS` polls until success or timeout.
  - [ ] 11.4 `DO_WHILE_SUCCESS` and `DO_UNTIL_CONNECTION_FAILURE` raise
        `SemanticError`.
  - [ ] 11.5 `aspects=None` falls back to instance default via
        `resolve_aspects`.
  - References: Requirement 7, Requirement 8, Requirement 9, Requirement 10.

- [ ] 12. Integration tests (`test_netboot_integration.py`).
  - [ ] 12.1 Against an OVMF VM: install HTTP option at a local HTTP
        server, write `BootNext`, reboot via 5-system-reset, verify VM
        fetched the image (check server logs).
  - [ ] 12.2 Gate on `AUTOMOJO_UEFI_OVMF_VM` env var.
  - [ ] 12.3 Repeat for PXE IPv4 and PXE IPv6 with a minimal dnsmasq
        PXE stub.
  - References: Requirement 4, Requirement 5.

- [ ] 13. Logging integration.
  - [ ] 13.1 Obtain channel `uefi.netboot` via
        `get_uefi_logger("uefi.netboot")`.
  - [ ] 13.2 Honor `aspects.logging_pattern` per the measured-interop
        contract.
  - References: Requirement 12.

- [ ] 14. Documentation.
  - [ ] 14.1 Author a usage guide at `userguide/25-02-network-boot.rst`
        covering the build → install → trigger flow for HTTP, PXE-v4,
        PXE-v6 with a runnable OVMF example.
  - [ ] 14.2 Cross-link from `AGENTS.md` Features section.
  - References: Introduction.
