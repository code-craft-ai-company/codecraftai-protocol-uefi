# Tasks — HII Forms Navigation

- [ ] 1. Create package skeleton at
      `source/packages/automojo/protocols/uefi/hii/`.
  - [ ] 1.1 Add `__init__.py` exporting `HiiFormNavigator`,
        `ScreenShape`, `HiiFormItem`, `Screen`.
  - [ ] 1.2 Add module docstring stating the screen-scrape-only
        contract (Requirement 15).
  - References: Requirement 15.

- [ ] 2. Implement `keystrokes.py`.
  - [ ] 2.1 Define every named key byte-sequence constant per the
        design.
  - [ ] 2.2 Add a `test_keystrokes.py` verifying each constant's byte
        value against the ECMA-48 / VT100 reference.
  - References: Requirement 3, Requirement 7.

- [ ] 3. Implement `screen.py`.
  - [ ] 3.1 Author `Screen` frozen dataclass with fields `lines`,
        `cursor_row`, `cursor_col`, `raw_bytes`.
  - [ ] 3.2 Author `HiiFormItem` frozen dataclass with fields
        `label`, `value`, `row`, `is_submenu`, `enum_values`,
        `numeric_min`, `numeric_max`.
  - [ ] 3.3 Author `_ansi_render(raw)` — an ANSI state machine that
        produces `(lines, cursor)` from raw bytes, handling CSI cursor
        moves, SGR attributes, and screen clears.
  - [ ] 3.4 Author `_capture(agent, aspects)` using `read_until_idle`.
  - [ ] 3.5 Add `test_ansi_render.py` covering cursor, SGR, clear, and
        oversized-sequence cases.
  - References: Requirement 8, Security.

- [ ] 4. Implement `shapes/base.py`.
  - [ ] 4.1 Author `ScreenShape(ABC)` with the abstract methods in the
        design.
  - [ ] 4.2 Add `test_shapebase.py` that asserts every shipped shape
        implements every abstract method.
  - References: Requirement 1, Requirement 3, Requirement 9.

- [ ] 5. Implement `shapes/ami.py` — AMI Aptio shape.
  - [ ] 5.1 Detection regex on banner / border characters.
  - [ ] 5.2 Item parsing from box-drawn layout.
  - [ ] 5.3 Keystroke conventions per the design.
  - [ ] 5.4 `test_shape_ami.py` with captured fixtures.
  - References: Requirement 1, Requirement 9.3.

- [ ] 6. Implement `shapes/insyde.py` — Insyde H2O shape.
  - [ ] 6.1 Detection, item parsing, keystrokes.
  - [ ] 6.2 `test_shape_insyde.py` with captured fixtures.
  - References: Requirement 1, Requirement 9.3.

- [ ] 7. Implement `shapes/phoenix.py` — Phoenix SecureCore shape.
  - [ ] 7.1 Detection, item parsing, keystrokes.
  - [ ] 7.2 `test_shape_phoenix.py` with captured fixtures.
  - References: Requirement 1, Requirement 9.3.

- [ ] 8. Implement `shapes/tianocore.py` — EDK2 / OVMF reference shape.
  - [ ] 8.1 Detection, item parsing, keystrokes.
  - [ ] 8.2 `test_shape_tianocore.py` with captured fixtures.
  - References: Requirement 1, Requirement 9.3.

- [ ] 9. Implement `shapes/registry.py`.
  - [ ] 9.1 Author the registry list and `register_shape` / `dispatch`
        helpers per the design.
  - [ ] 9.2 Warn on duplicate registration under `uefi.hii`.
  - [ ] 9.3 Ensure shipped shapes register themselves at import time.
  - [ ] 9.4 `test_registry.py` covering register, duplicate-warn,
        dispatch-single-match, dispatch-multi-match, dispatch-no-match.
  - References: Requirement 9.

- [ ] 10. Implement `navigator.py`.
  - [ ] 10.1 Author the `HiiFormNavigator` class with all public
        methods enumerated in the design.
  - [ ] 10.2 Every public method routes through `resolve_aspects`.
  - [ ] 10.3 Every raised `UefiError` includes a `Screen` snapshot in
        `details` when `capture_on_exception=True`.
  - [ ] 10.4 Raise `SemanticError` on `DO_WHILE_SUCCESS` or
        `DO_UNTIL_CONNECTION_FAILURE`.
  - [ ] 10.5 Raise `SemanticError` on access to a
        `hii_packages`-style attribute.
  - References: Requirements 2, 3, 4, 5, 6, 7, 8, 13, 15.

- [ ] 11. Unit tests — navigator (`test_navigator.py`).
  - [ ] 11.1 `find_item` exact match.
  - [ ] 11.2 `find_item` regex match.
  - [ ] 11.3 `find_item` case-insensitive.
  - [ ] 11.4 `navigate_to` correct arrow-key sequence.
  - [ ] 11.5 Post-navigation highlight mismatch raises.
  - [ ] 11.6 `set_boolean` happy path.
  - [ ] 11.7 `set_enum` happy path, invalid-choice error path.
  - [ ] 11.8 `set_numeric` happy path, out-of-range error path.
  - [ ] 11.9 `enter_setup` timeout path.
  - [ ] 11.10 `exit_setup(save=True)` / `exit_setup(save=False)`.
  - References: Requirements 2-7.

- [ ] 12. Fixtures corpus (`tests/fixtures/hii/`).
  - [ ] 12.1 AMI Aptio capture + expected screen + expected items +
        interaction transcript.
  - [ ] 12.2 Insyde H2O capture set.
  - [ ] 12.3 Phoenix SecureCore capture set.
  - [ ] 12.4 Tianocore / OVMF capture set.
  - [ ] 12.5 Document provenance in a `FIXTURES.md`.
  - References: Requirement 9.3, Testing Strategy.

- [ ] 13. Action-pattern tests (`test_actionpatterns.py`).
  - [ ] 13.1 `SINGLE_CALL` single attempt.
  - [ ] 13.2 `SINGLE_CONNECTED_CALL` retries connection class.
  - [ ] 13.3 `DO_UNTIL_SUCCESS` retries until success or timeout.
  - [ ] 13.4 `DO_WHILE_SUCCESS` / `DO_UNTIL_CONNECTION_FAILURE` raise
        `SemanticError`.
  - [ ] 13.5 `aspects=None` falls back to instance default.
  - References: Requirement 10, Requirement 11, Requirement 12,
        Requirement 13.

- [ ] 14. OVMF integration test.
  - [ ] 14.1 Enter Setup, toggle a boolean, save, reboot, verify
        persistence.
  - [ ] 14.2 Gate on `AUTOMOJO_UEFI_OVMF_VM` env var.
  - References: Requirement 4, Requirement 7.

- [ ] 15. Documentation.
  - [ ] 15.1 Usage guide `userguide/25-04-hii-forms.rst` explaining
        how to register a shape, capture a screen, and navigate.
  - [ ] 15.2 Cross-link from `AGENTS.md` Features section.
  - References: Introduction.
