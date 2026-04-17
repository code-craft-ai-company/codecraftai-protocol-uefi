# Tasks — Aspects

- [ ] 1. Create package skeleton at
      `source/packages/automojo/protocols/uefi/aspects/`.
  - [ ] 1.1 Add `__init__.py` that re-exports the public surface:
        `Freezable`, `ActionPattern`, `LoggingPattern`, `UefiAspects`,
        `UefiCmdAspects`, `DEFAULT_ASPECTS`, `DEFAULT_CMD_ASPECTS`,
        `UNDER_CONSTRUCTION_ASPECTS`, `resolve_aspects`, and every
        `DEFAULT_*` numeric constant.
  - [ ] 1.2 Add a module-level docstring to `__init__.py` summarizing the
        measured-interop contract.
  - References: Requirement 9.1.

- [ ] 2. Implement `freezable.py`.
  - [ ] 2.1 Author the `Freezable` class per the design.
  - [ ] 2.2 Add a unit test file `test_freezable.py` under
        `source/testroots/automojo/protocols/uefi/aspects/` covering
        pre-freeze mutation, freeze transition, post-freeze mutation
        rejection, and subclass inheritance.
  - References: Requirement 1.

- [ ] 3. Implement `patterns.py`.
  - [ ] 3.1 Author `ActionPattern(IntEnum)` with exactly the five members
        named in the requirements.
  - [ ] 3.2 Author `LoggingPattern(IntEnum)` with exactly three members.
  - [ ] 3.3 Add `test_patterns.py` covering membership count, values, and
        pickle stability.
  - References: Requirement 2.1–2.7, Requirement 4.1.

- [ ] 4. Implement `aspects.py`.
  - [ ] 4.1 Author `UefiAspects(Freezable)` with the constructor signature
        in the design document.
  - [ ] 4.2 Implement all validation steps in the order specified.
  - [ ] 4.3 Implement `__eq__`, `__hash__`, `__repr__`.
  - [ ] 4.4 Implement `merge(overlay)`.
  - [ ] 4.5 Add Sphinx-style `:param:` / `:raises:` docstrings per the
        repository coding standards.
  - References: Requirements 1, 2, 3, 4, 5, 6, 7, 9.2, 10.

- [ ] 5. Implement `cmdaspects.py`.
  - [ ] 5.1 Author `UefiCmdAspects(UefiAspects)` with the additional
        `expected_status`, `user`, `prompt_pattern`, `response_encoding`,
        and `transport_kind` fields.
  - [ ] 5.2 Implement type coercion for `expected_status`.
  - [ ] 5.3 Implement codec validation for `response_encoding`.
  - [ ] 5.4 Implement transport-kind literal validation.
  - [ ] 5.5 Override `merge` so it returns `UefiCmdAspects` when one side
        is that subtype.
  - References: Requirement 8, Requirement 10.2.

- [ ] 6. Implement `defaults.py`.
  - [ ] 6.1 Define every `DEFAULT_*` numeric constant.
  - [ ] 6.2 Define `DEFAULT_PROMPT_PATTERN`.
  - [ ] 6.3 Build `DEFAULT_ASPECTS`, `DEFAULT_CMD_ASPECTS`, and
        `UNDER_CONSTRUCTION_ASPECTS` as module-level `Final` singletons.
  - [ ] 6.4 Add `test_defaults.py` verifying identity preservation on
        re-import.
  - References: Requirement 9.

- [ ] 7. Implement `resolver.py`.
  - [ ] 7.1 Author `resolve_aspects(passed, instance_default)` per the
        design.
  - [ ] 7.2 Add `test_resolver.py` covering the three branches.
  - References: Requirement 11.

- [ ] 8. Author integration tests.
  - [ ] 8.1 Round-trip: construct `UefiCmdAspects`, pickle, unpickle,
        verify equality.
  - [ ] 8.2 Thread-safety smoke test: share a frozen aspects across 16
        worker threads performing 1e5 field reads each, verify no
        corruption.
  - References: Requirement 1.1, Requirement 9.

- [ ] 9. Coverage gate.
  - [ ] 9.1 Configure pytest-cov to enforce 100% line coverage for the
        `automojo.protocols.uefi.aspects` package.
  - [ ] 9.2 Add a CI step that fails on drop below 100%.
  - References: Testing Strategy.

- [ ] 10. Documentation.
  - [ ] 10.1 Author a short usage guide at
        `userguide/20-01-uefi-aspects.rst` showing how to build a
        preset, how to `merge`, and how to pick an `ActionPattern`.
  - [ ] 10.2 Cross-link from `AGENTS.md` Features section to the usage
        guide.
  - References: Introduction.
