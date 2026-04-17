# Requirements — Variable Services

## Introduction

The Variable Services feature implements UEFI non-volatile variable CRUD —
`GetVariable`, `SetVariable`, `GetNextVariableName`, and
`QueryVariableInfo` per UEFI Specification §8.2 — across any of the three
variable-capable transports this library supports:

* **Shell transport** — via the Shell `setvar` / `dmpstore` wrappers in
  5-shell-commands.
* **SSH-to-host transport** — via the `efivar` CLI and
  `/sys/firmware/efi/efivars/` filesystem on a running Linux target.
* **Redfish transport** — via the `/Systems/{id}/Bios/Settings` subtree and
  `SecureBoot` resource for the attribute subset Redfish exposes.

Callers always see the same API surface: a single `VariableStore` facade
hides which backend is in use.  The facade accepts an `UefiAgent` and
selects a backend based on `agent.transport_kind` by default, overridable
by the caller.

All eight attribute flags defined in UEFI §8.2 are round-trippable.
Authenticated writes (§8.2.6 — `EFI_VARIABLE_AUTHENTICATION_2`) accept
either a pre-built descriptor or an unwrapped signer (key + certificate)
and the facade assembles the descriptor internally.  Variable enumeration
streams results through a generator rather than eagerly materializing the
list — firmware typically exposes hundreds of variables and callers often
want to short-circuit enumeration on the first match.

## Requirements

### Requirement 1 — Aspects-Aware Public Entry Points

**User Story:** As a test author, I want every variable-service method to
accept an `aspects` override, so that retry and timeout behavior is
controllable per call.

#### Acceptance Criteria

1. WHEN the caller invokes `VariableStore.get_variable(name, guid, aspects=...)`
   THEN the system SHALL honor every field of the supplied
   `UefiCmdAspects` instance.
2. WHEN the caller passes `aspects=None` THEN the system SHALL call
   `resolve_aspects(None, self._aspects)` and use the store's default.
3. WHEN any public method dispatches to a backend THEN the backend SHALL
   receive the resolved aspects unchanged.
4. WHERE the caller passes a plain `UefiAspects` (not `UefiCmdAspects`) AND
   the selected backend is shell or host-OS THEN the system SHALL raise
   `SemanticError` because those backends require `prompt_pattern` and
   `response_encoding`.

### Requirement 2 — Action Pattern Support

**User Story:** As a test author, I want each variable-service operation
to support the action patterns that make semantic sense for it, so that
transient transport issues during SetVariable don't produce phantom
failures.

#### Acceptance Criteria

1. WHEN `aspects.action_pattern == ActionPattern.SINGLE_CALL` THEN the
   operation SHALL dispatch exactly once.
2. WHEN `aspects.action_pattern == ActionPattern.SINGLE_CONNECTED_CALL`
   THEN the operation SHALL establish a channel, dispatch once, close the
   channel.
3. WHEN `aspects.action_pattern == ActionPattern.DO_UNTIL_SUCCESS` THEN
   the operation SHALL retry on transient backend failures up to
   `aspects.completion_timeout` with `aspects.completion_interval` between
   attempts.
4. IF `aspects.action_pattern == ActionPattern.DO_WHILE_SUCCESS` AND the
   operation is `set_variable` THEN the system SHALL raise `SemanticError`
   because write-while-success is nonsensical for CRUD.
5. IF `aspects.action_pattern == ActionPattern.DO_UNTIL_CONNECTION_FAILURE`
   THEN the system SHALL raise `SemanticError`.

### Requirement 3 — Attribute Flag Round-Trip

**User Story:** As a test author, I want to write and read back variables
with any combination of UEFI variable attribute flags, so that my tests
can verify firmware attribute handling end-to-end.

#### Acceptance Criteria

1. WHEN `VariableAttributes` is imported THEN the system SHALL expose an
   `IntFlag` with exactly the members `NON_VOLATILE` (0x01),
   `BOOTSERVICE_ACCESS` (0x02), `RUNTIME_ACCESS` (0x04),
   `HARDWARE_ERROR_RECORD` (0x08), `AUTHENTICATED_WRITE_ACCESS` (0x10),
   `TIME_BASED_AUTHENTICATED_WRITE_ACCESS` (0x20), `APPEND_WRITE` (0x40),
   and `ENHANCED_AUTHENTICATED_ACCESS` (0x80), matching UEFI §8.2 bit
   values.
2. WHEN a variable is written with attributes `A` and read back THEN the
   system SHALL return attributes equal to `A` under `==`.
3. IF `RUNTIME_ACCESS` is set without `BOOTSERVICE_ACCESS` THEN the system
   SHALL raise `ConfigurationError` per UEFI §8.2 "RUNTIME_ACCESS set
   must also have BOOTSERVICE_ACCESS set."
4. IF `AUTHENTICATED_WRITE_ACCESS` and
   `TIME_BASED_AUTHENTICATED_WRITE_ACCESS` are both set THEN the system
   SHALL raise `ConfigurationError`.
5. IF `ENHANCED_AUTHENTICATED_ACCESS` is combined with either
   `AUTHENTICATED_WRITE_ACCESS` or
   `TIME_BASED_AUTHENTICATED_WRITE_ACCESS` THEN the system SHALL raise
   `ConfigurationError`.

### Requirement 4 — `get_variable`

**User Story:** As a test author, I want to read a variable by name and
vendor GUID and receive both its data and its attributes, so that I can
assert firmware state.

#### Acceptance Criteria

1. WHEN `get_variable(name, vendor_guid)` is called THEN the system SHALL
   return a `VariableRecord(name, vendor_guid, attributes, data)` dataclass.
2. IF the variable does not exist THEN the system SHALL raise
   `UefiVariableError` whose `efi_status` attribute is
   `EFI_NOT_FOUND` (`0x8000_0000_0000_000E`) per §8.2.1.
3. IF the data exceeds `aspects.inactivity_timeout`-bounded buffered read
   THEN the system SHALL raise `UefiVariableError` with
   `efi_status == EFI_BUFFER_TOO_SMALL` (`0x8000_0000_0000_0005`).
4. WHEN the caller passes `vendor_guid` as a `str` THEN the system SHALL
   accept any string form parseable by `uuid.UUID()`.
5. WHEN the caller passes `vendor_guid` as a `uuid.UUID` THEN the system
   SHALL use it unchanged.

### Requirement 5 — `set_variable`

**User Story:** As a test author, I want to write a variable atomically
with a chosen attribute mask, so that my firmware-state manipulations are
deterministic.

#### Acceptance Criteria

1. WHEN `set_variable(name, vendor_guid, attributes, data)` is called
   THEN the system SHALL dispatch a backend write carrying all four
   values and SHALL return a `VariableWriteReceipt(efi_status: int,
   bytes_written: int)`.
2. WHEN `data` is empty (`b""`) AND
   `TIME_BASED_AUTHENTICATED_WRITE_ACCESS` is not in `attributes` THEN
   the system SHALL interpret the call as a delete per §8.2.3.
3. WHEN `data` is empty AND `TIME_BASED_AUTHENTICATED_WRITE_ACCESS` is
   in `attributes` THEN the system SHALL still pass the
   `EFI_VARIABLE_AUTHENTICATION_2` descriptor payload as the write body
   per §8.2.6.
4. IF the backend rejects the write with a non-success EFI status THEN
   the system SHALL raise `UefiVariableError` carrying `name`,
   `vendor_guid`, `attributes`, and `efi_status` attributes.
5. WHERE the backend is Redfish THEN write SHALL go through
   `/Systems/{id}/Bios/Settings` and the operation SHALL record a pending
   `Task` that is polled per `aspects.completion_timeout` /
   `aspects.completion_interval`.

### Requirement 6 — Authenticated-Variable Writes

**User Story:** As a test author, I want to write authenticated variables
using either a pre-built descriptor or a key+certificate pair, so that
both low-level and high-level test cases are supported.

#### Acceptance Criteria

1. WHEN the caller passes
   `auth=EfiVariableAuthentication2Descriptor(...)` THEN the system SHALL
   prepend the descriptor bytes to `data` exactly as §8.2.6 specifies.
2. WHEN the caller passes `auth=SignerKeyPair(key=..., certificate=...,
   timestamp=...)` THEN the system SHALL construct the
   `EFI_VARIABLE_AUTHENTICATION_2` descriptor internally, including the
   `EFI_TIME` timestamp, the `WIN_CERTIFICATE_UEFI_GUID`, and the
   PKCS#7-signed hash of `(name || vendor_guid || attributes ||
   timestamp || data)` per §8.2.6.
3. WHEN the caller omits `timestamp` on a `SignerKeyPair` THEN the system
   SHALL default to a timezone-aware `datetime.now(timezone.utc)` and
   convert to `EFI_TIME` via layer-1 helpers.
4. IF `auth` is provided but `attributes` does not include
   `TIME_BASED_AUTHENTICATED_WRITE_ACCESS` THEN the system SHALL raise
   `ConfigurationError`.
5. IF `auth=None` AND `attributes` includes any authenticated-write flag
   THEN the system SHALL raise `ConfigurationError` naming the missing
   `auth`.
6. WHEN the timestamp of a subsequent write is not strictly greater than
   the firmware's stored timestamp THEN the backend will return
   `EFI_SECURITY_VIOLATION`; the system SHALL surface this as
   `UefiVariableError` with `efi_status == EFI_SECURITY_VIOLATION`
   (`0x8000_0000_0000_001A`).

### Requirement 7 — `get_next_variable_name` Enumeration

**User Story:** As a test author, I want to iterate every variable on the
target, so that my tests can detect unexpected leftover state.

#### Acceptance Criteria

1. WHEN `iter_variables()` is called THEN the system SHALL return a
   generator yielding `(name: str, vendor_guid: uuid.UUID)` tuples.
2. WHEN the generator is iterated lazily THEN the system SHALL issue one
   `GetNextVariableName` backend call per yielded tuple.
3. WHEN the backend indicates no further entries (EFI status
   `EFI_NOT_FOUND`) THEN the generator SHALL terminate.
4. WHEN the generator is closed early (e.g., via `break`) THEN the system
   SHALL release any cursor state the backend owns.
5. WHERE the backend is SSH-to-host THEN the generator SHALL stream the
   directory listing of `/sys/firmware/efi/efivars/` in sorted order.
6. WHEN the caller passes `name_filter: Optional[str]` THEN the
   generator SHALL only yield tuples whose name matches the filter (an
   `fnmatch`-style glob).

### Requirement 8 — `query_variable_info`

**User Story:** As a test author, I want to know total, remaining, and
maximum single-variable storage for each attribute class, so that I can
assert storage budget is not exceeded.

#### Acceptance Criteria

1. WHEN `query_variable_info(attributes)` is called THEN the system SHALL
   return `QueryVariableInfoResult(maximum_variable_storage_size,
   remaining_variable_storage_size, maximum_variable_size)` per §8.2.4.
2. IF `attributes` specifies a combination not supported by firmware THEN
   the system SHALL raise `UefiVariableError` with
   `efi_status == EFI_UNSUPPORTED` (`0x8000_0000_0000_0003`).
3. WHERE the backend is Redfish THEN the system SHALL derive values from
   Redfish `Bios` resource where available, and SHALL raise
   `UefiTransportError` when not.

### Requirement 9 — `VariableStore` Facade

**User Story:** As a test author, I want a single class that hides which
backend is in use, so that I can swap transports without changing call
sites.

#### Acceptance Criteria

1. WHEN `VariableStore(agent)` is constructed THEN the system SHALL
   select a backend by inspecting `agent.transport_kind`:
   `"serial"` / `"ipmi-sol"` → shell backend, `"ssh-host"` → host-OS
   backend, `"redfish"` → Redfish backend.
2. WHEN `VariableStore(agent, backend_override=...)` is constructed THEN
   the system SHALL use the override and SHALL NOT inspect
   `agent.transport_kind`.
3. IF `agent.transport_kind` is unrecognized AND no override is passed
   THEN the system SHALL raise `ConfigurationError`.
4. WHEN `VariableStore.__init__` returns THEN the facade SHALL be frozen
   against re-binding of `_backend` and `_aspects` attributes.

### Requirement 10 — Attributes From Byte Representation

**User Story:** As a maintainer, I want attribute flags to round-trip
through the 4-byte little-endian UEFI wire format, so that every backend
can interchange them.

#### Acceptance Criteria

1. WHEN `VariableAttributes.from_bytes(buf)` is called with a 4-byte
   little-endian buffer THEN the system SHALL return a
   `VariableAttributes` whose integer value equals
   `int.from_bytes(buf, "little")`.
2. WHEN `VariableAttributes(...).to_bytes()` is called THEN the system
   SHALL return a 4-byte little-endian representation.
3. IF the buffer is not exactly 4 bytes THEN the system SHALL raise
   `ConfigurationError`.

### Requirement 11 — Time-Based Auth Timestamp Helpers

**User Story:** As a test author, I want to build
`EFI_VARIABLE_AUTHENTICATION_2` descriptors from `datetime` objects, so
that I don't have to construct `EFI_TIME` manually.

#### Acceptance Criteria

1. WHEN a `datetime` is converted to `EFI_TIME` via the descriptor
   builder THEN the system SHALL delegate to the layer-1 helper
   `datetime_to_efi_time()` per §8.3.
2. IF the `datetime` is naive (lacks `tzinfo`) THEN the system SHALL
   raise `ConfigurationError`.
3. WHEN the descriptor is written back out THEN the `EFI_TIME.TimeZone`
   field SHALL encode the UTC offset in minutes per §8.3 `EFI_TIME`
   definition.

### Requirement 12 — Backend Parity

**User Story:** As a maintainer, I want every backend to implement the
same contract, so that caller code is transport-portable.

#### Acceptance Criteria

1. WHEN the shell, host-OS, and Redfish backends are tested against the
   same parametrized contract suite THEN the system SHALL pass
   equivalently on all backends for non-authenticated-write operations.
2. WHERE a backend cannot implement an operation (e.g., Redfish cannot
   produce a byte-for-byte `EFI_VARIABLE_AUTHENTICATION_2` write) THEN
   the backend SHALL raise `UefiTransportError` with
   `details` explaining the limitation, and the facade SHALL surface
   this without modification.
3. WHEN `iter_variables` is called on any backend THEN ordering SHALL be
   deterministic: sorted by `(vendor_guid, name)`.

### Requirement 13 — Backend Selection Logging

**User Story:** As an integrator, I want to know which backend the
facade selected, so that diagnostics across transports are readable.

#### Acceptance Criteria

1. WHEN `VariableStore(agent)` constructs THEN the system SHALL emit one
   `INFO` log on channel `uefi.variable` naming the selected backend.
2. WHEN a single public operation dispatches to a backend THEN the
   system SHALL emit one `DEBUG` log identifying the operation and the
   backend.
3. WHEN a write fails THEN the system SHALL emit one `ERROR` log
   including the `efi_status` value in hexadecimal.
