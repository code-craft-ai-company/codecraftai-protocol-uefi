# Requirements — Device Path Codec

## Introduction

The Device Path Codec provides a lossless binary ⇄ text codec for UEFI
Device Paths covering every node type enumerated in UEFI §10.3 and the
text representation defined in §10.6.  The codec exposes:

* `decode_binary(buf: bytes) -> DevicePath` — parse a concatenated
  sequence of device-path nodes ending at an End-of-Hardware /
  End-Entire node into a typed `DevicePath` tree.
* `encode_binary(path: DevicePath) -> bytes` — re-serialize a tree to
  bytes byte-identical to the original for every canonical example in
  §10.6.
* `decode_text(text: str) -> DevicePath` — parse the canonical text
  form via a hand-rolled recursive-descent parser (no `pyparsing`
  dependency).
* `encode_text(path: DevicePath) -> str` — render bytes-equivalent of
  `ConvertDevicePathToText()` per §10.6.4 for every canonical example.

The codec is transport-independent and performs no I/O.  It is consumed
by 5-boot-manager for `FilePathList` rendering, by 5-network-boot for
URI/IPv4/IPv6 node construction, and by any caller who needs to reason
about device-path equality.

Unknown node types (a `type/subtype` pair not present in the registry)
are represented using the generic `Path(type, subtype, data)` form per
§10.6.1.6 rather than raising.  This preserves forward compatibility
with new UEFI spec revisions.

Text parser and binary parser each dispatch via a `(type, subtype) →
callable` registry populated at module import time.

## Requirements

### Requirement 1 — Aspects Presence at Public Entry Points

**User Story:** As a test author, I want every codec entry point to
accept an `aspects` override, so that parse-time resource governance is
caller-controlled even for pure-CPU operations.

#### Acceptance Criteria

1. WHEN `decode_binary(buf, aspects=None)` is called THEN the system
   SHALL accept the parameter without error and SHALL resolve via
   `resolve_aspects(None, DEFAULT_CMD_ASPECTS)`.
2. WHEN `encode_binary(path, aspects=None)` is called THEN the system
   SHALL accept the parameter without error.
3. WHEN `decode_text(text, aspects=None)` is called THEN the system
   SHALL accept the parameter without error.
4. WHEN `encode_text(path, aspects=None)` is called THEN the system
   SHALL accept the parameter without error.
5. WHEN the caller passes a non-None `aspects` THEN the system SHALL
   honor `aspects.monitor_delay` by wrapping the parse in a
   `MonitoredScope` so long-running parses (pathological inputs) are
   observable.

### Requirement 2 — Action Pattern Support

**User Story:** As a test author, I want the codec to support the
action patterns that make sense for pure-CPU work, so that my caller
code stays uniform even when a codec call appears alongside I/O calls.

#### Acceptance Criteria

1. WHEN `aspects.action_pattern == ActionPattern.SINGLE_CALL` THEN the
   codec SHALL perform exactly one parse attempt.
2. WHEN `aspects.action_pattern == ActionPattern.SINGLE_CONNECTED_CALL`
   THEN the codec SHALL behave identically to `SINGLE_CALL` (no
   connection concept).
3. IF `aspects.action_pattern in {DO_UNTIL_SUCCESS, DO_WHILE_SUCCESS,
   DO_UNTIL_CONNECTION_FAILURE}` THEN the system SHALL raise
   `SemanticError` because pure-CPU parsers are deterministic and
   retrying is nonsensical.

### Requirement 3 — Generic Node Header Round-Trip

**User Story:** As a maintainer, I want every node's 4-byte generic
header (type, subtype, length) to round-trip bit-for-bit, so that
encoder correctness is easy to verify.

#### Acceptance Criteria

1. WHEN any node is decoded THEN the system SHALL produce a
   `DevicePathNode` carrying `type: int` (UINT8), `subtype: int`
   (UINT8), and `length: int` (UINT16 LE) consistent with the
   header.
2. WHEN any node is encoded THEN the system SHALL emit a 4-byte
   header carrying `type`, `subtype`, `length` with `length`
   computed from the body.
3. IF the header `length` is less than 4 THEN the system SHALL raise
   `UefiError` with `details` naming the observed value.
4. IF the header `length` would exceed the remaining input buffer THEN
   the system SHALL raise `UefiError`.

### Requirement 4 — Hardware Device Path Nodes (§10.3.2)

**User Story:** As a test author, I want every Hardware-type node
round-tripped, so that PCI, PCCARD, Memory-Mapped, Vendor, Controller,
and BMC paths are preserved losslessly.

#### Acceptance Criteria

1. WHEN a `(type=0x01, subtype=0x01)` PCI node per §10.3.2.1 is
   decoded THEN the system SHALL return a `PciNode(function, device)`
   with UINT8 fields.
2. WHEN a `(0x01, 0x02)` PCCARD node per §10.3.2.2 is decoded THEN
   the system SHALL return a `PccardNode(function_number)` with a
   UINT8 field.
3. WHEN a `(0x01, 0x03)` Memory-Mapped node per §10.3.2.3 is decoded
   THEN the system SHALL return a `MemoryMappedNode(memory_type:
   MemoryType, starting_address, ending_address)` with LE UINT64
   fields.
4. WHEN a `(0x01, 0x04)` Vendor node per §10.3.2.4 is decoded THEN
   the system SHALL return a `HardwareVendorNode(vendor_guid,
   vendor_data)`.
5. WHEN a `(0x01, 0x05)` Controller node per §10.3.2.5 is decoded
   THEN the system SHALL return a `ControllerNode(controller_number)`.
6. WHEN a `(0x01, 0x06)` BMC node per §10.3.2.6 is decoded THEN the
   system SHALL return a `BmcNode(interface_type, base_address)`.
7. WHEN any Hardware node is encoded THEN the result SHALL
   round-trip decode equal to the original under `==`.

### Requirement 5 — ACPI Device Path Nodes (§10.3.3)

**User Story:** As a test author, I want ACPI device-path nodes
round-tripped, including `_ADR` and NVDIMM variants.

#### Acceptance Criteria

1. WHEN `(0x02, 0x01)` ACPI node is decoded THEN the system SHALL
   return an `AcpiNode(hid: int, uid: int)` per §10.3.3.
2. WHEN `(0x02, 0x02)` Expanded ACPI node is decoded THEN the system
   SHALL return an `ExpandedAcpiNode(hid, uid, cid, hidstr, uidstr,
   cidstr)` with ASCII-string tails per §10.3.3.
3. WHEN `(0x02, 0x03)` ACPI `_ADR` node per §10.3.3.1 is decoded THEN
   the system SHALL return an `AcpiAdrNode(addresses: tuple[int, ...])`
   of UINT32 addresses.
4. WHEN `(0x02, 0x04)` NVDIMM node per §10.3.3.2 is decoded THEN the
   system SHALL return an `NvdimmNode(handle)` with a UINT32 field.
5. WHEN any ACPI node is encoded THEN the result SHALL round-trip.

### Requirement 6 — Messaging Device Path Nodes (§10.3.4)

**User Story:** As a test author, I want every Messaging-type node
(ATAPI, SCSI, Fibre Channel, USB, SATA, iSCSI, NVMe, URI, MAC,
IPv4/IPv6, Bluetooth, UFS, SD, DNS, REST, NVMe-oF, and the rest)
round-tripped.

#### Acceptance Criteria

1. WHEN a Messaging node is decoded for any subtype listed in §10.3.4.1
   through §10.3.4.33 THEN the system SHALL return the corresponding
   typed dataclass named `<Family>Node` (for example `UsbNode`,
   `SataNode`, `NvmeNamespaceNode`, `IPv4Node`, `IPv6Node`, `UriNode`,
   `MacAddressNode`, `VlanNode`, `BluetoothLeNode`, `DnsNode`,
   `NvmeOfNamespaceNode`).
2. WHEN an IPv4 node per §10.3.4.11 is decoded THEN the system SHALL
   surface `local_ip_address: ipaddress.IPv4Address`,
   `remote_ip_address: ipaddress.IPv4Address`, `local_port`,
   `remote_port`, `protocol`, `static_ip`, `gateway`, `subnet_mask`.
3. WHEN an IPv6 node per §10.3.4.12 is decoded THEN the system SHALL
   surface `local_ip_address: ipaddress.IPv6Address`,
   `remote_ip_address: ipaddress.IPv6Address`, `local_port`,
   `remote_port`, `protocol`, `ip_address_origin`,
   `prefix_length`, `gateway`.
4. WHEN a URI node per §10.3.4.22 is decoded THEN the system SHALL
   surface `uri: str` decoded as UTF-8 per the spec.
5. WHEN a MAC Address node per §10.3.4.10 is decoded THEN the system
   SHALL surface `mac_address: bytes` (32 bytes padded) and
   `if_type: int`.
6. WHEN an iSCSI node per §10.3.4.20 is decoded THEN the system SHALL
   surface `protocol`, `login_options`, `lun`, `tpgt`,
   `target_name: str`.
7. WHEN an NVMe Namespace node per §10.3.4.21 is decoded THEN the
   system SHALL surface `namespace_id: int`, `namespace_uuid: bytes`.
8. WHEN any Messaging node is encoded THEN the result SHALL
   round-trip.

### Requirement 7 — Media Device Path Nodes (§10.3.5)

**User Story:** As a test author, I want every Media-type node
(Hard-Drive, CD-ROM, Vendor, File Path, Media Protocol, PIWG FW File,
PIWG FW Volume, Relative Offset Range, RAM Disk) round-tripped.

#### Acceptance Criteria

1. WHEN a `(0x04, 0x01)` Hard-Drive node per §10.3.5.1 is decoded
   THEN the system SHALL return a `HardDriveNode(partition_number,
   partition_start, partition_size, partition_signature: bytes,
   partition_format, signature_type)`.
2. WHEN a `(0x04, 0x02)` CD-ROM node per §10.3.5.2 is decoded THEN
   the system SHALL return a `CdRomNode(boot_entry, partition_start,
   partition_size)`.
3. WHEN a `(0x04, 0x04)` File Path node per §10.3.5.4 is decoded
   THEN the system SHALL return a `FilePathNode(path: str)` with
   the UCS-2 null-terminated path decoded.
4. WHEN a `(0x04, 0x09)` RAM Disk node per §10.3.5.9 is decoded
   THEN the system SHALL return a `RamDiskNode(start_address,
   end_address, disk_type: uuid.UUID, instance)`.
5. WHEN a File Path node is encoded THEN the system SHALL emit a
   UCS-2 null-terminated buffer and SHALL raise `UefiError` on non-BMP
   characters.
6. WHEN any Media node is encoded THEN the result SHALL round-trip.

### Requirement 8 — BBS Device Path Nodes (§10.3.6)

**User Story:** As a test author, I want legacy BIOS Boot
Specification nodes round-tripped so that older firmware output is
readable.

#### Acceptance Criteria

1. WHEN a `(0x05, 0x01)` BBS node is decoded THEN the system SHALL
   return a `BbsNode(device_type, status_flag, description: str)`.
2. WHEN a BBS node is encoded THEN the result SHALL round-trip.

### Requirement 9 — End-of-Hardware / End-Entire Nodes

**User Story:** As a test author, I want the two End-node variants
represented explicitly so that concatenated device paths are
round-trippable.

#### Acceptance Criteria

1. WHEN a `(0x7F, 0x01)` End-of-Hardware node is decoded THEN the
   system SHALL return a distinguished `EndOfHardwareNode` singleton.
2. WHEN a `(0x7F, 0xFF)` End-Entire node is decoded THEN the system
   SHALL return a distinguished `EndEntireNode` singleton.
3. WHEN `decode_binary` encounters an End-Entire node THEN the parser
   SHALL stop and return.
4. WHEN `decode_binary` encounters an End-of-Hardware node THEN the
   parser SHALL begin a new concatenated path per §10.3.1.
5. WHEN `encode_binary` emits a `DevicePath` THEN the system SHALL
   ensure the output terminates in an End-Entire node.

### Requirement 10 — Unknown Node Generic Form (§10.6.1.6)

**User Story:** As a maintainer, I want unknown node types preserved
as opaque `Path(type,subtype,data)` rather than erroring, so that
forward compatibility is automatic.

#### Acceptance Criteria

1. WHEN a `(type, subtype)` pair not in the registry is encountered
   during binary decode THEN the system SHALL return a generic
   `UnknownDevicePathNode(type, subtype, data: bytes)` rather than
   raising.
2. WHEN an `UnknownDevicePathNode` is encoded back to binary THEN
   the system SHALL emit `header + data` byte-identical to what was
   decoded.
3. WHEN an `UnknownDevicePathNode` is encoded to text THEN the system
   SHALL emit `Path(<type>,<subtype>,<hex-bytes>)` per §10.6.1.6.
4. WHEN `Path(<type>,<subtype>,<hex>)` is decoded from text THEN the
   system SHALL produce an `UnknownDevicePathNode`.

### Requirement 11 — Text Encoder Byte-Identical to Spec Examples

**User Story:** As a maintainer, I want the text encoder to produce
byte-identical output to `ConvertDevicePathToText()` on every
canonical example in §10.6, so that interoperability with
vendor-produced device-path strings is exact.

#### Acceptance Criteria

1. WHEN every canonical example from §10.6 is decoded from binary and
   re-encoded to text THEN the system SHALL produce the text listed
   in the spec, byte-identical.
2. WHEN every canonical example from §10.6 is decoded from text and
   re-encoded to binary THEN the system SHALL produce the original
   binary bytes, byte-identical.
3. WHEN the text encoder encodes a multi-node path THEN the system
   SHALL separate nodes with the `/` character per §10.6.1.1.
4. WHEN a text encoding contains comma-separated arguments THEN the
   system SHALL emit them without embedded whitespace per §10.6.1.3.

### Requirement 12 — Text Parser Hand-Rolled

**User Story:** As a maintainer, I want the text parser to depend on
no third-party parsing library, so that the codec's install footprint
stays minimal.

#### Acceptance Criteria

1. WHEN the text parser module is imported THEN the system SHALL NOT
   import `pyparsing`, `lark`, `parsimonious`, or any other parsing
   third-party library.
2. WHEN the text parser consumes input THEN the implementation SHALL
   be a recursive-descent parser with each production implemented as
   a Python method that returns a typed result.
3. WHEN the text parser encounters a syntax error THEN the system
   SHALL raise `UefiError` with `details` carrying the offending
   position, surrounding context, and the production that failed.

### Requirement 13 — Node Registry Dispatch

**User Story:** As a maintainer, I want `(type, subtype) → callable`
dispatch to be a data-driven registry, so that adding a new node type
is a single-file change.

#### Acceptance Criteria

1. WHEN the codec module is imported THEN the system SHALL populate a
   `BINARY_DECODERS: dict[tuple[int, int], DecoderCallable]` and a
   `TEXT_DECODERS: dict[str, TextDecoderCallable]` at module load.
2. WHEN an entry is added to the registry after import THEN callers
   SHALL be able to decode a new node type without further changes
   inside the top-level `decode_binary` / `decode_text` functions.
3. WHEN the registry is consulted THEN the system SHALL NOT import
   vendor-specific code at decode time — all decoders are resolved at
   import time.

### Requirement 14 — Property-Based Round-Trip Tests

**User Story:** As a maintainer, I want `hypothesis`-driven
property-based tests asserting lossless round-trips over randomized
node trees, so that malformed-but-accepted inputs cannot produce
different outputs on re-encode.

#### Acceptance Criteria

1. WHEN the test suite runs THEN the system SHALL use `hypothesis`
   strategies to generate randomized `DevicePath` trees containing at
   least one of every known node type.
2. WHEN a randomized tree is encoded and decoded THEN the system SHALL
   observe `encode_binary(decode_binary(encode_binary(t))) ==
   encode_binary(t)` for every generated tree.
3. WHEN a randomized tree is rendered to text and parsed back THEN
   the system SHALL observe `decode_text(encode_text(t)) == t` under
   the codec's equality semantics.
4. WHEN hypothesis reports a failing example THEN the system SHALL
   ensure the failing binary is deterministically reproducible so CI
   can shrink to a minimal example.
