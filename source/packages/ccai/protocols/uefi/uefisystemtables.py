"""
    .. module:: uefisystemtables
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Module providing typed readout of the UEFI System Table and its
               adjacent tables: the EFI System Resource Table (ESRT,
               UEFI §23.4), the SMBIOS entry-point and table blob referenced
               from the EFI Configuration Table, the ACPI Root System
               Description Pointer (RSDP) and its XSDT / MADT children, and
               the ``EFI_MEMORY_ATTRIBUTES_TABLE`` (UEFI §4.6.4).  Each
               parser dataclass exposes a pure :meth:`from_bytes` classmethod
               that performs strict bounds and signature checking; the
               :class:`SystemTables` facade dispatches the raw-byte fetch
               over whichever transport a :class:`UefiAgent` is currently
               bound to and feeds the returned bytes into the matching
               parser.

               Parsers are transport-independent: they never open a socket
               or file handle, never import transport modules, and operate
               entirely on the buffer the caller hands them.  The facade in
               turn is the only class that knows how to turn
               ``/sys/firmware/efi/esrt/`` directory reads, UEFI Shell
               ``smbiosview`` / ``memmap`` output, or Redfish
               ``FirmwareInventory`` collections into the canonical
               serialized byte layouts defined in the UEFI and ACPI
               specifications.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Code Craft AI LLC"
__credits__ = []


import os
import re
import struct
import uuid

from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

from ccai.protocols.uefi.uefiaspects import (
    DEFAULT_CMD_ASPECTS,
    UefiCmdAspects,
    resolve_aspects,
)
from ccai.protocols.uefi.ueficonst import guid_from_bytes
from ccai.protocols.uefi.uefiexceptions import UefiError


__all__ = [
    "AcpiRsdp",
    "EsrtEntry",
    "EsrtTable",
    "MemoryAttributesEntry",
    "MemoryAttributesTable",
    "SmbiosStructure",
    "SmbiosTable",
    "SystemTables",
]


# ---------------------------------------------------------------------------
# Structural constants (UEFI §23.4, §4.6.4, ACPI 6.x, SMBIOS 3.x)
# ---------------------------------------------------------------------------

ESRT_HEADER_SIZE: int = 0x20
ESRT_ENTRY_SIZE: int = 0x28
ESRT_EXPECTED_VERSION: int = 1

MAT_HEADER_SIZE: int = 0x10
MAT_DESCRIPTOR_SIZE_MIN: int = 0x28
MAT_EXPECTED_VERSION: int = 1

SMBIOS_ANCHOR_2X: bytes = b"_SM_"
SMBIOS_ANCHOR_3X: bytes = b"_SM3_"
SMBIOS_STRUCTURE_HEADER_SIZE: int = 4

ACPI_RSDP_SIGNATURE: bytes = b"RSD PTR "
ACPI_RSDP_V1_SIZE: int = 20
ACPI_RSDP_V2_SIZE: int = 36


# ---------------------------------------------------------------------------
# ESRT
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EsrtEntry:
    """
        Single EFI System Resource Table entry per UEFI §23.4.

        :ivar fw_class: The firmware-class GUID (16-byte mixed-endian).
        :ivar fw_type: The firmware type code (1=unknown, 2=system firmware,
                       3=device firmware, 4=UEFI driver).
        :ivar fw_version: The currently-installed firmware version.
        :ivar lowest_supported_version: The lowest accepted rollback version.
        :ivar capsule_flags: Capsule flag bits understood by this component.
        :ivar last_attempt_version: Version number of the last update attempt.
        :ivar last_attempt_status: EFI_STATUS returned by the last attempt.
    """
    fw_class: uuid.UUID
    fw_type: int
    fw_version: int
    lowest_supported_version: int
    capsule_flags: int
    last_attempt_version: int
    last_attempt_status: int


@dataclass(frozen=True)
class EsrtTable:
    """
        Parsed EFI System Resource Table per UEFI §23.4.

        :ivar fw_resource_count: Declared number of entries in the table.
        :ivar fw_resource_count_max: Upper bound of entries the ESRT
                                     allocation can hold.
        :ivar fw_resource_version: Table version (must be 1).
        :ivar entries: Tuple of parsed :class:`EsrtEntry` values in table
                       order.
    """
    fw_resource_count: int
    fw_resource_count_max: int
    fw_resource_version: int
    entries: Tuple[EsrtEntry, ...]

    @classmethod
    def from_bytes(cls, buf: bytes) -> "EsrtTable":
        """
            Decode an ESRT buffer.

            :param buf: Raw table bytes starting at the ESRT header.

            :returns: A fully-populated :class:`EsrtTable`.
            :raises: UefiError
        """
        buffer = _require_buffer(field_name="EsrtTable.from_bytes", buf=buf)

        buffer_length = len(buffer)
        if buffer_length < ESRT_HEADER_SIZE:
            err_msg_lines = [
                "EsrtTable.from_bytes: buffer is shorter than the ESRT header.",
                "EXPECTED: at least {} bytes".format(ESRT_HEADER_SIZE),
                "OBSERVED: {} bytes".format(buffer_length),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg) from None

        fw_resource_count = _unpack_u32_le(buffer=buffer, offset=0x00)
        fw_resource_count_max = _unpack_u32_le(buffer=buffer, offset=0x04)
        fw_resource_version = _unpack_u64_le(buffer=buffer, offset=0x08)

        if fw_resource_version != ESRT_EXPECTED_VERSION:
            err_msg_lines = [
                "EsrtTable.from_bytes: unsupported FwResourceVersion.",
                "EXPECTED: 0x{:X}".format(ESRT_EXPECTED_VERSION),
                "OBSERVED: 0x{:X}".format(fw_resource_version),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg) from None

        total_entry_bytes = fw_resource_count * ESRT_ENTRY_SIZE
        required_length = ESRT_HEADER_SIZE + total_entry_bytes
        if buffer_length < required_length:
            err_msg_lines = [
                "EsrtTable.from_bytes: buffer is shorter than header plus declared entries.",
                "EXPECTED: at least {} bytes ({} header + {} * {} entry)".format(
                    required_length, ESRT_HEADER_SIZE, fw_resource_count, ESRT_ENTRY_SIZE,
                ),
                "OBSERVED: {} bytes".format(buffer_length),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg) from None

        parsed_entries: List[EsrtEntry] = []
        for index in range(fw_resource_count):
            entry_offset = ESRT_HEADER_SIZE + index * ESRT_ENTRY_SIZE
            entry = _parse_esrt_entry(
                buffer=buffer, offset=entry_offset, index=index,
            )
            parsed_entries.append(entry)

        rtnval = cls(
            fw_resource_count=fw_resource_count,
            fw_resource_count_max=fw_resource_count_max,
            fw_resource_version=fw_resource_version,
            entries=tuple(parsed_entries),
        )
        return rtnval


def _parse_esrt_entry(buffer: bytes, offset: int, index: int) -> EsrtEntry:
    """
        Decode a single ESRT entry at ``offset`` within ``buffer``.

        :param buffer: The full ESRT buffer.
        :param offset: The byte offset of the entry.
        :param index: The entry index (used for diagnostics).

        :returns: The decoded :class:`EsrtEntry`.
        :raises: UefiError
    """
    guid_slice = buffer[offset + 0x00:offset + 0x10]
    if len(guid_slice) != 0x10:
        err_msg_lines = [
            "EsrtTable.from_bytes: entry GUID slice is short.",
            "ENTRY_INDEX: {}".format(index),
            "EXPECTED: 16 bytes",
            "OBSERVED: {} bytes".format(len(guid_slice)),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiError(err_msg) from None

    try:
        fw_class = guid_from_bytes(guid_slice)
    except (TypeError, ValueError) as verr:
        err_msg_lines = [
            "EsrtTable.from_bytes: could not decode entry GUID.",
            "ENTRY_INDEX: {}".format(index),
            "OBSERVED: {!r}".format(guid_slice),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiError(err_msg) from verr

    fw_type = _unpack_u32_le(buffer=buffer, offset=offset + 0x10)
    fw_version = _unpack_u32_le(buffer=buffer, offset=offset + 0x14)
    lowest_supported_version = _unpack_u32_le(buffer=buffer, offset=offset + 0x18)
    capsule_flags = _unpack_u32_le(buffer=buffer, offset=offset + 0x1C)
    last_attempt_version = _unpack_u32_le(buffer=buffer, offset=offset + 0x20)
    last_attempt_status = _unpack_u32_le(buffer=buffer, offset=offset + 0x24)

    rtnval = EsrtEntry(
        fw_class=fw_class,
        fw_type=fw_type,
        fw_version=fw_version,
        lowest_supported_version=lowest_supported_version,
        capsule_flags=capsule_flags,
        last_attempt_version=last_attempt_version,
        last_attempt_status=last_attempt_status,
    )
    return rtnval


# ---------------------------------------------------------------------------
# SMBIOS
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SmbiosStructure:
    """
        Single SMBIOS structure per DMTF SMBIOS reference.

        :ivar structure_type: The structure type code (0=BIOS, 1=System, ...).
        :ivar length: Length of the formatted area including the 4-byte
                      structure header.
        :ivar handle: Unique 16-bit handle assigned by the BIOS.
        :ivar formatted: The raw formatted-area bytes.  ``len(formatted) ==
                         length`` (includes the 4-byte header).
        :ivar strings: Decoded string-section entries in table order.  Empty
                       tuple when the structure has no string references.
    """
    structure_type: int
    length: int
    handle: int
    formatted: bytes
    strings: Tuple[str, ...]


@dataclass(frozen=True)
class SmbiosTable:
    """
        Parsed SMBIOS table blob.

        :ivar anchor: The entry-point anchor (``b"_SM_"`` for SMBIOS 2.x,
                      ``b"_SM3_"`` for SMBIOS 3.x).
        :ivar version_major: Major version per entry point.
        :ivar version_minor: Minor version per entry point.
        :ivar table_length: Total table length in bytes.
        :ivar structures: Tuple of parsed :class:`SmbiosStructure`.
    """
    anchor: bytes
    version_major: int
    version_minor: int
    table_length: int
    structures: Tuple[SmbiosStructure, ...]

    @classmethod
    def from_bytes(cls, buf: bytes) -> "SmbiosTable":
        """
            Decode an SMBIOS entry-point plus table blob.

            The expected layout is the 2.x anchor (``_SM_`` at offset 0) or
            the 3.x anchor (``_SM3_`` at offset 0), followed by the
            structure stream.  Short blobs that begin with the structure
            stream itself (no entry point) are not accepted — callers that
            only possess the table body without the entry point should
            fabricate a minimal header.

            :param buf: The raw blob.

            :returns: The decoded :class:`SmbiosTable`.
            :raises: UefiError
        """
        buffer = _require_buffer(field_name="SmbiosTable.from_bytes", buf=buf)
        buffer_length = len(buffer)

        if buffer_length < len(SMBIOS_ANCHOR_2X):
            err_msg_lines = [
                "SmbiosTable.from_bytes: buffer is too short to contain an entry-point anchor.",
                "EXPECTED: at least {} bytes".format(len(SMBIOS_ANCHOR_2X)),
                "OBSERVED: {} bytes".format(buffer_length),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg) from None

        anchor_bytes: bytes
        version_major: int
        version_minor: int
        declared_length: int
        structures_offset: int

        if buffer.startswith(SMBIOS_ANCHOR_3X):
            # SMBIOS 3.x 24-byte entry point
            if buffer_length < 24:
                err_msg_lines = [
                    "SmbiosTable.from_bytes: SMBIOS 3.x entry point is truncated.",
                    "EXPECTED: at least 24 bytes",
                    "OBSERVED: {} bytes".format(buffer_length),
                ]
                err_msg = os.linesep.join(err_msg_lines)
                raise UefiError(err_msg) from None

            anchor_bytes = SMBIOS_ANCHOR_3X
            version_major = _unpack_u8(buffer=buffer, offset=0x07)
            version_minor = _unpack_u8(buffer=buffer, offset=0x08)
            declared_length = _unpack_u32_le(buffer=buffer, offset=0x0C)
            structures_offset = 24
        elif buffer.startswith(SMBIOS_ANCHOR_2X):
            # SMBIOS 2.x 31-byte entry point
            if buffer_length < 31:
                err_msg_lines = [
                    "SmbiosTable.from_bytes: SMBIOS 2.x entry point is truncated.",
                    "EXPECTED: at least 31 bytes",
                    "OBSERVED: {} bytes".format(buffer_length),
                ]
                err_msg = os.linesep.join(err_msg_lines)
                raise UefiError(err_msg) from None

            anchor_bytes = SMBIOS_ANCHOR_2X
            version_major = _unpack_u8(buffer=buffer, offset=0x06)
            version_minor = _unpack_u8(buffer=buffer, offset=0x07)
            declared_length = _unpack_u16_le(buffer=buffer, offset=0x16)
            structures_offset = 31
        else:
            err_msg_lines = [
                "SmbiosTable.from_bytes: unrecognized SMBIOS entry-point anchor.",
                "EXPECTED: {!r} or {!r}".format(SMBIOS_ANCHOR_2X, SMBIOS_ANCHOR_3X),
                "OBSERVED: {!r}".format(buffer[:max(len(SMBIOS_ANCHOR_3X), len(SMBIOS_ANCHOR_2X))]),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg) from None

        stream_end = structures_offset + declared_length
        if buffer_length < stream_end:
            err_msg_lines = [
                "SmbiosTable.from_bytes: structure stream is shorter than declared length.",
                "EXPECTED: at least {} bytes".format(stream_end),
                "OBSERVED: {} bytes".format(buffer_length),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg) from None

        stream = buffer[structures_offset:stream_end]
        parsed_structures = _parse_smbios_stream(stream=stream)

        rtnval = cls(
            anchor=anchor_bytes,
            version_major=version_major,
            version_minor=version_minor,
            table_length=declared_length,
            structures=tuple(parsed_structures),
        )
        return rtnval

    def find_by_type(self, structure_type: int) -> Tuple[SmbiosStructure, ...]:
        """
            Return all :class:`SmbiosStructure` values whose
            ``structure_type`` matches ``structure_type``.

            :param structure_type: The SMBIOS type code to filter by.

            :returns: A tuple of matching structures in table order.
        """
        matches: List[SmbiosStructure] = []
        for structure in self.structures:
            if structure.structure_type == structure_type:
                matches.append(structure)

        rtnval = tuple(matches)
        return rtnval


def _parse_smbios_stream(stream: bytes) -> List[SmbiosStructure]:
    """
        Walk the SMBIOS structure stream and yield parsed
        :class:`SmbiosStructure` values.

        :param stream: The structure stream bytes (entry point excluded).

        :returns: A list of parsed structures in table order.
        :raises: UefiError
    """
    parsed: List[SmbiosStructure] = []
    stream_length = len(stream)
    cursor = 0

    while cursor < stream_length:
        remaining = stream_length - cursor
        if remaining < SMBIOS_STRUCTURE_HEADER_SIZE:
            err_msg_lines = [
                "SmbiosTable.from_bytes: structure header is truncated.",
                "EXPECTED: at least {} bytes".format(SMBIOS_STRUCTURE_HEADER_SIZE),
                "OBSERVED: {} bytes remaining".format(remaining),
                "CURSOR: 0x{:X}".format(cursor),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg) from None

        structure_type = _unpack_u8(buffer=stream, offset=cursor + 0x00)
        structure_length = _unpack_u8(buffer=stream, offset=cursor + 0x01)
        structure_handle = _unpack_u16_le(buffer=stream, offset=cursor + 0x02)

        if structure_length < SMBIOS_STRUCTURE_HEADER_SIZE:
            err_msg_lines = [
                "SmbiosTable.from_bytes: structure length is smaller than the header.",
                "EXPECTED: >= {}".format(SMBIOS_STRUCTURE_HEADER_SIZE),
                "OBSERVED: {}".format(structure_length),
                "CURSOR: 0x{:X}".format(cursor),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg) from None

        if cursor + structure_length > stream_length:
            err_msg_lines = [
                "SmbiosTable.from_bytes: formatted area overruns the stream.",
                "STRUCTURE_TYPE: {}".format(structure_type),
                "STRUCTURE_LENGTH: {}".format(structure_length),
                "CURSOR: 0x{:X}".format(cursor),
                "STREAM_LENGTH: {}".format(stream_length),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg) from None

        formatted_area = bytes(stream[cursor:cursor + structure_length])

        string_section_start = cursor + structure_length
        string_cursor = string_section_start
        # Walk until we encounter a double-NUL terminator (NUL-NUL) or the
        # special "no strings" pattern (single NUL-NUL at offset).
        terminator_offset: Optional[int] = None
        while string_cursor < stream_length - 1:
            if stream[string_cursor] == 0x00 and stream[string_cursor + 1] == 0x00:
                terminator_offset = string_cursor
                break
            string_cursor += 1

        if terminator_offset is None:
            # Per SMBIOS spec a structure with no strings is still followed
            # by two NUL bytes.  A missing terminator is a parser error.
            err_msg_lines = [
                "SmbiosTable.from_bytes: string section missing double-NUL terminator.",
                "STRUCTURE_TYPE: {}".format(structure_type),
                "STRUCTURE_HANDLE: 0x{:04X}".format(structure_handle),
                "STRING_SECTION_START: 0x{:X}".format(string_section_start),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg) from None

        # Decode the strings.  An empty section (immediate NUL-NUL) carries
        # zero strings.
        strings_blob = stream[string_section_start:terminator_offset]
        parsed_strings = _decode_smbios_strings(blob=strings_blob)

        parsed_entry = SmbiosStructure(
            structure_type=structure_type,
            length=structure_length,
            handle=structure_handle,
            formatted=formatted_area,
            strings=tuple(parsed_strings),
        )
        parsed.append(parsed_entry)

        # Skip past the two terminator bytes.
        cursor = terminator_offset + 2

        # End-of-table marker: type 127 (0x7F) with no further structures.
        if structure_type == 0x7F:
            break

    return parsed


def _decode_smbios_strings(blob: bytes) -> List[str]:
    """
        Decode the NUL-delimited string section of an SMBIOS structure.

        Empty blob means zero strings.  SMBIOS does not mandate UTF-8;
        latin-1 is used so every byte round-trips losslessly.

        :param blob: The bytes between the formatted area and the
                     double-NUL terminator.

        :returns: A list of decoded strings in 1-based SMBIOS string
                  reference order.
    """
    if len(blob) == 0:
        rtnval: List[str] = []
        return rtnval

    rtnval = []
    parts = blob.split(b"\x00")
    for part in parts:
        if len(part) == 0:
            # Skip a trailing empty field that results from a section
            # ending exactly on a NUL (prior to the double-NUL).
            continue
        rtnval.append(part.decode("latin-1"))

    return rtnval


# ---------------------------------------------------------------------------
# ACPI RSDP
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AcpiRsdp:
    """
        Parsed ACPI Root System Description Pointer per ACPI 6.x §5.2.5.

        For revision 0 (ACPI 1.0) only the v1 header is present and the
        extended fields are ``None``.  For revision 2+ (ACPI 2.0 onward)
        the v2 extended fields are populated.

        :ivar signature: The 8-byte signature ``b"RSD PTR "``.
        :ivar checksum: The v1 checksum byte.
        :ivar oem_id: The 6-character OEM identifier.
        :ivar revision: Revision code (0 for 1.0, 2+ for 2.0+).
        :ivar rsdt_address: 32-bit physical address of the RSDT.
        :ivar length: Total RSDP length in bytes (revision >= 2).
        :ivar xsdt_address: 64-bit physical address of the XSDT
                            (revision >= 2).
        :ivar extended_checksum: Extended checksum byte (revision >= 2).
    """
    signature: bytes
    checksum: int
    oem_id: str
    revision: int
    rsdt_address: int
    length: Optional[int] = None
    xsdt_address: Optional[int] = None
    extended_checksum: Optional[int] = None

    @classmethod
    def from_bytes(cls, buf: bytes) -> "AcpiRsdp":
        """
            Decode an RSDP buffer.

            :param buf: At least 20 bytes for ACPI 1.0 or 36 bytes for
                        ACPI 2.0+.

            :returns: A populated :class:`AcpiRsdp`.
            :raises: UefiError
        """
        buffer = _require_buffer(field_name="AcpiRsdp.from_bytes", buf=buf)
        buffer_length = len(buffer)

        if buffer_length < ACPI_RSDP_V1_SIZE:
            err_msg_lines = [
                "AcpiRsdp.from_bytes: buffer is shorter than the v1 RSDP.",
                "EXPECTED: at least {} bytes".format(ACPI_RSDP_V1_SIZE),
                "OBSERVED: {} bytes".format(buffer_length),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg) from None

        signature = bytes(buffer[0:8])
        if signature != ACPI_RSDP_SIGNATURE:
            err_msg_lines = [
                "AcpiRsdp.from_bytes: signature mismatch.",
                "EXPECTED: {!r} (hex={})".format(
                    ACPI_RSDP_SIGNATURE, ACPI_RSDP_SIGNATURE.hex(),
                ),
                "OBSERVED: {!r} (hex={})".format(signature, signature.hex()),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg) from None

        checksum = _unpack_u8(buffer=buffer, offset=0x08)

        oem_id_slice = bytes(buffer[0x09:0x0F])
        try:
            oem_id = oem_id_slice.decode("latin-1")
        except UnicodeDecodeError as derr:
            err_msg_lines = [
                "AcpiRsdp.from_bytes: OEMID is not decodable.",
                "OBSERVED: {!r}".format(oem_id_slice),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg) from derr

        revision = _unpack_u8(buffer=buffer, offset=0x0F)
        rsdt_address = _unpack_u32_le(buffer=buffer, offset=0x10)

        length: Optional[int] = None
        xsdt_address: Optional[int] = None
        extended_checksum: Optional[int] = None

        if revision >= 2:
            if buffer_length < ACPI_RSDP_V2_SIZE:
                err_msg_lines = [
                    "AcpiRsdp.from_bytes: revision >= 2 buffer is shorter than the v2 RSDP.",
                    "REVISION: {}".format(revision),
                    "EXPECTED: at least {} bytes".format(ACPI_RSDP_V2_SIZE),
                    "OBSERVED: {} bytes".format(buffer_length),
                ]
                err_msg = os.linesep.join(err_msg_lines)
                raise UefiError(err_msg) from None

            length = _unpack_u32_le(buffer=buffer, offset=0x14)
            xsdt_address = _unpack_u64_le(buffer=buffer, offset=0x18)
            extended_checksum = _unpack_u8(buffer=buffer, offset=0x20)

        rtnval = cls(
            signature=signature,
            checksum=checksum,
            oem_id=oem_id,
            revision=revision,
            rsdt_address=rsdt_address,
            length=length,
            xsdt_address=xsdt_address,
            extended_checksum=extended_checksum,
        )
        return rtnval


# ---------------------------------------------------------------------------
# EFI_MEMORY_ATTRIBUTES_TABLE (UEFI §4.6.4)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MemoryAttributesEntry:
    """
        Single ``EFI_MEMORY_DESCRIPTOR`` entry of the memory attributes
        table per UEFI §7.2.

        :ivar memory_type: The UEFI memory type code.
        :ivar physical_start: The physical start address of the region.
        :ivar virtual_start: The virtual start address of the region.
        :ivar number_of_pages: Number of 4 KiB pages in the region.
        :ivar attribute: Bitfield of UEFI memory attributes
                         (``EFI_MEMORY_XP`` = ``0x2000``,
                         ``EFI_MEMORY_RO`` = ``0x20000``, etc.).
    """
    memory_type: int
    physical_start: int
    virtual_start: int
    number_of_pages: int
    attribute: int


@dataclass(frozen=True)
class MemoryAttributesTable:
    """
        Parsed ``EFI_MEMORY_ATTRIBUTES_TABLE`` per UEFI §4.6.4.

        :ivar version: Table version (must be 1).
        :ivar number_of_entries: Declared entry count.
        :ivar descriptor_size: Size in bytes of each descriptor record
                               (never smaller than
                               :data:`MAT_DESCRIPTOR_SIZE_MIN`).
        :ivar entries: Tuple of parsed :class:`MemoryAttributesEntry`.
    """
    version: int
    number_of_entries: int
    descriptor_size: int
    entries: Tuple[MemoryAttributesEntry, ...]

    @classmethod
    def from_bytes(cls, buf: bytes) -> "MemoryAttributesTable":
        """
            Decode a memory attributes table buffer.

            :param buf: The raw buffer starting at the 16-byte table header.

            :returns: A populated :class:`MemoryAttributesTable`.
            :raises: UefiError
        """
        buffer = _require_buffer(field_name="MemoryAttributesTable.from_bytes", buf=buf)
        buffer_length = len(buffer)

        if buffer_length < MAT_HEADER_SIZE:
            err_msg_lines = [
                "MemoryAttributesTable.from_bytes: buffer is shorter than the MAT header.",
                "EXPECTED: at least {} bytes".format(MAT_HEADER_SIZE),
                "OBSERVED: {} bytes".format(buffer_length),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg) from None

        version = _unpack_u32_le(buffer=buffer, offset=0x00)
        if version != MAT_EXPECTED_VERSION:
            err_msg_lines = [
                "MemoryAttributesTable.from_bytes: unsupported Version.",
                "EXPECTED: 0x{:X}".format(MAT_EXPECTED_VERSION),
                "OBSERVED: 0x{:X}".format(version),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg) from None

        number_of_entries = _unpack_u32_le(buffer=buffer, offset=0x04)
        descriptor_size = _unpack_u32_le(buffer=buffer, offset=0x08)

        if descriptor_size < MAT_DESCRIPTOR_SIZE_MIN:
            err_msg_lines = [
                "MemoryAttributesTable.from_bytes: DescriptorSize below EFI_MEMORY_DESCRIPTOR minimum.",
                "EXPECTED: at least {}".format(MAT_DESCRIPTOR_SIZE_MIN),
                "OBSERVED: {}".format(descriptor_size),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg) from None

        required_length = MAT_HEADER_SIZE + number_of_entries * descriptor_size
        if buffer_length < required_length:
            err_msg_lines = [
                "MemoryAttributesTable.from_bytes: buffer shorter than declared descriptors.",
                "EXPECTED: at least {} bytes".format(required_length),
                "OBSERVED: {} bytes".format(buffer_length),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg) from None

        parsed_entries: List[MemoryAttributesEntry] = []
        for index in range(number_of_entries):
            entry_offset = MAT_HEADER_SIZE + index * descriptor_size
            entry = _parse_memory_descriptor(
                buffer=buffer, offset=entry_offset,
            )
            parsed_entries.append(entry)

        rtnval = cls(
            version=version,
            number_of_entries=number_of_entries,
            descriptor_size=descriptor_size,
            entries=tuple(parsed_entries),
        )
        return rtnval


def _parse_memory_descriptor(buffer: bytes, offset: int) -> MemoryAttributesEntry:
    """
        Decode a single 40-byte ``EFI_MEMORY_DESCRIPTOR`` at ``offset``.

        :param buffer: The full MAT buffer.
        :param offset: Absolute byte offset of the descriptor within
                       ``buffer``.

        :returns: The decoded :class:`MemoryAttributesEntry`.
        :raises: UefiError
    """
    memory_type = _unpack_u32_le(buffer=buffer, offset=offset + 0x00)
    # The 4-byte pad at offset+0x04 is spec-reserved and not surfaced.
    physical_start = _unpack_u64_le(buffer=buffer, offset=offset + 0x08)
    virtual_start = _unpack_u64_le(buffer=buffer, offset=offset + 0x10)
    number_of_pages = _unpack_u64_le(buffer=buffer, offset=offset + 0x18)
    attribute = _unpack_u64_le(buffer=buffer, offset=offset + 0x20)

    rtnval = MemoryAttributesEntry(
        memory_type=memory_type,
        physical_start=physical_start,
        virtual_start=virtual_start,
        number_of_pages=number_of_pages,
        attribute=attribute,
    )
    return rtnval


# ---------------------------------------------------------------------------
# SystemTables facade
# ---------------------------------------------------------------------------


class SystemTables:
    """
        Transport-dispatched facade that fetches and parses UEFI System
        Table adjacent tables for a given :class:`UefiAgent`.  Each
        ``read_*`` method resolves the effective aspects via
        :func:`resolve_aspects`, issues the transport-appropriate command
        through ``agent.run_cmd``, and feeds the raw bytes into the
        matching parser's :meth:`from_bytes` classmethod.

        The mapping from ``agent.transport_kind`` to source is:

        * ``serial`` / ``ipmi-sol`` — UEFI Shell commands ``smbiosview``,
          ``memmap``, and ``dmpstore SystemResourceTable``.
        * ``ssh-host`` — Linux ``/sys/firmware/efi/`` and
          ``/sys/firmware/dmi/tables/`` reads via ``xxd -p -c0``.
        * ``redfish`` — REST reads against
          ``/redfish/v1/UpdateService/FirmwareInventory`` and
          ``/redfish/v1/Systems/{id}``.

        :param agent: The :class:`UefiAgent` bound to the target.
        :param aspects: Instance-default :class:`UefiCmdAspects`.  Defaults
                        to :data:`DEFAULT_CMD_ASPECTS`.
    """

    def __init__(self, agent: Any,
                       aspects: UefiCmdAspects = DEFAULT_CMD_ASPECTS) -> None:
        """
            Initialize the :class:`SystemTables` facade.

            :param agent: The :class:`UefiAgent`-like object bound to the
                          target.  Must expose a ``transport_kind``
                          attribute and a ``run_cmd`` method matching the
                          :class:`UefiAgent` signature.
            :param aspects: The instance default :class:`UefiCmdAspects`.
        """
        self._agent = agent
        self._aspects = aspects
        self._transport_kind: str = getattr(agent, "transport_kind", "")
        return

    # ------------------------------------------------------------------
    # Public read methods
    # ------------------------------------------------------------------

    def read_esrt(self, aspects: Optional[UefiCmdAspects] = None) -> EsrtTable:
        """
            Read the EFI System Resource Table.

            * ``ssh-host`` — assembles a synthetic ESRT blob from the
              per-entry files under ``/sys/firmware/efi/esrt/entries/``.
            * ``serial`` / ``ipmi-sol`` — issues ``dmpstore`` for the
              ``EFI_SYSTEM_RESOURCE_TABLE`` variable and decodes its hex
              data.
            * ``redfish`` — not supported in this facade (callers should
              use the dedicated Redfish firmware-inventory parser).

            :param aspects: Optional per-call :class:`UefiCmdAspects`.

            :returns: A parsed :class:`EsrtTable`.
            :raises: UefiError
        """
        effective = resolve_aspects(passed=aspects, instance_default=self._aspects)

        if self._transport_kind == "ssh-host":
            raw = self._read_esrt_ssh_host(aspects=effective)
        elif self._transport_kind in ("serial", "ipmi-sol"):
            raw = self._read_esrt_shell(aspects=effective)
        elif self._transport_kind == "redfish":
            err_msg = self._unsupported_message(operation="read_esrt")
            raise UefiError(err_msg) from None
        else:
            err_msg = self._unknown_transport_message(operation="read_esrt")
            raise UefiError(err_msg) from None

        rtnval = EsrtTable.from_bytes(buf=raw)
        return rtnval

    def read_smbios(self, aspects: Optional[UefiCmdAspects] = None) -> SmbiosTable:
        """
            Read the SMBIOS entry point and table blob.

            * ``ssh-host`` — concatenates
              ``/sys/firmware/dmi/tables/smbios_entry_point`` with
              ``/sys/firmware/dmi/tables/DMI``.
            * ``serial`` / ``ipmi-sol`` — issues ``smbiosview -b`` and
              decodes the binary dump.
            * ``redfish`` — unsupported; Redfish cannot deliver the raw
              structure stream.

            :param aspects: Optional per-call :class:`UefiCmdAspects`.

            :returns: A parsed :class:`SmbiosTable`.
            :raises: UefiError
        """
        effective = resolve_aspects(passed=aspects, instance_default=self._aspects)

        if self._transport_kind == "ssh-host":
            raw = self._read_smbios_ssh_host(aspects=effective)
        elif self._transport_kind in ("serial", "ipmi-sol"):
            raw = self._read_smbios_shell(aspects=effective)
        elif self._transport_kind == "redfish":
            err_msg = self._unsupported_message(operation="read_smbios")
            raise UefiError(err_msg) from None
        else:
            err_msg = self._unknown_transport_message(operation="read_smbios")
            raise UefiError(err_msg) from None

        rtnval = SmbiosTable.from_bytes(buf=raw)
        return rtnval

    def read_acpi_rsdp(self, aspects: Optional[UefiCmdAspects] = None) -> AcpiRsdp:
        """
            Read the ACPI RSDP.

            * ``ssh-host`` — reads ``/sys/firmware/acpi/tables/RSDP``.
            * ``serial`` / ``ipmi-sol`` / ``redfish`` — not implemented.

            :param aspects: Optional per-call :class:`UefiCmdAspects`.

            :returns: A parsed :class:`AcpiRsdp`.
            :raises: UefiError
        """
        effective = resolve_aspects(passed=aspects, instance_default=self._aspects)

        if self._transport_kind == "ssh-host":
            raw = self._read_acpi_rsdp_ssh_host(aspects=effective)
        elif self._transport_kind in ("serial", "ipmi-sol", "redfish"):
            err_msg = self._unsupported_message(operation="read_acpi_rsdp")
            raise UefiError(err_msg) from None
        else:
            err_msg = self._unknown_transport_message(operation="read_acpi_rsdp")
            raise UefiError(err_msg) from None

        rtnval = AcpiRsdp.from_bytes(buf=raw)
        return rtnval

    def read_memory_attributes_table(self,
                                     aspects: Optional[UefiCmdAspects] = None) -> MemoryAttributesTable:
        """
            Read the ``EFI_MEMORY_ATTRIBUTES_TABLE``.

            * ``ssh-host`` — reads
              ``/sys/firmware/efi/tables/MemoryAttributesTable`` when
              exported, otherwise raises :class:`UefiError`.
            * ``serial`` / ``ipmi-sol`` — not implemented in this facade
              (the Shell ``memmap`` output is not structurally identical
              to the §4.6.4 layout and requires a separate decoder).
            * ``redfish`` — not supported.

            :param aspects: Optional per-call :class:`UefiCmdAspects`.

            :returns: A parsed :class:`MemoryAttributesTable`.
            :raises: UefiError
        """
        effective = resolve_aspects(passed=aspects, instance_default=self._aspects)

        if self._transport_kind == "ssh-host":
            raw = self._read_mat_ssh_host(aspects=effective)
        elif self._transport_kind in ("serial", "ipmi-sol", "redfish"):
            err_msg = self._unsupported_message(operation="read_memory_attributes_table")
            raise UefiError(err_msg) from None
        else:
            err_msg = self._unknown_transport_message(operation="read_memory_attributes_table")
            raise UefiError(err_msg) from None

        rtnval = MemoryAttributesTable.from_bytes(buf=raw)
        return rtnval

    # ------------------------------------------------------------------
    # SSH-to-host transport backends
    # ------------------------------------------------------------------

    def _read_esrt_ssh_host(self, aspects: UefiCmdAspects) -> bytes:
        """
            Fetch and repack the ESRT from ``/sys/firmware/efi/esrt/``.

            The Linux esrt sysfs tree exports one directory per resource
            entry.  This helper fetches a canonical hex-encoded serialized
            blob via ``xxd``-style commands.

            :param aspects: The effective aspects for the call.

            :returns: The raw ESRT bytes.
            :raises: UefiError
        """
        # Fetch the packed ESRT blob if the target exposes one, falling
        # back to assembling it from the per-entry files is out of scope
        # for the facade; callers needing sysfs reassembly go through the
        # dedicated loader.
        path = "/sys/firmware/efi/esrt/table"
        rtnval = self._fetch_hex_file(path=path, aspects=aspects,
                                      operation="read_esrt")
        return rtnval

    def _read_smbios_ssh_host(self, aspects: UefiCmdAspects) -> bytes:
        """
            Fetch the SMBIOS entry point + DMI blob via
            ``/sys/firmware/dmi/tables/``.

            :param aspects: The effective aspects for the call.

            :returns: The raw bytes (entry point followed by the structure
                      stream).
            :raises: UefiError
        """
        ep_path = "/sys/firmware/dmi/tables/smbios_entry_point"
        dmi_path = "/sys/firmware/dmi/tables/DMI"

        entry_point = self._fetch_hex_file(
            path=ep_path, aspects=aspects, operation="read_smbios",
        )
        dmi_blob = self._fetch_hex_file(
            path=dmi_path, aspects=aspects, operation="read_smbios",
        )

        rtnval = entry_point + dmi_blob
        return rtnval

    def _read_acpi_rsdp_ssh_host(self, aspects: UefiCmdAspects) -> bytes:
        """
            Fetch the ACPI RSDP from ``/sys/firmware/acpi/tables/RSDP``.

            :param aspects: The effective aspects for the call.

            :returns: The raw RSDP bytes.
            :raises: UefiError
        """
        path = "/sys/firmware/acpi/tables/RSDP"
        rtnval = self._fetch_hex_file(path=path, aspects=aspects,
                                      operation="read_acpi_rsdp")
        return rtnval

    def _read_mat_ssh_host(self, aspects: UefiCmdAspects) -> bytes:
        """
            Fetch the Memory Attributes Table from a pre-exported sysfs
            path.  Most distributions do not export this table; callers
            that need it typically rely on a pre-boot capture.  The path
            used here is the conventional location; a missing file surfaces
            as :class:`UefiError`.

            :param aspects: The effective aspects for the call.

            :returns: The raw MAT bytes.
            :raises: UefiError
        """
        path = "/sys/firmware/efi/tables/MemoryAttributesTable"
        rtnval = self._fetch_hex_file(
            path=path, aspects=aspects,
            operation="read_memory_attributes_table",
        )
        return rtnval

    def _fetch_hex_file(self, path: str, aspects: UefiCmdAspects,
                              operation: str) -> bytes:
        """
            Issue ``xxd -p -c0 <path>`` through the bound agent and decode
            the hex output into raw bytes.

            :param path: The absolute file path on the target.
            :param aspects: The effective aspects for the call.
            :param operation: The public operation name (used in errors).

            :returns: The decoded bytes.
            :raises: UefiError
        """
        command = "xxd -p -c0 {}".format(path)
        status, stdout, stderr = self._agent.run_cmd(
            command=command, aspects=aspects,
        )

        if status != 0:
            err_msg_lines = [
                "SystemTables.{}: host read failed.".format(operation),
                "COMMAND: {}".format(command),
                "PATH: {}".format(path),
                "STATUS: {}".format(status),
            ]
            if stderr is not None and len(stderr) > 0:
                err_msg_lines.append("STDERR: {}".format(stderr.strip()))
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg) from None

        hex_text = (stdout if stdout is not None else "")
        # Collapse any whitespace that a wrapping xxd variant may emit.
        hex_text = re.sub(r"\s+", "", hex_text)

        if len(hex_text) == 0:
            err_msg_lines = [
                "SystemTables.{}: host read returned an empty buffer.".format(operation),
                "PATH: {}".format(path),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg) from None

        try:
            rtnval = bytes.fromhex(hex_text)
        except ValueError as verr:
            err_msg_lines = [
                "SystemTables.{}: host read did not yield valid hex output.".format(operation),
                "PATH: {}".format(path),
                "OBSERVED_PREFIX: {!r}".format(hex_text[:64]),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg) from verr

        return rtnval

    # ------------------------------------------------------------------
    # UEFI Shell transport backends
    # ------------------------------------------------------------------

    def _read_esrt_shell(self, aspects: UefiCmdAspects) -> bytes:
        """
            Fetch an ESRT blob through the UEFI Shell.

            Uses ``dmpstore -guid <EFI_SYSTEM_RESOURCE_TABLE_GUID>`` to
            retrieve the serialized table.  Shell drivers sometimes deliver
            the table via ``smbiosview`` extensions; callers that require
            that fallback should parse Shell output themselves and feed the
            bytes to :meth:`EsrtTable.from_bytes`.

            :param aspects: The effective aspects for the call.

            :returns: The raw ESRT bytes.
            :raises: UefiError
        """
        command = "dmpstore -guid b122a263-3661-4f68-9929-78f8b0d62180"
        status, stdout, stderr = self._agent.run_cmd(
            command=command, aspects=aspects,
        )

        if status != 0:
            err_msg_lines = [
                "SystemTables.read_esrt: Shell dmpstore returned non-zero status.",
                "COMMAND: {}".format(command),
                "STATUS: {}".format(status),
            ]
            if stderr is not None and len(stderr) > 0:
                err_msg_lines.append("STDERR: {}".format(stderr.strip()))
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg) from None

        rtnval = _parse_dmpstore_hex_bytes(
            stdout=stdout if stdout is not None else "",
            operation="read_esrt",
        )
        return rtnval

    def _read_smbios_shell(self, aspects: UefiCmdAspects) -> bytes:
        """
            Fetch an SMBIOS blob through the UEFI Shell's ``smbiosview -b``
            dump and decode the canonical hex-dump output.

            :param aspects: The effective aspects for the call.

            :returns: The raw SMBIOS bytes (entry point + stream).
            :raises: UefiError
        """
        command = "smbiosview -b"
        status, stdout, stderr = self._agent.run_cmd(
            command=command, aspects=aspects,
        )

        if status != 0:
            err_msg_lines = [
                "SystemTables.read_smbios: Shell smbiosview returned non-zero status.",
                "COMMAND: {}".format(command),
                "STATUS: {}".format(status),
            ]
            if stderr is not None and len(stderr) > 0:
                err_msg_lines.append("STDERR: {}".format(stderr.strip()))
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg) from None

        rtnval = _parse_dmpstore_hex_bytes(
            stdout=stdout if stdout is not None else "",
            operation="read_smbios",
        )
        return rtnval

    # ------------------------------------------------------------------
    # Error message helpers
    # ------------------------------------------------------------------

    def _unsupported_message(self, operation: str) -> str:
        """
            Build a "not supported on <transport_kind>" message suitable
            for :class:`UefiError`.

            :param operation: The public operation name.

            :returns: A joined multi-line error string.
        """
        err_msg_lines = [
            "SystemTables.{}: operation not supported on the bound transport.".format(operation),
            "TRANSPORT_KIND: {!r}".format(self._transport_kind),
            "HINT: choose a transport that exposes this table.",
        ]
        rtnval = os.linesep.join(err_msg_lines)
        return rtnval

    def _unknown_transport_message(self, operation: str) -> str:
        """
            Build an "unknown transport_kind" message for operations
            reached via an unrecognized ``transport_kind`` value.

            :param operation: The public operation name.

            :returns: A joined multi-line error string.
        """
        err_msg_lines = [
            "SystemTables.{}: unknown transport_kind on the bound agent.".format(operation),
            "OBSERVED: transport_kind={!r}".format(self._transport_kind),
            "ALLOWED: serial, ipmi-sol, ssh-host, redfish",
        ]
        rtnval = os.linesep.join(err_msg_lines)
        return rtnval


# ---------------------------------------------------------------------------
# Module-internal buffer helpers
# ---------------------------------------------------------------------------


def _require_buffer(field_name: str, buf: Any) -> bytes:
    """
        Validate that ``buf`` is a bytes-like object and coerce to
        :class:`bytes`.

        :param field_name: Calling site name for diagnostics.
        :param buf: The candidate value.

        :returns: The coerced buffer.
        :raises: UefiError
    """
    if not isinstance(buf, (bytes, bytearray, memoryview)):
        err_msg_lines = [
            "{}: 'buf' must be a bytes-like object.".format(field_name),
            "GOT: value={!r} type={}".format(buf, type(buf).__name__),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiError(err_msg) from None

    rtnval = bytes(buf)
    return rtnval


def _unpack_u8(buffer: bytes, offset: int) -> int:
    """
        Unpack one unsigned byte at ``offset``.

        :param buffer: The full buffer.
        :param offset: Zero-based byte offset.

        :returns: The decoded value.
        :raises: UefiError
    """
    if offset + 1 > len(buffer):
        err_msg_lines = [
            "_unpack_u8: buffer too short.",
            "OFFSET: {}".format(offset),
            "LENGTH: {}".format(len(buffer)),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiError(err_msg) from None

    value_tuple = struct.unpack_from("B", buffer, offset)
    rtnval = value_tuple[0]
    return rtnval


def _unpack_u16_le(buffer: bytes, offset: int) -> int:
    """
        Unpack an unsigned 16-bit little-endian value at ``offset``.

        :param buffer: The full buffer.
        :param offset: Zero-based byte offset.

        :returns: The decoded value.
        :raises: UefiError
    """
    if offset + 2 > len(buffer):
        err_msg_lines = [
            "_unpack_u16_le: buffer too short.",
            "OFFSET: {}".format(offset),
            "LENGTH: {}".format(len(buffer)),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiError(err_msg) from None

    value_tuple = struct.unpack_from("<H", buffer, offset)
    rtnval = value_tuple[0]
    return rtnval


def _unpack_u32_le(buffer: bytes, offset: int) -> int:
    """
        Unpack an unsigned 32-bit little-endian value at ``offset``.

        :param buffer: The full buffer.
        :param offset: Zero-based byte offset.

        :returns: The decoded value.
        :raises: UefiError
    """
    if offset + 4 > len(buffer):
        err_msg_lines = [
            "_unpack_u32_le: buffer too short.",
            "OFFSET: {}".format(offset),
            "LENGTH: {}".format(len(buffer)),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiError(err_msg) from None

    value_tuple = struct.unpack_from("<I", buffer, offset)
    rtnval = value_tuple[0]
    return rtnval


def _unpack_u64_le(buffer: bytes, offset: int) -> int:
    """
        Unpack an unsigned 64-bit little-endian value at ``offset``.

        :param buffer: The full buffer.
        :param offset: Zero-based byte offset.

        :returns: The decoded value.
        :raises: UefiError
    """
    if offset + 8 > len(buffer):
        err_msg_lines = [
            "_unpack_u64_le: buffer too short.",
            "OFFSET: {}".format(offset),
            "LENGTH: {}".format(len(buffer)),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiError(err_msg) from None

    value_tuple = struct.unpack_from("<Q", buffer, offset)
    rtnval = value_tuple[0]
    return rtnval


# ---------------------------------------------------------------------------
# Shell hex-dump parser used by the serial / ipmi-sol paths
# ---------------------------------------------------------------------------

# A canonical Shell-style hex dump line looks like:
#   00000000: 01 00 00 00 02 00 00 00  ........
# where columns are: offset, up to 16 hex bytes (space-separated and with an
# optional gap between the 8th and 9th byte), optional ASCII gutter.  We
# tolerate both the 16-byte-per-row default and narrower/wider rows.
_HEX_DUMP_LINE_RE = re.compile(
    r"^\s*[0-9A-Fa-f]+\s*[:|]?\s*(?P<hex>(?:[0-9A-Fa-f]{2}(?:[\s-]+|$))+)"
)


def _parse_dmpstore_hex_bytes(stdout: str, operation: str) -> bytes:
    """
        Parse a Shell ``dmpstore`` or ``smbiosview -b`` hex-dump into raw
        bytes.

        The parser accepts canonical ``OFFSET: HH HH HH ...  ASCII`` lines
        and silently drops any line that does not match the pattern
        (banner text, variable headers, separators, etc.).

        :param stdout: The captured stdout string.
        :param operation: The caller operation name used in error text.

        :returns: The concatenated decoded bytes.
        :raises: UefiError
    """
    if not isinstance(stdout, str):
        err_msg_lines = [
            "_parse_dmpstore_hex_bytes: 'stdout' must be a str.",
            "GOT: type={}".format(type(stdout).__name__),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiError(err_msg) from None

    accumulator = bytearray()
    for raw_line in stdout.splitlines():
        match = _HEX_DUMP_LINE_RE.match(raw_line)
        if match is None:
            continue

        hex_fragment = match.group("hex")
        # Strip trailing ASCII gutter if the line appended one (the gutter
        # is separated by at least two spaces from the hex column by
        # convention).
        hex_clean = re.sub(r"[^0-9A-Fa-f]", "", hex_fragment)
        if (len(hex_clean) % 2) != 0:
            err_msg_lines = [
                "_parse_dmpstore_hex_bytes: odd-length hex fragment.",
                "OPERATION: {}".format(operation),
                "FRAGMENT: {!r}".format(hex_fragment),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg) from None

        try:
            chunk = bytes.fromhex(hex_clean)
        except ValueError as verr:
            err_msg_lines = [
                "_parse_dmpstore_hex_bytes: invalid hex fragment.",
                "OPERATION: {}".format(operation),
                "FRAGMENT: {!r}".format(hex_fragment),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg) from verr

        accumulator.extend(chunk)

    if len(accumulator) == 0:
        err_msg_lines = [
            "_parse_dmpstore_hex_bytes: Shell output did not contain any hex-dump lines.",
            "OPERATION: {}".format(operation),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiError(err_msg) from None

    rtnval = bytes(accumulator)
    return rtnval
