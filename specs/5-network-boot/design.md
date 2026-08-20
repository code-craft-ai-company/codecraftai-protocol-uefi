# Design — Network Boot

## Overview

Network Boot composes 5-device-path-codec and 5-boot-manager into a focused
API that builds `EFI_LOAD_OPTION` entries for UEFI HTTP Boot (UEFI §24.7) and
PXE boot (UEFI §24.3) over IPv4 and IPv6.  The feature ships three builders,
an installer, and a one-shot trigger.  All construction is pure-Python;
I/O is delegated to 5-boot-manager so that this feature needs no transport
knowledge.

The feature's key invariant is round-trip fidelity: any option it produces
must encode and decode back to itself, and must render to a UEFI §10.6
text form that `ConvertDevicePathFromText()` accepts.

## Architecture

```
ccai.protocols.uefi.netboot/
    __init__.py           # re-exports public surface
    httpbuilder.py        # build_http_boot_option
    pxebuilder.py         # build_pxe_boot_option_ipv4, build_pxe_boot_option_ipv6
    installer.py          # install, trigger_one_shot
    options.py            # LoadOptionAttrFlags helper
    netbootfacade.py      # NetworkBoot facade wiring agent + boot-manager
```

Dependency direction:

```
options.py ── httpbuilder.py ──┐
                               ├── installer.py ── netbootfacade.py
             pxebuilder.py ────┘
```

`installer.py` imports 5-boot-manager's `BootManager`; builders import
5-device-path-codec's node constructors only.

## Components and Interfaces

### `NetworkBoot` facade (netbootfacade.py)

```python
class NetworkBoot:
    """
        Facade for building, installing, and triggering UEFI network
        boot options (HTTP, PXE over IPv4/IPv6).
    """

    def __init__(
        self,
        agent: "UefiAgent",
        boot_manager: "BootManager",
        aspects: Optional[UefiCmdAspects] = None,
    ) -> None:
        self._agent = agent
        self._boot_manager = boot_manager
        self._aspects = aspects
        return

    def build_http_boot_option(
        self,
        uri: str,
        description: str,
        mac_address: Optional[bytes] = None,
        hidden: bool = False,
        category_app: bool = False,
        optional_data: bytes = b"",
    ) -> "BootOption":
        """
            Construct a BootOption for UEFI HTTP Boot per §24.7.

            :param uri: The http:// or https:// URI to boot from.
            :param description: Human-readable option description.
            :param mac_address: Optional 6-byte MAC to bind the boot to.
            :param hidden: Set LOAD_OPTION_HIDDEN.
            :param category_app: Select LOAD_OPTION_CATEGORY_APP.
            :param optional_data: Appended OptionalData bytes.

            :returns: BootOption (not yet installed).
            :raises: ConfigurationError
        """
        rtnval = _build_http_boot_option(
            uri=uri,
            description=description,
            mac_address=mac_address,
            hidden=hidden,
            category_app=category_app,
            optional_data=optional_data,
        )
        return rtnval

    def build_pxe_boot_option_ipv4(
        self,
        description: str,
        mac_address: bytes,
        local_ip: Optional[bytes] = None,
        remote_ip: Optional[bytes] = None,
        hidden: bool = False,
        optional_data: bytes = b"",
    ) -> "BootOption": ...

    def build_pxe_boot_option_ipv6(
        self,
        description: str,
        mac_address: bytes,
        local_ip: Optional[bytes] = None,
        remote_ip: Optional[bytes] = None,
        hidden: bool = False,
        optional_data: bytes = b"",
    ) -> "BootOption": ...

    def install(
        self,
        boot_option: "BootOption",
        slot: Optional[int] = None,
        append_to_boot_order: bool = True,
        aspects: Optional[UefiCmdAspects] = None,
    ) -> int:
        """
            Install a BootOption into firmware.

            :returns: Assigned Boot#### slot index.
            :raises: UefiVariableError, ConfigurationError
        """
        effective = resolve_aspects(aspects, self._aspects)
        rtnval = _install_option(
            boot_manager=self._boot_manager,
            option=boot_option,
            slot=slot,
            append_to_boot_order=append_to_boot_order,
            aspects=effective,
        )
        return rtnval

    def trigger_one_shot(
        self,
        slot: int,
        verify: bool = False,
        aspects: Optional[UefiCmdAspects] = None,
    ) -> None:
        """
            Write BootNext = slot for one-shot network boot.

            :raises: UefiVariableError
        """
        effective = resolve_aspects(aspects, self._aspects)
        _trigger_one_shot(
            boot_manager=self._boot_manager,
            slot=slot,
            verify=verify,
            aspects=effective,
        )
        return
```

### `build_http_boot_option` (httpbuilder.py)

```python
def _build_http_boot_option(
    uri: str,
    description: str,
    mac_address: Optional[bytes],
    hidden: bool,
    category_app: bool,
    optional_data: bytes,
) -> "BootOption":
    _validate_uri(uri=uri)
    if mac_address is not None:
        _validate_mac(mac_address=mac_address)

    nodes = []
    if mac_address is not None:
        mac_node = build_mac_address_node(mac=mac_address, if_type=0x01)
        nodes.append(mac_node)

    uri_node = build_uri_node(uri=uri)
    nodes.append(uri_node)

    end_node = build_end_of_path_node()
    nodes.append(end_node)

    device_path = DevicePath(nodes=tuple(nodes))

    attributes = _assemble_attributes(hidden=hidden, category_app=category_app)

    rtnval = BootOption(
        attributes=attributes,
        description=description,
        device_path=device_path,
        optional_data=optional_data,
    )
    return rtnval
```

Helpers `build_mac_address_node`, `build_uri_node`, and
`build_end_of_path_node` are imported from 5-device-path-codec.

### `build_pxe_boot_option_ipv4` / `_ipv6` (pxebuilder.py)

Structurally identical to HTTP builder but emits a MAC node followed by an
IPv4 or IPv6 device-path node with packed `LocalIpAddress`,
`RemoteIpAddress`, `LocalPort`, `RemotePort`, and `Protocol` fields per
UEFI §10.3.4.11 (IPv4) and §10.3.4.12 (IPv6).

### `install` (installer.py)

```python
def _install_option(
    boot_manager: "BootManager",
    option: "BootOption",
    slot: Optional[int],
    append_to_boot_order: bool,
    aspects: UefiCmdAspects,
) -> int:
    if slot is None:
        assigned_slot = boot_manager.allocate_free_slot(aspects=aspects)
    else:
        existing = boot_manager.read_option(slot=slot, aspects=aspects)
        if existing is not None:
            err_msg_lines = [f"Slot Boot{slot:04X} already occupied."]
            err_msg_lines.append(f"EXISTING: description={existing.description!r}")
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiVariableError(
                err_msg,
                name=f"Boot{slot:04X}",
                details=[f"existing_description={existing.description}"],
            )
        assigned_slot = slot

    boot_manager.write_option(slot=assigned_slot, option=option, aspects=aspects)

    if append_to_boot_order is True:
        boot_manager.append_to_boot_order(slot=assigned_slot, aspects=aspects)

    rtnval = assigned_slot
    return rtnval
```

### `trigger_one_shot` (installer.py)

```python
def _trigger_one_shot(
    boot_manager: "BootManager",
    slot: int,
    verify: bool,
    aspects: UefiCmdAspects,
) -> None:
    existing = boot_manager.read_option(slot=slot, aspects=aspects)
    if existing is None:
        err_msg = f"BootNext target Boot{slot:04X} does not exist."
        raise UefiVariableError(err_msg, name=f"Boot{slot:04X}")

    boot_manager.write_boot_next(slot=slot, aspects=aspects)

    if verify is True:
        readback = boot_manager.read_boot_next(aspects=aspects)
        if readback != slot:
            err_msg_lines = ["BootNext readback disagrees with write."]
            err_msg_lines.append(f"WROTE: {slot}")
            err_msg_lines.append(f"READ:  {readback}")
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiVariableError(err_msg, name="BootNext")

    return
```

## Data Models

### `BootOption` (re-exported from 5-boot-manager)

Frozen dataclass with fields `attributes: int`, `description: str`,
`device_path: DevicePath`, `optional_data: bytes`.

### URI device-path node binary layout (UEFI §10.3.5.23)

| Offset | Size       | Field   | Notes                                       |
|-------:|-----------:|---------|---------------------------------------------|
| 0      | 1          | Type    | 0x03 (Messaging Device Path).               |
| 1      | 1          | SubType | 0x18 (URI).                                 |
| 2      | 2          | Length  | UINT16 little-endian; total node length.    |
| 4      | Length - 4 | Uri[]   | ASCII URI bytes, no terminator required.    |

### MAC-address device-path node binary layout (UEFI §10.3.4.10)

| Offset | Size | Field      | Notes                                                  |
|-------:|-----:|------------|--------------------------------------------------------|
| 0      | 1    | Type       | 0x03 (Messaging).                                      |
| 1      | 1    | SubType    | 0x0B (MAC Address).                                    |
| 2      | 2    | Length     | 0x25 (37).                                             |
| 4      | 32   | MacAddress | Padded with zeros beyond the HW-address length.        |
| 36     | 1    | IfType     | RFC 1700 network-interface type (1 = Ethernet).        |

### IPv4 device-path node binary layout (UEFI §10.3.4.11)

| Offset | Size | Field              | Notes                                   |
|-------:|-----:|--------------------|-----------------------------------------|
| 0      | 1    | Type               | 0x03.                                   |
| 1      | 1    | SubType            | 0x0C.                                   |
| 2      | 2    | Length             | 0x1B (27).                              |
| 4      | 4    | LocalIpAddress     | Big-endian IPv4.                        |
| 8      | 4    | RemoteIpAddress    | Big-endian IPv4.                        |
| 12     | 2    | LocalPort          | UINT16 little-endian.                   |
| 14     | 2    | RemotePort         | UINT16 little-endian.                   |
| 16     | 2    | Protocol           | IANA protocol number (6 = TCP).         |
| 18     | 1    | StaticIpAddress    | 1 if local IP is statically assigned.   |
| 19     | 4    | GatewayIpAddress   | Big-endian.                             |
| 23     | 4    | SubnetMask         | Big-endian.                             |

### IPv6 device-path node binary layout (UEFI §10.3.4.12)

| Offset | Size | Field              | Notes                                    |
|-------:|-----:|--------------------|------------------------------------------|
| 0      | 1    | Type               | 0x03.                                    |
| 1      | 1    | SubType            | 0x0D.                                    |
| 2      | 2    | Length             | 0x3C (60).                               |
| 4      | 16   | LocalIpAddress     | Network-byte-order 128-bit.              |
| 20     | 16   | RemoteIpAddress    | Network-byte-order 128-bit.              |
| 36     | 2    | LocalPort          | UINT16 little-endian.                    |
| 38     | 2    | RemotePort         | UINT16 little-endian.                    |
| 40     | 2    | Protocol           | IANA protocol number.                    |
| 42     | 1    | IpAddressOrigin    | 0 = manual, 1 = stateless, 2 = stateful. |
| 43     | 1    | PrefixLength       | UINT8.                                   |
| 44     | 16   | GatewayIpAddress   | Network-byte-order 128-bit.              |

### `EFI_LOAD_OPTION` binary layout (UEFI §3.1.3)

| Offset | Size              | Field              |
|-------:|------------------:|--------------------|
| 0      | 4                 | Attributes         |
| 4      | 2                 | FilePathListLength |
| 6      | 2 × (len+1)       | Description (UCS-2, NUL-terminated) |
| var    | FilePathListLength| FilePathList (DevicePath) |
| var    | remainder         | OptionalData       |

## Measured Interop Contract

Consumed `aspects` fields (all delegated through 5-boot-manager):

* `action_pattern` — `install` and `trigger_one_shot` route through the
  4-base action-pattern engine via the 5-boot-manager calls they make.
* `completion_timeout`, `completion_interval` — drive retry in
  `DO_UNTIL_SUCCESS` install-then-verify loops.
* `connection_timeout`, `connection_interval`,
  `allowed_connection_exceptions` — consumed by
  `SINGLE_CONNECTED_CALL` retries in the underlying variable write.
* `allowed_error_codes` — passed through to the shell-based write backend
  of 5-variable-services.
* `logging_pattern` — routes log emission on channel `uefi.netboot`.
* `logger` — injected logger; if `None`, `get_uefi_logger("uefi.netboot")`.

Supported `ActionPattern` values:

* `ActionPattern.SINGLE_CALL`
* `ActionPattern.SINGLE_CONNECTED_CALL`
* `ActionPattern.DO_UNTIL_SUCCESS`

`DO_WHILE_SUCCESS` and `DO_UNTIL_CONNECTION_FAILURE` SHALL raise
`SemanticError` — they are not meaningful for a short-lived install
operation.

Pure builders (`build_http_boot_option`, `build_pxe_boot_option_*`) do not
consume aspects.

## Error Handling

| Condition                                                  | Exception            | Included in `details`                                |
|------------------------------------------------------------|----------------------|------------------------------------------------------|
| Non-string URI                                             | `ConfigurationError` | observed type                                        |
| Unsupported URI scheme                                     | `ConfigurationError` | observed scheme, supported schemes                   |
| `mac_address` wrong length                                 | `ConfigurationError` | observed length, expected 6                          |
| IPv4 address not 4 bytes                                   | `ConfigurationError` | field name, observed length                          |
| IPv6 address not 16 bytes                                  | `ConfigurationError` | field name, observed length                          |
| Install into occupied slot                                  | `UefiVariableError`  | slot index, existing description                     |
| `trigger_one_shot` target slot missing                      | `UefiVariableError`  | slot index                                           |
| `trigger_one_shot` verify readback mismatch                 | `UefiVariableError`  | written slot, readback value                         |
| Variable store full / read-only at runtime                 | propagated           | as raised by 5-boot-manager                          |
| Unsupported `ActionPattern` requested                       | `SemanticError`      | requested pattern, supported patterns                |

## Logging

* Channel: `uefi.netboot`.
* INFO: option constructed, option installed into slot, `BootNext` written.
* DEBUG: encoded `DevicePath` byte-hex, attribute flags, optional-data length.
* WARNING: retryable failures during install.
* ERROR: terminal failures before re-raise.

## Security Considerations

* **No TLS trust management.**  For `https://` URIs, firmware performs TLS
  validation using its own trust store (typically via the HTTP Boot TLS
  configuration section of UEFI §24.7.6).  This feature does not touch
  that store; callers manage it through secure-boot / HTTPS-TLS certificate
  variables.
* **URL validation is syntactic only.**  A URI may point at a malicious
  server.  This feature does not reason about destinations; that is a
  caller-policy concern.
* **`optional_data` passthrough.**  Opaque bytes are written verbatim.
  Callers with firmware-specific option-data schemas are responsible for
  their correctness.
* **Variable-store quota.**  Large `optional_data` or descriptions can
  exhaust `QueryVariableInfo` remaining space; 5-variable-services raises
  on that condition and this feature propagates.

## Testing Strategy

### Unit tests (`test_httpbuilder.py`)

* `http://` and `https://` both accepted.
* Non-string URI raises `ConfigurationError`.
* Unsupported scheme (`tftp://`) raises `ConfigurationError`.
* MAC-address prefix present when `mac_address` is provided.
* `LOAD_OPTION_ACTIVE` set by default; `LOAD_OPTION_HIDDEN` additive;
  `LOAD_OPTION_CATEGORY_APP` vs. `_CATEGORY_BOOT`.
* `optional_data` bytes appended verbatim.

### Unit tests (`test_pxebuilder.py`)

* IPv4 zero-IP default (DHCP path) produced as all-zero bytes.
* IPv4 explicit IP written big-endian.
* IPv6 zero-IP default.
* IPv6 explicit 128-bit address written correctly.
* MAC-length validation.

### Round-trip test (`test_roundtrip.py`)

* For every builder: build → `BootOption.encode()` →
  `BootOption.decode()` → equal.
* For every builder: build → encode → decode → `to_text` → `from_text` →
  encode → equal.

### Installer tests (`test_installer.py`)

* `install` with `slot=None` calls `allocate_free_slot` and returns the
  allocated slot.
* `install` into occupied slot raises `UefiVariableError`.
* `append_to_boot_order=True` (default) calls `append_to_boot_order`.
* `append_to_boot_order=False` does not call it.
* `trigger_one_shot` with missing slot raises.
* `trigger_one_shot(verify=True)` detects readback mismatch.

### Action-pattern tests (`test_actionpatterns.py`)

* `SINGLE_CALL` — single write.
* `SINGLE_CONNECTED_CALL` — retries connection class.
* `DO_UNTIL_SUCCESS` — retries until success or timeout.
* `DO_WHILE_SUCCESS` / `DO_UNTIL_CONNECTION_FAILURE` raise
  `SemanticError`.
* `aspects=None` falls back to instance default.

### Integration tests (`test_netboot_integration.py`)

* Against an OVMF VM: install HTTP option pointing at a local HTTP server,
  `BootNext`, reboot, verify the VM fetched and attempted to boot.
* Gated on `CCAI_UEFI_OVMF_VM` env var.

### Coverage target

≥ 95% line coverage for `httpbuilder.py`, `pxebuilder.py`, `installer.py`.

## Dependencies on Other Features

* 1-aspects — consumes `UefiCmdAspects`, `resolve_aspects`,
  `ActionPattern`, `LoggingPattern`.
* 1-exceptions — raises `ConfigurationError`, `UefiVariableError`,
  `SemanticError`.
* 1-logging — emits on `uefi.netboot` channel.
* 5-device-path-codec — URI / MAC / IPv4 / IPv6 / end node constructors,
  text-form round-trip.
* 5-boot-manager — `BootOption` type, `allocate_free_slot`,
  `read_option`, `write_option`, `append_to_boot_order`,
  `read_boot_next`, `write_boot_next`.
* 5-variable-services (transitively via 5-boot-manager).
