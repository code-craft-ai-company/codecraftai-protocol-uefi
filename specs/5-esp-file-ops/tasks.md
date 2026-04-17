# Tasks — ESP File Operations

- [ ] 1. Create package skeleton at
      `source/packages/automojo/protocols/uefi/esp/`.
  - [ ] 1.1 Add `__init__.py` re-exporting `EspFileProvider`,
        `EspDirEntry`, `EspDirTree`, `EspFileAttributes`.
  - [ ] 1.2 Add `backends/__init__.py` re-exporting
        `AbstractEspFileBackend`, `ShellEspBackend`, `SshHostEspBackend`,
        `RedfishEspBackend`.
  - [ ] 1.3 Add a module-level docstring describing the facade contract.
  - References: Requirement 1.

- [ ] 2. Implement `paths.py`.
  - [ ] 2.1 Author `normalize_uefi_path(path, default_alias="FS0:")`.
  - [ ] 2.2 Author `split_uefi_path(path)` returning `(alias, segments)`.
  - [ ] 2.3 Author `uefi_to_posix(uefipath, esp_mount)` and
        `posix_to_uefi(posixpath, esp_mount, map_alias="FS0:")`.
  - [ ] 2.4 Reject `..` segments after expansion with `UefiError`.
  - [ ] 2.5 Add `test_paths.py` with a fixtures table covering the
        round-trip cases named in the Testing Strategy.
  - References: Requirement 2, Requirement 3.

- [ ] 3. Implement `models.py`.
  - [ ] 3.1 Author `EspFileAttributes`, `EspDirEntry`, `EspDirTree`
        dataclasses as `frozen=True`.
  - [ ] 3.2 Author `EspFileAttributes.from_int(raw: int)` bit decoder per
        the §13.4 layout table.
  - [ ] 3.3 Add `test_models.py` covering each bit combination.
  - References: Requirement 6.

- [ ] 4. Implement `AbstractEspFileBackend` in `backends/base.py`.
  - [ ] 4.1 Define the six abstract methods and `normalize`.
  - [ ] 4.2 Raise `AbstractMethodError` from each method body.
  - [ ] 4.3 Document each method with Sphinx-style docstrings.
  - References: Requirement 1.

- [ ] 5. Implement `ShellEspBackend` in `backends/shell.py`.
  - [ ] 5.1 Implement `normalize` calling `normalize_uefi_path`.
  - [ ] 5.2 Implement `file_push` via UEFI Shell `cp`, issuing the upload
        through the transport-appropriate staging path.
  - [ ] 5.3 Implement `file_pull` via UEFI Shell `cp` and capture the
        content back through the client.
  - [ ] 5.4 Implement `file_exists`, `directory_exists`, `directory`, and
        `directory_tree` by parsing `ls -sfo` output.
  - [ ] 5.5 Implement UEFI Shell quoting rules in a private helper.
  - [ ] 5.6 Translate "Access Denied" shell replies into `UefiError`.
  - [ ] 5.7 Add `test_shell_backend.py` with captured transcripts.
  - References: Requirement 1, Requirement 2, Requirement 6, Requirement 12.

- [ ] 6. Implement `SshHostEspBackend` in `backends/sshhost.py`.
  - [ ] 6.1 Implement ESP mount probe using `/proc/mounts` and the GPT
        type-GUID filter.
  - [ ] 6.2 Implement `normalize` calling `uefi_to_posix` with the
        probed mount.
  - [ ] 6.3 Delegate each operation to the composed `SshAgent` file API.
  - [ ] 6.4 Translate `EACCES` / `ENOENT` into `UefiError` with `details`.
  - [ ] 6.5 Add `test_sshhost_backend.py` using mocked `SshAgent`.
  - References: Requirement 1, Requirement 3, Requirement 12.

- [ ] 7. Implement `RedfishEspBackend` in `backends/redfish.py`.
  - [ ] 7.1 Implement `file_push` via VirtualMedia `InsertMedia` multipart
        POST.
  - [ ] 7.2 Implement `file_pull` via the VirtualMedia image URI.
  - [ ] 7.3 Implement TaskMonitor polling per `aspects.completion_interval`.
  - [ ] 7.4 Implement `file_exists` / `directory_exists` by GET on the
        VirtualMedia resource.
  - [ ] 7.5 Translate `403 Forbidden` responses into `UefiError`.
  - [ ] 7.6 Add `test_redfish_backend.py` using a `responses`-library fake
        BMC.
  - References: Requirement 1, Requirement 5, Requirement 12.

- [ ] 8. Implement chunked-stream layer in `provider.py`.
  - [ ] 8.1 Author `_stream_push(source, sink, chunk_size)` that iterates
        64 KiB (or caller-specified) chunks without loading the whole file.
  - [ ] 8.2 Validate `chunk_size ∈ [512, 16_777_216]`.
  - [ ] 8.3 Validate byte-length equality after transfer.
  - [ ] 8.4 Add `test_large_file_stream.py` that pushes a 64 MiB random file
        across each backend and verifies SHA-256 equality.
  - References: Requirement 4.

- [ ] 9. Implement `EspFileProvider` in `provider.py`.
  - [ ] 9.1 Author `__init__` that routes to the correct backend via the
        `client.transport_kind` discriminator.
  - [ ] 9.2 Implement `_run_with_pattern(aspects, operation)` dispatcher
        that honors `SINGLE_CALL`, `SINGLE_CONNECTED_CALL`, and
        `DO_UNTIL_SUCCESS`; raises `SemanticError` on other patterns.
  - [ ] 9.3 Implement each public method per the design signatures.
  - [ ] 9.4 Emit structured log lines at `uefi.esp.*` per `logging_pattern`.
  - References: Requirements 7, 8, 9, 10, 11.

- [ ] 10. Author action-pattern tests.
  - [ ] 10.1 `test_single_call.py` — exactly one backend invocation.
  - [ ] 10.2 `test_single_connected_call.py` — connectivity verified first,
        failure raises `ConnectionError`.
  - [ ] 10.3 `test_do_until_success.py` — retries on allowed exceptions,
        fails on non-allowed, observes `completion_timeout`.
  - [ ] 10.4 `test_unsupported_patterns.py` — `DO_WHILE_SUCCESS` and
        `DO_UNTIL_CONNECTION_FAILURE` raise `SemanticError`.
  - References: Requirement 7, Requirement 8, Requirement 9.

- [ ] 11. Author aspects-none passthrough tests.
  - [ ] 11.1 `test_aspects_none.py` — `aspects=None` substitutes the
        instance default across every public method.
  - [ ] 11.2 `test_aspects_none_no_default.py` — missing instance default
        raises `ConfigurationError`.
  - References: Requirement 10.

- [ ] 12. Author file-security tests.
  - [ ] 12.1 Shell backend "Access Denied" translation.
  - [ ] 12.2 SSH-to-host `EACCES` translation.
  - [ ] 12.3 Redfish `403 Forbidden` translation.
  - References: Requirement 12.

- [ ] 13. Author documentation.
  - [ ] 13.1 Usage guide at
        `userguide/50-01-uefi-esp-file-ops.rst` with examples for each
        transport.
  - [ ] 13.2 Cross-link from `AGENTS.md` Features section.
  - References: Introduction.

- [ ] 14. Coverage gate.
  - [ ] 14.1 Configure pytest-cov to require 95% line coverage for the
        `automojo.protocols.uefi.esp` package.
  - [ ] 14.2 Add CI step that fails on drop below 95%.
  - References: Testing Strategy.
