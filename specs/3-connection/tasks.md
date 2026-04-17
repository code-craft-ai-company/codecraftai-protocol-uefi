# Tasks — UefiConnection

- [ ] 1. Create package skeleton at
      `source/packages/automojo/protocols/uefi/connection/`.
  - [ ] 1.1 Add `__init__.py` that re-exports `UefiConnection` and
        `ConnectionEvent`.
  - [ ] 1.2 Add a module-level docstring summarizing lifecycle
        responsibility boundaries.
  - References: Requirement 1.1.

- [ ] 2. Implement `events.py`.
  - [ ] 2.1 Author `ConnectionEvent` as a frozen slotted dataclass with
        the fields listed in the design.
  - [ ] 2.2 Provide `as_dict()` helper for structured-log sinks.
  - References: Requirement 7.

- [ ] 3. Implement `ueficonnection.py`.
  - [ ] 3.1 Author the `UefiConnection` class with `__init__` that
        validates the `transport` argument and initializes every
        instance variable (including `_reconnect_count`,
        `_last_connect_begin`, `_last_connect_end`) per coding
        standards.
  - [ ] 3.2 Implement `connected`, `reconnect_count`, `aspects`, `host`,
        `transport`, `transport_kind` as properties; reject assignment
        on `connected` and `reconnect_count`.
  - [ ] 3.3 Implement `connect(aspects=None)` with `resolve_aspects`
        and delegation to `_do_connect_with_retry`.
  - [ ] 3.4 Implement `disconnect()` with suppressed-and-logged
        behavior on underlying exceptions.
  - [ ] 3.5 Implement `reconnect(aspects=None)` that disconnects then
        connects and increments `reconnect_count` on success.
  - [ ] 3.6 Implement `_do_connect_with_retry` mirroring
        `sshbase.py:288–375` — begin/end timing, interval sleep,
        allowed-exception handling, must_connect short-circuit, and
        structured log events at each transition.
  - [ ] 3.7 Use `isinstance(cerr, tuple(allowed))` rather than
        `type(cerr) in allowed`, so subclass exceptions are tolerated.
  - [ ] 3.8 Implement `__enter__` / `__exit__` context manager protocol.
  - [ ] 3.9 Add Sphinx-style docstrings for every public method.
  - References: Requirements 1, 2, 3, 5, 6, 8, 10, 11.

- [ ] 4. Author lifecycle unit tests
      (`test_ueficonnection_lifecycle.py`).
  - [ ] 4.1 Connect-when-disconnected transitions and log event.
  - [ ] 4.2 Connect-when-connected no-op with DEBUG log.
  - [ ] 4.3 Disconnect-when-connected transitions and log event.
  - [ ] 4.4 Disconnect-when-disconnected no-op.
  - [ ] 4.5 Reconnect flow increments `reconnect_count`.
  - [ ] 4.6 Assignment to `connected` and `reconnect_count` raises
        `AttributeError`.
  - References: Requirements 1, 2, 4.

- [ ] 5. Author retry-semantics unit tests
      (`test_ueficonnection_retry.py`).
  - [ ] 5.1 Transport raises once then succeeds — connect completes and
        emits one WARNING event.
  - [ ] 5.2 Empty `allowed_connection_exceptions` — first failure
        propagates.
  - [ ] 5.3 `isinstance` matches subclasses —
        `ConnectionResetError` tolerated when `ConnectionError` is
        allowed.
  - [ ] 5.4 Transport raises continuously — terminal
        `ConnectionError` with chained `__cause__`.
  - [ ] 5.5 `must_connect=True` skips the retry loop.
  - [ ] 5.6 Sleep between retries respects
        `aspects.connection_interval` (verify with mocked `time.sleep`).
  - References: Requirements 5, 6, 9.

- [ ] 6. Author aspects-passthrough unit tests
      (`test_ueficonnection_aspects.py`).
  - [ ] 6.1 `reconnect(aspects=None)` resolves to instance default.
  - [ ] 6.2 `reconnect(aspects=X)` uses `X` only for that call.
  - [ ] 6.3 Passed aspects do not mutate the stored instance default.
  - References: Requirements 3, 11.

- [ ] 7. Author structured-logging tests.
  - [ ] 7.1 Capture `uefi.transport` records during connect/disconnect/
        reconnect and assert field presence per the design's table.
  - [ ] 7.2 Verify terminal failure emits ERROR with the full chain.
  - [ ] 7.3 Verify `ConnectionEvent.as_dict()` round-trips via
        `json.dumps`.
  - References: Requirement 7.

- [ ] 8. Author transport-kind dispatch tests.
  - [ ] 8.1 Construct with `UefiSerialTransport`, `UefiRedfishTransport`,
        `UefiSshHostTransport`, `UefiIpmiSolTransport` — verify
        acceptance and that `transport_kind` reflects the right value.
  - [ ] 8.2 Construct with a non-`IUefiTransport` (e.g., a plain
        `object()`) — verify `ConfigurationError`.
  - References: Requirement 10.

- [ ] 9. Author integration tests using fake transports.
  - [ ] 9.1 Full connect → execute-surrogate → disconnect cycle against
        a `FakeSerialTransport`.
  - [ ] 9.2 Full connect → reconnect → disconnect cycle against a
        `FakeRedfishTransport` that drops connection after N calls.
  - [ ] 9.3 Verify connection-loss mid-operation is visible via
        `connected` becoming `False` before `reconnect()` runs.
  - References: Requirements 1, 10.

- [ ] 10. Coverage gate.
  - [ ] 10.1 Configure pytest-cov to require 95% line coverage across
        `automojo.protocols.uefi.connection`.
  - [ ] 10.2 Add CI step that fails on drop.
  - References: Testing Strategy.

- [ ] 11. Documentation.
  - [ ] 11.1 Author `userguide/20-03-uefi-connection.rst` with usage
        examples for each transport and a diagram of the reconnect
        loop.
  - [ ] 11.2 Cross-link from `AGENTS.md` Features section.
  - References: Introduction.
