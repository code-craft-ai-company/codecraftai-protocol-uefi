# Tasks — Reboot and Verify Workflow

- [ ] 1. Create package skeleton at
      `source/packages/automojo/protocols/uefi/workflows/rebootverify/`.
  - [ ] 1.1 Add `__init__.py` re-exporting `RebootAndVerifyWorkflow`,
        `RebootAndVerifyReport`, and the `reboot_and_verify` convenience
        function.
  - [ ] 1.2 Add a module-level docstring summarizing composition order.
  - References: Requirement 1.

- [ ] 2. Implement `RebootAndVerifyReport` dataclass.
  - [ ] 2.1 Author `rebootverifyreport.py` with the frozen dataclass fields
        named in the design.
  - [ ] 2.2 Implement `as_dict()` with base64 encoding of `console_capture`.
  - [ ] 2.3 Add Sphinx-style docstrings per repository coding standards.
  - References: Requirement 8.1, Requirement 8.3.

- [ ] 3. Implement `ConsoleCaptureBuffer`.
  - [ ] 3.1 Author `captureconsole.py` with the context-manager interface.
  - [ ] 3.2 Implement the raw-read tap for serial and IPMI-SOL transports.
  - [ ] 3.3 Implement the no-op path for Redfish.
  - [ ] 3.4 Enforce `max_bytes` truncation from the start of the buffer.
  - References: Requirement 3.

- [ ] 4. Implement `SessionStateSnapshot`.
  - [ ] 4.1 Author `stateforward.py` with the frozen dataclass.
  - [ ] 4.2 Implement `capture(agent)` classmethod reading map alias, cwd,
        and environment from the agent's current session.
  - [ ] 4.3 Implement `replay(agent, aspects)` with per-element best-effort
        semantics and `UefiShellError` on failure naming the failed element.
  - References: Requirement 4.

- [ ] 5. Implement `RebootAndVerifyWorkflow.run`.
  - [ ] 5.1 Author `rebootverifyworkflow.py` with the constructor and
        `run()` method.
  - [ ] 5.2 Implement aspects resolution via `resolve_aspects`.
  - [ ] 5.3 Implement the execution order from the design.
  - [ ] 5.4 Raise `SemanticError` on unsupported `ActionPattern`.
  - [ ] 5.5 Implement `DO_UNTIL_SUCCESS` retry loop bounded by
        `completion_timeout`.
  - References: Requirements 1, 2, 5, 6, 7.

- [ ] 6. Implement `reboot_and_verify` convenience function.
  - [ ] 6.1 Author the module-level function that wraps
        `RebootAndVerifyWorkflow.run`.
  - [ ] 6.2 Add a `:param:` / `:returns:` docstring per coding standards.
  - References: Requirement 1.

- [ ] 7. Wire logging channel `uefi.workflow.reboot`.
  - [ ] 7.1 Register the channel through `1-logging`.
  - [ ] 7.2 Emit INFO / DEBUG / ERROR lines per the design.
  - References: Logging section of design.

- [ ] 8. Unit tests.
  - [ ] 8.1 `test_workflow_aspects_delegation.py` — mock
        `5-system-reset.reset` and assert forwarded aspects match input.
  - [ ] 8.2 `test_workflow_semantic_errors.py` — each unsupported
        `ActionPattern` raises `SemanticError`.
  - [ ] 8.3 `test_workflow_console_capture.py` — buffer truncation and
        Redfish no-op path.
  - [ ] 8.4 `test_workflow_state_replay.py` — success and partial-failure.
  - [ ] 8.5 `test_workflow_wait_for_shell_false.py` — ICMP path and
        unsupported-transport `ConfigurationError`.
  - References: Requirements 2, 3, 4, 5, 6.

- [ ] 9. Action-pattern targeted tests.
  - [ ] 9.1 `test_workflow_action_pattern_single_call.py`.
  - [ ] 9.2 `test_workflow_action_pattern_do_until_success.py` — simulate
        transient failure then success.
  - [ ] 9.3 `test_workflow_action_pattern_rejects.py` — three rejected
        patterns each raise `SemanticError`.
  - References: Requirement 6.

- [ ] 10. Integration tests against QEMU/OVMF.
  - [ ] 10.1 Author a QEMU/OVMF fixture that spins up a VM with a serial
        console pinned to a PTY.
  - [ ] 10.2 `test_ovmf_cold_reset_wait_for_shell.py` — asserts
        `shell_verified` and `state_replayed` are `True`.
  - [ ] 10.3 `test_ovmf_warm_reset.py`.
  - [ ] 10.4 `test_ovmf_shutdown.py`.
  - [ ] 10.5 Gate integration tests behind `AUTOMOJO_OVMF_TEST=1`.
  - References: Requirement 9.

- [ ] 11. Report-shape tests.
  - [ ] 11.1 `test_report_as_dict.py` — verify JSON-safety after base64
        encoding.
  - [ ] 11.2 Verify report is attached to exception `details` on failure.
  - References: Requirement 8.

- [ ] 12. Coverage gate.
  - [ ] 12.1 Configure pytest-cov to enforce 95%+ line coverage for
        `automojo.protocols.uefi.workflows.rebootverify`.
  - [ ] 12.2 Add a CI step failing on drop.
  - References: Testing Strategy.

- [ ] 13. Documentation.
  - [ ] 13.1 Author `userguide/60-01-reboot-and-verify.rst` showing a
        minimal usage example and the fast `wait_for_shell=False` path.
  - [ ] 13.2 Cross-link from `AGENTS.md` Features section.
  - References: Introduction.
