# Tasks — System Reset

- [ ] 1. Create package skeleton at
      `source/packages/ccai/protocols/uefi/reset/`.
  - [ ] 1.1 Add `__init__.py` exporting `SystemReset`, `ResetKind`,
        `ResetFailedError`.
  - [ ] 1.2 Module docstring summarizing UEFI §8.5.1 scope and the
        `SshAgent.reboot()` heritage.
  - References: Introduction.

- [ ] 2. Implement `resetkind.py`.
  - [ ] 2.1 Author `ResetKind(IntEnum)` with `COLD`, `WARM`, `SHUTDOWN`,
        `PLATFORM_SPECIFIC`.
  - [ ] 2.2 Add `test_resetkind.py` verifying member count and
        non-`ResetKind` rejection.
  - References: Requirement 1.

- [ ] 3. Implement `errors.py`.
  - [ ] 3.1 Author `ResetFailedError(UefiError)` with `host`, `kind`,
        `elapsed_seconds`, `attempt_count`, `last_error`, `transport`
        attributes.
  - [ ] 3.2 Override `__str__` per the multiline-error-message coding
        standard.
  - [ ] 3.3 Add `as_dict()` method for structured logging consumers.
  - References: Requirement 14.

- [ ] 4. Implement `verifyloop.py` — the `SshAgent.reboot` port.
  - [ ] 4.1 Author `_reconnect_verify_loop(agent, kind, aspects)`
        implementing the exact algorithm in the design's "Reconnect-verify
        loop contract" section.
  - [ ] 4.2 Author `_wait_until_unreachable(agent, aspects)` for the
        shutdown inverse-polarity case.
  - [ ] 4.3 Author `_is_allowed_connection_exception(ex, aspects)`
        helper.
  - [ ] 4.4 Emit structured INFO/WARNING/ERROR events on `uefi.reset`.
  - References: Requirement 4, Requirement 6, Requirement 13, Requirement 14.

- [ ] 5. Implement `resetshell.py`.
  - [ ] 5.1 Map `ResetKind` → Shell `reset` flags per the design table.
  - [ ] 5.2 Assemble the hex-encoded `<guid-bytes><reset-data>` argument
        for `PLATFORM_SPECIFIC`.
  - [ ] 5.3 Tolerate a connection-class exception on command submission
        because `reset` severs the shell; gate on
        `aspects.allowed_connection_exceptions`.
  - References: Requirement 2, Requirement 3, Requirement 5.

- [ ] 6. Implement `resetredfish.py`.
  - [ ] 6.1 Map `ResetKind` → `ResetType` literal per the design table.
  - [ ] 6.2 POST to `/Actions/ComputerSystem.Reset` and poll any `202`
        TaskMonitor per `aspects.completion_interval`.
  - [ ] 6.3 Require `redfish_reset_type` for `PLATFORM_SPECIFIC` and
        raise `ConfigurationError` when omitted.
  - [ ] 6.4 Include `reason` as `@Message.ExtendedInfo` when the BMC
        accepts it.
  - References: Requirement 5, Requirement 8.

- [ ] 7. Implement `resetsshhost.py`.
  - [ ] 7.1 Probe for `efi-reset`, then `/sys/firmware/efi/reset_type`,
        then `systemctl reboot`/`poweroff` in that order.
  - [ ] 7.2 Cache capability results on the transport's feature-flag
        dict.
  - [ ] 7.3 Raise `UefiError` with `details` when no mechanism is
        available for the requested kind.
  - References: Requirement 2, Requirement 3, Requirement 4.

- [ ] 8. Implement `systemreset.py` facade.
  - [ ] 8.1 Author the `SystemReset` class.
  - [ ] 8.2 Author `_validate_reset_args(kind, reset_subtype_guid,
        redfish_reset_type, transport_kind)`.
  - [ ] 8.3 Author `_pack_reset_data(reset_subtype_guid, reset_data)`
        yielding the `GUID||data` buffer per UEFI §8.5.1.
  - [ ] 8.4 Author `_issue_reset(...)` dispatching by
        `agent.transport_kind`.
  - [ ] 8.5 Route `aspects=None` through `resolve_aspects`.
  - [ ] 8.6 Apply default `completion_timeout = 300s` when the caller
        and instance both omit one.
  - References: Requirement 7, Requirement 12.

- [ ] 9. Unit tests — `test_verifyloop.py`.
  - [ ] 9.1 Success-on-first-attempt path calls `verify_connectivity`
        exactly once.
  - [ ] 9.2 Success-after-N-attempts path sleeps between attempts.
  - [ ] 9.3 Allowed exception tolerated → loop continues.
  - [ ] 9.4 Non-allowed exception → immediate re-raise.
  - [ ] 9.5 Timeout elapsed → `ResetFailedError` with populated fields.
  - [ ] 9.6 `monitor_delay` is observed via mocked `time.sleep`.
  - [ ] 9.7 `_wait_until_unreachable` — success, failure, allowed
        exception.
  - References: Requirement 6, Requirement 4, Requirement 14.

- [ ] 10. Unit tests per transport.
  - [ ] 10.1 `test_resetshell.py` — command mapping and
        `PLATFORM_SPECIFIC` hex packing.
  - [ ] 10.2 `test_resetredfish.py` — `ResetType` mapping and
        `redfish_reset_type` override.
  - [ ] 10.3 `test_resetsshhost.py` — capability probe order and
        fallbacks.
  - References: Requirement 2, Requirement 3, Requirement 4, Requirement 5.

- [ ] 11. Simulated reset integration test
      (`test_reset_simulated.py`).
  - [ ] 11.1 Fake transport that accepts a reset command, drops the
        connection, and re-accepts after a configurable delay.
  - [ ] 11.2 Case: `delay=5s`, `completion_timeout=60s` → success,
        `attempt_count >= 1`.
  - [ ] 11.3 Case: `delay=120s`, `completion_timeout=60s` →
        `ResetFailedError` with `elapsed_seconds >= 60`.
  - [ ] 11.4 Case: non-allowed exception mid-loop → raises immediately.
  - References: Requirement 6, Requirement 14.

- [ ] 12. Action-pattern tests (`test_actionpatterns.py`).
  - [ ] 12.1 `SINGLE_CALL` — issues the command once.
  - [ ] 12.2 `SINGLE_CONNECTED_CALL` — retries allowed connection
        exceptions at issue time.
  - [ ] 12.3 `DO_UNTIL_SUCCESS` — retries command on rejection until
        accepted; loop budget is independent.
  - [ ] 12.4 `DO_WHILE_SUCCESS` and `DO_UNTIL_CONNECTION_FAILURE` raise
        `SemanticError`.
  - [ ] 12.5 `aspects=None` falls back to instance default.
  - References: Requirement 9, Requirement 10, Requirement 11,
        Requirement 12.

- [ ] 13. OVMF integration test.
  - [ ] 13.1 Cold reset an OVMF VM; verify Shell responsive within
        `aspects.completion_timeout`.
  - [ ] 13.2 Shutdown an OVMF VM; verify IPMI / QEMU agent confirms
        power off.
  - [ ] 13.3 Gate on `CCAI_UEFI_OVMF_VM` env var.
  - References: Requirement 2, Requirement 4, Requirement 6.

- [ ] 14. Documentation.
  - [ ] 14.1 Author a usage guide at `userguide/25-03-system-reset.rst`
        covering each `ResetKind`, the reconnect-verify contract, and
        the `ResetFailedError` recovery pattern.
  - [ ] 14.2 Cross-link from `AGENTS.md` Features section.
  - References: Introduction.
