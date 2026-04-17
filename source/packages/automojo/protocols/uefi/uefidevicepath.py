"""
    .. module:: uefidevicepath
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Pure binary and text codec for UEFI Device Paths as defined
               in the UEFI Specification section 10.  Provides two
               immutable value objects -- :class:`DevicePathNode` and
               :class:`DevicePath` -- and four transport-free codec entry
               points :func:`decode_device_path`, :func:`encode_device_path`,
               :func:`device_path_to_text`, :func:`device_path_from_text`.
               Dispatch between concrete node shapes is performed by
               :data:`_NODE_FORMATTERS` and :data:`_NODE_PARSERS` registries
               keyed by ``(type, subtype)`` and by canonical text-form
               option-name respectively.  Unknown ``(type, subtype)`` pairs
               round-trip through the generic ``Path(type,subtype,hex)``
               form per UEFI section 10.6.1.6 so the codec stays forward-
               compatible with new spec revisions.  The text parser is a
               hand-rolled recursive-descent parser using only the Python
               standard library -- no ``pyparsing``, no ``lark``.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Automation Mojo LLC"
__credits__ = []


import os
import uuid

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from automojo.protocols.uefi.uefiexceptions import UefiProtocolError
from automojo.protocols.uefi.uefihelpers import ucs2_decode, ucs2_encode


__all__ = [
    "DevicePath",
    "DevicePathNode",
    "decode_device_path",
    "encode_device_path",
    "device_path_from_text",
    "device_path_to_text",
    "END_OF_HARDWARE_NODE",
    "END_ENTIRE_NODE",
]


# ---------------------------------------------------------------------------
# Constants drawn from UEFI specification section 10.3.1
# ---------------------------------------------------------------------------

# Generic 4-byte header sizes and End-of-Path sentinel values.
_HEADER_LEN = 4
_END_TYPE = 0x7F
_END_OF_HARDWARE_SUBTYPE = 0x01
_END_ENTIRE_SUBTYPE = 0xFF

# Well-known EISA-encoded _HID values used by ACPI subtype 1 named-form
# translation (UEFI section 10.6.1.6 table 10.67).
_EISA_PNP0A03 = 0x0A0341D0   # PciRoot
_EISA_PNP0A08 = 0x0A0841D0   # PcieRoot
_EISA_PNP0604 = 0x060441D0   # Floppy
_EISA_PNP0301 = 0x030141D0   # Keyboard
_EISA_PNP0501 = 0x050141D0   # Serial
_EISA_PNP0401 = 0x040141D0   # ParallelPort


# ---------------------------------------------------------------------------
# Public value objects
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DevicePathNode:
    """
        An immutable UEFI device-path node: the 4-byte generic header
        fields (``type``, ``subtype``) together with the raw payload bytes
        following the header.

        The codec intentionally models every node as this single shape.
        Interpretation of ``data`` is performed by the text formatter and
        parser registries -- not by dataclass fields.  This keeps the
        binary encoder trivial (just prepend the header) and lets unknown
        ``(type, subtype)`` pairs round-trip losslessly.

        :param type: The node type byte (UEFI section 10.3.1 table 10.8).
        :param subtype: The node subtype byte within the family.
        :param data: The raw payload bytes following the 4-byte header.
    """

    type: int
    subtype: int
    data: bytes


@dataclass(frozen=True)
class DevicePath:
    """
        An ordered, immutable sequence of :class:`DevicePathNode`
        instances.  Equality is structural.  A fully-formed device path
        ends with the End-Entire terminator (``type=0x7F``,
        ``subtype=0xFF``); however the :class:`DevicePath` container
        itself never stores that terminator -- the binary encoder appends
        it and the binary decoder strips it.

        :param nodes: The tuple of nodes making up this device path.
    """

    nodes: Tuple[DevicePathNode, ...]


# ---------------------------------------------------------------------------
# Sentinel End-Node constants (exported for caller convenience)
# ---------------------------------------------------------------------------

END_OF_HARDWARE_NODE: DevicePathNode = DevicePathNode(
    type=_END_TYPE, subtype=_END_OF_HARDWARE_SUBTYPE, data=b""
)
END_ENTIRE_NODE: DevicePathNode = DevicePathNode(
    type=_END_TYPE, subtype=_END_ENTIRE_SUBTYPE, data=b""
)


# ---------------------------------------------------------------------------
# Binary codec
# ---------------------------------------------------------------------------

def decode_device_path(data: bytes) -> DevicePath:
    """
        Parse a concatenated sequence of UEFI Device Path nodes per
        UEFI section 10.3.1.

        Each node starts with a 4-byte header: ``type`` (u8),
        ``subtype`` (u8), ``length`` (u16 little-endian).  The ``length``
        is inclusive of the header itself.  Iteration continues until
        an End-Entire node (``type=0x7F``, ``subtype=0xFF``) is reached.
        Malformed input -- truncated header, length below the minimum
        four-byte header, or length beyond the remaining buffer -- is
        rejected with :class:`UefiProtocolError`.

        :param data: The raw device-path byte string.

        :returns: A :class:`DevicePath` carrying every node prior to the
                  End-Entire terminator.  The terminator itself is
                  stripped from the returned container.
        :raises: UefiProtocolError
    """
    if not isinstance(data, (bytes, bytearray, memoryview)):
        err_msg_lines = [
            "'data' must be a bytes-like object.",
            "GOT: value={!r} type={}".format(data, type(data).__name__),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiProtocolError(err_msg) from None

    buffer = bytes(data)
    buffer_length = len(buffer)

    nodes: List[DevicePathNode] = []
    offset = 0
    saw_terminator = False

    while offset < buffer_length:
        header_end = offset + _HEADER_LEN
        if header_end > buffer_length:
            err_msg_lines = [
                "Truncated device-path header.",
                "OFFSET: {}".format(offset),
                "REMAINING: {}".format(buffer_length - offset),
                "REQUIRED: {}".format(_HEADER_LEN),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiProtocolError(err_msg) from None

        node_type = buffer[offset]
        node_subtype = buffer[offset + 1]
        node_length = int.from_bytes(
            buffer[offset + 2:offset + 4], byteorder="little", signed=False
        )

        if node_length < _HEADER_LEN:
            err_msg_lines = [
                "Device-path node length is below the minimum header size.",
                "OFFSET: {}".format(offset),
                "OBSERVED: {}".format(node_length),
                "MINIMUM: {}".format(_HEADER_LEN),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiProtocolError(err_msg) from None

        node_end = offset + node_length
        if node_end > buffer_length:
            err_msg_lines = [
                "Device-path node length exceeds remaining buffer.",
                "OFFSET: {}".format(offset),
                "DECLARED: {}".format(node_length),
                "REMAINING: {}".format(buffer_length - offset),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiProtocolError(err_msg) from None

        payload = bytes(buffer[offset + _HEADER_LEN:node_end])

        if node_type == _END_TYPE and node_subtype == _END_ENTIRE_SUBTYPE:
            saw_terminator = True
            offset = node_end
            break

        node = DevicePathNode(
            type=node_type, subtype=node_subtype, data=payload,
        )
        nodes.append(node)
        offset = node_end

    if not saw_terminator:
        err_msg_lines = [
            "Device-path buffer is missing the End-Entire terminator.",
            "OFFSET: {}".format(offset),
            "BUFFER_LENGTH: {}".format(buffer_length),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiProtocolError(err_msg) from None

    rtnval = DevicePath(nodes=tuple(nodes))
    return rtnval


def encode_device_path(path: DevicePath) -> bytes:
    """
        Serialize a :class:`DevicePath` into its binary wire form per
        UEFI section 10.3.1.

        The encoder mirrors :func:`decode_device_path`: the 4-byte
        header of each node carries ``type``, ``subtype``, and
        ``length`` (LE u16), where ``length`` is the header plus the
        node's payload.  An End-Entire terminator (``type=0x7F``,
        ``subtype=0xFF``, ``length=4``) is always appended regardless of
        whether the caller passed one.

        :param path: The device path to serialize.

        :returns: The concatenated byte string including the trailing
                  End-Entire terminator.
        :raises: UefiProtocolError
    """
    if not isinstance(path, DevicePath):
        err_msg_lines = [
            "'path' must be a DevicePath.",
            "GOT: value={!r} type={}".format(path, type(path).__name__),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiProtocolError(err_msg) from None

    out = bytearray()
    for node_index, node in enumerate(path.nodes):
        if node.type == _END_TYPE and node.subtype == _END_ENTIRE_SUBTYPE:
            # Skip any End-Entire nodes the caller may have included;
            # the encoder always appends its own terminator so the output
            # has exactly one trailing end marker.
            continue

        body = node.data
        if not isinstance(body, (bytes, bytearray, memoryview)):
            err_msg_lines = [
                "DevicePathNode.data must be a bytes-like object.",
                "INDEX: {}".format(node_index),
                "TYPE: {}".format(node.type),
                "SUBTYPE: {}".format(node.subtype),
                "GOT: type={}".format(type(body).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiProtocolError(err_msg) from None

        body_bytes = bytes(body)
        total_length = _HEADER_LEN + len(body_bytes)
        if total_length > 0xFFFF:
            err_msg_lines = [
                "DevicePathNode encoded length exceeds UINT16 max.",
                "INDEX: {}".format(node_index),
                "TYPE: {}".format(node.type),
                "SUBTYPE: {}".format(node.subtype),
                "OBSERVED: {}".format(total_length),
                "MAXIMUM: {}".format(0xFFFF),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiProtocolError(err_msg) from None

        if not (0 <= node.type <= 0xFF):
            err_msg_lines = [
                "DevicePathNode.type is out of range for UINT8.",
                "INDEX: {}".format(node_index),
                "OBSERVED: {}".format(node.type),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiProtocolError(err_msg) from None

        if not (0 <= node.subtype <= 0xFF):
            err_msg_lines = [
                "DevicePathNode.subtype is out of range for UINT8.",
                "INDEX: {}".format(node_index),
                "OBSERVED: {}".format(node.subtype),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiProtocolError(err_msg) from None

        header = bytes([node.type, node.subtype])
        header = header + total_length.to_bytes(length=2, byteorder="little", signed=False)
        out.extend(header)
        out.extend(body_bytes)

    # Append the End-Entire terminator.
    terminator = bytes([_END_TYPE, _END_ENTIRE_SUBTYPE])
    terminator = terminator + (_HEADER_LEN).to_bytes(length=2, byteorder="little", signed=False)
    out.extend(terminator)

    rtnval = bytes(out)
    return rtnval


# ---------------------------------------------------------------------------
# GUID mixed-endian helpers
# ---------------------------------------------------------------------------
#
# UEFI stores GUIDs in the mixed-endian form defined by EFI_GUID: the
# first three fields are little-endian integers while the final two
# fields are a big-endian 2-byte "Data4a" and a 6-byte "Data4b" byte
# array.  :meth:`uuid.UUID.bytes_le` already matches that layout so we
# lean on it directly.
# ---------------------------------------------------------------------------

def _guid_to_bytes(g: uuid.UUID) -> bytes:
    """
        Convert a :class:`uuid.UUID` into the 16-byte EFI_GUID buffer.

        :param g: The GUID to convert.

        :returns: A 16-byte little-endian EFI_GUID representation.
    """
    rtnval = g.bytes_le
    return rtnval


def _guid_from_bytes(b: bytes) -> uuid.UUID:
    """
        Decode a 16-byte EFI_GUID buffer into a :class:`uuid.UUID`.

        :param b: The 16-byte buffer to decode.

        :returns: The parsed :class:`uuid.UUID`.
        :raises: UefiProtocolError
    """
    if len(b) != 16:
        err_msg_lines = [
            "EFI_GUID buffer must be exactly 16 bytes.",
            "OBSERVED: {}".format(len(b)),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiProtocolError(err_msg) from None

    rtnval = uuid.UUID(bytes_le=bytes(b))
    return rtnval


# ---------------------------------------------------------------------------
# ACPI _HID EISA-ID helpers
# ---------------------------------------------------------------------------

def _eisa_id_to_text(hid: int) -> str:
    """
        Format an ACPI ``_HID`` integer as its seven-character EISA text
        form (e.g. ``PNP0A03``).

        The ``_HID`` layout per ACPI Appendix A packs three 5-bit
        alphabetic-offset letters in the low 16 bits of the integer and
        four hexadecimal product digits in the high 16 bits.

        :param hid: The raw 32-bit ``_HID`` value.

        :returns: The EISA-ID seven-character string.
    """
    # The low 16 bits of _HID pack three 5-bit letter indices biased by
    # 0x40 ('@' == 0, 'A' == 1, ...).  Bit 15 is reserved (zero).  The
    # high 16 bits carry four hexadecimal product digits.  _HID values
    # are stored little-endian on the wire so after decoding to an int,
    # the low 16 bits directly yield the three letters.
    vendor_word = hid & 0xFFFF

    letter1 = (vendor_word >> 10) & 0x1F
    letter2 = (vendor_word >> 5) & 0x1F
    letter3 = vendor_word & 0x1F
    product = (hid >> 16) & 0xFFFF

    rtnval = "{}{}{}{:04X}".format(
        chr(0x40 + letter1),
        chr(0x40 + letter2),
        chr(0x40 + letter3),
        product,
    )
    return rtnval


def _eisa_id_from_text(text: str) -> int:
    """
        Parse a seven-character EISA-ID such as ``PNP0A03`` back into
        the ACPI ``_HID`` integer encoding.

        :param text: The EISA text form.

        :returns: The encoded ``_HID`` integer.
        :raises: UefiProtocolError
    """
    if len(text) != 7:
        err_msg_lines = [
            "EISA-ID text must be exactly 7 characters.",
            "OBSERVED: value={!r} length={}".format(text, len(text)),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiProtocolError(err_msg) from None

    upper_text = text.upper()
    letter1 = ord(upper_text[0]) - 0x40
    letter2 = ord(upper_text[1]) - 0x40
    letter3 = ord(upper_text[2]) - 0x40
    try:
        product = int(upper_text[3:7], 16)
    except ValueError as verr:
        err_msg_lines = [
            "EISA-ID product digits must be hexadecimal.",
            "OBSERVED: value={!r}".format(text),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiProtocolError(err_msg) from verr

    vendor_word = ((letter1 & 0x1F) << 10) | ((letter2 & 0x1F) << 5) | (letter3 & 0x1F)

    rtnval = (product << 16) | vendor_word
    return rtnval


# ---------------------------------------------------------------------------
# Text codec -- formatting helpers (UEFI section 10.6)
# ---------------------------------------------------------------------------

def _format_hex_run(buf: bytes) -> str:
    """
        Format a bytes payload as an unseparated uppercase hex run as
        used by UEFI section 10.6.1.6 generic ``Path(t,s,hex)`` form.

        :param buf: The payload bytes.

        :returns: The uppercase hex run string.
    """
    rtnval = buf.hex().upper()
    return rtnval


def _parse_hex_run(text: str) -> bytes:
    """
        Parse an unseparated hex run back into bytes.  The input must
        contain an even number of hex digits.

        :param text: The hex run string.

        :returns: The decoded bytes.
        :raises: UefiProtocolError
    """
    if (len(text) % 2) != 0:
        err_msg_lines = [
            "Hex run must have an even number of characters.",
            "OBSERVED: value={!r} length={}".format(text, len(text)),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiProtocolError(err_msg) from None

    try:
        rtnval = bytes.fromhex(text)
    except ValueError as verr:
        err_msg_lines = [
            "Hex run contains non-hexadecimal characters.",
            "OBSERVED: value={!r}".format(text),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiProtocolError(err_msg) from verr

    return rtnval


def _parse_integer_literal(text: str) -> int:
    """
        Parse an integer literal allowing optional ``0x`` / ``0X`` prefix
        for hex, per UEFI section 10.6.1.6 (``Integer`` row in Table 10.66).

        :param text: The literal string.

        :returns: The parsed integer value.
        :raises: UefiProtocolError
    """
    stripped = text.strip()
    if len(stripped) == 0:
        err_msg = "Empty integer literal."
        raise UefiProtocolError(err_msg) from None

    try:
        if stripped.startswith("0x") or stripped.startswith("0X"):
            rtnval = int(stripped, 16)
        elif stripped.startswith("-0x") or stripped.startswith("-0X"):
            rtnval = -int(stripped[3:], 16)
        else:
            rtnval = int(stripped, 10)
    except ValueError as verr:
        err_msg_lines = [
            "Malformed integer literal.",
            "OBSERVED: value={!r}".format(text),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiProtocolError(err_msg) from verr

    return rtnval


def _format_mac_address(mac: bytes) -> str:
    """
        Format a MAC-address byte run as the unseparated uppercase hex
        form used by UEFI section 10.6 (e.g. ``001320F5FA77``).

        :param mac: The 6-byte MAC address.

        :returns: The hex-run MAC string.
    """
    rtnval = mac.hex().upper()
    return rtnval


def _parse_mac_address(text: str) -> bytes:
    """
        Parse the unseparated MAC-address hex form back into bytes.

        :param text: The hex string.

        :returns: The decoded MAC bytes.
        :raises: UefiProtocolError
    """
    rtnval = _parse_hex_run(text)
    return rtnval


def _format_ipv4(buf: bytes) -> str:
    """
        Format a 4-byte IPv4 address buffer as dotted-decimal.

        :param buf: The 4-byte address.

        :returns: The dotted-decimal IPv4 string.
        :raises: UefiProtocolError
    """
    if len(buf) != 4:
        err_msg_lines = [
            "IPv4 address buffer must be exactly 4 bytes.",
            "OBSERVED: {}".format(len(buf)),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiProtocolError(err_msg) from None

    rtnval = "{}.{}.{}.{}".format(buf[0], buf[1], buf[2], buf[3])
    return rtnval


def _parse_ipv4(text: str) -> bytes:
    """
        Parse a dotted-decimal IPv4 literal back to a 4-byte buffer.

        :param text: The dotted-decimal string.

        :returns: The 4-byte address buffer.
        :raises: UefiProtocolError
    """
    parts = text.split(".")
    if len(parts) != 4:
        err_msg_lines = [
            "IPv4 literal must have exactly four dotted octets.",
            "OBSERVED: value={!r}".format(text),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiProtocolError(err_msg) from None

    octets: List[int] = []
    for part in parts:
        try:
            octet_value = int(part, 10)
        except ValueError as verr:
            err_msg_lines = [
                "IPv4 literal contains a non-numeric octet.",
                "OBSERVED: value={!r}".format(text),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiProtocolError(err_msg) from verr

        if octet_value < 0 or octet_value > 255:
            err_msg_lines = [
                "IPv4 literal octet out of range.",
                "OBSERVED: value={!r} octet={}".format(text, octet_value),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiProtocolError(err_msg) from None

        octets.append(octet_value)

    rtnval = bytes(octets)
    return rtnval


def _format_ipv6(buf: bytes) -> str:
    """
        Format a 16-byte IPv6 address buffer as its RFC4291 section 2.2
        colon-hex representation (no compression; the UEFI codec keeps
        the representation stable for easy round-tripping).

        :param buf: The 16-byte address.

        :returns: The colon-separated hex string.
        :raises: UefiProtocolError
    """
    if len(buf) != 16:
        err_msg_lines = [
            "IPv6 address buffer must be exactly 16 bytes.",
            "OBSERVED: {}".format(len(buf)),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiProtocolError(err_msg) from None

    groups: List[str] = []
    for group_index in range(8):
        lo = group_index * 2
        hi = lo + 2
        group_value = int.from_bytes(buf[lo:hi], byteorder="big", signed=False)
        groups.append("{:X}".format(group_value))

    rtnval = ":".join(groups)
    return rtnval


def _parse_ipv6(text: str) -> bytes:
    """
        Parse an uncompressed colon-hex IPv6 literal (8 groups) back
        into a 16-byte buffer.

        :param text: The colon-hex string.

        :returns: The 16-byte address.
        :raises: UefiProtocolError
    """
    parts = text.split(":")
    if len(parts) != 8:
        err_msg_lines = [
            "IPv6 literal must contain exactly eight colon-separated groups.",
            "OBSERVED: value={!r} groups={}".format(text, len(parts)),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiProtocolError(err_msg) from None

    out = bytearray()
    for part in parts:
        try:
            group_value = int(part, 16)
        except ValueError as verr:
            err_msg_lines = [
                "IPv6 literal group is not hexadecimal.",
                "OBSERVED: value={!r} group={!r}".format(text, part),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiProtocolError(err_msg) from verr

        if group_value < 0 or group_value > 0xFFFF:
            err_msg_lines = [
                "IPv6 literal group out of range.",
                "OBSERVED: value={!r} group={!r}".format(text, part),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiProtocolError(err_msg) from None

        out.extend(group_value.to_bytes(length=2, byteorder="big", signed=False))

    rtnval = bytes(out)
    return rtnval


# ---------------------------------------------------------------------------
# Per-node text formatters (binary payload -> canonical text form)
# ---------------------------------------------------------------------------
#
# Every formatter is invoked with a fully-constructed DevicePathNode and
# returns the text rendering (without a trailing '/').  A formatter that
# cannot fully understand the payload is free to raise
# :class:`UefiProtocolError` -- the top-level :func:`device_path_to_text`
# function falls back to the generic ``Path(t,s,hex)`` rendering only
# when the registry lacks an entry.
# ---------------------------------------------------------------------------

def _format_pci(node: DevicePathNode) -> str:
    """
        Format a (type=1, subtype=1) PCI node as ``Pci(<device>,<function>)``
        per UEFI section 10.6 Table 10.67.  Spec ordering is
        ``Pci(Device, Function)``; both fields are UINT8.

        :param node: The PCI node.

        :returns: The ``Pci(0xNN,0xM)`` text form.
    """
    # Binary layout per section 10.3.2.1 is (function, device) -- two
    # bytes following the header.
    if len(node.data) != 2:
        err_msg_lines = [
            "PCI node payload must be 2 bytes.",
            "OBSERVED: {}".format(len(node.data)),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiProtocolError(err_msg) from None

    function = node.data[0]
    device = node.data[1]
    rtnval = "Pci(0x{:X},0x{:X})".format(device, function)
    return rtnval


def _format_acpi(node: DevicePathNode) -> str:
    """
        Format a (type=2, subtype=1) ACPI node, preferring the
        named-forms (``PciRoot(...)``, ``PcieRoot(...)``, ``Floppy(...)``,
        ``Keyboard(...)``, ``Serial(...)``, ``ParallelPort(...)``) when
        the ``_HID`` matches a well-known EISA-ID per UEFI section
        10.6 Table 10.67.

        :param node: The ACPI node.

        :returns: The best-form text rendering.
    """
    if len(node.data) != 8:
        err_msg_lines = [
            "ACPI node payload must be 8 bytes.",
            "OBSERVED: {}".format(len(node.data)),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiProtocolError(err_msg) from None

    hid = int.from_bytes(node.data[0:4], byteorder="little", signed=False)
    uid = int.from_bytes(node.data[4:8], byteorder="little", signed=False)

    # Named forms per UEFI §10.6.1.6 Table 10.67.  Only the handful of
    # formally-standardized names are emitted here; Serial (PNP0501) and
    # ParallelPort (PNP0401) are not part of the formal named set and must
    # fall back to the generic ``Acpi(EISA-ID,UID)`` rendering.
    if hid == _EISA_PNP0A03:
        rtnval = "PciRoot(0x{:X})".format(uid)
    elif hid == _EISA_PNP0A08:
        rtnval = "PcieRoot(0x{:X})".format(uid)
    elif hid == _EISA_PNP0604:
        rtnval = "Floppy(0x{:X})".format(uid)
    elif hid == _EISA_PNP0301:
        rtnval = "Keyboard(0x{:X})".format(uid)
    else:
        eisa_text = _eisa_id_to_text(hid)
        rtnval = "Acpi({},0x{:X})".format(eisa_text, uid)

    return rtnval


def _format_usb(node: DevicePathNode) -> str:
    """
        Format a (type=3, subtype=5) USB node as ``USB(<port>,<interface>)``
        per UEFI section 10.6 Table 10.67.

        :param node: The USB node.

        :returns: The ``USB(...)`` text form.
    """
    if len(node.data) != 2:
        err_msg_lines = [
            "USB node payload must be 2 bytes.",
            "OBSERVED: {}".format(len(node.data)),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiProtocolError(err_msg) from None

    port = node.data[0]
    interface = node.data[1]
    rtnval = "USB(0x{:X},0x{:X})".format(port, interface)
    return rtnval


def _format_sata(node: DevicePathNode) -> str:
    """
        Format a (type=3, subtype=0x12) SATA node as
        ``Sata(<hba_port>,<port_multiplier>,<lun>)`` per UEFI section
        10.6 Table 10.67.

        :param node: The SATA node.

        :returns: The ``Sata(...)`` text form.
    """
    if len(node.data) != 6:
        err_msg_lines = [
            "SATA node payload must be 6 bytes.",
            "OBSERVED: {}".format(len(node.data)),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiProtocolError(err_msg) from None

    hba_port = int.from_bytes(node.data[0:2], byteorder="little", signed=False)
    pmpn = int.from_bytes(node.data[2:4], byteorder="little", signed=False)
    lun = int.from_bytes(node.data[4:6], byteorder="little", signed=False)
    rtnval = "Sata(0x{:X},0x{:X},0x{:X})".format(hba_port, pmpn, lun)
    return rtnval


def _format_nvme(node: DevicePathNode) -> str:
    """
        Format a (type=3, subtype=0x17) NVMe namespace node as
        ``NVMe(<nsid>,<eui64>)`` per UEFI section 10.6 Table 10.67.

        :param node: The NVMe node.

        :returns: The ``NVMe(...)`` text form.
    """
    if len(node.data) != 12:
        err_msg_lines = [
            "NVMe namespace node payload must be 12 bytes.",
            "OBSERVED: {}".format(len(node.data)),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiProtocolError(err_msg) from None

    nsid = int.from_bytes(node.data[0:4], byteorder="little", signed=False)
    eui_bytes = node.data[4:12]
    eui_parts = ["{:02X}".format(b) for b in eui_bytes]
    eui_text = "-".join(eui_parts)
    rtnval = "NVMe(0x{:X},{})".format(nsid, eui_text)
    return rtnval


def _format_mac(node: DevicePathNode) -> str:
    """
        Format a (type=3, subtype=0x0B) MAC-Address node as
        ``MAC(<macaddr>,<iftype>)`` per UEFI section 10.6 Table 10.67.

        The on-wire payload is a 32-byte MAC-address field followed by
        a single IfType UINT8 (section 10.3.4.10).  The MAC address is
        rendered as an unseparated uppercase hex run of the first six
        bytes to match the spec's sample output (e.g. ``001320F5FA77``).

        :param node: The MAC node.

        :returns: The ``MAC(...)`` text form.
    """
    if len(node.data) != 33:
        err_msg_lines = [
            "MAC node payload must be 33 bytes.",
            "OBSERVED: {}".format(len(node.data)),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiProtocolError(err_msg) from None

    mac_bytes = node.data[0:6]
    if_type = node.data[32]
    rtnval = "MAC({},0x{:X})".format(_format_mac_address(mac_bytes), if_type)
    return rtnval


def _format_ipv4_node(node: DevicePathNode) -> str:
    """
        Format a (type=3, subtype=0x0C) IPv4 node per UEFI section
        10.6 Table 10.67 as
        ``IPv4(<remoteip>,<protocol>,<static_or_dhcp>,<localip>)``.

        The 23-byte legacy payload lacks the later-added ``Gateway`` and
        ``SubnetMask`` fields; the 27-byte modern payload includes them.
        Both are accepted.

        :param node: The IPv4 node.

        :returns: The ``IPv4(...)`` text form.
    """
    if len(node.data) not in (23, 27):
        err_msg_lines = [
            "IPv4 node payload must be 23 or 27 bytes.",
            "OBSERVED: {}".format(len(node.data)),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiProtocolError(err_msg) from None

    local_ip = _format_ipv4(node.data[0:4])
    remote_ip = _format_ipv4(node.data[4:8])
    # local_port (2) at offset 8, remote_port (2) at offset 10.
    protocol = int.from_bytes(node.data[12:14], byteorder="little", signed=False)
    static_ip = node.data[14]
    static_text = "Static" if static_ip != 0 else "DHCP"
    rtnval = "IPv4({},0x{:X},{},{})".format(remote_ip, protocol, static_text, local_ip)
    return rtnval


def _format_ipv6_node(node: DevicePathNode) -> str:
    """
        Format a (type=3, subtype=0x0D) IPv6 node per UEFI section
        10.6 Table 10.67 as
        ``IPv6(<remoteip>,<protocol>,<origin>,<localip>)``.

        :param node: The IPv6 node.

        :returns: The ``IPv6(...)`` text form.
    """
    if len(node.data) != 56:
        err_msg_lines = [
            "IPv6 node payload must be 56 bytes.",
            "OBSERVED: {}".format(len(node.data)),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiProtocolError(err_msg) from None

    local_ip = _format_ipv6(node.data[0:16])
    remote_ip = _format_ipv6(node.data[16:32])
    # local_port (2) at 32, remote_port (2) at 34.
    protocol = int.from_bytes(node.data[36:38], byteorder="little", signed=False)
    origin = node.data[38]
    rtnval = "IPv6({},0x{:X},0x{:X},{})".format(remote_ip, protocol, origin, local_ip)
    return rtnval


def _format_uri(node: DevicePathNode) -> str:
    """
        Format a (type=3, subtype=0x18) URI node as ``Uri(<string>)``
        per UEFI section 10.6 Table 10.67.  The payload is the URI
        string encoded as UTF-8 without a null terminator.

        :param node: The URI node.

        :returns: The ``Uri(...)`` text form.
    """
    try:
        uri_text = node.data.decode("utf-8")
    except UnicodeDecodeError as derr:
        err_msg_lines = [
            "URI node payload is not valid UTF-8.",
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiProtocolError(err_msg) from derr

    rtnval = "Uri({})".format(uri_text)
    return rtnval


def _format_vendor(node: DevicePathNode, option_name: str) -> str:
    """
        Format any of the three Vendor node shapes (hardware/messaging/
        media) as ``<option_name>(<guid>,<hex>)`` per UEFI section
        10.6 Table 10.67.

        :param node: The vendor node.
        :param option_name: One of ``VenHw``, ``VenMsg``, ``VenMedia``.

        :returns: The canonical text form.
    """
    if len(node.data) < 16:
        err_msg_lines = [
            "Vendor node payload must carry at least a 16-byte GUID.",
            "OBSERVED: {}".format(len(node.data)),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiProtocolError(err_msg) from None

    guid = _guid_from_bytes(node.data[0:16])
    remainder = node.data[16:]
    if len(remainder) == 0:
        rtnval = "{}({})".format(option_name, str(guid).upper())
    else:
        rtnval = "{}({},{})".format(option_name, str(guid).upper(), _format_hex_run(remainder))
    return rtnval


def _format_venhw(node: DevicePathNode) -> str:
    """
        Format a (type=1, subtype=4) Hardware Vendor node.

        :param node: The vendor-hardware node.

        :returns: The ``VenHw(...)`` text form.
    """
    rtnval = _format_vendor(node=node, option_name="VenHw")
    return rtnval


def _format_venmsg(node: DevicePathNode) -> str:
    """
        Format a (type=3, subtype=0x0A) Vendor-Defined Messaging node.

        :param node: The vendor-messaging node.

        :returns: The ``VenMsg(...)`` text form.
    """
    rtnval = _format_vendor(node=node, option_name="VenMsg")
    return rtnval


def _format_venmedia(node: DevicePathNode) -> str:
    """
        Format a (type=4, subtype=3) Vendor-Defined Media node.

        :param node: The vendor-media node.

        :returns: The ``VenMedia(...)`` text form.
    """
    rtnval = _format_vendor(node=node, option_name="VenMedia")
    return rtnval


def _format_hard_drive(node: DevicePathNode) -> str:
    """
        Format a (type=4, subtype=1) Hard Drive node as
        ``HD(<partition>,<type>,<signature>,<start>,<size>)`` per UEFI
        section 10.6 Table 10.67.  ``type`` is rendered as the keyword
        ``MBR`` (1) or ``GPT`` (2) when it matches one of those known
        values, otherwise as a hexadecimal integer.

        :param node: The Hard-Drive node.

        :returns: The ``HD(...)`` text form.
    """
    if len(node.data) != 38:
        err_msg_lines = [
            "Hard-Drive node payload must be 38 bytes.",
            "OBSERVED: {}".format(len(node.data)),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiProtocolError(err_msg) from None

    partition_number = int.from_bytes(node.data[0:4], byteorder="little", signed=False)
    partition_start = int.from_bytes(node.data[4:12], byteorder="little", signed=False)
    partition_size = int.from_bytes(node.data[12:20], byteorder="little", signed=False)
    signature = node.data[20:36]
    partition_format = node.data[36]
    signature_type = node.data[37]

    if partition_format == 0x01:
        format_text = "MBR"
    elif partition_format == 0x02:
        format_text = "GPT"
    else:
        format_text = "0x{:X}".format(partition_format)

    if signature_type == 0x02:
        signature_text = str(_guid_from_bytes(signature)).upper()
    elif signature_type == 0x01:
        sig_int = int.from_bytes(signature[0:4], byteorder="little", signed=False)
        signature_text = "0x{:X}".format(sig_int)
    else:
        signature_text = _format_hex_run(signature)

    rtnval = "HD({},{},{},0x{:X},0x{:X})".format(
        partition_number, format_text, signature_text, partition_start, partition_size
    )
    return rtnval


def _format_cdrom(node: DevicePathNode) -> str:
    """
        Format a (type=4, subtype=2) CD-ROM node as
        ``CDROM(<bootentry>,<partstart>,<partsize>)`` per UEFI section
        10.6 Table 10.67.

        :param node: The CD-ROM node.

        :returns: The ``CDROM(...)`` text form.
    """
    if len(node.data) != 20:
        err_msg_lines = [
            "CD-ROM node payload must be 20 bytes.",
            "OBSERVED: {}".format(len(node.data)),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiProtocolError(err_msg) from None

    boot_entry = int.from_bytes(node.data[0:4], byteorder="little", signed=False)
    partition_start = int.from_bytes(node.data[4:12], byteorder="little", signed=False)
    partition_size = int.from_bytes(node.data[12:20], byteorder="little", signed=False)
    rtnval = "CDROM(0x{:X},0x{:X},0x{:X})".format(
        boot_entry, partition_start, partition_size
    )
    return rtnval


def _format_file_path(node: DevicePathNode) -> str:
    """
        Format a (type=4, subtype=4) File-Path node as ``File(<path>)``
        per UEFI section 10.6 Table 10.67.  The payload is a null-
        terminated UCS-2 buffer; the null terminator is stripped.

        :param node: The File-Path node.

        :returns: The ``File(...)`` text form.
    """
    if (len(node.data) % 2) != 0:
        err_msg_lines = [
            "File-Path node payload length must be even (UCS-2).",
            "OBSERVED: {}".format(len(node.data)),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiProtocolError(err_msg) from None

    decoded = ucs2_decode(node.data)
    # Strip trailing null terminators as emitted by firmware.
    while len(decoded) > 0 and decoded[-1] == "\x00":
        decoded = decoded[:-1]

    rtnval = "File({})".format(decoded)
    return rtnval


def _format_piwg_fv(node: DevicePathNode) -> str:
    """
        Format a (type=4, subtype=7) PIWG Firmware Volume node as
        ``Fv(<guid>)`` per UEFI section 10.6 Table 10.67.

        :param node: The Firmware Volume node.

        :returns: The ``Fv(...)`` text form.
    """
    if len(node.data) != 16:
        err_msg_lines = [
            "PIWG Firmware Volume node payload must be 16 bytes.",
            "OBSERVED: {}".format(len(node.data)),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiProtocolError(err_msg) from None

    guid = _guid_from_bytes(node.data)
    rtnval = "Fv({})".format(str(guid).upper())
    return rtnval


def _format_piwg_ff(node: DevicePathNode) -> str:
    """
        Format a (type=4, subtype=6) PIWG Firmware File node as
        ``FvFile(<guid>)`` per UEFI section 10.6 Table 10.67.

        :param node: The Firmware File node.

        :returns: The ``FvFile(...)`` text form.
    """
    if len(node.data) != 16:
        err_msg_lines = [
            "PIWG Firmware File node payload must be 16 bytes.",
            "OBSERVED: {}".format(len(node.data)),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiProtocolError(err_msg) from None

    guid = _guid_from_bytes(node.data)
    rtnval = "FvFile({})".format(str(guid).upper())
    return rtnval


# ---------------------------------------------------------------------------
# Per-node text parsers (argument list -> binary payload bytes)
# ---------------------------------------------------------------------------
#
# Each text parser receives the sequence of already-split argument
# strings (between the enclosing parentheses, with outer whitespace
# trimmed) and returns the complete :class:`DevicePathNode` -- including
# type and subtype.  This keeps the dispatch registry symmetric with the
# formatters.
# ---------------------------------------------------------------------------

def _parse_pci(args: List[str]) -> DevicePathNode:
    """
        Parse ``Pci(<device>,<function>)`` argument list.

        :param args: The two argument tokens.

        :returns: The PCI :class:`DevicePathNode`.
        :raises: UefiProtocolError
    """
    _expect_argc(option_name="Pci", args=args, expected=2)
    device = _parse_integer_literal(args[0])
    function = _parse_integer_literal(args[1])
    payload = bytes([function & 0xFF, device & 0xFF])
    rtnval = DevicePathNode(type=0x01, subtype=0x01, data=payload)
    return rtnval


def _parse_acpi(args: List[str]) -> DevicePathNode:
    """
        Parse ``Acpi(<eisa>,<uid>)`` argument list.

        :param args: The two argument tokens (``UID`` defaults to 0).

        :returns: The ACPI :class:`DevicePathNode`.
        :raises: UefiProtocolError
    """
    if len(args) == 1:
        hid_text = args[0]
        uid_value = 0
    elif len(args) == 2:
        hid_text = args[0]
        uid_value = _parse_integer_literal(args[1])
    else:
        err_msg_lines = [
            "Acpi(...) accepts 1 or 2 arguments.",
            "OBSERVED: {}".format(len(args)),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiProtocolError(err_msg) from None

    hid_value = _eisa_id_from_text(hid_text)
    payload = hid_value.to_bytes(length=4, byteorder="little", signed=False)
    payload = payload + uid_value.to_bytes(length=4, byteorder="little", signed=False)
    rtnval = DevicePathNode(type=0x02, subtype=0x01, data=payload)
    return rtnval


def _build_acpi_named(hid_value: int, args: List[str], option_name: str) -> DevicePathNode:
    """
        Build an ACPI node for one of the named forms (e.g. ``PciRoot``).

        :param hid_value: The pre-determined ``_HID`` EISA encoding.
        :param args: The text argument list (``UID`` only).
        :param option_name: The originating option-name (for diagnostics).

        :returns: The ACPI :class:`DevicePathNode`.
        :raises: UefiProtocolError
    """
    if len(args) == 0:
        uid_value = 0
    elif len(args) == 1:
        uid_value = _parse_integer_literal(args[0])
    else:
        err_msg_lines = [
            "{}(...) accepts 0 or 1 arguments.".format(option_name),
            "OBSERVED: {}".format(len(args)),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiProtocolError(err_msg) from None

    payload = hid_value.to_bytes(length=4, byteorder="little", signed=False)
    payload = payload + uid_value.to_bytes(length=4, byteorder="little", signed=False)
    rtnval = DevicePathNode(type=0x02, subtype=0x01, data=payload)
    return rtnval


def _parse_pciroot(args: List[str]) -> DevicePathNode:
    rtnval = _build_acpi_named(hid_value=_EISA_PNP0A03, args=args, option_name="PciRoot")
    return rtnval


def _parse_pcieroot(args: List[str]) -> DevicePathNode:
    rtnval = _build_acpi_named(hid_value=_EISA_PNP0A08, args=args, option_name="PcieRoot")
    return rtnval


def _parse_floppy(args: List[str]) -> DevicePathNode:
    rtnval = _build_acpi_named(hid_value=_EISA_PNP0604, args=args, option_name="Floppy")
    return rtnval


def _parse_keyboard(args: List[str]) -> DevicePathNode:
    rtnval = _build_acpi_named(hid_value=_EISA_PNP0301, args=args, option_name="Keyboard")
    return rtnval


def _parse_serial(args: List[str]) -> DevicePathNode:
    rtnval = _build_acpi_named(hid_value=_EISA_PNP0501, args=args, option_name="Serial")
    return rtnval


def _parse_parallelport(args: List[str]) -> DevicePathNode:
    rtnval = _build_acpi_named(hid_value=_EISA_PNP0401, args=args, option_name="ParallelPort")
    return rtnval


def _parse_usb(args: List[str]) -> DevicePathNode:
    """
        Parse ``USB(<port>,<interface>)``.

        :param args: The two argument tokens.

        :returns: The USB :class:`DevicePathNode`.
    """
    _expect_argc(option_name="USB", args=args, expected=2)
    port = _parse_integer_literal(args[0])
    interface = _parse_integer_literal(args[1])
    payload = bytes([port & 0xFF, interface & 0xFF])
    rtnval = DevicePathNode(type=0x03, subtype=0x05, data=payload)
    return rtnval


def _parse_sata(args: List[str]) -> DevicePathNode:
    """
        Parse ``Sata(<hba_port>,<port_multiplier>,<lun>)``.

        :param args: The three argument tokens.

        :returns: The SATA :class:`DevicePathNode`.
    """
    _expect_argc(option_name="Sata", args=args, expected=3)
    hba_port = _parse_integer_literal(args[0])
    pmpn = _parse_integer_literal(args[1])
    lun = _parse_integer_literal(args[2])
    payload = hba_port.to_bytes(length=2, byteorder="little", signed=False)
    payload = payload + pmpn.to_bytes(length=2, byteorder="little", signed=False)
    payload = payload + lun.to_bytes(length=2, byteorder="little", signed=False)
    rtnval = DevicePathNode(type=0x03, subtype=0x12, data=payload)
    return rtnval


def _parse_nvme(args: List[str]) -> DevicePathNode:
    """
        Parse ``NVMe(<nsid>,<eui>)``.

        :param args: The two argument tokens.

        :returns: The NVMe :class:`DevicePathNode`.
    """
    _expect_argc(option_name="NVMe", args=args, expected=2)
    nsid = _parse_integer_literal(args[0])
    eui_text = args[1].replace("-", "")
    eui_bytes = _parse_hex_run(eui_text)
    if len(eui_bytes) != 8:
        err_msg_lines = [
            "NVMe EUI-64 must decode to exactly 8 bytes.",
            "OBSERVED: {}".format(len(eui_bytes)),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiProtocolError(err_msg) from None

    payload = nsid.to_bytes(length=4, byteorder="little", signed=False) + eui_bytes
    rtnval = DevicePathNode(type=0x03, subtype=0x17, data=payload)
    return rtnval


def _parse_mac_node(args: List[str]) -> DevicePathNode:
    """
        Parse ``MAC(<macaddr>,<iftype>)``.

        :param args: The two argument tokens.

        :returns: The MAC-Address :class:`DevicePathNode`.
    """
    _expect_argc(option_name="MAC", args=args, expected=2)
    mac_bytes = _parse_mac_address(args[0])
    if len(mac_bytes) != 6:
        err_msg_lines = [
            "MAC address must decode to exactly 6 bytes.",
            "OBSERVED: {}".format(len(mac_bytes)),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiProtocolError(err_msg) from None

    if_type = _parse_integer_literal(args[1])
    # The spec MAC-Address payload is 32 bytes of MAC + 1 byte IfType.
    padding = b"\x00" * (32 - len(mac_bytes))
    payload = mac_bytes + padding + bytes([if_type & 0xFF])
    rtnval = DevicePathNode(type=0x03, subtype=0x0B, data=payload)
    return rtnval


def _parse_ipv4_node(args: List[str]) -> DevicePathNode:
    """
        Parse ``IPv4(<remoteip>,<protocol>,<static|dhcp>,<localip>)``.

        :param args: The four argument tokens.

        :returns: The IPv4 :class:`DevicePathNode`.
    """
    _expect_argc(option_name="IPv4", args=args, expected=4)
    remote_ip = _parse_ipv4(args[0])
    protocol = _parse_integer_literal(args[1])
    static_token = args[2].strip().lower()
    if static_token in ("static", "1"):
        static_ip = 1
    elif static_token in ("dhcp", "0"):
        static_ip = 0
    else:
        err_msg_lines = [
            "IPv4 'static' token must be 'Static' or 'DHCP'.",
            "OBSERVED: value={!r}".format(args[2]),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiProtocolError(err_msg) from None

    local_ip = _parse_ipv4(args[3])

    # Use the 23-byte minimum layout per UEFI section 10.3.4.11.
    # Gateway and SubnetMask default to 0.0.0.0 when not supplied via
    # the short 4-argument text form.
    payload = local_ip + remote_ip
    payload = payload + (0).to_bytes(length=2, byteorder="little", signed=False)  # local_port
    payload = payload + (0).to_bytes(length=2, byteorder="little", signed=False)  # remote_port
    payload = payload + protocol.to_bytes(length=2, byteorder="little", signed=False)
    payload = payload + bytes([static_ip & 0xFF])
    payload = payload + (b"\x00" * 4)   # gateway
    payload = payload + (b"\x00" * 4)   # subnet_mask
    rtnval = DevicePathNode(type=0x03, subtype=0x0C, data=payload)
    return rtnval


def _parse_ipv6_node(args: List[str]) -> DevicePathNode:
    """
        Parse ``IPv6(<remoteip>,<protocol>,<origin>,<localip>)``.

        :param args: The four argument tokens.

        :returns: The IPv6 :class:`DevicePathNode`.
    """
    _expect_argc(option_name="IPv6", args=args, expected=4)
    remote_ip = _parse_ipv6(args[0])
    protocol = _parse_integer_literal(args[1])
    origin = _parse_integer_literal(args[2])
    local_ip = _parse_ipv6(args[3])

    payload = local_ip + remote_ip
    payload = payload + (0).to_bytes(length=2, byteorder="little", signed=False)  # local_port
    payload = payload + (0).to_bytes(length=2, byteorder="little", signed=False)  # remote_port
    payload = payload + protocol.to_bytes(length=2, byteorder="little", signed=False)
    payload = payload + bytes([origin & 0xFF])
    payload = payload + bytes([0])  # prefix_length -- not represented in the short form
    payload = payload + (b"\x00" * 16)  # gateway
    rtnval = DevicePathNode(type=0x03, subtype=0x0D, data=payload)
    return rtnval


def _parse_uri(args: List[str]) -> DevicePathNode:
    """
        Parse ``Uri(<uri_string>)``.

        :param args: The single argument token.

        :returns: The URI :class:`DevicePathNode`.
    """
    if len(args) == 0:
        uri_text = ""
    elif len(args) == 1:
        uri_text = args[0]
    else:
        err_msg_lines = [
            "Uri(...) accepts 0 or 1 arguments.",
            "OBSERVED: {}".format(len(args)),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiProtocolError(err_msg) from None

    rtnval = DevicePathNode(type=0x03, subtype=0x18, data=uri_text.encode("utf-8"))
    return rtnval


def _parse_hard_drive(args: List[str]) -> DevicePathNode:
    """
        Parse ``HD(<partnum>,<type>,<sig>,<start>,<size>)``.

        :param args: The five argument tokens.

        :returns: The Hard-Drive :class:`DevicePathNode`.
    """
    _expect_argc(option_name="HD", args=args, expected=5)
    partition_number = _parse_integer_literal(args[0])

    format_token = args[1].strip().upper()
    if format_token == "MBR":
        partition_format = 0x01
        signature_type = 0x01
    elif format_token == "GPT":
        partition_format = 0x02
        signature_type = 0x02
    else:
        partition_format = _parse_integer_literal(args[1])
        signature_type = 0x00

    signature_text = args[2].strip()
    if signature_type == 0x02:
        try:
            signature_guid = uuid.UUID(signature_text)
        except ValueError as verr:
            err_msg_lines = [
                "HD(...) GPT signature must be a GUID.",
                "OBSERVED: value={!r}".format(signature_text),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiProtocolError(err_msg) from verr

        signature_bytes = _guid_to_bytes(signature_guid)
    elif signature_type == 0x01:
        sig_int = _parse_integer_literal(signature_text)
        signature_bytes = sig_int.to_bytes(length=4, byteorder="little", signed=False)
        signature_bytes = signature_bytes + (b"\x00" * 12)
    else:
        signature_bytes = _parse_hex_run(signature_text)
        if len(signature_bytes) < 16:
            signature_bytes = signature_bytes + (b"\x00" * (16 - len(signature_bytes)))
        else:
            signature_bytes = signature_bytes[:16]

    partition_start = _parse_integer_literal(args[3])
    partition_size = _parse_integer_literal(args[4])

    payload = partition_number.to_bytes(length=4, byteorder="little", signed=False)
    payload = payload + partition_start.to_bytes(length=8, byteorder="little", signed=False)
    payload = payload + partition_size.to_bytes(length=8, byteorder="little", signed=False)
    payload = payload + signature_bytes
    payload = payload + bytes([partition_format & 0xFF, signature_type & 0xFF])
    rtnval = DevicePathNode(type=0x04, subtype=0x01, data=payload)
    return rtnval


def _parse_cdrom(args: List[str]) -> DevicePathNode:
    """
        Parse ``CDROM(<bootentry>,<partstart>,<partsize>)``.

        :param args: The three argument tokens.

        :returns: The CD-ROM :class:`DevicePathNode`.
    """
    _expect_argc(option_name="CDROM", args=args, expected=3)
    boot_entry = _parse_integer_literal(args[0])
    partition_start = _parse_integer_literal(args[1])
    partition_size = _parse_integer_literal(args[2])
    payload = boot_entry.to_bytes(length=4, byteorder="little", signed=False)
    payload = payload + partition_start.to_bytes(length=8, byteorder="little", signed=False)
    payload = payload + partition_size.to_bytes(length=8, byteorder="little", signed=False)
    rtnval = DevicePathNode(type=0x04, subtype=0x02, data=payload)
    return rtnval


def _parse_file(args: List[str]) -> DevicePathNode:
    """
        Parse ``File(<path>)``.  The path is encoded as a UCS-2 null-
        terminated buffer.

        :param args: The single argument token.

        :returns: The File-Path :class:`DevicePathNode`.
    """
    _expect_argc(option_name="File", args=args, expected=1)
    path_text = args[0]
    encoded = ucs2_encode(path_text + "\x00")
    rtnval = DevicePathNode(type=0x04, subtype=0x04, data=encoded)
    return rtnval


def _parse_fv(args: List[str]) -> DevicePathNode:
    """
        Parse ``Fv(<guid>)``.

        :param args: The single argument token.

        :returns: The Firmware-Volume :class:`DevicePathNode`.
    """
    _expect_argc(option_name="Fv", args=args, expected=1)
    try:
        guid = uuid.UUID(args[0])
    except ValueError as verr:
        err_msg_lines = [
            "Fv(...) argument must be a GUID.",
            "OBSERVED: value={!r}".format(args[0]),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiProtocolError(err_msg) from verr

    rtnval = DevicePathNode(type=0x04, subtype=0x07, data=_guid_to_bytes(guid))
    return rtnval


def _parse_fvfile(args: List[str]) -> DevicePathNode:
    """
        Parse ``FvFile(<guid>)``.

        :param args: The single argument token.

        :returns: The Firmware-File :class:`DevicePathNode`.
    """
    _expect_argc(option_name="FvFile", args=args, expected=1)
    try:
        guid = uuid.UUID(args[0])
    except ValueError as verr:
        err_msg_lines = [
            "FvFile(...) argument must be a GUID.",
            "OBSERVED: value={!r}".format(args[0]),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiProtocolError(err_msg) from verr

    rtnval = DevicePathNode(type=0x04, subtype=0x06, data=_guid_to_bytes(guid))
    return rtnval


def _build_vendor(args: List[str], node_type: int, node_subtype: int, option_name: str) -> DevicePathNode:
    """
        Common parser back-end for ``VenHw(GUID,Data)``,
        ``VenMsg(GUID,Data)`` and ``VenMedia(GUID,Data)``.

        :param args: The 1 or 2 argument tokens.
        :param node_type: The resulting node ``type`` field.
        :param node_subtype: The resulting node ``subtype`` field.
        :param option_name: The originating option-name (for diagnostics).

        :returns: The vendor :class:`DevicePathNode`.
        :raises: UefiProtocolError
    """
    if len(args) == 1:
        guid_text = args[0]
        data_text = ""
    elif len(args) == 2:
        guid_text = args[0]
        data_text = args[1]
    else:
        err_msg_lines = [
            "{}(...) accepts 1 or 2 arguments.".format(option_name),
            "OBSERVED: {}".format(len(args)),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiProtocolError(err_msg) from None

    try:
        guid = uuid.UUID(guid_text)
    except ValueError as verr:
        err_msg_lines = [
            "{}(...) first argument must be a GUID.".format(option_name),
            "OBSERVED: value={!r}".format(guid_text),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiProtocolError(err_msg) from verr

    if len(data_text) == 0:
        data_bytes = b""
    else:
        data_bytes = _parse_hex_run(data_text)

    payload = _guid_to_bytes(guid) + data_bytes
    rtnval = DevicePathNode(type=node_type, subtype=node_subtype, data=payload)
    return rtnval


def _parse_venhw(args: List[str]) -> DevicePathNode:
    rtnval = _build_vendor(args=args, node_type=0x01, node_subtype=0x04, option_name="VenHw")
    return rtnval


def _parse_venmsg(args: List[str]) -> DevicePathNode:
    rtnval = _build_vendor(args=args, node_type=0x03, node_subtype=0x0A, option_name="VenMsg")
    return rtnval


def _parse_venmedia(args: List[str]) -> DevicePathNode:
    rtnval = _build_vendor(args=args, node_type=0x04, node_subtype=0x03, option_name="VenMedia")
    return rtnval


def _parse_generic_path(args: List[str]) -> DevicePathNode:
    """
        Parse the generic ``Path(<type>,<subtype>,<hex>)`` fallback form
        per UEFI section 10.6.1.6.

        :param args: The 2 or 3 argument tokens.

        :returns: The opaque :class:`DevicePathNode`.
        :raises: UefiProtocolError
    """
    if len(args) == 2:
        node_type = _parse_integer_literal(args[0])
        node_subtype = _parse_integer_literal(args[1])
        data_bytes = b""
    elif len(args) == 3:
        node_type = _parse_integer_literal(args[0])
        node_subtype = _parse_integer_literal(args[1])
        data_bytes = _parse_hex_run(args[2])
    else:
        err_msg_lines = [
            "Path(...) accepts 2 or 3 arguments.",
            "OBSERVED: {}".format(len(args)),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiProtocolError(err_msg) from None

    if not (0 <= node_type <= 0xFF):
        err_msg_lines = [
            "Path(...) 'type' must fit in a UINT8.",
            "OBSERVED: {}".format(node_type),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiProtocolError(err_msg) from None

    if not (0 <= node_subtype <= 0xFF):
        err_msg_lines = [
            "Path(...) 'subtype' must fit in a UINT8.",
            "OBSERVED: {}".format(node_subtype),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiProtocolError(err_msg) from None

    rtnval = DevicePathNode(type=node_type, subtype=node_subtype, data=data_bytes)
    return rtnval


def _expect_argc(option_name: str, args: List[str], expected: int) -> None:
    """
        Helper used by parsers to enforce a fixed argument count.

        :param option_name: The parser's option-name for diagnostics.
        :param args: The argument list received.
        :param expected: The exact expected count.

        :raises: UefiProtocolError
    """
    observed = len(args)
    if observed != expected:
        err_msg_lines = [
            "{}(...) expected {} arguments.".format(option_name, expected),
            "OBSERVED: {}".format(observed),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiProtocolError(err_msg) from None

    return


# ---------------------------------------------------------------------------
# Registries
# ---------------------------------------------------------------------------
#
# The registries are populated as plain module-level dicts.  Callers
# wishing to teach the codec about additional node types may insert new
# entries (though the production policy is to add them here rather than
# mutate at runtime).
# ---------------------------------------------------------------------------

_NodeFormatter = Callable[[DevicePathNode], str]
_NodeParser = Callable[[List[str]], DevicePathNode]


_NODE_FORMATTERS: Dict[Tuple[int, int], _NodeFormatter] = {
    (0x01, 0x01): _format_pci,
    (0x01, 0x04): _format_venhw,
    (0x02, 0x01): _format_acpi,
    (0x03, 0x05): _format_usb,
    (0x03, 0x0A): _format_venmsg,
    (0x03, 0x0B): _format_mac,
    (0x03, 0x0C): _format_ipv4_node,
    (0x03, 0x0D): _format_ipv6_node,
    (0x03, 0x12): _format_sata,
    (0x03, 0x17): _format_nvme,
    (0x03, 0x18): _format_uri,
    (0x04, 0x01): _format_hard_drive,
    (0x04, 0x02): _format_cdrom,
    (0x04, 0x03): _format_venmedia,
    (0x04, 0x04): _format_file_path,
    (0x04, 0x06): _format_piwg_ff,
    (0x04, 0x07): _format_piwg_fv,
}


_NODE_PARSERS: Dict[str, _NodeParser] = {
    "Pci": _parse_pci,
    "PCI": _parse_pci,            # spec examples use both cases
    "Acpi": _parse_acpi,
    "ACPI": _parse_acpi,
    "PciRoot": _parse_pciroot,
    "PcieRoot": _parse_pcieroot,
    "Floppy": _parse_floppy,
    "Keyboard": _parse_keyboard,
    "Serial": _parse_serial,
    "ParallelPort": _parse_parallelport,
    "USB": _parse_usb,
    "Usb": _parse_usb,
    "Sata": _parse_sata,
    "SATA": _parse_sata,
    "NVMe": _parse_nvme,
    "Nvme": _parse_nvme,
    "MAC": _parse_mac_node,
    "Mac": _parse_mac_node,
    "IPv4": _parse_ipv4_node,
    "IPv6": _parse_ipv6_node,
    "Uri": _parse_uri,
    "URI": _parse_uri,
    "HD": _parse_hard_drive,
    "CDROM": _parse_cdrom,
    "CdRom": _parse_cdrom,
    "File": _parse_file,
    "Fv": _parse_fv,
    "FvFile": _parse_fvfile,
    "VenHw": _parse_venhw,
    "VenMsg": _parse_venmsg,
    "VenMedia": _parse_venmedia,
    "Path": _parse_generic_path,
}


# ---------------------------------------------------------------------------
# Text codec entry points
# ---------------------------------------------------------------------------

def device_path_to_text(path: DevicePath) -> str:
    """
        Render a :class:`DevicePath` as its canonical text form per
        UEFI section 10.6.2.  Nodes are joined with ``'/'`` per section
        10.6.1.1.  Unknown ``(type, subtype)`` pairs fall back to the
        ``Path(type,subtype,hex)`` generic form per section 10.6.1.6.

        :param path: The device path to render.

        :returns: The canonical text representation.
        :raises: UefiProtocolError
    """
    if not isinstance(path, DevicePath):
        err_msg_lines = [
            "'path' must be a DevicePath.",
            "GOT: value={!r} type={}".format(path, type(path).__name__),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiProtocolError(err_msg) from None

    segments: List[str] = []
    for node in path.nodes:
        if node.type == _END_TYPE and node.subtype == _END_ENTIRE_SUBTYPE:
            # End-Entire nodes are implicit in the text form.
            continue

        key = (node.type, node.subtype)
        formatter = _NODE_FORMATTERS.get(key)
        if formatter is None:
            segment = "Path({},{},{})".format(
                node.type, node.subtype, _format_hex_run(node.data)
            )
        else:
            segment = formatter(node)

        segments.append(segment)

    rtnval = "/".join(segments)
    return rtnval


def device_path_from_text(text: str) -> DevicePath:
    """
        Parse the canonical text representation of a device path per
        UEFI section 10.6 back into a :class:`DevicePath`.

        The parser is a hand-rolled recursive-descent implementation
        using only the Python standard library.  The grammar accepted
        is::

            device_path := node ( '/' node )*
            node        := IDENT '(' args? ')'
            args        := arg ( ',' arg )*
            arg         := any character sequence until ',' or ')'
                           at the same parenthesis depth

        Leading ``/`` or ``\\`` characters are tolerated per UEFI
        section 10.6.1.5.

        :param text: The text representation.

        :returns: The parsed :class:`DevicePath`.
        :raises: UefiProtocolError
    """
    if not isinstance(text, str):
        err_msg_lines = [
            "'text' must be a str.",
            "GOT: value={!r} type={}".format(text, type(text).__name__),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiProtocolError(err_msg) from None

    parser = _DevicePathTextParser(text=text)
    rtnval = parser.parse()
    return rtnval


# ---------------------------------------------------------------------------
# Recursive-descent parser (hand-rolled, no external dependencies)
# ---------------------------------------------------------------------------

class _DevicePathTextParser:
    """
        Recursive-descent parser for the UEFI section 10.6 text form.

        The parser works character-by-character on the input string.
        Each production returns a fully-populated structure rather than
        a token stream so diagnostics can cite the offending character
        offset directly.

        :param text: The input text being parsed.
    """

    def __init__(self, text: str) -> None:
        """
            Initialize the parser with the input text.

            :param text: The text to parse.
        """
        self._text: str = text
        self._cursor: int = 0
        self._length: int = len(text)
        return

    def parse(self) -> DevicePath:
        """
            Top-level entry point.  Parses a ``device_path`` production.

            :returns: The resulting :class:`DevicePath`.
            :raises: UefiProtocolError
        """
        nodes: List[DevicePathNode] = []

        if self._length == 0:
            rtnval = DevicePath(nodes=tuple(nodes))
            return rtnval

        # Leading separator is permitted per section 10.6.1.5.
        self._skip_separators()

        while self._cursor < self._length:
            node = self._parse_node()
            nodes.append(node)
            if self._cursor >= self._length:
                break
            if not self._at_separator():
                err_msg_lines = [
                    "Expected '/' separator between device-path nodes.",
                    "OFFSET: {}".format(self._cursor),
                    "CONTEXT: {!r}".format(self._context_window()),
                ]
                err_msg = os.linesep.join(err_msg_lines)
                raise UefiProtocolError(err_msg) from None

            self._skip_separators()

        rtnval = DevicePath(nodes=tuple(nodes))
        return rtnval

    def _at_separator(self) -> bool:
        """
            Return whether the cursor is parked on a node separator.

            :returns: True when the next character is ``'/'`` or ``'\\'``.
        """
        if self._cursor >= self._length:
            rtnval = False
        else:
            current = self._text[self._cursor]
            rtnval = current == "/" or current == "\\"

        return rtnval

    def _skip_separators(self) -> None:
        """
            Advance past any run of node-separator characters.
        """
        while self._cursor < self._length and self._at_separator():
            self._cursor = self._cursor + 1

        return

    def _parse_node(self) -> DevicePathNode:
        """
            Parse a single ``node`` production: ``IDENT '(' args? ')'``.

            :returns: The constructed :class:`DevicePathNode`.
            :raises: UefiProtocolError
        """
        start_offset = self._cursor
        option_name = self._parse_identifier()
        if self._cursor >= self._length or self._text[self._cursor] != "(":
            err_msg_lines = [
                "Expected '(' following node option-name.",
                "OPTION: {!r}".format(option_name),
                "OFFSET: {}".format(self._cursor),
                "CONTEXT: {!r}".format(self._context_window()),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiProtocolError(err_msg) from None

        self._cursor = self._cursor + 1   # consume '('
        args = self._parse_argument_list()
        if self._cursor >= self._length or self._text[self._cursor] != ")":
            err_msg_lines = [
                "Expected ')' closing node option argument list.",
                "OPTION: {!r}".format(option_name),
                "OFFSET: {}".format(self._cursor),
                "CONTEXT: {!r}".format(self._context_window()),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiProtocolError(err_msg) from None

        self._cursor = self._cursor + 1   # consume ')'

        node_parser = _NODE_PARSERS.get(option_name)
        if node_parser is None:
            err_msg_lines = [
                "Unknown device-path option name.",
                "OPTION: {!r}".format(option_name),
                "OFFSET: {}".format(start_offset),
                "CONTEXT: {!r}".format(self._context_window(start_offset)),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiProtocolError(err_msg) from None

        rtnval = node_parser(args)
        return rtnval

    def _parse_identifier(self) -> str:
        """
            Consume a run of alphanumeric / underscore / hyphen characters.

            :returns: The identifier text.
            :raises: UefiProtocolError
        """
        start = self._cursor
        while self._cursor < self._length:
            ch = self._text[self._cursor]
            if ch.isalnum() or ch == "_":
                self._cursor = self._cursor + 1
            else:
                break

        if start == self._cursor:
            err_msg_lines = [
                "Expected device-path option name.",
                "OFFSET: {}".format(self._cursor),
                "CONTEXT: {!r}".format(self._context_window()),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiProtocolError(err_msg) from None

        rtnval = self._text[start:self._cursor]
        return rtnval

    def _parse_argument_list(self) -> List[str]:
        """
            Consume a comma-separated list of argument tokens up to (but
            not including) the matching closing ``)``.

            :returns: The list of argument tokens (outer whitespace
                      preserved for ``File(...)`` paths).
            :raises: UefiProtocolError
        """
        args: List[str] = []

        # Handle the empty argument list `()` explicitly.
        if self._cursor < self._length and self._text[self._cursor] == ")":
            rtnval = args
            return rtnval

        while True:
            token = self._parse_argument()
            args.append(token)

            if self._cursor >= self._length:
                err_msg_lines = [
                    "Unterminated argument list.",
                    "OFFSET: {}".format(self._cursor),
                    "CONTEXT: {!r}".format(self._context_window()),
                ]
                err_msg = os.linesep.join(err_msg_lines)
                raise UefiProtocolError(err_msg) from None

            ch = self._text[self._cursor]
            if ch == ",":
                self._cursor = self._cursor + 1
                continue
            if ch == ")":
                break

            err_msg_lines = [
                "Unexpected character in argument list.",
                "OFFSET: {}".format(self._cursor),
                "OBSERVED: {!r}".format(ch),
                "CONTEXT: {!r}".format(self._context_window()),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiProtocolError(err_msg) from None

        rtnval = args
        return rtnval

    def _parse_argument(self) -> str:
        """
            Consume a single argument token.  Tokens extend until the
            next comma or closing paren at the same nesting depth.
            Nested parentheses are permitted (useful for future-proofing
            the grammar) but carry no special meaning to the codec.

            :returns: The stripped argument string.
            :raises: UefiProtocolError
        """
        start = self._cursor
        depth = 0
        while self._cursor < self._length:
            ch = self._text[self._cursor]
            if depth == 0 and (ch == "," or ch == ")"):
                break
            if ch == "(":
                depth = depth + 1
            elif ch == ")":
                if depth == 0:
                    break
                depth = depth - 1

            self._cursor = self._cursor + 1

        raw = self._text[start:self._cursor]
        rtnval = raw.strip()
        return rtnval

    def _context_window(self, anchor: Optional[int] = None) -> str:
        """
            Return a short window of source text around the cursor for
            diagnostic messages.

            :param anchor: The offset to anchor the window on.  When
                           omitted the cursor is used.

            :returns: The context window as a string.
        """
        if anchor is None:
            anchor_offset = self._cursor
        else:
            anchor_offset = anchor

        start = max(0, anchor_offset - 12)
        end = min(self._length, anchor_offset + 12)
        rtnval = self._text[start:end]
        return rtnval
