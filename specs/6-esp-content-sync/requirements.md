# Requirements — ESP Content Sync Workflow

## Introduction

The ESP Content Sync workflow mirrors a local directory tree into
`/EFI/<vendor>/` on the target's EFI System Partition using rsync-style
delta semantics: only files that differ are pushed, files may optionally
be deleted when missing locally, and glob-based `--include` / `--exclude`
rules let callers restrict the scope.  The workflow is the common case for
staging UEFI applications (a vendor's `*.efi` loader, test binaries, HII
configuration artifacts) onto a target before a boot-order change or a
boot-to-option-once operation.

This workflow composes `5-esp-file-ops` with a checksum-diff layer and a
glob-matching layer.  It is layer-6 and delegates per-call aspects to
`5-esp-file-ops`.  Relevant UEFI spec sections are §13.3–§13.5 (File
Protocol, Simple File System).

## Requirements

### Requirement 1 — Single-Call Sync Entry Point

**User Story:** As a test author, I want one call that mirrors a local
directory onto the ESP, so that my test setup step is one line instead of
a loop over `file_push`.

#### Acceptance Criteria

1. WHEN the caller invokes `esp_content_sync(agent, local_root,
   remote_root, aspects=...)` THEN the system SHALL walk `local_root`,
   compute per-file checksums, diff against remote, and push only files
   whose checksums differ.
2. WHEN the workflow completes successfully THEN the system SHALL return
   an `EspSyncReport` with `succeeded=True` listing every action taken.
3. WHEN the workflow fails mid-transfer THEN the system SHALL raise the
   underlying error and attach the partial report to `details`.

### Requirement 2 — Checksum-Based Skip

**User Story:** As a test author, I want unchanged files skipped
automatically, so that re-running the same test does not spend time
re-pushing large binaries.

#### Acceptance Criteria

1. WHEN a remote file exists THEN the system SHALL compute its SHA-256
   via `5-esp-file-ops` (using `file_pull` into an in-memory buffer for
   transports without remote-side hashing, or the transport's native hash
   API when available).
2. WHEN the remote SHA-256 matches the local SHA-256 THEN the system SHALL
   skip the push and record `FileAction.SKIPPED` in the report.
3. WHEN the remote SHA-256 differs THEN the system SHALL push the file
   and record `FileAction.PUSHED`.
4. WHEN the remote file does not exist THEN the system SHALL push it and
   record `FileAction.CREATED`.

### Requirement 3 — Optional Delete-Missing Mode

**User Story:** As a test author, I want to mirror (not merely union), so
that files left over from a previous test do not leak forward.

#### Acceptance Criteria

1. WHEN the caller omits `delete=False` (default) THEN the system SHALL
   leave remote files untouched that do not correspond to any local file.
2. WHEN `delete=True` THEN the system SHALL delete remote files under
   `remote_root` that have no matching local file after include/exclude
   filtering, and record `FileAction.DELETED`.
3. WHEN `delete=True` AND a remote directory becomes empty THEN the
   system SHALL remove the directory and record
   `FileAction.DIRECTORY_REMOVED`.

### Requirement 4 — Include/Exclude Glob Lists

**User Story:** As a test author, I want to restrict the sync to a subset
of files, so that I do not push my entire build tree.

#### Acceptance Criteria

1. WHEN the caller passes `include=[...]` THEN the system SHALL only
   consider local files whose path (relative to `local_root`) matches
   at least one include glob.
2. WHEN the caller passes `exclude=[...]` THEN the system SHALL exclude
   local files whose path matches any exclude glob after include
   filtering.
3. WHEN both lists are omitted THEN the system SHALL consider every file
   under `local_root`.
4. WHEN a path matches both include and exclude THEN the system SHALL
   exclude it (exclude wins).
5. IF any glob pattern is malformed THEN the system SHALL raise
   `ConfigurationError` naming the offending pattern.

### Requirement 5 — Path Normalization

**User Story:** As a test author, I want to write POSIX-style paths in my
test regardless of which transport I happen to be using, so that my
include/exclude globs work the same everywhere.

#### Acceptance Criteria

1. WHEN `remote_root` is passed THEN the system SHALL accept either
   POSIX-style (`/EFI/vendor/`) or UEFI Shell-style (`FS0:\EFI\vendor\`)
   and normalize internally.
2. WHERE the transport is Shell the system SHALL translate internal
   POSIX paths to `FS0:\path\with\backslashes` per UEFI Shell conventions.
3. WHERE the transport is SSH-to-host the system SHALL translate to the
   OS mount point of the ESP (typically `/boot/efi` or `/efi`).
4. WHERE the transport is Redfish VirtualMedia the system SHALL use the
   VirtualMedia-mount-point convention.

### Requirement 6 — Structured Report

**User Story:** As a test author, I want a per-file action log in the
result, so that I can reason about what the sync actually did.

#### Acceptance Criteria

1. WHEN the workflow completes THEN the report SHALL carry:
   `succeeded: bool`, `local_root: Path`, `remote_root: str`,
   `actions: tuple[FileActionRecord, ...]`,
   `bytes_pushed: int`, `files_skipped: int`, `files_deleted: int`,
   `started_at: datetime`.
2. WHEN serialized via `as_dict()` THEN the report SHALL be JSON-safe.
3. WHERE `delete=True` the report SHALL include the directory-removed
   records distinctly from file-deleted records.

### Requirement 7 — Aspects Delegation

**User Story:** As a test author, I want the workflow's retry and timeout
behavior driven by the aspects object I already use, so that configuration
is not duplicated.

#### Acceptance Criteria

1. WHEN the caller passes `aspects` THEN the system SHALL forward it to
   `5-esp-file-ops` unchanged for every file push, pull, and delete.
2. IF `aspects is None` THEN the system SHALL resolve from the agent
   default via `resolve_aspects`.
3. WHEN `aspects.action_pattern == ActionPattern.SINGLE_CALL` THEN the
   system SHALL attempt each file action once and let errors propagate.

### Requirement 8 — Action Pattern Support

**User Story:** As a test author, I want to pick an appropriate retry
pattern for the workflow.

#### Acceptance Criteria

1. WHEN `aspects.action_pattern == ActionPattern.SINGLE_CALL` THEN each
   per-file action SHALL be attempted once.
2. WHEN `aspects.action_pattern == ActionPattern.DO_UNTIL_SUCCESS` THEN
   each per-file action SHALL retry up to `aspects.completion_timeout`.
3. WHEN `aspects.action_pattern == ActionPattern.SINGLE_CONNECTED_CALL`
   THEN the system SHALL open one channel and reuse it for every file
   action.
4. WHEN `aspects.action_pattern == ActionPattern.DO_WHILE_SUCCESS` THEN
   the system SHALL raise `SemanticError`.
5. WHEN `aspects.action_pattern ==
   ActionPattern.DO_UNTIL_CONNECTION_FAILURE` THEN the system SHALL raise
   `SemanticError` — a mirror completes, it does not loop until
   disconnection.

### Requirement 9 — Ordering Guarantees

**User Story:** As a test author, I want pushes to happen before deletes,
so that a failure mid-sync does not leave the target in a boot-broken
state.

#### Acceptance Criteria

1. WHEN `delete=True` THEN the system SHALL push or skip every local
   file first, then perform deletions.
2. WHEN creating new files THEN the system SHALL create any missing
   intermediate directories first.
3. WHEN deleting directories THEN the system SHALL delete leaf files
   first, then remove empty parent directories bottom-up.

### Requirement 10 — No-op Detection

**User Story:** As a test author, I want a crisp "nothing to do" result
when the two trees are already identical, so that my test output is quiet
on happy-path re-runs.

#### Acceptance Criteria

1. WHEN every local file's SHA-256 matches the corresponding remote
   SHA-256 AND no local files are absent remotely AND (delete=False OR no
   orphan remotely) THEN the report SHALL have `bytes_pushed == 0`,
   `files_skipped == len(considered)`, and every action record shall be
   `FileAction.SKIPPED`.
2. WHEN `logging_pattern == LoggingPattern.SUCCESS_ONLY` AND every file
   was skipped THEN the workflow SHALL emit a single summary log line
   rather than one per file.

### Requirement 11 — Integration Tests

**User Story:** As a maintainer, I want targeted tests for the three
distinctive cases (no-op, include/exclude, delete mode), so that I catch
regressions in each semantic axis independently.

#### Acceptance Criteria

1. WHEN the test suite runs THEN the system SHALL include a test that
   runs the workflow twice and asserts the second run is a complete no-op
   (every action is `SKIPPED`).
2. WHEN the test suite runs THEN the system SHALL include a test
   exercising both include and exclude globs with overlap (exclude wins).
3. WHEN the test suite runs THEN the system SHALL include a test
   exercising `delete=True` with files missing locally.
4. WHEN the test suite runs with `AUTOMOJO_OVMF_TEST=1` THEN the system
   SHALL include an integration test against an OVMF-mounted ESP.
