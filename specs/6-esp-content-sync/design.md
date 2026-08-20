# Design — ESP Content Sync Workflow

## Overview

The ESP Content Sync workflow mirrors a local directory tree into a remote
path on the target's EFI System Partition using rsync-style delta
semantics.  It composes `5-esp-file-ops` (for per-file push, pull, delete,
directory enumeration) with a local checksum/glob layer that decides what
actions to take.  The workflow returns an `EspSyncReport` with a full
per-file action record.

Design priorities in order: correctness (no silent skips of changed files,
no surprise deletions), determinism (the same inputs produce the same
report), and observability (every decision logged).

## Architecture

```
ccai.protocols.uefi.workflows.espsync/
    __init__.py              # re-exports
    espcontentsync.py        # EspContentSyncWorkflow + convenience fn
    espsyncreport.py         # report + FileActionRecord + FileAction enum
    globfilter.py            # include/exclude glob evaluator
    pathnormalize.py         # per-transport path translator
    diffengine.py            # local vs. remote SHA-256 diff logic
```

## Components and Interfaces

### `EspContentSyncWorkflow`

```python
class EspContentSyncWorkflow:
    def __init__(
        self,
        agent: "UefiAgent",
        default_aspects: Optional[UefiCmdAspects] = None,
    ) -> None:
        self._agent = agent
        self._default_aspects = default_aspects
        return

    def run(
        self,
        local_root: os.PathLike[str],
        remote_root: str,
        include: Optional[Sequence[str]] = None,
        exclude: Optional[Sequence[str]] = None,
        delete: bool = False,
        aspects: Optional[UefiCmdAspects] = None,
    ) -> "EspSyncReport":
        ...
```

Execution order:

1. Resolve aspects.
2. Validate `local_root` exists and is a directory.
3. Normalize `remote_root` for the active transport via
   `pathnormalize.to_transport(...)`.
4. Compile `include` / `exclude` globs via `globfilter.compile(...)`.
5. Walk `local_root` and collect `LocalFile(path, sha256, size)` records
   filtered by the glob filter.
6. Walk `remote_root` via `5-esp-file-ops.directory_tree(...)` and
   collect `RemoteFile(path, size)` records.
7. Compute diff via `diffengine.diff(local_files, remote_files,
   delete=delete, agent=agent, aspects=aspects)`.
8. Execute the diff: create missing directories → push new/changed files
   → (if `delete`) remove orphaned files → remove emptied directories.
9. Build and return `EspSyncReport`.

### `FileAction` enum and `FileActionRecord`

```python
class FileAction(IntEnum):
    SKIPPED = 0
    CREATED = 1
    PUSHED = 2
    DELETED = 3
    DIRECTORY_CREATED = 4
    DIRECTORY_REMOVED = 5

@dataclass(frozen=True)
class FileActionRecord:
    action: FileAction
    path: str
    bytes_transferred: int
    local_sha256: Optional[str]
    remote_sha256_before: Optional[str]
```

### `EspSyncReport`

```python
@dataclass(frozen=True)
class EspSyncReport:
    succeeded: bool
    local_root: Path
    remote_root: str
    actions: tuple[FileActionRecord, ...]
    bytes_pushed: int
    files_skipped: int
    files_deleted: int
    started_at: datetime

    def as_dict(self) -> dict[str, object]:
        ...
```

### `globfilter.compile`

```python
def compile(
    include: Optional[Sequence[str]],
    exclude: Optional[Sequence[str]],
) -> "GlobFilter":
    ...

class GlobFilter:
    def admits(self, relative_path: str) -> bool:
        # exclude wins over include; empty include == admit all
        ...
```

Uses `fnmatch.translate` to compile globs to regex; unmatched globs raise
`ConfigurationError` naming the offending pattern.

### `pathnormalize.to_transport`

```python
def to_transport(
    remote_root: str,
    transport_kind: str,
) -> str:
    # POSIX -> Shell (FS0:\...) | SSH-host (/boot/efi/...) | Redfish
    ...
```

### `diffengine.diff`

```python
@dataclass(frozen=True)
class DiffPlan:
    directories_to_create: tuple[str, ...]
    files_to_push: tuple[PushAction, ...]
    files_to_skip: tuple[str, ...]
    files_to_delete: tuple[str, ...]
    directories_to_remove: tuple[str, ...]

def diff(
    local_files: Sequence[LocalFile],
    remote_files: Sequence[RemoteFile],
    delete: bool,
    agent: "UefiAgent",
    aspects: UefiCmdAspects,
) -> DiffPlan:
    ...
```

The diff step computes per-file SHA-256 on the remote side on demand via
`5-esp-file-ops.file_pull` into an in-memory buffer (chunked for large
files).  Transports that expose a native remote-hash API (Redfish when
supported) use that instead.

### `esp_content_sync` convenience function

```python
def esp_content_sync(
    agent: "UefiAgent",
    local_root: os.PathLike[str],
    remote_root: str,
    include: Optional[Sequence[str]] = None,
    exclude: Optional[Sequence[str]] = None,
    delete: bool = False,
    aspects: Optional[UefiCmdAspects] = None,
) -> EspSyncReport:
    workflow = EspContentSyncWorkflow(agent=agent)
    rtnval = workflow.run(
        local_root=local_root,
        remote_root=remote_root,
        include=include,
        exclude=exclude,
        delete=delete,
        aspects=aspects,
    )
    return rtnval
```

## Data Models

* `LocalFile(path, sha256, size)`
* `RemoteFile(path, size)`
* `DiffPlan` — described above.
* `FileAction`, `FileActionRecord`, `EspSyncReport` — described above.

## Measured Interop Contract

The workflow delegates aspects unchanged to `5-esp-file-ops` for every
per-file operation.  Fields consumed:

* `aspects.action_pattern` — controls per-file retry behavior.  Supported:
  `SINGLE_CALL`, `SINGLE_CONNECTED_CALL`, `DO_UNTIL_SUCCESS`.
  `DO_WHILE_SUCCESS` and `DO_UNTIL_CONNECTION_FAILURE` raise
  `SemanticError`.
* `aspects.completion_timeout` / `aspects.completion_interval` —
  forwarded to each file op.
* `aspects.connection_timeout` / `aspects.connection_interval` —
  forwarded.
* `aspects.inactivity_timeout` / `aspects.inactivity_interval` —
  forwarded (important for large-file streaming).
* `aspects.allowed_error_codes` / `aspects.allowed_connection_exceptions`
  — forwarded.
* `aspects.logging_pattern` — consumed for per-file and summary logging.

The workflow does not impose invariants beyond action-pattern rejection.
It delegates all aspects to its constituent layer-5 feature.

## Error Handling

| Condition                                 | Exception              | Included in `details`                        |
|-------------------------------------------|------------------------|----------------------------------------------|
| `local_root` missing or not a directory   | `ConfigurationError`   | path, observed stat                          |
| Glob pattern malformed                    | `ConfigurationError`   | pattern                                      |
| Transport does not expose directory_tree  | `ConfigurationError`   | transport kind                               |
| Checksum computation fails                | underlying error       | path, transport-specific cause               |
| Push fails for a given file               | underlying error       | path, partial bytes, report so far           |
| `action_pattern` unsupported              | `SemanticError`        | offending pattern                            |
| `aspects` and agent default both `None`   | `ConfigurationError`   | agent repr                                   |

## Logging

Channel: `uefi.workflow.espsync`.

* At `INFO`: workflow start with roots, include/exclude counts, delete
  flag, aspects summary.
* At `INFO` (`LoggingPattern.ALL_RESULTS`): per-file action.
* At `INFO` (`LoggingPattern.SUCCESS_ONLY` + no-op run): one summary line
  only.
* At `DEBUG`: computed local and remote SHA-256 digests.
* At `ERROR`: structured dict of partial report on failure.

## Security Considerations

* The workflow writes to the ESP — a write-enabled, boot-relevant
  filesystem.  Callers must ensure `remote_root` is under a vendor
  subdirectory they control; the workflow does not enforce that.
* `delete=True` can remove firmware loader binaries and brick boot if
  misused.  The workflow logs every delete before performing it.
* Glob patterns are sanity-checked but not sandboxed; a sufficiently
  permissive include with `delete=True` can still wipe an entire vendor
  directory.  This is documented, not prevented.

## Testing Strategy

### Unit tests

* `test_globfilter.py` — include-only, exclude-only, overlap
  (exclude wins), malformed pattern.
* `test_pathnormalize.py` — POSIX to Shell, Shell to POSIX, SSH-host,
  Redfish round trips.
* `test_diffengine.py` — every combination of (local present/absent) ×
  (remote present/absent) × (checksum match/mismatch) × (delete flag).
* `test_noop_rerun.py` — two successive runs; second is all-skipped.
* `test_ordering.py` — push-before-delete, bottom-up directory
  removal.

### Integration tests

* `test_shell_transport_sync.py` — real files over Shell transport using
  a fake/simulated ESP.
* `test_ssh_host_sync.py` — real files over SSH-to-host.
* `test_ovmf_sync.py` — full OVMF-backed sync (gated by
  `CCAI_OVMF_TEST=1`).

### Action-pattern tests

* `SINGLE_CALL` per-file.
* `SINGLE_CONNECTED_CALL` — one channel held across files.
* `DO_UNTIL_SUCCESS` — simulated transient per-file failure retried.
* `DO_WHILE_SUCCESS` and `DO_UNTIL_CONNECTION_FAILURE` — rejected.

## Composition Diagram

```
caller
  │
  ▼
esp_content_sync(agent, local_root, remote_root, ...)
  │
  │  1. resolve_aspects(...)
  │  2. validate local_root
  │  3. pathnormalize.to_transport(remote_root, transport_kind)
  │  4. globfilter.compile(include, exclude)
  │
  ├──▶ walk local tree → LocalFile(path, sha256, size) records
  │
  ├──▶ 5-esp-file-ops.directory_tree(remote_root)
  │        → RemoteFile(path, size) records
  │
  ├──▶ diffengine.diff(locals, remotes, delete, agent, aspects)
  │        │
  │        └──▶ 5-esp-file-ops.file_pull(path)
  │             for each remote file to compute SHA-256 (or native
  │             remote-hash API when transport supports it)
  │
  ├──▶ for dir in plan.directories_to_create:
  │       5-esp-file-ops.directory_create(dir)
  │
  ├──▶ for push in plan.files_to_push:
  │       5-esp-file-ops.file_push(local, remote, aspects)
  │
  ├──▶ for file in plan.files_to_delete:        (if delete=True)
  │       5-esp-file-ops.file_delete(file, aspects)
  │
  ├──▶ for dir in plan.directories_to_remove:   (if delete=True)
  │       5-esp-file-ops.directory_remove(dir, aspects)
  ▼
EspSyncReport
```

## Dependencies on Other Features

* `1-aspects` — aspects, action patterns, resolver.
* `1-exceptions` — `ConfigurationError`, `SemanticError`.
* `1-logging` — channel registration.
* `1-helpers` — SHA-256 via `hashlib` wrapper; `indent_lines` for
  multiline log output.
* `4-agent` — agent and transport identity.
* `5-esp-file-ops` — every actual file op (push, pull, delete,
  directory_tree).
