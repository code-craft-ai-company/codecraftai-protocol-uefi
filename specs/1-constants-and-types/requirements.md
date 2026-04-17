# Requirements — Constants and Types

## Introduction

The Constants and Types feature provides the shared vocabulary of immutable
values, typed dataclasses, GUID aliases, and type aliases that every higher
layer in `automojo-uefi` references.  It collects numeric defaults (timeouts,
buffer sizes), textual templates for structured log rendering, UEFI Shell
prompt sentinels, the master registry of well-known UEFI GUIDs (such as
`EFI_GLOBAL_VARIABLE_GUID`, `EFI_IMAGE_SECURITY_DATABASE_GUID`, and
`EFI_SYSTEM_RESOURCE_TABLE_GUID`), and the `UefiJumpParams` dataclass that
parallels `SshJumpParams` for jump-host-reachable Redfish targets.

This feature has no dependencies on other features and is intended to be
trivially importable — it pulls in only the Python standard library.  Higher
layers consume its constants as read-only module attributes and its
dataclasses as frozen value objects.  The feature is the canonical home of
anything the rest of the library needs to reference by name rather than by
inlined literal.

## Requirements

### Requirement 1 — Well-Known GUID Registry

**User Story:** As a test author, I want to reference UEFI GUIDs by a named
Python constant rather than by hand-typing the GUID string, so that typos at
authoring time are caught by name resolution.

#### Acceptance Criteria

1. WHEN the caller imports `EFI_GLOBAL_VARIABLE_GUID` from the `uefiguids`
   submodule THEN the system SHALL expose it as a `uuid.UUID` instance whose
   canonical form equals `8be4df61-93ca-11d2-aa0d-00e098032b8c`.
2. WHEN the caller imports `EFI_IMAGE_SECURITY_DATABASE_GUID` THEN the system
   SHALL expose it as a `uuid.UUID` instance whose canonical form equals
   `d719b2cb-3d3a-4596-a3bc-dad00e67656f`.
3. WHEN the caller imports `EFI_SYSTEM_RESOURCE_TABLE_GUID` THEN the system
   SHALL expose it as a `uuid.UUID` instance whose canonical form equals
   `b122a263-3661-4f68-9929-78f8b0d62180`.
4. WHERE a well-known GUID is exposed by this feature the system SHALL expose
   it as a module-level `Final[uuid.UUID]` constant, never as a raw string or
   raw bytes literal.
5. WHEN the caller imports the `WELL_KNOWN_GUIDS` mapping THEN the system
   SHALL return a `types.MappingProxyType` whose keys are the constant names
   (e.g. `"EFI_GLOBAL_VARIABLE_GUID"`) and whose values are the matching
   `uuid.UUID` instances.
6. IF a consumer attempts to mutate the `WELL_KNOWN_GUIDS` mapping THEN the
   system SHALL raise `TypeError` because the proxy is read-only.

### Requirement 2 — GUID Binary Round-Trip Helpers

**User Story:** As a protocol author, I want to convert a `uuid.UUID` to the
UEFI mixed-endian 16-byte binary layout and back, so that I can read and
write UEFI GUID fields without re-deriving the byte-order convention at
every call site.

#### Acceptance Criteria

1. WHEN the caller invokes `guid_to_bytes(guid)` with a `uuid.UUID` THEN the
   system SHALL return a 16-byte `bytes` object in UEFI mixed-endian layout
   (little-endian `data1`/`data2`/`data3`, big-endian `data4`).
2. WHEN the caller invokes `guid_from_bytes(buf)` with a 16-byte `bytes`
   object THEN the system SHALL return a `uuid.UUID` equal to the originating
   value after a `guid_to_bytes` round-trip.
3. IF the caller passes a buffer of length other than 16 to `guid_from_bytes`
   THEN the system SHALL raise `ValueError` whose message names the observed
   length.
4. IF the caller passes a non-`uuid.UUID` value to `guid_to_bytes` THEN the
   system SHALL raise `TypeError` whose message names the observed type.
5. WHEN `guid_to_bytes(guid_from_bytes(buf))` is evaluated for any valid
   16-byte `buf` THEN the system SHALL produce a byte string equal to `buf`.

### Requirement 3 — Frozen Dataclass Conventions

**User Story:** As a library maintainer, I want every typed dataclass exposed
by this feature to be immutable, so that callers cannot accidentally mutate
shared value objects.

#### Acceptance Criteria

1. WHERE a dataclass is exposed by this feature the system SHALL declare it
   with `@dataclass(frozen=True, slots=True)` on Python versions that support
   `slots=True` and with `@dataclass(frozen=True)` otherwise.
2. IF a caller assigns to any attribute of a dataclass exposed by this
   feature THEN the system SHALL raise `dataclasses.FrozenInstanceError`.
3. WHERE a dataclass exposes a sequence-typed field the system SHALL declare
   the field as `tuple[...]` rather than `list[...]` to preserve shallow
   immutability.
4. WHEN `__eq__` is invoked on any dataclass exposed by this feature THEN the
   system SHALL return field-wise equality (the default dataclass behavior).

### Requirement 4 — UefiJumpParams Dataclass

**User Story:** As a test author, I want to describe a jump-host used to
reach a Redfish BMC that is on a private management network, so that I can
configure one-hop tunneling without writing bespoke SSH glue.

#### Acceptance Criteria

1. WHEN the caller constructs `UefiJumpParams(host, credential)` THEN the
   system SHALL accept it and set `port` to `22` by default.
2. WHEN the caller passes `port` explicitly THEN the system SHALL store the
   provided integer.
3. IF `port` is not an integer in the range `[1, 65535]` THEN the system
   SHALL raise `ConfigurationError` with `details` naming the field and value.
4. IF `host` is an empty string THEN the system SHALL raise
   `ConfigurationError`.
5. WHEN `__repr__` is invoked on a `UefiJumpParams` instance THEN the system
   SHALL redact any password or token carried by the embedded credential.

### Requirement 5 — Command-Output Templates

**User Story:** As a diagnostic-log reader, I want successful and failed
command results to be rendered with a stable, human-readable template that
uses the platform line separator, so that log output is legible on every
operating system.

#### Acceptance Criteria

1. WHEN the caller imports `TEMPLATE_COMMAND_SUCCESS` THEN the system SHALL
   return a string containing placeholders for the command tag, the command,
   the stdout block, and the stderr block.
2. WHEN the caller imports `TEMPLATE_COMMAND_FAILURE` THEN the system SHALL
   return a string containing the same placeholders as the success template
   plus a status-code placeholder.
3. WHERE a template embeds a line break the system SHALL embed `os.linesep`
   rather than a literal `"\n"`.
4. WHEN the caller formats either template with valid substitution arguments
   THEN the system SHALL produce a string that ends in a newline.

### Requirement 6 — Prompt Sentinel Constants

**User Story:** As a transport author, I want canonical regex patterns for
recognizing UEFI Shell and firmware-Setup prompts, so that prompt detection
is consistent across all transport implementations.

#### Acceptance Criteria

1. WHEN the caller imports `DEFAULT_SHELL_PROMPT_PATTERN` THEN the system
   SHALL return a `bytes` regex that matches the default UEFI Shell prompt
   (`Shell>` or `FS0:\>`-style prompts).
2. WHEN the caller imports `DEFAULT_INTERACTIVE_SENTINEL` THEN the system
   SHALL return a unique `bytes` value that is safe to embed inline in
   command output to aid prompt synchronization.
3. WHERE a sentinel or pattern is provided as `bytes` the system SHALL
   provide a matching `_STR` variant decoded as `"utf-8"`.
4. IF a caller modifies a pattern or sentinel constant THEN the system SHALL
   allow it (Python does not prevent rebinding a name) BUT the module SHALL
   document each constant as read-only by convention.

### Requirement 7 — Default Timeout and Interval Values

**User Story:** As a maintainer, I want numeric defaults used by the library
to live in one module, so that tuning a timeout does not require a global
grep.

#### Acceptance Criteria

1. WHEN the caller imports any of `DEFAULT_SHELL_TIMEOUT`,
   `DEFAULT_REDFISH_TIMEOUT`, `DEFAULT_RECONNECT_DELAY`, or
   `DEFAULT_CHUNK_SIZE` THEN the system SHALL return an `int` or `float` with
   the documented unit.
2. WHERE a default is a duration the system SHALL document the unit as
   seconds in the module docstring.
3. WHERE a default is a size the system SHALL document the unit as bytes in
   the module docstring.
4. IF a caller sees a name collision with an aspects `DEFAULT_*` constant
   THEN the documentation SHALL clarify that the aspects defaults take
   precedence for behavior parameters and that this module's defaults are
   used only where aspects are not applicable.

### Requirement 8 — Transport Kind Literals

**User Story:** As a test author, I want to refer to a transport by a string
literal from a closed set, so that typos are rejected at construction time.

#### Acceptance Criteria

1. WHEN the caller imports `TRANSPORT_KINDS` THEN the system SHALL return a
   `frozenset[str]` containing exactly `{"serial", "redfish", "ssh-host",
   "ipmi-sol"}`.
2. WHEN the caller imports any `TRANSPORT_KIND_*` constant THEN the system
   SHALL return a `str` that is a member of `TRANSPORT_KINDS`.
3. IF a caller mutates `TRANSPORT_KINDS` THEN the system SHALL raise
   `AttributeError` because the set is frozen.

### Requirement 9 — GUID Registry Drift Detection

**User Story:** As a release engineer, I want a test that fails when a
well-known GUID value is accidentally edited, so that silent registry drift
cannot ship.

#### Acceptance Criteria

1. WHEN the GUID-registry snapshot fixture runs THEN the system SHALL compare
   every entry of `WELL_KNOWN_GUIDS` against a JSON snapshot checked into
   `tests/fixtures/guid_registry.json`.
2. IF any registry entry differs from the snapshot THEN the system SHALL
   cause the test to fail with a diff of name, expected value, and observed
   value.
3. WHEN a new well-known GUID is added to the registry THEN the test author
   SHALL update the snapshot in the same commit.

### Requirement 10 — Module Surface Discipline

**User Story:** As a consumer, I want the public surface of this feature to
be exhaustively listed in `__all__`, so that `from ueficonst import *`
behaves deterministically.

#### Acceptance Criteria

1. WHEN the caller imports `ueficonst` THEN the system SHALL expose a module
   `__all__` tuple listing every public name.
2. WHEN the caller imports `uefiguids` THEN the system SHALL expose a module
   `__all__` tuple listing every well-known GUID constant and the
   `WELL_KNOWN_GUIDS` mapping.
3. WHERE a private helper is defined the system SHALL prefix its name with a
   single underscore and SHALL NOT list it in `__all__`.

### Requirement 11 — Zero Non-Stdlib Imports

**User Story:** As a library integrator, I want constants and types to be
importable without any third-party dependency, so that early-bootstrapping
code paths can reference them without pulling in the rest of the library.

#### Acceptance Criteria

1. WHEN `ueficonst` or `uefiguids` is imported THEN the system SHALL not
   import anything outside the Python standard library and the sibling
   `automojo.errors` package used for `ConfigurationError`.
2. IF a future change introduces a third-party import THEN a unit test SHALL
   fail until the import is removed or explicitly allow-listed.
