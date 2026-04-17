# Tasks — Interfaces

- [ ] 1. Create the package skeleton at
      `source/packages/automojo/protocols/uefi/interfaces/`.
  - [ ] 1.1 Add `__init__.py` that re-exports every protocol class and
        the `IMPLEMENTOR_COMPLIANCE_REGISTRY`.
  - [ ] 1.2 Populate `__all__`.
  - [ ] 1.3 Author a module-level docstring summarizing the
        structural-typing convention.
  - References: Requirement 11.

- [ ] 2. Author `IUefiTransport` in `uefiinterfaces.py`.
  - [ ] 2.1 Decorate with `@runtime_checkable`.
  - [ ] 2.2 Declare the `connected` property, `connect`, `disconnect`,
        `send`, `recv` methods with full type hints.
  - [ ] 2.3 Add Sphinx-style docstrings documenting semantics for each
        method, including the `recv(timeout=0)` non-blocking-poll
        contract.
  - References: Requirement 1, Requirement 6, Requirement 7,
        Requirement 8.

- [ ] 3. Author `IUefiShellRunner`.
  - [ ] 3.1 Decorate with `@runtime_checkable`.
  - [ ] 3.2 Declare `run_cmd(command, aspects=None)` with quoted forward
        references to `UefiCmdAspects` and `UefiShellResult`.
  - [ ] 3.3 Document the `aspects=None` "use instance default" semantic.
  - References: Requirement 2, Requirement 6, Requirement 7.

- [ ] 4. Author `IUefiVariableProvider`.
  - [ ] 4.1 Decorate with `@runtime_checkable`.
  - [ ] 4.2 Declare `get_variable`, `set_variable`,
        `enumerate_variables` with quoted forward references.
  - [ ] 4.3 Document the streaming-iterator contract of
        `enumerate_variables`.
  - [ ] 4.4 Document `UefiVariableError` as the raised exception type.
  - References: Requirement 3, Requirement 6, Requirement 7.

- [ ] 5. Author `IUefiFileProvider`.
  - [ ] 5.1 Decorate with `@runtime_checkable`.
  - [ ] 5.2 Declare the six file/directory methods with full type hints
        and `aspects=None` parameters.
  - [ ] 5.3 Document the UEFI Shell vs. POSIX path convention.
  - References: Requirement 4, Requirement 6, Requirement 7.

- [ ] 6. Author `IUefiSystemContext`.
  - [ ] 6.1 Import `ISystemContext` from the shared
        `automojo.interop.isystemcontext` module.
  - [ ] 6.2 Declare `IUefiSystemContext(ISystemContext, Protocol)` and
        decorate with `@runtime_checkable`.
  - [ ] 6.3 Declare the `firmware_vendor`, `firmware_revision`, and
        `secure_boot_state` properties with quoted forward references.
  - References: Requirement 5, Requirement 6, Requirement 7,
        Requirement 8.

- [ ] 7. Declare `IMPLEMENTOR_COMPLIANCE_REGISTRY`.
  - [ ] 7.1 Initialize as an empty `list[tuple[str, str]]`.
  - [ ] 7.2 Author a module-level comment explaining the (dotted path,
        protocol name) convention.
  - [ ] 7.3 Populate as concrete classes land in later features.
  - References: Requirement 9.

- [ ] 8. Author `test_uefiinterfaces.py`.
  - [ ] 8.1 Import each protocol and assert it is a `typing.Protocol`
        subclass.
  - [ ] 8.2 Assert each is decorated with `@runtime_checkable`.
  - [ ] 8.3 For each protocol, build an in-test stand-in class that
        implements every method and assert `isinstance` passes.
  - [ ] 8.4 For each protocol, build a stand-in class missing one
        required method and assert `isinstance` fails.
  - References: Requirement 1, Requirement 2, Requirement 3,
        Requirement 4, Requirement 5, Requirement 8.

- [ ] 9. Author `test_interface_compliance.py`.
  - [ ] 9.1 Iterate `IMPLEMENTOR_COMPLIANCE_REGISTRY`.
  - [ ] 9.2 For each entry, resolve the concrete dotted path and the
        protocol, then assert `isinstance`.
  - [ ] 9.3 Gracefully skip pairs whose concrete class requires
        non-fixture arguments by falling back to a class-level
        `hasattr`-based check.
  - [ ] 9.4 Print a clear failure message naming the missing methods
        when compliance fails.
  - References: Requirement 9.

- [ ] 10. Author `test_interfaces_no_io.py`.
  - [ ] 10.1 Parse `uefiinterfaces.py` with `ast`.
  - [ ] 10.2 Assert zero call expressions at module top level other
        than decorator applications.
  - [ ] 10.3 Snapshot `sys.modules` and assert no extraneous modules
        were loaded.
  - References: Requirement 10.

- [ ] 11. Coverage gate.
  - [ ] 11.1 Enforce 100% line coverage for
        `automojo.protocols.uefi.interfaces`.
  - [ ] 11.2 Add a CI step that fails on any coverage regression.
  - References: Testing Strategy.

- [ ] 12. Documentation.
  - [ ] 12.1 Author `userguide/20-07-uefi-interfaces.rst` listing each
        protocol, its method signatures, and a short example of how a
        concrete class opts in structurally.
  - [ ] 12.2 Document the compliance-registry convention.
  - [ ] 12.3 Cross-link from `AGENTS.md` Features section to the guide.
  - References: Introduction, Requirement 9.
