# Tasks — Constants and Types

- [ ] 1. Create the package skeleton at
      `source/packages/ccai/protocols/uefi/ueficonst/`.
  - [ ] 1.1 Add `__init__.py` that re-exports the public surface from the
        three submodules (`ueficonst`, `uefiguids`, `uefijumpparams`).
  - [ ] 1.2 Author a module-level docstring in `__init__.py` explaining that
        this package supplies the library's shared constants and that every
        value is read-only by convention.
  - [ ] 1.3 Populate `__all__` in each submodule with the public names.
  - References: Requirement 10, Requirement 11.

- [ ] 2. Author `uefiguids.py`.
  - [ ] 2.1 Define each well-known GUID as a `Final[uuid.UUID]` module
        attribute (`EFI_GLOBAL_VARIABLE_GUID`,
        `EFI_IMAGE_SECURITY_DATABASE_GUID`, `EFI_SYSTEM_RESOURCE_TABLE_GUID`,
        `EFI_CAPSULE_REPORT_GUID`, `EFI_DEVICE_PATH_PROTOCOL_GUID`,
        `EFI_LOADED_IMAGE_PROTOCOL_GUID`,
        `EFI_FIRMWARE_MANAGEMENT_PROTOCOL_GUID`).
  - [ ] 2.2 Build the `WELL_KNOWN_GUIDS` `MappingProxyType` index.
  - [ ] 2.3 Implement `guid_to_bytes(guid)` and `guid_from_bytes(buf)` per
        the design, with Sphinx docstrings and explicit type/length
        validation.
  - References: Requirement 1, Requirement 2.

- [ ] 3. Author `ueficonst.py`.
  - [ ] 3.1 Define the numeric defaults (`DEFAULT_SHELL_TIMEOUT`,
        `DEFAULT_REDFISH_TIMEOUT`, `DEFAULT_RECONNECT_DELAY`,
        `DEFAULT_CHUNK_SIZE`).
  - [ ] 3.2 Define the transport-kind literal constants and the
        `TRANSPORT_KINDS` frozenset.
  - [ ] 3.3 Define `DEFAULT_SHELL_PROMPT_PATTERN`,
        `DEFAULT_INTERACTIVE_SENTINEL`, and `DEFAULT_INTERACTIVE_SENTINEL_STR`.
  - [ ] 3.4 Define `TEMPLATE_COMMAND_SUCCESS` and `TEMPLATE_COMMAND_FAILURE`
        using `os.linesep` between lines.
  - References: Requirement 5, Requirement 6, Requirement 7, Requirement 8.

- [ ] 4. Author `uefijumpparams.py`.
  - [ ] 4.1 Declare the `UefiJumpParams` dataclass with
        `frozen=True, slots=True`.
  - [ ] 4.2 Implement `__post_init__` validation that raises
        `ConfigurationError` on empty `host` or out-of-range `port`, using a
        multiline error message built from a list.
  - [ ] 4.3 Override `__repr__` to redact the embedded credential.
  - References: Requirement 3, Requirement 4.

- [ ] 5. Author unit tests for `uefiguids.py`.
  - [ ] 5.1 Assert the canonical string form of each well-known GUID.
  - [ ] 5.2 Assert `WELL_KNOWN_GUIDS` is a `MappingProxyType` and rejects
        mutation.
  - [ ] 5.3 Round-trip every GUID through `guid_to_bytes` /
        `guid_from_bytes`.
  - [ ] 5.4 Negative tests: non-UUID input to `guid_to_bytes`, wrong-size
        buffer to `guid_from_bytes`.
  - References: Requirement 1, Requirement 2.

- [ ] 6. Author unit tests for `ueficonst.py`.
  - [ ] 6.1 Assert `TRANSPORT_KINDS` contains exactly the four expected
        literals.
  - [ ] 6.2 Assert each `TRANSPORT_KIND_*` constant is a member of the set.
  - [ ] 6.3 Assert templates contain `os.linesep` between the STDOUT / STDERR
        blocks.
  - [ ] 6.4 Assert `DEFAULT_SHELL_PROMPT_PATTERN` compiles as a `bytes`
        regex and matches canonical prompt strings.
  - References: Requirement 5, Requirement 6, Requirement 8.

- [ ] 7. Author unit tests for `uefijumpparams.py`.
  - [ ] 7.1 Construct a valid instance and assert fields round-trip via
        `dataclasses.asdict`.
  - [ ] 7.2 Assert empty `host` raises `ConfigurationError` and the message
        names the field.
  - [ ] 7.3 Assert out-of-range `port` raises `ConfigurationError`.
  - [ ] 7.4 Assert `repr(instance)` contains `"<redacted>"` and does not
        contain any credential secret string.
  - [ ] 7.5 Assert attribute assignment raises `FrozenInstanceError`.
  - References: Requirement 3, Requirement 4.

- [ ] 8. Create `tests/fixtures/guid_registry.json` snapshot and author
      `test_guid_snapshot.py`.
  - [ ] 8.1 Generate the snapshot by iterating `WELL_KNOWN_GUIDS`.
  - [ ] 8.2 Author a test that reads the snapshot file and asserts every
        (name, value) pair matches the live registry.
  - [ ] 8.3 Document the snapshot-update workflow in a short README block
        inside `tests/fixtures/`.
  - References: Requirement 9.

- [ ] 9. Author a zero-dependency import test.
  - [ ] 9.1 Add a test that imports `ueficonst` and `uefiguids` with
        `sys.modules` snapshotted, then asserts no third-party module was
        added to `sys.modules` by the import.
  - [ ] 9.2 Allow-list `ccai.errors` and `ccai.credentials` in the
        test's assertion.
  - References: Requirement 11.

- [ ] 10. Coverage gate.
  - [ ] 10.1 Configure pytest-cov to enforce 100% line coverage for the
        `ccai.protocols.uefi.ueficonst` package.
  - [ ] 10.2 Add a CI step that fails on any drop below 100%.
  - References: Testing Strategy.

- [ ] 11. Documentation.
  - [ ] 11.1 Author `userguide/20-02-uefi-constants-and-types.rst`
        describing the GUID registry, the round-trip helpers, and the
        templating convention.
  - [ ] 11.2 Cross-link from `AGENTS.md` Features section to the guide.
  - References: Introduction.
