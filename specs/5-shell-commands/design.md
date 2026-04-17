# Design — Shell Commands

## Overview

The Shell Commands feature is a collection of one-module-per-command-family
wrappers under `automojo.protocols.uefi.shell` that turn UEFI Shell text
output into frozen Python dataclasses.  Each wrapper is a thin function
that:

1. Resolves aspects via `resolve_aspects(passed, agent.aspects)`.
2. Builds the Shell command line using a small helper that handles quoting
   and flag assembly.
3. Calls the layer-4 `agent.run_shell_cmd(cmdline, aspects=aspects)` which
   returns a `UefiCmdResult` (stdout, stderr, status).
4. Feeds the stdout to the module's parser, which is a pure function.
5. Returns a `frozen=True` dataclass containing structured records,
   `raw_stdout`, `raw_stderr`, `exit_status`, and `unparsed_lines`.

No wrapper performs I/O itself; all I/O is delegated to `agent`.  This
keeps parsers testable as pure functions against captured fixtures.

## Architecture

```
automojo.protocols.uefi.shell/
    __init__.py         # re-exports the public wrapper functions
    _common.py          # shared quoting, flag-assembly, line-splitter helpers
    _parsers.py         # shared regex fragments used across wrappers
    enums.py            # BcfgTarget, ResetKind, MemoryType, FileAttribute
    shellmap.py         # shell_map + MapResult + MapEntry
    shellls.py          # shell_ls + LsResult + LsEntry
    shellcp.py          # shell_cp + CpResult
    shellcd.py          # shell_cd + CdResult
    shellbcfg.py        # shell_bcfg_* + BcfgDumpResult + BcfgEntry + ...
    shellsetvar.py      # shell_setvar + SetvarResult
    shelldmpstore.py    # shell_dmpstore + DmpstoreResult + DmpstoreEntry
    shellreset.py       # shell_reset + ResetResult
    shelldh.py          # shell_dh + DhResult + DhEntry
    shellmemmap.py      # shell_memmap + MemmapResult + MemmapEntry
    shellsmbiosview.py  # shell_smbiosview + SmbiosViewResult + SmbiosRecord
    shelldrivers.py     # shell_drivers + DriversResult + DriverEntry
    shelldevtree.py     # shell_devtree + DevtreeResult + DevtreeNode
```

Dependency direction (bottom-up, nothing imports upward):

```
shell*.py ──▶ _common.py + _parsers.py + enums.py
           ──▶ layer-1 (aspects, exceptions, helpers)
           ──▶ layer-4 (UefiAgent.run_shell_cmd)
```

## Components and Interfaces

### Common Helpers (`_common.py`)

```python
def quote_shell_path(path: str) -> str:
    """
        Quote a UEFI Shell path for safe interpolation.

        :param path: The path to quote.

        :returns: The path surrounded by double quotes, escaping any embedded
                  double quotes per Shell Specification §3.4.
    """
    escaped = path.replace('"', '""')
    rtnval = f'"{escaped}"'
    return rtnval


def split_output_lines(raw_stdout: str) -> list[str]:
    """
        Split Shell stdout into stripped, non-empty lines.

        :param raw_stdout: The raw stdout text as returned by the transport.

        :returns: A list of non-empty, right-stripped lines preserving order.
    """
    lines = []
    for line in raw_stdout.splitlines():
        stripped = line.rstrip()
        if len(stripped) > 0:
            lines.append(stripped)
    rtnval = lines
    return rtnval
```

### Wrapper Function Shape

Every wrapper follows the same skeleton:

```python
def shell_map(
    agent: "UefiAgent",
    sfo: bool = False,
    aspects: Optional[UefiCmdAspects] = None,
) -> MapResult:
    """
        Run the UEFI Shell 'map' command and return structured entries.

        :param agent: The UefiAgent dispatching the command.
        :param sfo: When True, pass '-sfo' and parse Standard Format Output.
        :param aspects: Optional aspects override.

        :returns: A MapResult dataclass.
        :raises: UefiShellError, SemanticError, ConfigurationError.
    """
    resolved_aspects = resolve_aspects(aspects, agent.aspects)
    if not isinstance(resolved_aspects, UefiCmdAspects):
        err_msg = "shell wrappers require UefiCmdAspects, got UefiAspects."
        raise SemanticError(err_msg)

    cmdline_parts = ["map"]
    if sfo is True:
        cmdline_parts.append("-sfo")
    cmdline = " ".join(cmdline_parts)

    cmd_result = agent.run_shell_cmd(cmdline, aspects=resolved_aspects)

    parsed = parse_map_output(
        raw_stdout=cmd_result.stdout,
        sfo=sfo,
    )

    rtnval = MapResult(
        entries=tuple(parsed.entries),
        unparsed_lines=tuple(parsed.unparsed_lines),
        raw_stdout=cmd_result.stdout,
        raw_stderr=cmd_result.stderr,
        exit_status=cmd_result.status,
    )
    return rtnval
```

### `shell_map` Parser

```python
_MAP_ENTRY_LINE = re.compile(
    r"^(?P<alias>[A-Z][A-Z0-9]*)"
    r"\s*:\s*"
    r"(?P<media>HD|CD|RAMDISK|USB|BLK|FLOPPY)"
    r"(?:\s+\[(?P<label>[^\]]*)\])?"
    r"\s*-\s*"
    r"(?P<dp>.+)$"
)
```

### `shell_dmpstore` Parser

`dmpstore` emits multi-line records of the form:

```
Variable NV+BS 'GlobalDontKnow':{8BE4DF61-93CA-11D2-AA0D-00E098032B8C}
  DataSize = 0x08
  Data:
  00000000: 01 02 03 04 05 06 07 08                         *........*
```

The parser groups lines into records using the "Variable " sentinel and
then decodes the hexdump block into `bytes`.

### `BcfgTarget`, `ResetKind`, `MemoryType` Enums

```python
class BcfgTarget(str, Enum):
    BOOT = "boot"
    DRIVER = "driver"


class ResetKind(str, Enum):
    COLD = "cold"
    WARM = "warm"
    SHUTDOWN = "shutdown"


class MemoryType(IntEnum):
    EFI_RESERVED_MEMORY_TYPE = 0
    EFI_LOADER_CODE = 1
    EFI_LOADER_DATA = 2
    EFI_BOOT_SERVICES_CODE = 3
    EFI_BOOT_SERVICES_DATA = 4
    EFI_RUNTIME_SERVICES_CODE = 5
    EFI_RUNTIME_SERVICES_DATA = 6
    EFI_CONVENTIONAL_MEMORY = 7
    EFI_UNUSABLE_MEMORY = 8
    EFI_ACPI_RECLAIM_MEMORY = 9
    EFI_ACPI_MEMORY_NVS = 10
    EFI_MEMORY_MAPPED_IO = 11
    EFI_MEMORY_MAPPED_IO_PORT_SPACE = 12
    EFI_PAL_CODE = 13
    EFI_PERSISTENT_MEMORY = 14
```

## Data Models

### `MapResult` / `MapEntry`

```python
@dataclass(frozen=True)
class MapEntry:
    alias: str
    media_type: str
    volume_label: Optional[str]
    device_path_text: str


@dataclass(frozen=True)
class MapResult:
    entries: tuple[MapEntry, ...]
    unparsed_lines: tuple[str, ...]
    raw_stdout: str
    raw_stderr: str
    exit_status: int
```

### `LsResult` / `LsEntry`

```python
class FileAttribute(IntFlag):
    READ_ONLY = 0x01
    HIDDEN = 0x02
    SYSTEM = 0x04
    DIRECTORY = 0x10
    ARCHIVE = 0x20


@dataclass(frozen=True)
class LsEntry:
    name: str
    size: int
    attributes: FileAttribute
    modified_time: datetime


@dataclass(frozen=True)
class LsResult:
    path: str
    entries: tuple[LsEntry, ...]
    unparsed_lines: tuple[str, ...]
    raw_stdout: str
    raw_stderr: str
    exit_status: int
```

### `BcfgDumpResult` / `BcfgEntry`

```python
@dataclass(frozen=True)
class BcfgEntry:
    index: int
    description: str
    device_path_text: str
    active: bool


@dataclass(frozen=True)
class BcfgDumpResult:
    target: BcfgTarget
    entries: tuple[BcfgEntry, ...]
    unparsed_lines: tuple[str, ...]
    raw_stdout: str
    raw_stderr: str
    exit_status: int
```

### `DmpstoreResult` / `DmpstoreEntry`

```python
@dataclass(frozen=True)
class DmpstoreEntry:
    name: str
    vendor_guid: uuid.UUID
    attributes: int
    data: bytes


@dataclass(frozen=True)
class DmpstoreResult:
    entries: tuple[DmpstoreEntry, ...]
    saved_to: Optional[str]
    unparsed_lines: tuple[str, ...]
    raw_stdout: str
    raw_stderr: str
    exit_status: int
```

### `MemmapResult` / `MemmapEntry`

| Field          | Type          | Meaning                                |
|----------------|---------------|----------------------------------------|
| `type`         | `MemoryType`  | EFI memory type enum                   |
| `start`        | `int`         | First physical byte (inclusive)        |
| `end`          | `int`         | Last physical byte (inclusive)         |
| `pages`        | `int`         | Page count (4 KiB pages per UEFI spec) |
| `attributes`   | `int`         | Raw attributes bitfield                |

### `SmbiosViewResult` / `SmbiosRecord`

```python
@dataclass(frozen=True)
class SmbiosRecord:
    handle: int
    type: int
    length: int
    fields: dict[str, str]   # sorted, immutable via MappingProxyType on access


@dataclass(frozen=True)
class SmbiosViewResult:
    records: tuple[SmbiosRecord, ...]
    unparsed_lines: tuple[str, ...]
    raw_stdout: str
    raw_stderr: str
    exit_status: int
```

### `DevtreeNode`

```python
@dataclass(frozen=True)
class DevtreeNode:
    depth: int
    handle: int
    name: str
    children: tuple["DevtreeNode", ...]
```

## Measured Interop Contract

Every public wrapper accepts `aspects: Optional[UefiCmdAspects] = None` at
the last positional slot of its signature.

Aspects fields consumed:

* `action_pattern` — dispatched to layer 4 `run_shell_cmd`.
* `completion_timeout`, `completion_interval` — used by the retry loop
  implemented in layer 4 when `action_pattern in {DO_UNTIL_SUCCESS,
  DO_WHILE_SUCCESS}`.
* `inactivity_timeout`, `inactivity_interval` — forwarded to the channel
  for prompt-wait governance.
* `monitor_delay` — forwarded to the `MonitoredScope` wrapping the call.
* `logging_pattern` — consulted in layer 4 `_log_command_result`.
* `allowed_error_codes` — used to decide whether a non-zero
  `exit_status` constitutes a failure for retry purposes.
* `expected_status` — default success status set (usually `0`); exposed so
  commands like `bcfg` which can return non-zero on intentionally-absent
  entries are configurable.
* `prompt_pattern` — forwarded to the channel's `recv_until`.
* `response_encoding` — used when decoding the byte buffer to `str`.
* `logger` — used for channel-scoped shell logging.

ActionPatterns supported:

| Pattern                          | Supported | Notes                                  |
|----------------------------------|-----------|----------------------------------------|
| `SINGLE_CALL`                    | Yes       | Default.                               |
| `SINGLE_CONNECTED_CALL`          | Yes       | Open-call-close around one invocation. |
| `DO_UNTIL_SUCCESS`               | Yes       | Retry on parse or status failure.      |
| `DO_WHILE_SUCCESS`               | Yes       | Re-issue while success; stop on fail.  |
| `DO_UNTIL_CONNECTION_FAILURE`    | No        | Raises `SemanticError`.                |

## Error Handling

| Condition                                    | Exception              | `details`                            |
|----------------------------------------------|------------------------|--------------------------------------|
| Non-allowed `exit_status`                    | `UefiShellError`       | command, status, stdout, stderr      |
| Parser could not reconcile mandatory fields  | `UefiShellError`       | command, offending block             |
| `aspects` is `UefiAspects` not `UefiCmdAspects` | `SemanticError`     | caller class name                    |
| `DO_UNTIL_CONNECTION_FAILURE` requested      | `SemanticError`        | pattern name                         |
| Invalid enum value passed                    | `ConfigurationError`   | offending value                      |
| `setvar` with time-based auth                | `SemanticError`        | pointer to 5-variable-services       |

`SemanticError` is never caught inside this feature — it always bubbles.

## Logging

Channel: `uefi.shell`.

* One `INFO` line per wrapper invocation: command text and resolved action
  pattern.
* One `DEBUG` line per successful parse: entry count.
* One `WARNING` line when `unparsed_lines` is non-empty: command and count.
* One `ERROR` line on `UefiShellError` before re-raise.

Logging is gated by `aspects.logging_pattern` exactly as in
`SshBase._log_command_result`.

## Security Considerations

* `shell_setvar` can write any Shell-reachable variable, including
  attributes with `RUNTIME_ACCESS`; callers must pair this wrapper with
  appropriate operational controls.
* `quote_shell_path` is the sole interpolation point for caller-supplied
  path strings — no wrapper concatenates raw user input into a command
  line.
* No wrapper logs variable `Data` contents at `INFO`; `dmpstore` entries
  are logged at `DEBUG` only, and the hexdump is truncated to 256 bytes.

## Testing Strategy

### Unit — parsers as pure functions

Every `parse_*` function is invoked against a fixture corpus under
`tests/fixtures/shell/<command>/` with file naming
`<vendor>-<shell-version>-<scenario>.txt`.  Each fixture has a sibling
`.expected.json` with the structured expectation.

### Unit — wrapper glue

With `agent.run_shell_cmd` mocked, verify:

* `aspects=None` resolves from `agent.aspects`.
* `aspects=<custom>` forwards unchanged.
* `UefiAspects` (not command aspects) raises `SemanticError`.
* `DO_UNTIL_CONNECTION_FAILURE` raises `SemanticError`.
* Quoting of paths with embedded spaces and quotes.

### Unit — enum validation

* `BcfgTarget` rejects arbitrary strings at wrapper boundary.
* `ResetKind` exhaustive enumeration round-trips through wrapper invocation.

### Integration — action-pattern behavior

Using a fake `UefiAgent` that records invocation count and can be scripted
to fail on specific attempts:

* `SINGLE_CALL` — exactly one invocation even on failure.
* `DO_UNTIL_SUCCESS` — stops on first success, retries up to
  `completion_timeout`.
* `DO_WHILE_SUCCESS` — re-issues while success, stops on first failure.

### Fixtures coverage gate

* Every wrapper SHALL have at least one AMI, one Insyde, one OVMF fixture.
* CI SHALL fail if a fixture file exists that is not referenced from a
  parametrized test.

## Dependencies on Other Features

* `1-aspects` — `UefiCmdAspects`, `resolve_aspects`, `ActionPattern`,
  `LoggingPattern`.
* `1-constants-and-types` — GUID aliases used by `dh` and `dmpstore`.
* `1-exceptions` — `UefiShellError`, `SemanticError`, `ConfigurationError`.
* `1-helpers` — `indent_lines`, hexdump decoder for `dmpstore`.
* `1-logging` — `uefi.shell` channel.
* `4-agent` — `UefiAgent.run_shell_cmd` dispatch surface.
* `4-session` — for `shell_cd`'s replay-on-reconnect hook.
* `5-device-path-codec` — referenced textually; this feature does not
  import from it.  Callers who want a decoded device path pass the
  `device_path_text` string to the codec themselves.

Downstream consumers: `5-variable-services` (shell backend),
`5-boot-manager` (bcfg fallback), `5-esp-file-ops` (shell backend),
`5-system-reset` (reset dispatch), `5-system-tables` (memmap/smbiosview
source).
