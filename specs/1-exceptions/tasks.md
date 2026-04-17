# Tasks — Exceptions

- [ ] 1. Create the package skeleton at
      `source/packages/automojo/protocols/uefi/errors/`.
  - [ ] 1.1 Add `__init__.py` that re-exports the public exception classes
        and the `SHELL_ERROR_OUTPUT_PREVIEW_LINES` constant.
  - [ ] 1.2 Author a module-level docstring in `__init__.py` stating the
        never-catch rule for `SemanticError`.
  - [ ] 1.3 Populate `__all__`.
  - References: Requirement 8, Requirement 10.

- [ ] 2. Author `UefiError` in `uefiexceptions.py`.
  - [ ] 2.1 Subclass the shared root exception from
        `automojo.errors.exceptions`.
  - [ ] 2.2 Accept `message: str` and `details: Optional[list[str]] = None`.
  - [ ] 2.3 Copy the incoming `details` list into a new list before storing.
  - [ ] 2.4 Raise `TypeError` when `details` is not a list and not `None`.
  - [ ] 2.5 Implement `__str__` that joins message + details with
        `os.linesep`.
  - [ ] 2.6 Implement `as_dict()` returning
        `{"type", "message", "details"}`.
  - References: Requirement 1, Requirement 2, Requirement 6, Requirement 7.

- [ ] 3. Author `UefiShellError`.
  - [ ] 3.1 Subclass `UefiError`.
  - [ ] 3.2 Accept `command`, `status`, `stdout`, `stderr` kwargs (all
        default to `None`).
  - [ ] 3.3 Validate `status` is `int` or `None`; raise `TypeError`
        otherwise.
  - [ ] 3.4 Override `__str__` to include `COMMAND`, `STATUS`, trimmed
        `STDOUT`, and trimmed `STDERR` blocks using `_preview`.
  - [ ] 3.5 Extend `as_dict()` to include the four context fields.
  - References: Requirement 3.

- [ ] 4. Author `UefiVariableError`.
  - [ ] 4.1 Subclass `UefiError`.
  - [ ] 4.2 Accept `name`, `vendor_guid`, `attributes`, `efi_status` kwargs.
  - [ ] 4.3 Validate `vendor_guid` is `uuid.UUID` or `None`; raise
        `TypeError` otherwise.
  - [ ] 4.4 Validate `efi_status` is `int` or `None`; raise `TypeError`
        otherwise.
  - [ ] 4.5 Extend `as_dict()` to render `vendor_guid` as a canonical string.
  - References: Requirement 4.

- [ ] 5. Author the four flat subclasses: `UefiCapsuleError`,
      `UefiSecureBootError`, `UefiTransportError`, `UefiProtocolError`.
  - [ ] 5.1 Each subclass `UefiError` with no additional fields.
  - [ ] 5.2 Each carries a one-line Sphinx-style docstring describing its
        domain.
  - References: Requirement 5.

- [ ] 6. Author the `_preview` helper and
      `SHELL_ERROR_OUTPUT_PREVIEW_LINES` constant.
  - [ ] 6.1 Default `SHELL_ERROR_OUTPUT_PREVIEW_LINES` to `40`.
  - [ ] 6.2 Implement `_preview(text)` to trim to the configured line count
        and append a truncation marker when longer.
  - References: Requirement 3.4.

- [ ] 7. Author unit tests in `test_uefiexceptions.py`.
  - [ ] 7.1 Assert every concrete class is a subclass of `UefiError`.
  - [ ] 7.2 Cover `UefiError` with and without `details`.
  - [ ] 7.3 Cover `UefiError(details=<non-list>)` raises `TypeError`.
  - [ ] 7.4 Cover `details` deep-copy: mutate caller list, verify exception
        unchanged.
  - [ ] 7.5 Cover `UefiShellError` attribute defaults.
  - [ ] 7.6 Cover `UefiShellError(status=<non-int>)` raises `TypeError`.
  - [ ] 7.7 Cover `UefiShellError.__str__` includes command/status/stdout/stderr
        when set.
  - [ ] 7.8 Cover `UefiVariableError` with UUID and non-UUID `vendor_guid`.
  - [ ] 7.9 Cover `UefiVariableError.as_dict()` renders `vendor_guid` as
        canonical string.
  - [ ] 7.10 Cover each of the four flat subclasses constructing and
        round-tripping via `as_dict()`.
  - References: Requirement 1, Requirement 2, Requirement 3, Requirement 4,
        Requirement 5, Requirement 6, Requirement 7.

- [ ] 8. Author `test_uefiexceptions_pickle.py`.
  - [ ] 8.1 For each concrete class, pickle an instance with representative
        context and unpickle it.
  - [ ] 8.2 Assert `str(original) == str(unpickled)`.
  - [ ] 8.3 Assert `original.as_dict() == unpickled.as_dict()`.
  - References: Requirement 9.

- [ ] 9. Author `test_semantic_error_rule.py`.
  - [ ] 9.1 Import `automojo.protocols.uefi.errors.uefiexceptions`.
  - [ ] 9.2 Iterate every class in `__all__`.
  - [ ] 9.3 Assert none is a subclass of `SemanticError` from the shared
        module.
  - References: Requirement 8.

- [ ] 10. Coverage gate.
  - [ ] 10.1 Enforce 100% line coverage for
        `automojo.protocols.uefi.errors`.
  - [ ] 10.2 Add a CI step that fails on any coverage regression.
  - References: Testing Strategy.

- [ ] 11. Documentation.
  - [ ] 11.1 Author `userguide/20-03-uefi-exceptions.rst` listing each
        class, its typed fields, and a short example of structured logging
        via `as_dict()`.
  - [ ] 11.2 Document the never-catch rule for `SemanticError` in the same
        guide.
  - [ ] 11.3 Cross-link from `AGENTS.md` Features section to the guide.
  - References: Requirement 8, Requirement 11.
