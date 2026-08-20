# Tasks — Logging

- [ ] 1. Create the package skeleton at
      `source/packages/ccai/protocols/uefi/logging/`.
  - [ ] 1.1 Add `__init__.py` that re-exports `get_uefi_logger`,
        `LoggingPattern`, `KNOWN_UEFI_CHANNELS`, and every `CHANNEL_*`
        constant.
  - [ ] 1.2 Populate `__all__`.
  - [ ] 1.3 Author a module-level docstring explaining the `uefi.*`
        namespace convention.
  - References: Requirement 8.

- [ ] 2. Author channel constants and the registry in `uefilogging.py`.
  - [ ] 2.1 Declare the thirteen `CHANNEL_*` `Final[str]` constants.
  - [ ] 2.2 Declare `KNOWN_UEFI_CHANNELS` as a `Final[frozenset[str]]` of
        the thirteen constants.
  - References: Requirement 2, Requirement 3.

- [ ] 3. Implement `get_uefi_logger(channel, strict=True)`.
  - [ ] 3.1 Reject non-string `channel` with `TypeError`.
  - [ ] 3.2 Prepend the `uefi.` prefix when missing; preserve when present.
  - [ ] 3.3 In strict mode, raise `ConfigurationError` with a multi-line
        message listing every known channel when the resolved name is
        unknown.
  - [ ] 3.4 In lax mode, emit a `WARNING` on `uefi.logging` and still
        return a logger.
  - [ ] 3.5 Delegate to `getEnhancedLogger(resolved)`.
  - [ ] 3.6 After obtaining the logger, verify every required method
        (`debug`, `info`, `warning`, `error`, `critical`) is callable;
        raise `ConfigurationError` listing any missing methods.
  - [ ] 3.7 Add a Sphinx-style docstring with `:param:`, `:returns:`, and
        `:raises:` entries.
  - References: Requirement 1, Requirement 5, Requirement 9.

- [ ] 4. Re-export `LoggingPattern` from `1-aspects`.
  - [ ] 4.1 Import `LoggingPattern` at module top.
  - [ ] 4.2 Add `LoggingPattern` to `__all__`.
  - [ ] 4.3 Document that the re-export shares class identity with the
        source.
  - References: Requirement 4, Requirement 10.

- [ ] 5. Author `test_get_uefi_logger.py`.
  - [ ] 5.1 Cover known channel with and without the `uefi.` prefix.
  - [ ] 5.2 Cover non-string channel raises `TypeError`.
  - [ ] 5.3 Cover strict-mode unknown channel raises
        `ConfigurationError`; assert the message lists every known
        channel.
  - [ ] 5.4 Cover `strict=False` returns a logger and emits a `WARNING`
        on `uefi.logging` (captured via `caplog`).
  - [ ] 5.5 Cover the returned logger has the five required methods.
  - References: Requirement 1, Requirement 5, Requirement 9.

- [ ] 6. Author `test_channels.py`.
  - [ ] 6.1 Assert `KNOWN_UEFI_CHANNELS` has exactly 13 members.
  - [ ] 6.2 Iterate the module `dir()` and count `CHANNEL_*` constants;
        assert exactly 13.
  - [ ] 6.3 Assert mutation of `KNOWN_UEFI_CHANNELS` raises
        `AttributeError`.
  - References: Requirement 2, Requirement 3.

- [ ] 7. Author `test_logging_pattern_reexport.py`.
  - [ ] 7.1 Import `LoggingPattern` from both this module and
        `1-aspects`; assert identity.
  - [ ] 7.2 Assert the class has members `ALL_RESULTS`, `FAILURE_ONLY`,
        `SUCCESS_ONLY`.
  - References: Requirement 4, Requirement 10.

- [ ] 8. Author `test_logging_smoke.py`.
  - [ ] 8.1 Loop over `KNOWN_UEFI_CHANNELS`.
  - [ ] 8.2 For each channel invoke `get_uefi_logger(channel)` then call
        `.info("smoke")`.
  - [ ] 8.3 Use `caplog` to capture records and assert one record per
        channel with the correct `name`.
  - References: Requirement 6.

- [ ] 9. Author `test_logging_no_side_effects.py`.
  - [ ] 9.1 Record `sys.modules` snapshot before import.
  - [ ] 9.2 Import `ccai.protocols.uefi.logging`.
  - [ ] 9.3 Assert no extra file handles, sockets, or subprocesses were
        opened (checked by observing that the module does not call
        `open`, `socket.socket`, or `subprocess.*` at import time via AST
        inspection).
  - References: Requirement 7.

- [ ] 10. Coverage gate.
  - [ ] 10.1 Enforce 100% line coverage for
        `ccai.protocols.uefi.logging`.
  - [ ] 10.2 Add a CI step that fails on any coverage regression.
  - References: Testing Strategy.

- [ ] 11. Documentation.
  - [ ] 11.1 Author `userguide/20-06-uefi-logging.rst` listing every
        documented channel, showing how to call `get_uefi_logger`, and
        explaining the strict/lax mode distinction.
  - [ ] 11.2 Cross-link from `AGENTS.md` Features section to the guide.
  - References: Introduction, Requirement 9.
