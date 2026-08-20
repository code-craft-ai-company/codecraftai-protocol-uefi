"""
    .. module:: test_uefigpt
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Unit tests for the pure-Python UEFI GPT parser / builder in
               :mod:`ccai.protocols.uefi.uefigpt`.  Every test builds a
               synthetic in-memory GPT disk image and validates decoding,
               re-encoding round-trip, CRC-32 validation, primary/backup
               pair validation, and the well-known partition-type GUID
               registry.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Code Craft AI LLC"
__credits__ = []


import dataclasses
import unittest
import uuid

from ccai.protocols.uefi.ueficonst import guid_to_bytes
from ccai.protocols.uefi.uefiexceptions import UefiError
from ccai.protocols.uefi.uefihelpers import crc32_uefi
from ccai.protocols.uefi.uefimbr import (
    MBR_PARTITION_COUNT,
    MBR_PROTECTIVE_OS_TYPE,
    MBR_SECTOR_SIZE,
    MBR_SIGNATURE,
    MasterBootRecord,
    MbrPartitionEntry,
)
from ccai.protocols.uefi.uefigpt import (
    APPLE_APFS_PARTITION_GUID,
    APPLE_HFS_PARTITION_GUID,
    CHROMEOS_KERNEL_PARTITION_GUID,
    CHROMEOS_ROOTFS_PARTITION_GUID,
    DEFAULT_SECTOR_SIZE,
    EFI_SYSTEM_PARTITION_GUID,
    GPT_HEADER_REVISION,
    GPT_HEADER_SIGNATURE,
    GPT_HEADER_SIZE,
    GPT_PARTITION_ENTRY_SIZE,
    GptDisk,
    GptHeader,
    GptPartitionEntry,
    LINUX_FILESYSTEM_DATA_PARTITION_GUID,
    LINUX_LVM_PARTITION_GUID,
    LINUX_SWAP_PARTITION_GUID,
    MICROSOFT_BASIC_DATA_PARTITION_GUID,
    MICROSOFT_RESERVED_PARTITION_GUID,
    UNUSED_PARTITION_GUID,
)


# ---------------------------------------------------------------------------
# Synthetic-image helpers
# ---------------------------------------------------------------------------


def _build_protective_mbr(sector_size: int, disk_size_lba: int) -> bytes:
    """
        Build a UEFI protective MBR sector of ``sector_size`` bytes for a
        disk that is ``disk_size_lba`` logical blocks long.

        :param sector_size: The logical block size in bytes.
        :param disk_size_lba: The total number of LBAs on the disk.

        :returns: The sector bytes for LBA 0.
    """
    protective_size_in_lba = min(disk_size_lba - 1, 0xFFFFFFFF)

    protective_record = MbrPartitionEntry(
        boot_indicator=0x00,
        starting_chs=b"\x00\x02\x00",
        os_type=MBR_PROTECTIVE_OS_TYPE,
        ending_chs=b"\xFF\xFF\xFF",
        starting_lba=1,
        size_in_lba=protective_size_in_lba,
    )
    empty_record = MbrPartitionEntry(
        boot_indicator=0x00,
        starting_chs=b"\x00\x00\x00",
        os_type=0x00,
        ending_chs=b"\x00\x00\x00",
        starting_lba=0,
        size_in_lba=0,
    )

    partition_entries = (protective_record, empty_record, empty_record, empty_record)

    mbr = MasterBootRecord(
        boot_code=b"\x00" * 440,
        disk_signature=0x00000000,
        reserved=0x0000,
        partition_entries=partition_entries,
        signature=MBR_SIGNATURE,
    )

    rtnval = mbr.to_bytes()
    return rtnval


def _build_entry_array(entries: tuple, entry_size: int, entry_count: int) -> bytes:
    """
        Serialize a list of :class:`GptPartitionEntry` instances followed
        by zero-filled unused entries up to ``entry_count``.

        :param entries: The real partition entries.
        :param entry_size: The size of one entry on disk.
        :param entry_count: The total number of entries to emit.

        :returns: The byte array.
    """
    buffer = bytearray()
    for entry in entries:
        entry_bytes = entry.to_bytes()
        entry_length = len(entry_bytes)
        padded = entry_bytes + b"\x00" * (entry_size - entry_length)
        buffer.extend(padded)

    remaining = entry_count - len(entries)
    for _ in range(remaining):
        buffer.extend(b"\x00" * entry_size)

    rtnval = bytes(buffer)
    return rtnval


def _build_header(
    my_lba: int,
    alternate_lba: int,
    first_usable_lba: int,
    last_usable_lba: int,
    partition_entry_lba: int,
    disk_guid: uuid.UUID,
    entry_array_bytes: bytes,
    entry_count: int,
    entry_size: int,
) -> GptHeader:
    """
        Build a :class:`GptHeader` whose CRC fields are recomputed over
        the supplied fields.
    """
    array_crc = crc32_uefi(entry_array_bytes)

    rtnval = GptHeader(
        signature=GPT_HEADER_SIGNATURE,
        revision=GPT_HEADER_REVISION,
        header_size=GPT_HEADER_SIZE,
        header_crc32=0,
        reserved=0,
        my_lba=my_lba,
        alternate_lba=alternate_lba,
        first_usable_lba=first_usable_lba,
        last_usable_lba=last_usable_lba,
        disk_guid=disk_guid,
        partition_entry_lba=partition_entry_lba,
        num_partition_entries=entry_count,
        size_of_partition_entry=entry_size,
        partition_entry_array_crc32=array_crc,
    )
    return rtnval


def _build_synthetic_gpt_image(
    disk_size_lba: int = 2048,
    sector_size: int = DEFAULT_SECTOR_SIZE,
    entry_count: int = 128,
    entry_size: int = GPT_PARTITION_ENTRY_SIZE,
) -> tuple:
    """
        Build a full synthetic in-memory GPT disk image of
        ``disk_size_lba`` sectors.  Returns a 3-tuple:
        ``(image_bytes, disk_guid, entries)``.
    """
    disk_guid = uuid.UUID("11111111-2222-3333-4444-555555555555")

    esp_entry = GptPartitionEntry(
        partition_type_guid=EFI_SYSTEM_PARTITION_GUID,
        unique_partition_guid=uuid.UUID("AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE"),
        starting_lba=2048 // sector_size + 34 if False else 34,
        ending_lba=133,
        attributes=0,
        partition_name="EFI System",
    )
    data_entry = GptPartitionEntry(
        partition_type_guid=LINUX_FILESYSTEM_DATA_PARTITION_GUID,
        unique_partition_guid=uuid.UUID("11112222-3333-4444-5555-666677778888"),
        starting_lba=134,
        ending_lba=1000,
        attributes=0,
        partition_name="Root \u00b5",
    )

    entries = (esp_entry, data_entry)
    primary_array_bytes = _build_entry_array(
        entries=entries,
        entry_size=entry_size,
        entry_count=entry_count,
    )

    primary_header = _build_header(
        my_lba=1,
        alternate_lba=disk_size_lba - 1,
        first_usable_lba=34,
        last_usable_lba=disk_size_lba - 34,
        partition_entry_lba=2,
        disk_guid=disk_guid,
        entry_array_bytes=primary_array_bytes,
        entry_count=entry_count,
        entry_size=entry_size,
    )

    # Backup header entry-array sits just before the backup header.
    entries_bytes_len = entry_count * entry_size
    entries_lba_span = (entries_bytes_len + sector_size - 1) // sector_size
    backup_entries_lba = (disk_size_lba - 1) - entries_lba_span

    backup_header = _build_header(
        my_lba=disk_size_lba - 1,
        alternate_lba=1,
        first_usable_lba=34,
        last_usable_lba=disk_size_lba - 34,
        partition_entry_lba=backup_entries_lba,
        disk_guid=disk_guid,
        entry_array_bytes=primary_array_bytes,
        entry_count=entry_count,
        entry_size=entry_size,
    )

    image = bytearray(disk_size_lba * sector_size)

    mbr_sector = _build_protective_mbr(
        sector_size=sector_size,
        disk_size_lba=disk_size_lba,
    )
    image[0:sector_size] = mbr_sector

    primary_header_bytes = primary_header.to_bytes()
    image[sector_size:sector_size + GPT_HEADER_SIZE] = primary_header_bytes

    array_offset = 2 * sector_size
    image[array_offset:array_offset + entries_bytes_len] = primary_array_bytes

    backup_array_offset = backup_entries_lba * sector_size
    image[backup_array_offset:backup_array_offset + entries_bytes_len] = primary_array_bytes

    backup_header_bytes = backup_header.to_bytes()
    backup_header_offset = (disk_size_lba - 1) * sector_size
    image[backup_header_offset:backup_header_offset + GPT_HEADER_SIZE] = backup_header_bytes

    rtnval = (bytes(image), disk_guid, entries)
    return rtnval


# ---------------------------------------------------------------------------
# Partition-type GUID registry
# ---------------------------------------------------------------------------

class PartitionTypeGuidTestCase(unittest.TestCase):
    """
        Verify every well-known partition-type GUID constant matches its
        spec-defined canonical string value.
    """

    def test_unused_partition(self) -> None:
        expected = uuid.UUID("00000000-0000-0000-0000-000000000000")
        self.assertEqual(UNUSED_PARTITION_GUID, expected)
        return

    def test_efi_system_partition(self) -> None:
        expected = uuid.UUID("C12A7328-F81F-11D2-BA4B-00A0C93EC93B")
        self.assertEqual(EFI_SYSTEM_PARTITION_GUID, expected)
        return

    def test_microsoft_basic_data(self) -> None:
        expected = uuid.UUID("EBD0A0A2-B9E5-4433-87C0-68B6B72699C7")
        self.assertEqual(MICROSOFT_BASIC_DATA_PARTITION_GUID, expected)
        return

    def test_microsoft_reserved(self) -> None:
        expected = uuid.UUID("E3C9E316-0B5C-4DB8-817D-F92DF00215AE")
        self.assertEqual(MICROSOFT_RESERVED_PARTITION_GUID, expected)
        return

    def test_linux_filesystem_data(self) -> None:
        expected = uuid.UUID("0FC63DAF-8483-4772-8E79-3D69D8477DE4")
        self.assertEqual(LINUX_FILESYSTEM_DATA_PARTITION_GUID, expected)
        return

    def test_linux_swap(self) -> None:
        expected = uuid.UUID("0657FD6D-A4AB-43C4-84E5-0933C84B4F4F")
        self.assertEqual(LINUX_SWAP_PARTITION_GUID, expected)
        return

    def test_linux_lvm(self) -> None:
        expected = uuid.UUID("E6D6D379-F507-44C2-A23C-238F2A3DF928")
        self.assertEqual(LINUX_LVM_PARTITION_GUID, expected)
        return

    def test_apple_hfs(self) -> None:
        expected = uuid.UUID("48465300-0000-11AA-AA11-00306543ECAC")
        self.assertEqual(APPLE_HFS_PARTITION_GUID, expected)
        return

    def test_apple_apfs(self) -> None:
        expected = uuid.UUID("7C3457EF-0000-11AA-AA11-00306543ECAC")
        self.assertEqual(APPLE_APFS_PARTITION_GUID, expected)
        return

    def test_chromeos_kernel(self) -> None:
        expected = uuid.UUID("FE3A2A5D-4F32-41A7-B725-ACCC3285A309")
        self.assertEqual(CHROMEOS_KERNEL_PARTITION_GUID, expected)
        return

    def test_chromeos_rootfs(self) -> None:
        expected = uuid.UUID("3CB8E202-3B7E-47DD-8A3C-7FF2A13CFCEC")
        self.assertEqual(CHROMEOS_ROOTFS_PARTITION_GUID, expected)
        return


# ---------------------------------------------------------------------------
# GptPartitionEntry round-trip
# ---------------------------------------------------------------------------

class GptPartitionEntryRoundTripTestCase(unittest.TestCase):
    """
        Verify :class:`GptPartitionEntry` decodes and re-encodes losslessly.
    """

    def test_round_trip_ascii_name(self) -> None:
        original = GptPartitionEntry(
            partition_type_guid=EFI_SYSTEM_PARTITION_GUID,
            unique_partition_guid=uuid.UUID("AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE"),
            starting_lba=34,
            ending_lba=133,
            attributes=0x0000000000000001,
            partition_name="EFI System",
        )

        serialized = original.to_bytes()
        self.assertEqual(len(serialized), GPT_PARTITION_ENTRY_SIZE)

        round_tripped = GptPartitionEntry.from_bytes(entry_bytes=serialized)
        self.assertEqual(round_tripped, original)
        return

    def test_round_trip_non_ascii_name(self) -> None:
        original = GptPartitionEntry(
            partition_type_guid=LINUX_FILESYSTEM_DATA_PARTITION_GUID,
            unique_partition_guid=uuid.UUID("11112222-3333-4444-5555-666677778888"),
            starting_lba=134,
            ending_lba=1000,
            attributes=0,
            partition_name="Root \u00b5",
        )

        serialized = original.to_bytes()
        round_tripped = GptPartitionEntry.from_bytes(entry_bytes=serialized)
        self.assertEqual(round_tripped, original)
        return

    def test_unused_entry_round_trip(self) -> None:
        unused = GptPartitionEntry(
            partition_type_guid=UNUSED_PARTITION_GUID,
            unique_partition_guid=UNUSED_PARTITION_GUID,
            starting_lba=0,
            ending_lba=0,
            attributes=0,
            partition_name="",
        )

        serialized = unused.to_bytes()
        self.assertEqual(serialized, b"\x00" * GPT_PARTITION_ENTRY_SIZE)

        round_tripped = GptPartitionEntry.from_bytes(entry_bytes=serialized)
        self.assertEqual(round_tripped, unused)
        return


# ---------------------------------------------------------------------------
# GptHeader decode / encode
# ---------------------------------------------------------------------------

class GptHeaderTestCase(unittest.TestCase):
    """
        Verify :class:`GptHeader.from_bytes` / :class:`GptHeader.to_bytes`.
    """

    def test_header_round_trip(self) -> None:
        image, disk_guid, _entries = _build_synthetic_gpt_image()
        lba1 = image[DEFAULT_SECTOR_SIZE:2 * DEFAULT_SECTOR_SIZE]

        header = GptHeader.from_bytes(lba1=lba1)
        self.assertEqual(header.signature, GPT_HEADER_SIGNATURE)
        self.assertEqual(header.revision, GPT_HEADER_REVISION)
        self.assertEqual(header.header_size, GPT_HEADER_SIZE)
        self.assertEqual(header.disk_guid, disk_guid)
        self.assertEqual(header.my_lba, 1)

        re_encoded = header.to_bytes()
        self.assertEqual(len(re_encoded), GPT_HEADER_SIZE)

        re_parsed = GptHeader.from_bytes(lba1=re_encoded)
        self.assertEqual(re_parsed, header)
        return

    def test_bad_signature_raises(self) -> None:
        image, _disk_guid, _entries = _build_synthetic_gpt_image()
        lba1 = bytearray(image[DEFAULT_SECTOR_SIZE:2 * DEFAULT_SECTOR_SIZE])
        lba1[0:8] = b"BADSIG!!"

        with self.assertRaises(UefiError) as caught:
            GptHeader.from_bytes(lba1=bytes(lba1))

        self.assertIn("signature", str(caught.exception).lower())
        return

    def test_corrupted_header_crc_raises_with_details(self) -> None:
        image, _disk_guid, _entries = _build_synthetic_gpt_image()
        lba1 = bytearray(image[DEFAULT_SECTOR_SIZE:2 * DEFAULT_SECTOR_SIZE])
        #  flip a byte inside the header that is NOT the CRC field (bytes 16..20).
        #  my_lba starts at offset 24 - flip a single bit there.
        lba1[24] = lba1[24] ^ 0x01

        with self.assertRaises(UefiError) as caught:
            GptHeader.from_bytes(lba1=bytes(lba1))

        details = caught.exception.details
        self.assertGreater(len(details), 0)

        joined_details = "\n".join(details)
        self.assertIn("COMPUTED:", joined_details)
        self.assertIn("STORED:", joined_details)
        return

    def test_header_size_too_small_raises(self) -> None:
        image, _disk_guid, _entries = _build_synthetic_gpt_image()
        lba1 = bytearray(image[DEFAULT_SECTOR_SIZE:2 * DEFAULT_SECTOR_SIZE])
        #  overwrite HeaderSize (offset 12) with a value smaller than 92
        lba1[12:16] = (50).to_bytes(4, "little")

        with self.assertRaises(UefiError):
            GptHeader.from_bytes(lba1=bytes(lba1))
        return


# ---------------------------------------------------------------------------
# GptDisk decode
# ---------------------------------------------------------------------------

class GptDiskTestCase(unittest.TestCase):
    """
        Verify :class:`GptDisk.from_bytes` and primary/backup validation.
    """

    def test_from_bytes_parses_synthetic_image(self) -> None:
        image, disk_guid, entries = _build_synthetic_gpt_image()

        disk = GptDisk.from_bytes(image_bytes=image)
        self.assertEqual(disk.header.disk_guid, disk_guid)
        self.assertEqual(len(disk.entries), 128)

        self.assertEqual(disk.entries[0].partition_type_guid, EFI_SYSTEM_PARTITION_GUID)
        self.assertEqual(disk.entries[0].partition_name, "EFI System")
        self.assertEqual(disk.entries[1].partition_type_guid, LINUX_FILESYSTEM_DATA_PARTITION_GUID)
        self.assertEqual(disk.entries[1].partition_name, "Root \u00b5")

        for tail_index in range(2, 128):
            self.assertEqual(disk.entries[tail_index].partition_type_guid, UNUSED_PARTITION_GUID)
        return

    def test_corrupted_array_crc_raises_with_details(self) -> None:
        image, _disk_guid, _entries = _build_synthetic_gpt_image()
        corrupted = bytearray(image)
        #  flip a byte in the middle of the partition entry array (LBA 2..)
        corrupted[2 * DEFAULT_SECTOR_SIZE] = corrupted[2 * DEFAULT_SECTOR_SIZE] ^ 0x01

        with self.assertRaises(UefiError) as caught:
            GptDisk.from_bytes(image_bytes=bytes(corrupted))

        details = caught.exception.details
        self.assertGreater(len(details), 0)

        joined_details = "\n".join(details)
        self.assertIn("COMPUTED:", joined_details)
        self.assertIn("STORED:", joined_details)
        return

    def test_validate_pair_happy_path(self) -> None:
        image, _disk_guid, _entries = _build_synthetic_gpt_image()
        disk_size_lba = len(image) // DEFAULT_SECTOR_SIZE

        primary_disk = GptDisk.from_bytes(image_bytes=image)

        backup_header_offset = (disk_size_lba - 1) * DEFAULT_SECTOR_SIZE
        backup_lba1 = image[backup_header_offset:backup_header_offset + DEFAULT_SECTOR_SIZE]

        #  pad the backup lba1 buffer to a full sector for from_bytes consistency
        if len(backup_lba1) < DEFAULT_SECTOR_SIZE:
            backup_lba1 = backup_lba1 + b"\x00" * (DEFAULT_SECTOR_SIZE - len(backup_lba1))

        backup_header = GptHeader.from_bytes(lba1=backup_lba1)
        backup_disk = GptDisk(header=backup_header, entries=primary_disk.entries)

        # validate_pair returns None on success
        result = GptDisk.validate_pair(primary=primary_disk, backup=backup_disk)
        self.assertIsNone(result)
        return

    def test_validate_pair_detects_guid_mismatch(self) -> None:
        image, _disk_guid, _entries = _build_synthetic_gpt_image()
        primary_disk = GptDisk.from_bytes(image_bytes=image)

        mutated_header = dataclasses.replace(
            primary_disk.header,
            my_lba=primary_disk.header.alternate_lba,
            alternate_lba=primary_disk.header.my_lba,
            disk_guid=uuid.UUID("99999999-9999-9999-9999-999999999999"),
        )
        backup_disk = GptDisk(header=mutated_header, entries=primary_disk.entries)

        with self.assertRaises(UefiError) as caught:
            GptDisk.validate_pair(primary=primary_disk, backup=backup_disk)

        joined_details = "\n".join(caught.exception.details)
        self.assertIn("disk_guid", joined_details)
        return

    def test_validate_pair_detects_lba_mismatch(self) -> None:
        image, _disk_guid, _entries = _build_synthetic_gpt_image()
        primary_disk = GptDisk.from_bytes(image_bytes=image)

        #  backup that says my_lba=1 (contradicting primary.alternate_lba)
        mutated_header = dataclasses.replace(
            primary_disk.header,
            my_lba=1,
            alternate_lba=1,
        )
        backup_disk = GptDisk(header=mutated_header, entries=primary_disk.entries)

        with self.assertRaises(UefiError) as caught:
            GptDisk.validate_pair(primary=primary_disk, backup=backup_disk)

        joined_details = "\n".join(caught.exception.details)
        self.assertTrue("my_lba" in joined_details or "alternate_lba" in joined_details)
        return


if __name__ == "__main__":
    unittest.main()
