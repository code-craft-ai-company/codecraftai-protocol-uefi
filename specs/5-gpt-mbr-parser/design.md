# Design — GPT / MBR Parser

## Overview

The GPT / MBR Parser is a pure-Python read/build module set for the disk
layout defined in UEFI §5.  Read paths (`from_bytes` / `validate_pair`)
are the default; build paths (`build`) are opt-in classmethods that
recompute CRCs via the layer-1 `crc32_uefi` helper (which is itself
validated against the UEFI spec test vector).

All top-level structures are `@dataclass(frozen=True)`.  Partition-type
GUIDs from UEFI §5.3.3 Table 22 are exposed as named `uuid.UUID`
constants under `partition_types`.  Primary/backup agreement is checked
via a standalone `GptHeader.validate_pair()` classmethod returning
`None` on success and raising `UefiError` on disagreement.

The parser performs no I/O.  Callers are expected to obtain LBA buffers
from 5-esp-file-ops, from a local file, or from a raw device as
appropriate — those sources are outside this feature's scope.

## Architecture

```
ccai.protocols.uefi.gpt/
    __init__.py              # public re-exports
    partition_types.py       # named UUID constants (§5.3.3 Table 22)
    gpt_header.py            # GptHeader dataclass + from_bytes/build/to_bytes
    gpt_entry.py             # GptPartitionEntry + GptPartitionArray
    gpt_attributes.py        # GptPartitionAttributes IntFlag
    mbr_protective.py        # ProtectiveMbr + MbrPartitionRecord
    mbr_legacy.py            # LegacyMbr
    _chs.py                  # CHS <-> LBA helpers (MBR)
```

Dependency direction (bottom-up):

```
gpt_header.py ─▶ gpt_entry.py ─▶ gpt_attributes.py
             ─▶ partition_types.py
             ─▶ layer-1 helpers (crc32_uefi, ucs2_decode)
             ─▶ layer-1 exceptions
mbr_protective.py ─▶ _chs.py
                  ─▶ layer-1 exceptions
```

## Components and Interfaces

### `GptHeader` (gpt_header.py)

```python
@dataclass(frozen=True)
class GptHeader:
    signature: bytes              # 8 bytes, always b"EFI PART"
    revision: int                 # UINT32 LE
    header_size: int              # UINT32 LE
    header_crc32: int             # UINT32 LE (as stored)
    my_lba: int                   # UINT64 LE
    alternate_lba: int            # UINT64 LE
    first_usable_lba: int         # UINT64 LE
    last_usable_lba: int          # UINT64 LE
    disk_guid: uuid.UUID          # 16 bytes mixed-endian per §5.3.2
    partition_entry_lba: int      # UINT64 LE
    number_of_partition_entries: int  # UINT32 LE
    size_of_partition_entry: int      # UINT32 LE
    partition_entry_array_crc32: int  # UINT32 LE
    reserved: bytes                   # remainder of sector

    @classmethod
    def from_bytes(
        cls,
        lba0: bytes,
        lba1: bytes,
        partition_entries_bytes: Optional[bytes] = None,
        aspects: Optional[UefiCmdAspects] = None,
    ) -> "GptHeader": ...

    @classmethod
    def build(
        cls,
        disk_size_lba: int,
        sector_size: int,
        entries: Sequence["GptPartitionEntry"],
        disk_guid: Optional[uuid.UUID] = None,
        revision: int = 0x00010000,
        aspects: Optional[UefiCmdAspects] = None,
    ) -> "GptHeader": ...

    def to_bytes(self) -> bytes: ...

    def backup_of(self, disk_size_lba: int) -> "GptHeader": ...

    @classmethod
    def validate_pair(
        cls,
        primary: "GptHeader",
        backup: "GptHeader",
        aspects: Optional[UefiCmdAspects] = None,
    ) -> None: ...
```

CRC validation inside `from_bytes`:

```python
def _validate_header_crc(header_bytes: bytes, header_size: int, stored_crc: int) -> None:
    scratch = bytearray(header_bytes[:header_size])
    scratch[16:20] = b"\x00\x00\x00\x00"
    computed = crc32_uefi(bytes(scratch))
    if computed != stored_crc:
        err_msg_lines = ["GPT HeaderCRC32 mismatch."]
        err_msg_lines.append(f"COMPUTED: 0x{computed:08X}")
        err_msg_lines.append(f"STORED:   0x{stored_crc:08X}")
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiError(err_msg)
    return
```

### `GptPartitionEntry` / `GptPartitionArray` (gpt_entry.py)

```python
@dataclass(frozen=True)
class GptPartitionEntry:
    partition_type_guid: uuid.UUID
    unique_partition_guid: uuid.UUID
    starting_lba: int
    ending_lba: int
    attributes: int
    partition_name: str

    def is_unused(self) -> bool:
        rtnval = self.partition_type_guid == uuid.UUID("00000000-0000-0000-0000-000000000000")
        return rtnval

    @classmethod
    def from_bytes(cls, buf: bytes) -> "GptPartitionEntry": ...

    def to_bytes(self, entry_size: int) -> bytes: ...


@dataclass(frozen=True)
class GptPartitionArray:
    entries: tuple[GptPartitionEntry, ...]
    entry_size: int

    @classmethod
    def from_bytes(
        cls,
        array_bytes: bytes,
        entry_size: int,
        entry_count: int,
        aspects: Optional[UefiCmdAspects] = None,
    ) -> "GptPartitionArray": ...

    def to_bytes(self) -> bytes: ...
```

### `GptPartitionAttributes` (gpt_attributes.py)

```python
class GptPartitionAttributes(IntFlag):
    REQUIRED_PARTITION = 1 << 0
    NO_BLOCK_IO_PROTOCOL = 1 << 1
    LEGACY_BIOS_BOOTABLE = 1 << 2
    # Bits 48..63 are type-GUID-specific; library treats them as opaque
```

### `partition_types.py`

```python
UNUSED_ENTRY: Final[uuid.UUID] = uuid.UUID("00000000-0000-0000-0000-000000000000")
EFI_SYSTEM_PARTITION: Final[uuid.UUID] = uuid.UUID("C12A7328-F81F-11D2-BA4B-00A0C93EC93B")
MBR_PARTITION_SCHEME: Final[uuid.UUID] = uuid.UUID("024DEE41-33E7-11D3-9D69-0008C781F39F")
MICROSOFT_RESERVED: Final[uuid.UUID] = uuid.UUID("E3C9E316-0B5C-4DB8-817D-F92DF00215AE")
MICROSOFT_BASIC_DATA: Final[uuid.UUID] = uuid.UUID("EBD0A0A2-B9E5-4433-87C0-68B6B72699C7")
LINUX_FILESYSTEM_DATA: Final[uuid.UUID] = uuid.UUID("0FC63DAF-8483-4772-8E79-3D69D8477DE4")
LINUX_SWAP: Final[uuid.UUID] = uuid.UUID("0657FD6D-A4AB-43C4-84E5-0933C84B4F4F")
LINUX_LVM: Final[uuid.UUID] = uuid.UUID("E6D6D379-F507-44C2-A23C-238F2A3DF928")
APPLE_HFS_PLUS: Final[uuid.UUID] = uuid.UUID("48465300-0000-11AA-AA11-00306543ECAC")
APPLE_APFS: Final[uuid.UUID] = uuid.UUID("7C3457EF-0000-11AA-AA11-00306543ECAC")
```

### `ProtectiveMbr` (mbr_protective.py)

```python
@dataclass(frozen=True)
class MbrPartitionRecord:
    boot_indicator: int
    starting_chs: tuple[int, int, int]
    os_type: int
    ending_chs: tuple[int, int, int]
    starting_lba: int
    size_in_lba: int


@dataclass(frozen=True)
class ProtectiveMbr:
    boot_code: bytes
    unique_mbr_disk_signature: int
    partition_record: tuple[MbrPartitionRecord, ...]
    signature: int   # always 0xAA55

    @classmethod
    def from_bytes(
        cls,
        lba0: bytes,
        aspects: Optional[UefiCmdAspects] = None,
    ) -> "ProtectiveMbr": ...

    @classmethod
    def build(
        cls,
        disk_size_lba: int,
        aspects: Optional[UefiCmdAspects] = None,
    ) -> "ProtectiveMbr": ...

    def to_bytes(self) -> bytes: ...
```

### `LegacyMbr` (mbr_legacy.py)

Same shape as `ProtectiveMbr` but without the §5.2.2 restriction on
`OSType == 0xEE` and with per-record bootability tracked.

## Data Models

### GPT Header Layout (UEFI §5.3.2, 92+ bytes in LBA1)

| Offset | Size | Field                          | Notes                                   |
|--------|------|--------------------------------|-----------------------------------------|
| 0      | 8    | `Signature`                    | `"EFI PART"` / `0x5452415020494645`      |
| 8      | 4    | `Revision`                     | UINT32 LE, `0x00010000` for 1.0          |
| 12     | 4    | `HeaderSize`                   | UINT32 LE, >= 92, <= sector size         |
| 16     | 4    | `HeaderCRC32`                  | Zeroed during CRC recompute              |
| 20     | 4    | Reserved                       | 0                                       |
| 24     | 8    | `MyLBA`                        | UINT64 LE                                |
| 32     | 8    | `AlternateLBA`                 | UINT64 LE                                |
| 40     | 8    | `FirstUsableLBA`               | UINT64 LE                                |
| 48     | 8    | `LastUsableLBA`                | UINT64 LE                                |
| 56     | 16   | `DiskGUID`                     | Mixed-endian GUID                        |
| 72     | 8    | `PartitionEntryLBA`            | UINT64 LE                                |
| 80     | 4    | `NumberOfPartitionEntries`     | UINT32 LE                                |
| 84     | 4    | `SizeOfPartitionEntry`         | UINT32 LE, multiple of 8, >= 128         |
| 88     | 4    | `PartitionEntryArrayCRC32`     | UINT32 LE                                |
| 92     | var  | Reserved to end of block       | Zero-padded                              |

### GPT Partition Entry Layout (§5.3.3, 128 bytes minimum)

| Offset | Size | Field                      | Notes                               |
|--------|------|----------------------------|-------------------------------------|
| 0      | 16   | `PartitionTypeGUID`        | Mixed-endian GUID                   |
| 16     | 16   | `UniquePartitionGUID`      | Mixed-endian GUID                   |
| 32     | 8    | `StartingLBA`              | UINT64 LE                           |
| 40     | 8    | `EndingLBA`                | UINT64 LE (inclusive)               |
| 48     | 8    | `Attributes`               | UINT64 LE; bits 0..2 generic, 48..63 type-specific |
| 56     | 72   | `PartitionName`            | UCS-2, up to 36 chars null-padded   |

### Protective MBR Layout (§5.2.2, 512 bytes in LBA0)

| Offset | Size | Field                          | Notes                                     |
|--------|------|--------------------------------|-------------------------------------------|
| 0      | 440  | `BootCode`                     | Unused in GPT context, often zero         |
| 440    | 4    | `UniqueMBRDiskSignature`       | UINT32 LE                                 |
| 444    | 2    | `Unknown`                      | 0                                         |
| 446    | 16   | `PartitionRecord[0]`           | `OSType=0xEE`, `StartingLBA=1`            |
| 462    | 16   | `PartitionRecord[1]`           | Zeroed                                    |
| 478    | 16   | `PartitionRecord[2]`           | Zeroed                                    |
| 494    | 16   | `PartitionRecord[3]`           | Zeroed                                    |
| 510    | 2    | `Signature`                    | `0x55AA`                                  |

### MBR Partition Record Layout (16 bytes)

| Offset | Size | Field                  | Notes                             |
|--------|------|------------------------|-----------------------------------|
| 0      | 1    | `BootIndicator`        | `0x00` or `0x80`                  |
| 1      | 3    | `StartingCHS`          | `(head, sector|cyl_hi, cyl_lo)`   |
| 4      | 1    | `OSType`               | `0xEE` for protective MBR         |
| 5      | 3    | `EndingCHS`            | Same layout as StartingCHS        |
| 8      | 4    | `StartingLBA`          | UINT32 LE                         |
| 12     | 4    | `SizeInLBA`            | UINT32 LE                         |

## Measured Interop Contract

Every public entry point accepts `aspects: Optional[UefiCmdAspects] =
None`.  Resolution uses `resolve_aspects(passed, DEFAULT_CMD_ASPECTS)`.

Aspects fields consumed:

* `action_pattern` — validated; only `SINGLE_CALL` and
  `SINGLE_CONNECTED_CALL` accepted.  Retry-flavored patterns raise
  `SemanticError`.
* `monitor_delay` — forwarded to a `MonitoredScope` wrapping the
  parse or build.
* `logger` — used for the handful of `INFO`/`WARNING` emissions.

Fields not consumed: `completion_*`, `connection_*`, `inactivity_*`,
`allowed_error_codes`, `allowed_connection_exceptions`, `must_connect`,
`prompt_pattern`, `response_encoding`, `transport_kind`,
`expected_status`.

ActionPatterns supported:

| Pattern                          | Supported | Notes                                  |
|----------------------------------|-----------|----------------------------------------|
| `SINGLE_CALL`                    | Yes       | Default.                               |
| `SINGLE_CONNECTED_CALL`          | Yes       | Identical to `SINGLE_CALL`.            |
| `DO_UNTIL_SUCCESS`               | No        | `SemanticError`.                       |
| `DO_WHILE_SUCCESS`               | No        | `SemanticError`.                       |
| `DO_UNTIL_CONNECTION_FAILURE`    | No        | `SemanticError`.                       |

## Error Handling

| Condition                                         | Exception            | `details`                                  |
|---------------------------------------------------|----------------------|--------------------------------------------|
| `Signature` != `"EFI PART"`                       | `UefiError`          | observed signature bytes                   |
| `HeaderSize` < 92 or > sector size                | `UefiError`          | observed value                             |
| Header CRC-32 mismatch                            | `UefiError`          | computed + stored CRCs in hex              |
| Partition-array CRC-32 mismatch                   | `UefiError`          | computed + stored CRCs, array byte offset  |
| Invalid `SizeOfPartitionEntry`                    | `UefiError`          | value, constraint (multiple-of-8, >= 128)  |
| Array buffer length mismatch                      | `UefiError`          | expected, observed                         |
| Primary/backup field disagreement                 | `UefiError`          | per-field mismatch table                   |
| Protective MBR signature != `0x55AA`              | `UefiError`          | observed signature                         |
| Protective MBR protective record missing          | `UefiError`          | partition records (typed summary)          |
| Legacy MBR `BootIndicator` not in `{0x00, 0x80}`  | `UefiError`          | record index, observed value               |
| Builder called with contradictory inputs          | `ConfigurationError` | field names, offending values              |
| Retry-flavored `action_pattern`                   | `SemanticError`      | pattern name                               |

## Logging

Channel: `uefi.gpt`.

* `DEBUG` on each successful parse — structure name, byte count, entry
  count.
* `WARNING` when `Revision` is newer than the library is written for.
* `WARNING` when `ProtectiveMbr` protective record does not span the
  disk.
* `ERROR` on any `UefiError` before re-raise, including CRC mismatch
  details in the log event.

## Security Considerations

* Parser inputs are potentially attacker-controlled disk images.  The
  parser enforces length limits before any allocation.
* CRC-32 computation is length-bounded by `HeaderSize`, capped at the
  sector size.
* `from_bytes` never writes to disk; only the opt-in `build()` +
  caller-side serialization via `to_bytes()` can produce bytes
  intended for writing.
* Builder APIs do not write to disk themselves — callers remain
  responsible for the write step and for any sector-level I/O safety
  concerns.

## Testing Strategy

### Unit — `partition_types`

* Snapshot test: every constant equals its spec-defined GUID.
* Identity test: re-import returns the same `uuid.UUID` objects.

### Unit — `GptHeader.from_bytes`

* Synthetic happy path: build a header by hand, serialize, re-parse,
  assert equality.
* CRC mismatch path: flip one byte, assert `UefiError` with computed
  and stored CRCs.
* Wrong signature, wrong revision (warning only), wrong
  `HeaderSize` paths.
* `validate_pair` happy and mismatched paths.

### Unit — `GptPartitionArray.from_bytes`

* 128-byte entry size happy path.
* 256-byte entry size happy path (padded zeros in tail).
* `entry_size` invalid (not multiple of 8, less than 128) raises
  `UefiError`.
* Buffer length mismatch raises `UefiError`.
* Partition name with BMP non-ASCII (e.g., "Boot \u00b5") round-trips.

### Unit — `ProtectiveMbr` / `LegacyMbr`

* Happy paths for each.
* Boot-indicator validation on `LegacyMbr`.
* Protective record size warning on undersized protective span.

### Builder round-trip

* `GptHeader.build(...)` → `to_bytes()` → `from_bytes(...)` equality.
* `ProtectiveMbr.build(disk_size_lba)` → `to_bytes()` →
  `from_bytes(...)` equality.
* `LegacyMbr.build(partitions)` round-trip.

### Fixture corpus

* Captured small `.img` fixtures under `tests/fixtures/gpt/`:
  - `linux-ext4-gpt.img`, `windows-ntfs-gpt.img`,
    `macos-apfs-gpt.img`, `esp-fat32-gpt.img`, `legacy-mbr.img`.
* Each image is a concatenation of first 34 LBAs and last 33 LBAs from
  a real disk so the complete GPT primary + backup can be validated
  without distributing terabyte-scale data.
* Parametrized test walks every fixture, parses primary + backup,
  calls `validate_pair()`.

### Action-pattern tests

* `SINGLE_CALL`, `SINGLE_CONNECTED_CALL` succeed.
* Retry-flavored patterns raise `SemanticError`.
* `aspects=None` resolves from `DEFAULT_CMD_ASPECTS`.

### Negative corpus

* Deliberately-corrupted fixtures (bit flips in CRC, in signature, in
  `HeaderSize`) — each raises `UefiError` with expected `details`.

## Dependencies on Other Features

* `1-aspects` — `UefiCmdAspects`, `resolve_aspects`, `ActionPattern`,
  `DEFAULT_CMD_ASPECTS`.
* `1-constants-and-types` — `guid_to_bytes`, `guid_from_bytes` (mixed-
  endian GUID conversion).
* `1-exceptions` — `UefiError`, `ConfigurationError`, `SemanticError`.
* `1-helpers` — `crc32_uefi(buf)` (required import),
  `ucs2_decode(buf)`, `MonitoredScope`.
* `1-logging` — `uefi.gpt` channel.

Downstream consumers: `5-esp-file-ops` (identifies EFI System
Partition via `EFI_SYSTEM_PARTITION` GUID), any workflow that asserts
disk layout before installing bootloaders.  No third-party runtime
dependency.
