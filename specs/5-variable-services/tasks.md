# Tasks — Variable Services

- [ ] 1. Create package skeleton at
      `source/packages/ccai/protocols/uefi/variables/`.
  - [ ] 1.1 Add `__init__.py` re-exporting `VariableStore`,
        `VariableAttributes`, `VariableRecord`, `VariableWriteReceipt`,
        `QueryVariableInfoResult`, `SignerKeyPair`,
        `EfiVariableAuthentication2Descriptor`.
  - [ ] 1.2 Add a module-level docstring describing the facade contract
        and the three backends.
  - [ ] 1.3 Create `backends/` sub-package with `__init__.py`.
  - References: Requirement 9.

- [ ] 2. Implement `attributes.py`.
  - [ ] 2.1 Author `VariableAttributes(IntFlag)` with all eight members
        at the UEFI §8.2 bit values.
  - [ ] 2.2 Author `from_bytes(buf)` / `to_bytes()` helpers.
  - [ ] 2.3 Author `validate()` covering cross-field rules:
        RUNTIME_ACCESS requires BOOTSERVICE_ACCESS; legacy and
        time-based auth-write are mutually exclusive; enhanced-auth
        cannot combine with legacy auth flags.
  - [ ] 2.4 Add `test_attributes.py` round-tripping every value
        0x00..0xFF and exercising every forbidden combination.
  - References: Requirement 3, Requirement 10.

- [ ] 3. Implement `records.py`.
  - [ ] 3.1 Author frozen dataclasses `VariableRecord`,
        `VariableWriteReceipt`, `QueryVariableInfoResult`.
  - [ ] 3.2 Add equality and `__repr__` tests; verify field types via
        `dataclasses.fields`.
  - References: Requirement 4.1, Requirement 5.1, Requirement 8.1.

- [ ] 4. Implement `auth_descriptor.py`.
  - [ ] 4.1 Author `SignerKeyPair` and
        `EfiVariableAuthentication2Descriptor` frozen dataclasses.
  - [ ] 4.2 Add timestamp-timezone-awareness validator.
  - [ ] 4.3 Add tests for naive-`datetime` rejection.
  - References: Requirement 6, Requirement 11.

- [ ] 5. Implement `auth_builder.py`.
  - [ ] 5.1 Author `build_authentication2_descriptor(name, guid,
        attributes, data, signer)` per UEFI §8.2.6.
  - [ ] 5.2 Assemble the signed-digest input in exact byte order (name
        UCS-2 no null || guid || attributes || timestamp || data).
  - [ ] 5.3 Produce DER PKCS#7 SignedData via
        `cryptography.hazmat.primitives.serialization.pkcs7`.
  - [ ] 5.4 Wrap in `WIN_CERTIFICATE_UEFI_GUID` per §31.2.
  - [ ] 5.5 Add a byte-for-byte test vector validated against EDK2
        `Pkcs7Sign` reference output.
  - [ ] 5.6 Add tests for `EFI_TIME` timezone encoding edge cases
        (UTC, +05:30, -08:00).
  - References: Requirement 6, Requirement 11.

- [ ] 6. Implement `backends/abstract.py`.
  - [ ] 6.1 Author `IVariableBackend` Protocol with `get`, `set`,
        `next`, `query` methods.
  - [ ] 6.2 Add `test_abstract.py` verifying that the three concrete
        backends all satisfy `isinstance(backend, IVariableBackend)`
        at runtime.
  - References: Requirement 12.

- [ ] 7. Implement `backends/shell_backend.py`.
  - [ ] 7.1 Author `ShellVariableBackend(agent)` consuming
        5-shell-commands wrappers.
  - [ ] 7.2 Implement `get` via `shell_dmpstore(name, guid)` parsing.
  - [ ] 7.3 Implement `set` via `shell_setvar` with full attribute flag
        emission.
  - [ ] 7.4 Implement `next` by iterating `shell_dmpstore` output in
        sorted `(guid, name)` order.
  - [ ] 7.5 Implement `query` by falling back to
        `UefiTransportError` when Shell does not expose
        `QueryVariableInfo`.
  - [ ] 7.6 Add round-trip tests covering every
        non-authenticated-write attribute combination.
  - References: Requirement 4, Requirement 5, Requirement 7,
    Requirement 12.

- [ ] 8. Implement `backends/host_backend.py`.
  - [ ] 8.1 Author `HostOsVariableBackend(agent)` that calls `efivar`
        where available and falls back to reading
        `/sys/firmware/efi/efivars/<name>-<guid>` directly.
  - [ ] 8.2 Implement `chattr -i` / `chattr +i` bracketing around
        writes to the immutable-by-default efivarfs entries.
  - [ ] 8.3 Implement authenticated write via the same descriptor
        builder the shell backend uses.
  - [ ] 8.4 Implement `query` via `efivar --query-variable-info` when
        available, raising `UefiTransportError` otherwise.
  - [ ] 8.5 Add integration tests gated by a `CCAI_UEFI_HOST`
        env-var pointing at a disposable Linux VM.
  - References: Requirement 4, Requirement 5, Requirement 7,
    Requirement 12.

- [ ] 9. Implement `backends/redfish_backend.py`.
  - [ ] 9.1 Author `RedfishVariableBackend(agent)` mapping
        `get`/`set` onto `/Systems/{id}/Bios` attribute reads and
        `/Systems/{id}/Bios/Settings` writes.
  - [ ] 9.2 Implement Task polling for `202 Accepted` responses
        honoring `aspects.completion_timeout` /
        `aspects.completion_interval`.
  - [ ] 9.3 Implement `next` by enumerating the `Bios.Attributes`
        map in sorted order.
  - [ ] 9.4 Implement `query` by consulting `Bios` resource metadata
        when the `Oem` block exposes storage stats; otherwise raise
        `UefiTransportError`.
  - [ ] 9.5 Implement refusal-to-assemble-raw-auth-descriptor path
        that raises `UefiTransportError` with explanatory `details`.
  - [ ] 9.6 Add tests using the fake Redfish server from
        2-transport-redfish.
  - References: Requirement 5.5, Requirement 6, Requirement 8.3,
    Requirement 12.2.

- [ ] 10. Implement `store.py`.
  - [ ] 10.1 Author `VariableStore(agent, backend_override, aspects)`.
  - [ ] 10.2 Implement `_select_backend(agent, override)` dispatching
        on `agent.transport_kind`.
  - [ ] 10.3 Implement `get_variable`, `set_variable`, `iter_variables`,
        `query_variable_info` per the design.
  - [ ] 10.4 Implement the `auth` parameter branching for
        `set_variable`: raw descriptor vs. `SignerKeyPair`.
  - [ ] 10.5 Implement glob-based name filter in `iter_variables`.
  - [ ] 10.6 Implement cursor cleanup on generator close.
  - [ ] 10.7 Emit `uefi.variable` channel logs per the design.
  - References: Requirement 1, Requirement 2, Requirement 4–9,
    Requirement 13.

- [ ] 11. Action-pattern targeted tests.
  - [ ] 11.1 Add `test_action_patterns.py` using a scripted fake
        backend.
  - [ ] 11.2 Verify `SINGLE_CALL` for each read and write op.
  - [ ] 11.3 Verify `SINGLE_CONNECTED_CALL` open/close pair.
  - [ ] 11.4 Verify `DO_UNTIL_SUCCESS` converges within
        `completion_timeout`.
  - [ ] 11.5 Verify `DO_WHILE_SUCCESS` raises `SemanticError` on
        `set_variable` and works for `query_variable_info`.
  - [ ] 11.6 Verify `DO_UNTIL_CONNECTION_FAILURE` raises
        `SemanticError`.
  - [ ] 11.7 Verify `aspects=None` resolves from store default.
  - References: Requirement 1, Requirement 2.

- [ ] 12. Contract-parity suite.
  - [ ] 12.1 Author `test_backend_contract.py` parametrized over each
        backend (with its fixtures) exercising the read-after-write,
        delete-then-read, enumerate-then-read, and query flows.
  - [ ] 12.2 Verify `iter_variables` yields sorted `(guid, name)` on
        every backend.
  - References: Requirement 12.

- [ ] 13. Auth-write integration tests.
  - [ ] 13.1 Generate a self-signed test PK in a `conftest.py` fixture.
  - [ ] 13.2 Verify write-then-read round-trip for PK with
        `TIME_BASED_AUTHENTICATED_WRITE_ACCESS`.
  - [ ] 13.3 Verify non-monotonic timestamp raises
        `UefiVariableError(EFI_SECURITY_VIOLATION)`.
  - [ ] 13.4 Verify `auth=None` + auth-flag-set raises
        `ConfigurationError`.
  - [ ] 13.5 Verify `auth` provided + auth-flag-absent raises
        `ConfigurationError`.
  - References: Requirement 6.

- [ ] 14. Documentation.
  - [ ] 14.1 Author a usage guide at
        `userguide/50-02-variable-services.rst` showing CRUD for
        non-auth and time-based-auth variables across each transport.
  - [ ] 14.2 Cross-link from `AGENTS.md` Features section.
  - References: Introduction.
