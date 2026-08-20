"""
    .. module:: uefiloadedimage
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Pure-Python parser for UEFI PE/COFF images (``.efi``
               executables) per the Microsoft PE/COFF specification and
               UEFI specification section 9.1 (LoadImage format) and
               section 2.1 (EFI image machine types).  The parser is
               strictly read-only: it produces frozen dataclasses that
               describe the DOS stub offset to the PE header, the COFF
               file header, the PE32 / PE32+ optional header, the COFF
               section table, and any ``WIN_CERTIFICATE`` attribute
               certificates embedded in the ``IMAGE_DIRECTORY_ENTRY_SECURITY``
               data directory.  There is no image writer, no re-signer,
               no section injector, and no transport dependency; the
               parser operates entirely on a byte buffer supplied by the
               caller.  Only the Python standard library is used; there
               is no ``pefile`` or ``lief`` dependency.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Code Craft AI LLC"
__credits__ = []


import enum
import os
import struct

from dataclasses import dataclass
from typing import Final, List, Tuple

from ccai.protocols.uefi.uefiexceptions import UefiProtocolError


__all__ = [
    "AttributeCertificate",
    "CoffHeader",
    "DOS_SIGNATURE",
    "DOS_E_LFANEW_OFFSET",
    "OPTIONAL_HEADER_MAGIC_PE32",
    "OPTIONAL_HEADER_MAGIC_PE32_PLUS",
    "OptionalHeaderPe32Plus",
    "PE_SIGNATURE",
    "PeMachine",
    "PeSubsystem",
    "SECTION_HEADER_SIZE",
    "SectionHeader",
    "UefiImage",
    "WIN_CERT_REVISION_2_0",
    "WIN_CERT_TYPE_PKCS_SIGNED_DATA",
]


# ---------------------------------------------------------------------------
# Constants (Microsoft PE/COFF specification, UEFI specification section 9.1)
# ---------------------------------------------------------------------------

#: The 2-byte DOS stub signature ("MZ") that every PE image begins with.
DOS_SIGNATURE: Final[bytes] = b"MZ"

#: Offset within the DOS stub of the ``e_lfanew`` 32-bit field that points
#: at the PE signature.
DOS_E_LFANEW_OFFSET: Final[int] = 0x3C

#: The 4-byte PE signature ("PE\0\0") that introduces the COFF header.
PE_SIGNATURE: Final[bytes] = b"PE\x00\x00"

#: Size in bytes of the COFF ``IMAGE_FILE_HEADER`` structure.
COFF_HEADER_SIZE: Final[int] = 20

#: Size in bytes of a single ``IMAGE_SECTION_HEADER`` record.
SECTION_HEADER_SIZE: Final[int] = 40

#: Size in bytes of a single ``IMAGE_DATA_DIRECTORY`` record.
DATA_DIRECTORY_ENTRY_SIZE: Final[int] = 8

#: Required number of data-directory entries for a well-formed PE32+ image.
DATA_DIRECTORY_COUNT: Final[int] = 16

#: ``Magic`` value in the optional header that identifies a PE32 image.
OPTIONAL_HEADER_MAGIC_PE32: Final[int] = 0x10B

#: ``Magic`` value in the optional header that identifies a PE32+ image.
OPTIONAL_HEADER_MAGIC_PE32_PLUS: Final[int] = 0x20B

#: Index of the ``IMAGE_DIRECTORY_ENTRY_SECURITY`` entry within the data
#: directory table.
SECURITY_DIRECTORY_INDEX: Final[int] = 4

#: ``wRevision`` value for a WIN_CERTIFICATE structure conforming to the
#: 2.0 Authenticode layout.
WIN_CERT_REVISION_2_0: Final[int] = 0x0200

#: ``wCertificateType`` value that identifies a DER-encoded PKCS#7
#: SignedData payload.
WIN_CERT_TYPE_PKCS_SIGNED_DATA: Final[int] = 0x0002


# ---------------------------------------------------------------------------
# Enums (UEFI specification section 2.1)
# ---------------------------------------------------------------------------


class PeMachine(enum.IntEnum):
    """
        Named enum for the COFF ``Machine`` field.  Values correspond to
        the ``IMAGE_FILE_MACHINE_*`` constants enumerated in the Microsoft
        PE/COFF specification and cross-referenced by UEFI specification
        section 2.1 (EFI Image Types).
    """

    I386 = 0x014C
    IA64 = 0x0200
    X64 = 0x8664
    AARCH32 = 0x01C4
    AARCH32_THUMB = 0x01C2
    AARCH64 = 0xAA64
    RISCV32 = 0x5032
    RISCV64 = 0x5064
    RISCV128 = 0x5128
    EBC = 0x0EBC


class PeSubsystem(enum.IntEnum):
    """
        Named enum for the PE ``Subsystem`` field restricted to the UEFI
        image subsystems defined by UEFI specification section 2.1.
    """

    EFI_APPLICATION = 10
    EFI_BOOT_SERVICE_DRIVER = 11
    EFI_RUNTIME_DRIVER = 12
    EFI_ROM = 13


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CoffHeader:
    """
        Parsed representation of the 20-byte COFF ``IMAGE_FILE_HEADER``.

        :param machine: Machine type as a :class:`PeMachine` value.
        :param number_of_sections: Count of entries in the section table.
        :param time_date_stamp: Timestamp value from the COFF header.
        :param pointer_to_symbol_table: File offset of the COFF symbol table.
        :param number_of_symbols: Count of entries in the symbol table.
        :param size_of_optional_header: Byte count of the optional header.
        :param characteristics: Raw characteristics flag bitmask.
    """

    machine: PeMachine
    number_of_sections: int
    time_date_stamp: int
    pointer_to_symbol_table: int
    number_of_symbols: int
    size_of_optional_header: int
    characteristics: int


@dataclass(frozen=True)
class OptionalHeaderPe32Plus:
    """
        Parsed representation of the PE32 / PE32+ optional header.

        :param magic: ``Magic`` field (``0x10B`` PE32, ``0x20B`` PE32+).
        :param address_of_entry_point: RVA of the image entry point.
        :param image_base: Preferred load address.  64-bit for PE32+,
                           32-bit for PE32.
        :param section_alignment: In-memory alignment of each section.
        :param file_alignment: File alignment of each section's raw data.
        :param size_of_image: Total image size in bytes when loaded.
        :param size_of_headers: Combined size of DOS stub, PE signature,
                                COFF header, optional header, and section
                                table.
        :param checksum: Image checksum value.
        :param subsystem: PE subsystem as a :class:`PeSubsystem` value.
        :param dll_characteristics: Raw DLL-characteristics bitmask.
        :param size_of_stack_reserve: Reserved stack size.
        :param size_of_stack_commit: Initially-committed stack size.
        :param size_of_heap_reserve: Reserved heap size.
        :param size_of_heap_commit: Initially-committed heap size.
        :param number_of_rva_and_sizes: Count of data-directory entries.
        :param data_directories: Tuple of ``(rva, size)`` pairs, one per
                                 data-directory entry in file order.
    """

    magic: int
    address_of_entry_point: int
    image_base: int
    section_alignment: int
    file_alignment: int
    size_of_image: int
    size_of_headers: int
    checksum: int
    subsystem: PeSubsystem
    dll_characteristics: int
    size_of_stack_reserve: int
    size_of_stack_commit: int
    size_of_heap_reserve: int
    size_of_heap_commit: int
    number_of_rva_and_sizes: int
    data_directories: Tuple[Tuple[int, int], ...]


@dataclass(frozen=True)
class SectionHeader:
    """
        Parsed representation of a 40-byte COFF ``IMAGE_SECTION_HEADER``.

        :param name: Section name decoded as ASCII with trailing NUL bytes
                     stripped.
        :param virtual_size: In-memory size of the section.
        :param virtual_address: RVA of the section.
        :param size_of_raw_data: Byte count of the section's raw data in
                                 the file.
        :param pointer_to_raw_data: File offset of the section's raw data.
        :param characteristics: Raw characteristics flag bitmask.
    """

    name: str
    virtual_size: int
    virtual_address: int
    size_of_raw_data: int
    pointer_to_raw_data: int
    characteristics: int


@dataclass(frozen=True)
class AttributeCertificate:
    """
        Parsed representation of a ``WIN_CERTIFICATE`` entry from the
        attribute-certificate table referenced by
        ``IMAGE_DIRECTORY_ENTRY_SECURITY``.

        :param length: ``dwLength`` field from the certificate header.
        :param revision: ``wRevision`` field (``0x0200`` indicates the
                         2.0 Authenticode layout).
        :param cert_type: ``wCertificateType`` field (``0x0002`` indicates
                          a DER-encoded PKCS#7 SignedData payload).
        :param raw_signature: The ``bCertificate`` payload bytes.
    """

    length: int
    revision: int
    cert_type: int
    raw_signature: bytes


# ---------------------------------------------------------------------------
# Bounds-checking helpers
# ---------------------------------------------------------------------------


def _require_range(buf: bytes, offset: int, size: int, field: str) -> None:
    """
        Verify that the half-open byte range ``[offset, offset+size)``
        lies entirely within ``buf``; otherwise raise
        :class:`UefiProtocolError` naming the offending field.

        :param buf: The backing byte buffer.
        :param offset: Start of the half-open range.
        :param size: Byte count to be consumed starting at ``offset``.
        :param field: Human-readable name of the field being read, used
                      when constructing the error message.

        :raises UefiProtocolError: When the range would exceed ``buf``.
    """
    buffer_length = len(buf)
    if offset < 0 or size < 0 or offset + size > buffer_length:
        err_msg_lines = ["Attempted to read past the end of the PE image buffer."]
        err_msg_lines.append("FIELD: {}".format(field))
        err_msg_lines.append("OFFSET: {}".format(offset))
        err_msg_lines.append("SIZE: {}".format(size))
        err_msg_lines.append("BUFFER LENGTH: {}".format(buffer_length))

        err_msg = os.linesep.join(err_msg_lines)
        raise UefiProtocolError(err_msg) from None

    return


def _read_u16(buf: bytes, offset: int, field: str) -> int:
    """
        Read a little-endian unsigned 16-bit integer at ``offset``.

        :param buf: The backing byte buffer.
        :param offset: Byte offset within ``buf`` at which to read.
        :param field: Field name used for error reporting.

        :returns: The decoded integer value.
        :raises UefiProtocolError: On out-of-range read.
    """
    _require_range(buf=buf, offset=offset, size=2, field=field)
    (value,) = struct.unpack_from("<H", buf, offset)
    return value


def _read_u32(buf: bytes, offset: int, field: str) -> int:
    """
        Read a little-endian unsigned 32-bit integer at ``offset``.

        :param buf: The backing byte buffer.
        :param offset: Byte offset within ``buf`` at which to read.
        :param field: Field name used for error reporting.

        :returns: The decoded integer value.
        :raises UefiProtocolError: On out-of-range read.
    """
    _require_range(buf=buf, offset=offset, size=4, field=field)
    (value,) = struct.unpack_from("<I", buf, offset)
    return value


def _read_u64(buf: bytes, offset: int, field: str) -> int:
    """
        Read a little-endian unsigned 64-bit integer at ``offset``.

        :param buf: The backing byte buffer.
        :param offset: Byte offset within ``buf`` at which to read.
        :param field: Field name used for error reporting.

        :returns: The decoded integer value.
        :raises UefiProtocolError: On out-of-range read.
    """
    _require_range(buf=buf, offset=offset, size=8, field=field)
    (value,) = struct.unpack_from("<Q", buf, offset)
    return value


# ---------------------------------------------------------------------------
# Header parsers
# ---------------------------------------------------------------------------


def _parse_coff_header(buf: bytes, offset: int) -> CoffHeader:
    """
        Decode the 20-byte COFF file header at ``offset``.

        :param buf: The backing byte buffer.
        :param offset: Byte offset of the COFF header start.

        :returns: A fully-populated :class:`CoffHeader`.
        :raises UefiProtocolError: On bounds violation or on an unknown
                                   machine value.
    """
    _require_range(buf=buf, offset=offset, size=COFF_HEADER_SIZE, field="IMAGE_FILE_HEADER")

    machine_value = _read_u16(buf=buf, offset=offset + 0x00, field="IMAGE_FILE_HEADER.Machine")
    number_of_sections = _read_u16(buf=buf, offset=offset + 0x02, field="IMAGE_FILE_HEADER.NumberOfSections")
    time_date_stamp = _read_u32(buf=buf, offset=offset + 0x04, field="IMAGE_FILE_HEADER.TimeDateStamp")
    pointer_to_symbol_table = _read_u32(buf=buf, offset=offset + 0x08, field="IMAGE_FILE_HEADER.PointerToSymbolTable")
    number_of_symbols = _read_u32(buf=buf, offset=offset + 0x0C, field="IMAGE_FILE_HEADER.NumberOfSymbols")
    size_of_optional_header = _read_u16(buf=buf, offset=offset + 0x10, field="IMAGE_FILE_HEADER.SizeOfOptionalHeader")
    characteristics = _read_u16(buf=buf, offset=offset + 0x12, field="IMAGE_FILE_HEADER.Characteristics")

    try:
        machine = PeMachine(machine_value)
    except ValueError:
        err_msg_lines = ["Unrecognized COFF machine value in the PE image header."]
        err_msg_lines.append("MACHINE: 0x{:04X}".format(machine_value))
        err_msg_lines.append("OFFSET: {}".format(offset))

        err_msg = os.linesep.join(err_msg_lines)
        raise UefiProtocolError(err_msg) from None

    header = CoffHeader(
        machine=machine,
        number_of_sections=number_of_sections,
        time_date_stamp=time_date_stamp,
        pointer_to_symbol_table=pointer_to_symbol_table,
        number_of_symbols=number_of_symbols,
        size_of_optional_header=size_of_optional_header,
        characteristics=characteristics,
    )
    return header


def _parse_optional_header(buf: bytes, offset: int, size_of_optional_header: int) -> Tuple[OptionalHeaderPe32Plus, bool]:
    """
        Decode the PE optional header and the trailing data-directory
        array at ``offset``.  Dispatches on the ``Magic`` field to the
        appropriate PE32 / PE32+ layout.

        :param buf: The backing byte buffer.
        :param offset: Byte offset of the optional header start.
        :param size_of_optional_header: Value from the COFF header,
                                        used to bound the read window.

        :returns: A 2-tuple ``(header, is_pe32_plus)`` where ``header``
                  is a :class:`OptionalHeaderPe32Plus` populated from
                  whichever layout was detected and ``is_pe32_plus`` is
                  ``True`` when the magic is ``0x20B``.
        :raises UefiProtocolError: On bounds violation, unknown magic,
                                   or a ``NumberOfRvaAndSizes`` that
                                   would overflow the header window.
    """
    _require_range(buf=buf, offset=offset, size=size_of_optional_header, field="IMAGE_OPTIONAL_HEADER")

    magic = _read_u16(buf=buf, offset=offset + 0x00, field="IMAGE_OPTIONAL_HEADER.Magic")

    if magic == OPTIONAL_HEADER_MAGIC_PE32_PLUS:
        is_pe32_plus = True
    elif magic == OPTIONAL_HEADER_MAGIC_PE32:
        is_pe32_plus = False
    else:
        err_msg_lines = ["Unrecognized IMAGE_OPTIONAL_HEADER magic value."]
        err_msg_lines.append("MAGIC: 0x{:04X}".format(magic))
        err_msg_lines.append("OFFSET: {}".format(offset))

        err_msg = os.linesep.join(err_msg_lines)
        raise UefiProtocolError(err_msg) from None

    address_of_entry_point = _read_u32(buf=buf, offset=offset + 0x10, field="IMAGE_OPTIONAL_HEADER.AddressOfEntryPoint")

    if is_pe32_plus:
        image_base = _read_u64(buf=buf, offset=offset + 0x18, field="IMAGE_OPTIONAL_HEADER.ImageBase")
        stack_reserve_offset = offset + 0x48
        stack_commit_offset = offset + 0x50
        heap_reserve_offset = offset + 0x58
        heap_commit_offset = offset + 0x60
        rva_count_offset = offset + 0x6C
        data_directories_offset = offset + 0x70
        stack_reserve = _read_u64(buf=buf, offset=stack_reserve_offset, field="IMAGE_OPTIONAL_HEADER.SizeOfStackReserve")
        stack_commit = _read_u64(buf=buf, offset=stack_commit_offset, field="IMAGE_OPTIONAL_HEADER.SizeOfStackCommit")
        heap_reserve = _read_u64(buf=buf, offset=heap_reserve_offset, field="IMAGE_OPTIONAL_HEADER.SizeOfHeapReserve")
        heap_commit = _read_u64(buf=buf, offset=heap_commit_offset, field="IMAGE_OPTIONAL_HEADER.SizeOfHeapCommit")
    else:
        image_base = _read_u32(buf=buf, offset=offset + 0x1C, field="IMAGE_OPTIONAL_HEADER.ImageBase")
        stack_reserve_offset = offset + 0x48
        stack_commit_offset = offset + 0x4C
        heap_reserve_offset = offset + 0x50
        heap_commit_offset = offset + 0x54
        rva_count_offset = offset + 0x5C
        data_directories_offset = offset + 0x60
        stack_reserve = _read_u32(buf=buf, offset=stack_reserve_offset, field="IMAGE_OPTIONAL_HEADER.SizeOfStackReserve")
        stack_commit = _read_u32(buf=buf, offset=stack_commit_offset, field="IMAGE_OPTIONAL_HEADER.SizeOfStackCommit")
        heap_reserve = _read_u32(buf=buf, offset=heap_reserve_offset, field="IMAGE_OPTIONAL_HEADER.SizeOfHeapReserve")
        heap_commit = _read_u32(buf=buf, offset=heap_commit_offset, field="IMAGE_OPTIONAL_HEADER.SizeOfHeapCommit")

    section_alignment = _read_u32(buf=buf, offset=offset + 0x20, field="IMAGE_OPTIONAL_HEADER.SectionAlignment")
    file_alignment = _read_u32(buf=buf, offset=offset + 0x24, field="IMAGE_OPTIONAL_HEADER.FileAlignment")
    size_of_image = _read_u32(buf=buf, offset=offset + 0x38, field="IMAGE_OPTIONAL_HEADER.SizeOfImage")
    size_of_headers = _read_u32(buf=buf, offset=offset + 0x3C, field="IMAGE_OPTIONAL_HEADER.SizeOfHeaders")
    checksum = _read_u32(buf=buf, offset=offset + 0x40, field="IMAGE_OPTIONAL_HEADER.CheckSum")
    subsystem_value = _read_u16(buf=buf, offset=offset + 0x44, field="IMAGE_OPTIONAL_HEADER.Subsystem")
    dll_characteristics = _read_u16(buf=buf, offset=offset + 0x46, field="IMAGE_OPTIONAL_HEADER.DllCharacteristics")
    number_of_rva_and_sizes = _read_u32(buf=buf, offset=rva_count_offset, field="IMAGE_OPTIONAL_HEADER.NumberOfRvaAndSizes")

    try:
        subsystem = PeSubsystem(subsystem_value)
    except ValueError:
        err_msg_lines = ["Unrecognized PE image subsystem value."]
        err_msg_lines.append("SUBSYSTEM: {}".format(subsystem_value))
        err_msg_lines.append("OFFSET: {}".format(offset + 0x44))

        err_msg = os.linesep.join(err_msg_lines)
        raise UefiProtocolError(err_msg) from None

    if number_of_rva_and_sizes > DATA_DIRECTORY_COUNT:
        err_msg_lines = ["NumberOfRvaAndSizes exceeds the PE/COFF maximum."]
        err_msg_lines.append("OBSERVED: {}".format(number_of_rva_and_sizes))
        err_msg_lines.append("MAXIMUM: {}".format(DATA_DIRECTORY_COUNT))

        err_msg = os.linesep.join(err_msg_lines)
        raise UefiProtocolError(err_msg) from None

    data_directory_bytes = number_of_rva_and_sizes * DATA_DIRECTORY_ENTRY_SIZE
    _require_range(buf=buf, offset=data_directories_offset, size=data_directory_bytes, field="IMAGE_OPTIONAL_HEADER.DataDirectory")

    directories_list: List[Tuple[int, int]] = []
    for index in range(number_of_rva_and_sizes):
        entry_offset = data_directories_offset + (index * DATA_DIRECTORY_ENTRY_SIZE)
        entry_rva = _read_u32(buf=buf, offset=entry_offset + 0x00, field="IMAGE_DATA_DIRECTORY[{}].VirtualAddress".format(index))
        entry_size = _read_u32(buf=buf, offset=entry_offset + 0x04, field="IMAGE_DATA_DIRECTORY[{}].Size".format(index))
        directories_list.append((entry_rva, entry_size))

    data_directories = tuple(directories_list)

    header = OptionalHeaderPe32Plus(
        magic=magic,
        address_of_entry_point=address_of_entry_point,
        image_base=image_base,
        section_alignment=section_alignment,
        file_alignment=file_alignment,
        size_of_image=size_of_image,
        size_of_headers=size_of_headers,
        checksum=checksum,
        subsystem=subsystem,
        dll_characteristics=dll_characteristics,
        size_of_stack_reserve=stack_reserve,
        size_of_stack_commit=stack_commit,
        size_of_heap_reserve=heap_reserve,
        size_of_heap_commit=heap_commit,
        number_of_rva_and_sizes=number_of_rva_and_sizes,
        data_directories=data_directories,
    )

    result = (header, is_pe32_plus)
    return result


def _parse_section_headers(buf: bytes, offset: int, count: int) -> Tuple[SectionHeader, ...]:
    """
        Decode ``count`` contiguous :class:`SectionHeader` records
        beginning at ``offset``.

        :param buf: The backing byte buffer.
        :param offset: Byte offset of the first section-header record.
        :param count: Number of section headers to decode.

        :returns: A tuple of :class:`SectionHeader` in file order.
        :raises UefiProtocolError: On bounds violation.
    """
    total_bytes = count * SECTION_HEADER_SIZE
    _require_range(buf=buf, offset=offset, size=total_bytes, field="IMAGE_SECTION_HEADER_TABLE")

    sections_list: List[SectionHeader] = []
    for index in range(count):
        record_offset = offset + (index * SECTION_HEADER_SIZE)
        field_prefix = "IMAGE_SECTION_HEADER[{}]".format(index)

        _require_range(buf=buf, offset=record_offset, size=8, field=field_prefix + ".Name")
        name_bytes = bytes(buf[record_offset:record_offset + 8])
        name = name_bytes.rstrip(b"\x00").decode(encoding="ascii", errors="replace")

        virtual_size = _read_u32(buf=buf, offset=record_offset + 0x08, field=field_prefix + ".VirtualSize")
        virtual_address = _read_u32(buf=buf, offset=record_offset + 0x0C, field=field_prefix + ".VirtualAddress")
        size_of_raw_data = _read_u32(buf=buf, offset=record_offset + 0x10, field=field_prefix + ".SizeOfRawData")
        pointer_to_raw_data = _read_u32(buf=buf, offset=record_offset + 0x14, field=field_prefix + ".PointerToRawData")
        characteristics = _read_u32(buf=buf, offset=record_offset + 0x24, field=field_prefix + ".Characteristics")

        section = SectionHeader(
            name=name,
            virtual_size=virtual_size,
            virtual_address=virtual_address,
            size_of_raw_data=size_of_raw_data,
            pointer_to_raw_data=pointer_to_raw_data,
            characteristics=characteristics,
        )
        sections_list.append(section)

    sections = tuple(sections_list)
    return sections


def _parse_attribute_certificates(buf: bytes, directory_offset: int, directory_size: int) -> Tuple[AttributeCertificate, ...]:
    """
        Walk the attribute-certificate table that lives at ``directory_offset``
        for ``directory_size`` bytes.  Each table entry begins with an
        8-byte ``WIN_CERTIFICATE`` header (``dwLength``, ``wRevision``,
        ``wCertificateType``) followed by the ``bCertificate`` payload,
        and successive entries are aligned to 8-byte boundaries as
        required by the PE/COFF specification.

        :param buf: The backing byte buffer.
        :param directory_offset: File offset of the first certificate.
        :param directory_size: Byte count of the certificate table.

        :returns: A tuple of :class:`AttributeCertificate` records in
                  file order.  Empty when the security directory is
                  empty.
        :raises UefiProtocolError: On bounds violation or on a
                                   zero / malformed ``dwLength``.
    """
    if directory_offset == 0 or directory_size == 0:
        certificates: Tuple[AttributeCertificate, ...] = ()
        return certificates

    _require_range(buf=buf, offset=directory_offset, size=directory_size, field="ATTRIBUTE_CERTIFICATE_TABLE")

    certificates_list: List[AttributeCertificate] = []
    cursor = directory_offset
    end_offset = directory_offset + directory_size

    while cursor < end_offset:
        _require_range(buf=buf, offset=cursor, size=8, field="WIN_CERTIFICATE.Header")

        length = _read_u32(buf=buf, offset=cursor + 0x00, field="WIN_CERTIFICATE.dwLength")
        revision = _read_u16(buf=buf, offset=cursor + 0x04, field="WIN_CERTIFICATE.wRevision")
        cert_type = _read_u16(buf=buf, offset=cursor + 0x06, field="WIN_CERTIFICATE.wCertificateType")

        if length < 8:
            err_msg_lines = ["WIN_CERTIFICATE.dwLength is smaller than the 8-byte header."]
            err_msg_lines.append("LENGTH: {}".format(length))
            err_msg_lines.append("OFFSET: {}".format(cursor))

            err_msg = os.linesep.join(err_msg_lines)
            raise UefiProtocolError(err_msg) from None

        if cursor + length > end_offset:
            err_msg_lines = ["WIN_CERTIFICATE extends past the attribute-certificate table."]
            err_msg_lines.append("OFFSET: {}".format(cursor))
            err_msg_lines.append("LENGTH: {}".format(length))
            err_msg_lines.append("TABLE END: {}".format(end_offset))

            err_msg = os.linesep.join(err_msg_lines)
            raise UefiProtocolError(err_msg) from None

        payload_offset = cursor + 8
        payload_size = length - 8
        _require_range(buf=buf, offset=payload_offset, size=payload_size, field="WIN_CERTIFICATE.bCertificate")
        raw_signature = bytes(buf[payload_offset:payload_offset + payload_size])

        certificate = AttributeCertificate(
            length=length,
            revision=revision,
            cert_type=cert_type,
            raw_signature=raw_signature,
        )
        certificates_list.append(certificate)

        # Advance to the next 8-byte aligned entry.
        advance = length
        remainder = advance % 8
        if remainder != 0:
            advance = advance + (8 - remainder)
        cursor = cursor + advance

    certificates = tuple(certificates_list)
    return certificates


# ---------------------------------------------------------------------------
# UefiImage root parser
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UefiImage:
    """
        Parsed representation of a UEFI PE/COFF image.

        :param dos_stub_e_lfanew: File offset of the PE signature as read
                                  from the DOS stub ``e_lfanew`` field.
        :param coff_header: The parsed :class:`CoffHeader`.
        :param optional_header: The parsed :class:`OptionalHeaderPe32Plus`.
        :param sections: Tuple of parsed :class:`SectionHeader` records in
                         file order.
        :param certificates: Tuple of parsed :class:`AttributeCertificate`
                             records from the security data directory.
        :param is_pe32_plus: ``True`` when the optional header magic is
                             ``0x20B``; ``False`` for ``0x10B`` PE32.
    """

    dos_stub_e_lfanew: int
    coff_header: CoffHeader
    optional_header: OptionalHeaderPe32Plus
    sections: Tuple[SectionHeader, ...]
    certificates: Tuple[AttributeCertificate, ...]
    is_pe32_plus: bool

    @classmethod
    def from_bytes(cls, data: bytes) -> "UefiImage":
        """
            Parse the supplied ``data`` buffer as a PE/COFF UEFI image.
            The buffer is treated as read-only; no modifications are made
            and no I/O is performed.

            :param data: Byte buffer containing the complete image.

            :returns: A fully-populated :class:`UefiImage`.
            :raises UefiProtocolError: When the buffer is not a valid
                                       PE/COFF image or when any parsed
                                       offset escapes the buffer bounds.
        """
        if not isinstance(data, (bytes, bytearray, memoryview)):
            err_msg_lines = ["UefiImage.from_bytes requires a bytes-like object."]
            err_msg_lines.append("GOT TYPE: {}".format(type(data).__name__))

            err_msg = os.linesep.join(err_msg_lines)
            raise TypeError(err_msg) from None

        if isinstance(data, (bytearray, memoryview)):
            buf = bytes(data)
        else:
            buf = data

        buffer_length = len(buf)

        # --- Validate the DOS stub signature.
        if buffer_length < 2:
            err_msg_lines = ["PE image buffer is too small to contain the DOS stub signature."]
            err_msg_lines.append("BUFFER LENGTH: {}".format(buffer_length))

            err_msg = os.linesep.join(err_msg_lines)
            raise UefiProtocolError(err_msg) from None

        observed_mz = bytes(buf[0:2])
        if observed_mz != DOS_SIGNATURE:
            err_msg_lines = ["PE image does not begin with the DOS 'MZ' signature."]
            err_msg_lines.append("EXPECTED: {!r}".format(DOS_SIGNATURE))
            err_msg_lines.append("OBSERVED: {!r}".format(observed_mz))

            err_msg = os.linesep.join(err_msg_lines)
            raise UefiProtocolError(err_msg) from None

        # --- Read e_lfanew and validate the PE signature at that offset.
        e_lfanew = _read_u32(buf=buf, offset=DOS_E_LFANEW_OFFSET, field="IMAGE_DOS_HEADER.e_lfanew")

        _require_range(buf=buf, offset=e_lfanew, size=4, field="IMAGE_NT_HEADERS.Signature")
        observed_pe = bytes(buf[e_lfanew:e_lfanew + 4])
        if observed_pe != PE_SIGNATURE:
            err_msg_lines = ["PE signature bytes are not 'PE\\0\\0' at e_lfanew."]
            err_msg_lines.append("E_LFANEW: 0x{:X}".format(e_lfanew))
            err_msg_lines.append("EXPECTED: {!r}".format(PE_SIGNATURE))
            err_msg_lines.append("OBSERVED: {!r}".format(observed_pe))

            err_msg = os.linesep.join(err_msg_lines)
            raise UefiProtocolError(err_msg) from None

        # --- Parse the COFF file header.
        coff_header_offset = e_lfanew + 4
        coff_header = _parse_coff_header(buf=buf, offset=coff_header_offset)

        # --- Parse the PE optional header (and the embedded data directory).
        optional_header_offset = coff_header_offset + COFF_HEADER_SIZE
        optional_header, is_pe32_plus = _parse_optional_header(
            buf=buf,
            offset=optional_header_offset,
            size_of_optional_header=coff_header.size_of_optional_header,
        )

        # --- Parse the section table immediately after the optional header.
        section_table_offset = optional_header_offset + coff_header.size_of_optional_header
        sections = _parse_section_headers(
            buf=buf,
            offset=section_table_offset,
            count=coff_header.number_of_sections,
        )

        # --- Parse the attribute-certificate table if present.
        if len(optional_header.data_directories) > SECURITY_DIRECTORY_INDEX:
            security_rva, security_size = optional_header.data_directories[SECURITY_DIRECTORY_INDEX]
        else:
            security_rva = 0
            security_size = 0

        certificates = _parse_attribute_certificates(
            buf=buf,
            directory_offset=security_rva,
            directory_size=security_size,
        )

        image = cls(
            dos_stub_e_lfanew=e_lfanew,
            coff_header=coff_header,
            optional_header=optional_header,
            sections=sections,
            certificates=certificates,
            is_pe32_plus=is_pe32_plus,
        )
        return image

    @property
    def machine(self) -> PeMachine:
        """
            Convenience accessor for the COFF ``Machine`` field as a
            :class:`PeMachine`.

            :returns: The machine enum value from the COFF header.
        """
        rtnval = self.coff_header.machine
        return rtnval

    @property
    def subsystem(self) -> PeSubsystem:
        """
            Convenience accessor for the optional-header ``Subsystem``
            field as a :class:`PeSubsystem`.

            :returns: The subsystem enum value from the optional header.
        """
        rtnval = self.optional_header.subsystem
        return rtnval

    def get_directory(self, index: int) -> Tuple[int, int]:
        """
            Return the ``(rva, size)`` pair for the data directory at
            ``index``.

            :param index: Zero-based index into the data-directory table.

            :returns: Tuple of ``(virtual_address, size)`` for the entry.
            :raises IndexError: When ``index`` is outside the range of
                                decoded data-directory entries.
        """
        directory_count = len(self.optional_header.data_directories)
        if index < 0 or index >= directory_count:
            err_msg_lines = ["Data-directory index is out of range for this image."]
            err_msg_lines.append("INDEX: {}".format(index))
            err_msg_lines.append("DIRECTORY COUNT: {}".format(directory_count))

            err_msg = os.linesep.join(err_msg_lines)
            raise IndexError(err_msg) from None

        rtnval = self.optional_header.data_directories[index]
        return rtnval
