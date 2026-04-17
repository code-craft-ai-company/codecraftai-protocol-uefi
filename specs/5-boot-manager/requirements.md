# Requirements — Boot Manager

## Introduction

The Boot Manager feature wraps the UEFI-defined boot-option variables and
their companion control variables into a typed Python API:

* `Boot####` (per-option descriptor records), `BootOrder` (execution
  sequence), `BootNext` (one-shot override), `BootCurrent` (read-only
  witness of the last-booted option), `Timeout` (Boot Manager inactivity
  delay).
* `Driver####` / `DriverOrder` and `SysPrep####` — identical codec and
  ordering semantics, separate name-spaces per UEFI §3.3.
* Binary ⇄ dataclass codec for `EFI_LOAD_OPTION` per UEFI §3.1.3.

Per UEFI §3.1.3, each `Boot####` / `Driver####` / `SysPrep####` variable
holds an `EFI_LOAD_OPTION` structure — a byte-packed concatenation of
`Attributes` (UINT32), `FilePathListLength` (UINT16), a null-terminated
UCS-2 `Description`, a `FilePathList` of device-path bytes, and an
optional `OptionalData` suffix.  `BootOrder`, `DriverOrder`, and
`BootNext` are arrays (or single instance) of UINT16 option IDs.

This feature sits on top of 5-variable-services for I/O and delegates to
5-device-path-codec for human-readable rendering of the embedded
`FilePathList`.  It references UEFI §3, §3.1.3, and §3.3.

## Requirements

### Requirement 1 — Aspects-Aware Public Entry Points

**User Story:** As a test author, I want every boot-manager method to
accept an `aspects` override, so that retry and timeout behavior is
controllable per call.

#### Acceptance Criteria

1. WHEN the caller invokes any public method on `BootManager` THEN the
   system SHALL accept `aspects: Optional[UefiCmdAspects] = None` as the
   last parameter.
2. WHEN the caller passes `aspects=None` THEN the system SHALL call
   `resolve_aspects(None, self._aspects)` and use the instance default.
3. WHEN a method dispatches to `VariableStore` THEN the resolved aspects
   SHALL be forwarded unchanged.
4. IF `aspects` is not a `UefiCmdAspects` THEN the system SHALL raise
   `SemanticError`.

### Requirement 2 — Action Pattern Support

**User Story:** As a test author, I want to pick the retry pattern for
boot-manager operations so that atomic read-modify-write cycles survive
transient firmware busy states.

#### Acceptance Criteria

1. WHEN `aspects.action_pattern == ActionPattern.SINGLE_CALL` THEN the
   operation SHALL dispatch exactly once.
2. WHEN `aspects.action_pattern == ActionPattern.SINGLE_CONNECTED_CALL`
   THEN the operation SHALL open-dispatch-close exactly once.
3. WHEN `aspects.action_pattern == ActionPattern.DO_UNTIL_SUCCESS` AND
   the operation is a read THEN the system SHALL retry on transient
   transport errors up to `aspects.completion_timeout`.
4. WHEN `aspects.action_pattern == ActionPattern.DO_UNTIL_SUCCESS` AND
   the operation is an atomic-order update THEN the system SHALL retry
   read-modify-write on detection of a concurrent change up to
   `aspects.completion_timeout`.
5. IF `aspects.action_pattern == ActionPattern.DO_WHILE_SUCCESS` THEN
   the system SHALL raise `SemanticError` for every mutating operation.
6. IF `aspects.action_pattern == ActionPattern.DO_UNTIL_CONNECTION_FAILURE`
   THEN the system SHALL raise `SemanticError`.

### Requirement 3 — `EFI_LOAD_OPTION` Decode

**User Story:** As a test author, I want to decode raw `Boot####` bytes
into a structured `BootOption` object, so that my assertions are
field-level rather than byte-level.

#### Acceptance Criteria

1. WHEN `BootOption.decode(buf)` is called with a valid
   `EFI_LOAD_OPTION` byte buffer THEN the system SHALL return a frozen
   `BootOption(attributes, description, file_path_bytes, optional_data)`
   dataclass per §3.1.3.
2. WHEN `buf` is shorter than the minimum header (4 + 2 bytes) THEN the
   system SHALL raise `UefiVariableError` with a `details` field naming
   the observed length.
3. WHEN the description UCS-2 buffer is not null-terminated before the
   reported `FilePathList` offset THEN the system SHALL raise
   `UefiVariableError`.
4. WHEN `FilePathListLength` exceeds the remaining buffer THEN the
   system SHALL raise `UefiVariableError`.
5. WHEN the caller requests a decoded device-path rendering THEN the
   system SHALL delegate to 5-device-path-codec for conversion of
   `file_path_bytes` to text.

### Requirement 4 — `EFI_LOAD_OPTION` Encode

**User Story:** As a test author, I want to encode a constructed
`BootOption` back into bytes, so that I can write it via the variable
store.

#### Acceptance Criteria

1. WHEN `BootOption.encode()` is called THEN the system SHALL return a
   `bytes` object laid out as per §3.1.3: `Attributes (4 LE) ||
   FilePathListLength (2 LE) || Description (UCS-2 null-terminated) ||
   FilePathList || OptionalData`.
2. WHEN the round-trip `BootOption.decode(original.encode())` is
   performed THEN the resulting `BootOption` SHALL compare equal under
   `==` to the original.
3. WHEN `BootOption.encode()` is invoked and `description` contains a
   non-BMP character THEN the system SHALL raise `UefiError` because
   UEFI strings are strict UCS-2.
4. WHEN `FilePathListLength` is computed THEN the system SHALL use the
   exact byte count of `file_path_bytes` (not a higher watermark).

### Requirement 5 — `BootOption` Factory Classmethods

**User Story:** As a test author, I want convenience constructors for
common load-option shapes, so that I don't hand-assemble device-paths for
simple cases.

#### Acceptance Criteria

1. WHEN `BootOption.for_file_path(description, volume_alias, path,
   optional_data=b"", attributes=LOAD_OPTION_ACTIVE)` is called THEN
   the system SHALL build a load option whose `FilePathList` consists of
   a Hard-Drive node (or File Path node, if `volume_alias` resolves to a
   generic path) followed by a File Path Media Device Path node per
   §10.3.5.4, terminated by an End-of-Hardware node.
2. WHEN `BootOption.for_http_boot(description, uri,
   optional_data=b"", attributes=LOAD_OPTION_ACTIVE)` is called THEN the
   system SHALL build a load option whose `FilePathList` contains a URI
   Device Path node per §10.3.4.22 followed by an End-of-Hardware node.
3. WHEN any factory classmethod is called THEN the result SHALL
   round-trip through `encode`/`decode` unchanged.
4. WHEN a factory is called with `attributes=0` THEN the system SHALL
   emit the option but SHALL log a `WARNING` on channel `uefi.boot`
   noting the option is not marked `LOAD_OPTION_ACTIVE`.

### Requirement 6 — `BootOption` Is Immutable

**User Story:** As a maintainer, I want `BootOption` instances to be
immutable so that callers cannot accidentally mutate them between read
and write.

#### Acceptance Criteria

1. WHEN `BootOption` is defined THEN the class SHALL use
   `@dataclass(frozen=True)`.
2. WHEN the caller assigns to any field of a `BootOption` THEN the
   system SHALL raise `dataclasses.FrozenInstanceError`.
3. WHEN `BootOption` is hashed THEN the system SHALL produce a stable
   hash based on `(attributes, description, file_path_bytes,
   optional_data)`.

### Requirement 7 — `Boot####` Read / Write / Delete

**User Story:** As a test author, I want to read, write, and delete
individual `Boot####` entries so that I can manipulate the boot catalogue.

#### Acceptance Criteria

1. WHEN `read_boot_option(option_id: int)` is called THEN the system
   SHALL fetch the variable named `Boot{option_id:04X}` under
   `EFI_GLOBAL_VARIABLE_GUID` and return a `BootOption`.
2. WHEN `write_boot_option(option_id, option)` is called THEN the
   system SHALL write `Boot{option_id:04X}` with attributes
   `NON_VOLATILE | BOOTSERVICE_ACCESS | RUNTIME_ACCESS`.
3. WHEN `delete_boot_option(option_id)` is called THEN the system
   SHALL write empty data with the same attributes to effect the
   delete.
4. WHEN `read_boot_option` encounters an ID that does not exist THEN
   the system SHALL raise `UefiVariableError` whose `efi_status` is
   `EFI_NOT_FOUND`.
5. IF `option_id` is outside `0x0000..0xFFFF` THEN the system SHALL
   raise `ConfigurationError`.

### Requirement 8 — `BootOrder` Atomic Update

**User Story:** As a test author, I want `BootOrder` reads and writes to
be atomic in the face of concurrent firmware-side changes, so that my
reorderings are not silently overwritten.

#### Acceptance Criteria

1. WHEN `read_boot_order()` is called THEN the system SHALL return a
   `tuple[int, ...]` of option IDs decoded as an array of UINT16
   little-endian values.
2. WHEN `write_boot_order(new_order)` is called THEN the system SHALL
   encode `new_order` as packed UINT16 LE and write it to `BootOrder`
   under `EFI_GLOBAL_VARIABLE_GUID` with `NON_VOLATILE |
   BOOTSERVICE_ACCESS | RUNTIME_ACCESS`.
3. WHEN `update_boot_order(transform)` is called with a callable
   `transform(old: tuple[int, ...]) -> tuple[int, ...]` THEN the system
   SHALL read, apply, write, then re-read; on detection of a concurrent
   change the system SHALL re-apply the transform up to
   `aspects.completion_timeout` at `aspects.completion_interval`.
4. IF the transform cannot converge within `aspects.completion_timeout`
   THEN the system SHALL raise `UefiVariableError` with
   `details` listing each attempt's observed order.
5. IF the transform returns an order containing an option ID that does
   not correspond to an existing `Boot####` entry THEN the system SHALL
   raise `UefiVariableError` before writing.

### Requirement 9 — `BootNext` Verification

**User Story:** As a test author, I want `set_boot_next(option_id)` to
verify the target option exists before writing, so that I never produce
an unbootable one-shot state.

#### Acceptance Criteria

1. WHEN `set_boot_next(option_id)` is called THEN the system SHALL
   first call `read_boot_option(option_id)`.
2. IF `read_boot_option` raises `UefiVariableError` with `efi_status ==
   EFI_NOT_FOUND` THEN the system SHALL re-raise `UefiVariableError`
   with a message explaining that `Boot{option_id:04X}` does not exist.
3. WHEN the option exists THEN the system SHALL write `BootNext` with
   the two-byte LE encoding of `option_id`.
4. WHEN `clear_boot_next()` is called THEN the system SHALL delete the
   `BootNext` variable.
5. WHEN `read_boot_next()` is called AND `BootNext` is unset THEN the
   system SHALL return `None` rather than raising.

### Requirement 10 — `BootCurrent` Read-Only

**User Story:** As a test author, I want to read `BootCurrent` after a
boot to assert which option actually ran, so that one-shot tests can
verify themselves.

#### Acceptance Criteria

1. WHEN `read_boot_current()` is called THEN the system SHALL return an
   `int` decoded as a UINT16 LE.
2. IF `BootCurrent` is not present THEN the system SHALL raise
   `UefiVariableError` with `efi_status == EFI_NOT_FOUND`.
3. WHERE the backend is Redfish THEN the system SHALL read
   `/Systems/{id}/Boot/BootSourceOverrideTarget` and translate where
   possible, raising `UefiTransportError` when the mapping is not
   well-defined.

### Requirement 11 — `Driver####` and `SysPrep####` Parity

**User Story:** As a test author, I want identical APIs for
`Driver####` and `SysPrep####` so that I can treat them uniformly.

#### Acceptance Criteria

1. WHEN `read_driver_option(option_id)` /
   `write_driver_option(option_id, option)` /
   `delete_driver_option(option_id)` are called THEN the system SHALL
   operate on `Driver{option_id:04X}` under
   `EFI_GLOBAL_VARIABLE_GUID`.
2. WHEN `read_driver_order` / `write_driver_order` /
   `update_driver_order` are called THEN the system SHALL behave
   identically to the `Boot*` equivalents against the `DriverOrder`
   variable.
3. WHEN `read_sysprep_option(option_id)` /
   `write_sysprep_option(option_id, option)` are called THEN the system
   SHALL operate on `SysPrep{option_id:04X}`.
4. WHEN `read_sysprep_order` / `write_sysprep_order` are called THEN
   the system SHALL operate on `SysPrepOrder`.

### Requirement 12 — `Timeout` Variable

**User Story:** As a test author, I want to inspect and adjust the Boot
Manager inactivity timeout, so that I can script scenarios that depend
on it.

#### Acceptance Criteria

1. WHEN `read_timeout()` is called THEN the system SHALL return an
   `int` decoded as a UINT16 LE (seconds).
2. WHEN `write_timeout(seconds)` is called AND `seconds` is in
   `0..65535` THEN the system SHALL write the value.
3. IF `seconds` is outside `0..65535` THEN the system SHALL raise
   `ConfigurationError`.
4. WHEN `delete_timeout()` is called THEN the system SHALL delete the
   `Timeout` variable so firmware defaults apply.

### Requirement 13 — Human-Readable Rendering

**User Story:** As a test author, I want a printable representation of a
boot option that includes the decoded device-path, so that log output
is legible without external tools.

#### Acceptance Criteria

1. WHEN `BootOption.render()` is called THEN the system SHALL return a
   multi-line string containing `attributes` (hex and decoded flags),
   `description`, device-path text delegated to 5-device-path-codec,
   and a hex-dump of `optional_data`.
2. WHEN 5-device-path-codec raises on an unknown node type THEN the
   render SHALL substitute the generic `Path(type,subtype,data)` form
   and SHALL NOT raise.
3. WHEN `render()` is called on an option with `optional_data == b""`
   THEN the system SHALL omit the hex-dump section.

### Requirement 14 — Enumerate All Options

**User Story:** As a test author, I want to enumerate every `Boot####`
(or `Driver####`, `SysPrep####`) in the target's NVRAM, so that my
catalogue inspection doesn't depend on `BootOrder`.

#### Acceptance Criteria

1. WHEN `iter_boot_options()` is called THEN the system SHALL return a
   generator yielding `(option_id: int, option: BootOption)` tuples.
2. WHEN the underlying enumeration is performed THEN the system SHALL
   delegate to `VariableStore.iter_variables(name_filter="Boot[0-9A-F][0-9A-F][0-9A-F][0-9A-F]")`.
3. WHEN yielding THEN the system SHALL decode the referenced variable
   and SHALL skip (logging a `WARNING`) entries that fail to decode.
4. WHEN the caller closes the generator early THEN the system SHALL
   release any cursor state per 5-variable-services semantics.
