# Tasks — GPT / MBR Parser

- [ ] 1. Create package skeleton at
      `source/packages/ccai/protocols/uefi/gpt/`.
  - [ ] 1.1 Add `__init__.py` re-exporting `GptHeader`,
        `GptPartitionEntry`, `GptPartitionArray`,
        `GptPartitionAttributes`, `ProtectiveMbr`,
        `MbrPartitionRecord`, `LegacyMbr`, and the `partition_types`
        module namespace.
  - [ ] 1.2 Add a module-level docstring citing UEFI §5 subsections
        and naming the layer-1 dependencies.
  - [ ] 1.3 Add a dedicated companion package
        `source/packages/ccai/protocols/uefi/mbr/` for
        `ProtectiveMbr` / `LegacyMbr` if the team prefers separation;
        otherwise keep them under `gpt/` with clear submodule naming.
  - References: Requirement 5, Requirement 11.

- [ ] 2. Author `partition_types.py`.
  - [ ] 2.1 Define every named `uuid.UUID` constant from UEFI §5.3.3
        Table 22 per the design.
  - [ ] 2.2 Add a `__all__` list naming every constant.
  - [ ] 2.3 Add `test_partition_types.py` with a snapshot dict of
        expected `{name: str(uuid)}` pairs; fail on drift.
  - [ ] 2.4 Add an identity test asserting re-import returns the same
        object.
  - References: Requirement 5.

- [ ] 3. Author `gpt_attributes.py`.
  - [ ] 3.1 Define `GptPartitionAttributes(IntFlag)` with the generic
        bit-0..2 flags.
  - [ ] 3.2 Document the bit-48..63 type-specific interpretation as
        opaque (noted in the docstring).
  - [ ] 3.3 Add round-trip tests 0x00..0xFF via to/from int.
  - References: Requirement 4.

- [ ] 4. Author `gpt_entry.py` decode path.
  - [ ] 4.1 Author `GptPartitionEntry` frozen dataclass with all
        six fields.
  - [ ] 4.2 Implement `from_bytes(buf)` decoding 128 bytes.
  - [ ] 4.3 Implement `is_unused()` helper.
  - [ ] 4.4 Author `GptPartitionArray.from_bytes(array_bytes,
        entry_size, entry_count, aspects)` enforcing
        `entry_size` validity and total length.
  - [ ] 4.5 Implement UCS-2 decoding of the `PartitionName` field
        stripping null-padding.
  - [ ] 4.6 Add unit tests covering normal, unused, and
        bmp-non-ASCII-named entries.
  - References: Requirement 4.

- [ ] 5. Author `gpt_entry.py` encode path.
  - [ ] 5.1 Implement `GptPartitionEntry.to_bytes(entry_size)`.
  - [ ] 5.2 Implement `GptPartitionArray.to_bytes()`.
  - [ ] 5.3 Add round-trip tests for 128-byte and 256-byte entry
        sizes.
  - [ ] 5.4 Add a test asserting `to_bytes` of 128 unused entries
        produces 128 * `entry_size` zero bytes.
  - References: Requirement 4, Requirement 13.

- [ ] 6. Author `gpt_header.py` decode path.
  - [ ] 6.1 Author `GptHeader` frozen dataclass with all documented
        fields.
  - [ ] 6.2 Implement `from_bytes(lba0, lba1, partition_entries_bytes,
        aspects)` including signature, revision, header-size checks
        and the CRC-32 validation using `crc32_uefi` from layer 1.
  - [ ] 6.3 Implement `_validate_header_crc` and
        `_validate_partition_array_crc` helpers per design.
  - [ ] 6.4 Implement retry-pattern rejection raising `SemanticError`.
  - [ ] 6.5 Add unit tests for each failure class.
  - References: Requirement 2, Requirement 3, Requirement 11.

- [ ] 7. Author `gpt_header.py` encode / build path.
  - [ ] 7.1 Implement `GptHeader.to_bytes()` zero-padded to
        `header_size`.
  - [ ] 7.2 Implement `GptHeader.build(disk_size_lba, sector_size,
        entries, disk_guid, revision, aspects)` with CRC
        recomputation.
  - [ ] 7.3 Implement `GptHeader.backup_of(disk_size_lba)` swapping
        `MyLBA`/`AlternateLBA` and repositioning `PartitionEntryLBA`
        per §5.3.2.
  - [ ] 7.4 Add a build-then-parse round-trip test with 8 partitions.
  - References: Requirement 9, Requirement 10, Requirement 13.

- [ ] 8. Implement `validate_pair`.
  - [ ] 8.1 Author `GptHeader.validate_pair(primary, backup,
        aspects)`.
  - [ ] 8.2 Emit `UefiError` with per-field mismatch detail lines on
        disagreement.
  - [ ] 8.3 Add tests for every individual field mismatch and the
        happy path.
  - References: Requirement 6.

- [ ] 9. Author `mbr_protective.py`.
  - [ ] 9.1 Author `MbrPartitionRecord` frozen dataclass with CHS
        tuples.
  - [ ] 9.2 Author `ProtectiveMbr` frozen dataclass.
  - [ ] 9.3 Author `_chs.py` helpers for 3-byte CHS packing/unpacking.
  - [ ] 9.4 Implement `from_bytes(lba0, aspects)` with signature
        (`0x55AA`) and protective-record (`OSType=0xEE`,
        `StartingLBA=1`) validation.
  - [ ] 9.5 Implement `build(disk_size_lba, aspects)` producing a
        valid protective MBR with CHS fields clamped to
        `(0x3FF, 0xFF, 0xFE)` max per MBR conventions.
  - [ ] 9.6 Implement `to_bytes()`.
  - [ ] 9.7 Add round-trip tests with small (1GiB) and large (16TiB)
        disk sizes.
  - References: Requirement 7.

- [ ] 10. Author `mbr_legacy.py`.
  - [ ] 10.1 Author `LegacyMbr` frozen dataclass paralleling
        `ProtectiveMbr` but treating all records as legitimate.
  - [ ] 10.2 Implement `from_bytes(lba0, aspects)` with
        `BootIndicator` validation raising `UefiError` on invalid
        values.
  - [ ] 10.3 Implement `build(partitions, boot_code=b"", aspects=None)`.
  - [ ] 10.4 Implement `to_bytes()`.
  - [ ] 10.5 Add tests for happy paths, invalid boot-indicator, and a
        round-trip for a 4-primary-partition layout.
  - References: Requirement 8, Requirement 13.

- [ ] 11. Wire `crc32_uefi` dependency from layer 1.
  - [ ] 11.1 Import `crc32_uefi` eagerly at module load of
        `gpt_header.py` and `gpt_entry.py`; raise `ImportError` if
        absent.
  - [ ] 11.2 Add a test that `gpt_header.crc32_uefi is
        ccai.protocols.uefi.helpers.crc32_uefi` (identity).
  - [ ] 11.3 Add a test using the UEFI spec CRC-32 test vector to
        cross-check.
  - References: Requirement 11.

- [ ] 12. Action-pattern targeted tests.
  - [ ] 12.1 Add `test_action_patterns.py` covering `from_bytes`,
        `build`, `validate_pair` on every structure.
  - [ ] 12.2 Verify `SINGLE_CALL` and `SINGLE_CONNECTED_CALL`
        succeed.
  - [ ] 12.3 Verify `DO_UNTIL_SUCCESS`, `DO_WHILE_SUCCESS`,
        `DO_UNTIL_CONNECTION_FAILURE` raise `SemanticError`.
  - [ ] 12.4 Verify `aspects=None` resolves from
        `DEFAULT_CMD_ASPECTS`.
  - References: Requirement 1, Requirement 2.

- [ ] 13. Fixtures corpus.
  - [ ] 13.1 Capture `tests/fixtures/gpt/linux-ext4-gpt.img`,
        `windows-ntfs-gpt.img`, `macos-apfs-gpt.img`,
        `esp-fat32-gpt.img`, `legacy-mbr.img` — each a stitched
        first-34 + last-33 LBA image of at most 2 MiB.
  - [ ] 13.2 Write a sibling `.md` per fixture documenting the source
        device and the capture procedure (including the exact `dd`
        offset/size commands).
  - [ ] 13.3 Add parametrized tests parsing each fixture, calling
        `validate_pair`, and snapshotting the partition-name list.
  - [ ] 13.4 Add a CI gate that fails when a fixture file exists
        without a matching parametrized test.
  - References: Requirement 12.

- [ ] 14. Negative-corpus tests.
  - [ ] 14.1 Author fixtures under `tests/fixtures/gpt/negative/`
        containing deliberately-corrupted images (CRC bit flip,
        bad signature, bad `HeaderSize`, truncated array).
  - [ ] 14.2 Add tests asserting `UefiError` with the documented
        `details` content for each.
  - References: Requirement 3, Requirement 4, Requirement 7,
    Requirement 8.

- [ ] 15. Documentation.
  - [ ] 15.1 Author a usage guide at
        `userguide/50-05-gpt-mbr-parser.rst` covering read,
        validate-pair, and build flows.
  - [ ] 15.2 Cross-link from `AGENTS.md` Features section.
  - References: Introduction.
