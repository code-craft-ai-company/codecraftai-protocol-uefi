# Tasks — Boot To Setup Workflow

- [ ] 1. Create package skeleton at
      `source/packages/ccai/protocols/uefi/workflows/boottosetup/`.
  - [ ] 1.1 Add `__init__.py` re-exporting `BootToSetupWorkflow`,
        `BootToSetupContext`, `BootToSetupReport`, and the
        `boot_to_setup` convenience function.
  - [ ] 1.2 Add a module-level docstring summarizing composition and
        the enter/exit phase contract.
  - References: Requirement 1.

- [ ] 2. Implement `BootToSetupReport`.
  - [ ] 2.1 Author `boottosetupreport.py` with the dataclass fields
        named in the design.
  - [ ] 2.2 Implement `as_dict()` producing JSON-safe output.
  - [ ] 2.3 Sphinx-style docstrings per coding standards.
  - References: Requirement 7.

- [ ] 3. Implement `setupreadyprobe.wait_for_setup`.
  - [ ] 3.1 Author `setupreadyprobe.py`.
  - [ ] 3.2 Implement serial and IPMI-SOL banner detection using the
        `read_until` primitive from `2-transport-serial`.
  - [ ] 3.3 Implement Redfish banner probe via `/Systems/{id}/Bios`
        state poll.
  - [ ] 3.4 Raise `UefiShellError` on timeout with captured bytes in
        `details`.
  - References: Requirement 4.

- [ ] 4. Implement `BootToSetupContext`.
  - [ ] 4.1 Author `boottosetupcontext.py`.
  - [ ] 4.2 Implement `__enter__` returning self.
  - [ ] 4.3 Implement `__exit__` running the exit-phase sequence
        exactly as in the composition diagram.
  - [ ] 4.4 Implement `navigator` and `report` properties.
  - [ ] 4.5 Ensure `os_indications_restored` preserves bits the firmware
        may have changed during Setup per Requirement 6.2.
  - References: Requirements 3, 6, 7.

- [ ] 5. Implement `BootToSetupWorkflow.enter`.
  - [ ] 5.1 Author `boottosetupworkflow.py`.
  - [ ] 5.2 Implement aspects resolution.
  - [ ] 5.3 Read `OsIndicationsSupported`; validate BOOT_TO_FW_UI bit.
  - [ ] 5.4 Read and record current `OsIndications`.
  - [ ] 5.5 Write `OsIndications = prior | BOOT_TO_FW_UI` with attrs
        `NV | BS | RT`.
  - [ ] 5.6 Invoke `6-reboot-and-verify` with `wait_for_shell=False`.
  - [ ] 5.7 Invoke `setupreadyprobe.wait_for_setup`.
  - [ ] 5.8 Construct `HiiFormNavigator` from `5-hii-forms`.
  - [ ] 5.9 Build and return `BootToSetupContext`.
  - [ ] 5.10 Raise `SemanticError` for unsupported `ActionPattern`.
  - [ ] 5.11 Implement `DO_UNTIL_SUCCESS` retry on enter phase only.
  - References: Requirements 1, 2, 3, 4, 5, 8, 9.

- [ ] 6. Implement `boot_to_setup` convenience function.
  - [ ] 6.1 Author the module-level wrapper.
  - [ ] 6.2 Sphinx-style docstring.
  - References: Requirement 1.

- [ ] 7. Wire logging channel `uefi.workflow.boottosetup`.
  - [ ] 7.1 Register the channel through `1-logging`.
  - [ ] 7.2 Emit INFO lines per the design.
  - [ ] 7.3 Emit DEBUG lines with banner detection details.
  - [ ] 7.4 Emit ERROR structured dict on failure.
  - References: Logging section of design.

- [ ] 8. Unit tests.
  - [ ] 8.1 `test_capability_probe.py` — missing bit raises
        `UefiCapabilityError`.
  - [ ] 8.2 `test_preserve_other_bits.py` — prior bits preserved on
        write and restore.
  - [ ] 8.3 `test_setup_banner_timeout.py` — raises `UefiShellError`.
  - [ ] 8.4 `test_restore_failure.py` — raises `UefiVariableError`.
  - [ ] 8.5 `test_aspects_forwarding.py` — forwarded to each
        constituent feature.
  - [ ] 8.6 `test_context_manager.py` — `with` block restores even on
        exception inside block.
  - References: Requirements 2, 3, 6, 8.

- [ ] 9. Action-pattern targeted tests.
  - [ ] 9.1 `test_action_pattern_single_call.py`.
  - [ ] 9.2 `test_action_pattern_do_until_success.py` — enter phase
        retried, exit phase not retried.
  - [ ] 9.3 `test_action_pattern_rejects.py` — three rejected
        patterns raise `SemanticError`.
  - References: Requirement 9.

- [ ] 10. Integration tests against OVMF.
  - [ ] 10.1 Build an OVMF fixture that advertises `BOOT_TO_FW_UI`.
  - [ ] 10.2 `test_ovmf_boot_to_setup_round_trip.py` — asserts
        `setup_ready_at` populated and `os_indications_restored ==
        os_indications_prior`.
  - [ ] 10.3 `test_ovmf_no_boot_to_fw_ui.py` — capability failure.
  - [ ] 10.4 `test_ovmf_navigator_smoke.py` — read one form,
        `len(items) > 0`.
  - [ ] 10.5 Gate behind `CCAI_OVMF_TEST=1`.
  - References: Requirement 10.

- [ ] 11. Report-shape tests.
  - [ ] 11.1 `test_report_as_dict.py` — JSON-safety.
  - [ ] 11.2 Verify report attached to exception `details` on failure.
  - References: Requirement 7.

- [ ] 12. Coverage gate.
  - [ ] 12.1 Configure pytest-cov to enforce 95%+ line coverage for
        `ccai.protocols.uefi.workflows.boottosetup`.
  - [ ] 12.2 CI step failing on drop.
  - References: Testing Strategy.

- [ ] 13. Documentation.
  - [ ] 13.1 Author `userguide/60-05-boot-to-setup.rst` showing a
        `with boot_to_setup(...) as ctx: ...` example.
  - [ ] 13.2 Cross-link from `AGENTS.md` Features section.
  - References: Introduction.
