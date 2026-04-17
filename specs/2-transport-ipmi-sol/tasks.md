# Tasks — IPMI-SOL Transport

- [ ] 1. Create package skeleton at
      `source/packages/automojo/protocols/uefi/transport/ipmisol/`.
  - [ ] 1.1 Add `__init__.py` re-exporting `UefiIpmiSolTransport`.
  - [ ] 1.2 Add a module-level docstring naming the composition
        relationship with `2-transport-serial` and the pre-boot capability.
  - [ ] 1.3 Add the package to the dependency-direction test so it cannot
        import from layers 3+.
  - References: Requirement 6.

- [ ] 2. Implement `cmdredact.py`.
  - [ ] 2.1 Author `redact_argv(argv)` covering `-P`, `--password=...`,
        and trailing `-P` with no value.
  - [ ] 2.2 Add `test_cmdredact.py` covering every redaction case and
        non-credential passthrough.
  - References: Requirement 8.3, Requirement 11.6.

- [ ] 3. Implement `_SolBackend` ABC.
  - [ ] 3.1 Author the ABC in `ipmisoltransport.py` (or an adjacent
        `_backend.py`) with `activate`, `deactivate`, `read`, `write`,
        `is_open`.
  - [ ] 3.2 Add interface conformance tests that both concrete backends
        must satisfy.
  - References: Requirement 1.

- [ ] 4. Implement `ipmitool_backend.py`.
  - [ ] 4.1 Author argv assembly honoring `interface`, `bmc_host`,
        `username`, `password`.
  - [ ] 4.2 Spawn subprocess for `sol activate`; pump stdout/stderr.
  - [ ] 4.3 Implement `deactivate()` via a separate `ipmitool sol
        deactivate` invocation.
  - [ ] 4.4 Raise `UefiTransportError` on non-zero exit with argv (after
        `redact_argv`), stdout, and stderr in `details`.
  - [ ] 4.5 Detect `ipmitool` not on PATH and raise `UefiTransportError`
        with the searched PATH.
  - [ ] 4.6 Add `test_ipmitool_backend.py` using mocked `subprocess.Popen`.
  - References: Requirement 1.2, Requirement 1.5, Requirement 8.1.

- [ ] 5. Implement `pyghmi_backend.py`.
  - [ ] 5.1 Author `PyghmiSolBackend` with a thread-safe inbound byte
        queue fed by `pyghmi`'s callback.
  - [ ] 5.2 Wrap every `pyghmi` exception in `UefiTransportError` with
        `from`.
  - [ ] 5.3 Add `test_pyghmi_backend.py` with mocked session.
  - References: Requirement 1.1, Requirement 8.2.

- [ ] 6. Implement `solactivation.py`.
  - [ ] 6.1 Author `ExistingSessionInfo` frozen dataclass.
  - [ ] 6.2 Author `probe_existing_session(backend_or_argv, logger)`
        parsing `sol info` output for user / source IP.
  - [ ] 6.3 Author `force_deactivate(backend_or_argv, logger)` raising
        `UefiTransportError` on "Deactivation failed".
  - [ ] 6.4 Define `DEACTIVATION_FAILED_SENTINEL`.
  - [ ] 6.5 Add `test_solactivation.py`.
  - References: Requirement 2.

- [ ] 7. Implement `UefiIpmiSolTransport` in `ipmisoltransport.py`.
  - [ ] 7.1 Author the constructor — initialize every instance variable per
        the coding standards; reject both / neither of `credential` /
        `ipmi_session`.
  - [ ] 7.2 Implement `connect()` honoring every `ActionPattern` value and
        raising `SemanticError` on `DO_WHILE_SUCCESS`.
  - [ ] 7.3 Call `probe_existing_session` and respect `evict_existing`.
  - [ ] 7.4 Treat "Deactivation failed" as non-retryable and surface
        immediately.
  - [ ] 7.5 Implement `disconnect()` with bounded wait
        (`completion_timeout` / `completion_interval`) for the
        deactivation ack; suppress failures with `WARNING` log.
  - [ ] 7.6 Implement `send` / `recv` / `read_until` by composing over
        `2-transport-serial.readuntil` and the `normalize(mode)` factory.
  - [ ] 7.7 Implement `is_connected` and `transport_kind` properties.
  - [ ] 7.8 Implement `__enter__` / `__exit__`.
  - [ ] 7.9 Add Sphinx-style `:param:` / `:returns:` / `:raises:`
        docstrings on every public method per the repository coding
        standards.
  - References: Requirements 1, 2, 3, 4, 5, 6, 7, 9.

- [ ] 8. Wire up line-ending normalization via the serial transport.
  - [ ] 8.1 Import `normalize(mode)` from
        `automojo.protocols.uefi.transport.serial`.
  - [ ] 8.2 Apply to inbound bytes only; outbound is literal.
  - [ ] 8.3 Reuse `2-transport-serial`'s normalization tests as a shared
        fixture.
  - References: Requirement 5.

- [ ] 9. Structured logging wiring.
  - [ ] 9.1 Use `get_uefi_logger("uefi.transport.ipmisol")`.
  - [ ] 9.2 Emit the INFO/WARNING/ERROR/DEBUG lines called out in the
        design.
  - [ ] 9.3 Always pass `ipmitool` argv through `redact_argv` before
        logging.
  - [ ] 9.4 Assert in tests that no credential-like value is ever logged.
  - References: Requirement 11.

- [ ] 10. Author targeted action-pattern tests.
  - [ ] 10.1 `SINGLE_CALL` — one activation, raise on failure.
  - [ ] 10.2 `SINGLE_CONNECTED_CALL` — no-op on already-connected.
  - [ ] 10.3 `DO_UNTIL_SUCCESS` — retry until deadline.
  - [ ] 10.4 `DO_UNTIL_CONNECTION_FAILURE` — liveness probe raises on
        break.
  - [ ] 10.5 `DO_WHILE_SUCCESS` — raises `SemanticError`.
  - References: Requirement 10.

- [ ] 11. Integration tests.
  - [ ] 11.1 Gate under `AUTOMOJO_IPMI_HOST` env var.
  - [ ] 11.2 Round-trip: activate → send `\r\n` → read prompt →
        deactivate.
  - [ ] 11.3 Eviction race: spawn two transports; second with
        `evict_existing=True` wins.
  - [ ] 11.4 "Deactivation failed" injection using a fake `ipmitool`
        wrapper on `PATH`.
  - References: Requirements 2, 3, 7, 8.

- [ ] 12. Satisfy `IUefiTransport` + documentation.
  - [ ] 12.1 Add an `isinstance(UefiIpmiSolTransport(...), IUefiTransport)`
        runtime-check test.
  - [ ] 12.2 Confirm `transport_kind == "ipmi-sol"`.
  - [ ] 12.3 Author a short usage guide at
        `userguide/20-05-uefi-transport-ipmi-sol.rst` covering pyghmi vs.
        ipmitool, `evict_existing`, and the `Deactivation failed` triage
        path.
  - [ ] 12.4 Cross-link from `AGENTS.md` Transports section.
  - References: Requirement 6, Introduction.
