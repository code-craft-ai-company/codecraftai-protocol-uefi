# Design — UefiAgent

## Overview

`UefiAgent` is the one-shot, stateless-between-calls convenience class in
the UEFI interop library.  Every public method opens a fresh client
(which in turn opens a fresh channel), performs the operation, and
closes the client before returning.  This keeps one-shot calls free of
cached-state surprises while still being measured-interop-friendly —
retry, timeout, and logging are all controlled through the caller-
supplied `aspects`.

The class mirrors `SshAgent`
(`/Users/myron/repos/codecraftai-protocol-ssh/source/packages/ccai/protocols/ssh/sshagent.py`)
closely and inherits from `UefiBase` exactly as `SshAgent` inherits from
`SshBase`.  The notable UEFI-specific additions are:

* **Multi-transport dispatch.**  `_create_client()` uses the
  `default_client_factory` from `4-base` to build the appropriate
  `UefiClient` subclass for the agent's `transport_kind`.
* **`reboot()` works over any transport.**  The reset command is chosen
  based on `transport_kind`; the subsequent port-return wait and
  `verify_connectivity()` are transport-agnostic.
* **File primitives limited to SSH-host.**  File-push/pull, directory
  walk, etc. are only valid for the SSH-host transport; calling them on
  serial/Redfish/IPMI-SOL raises `SemanticError` with a pointer to
  `5-esp-file-ops`.

## Architecture

```
ccai.protocols.uefi.agent/
    __init__.py    # re-exports UefiAgent
    uefiagent.py   # the class
```

Dependency direction:

```
uefiagent.py ──▶ ccai.protocols.uefi.base        # UefiBase
             ──▶ ccai.protocols.uefi.session     # UefiSession (for open_session)
             ──▶ ccai.protocols.uefi.client      # UefiClient subclasses
             ──▶ ccai.protocols.uefi.connection  # UefiConnection
             ──▶ ccai.protocols.uefi.aspects
             ──▶ ccai.protocols.uefi.credentials
             ──▶ ccai.protocols.uefi.exceptions
             ──▶ ccai.protocols.uefi.logging
```

## Components and Interfaces

### `UefiAgent` (uefiagent.py)

```python
class UefiAgent(UefiBase):
    """
        One-shot UEFI interop agent.  Every public method opens a fresh
        channel, performs the operation, closes the channel.  Mirrors
        ``SshAgent``.
    """

    def __init__(
        self,
        host: str,
        primary_credential: UefiCredential,
        transport_kind: str,
        jump: Optional[UefiJumpParams] = None,
        users: Optional[Dict[str, UefiCredential]] = None,
        called_id: Optional[str] = None,
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
        self._logger.debug(
            "uefi.base: agent constructed host=%s transport_kind=%s called_id=%s",
            host, transport_kind, called_id,
        )
        return

    def __enter__(self) -> "UefiAgent":
        return self

    def __exit__(self, ex_type, ex_val, ex_tb) -> bool:
        return False

    # ------------------------------------------------------------------
    # run_cmd — one-shot command dispatch
    # ------------------------------------------------------------------
    def run_cmd(
        self,
        command: str,
        exp_status: Union[int, Sequence[int]] = 0,
        user: Optional[str] = None,
        aspects: Optional[UefiCmdAspects] = None,
        client: Optional[UefiClient] = None,
    ) -> Tuple[int, str, str]:
        resolved = resolve_aspects(aspects, self._aspects)
        cleanup_client = False
        status: Optional[int] = None
        stdout: Optional[str] = None
        stderr: Optional[str] = None
        try:
            if client is None:
                client = self._create_client(session_user=user, aspects=resolved)
                cleanup_client = True
            status, stdout, stderr = self._run_cmd(
                runner=client,
                command=command,
                exp_status=exp_status,
                user=user,
                aspects=resolved,
            )
        finally:
            if cleanup_client and client is not None:
                client.close()
        return (status, stdout, stderr)

    # ------------------------------------------------------------------
    # open_session — analog of SshAgent.open_session
    # ------------------------------------------------------------------
    def open_session(
        self,
        user: Optional[str] = None,
        basis_session: Optional["UefiSession"] = None,
        aspects: Optional[UefiCmdAspects] = None,
    ) -> "UefiSession":
        resolved = resolve_aspects(aspects, self._aspects)
        session_credential = self._primary_credential
        if user is not None:
            if user not in self._users:
                err_msg_lines = [
                    "Specified user role not found in agent users dict.",
                    f"USER: {user}",
                    f"AVAILABLE: {sorted(self._users.keys())}",
                ]
                err_msg = os.linesep.join(err_msg_lines)
                raise ConfigurationError(err_msg) from None
            session_credential = self._users[user]

        if basis_session is not None:
            session = UefiSession(
                host=basis_session.host,
                primary_credential=session_credential,
                transport_kind=basis_session.transport_kind,
                jump=basis_session.jump,
                users=basis_session.users,
                called_id=basis_session._called_id,
                basis_session=basis_session,
                aspects=resolved,
                logger=self._logger,
            )
        else:
            session = UefiSession(
                host=self._host,
                primary_credential=session_credential,
                transport_kind=self._transport_kind,
                jump=self._jump,
                users=self._users,
                called_id=self._called_id,
                aspects=resolved,
                logger=self._logger,
            )

        self._logger.info(
            "uefi.base: session opened host=%s user=%s basis_session=%s",
            self._host, user, basis_session is not None,
        )
        return session

    # ------------------------------------------------------------------
    # reboot — reconnect-verify pattern mirroring sshagent.py:132–184
    # ------------------------------------------------------------------
    def reboot(self, aspects: Optional[UefiCmdAspects] = None) -> None:
        resolved = resolve_aspects(aspects, self._aspects)
        begin = time.time()
        completion_timeout = resolved.completion_timeout
        retry_interval = resolved.completion_interval
        grace_period = getattr(resolved, "reboot_grace_period", 30)

        self._logger.info(
            "uefi.base: reboot begin host=%s completion_timeout=%ds grace_period=%ds",
            self._host, completion_timeout, grace_period,
        )

        reset_command = self._transport_reset_command()
        try:
            self.run_cmd(command=reset_command, aspects=resolved)
        except (ConnectionError, TimeoutError, OSError, EOFError) as cerr:
            self._logger.debug(
                "uefi.base: expected connection error during reboot: %r", cerr,
            )
        except KeyboardInterrupt:
            raise
        except SystemExit:
            raise
        except Exception as cerr:
            self._logger.warning(
                "uefi.base: unexpected exception during reboot command: %r", cerr,
            )

        time.sleep(grace_period)

        end_time = begin + completion_timeout
        last_exception: Optional[BaseException] = None
        while time.time() < end_time:
            try:
                if self.verify_connectivity():
                    elapsed_ms = int((time.time() - begin) * 1000)
                    self._logger.info(
                        "uefi.base: reboot complete host=%s elapsed_ms=%d",
                        self._host, elapsed_ms,
                    )
                    return
            except BaseException as cerr:
                last_exception = cerr
            time.sleep(retry_interval)

        elapsed_ms = int((time.time() - begin) * 1000)
        err_msg_lines = [
            "UefiAgent.reboot: target did not return within completion_timeout.",
            f"HOST: {self._host}",
            f"TRANSPORT: {self._transport_kind}",
            f"COMPLETION_TIMEOUT: {completion_timeout}s",
            f"ELAPSED_MS: {elapsed_ms}",
            f"LAST_ERR: {last_exception!r}",
        ]
        err_msg = os.linesep.join(err_msg_lines)
        self._logger.error(
            "uefi.base: reboot timeout host=%s elapsed_ms=%d last_err=%r",
            self._host, elapsed_ms, last_exception,
        )
        raise TimeoutError(err_msg) from last_exception

    # ------------------------------------------------------------------
    # _create_client — client construction with aspects retry
    # ------------------------------------------------------------------
    def _create_client(
        self,
        session_user: Optional[str] = None,
        aspects: Optional[UefiCmdAspects] = None,
    ) -> UefiClient:
        resolved = resolve_aspects(aspects, self._aspects)
        credential = self._primary_credential
        if session_user is not None:
            if session_user not in self._users:
                err_msg_lines = [
                    "session_user not in users dict.",
                    f"USER: {session_user}",
                    f"AVAILABLE: {sorted(self._users.keys())}",
                ]
                err_msg = os.linesep.join(err_msg_lines)
                raise ConfigurationError(err_msg) from None
            credential = self._users[session_user]

        transport = self._build_transport(credential=credential, aspects=resolved)
        connection = UefiConnection(transport=transport, aspects=resolved, host=self._host, logger=self._logger)
        connection.connect()
        client = self._client_factory(
            transport_kind=self._transport_kind,
            transport=transport,
            aspects=resolved,
        )
        return client

    def _build_transport(self, credential: UefiCredential, aspects: UefiCmdAspects) -> IUefiTransport:
        # Dispatches to the right transport class based on transport_kind.
        # Implementation detail omitted here; see the transport features 2-*.
        ...

    def _transport_reset_command(self) -> str:
        if self._transport_kind in ("serial", "ipmi-sol"):
            return "reset -c"
        if self._transport_kind == "redfish":
            return "POST /redfish/v1/Systems/{id}/Actions/ComputerSystem.Reset {\"ResetType\":\"ForceRestart\"}"
        if self._transport_kind == "ssh-host":
            return "reboot"
        err_msg = f"Unknown transport_kind for reset: {self._transport_kind!r}."
        raise ConfigurationError(err_msg) from None
```

### File / directory primitives

Delegated to the composed SSH-host transport for `transport_kind ==
"ssh-host"`; raise `SemanticError` for other transports with a pointer
to `5-esp-file-ops`.

```python
    def file_push(self, localpath: str, remotepath: str,
                  aspects: Optional[UefiCmdAspects] = None,
                  ssh_client: Optional[SshClient] = None) -> None:
        if self._transport_kind != "ssh-host":
            err_msg = (
                "UefiAgent.file_push is SSH-host only.  For UEFI Shell or Redfish "
                "file operations, use layer-5 EspFileProvider (5-esp-file-ops)."
            )
            raise SemanticError(err_msg) from None
        # Delegate to composed SshAgent:
        self._transport_ssh_agent.file_push(
            localpath=localpath, remotepath=remotepath, ssh_client=ssh_client,
        )
        return
```

## Data Models

`UefiAgent` carries no additional instance state beyond what
`UefiBase.__init__` establishes.  All instance variables are initialized
in `__init__` via `super().__init__` per
`/Users/myron/repos/CLAUDE.md`.

## Measured Interop Contract

`UefiAgent` consumes the same `UefiCmdAspects` fields as `UefiBase`
(see `4-base` Measured Interop Contract).  Agent-specific consumption:

| Field                     | Where consumed                                   |
|---------------------------|--------------------------------------------------|
| `connection_timeout`      | `_create_client` retry loop (via `UefiConnection`) |
| `connection_interval`     | `_create_client` retry loop                      |
| `allowed_connection_exceptions` | `_create_client`                           |
| `must_connect`            | `_create_client`                                 |
| `completion_timeout`      | `reboot` port-return deadline                    |
| `completion_interval`     | `reboot` polling interval                        |
| `reboot_grace_period`     | (agent-specific; falls back to 30 if absent)     |

`ActionPattern`s supported by `UefiAgent.run_cmd`:

| Pattern                         | Supported                                |
|---------------------------------|------------------------------------------|
| `SINGLE_CALL`                   | yes                                      |
| `DO_UNTIL_SUCCESS`              | yes                                      |
| `DO_WHILE_SUCCESS`              | yes                                      |
| `SINGLE_CONNECTED_CALL`         | SemanticError (session-only)             |
| `DO_UNTIL_CONNECTION_FAILURE`   | SemanticError (session-only)             |

Mirrors `sshbase.py:625–627`.

## Error Handling

| Condition                                             | Exception             | Included in `details`              |
|-------------------------------------------------------|-----------------------|------------------------------------|
| Unknown `transport_kind`                              | `ConfigurationError`  | received value                     |
| `user` not in `self._users`                           | `ConfigurationError`  | user, available roles              |
| `SINGLE_CONNECTED_CALL` / `DO_UNTIL_CONNECTION_FAILURE` passed | `SemanticError` | pattern                   |
| File/directory primitive on non-SSH-host transport    | `SemanticError`       | transport_kind, pointer to 5-esp-file-ops |
| `reboot` target does not return                       | `TimeoutError`        | host, transport, completion_timeout, elapsed_ms, last_exception |
| Connection build fails after retry                    | `ConnectionError`     | host, transport_kind, begin/end, attempts, last exception |
| `aspects` resolution with both None                   | `ConfigurationError`  | caller frame                       |

## Logging

All output routes through `get_uefi_logger("uefi.base")` (inherited from
`UefiBase`):

| Event                   | Level | Fields                                                 |
|-------------------------|-------|--------------------------------------------------------|
| agent.constructed       | DEBUG | host, transport_kind, called_id, aspects_summary       |
| agent.session.opened    | INFO  | host, session_user, basis_session                      |
| agent.reboot.begin      | INFO  | host, completion_timeout, grace_period                 |
| agent.reboot.complete   | INFO  | host, elapsed_ms                                       |
| agent.reboot.timeout    | ERROR | host, elapsed_ms, last_exception                       |
| agent.reboot.expected_err | DEBUG | host, exception_repr                                   |

## Security Considerations

* `UefiAgent` holds a credential reference but never writes it to a
  log line.  `__repr__` redacts `password`, `token`, `keyraw`,
  `keypasswd`, and any attribute ending in `_secret`.
* `reboot()` does not log the reset command body (which could reveal
  firmware-specific secrets on some platforms) — only the transport
  kind.

## Testing Strategy

### Unit tests (`test_uefiagent_construct.py`)

* Construction with each valid `transport_kind`.
* Construction with an invalid `transport_kind` raises
  `ConfigurationError`.
* `called_id` formation of `_target_tag`.
* Custom `aspects` stored as instance default.

### Unit tests (`test_uefiagent_run_cmd.py`)

* One-shot `run_cmd` opens and closes a client.
* `run_cmd(client=existing)` does not close the provided client.
* `run_cmd(user="ops")` uses the `ops` credential.
* `user` not in users raises `ConfigurationError`.
* `SINGLE_CALL` / `DO_UNTIL_SUCCESS` / `DO_WHILE_SUCCESS` pass through
  to `UefiBase._run_cmd`.
* `SINGLE_CONNECTED_CALL` and `DO_UNTIL_CONNECTION_FAILURE` raise
  `SemanticError`.

### Unit tests (`test_uefiagent_open_session.py`)

* Session inherits host/credential/transport/jump/aspects.
* `user` parameter switches the session's primary credential.
* `basis_session` clones connection parameters from an existing
  session (parallels `sshagent.py:87–96`).
* `aspects=None` → session gets agent default.

### Unit tests (`test_uefiagent_reboot.py`)

* Happy path: reset sent, grace period elapses, verify_connectivity
  returns `True` → function returns.
* Expected connection-error during reset is swallowed at DEBUG.
* Timeout path: verify_connectivity never returns `True` →
  `TimeoutError` with the expected `details` block.
* Custom `reboot_grace_period` via aspects is honored.
* Per-transport reset command (serial, redfish, ssh-host) is dispatched
  correctly.

### Unit tests (`test_uefiagent_create_client.py`)

* Client built for each `transport_kind`.
* Retry loop respects `connection_timeout` / `connection_interval`.
* `allowed_connection_exceptions` honored.
* `must_connect=True` short-circuits retry on first failure.

### Unit tests (`test_uefiagent_file_primitives.py`)

* `file_push` on SSH-host transport forwards to composed `SshAgent`.
* `file_push` on serial / Redfish / IPMI-SOL raises `SemanticError`
  with the 5-esp-file-ops pointer.

### Integration tests

* Reboot flow test using a fake transport that simulates disconnect,
  port-return delay, and reconnect.  Verify the reconnect-verify cycle
  completes within the expected elapsed time.
* Session-basis reuse test: open a session, open a second session
  with the first as `basis_session`, verify the second session does
  not create a new connection.
* Coverage target: 95% line coverage.

## Dependencies on Other Features

* `1-aspects`, `1-credentials`, `1-exceptions`, `1-helpers`,
  `1-interfaces`, `1-logging`, `1-constants-and-types`.
* `2-transport-*` — the concrete transports the agent drives.
* `3-client`, `3-connection`, `3-channel` — the framing layer.
* `4-base` — parent class; provides `_run_cmd`, `_log_command_result`,
  `verify_connectivity`, and the `IUefiSystemContext` mixin.
* `4-session` — returned by `open_session`.
