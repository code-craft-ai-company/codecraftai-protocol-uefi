# Requirements — UefiSession

## Introduction

`UefiSession` is the persistent-channel class for UEFI interop workloads
that need to issue many commands against a shared transport context
without paying the connection-establishment cost per call.  The
canonical use case is a long-running UEFI Shell session in which the
caller has mapped ESP volumes, set aliases, and changed into a working
directory — all of which must persist across commands.

`UefiSession` is the direct analog of `SshSession`
(`/Users/myron/repos/codecraftai-protocol-ssh/source/packages/ccai/protocols/ssh/sshsession.py`)
and inherits from `UefiBase` exactly as `SshSession` inherits from
`SshBase`.  A session maintains exactly one open channel between
`__enter__` and `__exit__`.  Session-flavored reconnection on transient
failure is driven by the `DO_UNTIL_CONNECTION_FAILURE` action pattern;
the other patterns raise `SemanticError` — mirroring
`sshbase.py:625–627` with the supported-pattern inverted for sessions.

Sessions also preserve environment-like state — the current ESP map
alias and the current working directory inside the shell — and replay
the state on reconnect via a `_replay_actions` list.  The `basis_session`
parameter clones connection parameters of an existing session.

## Requirements

### Requirement 1 — Single Open Channel Between `__enter__` And `__exit__`

**User Story:** As a test author, I want the session to hold exactly
one open channel for the duration of the `with` block, so that I get
persistent shell state without thinking about lifecycle.

#### Acceptance Criteria

1. WHEN `__enter__` is called THEN the system SHALL open exactly one
   `UefiChannel` and store it as `self._channel`.
2. WHEN `__exit__` is called (normally or by exception) THEN the
   system SHALL close the single channel and then close the underlying
   client/connection unless `basis_session` owns the connection.
3. WHILE the session is inside the `with` block AND the channel is
   open THEN `self.connected` SHALL return `True`.
4. IF the caller tries to call `run_cmd` outside of a `with` block THEN
   the system SHALL raise `SemanticError` naming the required
   context-manager usage.

### Requirement 2 — Session-Only Action Pattern Support

**User Story:** As a test author, I want `UefiSession.run_cmd` to
support only the two "connected" patterns, so that misuse of session
semantics is loud.

#### Acceptance Criteria

1. WHEN `aspects.action_pattern == SINGLE_CONNECTED_CALL` THEN the
   system SHALL run the command on the persistent channel once and
   return.
2. WHEN `aspects.action_pattern == DO_UNTIL_CONNECTION_FAILURE` THEN
   the system SHALL loop the command on the persistent channel until
   the connection raises an allowed-connection exception, then return
   cleanly.
3. IF `aspects.action_pattern == SINGLE_CALL`, `DO_UNTIL_SUCCESS`, or
   `DO_WHILE_SUCCESS` THEN the system SHALL raise `SemanticError` —
   inverted version of `sshbase.py:625–627`.
4. WHEN `DO_UNTIL_CONNECTION_FAILURE` exits due to the allowed-
   connection exception THEN the system SHALL transition the session
   to a "needs-reconnect" state so that the next call triggers
   automatic reconnect.

### Requirement 3 — Automatic Reconnect On Transient Failure

**User Story:** As a test author driving a long session across
transient network blips, I want the session to reconnect automatically
under `DO_UNTIL_CONNECTION_FAILURE`, so that my test does not abort on
a single dropout.

#### Acceptance Criteria

1. WHEN the persistent channel raises an exception listed in
   `aspects.allowed_connection_exceptions` THEN the system SHALL
   close the channel, call `UefiConnection.reconnect()`, open a new
   channel, and replay `_replay_actions`.
2. WHEN reconnect succeeds THEN the system SHALL resume the current
   `DO_UNTIL_CONNECTION_FAILURE` loop iteration.
3. IF reconnect exhausts `aspects.connection_timeout` without success
   THEN the system SHALL raise `ConnectionError` with `details`
   including reconnect_count and the chained last exception.
4. WHEN reconnect succeeds THEN the system SHALL increment
   `session.reconnect_count` (exposed as a property).

### Requirement 4 — `_replay_actions` State Replay

**User Story:** As a test author, I want environment-like state (ESP
map alias, current working directory) to survive reconnects, so that
my next command sees the same environment as if no dropout had
occurred.

#### Acceptance Criteria

1. WHEN the session records a state-changing command via
   `record_replay(action)` THEN the system SHALL append `action` to
   `self._replay_actions`.
2. WHEN reconnect succeeds THEN the system SHALL iterate
   `self._replay_actions` in insertion order and invoke each callable
   with `self` as the sole argument.
3. WHEN a replay action raises an exception THEN the system SHALL
   wrap it in `UefiError` with `details` naming the failed action
   index and chain the original via `__cause__`.
4. WHEN the caller clears replay state via `clear_replay()` THEN the
   system SHALL empty `self._replay_actions`.
5. WHERE `basis_session` is supplied THEN the system SHALL copy
   `basis_session._replay_actions` into the new session's list at
   construction time.

### Requirement 5 — `basis_session` Parameter

**User Story:** As a test author juggling multiple session flavors
against the same target, I want a `basis_session` parameter that
clones the connection parameters of an existing session, so that I
can open a parallel session without duplicating configuration.

#### Acceptance Criteria

1. WHEN `UefiSession(..., basis_session=existing)` is constructed
   THEN the system SHALL copy `existing`'s host, transport kind,
   jump, users, called_id, and the client handle.
2. WHEN the new session enters its `with` block THEN it SHALL reuse
   the basis session's connection rather than opening a new one.
3. WHEN the new session exits THEN the system SHALL NOT close the
   basis session's connection — the basis session retains ownership
   (mirrors `sshsession.py:396–411`).
4. IF the basis session's underlying connection has been closed THEN
   the system SHALL raise `RuntimeError` with a message that the
   basis session is no longer active (mirrors `sshsession.py:400–405`).

### Requirement 6 — Environment Preservation Helpers

**User Story:** As a test author interacting with the UEFI Shell, I
want helpers for `map <alias> <path>` and `cd <path>` that update
session state AND record replay actions, so that I never have to
manage replay myself.

#### Acceptance Criteria

1. WHEN `session.map_alias(alias, path)` is called THEN the system
   SHALL send `map <alias> <path>` to the shell, verify status, update
   `session.current_map_alias`, and record a replay action that
   re-runs the same `map` command on reconnect.
2. WHEN `session.chdir(path)` is called THEN the system SHALL send
   `cd <path>` to the shell, verify status, update `session.cwd`, and
   record a replay action.
3. IF the shell returns a non-zero status THEN the system SHALL raise
   `UefiShellError` with `command`, `status`, `stdout`, `stderr`.
4. WHEN the caller reads `session.current_map_alias` or `session.cwd`
   THEN the system SHALL return the last successfully set value (may
   be `None` if never set).

### Requirement 7 — `reboot()` Closes The Session

**User Story:** As a test author, I want `session.reboot()` to issue
a reset and then close the session (rather than reconnect), so that
the caller can decide whether to open a fresh session after the
reboot.

#### Acceptance Criteria

1. WHEN `reboot(aspects=None)` is called on a session THEN the system
   SHALL issue the transport-native reset command, swallow expected
   connection errors at DEBUG, and then raise
   `UnExpectedSessionClosureError` mirroring
   `sshsession.py:328–354`.
2. WHEN `UnExpectedSessionClosureError` is raised THEN the session
   SHALL set `self._channel = None` and `self._client = None` before
   the exception propagates.

### Requirement 8 — `close()` Idempotent And Safe

**User Story:** As a test author, I want `session.close()` to be safe
to call multiple times and to never raise, so that cleanup paths are
reliable.

#### Acceptance Criteria

1. WHEN `close()` is called for the first time on an open session THEN
   the system SHALL close the channel, close the client (unless
   `basis_session` owns it), and set `self._channel = None`,
   `self._client = None`.
2. WHEN `close()` is called a second or subsequent time THEN the
   system SHALL return without error.
3. IF any underlying close raises THEN the system SHALL log the
   exception at WARNING under `uefi.transport` and suppress it —
   matches `sshsession.py:188–212`.

### Requirement 9 — Aspects None-Passthrough

**User Story:** As a test author, I want `aspects=None` on any session
method to fall back to the instance default, so that call sites stay
concise.

#### Acceptance Criteria

1. WHEN any public method is called with `aspects=None` THEN the
   system SHALL resolve via `resolve_aspects(None, self._aspects)`.
2. WHEN `aspects` is non-None THEN the system SHALL use the passed
   aspects for the call only.

### Requirement 10 — `reconnect_count` Exposure

**User Story:** As a test author, I want a reliable counter of
session-level reconnect events, so that my diagnostics can assert how
many transient blips occurred.

#### Acceptance Criteria

1. WHEN the session is first constructed THEN
   `session.reconnect_count` SHALL be `0`.
2. WHEN an automatic reconnect completes successfully THEN the
   system SHALL increment `reconnect_count` by `1`.
3. WHEN `reconnect_count` is read THEN the system SHALL return an
   int and SHALL reject any assignment with `AttributeError`.

### Requirement 11 — Structured Logging Of Session Transitions

**User Story:** As an operator, I want session enter/exit, reconnect,
and replay events to emit structured log records, so that I can
reconstruct session timelines from log tails.

#### Acceptance Criteria

1. WHEN `__enter__` completes THEN the system SHALL emit an INFO log
   on `uefi.base` with `host`, `transport_kind`, `called_id`.
2. WHEN `__exit__` completes THEN the system SHALL emit an INFO log
   on `uefi.base` with `host`, `elapsed_ms`.
3. WHEN an automatic reconnect begins THEN the system SHALL emit a
   WARNING log on `uefi.transport` with `host`, `trigger_exception`.
4. WHEN an automatic reconnect completes THEN the system SHALL emit
   an INFO log with `host`, `reconnect_count`,
   `replay_actions_count`, `elapsed_ms`.
5. WHEN a replay action fails THEN the system SHALL emit an ERROR
   log with `action_index`, `action_repr`, `exception_repr`.

### Requirement 12 — Credential And Transport Inherited From Base

**User Story:** As a test author, I want the session to use the same
credential/transport validation as the agent, so that misconfiguration
is caught at construction time.

#### Acceptance Criteria

1. WHEN `UefiSession.__init__` is called THEN the system SHALL invoke
   `UefiBase.__init__` with the same arguments, which performs
   credential/transport validation.
2. IF the credential/transport combination is invalid THEN the system
   SHALL raise `ConfigurationError`, bubbling up from
   `UefiBase._validate_credential`.
