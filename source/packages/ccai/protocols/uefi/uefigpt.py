"""
    .. module:: uefigpt
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Pure-Python parser / builder for the UEFI GUID Partition Table
               (GPT) structures defined by UEFI specification section 5.3.
               Provides frozen dataclasses for the GPT header, for a GPT
               partition entry, and for a whole-disk GPT view, together with
               well-known partition-type GUID constants from UEFI section
               5.3.3 Table 22.  CRC-32 validation is performed via the
               layer-1 :func:`crc32_uefi` helper so the computed value
               matches ``EFI_BOOT_SERVICES.CalculateCrc32``.  This module
               performs no I/O: callers are expected to pass in raw byte
               buffers obtained from a disk image or a block device.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Code Craft AI LLC"
__credits__ = []


import os
import struct
import uuid

from dataclasses import dataclass
from typing import Final, List, Optional, Tuple

from ccai.protocols.uefi.ueficonst import guid_from_bytes, guid_to_bytes
from ccai.protocols.uefi.uefiexceptions import UefiError
from ccai.protocols.uefi.uefihelpers import crc32_uefi, ucs2_decode, ucs2_encode


__all__ = [
    "APPLE_APFS_PARTITION_GUID",
    "APPLE_HFS_PARTITION_GUID",
    "CHROMEOS_KERNEL_PARTITION_GUID",
    "CHROMEOS_ROOTFS_PARTITION_GUID",
    "DEFAULT_SECTOR_SIZE",
    "EFI_SYSTEM_PARTITION_GUID",
    "GPT_HEADER_REVISION",
    "GPT_HEADER_SIGNATURE",
    "GPT_HEADER_SIZE",
    "GPT_PARTITION_ENTRY_SIZE",
    "GPT_PARTITION_NAME_LENGTH",
    "GptDisk",
    "GptHeader",
    "GptPartitionEntry",
    "LINUX_FILESYSTEM_DATA_PARTITION_GUID",
    "LINUX_LVM_PARTITION_GUID",
    "LINUX_SWAP_PARTITION_GUID",
    "MICROSOFT_BASIC_DATA_PARTITION_GUID",
    "MICROSOFT_RESERVED_PARTITION_GUID",
    "UNUSED_PARTITION_GUID",
]


# ---------------------------------------------------------------------------
# Constants (UEFI specification section 5.3)
# ---------------------------------------------------------------------------

#: The 8-byte ``Signature`` field value that identifies an ``EFI_PART``
#: header.  Equivalent to the 64-bit little-endian constant
#: ``0x5452415020494645`` cited in UEFI section 5.3.2.
GPT_HEADER_SIGNATURE: Final[bytes] = b"EFI PART"

#: The ``Revision`` field value for UEFI specification revision 1.0 of the
#: GPT header layout (the revision that defined the 92-byte header shape).
GPT_HEADER_REVISION: Final[int] = 0x00010000

#: The ``HeaderSize`` for a UEFI 1.0 GPT header in bytes (see Table 5-6).
GPT_HEADER_SIZE: Final[int] = 92

#: The nominal size of a single ``GPT_PARTITION_ENTRY`` in bytes.  The UEFI
#: specification requires this to be a multiple of 8 and at least 128.
GPT_PARTITION_ENTRY_SIZE: Final[int] = 128

#: Number of bytes reserved for ``PartitionName`` (36 UCS-2 characters).
GPT_PARTITION_NAME_LENGTH: Final[int] = 72

#: The default logical block size assumed by :meth:`GptDisk.from_bytes`.  A
#: caller that reads a 4K-native disk should pass ``sector_size=4096``.
DEFAULT_SECTOR_SIZE: Final[int] = 512


# ---------------------------------------------------------------------------
# Well-known partition-type GUID constants (UEFI section 5.3.3 Table 22)
# ---------------------------------------------------------------------------

UNUSED_PARTITION_GUID: Final[uuid.UUID] = uuid.UUID("00000000-0000-0000-0000-000000000000")
EFI_SYSTEM_PARTITION_GUID: Final[uuid.UUID] = uuid.UUID("C12A7328-F81F-11D2-BA4B-00A0C93EC93B")
MICROSOFT_BASIC_DATA_PARTITION_GUID: Final[uuid.UUID] = uuid.UUID("EBD0A0A2-B9E5-4433-87C0-68B6B72699C7")
MICROSOFT_RESERVED_PARTITION_GUID: Final[uuid.UUID] = uuid.UUID("E3C9E316-0B5C-4DB8-817D-F92DF00215AE")
LINUX_FILESYSTEM_DATA_PARTITION_GUID: Final[uuid.UUID] = uuid.UUID("0FC63DAF-8483-4772-8E79-3D69D8477DE4")
LINUX_SWAP_PARTITION_GUID: Final[uuid.UUID] = uuid.UUID("0657FD6D-A4AB-43C4-84E5-0933C84B4F4F")
LINUX_LVM_PARTITION_GUID: Final[uuid.UUID] = uuid.UUID("E6D6D379-F507-44C2-A23C-238F2A3DF928")
APPLE_HFS_PARTITION_GUID: Final[uuid.UUID] = uuid.UUID("48465300-0000-11AA-AA11-00306543ECAC")
APPLE_APFS_PARTITION_GUID: Final[uuid.UUID] = uuid.UUID("7C3457EF-0000-11AA-AA11-00306543ECAC")
CHROMEOS_KERNEL_PARTITION_GUID: Final[uuid.UUID] = uuid.UUID("FE3A2A5D-4F32-41A7-B725-ACCC3285A309")
CHROMEOS_ROOTFS_PARTITION_GUID: Final[uuid.UUID] = uuid.UUID("3CB8E202-3B7E-47DD-8A3C-7FF2A13CFCEC")


# ---------------------------------------------------------------------------
# Byte-layout helpers
# ---------------------------------------------------------------------------
#
# ``_GPT_HEADER_STRUCT`` mirrors UEFI section 5.3.2 Table 5-6 for the first
# 92 bytes of LBA 1.  The ``DiskGUID`` field is declared as a raw 16-byte
# buffer because the UEFI GUID layout is mixed-endian and :mod:`uuid`
# handles the conversion via :meth:`uuid.UUID.bytes_le`.
# ---------------------------------------------------------------------------

_GPT_HEADER_STRUCT: Final[struct.Struct] = struct.Struct("<8sIIIIQQQQ16sQIII")
_GPT_ENTRY_STRUCT: Final[struct.Struct] = struct.Struct("<16s16sQQQ")


# ---------------------------------------------------------------------------
# GptHeader dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GptHeader:
    """
        Value object representing the first 92 bytes of a UEFI GPT header
        as laid out in UEFI section 5.3.2 Table 5-6.

        :param signature: The 8-byte ``Signature`` field; must equal
                          :data:`GPT_HEADER_SIGNATURE`.
        :param revision: The ``Revision`` field (``UINT32`` little-endian).
        :param header_size: The ``HeaderSize`` field.  The CRC-32 is
                            computed over exactly this many header bytes.
        :param header_crc32: The stored ``HeaderCRC32`` value as parsed
                             from the buffer (this is preserved so equality
                             comparisons round-trip byte-for-byte).
        :param reserved: The 4-byte reserved field that follows
                         ``HeaderCRC32`` in the header.  UEFI requires this
                         to be zero.
        :param my_lba: Logical block address of this header.
        :param alternate_lba: Logical block address of the paired header.
        :param first_usable_lba: First LBA usable for partitions.
        :param last_usable_lba: Last LBA usable for partitions.
        :param disk_guid: The disk's ``DiskGUID`` as a
                          :class:`uuid.UUID`.
        :param partition_entry_lba: Logical block address where the
                                    partition entry array starts.
        :param num_partition_entries: The ``NumberOfPartitionEntries``
                                      field value.
        :param size_of_partition_entry: The ``SizeOfPartitionEntry`` field
                                        value (typically 128).
        :param partition_entry_array_crc32: The stored CRC-32 over the
                                            full entry-array buffer.
    """

    signature: bytes
    revision: int
    header_size: int
    header_crc32: int
    reserved: int
    my_lba: int
    alternate_lba: int
    first_usable_lba: int
    last_usable_lba: int
    disk_guid: uuid.UUID
    partition_entry_lba: int
    num_partition_entries: int
    size_of_partition_entry: int
    partition_entry_array_crc32: int

    @classmethod
    def from_bytes(cls, lba1: bytes) -> "GptHeader":
        """
            Decode a GPT header from the first ``GPT_HEADER_SIZE`` bytes of
            a GPT LBA-1 sector.  Validates the signature, the revision,
            the header size, and the ``HeaderCRC32``.

            :param lba1: The raw bytes of LBA 1 (expected to be at least
                         :data:`GPT_HEADER_SIZE` bytes long; typically an
                         entire 512-byte sector).

            :returns: A :class:`GptHeader` instance populated from ``lba1``.
            :raises: UefiError
        """
        if not isinstance(lba1, (bytes, bytearray, memoryview)):
            err_msg_lines = [
                "'lba1' must be a bytes-like object.",
                "GOT: type={}".format(type(lba1).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg)

        buffer = bytes(lba1)
        buffer_length = len(buffer)

        if buffer_length < GPT_HEADER_SIZE:
            err_msg_lines = [
                "GPT LBA1 buffer is too small to contain a GPT header.",
                "EXPECTED: at least {} bytes".format(GPT_HEADER_SIZE),
                "OBSERVED: {} bytes".format(buffer_length),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg)

        unpacked = _GPT_HEADER_STRUCT.unpack_from(buffer, 0)
        signature = unpacked[0]
        revision = unpacked[1]
        header_size = unpacked[2]
        stored_header_crc32 = unpacked[3]
        reserved = unpacked[4]
        my_lba = unpacked[5]
        alternate_lba = unpacked[6]
        first_usable_lba = unpacked[7]
        last_usable_lba = unpacked[8]
        disk_guid_bytes = unpacked[9]
        partition_entry_lba = unpacked[10]
        num_partition_entries = unpacked[11]
        size_of_partition_entry = unpacked[12]
        partition_entry_array_crc32 = unpacked[13]

        if signature != GPT_HEADER_SIGNATURE:
            err_msg_lines = [
                "GPT header signature mismatch.",
                "EXPECTED: {!r}".format(GPT_HEADER_SIGNATURE),
                "OBSERVED: {!r}".format(signature),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg)

        if revision != GPT_HEADER_REVISION:
            err_msg_lines = [
                "GPT header revision not supported.",
                "EXPECTED: 0x{:08X}".format(GPT_HEADER_REVISION),
                "OBSERVED: 0x{:08X}".format(revision),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg)

        if header_size < GPT_HEADER_SIZE:
            err_msg_lines = [
                "GPT header size smaller than the UEFI 1.0 minimum.",
                "EXPECTED: >= {}".format(GPT_HEADER_SIZE),
                "OBSERVED: {}".format(header_size),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg)

        if header_size > buffer_length:
            err_msg_lines = [
                "GPT header size larger than the supplied buffer.",
                "HEADER_SIZE: {}".format(header_size),
                "BUFFER_LENGTH: {}".format(buffer_length),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg)

        scratch = bytearray(buffer[:header_size])
        scratch[16:20] = b"\x00\x00\x00\x00"
        computed_header_crc32 = crc32_uefi(bytes(scratch))

        if computed_header_crc32 != stored_header_crc32:
            err_msg_lines = [
                "GPT HeaderCRC32 mismatch.",
                "COMPUTED: 0x{:08X}".format(computed_header_crc32),
                "STORED:   0x{:08X}".format(stored_header_crc32),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            detail_lines = [
                "COMPUTED: 0x{:08X}".format(computed_header_crc32),
                "STORED:   0x{:08X}".format(stored_header_crc32),
            ]
            raise UefiError(err_msg, details=detail_lines)

        disk_guid = guid_from_bytes(disk_guid_bytes)

        rtnval = cls(
            signature=signature,
            revision=revision,
            header_size=header_size,
            header_crc32=stored_header_crc32,
            reserved=reserved,
            my_lba=my_lba,
            alternate_lba=alternate_lba,
            first_usable_lba=first_usable_lba,
            last_usable_lba=last_usable_lba,
            disk_guid=disk_guid,
            partition_entry_lba=partition_entry_lba,
            num_partition_entries=num_partition_entries,
            size_of_partition_entry=size_of_partition_entry,
            partition_entry_array_crc32=partition_entry_array_crc32,
        )
        return rtnval

    def to_bytes(self) -> bytes:
        """
            Serialize the header into a :data:`GPT_HEADER_SIZE`-byte
            buffer suitable for placing at LBA 1.  The ``HeaderCRC32``
            value inside the returned buffer is recomputed over the
            serialized bytes, so the output is always internally
            consistent regardless of what ``self.header_crc32`` holds.

            :returns: A ``bytes`` object of length :data:`GPT_HEADER_SIZE`.
        """
        disk_guid_bytes = guid_to_bytes(self.disk_guid)

        scratch = bytearray(_GPT_HEADER_STRUCT.pack(
            self.signature,
            self.revision,
            self.header_size,
            0,
            self.reserved,
            self.my_lba,
            self.alternate_lba,
            self.first_usable_lba,
            self.last_usable_lba,
            disk_guid_bytes,
            self.partition_entry_lba,
            self.num_partition_entries,
            self.size_of_partition_entry,
            self.partition_entry_array_crc32,
        ))

        computed_header_crc32 = crc32_uefi(bytes(scratch))
        scratch[16:20] = computed_header_crc32.to_bytes(4, "little")

        rtnval = bytes(scratch)
        return rtnval


# ---------------------------------------------------------------------------
# GptPartitionEntry dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GptPartitionEntry:
    """
        Value object representing a single 128-byte entry in the GPT
        Partition Entry Array as laid out in UEFI section 5.3.3.

        :param partition_type_guid: Type GUID identifying the kind of
                                    partition (for example, :data:`EFI_SYSTEM_PARTITION_GUID`).
        :param unique_partition_guid: The per-partition unique GUID.
        :param starting_lba: First LBA of the partition (inclusive).
        :param ending_lba: Last LBA of the partition (inclusive).
        :param attributes: 64-bit attribute bitfield (bits 0..2 generic,
                           bits 48..63 type-specific).
        :param partition_name: The ``PartitionName`` field decoded from
                               UCS-2 with trailing nulls stripped.
    """

    partition_type_guid: uuid.UUID
    unique_partition_guid: uuid.UUID
    starting_lba: int
    ending_lba: int
    attributes: int
    partition_name: str

    @classmethod
    def from_bytes(cls, entry_bytes: bytes) -> "GptPartitionEntry":
        """
            Decode a single partition entry from its 128-byte on-disk
            representation.

            :param entry_bytes: A buffer of length
                                :data:`GPT_PARTITION_ENTRY_SIZE`.

            :returns: The decoded :class:`GptPartitionEntry`.
            :raises: UefiError
        """
        if not isinstance(entry_bytes, (bytes, bytearray, memoryview)):
            err_msg_lines = [
                "'entry_bytes' must be a bytes-like object.",
                "GOT: type={}".format(type(entry_bytes).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg)

        buffer = bytes(entry_bytes)
        buffer_length = len(buffer)

        if buffer_length != GPT_PARTITION_ENTRY_SIZE:
            err_msg_lines = [
                "GPT partition entry buffer length mismatch.",
                "EXPECTED: {} bytes".format(GPT_PARTITION_ENTRY_SIZE),
                "OBSERVED: {} bytes".format(buffer_length),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg)

        unpacked = _GPT_ENTRY_STRUCT.unpack_from(buffer, 0)
        type_guid_bytes = unpacked[0]
        unique_guid_bytes = unpacked[1]
        starting_lba = unpacked[2]
        ending_lba = unpacked[3]
        attributes = unpacked[4]

        partition_type_guid = guid_from_bytes(type_guid_bytes)
        unique_partition_guid = guid_from_bytes(unique_guid_bytes)

        name_bytes = buffer[56:56 + GPT_PARTITION_NAME_LENGTH]
        decoded_name = ucs2_decode(name_bytes)
        null_index = decoded_name.find("\x00")
        if null_index >= 0:
            partition_name = decoded_name[:null_index]
        else:
            partition_name = decoded_name

        rtnval = cls(
            partition_type_guid=partition_type_guid,
            unique_partition_guid=unique_partition_guid,
            starting_lba=starting_lba,
            ending_lba=ending_lba,
            attributes=attributes,
            partition_name=partition_name,
        )
        return rtnval

    def to_bytes(self) -> bytes:
        """
            Serialize the partition entry into its 128-byte on-disk
            representation.  The ``PartitionName`` is encoded as UCS-2
            and zero-padded to :data:`GPT_PARTITION_NAME_LENGTH` bytes.

            :returns: A ``bytes`` object of length
                      :data:`GPT_PARTITION_ENTRY_SIZE`.
            :raises: UefiError
        """
        encoded_name = ucs2_encode(self.partition_name)
        encoded_name_length = len(encoded_name)

        if encoded_name_length > GPT_PARTITION_NAME_LENGTH:
            err_msg_lines = [
                "GPT partition_name exceeds the 72-byte field.",
                "EXPECTED: <= {} bytes".format(GPT_PARTITION_NAME_LENGTH),
                "OBSERVED: {} bytes".format(encoded_name_length),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg)

        padded_name = encoded_name + b"\x00" * (GPT_PARTITION_NAME_LENGTH - encoded_name_length)

        type_guid_bytes = guid_to_bytes(self.partition_type_guid)
        unique_guid_bytes = guid_to_bytes(self.unique_partition_guid)

        header_part = _GPT_ENTRY_STRUCT.pack(
            type_guid_bytes,
            unique_guid_bytes,
            self.starting_lba,
            self.ending_lba,
            self.attributes,
        )

        rtnval = header_part + padded_name
        return rtnval


# ---------------------------------------------------------------------------
# GptDisk dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GptDisk:
    """
        Value object pairing a GPT header with its decoded partition-entry
        array.

        :param header: The parsed :class:`GptHeader`.
        :param entries: Tuple of :class:`GptPartitionEntry` values.  The
                        tuple length equals ``header.num_partition_entries``
                        and preserves array indexing (unused entries are
                        included).
    """

    header: GptHeader
    entries: Tuple[GptPartitionEntry, ...]

    @classmethod
    def from_bytes(cls, image_bytes: bytes, sector_size: int = DEFAULT_SECTOR_SIZE) -> "GptDisk":
        """
            Parse a primary GPT from a raw disk-image buffer.  The buffer
            must start at LBA 0 (the protective MBR).  The header is
            located at ``sector_size`` (LBA 1) and the entry array is
            located at ``header.partition_entry_lba * sector_size``.  The
            partition-entry-array CRC-32 is validated against
            ``header.partition_entry_array_crc32``.

            :param image_bytes: Bytes starting at LBA 0 of a GPT disk
                                image.  Must be long enough to contain the
                                header and the full entry array.
            :param sector_size: Logical block size in bytes.  Defaults to
                                :data:`DEFAULT_SECTOR_SIZE`.

            :returns: A :class:`GptDisk` populated from the image.
            :raises: UefiError
        """
        if not isinstance(image_bytes, (bytes, bytearray, memoryview)):
            err_msg_lines = [
                "'image_bytes' must be a bytes-like object.",
                "GOT: type={}".format(type(image_bytes).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg)

        if not isinstance(sector_size, int) or isinstance(sector_size, bool):
            err_msg_lines = [
                "'sector_size' must be an int.",
                "GOT: type={}".format(type(sector_size).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg)

        if sector_size <= 0:
            err_msg_lines = [
                "'sector_size' must be positive.",
                "OBSERVED: {}".format(sector_size),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg)

        buffer = bytes(image_bytes)
        buffer_length = len(buffer)

        lba1_start = sector_size
        lba1_end = lba1_start + sector_size

        if buffer_length < lba1_end:
            err_msg_lines = [
                "Image buffer is too small to contain LBA 1.",
                "EXPECTED: at least {} bytes".format(lba1_end),
                "OBSERVED: {} bytes".format(buffer_length),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg)

        lba1 = buffer[lba1_start:lba1_end]
        header = GptHeader.from_bytes(lba1=lba1)

        entry_size = header.size_of_partition_entry
        entry_count = header.num_partition_entries

        if entry_size < GPT_PARTITION_ENTRY_SIZE:
            err_msg_lines = [
                "GPT size_of_partition_entry below the UEFI minimum.",
                "EXPECTED: >= {}".format(GPT_PARTITION_ENTRY_SIZE),
                "OBSERVED: {}".format(entry_size),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg)

        if (entry_size % 8) != 0:
            err_msg_lines = [
                "GPT size_of_partition_entry must be a multiple of 8.",
                "OBSERVED: {}".format(entry_size),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg)

        array_offset = header.partition_entry_lba * sector_size
        array_size = entry_size * entry_count
        array_end = array_offset + array_size

        if buffer_length < array_end:
            err_msg_lines = [
                "Image buffer is too small to contain the GPT partition entry array.",
                "EXPECTED: at least {} bytes".format(array_end),
                "OBSERVED: {} bytes".format(buffer_length),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg)

        array_bytes = buffer[array_offset:array_end]
        computed_array_crc32 = crc32_uefi(array_bytes)

        if computed_array_crc32 != header.partition_entry_array_crc32:
            err_msg_lines = [
                "GPT PartitionEntryArrayCRC32 mismatch.",
                "COMPUTED: 0x{:08X}".format(computed_array_crc32),
                "STORED:   0x{:08X}".format(header.partition_entry_array_crc32),
                "ARRAY_OFFSET: {}".format(array_offset),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            detail_lines = [
                "COMPUTED: 0x{:08X}".format(computed_array_crc32),
                "STORED:   0x{:08X}".format(header.partition_entry_array_crc32),
                "ARRAY_OFFSET: {}".format(array_offset),
            ]
            raise UefiError(err_msg, details=detail_lines)

        decoded_entries: List[GptPartitionEntry] = []
        for entry_index in range(entry_count):
            entry_start = entry_index * entry_size
            entry_end = entry_start + entry_size
            full_entry_bytes = array_bytes[entry_start:entry_end]
            core_entry_bytes = full_entry_bytes[:GPT_PARTITION_ENTRY_SIZE]
            decoded = GptPartitionEntry.from_bytes(entry_bytes=core_entry_bytes)
            decoded_entries.append(decoded)

        rtnval = cls(header=header, entries=tuple(decoded_entries))
        return rtnval

    @classmethod
    def validate_pair(cls, primary: "GptDisk", backup: "GptDisk") -> None:
        """
            Verify that a primary / backup pair of parsed GPT disks is
            internally consistent per UEFI section 5.3.2.  The following
            invariants are checked:

            * ``primary.header.my_lba == backup.header.alternate_lba``
            * ``primary.header.alternate_lba == backup.header.my_lba``
            * ``primary.header.disk_guid == backup.header.disk_guid``
            * ``primary.header.first_usable_lba == backup.header.first_usable_lba``
            * ``primary.header.last_usable_lba == backup.header.last_usable_lba``
            * ``primary.header.partition_entry_array_crc32 == backup.header.partition_entry_array_crc32``
            * ``primary.header.num_partition_entries == backup.header.num_partition_entries``
            * ``primary.header.size_of_partition_entry == backup.header.size_of_partition_entry``

            :param primary: The primary-header :class:`GptDisk`.
            :param backup: The backup-header :class:`GptDisk`.

            :returns: ``None`` on success.
            :raises: UefiError
        """
        if not isinstance(primary, GptDisk):
            err_msg_lines = [
                "'primary' must be a GptDisk.",
                "GOT: type={}".format(type(primary).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg)

        if not isinstance(backup, GptDisk):
            err_msg_lines = [
                "'backup' must be a GptDisk.",
                "GOT: type={}".format(type(backup).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg)

        mismatches: List[str] = []

        if primary.header.my_lba != backup.header.alternate_lba:
            mismatches.append("my_lba/alternate_lba: primary.my_lba=0x{:X} backup.alternate_lba=0x{:X}".format(
                primary.header.my_lba, backup.header.alternate_lba))

        if primary.header.alternate_lba != backup.header.my_lba:
            mismatches.append("alternate_lba/my_lba: primary.alternate_lba=0x{:X} backup.my_lba=0x{:X}".format(
                primary.header.alternate_lba, backup.header.my_lba))

        if primary.header.disk_guid != backup.header.disk_guid:
            mismatches.append("disk_guid: primary={} backup={}".format(
                primary.header.disk_guid, backup.header.disk_guid))

        if primary.header.first_usable_lba != backup.header.first_usable_lba:
            mismatches.append("first_usable_lba: primary=0x{:X} backup=0x{:X}".format(
                primary.header.first_usable_lba, backup.header.first_usable_lba))

        if primary.header.last_usable_lba != backup.header.last_usable_lba:
            mismatches.append("last_usable_lba: primary=0x{:X} backup=0x{:X}".format(
                primary.header.last_usable_lba, backup.header.last_usable_lba))

        if primary.header.partition_entry_array_crc32 != backup.header.partition_entry_array_crc32:
            mismatches.append("partition_entry_array_crc32: primary=0x{:08X} backup=0x{:08X}".format(
                primary.header.partition_entry_array_crc32,
                backup.header.partition_entry_array_crc32))

        if primary.header.num_partition_entries != backup.header.num_partition_entries:
            mismatches.append("num_partition_entries: primary={} backup={}".format(
                primary.header.num_partition_entries,
                backup.header.num_partition_entries))

        if primary.header.size_of_partition_entry != backup.header.size_of_partition_entry:
            mismatches.append("size_of_partition_entry: primary={} backup={}".format(
                primary.header.size_of_partition_entry,
                backup.header.size_of_partition_entry))

        if len(mismatches) > 0:
            err_msg_lines = ["GPT primary/backup header disagreement."]
            for mismatch in mismatches:
                err_msg_lines.append(mismatch)
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg, details=list(mismatches))

        return
