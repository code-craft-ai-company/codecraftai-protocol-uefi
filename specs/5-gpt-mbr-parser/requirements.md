# Requirements — GPT / MBR Parser

## Introduction

The GPT / MBR Parser feature reads and optionally builds the disk-layout
structures defined in UEFI §5: the protective MBR (§5.2.2), the primary
and backup GPT headers (§5.3.2), the GPT Partition Entry Array (§5.3.3),
and the legacy MBR for targets that still use it.  CRC-32 validation is
performed via layer-1 helpers so the computed CRC matches
`EFI_BOOT_SERVICES.CalculateCrc32`.

The feature is pure Python — no shelling out to `sgdisk`, `parted`,
`gdisk`, or `fdisk` — so it runs anywhere the library runs, including
against captured disk images and in air-gapped CI.  Read-only usage is
the default; builder APIs are opt-in via explicit `GptHeader.build(...)`
/ `GptPartitionEntry.build(...)` / `ProtectiveMbr.build(...)` /
`LegacyMbr.build(...)` classmethods.

Partition type GUIDs from §5.3.3 Table 22 are enumerated as named
constants under `uefigpt.partition_types`.  Primary/backup header
comparison is exposed via `GptHeader.validate_pair(primary, backup)`;
mismatches raise `UefiError` with both CRCs and both headers in
`details`.

Fixtures for tests are captured from real disks and stored as small
`.img` files under `tests/fixtures/gpt/`.

## Requirements

### Requirement 1 — Aspects Presence at Public Entry Points

**User Story:** As a test author, I want every parser entry point to
accept an `aspects` override, so that parse-time resource governance is
caller-controlled even for pure-CPU operations.

#### Acceptance Criteria

1. WHEN any `from_bytes` or `validate_pair` classmethod is invoked
   THEN the system SHALL accept `aspects: Optional[UefiCmdAspects] =
   None` as the last parameter.
2. WHEN the caller passes `aspects=None` THEN the system SHALL resolve
   via `resolve_aspects(None, DEFAULT_CMD_ASPECTS)`.
3. WHEN any builder classmethod (`build`) is invoked THEN the system
   SHALL accept the same optional `aspects` parameter.
4. WHEN the resolved aspects has `monitor_delay > 0` THEN the system
   SHALL wrap the parse/build in a `MonitoredScope` named
   `gpt.<operation>`.

### Requirement 2 — Action Pattern Support

**User Story:** As a test author, I want the parser to support the
action patterns that make sense for pure-CPU work, so that my caller
code stays uniform with I/O-bound features.

#### Acceptance Criteria

1. WHEN `aspects.action_pattern == ActionPattern.SINGLE_CALL` THEN the
   parser SHALL perform exactly one parse attempt.
2. WHEN `aspects.action_pattern == ActionPattern.SINGLE_CONNECTED_CALL`
   THEN the parser SHALL behave identically to `SINGLE_CALL`.
3. IF `aspects.action_pattern in {DO_UNTIL_SUCCESS, DO_WHILE_SUCCESS,
   DO_UNTIL_CONNECTION_FAILURE}` THEN the system SHALL raise
   `SemanticError`.

### Requirement 3 — GPT Header Decode with Dual-CRC Validation

**User Story:** As a test author, I want to decode a GPT header and
have both CRC fields validated automatically, so that corrupted headers
fail fast.

#### Acceptance Criteria

1. WHEN `GptHeader.from_bytes(lba0: bytes, lba1: bytes)` is called
   with the protective MBR sector (`lba0`) and the primary GPT header
   sector (`lba1`) THEN the system SHALL decode a `GptHeader` per
   UEFI §5.3.2.
2. WHEN decoding THEN the system SHALL compute the `HeaderCRC32` over
   bytes `[0, HeaderSize)` with the `HeaderCRC32` field itself zeroed
   and compare to the stored value.
3. IF the computed `HeaderCRC32` does not match the stored
   `HeaderCRC32` THEN the system SHALL raise `UefiError` with
   `details` listing both computed and stored values in hex.
4. WHEN decoding THEN the system SHALL compute `PartitionEntryArrayCRC32`
   over the full entry array bytes and compare to the stored value.
5. IF the computed `PartitionEntryArrayCRC32` does not match the
   stored value THEN the system SHALL raise `UefiError` with
   `details` listing both CRCs in hex and the byte offset of the
   failing array.
6. WHEN `Signature` field is not the ASCII bytes `"EFI PART"` (i.e.,
   the 64-bit constant `0x5452415020494645`) THEN the system SHALL
   raise `UefiError`.
7. WHEN `Revision` is not `0x00010000` THEN the system SHALL emit a
   `WARNING` log on channel `uefi.gpt` and SHALL continue — newer
   revisions are forward-compatible unless fields change.
8. IF `HeaderSize` is less than 92 or greater than the sector size
   THEN the system SHALL raise `UefiError`.

### Requirement 4 — GPT Partition Entry Array Decode

**User Story:** As a test author, I want the GPT partition entry array
decoded into a tuple of typed records, so that I can assert partition
layout without byte-level parsing.

#### Acceptance Criteria

1. WHEN `GptPartitionArray.from_bytes(array_bytes, entry_size: int,
   entry_count: int)` is called THEN the system SHALL decode a tuple
   of `GptPartitionEntry` records per §5.3.3.
2. WHEN decoding each entry THEN the system SHALL surface
   `partition_type_guid: uuid.UUID`, `unique_partition_guid: uuid.UUID`,
   `starting_lba: int`, `ending_lba: int`, `attributes: int`,
   `partition_name: str` (UCS-2 null-stripped).
3. WHEN an entry's `partition_type_guid` is the zero GUID THEN the
   system SHALL mark the entry as unused and include it in the tuple
   (preserving array indexing).
4. WHEN `entry_size` is less than 128 or not a multiple of 8 THEN the
   system SHALL raise `UefiError` per §5.3.3 ("must be a multiple of
   8 bytes, and must be greater than or equal to 128 bytes").
5. WHEN the total byte length of `array_bytes` is not exactly
   `entry_size * entry_count` THEN the system SHALL raise
   `UefiError`.

### Requirement 5 — Partition Type GUID Constants (§5.3.3 Table 22)

**User Story:** As a test author, I want well-known partition-type
GUIDs exposed as named constants, so that my assertions are readable.

#### Acceptance Criteria

1. WHEN `uefigpt.partition_types` is imported THEN the system SHALL
   expose at least these named `uuid.UUID` constants, all values
   matching UEFI §5.3.3 Table 22:
   - `UNUSED_ENTRY` = `00000000-0000-0000-0000-000000000000`
   - `EFI_SYSTEM_PARTITION` = `C12A7328-F81F-11D2-BA4B-00A0C93EC93B`
   - `MBR_PARTITION_SCHEME` = `024DEE41-33E7-11D3-9D69-0008C781F39F`
   - `MICROSOFT_RESERVED` = `E3C9E316-0B5C-4DB8-817D-F92DF00215AE`
   - `MICROSOFT_BASIC_DATA` = `EBD0A0A2-B9E5-4433-87C0-68B6B72699C7`
   - `LINUX_FILESYSTEM_DATA` = `0FC63DAF-8483-4772-8E79-3D69D8477DE4`
   - `LINUX_SWAP` = `0657FD6D-A4AB-43C4-84E5-0933C84B4F4F`
   - `LINUX_LVM` = `E6D6D379-F507-44C2-A23C-238F2A3DF928`
   - `APPLE_HFS_PLUS` = `48465300-0000-11AA-AA11-00306543ECAC`
   - `APPLE_APFS` = `7C3457EF-0000-11AA-AA11-00306543ECAC`
2. WHEN the constants are imported twice THEN the same `uuid.UUID`
   object identity SHALL be returned (module-level singletons).
3. WHEN a test asserts the GUID registry against a snapshot THEN the
   system SHALL fail if any value has drifted.

### Requirement 6 — Primary / Backup Pair Validation

**User Story:** As a test author, I want to validate the primary and
backup GPT headers agree, so that I can detect half-written updates
before they manifest as boot failures.

#### Acceptance Criteria

1. WHEN `GptHeader.validate_pair(primary: GptHeader, backup: GptHeader)`
   is called THEN the system SHALL verify that `primary.MyLBA ==
   backup.AlternateLBA`, `primary.AlternateLBA == backup.MyLBA`,
   `primary.DiskGUID == backup.DiskGUID`,
   `primary.FirstUsableLBA == backup.FirstUsableLBA`,
   `primary.LastUsableLBA == backup.LastUsableLBA`,
   `primary.PartitionEntryArrayCRC32 == backup.PartitionEntryArrayCRC32`.
2. IF any of the above fields disagree THEN the system SHALL raise
   `UefiError` whose `details` lists each field-mismatch with both
   values.
3. WHEN both headers validate individually but differ only in
   `MyLBA`/`AlternateLBA` (as expected) THEN the system SHALL return
   `None`.

### Requirement 7 — Protective MBR Decode

**User Story:** As a test author, I want to decode the protective MBR
per §5.2.2, so that I can assert the protective partition entry covers
the whole disk.

#### Acceptance Criteria

1. WHEN `ProtectiveMbr.from_bytes(lba0: bytes)` is called THEN the
   system SHALL decode a `ProtectiveMbr(boot_code: bytes, unique_mbr_disk_signature: int,
   partition_record: tuple[MbrPartitionRecord, ...])` per §5.2.2.
2. WHEN any partition record has `OSType == 0xEE` THEN the record
   SHALL be identified as the protective partition record.
3. WHEN the protective record's `StartingLBA` is not `0x00000001`
   THEN the system SHALL raise `UefiError`.
4. WHEN the protective record's `SizeInLBA` does not cover the
   remainder of the disk (to at least `DiskSizeLBA - 1`, or
   `0xFFFFFFFF` when the disk exceeds 2^32 LBAs) THEN the system
   SHALL emit a `WARNING` on channel `uefi.gpt`.
5. WHEN the `Signature` field is not `0x55AA` THEN the system SHALL
   raise `UefiError`.

### Requirement 8 — Legacy MBR Decode

**User Story:** As a test author, I want to decode the legacy MBR on
non-GPT disks, so that I can inspect partitioning on older targets.

#### Acceptance Criteria

1. WHEN `LegacyMbr.from_bytes(lba0)` is called THEN the system SHALL
   decode a `LegacyMbr(boot_code, unique_mbr_disk_signature,
   partition_record)` structure per §5.2.1.
2. WHEN a record has `BootIndicator == 0x80` THEN the record SHALL
   be marked active.
3. IF any record has `BootIndicator` other than `0x00` or `0x80` THEN
   the system SHALL raise `UefiError`.
4. WHEN the `Signature` field is not `0x55AA` THEN the system SHALL
   raise `UefiError`.
5. WHEN no partition record has `OSType == 0xEE` THEN the disk SHALL
   be treated as legacy MBR rather than GPT-on-MBR.

### Requirement 9 — Read-Only Default

**User Story:** As a maintainer, I want builder APIs to be explicitly
opt-in, so that accidental disk-modification is impossible from casual
use.

#### Acceptance Criteria

1. WHEN `GptHeader`, `GptPartitionEntry`, `ProtectiveMbr`, `LegacyMbr`
   dataclasses are defined THEN the system SHALL declare them
   `@dataclass(frozen=True)`.
2. WHEN a caller constructs one of these classes via its regular
   constructor with invalid values THEN the system SHALL raise
   `ConfigurationError` from a `__post_init__` validator.
3. WHEN a caller wants to construct a new instance THEN they SHALL use
   the explicit `build(...)` classmethod, which performs additional
   validation and CRC recomputation.
4. WHEN `build(...)` is called without required parameters THEN the
   system SHALL raise `ConfigurationError` naming the missing fields.

### Requirement 10 — GPT Header Build

**User Story:** As a test author, I want to construct a GPT header
with recomputed CRCs, so that I can produce valid test images without
leaving the library.

#### Acceptance Criteria

1. WHEN `GptHeader.build(disk_size_lba, sector_size, entries:
   Sequence[GptPartitionEntry], disk_guid=None, revision=0x00010000)`
   is called THEN the system SHALL produce a primary `GptHeader` with
   `HeaderCRC32` and `PartitionEntryArrayCRC32` computed correctly.
2. WHEN `disk_guid` is `None` THEN the system SHALL generate a
   fresh random GUID via `uuid.uuid4()`.
3. WHEN `entries` is empty THEN the system SHALL produce an array of
   128 zeroed entries (the UEFI-minimum) with the appropriate CRC.
4. WHEN `build` finishes THEN calling `header.to_bytes()` SHALL
   produce a 92-byte (or `HeaderSize`-byte) buffer that, together
   with the built array, passes `GptHeader.from_bytes(...)`
   validation.
5. WHEN `backup_of(primary)` is called THEN the system SHALL return a
   `GptHeader` laid out at `primary.AlternateLBA` with `MyLBA` and
   `AlternateLBA` swapped and `PartitionEntryLBA` repositioned per
   §5.3.2.

### Requirement 11 — CRC-32 Via Layer-1 Helpers

**User Story:** As a maintainer, I want CRC-32 computation to come
from the layer-1 `uefihelpers` module, so that there is exactly one
implementation and the UEFI-spec test vector is tested once.

#### Acceptance Criteria

1. WHEN CRC-32 is computed THEN the system SHALL call
   `ccai.protocols.uefi.helpers.crc32_uefi(buf)` — never the
   stdlib `zlib.crc32` directly.
2. IF the layer-1 helper is not importable at module load THEN the
   system SHALL raise `ImportError` eagerly rather than deferring to
   parse time.
3. WHEN the parser validates a CRC THEN the system SHALL use
   `int.to_bytes(4, "little")` for comparison with stored CRC bytes.

### Requirement 12 — Fixtures from Real Disks

**User Story:** As a maintainer, I want the test corpus to include
small captured disk images from real disks, so that parser regressions
against real-world firmware output are caught.

#### Acceptance Criteria

1. WHEN the test suite runs THEN the system SHALL decode every fixture
   under `tests/fixtures/gpt/` — each a small `.img` file of at most
   2 MiB containing only the first 34 LBAs and the last 33 LBAs —
   and SHALL assert successful `validate_pair()`.
2. WHEN a new fixture is added THEN CI SHALL fail if the fixture is
   not referenced from a parametrized test.
3. WHEN a fixture is captured THEN the source device make/model and
   capture procedure SHALL be recorded in a sibling `.md` file.
4. WHEN a fixture carries partition names with non-ASCII BMP
   characters THEN the parser SHALL decode them correctly.

### Requirement 13 — Build-Then-Parse Round-Trip

**User Story:** As a maintainer, I want builder output to round-trip
through the parser, so that build correctness is structurally
verified.

#### Acceptance Criteria

1. WHEN `GptHeader.build(...)` is called and then serialized and
   parsed via `GptHeader.from_bytes(...)` THEN the resulting
   `GptHeader` SHALL compare equal under `==` to the built instance.
2. WHEN `ProtectiveMbr.build(disk_size_lba)` is called and
   re-parsed THEN the result SHALL round-trip under `==`.
3. WHEN `LegacyMbr.build(partitions)` is called and re-parsed THEN
   the result SHALL round-trip under `==`.
