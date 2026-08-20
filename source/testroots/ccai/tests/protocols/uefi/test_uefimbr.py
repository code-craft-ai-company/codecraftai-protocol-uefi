"""
    .. module:: test_uefimbr
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Unit tests for the pure-Python UEFI legacy / protective MBR
               parser in :mod:`ccai.protocols.uefi.uefimbr`.  Covers a
               classic-MBR round-trip, the :meth:`MasterBootRecord.is_protective`
               classifier against a UEFI protective MBR and a classic MBR,
               and the missing-signature failure path.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Code Craft AI LLC"
__credits__ = []


import unittest

from ccai.protocols.uefi.uefiexceptions import UefiError
from ccai.protocols.uefi.uefimbr import (
    MBR_BOOT_CODE_SIZE,
    MBR_PROTECTIVE_OS_TYPE,
    MBR_SECTOR_SIZE,
    MBR_SIGNATURE,
    MasterBootRecord,
    MbrPartitionEntry,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _zero_record() -> MbrPartitionEntry:
    """
        Return a zero-filled :class:`MbrPartitionEntry`.
    """
    rtnval = MbrPartitionEntry(
        boot_indicator=0x00,
        starting_chs=b"\x00\x00\x00",
        os_type=0x00,
        ending_chs=b"\x00\x00\x00",
        starting_lba=0,
        size_in_lba=0,
    )
    return rtnval


def _classic_mbr() -> MasterBootRecord:
    """
        Build a classic MBR with one active DOS partition and three zero
        records.
    """
    active_record = MbrPartitionEntry(
        boot_indicator=0x80,
        starting_chs=b"\x00\x02\x00",
        os_type=0x83,  # Linux native
        ending_chs=b"\xFE\xFF\xFF",
        starting_lba=2048,
        size_in_lba=100000,
    )

    partition_entries = (
        active_record,
        _zero_record(),
        _zero_record(),
        _zero_record(),
    )

    rtnval = MasterBootRecord(
        boot_code=b"\xEB\xFE" + b"\x00" * (MBR_BOOT_CODE_SIZE - 2),
        disk_signature=0xDEADBEEF,
        reserved=0x0000,
        partition_entries=partition_entries,
        signature=MBR_SIGNATURE,
    )
    return rtnval


def _protective_mbr(protective_size_in_lba: int = 0x7FFFFFFF) -> MasterBootRecord:
    """
        Build a UEFI protective MBR with one ``0xEE`` record spanning the
        whole disk and three zero records.
    """
    protective_record = MbrPartitionEntry(
        boot_indicator=0x00,
        starting_chs=b"\x00\x02\x00",
        os_type=MBR_PROTECTIVE_OS_TYPE,
        ending_chs=b"\xFF\xFF\xFF",
        starting_lba=1,
        size_in_lba=protective_size_in_lba,
    )

    partition_entries = (
        protective_record,
        _zero_record(),
        _zero_record(),
        _zero_record(),
    )

    rtnval = MasterBootRecord(
        boot_code=b"\x00" * MBR_BOOT_CODE_SIZE,
        disk_signature=0x00000000,
        reserved=0x0000,
        partition_entries=partition_entries,
        signature=MBR_SIGNATURE,
    )
    return rtnval


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------

class MasterBootRecordRoundTripTestCase(unittest.TestCase):
    """
        Verify classic and protective MBRs round-trip through
        :meth:`MasterBootRecord.to_bytes` / :meth:`MasterBootRecord.from_bytes`.
    """

    def test_classic_round_trip(self) -> None:
        classic = _classic_mbr()

        serialized = classic.to_bytes()
        self.assertEqual(len(serialized), MBR_SECTOR_SIZE)

        round_tripped = MasterBootRecord.from_bytes(lba0=serialized)
        self.assertEqual(round_tripped, classic)
        return

    def test_protective_round_trip(self) -> None:
        protective = _protective_mbr()

        serialized = protective.to_bytes()
        self.assertEqual(len(serialized), MBR_SECTOR_SIZE)

        round_tripped = MasterBootRecord.from_bytes(lba0=serialized)
        self.assertEqual(round_tripped, protective)
        return


# ---------------------------------------------------------------------------
# is_protective classifier
# ---------------------------------------------------------------------------

class IsProtectiveTestCase(unittest.TestCase):
    """
        Verify :meth:`MasterBootRecord.is_protective`.
    """

    def test_protective_mbr_is_protective(self) -> None:
        protective = _protective_mbr()
        self.assertTrue(protective.is_protective())
        return

    def test_classic_mbr_is_not_protective(self) -> None:
        classic = _classic_mbr()
        self.assertFalse(classic.is_protective())
        return

    def test_protective_with_wrong_starting_lba_is_not_protective(self) -> None:
        #  an ``0xEE`` record that does not start at LBA 1 does not count
        #  as a valid protective MBR per UEFI section 5.2.3.
        odd_record = MbrPartitionEntry(
            boot_indicator=0x00,
            starting_chs=b"\x00\x02\x00",
            os_type=MBR_PROTECTIVE_OS_TYPE,
            ending_chs=b"\xFF\xFF\xFF",
            starting_lba=2,
            size_in_lba=100,
        )
        partition_entries = (
            odd_record,
            _zero_record(),
            _zero_record(),
            _zero_record(),
        )
        record = MasterBootRecord(
            boot_code=b"\x00" * MBR_BOOT_CODE_SIZE,
            disk_signature=0x00000000,
            reserved=0x0000,
            partition_entries=partition_entries,
            signature=MBR_SIGNATURE,
        )
        self.assertFalse(record.is_protective())
        return

    def test_protective_with_extra_nonzero_record_is_not_protective(self) -> None:
        protective_record = MbrPartitionEntry(
            boot_indicator=0x00,
            starting_chs=b"\x00\x02\x00",
            os_type=MBR_PROTECTIVE_OS_TYPE,
            ending_chs=b"\xFF\xFF\xFF",
            starting_lba=1,
            size_in_lba=1000,
        )
        other_record = MbrPartitionEntry(
            boot_indicator=0x00,
            starting_chs=b"\x00\x00\x00",
            os_type=0x83,
            ending_chs=b"\x00\x00\x00",
            starting_lba=2048,
            size_in_lba=100,
        )
        partition_entries = (
            protective_record,
            other_record,
            _zero_record(),
            _zero_record(),
        )
        record = MasterBootRecord(
            boot_code=b"\x00" * MBR_BOOT_CODE_SIZE,
            disk_signature=0x00000000,
            reserved=0x0000,
            partition_entries=partition_entries,
            signature=MBR_SIGNATURE,
        )
        self.assertFalse(record.is_protective())
        return


# ---------------------------------------------------------------------------
# Signature validation
# ---------------------------------------------------------------------------

class SignatureValidationTestCase(unittest.TestCase):
    """
        Verify that :meth:`MasterBootRecord.from_bytes` raises
        :class:`UefiError` when the trailing ``0xAA55`` signature is
        missing.
    """

    def test_missing_signature_raises(self) -> None:
        classic = _classic_mbr()
        serialized = bytearray(classic.to_bytes())
        serialized[510:512] = b"\x00\x00"

        with self.assertRaises(UefiError) as caught:
            MasterBootRecord.from_bytes(lba0=bytes(serialized))

        self.assertIn("signature", str(caught.exception).lower())
        return

    def test_wrong_length_raises(self) -> None:
        short_buffer = b"\x00" * 256
        with self.assertRaises(UefiError):
            MasterBootRecord.from_bytes(lba0=short_buffer)
        return


if __name__ == "__main__":
    unittest.main()
