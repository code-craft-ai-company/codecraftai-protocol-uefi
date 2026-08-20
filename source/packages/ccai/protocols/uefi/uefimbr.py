"""
    .. module:: uefimbr
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Pure-Python parser / builder for the legacy Master Boot
               Record and the UEFI "protective MBR" structures defined in
               UEFI specification section 5.2.  Provides
               :class:`MbrPartitionEntry` and :class:`MasterBootRecord`
               frozen dataclasses together with decode / encode helpers
               and a :meth:`MasterBootRecord.is_protective` classifier that
               implements the UEFI section 5.2.3 rules.  This module
               performs no I/O: callers pass in the raw bytes of LBA 0.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Code Craft AI LLC"
__credits__ = []


import os
import struct

from dataclasses import dataclass
from typing import Final, List, Tuple

from ccai.protocols.uefi.uefiexceptions import UefiError


__all__ = [
    "MBR_BOOT_CODE_SIZE",
    "MBR_PARTITION_COUNT",
    "MBR_PARTITION_ENTRY_SIZE",
    "MBR_PROTECTIVE_OS_TYPE",
    "MBR_SECTOR_SIZE",
    "MBR_SIGNATURE",
    "MasterBootRecord",
    "MbrPartitionEntry",
]


# ---------------------------------------------------------------------------
# Constants (UEFI specification section 5.2)
# ---------------------------------------------------------------------------

#: The nominal LBA 0 sector size in bytes.
MBR_SECTOR_SIZE: Final[int] = 512

#: Number of bytes of ``BootCode`` at the head of the MBR (UEFI section
#: 5.2.1 Table 5-1 / 5.2.2 Table 5-3).
MBR_BOOT_CODE_SIZE: Final[int] = 440

#: Size of a single 16-byte MBR partition record.
MBR_PARTITION_ENTRY_SIZE: Final[int] = 16

#: Number of partition records in the legacy MBR partition table.
MBR_PARTITION_COUNT: Final[int] = 4

#: The required ``Signature`` trailer value (little-endian ``0x55 0xAA`` ->
#: value ``0xAA55`` when read as ``UINT16 LE``).
MBR_SIGNATURE: Final[int] = 0xAA55

#: The ``OSType`` byte value that identifies a UEFI protective partition
#: record per UEFI section 5.2.3.
MBR_PROTECTIVE_OS_TYPE: Final[int] = 0xEE


# ---------------------------------------------------------------------------
# Byte-layout helpers
# ---------------------------------------------------------------------------
#
# ``_MBR_PREAMBLE_STRUCT`` decodes the 6 bytes that sit between the boot
# code and the partition table: a ``UINT32`` disk signature followed by
# a 2-byte reserved field.  ``_MBR_PARTITION_ENTRY_STRUCT`` decodes a
# single 16-byte record.
# ---------------------------------------------------------------------------

_MBR_PREAMBLE_STRUCT: Final[struct.Struct] = struct.Struct("<IH")
_MBR_PARTITION_ENTRY_STRUCT: Final[struct.Struct] = struct.Struct("<B3sB3sII")
_MBR_SIGNATURE_STRUCT: Final[struct.Struct] = struct.Struct("<H")


# ---------------------------------------------------------------------------
# MbrPartitionEntry dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MbrPartitionEntry:
    """
        Value object representing a single 16-byte MBR partition record.

        :param boot_indicator: ``BootIndicator`` byte.  ``0x80`` marks the
                               record as active; ``0x00`` marks it inactive.
        :param starting_chs: Raw 3-byte ``StartingCHS`` field.
        :param os_type: ``OSType`` byte (``0xEE`` for a UEFI protective
                        record).
        :param ending_chs: Raw 3-byte ``EndingCHS`` field.
        :param starting_lba: ``StartingLBA`` ``UINT32`` little-endian.
        :param size_in_lba: ``SizeInLBA`` ``UINT32`` little-endian.
    """

    boot_indicator: int
    starting_chs: bytes
    os_type: int
    ending_chs: bytes
    starting_lba: int
    size_in_lba: int

    @classmethod
    def from_bytes(cls, entry_bytes: bytes) -> "MbrPartitionEntry":
        """
            Decode a single 16-byte MBR partition record.

            :param entry_bytes: A buffer of length
                                :data:`MBR_PARTITION_ENTRY_SIZE`.

            :returns: The decoded :class:`MbrPartitionEntry`.
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

        if buffer_length != MBR_PARTITION_ENTRY_SIZE:
            err_msg_lines = [
                "MBR partition record length mismatch.",
                "EXPECTED: {} bytes".format(MBR_PARTITION_ENTRY_SIZE),
                "OBSERVED: {} bytes".format(buffer_length),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg)

        unpacked = _MBR_PARTITION_ENTRY_STRUCT.unpack_from(buffer, 0)
        boot_indicator = unpacked[0]
        starting_chs = unpacked[1]
        os_type = unpacked[2]
        ending_chs = unpacked[3]
        starting_lba = unpacked[4]
        size_in_lba = unpacked[5]

        rtnval = cls(
            boot_indicator=boot_indicator,
            starting_chs=starting_chs,
            os_type=os_type,
            ending_chs=ending_chs,
            starting_lba=starting_lba,
            size_in_lba=size_in_lba,
        )
        return rtnval

    def to_bytes(self) -> bytes:
        """
            Serialize the partition record into its 16-byte on-disk
            representation.

            :returns: A ``bytes`` object of length
                      :data:`MBR_PARTITION_ENTRY_SIZE`.
            :raises: UefiError
        """
        starting_chs_length = len(self.starting_chs)
        ending_chs_length = len(self.ending_chs)

        if starting_chs_length != 3:
            err_msg_lines = [
                "MBR starting_chs must be 3 bytes.",
                "OBSERVED: {} bytes".format(starting_chs_length),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg)

        if ending_chs_length != 3:
            err_msg_lines = [
                "MBR ending_chs must be 3 bytes.",
                "OBSERVED: {} bytes".format(ending_chs_length),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg)

        rtnval = _MBR_PARTITION_ENTRY_STRUCT.pack(
            self.boot_indicator,
            bytes(self.starting_chs),
            self.os_type,
            bytes(self.ending_chs),
            self.starting_lba,
            self.size_in_lba,
        )
        return rtnval


# ---------------------------------------------------------------------------
# MasterBootRecord dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MasterBootRecord:
    """
        Value object representing an entire 512-byte LBA 0 sector of a
        legacy MBR or UEFI protective MBR per UEFI section 5.2.

        :param boot_code: Raw :data:`MBR_BOOT_CODE_SIZE`-byte boot-code
                          area.
        :param disk_signature: ``UINT32`` ``UniqueMBRDiskSignature``.
        :param reserved: ``UINT16`` reserved field (typically 0).
        :param partition_entries: Tuple of exactly
                                  :data:`MBR_PARTITION_COUNT`
                                  :class:`MbrPartitionEntry` records.
        :param signature: ``UINT16`` trailer; should equal
                          :data:`MBR_SIGNATURE`.
    """

    boot_code: bytes
    disk_signature: int
    reserved: int
    partition_entries: Tuple[MbrPartitionEntry, ...]
    signature: int

    @classmethod
    def from_bytes(cls, lba0: bytes) -> "MasterBootRecord":
        """
            Decode a full 512-byte LBA 0 buffer.

            :param lba0: The raw bytes of LBA 0.  Must be exactly
                         :data:`MBR_SECTOR_SIZE` bytes long.

            :returns: A :class:`MasterBootRecord` populated from ``lba0``.
            :raises: UefiError
        """
        if not isinstance(lba0, (bytes, bytearray, memoryview)):
            err_msg_lines = [
                "'lba0' must be a bytes-like object.",
                "GOT: type={}".format(type(lba0).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg)

        buffer = bytes(lba0)
        buffer_length = len(buffer)

        if buffer_length != MBR_SECTOR_SIZE:
            err_msg_lines = [
                "MBR LBA0 buffer length mismatch.",
                "EXPECTED: {} bytes".format(MBR_SECTOR_SIZE),
                "OBSERVED: {} bytes".format(buffer_length),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg)

        boot_code = buffer[0:MBR_BOOT_CODE_SIZE]

        preamble_offset = MBR_BOOT_CODE_SIZE
        preamble_bytes = buffer[preamble_offset:preamble_offset + 6]
        preamble_unpacked = _MBR_PREAMBLE_STRUCT.unpack_from(preamble_bytes, 0)
        disk_signature = preamble_unpacked[0]
        reserved = preamble_unpacked[1]

        partition_table_offset = 446
        decoded_entries: List[MbrPartitionEntry] = []
        for entry_index in range(MBR_PARTITION_COUNT):
            entry_start = partition_table_offset + (entry_index * MBR_PARTITION_ENTRY_SIZE)
            entry_end = entry_start + MBR_PARTITION_ENTRY_SIZE
            entry_bytes = buffer[entry_start:entry_end]
            decoded = MbrPartitionEntry.from_bytes(entry_bytes=entry_bytes)
            decoded_entries.append(decoded)

        signature_bytes = buffer[510:512]
        signature_unpacked = _MBR_SIGNATURE_STRUCT.unpack_from(signature_bytes, 0)
        signature = signature_unpacked[0]

        if signature != MBR_SIGNATURE:
            err_msg_lines = [
                "MBR signature mismatch.",
                "EXPECTED: 0x{:04X}".format(MBR_SIGNATURE),
                "OBSERVED: 0x{:04X}".format(signature),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg)

        rtnval = cls(
            boot_code=boot_code,
            disk_signature=disk_signature,
            reserved=reserved,
            partition_entries=tuple(decoded_entries),
            signature=signature,
        )
        return rtnval

    def to_bytes(self) -> bytes:
        """
            Serialize the record into a :data:`MBR_SECTOR_SIZE`-byte
            buffer suitable for writing to LBA 0.

            :returns: A ``bytes`` object of length
                      :data:`MBR_SECTOR_SIZE`.
            :raises: UefiError
        """
        boot_code_length = len(self.boot_code)
        if boot_code_length != MBR_BOOT_CODE_SIZE:
            err_msg_lines = [
                "MBR boot_code length mismatch.",
                "EXPECTED: {} bytes".format(MBR_BOOT_CODE_SIZE),
                "OBSERVED: {} bytes".format(boot_code_length),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg)

        entry_count = len(self.partition_entries)
        if entry_count != MBR_PARTITION_COUNT:
            err_msg_lines = [
                "MBR partition_entries length mismatch.",
                "EXPECTED: {} records".format(MBR_PARTITION_COUNT),
                "OBSERVED: {} records".format(entry_count),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg)

        scratch = bytearray(MBR_SECTOR_SIZE)
        scratch[0:MBR_BOOT_CODE_SIZE] = bytes(self.boot_code)

        preamble_bytes = _MBR_PREAMBLE_STRUCT.pack(self.disk_signature, self.reserved)
        scratch[MBR_BOOT_CODE_SIZE:MBR_BOOT_CODE_SIZE + 6] = preamble_bytes

        partition_table_offset = 446
        for entry_index in range(MBR_PARTITION_COUNT):
            entry = self.partition_entries[entry_index]
            entry_bytes = entry.to_bytes()
            entry_start = partition_table_offset + (entry_index * MBR_PARTITION_ENTRY_SIZE)
            entry_end = entry_start + MBR_PARTITION_ENTRY_SIZE
            scratch[entry_start:entry_end] = entry_bytes

        signature_bytes = _MBR_SIGNATURE_STRUCT.pack(self.signature)
        scratch[510:512] = signature_bytes

        rtnval = bytes(scratch)
        return rtnval

    def is_protective(self) -> bool:
        """
            Classify this MBR as a UEFI protective MBR per UEFI section
            5.2.3.  The record is protective when exactly one partition
            record has ``OSType == 0xEE`` starting at LBA 1 and the
            remaining records are zero-filled (``OSType == 0``).

            :returns: ``True`` if this MBR is a protective MBR;
                      ``False`` otherwise (legacy MBR or any MBR that
                      does not satisfy the section 5.2.3 shape).
        """
        protective_count = 0
        protective_entry = None
        non_zero_other = False

        for entry in self.partition_entries:
            if entry.os_type == MBR_PROTECTIVE_OS_TYPE:
                protective_count += 1
                protective_entry = entry
            elif entry.os_type != 0:
                non_zero_other = True

        if protective_count != 1:
            rtnval = False
        elif non_zero_other is True:
            rtnval = False
        elif protective_entry is None:
            rtnval = False
        elif protective_entry.starting_lba != 1:
            rtnval = False
        elif protective_entry.size_in_lba == 0:
            rtnval = False
        else:
            rtnval = True

        return rtnval
