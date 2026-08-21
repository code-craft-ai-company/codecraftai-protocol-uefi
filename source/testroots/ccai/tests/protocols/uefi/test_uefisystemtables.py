"""
    .. module:: test_uefisystemtables
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Unit tests for the :mod:`ccai.protocols.uefi.uefisystemtables`
               module.  Exercises each parser's :meth:`from_bytes` round trip
               against byte fixtures crafted in-memory, the :class:`SystemTables`
               facade's ``ssh-host`` happy path using a canned-output fake
               agent, :meth:`SmbiosTable.find_by_type`, and the
               unsupported-transport error path.

               No real serial ports, SSH sessions, or BMC endpoints are
               touched; every test uses a minimal :class:`_FakeUefiAgent`
               stand-in.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Code Craft AI LLC"
__credits__ = []


from ccai import testbase

import struct
import uuid

from typing import Any, Callable, List, Optional, Tuple

from ccai.protocols.uefi.uefiaspects import (
    DEFAULT_CMD_ASPECTS,
    UefiCmdAspects,
)
from ccai.protocols.uefi.ueficonst import guid_to_bytes
from ccai.protocols.uefi.uefiexceptions import UefiError
from ccai.protocols.uefi.uefisystemtables import (
    ACPI_RSDP_SIGNATURE,
    AcpiRsdp,
    EsrtEntry,
    EsrtTable,
    MemoryAttributesEntry,
    MemoryAttributesTable,
    SMBIOS_ANCHOR_2X,
    SmbiosStructure,
    SmbiosTable,
    SystemTables,
)


# ---------------------------------------------------------------------------
# Fake UefiAgent used by the facade tests
# ---------------------------------------------------------------------------


class _FakeUefiAgent:
    """
        Minimal :class:`UefiAgent` stand-in.  Records every ``run_cmd``
        call and returns canned ``(status, stdout, stderr)`` tuples based
        on a caller-supplied ``responder`` callable.
    """

    def __init__(self, transport_kind: str,
                       responder: Optional[Callable[[str, Optional[UefiCmdAspects]], Tuple[int, str, str]]] = None) -> None:
        """
            Construct the fake agent.

            :param transport_kind: Transport kind selector string.
            :param responder: Callable mapping ``(command, aspects)`` to
                              ``(status, stdout, stderr)``.  Defaults to a
                              responder that returns an empty-success
                              tuple.
        """
        self.transport_kind: str = transport_kind
        self.calls: List[Tuple[str, Optional[UefiCmdAspects]]] = []

        if responder is None:
            default_responder: Callable[[str, Optional[UefiCmdAspects]], Tuple[int, str, str]] = (
                lambda command, aspects: (0, "", "")
            )
            self._responder = default_responder
        else:
            self._responder = responder
        return

    def run_cmd(self, command: str,
                      exp_status: Any = 0,
                      user: Optional[str] = None,
                      aspects: Optional[UefiCmdAspects] = None) -> Tuple[int, str, str]:
        """
            Record the call and delegate to the canned responder.
        """
        self.calls.append((command, aspects))
        rtnval = self._responder(command, aspects)
        return rtnval


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _build_esrt_bytes(entries: List[EsrtEntry],
                      fw_resource_count_max: int = 4,
                      fw_resource_version: int = 1) -> bytes:
    """
        Build a well-formed ESRT byte buffer suitable for feeding to
        :meth:`EsrtTable.from_bytes`.

        :param entries: Pre-built :class:`EsrtEntry` values.
        :param fw_resource_count_max: Value to write into the
                                      ``FwResourceCountMax`` field.
        :param fw_resource_version: Value to write into the
                                    ``FwResourceVersion`` field.

        :returns: A bytes buffer containing header + entries.
    """
    count = len(entries)
    header = struct.pack(
        "<IIQ16s", count, fw_resource_count_max, fw_resource_version,
        b"\x00" * 16,
    )
    body = bytearray()
    for entry in entries:
        guid_bytes = guid_to_bytes(entry.fw_class)
        body.extend(guid_bytes)
        body.extend(struct.pack(
            "<IIIIII",
            entry.fw_type,
            entry.fw_version,
            entry.lowest_supported_version,
            entry.capsule_flags,
            entry.last_attempt_version,
            entry.last_attempt_status,
        ))

    rtnval = header + bytes(body)
    return rtnval


def _build_smbios_structure(structure_type: int,
                            handle: int,
                            formatted_payload: bytes,
                            strings: List[str]) -> bytes:
    """
        Build a single SMBIOS structure (formatted area + string section).

        :param structure_type: The type code.
        :param handle: The 16-bit handle.
        :param formatted_payload: Bytes appended after the 4-byte header.
                                  ``length`` is computed as ``4 +
                                  len(formatted_payload)``.
        :param strings: List of strings to emit.  Empty list is allowed.

        :returns: The encoded structure including its double-NUL terminator.
    """
    length = 4 + len(formatted_payload)
    structure = struct.pack("<BBH", structure_type, length, handle)
    structure += formatted_payload

    if len(strings) == 0:
        structure += b"\x00\x00"
    else:
        string_section = b""
        for raw_string in strings:
            string_section += raw_string.encode("latin-1") + b"\x00"
        string_section += b"\x00"  # second terminator
        structure += string_section

    rtnval = structure
    return rtnval


def _build_smbios_bytes(structures: List[bytes],
                        version_major: int = 3,
                        version_minor: int = 2) -> bytes:
    """
        Build a 2.x SMBIOS entry point followed by the concatenated
        ``structures`` stream.  The 2.x entry point is used because its
        fields are easier to produce by hand; the parser accepts both
        anchors.

        :param structures: Pre-built structure byte blobs in order.
        :param version_major: Major SMBIOS version.
        :param version_minor: Minor SMBIOS version.

        :returns: The full 31-byte entry point plus structure stream.
    """
    stream = b"".join(structures)
    table_length = len(stream)

    # Entry point layout (2.x): 31 bytes total.  Most fields are
    # placeholders here because the parser only consumes anchor, version,
    # and table length.
    entry_point = bytearray(31)
    entry_point[0:4] = SMBIOS_ANCHOR_2X
    entry_point[4] = 0x00  # checksum placeholder
    entry_point[5] = 0x1F  # entry point length
    entry_point[6] = version_major
    entry_point[7] = version_minor
    entry_point[8:10] = struct.pack("<H", 0xFFFF)  # max structure size
    entry_point[10] = 0x00  # entry point revision
    # 0x0B..0x0F formatted area
    entry_point[0x10:0x15] = b"_DMI_"
    entry_point[0x15] = 0x00  # intermediate checksum
    entry_point[0x16:0x18] = struct.pack("<H", table_length)
    entry_point[0x18:0x1C] = struct.pack("<I", 0x00000000)  # address
    entry_point[0x1C:0x1E] = struct.pack("<H", len(structures))
    entry_point[0x1E] = 0x00  # BCD revision

    rtnval = bytes(entry_point) + stream
    return rtnval


def _build_acpi_rsdp_v2(revision: int = 2) -> bytes:
    """
        Build an ACPI 2.0+ RSDP byte buffer.

        :param revision: The revision byte to embed.

        :returns: The encoded 36-byte RSDP.
    """
    signature = ACPI_RSDP_SIGNATURE
    checksum = 0x12
    oem_id = b"ACPITS"
    rsdt_address = 0xDEADBEEF
    length = 36
    xsdt_address = 0x00000000_CAFEBABE
    extended_checksum = 0x34
    reserved = b"\x00\x00\x00"

    buffer = bytearray()
    buffer.extend(signature)
    buffer.append(checksum)
    buffer.extend(oem_id)
    buffer.append(revision)
    buffer.extend(struct.pack("<I", rsdt_address))
    buffer.extend(struct.pack("<I", length))
    buffer.extend(struct.pack("<Q", xsdt_address))
    buffer.append(extended_checksum)
    buffer.extend(reserved)

    rtnval = bytes(buffer)
    return rtnval


def _build_memory_attributes_bytes(entries: List[MemoryAttributesEntry],
                                   version: int = 1,
                                   descriptor_size: int = 40) -> bytes:
    """
        Build a well-formed memory-attributes-table buffer.

        :param entries: Pre-built :class:`MemoryAttributesEntry` values.
        :param version: Value to write into the ``Version`` field.
        :param descriptor_size: Value to write into the ``DescriptorSize``
                                field.

        :returns: The encoded MAT.
    """
    count = len(entries)
    header = struct.pack("<IIII", version, count, descriptor_size, 0)

    body = bytearray()
    for entry in entries:
        descriptor = struct.pack(
            "<IIQQQQ",
            entry.memory_type,
            0,  # padding
            entry.physical_start,
            entry.virtual_start,
            entry.number_of_pages,
            entry.attribute,
        )
        # Descriptor size may be larger than the 40-byte spec minimum; pad.
        if descriptor_size > len(descriptor):
            descriptor = descriptor + b"\x00" * (descriptor_size - len(descriptor))
        body.extend(descriptor)

    rtnval = header + bytes(body)
    return rtnval


# ---------------------------------------------------------------------------
# ESRT parser tests
# ---------------------------------------------------------------------------


# ----------------------------------------------------------------------
# EsrtParserTests
# ----------------------------------------------------------------------

"""
    Round-trip tests for :class:`EsrtTable.from_bytes`.
"""

def test_esrt_parser_tests_round_trip_single_entry() -> None:
    """
        Encode one ESRT entry and decode it back to the same values.
    """
    expected_entry = EsrtEntry(
        fw_class=uuid.UUID("12345678-1234-5678-1234-567812345678"),
        fw_type=2,
        fw_version=0x0010_0000,
        lowest_supported_version=0x0008_0000,
        capsule_flags=0x0000_0000,
        last_attempt_version=0x0010_0000,
        last_attempt_status=0x0000_0000,
    )
    encoded = _build_esrt_bytes(entries=[expected_entry])

    decoded = EsrtTable.from_bytes(buf=encoded)

    testbase.assert_equal(decoded.fw_resource_count, 1)
    testbase.assert_equal(decoded.fw_resource_version, 1)
    testbase.assert_equal(len(decoded.entries), 1)
    decoded_entry = decoded.entries[0]
    testbase.assert_equal(decoded_entry.fw_class, expected_entry.fw_class)
    testbase.assert_equal(decoded_entry.fw_type, expected_entry.fw_type)
    testbase.assert_equal(decoded_entry.fw_version, expected_entry.fw_version)
    testbase.assert_equal(decoded_entry.lowest_supported_version,
                     expected_entry.lowest_supported_version)
    testbase.assert_equal(decoded_entry.capsule_flags,
                     expected_entry.capsule_flags)
    testbase.assert_equal(decoded_entry.last_attempt_version,
                     expected_entry.last_attempt_version)
    testbase.assert_equal(decoded_entry.last_attempt_status,
                     expected_entry.last_attempt_status)
    return

def test_esrt_parser_tests_round_trip_multiple_entries() -> None:
    """
        Encode three ESRT entries and decode all three back.
    """
    entry_a = EsrtEntry(
        fw_class=uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        fw_type=2,
        fw_version=1,
        lowest_supported_version=1,
        capsule_flags=0,
        last_attempt_version=1,
        last_attempt_status=0,
    )
    entry_b = EsrtEntry(
        fw_class=uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        fw_type=3,
        fw_version=2,
        lowest_supported_version=1,
        capsule_flags=0,
        last_attempt_version=2,
        last_attempt_status=0,
    )
    entry_c = EsrtEntry(
        fw_class=uuid.UUID("cccccccc-cccc-cccc-cccc-cccccccccccc"),
        fw_type=4,
        fw_version=3,
        lowest_supported_version=2,
        capsule_flags=0,
        last_attempt_version=3,
        last_attempt_status=0,
    )
    encoded = _build_esrt_bytes(entries=[entry_a, entry_b, entry_c])

    decoded = EsrtTable.from_bytes(buf=encoded)

    testbase.assert_equal(decoded.fw_resource_count, 3)
    testbase.assert_equal(len(decoded.entries), 3)
    testbase.assert_equal(decoded.entries[0].fw_class, entry_a.fw_class)
    testbase.assert_equal(decoded.entries[1].fw_class, entry_b.fw_class)
    testbase.assert_equal(decoded.entries[2].fw_class, entry_c.fw_class)
    return

def test_esrt_parser_tests_short_header_raises() -> None:
    """
        A buffer shorter than the 32-byte header raises
        :class:`UefiError`.
    """
    short = b"\x00" * 8
    with testbase.raises(UefiError):
        EsrtTable.from_bytes(buf=short)
    return

def test_esrt_parser_tests_bad_version_raises() -> None:
    """
        A header with the wrong FwResourceVersion raises
        :class:`UefiError`.
    """
    bad = struct.pack("<IIQ16s", 0, 0, 99, b"\x00" * 16)
    with testbase.raises(UefiError):
        EsrtTable.from_bytes(buf=bad)
    return

def test_esrt_parser_tests_entries_body_short_raises() -> None:
    """
        A header that declares more entries than the body contains
        raises :class:`UefiError`.
    """
    truncated = struct.pack("<IIQ16s", 2, 2, 1, b"\x00" * 16)
    # Append one partial entry rather than two full.
    truncated = truncated + b"\x00" * 16
    with testbase.raises(UefiError):
        EsrtTable.from_bytes(buf=truncated)
    return


# ---------------------------------------------------------------------------
# SMBIOS parser tests
# ---------------------------------------------------------------------------


# ----------------------------------------------------------------------
# SmbiosParserTests
# ----------------------------------------------------------------------

"""
    Round-trip and API tests for :class:`SmbiosTable`.
"""

def test_round_trip_two_structures() -> None:
    """
        Encode a type 0 and a type 1 structure, decode them, and
        validate the fields and strings come back unchanged.
    """
    bios_structure = _build_smbios_structure(
        structure_type=0,
        handle=0x0001,
        formatted_payload=bytes([0x01, 0x02, 0x03]),
        strings=["VendorABC", "1.0.0"],
    )
    system_structure = _build_smbios_structure(
        structure_type=1,
        handle=0x0002,
        formatted_payload=bytes([0x04, 0x05]),
        strings=["ACME Corp", "Model-1", "Serial-42"],
    )
    end_marker = _build_smbios_structure(
        structure_type=0x7F,
        handle=0xFFFF,
        formatted_payload=b"",
        strings=[],
    )

    encoded = _build_smbios_bytes(
        structures=[bios_structure, system_structure, end_marker],
        version_major=3,
        version_minor=2,
    )

    decoded = SmbiosTable.from_bytes(buf=encoded)

    testbase.assert_equal(decoded.anchor, SMBIOS_ANCHOR_2X)
    testbase.assert_equal(decoded.version_major, 3)
    testbase.assert_equal(decoded.version_minor, 2)
    testbase.assert_equal(len(decoded.structures), 3)

    bios = decoded.structures[0]
    testbase.assert_equal(bios.structure_type, 0)
    testbase.assert_equal(bios.handle, 0x0001)
    testbase.assert_equal(bios.strings, ("VendorABC", "1.0.0"))

    system = decoded.structures[1]
    testbase.assert_equal(system.structure_type, 1)
    testbase.assert_equal(system.handle, 0x0002)
    testbase.assert_equal(system.strings, ("ACME Corp", "Model-1", "Serial-42"))

    terminator = decoded.structures[2]
    testbase.assert_equal(terminator.structure_type, 0x7F)
    return

def test_find_by_type() -> None:
    """
        :meth:`SmbiosTable.find_by_type` returns only the structures
        matching the requested type code.
    """
    type_1_a = _build_smbios_structure(
        structure_type=1, handle=0x0010,
        formatted_payload=b"\x00", strings=["A"],
    )
    type_1_b = _build_smbios_structure(
        structure_type=1, handle=0x0011,
        formatted_payload=b"\x01", strings=["B"],
    )
    type_2 = _build_smbios_structure(
        structure_type=2, handle=0x0020,
        formatted_payload=b"\x02", strings=["C"],
    )
    end_marker = _build_smbios_structure(
        structure_type=0x7F, handle=0xFFFF,
        formatted_payload=b"", strings=[],
    )

    encoded = _build_smbios_bytes(
        structures=[type_1_a, type_1_b, type_2, end_marker],
    )
    decoded = SmbiosTable.from_bytes(buf=encoded)

    type_1_matches = decoded.find_by_type(structure_type=1)
    testbase.assert_equal(len(type_1_matches), 2)
    testbase.assert_equal(type_1_matches[0].handle, 0x0010)
    testbase.assert_equal(type_1_matches[1].handle, 0x0011)

    type_2_matches = decoded.find_by_type(structure_type=2)
    testbase.assert_equal(len(type_2_matches), 1)
    testbase.assert_equal(type_2_matches[0].handle, 0x0020)

    type_99_matches = decoded.find_by_type(structure_type=99)
    testbase.assert_equal(len(type_99_matches), 0)
    return

def test_missing_double_nul_raises() -> None:
    """
        An SMBIOS structure whose string section is missing the
        double-NUL terminator raises :class:`UefiError`.
    """
    # Build a structure manually without the trailing double-NUL.
    broken_structure = struct.pack("<BBH", 1, 4, 0x0001) + b"TEXT"
    # Provide a 31-byte entry point wrapping this broken stream.
    table_length = len(broken_structure)
    entry_point = bytearray(31)
    entry_point[0:4] = SMBIOS_ANCHOR_2X
    entry_point[5] = 0x1F
    entry_point[6] = 2
    entry_point[7] = 8
    entry_point[0x16:0x18] = struct.pack("<H", table_length)
    encoded = bytes(entry_point) + broken_structure

    with testbase.raises(UefiError):
        SmbiosTable.from_bytes(buf=encoded)
    return

def test_unknown_anchor_raises() -> None:
    """
        A buffer without either the ``_SM_`` or ``_SM3_`` anchor
        raises :class:`UefiError`.
    """
    bogus = b"XXXX" + b"\x00" * 40
    with testbase.raises(UefiError):
        SmbiosTable.from_bytes(buf=bogus)
    return


# ---------------------------------------------------------------------------
# ACPI RSDP parser tests
# ---------------------------------------------------------------------------


# ----------------------------------------------------------------------
# AcpiRsdpParserTests
# ----------------------------------------------------------------------

"""
    Round-trip tests for :class:`AcpiRsdp.from_bytes`.
"""

def test_round_trip_v2() -> None:
    """
        Build a v2 RSDP and round-trip its fields.
    """
    encoded = _build_acpi_rsdp_v2(revision=2)

    decoded = AcpiRsdp.from_bytes(buf=encoded)

    testbase.assert_equal(decoded.signature, ACPI_RSDP_SIGNATURE)
    testbase.assert_equal(decoded.oem_id, "ACPITS")
    testbase.assert_equal(decoded.revision, 2)
    testbase.assert_equal(decoded.rsdt_address, 0xDEADBEEF)
    testbase.assert_equal(decoded.length, 36)
    testbase.assert_equal(decoded.xsdt_address, 0x00000000_CAFEBABE)
    testbase.assert_equal(decoded.extended_checksum, 0x34)
    return

def test_round_trip_v1() -> None:
    """
        A 20-byte RSDP with revision 0 populates only the v1 fields.
    """
    v2 = _build_acpi_rsdp_v2(revision=0)
    encoded = v2[:20]

    decoded = AcpiRsdp.from_bytes(buf=encoded)

    testbase.assert_equal(decoded.signature, ACPI_RSDP_SIGNATURE)
    testbase.assert_equal(decoded.revision, 0)
    testbase.assert_equal(decoded.rsdt_address, 0xDEADBEEF)
    testbase.assert_true(decoded.length is None)
    testbase.assert_true(decoded.xsdt_address is None)
    testbase.assert_true(decoded.extended_checksum is None)
    return

def test_bad_signature_raises() -> None:
    """
        A buffer with the wrong signature raises :class:`UefiError`.
    """
    bad = b"BAD SIG!" + b"\x00" * 28
    with testbase.raises(UefiError):
        AcpiRsdp.from_bytes(buf=bad)
    return

def test_short_buffer_raises() -> None:
    """
        A buffer shorter than the v1 header raises :class:`UefiError`.
    """
    short = ACPI_RSDP_SIGNATURE + b"\x00" * 4
    with testbase.raises(UefiError):
        AcpiRsdp.from_bytes(buf=short)
    return

def test_revision_2_with_short_buffer_raises() -> None:
    """
        A buffer that declares revision 2 but is only 20 bytes long
        raises :class:`UefiError`.
    """
    v2 = _build_acpi_rsdp_v2(revision=2)
    # Keep only the v1 portion while leaving revision=2 in place.
    truncated = v2[:20]
    with testbase.raises(UefiError):
        AcpiRsdp.from_bytes(buf=truncated)
    return


# ---------------------------------------------------------------------------
# Memory Attributes Table parser tests
# ---------------------------------------------------------------------------


# ----------------------------------------------------------------------
# MemoryAttributesParserTests
# ----------------------------------------------------------------------

"""
    Round-trip tests for :class:`MemoryAttributesTable.from_bytes`.
"""

def test_memory_attributes_parser_tests_round_trip_two_entries() -> None:
    """
        Encode two memory-attribute entries and decode them back.
    """
    entry_a = MemoryAttributesEntry(
        memory_type=3,
        physical_start=0x0000_0000_1000_0000,
        virtual_start=0x0000_0000_1000_0000,
        number_of_pages=0x0010,
        attribute=0x0000_0000_0000_2000,
    )
    entry_b = MemoryAttributesEntry(
        memory_type=6,
        physical_start=0x0000_0000_2000_0000,
        virtual_start=0x0000_0000_2000_0000,
        number_of_pages=0x0020,
        attribute=0x0000_0000_0002_0000,
    )
    encoded = _build_memory_attributes_bytes(entries=[entry_a, entry_b])

    decoded = MemoryAttributesTable.from_bytes(buf=encoded)

    testbase.assert_equal(decoded.version, 1)
    testbase.assert_equal(decoded.number_of_entries, 2)
    testbase.assert_equal(decoded.descriptor_size, 40)
    testbase.assert_equal(len(decoded.entries), 2)

    first = decoded.entries[0]
    testbase.assert_equal(first.memory_type, entry_a.memory_type)
    testbase.assert_equal(first.physical_start, entry_a.physical_start)
    testbase.assert_equal(first.attribute, entry_a.attribute)

    second = decoded.entries[1]
    testbase.assert_equal(second.memory_type, entry_b.memory_type)
    testbase.assert_equal(second.physical_start, entry_b.physical_start)
    testbase.assert_equal(second.attribute, entry_b.attribute)
    return

def test_memory_attributes_parser_tests_bad_version_raises() -> None:
    """
        A header with ``Version != 1`` raises :class:`UefiError`.
    """
    bad = struct.pack("<IIII", 99, 0, 40, 0)
    with testbase.raises(UefiError):
        MemoryAttributesTable.from_bytes(buf=bad)
    return

def test_memory_attributes_parser_tests_short_header_raises() -> None:
    """
        A buffer shorter than the 16-byte MAT header raises
        :class:`UefiError`.
    """
    short = b"\x01" * 4
    with testbase.raises(UefiError):
        MemoryAttributesTable.from_bytes(buf=short)
    return

def test_memory_attributes_parser_tests_descriptor_size_below_minimum_raises() -> None:
    """
        A header with ``DescriptorSize`` below the 40-byte minimum
        raises :class:`UefiError`.
    """
    bad = struct.pack("<IIII", 1, 0, 8, 0)
    with testbase.raises(UefiError):
        MemoryAttributesTable.from_bytes(buf=bad)
    return


# ---------------------------------------------------------------------------
# SystemTables facade tests
# ---------------------------------------------------------------------------


def _hex_dump(blob: bytes) -> str:
    """
        Emit ``blob`` as a single continuous hex string followed by a
        newline - the exact shape ``xxd -p -c0`` produces.

        :param blob: The bytes to encode.

        :returns: The canonical hex-dump string.
    """
    rtnval = blob.hex() + "\n"
    return rtnval


# ----------------------------------------------------------------------
# SystemTablesFacadeTests
# ----------------------------------------------------------------------

"""
    Exercises the :class:`SystemTables` facade's ``ssh-host`` dispatch
    path and the unsupported-transport error path.
"""

def test_read_esrt_ssh_host_happy_path() -> None:
    """
        When the fake agent returns canned hex for the ESRT sysfs
        file, :meth:`SystemTables.read_esrt` returns a parsed
        :class:`EsrtTable`.
    """
    entry = EsrtEntry(
        fw_class=uuid.UUID("12345678-1234-5678-1234-567812345678"),
        fw_type=2, fw_version=1,
        lowest_supported_version=1, capsule_flags=0,
        last_attempt_version=1, last_attempt_status=0,
    )
    esrt_bytes = _build_esrt_bytes(entries=[entry])
    canned_hex = _hex_dump(blob=esrt_bytes)

    def responder(command: str,
                  aspects: Optional[UefiCmdAspects]) -> Tuple[int, str, str]:
        """
            Return the canned hex-dump for the ESRT path.
        """
        if "/sys/firmware/efi/esrt/table" in command:
            rtnval = (0, canned_hex, "")
            return rtnval
        rtnval = (1, "", "unknown path")
        return rtnval

    fake = _FakeUefiAgent(
        transport_kind="ssh-host", responder=responder,
    )
    tables = SystemTables(agent=fake, aspects=DEFAULT_CMD_ASPECTS)

    decoded = tables.read_esrt()

    testbase.assert_equal(decoded.fw_resource_count, 1)
    testbase.assert_equal(decoded.entries[0].fw_class, entry.fw_class)
    testbase.assert_equal(len(fake.calls), 1)
    return

def test_read_smbios_ssh_host_happy_path() -> None:
    """
        :meth:`SystemTables.read_smbios` concatenates the entry-point
        and DMI sysfs reads before feeding the result to the parser.
    """
    end_marker = _build_smbios_structure(
        structure_type=0x7F, handle=0xFFFF,
        formatted_payload=b"", strings=[],
    )
    smbios_bytes = _build_smbios_bytes(
        structures=[end_marker], version_major=3, version_minor=0,
    )
    # Split so the facade concatenates the two canned reads.
    ep_bytes = smbios_bytes[:31]
    dmi_bytes = smbios_bytes[31:]

    def responder(command: str,
                  aspects: Optional[UefiCmdAspects]) -> Tuple[int, str, str]:
        """
            Return the split entry-point and DMI payloads.
        """
        if "smbios_entry_point" in command:
            rtnval = (0, _hex_dump(blob=ep_bytes), "")
            return rtnval
        if "/tables/DMI" in command:
            rtnval = (0, _hex_dump(blob=dmi_bytes), "")
            return rtnval
        rtnval = (1, "", "unknown path")
        return rtnval

    fake = _FakeUefiAgent(
        transport_kind="ssh-host", responder=responder,
    )
    tables = SystemTables(agent=fake, aspects=DEFAULT_CMD_ASPECTS)

    decoded = tables.read_smbios()

    testbase.assert_equal(decoded.anchor, SMBIOS_ANCHOR_2X)
    testbase.assert_equal(decoded.version_major, 3)
    testbase.assert_equal(decoded.version_minor, 0)
    return

def test_read_acpi_rsdp_ssh_host_happy_path() -> None:
    """
        :meth:`SystemTables.read_acpi_rsdp` decodes the canned hex
        dump of the RSDP file into an :class:`AcpiRsdp` instance.
    """
    rsdp_bytes = _build_acpi_rsdp_v2(revision=2)
    canned_hex = _hex_dump(blob=rsdp_bytes)

    def responder(command: str,
                  aspects: Optional[UefiCmdAspects]) -> Tuple[int, str, str]:
        """
            Return the canned RSDP payload.
        """
        if "/sys/firmware/acpi/tables/RSDP" in command:
            rtnval = (0, canned_hex, "")
            return rtnval
        rtnval = (1, "", "unknown path")
        return rtnval

    fake = _FakeUefiAgent(
        transport_kind="ssh-host", responder=responder,
    )
    tables = SystemTables(agent=fake, aspects=DEFAULT_CMD_ASPECTS)

    decoded = tables.read_acpi_rsdp()

    testbase.assert_equal(decoded.signature, ACPI_RSDP_SIGNATURE)
    testbase.assert_equal(decoded.revision, 2)
    testbase.assert_equal(decoded.rsdt_address, 0xDEADBEEF)
    testbase.assert_equal(decoded.xsdt_address, 0x00000000_CAFEBABE)
    return

def test_read_memory_attributes_table_ssh_host_happy_path() -> None:
    """
        :meth:`SystemTables.read_memory_attributes_table` decodes a
        canned MAT payload into a :class:`MemoryAttributesTable`.
    """
    entry = MemoryAttributesEntry(
        memory_type=3,
        physical_start=0x0000_0000_1000_0000,
        virtual_start=0x0000_0000_1000_0000,
        number_of_pages=0x0010,
        attribute=0x0000_0000_0000_2000,
    )
    mat_bytes = _build_memory_attributes_bytes(entries=[entry])
    canned_hex = _hex_dump(blob=mat_bytes)

    def responder(command: str,
                  aspects: Optional[UefiCmdAspects]) -> Tuple[int, str, str]:
        """
            Return the canned MAT payload.
        """
        if "MemoryAttributesTable" in command:
            rtnval = (0, canned_hex, "")
            return rtnval
        rtnval = (1, "", "unknown path")
        return rtnval

    fake = _FakeUefiAgent(
        transport_kind="ssh-host", responder=responder,
    )
    tables = SystemTables(agent=fake, aspects=DEFAULT_CMD_ASPECTS)

    decoded = tables.read_memory_attributes_table()

    testbase.assert_equal(decoded.version, 1)
    testbase.assert_equal(decoded.number_of_entries, 1)
    testbase.assert_equal(decoded.entries[0].attribute, entry.attribute)
    return

def test_read_acpi_rsdp_unsupported_on_redfish() -> None:
    """
        :meth:`SystemTables.read_acpi_rsdp` raises :class:`UefiError`
        with a clear "not supported" message on a ``redfish``
        transport.
    """
    fake = _FakeUefiAgent(transport_kind="redfish")
    tables = SystemTables(agent=fake, aspects=DEFAULT_CMD_ASPECTS)

    with testbase.raises(UefiError) as caught:
        tables.read_acpi_rsdp()

    message = str(caught.exception)
    testbase.assert_true("not supported" in message)
    testbase.assert_true("'redfish'" in message)
    return

def test_read_esrt_unsupported_on_redfish() -> None:
    """
        :meth:`SystemTables.read_esrt` raises :class:`UefiError` on a
        ``redfish`` transport because this facade does not implement
        the Redfish firmware-inventory reassembly path.
    """
    fake = _FakeUefiAgent(transport_kind="redfish")
    tables = SystemTables(agent=fake, aspects=DEFAULT_CMD_ASPECTS)

    with testbase.raises(UefiError) as caught:
        tables.read_esrt()

    message = str(caught.exception)
    testbase.assert_true("not supported" in message)
    testbase.assert_true("'redfish'" in message)
    return

def test_read_memory_attributes_unsupported_on_serial() -> None:
    """
        :meth:`SystemTables.read_memory_attributes_table` raises
        :class:`UefiError` on a ``serial`` transport.
    """
    fake = _FakeUefiAgent(transport_kind="serial")
    tables = SystemTables(agent=fake, aspects=DEFAULT_CMD_ASPECTS)

    with testbase.raises(UefiError) as caught:
        tables.read_memory_attributes_table()

    message = str(caught.exception)
    testbase.assert_true("not supported" in message)
    testbase.assert_true("'serial'" in message)
    return

def test_unknown_transport_kind_raises() -> None:
    """
        A transport kind that does not appear in the allowed set
        produces an "unknown transport_kind" :class:`UefiError`.
    """
    fake = _FakeUefiAgent(transport_kind="bogus")
    tables = SystemTables(agent=fake, aspects=DEFAULT_CMD_ASPECTS)

    with testbase.raises(UefiError) as caught:
        tables.read_esrt()

    message = str(caught.exception)
    testbase.assert_true("unknown transport_kind" in message)
    return

def test_ssh_host_nonzero_status_raises() -> None:
    """
        When the agent returns a non-zero status the facade raises
        :class:`UefiError` with the command and status populated.
    """
    def responder(command: str,
                  aspects: Optional[UefiCmdAspects]) -> Tuple[int, str, str]:
        """
            Always report failure.
        """
        rtnval = (1, "", "permission denied")
        return rtnval

    fake = _FakeUefiAgent(
        transport_kind="ssh-host", responder=responder,
    )
    tables = SystemTables(agent=fake, aspects=DEFAULT_CMD_ASPECTS)

    with testbase.raises(UefiError) as caught:
        tables.read_esrt()

    message = str(caught.exception)
    testbase.assert_true("STATUS:" in message)
    testbase.assert_true("xxd" in message)
    return
