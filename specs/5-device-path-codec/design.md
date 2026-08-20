# Design — Device Path Codec

## Overview

The Device Path Codec is a pure-Python module set that delivers a
lossless binary ⇄ text codec for UEFI Device Paths per UEFI §10.  It is
transport-agnostic and performs no I/O.  A `DevicePath` is a tree-like
immutable value object: a sequence of `DevicePathNode` subclasses
terminated by an `EndEntireNode`.  Node equality is structural.

Binary decoding is table-driven through a `BINARY_DECODERS:
dict[tuple[int, int], Callable]` registry; each decoder returns a fully
populated typed dataclass.  Unknown `(type, subtype)` pairs fall through
to `UnknownDevicePathNode(type, subtype, data)` so forward compatibility
is automatic.

Text encoding and decoding similarly dispatch through a name-keyed
registry.  The text parser is a hand-rolled recursive-descent parser in
pure Python — no `pyparsing`, no `lark`.

The codec exposes `aspects` at every public entry point only so that
`monitor_delay` can wrap long-running parses in a `MonitoredScope` and
so that the public surface is uniform with the rest of layer 5.
Retry-flavored action patterns are rejected with `SemanticError`
because parse is deterministic.

## Architecture

```
ccai.protocols.uefi.devicepath/
    __init__.py                   # public re-exports
    nodes/
        __init__.py
        base.py                   # DevicePathNode abstract base, DevicePath
        end.py                    # EndOfHardwareNode, EndEntireNode singletons
        hardware.py               # PciNode, PccardNode, MemoryMappedNode, ...
        acpi.py                   # AcpiNode, ExpandedAcpiNode, AcpiAdrNode, NvdimmNode
        messaging.py              # UsbNode, SataNode, NvmeNode, IPv4Node, UriNode, ...
        media.py                  # HardDriveNode, CdRomNode, FilePathNode, RamDiskNode, ...
        bbs.py                    # BbsNode
        unknown.py                # UnknownDevicePathNode
    binary/
        decoder.py                # decode_binary() + BINARY_DECODERS registry
        encoder.py                # encode_binary()
        _helpers.py               # read_uint8/16/32/64, read_guid, read_ucs2z
    text/
        decoder.py                # decode_text() + TEXT_DECODERS registry
        encoder.py                # encode_text() + TEXT_ENCODERS registry
        _lexer.py                 # token stream
        _parser.py                # recursive-descent parser
```

Dependency direction (bottom-up):

```
binary/encoder.py ─▶ binary/_helpers.py
                  ─▶ nodes/*.py
binary/decoder.py ─▶ nodes/*.py ─▶ nodes/base.py
text/encoder.py   ─▶ nodes/*.py
text/decoder.py   ─▶ text/_parser.py ─▶ text/_lexer.py ─▶ nodes/*.py
```

## Components and Interfaces

### `DevicePathNode` base (nodes/base.py)

```python
@dataclass(frozen=True)
class DevicePathNode:
    type: int
    subtype: int

    def header_length(self) -> int:
        """
            Return the 16-bit length field for this node's 4-byte header.

            :returns: The total byte length of this node including header.
        """
        body = self._encode_body()
        rtnval = 4 + len(body)
        return rtnval

    def _encode_body(self) -> bytes:
        raise AbstractMethodError(
            "Subclasses of DevicePathNode must implement _encode_body()."
        )


@dataclass(frozen=True)
class DevicePath:
    nodes: tuple[DevicePathNode, ...]

    def __iter__(self) -> Iterator[DevicePathNode]:
        rtnval = iter(self.nodes)
        return rtnval
```

### Node-Class Examples

```python
@dataclass(frozen=True)
class PciNode(DevicePathNode):
    function: int   # UINT8
    device: int     # UINT8


@dataclass(frozen=True)
class UsbNode(DevicePathNode):
    parent_port: int       # UINT8
    interface: int         # UINT8


@dataclass(frozen=True)
class IPv4Node(DevicePathNode):
    local_ip_address: ipaddress.IPv4Address
    remote_ip_address: ipaddress.IPv4Address
    local_port: int        # UINT16 LE
    remote_port: int       # UINT16 LE
    protocol: int          # UINT16 LE
    static_ip: bool
    gateway: ipaddress.IPv4Address
    subnet_mask: ipaddress.IPv4Address


@dataclass(frozen=True)
class UriNode(DevicePathNode):
    uri: str


@dataclass(frozen=True)
class FilePathNode(DevicePathNode):
    path: str


@dataclass(frozen=True)
class HardDriveNode(DevicePathNode):
    partition_number: int
    partition_start: int       # UINT64 LE
    partition_size: int        # UINT64 LE
    partition_signature: bytes # 16 bytes
    partition_format: int      # UINT8
    signature_type: int        # UINT8


@dataclass(frozen=True)
class UnknownDevicePathNode(DevicePathNode):
    data: bytes
```

### Binary Decoder (binary/decoder.py)

```python
def decode_binary(
    buf: bytes,
    aspects: Optional[UefiCmdAspects] = None,
) -> DevicePath:
    """
        Parse a concatenated sequence of device-path nodes.

        :param buf: The binary buffer terminated by an End-Entire node.
        :param aspects: Optional aspects override.

        :returns: A DevicePath containing parsed nodes.
        :raises: UefiError, SemanticError.
    """
    resolved = resolve_aspects(aspects, DEFAULT_CMD_ASPECTS)
    _reject_retry_patterns(resolved)

    nodes: list[DevicePathNode] = []
    offset = 0
    with MonitoredScope(delay=resolved.monitor_delay, name="devicepath.decode_binary"):
        while True:
            if offset + 4 > len(buf):
                err_msg = f"Truncated device-path header at offset {offset}."
                raise UefiError(err_msg)

            node_type = buf[offset]
            node_subtype = buf[offset + 1]
            node_length = int.from_bytes(buf[offset + 2:offset + 4], byteorder="little", signed=False)

            if node_length < 4:
                err_msg = f"Device-path node length {node_length} < 4 at offset {offset}."
                raise UefiError(err_msg)
            if offset + node_length > len(buf):
                err_msg = f"Device-path node length {node_length} exceeds buffer at offset {offset}."
                raise UefiError(err_msg)

            body = buf[offset + 4:offset + node_length]
            decoder = BINARY_DECODERS.get((node_type, node_subtype))
            if decoder is None:
                node = UnknownDevicePathNode(
                    type=node_type,
                    subtype=node_subtype,
                    data=bytes(body),
                )
            else:
                node = decoder(body)

            nodes.append(node)
            offset += node_length

            if isinstance(node, EndEntireNode):
                break

    rtnval = DevicePath(nodes=tuple(nodes))
    return rtnval
```

### Binary Encoder (binary/encoder.py)

```python
def encode_binary(
    path: DevicePath,
    aspects: Optional[UefiCmdAspects] = None,
) -> bytes:
    """
        Serialize a DevicePath to the concatenated binary form.

        :param path: The DevicePath to serialize.
        :param aspects: Optional aspects override.

        :returns: The concatenated bytes, terminated by an End-Entire node.
        :raises: UefiError, SemanticError.
    """
    resolved = resolve_aspects(aspects, DEFAULT_CMD_ASPECTS)
    _reject_retry_patterns(resolved)

    nodes = list(path.nodes)
    if len(nodes) == 0 or not isinstance(nodes[-1], EndEntireNode):
        nodes.append(EndEntireNode())

    out = bytearray()
    for node in nodes:
        body = node._encode_body()
        total_length = 4 + len(body)
        header = bytes([node.type, node.subtype]) + total_length.to_bytes(length=2, byteorder="little", signed=False)
        out.extend(header)
        out.extend(body)

    rtnval = bytes(out)
    return rtnval
```

### Text Encoder (text/encoder.py)

```python
def encode_text(
    path: DevicePath,
    aspects: Optional[UefiCmdAspects] = None,
) -> str: ...
```

Dispatch via `TEXT_ENCODERS: dict[type[DevicePathNode], Callable]`.
Nodes are joined with `/` per §10.6.1.1.  The trailing `EndEntireNode`
is rendered as the empty string (not emitted), matching the reference
`ConvertDevicePathToText()` behavior.

### Text Decoder (text/decoder.py)

```python
def decode_text(
    text: str,
    aspects: Optional[UefiCmdAspects] = None,
) -> DevicePath: ...
```

Flow:

1. `_lexer.Tokenizer(text)` produces a stream of tokens: `IDENT`,
   `LPAREN`, `RPAREN`, `COMMA`, `SLASH`, `NUMBER`, `STRING`, `EOF`.
2. `_parser.Parser(tokens).parse_device_path()` returns a `DevicePath`.
3. Each `IDENT(args...)` production is looked up in `TEXT_DECODERS` and
   the decoder consumes the args.

Productions:

```
device_path := node ( "/" node )* EOF
node        := IDENT "(" args ")"
args        := arg ( "," arg )*
arg         := NUMBER | STRING | HEX_RUN | IP_LITERAL | IDENT
```

### Registry Layout

```python
BINARY_DECODERS: dict[tuple[int, int], Callable[[bytes], DevicePathNode]] = {
    (0x01, 0x01): _decode_pci,
    (0x01, 0x02): _decode_pccard,
    (0x01, 0x03): _decode_memory_mapped,
    (0x01, 0x04): _decode_hardware_vendor,
    (0x01, 0x05): _decode_controller,
    (0x01, 0x06): _decode_bmc,
    (0x02, 0x01): _decode_acpi,
    (0x02, 0x02): _decode_expanded_acpi,
    (0x02, 0x03): _decode_acpi_adr,
    (0x02, 0x04): _decode_nvdimm,
    (0x03, 0x01): _decode_atapi,
    (0x03, 0x02): _decode_scsi,
    (0x03, 0x03): _decode_fibre_channel,
    (0x03, 0x05): _decode_usb,
    (0x03, 0x0B): _decode_mac_address,
    (0x03, 0x0C): _decode_ipv4,
    (0x03, 0x0D): _decode_ipv6,
    (0x03, 0x12): _decode_sata,
    (0x03, 0x13): _decode_iscsi,
    (0x03, 0x17): _decode_nvme_namespace,
    (0x03, 0x18): _decode_uri,
    (0x04, 0x01): _decode_hard_drive,
    (0x04, 0x02): _decode_cdrom,
    (0x04, 0x04): _decode_file_path,
    (0x04, 0x09): _decode_ram_disk,
    (0x05, 0x01): _decode_bbs,
    (0x7F, 0x01): _decode_end_of_hardware,
    (0x7F, 0xFF): _decode_end_entire,
    # ... remaining subtypes per §10.3.4 ...
}
```

## Data Models

### Generic Node Header (UEFI §10.3.1, 4 bytes)

| Offset | Size | Field     | Notes                                     |
|--------|------|-----------|-------------------------------------------|
| 0      | 1    | `Type`    | High-level family (1=HW, 2=ACPI, 3=MSG, 4=MEDIA, 5=BBS, 0x7F=End) |
| 1      | 1    | `SubType` | Subtype within family                     |
| 2      | 2    | `Length`  | LE UINT16; total node length incl. header |

### PCI Node (§10.3.2.1, 6 bytes total)

| Offset | Size | Field       | Notes           |
|--------|------|-------------|-----------------|
| 0      | 4    | Header      | type=1, sub=1   |
| 4      | 1    | `Function`  | UINT8           |
| 5      | 1    | `Device`    | UINT8           |

### IPv4 Node (§10.3.4.11, 27 bytes total)

| Offset | Size | Field            | Notes                          |
|--------|------|------------------|--------------------------------|
| 0      | 4    | Header           | type=3, sub=0x0C               |
| 4      | 4    | `LocalIpAddr`    | IPv4, big-endian per spec      |
| 8      | 4    | `RemoteIpAddr`   | IPv4                           |
| 12     | 2    | `LocalPort`      | UINT16 LE                      |
| 14     | 2    | `RemotePort`     | UINT16 LE                      |
| 16     | 2    | `Protocol`       | UINT16 LE                      |
| 18     | 1    | `StaticIp`       | 0 or 1                         |
| 19     | 4    | `Gateway`        | IPv4                           |
| 23     | 4    | `SubnetMask`     | IPv4                           |

### IPv6 Node (§10.3.4.12, 60 bytes total)

| Offset | Size | Field               | Notes                       |
|--------|------|---------------------|-----------------------------|
| 0      | 4    | Header              | type=3, sub=0x0D            |
| 4      | 16   | `LocalIpAddr`       | IPv6                        |
| 20     | 16   | `RemoteIpAddr`      | IPv6                        |
| 36     | 2    | `LocalPort`         | UINT16 LE                   |
| 38     | 2    | `RemotePort`        | UINT16 LE                   |
| 40     | 2    | `Protocol`          | UINT16 LE                   |
| 42     | 1    | `IpAddressOrigin`   | UINT8                       |
| 43     | 1    | `PrefixLength`      | UINT8                       |
| 44     | 16   | `GatewayIpAddr`     | IPv6                        |

### Hard Drive Node (§10.3.5.1, 42 bytes total)

| Offset | Size | Field                 | Notes                       |
|--------|------|-----------------------|-----------------------------|
| 0      | 4    | Header                | type=4, sub=1               |
| 4      | 4    | `PartitionNumber`     | UINT32 LE                   |
| 8      | 8    | `PartitionStart`      | UINT64 LE (LBA)             |
| 16     | 8    | `PartitionSize`       | UINT64 LE (LBAs)            |
| 24     | 16   | `PartitionSignature`  | GUID or MBR signature       |
| 40     | 1    | `PartitionFormat`     | 1=MBR, 2=GPT                |
| 41     | 1    | `SignatureType`       | 0=none, 1=MBR, 2=GPT GUID   |

### File Path Node (§10.3.5.4, variable)

| Offset | Size | Field    | Notes                                    |
|--------|------|----------|------------------------------------------|
| 0      | 4    | Header   | type=4, sub=4                            |
| 4      | var  | `Path`   | UCS-2 null-terminated path string        |

### URI Node (§10.3.4.22, variable)

| Offset | Size | Field   | Notes                                    |
|--------|------|---------|------------------------------------------|
| 0      | 4    | Header  | type=3, sub=0x18                         |
| 4      | var  | `Uri`   | UTF-8 string, no null terminator         |

### End-of-Hardware / End-Entire (§10.3.1, 4 bytes)

| Offset | Size | Field   | Notes                                    |
|--------|------|---------|------------------------------------------|
| 0      | 1    | `Type`  | 0x7F                                     |
| 1      | 1    | `SubType`| 0x01 (end-of-hardware) / 0xFF (end-entire) |
| 2      | 2    | `Length`| 4                                        |

## Measured Interop Contract

Every public entry point accepts `aspects: Optional[UefiCmdAspects] =
None`.  Resolution uses `resolve_aspects(passed, DEFAULT_CMD_ASPECTS)`.

Aspects fields consumed:

* `action_pattern` — validated; only `SINGLE_CALL` and
  `SINGLE_CONNECTED_CALL` are accepted.  All retry-flavored patterns
  raise `SemanticError`.
* `monitor_delay` — forwarded to a `MonitoredScope` wrapping the parse,
  so pathological inputs surface as observable long operations.
* `logger` — consulted only to emit a single `DEBUG` line per parse
  reporting node count and byte count.

Fields not consumed by this feature (documented for completeness):
`completion_timeout`, `completion_interval`, `connection_timeout`,
`connection_interval`, `inactivity_timeout`, `inactivity_interval`,
`allowed_error_codes`, `allowed_connection_exceptions`, `must_connect`,
`prompt_pattern`, `response_encoding`, `transport_kind`,
`expected_status`.

ActionPatterns supported:

| Pattern                          | Supported | Notes                                  |
|----------------------------------|-----------|----------------------------------------|
| `SINGLE_CALL`                    | Yes       | Default.                               |
| `SINGLE_CONNECTED_CALL`          | Yes       | Treated as `SINGLE_CALL` (no I/O).     |
| `DO_UNTIL_SUCCESS`               | No        | `SemanticError` — parse is deterministic. |
| `DO_WHILE_SUCCESS`               | No        | `SemanticError`.                       |
| `DO_UNTIL_CONNECTION_FAILURE`    | No        | `SemanticError`.                       |

## Error Handling

| Condition                                               | Exception       | `details`                            |
|---------------------------------------------------------|-----------------|--------------------------------------|
| Node header extends past buffer                         | `UefiError`     | offset, observed length              |
| Node length < 4                                         | `UefiError`     | offset, observed length              |
| Missing End-Entire terminator                           | `UefiError`     | final offset                         |
| File-path UCS-2 buffer not null-terminated              | `UefiError`     | offset, preview bytes                |
| File-path encode of non-BMP character                   | `UefiError`     | offending code point                 |
| URI node containing invalid UTF-8                       | `UefiError`     | offset                               |
| Text parse syntax error                                 | `UefiError`     | line, column, context, production    |
| Text decoder-name not in registry                       | `UefiError`     | name, offset                         |
| Unknown binary `(type, subtype)`                        | — (accepted)    | falls through to `UnknownDevicePathNode` |
| Retry-flavored `action_pattern`                         | `SemanticError` | pattern name                         |

## Logging

Channel: `uefi.devicepath`.

* `DEBUG` per public call — function name, input size, output node
  count, output size.
* `WARNING` when an `UnknownDevicePathNode` is produced during binary
  decode — `(type, subtype, data_len)`.
* No `INFO` / `ERROR` emissions from steady-state parsing; errors
  surface as raised exceptions.

## Security Considerations

* `decode_binary` is the primary attack surface: malicious device-path
  buffers could try to produce pathological allocation.  Each node's
  header length is validated against remaining buffer *before* any
  allocation; the outer parser never allocates more than `len(buf)`
  total bytes.
* `decode_text` similarly is bounded: the lexer rejects tokens longer
  than `2 * len(input)` to block adversarial quoting.
* The codec holds no credentials.

## Testing Strategy

### Unit — per-node round-trip

For every node class, a hand-written positive fixture in
`tests/fixtures/devicepath/<family>/<subtype>.bin` with a companion
`.expected.json` and `.expected.text`:

* `decode_binary(fixture) == expected`.
* `encode_binary(expected) == fixture`.
* `encode_text(expected) == expected_text`.
* `decode_text(expected_text) == expected`.

### Unit — spec example corpus

Every canonical example from UEFI §10.6 captured as a fixture:

* Binary → text: matches spec byte-for-byte.
* Text → binary: round-trips.

### Unit — unknown-node fallthrough

* Synthesize a node with `(type=0x06, subtype=0x12, data=b"\xab\xcd")`
  (an unassigned pair at the time of writing).  Decode → produces
  `UnknownDevicePathNode`.  Encode text → `Path(6,18,abcd)`.  Round
  trip through text and binary both preserve bytes.

### Property-based — `hypothesis`

* Strategy composes random trees containing at least one of every
  registered node type.  Assertions:
  - `encode_binary(decode_binary(encode_binary(t))) == encode_binary(t)`.
  - `decode_text(encode_text(t)) == t`.
* Failing examples are minimized automatically by `hypothesis`.

### Negative — malformed inputs

* Truncated header, over-declared length, missing End-Entire, file-path
  without null terminator, URI with invalid UTF-8, text parser
  unexpected-comma — each raises `UefiError` with the documented
  `details`.

### Action-pattern tests

* `SINGLE_CALL`, `SINGLE_CONNECTED_CALL` succeed.
* `DO_UNTIL_SUCCESS`, `DO_WHILE_SUCCESS`,
  `DO_UNTIL_CONNECTION_FAILURE` each raise `SemanticError`.
* `aspects=None` resolves from `DEFAULT_CMD_ASPECTS`.

### No-pyparsing assertion

* A test walks imports from `text/decoder.py` and asserts that no
  third-party parsing library is reachable.

## Dependencies on Other Features

* `1-aspects` — `UefiCmdAspects`, `resolve_aspects`, `ActionPattern`,
  `DEFAULT_CMD_ASPECTS`.
* `1-constants-and-types` — `guid_from_bytes`, `guid_to_bytes`,
  well-known GUIDs used by Messaging/Media vendor-defined subtypes.
* `1-exceptions` — `UefiError`, `SemanticError`, `AbstractMethodError`.
* `1-helpers` — UCS-2 encode/decode, `MonitoredScope`.
* `1-logging` — `uefi.devicepath` channel.

Downstream consumers: `5-boot-manager` (via `BootOption.render()` and
`BootOption.for_file_path` / `for_http_boot`), `5-network-boot` (URI
node construction), `5-gpt-mbr-parser` (signature cross-referencing
through partition-signature GUID identity), any caller manipulating
device-path strings.

Test-only dependency: `hypothesis`.
