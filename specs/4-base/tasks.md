# Tasks — UefiBase

- [ ] 1. Create package skeleton at
      `source/packages/automojo/protocols/uefi/base/`.
  - [ ] 1.1 Add `__init__.py` that re-exports `UefiBase` and
        `default_client_factory`.
  - [ ] 1.2 Add a module-level docstring summarizing the five-pattern
        engine and the `IUefiSystemContext` role.
  - References: Requirement 7.1.

- [ ] 2. Implement `runctx.py`.
  - [ ] 2.1 Author `_RunContext` dataclass bundling
        `completion_toctx`, `inactivity_timeout`, `inactivity_interval`,
        `monitor_delay`, `logging_pattern`, `allowed_exceptions`,
        `must_connect`, `response_encoding`, `prompt_pattern`.
  - [ ] 2.2 Populate `_RunContext` from a `UefiCmdAspects` via a
        `from_aspects(aspects)` classmethod.
  - References: Requirements 2, 4, 10.

- [ ] 3. Implement `uefibase.py` — constructor and properties.
  - [ ] 3.1 Author `__init__` that initializes every instance
        attribute listed in the design (including `_user_lookup_table`,
        `_group_lookup_table`, and the firmware/secure-boot caches)
        per coding standards; perform no network I/O.
  - [ ] 3.2 Implement `aspects`, `host`, `transport_kind`, `jump`,
        `users`, `primary_credential`, `target_tag` as properties.
  - [ ] 3.3 Implement `_target_tag` formation: `called_id=None → host`,
        else `[called_id]` — mirrors `sshbase.py:113–115`.
  - [ ] 3.4 Implement `_validate_credential` which calls
        `credential.validate()` and cross-checks `transport_kind` vs.
        credential class.
  - References: Requirements 1, 11, 12.

- [ ] 4. Implement `IUefiSystemContext` compliance.
  - [ ] 4.1 Declare inheritance from `IUefiSystemContext`.
  - [ ] 4.2 Implement `firmware_vendor`, `firmware_revision`,
        `secure_boot_state` as cached properties.
  - [ ] 4.3 Implement private `_probe_firmware_vendor`,
        `_probe_firmware_revision`, `_probe_secure_boot_state`
        methods that route through `run_cmd` or `run_redfish` based on
        `transport_kind`.
  - References: Requirement 7.

- [ ] 5. Implement `reboot`, `run_cmd`, `run_redfish`, `run_variable`
      as abstract methods.
  - [ ] 5.1 Raise `AbstractMethodError` with the subclass name in each
        method, mirroring `sshbase.py:234–256`.
  - References: Requirement 6.

- [ ] 6. Implement `verify_connectivity`.
  - [ ] 6.1 Branch on `transport_kind` to select probe:
        `echo Hello` for shell/SSH-host, `GET /redfish/v1/` for
        Redfish.
  - [ ] 6.2 Compare response against the expected value and return
        `True`/`False` accordingly.
  - [ ] 6.3 Swallow `ConnectionError` at DEBUG level and return `False`.
  - [ ] 6.4 Raise `ConfigurationError` on unknown transport kinds.
  - References: Requirement 5.

- [ ] 7. Implement `_log_command_result`.
  - [ ] 7.1 Follow `sshbase.py:469–499` exactly, with `LoggingPattern`
        dispatch.
  - [ ] 7.2 Prepend `_target_tag` to every log line.
  - [ ] 7.3 Use `TEMPLATE_COMMAND_SUCCESS` / `TEMPLATE_COMMAND_FAILURE`
        templates imported from `1-constants-and-types`.
  - References: Requirement 8, Requirement 11.3.

- [ ] 8. Implement `_run_cmd` — the action-pattern engine.
  - [ ] 8.1 Resolve aspects via `resolve_aspects(aspects,
        self._aspects)` at the top.
  - [ ] 8.2 Build `_RunContext` and a thread monitor message that
        includes thread id, name, and command.
  - [ ] 8.3 Implement `SINGLE_CALL` path with a `MonitoredScope` label
        `"RUNCMD-SINGULAR"`.
  - [ ] 8.4 Implement `DO_UNTIL_SUCCESS` path mirroring
        `sshbase.py:553–587`, extended with allowed-exception handling
        and `must_connect` short-circuit.
  - [ ] 8.5 Implement `DO_WHILE_SUCCESS` path mirroring
        `sshbase.py:590–623`.
  - [ ] 8.6 Implement `SINGLE_CONNECTED_CALL` path that requires a
        `UefiChannel` runner; raise `SemanticError` otherwise.
  - [ ] 8.7 Implement `DO_UNTIL_CONNECTION_FAILURE` path that loops
        until an allowed-connection exception is raised and exits
        cleanly, or raises on non-allowed exceptions.
  - [ ] 8.8 Raise `SemanticError` for unknown patterns — mirrors
        `sshbase.py:628–630`.
  - [ ] 8.9 Return `(status, stdout, stderr)` three-tuple in all
        success paths.
  - References: Requirements 2, 3, 4, 9, 10, 13.

- [ ] 9. Implement `_dispatch_inner`.
  - [ ] 9.1 When `runner` is `UefiClient`, call `.execute(command,
        aspects)` and map to three-tuple.
  - [ ] 9.2 When `runner` is `UefiChannel`, send command, `recv_until`
        prompt, parse into three-tuple.
  - [ ] 9.3 Raise `SemanticError` for unsupported runner types.
  - References: Requirement 13.

- [ ] 10. Implement `default_client_factory`.
  - [ ] 10.1 Dispatch on `transport_kind` to `UefiShellClient`,
        `UefiRedfishClient`, `UefiHostOsClient`.
  - [ ] 10.2 Raise `ConfigurationError` on unknown transport kinds.
  - References: Requirement 12.

- [ ] 11. Author init unit tests (`test_uefibase_init.py`).
  - [ ] 11.1 Verify all documented instance variables exist after
        `__init__`.
  - [ ] 11.2 Verify no network I/O in `__init__` (monkeypatch socket
        module to raise on use).
  - [ ] 11.3 Verify `_target_tag` formation under both
        `called_id=None` and non-None.
  - [ ] 11.4 Verify credential/transport mismatch raises
        `ConfigurationError`.
  - References: Requirements 1, 11, 12.

- [ ] 12. Author action-pattern unit tests
      (`test_uefibase_action_pattern.py`).
  - [ ] 12.1 `SINGLE_CALL` runs exactly once.
  - [ ] 12.2 `DO_UNTIL_SUCCESS` happy path: loops until match.
  - [ ] 12.3 `DO_UNTIL_SUCCESS` timeout path: TimeoutContext error with
        details including command, STDOUT, STDERR.
  - [ ] 12.4 `DO_WHILE_SUCCESS` happy path: loops while match.
  - [ ] 12.5 `DO_WHILE_SUCCESS` timeout path.
  - [ ] 12.6 `SINGLE_CONNECTED_CALL` requires `UefiChannel` runner;
        raise `SemanticError` with `UefiClient`.
  - [ ] 12.7 `DO_UNTIL_CONNECTION_FAILURE` exits cleanly on allowed
        exception.
  - [ ] 12.8 Unknown pattern raises `SemanticError`.
  - References: Requirement 2.

- [ ] 13. Author allowed-exception / must_connect tests.
  - [ ] 13.1 Allowed exception inside `DO_UNTIL_SUCCESS` continues the
        loop.
  - [ ] 13.2 Non-allowed exception propagates.
  - [ ] 13.3 `must_connect=True` + `ConnectionError` re-raises without
        retry.
  - [ ] 13.4 `DO_UNTIL_CONNECTION_FAILURE` exits on allowed exception.
  - References: Requirement 10.

- [ ] 14. Author MonitoredScope tests
      (`test_uefibase_monitored_scope.py`).
  - [ ] 14.1 Verify each pattern wraps the inner call with the labelled
        scope (`RUNCMD-SINGULAR`, `RUNCMD-DO_UNTIL_SUCCESS`, etc.).
  - [ ] 14.2 Verify `monitor_delay` flows from aspects to
        `MonitoredScope(notify_delay=...)`.
  - [ ] 14.3 Verify the monitor message includes thread id, name, and
        command.
  - References: Requirement 3.

- [ ] 15. Author logging tests (`test_uefibase_logging.py`).
  - [ ] 15.1 `LoggingPattern.ALL_RESULTS` logs both success and
        failure.
  - [ ] 15.2 `LoggingPattern.SUCCESS_ONLY` suppresses failure logs.
  - [ ] 15.3 `LoggingPattern.FAILURE_ONLY` suppresses success logs.
  - [ ] 15.4 Int vs. sequence `expected_status` comparison.
  - [ ] 15.5 `_target_tag` prefix present on every line.
  - References: Requirement 8.

- [ ] 16. Author abstract-method tests
      (`test_uefibase_abstract.py`).
  - [ ] 16.1 `reboot`, `run_cmd`, `run_redfish`, `run_variable` all
        raise `AbstractMethodError` with the subclass name included.
  - References: Requirement 6.

- [ ] 17. Author system-context tests.
  - [ ] 17.1 `isinstance(base, IUefiSystemContext)` is True.
  - [ ] 17.2 `firmware_vendor`/`firmware_revision`/`secure_boot_state`
        cache on first access.
  - References: Requirement 7.

- [ ] 18. Author hung-thread integration test.
  - [ ] 18.1 Inner call blocks forever; verify `MonitoredScope` fires
        after `monitor_delay` with the expected message.
  - References: Requirement 3.

- [ ] 19. Coverage gate.
  - [ ] 19.1 Configure pytest-cov for 95% line coverage overall and
        100% on the `_run_cmd` dispatch switch.
  - [ ] 19.2 Add CI step that fails on drop.
  - References: Testing Strategy.

- [ ] 20. Documentation.
  - [ ] 20.1 Author `userguide/20-04-uefi-base.rst` with the
        five-pattern matrix and a worked example per pattern.
  - [ ] 20.2 Cross-link from `AGENTS.md` Features section.
  - References: Introduction.
