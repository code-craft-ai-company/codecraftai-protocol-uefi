# Requirements — ESP File Operations

## Introduction

The ESP File Operations feature provides a transport-agnostic file API over
the EFI System Partition (ESP) that mirrors the `SshAgent` file API in
`automojo-protocol-ssh`.  It exposes `file_push`, `file_pull`, `file_exists`,
`directory_exists`, `directory`, and `directory_tree` methods whose
signatures are identical across the three supported transports — UEFI Shell
(`cp` / `ls`), SSH-to-host (OS-mounted ESP via `/boot/efi`), and Redfish
(VirtualMedia upload / download).

Callers of this feature do not switch code paths based on transport.  The
`EspFileProvider` facade owns the routing decision and hides per-transport
path-normalization, chunked-streaming, and authentication mechanics.  Paths
handed to the facade are normalized to the UEFI convention (`\` separators
with `FS0:` volume aliases) for the Shell transport and translated to POSIX
for the SSH-to-host transport.

This feature targets firmware-update flows, bootloader staging, and diagnostic
evidence retrieval — operations that need to move files between a test
harness and the ESP of a device under test without the caller having to know
whether the device is currently in UEFI Shell, booted into an OS, or reachable
only through an out-of-band BMC.  It references UEFI Specification
§13.3 (Simple File System Protocol), §13.4 (File Protocol), and §13.5
(UEFI File-Level Security).

## Requirements

### Requirement 1 — Uniform API Surface Across Transports

**User Story:** As a test author, I want the same `EspFileProvider` method
signatures regardless of transport, so that my test code never branches on
transport kind.

#### Acceptance Criteria

1. WHEN `EspFileProvider.file_push(localpath, remotepath, aspects)` is invoked
   over the Shell, SSH-to-host, or Redfish transport THEN the system SHALL
   accept identical argument types and SHALL raise identical exception types
   on failure.
2. WHEN `EspFileProvider.file_pull(remotepath, localpath, aspects)` is invoked
   over any supported transport THEN the system SHALL produce a local file
   byte-identical to the remote source.
3. WHEN `EspFileProvider.file_exists(remotepath, aspects)` is invoked over any
   supported transport THEN the system SHALL return `bool`.
4. WHEN `EspFileProvider.directory_exists(remotepath, aspects)` is invoked
   over any supported transport THEN the system SHALL return `bool`.
5. WHEN `EspFileProvider.directory(remotepath, aspects)` is invoked over any
   supported transport THEN the system SHALL return a `list[EspDirEntry]`
   containing only the immediate children of `remotepath`.
6. WHEN `EspFileProvider.directory_tree(remotepath, aspects)` is invoked over
   any supported transport THEN the system SHALL return an `EspDirTree` whose
   children recursively enumerate the subtree rooted at `remotepath`.

### Requirement 2 — UEFI-Convention Path Normalization (Shell Transport)

**User Story:** As a test author, I want to pass paths using whichever
separator feels natural, so that I do not have to hand-roll path handling
for the UEFI Shell.

#### Acceptance Criteria

1. WHEN the Shell backend receives a path using `/` separators THEN the
   system SHALL normalize the path to `\` separators before issuing shell
   commands.
2. WHEN the Shell backend receives a path without a volume alias THEN the
   system SHALL prepend the active map alias (default `FS0:`) resolved via
   the UEFI Shell `map` command per §13.3.
3. WHEN the Shell backend receives a path already beginning with a `FS#:` or
   `BLK#:` alias THEN the system SHALL preserve that alias without
   modification.
4. IF the normalized path contains `..` segments after expansion THEN the
   system SHALL raise `UefiError` with `details` naming the input and the
   offending expansion.
5. WHEN the Shell backend renders a normalized path into a `cp` or `ls`
   command THEN the system SHALL quote path tokens containing spaces per the
   UEFI Shell quoting rules.

### Requirement 3 — POSIX Translation (SSH-to-Host Transport)

**User Story:** As a test author, I want to address ESP files using UEFI
paths even when the backing transport is SSH-to-host, so that my test code
does not have to know that `FS0:\EFI\BOOT\BOOTX64.EFI` lives at
`/boot/efi/EFI/BOOT/BOOTX64.EFI` on a running Linux.

#### Acceptance Criteria

1. WHEN the SSH-to-host backend receives a UEFI-convention path (e.g.,
   `FS0:\EFI\BOOT\BOOTX64.EFI`) THEN the system SHALL translate it to
   `{esp_mount}/EFI/BOOT/BOOTX64.EFI` where `esp_mount` is probed from the OS.
2. WHEN `esp_mount` cannot be determined THEN the system SHALL raise
   `UefiTransportError` with `details` listing the probed candidate mounts
   (`/boot/efi`, `/efi`, `/boot`) and the kernel `mount` output.
3. WHEN the SSH-to-host backend receives a POSIX-style path rooted at the
   discovered `esp_mount` THEN the system SHALL accept it without translation.
4. IF a path resolves outside of `esp_mount` after translation THEN the
   system SHALL raise `UefiError` citing the input and the resolved path.

### Requirement 4 — Chunked Streaming for Large Files

**User Story:** As a test author, I want to push a multi-megabyte firmware
capsule without blowing the process heap, so that very large payloads move
without memory spikes.

#### Acceptance Criteria

1. WHEN `file_push` is invoked for a source file larger than 16 MiB THEN the
   system SHALL stream chunks of `aspects.chunk_size` bytes (default 64 KiB)
   without loading the entire file into memory.
2. WHEN the caller passes `chunk_size` via the aspects override THEN the
   system SHALL honor it exactly, provided it is >= 512 bytes and <= 16 MiB.
3. IF `chunk_size` is outside the [512, 16777216] range THEN the system SHALL
   raise `ConfigurationError` naming the offending value.
4. WHEN streaming completes THEN the system SHALL verify the destination byte
   length equals the source byte length before returning.
5. IF byte-length verification fails THEN the system SHALL raise `UefiError`
   with `details` listing expected and observed sizes.

### Requirement 5 — Redfish VirtualMedia Upload

**User Story:** As a test author running against a headless BMC, I want to
push files into the ESP via VirtualMedia without setting up a separate HTTP
server, so that I do not need external infrastructure.

#### Acceptance Criteria

1. WHEN the Redfish backend executes `file_push` THEN the system SHALL POST
   the payload to `/redfish/v1/Managers/{id}/VirtualMedia/{slot}/Actions/VirtualMedia.InsertMedia`
   using a multipart body per the UpdateService / VirtualMedia conventions.
2. WHEN the Redfish response is `202 Accepted` with a `Location:` TaskMonitor
   THEN the system SHALL poll the task every `aspects.completion_interval`
   until `aspects.completion_timeout` elapses.
3. IF the task reports a failure `TaskState` THEN the system SHALL raise
   `UefiError` with `details` containing the Redfish `Messages[]` array.
4. WHEN the backend must authenticate THEN the system SHALL use the
   `UefiRedfishCredential` session token and SHALL refresh on 401 per the
   transport contract.

### Requirement 6 — Directory Listing Normalization

**User Story:** As a test author, I want directory listings as typed
dataclasses so that I never have to parse `ls` text output myself.

#### Acceptance Criteria

1. WHEN `directory(remotepath)` returns THEN each element SHALL be an
   `EspDirEntry` with fields `name: str`, `is_dir: bool`, `size: int`,
   `mtime: datetime`, `attributes: EspFileAttributes`.
2. WHEN a file has the UEFI read-only attribute set (§13.4) THEN the
   returned `EspFileAttributes.read_only` SHALL be `True`.
3. WHEN `directory_tree(remotepath)` returns THEN the root SHALL be an
   `EspDirTree` whose `entries` list enumerates immediate children and whose
   `subtrees` list enumerates nested `EspDirTree` objects in pre-order.
4. WHEN the caller enumerates `.` or `..` pseudo-entries THEN the system
   SHALL NOT include them in either return.
5. WHEN the target directory does not exist THEN `directory` and
   `directory_tree` SHALL raise `UefiError` with `details` naming the path.

### Requirement 7 — SINGLE_CALL Action Pattern

**User Story:** As a test author, I want file operations to default to a
single attempt so that I can detect transport instability immediately.

#### Acceptance Criteria

1. WHEN `aspects.action_pattern == ActionPattern.SINGLE_CALL` THEN the system
   SHALL invoke the backend exactly once and SHALL propagate any exception
   raised.
2. WHEN `aspects.action_pattern == ActionPattern.SINGLE_CALL` and the backend
   succeeds THEN the system SHALL return the backend result unchanged.

### Requirement 8 — SINGLE_CONNECTED_CALL Action Pattern

**User Story:** As a test author, I want to ensure the transport is connected
before the file operation runs, but without adding retry semantics to the
operation itself, so that connectivity faults fail fast and distinctly.

#### Acceptance Criteria

1. WHEN `aspects.action_pattern == ActionPattern.SINGLE_CONNECTED_CALL` THEN
   the system SHALL call `transport.verify_connectivity(aspects)` before the
   file operation.
2. IF connectivity verification fails within `aspects.connection_timeout`
   THEN the system SHALL raise `ConnectionError` without invoking the file
   operation.

### Requirement 9 — DO_UNTIL_SUCCESS Action Pattern

**User Story:** As a test author, I want flaky BMC uploads to be retried
until success within a bounded time, so that I do not have to hand-roll a
retry loop.

#### Acceptance Criteria

1. WHEN `aspects.action_pattern == ActionPattern.DO_UNTIL_SUCCESS` THEN the
   system SHALL retry the backend every `aspects.completion_interval` until
   the backend succeeds or `aspects.completion_timeout` elapses.
2. WHEN a retry attempt raises an exception listed in
   `aspects.allowed_connection_exceptions` THEN the system SHALL sleep the
   interval and retry.
3. WHEN a retry attempt raises a non-allowed exception THEN the system SHALL
   re-raise immediately without further retries.
4. IF the timeout elapses without success THEN the system SHALL raise
   `TimeoutError` with `details` listing the last failure.

### Requirement 10 — Default Aspects Passthrough

**User Story:** As a test author, I want to omit the `aspects` parameter
entirely and fall back to the instance default, so that common call sites
stay readable.

#### Acceptance Criteria

1. WHEN any method is invoked with `aspects=None` THEN the system SHALL
   substitute the `EspFileProvider` instance default via `resolve_aspects`.
2. IF the `EspFileProvider` instance has no default aspects THEN the system
   SHALL raise `ConfigurationError`.

### Requirement 11 — Existence Checks Are Non-Destructive

**User Story:** As a test author, I want `file_exists` and
`directory_exists` to never mutate remote state, so that I can use them
freely in assertions.

#### Acceptance Criteria

1. WHEN `file_exists` or `directory_exists` is invoked THEN the system SHALL
   NOT write, create, or modify any filesystem object.
2. WHEN the queried path is a directory on a filesystem that distinguishes
   regular files from directories THEN `file_exists` SHALL return `False`
   and `directory_exists` SHALL return `True`.
3. WHEN the queried path does not exist THEN both predicates SHALL return
   `False` without raising.
4. IF the transport itself fails (network error, disconnected shell) THEN
   the predicate SHALL raise the transport error — existence ambiguity must
   not be silently swallowed.

### Requirement 12 — File-Level Security Honored

**User Story:** As a firmware engineer, I want ESP file operations to
respect UEFI file-level security per §13.5, so that operations that the
firmware would refuse are surfaced clearly rather than masked.

#### Acceptance Criteria

1. WHEN the Shell backend receives an "Access Denied" response from the
   firmware THEN the system SHALL raise `UefiError` with `details` including
   the firmware error text and the attempted path.
2. WHEN the SSH-to-host backend receives `EACCES` from a POSIX write THEN
   the system SHALL raise `UefiError` with `details` listing the path and
   the `errno` value.
3. WHEN the Redfish backend receives a `403 Forbidden` response THEN the
   system SHALL raise `UefiError` with `details` containing the Redfish
   `Messages[]` array.
