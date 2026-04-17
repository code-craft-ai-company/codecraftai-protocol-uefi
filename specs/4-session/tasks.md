# Tasks — UefiSession

- [ ] 1. Create package skeleton at
      `source/packages/automojo/protocols/uefi/session/`.
  - [ ] 1.1 Add `__init__.py` that re-exports `UefiSession`.
  - [ ] 1.2 Add a module-level docstring summarizing the persistent-
        channel / replay-on-reconnect / basis-session semantics.
  - References: Requirement 1.

- [ ] 2. Implement `uefisession.py` — constructor.
  - [ ] 2.1 Author `UefiSession(UefiBase)` with `__init__` that calls
        `UefiBase.__init__` and initializes every session-specific
        instance variable:
        `_basis_session`, `_client`, `_channel`, `_reconnect_count`,
        `_current_map_alias`, `_cwd`, `_replay_actions`,
        `_enter_time`.
  - [ ] 2.2 When `basis_session is not None`, seed
        `_replay_actions` with a copy of
        `basis_session._replay_actions`.
  - [ ] 2.3 Add Sphinx-style docstrings per repository coding
        standards.
  - References: Requirements 1, 4.5, 12.

- [ ] 3. Implement read-only properties.
  - [ ] 3.1 `connected` — channel is not None and not closed.
  - [ ] 3.2 `reconnect_count` — int, read-only.
  - [ ] 3.3 `current_map_alias` — optional str.
  - [ ] 3.4 `cwd` — optional str.
  - [ ] 3.5 `basis_session` — optional session reference.
  - [ ] 3.6 Assignment to any of these properties raises
        `AttributeError`.
  - References: Requirements 1, 6, 10.

- [ ] 4. Implement context-manager protocol.
  - [ ] 4.1 `__enter__` records `_enter_time`, calls
        `_open_client_and_channel`, logs `session.enter`.
  - [ ] 4.2 `__exit__` calls `close()` and logs `session.exit` with
        `elapsed_ms`.
  - [ ] 4.3 Exception inside the `with` block propagates unchanged.
  - References: Requirement 1, Requirement 11.1–11.2.

- [ ] 5. Implement `_open_client_and_channel`.
  - [ ] 5.1 When `basis_session is not None`, reuse
        `basis_session._client`; raise `RuntimeError` if it is
        `None`.
  - [ ] 5.2 Otherwise, construct a client via
        `_create_client_like_agent(aspects=self._aspects)`.
  - [ ] 5.3 Call `self._client.open_channel()` and store on
        `self._channel`.
  - References: Requirement 5.

- [ ] 6. Implement `run_cmd`.
  - [ ] 6.1 Guard: `self._channel is None` → `SemanticError`.
  - [ ] 6.2 Resolve aspects; reject `SINGLE_CALL` /
        `DO_UNTIL_SUCCESS` / `DO_WHILE_SUCCESS` with
        `SemanticError` (inverse of `sshbase.py:625–627`).
  - [ ] 6.3 Implement `SINGLE_CONNECTED_CALL` path: call
        `UefiBase._run_cmd` with the persistent channel.
  - [ ] 6.4 Implement `DO_UNTIL_CONNECTION_FAILURE` path: call in a
        loop; on allowed-connection exceptions, call
        `_auto_reconnect`; on status divergence, return.
  - [ ] 6.5 Leave non-allowed exceptions to propagate.
  - References: Requirement 2.

- [ ] 7. Implement `_auto_reconnect`.
  - [ ] 7.1 Log `session.reconnect.begin` WARNING with
        `trigger_exception`.
  - [ ] 7.2 Close the current channel and client (unless
        `basis_session` owns the client), suppressing any raises.
  - [ ] 7.3 Build a new client and open a new channel via
        `_create_client_like_agent(aspects=resolved)`.
  - [ ] 7.4 Increment `_reconnect_count`.
  - [ ] 7.5 Call `_replay()`.
  - [ ] 7.6 Log `session.reconnect.complete` INFO with
        `reconnect_count`, `replay_actions_count`, `elapsed_ms`.
  - References: Requirements 3, 10, 11.3–11.4.

- [ ] 8. Implement `_replay`.
  - [ ] 8.1 Iterate `_replay_actions` in order, calling each with
        `self`.
  - [ ] 8.2 On any exception, wrap in `UefiError` with
        `action_index`, `action repr`, `CAUSE` in details; chain
        original via `__cause__`.
  - [ ] 8.3 Log `session.replay.failed` ERROR before raising.
  - References: Requirement 4, Requirement 11.5.

- [ ] 9. Implement `record_replay` and `clear_replay`.
  - [ ] 9.1 `record_replay(action)` appends to `_replay_actions`.
  - [ ] 9.2 `clear_replay()` empties the list.
  - References: Requirement 4.1, Requirement 4.4.

- [ ] 10. Implement environment helpers `map_alias` and `chdir`.
  - [ ] 10.1 `map_alias(alias, path, aspects=None)`: send
        `map {alias} {path}`, verify zero status, update
        `_current_map_alias`, record a replay action that re-runs
        the same map command.
  - [ ] 10.2 `chdir(path, aspects=None)`: send `cd {path}`, verify
        zero status, update `_cwd`, record a replay action.
  - [ ] 10.3 Non-zero shell status → raise `UefiShellError(command,
        status, stdout, stderr)`.
  - References: Requirement 6.

- [ ] 11. Implement `reboot`.
  - [ ] 11.1 Issue the transport-native reset via `run_cmd`, swallow
        expected `(ConnectionError, TimeoutError, OSError, EOFError)`
        at DEBUG (mirrors `sshsession.py:337–350`).
  - [ ] 11.2 Clear `_channel` and `_client` references.
  - [ ] 11.3 Raise `UnExpectedSessionClosureError`.
  - References: Requirement 7.

- [ ] 12. Implement `close`.
  - [ ] 12.1 Close `_channel` with suppressed-and-logged errors.
  - [ ] 12.2 Close `_client` only when `_basis_session is None`,
        with suppressed-and-logged errors.
  - [ ] 12.3 Idempotent: second call is a no-op.
  - References: Requirement 8.

- [ ] 13. Author enter/exit unit tests
      (`test_uefisession_enter_exit.py`).
  - [ ] 13.1 `with UefiSession(...) as s:` opens exactly one channel.
  - [ ] 13.2 `s.connected` is True inside, False after.
  - [ ] 13.3 `run_cmd` outside the `with` raises `SemanticError`.
  - [ ] 13.4 `__exit__` closes channel + client.
  - [ ] 13.5 Exception in the `with` propagates; teardown still runs.
  - References: Requirements 1, 8.

- [ ] 14. Author pattern-matrix unit tests
      (`test_uefisession_patterns.py`).
  - [ ] 14.1 `SINGLE_CONNECTED_CALL` runs once on persistent channel.
  - [ ] 14.2 `DO_UNTIL_CONNECTION_FAILURE` loops until allowed
        exception exits cleanly.
  - [ ] 14.3 Each non-session pattern raises `SemanticError`.
  - References: Requirement 2.

- [ ] 15. Author auto-reconnect unit tests
      (`test_uefisession_auto_reconnect.py`).
  - [ ] 15.1 Allowed exception under `DO_UNTIL_CONNECTION_FAILURE`
        triggers reconnect and increments `reconnect_count`.
  - [ ] 15.2 `_replay_actions` replayed in insertion order on
        reconnect.
  - [ ] 15.3 Replay-action failure raises `UefiError` with
        `action_index` in details.
  - [ ] 15.4 Reconnect exhausting `connection_timeout` raises
        `ConnectionError`.
  - References: Requirements 3, 4, 11.

- [ ] 16. Author basis-session unit tests
      (`test_uefisession_basis.py`).
  - [ ] 16.1 Basis-session construction shares client handle.
  - [ ] 16.2 Inactive basis raises `RuntimeError` on `__enter__`.
  - [ ] 16.3 Derived session inherits replay list from basis.
  - [ ] 16.4 Closing derived does not close basis's client.
  - References: Requirement 5.

- [ ] 17. Author environment-helper tests.
  - [ ] 17.1 `map_alias` updates `current_map_alias` and records
        replay.
  - [ ] 17.2 `chdir` updates `cwd` and records replay.
  - [ ] 17.3 Non-zero status raises `UefiShellError` with populated
        attributes.
  - [ ] 17.4 Replay entry re-runs the shell command with the same
        arguments.
  - References: Requirement 6.

- [ ] 18. Author reboot unit tests.
  - [ ] 18.1 `reboot()` issues reset, swallows expected connection
        errors at DEBUG.
  - [ ] 18.2 `_channel` and `_client` are cleared before the
        `UnExpectedSessionClosureError` raises.
  - References: Requirement 7.

- [ ] 19. Author integration tests.
  - [ ] 19.1 Long `DO_UNTIL_CONNECTION_FAILURE` loop over a fake
        transport that drops after N commands; verify reconnect and
        replay.
  - [ ] 19.2 Basis-session parallel-session test (agent opens
        session A; session B with A as basis; both make calls).
  - [ ] 19.3 Structured-log capture: enter / exit / reconnect events
        carry expected fields.
  - References: Requirements 3, 5, 11.

- [ ] 20. Coverage gate.
  - [ ] 20.1 Configure pytest-cov for 95% line coverage across
        `automojo.protocols.uefi.session`.
  - [ ] 20.2 Add CI step that fails on drop.
  - References: Testing Strategy.

- [ ] 21. Documentation.
  - [ ] 21.1 Author `userguide/20-04-uefi-session.rst` with a
        persistent-shell example and a diagram of the auto-reconnect
        loop.
  - [ ] 21.2 Cross-link from `AGENTS.md` Features section.
  - References: Introduction.
