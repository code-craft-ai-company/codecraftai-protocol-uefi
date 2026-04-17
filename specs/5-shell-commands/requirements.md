# Requirements — Shell Commands

## Introduction

The Shell Commands feature provides high-level, typed Python wrappers around
the most-used commands of the UEFI Shell: `map`, `ls`, `cp`, `cd`, `bcfg`,
`setvar`, `dmpstore`, `reset`, `dh`, `memmap`, `smbiosview`, `drivers`, and
`devtree`.  Each wrapper parses the textual output of its command into a
typed `frozen=True` dataclass so that test authors never have to scrape raw
strings.  Wrappers are organized one module per command family
(`shellmap.py`, `shellbcfg.py`, `shellsetvar.py`, …) under
`packages/automojo/protocols/uefi/shell/`.

This feature is scoped to the *UEFI Shell Specification* (version 2.2), not
to the core UEFI specification — the Shell is a separate document.  All
wrappers ride on top of layer 4 (`UefiAgent` / `UefiSession`) for command
dispatch, which in turn rides on the layer-3 `UefiShellClient` and the
shell-capable layer-2 transports (serial, IPMI-SOL).

Because UEFI Shell output is frequently affected by firmware vendor
customizations, every wrapper preserves lines it could not classify into a
`.unparsed_lines: tuple[str, ...]` field so callers can still inspect the
original text for diagnosis.  Regex parsers are compiled once at module
import time and are exercised against a checked-in fixtures corpus of
captured real-world output from at least one AMI, one Insyde, one Phoenix,
and one tianocore/OVMF target per Shell version.

## Requirements

### Requirement 1 — Aspects-Aware Public Entry Points

**User Story:** As a test author, I want every shell-command wrapper to
accept an `aspects` override, so that I can tune retry and timeout behavior
per call without rebuilding the agent.

#### Acceptance Criteria

1. WHEN a caller invokes any public wrapper (for example
   `shell_map(agent, aspects=custom)`) THEN the system SHALL honor every
   field of the supplied `UefiCmdAspects` instance.
2. WHEN a caller invokes any public wrapper with `aspects=None` THEN the
   system SHALL call `resolve_aspects(None, agent.aspects)` and use the
   instance default.
3. IF `resolve_aspects` raises `ConfigurationError` THEN the wrapper SHALL
   propagate the error without wrapping or modifying it.
4. WHERE the caller passes a `UefiAspects` (not a `UefiCmdAspects`) THEN the
   wrapper SHALL raise `SemanticError` because shell dispatch requires
   `prompt_pattern` and `response_encoding`.
5. WHEN the wrapper issues the underlying `run_cmd` THEN it SHALL pass the
   resolved aspects through unchanged.

### Requirement 2 — Action Pattern Coverage

**User Story:** As a test author, I want each wrapper to support every
action pattern declared as valid for shell-oriented commands, so that my
retry strategy is consistent across the stack.

#### Acceptance Criteria

1. WHEN `aspects.action_pattern == ActionPattern.SINGLE_CALL` THEN the
   wrapper SHALL issue exactly one command invocation.
2. WHEN `aspects.action_pattern == ActionPattern.SINGLE_CONNECTED_CALL` THEN
   the wrapper SHALL establish a channel, issue one invocation, and close the
   channel.
3. WHEN `aspects.action_pattern == ActionPattern.DO_UNTIL_SUCCESS` THEN the
   wrapper SHALL retry on parse failure or non-allowed status up to
   `aspects.completion_timeout` with `aspects.completion_interval` between
   attempts.
4. WHEN `aspects.action_pattern == ActionPattern.DO_WHILE_SUCCESS` THEN the
   wrapper SHALL re-issue the command while each previous invocation returns
   success, stopping on first failure or on `completion_timeout`.
5. IF `aspects.action_pattern == ActionPattern.DO_UNTIL_CONNECTION_FAILURE`
   THEN the wrapper SHALL raise `SemanticError` because shell commands are
   not connection-flap probes.

### Requirement 3 — Typed Dataclass Return Values

**User Story:** As a test author, I want each wrapper to return a frozen
dataclass rather than a raw string, so that my downstream assertions are
structural rather than text-based.

#### Acceptance Criteria

1. WHEN any wrapper returns successfully THEN the system SHALL return an
   instance of that wrapper's declared dataclass.
2. WHEN any wrapper returns a dataclass THEN the system SHALL ensure
   `frozen=True` so the caller cannot accidentally mutate results.
3. WHEN any wrapper parses output THEN it SHALL preserve all unrecognized
   lines in an `.unparsed_lines: tuple[str, ...]` field.
4. WHEN a wrapper produces a dataclass THEN the dataclass SHALL also carry
   `.raw_stdout: str`, `.raw_stderr: str`, and `.exit_status: int` fields
   for debuggability.
5. WHERE a wrapper represents a list-of-records command (e.g., `map`, `ls`,
   `dh`) THEN the dataclass SHALL expose a `.entries: tuple[<RecordType>, ...]`
   field rather than `list`.

### Requirement 4 — `map` Wrapper

**User Story:** As a test author, I want to enumerate Shell mappings
(`FS0:`, `BLK1:`, …) as structured records, so that I can assert which ESP
is currently reachable.

#### Acceptance Criteria

1. WHEN `shell_map(agent)` runs THEN the system SHALL return a
   `MapResult(entries=tuple[MapEntry, ...], ...)` dataclass per the Shell
   Specification `map` command.
2. WHEN parsing each `MapEntry` THEN the system SHALL extract `alias`
   (e.g., `FS0`), `media_type` (`HD`, `CD`, `RAMDISK`, `USB`, `BLK`),
   `volume_label` when present, and a `device_path_text` string suitable for
   passing to the layer-5 Device Path codec.
3. IF a mapping line cannot be classified THEN the wrapper SHALL append the
   original line to `.unparsed_lines` and continue.
4. WHEN `shell_map(agent, sfo=True)` is invoked THEN the wrapper SHALL issue
   `map -sfo` and parse the Standard-Format-Output variant.

### Requirement 5 — `ls` and `cp` Wrappers

**User Story:** As a test author, I want to inspect and copy files on the
ESP through typed APIs, so that my tests never embed shell text parsing.

#### Acceptance Criteria

1. WHEN `shell_ls(agent, path)` runs THEN the system SHALL return an
   `LsResult` whose `.entries: tuple[LsEntry, ...]` carry `name`, `size`,
   `attributes` (enumerated flags: `READ_ONLY`, `HIDDEN`, `SYSTEM`,
   `DIRECTORY`, `ARCHIVE`), and `modified_time: datetime`.
2. WHEN `shell_cp(agent, src, dst, recursive=False)` runs THEN the system
   SHALL issue `cp <src> <dst>` (or `cp -r`) and return a `CpResult` with
   `.files_copied: int`.
3. IF the source path does not resolve THEN the system SHALL raise
   `UefiShellError` carrying `command`, `status`, `stdout`, and `stderr`.
4. WHEN the caller passes a path containing spaces THEN the wrapper SHALL
   quote the path correctly for the UEFI Shell (double-quotes around the
   full path).

### Requirement 6 — `cd` Wrapper

**User Story:** As a test author, I want to change the Shell's working
directory and have that state survive within a session, so that my
subsequent `ls` and `cp` calls behave intuitively.

#### Acceptance Criteria

1. WHEN `shell_cd(agent, path)` runs inside a `UefiSession` THEN the system
   SHALL record the directory change in the session's `_replay_actions`
   list so that reconnects replay the `cd`.
2. WHEN `shell_cd(agent, None)` runs THEN the system SHALL issue bare `cd`
   and return the current working directory as `.cwd`.
3. IF the target path does not exist THEN the wrapper SHALL raise
   `UefiShellError` and SHALL NOT record a replay action.

### Requirement 7 — `bcfg` Wrapper

**User Story:** As a test author, I want to manipulate boot and driver
option entries via `bcfg` without parsing its output by hand, so that tests
of boot-order behavior are legible.

#### Acceptance Criteria

1. WHEN `shell_bcfg_dump(agent, target=BcfgTarget.BOOT)` runs THEN the
   system SHALL return a `BcfgDumpResult` with `.entries: tuple[BcfgEntry, ...]`
   whose fields are `index: int`, `description: str`, `device_path_text: str`,
   `active: bool`.
2. WHEN `shell_bcfg_add(agent, target, position, file_path, description)`
   runs THEN the system SHALL issue `bcfg <target> add <pos> <fp> "<desc>"`
   and return a `BcfgAddResult` with `.new_index: int`.
3. WHEN `shell_bcfg_rm(agent, target, index)` runs THEN the system SHALL
   issue `bcfg <target> rm <index>` and return a `BcfgRmResult` with
   `.removed_index: int`.
4. WHEN `shell_bcfg_mv(agent, target, from_index, to_index)` runs THEN the
   system SHALL issue `bcfg <target> mv <from> <to>` and return a
   `BcfgMvResult`.
5. IF `target` is not a member of `BcfgTarget` (enum: `BOOT`, `DRIVER`) THEN
   the wrapper SHALL raise `ConfigurationError`.

### Requirement 8 — `setvar` and `dmpstore` Wrappers

**User Story:** As a test author, I want low-level access to UEFI variables
via the Shell so that my variable-services backend (5-variable-services)
can drive CRUD on any shell-capable transport.

#### Acceptance Criteria

1. WHEN `shell_setvar(agent, name, vendor_guid, attributes, data)` runs THEN
   the system SHALL assemble a syntactically valid `setvar` invocation per
   Shell Specification §5.6.19 (including the `-guid`, `-nv`, `-bs`, `-rt`,
   `-at` option flags) and return a `SetvarResult` with `.status: int`.
2. WHEN `shell_setvar(agent, name, vendor_guid, None, None)` runs (delete
   semantics) THEN the system SHALL issue `setvar <name> <guid> =` and
   return a `SetvarResult` with `.deleted: bool = True`.
3. WHEN `shell_dmpstore(agent, name=None, vendor_guid=None)` runs THEN the
   system SHALL return a `DmpstoreResult` with
   `.entries: tuple[DmpstoreEntry, ...]` carrying `name`, `vendor_guid:
   uuid.UUID`, `attributes: int`, `data: bytes`.
4. WHEN the caller passes `save_path` THEN the wrapper SHALL issue
   `dmpstore -s <path>` and return the on-target path in `.saved_to`.
5. IF `attributes` includes `TIME_BASED_AUTHENTICATED_WRITE_ACCESS` THEN the
   wrapper SHALL refuse to synthesize a `setvar` call and SHALL raise
   `SemanticError` directing the caller to 5-variable-services — Shell
   `setvar` cannot sign authenticated variables.

### Requirement 9 — `reset` Wrapper

**User Story:** As a test author, I want to trigger a reset through the
Shell with correct reconnect semantics, so that warm/cold/shutdown
semantics are consistently surfaced.

#### Acceptance Criteria

1. WHEN `shell_reset(agent, kind=ResetKind.COLD)` runs THEN the system SHALL
   issue `reset -c` and return a `ResetResult` with `.issued_at: datetime`.
2. WHEN `shell_reset(agent, kind=ResetKind.WARM)` runs THEN the system
   SHALL issue `reset -w`.
3. WHEN `shell_reset(agent, kind=ResetKind.SHUTDOWN)` runs THEN the system
   SHALL issue `reset -s` and SHALL not attempt any reconnect verification —
   reconnect is the responsibility of the layer-6 workflows.
4. WHEN `shell_reset(agent, reason="text")` runs THEN the system SHALL
   append the reason string correctly-quoted.
5. WHEN the reset command is issued THEN the wrapper SHALL treat
   `ConnectionError` during output read as expected (the target is going
   away) rather than failure, provided the command was successfully sent.

### Requirement 10 — `dh` and `devtree` Wrappers

**User Story:** As a test author, I want to enumerate UEFI handles and the
device-tree hierarchy, so that I can assert which drivers have bound to
which controllers, per UEFI Shell Spec §5.6.2 and §5.6.6.

#### Acceptance Criteria

1. WHEN `shell_dh(agent)` runs THEN the system SHALL return a `DhResult`
   with `.entries: tuple[DhEntry, ...]` carrying `handle: int`,
   `protocols: tuple[uuid.UUID, ...]`, `driver_name: Optional[str]`,
   `controller_name: Optional[str]`.
2. WHEN `shell_dh(agent, protocol_guid=...)` runs THEN the wrapper SHALL
   issue `dh -p <guid>` and restrict results to handles implementing that
   protocol.
3. WHEN `shell_devtree(agent)` runs THEN the system SHALL return a
   `DevtreeResult` with a hierarchical `.root: DevtreeNode` structure whose
   `children: tuple[DevtreeNode, ...]` is populated by parsing indentation.
4. IF `shell_devtree`'s parser cannot reconcile indentation depth THEN the
   wrapper SHALL raise `UefiShellError` with the offending block captured
   in `details`.

### Requirement 11 — `memmap`, `smbiosview`, `drivers` Wrappers

**User Story:** As a test author, I want structured readout of the memory
map, SMBIOS tables, and driver list, so that platform-attribute tests do
not regex-parse shell output inline.

#### Acceptance Criteria

1. WHEN `shell_memmap(agent)` runs THEN the system SHALL return a
   `MemmapResult` with `.entries: tuple[MemmapEntry, ...]` carrying
   `type: MemoryType` enum, `start: int`, `end: int`, `pages: int`,
   `attributes: int`.
2. WHEN `shell_smbiosview(agent, record_type=None)` runs THEN the system
   SHALL return a `SmbiosViewResult` with `.records: tuple[SmbiosRecord, ...]`
   carrying `handle: int`, `type: int`, `length: int`, `fields: dict[str, str]`.
3. WHEN `shell_smbiosview(agent, record_type=1)` runs THEN the wrapper
   SHALL issue `smbiosview -t 1` and restrict to System Information.
4. WHEN `shell_drivers(agent)` runs THEN the system SHALL return a
   `DriversResult` with `.entries: tuple[DriverEntry, ...]` carrying
   `handle: int`, `version: int`, `driver_name: str`, `image_name: str`,
   `diagnostics_supported: bool`, `configuration_supported: bool`.

### Requirement 12 — Unparsed-Lines Preservation

**User Story:** As a test author, I want nothing the Shell prints to be
silently discarded, so that I can diagnose parser regressions on new
firmware revisions without editing library code.

#### Acceptance Criteria

1. WHEN any wrapper encounters an output line it cannot attribute to a
   structured field THEN the system SHALL append the line verbatim to
   `.unparsed_lines`.
2. WHEN the resulting dataclass is rendered via `__repr__` THEN the system
   SHALL include the count of `unparsed_lines` for quick inspection.
3. WHERE `unparsed_lines` is non-empty THEN the wrapper SHALL emit a
   single `WARNING` log record on channel `uefi.shell` naming the command
   and the count of lines.

### Requirement 13 — Fixtures Corpus Coverage

**User Story:** As a maintainer, I want every wrapper's parser to be
validated against captured real-world output from multiple firmware
vendors, so that regressions are caught before they ship.

#### Acceptance Criteria

1. WHEN the test suite runs THEN the system SHALL parse every fixture in
   `tests/fixtures/shell/<command>/` and SHALL assert zero unparsed lines on
   the canonical fixtures.
2. WHERE a fixture is marked `variant=True` THEN non-zero unparsed lines
   SHALL be tolerated but shall be frozen as a snapshot.
3. WHEN a new fixture is added THEN CI SHALL fail if the fixture is not
   listed in the wrapper's parametrized test set.

### Requirement 14 — Parser Compile-Time Determinism

**User Story:** As a maintainer, I want all regex parsers compiled once at
module-import time, so that parsing latency is predictable and a bad regex
fails loudly at import.

#### Acceptance Criteria

1. WHEN any `shell*.py` module is first imported THEN the system SHALL
   compile every regex used by that module at module top level.
2. IF a regex fails to compile at import time THEN the system SHALL raise
   `re.error` and SHALL NOT defer the error to first use.
3. WHEN a test parametrizes parsers THEN the system SHALL expose each
   compiled regex as a module-level constant for direct testing.
