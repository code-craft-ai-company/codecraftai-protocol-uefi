# Tasks — Secure Boot

- [ ] 1. Create package skeleton at
      `source/packages/ccai/protocols/uefi/secureboot/`.
  - [ ] 1.1 Add `__init__.py` re-exporting `SecureBootInspector`,
        `SecureBootEnroller`, `SecureBootState`, `Signature`,
        `SignatureType`, `SignatureListCodec`, `AuditLogger`.
  - [ ] 1.2 Add a module-level docstring summarizing the key-handling
        boundary.
  - References: Requirement 1, Requirement 2, Requirement 6.

- [ ] 2. Implement `state.py`.
  - [ ] 2.1 Author `SecureBootState` with `is_setup_mode`,
        `is_deployed`, `is_enforcing` properties.
  - [ ] 2.2 Add `test_state.py`.
  - References: Requirement 1.

- [ ] 3. Implement `signatures.py`.
  - [ ] 3.1 Author `SignatureType` enum with named members from §32.4.1.
  - [ ] 3.2 Author `Signature` frozen dataclass.
  - [ ] 3.3 Author `SignatureListCodec.decode(buf)` with bounds checks.
  - [ ] 3.4 Author `SignatureListCodec.encode(signatures)` emitting one
        ESL per signature type.
  - [ ] 3.5 Add `test_signature_list_roundtrip.py` using `hypothesis`.
  - [ ] 3.6 Add `test_signature_list_malformed.py`.
  - References: Requirement 2, Requirement 13, Requirement 14.

- [ ] 4. Implement `authdesc.py`.
  - [ ] 4.1 Author `efi_time_from_datetime(dt)` and
        `efi_time_to_datetime(buf)` helpers that enforce Pad1/Pad2/
        Nanosecond == 0.
  - [ ] 4.2 Author `build_tbs(variable_name, vendor_guid, attributes,
        timestamp, new_data)`.
  - [ ] 4.3 Author `wrap_win_certificate_uefi_guid(pkcs7: bytes)`
        building the `WIN_CERTIFICATE_UEFI_GUID` structure.
  - [ ] 4.4 Author `build_auth2_descriptor(...)` composing EFI_TIME +
        WIN_CERTIFICATE.
  - [ ] 4.5 Validate signer-returned bytes (non-empty, bytes type).
  - [ ] 4.6 Add `test_authdesc_structure.py` verifying field offsets
        against §8.2.5.
  - References: Requirement 3, Requirement 6.

- [ ] 5. Implement `monotonicity.py`.
  - [ ] 5.1 Author `TimeStampMonotonicityTracker` with
        `check_and_record(name, guid, ts)`.
  - [ ] 5.2 Raise `UefiSecureBootError` on non-monotone inputs.
  - [ ] 5.3 Expose `seed(mapping)` for process-boundary seeding.
  - [ ] 5.4 Add `test_monotonicity.py`.
  - References: Requirement 7.

- [ ] 6. Implement `audit.py`.
  - [ ] 6.1 Author `AuditLogger` bound to `uefi.secureboot.audit`.
  - [ ] 6.2 Implement `record_force_enroll(database, payload_sha256,
        operator, ticket)`.
  - [ ] 6.3 Add `test_audit_logger.py` asserting no payload bytes, no
        signature bytes appear in the emitted record.
  - References: Requirement 5.4, Requirement 12.

- [ ] 7. Implement `inspector.py`.
  - [ ] 7.1 Author `SecureBootInspector.__init__`.
  - [ ] 7.2 Implement `state(aspects)` reading the four variables.
  - [ ] 7.3 Implement `enumerate(database, aspects)` dispatching to the
        correct vendor GUID and decoding via `SignatureListCodec`.
  - [ ] 7.4 Implement `_run_with_pattern` for `SINGLE_CALL`,
        `SINGLE_CONNECTED_CALL`, `DO_UNTIL_SUCCESS`.
  - [ ] 7.5 Add `test_state.py`, `test_enumerate.py`,
        `test_enumerate_malformed.py`, and
        `test_inspect_action_patterns.py`.
  - References: Requirements 1, 2, 8, 9, 10.1.

- [ ] 8. Implement `enroller.py`.
  - [ ] 8.1 Author `SecureBootEnroller.__init__` with composition of
        `VariableStore`, `SecureBootInspector`, monotonicity tracker,
        and audit logger.
  - [ ] 8.2 Implement database-name validation.
  - [ ] 8.3 Implement SetupMode guard with `force=True` +
        acknowledgement flow; raise `ConfigurationError` on invalid ack.
  - [ ] 8.4 Call `build_auth2_descriptor(...)` using caller's signer.
  - [ ] 8.5 Set attributes including
        `TIME_BASED_AUTHENTICATED_WRITE_ACCESS`; for `dbx`, OR in
        `APPEND_WRITE`.
  - [ ] 8.6 Invoke `VariableStore.set_variable(...)` with the assembled
        payload.
  - [ ] 8.7 Translate `EFI_SECURITY_VIOLATION` into
        `UefiSecureBootError`.
  - [ ] 8.8 Reject `DO_UNTIL_SUCCESS` / `DO_WHILE_SUCCESS` with
        `SemanticError`.
  - [ ] 8.9 Implement `enroll_pre_signed(descriptor, ...)` with
        structural validation only.
  - References: Requirements 3, 4, 5, 6, 7, 8, 9, 10.2.

- [ ] 9. Author enrollment tests.
  - [ ] 9.1 `test_enroll_setup_mode.py` — happy path inside SetupMode.
  - [ ] 9.2 `test_enroll_deployed_mode_refuses.py` — refuses without
        `force=True`.
  - [ ] 9.3 `test_enroll_force_ack.py` — `force=True` with and without
        valid acknowledgement.
  - [ ] 9.4 `test_enroll_monotonicity.py` — replay rejected.
  - [ ] 9.5 `test_enroll_pre_signed.py` — pre-signed descriptors
        accepted and structurally validated.
  - [ ] 9.6 `test_dbx_append_only.py` — `APPEND_WRITE` default;
        `replace=True` gated.
  - [ ] 9.7 `test_enroll_signer_bad_return.py`.
  - [ ] 9.8 `test_enroll_action_patterns.py` — `DO_UNTIL_SUCCESS`
        rejected with `SemanticError`.
  - References: Requirements 3, 4, 5, 6, 7.

- [ ] 10. Author aspects-none passthrough tests.
  - [ ] 10.1 `test_inspect_aspects_none.py`.
  - [ ] 10.2 `test_enroll_aspects_none.py`.
  - References: Requirement 11.

- [ ] 11. Author security-boundary tests.
  - [ ] 11.1 `test_no_key_material_read.py` — filesystem-access audit
        fixture.
  - [ ] 11.2 `test_no_openssl_subprocess.py` — subprocess audit fixture.
  - [ ] 11.3 `test_logs_exclude_signer_io.py` — log-capture fixture
        scanned for signer I/O bytes.
  - [ ] 11.4 `test_ack_required_for_force.py`.
  - References: Requirement 6, Requirement 12, Security Considerations.

- [ ] 12. Author fixtures.
  - [ ] 12.1 Generate ephemeral test PK / KEK / db / dbx under
        `tests/fixtures/secureboot/` via a `conftest.py` fixture using
        `cryptography.hazmat` — keys live in memory only.
  - [ ] 12.2 Capture representative ESL blobs for decode tests.
  - References: Testing Strategy.

- [ ] 13. Integration tests.
  - [ ] 13.1 OVMF-backed test running the full inspect → enroll → verify
        loop against test certificates.
  - References: Requirement 1, Requirement 3.

- [ ] 14. Documentation and coverage.
  - [ ] 14.1 Usage guide at `userguide/50-04-uefi-secure-boot.rst`
        covering inspection, enrollment, and the signer boundary.
  - [ ] 14.2 Prominent section in the guide on the `force=True` +
        acknowledgement contract.
  - [ ] 14.3 pytest-cov gate at 95% line / 90% branch.
  - References: Security Considerations, Testing Strategy.
