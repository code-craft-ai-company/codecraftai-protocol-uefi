# Tasks — Measured Boot

- [ ] 1. Create package skeleton at
      `source/packages/automojo/protocols/uefi/measured/`.
  - [ ] 1.1 Add `__init__.py` exporting `MeasuredBoot`, `PcrEvent`,
        `ValidationReport`, `PcrMismatch`, `EventType`, `TpmAlgId`,
        `UefiVariableEventData`, `UefiImageLoadEventData`,
        `GptEventData`, `FirmwareBlob2EventData`.
  - [ ] 1.2 Add a module docstring summarizing the read-only contract and
        the UEFI §38 reference.
  - References: Requirement 12.1, Requirement 12.2.

- [ ] 2. Implement `eventtypes.py`.
  - [ ] 2.1 Author `EventType(IntEnum)` with every event type enumerated in
        UEFI §38.4.1 and the TCG PC Client profile.
  - [ ] 2.2 Author `TpmAlgId(IntEnum)` with SHA1, SHA256, SHA384, SHA512,
        and SM3_256 members.
  - [ ] 2.3 Author a `DIGEST_SIZE_BY_ALG` map keyed by `TpmAlgId`.
  - References: Requirement 1.3, Requirement 10.3.

- [ ] 3. Implement `pcrevent.py`.
  - [ ] 3.1 Author `PcrEvent` frozen dataclass with the fields defined in
        the design.
  - [ ] 3.2 Author the four well-known event-data dataclasses
        (`UefiVariableEventData`, `UefiImageLoadEventData`, `GptEventData`,
        `FirmwareBlob2EventData`).
  - [ ] 3.3 Add `__repr__` overrides that truncate long `bytes` fields for
        readability.
  - References: Requirement 1.1, Requirement 1.6, Requirement 9.

- [ ] 4. Implement `eventlog.py`.
  - [ ] 4.1 Author `parse_event_log(buf) -> Iterator[PcrEvent]` streaming
        parser.
  - [ ] 4.2 Implement the first-event decode as `TCG_PCR_EVENT` plus
        `EFI_SPEC_ID_EVENT_STRUCT` extraction.
  - [ ] 4.3 Validate the spec-ID signature `"Spec ID Event03"` and the
        declared version; raise `UefiError` with actionable details on
        mismatch.
  - [ ] 4.4 Implement `TCG_PCR_EVENT2` decoding per the design's layout
        table, using layer-1 integer packers.
  - [ ] 4.5 Dispatch well-known event types through a
        `_EVENT_TYPE_DECODERS` registry.
  - [ ] 4.6 Raise `UefiError` on truncation with byte offset + field name
        in `details`.
  - References: Requirement 1, Requirement 9, Requirement 10.

- [ ] 5. Implement `pcrreader.py`.
  - [ ] 5.1 Author `read_pcrs(agent, banks, aspects)` dispatching through
        the action-pattern engine.
  - [ ] 5.2 Shell out to `tpm2_pcrread <alg>` once per bank.
  - [ ] 5.3 Parse both `tpm2-tools` v4 and v5 stdout formats using two
        compiled regexes.
  - [ ] 5.4 Detect "command not found" and raise `UefiError` with
        remediation hints.
  - [ ] 5.5 Probe `tpm2_getcap pcrs` once on first call per agent to
        cache implemented banks; raise `UefiError` when a requested bank
        is not implemented.
  - References: Requirement 2.

- [ ] 6. Implement `validator.py`.
  - [ ] 6.1 Author `_initialize_accumulators(pcrs)` seeding a zero-filled
        accumulator per (pcr_index, algorithm) pair present in live PCRs.
  - [ ] 6.2 Author `_extend_accumulators(accumulators, event, strict)`
        implementing the TCG Extend operation
        `PCR_new = H(PCR_old || event_digest)`.
  - [ ] 6.3 Author `_compare(accumulators, pcrs)` returning a list of
        `PcrMismatch` records.
  - [ ] 6.4 Author `validate_log_against_pcrs(events, pcrs, strict_algorithms)`
        that raises `UefiError` with a per-PCR diff table on mismatch or
        returns `ValidationReport(success=True, ...)` on match.
  - [ ] 6.5 Implement `ValidationReport` and `PcrMismatch` dataclasses.
  - References: Requirement 3.

- [ ] 7. Implement `measuredboot.py` facade.
  - [ ] 7.1 Author the `MeasuredBoot` class per the design.
  - [ ] 7.2 Implement `load_event_log_from_host(path, aspects)` calling
        `agent.file_pull_bytes`.
  - [ ] 7.3 Route `read_pcrs` through `resolve_aspects` and the
        action-pattern engine.
  - [ ] 7.4 Raise `SemanticError` on requests for `DO_WHILE_SUCCESS` or
        `DO_UNTIL_CONNECTION_FAILURE`.
  - References: Requirement 2, Requirement 4, Requirement 8, Requirement 12.

- [ ] 8. Collect a fixtures corpus under `tests/fixtures/measured/`.
  - [ ] 8.1 Capture `binary_bios_measurements` + `tpm2_pcrread` pairs from
        OVMF+swtpm (SHA-256 only).
  - [ ] 8.2 Capture a bare-metal AMI system pair (SHA-1 + SHA-256 + SHA-384).
  - [ ] 8.3 Capture a Phoenix/Insyde system pair with SM3_256 present.
  - [ ] 8.4 Document provenance and platform for each capture in a
        `FIXTURES.md` alongside the blobs.
  - References: Testing Strategy.

- [ ] 9. Author parser unit tests (`test_eventlog.py`).
  - [ ] 9.1 Spec-ID signature accept / reject.
  - [ ] 9.2 Multi-algorithm digest decode.
  - [ ] 9.3 Truncation sweep: cut one byte shorter each iteration, assert
        `UefiError` with correct offset in `details`.
  - [ ] 9.4 Well-known event-type decoders emit the expected dataclass.
  - [ ] 9.5 Unknown event types pass through as raw bytes.
  - References: Requirement 1, Requirement 9, Requirement 10.

- [ ] 10. Author validator unit tests (`test_validator.py`).
  - [ ] 10.1 Successful replay over the captured fixtures corpus.
  - [ ] 10.2 Mismatch injection: mutate one event's digest, assert
        `UefiError` carries a mismatch record.
  - [ ] 10.3 `strict_algorithms=True` flags missing banks.
  - [ ] 10.4 `strict_algorithms=False` records skipped banks in the report.
  - References: Requirement 3.

- [ ] 11. Author PCR-reader unit tests (`test_pcrreader.py`).
  - [ ] 11.1 v4 and v5 output-format parsing.
  - [ ] 11.2 Missing binary raises `UefiError` with remediation hint.
  - [ ] 11.3 Non-zero status not in `allowed_error_codes` raises
        `UefiShellError`.
  - [ ] 11.4 Requested-but-unimplemented bank raises `UefiError` with the
        implemented banks listed.
  - References: Requirement 2.

- [ ] 12. Author action-pattern tests (`test_actionpatterns.py`).
  - [ ] 12.1 `SINGLE_CALL` — one invocation, fail fast.
  - [ ] 12.2 `SINGLE_CONNECTED_CALL` — retry on connection class, fail on
        app error.
  - [ ] 12.3 `DO_UNTIL_SUCCESS` — poll and eventually succeed within
        timeout; assert `TimeoutError` on elapse.
  - [ ] 12.4 `DO_WHILE_SUCCESS` and `DO_UNTIL_CONNECTION_FAILURE` raise
        `SemanticError`.
  - [ ] 12.5 `aspects=None` routes through `resolve_aspects` to the
        instance default.
  - References: Requirement 5, Requirement 6, Requirement 7, Requirement 8.

- [ ] 13. Authoring an integration test.
  - [ ] 13.1 Stand up an OVMF+swtpm VM, boot it, pull the event log,
        read PCRs, validate.
  - [ ] 13.2 Gate the test on `AUTOMOJO_UEFI_OVMF_VM` env var.
  - [ ] 13.3 Emit a failure report that includes the diff table on
        validation failure.
  - References: Requirement 3.

- [ ] 14. Documentation.
  - [ ] 14.1 Author a usage guide at `userguide/25-01-measured-boot.rst`
        covering the parse-read-validate flow and explaining the
        read-only contract.
  - [ ] 14.2 Cross-link from `AGENTS.md` Features section.
  - References: Introduction, Requirement 12.
