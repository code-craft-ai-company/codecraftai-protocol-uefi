# Requirements — UefiAgent

## Introduction

`UefiAgent` is the one-shot convenience class for interacting with a UEFI
target.  Every public method opens a fresh channel (via a freshly-built
`UefiClient`), performs the operation, and closes the channel before
returning.  It is the direct analog of `SshAgent`
(`/Users/myron/repos/automojo-protocol-ssh/source/packages/automojo/protocols/ssh/sshagent.py`)
and inherits from `UefiBase` exactly as `SshAgent` inherits from
`SshBase`.

`UefiAgent` is the default entry point for short-lived, measured
operations.  For workloads that need to hold a shell channel open across
many commands, callers use `UefiAgent.open_session(...)` to get a
`UefiSession`.  `UefiAgent.reboot()` implements the same reconnect-
verify pattern as `SshAgent.reboot()`
(`sshagent.py:132–184`) — issue a reset, wait for the port to come
back, verify shell, raise `TimeoutError` if the target does not return.

## Requirements

### Requirement 1 — Constructor Surface

**User Story:** As a test author, I want to construct a `UefiAgent` with
host, credential, transport kind, optional jump, optional called_id, and
aspects, so that I have a one-shot agent ready to use.

#### Acceptance Criteria

1. WHEN `UefiAgent(host, primary_credential, transport_kind)` is called
   with only the required positional args THEN the system SHALL
   construct successfully and store `aspects = DEFAULT_CMD_ASPECTS`.
2. WHEN `UefiAgent(..., jump=UefiJumpParams(...))` is called THEN the
   system SHALL forward the jump to `UefiBase.__init__`.
3. WHEN `UefiAgent(..., called_id="node-42")` is called THEN the
   system SHALL forward `called_id` to `UefiBase.__init__` so that
   `_target_tag` becomes `"[node-42]"`.
4. WHEN `UefiAgent(..., aspects=custom_aspects)` is called THEN the
   system SHALL use `custom_aspects` as the instance default.
5. IF `transport_kind` is not one of the registered kinds (`"serial"`,
   `"redfish"`, `"ssh-host"`, `"ipmi-sol"`) THEN the system SHALL raise
   `ConfigurationError`.

### Requirement 2 — Fresh Channel Per Call

**User Story:** As a test author running one-shot operations, I want
each public method call to open and close its own channel, so that
state never leaks between calls.

#### Acceptance Criteria

1. WHEN any public method (`run_cmd`, `run_redfish`, `run_variable`,
   `file_push`, `file_pull`, `file_exists`, etc.) is called THEN the
   system SHALL build a `UefiClient` via `_create_client()` and
   close it before returning.
2. WHEN two successive calls are made THEN the system SHALL NOT reuse
   any channel, `UefiClient` instance, or `UefiConnection` between
   them unless the caller explicitly passes `client=...`.
3. WHEN the caller passes `client=existing_client` to a public method
   THEN the system SHALL use the provided client and SHALL NOT close
   it on return — ownership remains with the caller (mirrors
   `sshagent.py:186–218`).

### Requirement 3 — Aspects Override On Every Public Method

**User Story:** As a test author, I want every public method to accept
an optional `aspects` parameter, so that per-call overrides do not
require rebuilding the agent.

#### Acceptance Criteria

1. WHEN a public method is called with `aspects=None` THEN the system
   SHALL use `self._aspects` for that call.
2. WHEN a public method is called with non-None `aspects` THEN the
   system SHALL use the passed aspects for that call only.
3. WHEN a public method returns THEN the instance default aspects
   SHALL be unchanged.

### Requirement 4 — `run_cmd` Supported Patterns

**User Story:** As a test author, I want `UefiAgent.run_cmd` to support
the same three patterns as `SshBase._run_cmd` does — no session-only
patterns — so that one-shot usage cannot misuse long-running-session
behaviors.

#### Acceptance Criteria

1. WHEN `aspects.action_pattern == SINGLE_CALL` THEN the system SHALL
   run once and return.
2. WHEN `aspects.action_pattern == DO_UNTIL_SUCCESS` THEN the system
   SHALL loop until match or timeout.
3. WHEN `aspects.action_pattern == DO_WHILE_SUCCESS` THEN the system
   SHALL loop while match or until timeout.
4. IF `aspects.action_pattern == SINGLE_CONNECTED_CALL` or
   `DO_UNTIL_CONNECTION_FAILURE` THEN the system SHALL raise
   `SemanticError` per `sshbase.py:625–627`.

### Requirement 5 — `reboot()` Reconnect-Verify Pattern

**User Story:** As a test author, I want `UefiAgent.reboot()` to issue
a reset, wait for the target's port to return, and verify shell, so
that I get a single-call primitive that matches `SshAgent.reboot()`.

#### Acceptance Criteria

1. WHEN `reboot(aspects=None)` is called THEN the system SHALL issue
   the transport-native reset command (UEFI Shell `reset -c`,
   `/redfish/v1/Systems/{id}/Actions/ComputerSystem.Reset`, or
   OS-level `reboot` via SSH-host).
2. WHEN the reset command raises a connection-related exception THEN
   the system SHALL log at DEBUG and continue (mirrors
   `sshagent.py:143–145`).
3. WHEN the reset has been issued THEN the system SHALL wait up to
   `aspects.completion_timeout` seconds (default 300) polling
   `verify_connectivity()` every `aspects.completion_interval` seconds
   (default 10).
4. WHEN `verify_connectivity()` returns `True` THEN the system SHALL
   return successfully.
5. IF `verify_connectivity()` never returns `True` within the deadline
   THEN the system SHALL raise `TimeoutError` with `details` including
   `host`, `transport_kind`, `completion_timeout`, and the last
   exception observed.
6. WHEN `reboot` is called THEN the system SHALL wait a grace period
   (default 30s, configurable via
   `aspects.reboot_grace_period`) before polling, mirroring
   `sshagent.py:168–169`.

### Requirement 6 — `open_session()` Parallels SshAgent.open_session

**User Story:** As a test author, I want `UefiAgent.open_session()` to
produce a `UefiSession` that shares my aspects and credentials, so that
I can switch between one-shot and session modes seamlessly.

#### Acceptance Criteria

1. WHEN `open_session()` is called with no arguments THEN the system
   SHALL return a new `UefiSession` with the agent's host, credential,
   transport kind, jump, called_id, and aspects.
2. WHEN `open_session(user="ops")` is called AND `"ops"` is in
   `self._users` THEN the session SHALL be built with
   `self._users["ops"]` as its primary credential.
3. IF `user` is specified but not in `self._users` THEN the system
   SHALL raise `ConfigurationError`.
4. WHEN `open_session(basis_session=existing)` is called THEN the new
   session SHALL clone connection parameters from `existing` —
   mirroring `sshagent.py:87–96`.
5. WHEN `open_session(aspects=None)` is called THEN the new session
   SHALL inherit `self._aspects`.
6. WHEN `open_session(aspects=custom)` is called THEN the new session
   SHALL use `custom`.

### Requirement 7 — `_create_client()` Respects Aspects Retry

**User Story:** As a test author, I want client construction to
respect `aspects.connection_timeout` / `connection_interval` /
`allowed_connection_exceptions`, so that flaky targets do not
immediately abort one-shot calls.

#### Acceptance Criteria

1. WHEN `_create_client()` is called THEN the system SHALL route
   through a `UefiConnection`'s retry loop under the agent's aspects.
2. WHEN the target is transiently unreachable AND the exception type
   is in `aspects.allowed_connection_exceptions` THEN the system SHALL
   retry every `connection_interval` up to `connection_timeout`.
3. IF the connection cannot be established before `connection_timeout`
   elapses THEN the system SHALL raise `ConnectionError` with a
   multiline `details` block including host, transport kind,
   begin/end timestamps, and the chained last exception.
4. WHERE `aspects.must_connect == True` THEN the system SHALL re-raise
   on the first failure without retry.

### Requirement 8 — Directory And File Primitives (SSH-Host Transport Only)

**User Story:** As a test author running against an SSH-to-host
transport, I want the agent to expose `file_push`, `file_pull`,
`file_exists`, `directory_exists`, `directory`, and `directory_tree`,
so that my code parallels `SshAgent`.

#### Acceptance Criteria

1. WHEN `file_push(localpath, remotepath)` is called on an SSH-host
   transport THEN the system SHALL forward to the composed
   `SshAgent.file_push`.
2. WHEN any file / directory method is called on a non-SSH-host
   transport THEN the system SHALL raise `SemanticError` with a
   message directing the caller to layer 5 `5-esp-file-ops`.
3. WHERE `ssh_client` is passed THEN the system SHALL reuse it and
   SHALL NOT close it on return.

### Requirement 9 — Aspects None-Passthrough

**User Story:** As a test author, I want `aspects=None` on any method
to fall back to the instance default, so that call sites stay concise.

#### Acceptance Criteria

1. WHEN any public method is called with `aspects=None` THEN the system
   SHALL resolve via `resolve_aspects(None, self._aspects)`.
2. WHERE both are `None` THEN the system SHALL raise `ConfigurationError`
   per `resolve_aspects` contract.

### Requirement 10 — Structured Logging On Agent-Level Events

**User Story:** As an operator, I want agent construction, session
opening, and reboot transitions to emit structured log events on
`uefi.base`, so that I can trace agent-level operations.

#### Acceptance Criteria

1. WHEN `__init__` completes THEN the system SHALL log at DEBUG with
   `host`, `transport_kind`, `called_id`, `aspects_summary`.
2. WHEN `open_session` returns THEN the system SHALL log at INFO with
   `host`, `session_user`, `basis_session=bool`.
3. WHEN `reboot` begins THEN the system SHALL log at INFO with
   `host`, `aspects.completion_timeout`, `reboot_grace_period`.
4. WHEN `reboot` completes successfully THEN the system SHALL log at
   INFO with `elapsed_ms`.
5. WHEN `reboot` times out THEN the system SHALL log at ERROR with
   `elapsed_ms` and the last observed exception.

### Requirement 11 — Context Manager Protocol

**User Story:** As a test author, I want to use `UefiAgent` in a
`with` block, so that any resource created during a call is cleaned up
on exit.

#### Acceptance Criteria

1. WHEN `with UefiAgent(...) as agent:` is entered THEN the system
   SHALL yield the instance unchanged (no eager connect — agents are
   lazy by design).
2. WHEN the `with` block exits THEN the system SHALL close any cached
   connection state (there is normally none, since each method is
   self-contained).
3. WHEN the `with` block raises THEN the system SHALL NOT suppress the
   exception.

### Requirement 12 — Credential Role Lookup (Multi-User)

**User Story:** As a test author targeting a BMC with multiple
credentials, I want to keep a dict of role → credential and select at
call time, so that my specs do not need to repeat credentials.

#### Acceptance Criteria

1. WHEN `run_cmd(..., user="ops")` is called AND `"ops"` is in
   `self._users` THEN the system SHALL use the `ops` credential for
   client construction for that call only.
2. IF `user` is specified but `self._users` is `None` or empty THEN
   the system SHALL raise `ConfigurationError` with guidance to pass a
   `users` dict at construction time (mirrors `sshbase.py:521–527`).
3. IF `user` is specified but not in `self._users` THEN the system
   SHALL raise `ConfigurationError` with `details` naming the
   requested user and the available roles.
