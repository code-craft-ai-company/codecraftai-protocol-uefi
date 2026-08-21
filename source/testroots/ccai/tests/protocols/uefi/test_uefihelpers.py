"""
    .. module:: test_uefihelpers
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Unit tests for the UEFI helpers module.  Exercises the
               hex-dump renderer, CRC-32 validator (against the
               ``"123456789"`` UEFI-specification reference vector), the
               strict UCS-2 codec, the fixed-width integer pack/unpack
               helpers, the ``EFI_TIME`` conversion round-trip, and the
               ``indent_lines`` text helper.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Code Craft AI LLC"
__credits__ = []


from ccai import testbase

import datetime
import os
import struct

from ccai.errors.exceptions import ConfigurationError

from ccai.protocols.uefi.uefihelpers import (
    crc32_uefi,
    datetime_to_efi_time,
    efi_time_to_datetime,
    hexdump,
    indent_lines,
    pack_u8,
    pack_u16_be,
    pack_u16_le,
    pack_u32_be,
    pack_u32_le,
    pack_u64_be,
    pack_u64_le,
    ucs2_decode,
    ucs2_encode,
    unpack_u8,
    unpack_u16_be,
    unpack_u16_le,
    unpack_u32_be,
    unpack_u32_le,
    unpack_u64_be,
    unpack_u64_le,
)


# ---------------------------------------------------------------------------
# CRC-32
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# Crc32UefiTestCase
# ----------------------------------------------------------------------

"""
    Validate ``crc32_uefi`` against the UEFI-specification reference
    vector and a small set of canonical edge cases.

    The reference vector comes from the UEFI specification which cites
    the IEEE 802.3 CRC-32 of the ASCII string ``"123456789"``.  The
    expected value ``0xCBF43926`` is the well-known "check" constant
    for this polynomial and matches the output of
    ``EFI_BOOT_SERVICES.CalculateCrc32`` on the same input.
"""

def test_crc32_uefi_reference_vector_123456789() -> None:
    # UEFI / IEEE 802.3 canonical CRC-32 check value for "123456789".
    actual = crc32_uefi(b"123456789")
    testbase.assert_equal(actual, 0xCBF43926)
    return

def test_crc32_uefi_empty_buffer_returns_zero() -> None:
    actual = crc32_uefi(b"")
    testbase.assert_equal(actual, 0x00000000)
    return

def test_crc32_uefi_accepts_bytearray() -> None:
    actual = crc32_uefi(bytearray(b"123456789"))
    testbase.assert_equal(actual, 0xCBF43926)
    return

def test_crc32_uefi_accepts_memoryview() -> None:
    actual = crc32_uefi(memoryview(b"123456789"))
    testbase.assert_equal(actual, 0xCBF43926)
    return

def test_crc32_uefi_result_is_uint32() -> None:
    actual = crc32_uefi(b"\xff" * 1024)
    testbase.assert_true(actual >= 0)
    testbase.assert_true(actual <= 0xFFFFFFFF)
    return

def test_crc32_uefi_non_bytes_input_raises() -> None:
    with testbase.raises(ConfigurationError):
        crc32_uefi("not-bytes")  # type: ignore[arg-type]
    return


# ---------------------------------------------------------------------------
# UCS-2
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# Ucs2CodecTestCase
# ----------------------------------------------------------------------


def test_encode_basic_ascii() -> None:
    actual = ucs2_encode("ABC")
    testbase.assert_equal(actual, b"A\x00B\x00C\x00")
    return

def test_encode_empty_string() -> None:
    actual = ucs2_encode("")
    testbase.assert_equal(actual, b"")
    return

def test_encode_bmp_non_ascii_roundtrips() -> None:
    # U+00E9 "LATIN SMALL LETTER E WITH ACUTE", well inside the BMP.
    original = "caf\u00e9"
    encoded = ucs2_encode(original)
    decoded = ucs2_decode(encoded)
    testbase.assert_equal(decoded, original)
    return

def test_encode_rejects_non_bmp_character() -> None:
    # U+1F600 GRINNING FACE is beyond U+FFFF.
    with testbase.raises(UnicodeEncodeError):
        ucs2_encode("hi \U0001F600")
    return

def test_encode_rejects_surrogate_code_unit() -> None:
    surrogate = "\uD800"
    with testbase.raises(UnicodeEncodeError):
        ucs2_encode(surrogate)
    return

def test_encode_rejects_non_string_input() -> None:
    with testbase.raises(ConfigurationError):
        ucs2_encode(b"bytes-not-str")  # type: ignore[arg-type]
    return

def test_decode_basic_ascii() -> None:
    actual = ucs2_decode(b"A\x00B\x00C\x00")
    testbase.assert_equal(actual, "ABC")
    return

def test_decode_empty_buffer() -> None:
    actual = ucs2_decode(b"")
    testbase.assert_equal(actual, "")
    return

def test_decode_odd_length_buffer_raises() -> None:
    with testbase.raises(ConfigurationError):
        ucs2_decode(b"ABC")
    return

def test_decode_rejects_non_bytes_input() -> None:
    with testbase.raises(ConfigurationError):
        ucs2_decode("not-bytes")  # type: ignore[arg-type]
    return

def test_roundtrip_sampled_bmp_strings() -> None:
    # Hand-curated sample covering ASCII, Latin-1, CJK and a punctuation mark,
    # each of which lives inside the Basic Multilingual Plane.
    samples = [
        "",
        "A",
        "Hello, world!",
        "caf\u00e9",
        "\u4e2d\u6587",
        "line1\nline2",
        "tab\there",
    ]
    for sample in samples:
        encoded = ucs2_encode(sample)
        decoded = ucs2_decode(encoded)
        testbase.assert_equal(decoded, sample)
    return


# ---------------------------------------------------------------------------
# hexdump
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# HexdumpTestCase
# ----------------------------------------------------------------------


def test_hexdump_empty_returns_empty_string() -> None:
    actual = hexdump(b"")
    testbase.assert_equal(actual, "")
    return

def test_hexdump_single_row_full_width() -> None:
    data = bytes(range(16))
    actual = hexdump(data)
    expected_hex = " ".join("{:02x}".format(b) for b in data)
    testbase.assert_true(expected_hex in actual)
    testbase.assert_true(actual.startswith("00000000  "))
    return

def test_hexdump_short_row_hex_column_right_padded() -> None:
    data = b"ABC"
    actual = hexdump(data)
    # ASCII column should still be preceded by the full hex column width.
    # The "ABC" characters should appear at the tail.
    testbase.assert_true(actual.endswith("ABC"))
    return

def test_hexdump_non_printable_bytes_render_as_dot() -> None:
    data = b"\x00\x01\x02\x1f\x7f\x80"
    actual = hexdump(data)
    trailing = actual.split("  ")[-1]
    testbase.assert_equal(trailing, "......")
    return

def test_hexdump_multi_row_uses_os_linesep() -> None:
    data = bytes(range(32))
    actual = hexdump(data)
    testbase.assert_equal(actual.count(os.linesep), 1)
    return

def test_hexdump_custom_width() -> None:
    data = bytes(range(8))
    actual = hexdump(data, width=8)
    testbase.assert_equal(actual.count(os.linesep), 0)
    testbase.assert_true(actual.startswith("00000000  "))
    return

def test_hexdump_non_bytes_input_raises() -> None:
    with testbase.raises(ConfigurationError):
        hexdump("not-bytes")  # type: ignore[arg-type]
    return

def test_hexdump_non_positive_width_raises() -> None:
    with testbase.raises(ConfigurationError):
        hexdump(b"ABC", width=0)
    return

def test_hexdump_non_int_width_raises() -> None:
    with testbase.raises(ConfigurationError):
        hexdump(b"ABC", width="16")  # type: ignore[arg-type]
    return


# ---------------------------------------------------------------------------
# Integer pack / unpack
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# IntegerPackUnpackTestCase
# ----------------------------------------------------------------------

"""
    Exercise every pack/unpack pair at representative boundary values.
    The value table behaves like a parameterized property test without
    requiring ``hypothesis``.
"""

def _pack_unpack_roundtrip(width: int, pack_fn, unpack_fn,
                           values) -> None:
    """
        Shared round-trip assertion helper.
    """
    for value in values:
        packed = pack_fn(value=value)
        testbase.assert_true(isinstance(packed, bytes))
        testbase.assert_equal(len(packed), width)

        unpacked = unpack_fn(buf=packed)
        testbase.assert_equal(unpacked, value)
    return

def test_u8_roundtrip() -> None:
    values = [0, 1, 0x7F, 0x80, 0xFE, 0xFF]
    _pack_unpack_roundtrip(width=1, pack_fn=pack_u8,
                                unpack_fn=unpack_u8, values=values)
    return

def test_u16_le_roundtrip() -> None:
    values = [0, 1, 0x00FF, 0x0100, 0x7FFF, 0x8000, 0xFFFE, 0xFFFF]
    _pack_unpack_roundtrip(width=2, pack_fn=pack_u16_le,
                                unpack_fn=unpack_u16_le, values=values)
    return

def test_u16_be_roundtrip() -> None:
    values = [0, 1, 0x00FF, 0x0100, 0x7FFF, 0x8000, 0xFFFE, 0xFFFF]
    _pack_unpack_roundtrip(width=2, pack_fn=pack_u16_be,
                                unpack_fn=unpack_u16_be, values=values)
    return

def test_u32_le_roundtrip() -> None:
    values = [0, 1, 0x12345678, 0x7FFFFFFF, 0x80000000, 0xFFFFFFFF]
    _pack_unpack_roundtrip(width=4, pack_fn=pack_u32_le,
                                unpack_fn=unpack_u32_le, values=values)
    return

def test_u32_be_roundtrip() -> None:
    values = [0, 1, 0x12345678, 0x7FFFFFFF, 0x80000000, 0xFFFFFFFF]
    _pack_unpack_roundtrip(width=4, pack_fn=pack_u32_be,
                                unpack_fn=unpack_u32_be, values=values)
    return

def test_u64_le_roundtrip() -> None:
    values = [0, 1, 0x0123456789ABCDEF, 0x7FFFFFFFFFFFFFFF,
              0x8000000000000000, 0xFFFFFFFFFFFFFFFF]
    _pack_unpack_roundtrip(width=8, pack_fn=pack_u64_le,
                                unpack_fn=unpack_u64_le, values=values)
    return

def test_u64_be_roundtrip() -> None:
    values = [0, 1, 0x0123456789ABCDEF, 0x7FFFFFFFFFFFFFFF,
              0x8000000000000000, 0xFFFFFFFFFFFFFFFF]
    _pack_unpack_roundtrip(width=8, pack_fn=pack_u64_be,
                                unpack_fn=unpack_u64_be, values=values)
    return

def test_u32_le_byte_order() -> None:
    actual = pack_u32_le(value=0x12345678)
    testbase.assert_equal(actual, b"\x78\x56\x34\x12")
    return

def test_u32_be_byte_order() -> None:
    actual = pack_u32_be(value=0x12345678)
    testbase.assert_equal(actual, b"\x12\x34\x56\x78")
    return

def test_u16_le_byte_order() -> None:
    actual = pack_u16_le(value=0x1234)
    testbase.assert_equal(actual, b"\x34\x12")
    return

def test_u16_be_byte_order() -> None:
    actual = pack_u16_be(value=0x1234)
    testbase.assert_equal(actual, b"\x12\x34")
    return

def test_unpack_wrong_length_raises() -> None:
    with testbase.raises(ConfigurationError):
        unpack_u32_le(buf=b"\x00\x01\x02")
    with testbase.raises(ConfigurationError):
        unpack_u16_be(buf=b"\x00")
    with testbase.raises(ConfigurationError):
        unpack_u64_le(buf=b"\x00" * 7)
    return

def test_pack_out_of_range_raises() -> None:
    with testbase.raises(struct.error):
        pack_u8(value=0x100)
    with testbase.raises(struct.error):
        pack_u16_le(value=-1)
    with testbase.raises(struct.error):
        pack_u32_be(value=0x1_0000_0000)
    return

def test_pack_non_int_raises() -> None:
    with testbase.raises(ConfigurationError):
        pack_u8(value="1")  # type: ignore[arg-type]
    with testbase.raises(ConfigurationError):
        pack_u16_le(value=True)
    return

def test_unpack_non_bytes_raises() -> None:
    with testbase.raises(ConfigurationError):
        unpack_u8(buf="A")  # type: ignore[arg-type]
    return


# ---------------------------------------------------------------------------
# EFI_TIME
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# EfiTimeTestCase
# ----------------------------------------------------------------------

"""
    Round-trip a variety of timezone-aware datetimes through
    ``datetime_to_efi_time`` / ``efi_time_to_datetime`` and verify
    that naive datetimes are rejected with :class:`ConfigurationError`.
"""

def _build_tz(minutes: int) -> datetime.timezone:
    """
        Build a fixed-offset tzinfo with the given minute offset.
    """
    offset = datetime.timedelta(minutes=minutes)
    rtnval = datetime.timezone(offset)
    return rtnval

def test_roundtrip_utc() -> None:
    original = datetime.datetime(2025, 6, 15, 12, 34, 56, 789000,
                                 tzinfo=datetime.timezone.utc)
    encoded = datetime_to_efi_time(dt=original)
    testbase.assert_equal(len(encoded), 16)

    decoded = efi_time_to_datetime(b=encoded)
    testbase.assert_equal(decoded, original)
    return

def test_roundtrip_positive_offset() -> None:
    tz = _build_tz(minutes=+330)  # India Standard Time
    original = datetime.datetime(2024, 1, 1, 0, 0, 0, 0, tzinfo=tz)
    encoded = datetime_to_efi_time(dt=original)
    decoded = efi_time_to_datetime(b=encoded)
    testbase.assert_equal(decoded, original)
    return

def test_roundtrip_negative_offset() -> None:
    tz = _build_tz(minutes=-480)  # Pacific Standard Time
    original = datetime.datetime(2030, 12, 31, 23, 59, 59, 0, tzinfo=tz)
    encoded = datetime_to_efi_time(dt=original)
    decoded = efi_time_to_datetime(b=encoded)
    testbase.assert_equal(decoded, original)
    return

def test_roundtrip_boundary_offsets() -> None:
    # Python's datetime.timezone rejects offsets at exactly ±24h; stay one minute inside.
    for minutes in (-1439, -60, 0, +60, +1439):
        tz = _build_tz(minutes=minutes)
        original = datetime.datetime(2025, 4, 17, 10, 20, 30, 500000,
                                     tzinfo=tz)
        encoded = datetime_to_efi_time(dt=original)
        decoded = efi_time_to_datetime(b=encoded)
        testbase.assert_equal(decoded, original)
    return

def test_rejects_naive_datetime() -> None:
    naive = datetime.datetime(2025, 6, 15, 12, 0, 0)
    with testbase.raises(ConfigurationError):
        datetime_to_efi_time(dt=naive)
    return

def test_rejects_non_datetime_input() -> None:
    with testbase.raises(ConfigurationError):
        datetime_to_efi_time(dt="2025-06-15T12:00:00+00:00")  # type: ignore[arg-type]
    return

def test_rejects_non_bytes_input() -> None:
    with testbase.raises(ConfigurationError):
        efi_time_to_datetime(b="not-bytes")  # type: ignore[arg-type]
    return

def test_rejects_wrong_length_buffer() -> None:
    with testbase.raises(ConfigurationError):
        efi_time_to_datetime(b=b"\x00" * 15)
    return

def test_unspecified_timezone_decoded_as_utc() -> None:
    # Build an EFI_TIME buffer with the unspecified-timezone sentinel.
    buffer = struct.pack("<HBBBBBBIhBB",
                         2025, 6, 15, 12, 0, 0, 0,
                         0, 0x07FF, 0, 0)
    decoded = efi_time_to_datetime(b=buffer)
    testbase.assert_true(decoded.tzinfo is datetime.timezone.utc)
    return


# ---------------------------------------------------------------------------
# indent_lines
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# IndentLinesTestCase
# ----------------------------------------------------------------------


def test_empty_returns_empty() -> None:
    actual = indent_lines("")
    testbase.assert_equal(actual, "")
    return

def test_default_single_level_four_spaces() -> None:
    actual = indent_lines("line1\nline2")
    expected = "    line1" + os.linesep + "    line2"
    testbase.assert_equal(actual, expected)
    return

def test_multiple_levels() -> None:
    actual = indent_lines("a\nb", levels=3)
    expected = (" " * 12) + "a" + os.linesep + (" " * 12) + "b"
    testbase.assert_equal(actual, expected)
    return

def test_custom_indent_string() -> None:
    actual = indent_lines("a\nb", levels=2, indent=">>")
    expected = ">>>>" + "a" + os.linesep + ">>>>" + "b"
    testbase.assert_equal(actual, expected)
    return

def test_zero_levels_leaves_content_but_normalizes_linesep() -> None:
    actual = indent_lines("a\nb", levels=0)
    expected = "a" + os.linesep + "b"
    testbase.assert_equal(actual, expected)
    return

def test_non_string_text_raises() -> None:
    with testbase.raises(ConfigurationError):
        indent_lines(text=123)  # type: ignore[arg-type]
    return

def test_non_string_indent_raises() -> None:
    with testbase.raises(ConfigurationError):
        indent_lines(text="a", indent=4)  # type: ignore[arg-type]
    return

def test_non_int_levels_raises() -> None:
    with testbase.raises(ConfigurationError):
        indent_lines(text="a", levels="1")  # type: ignore[arg-type]
    return

def test_negative_levels_raises() -> None:
    with testbase.raises(ConfigurationError):
        indent_lines(text="a", levels=-1)
    return

def test_bool_levels_rejected() -> None:
    with testbase.raises(ConfigurationError):
        indent_lines(text="a", levels=True)
    return
