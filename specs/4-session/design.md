# Design — UefiSession

## Overview

`UefiSession` is the persistent-channel class in the UEFI interop library.
It holds a single open `UefiChannel` between `__enter__` and `__exit__`,
and uses the `DO_UNTIL_CONNECTION_FAILURE` action pattern to drive
automatic reconnection across transient failures.  The session preserves
environment-like state — current ESP map alias, current working
directory inside the UEFI Shell — via a `_replay_actions: list[Callable]`
list that is replayed after every reconnect.

The class is a UEFI-flavored port of `SshSession`
(`/Users/myron/repos/automojo-protocol-ssh/source/packages/automojo/protocols/ssh/sshsession.py`).
It inherits from `UefiBase` (so the action-pattern engine is already
implemented) and adds three things:

1. **Channel persistence.**  A single `UefiChannel` is held for the
   duration of the `with` block; `__exit__` closes it.
2. **Replay on reconnect.**  The `_replay_actions` list is invoked in
   order after every successful reconnect.
3. **`basis_session` cloning.**  The session can reuse the connection
   of an existing session (mirrors `sshsession.py:396–411`).

The supported action-pattern set is the inverse of `UefiAgent`'s —
`SINGLE_CONNECTED_CALL` and `DO_UNTIL_CONNECTION_FAILURE` only; the
other three raise `SemanticError`.  This matches `sshbase.py:625–627`
with the sides swapped: the SSH library currently implements only the
three non-connected patterns, whereas UEFI sessions are specifically for
long-running connected work.

## Architecture

```
automojo.protocols.uefi.session/
    __init__.py     # re-exports UefiSession
    uefisession.py  # the class
```

Dependency direction:

```
uefisession.py ──▶ automojo.protocols.uefi.base       # UefiBase
               ──▶ automojo.protocols.uefi.client     # UefiClient subclasses
               ──▶ automojo.protocols.uefi.connection # UefiConnection
               ──▶ automojo.protocols.uefi.channel    # UefiChannel subclasses
               ──▶ automojo.protocols.uefi.aspects
               ──▶ automojo.protocols.uefi.credentials
               ──▶ automojo.protocols.uefi.exceptions
               ──▶ automojo.protocols.uefi.logging
```

## Components and Interfaces

### `UefiSession` (uefisession.py)

```python
class UefiSession(UefiBase):
    """
        Persistent-channel UEFI session.  Holds one ``UefiChannel`` for
        the lifetime of a ``with`` block; reconnects automatically under
        ``DO_UNTIL_CONNECTION_FAILURE``; replays environment state on
        reconnect.  Mirrors ``SshSession``.
    """

    def __init__(
        self,
        host: str,
        primary_credential: UefiCredential,
        transport_kind: str,
        jump: Optional[UefiJumpParams] = None,
        users: Optional[Dict[str, UefiCredential]] = None,
        called_id: Optional[str] = None,
        basis_session: Optional["UefiSession"] = None,
        aspects: UefiCmdAspects = DEFAULT_CMD_ASPECTS,
        client_factory: Optional[Callable[..., UefiClient]] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        UefiBase.__init__(
            self,
            host=host,
            primary_credential=primary_credential,
            transport_kind=transport_kind,
            jump=jump,
            users=users,
            called_id=called_id,
            aspects=aspects,
            client_factory=client_factory,
            logger=logger,
        )
        self._basis_session = basis_session
        self._client: Optional[UefiClient] = None
        self._channel: Optional[UefiChannel] = None
        self._reconnect_count: int = 0
        self._current_map_alias: Optional[str] = None
        self._cwd: Optional[str] = None
        if basis_session is not None:
            self._replay_actions: list[Callable[["UefiSession"], None]] = list(basis_session._replay_actions)
        else:
            self._replay_actions = []
        self._enter_time: Optional[float] = None
        return

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------
    @property
    def connected(self) -> bool:
        return self._channel is not None and not self._channel.closed

    @property
    def reconnect_count(self) -> int:
        return self._reconnect_count

    @property
    def current_map_alias(self) -> Optional[str]:
        return self._current_map_alias

    @property
    def cwd(self) -> Optional[str]:
        return self._cwd

    @property
    def basis_session(self) -> Optional["UefiSession"]:
        return self._basis_session

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------
    def __enter__(self) -> "UefiSession":
        self._enter_time = time.time()
        self._open_client_and_channel()
        self._logger.info(
            "uefi.base: session enter host=%s transport_kind=%s called_id=%s",
            self._host, self._transport_kind, self._called_id,
        )
        return self

    def __exit__(self, ex_type, ex_val, ex_tb) -> bool:
        self.close()
        elapsed_ms = int((time.time() - (self._enter_time or time.time())) * 1000)
        self._logger.info(
            "uefi.base: session exit host=%s elapsed_ms=%d",
            self._host, elapsed_ms,
        )
        return False

    # ------------------------------------------------------------------
    # run_cmd — session-only patterns
    # ------------------------------------------------------------------
    def run_cmd(
        self,
        command: str,
        exp_status: Union[int, Sequence[int]] = 0,
        aspects: Optional[UefiCmdAspects] = None,
    ) -> Tuple[int, str, str]:
        if self._channel is None:
            err_msg = (
                "UefiSession.run_cmd requires the session to be entered via `with` block "
                "or by explicit call to __enter__."
            )
            raise SemanticError(err_msg) from None

        resolved = resolve_aspects(aspects, self._aspects)
        pattern = resolved.action_pattern

        if pattern in (ActionPattern.SINGLE_CALL, ActionPattern.DO_UNTIL_SUCCESS, ActionPattern.DO_WHILE_SUCCESS):
            err_msg_lines = [
                "UefiSession does not support SINGLE_CALL / DO_UNTIL_SUCCESS / DO_WHILE_SUCCESS.",
                "Use UefiAgent for one-shot patterns, or change action_pattern to SINGLE_CONNECTED_CALL "
                "or DO_UNTIL_CONNECTION_FAILURE.",
                f"PASSED: {pattern.name}",
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise SemanticError(err_msg) from None

        if pattern == ActionPattern.SINGLE_CONNECTED_CALL:
            try:
                status, stdout, stderr = self._run_cmd(
                    runner=self._channel,
                    command=command,
                    exp_status=exp_status,
                    aspects=resolved,
                )
            except BaseException as cerr:
                self._maybe_auto_reconnect(cerr=cerr, resolved=resolved)
                raise
            return (status, stdout, stderr)

        if pattern == ActionPattern.DO_UNTIL_CONNECTION_FAILURE:
            while True:
                try:
                    status, stdout, stderr = self._run_cmd(
                        runner=self._channel,
                        command=command,
                        exp_status=exp_status,
                        aspects=resolved,
                    )
                except BaseException as cerr:
                    allowed = tuple(resolved.allowed_connection_exceptions)
                    if len(allowed) > 0 and isinstance(cerr, allowed):
                        self._auto_reconnect(trigger=cerr, resolved=resolved)
                        continue
                    raise
                if not self._status_matches(status, exp_status):
                    return (status, stdout, stderr)

        err_msg = f"UefiSession: unknown ActionPattern. pattern={pattern}"
        raise SemanticError(err_msg) from None

    # ------------------------------------------------------------------
    # Replay registration
    # ------------------------------------------------------------------
    def record_replay(self, action: Callable[["UefiSession"], None]) -> None:
        self._replay_actions.append(action)
        return

    def clear_replay(self) -> None:
        self._replay_actions.clear()
        return

    # ------------------------------------------------------------------
    # Environment helpers
    # ------------------------------------------------------------------
    def map_alias(self, alias: str, path: str, aspects: Optional[UefiCmdAspects] = None) -> None:
        cmd = f"map {alias} {path}"
        status, stdout, stderr = self.run_cmd(command=cmd, aspects=aspects)
        if status != 0:
            raise UefiShellError(command=cmd, status=status, stdout=stdout, stderr=stderr)
        self._current_map_alias = alias

        def replay(sess: "UefiSession") -> None:
            sess.run_cmd(command=cmd)
            sess._current_map_alias = alias
            return

        self.record_replay(action=replay)
        return

    def chdir(self, path: str, aspects: Optional[UefiCmdAspects] = None) -> None:
        cmd = f"cd {path}"
        status, stdout, stderr = self.run_cmd(command=cmd, aspects=aspects)
        if status != 0:
            raise UefiShellError(command=cmd, status=status, stdout=stdout, stderr=stderr)
        self._cwd = path

        def replay(sess: "UefiSession") -> None:
            sess.run_cmd(command=cmd)
            sess._cwd = path
            return

        self.record_replay(action=replay)
        return

    # ------------------------------------------------------------------
    # reboot — closes the session
    # ------------------------------------------------------------------
    def reboot(self, aspects: Optional[UefiCmdAspects] = None) -> None:
        resolved = resolve_aspects(aspects, self._aspects)
        reset_command = self._transport_reset_command()
        try:
            self.run_cmd(command=reset_command, aspects=resolved)
        except (ConnectionError, TimeoutError, OSError, EOFError) as cerr:
            self._logger.debug(
                "uefi.base: expected connection error during session reboot: %r", cerr,
            )
        except KeyboardInterrupt:
            raise
        except SystemExit:
            raise
        except Exception as cerr:
            self._logger.warning(
                "uefi.base: unexpected exception during session reboot: %r", cerr,
            )
        # Clear channel/client references before raising.
        self._channel = None
        self._client = None
        err_msg = "Session closed due to reboot."
        raise UnExpectedSessionClosureError(err_msg) from None

    # ------------------------------------------------------------------
    # close
    # ------------------------------------------------------------------
    def close(self) -> None:
        if self._channel is not None:
            try:
                self._channel.close()
            except BaseException as cerr:
                self._logger.warning(
                    "uefi.transport: session channel close suppressed host=%s err=%r",
                    self._host, cerr,
                )
            self._channel = None
        if self._client is not None and self._basis_session is None:
            try:
                self._client.close()
            except BaseException as cerr:
                self._logger.warning(
                    "uefi.transport: session client close suppressed host=%s err=%r",
                    self._host, cerr,
                )
            self._client = None
        return

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------
    def _open_client_and_channel(self) -> None:
        if self._basis_session is not None:
            basis = self._basis_session
            if basis._client is None:
                err_msg = "Basis session client is None; basis session is not active."
                raise RuntimeError(err_msg) from None
            self._client = basis._client
        else:
            self._client = self._create_client_like_agent(aspects=self._aspects)
        self._channel = self._client.open_channel()
        return

    def _auto_reconnect(self, trigger: BaseException, resolved: UefiCmdAspects) -> None:
        begin = time.time()
        self._logger.warning(
            "uefi.transport: session auto-reconnect host=%s trigger=%r",
            self._host, trigger,
        )
        if self._channel is not None:
            try:
                self._channel.close()
            except BaseException:
                pass
            self._channel = None
        if self._client is not None and self._basis_session is None:
            try:
                self._client.close()
            except BaseException:
                pass
            self._client = None

        self._client = self._create_client_like_agent(aspects=resolved)
        self._channel = self._client.open_channel()
        self._reconnect_count += 1
        self._replay()
        elapsed_ms = int((time.time() - begin) * 1000)
        self._logger.info(
            "uefi.base: session reconnect complete host=%s reconnect_count=%d "
            "replay_actions_count=%d elapsed_ms=%d",
            self._host, self._reconnect_count, len(self._replay_actions), elapsed_ms,
        )
        return

    def _replay(self) -> None:
        for idx, action in enumerate(self._replay_actions):
            try:
                action(self)
            except BaseException as cerr:
                err_msg_lines = [
                    "UefiSession replay action failed.",
                    f"ACTION_INDEX: {idx}",
                    f"ACTION: {action!r}",
                    f"CAUSE: {cerr!r}",
                ]
                err_msg = os.linesep.join(err_msg_lines)
                self._logger.error(
                    "uefi.base: session replay failed action_index=%d action=%r err=%r",
                    idx, action, cerr,
                )
                raise UefiError(err_msg) from cerr
        return

    def _maybe_auto_reconnect(self, cerr: BaseException, resolved: UefiCmdAspects) -> None:
        """
            Called from SINGLE_CONNECTED_CALL path — the session does NOT
            auto-reconnect in that pattern, but it DOES mark itself as
            needing reconnect if the error type is an allowed connection
            exception, so the next call can reconnect.
        """
        allowed = tuple(resolved.allowed_connection_exceptions)
        if len(allowed) > 0 and isinstance(cerr, allowed):
            if self._channel is not None:
                try:
                    self._channel.close()
                except BaseException:
                    pass
            self._channel = None
        return

    def _create_client_like_agent(self, aspects: UefiCmdAspects) -> UefiClient:
        # Re-uses the same construction path as UefiAgent._create_client.
        ...
```

## Data Models

Session-specific instance state (all initialized in `__init__`):

* `_basis_session: Optional[UefiSession]`
* `_client: Optional[UefiClient]`
* `_channel: Optional[UefiChannel]`
* `_reconnect_count: int`
* `_current_map_alias: Optional[str]`
* `_cwd: Optional[str]`
* `_replay_actions: list[Callable[[UefiSession], None]]`
* `_enter_time: Optional[float]`

Per `/Users/myron/repos/CLAUDE.md` class-variable rule, no new
attributes are introduced outside `__init__`.

## Measured Interop Contract

`UefiSession` consumes the same `UefiCmdAspects` fields as `UefiBase`,
with the following session-specific dependencies:

| Field                                | Where consumed                                                  |
|--------------------------------------|-----------------------------------------------------------------|
| `action_pattern`                     | `run_cmd` dispatch; only `SINGLE_CONNECTED_CALL` and `DO_UNTIL_CONNECTION_FAILURE` are accepted |
| `allowed_connection_exceptions`      | automatic reconnect trigger in `DO_UNTIL_CONNECTION_FAILURE`    |
| `connection_timeout`, `connection_interval` | `_auto_reconnect` retry bound via `UefiConnection`         |
| `must_connect`                       | `_auto_reconnect` re-raise path                                 |
| `completion_timeout`, `completion_interval` | inherited `_run_cmd` behavior                            |
| `inactivity_timeout`, `inactivity_interval` | channel-level `recv_until`                               |
| `monitor_delay`                      | `MonitoredScope` wrapping inside `_run_cmd`                     |
| `logging_pattern`, `expected_status` | `_log_command_result`                                           |
| `prompt_pattern`, `response_encoding`| `_dispatch_inner` when runner is `UefiChannel`                  |

`ActionPattern`s supported by `UefiSession.run_cmd`:

| Pattern                         | Supported                                |
|---------------------------------|------------------------------------------|
| `SINGLE_CALL`                   | SemanticError (agent-only)               |
| `DO_UNTIL_SUCCESS`              | SemanticError (agent-only)               |
| `DO_WHILE_SUCCESS`              | SemanticError (agent-only)               |
| `SINGLE_CONNECTED_CALL`         | yes                                      |
| `DO_UNTIL_CONNECTION_FAILURE`   | yes                                      |

This is the inverse of the `UefiAgent` matrix.

## Error Handling

| Condition                                              | Exception                          | Included in `details`              |
|--------------------------------------------------------|------------------------------------|------------------------------------|
| `run_cmd` called outside `with`                        | `SemanticError`                    | guidance text                      |
| Non-session action pattern passed                      | `SemanticError`                    | pattern name                       |
| Basis session's client no longer active                | `RuntimeError`                     | basis session repr                 |
| Auto-reconnect exhausts timeout                        | `ConnectionError`                  | reconnect_count, last exception    |
| Replay action raises                                   | `UefiError`                        | action_index, action repr          |
| `reboot()` called on session                           | `UnExpectedSessionClosureError`    | mirrors `sshsession.py:328–354`    |
| Shell command non-zero status in `map_alias`/`chdir`   | `UefiShellError`                   | command, status, stdout, stderr    |
| Channel/client close raises                            | suppressed + WARNING               | logged                             |

## Logging

All output routes through `get_uefi_logger("uefi.base")` /
`get_uefi_logger("uefi.transport")`:

| Event                       | Level   | Fields                                               |
|-----------------------------|---------|------------------------------------------------------|
| session.enter               | INFO    | host, transport_kind, called_id                      |
| session.exit                | INFO    | host, elapsed_ms                                     |
| session.reconnect.begin     | WARNING | host, trigger_exception                              |
| session.reconnect.complete  | INFO    | host, reconnect_count, replay_actions_count, elapsed_ms |
| session.replay.failed       | ERROR   | action_index, action_repr, exception_repr            |
| session.close.suppressed    | WARNING | host, exception_repr                                 |

## Security Considerations

* `UefiSession` inherits credential-redaction discipline from
  `UefiBase` (via its `__repr__`).
* Replay actions are caller-supplied callables; they may close over
  secrets.  The library only stores the callables, never serializes
  them — so there is no persistent leak vector.
* `basis_session` reuse shares a `UefiClient` instance; credentials
  held by that client are shared.  Callers must understand that
  closing the basis session invalidates all derived sessions.

## Testing Strategy

### Unit tests (`test_uefisession_enter_exit.py`)

* `with UefiSession(...) as s:` opens exactly one channel; `s.connected`
  is `True` inside, `False` after.
* Calling `run_cmd` outside the `with` block raises `SemanticError`.
* `__exit__` closes channel + client.
* Exception inside the `with` block still closes cleanly and does not
  swallow the exception.

### Unit tests (`test_uefisession_patterns.py`)

* `SINGLE_CONNECTED_CALL` runs the command on the persistent channel.
* `DO_UNTIL_CONNECTION_FAILURE` loops until an allowed exception exits.
* `SINGLE_CALL` / `DO_UNTIL_SUCCESS` / `DO_WHILE_SUCCESS` raise
  `SemanticError`.

### Unit tests (`test_uefisession_auto_reconnect.py`)

* Allowed exception under `DO_UNTIL_CONNECTION_FAILURE` triggers
  `_auto_reconnect` and increments `reconnect_count`.
* `_replay_actions` replayed in insertion order post-reconnect.
* Replay-action failure raises `UefiError` chained from the inner
  exception.
* Reconnect exhausting `connection_timeout` raises `ConnectionError`.

### Unit tests (`test_uefisession_basis.py`)

* `basis_session` construction shares the client handle.
* Inactive basis session raises `RuntimeError` on `__enter__`.
* Derived session's `_replay_actions` is seeded from the basis
  session.
* Closing the derived session does not close the basis session's
  client.

### Unit tests (`test_uefisession_env_helpers.py`)

* `map_alias` updates `current_map_alias` on success.
* `map_alias` records a replay action that re-runs the map on
  reconnect.
* `chdir` updates `cwd` and records replay.
* Non-zero shell status raises `UefiShellError`.

### Unit tests (`test_uefisession_reboot.py`)

* `reboot()` issues reset, swallows expected connection errors, and
  raises `UnExpectedSessionClosureError`.
* `_channel` and `_client` are cleared before the exception
  propagates.

### Integration tests

* Long-running `DO_UNTIL_CONNECTION_FAILURE` loop over a fake
  transport that drops after N commands; verify reconnect and replay.
* Basis-session parallel-session test with two concurrent sessions
  sharing the client.
* Coverage target: 95% line coverage.

## Dependencies on Other Features

* `1-aspects`, `1-credentials`, `1-exceptions` (`UefiError`,
  `UefiShellError`, `UnExpectedSessionClosureError`),
  `1-interfaces`, `1-logging`.
* `2-transport-*` — via the composed client.
* `3-client`, `3-connection`, `3-channel`.
* `4-base` — parent class providing `_run_cmd`, `_log_command_result`,
  `verify_connectivity`, and `IUefiSystemContext`.
* `4-agent` — creates sessions via `open_session`; may also supply
  `basis_session`.
