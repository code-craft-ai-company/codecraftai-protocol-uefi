# Tasks — Serial Transport

- [ ] 1. Create package skeleton at
      `source/packages/automojo/protocols/uefi/transport/serial/`.
  - [ ] 1.1 Add `__init__.py` re-exporting `UefiSerialTransport`.
  - [ ] 1.2 Add a module-level docstring summarizing the measured-interop
        contract and the supported endpoint schemes.
  - [ ] 1.3 Add the package to the dependency-direction test so it cannot
        import from layers 3+.
  - References: Requirement 9.

- [ ] 2. Implement `endpoints.py`.
  - [ ] 2.1 Author `parse_endpoint(endpoint: str) -> tuple[scheme, address]`.
  - [ ] 2.2 Author `select_backend(scheme) -> type[_SerialBackend]`.
  - [ ] 2.3 Raise `ConfigurationError` for unknown schemes with the required
        `details` payload.
  - [ ] 2.4 Add `test_endpoints.py` covering every accepted scheme and a
        representative rejected scheme.
  - References: Requirement 1.

- [ ] 3. Implement `termios_snapshot.py`.
  - [ ] 3.1 Author `TermiosSnapshot` frozen dataclass.
  - [ ] 3.2 Author `capture_termios(fd)` and `restore_termios(snapshot)`.
  - [ ] 3.3 Add `test_termios_snapshot.py` using a PTY pair to verify that
        captured attributes restore cleanly.
  - References: Requirement 6.2, Requirement 6.5.

- [ ] 4. Implement the three `_SerialBackend` subclasses.
  - [ ] 4.1 Author `local_backend.py` using `pyserial`; apply
        `flow_control`, `parity`, `stop_bits`, `baud` at open; capture
        termios at open and restore at close.
  - [ ] 4.2 Author `socket_backend.py` using `socket.create_connection`;
        reject link-layer params at open with `ConfigurationError`.
  - [ ] 4.3 Author `telnet_backend.py` on top of `socket_backend.py`; strip
        IAC (0xFF) negotiation bytes from the inbound stream.
  - [ ] 4.4 Add unit tests per backend using mocked `pyserial` /
        `socket.socket` handles.
  - References: Requirement 1, Requirement 3.

- [ ] 5. Implement `readuntil.py`.
  - [ ] 5.1 Author the `read_until` loop per the design, accepting bytes or
        compiled `re.Pattern[bytes]`.
  - [ ] 5.2 Populate `.partial` on `TimeoutError`.
  - [ ] 5.3 Emit an inactivity DEBUG heartbeat every
        `aspects.inactivity_interval` seconds.
  - [ ] 5.4 Add `test_readuntil.py` covering chunk-boundary match, regex
        match, timeout with populated `.partial`, inactivity heartbeat.
  - References: Requirement 5, Requirement 10.4.

- [ ] 6. Implement `UefiSerialTransport` in `serialtransport.py`.
  - [ ] 6.1 Author the constructor — initialize every instance variable per
        the coding standards (no lazy attribute creation elsewhere).
  - [ ] 6.2 Implement `connect()` honoring all five `ActionPattern` values
        (raising `SemanticError` for `DO_WHILE_SUCCESS`).
  - [ ] 6.3 Implement `disconnect()` / `close()` as context-manager-safe
        no-ops on an already-closed transport.
  - [ ] 6.4 Implement `send`, `recv`, `read_until` delegating to the backend
        and the normalization helper.
  - [ ] 6.5 Implement `__enter__` / `__exit__` to call connect/close.
  - [ ] 6.6 Implement the `is_connected` and `transport_kind` properties.
  - [ ] 6.7 Add Sphinx-style `:param:` / `:returns:` / `:raises:` docstrings
        on every public method per the repository coding standards.
  - References: Requirements 1, 2, 3, 4, 6, 7, 9.

- [ ] 7. Wire up line-ending normalization.
  - [ ] 7.1 Author a small `normalize(mode)` helper returning a
        `Callable[[bytes], bytes]`.
  - [ ] 7.2 Handle CR-split-across-chunks using a one-byte carry buffer in
        the closure.
  - [ ] 7.3 Add `test_normalization.py` covering every mode plus the split
        case.
  - References: Requirement 4.

- [ ] 8. Structured logging wiring.
  - [ ] 8.1 Use `get_uefi_logger("uefi.transport.serial")`.
  - [ ] 8.2 Emit the INFO/WARNING/DEBUG lines called out in the design.
  - [ ] 8.3 Assert in tests that no credential-like value is ever logged.
  - References: Requirement 10.

- [ ] 9. Author targeted action-pattern tests.
  - [ ] 9.1 `SINGLE_CALL` — one attempt, raise on failure.
  - [ ] 9.2 `SINGLE_CONNECTED_CALL` — no-op on already-connected.
  - [ ] 9.3 `DO_UNTIL_SUCCESS` — retry until deadline.
  - [ ] 9.4 `DO_UNTIL_CONNECTION_FAILURE` — liveness probe raises on break.
  - [ ] 9.5 `DO_WHILE_SUCCESS` — raises `SemanticError`.
  - References: Requirement 8, Requirement 2.

- [ ] 10. Integration tests.
  - [ ] 10.1 PTY-backed local-device round trip on POSIX.
  - [ ] 10.2 `socat`-backed `socket://` round trip (fixture spawns `socat`).
  - [ ] 10.3 Fault-injection: flapping connection / truncated reads /
        CR-only line endings.
  - [ ] 10.4 Gate under an env var for CI runs without hardware.
  - References: Requirements 1, 2, 4, 5.

- [ ] 11. Satisfy `IUefiTransport`.
  - [ ] 11.1 Add an `isinstance(UefiSerialTransport(...), IUefiTransport)`
        runtime-check test.
  - [ ] 11.2 Confirm `transport_kind == "serial"`.
  - References: Requirement 9.

- [ ] 12. Documentation.
  - [ ] 12.1 Author a short usage guide at
        `userguide/20-02-uefi-transport-serial.rst` covering local device
        setup, network concentrator setup, and the
        `line_ending_normalization` trade-offs.
  - [ ] 12.2 Cross-link from `AGENTS.md` Transports section.
  - References: Introduction.
