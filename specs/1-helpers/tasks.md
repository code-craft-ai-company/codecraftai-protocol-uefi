# Tasks — Helpers

- [ ] 1. Create the package skeleton at
      `source/packages/ccai/protocols/uefi/helpers/`.
  - [ ] 1.1 Add `__init__.py` that re-exports the public surface from
        `uefihelpers.py`.
  - [ ] 1.2 Populate `__all__` in `uefihelpers.py`.
  - [ ] 1.3 Add a module-level docstring explaining the zero-I/O
        invariant.
  - References: Requirement 8, Requirement 11.

- [ ] 2. Implement the UCS-2 codec.
  - [ ] 2.1 Author `ucs2_encode(text, terminator=False)` with strict
        surrogate and BMP checks.
  - [ ] 2.2 Author `ucs2_decode(buf, strip_null=False)` with explicit
        even-length validation.
  - [ ] 2.3 Add Sphinx-style docstrings with `:raises:` for both functions.
  - References: Requirement 1.

- [ ] 3. Implement CRC-32.
  - [ ] 3.1 Build the CRC-32 table once at import time using polynomial
        `0xEDB88320` and store it as `_CRC32_TABLE`.
  - [ ] 3.2 Author `crc32_uefi(data)` validating input type.
  - [ ] 3.3 Add a reference-vector comment in the source file documenting
        `crc32_uefi(b"123456789") == 0xCBF43926`.
  - References: Requirement 2.

- [ ] 4. Implement `hexdump(data, offset=0)`.
  - [ ] 4.1 Render one 16-byte row per line with offset / hex / ASCII
        columns.
  - [ ] 4.2 Right-pad short rows so the ASCII column aligns.
  - [ ] 4.3 Replace non-printable bytes with `"."`.
  - [ ] 4.4 Join rows with `os.linesep`.
  - References: Requirement 3.

- [ ] 5. Implement integer pack/unpack helpers.
  - [ ] 5.1 Author `pack_le_u{8,16,32,64}` and `pack_be_u{8,16,32,64}`.
  - [ ] 5.2 Author matching `unpack_le_u{...}` and `unpack_be_u{...}`
        each with explicit length validation and `ValueError` on mismatch.
  - [ ] 5.3 Add Sphinx-style docstrings for each.
  - References: Requirement 4.

- [ ] 6. Implement GUID shortcut helpers.
  - [ ] 6.1 Import `guid_to_bytes` and `guid_from_bytes` from
        `1-constants-and-types`.
  - [ ] 6.2 Author `pack_guid` / `unpack_guid` as thin wrappers that let
        the underlying exceptions bubble.
  - References: Requirement 5.

- [ ] 7. Implement `indent_lines(text, prefix="    ")`.
  - [ ] 7.1 Validate `prefix` is a string; raise `TypeError` otherwise.
  - [ ] 7.2 Handle empty input by returning the empty string.
  - [ ] 7.3 Split on any universal newline and rejoin with `os.linesep`.
  - References: Requirement 6.

- [ ] 8. Re-export `TimeoutContext` and `MonitoredScope` from the shared
      `ccai.xmodules` modules.
  - [ ] 8.1 Import both classes at module top.
  - [ ] 8.2 Add both to `__all__`.
  - [ ] 8.3 Document the re-export policy (no subclassing, no wrapping).
  - References: Requirement 7.

- [ ] 9. Author unit tests.
  - [ ] 9.1 `test_ucs2.py` covering encode/decode, surrogate rejection,
        non-BMP rejection, odd-length decode rejection.
  - [ ] 9.2 `test_crc32.py` covering the reference vector, empty buffer,
        `memoryview` and `bytearray` inputs, non-bytes-like rejection.
  - [ ] 9.3 `test_hexdump.py` covering empty, short, full rows, custom
        offset, non-printable mapping.
  - [ ] 9.4 `test_pack.py` covering every pack/unpack pair and
        wrong-length rejection.
  - [ ] 9.5 `test_guid_shortcuts.py` covering round-trip over every value
        in `WELL_KNOWN_GUIDS`.
  - [ ] 9.6 `test_indent_lines.py` covering empty, single-line,
        multi-line, custom prefix, non-string `prefix` rejection.
  - References: Requirement 1, Requirement 2, Requirement 3, Requirement 4,
        Requirement 5, Requirement 6.

- [ ] 10. Author `test_helpers_properties.py` gated on hypothesis
       availability.
  - [ ] 10.1 Property: UCS-2 round-trip on arbitrary BMP-only strings.
  - [ ] 10.2 Property: pack/unpack round-trip on arbitrary ints per width.
  - [ ] 10.3 Skip the test module gracefully when `hypothesis` is not
        installed.
  - References: Requirement 10.

- [ ] 11. Author `test_helpers_no_io.py`.
  - [ ] 11.1 Parse `uefihelpers.py` with `ast` and walk for call
        expressions.
  - [ ] 11.2 Fail if any call targets `open`, `socket.socket`,
        `subprocess.*`, `requests.*`, or `urllib.*`.
  - References: Requirement 8.

- [ ] 12. Coverage gate.
  - [ ] 12.1 Enforce 100% line coverage for
        `ccai.protocols.uefi.helpers`.
  - [ ] 12.2 Add a CI step that fails on any coverage regression.
  - References: Testing Strategy.

- [ ] 13. Documentation.
  - [ ] 13.1 Author `userguide/20-05-uefi-helpers.rst` listing each helper
        with one-line descriptions and a single worked example of the
        hex-dump renderer and the CRC-32 helper.
  - [ ] 13.2 Cross-link from `AGENTS.md` Features section to the guide.
  - References: Introduction, Requirement 9.
