# Tasks — Shell Commands

- [ ] 1. Create package skeleton at
      `source/packages/ccai/protocols/uefi/shell/`.
  - [ ] 1.1 Add `__init__.py` that re-exports every wrapper function,
        every result dataclass, and the `BcfgTarget`, `ResetKind`,
        `MemoryType`, and `FileAttribute` enums.
  - [ ] 1.2 Add a module-level docstring summarizing the one-module-per-command
        convention and the unparsed-lines preservation rule.
  - [ ] 1.3 Wire up `_common.py` and `_parsers.py` internal modules.
  - References: Requirement 12.1, Requirement 14.1.

- [ ] 2. Implement shared helpers in `_common.py`.
  - [ ] 2.1 Author `quote_shell_path(path)` with tests for embedded
        spaces and embedded double quotes.
  - [ ] 2.2 Author `split_output_lines(raw_stdout)` preserving order and
        dropping blank lines.
  - [ ] 2.3 Author `assemble_cmdline(parts)` helper that joins flag
        tokens with single spaces.
  - [ ] 2.4 Author the retry-result classifier used by each wrapper to
        decide whether a non-zero `exit_status` is allowed.
  - References: Requirement 5.4, Requirement 1.1.

- [ ] 3. Implement `enums.py`.
  - [ ] 3.1 Define `BcfgTarget`, `ResetKind`, `MemoryType`,
        `FileAttribute` per the design.
  - [ ] 3.2 Add `test_enums.py` verifying member names, stable values,
        and pickle stability.
  - References: Requirement 7.5, Requirement 9.1–9.3.

- [ ] 4. Implement `shellmap.py` and its parser.
  - [ ] 4.1 Author the module-level compiled regex `_MAP_ENTRY_LINE`.
  - [ ] 4.2 Author `parse_map_output(raw_stdout, sfo)` as a pure
        function returning `(entries, unparsed_lines)`.
  - [ ] 4.3 Author `shell_map(agent, sfo, aspects)` wrapper.
  - [ ] 4.4 Collect AMI, Insyde, Phoenix, OVMF fixtures under
        `tests/fixtures/shell/map/`.
  - [ ] 4.5 Add parametrized `test_shell_map.py` over the fixtures plus
        the `-sfo` variant.
  - References: Requirement 4, Requirement 13.

- [ ] 5. Implement `shellls.py`, `shellcp.py`, and `shellcd.py`.
  - [ ] 5.1 Author `parse_ls_output` handling both short and long
        formats.
  - [ ] 5.2 Author `shell_ls`, `shell_cp`, `shell_cd` wrappers.
  - [ ] 5.3 Implement the `shell_cd` session-replay hook that registers
        a callable on the `UefiSession._replay_actions` list.
  - [ ] 5.4 Add fixtures for ESP directories including files with
        spaces, hidden files, and subdirectories.
  - [ ] 5.5 Add tests verifying `shell_cp` recursive flag emission and
        `shell_cd(None)` returns the current working directory.
  - References: Requirement 5, Requirement 6.

- [ ] 6. Implement `shellbcfg.py`.
  - [ ] 6.1 Author `parse_bcfg_dump` covering the `dump`, `dump -v`
        variants.
  - [ ] 6.2 Author `shell_bcfg_dump`, `shell_bcfg_add`,
        `shell_bcfg_rm`, `shell_bcfg_mv` wrappers.
  - [ ] 6.3 Wire `BcfgTarget` enum validation at each wrapper entry.
  - [ ] 6.4 Add fixtures covering Boot and Driver targets.
  - [ ] 6.5 Add tests for add/rm/mv on an empty bcfg list and on a full
        list.
  - References: Requirement 7.

- [ ] 7. Implement `shellsetvar.py` and `shelldmpstore.py`.
  - [ ] 7.1 Author `shell_setvar` with flag assembly for `-nv`, `-bs`,
        `-rt`, `-at`, `-guid`.
  - [ ] 7.2 Author the refusal path that raises `SemanticError` for
        `TIME_BASED_AUTHENTICATED_WRITE_ACCESS`.
  - [ ] 7.3 Author `shell_dmpstore` with support for `-s <path>` save
        mode and record-grouping parser.
  - [ ] 7.4 Author a hexdump decoder helper in `_common.py`.
  - [ ] 7.5 Add round-trip tests: setvar → dmpstore readback, for each
        combination of `NON_VOLATILE`, `BOOTSERVICE_ACCESS`,
        `RUNTIME_ACCESS`.
  - References: Requirement 8.

- [ ] 8. Implement `shellreset.py`.
  - [ ] 8.1 Author `shell_reset(agent, kind, reason, aspects)` with
        correct flag emission.
  - [ ] 8.2 Implement the `ConnectionError`-during-output-read tolerance
        path.
  - [ ] 8.3 Add tests covering each `ResetKind` and the reason-quoting
        behavior.
  - References: Requirement 9.

- [ ] 9. Implement `shelldh.py` and `shelldevtree.py`.
  - [ ] 9.1 Author `parse_dh_output` recognizing the protocol-GUID block
        and the optional driver/controller-name blocks.
  - [ ] 9.2 Author `shell_dh(agent, protocol_guid, aspects)` wrapper.
  - [ ] 9.3 Author the indentation-aware `parse_devtree_output` building
        a `DevtreeNode` tree.
  - [ ] 9.4 Author `shell_devtree(agent, aspects)` wrapper.
  - [ ] 9.5 Add fixtures with deeply nested devtrees (≥ 6 levels).
  - [ ] 9.6 Add tests for indentation-reconciliation failure raising
        `UefiShellError`.
  - References: Requirement 10.

- [ ] 10. Implement `shellmemmap.py`, `shellsmbiosview.py`, `shelldrivers.py`.
  - [ ] 10.1 Author `parse_memmap_output` mapping Shell type names onto
        `MemoryType` enum values.
  - [ ] 10.2 Author `parse_smbiosview_output` with per-type-code field
        dictionaries.
  - [ ] 10.3 Author `parse_drivers_output`.
  - [ ] 10.4 Wire each wrapper function.
  - [ ] 10.5 Add fixtures from AMI, Insyde, Phoenix, OVMF for each.
  - [ ] 10.6 Add tests asserting `fields` dict immutability via
        `MappingProxyType` on property access.
  - References: Requirement 11.

- [ ] 11. Enforce compile-time regex discipline.
  - [ ] 11.1 Add a `conftest.py` plugin that walks `shell/` at collection
        time and asserts every regex referenced in wrapper modules is a
        module-level `re.Pattern`, not constructed inside a function.
  - [ ] 11.2 Add a smoke test that imports every wrapper module and
        verifies zero `re.error` at import.
  - References: Requirement 14.

- [ ] 12. Action-pattern targeted tests.
  - [ ] 12.1 Add `test_action_patterns.py` with a fake `UefiAgent`
        scripted to fail N attempts and succeed on attempt N+1.
  - [ ] 12.2 Verify `SINGLE_CALL` invokes exactly once.
  - [ ] 12.3 Verify `SINGLE_CONNECTED_CALL` invokes exactly once with
        open/close pair.
  - [ ] 12.4 Verify `DO_UNTIL_SUCCESS` stops on first success within
        `completion_timeout`.
  - [ ] 12.5 Verify `DO_WHILE_SUCCESS` re-issues while success and stops
        on first failure.
  - [ ] 12.6 Verify `DO_UNTIL_CONNECTION_FAILURE` raises `SemanticError`.
  - [ ] 12.7 Verify `aspects=None` resolves from `agent.aspects`.
  - References: Requirement 1, Requirement 2.

- [ ] 13. Fixtures corpus infrastructure.
  - [ ] 13.1 Stand up `tests/fixtures/shell/` with the subdirectory
        structure documented in the design.
  - [ ] 13.2 Add a `conftest.py` fixture that parametrizes over every
        fixture file and its `.expected.json` sibling.
  - [ ] 13.3 Add a CI gate that fails when a fixture file exists with
        no matching test parametrization.
  - References: Requirement 13.

- [ ] 14. Documentation.
  - [ ] 14.1 Author a usage guide at
        `userguide/50-01-shell-commands.rst` showing one example per
        wrapper.
  - [ ] 14.2 Cross-link each wrapper's docstring to the relevant UEFI
        Shell Specification section number.
  - [ ] 14.3 Cross-link from `AGENTS.md` Features section.
  - References: Introduction.
