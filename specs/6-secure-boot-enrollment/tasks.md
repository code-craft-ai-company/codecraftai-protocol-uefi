# Tasks — Secure Boot Enrollment Workflow

- [ ] 1. Create package skeleton at
      `source/packages/automojo/protocols/uefi/workflows/secureboot/`.
  - [ ] 1.1 Add `__init__.py` re-exporting
        `SecureBootEnrollmentWorkflow`, `SecureBootEnrollmentReport`,
        `SecureBootBundle`, `BundleEntry`, `VariableWrite`, and the
        `secure_boot_enrollment` convenience function.
  - [ ] 1.2 Add a module-level docstring summarizing composition,
        safety invariants, and the must_connect requirement.
  - References: Requirement 1, Requirement 2.

- [ ] 2. Implement `SecureBootBundle` and `BundleEntry`.
  - [ ] 2.1 Author `secureboothbundle.py` with frozen dataclasses.
  - [ ] 2.2 Implement `__post_init__` validation (empty bundle, PK
        exactly one entry, prewrapped XOR signer triple).
  - [ ] 2.3 Sphinx-style docstrings per coding standards.
  - References: Requirement 7.

- [ ] 3. Implement `VariableWrite`.
  - [ ] 3.1 Author the frozen dataclass in
        `secureboothenrollmentreport.py`.
  - References: Requirement 9.

- [ ] 4. Implement `SecureBootEnrollmentReport`.
  - [ ] 4.1 Author `secureboothenrollmentreport.py` with all fields
        named in the design.
  - [ ] 4.2 Implement `as_dict()` with SHA-256-only payload rendering.
  - References: Requirement 9.

- [ ] 5. Implement `SafetyGates`.
  - [ ] 5.1 Author `safetygates.py`.
  - [ ] 5.2 Implement must_connect invariant gate first.
  - [ ] 5.3 Implement bundle-validity gate.
  - [ ] 5.4 Implement SetupMode precondition per UEFI §32.3 including
        `DeployedMode == 1` unconditional refusal.
  - [ ] 5.5 Implement signature-algorithm capability probe.
  - [ ] 5.6 Implement dry-run short-circuit.
  - References: Requirements 2, 3, 4.4, 6, 7.

- [ ] 6. Implement `verifywrite.verify`.
  - [ ] 6.1 Author `verifywrite.py`.
  - [ ] 6.2 Re-read the variable, parse signature list via
        `5-secure-boot`, compare expected vs. observed.
  - [ ] 6.3 Raise `UefiSecureBootError` with missing entries in
        `details` on mismatch.
  - References: Requirement 5.

- [ ] 7. Implement `SecureBootEnrollmentWorkflow.run`.
  - [ ] 7.1 Author `secureboothenrollmentworkflow.py`.
  - [ ] 7.2 Implement aspects resolution and must_connect enforcement.
  - [ ] 7.3 Run safety gates.
  - [ ] 7.4 Read and record initial state variables.
  - [ ] 7.5 Dry-run short-circuit builds plan and returns.
  - [ ] 7.6 Enroll in order `(dbx, db, KEK, PK)` per UEFI §32.
  - [ ] 7.7 Use `APPEND_WRITE` for `dbx` entries per UEFI §8.2.5.
  - [ ] 7.8 Use `TIME_BASED_AUTHENTICATED_WRITE_ACCESS` for PK per
        UEFI §8.2.5.
  - [ ] 7.9 Verify each write before moving to the next.
  - [ ] 7.10 Verify `SetupMode` transition to `0` and `SecureBoot ==
        1` after PK enroll.
  - [ ] 7.11 Raise `SemanticError` on unsupported `ActionPattern`.
  - References: Requirements 1, 3, 4, 5, 8, 10, 11.

- [ ] 8. Implement `secure_boot_enrollment` convenience function.
  - [ ] 8.1 Author the module-level wrapper.
  - [ ] 8.2 Sphinx-style docstring.
  - References: Requirement 1.

- [ ] 9. Wire logging channel `uefi.workflow.secureboot`.
  - [ ] 9.1 Register the channel through `1-logging`.
  - [ ] 9.2 Emit INFO / DEBUG lines per the design.
  - [ ] 9.3 Audit: no log line contains raw key material.
  - References: Logging and Security Considerations sections of design.

- [ ] 10. Unit tests.
  - [ ] 10.1 `test_must_connect_enforced.py` — `must_connect=False`
        raises before any variable read.
  - [ ] 10.2 `test_bundle_validation.py` — empty, missing PK, multiple
        PKs, malformed entry.
  - [ ] 10.3 `test_gate_ordering.py` — earlier gates short-circuit
        later ones.
  - [ ] 10.4 `test_deployed_mode_rejected.py`.
  - [ ] 10.5 `test_dry_run.py` — gates run, zero writes.
  - [ ] 10.6 `test_verify_write.py` — missing entries raise
        `UefiSecureBootError`.
  - [ ] 10.7 `test_aspects_forwarding.py` — must_connect layered on
        forwarded aspects.
  - References: Requirements 2, 3, 4, 5, 6, 7, 10.

- [ ] 11. Action-pattern targeted tests.
  - [ ] 11.1 `test_action_pattern_single_call.py`.
  - [ ] 11.2 `test_action_pattern_single_connected_call.py`.
  - [ ] 11.3 `test_action_pattern_rejects.py` — three rejected
        patterns raise `SemanticError`.
  - References: Requirement 11.

- [ ] 12. Integration tests against OVMF.
  - [ ] 12.1 Build `conftest.py` fixture generating PK/KEK/db/dbx
        test certificates per-session.
  - [ ] 12.2 `test_ovmf_full_enrollment.py` — asserts
        `setup_mode_after==0` and `secure_boot_after==1`.
  - [ ] 12.3 `test_ovmf_dbx_only.py` — append-write semantics.
  - [ ] 12.4 `test_ovmf_precondition_user_mode.py`.
  - [ ] 12.5 Gate behind `AUTOMOJO_OVMF_TEST=1` and
        `AUTOMOJO_SECURE_BOOT_FIXTURES`.
  - References: Requirement 12.

- [ ] 13. Report-shape tests.
  - [ ] 13.1 `test_report_as_dict.py` — JSON-safety; payload fields
        are SHA-256 hex digests, not bytes.
  - [ ] 13.2 Verify report is attached to exception `details` on
        failure.
  - References: Requirement 9.

- [ ] 14. Coverage gate and security audit.
  - [ ] 14.1 Configure pytest-cov to enforce 95%+ line coverage for
        `automojo.protocols.uefi.workflows.secureboot`.
  - [ ] 14.2 Add a log-audit test asserting no raw key material
        appears in captured log output.
  - [ ] 14.3 Add a CI step failing on coverage drop.
  - References: Testing Strategy, Security Considerations.
