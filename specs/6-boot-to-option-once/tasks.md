# Tasks — Boot To Option Once Workflow

- [ ] 1. Create package skeleton at
      `source/packages/ccai/protocols/uefi/workflows/boottooptiononce/`.
  - [ ] 1.1 Add `__init__.py` re-exporting
        `BootToOptionOnceWorkflow`, `BootToOptionOnceReport`, and the
        `boot_to_option_once` convenience function.
  - [ ] 1.2 Add a module-level docstring summarizing composition and
        the UEFI §3.1.5 compliance check.
  - References: Requirement 1.

- [ ] 2. Implement `BootToOptionOnceReport`.
  - [ ] 2.1 Author `boottooptiononcereport.py` with the frozen
        dataclass fields named in the design.
  - [ ] 2.2 Implement `as_dict()` producing JSON-safe output.
  - [ ] 2.3 Sphinx-style docstrings per coding standards.
  - References: Requirement 8.

- [ ] 3. Implement `cleanup.delete_boot_next`.
  - [ ] 3.1 Author `cleanup.py`.
  - [ ] 3.2 Implement best-effort delete returning `None` on success
        or a string describing the failure.
  - [ ] 3.3 Never raise from this helper; the caller is always in an
        already-failing path.
  - References: Requirement 7.

- [ ] 4. Implement `BootToOptionOnceWorkflow.run`.
  - [ ] 4.1 Author `boottooptiononce.py` with constructor and `run()`.
  - [ ] 4.2 Implement aspects resolution.
  - [ ] 4.3 Validate `option_id` in `0x0000..0xFFFF`, raise
        `ConfigurationError` otherwise.
  - [ ] 4.4 Read `Boot{option_id:04X}` via `5-boot-manager`; raise
        `UefiVariableError` if absent.
  - [ ] 4.5 Decode `EFI_LOAD_OPTION` unless `aspects.skip_decode=True`.
  - [ ] 4.6 Read `BootOrder` and record as `boot_order_before`.
  - [ ] 4.7 Read existing `BootNext` and record.
  - [ ] 4.8 Write `BootNext = option_id` as UINT16 LE with attrs
        `NV | BS | RT`.
  - [ ] 4.9 Invoke `6-reboot-and-verify(wait_for_shell=True)`.
  - [ ] 4.10 Read `BootCurrent`, `BootNext`, `BootOrder` post-reset.
  - [ ] 4.11 Validate `BootCurrent == option_id`.
  - [ ] 4.12 Validate `BootOrder` byte-equal to `boot_order_before`.
  - [ ] 4.13 Validate `BootNext` cleared per UEFI §3.1.5; raise
        `UefiVariableError` with spec citation on violation.
  - [ ] 4.14 On failure between write and reboot, invoke
        `cleanup.delete_boot_next` and append its outcome to the
        primary exception's details.
  - [ ] 4.15 Raise `SemanticError` on unsupported `ActionPattern`.
  - [ ] 4.16 Implement `DO_UNTIL_SUCCESS` retry on the full workflow.
  - References: Requirements 1, 2, 3, 4, 5, 6, 7, 9, 10.

- [ ] 5. Implement `boot_to_option_once` convenience function.
  - [ ] 5.1 Author the module-level wrapper.
  - [ ] 5.2 Sphinx-style docstring.
  - References: Requirement 1.

- [ ] 6. Wire logging channel `uefi.workflow.bootnext`.
  - [ ] 6.1 Register the channel through `1-logging`.
  - [ ] 6.2 Emit INFO / DEBUG / ERROR lines per the design.
  - References: Logging section of design.

- [ ] 7. Unit tests — existence check.
  - [ ] 7.1 `test_option_missing.py` — absent variable raises
        `UefiVariableError`.
  - [ ] 7.2 `test_decode_failure.py` — malformed load option raises.
  - [ ] 7.3 `test_skip_decode.py` — bypasses decode path.
  - References: Requirement 2.

- [ ] 8. Unit tests — BootOrder preservation.
  - [ ] 8.1 `test_bootorder_preserved.py` — unchanged BootOrder
        passes.
  - [ ] 8.2 `test_bootorder_mutated.py` — changed BootOrder raises
        `UefiVariableError` with both values in details.
  - References: Requirement 3.

- [ ] 9. Unit tests — BootNext auto-clear.
  - [ ] 9.1 `test_bootnext_cleared.py` — absent →
        `boot_next_cleared=True`.
  - [ ] 9.2 `test_bootnext_persists_same_value.py` — value equals
        option_id → raise §3.1.5 violation.
  - [ ] 9.3 `test_bootnext_persists_different_value.py` — value
        differs → record and raise.
  - References: Requirement 6.

- [ ] 10. Unit tests — BootCurrent check.
  - [ ] 10.1 `test_bootcurrent_matches.py`.
  - [ ] 10.2 `test_bootcurrent_mismatch.py` — raises with both values
        in details.
  - References: Requirement 5.

- [ ] 11. Unit tests — cleanup path.
  - [ ] 11.1 `test_cleanup_after_write_failure.py` — post-write
        failure triggers best-effort delete.
  - [ ] 11.2 `test_cleanup_delete_fails.py` — delete failure appended
        to primary exception.
  - References: Requirement 7.

- [ ] 12. Action-pattern targeted tests.
  - [ ] 12.1 `test_action_pattern_single_call.py`.
  - [ ] 12.2 `test_action_pattern_do_until_success.py`.
  - [ ] 12.3 `test_action_pattern_rejects.py` — three rejected
        patterns raise `SemanticError`.
  - References: Requirement 10.

- [ ] 13. Integration tests against OVMF.
  - [ ] 13.1 `test_ovmf_shell_boot_once.py` — asserts
        `boot_current_after == option_id` and `boot_next_cleared is
        True`.
  - [ ] 13.2 `test_ovmf_invalid_option.py` — existence-failure path.
  - [ ] 13.3 `test_ovmf_simulated_noncompliance.py` — hook that
        re-sets `BootNext` after clear to exercise §3.1.5 violation
        path.
  - [ ] 13.4 Gate behind `CCAI_OVMF_TEST=1`.
  - References: Requirement 11.

- [ ] 14. Coverage gate and documentation.
  - [ ] 14.1 Configure pytest-cov to enforce 95%+ line coverage for
        `ccai.protocols.uefi.workflows.boottooptiononce`.
  - [ ] 14.2 CI step failing on drop.
  - [ ] 14.3 Author `userguide/60-06-boot-to-option-once.rst` with a
        minimal example plus the UEFI §3.1.5 compliance note.
  - [ ] 14.4 Cross-link from `AGENTS.md` Features section.
  - References: Testing Strategy, Introduction.
