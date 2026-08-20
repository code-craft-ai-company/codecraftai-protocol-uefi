# Tasks — UefiClient

- [ ] 1. Create package skeleton at
      `source/packages/ccai/protocols/uefi/client/`.
  - [ ] 1.1 Add `__init__.py` that re-exports `UefiClient`,
        `UefiShellClient`, `UefiRedfishClient`, `UefiHostOsClient`, and
        `UefiExecuteResult`.
  - [ ] 1.2 Add a module-level docstring summarizing the "client = framing,
        layer-4 = retry" division of labor.
  - References: Requirement 1.1.

- [ ] 2. Implement `ueficlient.py` (abstract base).
  - [ ] 2.1 Author the `UefiClient` class with `supported_action_patterns`
        class attribute, `__init__`, `aspects`/`transport`/`closed`
        properties, `open_channel`, `execute`, `close`, and the
        `__enter__`/`__exit__` context-manager protocol.
  - [ ] 2.2 Implement `_check_action_pattern` with a multiline error
        message per `/Users/myron/repos/CLAUDE.md` standards.
  - [ ] 2.3 Mark `_validate_transport`, `_open_channel_impl`,
        `_execute_impl`, and `_close_impl` as abstract by raising
        `AbstractMethodError` with the subclass name included.
  - [ ] 2.4 Initialize every instance variable in `__init__` per
        coding standards; no lazy attribute creation elsewhere.
  - [ ] 2.5 Add Sphinx-style docstrings for every public method.
  - References: Requirement 1.1–1.4, Requirement 2.1–2.4, Requirement 3,
    Requirement 8, Requirement 9, Requirement 12.

- [ ] 3. Implement `uefishellclient.py`.
  - [ ] 3.1 Author `UefiShellClient(UefiClient)` with
        `supported_action_patterns = {SINGLE_CALL, DO_UNTIL_SUCCESS,
        DO_WHILE_SUCCESS}`.
  - [ ] 3.2 Implement `_validate_transport` to accept
        `UefiSerialTransport` and `UefiIpmiSolTransport` and reject all
        others with `ConfigurationError`.
  - [ ] 3.3 Implement `_open_channel_impl` that rejects `multiplex=True`
        with `SemanticError`.
  - [ ] 3.4 Implement `_execute_impl` that sends, reads until
        `aspects.prompt_pattern`, and parses shell status into
        `UefiExecuteResult`.
  - [ ] 3.5 Implement `_parse_shell_response` private helper that
        separates status/stdout/stderr from the raw prompt-bounded
        capture.
  - References: Requirement 5, Requirement 11.1–11.2.

- [ ] 4. Implement `uefiredfishclient.py`.
  - [ ] 4.1 Author `UefiRedfishClient(UefiClient)` with
        `supported_action_patterns = {SINGLE_CALL, DO_UNTIL_SUCCESS,
        DO_WHILE_SUCCESS}`.
  - [ ] 4.2 Implement `_validate_transport` to require
        `UefiRedfishTransport`.
  - [ ] 4.3 Implement `_open_channel_impl` that supports
        `multiplex=True` by sharing the HTTP session.
  - [ ] 4.4 Implement `_execute_impl` with command parsing and 202
        TaskMonitor polling bounded by `aspects.completion_timeout`.
  - [ ] 4.5 Implement `_parse_redfish_command` private helper that
        decomposes `"VERB path [json_body]"` into a tuple.
  - References: Requirement 6, Requirement 11.3.

- [ ] 5. Implement `uefihostosclient.py`.
  - [ ] 5.1 Author `UefiHostOsClient(UefiClient)` with
        `supported_action_patterns = {SINGLE_CALL, DO_UNTIL_SUCCESS,
        DO_WHILE_SUCCESS}`.
  - [ ] 5.2 Implement `_validate_transport` to require
        `UefiSshHostTransport`.
  - [ ] 5.3 Implement `_open_channel_impl` that wraps the composed
        `SshAgent`'s channel (multiplex pass-through).
  - [ ] 5.4 Implement `_execute_impl` by forwarding to
        `self._transport.ssh_agent.run_cmd` and wrapping the returned
        three-tuple into `UefiExecuteResult`.
  - References: Requirement 7, Requirement 11.4.

- [ ] 6. Implement `UefiExecuteResult` dataclass.
  - [ ] 6.1 Declare as `@dataclass(frozen=True, slots=True)`.
  - [ ] 6.2 Add `from_redfish_task` and `from_http_response` classmethod
        factories.
  - [ ] 6.3 Add `__repr__` that redacts likely-secret fields.
  - References: Requirement 3.5, Requirement 7.3.

- [ ] 7. Author unit tests for the abstract base
      (`test_ueficlient_base.py`).
  - [ ] 7.1 Test `AbstractMethodError` is raised when any abstract
        method is called on the base.
  - [ ] 7.2 Test `_check_action_pattern` with a contrived subclass
        whose `supported_action_patterns` excludes each pattern in turn.
  - [ ] 7.3 Test `close()` idempotency.
  - [ ] 7.4 Test `close()` suppresses channel / transport close
        exceptions with a WARNING log.
  - [ ] 7.5 Test context-manager closes on exception without
        swallowing.
  - References: Requirement 1, Requirement 8, Requirement 12.

- [ ] 8. Author targeted action-pattern tests per subclass.
  - [ ] 8.1 For `UefiShellClient`, one test per supported
        `ActionPattern` (`SINGLE_CALL`, `DO_UNTIL_SUCCESS`,
        `DO_WHILE_SUCCESS`) using a fake serial transport.
  - [ ] 8.2 For `UefiShellClient`, assert `SemanticError` for
        `SINGLE_CONNECTED_CALL` and `DO_UNTIL_CONNECTION_FAILURE`.
  - [ ] 8.3 For `UefiRedfishClient`, same three-plus-two matrix using a
        `responses`-backed fake Redfish server.
  - [ ] 8.4 For `UefiHostOsClient`, same matrix using a mocked
        `SshAgent`.
  - References: Requirement 5, Requirement 6, Requirement 7.

- [ ] 9. Author connection-loss fault-injection tests.
  - [ ] 9.1 For each subclass, raise `ConnectionError` from the transport
        mid-`execute` and verify the client re-raises without retry.
  - [ ] 9.2 Verify the associated channel is closed before the exception
        propagates.
  - [ ] 9.3 Raise `TimeoutError` from the transport during an inactivity
        window and verify re-raise without retry.
  - References: Requirement 4.

- [ ] 10. Author aspects-passthrough tests.
  - [ ] 10.1 Test that `execute(command, aspects=None)` resolves to the
        client default.
  - [ ] 10.2 Test that a non-None `aspects` overrides the default on a
        per-call basis.
  - [ ] 10.3 Test that a client constructed without an instance default
        and called with `aspects=None` raises `ConfigurationError`.
  - References: Requirement 9.

- [ ] 11. Author structured-logging tests.
  - [ ] 11.1 Capture log output under `uefi.transport` during execute.
  - [ ] 11.2 Assert `dispatch.begin`, `dispatch.complete`, and
        `dispatch.error` fields.
  - [ ] 11.3 Verify `FAILURE_ONLY` suppresses `dispatch.complete` on
        success.
  - [ ] 11.4 Verify secret-redaction in log lines for a Redfish
        `Sessions` POST.
  - References: Requirement 10.

- [ ] 12. Coverage gate.
  - [ ] 12.1 Configure pytest-cov to require 95% line coverage across
        `ccai.protocols.uefi.client`.
  - [ ] 12.2 Add a CI step that fails on drop.
  - References: Testing Strategy.

- [ ] 13. Documentation.
  - [ ] 13.1 Author `userguide/20-03-uefi-client.rst` covering each
        subclass and a sample usage.
  - [ ] 13.2 Cross-link from `AGENTS.md` Features section.
  - References: Introduction.
