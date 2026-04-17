# Requirements — Helpers

## Introduction

The Helpers feature provides a collection of pure-function primitives used
by every higher layer in `automojo-uefi`.  It covers hex-dump rendering,
CRC-32 (bit-for-bit compatible with `EFI_BOOT_SERVICES.CalculateCrc32` and
validated against a UEFI-spec test vector), UCS-2 encode/decode (strict, no
UTF-16 surrogate pairs), big- and little-endian integer packers,
`uuid.UUID` binary packers (thin wrappers over the `1-constants-and-types`
feature's `guid_to_bytes` / `guid_from_bytes`), thin wrappers around the
shared `TimeoutContext` and `MonitoredScope` primitives, and text helpers
such as `indent_lines`.

Every helper is a pure function — zero I/O, no transport dependency, no
third-party imports.  The module is imported by every feature above it;
keeping it light is a hard requirement.  The UCS-2 codec is UEFI-strict and
raises `UnicodeEncodeError` on any character outside the Basic Multilingual
Plane, because UEFI strings are defined as UCS-2, not UTF-16.  The CRC-32
implementation is validated against the reference vector from the UEFI
specification (`"123456789"` → `0xCBF43926`).

## Requirements

### Requirement 1 — UCS-2 Encode / Decode

**User Story:** As a protocol author building UEFI variables, I want to
encode Python strings into UCS-2 with null-termination and decode UCS-2
buffers back into strings, so that I do not re-implement the UEFI string
convention at every call site.

#### Acceptance Criteria

1. WHEN the caller invokes `ucs2_encode("ABC")` THEN the system SHALL
   return the six-byte sequence `b"A\x00B\x00C\x00"`.
2. WHEN the caller invokes `ucs2_encode("ABC", terminator=True)` THEN the
   system SHALL append a two-byte null terminator, returning eight bytes.
3. IF the input string contains any character whose Unicode code point is
   greater than `0xFFFF` THEN the system SHALL raise `UnicodeEncodeError`
   whose message names the offending character and its code point.
4. IF the input string contains a surrogate code unit (U+D800–U+DFFF) THEN
   the system SHALL raise `UnicodeEncodeError`.
5. WHEN the caller invokes `ucs2_decode(b"A\x00B\x00C\x00")` THEN the
   system SHALL return the string `"ABC"`.
6. WHEN the caller invokes `ucs2_decode(b"A\x00B\x00\x00\x00", strip_null=True)`
   THEN the system SHALL return `"AB"`.
7. IF the input buffer length to `ucs2_decode` is odd THEN the system SHALL
   raise `ValueError` whose message names the observed length.

### Requirement 2 — CRC-32 Matching UEFI Spec

**User Story:** As a GPT-header implementer, I want a CRC-32 routine that
matches `EFI_BOOT_SERVICES.CalculateCrc32` exactly, so that header-CRC
fields I compute match what firmware computes.

#### Acceptance Criteria

1. WHEN the caller invokes `crc32_uefi(b"123456789")` THEN the system SHALL
   return `0xCBF43926` (the reference vector from the UEFI specification).
2. WHEN the caller invokes `crc32_uefi(b"")` THEN the system SHALL return
   `0x00000000`.
3. WHEN the caller invokes `crc32_uefi(data)` for any `bytes` input THEN the
   system SHALL return an unsigned 32-bit integer.
4. WHEN the caller invokes `crc32_uefi(data)` where `data` is a
   `memoryview` or `bytearray` THEN the system SHALL accept the input and
   return the same result as if it had been passed as `bytes`.
5. IF the caller passes a non-`bytes`-like value THEN the system SHALL raise
   `TypeError`.
6. WHEN the caller invokes `crc32_uefi` over a concatenated buffer THEN the
   system SHALL return the same value as
   `crc32_uefi(left) ^ (partial continuation semantics are out of scope —
   the function does not expose an incremental API in v1)`.

### Requirement 3 — Hex-Dump Renderer

**User Story:** As a diagnostician, I want to render a `bytes` buffer as a
classic offset/hex/ASCII hex dump, so that exception `details` lists are
legible.

#### Acceptance Criteria

1. WHEN the caller invokes `hexdump(data)` with non-empty data THEN the
   system SHALL return a string with one line per 16-byte row.
2. WHERE a row has fewer than 16 bytes the system SHALL right-pad the hex
   column with spaces so the ASCII column still aligns.
3. WHERE a byte is outside the printable range `0x20`–`0x7E` the system
   SHALL render the ASCII column character as `"."`.
4. WHEN the caller invokes `hexdump(b"")` THEN the system SHALL return the
   empty string.
5. WHEN the caller invokes `hexdump(data, offset=0x1000)` THEN the system
   SHALL render the first row's offset column as `00001000`.
6. WHEN a `hexdump` result is interpolated into an exception's `details`
   list THEN the embedded line separators SHALL be `os.linesep`.

### Requirement 4 — Integer Pack / Unpack Helpers

**User Story:** As a protocol author, I want named packers for 8/16/32/64-bit
unsigned integers in big- and little-endian order, so that I do not repeat
`struct.pack` format strings at every call site.

#### Acceptance Criteria

1. WHEN the caller invokes `pack_le_u32(0x12345678)` THEN the system SHALL
   return `b"\x78\x56\x34\x12"`.
2. WHEN the caller invokes `pack_be_u32(0x12345678)` THEN the system SHALL
   return `b"\x12\x34\x56\x78"`.
3. WHEN the caller invokes `unpack_le_u32(b"\x78\x56\x34\x12")` THEN the
   system SHALL return `0x12345678`.
4. WHEN equivalent `_u8`, `_u16`, `_u64` helpers are invoked THEN the system
   SHALL pack and unpack matching widths.
5. IF the caller passes a value outside the representable range of the
   chosen width THEN the system SHALL raise `struct.error` (allowed to
   bubble from `struct.pack`).
6. IF the caller passes a buffer whose length does not match the expected
   width to any `unpack_*` helper THEN the system SHALL raise `ValueError`
   whose message names the observed length and the expected length.

### Requirement 5 — GUID Binary Packers

**User Story:** As a consumer of GUIDs, I want shortcut names
(`pack_guid` / `unpack_guid`) in the same module as the other binary
helpers, so that a single import pulls in the whole binary toolkit.

#### Acceptance Criteria

1. WHEN the caller invokes `pack_guid(guid)` THEN the system SHALL delegate
   to `guid_to_bytes` from `1-constants-and-types` and return its result.
2. WHEN the caller invokes `unpack_guid(buf)` THEN the system SHALL
   delegate to `guid_from_bytes` and return its result.
3. WHERE the caller passes invalid input the system SHALL allow the
   underlying exception to bubble unchanged (`TypeError` or `ValueError`).

### Requirement 6 — indent_lines Helper

**User Story:** As a diagnostic-message author, I want to indent a block of
text by N spaces and have it handle any embedded line separator, so that
nested multi-line messages remain readable.

#### Acceptance Criteria

1. WHEN the caller invokes `indent_lines("A\nB", prefix="    ")` THEN the
   system SHALL return `"    A" + os.linesep + "    B"` (converting the
   input's line separator to `os.linesep`).
2. WHEN the caller invokes `indent_lines("", prefix="    ")` THEN the
   system SHALL return the empty string.
3. WHEN the caller omits `prefix` THEN the system SHALL default to four
   spaces.
4. WHEN the input contains `os.linesep` THEN the system SHALL preserve the
   split correctly on every platform.
5. IF the caller passes a non-string `prefix` THEN the system SHALL raise
   `TypeError`.

### Requirement 7 — TimeoutContext and MonitoredScope Wrappers

**User Story:** As a consumer, I want to import `TimeoutContext` and
`MonitoredScope` through this helpers module, so that I do not spell out
the full shared-module path at every call site.

#### Acceptance Criteria

1. WHEN the caller imports `TimeoutContext` from `uefihelpers` THEN the
   system SHALL expose the class re-exported from the shared
   `automojo.xmodules` helpers module.
2. WHEN the caller imports `MonitoredScope` from `uefihelpers` THEN the
   system SHALL expose the class re-exported from the same shared module.
3. WHERE this feature re-exports shared classes the system SHALL NOT wrap
   or subclass them — it shall re-export them exactly as imported.

### Requirement 8 — Zero I/O Guarantee

**User Story:** As a library architect, I want helpers to be pure functions
with no I/O, so that importing them cannot have hidden side effects.

#### Acceptance Criteria

1. WHERE a helper is defined in this feature the system SHALL NOT perform
   any file-system, network, or subprocess I/O.
2. WHERE a helper is defined in this feature the system SHALL NOT mutate
   any module-level state after import.
3. WHEN a linter or static-analysis test scans the module THEN the system
   SHALL report zero usages of `open`, `socket`, `subprocess`, or
   `requests`.

### Requirement 9 — Deterministic Output

**User Story:** As a test author, I want each helper to produce the same
output for the same input on every Python version and OS, so that golden
tests are stable.

#### Acceptance Criteria

1. WHEN any helper is invoked with the same input twice in the same process
   THEN the system SHALL return equal outputs.
2. WHEN any helper is invoked on different platforms with the same input
   THEN the system SHALL return equal outputs, except for
   `indent_lines` whose line separator is `os.linesep` by design.
3. WHERE a helper depends on `os.linesep` the system SHALL document that
   dependence explicitly.

### Requirement 10 — Property-Based Round-Trip Coverage

**User Story:** As a maintainer, I want hypothesis-style round-trip coverage
on the encode/decode and pack/unpack helpers, so that corner cases are
exercised beyond my hand-crafted fixtures.

#### Acceptance Criteria

1. WHEN `hypothesis` is available in the test environment THEN the test
   suite SHALL include property tests that generate arbitrary UCS-2-safe
   strings and round-trip them through `ucs2_encode` / `ucs2_decode`.
2. WHEN `hypothesis` is available THEN the test suite SHALL include
   round-trip tests for each `pack_*` / `unpack_*` helper pair.
3. WHEN `hypothesis` is not available THEN the test suite SHALL skip
   property tests rather than fail.

### Requirement 11 — Stable Module Surface

**User Story:** As a consumer, I want the helpers module to expose a
documented `__all__`, so that `from uefihelpers import *` behaves
deterministically.

#### Acceptance Criteria

1. WHEN `uefihelpers` is imported THEN the system SHALL expose `__all__`
   listing every public helper function and every re-exported class.
2. WHERE a private helper is defined the system SHALL prefix its name with
   a single underscore and SHALL NOT include it in `__all__`.
