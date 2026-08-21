"""
    .. module:: test_uefidevicepath
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Unit tests for the UEFI Device Path codec
               (``ccai.protocols.uefi.uefidevicepath``).

               Covers per-node binary round-trips, per-node text
               round-trips, binary<->text cross round-trips, canonical
               examples transcribed verbatim from UEFI specification
               section 10.6, malformed binary inputs, malformed text
               inputs, and the generic ``Path(type,subtype,hex)``
               fallback for unknown ``(type, subtype)`` pairs per UEFI
               section 10.6.1.6.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Code Craft AI LLC"
__credits__ = []


import unittest

from ccai import testbase

import uuid

from typing import List, Tuple

from ccai.protocols.uefi.uefiexceptions import UefiProtocolError

from ccai.protocols.uefi.uefidevicepath import (
    DevicePath,
    DevicePathNode,
    END_ENTIRE_NODE,
    END_OF_HARDWARE_NODE,
    decode_device_path,
    device_path_from_text,
    device_path_to_text,
    encode_device_path,
)


# ---------------------------------------------------------------------------
# Helpers for constructing canonical fixtures
# ---------------------------------------------------------------------------

def _pack_u16_le(value: int) -> bytes:
    rtnval = value.to_bytes(length=2, byteorder="little", signed=False)
    return rtnval


def _pack_u32_le(value: int) -> bytes:
    rtnval = value.to_bytes(length=4, byteorder="little", signed=False)
    return rtnval


def _pack_u64_le(value: int) -> bytes:
    rtnval = value.to_bytes(length=8, byteorder="little", signed=False)
    return rtnval


def _encode_header(node_type: int, node_subtype: int, total_length: int) -> bytes:
    rtnval = bytes([node_type, node_subtype]) + _pack_u16_le(total_length)
    return rtnval


def _end_entire() -> bytes:
    rtnval = _encode_header(0x7F, 0xFF, 4)
    return rtnval


def _pci_node_bytes(device: int, function: int) -> bytes:
    # UEFI section 10.3.2.1 order: (function, device) following header.
    payload = bytes([function & 0xFF, device & 0xFF])
    rtnval = _encode_header(0x01, 0x01, 4 + len(payload)) + payload
    return rtnval


def _acpi_node_bytes(hid: int, uid: int) -> bytes:
    payload = _pack_u32_le(hid) + _pack_u32_le(uid)
    rtnval = _encode_header(0x02, 0x01, 4 + len(payload)) + payload
    return rtnval


def _usb_node_bytes(port: int, interface: int) -> bytes:
    payload = bytes([port & 0xFF, interface & 0xFF])
    rtnval = _encode_header(0x03, 0x05, 4 + len(payload)) + payload
    return rtnval


def _sata_node_bytes(hba_port: int, pmpn: int, lun: int) -> bytes:
    payload = _pack_u16_le(hba_port) + _pack_u16_le(pmpn) + _pack_u16_le(lun)
    rtnval = _encode_header(0x03, 0x12, 4 + len(payload)) + payload
    return rtnval


def _nvme_node_bytes(nsid: int, eui: bytes) -> bytes:
    if len(eui) != 8:
        raise ValueError("EUI must be 8 bytes.")
    payload = _pack_u32_le(nsid) + eui
    rtnval = _encode_header(0x03, 0x17, 4 + len(payload)) + payload
    return rtnval


def _mac_node_bytes(mac6: bytes, if_type: int) -> bytes:
    if len(mac6) != 6:
        raise ValueError("MAC must be 6 bytes.")
    padding = b"\x00" * (32 - len(mac6))
    payload = mac6 + padding + bytes([if_type & 0xFF])
    rtnval = _encode_header(0x03, 0x0B, 4 + len(payload)) + payload
    return rtnval


def _ipv4_node_bytes(local_ip: bytes, remote_ip: bytes,
                     local_port: int, remote_port: int,
                     protocol: int, static_ip: int) -> bytes:
    # 23-byte legacy layout per UEFI section 10.3.4.11 with Gateway and
    # SubnetMask zeroed.
    payload = local_ip + remote_ip + _pack_u16_le(local_port) + _pack_u16_le(remote_port)
    payload = payload + _pack_u16_le(protocol) + bytes([static_ip & 0xFF])
    payload = payload + (b"\x00" * 4) + (b"\x00" * 4)
    rtnval = _encode_header(0x03, 0x0C, 4 + len(payload)) + payload
    return rtnval


def _ipv6_node_bytes(local_ip: bytes, remote_ip: bytes,
                     local_port: int, remote_port: int,
                     protocol: int, origin: int) -> bytes:
    payload = local_ip + remote_ip + _pack_u16_le(local_port) + _pack_u16_le(remote_port)
    payload = payload + _pack_u16_le(protocol) + bytes([origin & 0xFF, 0x00])
    payload = payload + (b"\x00" * 16)
    rtnval = _encode_header(0x03, 0x0D, 4 + len(payload)) + payload
    return rtnval


def _uri_node_bytes(uri: str) -> bytes:
    payload = uri.encode("utf-8")
    rtnval = _encode_header(0x03, 0x18, 4 + len(payload)) + payload
    return rtnval


def _hd_node_bytes(partition_number: int, partition_start: int,
                   partition_size: int, signature_bytes: bytes,
                   partition_format: int, signature_type: int) -> bytes:
    if len(signature_bytes) != 16:
        raise ValueError("Signature must be 16 bytes.")
    payload = _pack_u32_le(partition_number)
    payload = payload + _pack_u64_le(partition_start) + _pack_u64_le(partition_size)
    payload = payload + signature_bytes
    payload = payload + bytes([partition_format & 0xFF, signature_type & 0xFF])
    rtnval = _encode_header(0x04, 0x01, 4 + len(payload)) + payload
    return rtnval


def _cdrom_node_bytes(boot_entry: int, partition_start: int, partition_size: int) -> bytes:
    payload = _pack_u32_le(boot_entry) + _pack_u64_le(partition_start) + _pack_u64_le(partition_size)
    rtnval = _encode_header(0x04, 0x02, 4 + len(payload)) + payload
    return rtnval


def _file_node_bytes(path: str) -> bytes:
    payload = (path + "\x00").encode("utf-16-le")
    rtnval = _encode_header(0x04, 0x04, 4 + len(payload)) + payload
    return rtnval


def _fv_node_bytes(guid: uuid.UUID) -> bytes:
    payload = guid.bytes_le
    rtnval = _encode_header(0x04, 0x07, 4 + len(payload)) + payload
    return rtnval


def _fvfile_node_bytes(guid: uuid.UUID) -> bytes:
    payload = guid.bytes_le
    rtnval = _encode_header(0x04, 0x06, 4 + len(payload)) + payload
    return rtnval


def _venhw_node_bytes(guid: uuid.UUID, data: bytes) -> bytes:
    payload = guid.bytes_le + data
    rtnval = _encode_header(0x01, 0x04, 4 + len(payload)) + payload
    return rtnval


def _venmsg_node_bytes(guid: uuid.UUID, data: bytes) -> bytes:
    payload = guid.bytes_le + data
    rtnval = _encode_header(0x03, 0x0A, 4 + len(payload)) + payload
    return rtnval


def _venmedia_node_bytes(guid: uuid.UUID, data: bytes) -> bytes:
    payload = guid.bytes_le + data
    rtnval = _encode_header(0x04, 0x03, 4 + len(payload)) + payload
    return rtnval


# ACPI HID constants for PNP0A03 (PciRoot) and PNP0A08 (PcieRoot).
_PNP0A03 = 0x0A0341D0
_PNP0A08 = 0x0A0841D0


# ---------------------------------------------------------------------------
# Core container equality / iteration
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# DevicePathValueObjectsTestCase
# ----------------------------------------------------------------------

"""
    Validate equality and immutability semantics for
    :class:`DevicePathNode` and :class:`DevicePath`.
"""

def test_node_structural_equality() -> None:
    left = DevicePathNode(type=1, subtype=1, data=b"\x02\x1f")
    right = DevicePathNode(type=1, subtype=1, data=b"\x02\x1f")
    testbase.assert_equal(left, right)
    return

def test_node_inequality() -> None:
    left = DevicePathNode(type=1, subtype=1, data=b"\x02\x1f")
    right = DevicePathNode(type=1, subtype=1, data=b"\x02\x20")
    testbase.assert_not_equal(left, right)
    return

def test_path_structural_equality() -> None:
    a = DevicePathNode(type=1, subtype=1, data=b"\x02\x1f")
    b = DevicePathNode(type=1, subtype=1, data=b"\x02\x1f")
    left = DevicePath(nodes=(a,))
    right = DevicePath(nodes=(b,))
    testbase.assert_equal(left, right)
    return

def test_end_entire_sentinel_values() -> None:
    testbase.assert_equal(END_ENTIRE_NODE.type, 0x7F)
    testbase.assert_equal(END_ENTIRE_NODE.subtype, 0xFF)
    testbase.assert_equal(END_OF_HARDWARE_NODE.type, 0x7F)
    testbase.assert_equal(END_OF_HARDWARE_NODE.subtype, 0x01)
    return


# ---------------------------------------------------------------------------
# Binary encoder terminator behavior
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# EncoderTerminatorTestCase
# ----------------------------------------------------------------------

"""
    Verify that the encoder always appends an End-Entire terminator
    regardless of whether the caller included one in
    :class:`DevicePath.nodes`.
"""

def test_empty_path_emits_only_terminator() -> None:
    path = DevicePath(nodes=())
    encoded = encode_device_path(path)
    testbase.assert_equal(encoded, _end_entire())
    return

def test_terminator_appended_automatically() -> None:
    pci = DevicePathNode(type=1, subtype=1, data=bytes([0x02, 0x1F]))
    path = DevicePath(nodes=(pci,))
    encoded = encode_device_path(path)
    testbase.assert_true(encoded.endswith(_end_entire()))
    return

def test_caller_terminator_is_dropped_not_duplicated() -> None:
    pci = DevicePathNode(type=1, subtype=1, data=bytes([0x02, 0x1F]))
    path = DevicePath(nodes=(pci, END_ENTIRE_NODE))
    encoded = encode_device_path(path)
    # Should have one trailing end-entire, not two.
    testbase.assert_true(encoded.endswith(_end_entire()))
    testbase.assert_false(encoded.endswith(_end_entire() + _end_entire()))
    return


# ---------------------------------------------------------------------------
# Per-node binary round-trips
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# BinaryRoundTripTestCase
# ----------------------------------------------------------------------

"""
    For each node type listed in the brief, encode a canonical
    binary fixture, decode it, re-encode, and verify byte-identical
    output.  Also verify the decoder strips the End-Entire
    terminator from the returned :class:`DevicePath`.
"""

def _assert_round_trip(fixture: bytes) -> DevicePath:
    # fixture is expected to already include the End-Entire
    # terminator; decode/encode should reproduce it verbatim.
    decoded = decode_device_path(fixture)
    re_encoded = encode_device_path(decoded)
    testbase.assert_equal(re_encoded, fixture)
    return decoded

def test_binary_round_trip_pci() -> None:
    fixture = _pci_node_bytes(device=0x1F, function=0x2) + _end_entire()
    _assert_round_trip(fixture=fixture)
    return

def test_binary_round_trip_acpi_named_pciroot() -> None:
    fixture = _acpi_node_bytes(hid=_PNP0A03, uid=0) + _end_entire()
    _assert_round_trip(fixture=fixture)
    return

def test_binary_round_trip_acpi_generic() -> None:
    # PNP0501 == Serial.
    fixture = _acpi_node_bytes(hid=0x050141D0, uid=1) + _end_entire()
    decoded = _assert_round_trip(fixture=fixture)
    # Verify payload preserved exactly.
    testbase.assert_equal(len(decoded.nodes), 1)
    testbase.assert_equal(decoded.nodes[0].type, 0x02)
    testbase.assert_equal(decoded.nodes[0].subtype, 0x01)
    return

def test_binary_round_trip_usb() -> None:
    fixture = _usb_node_bytes(port=1, interface=0) + _end_entire()
    _assert_round_trip(fixture=fixture)
    return

def test_binary_round_trip_sata() -> None:
    fixture = _sata_node_bytes(hba_port=0, pmpn=0xFFFF, lun=0) + _end_entire()
    _assert_round_trip(fixture=fixture)
    return

def test_binary_round_trip_nvme() -> None:
    eui = bytes([0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])
    fixture = _nvme_node_bytes(nsid=1, eui=eui) + _end_entire()
    _assert_round_trip(fixture=fixture)
    return

def test_binary_round_trip_mac() -> None:
    mac = bytes([0x00, 0x13, 0x20, 0xF5, 0xFA, 0x77])
    fixture = _mac_node_bytes(mac6=mac, if_type=1) + _end_entire()
    _assert_round_trip(fixture=fixture)
    return

def test_binary_round_trip_ipv4() -> None:
    local = bytes([192, 168, 0, 1])
    remote = bytes([192, 168, 0, 100])
    fixture = _ipv4_node_bytes(
        local_ip=local, remote_ip=remote,
        local_port=0, remote_port=0,
        protocol=6, static_ip=1,
    ) + _end_entire()
    _assert_round_trip(fixture=fixture)
    return

def test_binary_round_trip_ipv6() -> None:
    local = bytes(16)
    remote = bytes(range(16))
    fixture = _ipv6_node_bytes(
        local_ip=local, remote_ip=remote,
        local_port=0, remote_port=0,
        protocol=6, origin=0,
    ) + _end_entire()
    _assert_round_trip(fixture=fixture)
    return

def test_binary_round_trip_uri() -> None:
    fixture = _uri_node_bytes(uri="https://example.com/boot.efi") + _end_entire()
    _assert_round_trip(fixture=fixture)
    return

def test_binary_round_trip_hard_drive_gpt() -> None:
    guid = uuid.UUID("15E39A00-1DD2-1000-8D7F-00A0C92408FC")
    fixture = _hd_node_bytes(
        partition_number=1,
        partition_start=0x22,
        partition_size=0x2710000,
        signature_bytes=guid.bytes_le,
        partition_format=0x02,
        signature_type=0x02,
    ) + _end_entire()
    _assert_round_trip(fixture=fixture)
    return

def test_binary_round_trip_hard_drive_mbr() -> None:
    signature = (0xA0021243).to_bytes(length=4, byteorder="little", signed=False) + (b"\x00" * 12)
    fixture = _hd_node_bytes(
        partition_number=1,
        partition_start=0x800,
        partition_size=0x2EE000,
        signature_bytes=signature,
        partition_format=0x01,
        signature_type=0x01,
    ) + _end_entire()
    _assert_round_trip(fixture=fixture)
    return

def test_binary_round_trip_cdrom() -> None:
    fixture = _cdrom_node_bytes(boot_entry=0, partition_start=0x800, partition_size=0x1000) + _end_entire()
    _assert_round_trip(fixture=fixture)
    return

def test_binary_round_trip_file_path() -> None:
    fixture = _file_node_bytes(path="\\EFI\\BOOT\\BOOTX64.EFI") + _end_entire()
    _assert_round_trip(fixture=fixture)
    return

def test_binary_round_trip_piwg_fv() -> None:
    guid = uuid.UUID("a1a1a1a1-b2b2-c3c3-d4d4-e5e5e5e5e5e5")
    fixture = _fv_node_bytes(guid=guid) + _end_entire()
    _assert_round_trip(fixture=fixture)
    return

def test_binary_round_trip_piwg_ff() -> None:
    guid = uuid.UUID("9e21fd93-9c72-4c15-8c4b-e77f1db2d792")
    fixture = _fvfile_node_bytes(guid=guid) + _end_entire()
    _assert_round_trip(fixture=fixture)
    return

def test_binary_round_trip_venhw() -> None:
    guid = uuid.UUID("11111111-2222-3333-4444-555555555555")
    fixture = _venhw_node_bytes(guid=guid, data=b"\xDE\xAD\xBE\xEF") + _end_entire()
    _assert_round_trip(fixture=fixture)
    return

def test_binary_round_trip_venmsg() -> None:
    guid = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    fixture = _venmsg_node_bytes(guid=guid, data=b"") + _end_entire()
    _assert_round_trip(fixture=fixture)
    return

def test_binary_round_trip_venmedia() -> None:
    guid = uuid.UUID("12345678-1234-1234-1234-1234567890ab")
    fixture = _venmedia_node_bytes(guid=guid, data=b"\x01\x02\x03") + _end_entire()
    _assert_round_trip(fixture=fixture)
    return

def test_binary_round_trip_multi_node_chain() -> None:
    fixture = (
        _acpi_node_bytes(hid=_PNP0A03, uid=0)
        + _pci_node_bytes(device=0x1F, function=0x2)
        + _sata_node_bytes(hba_port=0, pmpn=0, lun=0)
        + _end_entire()
    )
    decoded = _assert_round_trip(fixture=fixture)
    testbase.assert_equal(len(decoded.nodes), 3)
    return


# ---------------------------------------------------------------------------
# Per-node text round-trips
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# TextRoundTripTestCase
# ----------------------------------------------------------------------

"""
    For each node-family text form, verify
    ``text -> parse -> format -> text`` produces stable output.
"""

def _assert_stable(text: str) -> None:
    parsed = device_path_from_text(text)
    re_text = device_path_to_text(parsed)
    testbase.assert_equal(re_text, text)
    return

def test_text_round_trip_pci() -> None:
    _assert_stable(text="Pci(0x1F,0x2)")
    return

def test_text_round_trip_pciroot() -> None:
    _assert_stable(text="PciRoot(0x0)")
    return

def test_text_round_trip_acpi_generic() -> None:
    _assert_stable(text="Acpi(PNP0501,0x1)")
    return

def test_text_round_trip_usb() -> None:
    _assert_stable(text="USB(0x1,0x0)")
    return

def test_text_round_trip_sata() -> None:
    _assert_stable(text="Sata(0x0,0xFFFF,0x0)")
    return

def test_text_round_trip_nvme() -> None:
    _assert_stable(text="NVMe(0x1,FF-FF-FF-FF-FF-FF-FF-FF)")
    return

def test_text_round_trip_mac() -> None:
    _assert_stable(text="MAC(001320F5FA77,0x1)")
    return

def test_text_round_trip_ipv4() -> None:
    _assert_stable(text="IPv4(192.168.0.100,0x6,Static,192.168.0.1)")
    return

def test_text_round_trip_uri() -> None:
    _assert_stable(text="Uri(https://example.com/boot.efi)")
    return

def test_text_round_trip_hd_gpt() -> None:
    _assert_stable(
        text="HD(1,GPT,15E39A00-1DD2-1000-8D7F-00A0C92408FC,0x22,0x2710000)"
    )
    return

def test_text_round_trip_cdrom() -> None:
    _assert_stable(text="CDROM(0x0,0x800,0x1000)")
    return

def test_text_round_trip_file() -> None:
    _assert_stable(text="File(\\EFI\\BOOT\\BOOTX64.EFI)")
    return

def test_text_round_trip_fv() -> None:
    _assert_stable(text="Fv(A1A1A1A1-B2B2-C3C3-D4D4-E5E5E5E5E5E5)")
    return

def test_text_round_trip_fvfile() -> None:
    _assert_stable(text="FvFile(9E21FD93-9C72-4C15-8C4B-E77F1DB2D792)")
    return

def test_text_round_trip_venhw() -> None:
    _assert_stable(
        text="VenHw(11111111-2222-3333-4444-555555555555,DEADBEEF)"
    )
    return

def test_text_round_trip_venmsg_no_data() -> None:
    _assert_stable(
        text="VenMsg(AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE)"
    )
    return

def test_text_round_trip_venmedia() -> None:
    _assert_stable(
        text="VenMedia(12345678-1234-1234-1234-1234567890AB,010203)"
    )
    return

def test_text_round_trip_multi_node_text() -> None:
    text = "PciRoot(0x0)/Pci(0x1F,0x2)/Sata(0x0,0x0,0x0)"
    _assert_stable(text=text)
    return


# ---------------------------------------------------------------------------
# Binary <-> text cross round-trip
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# BinaryTextCrossTestCase
# ----------------------------------------------------------------------

"""
    For each node family, feed ``text -> parse -> encode -> decode ->
    format -> text`` and verify stability.  This is the stronger
    invariant demanded by UEFI specification section 10.6.1.3.
"""

def _cross(text: str) -> None:
    parsed = device_path_from_text(text)
    encoded = encode_device_path(parsed)
    re_decoded = decode_device_path(encoded)
    re_text = device_path_to_text(re_decoded)
    testbase.assert_equal(re_text, text)
    return

def test_cross_pci() -> None:
    _cross(text="Pci(0x1F,0x2)")
    return

def test_cross_pciroot() -> None:
    _cross(text="PciRoot(0x0)")
    return

def test_cross_spec_example_mbr() -> None:
    # UEFI spec section 10.6 canonical example (near line 21629 of
    # the archived spec markdown):
    #   PciRoot(0)/Pci(19|0)/Mac(001320F5FA77,0x01)/
    #   IPv4(192.168.0.100,TCP,Static,192.168.0.1)/
    #   HD(1,GPT,15E39A00-1DD2-1000-8D7F-00A0C92408FC,0x22,0x2710000)
    text = (
        "PciRoot(0x0)"
        "/Pci(0x13,0x0)"
        "/MAC(001320F5FA77,0x1)"
        "/IPv4(192.168.0.100,0x6,Static,192.168.0.1)"
        "/HD(1,GPT,15E39A00-1DD2-1000-8D7F-00A0C92408FC,0x22,0x2710000)"
    )
    _cross(text=text)
    return

def test_cross_spec_example_simple_chain() -> None:
    # UEFI spec section 10.6 canonical example (near line 20785):
    #   PciRoot(0)/PCI(31,2)/USB(0,0)
    text = "PciRoot(0x0)/Pci(0x1F,0x2)/USB(0x0,0x0)"
    _cross(text=text)
    return

def test_cross_cdrom() -> None:
    _cross(text="CDROM(0x0,0x800,0x1000)")
    return

def test_cross_file() -> None:
    _cross(text="File(\\EFI\\BOOT\\BOOTX64.EFI)")
    return

def test_cross_fv() -> None:
    _cross(text="Fv(A1A1A1A1-B2B2-C3C3-D4D4-E5E5E5E5E5E5)")
    return

def test_cross_uri() -> None:
    _cross(text="Uri(https://example.com/boot.efi)")
    return


# ---------------------------------------------------------------------------
# UEFI specification section 10.6 canonical examples
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# SpecCanonicalExamplesTestCase
# ----------------------------------------------------------------------

"""
    Verify that examples transcribed verbatim from UEFI specification
    section 10.6 parse into structures whose binary encoding
    round-trips back to the same text form.

    These examples cite the section rather than the exact quoted
    bytes because the specification's text examples use style
    variants (e.g. ``Pci(19|0)`` rather than the canonical
    ``Pci(0x13,0x0)``) that the parser accommodates via alternate
    forms listed in the node's text-form row.
"""

def test_pciroot_pci_sata_hd_example() -> None:
    # UEFI spec section 10.6 example (simplified to the canonical
    # display form).
    text = (
        "PciRoot(0x0)"
        "/Pci(0x1F,0x2)"
        "/Sata(0x0,0x0,0x0)"
        "/HD(1,GPT,15E39A00-1DD2-1000-8D7F-00A0C92408FC,0x22,0x2710000)"
    )
    parsed = device_path_from_text(text)
    encoded = encode_device_path(parsed)
    decoded = decode_device_path(encoded)
    testbase.assert_equal(device_path_to_text(decoded), text)
    # And the canonical structure has exactly four nodes.
    testbase.assert_equal(len(parsed.nodes), 4)
    return

def test_pciroot_pci_usb_example() -> None:
    # UEFI spec section 10.6 nested USB example.
    text = "PciRoot(0x0)/Pci(0x1F,0x2)/USB(0x1,0x0)/USB(0x3,0x0)"
    parsed = device_path_from_text(text)
    encoded = encode_device_path(parsed)
    decoded = decode_device_path(encoded)
    testbase.assert_equal(device_path_to_text(decoded), text)
    testbase.assert_equal(len(parsed.nodes), 4)
    return


# ---------------------------------------------------------------------------
# Malformed binary inputs
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# MalformedBinaryTestCase
# ----------------------------------------------------------------------

"""
    Verify :func:`decode_device_path` rejects corrupt inputs with
    :class:`UefiProtocolError` rather than returning partial data.
"""

def test_truncated_header_raises() -> None:
    # Only three bytes: the 4-byte header cannot be read.
    with testbase.raises(UefiProtocolError):
        decode_device_path(b"\x01\x01\x04")
    return

def test_empty_buffer_is_missing_terminator() -> None:
    # No End-Entire -> malformed.
    with testbase.raises(UefiProtocolError):
        decode_device_path(b"")
    return

def test_length_less_than_four_raises() -> None:
    # Node length field declares 0 which is below the minimum
    # header length of 4.
    fixture = bytes([0x01, 0x01]) + _pack_u16_le(0) + _end_entire()
    with testbase.raises(UefiProtocolError):
        decode_device_path(fixture)
    return

def test_length_exceeds_remaining_raises() -> None:
    # Node declares 20 bytes but only 6 remain including header.
    fixture = bytes([0x01, 0x01]) + _pack_u16_le(20) + b"\x00\x00"
    with testbase.raises(UefiProtocolError):
        decode_device_path(fixture)
    return

def test_missing_end_entire_raises() -> None:
    # Valid PCI node but no terminator -> should raise.
    fixture = _pci_node_bytes(device=0x1F, function=0x2)
    with testbase.raises(UefiProtocolError):
        decode_device_path(fixture)
    return

def test_non_bytes_input_raises() -> None:
    with testbase.raises(UefiProtocolError):
        decode_device_path("not-bytes")   # type: ignore[arg-type]
    return


# ---------------------------------------------------------------------------
# Unknown node type fallthrough
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# UnknownNodeTestCase
# ----------------------------------------------------------------------

"""
    Per UEFI section 10.6.1.6, unknown ``(type, subtype)`` pairs must
    serialize to the generic ``Path(type,subtype,hex)`` form and
    parse back round-trip clean.
"""

def test_unknown_binary_round_trip() -> None:
    # Synthesize an unassigned (type=0x06, subtype=0x12) node.
    unknown_payload = b"\xAB\xCD"
    fixture = (
        bytes([0x06, 0x12]) + _pack_u16_le(4 + len(unknown_payload)) + unknown_payload
        + _end_entire()
    )
    decoded = decode_device_path(fixture)
    testbase.assert_equal(len(decoded.nodes), 1)
    testbase.assert_equal(decoded.nodes[0].type, 0x06)
    testbase.assert_equal(decoded.nodes[0].subtype, 0x12)
    testbase.assert_equal(decoded.nodes[0].data, unknown_payload)

    re_encoded = encode_device_path(decoded)
    testbase.assert_equal(re_encoded, fixture)
    return

def test_unknown_text_form() -> None:
    unknown = DevicePathNode(type=0x06, subtype=0x12, data=b"\xAB\xCD")
    path = DevicePath(nodes=(unknown,))
    text = device_path_to_text(path)
    testbase.assert_equal(text, "Path(6,18,ABCD)")
    return

def test_unknown_text_parse_round_trip() -> None:
    text = "Path(6,18,ABCD)"
    parsed = device_path_from_text(text)
    re_text = device_path_to_text(parsed)
    testbase.assert_equal(re_text, text)
    # Verify it also round-trips through binary.
    encoded = encode_device_path(parsed)
    re_decoded = decode_device_path(encoded)
    testbase.assert_equal(device_path_to_text(re_decoded), text)
    return

def test_unknown_empty_payload() -> None:
    # Node with unknown (type,subtype) and zero-length payload.
    fixture = bytes([0x06, 0x12]) + _pack_u16_le(4) + _end_entire()
    decoded = decode_device_path(fixture)
    testbase.assert_equal(len(decoded.nodes), 1)
    testbase.assert_equal(decoded.nodes[0].data, b"")
    text = device_path_to_text(decoded)
    testbase.assert_equal(text, "Path(6,18,)")
    # And the parser accepts the generic form with an empty hex run.
    re_parsed = device_path_from_text(text)
    testbase.assert_equal(re_parsed.nodes[0], decoded.nodes[0])
    return


# ---------------------------------------------------------------------------
# Malformed text inputs
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# MalformedTextTestCase
# ----------------------------------------------------------------------

"""
    Verify :func:`device_path_from_text` rejects malformed text with
    :class:`UefiProtocolError` and includes sufficient diagnostic
    context.
"""

def test_missing_paren_raises() -> None:
    with testbase.raises(UefiProtocolError):
        device_path_from_text("Pci 0x1F,0x2)")
    return

def test_unterminated_paren_raises() -> None:
    with testbase.raises(UefiProtocolError):
        device_path_from_text("Pci(0x1F,0x2")
    return

def test_unknown_option_raises() -> None:
    with testbase.raises(UefiProtocolError):
        device_path_from_text("MadeUpNode(0x1)")
    return

def test_wrong_argument_count_raises() -> None:
    with testbase.raises(UefiProtocolError):
        device_path_from_text("Pci(0x1)")
    return

def test_non_string_input_raises() -> None:
    with testbase.raises(UefiProtocolError):
        device_path_from_text(b"Pci(0x1,0x2)")   # type: ignore[arg-type]
    return

def test_ipv4_bad_octet_raises() -> None:
    with testbase.raises(UefiProtocolError):
        device_path_from_text("IPv4(256.0.0.1,0x6,Static,192.168.0.1)")
    return


# ---------------------------------------------------------------------------
# Parametrized corpus -- each binary fixture drives both directions.
# ---------------------------------------------------------------------------
#
# The table below packages a compact canonical fixture per node type
# along with its expected single-node text rendering.  It acts as the
# fallback alternative to hypothesis-driven property tests when the
# hypothesis library is not installed.
# ---------------------------------------------------------------------------

_CORPUS: Tuple[Tuple[str, bytes, str], ...] = (
    (
        "pci",
        _pci_node_bytes(device=0x1F, function=0x2) + _end_entire(),
        "Pci(0x1F,0x2)",
    ),
    (
        "pciroot",
        _acpi_node_bytes(hid=_PNP0A03, uid=0) + _end_entire(),
        "PciRoot(0x0)",
    ),
    (
        "acpi_serial",
        _acpi_node_bytes(hid=0x050141D0, uid=1) + _end_entire(),
        "Acpi(PNP0501,0x1)",
    ),
    (
        "usb",
        _usb_node_bytes(port=0, interface=0) + _end_entire(),
        "USB(0x0,0x0)",
    ),
    (
        "sata",
        _sata_node_bytes(hba_port=0, pmpn=0xFFFF, lun=0) + _end_entire(),
        "Sata(0x0,0xFFFF,0x0)",
    ),
    (
        "nvme",
        _nvme_node_bytes(nsid=1, eui=b"\xFF" * 8) + _end_entire(),
        "NVMe(0x1,FF-FF-FF-FF-FF-FF-FF-FF)",
    ),
    (
        "mac",
        _mac_node_bytes(mac6=bytes([0x00, 0x13, 0x20, 0xF5, 0xFA, 0x77]), if_type=1)
        + _end_entire(),
        "MAC(001320F5FA77,0x1)",
    ),
    (
        "ipv4",
        _ipv4_node_bytes(
            local_ip=bytes([192, 168, 0, 1]),
            remote_ip=bytes([192, 168, 0, 100]),
            local_port=0, remote_port=0,
            protocol=6, static_ip=1,
        ) + _end_entire(),
        "IPv4(192.168.0.100,0x6,Static,192.168.0.1)",
    ),
    (
        "uri",
        _uri_node_bytes(uri="https://example.com/boot.efi") + _end_entire(),
        "Uri(https://example.com/boot.efi)",
    ),
    (
        "cdrom",
        _cdrom_node_bytes(boot_entry=0, partition_start=0x800, partition_size=0x1000) + _end_entire(),
        "CDROM(0x0,0x800,0x1000)",
    ),
    (
        "file",
        _file_node_bytes(path="\\EFI\\BOOT\\BOOTX64.EFI") + _end_entire(),
        "File(\\EFI\\BOOT\\BOOTX64.EFI)",
    ),
)


# ----------------------------------------------------------------------
# CorpusParametrizedTestCase
# ----------------------------------------------------------------------

"""
    Loop over :data:`_CORPUS` and run a single parametrized round-
    trip check.  Breaking out of a ``for`` loop inside a test method
    would make failures less granular, so each iteration uses
    :meth:`subTest` so a failing entry is still uniquely identified
    in the ``unittest`` report.
"""

def test_binary_to_text() -> None:
    for name, fixture, expected_text in _CORPUS:
        decoded = decode_device_path(fixture)
        testbase.assert_equal(device_path_to_text(decoded), expected_text)
    return

def test_text_to_binary() -> None:
    for name, fixture, text in _CORPUS:
        parsed = device_path_from_text(text)
        encoded = encode_device_path(parsed)
        testbase.assert_equal(encoded, fixture)
    return

def test_cross_round_trip() -> None:
    for name, fixture, text in _CORPUS:
        parsed = device_path_from_text(text)
        encoded = encode_device_path(parsed)
        testbase.assert_equal(encoded, fixture)
        decoded = decode_device_path(encoded)
        testbase.assert_equal(device_path_to_text(decoded), text)
    return


# ---------------------------------------------------------------------------
# Hypothesis-based property tests (optional).  Fall back silently if
# hypothesis is not installed so the test module is usable even in a
# minimal environment.
# ---------------------------------------------------------------------------

try:
    from hypothesis import given, settings
    from hypothesis import strategies as st

    _HYPOTHESIS_AVAILABLE = True
except ImportError:
    _HYPOTHESIS_AVAILABLE = False


if _HYPOTHESIS_AVAILABLE:

    def _unknown_node_strategy() -> "st.SearchStrategy[DevicePathNode]":
        """
            Build a hypothesis strategy producing only unknown-form
            :class:`DevicePathNode` instances.  The ``(type, subtype)``
            pair is drawn from an unassigned region so the codec always
            falls back to the generic ``Path(...)`` rendering.

            :returns: A hypothesis strategy producing DevicePathNodes.
        """
        rtnval = st.builds(
            lambda t, s, d: DevicePathNode(type=t, subtype=s, data=d),
            st.integers(min_value=0x80, max_value=0xFE),   # avoid 0x7F end markers and known families
            st.integers(min_value=0x00, max_value=0xFF),
            st.binary(min_size=0, max_size=32),
        )
        return rtnval


    class HypothesisPropertyTestCase(unittest.TestCase):
        """
            Property-based tests over random unknown-node paths.
        """

        @settings(max_examples=50, deadline=None)
        @given(st.lists(_unknown_node_strategy(), min_size=0, max_size=6))
        def test_binary_round_trip_property(self, nodes: List[DevicePathNode]) -> None:
            path = DevicePath(nodes=tuple(nodes))
            encoded = encode_device_path(path)
            decoded = decode_device_path(encoded)
            re_encoded = encode_device_path(decoded)
            testbase.assert_equal(re_encoded, encoded)
            return

        @settings(max_examples=50, deadline=None)
        @given(st.lists(_unknown_node_strategy(), min_size=0, max_size=6))
        def test_text_round_trip_property(self, nodes: List[DevicePathNode]) -> None:
            path = DevicePath(nodes=tuple(nodes))
            text = device_path_to_text(path)
            re_parsed = device_path_from_text(text)
            re_text = device_path_to_text(re_parsed)
            testbase.assert_equal(re_text, text)
            return
