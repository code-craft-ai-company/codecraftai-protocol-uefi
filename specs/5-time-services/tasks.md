# Tasks — Time Services

- [ ] 1. Create package skeleton at
      `source/packages/ccai/protocols/uefi/time/`.
  - [ ] 1.1 Add `__init__.py` exporting `TimeServices`.
  - [ ] 1.2 Module docstring summarizing UEFI §8.3 scope and the
        strict tz-aware contract.
  - References: Introduction, Requirement 1, Requirement 2.

- [ ] 2. Implement `parsers.py`.
  - [ ] 2.1 Author `_parse_shell_time_output(stdout)` handling AMI and
        tianocore shell variants; returns UTC-aware `datetime`.
  - [ ] 2.2 Author `_parse_redfish_datetime(body)` reading `DateTime`
        and `DateTimeLocalOffset` fields per DSP0266.
  - [ ] 2.3 Author `_parse_iso_utc(text)` tolerating fractional seconds
        and trailing offsets.
  - [ ] 2.4 Author `test_parsers.py` covering Zulu, positive offset,
        negative offset, fractional seconds, both Shell variants.
  - References: Requirement 1, Requirement 3, Requirement 4,
        Requirement 5.

- [ ] 3. Implement `shellbackend.py`.
  - [ ] 3.1 Author `_get_time_shell(agent, aspects)` running Shell
        `time` (and `date` if needed) and returning UTC-aware
        `datetime`.
  - [ ] 3.2 Author `_set_time_shell(agent, dt, aspects)` converting to
        UTC and issuing `time -d MM/DD/YYYY HH:MM:SS`.
  - [ ] 3.3 Surface non-zero statuses as `UefiShellError` unless listed
        in `aspects.allowed_error_codes`.
  - [ ] 3.4 `test_shellbackend.py` with a fake agent and canned outputs.
  - References: Requirement 3.

- [ ] 4. Implement `redfishbackend.py`.
  - [ ] 4.1 Author `_get_time_redfish(agent, aspects)` via
        `agent.redfish_get` on `Managers/{id}`.
  - [ ] 4.2 Author `_set_time_redfish(agent, dt, aspects)` via
        `agent.redfish_patch` with an ISO 8601 `DateTime` body.
  - [ ] 4.3 Honor `202 Accepted` task polling per
        `aspects.completion_interval`.
  - [ ] 4.4 Surface 4xx/5xx as `UefiError` with status + body in
        `details`.
  - [ ] 4.5 `test_redfishbackend.py` with fake agent + canned bodies.
  - References: Requirement 4.

- [ ] 5. Implement `hwclockbackend.py`.
  - [ ] 5.1 Author `_get_time_hwclock(agent, aspects)` shelling to
        `hwclock --show --utc --output-format=iso` and parsing the
        ISO result.
  - [ ] 5.2 Author `_set_time_hwclock(agent, dt, aspects)` shelling to
        `sudo hwclock --set --date '<iso>' --utc`.
  - [ ] 5.3 Detect "command not found" and raise `UefiError` with a
        `util-linux` install hint.
  - [ ] 5.4 Detect `sudo` prompts and raise `ConfigurationError` via
        the 2-transport-ssh-host contract.
  - [ ] 5.5 `test_hwclockbackend.py` with fake agent + canned outputs.
  - References: Requirement 5.

- [ ] 6. Implement `timeservices.py` facade.
  - [ ] 6.1 Author the `TimeServices` class per the design.
  - [ ] 6.2 Author `_validate_set_time_input(dt)` rejecting non-
        `datetime` and naive `datetime` with `ConfigurationError`.
  - [ ] 6.3 Author `_dispatch_get_time` and `_dispatch_set_time`
        selecting backend by `agent.transport_kind`.
  - [ ] 6.4 Author `_verify_readback(agent, written, tolerance,
        aspects)` for `verify=True`.
  - [ ] 6.5 Raise `SemanticError` on `DO_WHILE_SUCCESS` or
        `DO_UNTIL_CONNECTION_FAILURE`.
  - References: Requirement 2, Requirement 9, Requirement 10.

- [ ] 7. Unit tests — validation (`test_validation.py`).
  - [ ] 7.1 `set_time(naive_dt)` raises `ConfigurationError`.
  - [ ] 7.2 `set_time("2026-01-01")` raises `ConfigurationError`.
  - [ ] 7.3 `set_time(aware_dt)` passes.
  - References: Requirement 2.

- [ ] 8. Round-trip test (`test_roundtrip.py`).
  - [ ] 8.1 Fake-clock agent: `set_time(x)` then `get_time()` equal
        within tolerance.
  - [ ] 8.2 `verify=True` happy path.
  - [ ] 8.3 `verify=True` outside-tolerance raises `UefiError` with
        populated details.
  - References: Requirement 10.

- [ ] 9. Action-pattern tests (`test_actionpatterns.py`).
  - [ ] 9.1 `SINGLE_CALL` one invocation.
  - [ ] 9.2 `SINGLE_CONNECTED_CALL` retries connection class.
  - [ ] 9.3 `DO_UNTIL_SUCCESS` polls until success or timeout;
        `TimeoutError` on elapse.
  - [ ] 9.4 `DO_WHILE_SUCCESS` / `DO_UNTIL_CONNECTION_FAILURE` raise
        `SemanticError`.
  - [ ] 9.5 `aspects=None` falls back to instance default.
  - References: Requirement 6, Requirement 7, Requirement 8,
        Requirement 9.

- [ ] 10. TZ-handling tests (`test_tzhandling.py`).
  - [ ] 10.1 `zoneinfo.ZoneInfo("US/Pacific")` during DST: UTC
        conversion correct.
  - [ ] 10.2 Fixed-offset `datetime`: correct.
  - [ ] 10.3 Returned `datetime.tzinfo` is never `None` across backends
        (parametrized).
  - References: Requirement 1, Requirement 12.

- [ ] 11. Integration test — Shell.
  - [ ] 11.1 OVMF VM: set a known time through the Shell backend, read
        back, assert within tolerance.
  - [ ] 11.2 Gate on `CCAI_UEFI_OVMF_VM` env var.
  - References: Requirement 3, Requirement 10.

- [ ] 12. Integration test — Redfish.
  - [ ] 12.1 Against a test BMC simulator (or a containerized Redfish
        emulator): `PATCH` DateTime, `GET` DateTime, assert round-trip.
  - [ ] 12.2 Gate on `CCAI_UEFI_REDFISH_HOST` env var.
  - References: Requirement 4.

- [ ] 13. Integration test — hwclock.
  - [ ] 13.1 Linux VM with passwordless sudo: set and read hwclock,
        assert round-trip.
  - [ ] 13.2 Gate on `CCAI_UEFI_SSH_HOST` env var.
  - References: Requirement 5.

- [ ] 14. Documentation.
  - [ ] 14.1 Usage guide `userguide/25-05-time-services.rst` covering
        the tz-aware contract, each backend, and the `verify=True`
        pattern; include a worked example of RTC-drift testing.
  - [ ] 14.2 Cross-link from `AGENTS.md` Features section.
  - References: Introduction.
