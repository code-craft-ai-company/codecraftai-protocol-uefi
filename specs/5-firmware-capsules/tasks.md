# Tasks — Firmware Capsules

- [ ] 1. Create package skeleton at
      `source/packages/ccai/protocols/uefi/capsules/`.
  - [ ] 1.1 Add `__init__.py` re-exporting `CapsuleBuilder`,
        `BuiltCapsule`, `FmpCapsulePayload`, `CapsuleDelivery`,
        `CapsuleReport`, `CapsuleReportRecord`, and every capsule flag
        constant.
  - [ ] 1.2 Add `backends/__init__.py` for the three delivery backends.
  - References: Requirement 13.

- [ ] 2. Implement `flags.py`.
  - [ ] 2.1 Define `CAPSULE_FLAGS_PERSIST_ACROSS_RESET`,
        `CAPSULE_FLAGS_POPULATE_SYSTEM_TABLE`, `CAPSULE_FLAGS_INITIATE_RESET`.
  - [ ] 2.2 Add a combined `capsule_flags_from(...)` helper that enforces
        §8.5.3's rule that `POPULATE_SYSTEM_TABLE` implies
        `PERSIST_ACROSS_RESET`.
  - [ ] 2.3 Add `test_flags.py`.
  - References: Requirement 1.3, Requirement 1.4, Requirement 1.5.

- [ ] 3. Implement `fmp.py`.
  - [ ] 3.1 Author `FmpCapsulePayload` (frozen dataclass) per the design.
  - [ ] 3.2 Author `encode_fmp_header(payload)` that emits the §23.3
        header and offset array.
  - [ ] 3.3 Author `encode_fmp_auth_descriptor(tbs, sig, timestamp)` that
        wraps the signer's output in `EFI_FIRMWARE_IMAGE_AUTHENTICATION`.
  - [ ] 3.4 Add `test_fmp.py` covering offset arrays, override path, size
        invariants.
  - References: Requirement 2, Requirement 8.4.

- [ ] 4. Implement `builder.py`.
  - [ ] 4.1 Author `BuiltCapsule` dataclass.
  - [ ] 4.2 Author `CapsuleBuilder.__init__` per the design signature.
  - [ ] 4.3 Implement `build()` emitting `EFI_CAPSULE_HEADER` + optional
        FMP + payload + optional signature.
  - [ ] 4.4 Compute SHA-256 of the output and attach to `BuiltCapsule`.
  - [ ] 4.5 Enforce `CapsuleImageSize <= 0xFFFFFFFF`.
  - [ ] 4.6 Call signer with exact TBS bytes, validate return type/length.
  - [ ] 4.7 Add `test_builder_header.py`, `test_builder_fmp.py`,
        `test_builder_signer.py`, `test_builder_size_limits.py`.
  - [ ] 4.8 Add `test_builder_no_key_material.py` — pytest hook asserts
        no filesystem key read during builder runs.
  - References: Requirements 1, 2, 8, 13, 14.

- [ ] 5. Implement `backends/os_runtime.py`.
  - [ ] 5.1 Probe `/dev/efi_capsule_loader` existence via the active
        SSH-to-host transport.
  - [ ] 5.2 Write capsule bytes to the device with a chunked writer.
  - [ ] 5.3 On device absence, fall back to `fwupdmgr install
        --allow-reinstall` invocation.
  - [ ] 5.4 Translate `EINVAL` into `UefiCapsuleError` with hex-dump of
        header bytes.
  - [ ] 5.5 Add `test_os_runtime_backend.py`.
  - References: Requirement 3.

- [ ] 6. Implement `backends/mass_storage.py`.
  - [ ] 6.1 Accept an `EspFileProvider` via composition.
  - [ ] 6.2 Create `/EFI/UpdateCapsule/` if missing.
  - [ ] 6.3 Push capsule with default `auto-<guid>-<sha16>.cap` naming.
  - [ ] 6.4 Toggle `EFI_OS_INDICATIONS_FILE_CAPSULE_DELIVERY_SUPPORTED`
        via the `VariableStore`.
  - [ ] 6.5 Add `test_mass_storage_backend.py`.
  - References: Requirement 4.

- [ ] 7. Implement `backends/redfish.py`.
  - [ ] 7.1 Detect multipart endpoint via `UpdateService` capabilities.
  - [ ] 7.2 POST multipart body.
  - [ ] 7.3 Fall back to `SimpleUpdate` with caller-supplied URL.
  - [ ] 7.4 Poll `TaskMonitor` per aspects timing.
  - [ ] 7.5 Translate terminal failure `TaskState` into `UefiCapsuleError`.
  - [ ] 7.6 Add `test_redfish_backend.py` using `responses`-library.
  - References: Requirement 5.

- [ ] 8. Implement `delivery.py` facade.
  - [ ] 8.1 Author `CapsuleDelivery.__init__` with composition parameters.
  - [ ] 8.2 Implement `_run_with_pattern` dispatcher for `SINGLE_CALL`,
        `SINGLE_CONNECTED_CALL`, `DO_UNTIL_SUCCESS`.
  - [ ] 8.3 Implement `deliver_via_os`, `deliver_via_mass_storage`,
        `deliver_via_redfish`.
  - [ ] 8.4 Emit structured logs at `uefi.capsule.*` per
        `logging_pattern`.
  - [ ] 8.5 Reject unsupported patterns with `SemanticError`.
  - References: Requirements 9, 10, 11, 12.

- [ ] 9. Implement `report.py`.
  - [ ] 9.1 Author `CapsuleReportRecord` with `is_success` property.
  - [ ] 9.2 Implement `CapsuleReport.collect(aspects)` enumerating
        `EFI_CAPSULE_REPORT_GUID` variables.
  - [ ] 9.3 Implement `CapsuleReport.latest_for(capsule_guid, aspects)`.
  - [ ] 9.4 Support `DO_UNTIL_SUCCESS` polling for late-arriving reports.
  - [ ] 9.5 Add `test_report_collect.py`, `test_report_latest_for.py`,
        `test_report_do_until_success.py`.
  - References: Requirements 6, 7, 11.

- [ ] 10. Author action-pattern tests for delivery.
  - [ ] 10.1 `test_delivery_single_call.py`.
  - [ ] 10.2 `test_delivery_single_connected_call.py`.
  - [ ] 10.3 `test_delivery_do_until_success.py`.
  - [ ] 10.4 `test_delivery_unsupported_patterns.py`.
  - References: Requirements 9, 10, 11.

- [ ] 11. Author aspects-none passthrough tests.
  - [ ] 11.1 `test_builder_synchronous.py` — builder does not take aspects.
  - [ ] 11.2 `test_delivery_aspects_none.py`.
  - [ ] 11.3 `test_report_aspects_none.py`.
  - References: Requirement 12.

- [ ] 12. Author security-boundary tests.
  - [ ] 12.1 Static scan: ensure no `open()` in the package reads under
        `**/keys/**`, `**/.ssh/**`, `**/pki/**`.
  - [ ] 12.2 Ensure no subprocess invocation of `openssl`, `sbsign`,
        `gpg`, `pkcs11-tool`.
  - [ ] 12.3 Assert capsule payload bytes do not appear in emitted log
        lines at any level.
  - [ ] 12.4 Assert signer callable receives exactly the TBS bytes the
        builder computed.
  - References: Requirement 8, Security Considerations.

- [ ] 13. Integration tests.
  - [ ] 13.1 EDK2 known-good capsule vector — build and byte-compare.
  - [ ] 13.2 End-to-end Redfish multipart upload test against fake BMC.
  - [ ] 13.3 Mass-storage round-trip against a tmpfs-backed fake ESP.
  - References: Requirement 1, Requirement 4, Requirement 5.

- [ ] 14. Documentation and coverage.
  - [ ] 14.1 Usage guide at `userguide/50-03-uefi-firmware-capsules.rst`
        covering builder, delivery modes, report polling, and the signer
        boundary.
  - [ ] 14.2 pytest-cov gate at 95% line / 90% branch for the package.
  - [ ] 14.3 CI job publishes the security-boundary scan output as an
        artifact.
  - References: Security Considerations, Testing Strategy.
