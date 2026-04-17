# Tasks — Device Path Codec

- [ ] 1. Create package skeleton at
      `source/packages/automojo/protocols/uefi/devicepath/`.
  - [ ] 1.1 Add `__init__.py` re-exporting `DevicePath`,
        `DevicePathNode`, every concrete node class,
        `decode_binary`, `encode_binary`, `decode_text`,
        `encode_text`, and the `UnknownDevicePathNode` fallthrough
        type.
  - [ ] 1.2 Add a module-level docstring citing UEFI §10 subsections.
  - [ ] 1.3 Create `nodes/`, `binary/`, `text/` sub-packages.
  - References: Requirement 1, Requirement 13.

- [ ] 2. Implement node base (`nodes/base.py`).
  - [ ] 2.1 Author `DevicePathNode` frozen base dataclass with
        `type`, `subtype`, and an `_encode_body()` abstract.
  - [ ] 2.2 Author `DevicePath(nodes)` container.
  - [ ] 2.3 Author `EndOfHardwareNode`, `EndEntireNode` as singletons
        in `nodes/end.py`.
  - [ ] 2.4 Add tests for base equality, iteration, and header-length
        computation.
  - References: Requirement 3, Requirement 9.

- [ ] 3. Implement Hardware node types (`nodes/hardware.py`).
  - [ ] 3.1 Author `PciNode`, `PccardNode`, `MemoryMappedNode`,
        `HardwareVendorNode`, `ControllerNode`, `BmcNode`.
  - [ ] 3.2 Author `_encode_body()` for each.
  - [ ] 3.3 Author the companion binary decoders in
        `binary/decoder.py` keyed by `(0x01, subtype)`.
  - [ ] 3.4 Collect fixture `.bin` files for each subtype.
  - [ ] 3.5 Add parametrized round-trip tests.
  - References: Requirement 4.

- [ ] 4. Implement ACPI node types (`nodes/acpi.py`).
  - [ ] 4.1 Author `AcpiNode`, `ExpandedAcpiNode`, `AcpiAdrNode`,
        `NvdimmNode`.
  - [ ] 4.2 Author `_encode_body()` for each.
  - [ ] 4.3 Register `(0x02, subtype)` decoders.
  - [ ] 4.4 Add round-trip tests for each including multi-address
        `_ADR` variants.
  - References: Requirement 5.

- [ ] 5. Implement Messaging node types (`nodes/messaging.py`).
  - [ ] 5.1 Author `AtapiNode`, `ScsiNode`, `FibreChannelNode`,
        `UsbNode`, `UsbWwidNode`, `DeviceLogicalUnitNode`,
        `MacAddressNode`, `IPv4Node`, `IPv6Node`, `VlanNode`,
        `InfinibandNode`, `UartNode`, `VendorMessagingNode`,
        `UartFlowControlNode`, `SasNode`, `SasExtendedNode`,
        `IscsiNode`, `NvmeNamespaceNode`, `UriNode`, `UfsNode`,
        `SdNode`, `BluetoothNode`, `WirelessNode`, `EmmcNode`,
        `BluetoothLeNode`, `DnsNode`, `NvdimmNamespaceNode`,
        `RestServiceNode`, `NvmeOfNamespaceNode`.
  - [ ] 5.2 Author `_encode_body()` for each.
  - [ ] 5.3 Register `(0x03, subtype)` decoders per §10.3.4.
  - [ ] 5.4 Collect fixture `.bin` files covering at minimum PCI+USB
        chains, SATA, NVMe, iSCSI, IPv4/IPv6, URI, MAC.
  - [ ] 5.5 Add tests that IP address fields round-trip through
        `ipaddress.IPv4Address` / `IPv6Address`.
  - References: Requirement 6.

- [ ] 6. Implement Media node types (`nodes/media.py`).
  - [ ] 6.1 Author `HardDriveNode`, `CdRomNode`,
        `MediaVendorNode`, `FilePathNode`, `MediaProtocolNode`,
        `PiwgFirmwareFileNode`, `PiwgFirmwareVolumeNode`,
        `RelativeOffsetRangeNode`, `RamDiskNode`.
  - [ ] 6.2 Author `_encode_body()` for each.
  - [ ] 6.3 Register `(0x04, subtype)` decoders.
  - [ ] 6.4 Implement FilePathNode UCS-2 null-termination check;
        raise `UefiError` on missing terminator or non-BMP character
        at encode.
  - [ ] 6.5 Add fixtures and round-trip tests.
  - References: Requirement 7.

- [ ] 7. Implement BBS nodes (`nodes/bbs.py`).
  - [ ] 7.1 Author `BbsNode(device_type, status_flag, description)`.
  - [ ] 7.2 Author `_encode_body()` (ASCII description).
  - [ ] 7.3 Register `(0x05, 0x01)` decoder.
  - [ ] 7.4 Add round-trip tests.
  - References: Requirement 8.

- [ ] 8. Implement the unknown-node fallthrough (`nodes/unknown.py`).
  - [ ] 8.1 Author `UnknownDevicePathNode(type, subtype, data)`.
  - [ ] 8.2 Implement `_encode_body()` returning `data` verbatim.
  - [ ] 8.3 Implement the decoder fallthrough inside
        `binary/decoder.py`.
  - [ ] 8.4 Add tests synthesizing an unassigned `(type, subtype)` and
        verifying binary and text round-trips.
  - References: Requirement 10.

- [ ] 9. Implement binary helpers and encoder.
  - [ ] 9.1 Author `binary/_helpers.py` with `read_uint8`,
        `read_uint16_le`, `read_uint32_le`, `read_uint64_le`,
        `read_guid`, `read_ucs2z`, `read_ascii` cursor-style readers.
  - [ ] 9.2 Author `encode_binary(path, aspects)` per design.
  - [ ] 9.3 Ensure encoder appends a trailing `EndEntireNode` when
        absent.
  - [ ] 9.4 Add tests for empty path, single-node path, and 100-node
        path.
  - References: Requirement 3, Requirement 9.

- [ ] 10. Implement binary decoder glue.
  - [ ] 10.1 Author `decode_binary(buf, aspects)` per design, with the
        registry-driven dispatch and the unknown-node fallthrough.
  - [ ] 10.2 Implement retry-pattern rejection raising
        `SemanticError`.
  - [ ] 10.3 Implement `MonitoredScope` wrap.
  - [ ] 10.4 Add negative tests: truncated header, length < 4,
        length > remaining, missing End-Entire.
  - References: Requirement 2, Requirement 3, Requirement 9,
    Requirement 10.

- [ ] 11. Implement text encoder.
  - [ ] 11.1 Author `encode_text(path, aspects)` per design.
  - [ ] 11.2 Author per-node text encoders in `text/encoder.py`
        keyed by node class, producing spec-exact text per §10.6.
  - [ ] 11.3 Implement `UnknownDevicePathNode` → `Path(t,s,hex)`
        rendering per §10.6.1.6.
  - [ ] 11.4 Add parametrized tests against every canonical §10.6
        example.
  - References: Requirement 10.3, Requirement 11.

- [ ] 12. Implement text lexer.
  - [ ] 12.1 Author `text/_lexer.py` producing `IDENT`, `LPAREN`,
        `RPAREN`, `COMMA`, `SLASH`, `NUMBER`, `STRING`, `HEX_RUN`,
        `IP_LITERAL`, `EOF` tokens.
  - [ ] 12.2 Implement token-length bound (reject tokens longer than
        `2 * len(input)`) to block adversarial quoting.
  - [ ] 12.3 Add tests for every token kind, plus positioned-error
        reports (line, column).
  - References: Requirement 12.

- [ ] 13. Implement text recursive-descent parser.
  - [ ] 13.1 Author `text/_parser.py` with `parse_device_path`,
        `parse_node`, `parse_args`, `parse_arg` methods.
  - [ ] 13.2 Implement dispatch via
        `TEXT_DECODERS: dict[str, Callable]`.
  - [ ] 13.3 On unknown IDENT raise `UefiError` with `details`
        naming the offending IDENT and its position.
  - [ ] 13.4 Add an import-graph test asserting no third-party
        parser libraries are imported.
  - References: Requirement 12, Requirement 13.

- [ ] 14. Author the §10.6 canonical-example fixture corpus.
  - [ ] 14.1 Transcribe every example in §10.6 into
        `tests/fixtures/devicepath/spec/<example>.bin` and
        `<example>.text` pairs.
  - [ ] 14.2 Add `test_spec_corpus.py` asserting binary → text and
        text → binary round-trip byte-identical.
  - [ ] 14.3 Add a CI gate that fails if a fixture file exists
        without a matching test parametrization.
  - References: Requirement 11.

- [ ] 15. Hypothesis property-based round-trip tests.
  - [ ] 15.1 Add `hypothesis` to the test-only dependency list.
  - [ ] 15.2 Author strategies for each node class producing well-
        formed instances (valid IPv4/IPv6 addresses, BMP-only
        UCS-2 strings, etc.).
  - [ ] 15.3 Author a composite strategy for `DevicePath` trees.
  - [ ] 15.4 Add the `encode(decode(encode(t))) == encode(t)`
        property for binary.
  - [ ] 15.5 Add the `decode(encode(t)) == t` property for text.
  - [ ] 15.6 Register a `hypothesis.settings(deadline=None)` profile
        in `conftest.py`.
  - References: Requirement 14.
