"""
    .. module:: uefihelpers
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Pure-function helper primitives used throughout the UEFI
               protocol layer.  Covers canonical hex-dump rendering, CRC-32
               matching ``EFI_BOOT_SERVICES.CalculateCrc32``, strict UCS-2
               encode/decode, fixed-width integer pack/unpack helpers,
               ``EFI_TIME`` conversion helpers, and an ``indent_lines`` text
               helper.  Every function is pure: no file-system, network, or
               subprocess I/O and no module-level state beyond the CRC-32
               lookup table built once at import time.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Code Craft AI LLC"
__credits__ = []


import datetime
import os
import struct

from typing import Tuple

from ccai.errors.exceptions import ConfigurationError


__all__ = [
    "crc32_uefi",
    "datetime_to_efi_time",
    "efi_time_to_datetime",
    "hexdump",
    "indent_lines",
    "pack_u16_be",
    "pack_u16_le",
    "pack_u32_be",
    "pack_u32_le",
    "pack_u64_be",
    "pack_u64_le",
    "pack_u8",
    "ucs2_decode",
    "ucs2_encode",
    "unpack_u16_be",
    "unpack_u16_le",
    "unpack_u32_be",
    "unpack_u32_le",
    "unpack_u64_be",
    "unpack_u64_le",
    "unpack_u8",
]


# ---------------------------------------------------------------------------
# CRC-32 table
# ---------------------------------------------------------------------------
#
# The polynomial ``0xEDB88320`` is the reversed form of the IEEE 802.3
# CRC-32 polynomial used by the UEFI specification for
# ``EFI_BOOT_SERVICES.CalculateCrc32``.  The table is built once at import
# time and never mutated afterwards.  The implementation is validated
# against the UEFI reference vector ``crc32_uefi(b"123456789") == 0xCBF43926``
# in the unit tests (see ``test_uefihelpers.py``).
# ---------------------------------------------------------------------------

_CRC32_POLY = 0xEDB88320


def _build_crc32_table(poly: int) -> Tuple[int, ...]:
    """
        Build the 256-entry CRC-32 lookup table for ``poly``.

        :param poly: The reversed polynomial value.

        :returns: A 256-element tuple of precomputed CRC values.
    """
    table_entries = []
    for byte_value in range(256):
        crc = byte_value
        for _ in range(8):
            if (crc & 1) != 0:
                crc = (crc >> 1) ^ poly
            else:
                crc = crc >> 1
        table_entries.append(crc)

    rtnval = tuple(table_entries)
    return rtnval


_CRC32_TABLE: Tuple[int, ...] = _build_crc32_table(_CRC32_POLY)


# ---------------------------------------------------------------------------
# Hex-dump renderer
# ---------------------------------------------------------------------------

def hexdump(data: bytes, width: int = 16) -> str:
    """
        Render ``data`` as a canonical offset / hex / ASCII hex dump.

        Each row is labelled with an 8-hex-digit offset.  Bytes outside the
        printable ASCII range (``0x20``-``0x7E``) are rendered as ``'.'`` in
        the ASCII column.  Short rows are right-padded in the hex column so
        the ASCII column remains aligned.  Rows are joined with
        :data:`os.linesep`.

        :param data: The buffer to render.  Must be ``bytes``, ``bytearray``,
                     or ``memoryview``.
        :param width: Number of bytes per row.  Must be a positive integer.

        :returns: A single string.  When ``data`` is empty the function
                  returns the empty string.
        :raises: ConfigurationError
    """
    if not isinstance(data, (bytes, bytearray, memoryview)):
        err_msg_lines = [
            "'data' must be a bytes-like object.",
            "GOT: value={!r} type={}".format(data, type(data).__name__),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise ConfigurationError(err_msg) from None

    if not isinstance(width, int) or isinstance(width, bool):
        err_msg_lines = [
            "'width' must be a positive int.",
            "GOT: value={!r} type={}".format(width, type(width).__name__),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise ConfigurationError(err_msg) from None

    if width <= 0:
        err_msg_lines = [
            "'width' must be greater than zero.",
            "GOT: value={}".format(width),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise ConfigurationError(err_msg) from None

    buffer = bytes(data)
    total = len(buffer)

    if total == 0:
        rtnval = ""
    else:
        lines = []
        hex_column_width = (3 * width) - 1
        for row_start in range(0, total, width):
            row = buffer[row_start:row_start + width]

            hex_parts = ["{:02x}".format(byte_value) for byte_value in row]
            hex_column = " ".join(hex_parts)
            hex_column_padded = hex_column.ljust(hex_column_width)

            ascii_chars = []
            for byte_value in row:
                if 0x20 <= byte_value <= 0x7E:
                    ascii_chars.append(chr(byte_value))
                else:
                    ascii_chars.append(".")
            ascii_column = "".join(ascii_chars)

            line = "{:08x}  {}  {}".format(row_start, hex_column_padded, ascii_column)
            lines.append(line)

        rtnval = os.linesep.join(lines)

    return rtnval


# ---------------------------------------------------------------------------
# CRC-32 (UEFI-compatible)
# ---------------------------------------------------------------------------

def crc32_uefi(data: bytes) -> int:
    """
        Compute a CRC-32 over ``data`` using the IEEE 802.3 polynomial,
        matching ``EFI_BOOT_SERVICES.CalculateCrc32`` from the UEFI
        specification.

        This routine is validated against the UEFI-specification reference
        vector ``crc32_uefi(b"123456789") == 0xCBF43926``.  The result is
        equivalent to :func:`zlib.crc32` in practice, but the table-driven
        implementation is kept local so that the helpers package does not
        depend on any third-party module and to document the polynomial
        explicitly.

        :param data: The input buffer.  Must be ``bytes``, ``bytearray``,
                     or ``memoryview``.

        :returns: An unsigned 32-bit integer (``0 <= x <= 0xFFFFFFFF``).
        :raises: ConfigurationError
    """
    if not isinstance(data, (bytes, bytearray, memoryview)):
        err_msg_lines = [
            "'data' must be a bytes-like object.",
            "GOT: value={!r} type={}".format(data, type(data).__name__),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise ConfigurationError(err_msg) from None

    buffer = bytes(data)

    crc = 0xFFFFFFFF
    for byte_value in buffer:
        index = (crc ^ byte_value) & 0xFF
        crc = (crc >> 8) ^ _CRC32_TABLE[index]

    rtnval = crc ^ 0xFFFFFFFF
    return rtnval


# ---------------------------------------------------------------------------
# UCS-2 codec
# ---------------------------------------------------------------------------

def ucs2_encode(s: str) -> bytes:
    """
        Encode a Python string into a strict little-endian UCS-2 buffer.

        UEFI strings are defined as UCS-2, not UTF-16.  Characters whose
        code point is greater than ``U+FFFF`` are not representable and
        would silently round-trip through :meth:`str.encode('utf-16-le')`
        as surrogate pairs.  This function rejects such characters (and
        raw surrogate code units) explicitly so that callers never write a
        corrupted buffer into firmware.

        :param s: The input string.

        :returns: The encoded bytes (``2 * len(s)`` bytes long).
        :raises: ConfigurationError, UnicodeEncodeError
    """
    if not isinstance(s, str):
        err_msg_lines = [
            "'s' must be a str.",
            "GOT: value={!r} type={}".format(s, type(s).__name__),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise ConfigurationError(err_msg) from None

    for index, character in enumerate(s):
        code_point = ord(character)
        if code_point > 0xFFFF:
            err_msg = "ucs2_encode: character at index {} (U+{:04X}) is outside the Basic Multilingual Plane.".format(index, code_point)
            raise UnicodeEncodeError("ucs-2-strict", s, index, index + 1, err_msg)
        if 0xD800 <= code_point <= 0xDFFF:
            err_msg = "ucs2_encode: character at index {} (U+{:04X}) is a surrogate code unit.".format(index, code_point)
            raise UnicodeEncodeError("ucs-2-strict", s, index, index + 1, err_msg)

    rtnval = s.encode("utf-16-le")
    return rtnval


def ucs2_decode(b: bytes) -> str:
    """
        Decode a little-endian UCS-2 buffer into a Python string.

        Inverse of :func:`ucs2_encode`.  The input length must be even.

        :param b: The buffer to decode.  Must be ``bytes``, ``bytearray``,
                  or ``memoryview``.

        :returns: The decoded string.
        :raises: ConfigurationError, UnicodeDecodeError
    """
    if not isinstance(b, (bytes, bytearray, memoryview)):
        err_msg_lines = [
            "'b' must be a bytes-like object.",
            "GOT: value={!r} type={}".format(b, type(b).__name__),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise ConfigurationError(err_msg) from None

    buffer = bytes(b)
    buffer_length = len(buffer)
    if (buffer_length % 2) != 0:
        err_msg_lines = [
            "ucs2_decode: buffer length must be even.",
            "OBSERVED: length={}".format(buffer_length),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise ConfigurationError(err_msg) from None

    rtnval = buffer.decode("utf-16-le")
    return rtnval


# ---------------------------------------------------------------------------
# Integer pack / unpack helpers
# ---------------------------------------------------------------------------
#
# Helpers are provided for u8 (width-agnostic endianness), and for
# u16 / u32 / u64 in both little-endian and big-endian order.  Each
# ``pack_*`` helper lets :func:`struct.error` bubble when the input is
# outside the representable range for the chosen width.  Each
# ``unpack_*`` helper validates buffer length explicitly and raises
# :class:`ConfigurationError` on mismatch.
# ---------------------------------------------------------------------------


def _require_int(field_name: str, value: object) -> int:
    """
        Validate that ``value`` is an ``int`` (not ``bool``).

        :param field_name: The calling field name for diagnostics.
        :param value: The candidate value.

        :returns: The validated integer value.
        :raises: ConfigurationError
    """
    if not isinstance(value, int) or isinstance(value, bool):
        err_msg_lines = [
            "'{}' must be an int.".format(field_name),
            "GOT: value={!r} type={}".format(value, type(value).__name__),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise ConfigurationError(err_msg) from None

    rtnval = value
    return rtnval


def _require_bytes(field_name: str, value: object) -> bytes:
    """
        Validate that ``value`` is a bytes-like object and coerce to
        :class:`bytes`.

        :param field_name: The calling field name for diagnostics.
        :param value: The candidate value.

        :returns: The validated buffer as :class:`bytes`.
        :raises: ConfigurationError
    """
    if not isinstance(value, (bytes, bytearray, memoryview)):
        err_msg_lines = [
            "'{}' must be a bytes-like object.".format(field_name),
            "GOT: value={!r} type={}".format(value, type(value).__name__),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise ConfigurationError(err_msg) from None

    rtnval = bytes(value)
    return rtnval


def _check_unpack_length(field_name: str, buffer: bytes, expected: int) -> None:
    """
        Verify that ``buffer`` has exactly ``expected`` bytes.

        :param field_name: The calling field name for diagnostics.
        :param buffer: The buffer to check.
        :param expected: The expected length.

        :raises: ConfigurationError
    """
    observed = len(buffer)
    if observed != expected:
        err_msg_lines = [
            "'{}' buffer length mismatch.".format(field_name),
            "EXPECTED: {}".format(expected),
            "OBSERVED: {}".format(observed),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise ConfigurationError(err_msg) from None

    return


def pack_u8(value: int) -> bytes:
    """
        Pack ``value`` as a single unsigned byte.

        :param value: The value to pack (``0 <= value <= 0xFF``).

        :returns: A one-byte buffer.
        :raises: ConfigurationError, struct.error
    """
    validated = _require_int("value", value)
    rtnval = struct.pack("B", validated)
    return rtnval


def pack_u16_le(value: int) -> bytes:
    """
        Pack ``value`` as an unsigned 16-bit little-endian integer.

        :param value: The value to pack (``0 <= value <= 0xFFFF``).

        :returns: A two-byte buffer.
        :raises: ConfigurationError, struct.error
    """
    validated = _require_int("value", value)
    rtnval = struct.pack("<H", validated)
    return rtnval


def pack_u16_be(value: int) -> bytes:
    """
        Pack ``value`` as an unsigned 16-bit big-endian integer.

        :param value: The value to pack (``0 <= value <= 0xFFFF``).

        :returns: A two-byte buffer.
        :raises: ConfigurationError, struct.error
    """
    validated = _require_int("value", value)
    rtnval = struct.pack(">H", validated)
    return rtnval


def pack_u32_le(value: int) -> bytes:
    """
        Pack ``value`` as an unsigned 32-bit little-endian integer.

        :param value: The value to pack (``0 <= value <= 0xFFFFFFFF``).

        :returns: A four-byte buffer.
        :raises: ConfigurationError, struct.error
    """
    validated = _require_int("value", value)
    rtnval = struct.pack("<I", validated)
    return rtnval


def pack_u32_be(value: int) -> bytes:
    """
        Pack ``value`` as an unsigned 32-bit big-endian integer.

        :param value: The value to pack (``0 <= value <= 0xFFFFFFFF``).

        :returns: A four-byte buffer.
        :raises: ConfigurationError, struct.error
    """
    validated = _require_int("value", value)
    rtnval = struct.pack(">I", validated)
    return rtnval


def pack_u64_le(value: int) -> bytes:
    """
        Pack ``value`` as an unsigned 64-bit little-endian integer.

        :param value: The value to pack (``0 <= value <= 0xFFFFFFFFFFFFFFFF``).

        :returns: An eight-byte buffer.
        :raises: ConfigurationError, struct.error
    """
    validated = _require_int("value", value)
    rtnval = struct.pack("<Q", validated)
    return rtnval


def pack_u64_be(value: int) -> bytes:
    """
        Pack ``value`` as an unsigned 64-bit big-endian integer.

        :param value: The value to pack (``0 <= value <= 0xFFFFFFFFFFFFFFFF``).

        :returns: An eight-byte buffer.
        :raises: ConfigurationError, struct.error
    """
    validated = _require_int("value", value)
    rtnval = struct.pack(">Q", validated)
    return rtnval


def unpack_u8(buf: bytes) -> int:
    """
        Unpack a one-byte buffer as an unsigned 8-bit integer.

        :param buf: The buffer to unpack.

        :returns: The unpacked value.
        :raises: ConfigurationError
    """
    buffer = _require_bytes("buf", buf)
    _check_unpack_length("unpack_u8", buffer, 1)

    unpacked_tuple = struct.unpack("B", buffer)
    rtnval = unpacked_tuple[0]
    return rtnval


def unpack_u16_le(buf: bytes) -> int:
    """
        Unpack a two-byte little-endian buffer as an unsigned 16-bit int.

        :param buf: The buffer to unpack.

        :returns: The unpacked value.
        :raises: ConfigurationError
    """
    buffer = _require_bytes("buf", buf)
    _check_unpack_length("unpack_u16_le", buffer, 2)

    unpacked_tuple = struct.unpack("<H", buffer)
    rtnval = unpacked_tuple[0]
    return rtnval


def unpack_u16_be(buf: bytes) -> int:
    """
        Unpack a two-byte big-endian buffer as an unsigned 16-bit int.

        :param buf: The buffer to unpack.

        :returns: The unpacked value.
        :raises: ConfigurationError
    """
    buffer = _require_bytes("buf", buf)
    _check_unpack_length("unpack_u16_be", buffer, 2)

    unpacked_tuple = struct.unpack(">H", buffer)
    rtnval = unpacked_tuple[0]
    return rtnval


def unpack_u32_le(buf: bytes) -> int:
    """
        Unpack a four-byte little-endian buffer as an unsigned 32-bit int.

        :param buf: The buffer to unpack.

        :returns: The unpacked value.
        :raises: ConfigurationError
    """
    buffer = _require_bytes("buf", buf)
    _check_unpack_length("unpack_u32_le", buffer, 4)

    unpacked_tuple = struct.unpack("<I", buffer)
    rtnval = unpacked_tuple[0]
    return rtnval


def unpack_u32_be(buf: bytes) -> int:
    """
        Unpack a four-byte big-endian buffer as an unsigned 32-bit int.

        :param buf: The buffer to unpack.

        :returns: The unpacked value.
        :raises: ConfigurationError
    """
    buffer = _require_bytes("buf", buf)
    _check_unpack_length("unpack_u32_be", buffer, 4)

    unpacked_tuple = struct.unpack(">I", buffer)
    rtnval = unpacked_tuple[0]
    return rtnval


def unpack_u64_le(buf: bytes) -> int:
    """
        Unpack an eight-byte little-endian buffer as an unsigned 64-bit int.

        :param buf: The buffer to unpack.

        :returns: The unpacked value.
        :raises: ConfigurationError
    """
    buffer = _require_bytes("buf", buf)
    _check_unpack_length("unpack_u64_le", buffer, 8)

    unpacked_tuple = struct.unpack("<Q", buffer)
    rtnval = unpacked_tuple[0]
    return rtnval


def unpack_u64_be(buf: bytes) -> int:
    """
        Unpack an eight-byte big-endian buffer as an unsigned 64-bit int.

        :param buf: The buffer to unpack.

        :returns: The unpacked value.
        :raises: ConfigurationError
    """
    buffer = _require_bytes("buf", buf)
    _check_unpack_length("unpack_u64_be", buffer, 8)

    unpacked_tuple = struct.unpack(">Q", buffer)
    rtnval = unpacked_tuple[0]
    return rtnval


# ---------------------------------------------------------------------------
# EFI_TIME conversion
# ---------------------------------------------------------------------------
#
# The UEFI specification defines ``EFI_TIME`` as a 16-byte structure with
# the following little-endian layout:
#
#     Offset  Size  Field
#     ------  ----  --------------------------
#      0      2     Year         (u16)
#      2      1     Month        (u8)
#      3      1     Day          (u8)
#      4      1     Hour         (u8)
#      5      1     Minute       (u8)
#      6      1     Second       (u8)
#      7      1     Pad1         (u8, must be 0)
#      8      4     Nanosecond   (u32)
#     12      2     TimeZone     (i16, minutes from UTC, -1440..+1440 or 0x7FF for unspecified)
#     14      1     Daylight     (u8, bit flags)
#     15      1     Pad2         (u8, must be 0)
# ---------------------------------------------------------------------------

_EFI_TIME_SIZE = 16
_EFI_TIME_UNSPECIFIED_TIMEZONE = 0x07FF


def efi_time_to_datetime(b: bytes) -> datetime.datetime:
    """
        Decode a 16-byte ``EFI_TIME`` buffer into a timezone-aware
        :class:`datetime.datetime`.

        The returned datetime carries a fixed-offset timezone derived from
        the ``TimeZone`` field (minutes west/east of UTC).  When the buffer
        reports the UEFI "unspecified" sentinel (``0x07FF``) the returned
        datetime is attached to UTC.

        :param b: The 16-byte ``EFI_TIME`` buffer.

        :returns: A timezone-aware :class:`datetime.datetime`.
        :raises: ConfigurationError
    """
    buffer = _require_bytes("b", b)
    _check_unpack_length("efi_time_to_datetime", buffer, _EFI_TIME_SIZE)

    unpacked_fields = struct.unpack("<HBBBBBBIhBB", buffer)
    year = unpacked_fields[0]
    month = unpacked_fields[1]
    day = unpacked_fields[2]
    hour = unpacked_fields[3]
    minute = unpacked_fields[4]
    second = unpacked_fields[5]
    # pad1 (unpacked_fields[6]) is ignored
    nanosecond = unpacked_fields[7]
    timezone_minutes = unpacked_fields[8]
    # daylight (unpacked_fields[9]) is ignored in conversion
    # pad2 (unpacked_fields[10]) is ignored

    if nanosecond >= 1_000_000_000:
        err_msg_lines = [
            "'nanosecond' field is out of range.",
            "EXPECTED: 0 <= value < 1_000_000_000",
            "OBSERVED: {}".format(nanosecond),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise ConfigurationError(err_msg) from None

    microsecond = nanosecond // 1000

    if timezone_minutes == _EFI_TIME_UNSPECIFIED_TIMEZONE:
        resolved_tzinfo = datetime.timezone.utc
    else:
        if timezone_minutes < -1440 or timezone_minutes > 1440:
            err_msg_lines = [
                "'timezone' field is out of range.",
                "EXPECTED: -1440 <= value <= 1440 or 0x07FF",
                "OBSERVED: {}".format(timezone_minutes),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg) from None

        offset = datetime.timedelta(minutes=timezone_minutes)
        resolved_tzinfo = datetime.timezone(offset)

    try:
        rtnval = datetime.datetime(year, month, day, hour, minute, second,
                                   microsecond, tzinfo=resolved_tzinfo)
    except ValueError as verr:
        err_msg_lines = [
            "'EFI_TIME' fields do not form a valid datetime.",
            "YEAR: {}".format(year),
            "MONTH: {}".format(month),
            "DAY: {}".format(day),
            "HOUR: {}".format(hour),
            "MINUTE: {}".format(minute),
            "SECOND: {}".format(second),
            "MICROSECOND: {}".format(microsecond),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise ConfigurationError(err_msg) from verr

    return rtnval


def datetime_to_efi_time(dt: datetime.datetime) -> bytes:
    """
        Encode a timezone-aware :class:`datetime.datetime` into a 16-byte
        ``EFI_TIME`` buffer.

        Naive datetimes are rejected because the UEFI ``EFI_TIME`` struct
        carries a timezone field.  Silently assuming UTC would round-trip
        lossily through firmware.

        :param dt: The datetime to encode.  Must be timezone-aware.

        :returns: A 16-byte ``EFI_TIME`` buffer.
        :raises: ConfigurationError
    """
    if not isinstance(dt, datetime.datetime):
        err_msg_lines = [
            "'dt' must be a datetime.datetime.",
            "GOT: value={!r} type={}".format(dt, type(dt).__name__),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise ConfigurationError(err_msg) from None

    dt_tzinfo = dt.tzinfo
    if dt_tzinfo is None or dt_tzinfo.utcoffset(dt) is None:
        err_msg_lines = [
            "'dt' must be a timezone-aware datetime.",
            "OBSERVED: tzinfo={!r}".format(dt_tzinfo),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise ConfigurationError(err_msg) from None

    utc_offset = dt.utcoffset()
    offset_total_seconds = int(utc_offset.total_seconds())

    if (offset_total_seconds % 60) != 0:
        err_msg_lines = [
            "'dt' timezone offset must be an integer number of minutes.",
            "OBSERVED: offset_seconds={}".format(offset_total_seconds),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise ConfigurationError(err_msg) from None

    timezone_minutes = offset_total_seconds // 60

    if timezone_minutes < -1440 or timezone_minutes > 1440:
        err_msg_lines = [
            "'dt' timezone offset is out of range for EFI_TIME.",
            "EXPECTED: -1440 <= minutes <= 1440",
            "OBSERVED: {}".format(timezone_minutes),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise ConfigurationError(err_msg) from None

    year = dt.year
    month = dt.month
    day = dt.day
    hour = dt.hour
    minute = dt.minute
    second = dt.second
    pad1 = 0
    nanosecond = dt.microsecond * 1000
    daylight = 0
    pad2 = 0

    if year < 0 or year > 0xFFFF:
        err_msg_lines = [
            "'dt.year' is out of range for EFI_TIME u16 field.",
            "EXPECTED: 0 <= year <= 65535",
            "OBSERVED: {}".format(year),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise ConfigurationError(err_msg) from None

    rtnval = struct.pack("<HBBBBBBIhBB", year, month, day, hour, minute, second,
                         pad1, nanosecond, timezone_minutes, daylight, pad2)
    return rtnval


# ---------------------------------------------------------------------------
# indent_lines
# ---------------------------------------------------------------------------

def indent_lines(text: str, levels: int = 1, indent: str = "    ") -> str:
    """
        Indent every line of ``text`` by ``levels`` repetitions of
        ``indent`` and re-join with :data:`os.linesep`.

        The function splits on any universal newline (``\\n``, ``\\r\\n``,
        ``\\r``) so input that carries a foreign line separator is
        normalized on output.  An empty input returns the empty string.

        :param text: The multi-line input.
        :param levels: The indent depth.  Must be a non-negative int.
        :param indent: The unit indent string.

        :returns: The indented string.
        :raises: ConfigurationError
    """
    if not isinstance(text, str):
        err_msg_lines = [
            "'text' must be a str.",
            "GOT: value={!r} type={}".format(text, type(text).__name__),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise ConfigurationError(err_msg) from None

    if not isinstance(levels, int) or isinstance(levels, bool):
        err_msg_lines = [
            "'levels' must be a non-negative int.",
            "GOT: value={!r} type={}".format(levels, type(levels).__name__),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise ConfigurationError(err_msg) from None

    if levels < 0:
        err_msg_lines = [
            "'levels' must be a non-negative int.",
            "OBSERVED: {}".format(levels),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise ConfigurationError(err_msg) from None

    if not isinstance(indent, str):
        err_msg_lines = [
            "'indent' must be a str.",
            "GOT: value={!r} type={}".format(indent, type(indent).__name__),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise ConfigurationError(err_msg) from None

    if len(text) == 0:
        rtnval = ""
    else:
        prefix = indent * levels
        split_lines = text.splitlines()
        indented_lines = [prefix + line for line in split_lines]
        rtnval = os.linesep.join(indented_lines)

    return rtnval
