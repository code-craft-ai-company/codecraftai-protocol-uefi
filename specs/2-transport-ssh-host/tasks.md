# Tasks — SSH-to-Host Transport

- [ ] 1. Create package skeleton at
      `source/packages/automojo/protocols/uefi/transport/sshhost/`.
  - [ ] 1.1 Add `__init__.py` re-exporting `UefiSshHostTransport` and
        `HostCmdResult`.
  - [ ] 1.2 Add a module-level docstring naming the composition-over-
        inheritance relationship with `SshAgent` and the post-boot-only
        limitation.
  - [ ] 1.3 Add the package to the dependency-direction test so it cannot
        import from layers 3+.
  - References: Requirement 1, Requirement 6.

- [ ] 2. Implement `result.py`.
  - [ ] 2.1 Author `HostCmdResult` frozen dataclass with `command`,
        `status`, `stdout`, `stderr`, `duration_s`, `was_root`.
  - [ ] 2.2 Add `test_result.py` covering construction and equality.
  - References: Requirement 7.1.

- [ ] 3. Implement `distrohint.py`.
  - [ ] 3.1 Author `DISTRO_INSTALL_TEMPLATES` and `CAPABILITY_PACKAGE`
        tables per the design.
  - [ ] 3.2 Author `fetch_distro_id(ssh_agent)` that reads
        `/etc/os-release` and extracts `ID`.
  - [ ] 3.3 Author `install_hint(distro_id, capability)` returning the
        formatted command.
  - [ ] 3.4 Add `test_distrohint.py` covering every mapped distro plus
        unknown / missing `ID`.
  - References: Requirement 8.

- [ ] 4. Implement `sudopolicy.py`.
  - [ ] 4.1 Author `SUDO_PROMPT_SENTINELS` constant.
  - [ ] 4.2 Author `apply_sudo(command, as_root, effective_user)` covering
        all four branches in the design.
  - [ ] 4.3 Author `detect_sudo_prompt(stderr)` returning bool.
  - [ ] 4.4 Add `test_sudopolicy.py` covering every branch of `apply_sudo`
        and each sentinel.
  - References: Requirement 4.

- [ ] 5. Implement `capabilityprobe.py`.
  - [ ] 5.1 Author `PROBE_COMMANDS` constant.
  - [ ] 5.2 Author `probe_capabilities(ssh_agent, aspects, logger) ->
        dict[str, bool]`.
  - [ ] 5.3 Log a `WARNING` when a probe fails with a non-absence error.
  - [ ] 5.4 Add `test_capabilityprobe.py` covering: all-present, one-
        missing, probe-raises-unrelated-exception.
  - References: Requirement 2.

- [ ] 6. Implement `efivarfsio.py`.
  - [ ] 6.1 Author `read_efivar(transport, name, vendor_guid, aspects)`.
  - [ ] 6.2 Author `write_efivar(transport, name, vendor_guid, payload,
        aspects)` including `chattr -i` clearing and base64-framed `tee`.
  - [ ] 6.3 `shlex.quote` every path and payload fragment.
  - [ ] 6.4 Raise `UefiVariableError` with populated `name` / `vendor_guid`
        / `stderr` on failure.
  - [ ] 6.5 Add `test_efivarfsio.py` covering round-trip and failure cases
        using a mocked transport.
  - References: Requirement 7.4, Requirement 7.5.

- [ ] 7. Implement `UefiSshHostTransport` in `sshhosttransport.py`.
  - [ ] 7.1 Author the constructor — initialize every instance variable per
        the coding standards; reject non-`SshAgent`.
  - [ ] 7.2 Implement `connect()` honoring every `ActionPattern` value and
        raising `SemanticError` on `DO_WHILE_SUCCESS`.
  - [ ] 7.3 Run capability probing + `fetch_distro_id` + `id -un` at
        `connect()` and populate `.capabilities`, `._distro_hint`,
        `._effective_user`.
  - [ ] 7.4 Implement `require(capability_name)` with distro-aware hint.
  - [ ] 7.5 Implement `run_cmd(command, aspects, as_root)` with sudo
        policy + prompt detection.
  - [ ] 7.6 Implement `file_pull` / `file_push` as pass-throughs.
  - [ ] 7.7 Implement `read_efivar` / `write_efivar` delegating to
        `efivarfsio.py`.
  - [ ] 7.8 Raise `SemanticError` on `send`, `recv`, `read_until`.
  - [ ] 7.9 Implement `capabilities`, `is_connected`, `transport_kind`
        properties.
  - [ ] 7.10 Implement `disconnect()` — does NOT close the supplied
        `SshAgent`.
  - [ ] 7.11 Add Sphinx-style `:param:` / `:returns:` / `:raises:`
        docstrings on every public method per the repository coding
        standards.
  - References: Requirements 1, 3, 4, 5, 6, 7, 9.

- [ ] 8. Structured logging wiring.
  - [ ] 8.1 Use `get_uefi_logger("uefi.transport.sshhost")`.
  - [ ] 8.2 Emit the INFO/WARNING/ERROR/DEBUG lines called out in the
        design.
  - [ ] 8.3 Redact credential-shaped tokens from logged commands (regex
        for `Bearer `, `password=`, `--password`).
  - [ ] 8.4 Assert in tests that no credential-like value is ever logged.
  - References: Requirement 11.

- [ ] 9. Author targeted action-pattern tests.
  - [ ] 9.1 `SINGLE_CALL` — one probe, raise on failure.
  - [ ] 9.2 `SINGLE_CONNECTED_CALL` — skip re-probe when already connected.
  - [ ] 9.3 `DO_UNTIL_SUCCESS` — retry until deadline.
  - [ ] 9.4 `DO_UNTIL_CONNECTION_FAILURE` — liveness probe raises on break.
  - [ ] 9.5 `DO_WHILE_SUCCESS` — raises `SemanticError`.
  - References: Requirement 10.

- [ ] 10. Integration tests against a disposable Linux VM.
  - [ ] 10.1 Fixture: ephemeral VM (libvirt / cloud-init) with an
        `AUTOMOJO_SSH_HOST_FIXTURE` env var gate.
  - [ ] 10.2 Round-trip: connect → probe → `efibootmgr -v` parse →
        disconnect.
  - [ ] 10.3 Sudo-prompt fixture: sudoers misconfigured to require a
        password; transport raises `ConfigurationError`.
  - [ ] 10.4 `write_efivar` round-trip against a non-critical variable on a
        disposable VM.
  - References: Requirements 2, 4, 7.

- [ ] 11. Satisfy `IUefiTransport`.
  - [ ] 11.1 Add an `isinstance(UefiSshHostTransport(...), IUefiTransport)`
        runtime-check test.
  - [ ] 11.2 Confirm `transport_kind == "ssh-host"`.
  - [ ] 11.3 Confirm `send` / `recv` / `read_until` raise `SemanticError`.
  - References: Requirement 6.

- [ ] 12. Documentation.
  - [ ] 12.1 Author a short usage guide at
        `userguide/20-04-uefi-transport-ssh-host.rst` covering how to reuse
        an `SshAgent`, capability probing, and the sudo policy.
  - [ ] 12.2 Cross-link from `AGENTS.md` Transports section.
  - References: Introduction.
