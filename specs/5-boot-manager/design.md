# Design — Boot Manager

## Overview

Boot Manager delivers `BootManager`, a façade class over
5-variable-services that exposes the `Boot####`, `BootOrder`, `BootNext`,
`BootCurrent`, `Timeout`, `Driver####`, `DriverOrder`, `SysPrep####`, and
`SysPrepOrder` global-namespace variables defined by UEFI §3.3.  The
feature also provides `BootOption`, a frozen dataclass representing
`EFI_LOAD_OPTION` per UEFI §3.1.3, with symmetric `decode` / `encode`
methods and factory classmethods for common shapes.

`BootManager` is transport-agnostic by construction: every I/O goes
through `VariableStore`.  Atomic order updates are implemented as a
read-modify-write loop that re-reads after write and re-applies the
caller's transform if the observed pre-state changed between read and
write (best-effort optimistic concurrency).

Human-readable rendering delegates to 5-device-path-codec for the
embedded `FilePathList`.  The codec dependency is imported only inside
the `render()` method so `BootManager` remains usable in environments
where the codec is not available (the raw bytes are always exposed).

## Architecture

```
automojo.protocols.uefi.bootmanager/
    __init__.py        # re-exports public surface
    attributes.py      # LoadOptionAttributes IntFlag
    load_option.py     # BootOption dataclass + decode/encode + factories
    manager.py         # BootManager facade
    order_codec.py     # pack_u16_array / unpack_u16_array helpers
    render.py          # BootOption.render() implementation
```

Dependency direction (bottom-up):

```
manager.py ──▶ load_option.py ──▶ attributes.py ──▶ order_codec.py
           ──▶ 5-variable-services
render.py  ──▶ load_option.py
           ──▶ 5-device-path-codec (deferred import)
```

## Components and Interfaces

### `LoadOptionAttributes` (attributes.py)

```python
class LoadOptionAttributes(IntFlag):
    LOAD_OPTION_ACTIVE = 0x00000001
    LOAD_OPTION_FORCE_RECONNECT = 0x00000002
    LOAD_OPTION_HIDDEN = 0x00000008
    LOAD_OPTION_CATEGORY_BOOT = 0x00000000  # value 0 — sentinel
    LOAD_OPTION_CATEGORY_APP = 0x00000100

    CATEGORY_MASK = 0x00001F00

    @classmethod
    def category_of(cls, attrs: int) -> int:
        """
            Extract the LOAD_OPTION_CATEGORY sub-field.

            :param attrs: The raw 32-bit attributes value.

            :returns: The category code masked to CATEGORY_MASK.
        """
        rtnval = attrs & int(cls.CATEGORY_MASK)
        return rtnval
```

### `BootOption` (load_option.py)

```python
@dataclass(frozen=True)
class BootOption:
    attributes: int
    description: str
    file_path_bytes: bytes
    optional_data: bytes = b""

    @classmethod
    def decode(cls, buf: bytes) -> "BootOption":
        """
            Decode a Boot####/Driver####/SysPrep#### variable body.

            :param buf: The raw variable body bytes per UEFI §3.1.3.

            :returns: A BootOption.
            :raises: UefiVariableError.
        """
        if len(buf) < 6:
            err_msg_lines = ["EFI_LOAD_OPTION buffer is too short."]
            err_msg_lines.append(f"EXPECTED: >= 6 bytes; GOT: {len(buf)}.")
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiVariableError(err_msg)

        attributes = int.from_bytes(buf[0:4], byteorder="little", signed=False)
        fpl_length = int.from_bytes(buf[4:6], byteorder="little", signed=False)

        desc_end = cls._find_ucs2_null_terminator(buf, start=6)
        description = buf[6:desc_end].decode("utf-16-le")

        fp_start = desc_end + 2
        fp_end = fp_start + fpl_length
        if fp_end > len(buf):
            err_msg_lines = ["FilePathListLength exceeds remaining buffer."]
            err_msg_lines.append(f"FilePathListLength: {fpl_length}")
            err_msg_lines.append(f"Remaining: {len(buf) - fp_start}")
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiVariableError(err_msg)

        file_path_bytes = bytes(buf[fp_start:fp_end])
        optional_data = bytes(buf[fp_end:])

        rtnval = cls(
            attributes=attributes,
            description=description,
            file_path_bytes=file_path_bytes,
            optional_data=optional_data,
        )
        return rtnval

    def encode(self) -> bytes:
        """
            Serialize this BootOption as a Boot####/Driver####/SysPrep####
            variable body.

            :returns: The raw bytes per UEFI §3.1.3.
            :raises: UefiError on non-BMP characters in description.
        """
        try:
            desc_bytes = self.description.encode("utf-16-le")
        except UnicodeEncodeError as exc:
            err_msg = "BootOption.description must be strict UCS-2 (BMP-only)."
            raise UefiError(err_msg) from exc

        fpl_length = len(self.file_path_bytes)
        attr_bytes = int(self.attributes).to_bytes(length=4, byteorder="little", signed=False)
        fpl_bytes = fpl_length.to_bytes(length=2, byteorder="little", signed=False)

        rtnval = attr_bytes + fpl_bytes + desc_bytes + b"\x00\x00" + self.file_path_bytes + self.optional_data
        return rtnval

    @classmethod
    def for_file_path(
        cls,
        description: str,
        volume_alias: str,
        path: str,
        optional_data: bytes = b"",
        attributes: int = LoadOptionAttributes.LOAD_OPTION_ACTIVE,
    ) -> "BootOption": ...

    @classmethod
    def for_http_boot(
        cls,
        description: str,
        uri: str,
        optional_data: bytes = b"",
        attributes: int = LoadOptionAttributes.LOAD_OPTION_ACTIVE,
    ) -> "BootOption": ...

    def render(self) -> str: ...
```

### `BootManager` (manager.py)

```python
class BootManager:
    def __init__(
        self,
        store: "VariableStore",
        aspects: Optional[UefiCmdAspects] = None,
    ) -> None:
        self._store: "VariableStore" = store
        self._aspects: UefiCmdAspects = aspects if aspects is not None else store._aspects
        self._vendor_guid: uuid.UUID = uuid.UUID("8BE4DF61-93CA-11D2-AA0D-00E098032B8C")  # EFI_GLOBAL_VARIABLE_GUID
        return

    def read_boot_option(self, option_id: int, aspects: Optional[UefiCmdAspects] = None) -> BootOption: ...
    def write_boot_option(self, option_id: int, option: BootOption, aspects: Optional[UefiCmdAspects] = None) -> None: ...
    def delete_boot_option(self, option_id: int, aspects: Optional[UefiCmdAspects] = None) -> None: ...

    def read_boot_order(self, aspects: Optional[UefiCmdAspects] = None) -> tuple[int, ...]: ...
    def write_boot_order(self, new_order: Sequence[int], aspects: Optional[UefiCmdAspects] = None) -> None: ...
    def update_boot_order(
        self,
        transform: Callable[[tuple[int, ...]], tuple[int, ...]],
        aspects: Optional[UefiCmdAspects] = None,
    ) -> tuple[int, ...]: ...

    def read_boot_next(self, aspects: Optional[UefiCmdAspects] = None) -> Optional[int]: ...
    def set_boot_next(self, option_id: int, aspects: Optional[UefiCmdAspects] = None) -> None: ...
    def clear_boot_next(self, aspects: Optional[UefiCmdAspects] = None) -> None: ...

    def read_boot_current(self, aspects: Optional[UefiCmdAspects] = None) -> int: ...

    def read_timeout(self, aspects: Optional[UefiCmdAspects] = None) -> int: ...
    def write_timeout(self, seconds: int, aspects: Optional[UefiCmdAspects] = None) -> None: ...
    def delete_timeout(self, aspects: Optional[UefiCmdAspects] = None) -> None: ...

    # Driver####, DriverOrder parity
    def read_driver_option(self, option_id: int, aspects: Optional[UefiCmdAspects] = None) -> BootOption: ...
    def write_driver_option(self, option_id: int, option: BootOption, aspects: Optional[UefiCmdAspects] = None) -> None: ...
    def delete_driver_option(self, option_id: int, aspects: Optional[UefiCmdAspects] = None) -> None: ...
    def read_driver_order(self, aspects: Optional[UefiCmdAspects] = None) -> tuple[int, ...]: ...
    def write_driver_order(self, new_order: Sequence[int], aspects: Optional[UefiCmdAspects] = None) -> None: ...
    def update_driver_order(self, transform: Callable, aspects: Optional[UefiCmdAspects] = None) -> tuple[int, ...]: ...

    # SysPrep####, SysPrepOrder parity
    def read_sysprep_option(self, option_id: int, aspects: Optional[UefiCmdAspects] = None) -> BootOption: ...
    def write_sysprep_option(self, option_id: int, option: BootOption, aspects: Optional[UefiCmdAspects] = None) -> None: ...
    def read_sysprep_order(self, aspects: Optional[UefiCmdAspects] = None) -> tuple[int, ...]: ...
    def write_sysprep_order(self, new_order: Sequence[int], aspects: Optional[UefiCmdAspects] = None) -> None: ...

    def iter_boot_options(self, aspects: Optional[UefiCmdAspects] = None) -> Iterator[tuple[int, BootOption]]: ...
```

### `order_codec.py`

```python
def pack_u16_array(values: Sequence[int]) -> bytes:
    """
        Pack a sequence of UINT16 values little-endian.

        :param values: The option IDs to pack.

        :returns: Concatenated little-endian UINT16 bytes.
        :raises: ConfigurationError.
    """
    out = bytearray()
    for index, value in enumerate(values):
        if value < 0 or value > 0xFFFF:
            err_msg = f"values[{index}]={value} out of UINT16 range."
            raise ConfigurationError(err_msg)
        out.extend(value.to_bytes(length=2, byteorder="little", signed=False))
    rtnval = bytes(out)
    return rtnval


def unpack_u16_array(buf: bytes) -> tuple[int, ...]:
    """
        Unpack a buffer as a tuple of UINT16 values.

        :param buf: The packed bytes.

        :returns: Tuple of unpacked integers.
        :raises: ConfigurationError if buf length is odd.
    """
    if len(buf) % 2 != 0:
        err_msg = f"Buffer length {len(buf)} not a multiple of 2."
        raise ConfigurationError(err_msg)
    result = []
    for offset in range(0, len(buf), 2):
        chunk = buf[offset:offset + 2]
        value = int.from_bytes(chunk, byteorder="little", signed=False)
        result.append(value)
    rtnval = tuple(result)
    return rtnval
```

## Data Models

### `EFI_LOAD_OPTION` Byte Layout (UEFI §3.1.3)

| Offset              | Size           | Field                 | Notes                                    |
|---------------------|----------------|-----------------------|------------------------------------------|
| 0                   | 4              | `Attributes`          | LE UINT32, LoadOptionAttributes flags    |
| 4                   | 2              | `FilePathListLength`  | LE UINT16, byte count of `FilePathList`  |
| 6                   | var            | `Description`         | UCS-2 null-terminated                    |
| 6 + len(desc)       | `FilePathListLength` | `FilePathList`  | Concatenated device-path nodes           |
| 6 + len(desc) + FPL | var            | `OptionalData`        | Remainder of buffer, may be empty        |

`len(desc)` includes the two-byte NUL terminator.

### `Attributes` Bitfield (UEFI §3.1.3)

| Bits   | Name                           | Notes                       |
|--------|--------------------------------|-----------------------------|
| 0      | `LOAD_OPTION_ACTIVE`           | `0x00000001`                |
| 1      | `LOAD_OPTION_FORCE_RECONNECT`  | `0x00000002`                |
| 2      | Reserved                       | 0                           |
| 3      | `LOAD_OPTION_HIDDEN`           | `0x00000008`                |
| 4–7    | Reserved                       | 0                           |
| 8–12   | `LOAD_OPTION_CATEGORY` (5-bit) | mask `0x00001F00`           |
|        | `CATEGORY_BOOT`                | `0x00000000`                |
|        | `CATEGORY_APP`                 | `0x00000100`                |
| 13–31  | Reserved                       | 0                           |

### `BootOrder` Layout

Packed array of UINT16 LE option IDs; no header, no terminator.
Length = `len(variable_data) / 2`.

### `BootNext` Layout

Single UINT16 LE option ID (2 bytes total).

### `BootCurrent` Layout

Single UINT16 LE option ID (2 bytes total, read-only).

### `Timeout` Layout

Single UINT16 LE seconds value (2 bytes total).

## Measured Interop Contract

Every public method accepts `aspects: Optional[UefiCmdAspects] = None` at
the last positional slot.  `resolve_aspects(aspects, self._aspects)` is
invoked at the top of every public method; the resolved aspects is
forwarded to `VariableStore` unchanged.

Aspects fields consumed:

* `action_pattern` — forwarded to `VariableStore`; additionally used by
  `update_*_order` to drive the optimistic read-modify-write retry.
* `completion_timeout`, `completion_interval` — bound the atomic-order
  retry loop.
* `connection_timeout`, `connection_interval` — forwarded.
* `inactivity_timeout`, `inactivity_interval` — forwarded.
* `monitor_delay` — wraps each operation in a `MonitoredScope`.
* `logging_pattern` — consulted for per-op log emission on
  `uefi.boot`.
* `allowed_error_codes` — forwarded; used by callers who want to
  tolerate `EFI_NOT_FOUND` during opportunistic reads.
* `expected_status` — default 0 (EFI_SUCCESS).
* `prompt_pattern`, `response_encoding` — forwarded unchanged.

ActionPatterns supported:

| Pattern                          | Reads | Writes | `update_*_order` |
|----------------------------------|-------|--------|------------------|
| `SINGLE_CALL`                    | Yes   | Yes    | No — must use DO_UNTIL_SUCCESS |
| `SINGLE_CONNECTED_CALL`          | Yes   | Yes    | No — must use DO_UNTIL_SUCCESS |
| `DO_UNTIL_SUCCESS`               | Yes   | Yes    | Yes              |
| `DO_WHILE_SUCCESS`               | No — `SemanticError` on writes | No | No |
| `DO_UNTIL_CONNECTION_FAILURE`    | No    | No     | No               |

## Error Handling

| Condition                                                    | Exception            | `details`                                     |
|--------------------------------------------------------------|----------------------|-----------------------------------------------|
| Buffer too short for `EFI_LOAD_OPTION`                       | `UefiVariableError`  | observed length                               |
| Description lacks UCS-2 null terminator                      | `UefiVariableError`  | offset, preview bytes                         |
| `FilePathListLength` exceeds remaining buffer                | `UefiVariableError`  | declared length, remaining                    |
| Description contains non-BMP character (encode)              | `UefiError`          | offending code point                          |
| `option_id` outside `0x0000..0xFFFF`                         | `ConfigurationError` | offending value                               |
| `Timeout` seconds outside `0..65535`                         | `ConfigurationError` | offending value                               |
| `set_boot_next` target does not exist                        | `UefiVariableError`  | `Boot{id:04X}`, `EFI_NOT_FOUND`               |
| `update_boot_order` fails to converge                        | `UefiVariableError`  | per-attempt observed orders                   |
| Transform produces order with unknown option ID              | `UefiVariableError`  | unknown IDs, available IDs                    |
| Packed array has odd byte length                             | `ConfigurationError` | observed length                               |
| `DO_WHILE_SUCCESS` on mutating op                            | `SemanticError`      | op name                                       |
| `DO_UNTIL_CONNECTION_FAILURE` on any op                      | `SemanticError`      | op name                                       |

## Logging

Channel: `uefi.boot`.

* `INFO` on each write / delete — op, option ID, variable name.
* `DEBUG` on each read — op, option ID.
* `WARNING` when a factory classmethod is called with `attributes=0`.
* `WARNING` when `iter_boot_options` skips a malformed entry — option
  ID, exception type.
* `ERROR` on any `UefiVariableError` before re-raise.

## Security Considerations

* Writing `Boot####` or altering `BootOrder` can render the system
  unbootable; all write APIs accept aspects so callers can adopt
  conservative retry policies.
* `optional_data` may contain caller-supplied opaque bytes (for example,
  shim bootloader configuration blobs) — this feature does not interpret
  or sanitize those bytes.
* Redfish backend writes go through `/Systems/{id}/Bios/Settings` which
  typically applies on next reboot; callers MUST NOT assume immediate
  effect and SHOULD compose with 5-system-reset + 6-reboot-and-verify
  when verification is required.

## Testing Strategy

### Unit — `BootOption`

* Encode/decode round-trip for corpus fixtures covering:
  - Windows Boot Manager (`\EFI\Microsoft\Boot\bootmgfw.efi`)
  - shim (`\EFI\ubuntu\shimx64.efi`)
  - GRUB (`\EFI\ubuntu\grubx64.efi`)
  - rEFInd (`\EFI\refind\refind_x64.efi`)
  - UEFI Shell
  - HTTP boot URI entry
  - NVMe over Fabric entry
* Short-buffer and missing-null-terminator error paths.
* Non-BMP description raises `UefiError` on encode.
* Factory classmethods produce lossless round-trips.
* `frozen=True` — mutation raises `FrozenInstanceError`.

### Unit — `order_codec`

* Round-trip for random 0–255-element UINT16 arrays.
* Out-of-range value raises `ConfigurationError`.
* Odd-length buffer raises `ConfigurationError`.

### Unit — `BootManager`

Using a fake `VariableStore` that tracks read/write counts and can be
scripted to change state between reads:

* `read_boot_option` / `write_boot_option` / `delete_boot_option`
  happy paths.
* `set_boot_next` verifies existence first; raises `UefiVariableError`
  when target missing.
* `update_boot_order` converges when pre-state is stable.
* `update_boot_order` retries when pre-state changes, converges within
  `completion_timeout`, raises on timeout.
* `update_boot_order` rejects a transform that returns an unknown
  option ID.
* `iter_boot_options` yields sorted by ID and skips malformed entries
  with a `WARNING` log.
* `Driver####` and `SysPrep####` parity by parametrized variable name
  prefix.

### Action-pattern targeted tests

* Every `ActionPattern` exercised against each mutating and non-mutating
  op per the contract table.
* `DO_WHILE_SUCCESS` on every write raises `SemanticError`.
* `DO_UNTIL_CONNECTION_FAILURE` raises `SemanticError`.

### Integration

* QEMU/OVMF: read default `BootOrder`, add a Shell boot option, set
  `BootNext`, reset, verify `BootCurrent`.

## Dependencies on Other Features

* `1-aspects` — `UefiCmdAspects`, `resolve_aspects`, `ActionPattern`.
* `1-constants-and-types` — `EFI_GLOBAL_VARIABLE_GUID`.
* `1-exceptions` — `UefiError`, `UefiVariableError`, `SemanticError`,
  `ConfigurationError`.
* `1-helpers` — UCS-2 encode/decode round-trip helpers.
* `1-logging` — `uefi.boot` channel.
* `5-variable-services` — `VariableStore`, `VariableAttributes`.
* `5-device-path-codec` — imported lazily inside `BootOption.render()`
  for textual representation of `file_path_bytes`.  No hard import at
  module load.

Downstream consumers: `5-network-boot`, `6-boot-to-setup`,
`6-boot-to-option-once`.
