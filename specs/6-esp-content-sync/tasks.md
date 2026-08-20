# Tasks — ESP Content Sync Workflow

- [ ] 1. Create package skeleton at
      `source/packages/ccai/protocols/uefi/workflows/espsync/`.
  - [ ] 1.1 Add `__init__.py` re-exporting `EspContentSyncWorkflow`,
        `EspSyncReport`, `FileAction`, `FileActionRecord`, and the
        `esp_content_sync` convenience function.
  - [ ] 1.2 Add a module-level docstring summarizing composition and
        the ordering guarantees.
  - References: Requirement 1, Requirement 9.

- [ ] 2. Implement `FileAction` enum and `FileActionRecord`.
  - [ ] 2.1 Author `espsyncreport.py`.
  - [ ] 2.2 Declare the `FileAction(IntEnum)` with the six members
        named in the design.
  - [ ] 2.3 Declare the `FileActionRecord` frozen dataclass.
  - References: Requirement 2, Requirement 3, Requirement 6.

- [ ] 3. Implement `EspSyncReport`.
  - [ ] 3.1 Author the frozen dataclass with fields named in the design.
  - [ ] 3.2 Implement `as_dict()` producing JSON-safe output.
  - [ ] 3.3 Sphinx-style docstrings per coding standards.
  - References: Requirement 6.

- [ ] 4. Implement `globfilter`.
  - [ ] 4.1 Author `globfilter.py` with `compile()` and `GlobFilter`.
  - [ ] 4.2 Use `fnmatch.translate` for glob → regex.
  - [ ] 4.3 Implement "exclude wins over include" semantics.
  - [ ] 4.4 Raise `ConfigurationError` on malformed patterns.
  - [ ] 4.5 Unit tests `test_globfilter.py` — include-only, exclude-only,
        overlap, malformed.
  - References: Requirement 4.

- [ ] 5. Implement `pathnormalize`.
  - [ ] 5.1 Author `pathnormalize.py` with `to_transport()` for Shell,
        SSH-host, Redfish VirtualMedia.
  - [ ] 5.2 Implement POSIX ↔ Shell (`FS0:\...`) round-trip.
  - [ ] 5.3 Unit tests `test_pathnormalize.py`.
  - References: Requirement 5.

- [ ] 6. Implement `diffengine`.
  - [ ] 6.1 Author `diffengine.py` with `diff()` returning `DiffPlan`.
  - [ ] 6.2 Implement per-remote-file SHA-256 via `5-esp-file-ops.file_pull`
        chunked reads into `hashlib`.
  - [ ] 6.3 Short-circuit to size-mismatch when sizes differ (avoid pull).
  - [ ] 6.4 Unit tests covering every (local/remote × checksum ×
        delete-flag) combination.
  - References: Requirement 2, Requirement 3.

- [ ] 7. Implement `EspContentSyncWorkflow.run`.
  - [ ] 7.1 Author `espcontentsync.py` with constructor and `run()`.
  - [ ] 7.2 Implement aspects resolution and `SemanticError` for
        unsupported `ActionPattern`.
  - [ ] 7.3 Validate `local_root` exists and is a directory.
  - [ ] 7.4 Normalize `remote_root` and compile glob filters.
  - [ ] 7.5 Walk local tree and build `LocalFile` records (SHA-256 +
        size), filtered by globs.
  - [ ] 7.6 Walk remote tree via `5-esp-file-ops.directory_tree`.
  - [ ] 7.7 Call `diffengine.diff`.
  - [ ] 7.8 Execute plan in the documented order (dirs → push → delete
        → dir-remove).
  - [ ] 7.9 Emit action records and aggregate totals into report.
  - References: Requirements 1, 2, 3, 5, 7, 8, 9.

- [ ] 8. Implement `esp_content_sync` convenience function.
  - [ ] 8.1 Author the module-level wrapper.
  - [ ] 8.2 Sphinx-style docstring per coding standards.
  - References: Requirement 1.

- [ ] 9. Wire logging channel `uefi.workflow.espsync`.
  - [ ] 9.1 Register the channel through `1-logging`.
  - [ ] 9.2 Emit INFO per-file action under `LoggingPattern.ALL_RESULTS`.
  - [ ] 9.3 Emit single summary line for no-op runs under
        `LoggingPattern.SUCCESS_ONLY`.
  - [ ] 9.4 Emit DEBUG lines with computed digests.
  - References: Requirement 10, Logging section.

- [ ] 10. Action-pattern targeted tests.
  - [ ] 10.1 `test_action_pattern_single_call.py` — per-file attempt
        once.
  - [ ] 10.2 `test_action_pattern_single_connected_call.py` — one
        channel reused for the whole workflow.
  - [ ] 10.3 `test_action_pattern_do_until_success.py` — transient
        per-file failure retried.
  - [ ] 10.4 `test_action_pattern_rejects.py` — `DO_WHILE_SUCCESS` and
        `DO_UNTIL_CONNECTION_FAILURE` raise `SemanticError`.
  - References: Requirement 8.

- [ ] 11. Unit tests for workflow behaviors.
  - [ ] 11.1 `test_noop_rerun.py` — second run is all-skipped.
  - [ ] 11.2 `test_include_exclude.py` — overlap: exclude wins.
  - [ ] 11.3 `test_delete_mode.py` — orphans removed, empty dirs
        removed.
  - [ ] 11.4 `test_ordering.py` — push-before-delete, bottom-up dir
        removal.
  - [ ] 11.5 `test_aspects_forwarding.py`.
  - References: Requirements 2, 3, 4, 9, 10.

- [ ] 12. Integration tests.
  - [ ] 12.1 `test_shell_transport_sync.py` — against a fake/simulated
        Shell ESP.
  - [ ] 12.2 `test_ssh_host_sync.py` — against a real SSH-to-host target
        (gated by env var).
  - [ ] 12.3 `test_ovmf_sync.py` — against OVMF VM.
  - [ ] 12.4 Gate OVMF test behind `CCAI_OVMF_TEST=1`.
  - References: Requirement 11.

- [ ] 13. Coverage gate.
  - [ ] 13.1 Configure pytest-cov to enforce 95%+ line coverage for
        `ccai.protocols.uefi.workflows.espsync`.
  - [ ] 13.2 CI step failing on drop.
  - References: Testing Strategy.

- [ ] 14. Documentation.
  - [ ] 14.1 Author `userguide/60-04-esp-content-sync.rst` with a
        minimal usage example, include/exclude example, and
        delete-mode example with safety notes.
  - [ ] 14.2 Cross-link from `AGENTS.md` Features section.
  - References: Introduction.
