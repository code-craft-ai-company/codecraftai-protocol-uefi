"""
    .. module:: test_uefiloadedimage
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Unit tests for the pure-Python UEFI PE/COFF image parser in
               :mod:`automojo.protocols.uefi.uefiloadedimage`.  Every test
               builds a synthetic in-memory PE32+ image whose layout is
               pinned to the offsets documented in the Microsoft PE/COFF
               specification and UEFI specification section 9.1, then
               parses it through :meth:`UefiImage.from_bytes` and validates
               every decoded field.  Additional tests cover a PE32+
               runtime-driver subsystem variant, an image with an
               attribute-certificate table, and four classes of malformed
               input (missing ``MZ``, missing ``PE\\0\\0``, truncated
               buffer, invalid optional-header magic).

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Automation Mojo LLC"
__credits__ = []


import struct
import unittest

from automojo.protocols.uefi.uefiexceptions import UefiProtocolError
from automojo.protocols.uefi.uefiloadedimage import (
    DATA_DIRECTORY_ENTRY_SIZE,
    DOS_E_LFANEW_OFFSET,
    DOS_SIGNATURE,
    OPTIONAL_HEADER_MAGIC_PE32,
    OPTIONAL_HEADER_MAGIC_PE32_PLUS,
    PE_SIGNATURE,
    SECTION_HEADER_SIZE,
    WIN_CERT_REVISION_2_0,
    WIN_CERT_TYPE_PKCS_SIGNED_DATA,
    AttributeCertificate,
    CoffHeader,
    OptionalHeaderPe32Plus,
    PeMachine,
    PeSubsystem,
    SectionHeader,
    UefiImage,
)


# ---------------------------------------------------------------------------
# Synthetic-image layout constants (pinned to the task prompt).
# ---------------------------------------------------------------------------

E_LFANEW = 0x40
PE_SIGNATURE_OFFSET = E_LFANEW
COFF_HEADER_OFFSET = E_LFANEW + 4              # 0x44
OPTIONAL_HEADER_OFFSET = COFF_HEADER_OFFSET + 20  # 0x58
OPTIONAL_HEADER_SIZE = 0xF0                    # 240 bytes = 112-byte base + 128-byte directories
SECTION_TABLE_OFFSET = OPTIONAL_HEADER_OFFSET + OPTIONAL_HEADER_SIZE  # 0x148

DEFAULT_NUMBER_OF_RVA_AND_SIZES = 16

DEFAULT_IMAGE_BASE = 0x0000000140000000
DEFAULT_ENTRY_POINT = 0x00001000
DEFAULT_SECTION_ALIGNMENT = 0x1000
DEFAULT_FILE_ALIGNMENT = 0x200
DEFAULT_SIZE_OF_IMAGE = 0x00010000
DEFAULT_SIZE_OF_HEADERS = 0x00000200
DEFAULT_CHECKSUM = 0x0000ABCD
DEFAULT_DLL_CHARACTERISTICS = 0x0160
DEFAULT_STACK_RESERVE = 0x0000000000100000
DEFAULT_STACK_COMMIT = 0x0000000000001000
DEFAULT_HEAP_RESERVE = 0x0000000000100000
DEFAULT_HEAP_COMMIT = 0x0000000000001000

DEFAULT_SECTION_NAME = ".text"
DEFAULT_SECTION_VIRTUAL_SIZE = 0x1234
DEFAULT_SECTION_VIRTUAL_ADDRESS = 0x1000
DEFAULT_SECTION_SIZE_OF_RAW_DATA = 0x0200
DEFAULT_SECTION_POINTER_TO_RAW_DATA = 0x0400
DEFAULT_SECTION_CHARACTERISTICS = 0x60000020

DEFAULT_COFF_CHARACTERISTICS = 0x0022
DEFAULT_TIME_DATE_STAMP = 0x5F3C1A2B


# ---------------------------------------------------------------------------
# Synthetic-image builders.
# ---------------------------------------------------------------------------


def _build_optional_header(
    subsystem: int,
    image_base: int,
    data_directories: list,
) -> bytes:
    """
        Build a 240-byte PE32+ optional header including its trailing
        16-entry data-directory array.  Field offsets are pinned to the
        ``IMAGE_OPTIONAL_HEADER64`` layout documented in the Microsoft
        PE/COFF specification.

        :param subsystem: Value for the ``Subsystem`` field at offset 0x44.
        :param image_base: 64-bit ``ImageBase`` value at offset 0x18.
        :param data_directories: List of 16 ``(rva, size)`` tuples.

        :returns: Exactly ``OPTIONAL_HEADER_SIZE`` bytes of optional
                  header plus directory entries.
    """
    buf = bytearray(OPTIONAL_HEADER_SIZE)

    struct.pack_into("<H", buf, 0x00, OPTIONAL_HEADER_MAGIC_PE32_PLUS)
    struct.pack_into("<B", buf, 0x02, 14)                                  # MajorLinkerVersion
    struct.pack_into("<B", buf, 0x03, 0)                                   # MinorLinkerVersion
    struct.pack_into("<I", buf, 0x04, 0x0200)                              # SizeOfCode
    struct.pack_into("<I", buf, 0x08, 0x0100)                              # SizeOfInitializedData
    struct.pack_into("<I", buf, 0x0C, 0x0000)                              # SizeOfUninitializedData
    struct.pack_into("<I", buf, 0x10, DEFAULT_ENTRY_POINT)                 # AddressOfEntryPoint
    struct.pack_into("<I", buf, 0x14, 0x00001000)                          # BaseOfCode
    struct.pack_into("<Q", buf, 0x18, image_base)                          # ImageBase (PE32+)
    struct.pack_into("<I", buf, 0x20, DEFAULT_SECTION_ALIGNMENT)           # SectionAlignment
    struct.pack_into("<I", buf, 0x24, DEFAULT_FILE_ALIGNMENT)              # FileAlignment
    struct.pack_into("<H", buf, 0x28, 6)                                   # MajorOperatingSystemVersion
    struct.pack_into("<H", buf, 0x2A, 0)                                   # MinorOperatingSystemVersion
    struct.pack_into("<H", buf, 0x2C, 0)                                   # MajorImageVersion
    struct.pack_into("<H", buf, 0x2E, 0)                                   # MinorImageVersion
    struct.pack_into("<H", buf, 0x30, 6)                                   # MajorSubsystemVersion
    struct.pack_into("<H", buf, 0x32, 0)                                   # MinorSubsystemVersion
    struct.pack_into("<I", buf, 0x34, 0)                                   # Win32VersionValue
    struct.pack_into("<I", buf, 0x38, DEFAULT_SIZE_OF_IMAGE)               # SizeOfImage
    struct.pack_into("<I", buf, 0x3C, DEFAULT_SIZE_OF_HEADERS)             # SizeOfHeaders
    struct.pack_into("<I", buf, 0x40, DEFAULT_CHECKSUM)                    # CheckSum
    struct.pack_into("<H", buf, 0x44, subsystem)                           # Subsystem
    struct.pack_into("<H", buf, 0x46, DEFAULT_DLL_CHARACTERISTICS)         # DllCharacteristics
    struct.pack_into("<Q", buf, 0x48, DEFAULT_STACK_RESERVE)               # SizeOfStackReserve
    struct.pack_into("<Q", buf, 0x50, DEFAULT_STACK_COMMIT)                # SizeOfStackCommit
    struct.pack_into("<Q", buf, 0x58, DEFAULT_HEAP_RESERVE)                # SizeOfHeapReserve
    struct.pack_into("<Q", buf, 0x60, DEFAULT_HEAP_COMMIT)                 # SizeOfHeapCommit
    struct.pack_into("<I", buf, 0x68, 0)                                   # LoaderFlags
    struct.pack_into("<I", buf, 0x6C, DEFAULT_NUMBER_OF_RVA_AND_SIZES)     # NumberOfRvaAndSizes

    for index in range(DEFAULT_NUMBER_OF_RVA_AND_SIZES):
        entry_rva, entry_size = data_directories[index]
        entry_offset = 0x70 + (index * DATA_DIRECTORY_ENTRY_SIZE)
        struct.pack_into("<I", buf, entry_offset + 0x00, entry_rva)
        struct.pack_into("<I", buf, entry_offset + 0x04, entry_size)

    rtnval = bytes(buf)
    return rtnval


def _build_section_header(
    name: str,
    virtual_size: int,
    virtual_address: int,
    size_of_raw_data: int,
    pointer_to_raw_data: int,
    characteristics: int,
) -> bytes:
    """
        Build a 40-byte ``IMAGE_SECTION_HEADER`` record.

        :param name: ASCII section name (truncated or NUL-padded to 8 bytes).
        :param virtual_size: In-memory size of the section.
        :param virtual_address: RVA of the section.
        :param size_of_raw_data: File size of the section's raw data.
        :param pointer_to_raw_data: File offset of the section's raw data.
        :param characteristics: Raw characteristics bitmask.

        :returns: Exactly ``SECTION_HEADER_SIZE`` bytes.
    """
    buf = bytearray(SECTION_HEADER_SIZE)

    name_bytes = name.encode(encoding="ascii")
    padded_name = name_bytes[:8].ljust(8, b"\x00")
    buf[0:8] = padded_name

    struct.pack_into("<I", buf, 0x08, virtual_size)
    struct.pack_into("<I", buf, 0x0C, virtual_address)
    struct.pack_into("<I", buf, 0x10, size_of_raw_data)
    struct.pack_into("<I", buf, 0x14, pointer_to_raw_data)
    struct.pack_into("<I", buf, 0x18, 0)                    # PointerToRelocations
    struct.pack_into("<I", buf, 0x1C, 0)                    # PointerToLineNumbers
    struct.pack_into("<H", buf, 0x20, 0)                    # NumberOfRelocations
    struct.pack_into("<H", buf, 0x22, 0)                    # NumberOfLineNumbers
    struct.pack_into("<I", buf, 0x24, characteristics)

    rtnval = bytes(buf)
    return rtnval


def _build_minimal_pe_image(
    subsystem: int = int(PeSubsystem.EFI_APPLICATION),
    machine: int = int(PeMachine.X64),
    extra_directories: list = None,
    certificate_payload: bytes = None,
) -> bytes:
    """
        Build a minimal but well-formed PE32+ image.  Layout:

            0x000 - 0x03F : DOS stub (with e_lfanew = 0x40)
            0x040 - 0x043 : PE signature
            0x044 - 0x057 : COFF file header (20 bytes)
            0x058 - 0x147 : Optional header (240 bytes incl. directories)
            0x148 - 0x16F : One section header (40 bytes)
            0x170 - ...   : Optional attribute-certificate table appended
                            at the end of the buffer.

        :param subsystem: Value to write into the optional header's
                          ``Subsystem`` field.
        :param machine: Value to write into the COFF ``Machine`` field.
        :param extra_directories: Optional list of 16 ``(rva, size)``
                                  tuples.  When ``None``, all directory
                                  entries are zero.
        :param certificate_payload: Optional DER-style byte payload.  When
                                    provided, a ``WIN_CERTIFICATE``
                                    attribute-certificate table holding a
                                    single entry is appended to the
                                    buffer and the security data directory
                                    is patched to reference it.

        :returns: The complete image bytes.
    """
    if extra_directories is None:
        data_directories = [(0, 0)] * DEFAULT_NUMBER_OF_RVA_AND_SIZES
    else:
        data_directories = list(extra_directories)

    # Assemble DOS stub.
    dos_stub = bytearray(E_LFANEW)
    dos_stub[0:2] = DOS_SIGNATURE
    struct.pack_into("<I", dos_stub, DOS_E_LFANEW_OFFSET, E_LFANEW)

    # Assemble COFF file header.
    coff_header = bytearray(20)
    struct.pack_into("<H", coff_header, 0x00, machine)
    struct.pack_into("<H", coff_header, 0x02, 1)                                   # NumberOfSections
    struct.pack_into("<I", coff_header, 0x04, DEFAULT_TIME_DATE_STAMP)             # TimeDateStamp
    struct.pack_into("<I", coff_header, 0x08, 0)                                   # PointerToSymbolTable
    struct.pack_into("<I", coff_header, 0x0C, 0)                                   # NumberOfSymbols
    struct.pack_into("<H", coff_header, 0x10, OPTIONAL_HEADER_SIZE)                # SizeOfOptionalHeader
    struct.pack_into("<H", coff_header, 0x12, DEFAULT_COFF_CHARACTERISTICS)        # Characteristics

    # Section header.
    section_header_bytes = _build_section_header(
        name=DEFAULT_SECTION_NAME,
        virtual_size=DEFAULT_SECTION_VIRTUAL_SIZE,
        virtual_address=DEFAULT_SECTION_VIRTUAL_ADDRESS,
        size_of_raw_data=DEFAULT_SECTION_SIZE_OF_RAW_DATA,
        pointer_to_raw_data=DEFAULT_SECTION_POINTER_TO_RAW_DATA,
        characteristics=DEFAULT_SECTION_CHARACTERISTICS,
    )

    # If a certificate payload is requested, patch the security directory to
    # point at a fabricated attribute-certificate table that we append at the
    # end of the buffer (after the section table).
    if certificate_payload is not None:
        certificate_entry_length = 8 + len(certificate_payload)
        remainder = certificate_entry_length % 8
        if remainder != 0:
            padded_entry_length = certificate_entry_length + (8 - remainder)
        else:
            padded_entry_length = certificate_entry_length

        certificate_entry = bytearray(padded_entry_length)
        struct.pack_into("<I", certificate_entry, 0x00, certificate_entry_length)
        struct.pack_into("<H", certificate_entry, 0x04, WIN_CERT_REVISION_2_0)
        struct.pack_into("<H", certificate_entry, 0x06, WIN_CERT_TYPE_PKCS_SIGNED_DATA)
        certificate_entry[8:8 + len(certificate_payload)] = certificate_payload

        security_offset = SECTION_TABLE_OFFSET + SECTION_HEADER_SIZE
        security_size = padded_entry_length
        data_directories[4] = (security_offset, security_size)
    else:
        certificate_entry = None
        security_offset = 0
        security_size = 0

    # Build the optional header with the (possibly patched) directory table.
    optional_header_bytes = _build_optional_header(
        subsystem=subsystem,
        image_base=DEFAULT_IMAGE_BASE,
        data_directories=data_directories,
    )

    image = bytearray()
    image.extend(dos_stub)
    image.extend(PE_SIGNATURE)
    image.extend(coff_header)
    image.extend(optional_header_bytes)
    image.extend(section_header_bytes)
    if certificate_entry is not None:
        image.extend(certificate_entry)

    rtnval = bytes(image)
    return rtnval


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestUefiImageParseValidPe32Plus(unittest.TestCase):
    """
        Validate every decoded field when :meth:`UefiImage.from_bytes` is
        invoked on a minimal well-formed PE32+ image.
    """

    def test_parse_minimal_pe32_plus_image(self) -> None:
        """
            Every decoded field matches the synthetic layout constants.
        """
        image_bytes = _build_minimal_pe_image()
        image = UefiImage.from_bytes(data=image_bytes)

        self.assertIsInstance(image, UefiImage)
        self.assertEqual(image.dos_stub_e_lfanew, E_LFANEW)
        self.assertTrue(image.is_pe32_plus)

        coff_header = image.coff_header
        self.assertIsInstance(coff_header, CoffHeader)
        self.assertEqual(coff_header.machine, PeMachine.X64)
        self.assertEqual(coff_header.number_of_sections, 1)
        self.assertEqual(coff_header.time_date_stamp, DEFAULT_TIME_DATE_STAMP)
        self.assertEqual(coff_header.pointer_to_symbol_table, 0)
        self.assertEqual(coff_header.number_of_symbols, 0)
        self.assertEqual(coff_header.size_of_optional_header, OPTIONAL_HEADER_SIZE)
        self.assertEqual(coff_header.characteristics, DEFAULT_COFF_CHARACTERISTICS)

        optional_header = image.optional_header
        self.assertIsInstance(optional_header, OptionalHeaderPe32Plus)
        self.assertEqual(optional_header.magic, OPTIONAL_HEADER_MAGIC_PE32_PLUS)
        self.assertEqual(optional_header.address_of_entry_point, DEFAULT_ENTRY_POINT)
        self.assertEqual(optional_header.image_base, DEFAULT_IMAGE_BASE)
        self.assertEqual(optional_header.section_alignment, DEFAULT_SECTION_ALIGNMENT)
        self.assertEqual(optional_header.file_alignment, DEFAULT_FILE_ALIGNMENT)
        self.assertEqual(optional_header.size_of_image, DEFAULT_SIZE_OF_IMAGE)
        self.assertEqual(optional_header.size_of_headers, DEFAULT_SIZE_OF_HEADERS)
        self.assertEqual(optional_header.checksum, DEFAULT_CHECKSUM)
        self.assertEqual(optional_header.subsystem, PeSubsystem.EFI_APPLICATION)
        self.assertEqual(optional_header.dll_characteristics, DEFAULT_DLL_CHARACTERISTICS)
        self.assertEqual(optional_header.size_of_stack_reserve, DEFAULT_STACK_RESERVE)
        self.assertEqual(optional_header.size_of_stack_commit, DEFAULT_STACK_COMMIT)
        self.assertEqual(optional_header.size_of_heap_reserve, DEFAULT_HEAP_RESERVE)
        self.assertEqual(optional_header.size_of_heap_commit, DEFAULT_HEAP_COMMIT)
        self.assertEqual(optional_header.number_of_rva_and_sizes, DEFAULT_NUMBER_OF_RVA_AND_SIZES)
        self.assertEqual(len(optional_header.data_directories), DEFAULT_NUMBER_OF_RVA_AND_SIZES)
        for rva, size in optional_header.data_directories:
            self.assertEqual(rva, 0)
            self.assertEqual(size, 0)

        sections = image.sections
        self.assertEqual(len(sections), 1)
        section = sections[0]
        self.assertIsInstance(section, SectionHeader)
        self.assertEqual(section.name, DEFAULT_SECTION_NAME)
        self.assertEqual(section.virtual_size, DEFAULT_SECTION_VIRTUAL_SIZE)
        self.assertEqual(section.virtual_address, DEFAULT_SECTION_VIRTUAL_ADDRESS)
        self.assertEqual(section.size_of_raw_data, DEFAULT_SECTION_SIZE_OF_RAW_DATA)
        self.assertEqual(section.pointer_to_raw_data, DEFAULT_SECTION_POINTER_TO_RAW_DATA)
        self.assertEqual(section.characteristics, DEFAULT_SECTION_CHARACTERISTICS)

        self.assertEqual(image.certificates, ())
        return


class TestUefiImageSubsystemVariants(unittest.TestCase):
    """
        Validate that varying the optional-header ``Subsystem`` value
        surfaces the correct :class:`PeSubsystem` enum member.
    """

    def test_parse_efi_runtime_driver_subsystem(self) -> None:
        """
            ``Subsystem == 12`` decodes to ``PeSubsystem.EFI_RUNTIME_DRIVER``.
        """
        image_bytes = _build_minimal_pe_image(subsystem=int(PeSubsystem.EFI_RUNTIME_DRIVER))
        image = UefiImage.from_bytes(data=image_bytes)

        self.assertEqual(image.subsystem, PeSubsystem.EFI_RUNTIME_DRIVER)
        self.assertEqual(image.optional_header.subsystem, PeSubsystem.EFI_RUNTIME_DRIVER)
        return

    def test_parse_efi_boot_service_driver_subsystem(self) -> None:
        """
            ``Subsystem == 11`` decodes to
            ``PeSubsystem.EFI_BOOT_SERVICE_DRIVER``.
        """
        image_bytes = _build_minimal_pe_image(subsystem=int(PeSubsystem.EFI_BOOT_SERVICE_DRIVER))
        image = UefiImage.from_bytes(data=image_bytes)

        self.assertEqual(image.subsystem, PeSubsystem.EFI_BOOT_SERVICE_DRIVER)
        return

    def test_parse_efi_rom_subsystem(self) -> None:
        """
            ``Subsystem == 13`` decodes to ``PeSubsystem.EFI_ROM``.
        """
        image_bytes = _build_minimal_pe_image(subsystem=int(PeSubsystem.EFI_ROM))
        image = UefiImage.from_bytes(data=image_bytes)

        self.assertEqual(image.subsystem, PeSubsystem.EFI_ROM)
        return


class TestUefiImageProperties(unittest.TestCase):
    """
        Validate the convenience accessors on :class:`UefiImage`.
    """

    def test_machine_property_matches_coff_header(self) -> None:
        """
            ``image.machine`` equals the COFF header's ``machine`` value.
        """
        image_bytes = _build_minimal_pe_image(machine=int(PeMachine.AARCH64))
        image = UefiImage.from_bytes(data=image_bytes)

        self.assertEqual(image.machine, PeMachine.AARCH64)
        self.assertEqual(image.machine, image.coff_header.machine)
        return

    def test_subsystem_property_matches_optional_header(self) -> None:
        """
            ``image.subsystem`` equals the optional header's ``subsystem``
            value.
        """
        image_bytes = _build_minimal_pe_image(subsystem=int(PeSubsystem.EFI_RUNTIME_DRIVER))
        image = UefiImage.from_bytes(data=image_bytes)

        self.assertEqual(image.subsystem, PeSubsystem.EFI_RUNTIME_DRIVER)
        self.assertEqual(image.subsystem, image.optional_header.subsystem)
        return

    def test_get_directory_returns_tuple(self) -> None:
        """
            ``get_directory`` returns the requested data-directory pair.
        """
        directories = [(0, 0)] * DEFAULT_NUMBER_OF_RVA_AND_SIZES
        directories[2] = (0x2000, 0x0400)
        image_bytes = _build_minimal_pe_image(extra_directories=directories)
        image = UefiImage.from_bytes(data=image_bytes)

        rtnval = image.get_directory(index=2)
        self.assertEqual(rtnval, (0x2000, 0x0400))
        return

    def test_get_directory_raises_on_out_of_range(self) -> None:
        """
            ``get_directory`` raises ``IndexError`` when the requested
            index lies outside the decoded directory table.
        """
        image_bytes = _build_minimal_pe_image()
        image = UefiImage.from_bytes(data=image_bytes)

        with self.assertRaises(IndexError):
            image.get_directory(index=99)
        return


class TestUefiImageAttributeCertificates(unittest.TestCase):
    """
        Validate that the security data directory is walked and the
        embedded ``WIN_CERTIFICATE`` payload is surfaced.
    """

    def test_parse_image_with_attribute_certificate(self) -> None:
        """
            An image whose security directory points at one PKCS#7
            certificate surfaces it on ``image.certificates`` with the
            expected DER payload.
        """
        expected_payload = b"\x30\x82\x01\x02" + b"DERDATA0123456789" + b"\xAB" * 16
        image_bytes = _build_minimal_pe_image(certificate_payload=expected_payload)
        image = UefiImage.from_bytes(data=image_bytes)

        self.assertEqual(len(image.certificates), 1)
        certificate = image.certificates[0]
        self.assertIsInstance(certificate, AttributeCertificate)
        self.assertEqual(certificate.revision, WIN_CERT_REVISION_2_0)
        self.assertEqual(certificate.cert_type, WIN_CERT_TYPE_PKCS_SIGNED_DATA)
        self.assertEqual(certificate.length, 8 + len(expected_payload))
        self.assertEqual(certificate.raw_signature[:len(expected_payload)], expected_payload)
        return

    def test_get_directory_returns_security_entry(self) -> None:
        """
            ``get_directory(4)`` returns the security directory pair that
            was patched into the optional header.
        """
        expected_payload = b"\x30\x82\x00\x10" + b"payload!"
        image_bytes = _build_minimal_pe_image(certificate_payload=expected_payload)
        image = UefiImage.from_bytes(data=image_bytes)

        security_rva, security_size = image.get_directory(index=4)
        self.assertGreater(security_rva, 0)
        self.assertGreater(security_size, 0)
        return


class TestUefiImageMalformedInput(unittest.TestCase):
    """
        Validate that malformed input is rejected with
        :class:`UefiProtocolError` rather than with a raw ``struct.error``
        or ``IndexError``.
    """

    def test_missing_mz_signature_raises(self) -> None:
        """
            A buffer whose first two bytes are not ``b"MZ"`` raises
            :class:`UefiProtocolError`.
        """
        image_bytes = _build_minimal_pe_image()
        tampered = bytearray(image_bytes)
        tampered[0] = 0x00
        tampered[1] = 0x00

        with self.assertRaises(UefiProtocolError):
            UefiImage.from_bytes(data=bytes(tampered))
        return

    def test_bad_pe_signature_raises(self) -> None:
        """
            A buffer whose bytes at ``e_lfanew`` are not ``b"PE\\0\\0"``
            raises :class:`UefiProtocolError`.
        """
        image_bytes = _build_minimal_pe_image()
        tampered = bytearray(image_bytes)
        tampered[E_LFANEW:E_LFANEW + 4] = b"XYZ!"

        with self.assertRaises(UefiProtocolError):
            UefiImage.from_bytes(data=bytes(tampered))
        return

    def test_truncated_buffer_raises(self) -> None:
        """
            Truncating the image to less than the optional-header window
            raises :class:`UefiProtocolError`.
        """
        image_bytes = _build_minimal_pe_image()
        truncated = image_bytes[:OPTIONAL_HEADER_OFFSET + 10]

        with self.assertRaises(UefiProtocolError):
            UefiImage.from_bytes(data=truncated)
        return

    def test_tiny_buffer_raises(self) -> None:
        """
            A buffer too small even to contain the DOS signature raises
            :class:`UefiProtocolError`.
        """
        with self.assertRaises(UefiProtocolError):
            UefiImage.from_bytes(data=b"\x00")
        return

    def test_invalid_optional_header_magic_raises(self) -> None:
        """
            An optional-header magic that is neither PE32 nor PE32+
            raises :class:`UefiProtocolError`.
        """
        image_bytes = _build_minimal_pe_image()
        tampered = bytearray(image_bytes)
        struct.pack_into("<H", tampered, OPTIONAL_HEADER_OFFSET, 0xDEAD)

        with self.assertRaises(UefiProtocolError):
            UefiImage.from_bytes(data=bytes(tampered))
        return

    def test_unknown_machine_value_raises(self) -> None:
        """
            An unrecognized COFF ``Machine`` value raises
            :class:`UefiProtocolError`.
        """
        image_bytes = _build_minimal_pe_image()
        tampered = bytearray(image_bytes)
        struct.pack_into("<H", tampered, COFF_HEADER_OFFSET, 0xDEAD)

        with self.assertRaises(UefiProtocolError):
            UefiImage.from_bytes(data=bytes(tampered))
        return

    def test_non_bytes_input_raises_type_error(self) -> None:
        """
            Passing something that is not a bytes-like object raises
            :class:`TypeError`.
        """
        with self.assertRaises(TypeError):
            UefiImage.from_bytes(data=12345)
        return


if __name__ == "__main__":
    unittest.main()
