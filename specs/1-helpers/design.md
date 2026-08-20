# Design — Helpers

## Overview

The Helpers feature delivers a single module, `uefihelpers.py`, that
collects the pure-function primitives used across the library: UCS-2
encode/decode, CRC-32 (UEFI-spec compatible), hex-dump rendering, integer
packers/unpackers for u8/u16/u32/u64 in both endiannesses, GUID pack/unpack
shortcuts that delegate to `1-constants-and-types`, `indent_lines`, and
re-exports of the shared `TimeoutContext` and `MonitoredScope` classes.

The module is deliberately flat — one module, no submodules — because every
helper is independent and the file stays small.  Every public function has
a Sphinx-style docstring, explicit type hints, and an explicit `return`
statement.  No third-party imports.  No I/O.  No module-level side effects
beyond binding functions and the CRC-32 lookup table.

## Architecture

```
ccai.protocols.uefi.helpers/
    __init__.py        # re-exports uefihelpers public surface
    uefihelpers.py     # all helper functions + re-exports
```

Dependency direction:

```
uefihelpers.py  ──▶  ccai.protocols.uefi.ueficonst.uefiguids   (guid helpers)
                ──▶  ccai.xmodules.xtimeout                     (TimeoutContext)
                ──▶  ccai.xmodules.xmonitored                   (MonitoredScope)
```

## Components and Interfaces

### UCS-2 Codec

```python
def ucs2_encode(text: str, terminator: bool = False) -> bytes:
    """
        Encode a Python string into a UEFI-strict UCS-2 buffer.

        :param text: The input string.
        :param terminator: When ``True`` append a ``b"\\x00\\x00"`` terminator.

        :returns: The encoded bytes.
        :raises: UnicodeEncodeError
    """
    for idx, ch in enumerate(text):
        cp = ord(ch)
        if cp > 0xFFFF or 0xD800 <= cp <= 0xDFFF:
            err_msg = f"ucs2_encode: character at index {idx} (U+{cp:04X}) is outside UCS-2."
            raise UnicodeEncodeError("ucs-2-strict", text, idx, idx + 1, err_msg)

    encoded = text.encode("utf-16-le")
    if terminator:
        rtnval = encoded + b"\x00\x00"
    else:
        rtnval = encoded
    return rtnval


def ucs2_decode(buf: bytes, strip_null: bool = False) -> str:
    """
        Decode a UEFI UCS-2 buffer into a Python string.

        :param buf: The buffer to decode; must have even length.
        :param strip_null: When ``True`` truncate at the first ``0x0000`` unit.

        :returns: The decoded string.
        :raises: ValueError, UnicodeDecodeError
    """
    if len(buf) % 2 != 0:
        err_msg = f"ucs2_decode: buffer length must be even, observed={len(buf)}."
        raise ValueError(err_msg)

    text = bytes(buf).decode("utf-16-le")
    if strip_null:
        nul = text.find("\x00")
        if nul >= 0:
            text = text[:nul]
    rtnval = text
    return rtnval
```

### CRC-32 (UEFI-compatible)

```python
_CRC32_POLY = 0xEDB88320
_CRC32_TABLE: tuple[int, ...] = tuple(_build_crc32_table(_CRC32_POLY))


def crc32_uefi(data: bytes) -> int:
    """
        Compute CRC-32 matching ``EFI_BOOT_SERVICES.CalculateCrc32``.

        :param data: The input buffer (``bytes``, ``bytearray``, or
                     ``memoryview``).

        :returns: An unsigned 32-bit integer.
        :raises: TypeError
    """
    if not isinstance(data, (bytes, bytearray, memoryview)):
        err_msg = f"crc32_uefi: data must be bytes-like, observed type={type(data).__name__}."
        raise TypeError(err_msg)

    crc = 0xFFFFFFFF
    for byte in bytes(data):
        idx = (crc ^ byte) & 0xFF
        crc = (crc >> 8) ^ _CRC32_TABLE[idx]
    rtnval = crc ^ 0xFFFFFFFF
    return rtnval
```

The implementation is validated in tests against the reference vector
`crc32_uefi(b"123456789") == 0xCBF43926` from the UEFI specification.

### Hex-Dump

```python
def hexdump(data: bytes, offset: int = 0) -> str:
    """
        Render ``data`` as an offset / hex / ASCII dump.

        :param data: The buffer to render.
        :param offset: The byte offset to label the first row with.

        :returns: A single string with ``os.linesep``-separated rows.
    """
    if len(data) == 0:
        rtnval = ""
    else:
        lines: list[str] = []
        for row_start in range(0, len(data), 16):
            row = data[row_start:row_start + 16]
            hex_col = " ".join(f"{b:02x}" for b in row)
            hex_col = hex_col.ljust(3 * 16 - 1)
            ascii_col = "".join(chr(b) if 0x20 <= b <= 0x7E else "." for b in row)
            lines.append(f"{offset + row_start:08x}  {hex_col}  {ascii_col}")
        rtnval = os.linesep.join(lines)
    return rtnval
```

### Integer Packers

```python
def pack_le_u8(value: int) -> bytes:
    rtnval = struct.pack("<B", value)
    return rtnval


def pack_le_u16(value: int) -> bytes:
    rtnval = struct.pack("<H", value)
    return rtnval


def pack_le_u32(value: int) -> bytes:
    rtnval = struct.pack("<I", value)
    return rtnval


def pack_le_u64(value: int) -> bytes:
    rtnval = struct.pack("<Q", value)
    return rtnval


# ... symmetric pack_be_u{8,16,32,64} and unpack_{le,be}_u{8,16,32,64} follow.


def unpack_le_u32(buf: bytes) -> int:
    if len(buf) != 4:
        err_msg = f"unpack_le_u32: buffer length must be 4, observed={len(buf)}."
        raise ValueError(err_msg)
    rtnval = struct.unpack("<I", bytes(buf))[0]
    return rtnval
```

Every `unpack_*` helper validates length explicitly and raises `ValueError`
with both observed and expected lengths in the message.

### GUID Pack / Unpack Shortcuts

```python
from ccai.protocols.uefi.ueficonst.uefiguids import guid_to_bytes, guid_from_bytes


def pack_guid(guid: uuid.UUID) -> bytes:
    rtnval = guid_to_bytes(guid)
    return rtnval


def unpack_guid(buf: bytes) -> uuid.UUID:
    rtnval = guid_from_bytes(buf)
    return rtnval
```

### `indent_lines`

```python
def indent_lines(text: str, prefix: str = "    ") -> str:
    """
        Indent every line of ``text`` by ``prefix`` and re-join using
        ``os.linesep``.

        :param text: The multi-line input.
        :param prefix: The indent prefix.

        :returns: The indented string.
        :raises: TypeError
    """
    if not isinstance(prefix, str):
        err_msg = f"indent_lines: prefix must be str, observed type={type(prefix).__name__}."
        raise TypeError(err_msg)

    if len(text) == 0:
        rtnval = ""
    else:
        lines = text.splitlines()
        indented = [prefix + line for line in lines]
        rtnval = os.linesep.join(indented)
    return rtnval
```

### Re-exports

```python
from ccai.xmodules.xtimeout import TimeoutContext
from ccai.xmodules.xmonitored import MonitoredScope
```

Re-exports are listed in `__all__` alongside the native helpers.

## Data Models

The feature defines no persistent or value-carrying types.  The only
module-level state is `_CRC32_TABLE` (a tuple of 256 ints built once at
import time).  Helpers operate on `bytes`, `str`, and `uuid.UUID` values.

## Measured Interop Contract

This feature does not consume aspects.  It *supports* the measured-interop
contract by re-exporting `TimeoutContext` and `MonitoredScope`, which are
the primitives the layer-4 action-pattern engine uses to bound wait loops
and detect hangs.

## Error Handling

| Condition                                                | Exception              | Notes                               |
|----------------------------------------------------------|------------------------|-------------------------------------|
| `ucs2_encode` on non-BMP or surrogate character          | `UnicodeEncodeError`   | Offending index + code point in msg |
| `ucs2_decode` odd-length buffer                          | `ValueError`           | Observed length in msg              |
| `crc32_uefi` non-bytes-like input                        | `TypeError`            | Observed type name in msg           |
| `unpack_*_u{8,16,32,64}` wrong-length buffer             | `ValueError`           | Observed + expected length in msg   |
| `pack_guid` non-UUID input                               | `TypeError`            | Bubbles from `guid_to_bytes`        |
| `unpack_guid` wrong-length buffer                        | `ValueError`           | Bubbles from `guid_from_bytes`      |
| `indent_lines` non-string `prefix`                       | `TypeError`            | Observed type name in msg           |

`SemanticError` is not raised from this feature.  `ConfigurationError` is
not raised either, because helpers are I/O-free and have no configuration
surface.

## Logging

This feature emits no log output.  The `hexdump` and `indent_lines`
helpers produce strings that callers embed into their own log lines.

## Security Considerations

* `ucs2_encode` is strict — no silent truncation, no surrogate pass-through.
  This prevents a subtle class of bugs where a Python string containing a
  surrogate pair (e.g., from mis-decoded UTF-16) round-trips through a UEFI
  write without error but stores a corrupted buffer.
* `crc32_uefi` is not a cryptographic primitive.  The module docstring
  documents this explicitly to discourage misuse.
* `hexdump` renders arbitrary bytes.  Callers that pass credential material
  should not.  Secret redaction is the caller's responsibility, per the
  same convention followed by `1-credentials`.

## Testing Strategy

### Unit tests (`test_ucs2.py`)

* `ucs2_encode("ABC")` → `b"A\x00B\x00C\x00"`.
* `ucs2_encode("ABC", terminator=True)` appends two null bytes.
* `ucs2_encode("\U0001F600")` raises `UnicodeEncodeError`.
* `ucs2_encode("\uD800")` raises `UnicodeEncodeError`.
* `ucs2_decode(b"A\x00B\x00C\x00")` → `"ABC"`.
* `ucs2_decode(buf, strip_null=True)` truncates at first null.
* Odd-length buffer raises `ValueError` with the observed length.

### Unit tests (`test_crc32.py`)

* Reference vector: `crc32_uefi(b"123456789") == 0xCBF43926`.
* Empty buffer: `crc32_uefi(b"") == 0`.
* `memoryview` and `bytearray` inputs produce the same result as `bytes`.
* Non-bytes-like input raises `TypeError`.

### Unit tests (`test_hexdump.py`)

* `hexdump(b"")` returns `""`.
* Representative buffer renders three columns: offset (8 hex), hex bytes,
  ASCII column; rows separated by `os.linesep`.
* Offset parameter shifts the first row's label.
* Non-printable bytes render as `"."`.

### Unit tests (`test_pack.py`)

* Every `pack_*` / `unpack_*` pair: round-trip, boundary values, wrong-size
  buffer raises `ValueError`.
* Out-of-range values to `pack_*` raise `struct.error`.

### Unit tests (`test_guid_shortcuts.py`)

* `pack_guid` / `unpack_guid` round-trip for every value in
  `WELL_KNOWN_GUIDS`.
* Non-UUID input to `pack_guid` raises `TypeError`.

### Unit tests (`test_indent_lines.py`)

* Empty input returns empty string.
* Single-line input returns prefix + line.
* Multi-line input preserves structure and joins with `os.linesep`.
* Non-string `prefix` raises `TypeError`.

### Property tests (`test_helpers_properties.py`, skipped when hypothesis
unavailable)

* UCS-2 round-trip on arbitrary BMP-only strings.
* Pack/unpack round-trip on arbitrary integers in range for each width.

### No-I/O test (`test_helpers_no_io.py`)

* Parse `uefihelpers.py` as a Python AST and assert zero `open`,
  `socket.socket`, `subprocess.*`, `requests.*`, or `urllib.*` call
  expressions.

### Coverage target

100% line coverage.

## Dependencies on Other Features

* Depends on `1-constants-and-types` for `guid_to_bytes` and
  `guid_from_bytes`.
* Depends on `ccai.xmodules.xtimeout.TimeoutContext` and
  `ccai.xmodules.xmonitored.MonitoredScope` (re-exported only).
* No dependency on `1-aspects`, `1-exceptions`, `1-credentials`,
  `1-logging`, or `1-interfaces`.
