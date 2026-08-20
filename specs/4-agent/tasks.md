# Tasks — UefiAgent

- [ ] 1. Create package skeleton at
      `source/packages/ccai/protocols/uefi/agent/`.
  - [ ] 1.1 Add `__init__.py` that re-exports `UefiAgent`.
  - [ ] 1.2 Add a module-level docstring summarizing one-shot
        semantics and the `SshAgent` analogy.
  - References: Requirement 1.

- [ ] 2. Implement `uefiagent.py` — constructor and properties.
  - [ ] 2.1 Author `UefiAgent(UefiBase)` with `__init__` that forwards
        `host`, `primary_credential`, `transport_kind`, `jump`,
        `users`, `called_id`, `aspects`, `client_factory`, `logger` to
        `UefiBase.__init__`.
  - [ ] 2.2 Emit `agent.constructed` DEBUG log event after super init.
  - [ ] 2.3 Implement `__enter__`/`__exit__` context-manager protocol
        (no eager connect).
  - References: Requirements 1, 10, 11.

- [ ] 3. Implement `_create_client`.
  - [ ] 3.1 Resolve aspects via `resolve_aspects(aspects,
        self._aspects)`.
  - [ ] 3.2 Resolve credential via `session_user` lookup in
        `self._users`; raise `ConfigurationError` on miss.
  - [ ] 3.3 Build transport via `_build_transport` (dispatching on
        `transport_kind`).
  - [ ] 3.4 Wrap in a `UefiConnection`; call `connect()` (retry loop
        enforced inside the connection).
  - [ ] 3.5 Build the client via `self._client_factory(transport_kind,
        transport, aspects)`.
  - [ ] 3.6 Return the client; leave lifecycle to the caller.
  - References: Requirement 7.

- [ ] 4. Implement `_build_transport`.
  - [ ] 4.1 Dispatch on `transport_kind` to `UefiSerialTransport`,
        `UefiRedfishTransport`, `UefiSshHostTransport`, or
        `UefiIpmiSolTransport`.
  - [ ] 4.2 Raise `ConfigurationError` on unknown kinds.
  - References: Requirement 1.5.

- [ ] 5. Implement `run_cmd`.
  - [ ] 5.1 Accept `command`, `exp_status`, `user`, `aspects`,
        `client`.
  - [ ] 5.2 Resolve aspects; build a client unless `client` is passed.
  - [ ] 5.3 Call `self._run_cmd(runner=client, ...)` from
        `UefiBase`.
  - [ ] 5.4 Close the client in `finally` if it was created here;
        leave caller-supplied clients untouched.
  - [ ] 5.5 Return the three-tuple.
  - [ ] 5.6 Let `SINGLE_CONNECTED_CALL` /
        `DO_UNTIL_CONNECTION_FAILURE` propagate as `SemanticError`
        from the base.
  - References: Requirements 2, 3, 4, 12.

- [ ] 6. Implement `run_redfish` / `run_variable` (thin wrappers).
  - [ ] 6.1 `run_redfish` builds a Redfish-capable client and calls
        `_run_cmd` with a Redfish-framed command.
  - [ ] 6.2 `run_variable` routes to the appropriate backend per
        `transport_kind` — SSH-host → `efivar`; Redfish → BIOS
        attribute; Shell → `setvar`/`dmpstore`.
  - [ ] 6.3 Both accept `aspects=None` with instance-default fallback.
  - References: Requirements 2, 3.

- [ ] 7. Implement `open_session`.
  - [ ] 7.1 Resolve `session_credential` from `self._users[user]` or
        `self._primary_credential`.
  - [ ] 7.2 When `basis_session` is provided, construct the new
        session from its parameters (mirrors `sshagent.py:87–96`).
  - [ ] 7.3 When `basis_session` is None, construct the new session
        from the agent's parameters.
  - [ ] 7.4 Resolve aspects; log `agent.session.opened` at INFO.
  - References: Requirement 6, Requirement 10.2.

- [ ] 8. Implement `reboot`.
  - [ ] 8.1 Resolve aspects; compute deadline and grace period.
  - [ ] 8.2 Dispatch `_transport_reset_command()` via `run_cmd`,
        swallowing expected `(ConnectionError, TimeoutError, OSError,
        EOFError)` at DEBUG — mirrors `sshagent.py:143–145`.
  - [ ] 8.3 Sleep for `grace_period` seconds before polling.
  - [ ] 8.4 Poll `verify_connectivity()` every
        `aspects.completion_interval` until True or deadline elapses.
  - [ ] 8.5 Emit `agent.reboot.begin`, `agent.reboot.complete`,
        `agent.reboot.timeout` log events.
  - [ ] 8.6 Raise `TimeoutError` with a multiline `details` block on
        deadline expiry.
  - References: Requirements 5, 10.3–10.5.

- [ ] 9. Implement `_transport_reset_command`.
  - [ ] 9.1 Serial / IPMI-SOL → `"reset -c"`.
  - [ ] 9.2 Redfish → `"POST /redfish/v1/Systems/{id}/Actions/
        ComputerSystem.Reset {ResetType: ForceRestart}"`.
  - [ ] 9.3 SSH-host → `"reboot"`.
  - [ ] 9.4 Raise `ConfigurationError` on unknown kinds.
  - References: Requirement 5.1.

- [ ] 10. Implement file/directory primitives (SSH-host only).
  - [ ] 10.1 `file_push`, `file_pull`, `file_exists`,
        `directory`, `directory_exists`, `directory_tree` — delegate
        to the composed `SshAgent` when `transport_kind == "ssh-host"`.
  - [ ] 10.2 On any other transport, raise `SemanticError` with a
        pointer to `5-esp-file-ops`.
  - [ ] 10.3 Accept optional `ssh_client` parameter; do not close
        caller-supplied clients.
  - References: Requirement 8.

- [ ] 11. Author construction unit tests
      (`test_uefiagent_construct.py`).
  - [ ] 11.1 Valid `transport_kind` constructions (all four).
  - [ ] 11.2 Invalid `transport_kind` raises `ConfigurationError`.
  - [ ] 11.3 `called_id` → `_target_tag = "[called_id]"`.
  - [ ] 11.4 Custom aspects stored as instance default and not
        mutated by later calls.
  - References: Requirement 1.

- [ ] 12. Author run_cmd unit tests
      (`test_uefiagent_run_cmd.py`).
  - [ ] 12.1 One-shot `run_cmd` opens and closes a client.
  - [ ] 12.2 `run_cmd(client=existing)` does not close it.
  - [ ] 12.3 `user="ops"` uses the ops credential for client build.
  - [ ] 12.4 `user` not in users → `ConfigurationError`.
  - [ ] 12.5 One action-pattern test per supported pattern
        (`SINGLE_CALL`, `DO_UNTIL_SUCCESS`, `DO_WHILE_SUCCESS`).
  - [ ] 12.6 Session-only patterns raise `SemanticError`.
  - References: Requirements 2, 3, 4, 12.

- [ ] 13. Author open_session unit tests.
  - [ ] 13.1 No args: session inherits host/credential/transport/
        aspects.
  - [ ] 13.2 `user` parameter switches credential.
  - [ ] 13.3 `basis_session` clones params from existing session.
  - [ ] 13.4 `aspects=None` → session gets agent default.
  - [ ] 13.5 `aspects=custom` → session gets custom.
  - References: Requirement 6.

- [ ] 14. Author reboot unit tests
      (`test_uefiagent_reboot.py`).
  - [ ] 14.1 Happy path: reset sent, grace period, verify OK.
  - [ ] 14.2 Expected connection-error during reset swallowed at
        DEBUG.
  - [ ] 14.3 Timeout path: TimeoutError with expected `details`.
  - [ ] 14.4 Custom `reboot_grace_period` honored.
  - [ ] 14.5 Per-transport reset command dispatched correctly.
  - References: Requirement 5.

- [ ] 15. Author `_create_client` retry tests.
  - [ ] 15.1 Retry loop respects `connection_timeout` /
        `connection_interval`.
  - [ ] 15.2 `allowed_connection_exceptions` honored via
        `UefiConnection`.
  - [ ] 15.3 `must_connect=True` short-circuits on first failure.
  - References: Requirement 7.

- [ ] 16. Author file-primitive tests.
  - [ ] 16.1 SSH-host transport → delegates to composed `SshAgent`.
  - [ ] 16.2 Non-SSH-host transport → `SemanticError` with
        5-esp-file-ops pointer.
  - References: Requirement 8.

- [ ] 17. Author integration tests.
  - [ ] 17.1 Reboot flow using a fake transport that simulates
        disconnect/reconnect with configurable delay.
  - [ ] 17.2 Session-basis reuse: agent opens session A, then session
        B with A as basis; verify no new connection for B.
  - References: Requirements 5, 6.

- [ ] 18. Coverage gate.
  - [ ] 18.1 Configure pytest-cov for 95% line coverage across
        `ccai.protocols.uefi.agent`.
  - [ ] 18.2 Add CI step that fails on drop.
  - References: Testing Strategy.

- [ ] 19. Documentation.
  - [ ] 19.1 Author `userguide/20-04-uefi-agent.rst` with examples
        per transport and a reboot-flow diagram.
  - [ ] 19.2 Cross-link from `AGENTS.md` Features section.
  - References: Introduction.
