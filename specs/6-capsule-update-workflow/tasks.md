# Tasks — Capsule Update Workflow

- [ ] 1. Create package skeleton at
      `source/packages/automojo/protocols/uefi/workflows/capsuleupdate/`.
  - [ ] 1.1 Add `__init__.py` re-exporting `CapsuleUpdateWorkflow`,
        `CapsuleUpdateReport`, and the `capsule_update_workflow`
        convenience function.
  - [ ] 1.2 Add a module-level docstring summarizing composition and
        safety-gate ordering.
  - References: Requirement 1.

- [ ] 2. Implement `CapsuleUpdateReport` dataclass.
  - [ ] 2.1 Author `capsuleupdatereport.py` with all fields named in the
        design.
  - [ ] 2.2 Implement `as_dict()` producing JSON-safe output (base64 the
        embedded `RebootAndVerifyReport.console_capture`).
  - [ ] 2.3 Add Sphinx-style `:param:` / `:returns:` docstrings per
        repository coding standards.
  - References: Requirement 7.

- [ ] 3. Implement `SafetyGates` preflight checks.
  - [ ] 3.1 Author `safetygates.py` with one method per gate named in the
        design, each returning `None` and raising on failure.
  - [ ] 3.2 Implement header-validity gate parsing
        `EFI_CAPSULE_HEADER` per UEFI spec §23.3.
  - [ ] 3.3 Implement firmware-capability gate reading
        `OsIndicationsSupported` per UEFI spec §8.5.1.
  - [ ] 3.4 Implement ESRT-presence gate reading ESRT per UEFI spec §23.4.
  - [ ] 3.5 Implement NV-storage-sufficiency gate calling
        `QueryVariableInfo`.
  - [ ] 3.6 Implement delivery-mode/transport compatibility gate.
  - [ ] 3.7 Implement dry-run short-circuit logic.
  - References: Requirement 8.

- [ ] 4. Implement `esrtdiff.advanced`.
  - [ ] 4.1 Author `esrtdiff.py` with the `advanced()` function.
  - [ ] 4.2 Support optional `firmware_class_guid` filter.
  - [ ] 4.3 Unit tests: no advance, single advance, multiple advances,
        `firmware_class_guid` filter behavior.
  - References: Requirement 6.

- [ ] 5. Implement `CapsuleUpdateWorkflow.run`.
  - [ ] 5.1 Author `capsuleupdateworkflow.py` with constructor and
        `run()` method.
  - [ ] 5.2 Implement aspects resolution and delivery-mode default.
  - [ ] 5.3 Implement capsule bytes/path loading and SHA-256 hashing.
  - [ ] 5.4 Run safety gates in the documented order.
  - [ ] 5.5 Take ESRT snapshot before delivery.
  - [ ] 5.6 If `aspects.dry_run`, build partial report and return.
  - [ ] 5.7 Deliver capsule via `5-firmware-capsules`.
  - [ ] 5.8 Invoke `6-reboot-and-verify(wait_for_shell=True)`.
  - [ ] 5.9 Read Capsule Report variables per UEFI spec §8.5.6.
  - [ ] 5.10 Snapshot ESRT post-reboot and run `esrtdiff.advanced`.
  - [ ] 5.11 Raise `SemanticError` on unsupported `ActionPattern`.
  - [ ] 5.12 Implement `DO_UNTIL_SUCCESS` retry on transient delivery
        failures.
  - References: Requirements 1, 2, 3, 4, 5, 6, 8, 9, 10, 12.

- [ ] 6. Implement `capsule_update_workflow` convenience function.
  - [ ] 6.1 Author the module-level wrapper.
  - [ ] 6.2 Sphinx-style docstring per coding standards.
  - References: Requirement 1.

- [ ] 7. Wire logging channel `uefi.workflow.capsule`.
  - [ ] 7.1 Register the channel through `1-logging`.
  - [ ] 7.2 Emit INFO lines for start, each gate pass, delivery,
        reboot, report read.
  - [ ] 7.3 Emit DEBUG hex dumps of ESRT before/after.
  - [ ] 7.4 Emit ERROR structured dict on failure.
  - References: Logging section of design.

- [ ] 8. Unit tests.
  - [ ] 8.1 `test_safetygates_ordering.py` — each gate short-circuits
        later gates.
  - [ ] 8.2 `test_safetygates_header.py` — malformed capsules rejected.
  - [ ] 8.3 `test_safetygates_capability.py` — missing capability bit
        rejected.
  - [ ] 8.4 `test_safetygates_nvstorage.py` — insufficient storage
        rejected.
  - [ ] 8.5 `test_workflow_dry_run.py` — gates run, no delivery or
        reboot.
  - [ ] 8.6 `test_workflow_hashing.py` — SHA-256 matches golden
        vectors for bytes and path inputs.
  - [ ] 8.7 `test_workflow_aspects_delegation.py` — mock layer-5
        features and assert forwarded aspects.
  - [ ] 8.8 `test_esrt_advancement.py` — before/after diff cases.
  - References: Requirements 2, 3, 4, 6, 8, 9, 12.

- [ ] 9. Action-pattern targeted tests.
  - [ ] 9.1 `test_action_pattern_single_call.py`.
  - [ ] 9.2 `test_action_pattern_do_until_success.py` — transient then
        success.
  - [ ] 9.3 `test_action_pattern_rejects.py` — three rejected patterns
        raise `SemanticError`.
  - References: Requirement 10.

- [ ] 10. Integration tests against OVMF.
  - [ ] 10.1 Build a test-signed capsule fixture (FMP format).
  - [ ] 10.2 `test_ovmf_capsule_update_success.py` — asserts
        `esrt_advanced` and `reported_status_guid ==
        EFI_CAPSULE_REPORT_STATUS_SUCCESS_GUID`.
  - [ ] 10.3 `test_ovmf_capsule_update_no_esrt.py` — precondition
        failure.
  - [ ] 10.4 `test_ovmf_capsule_update_dry_run.py` — dry-run returns
        without flashing.
  - [ ] 10.5 Gate behind `AUTOMOJO_OVMF_TEST=1` and
        `AUTOMOJO_CAPSULE_FIXTURE`.
  - References: Requirement 11.

- [ ] 11. Report-shape tests.
  - [ ] 11.1 `test_report_as_dict.py` — JSON-safety, base64 of nested
        console_capture.
  - [ ] 11.2 Verify report is attached to exception `details` on failure.
  - References: Requirement 7.

- [ ] 12. Coverage gate.
  - [ ] 12.1 Configure pytest-cov to enforce 95%+ line coverage for
        `automojo.protocols.uefi.workflows.capsuleupdate`.
  - [ ] 12.2 Add a CI step failing on drop.
  - References: Testing Strategy.

- [ ] 13. Documentation.
  - [ ] 13.1 Author `userguide/60-02-capsule-update-workflow.rst` with a
        minimal usage example, dry-run example, and the full report
        shape.
  - [ ] 13.2 Cross-link from `AGENTS.md` Features section.
  - References: Introduction.
