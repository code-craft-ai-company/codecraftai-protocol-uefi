# Design — UefiBase

## Overview

`UefiBase` is the hub of the library's measured-interop machinery.  It owns
credentials, default aspects, the host/transport-kind configuration, the
`MonitoredScope`/`TimeoutContext`-backed action-pattern engine, and the
`LoggingPattern`-driven result logger.  It implements
`IUefiSystemContext` so every subclass instance is immediately usable as
a system-context argument by layer-5 capability modules.

The class is a faithful port of `SshBase`
(`/Users/myron/repos/automojo-protocol-ssh/source/packages/automojo/protocols/ssh/sshbase.py`),
with three important extensions:

1. **Five-pattern engine.**  The SSH base only supports three patterns
   (see `sshbase.py:625–627`); UEFI sessions need
   `SINGLE_CONNECTED_CALL` and `DO_UNTIL_CONNECTION_FAILURE` for long-
   running shell work across firmware reboots.
2. **Pluggable client factory.**  `UefiBase` accepts a
   `_client_factory(transport_kind)` callable so the same base class
   can instantiate any of the three `UefiClient` subclasses without
   branching on transport type at the call site.
3. **Transport-aware `_run_cmd` return normalization.**  Redfish results
   are mapped into the `(status, stdout, stderr)` contract so layer-5
   capabilities can be transport-agnostic.

## Architecture

```
automojo.protocols.uefi.base/
    __init__.py       # re-exports UefiBase
    uefibase.py       # the class
    runctx.py         # _RunContext dataclass for threading parameters
```

Dependency direction (nothing imports upward):

```
uefibase.py ──▶ automojo.protocols.uefi.aspects
            ──▶ automojo.protocols.uefi.client
            ──▶ automojo.protocols.uefi.connection
            ──▶ automojo.protocols.uefi.channel
            ──▶ automojo.protocols.uefi.credentials
            ──▶ automojo.protocols.uefi.exceptions
            ──▶ automojo.protocols.uefi.interfaces
            ──▶ automojo.protocols.uefi.logging
            ──▶ automojo.waiting.waitmodel            # TimeoutContext
            ──▶ automojo.xmodules.xlogging.monitoredscope  # MonitoredScope
            ──▶ automojo.errors.exceptions            # AbstractMethodError, SemanticError, ConfigurationError
```

## Components and Interfaces

### `UefiBase` (uefibase.py)

Constructor signature (mirrors `sshbase.py:69–127`, adapted for UEFI):

```python
class UefiBase(IUefiSystemContext):
    """
        Shared-state/shared-behavior base for UefiAgent and UefiSession.
        Owns credentials, aspects, host/transport config, and the five-
        pattern action-pattern engine.
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
        self._host = host
        self._primary_credential = primary_credential
        self._transport_kind = transport_kind
        self._jump = jump
        self._users = users if users is not None else {}
        self._called_id = called_id
        self._target_tag = host if called_id is None else f"[{called_id}]"
        self._aspects = aspects
        self._logger = logger if logger is not None else get_uefi_logger("uefi.base")
        self._client_factory = client_factory if client_factory is not None else default_client_factory
        self._user_lookup_table: dict[int, str] = {}
        self._group_lookup_table: dict[int, str] = {}
        self._firmware_vendor_cache: Optional[str] = None
        self._firmware_revision_cache: Optional[str] = None
        self._secure_boot_state_cache: Optional[SecureBootState] = None
        self._validate_credential()
        return

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------
    @property
    def aspects(self) -> UefiCmdAspects:
        return self._aspects

    @property
    def host(self) -> str:
        return self._host

    @property
    def transport_kind(self) -> str:
        return self._transport_kind

    @property
    def jump(self) -> Optional[UefiJumpParams]:
        return self._jump

    @property
    def users(self) -> Dict[str, UefiCredential]:
        return self._users

    @property
    def primary_credential(self) -> UefiCredential:
        return self._primary_credential

    @property
    def target_tag(self) -> str:
        return self._target_tag

    # ------------------------------------------------------------------
    # IUefiSystemContext
    # ------------------------------------------------------------------
    @property
    def firmware_vendor(self) -> str:
        if self._firmware_vendor_cache is None:
            self._firmware_vendor_cache = self._probe_firmware_vendor()
        return self._firmware_vendor_cache

    @property
    def firmware_revision(self) -> str:
        if self._firmware_revision_cache is None:
            self._firmware_revision_cache = self._probe_firmware_revision()
        return self._firmware_revision_cache

    @property
    def secure_boot_state(self) -> SecureBootState:
        if self._secure_boot_state_cache is None:
            self._secure_boot_state_cache = self._probe_secure_boot_state()
        return self._secure_boot_state_cache

    # ------------------------------------------------------------------
    # Methods intended for override
    # ------------------------------------------------------------------
    def reboot(self, aspects: Optional[UefiCmdAspects] = None) -> None:
        raise AbstractMethodError(
            "UefiBase.reboot must be overloaded by derived class '%s'." % type(self).__name__
        ) from None

    def run_cmd(
        self,
        command: str,
        exp_status: Union[int, Sequence[int]] = 0,
        user: Optional[str] = None,
        aspects: Optional[UefiCmdAspects] = None,
    ) -> Tuple[int, str, str]:
        raise AbstractMethodError(
            "UefiBase.run_cmd must be overloaded by derived class '%s'." % type(self).__name__
        ) from None

    def run_redfish(
        self,
        verb: str,
        path: str,
        json_body: Optional[dict] = None,
        aspects: Optional[UefiCmdAspects] = None,
    ) -> Tuple[int, str, str]:
        raise AbstractMethodError(
            "UefiBase.run_redfish must be overloaded by derived class '%s'." % type(self).__name__
        ) from None

    def run_variable(
        self,
        name: str,
        vendor_guid: UUID,
        operation: str,
        payload: Optional[bytes] = None,
        aspects: Optional[UefiCmdAspects] = None,
    ) -> Tuple[int, str, str]:
        raise AbstractMethodError(
            "UefiBase.run_variable must be overloaded by derived class '%s'." % type(self).__name__
        ) from None

    # ------------------------------------------------------------------
    # verify_connectivity
    # ------------------------------------------------------------------
    def verify_connectivity(self) -> bool:
        vok = False
        try:
            if self._transport_kind in ("serial", "ipmi-sol"):
                status, stdout, _ = self.run_cmd("echo Hello")
                if status == 0 and "Hello" in stdout:
                    vok = True
            elif self._transport_kind == "redfish":
                status, _, _ = self.run_redfish(verb="GET", path="/redfish/v1/")
                if status == 200:
                    vok = True
            elif self._transport_kind == "ssh-host":
                status, stdout, _ = self.run_cmd("echo Hello")
                if status == 0 and stdout.strip() == "Hello":
                    vok = True
            else:
                err_msg = f"Unknown transport_kind for verify_connectivity: {self._transport_kind!r}."
                raise ConfigurationError(err_msg) from None
        except ConnectionError as cerr:
            self._logger.debug(
                "uefi.base: verify_connectivity observed ConnectionError: %r", cerr,
            )
            vok = False
        return vok
```

### `_log_command_result` — matches `sshbase.py:469–499`

```python
    def _log_command_result(
        self,
        command: str,
        status: int,
        stdout: str,
        stderr: str,
        exp_status: Union[int, Sequence[int]],
        logging_pattern: LoggingPattern,
    ) -> None:
        tgt_tag = self._target_tag if self._target_tag is not None else ""

        if isinstance(exp_status, int):
            matched = (status == exp_status)
        else:
            matched = (status in exp_status)

        if matched:
            if logging_pattern in (LoggingPattern.ALL_RESULTS, LoggingPattern.SUCCESS_ONLY):
                self._logger.info(TEMPLATE_COMMAND_SUCCESS % (tgt_tag, command, stdout, stderr))
        else:
            if logging_pattern in (LoggingPattern.ALL_RESULTS, LoggingPattern.FAILURE_ONLY):
                self._logger.error(TEMPLATE_COMMAND_FAILURE % (tgt_tag, command, stdout, stderr))
        return
```

### `_run_cmd` — the action-pattern engine

Matches `sshbase.py:501–632` structurally, with five patterns instead of
three and with additional allowed-connection-exception handling.
Abridged skeleton:

```python
    def _run_cmd(
        self,
        runner: Union[UefiClient, UefiChannel],
        command: str,
        exp_status: Union[int, Sequence[int]] = 0,
        user: Optional[str] = None,
        aspects: Optional[UefiCmdAspects] = None,
    ) -> Tuple[int, str, str]:
        resolved = resolve_aspects(aspects, self._aspects)
        completion_toctx = TimeoutContext(resolved.completion_timeout, resolved.completion_interval)
        inactivity_timeout = resolved.inactivity_timeout
        inactivity_interval = resolved.inactivity_interval
        monitor_delay = resolved.monitor_delay
        logging_pattern = resolved.logging_pattern
        allowed_exceptions = tuple(resolved.allowed_connection_exceptions)
        must_connect = resolved.must_connect

        this_thr = threading.current_thread()
        monmsg = "Thread failed to exit monitored scope. thid=%s thname=%s cmd=%s" % (
            this_thr.ident, this_thr.name, command,
        )

        status: Optional[int] = None
        stdout: Optional[str] = None
        stderr: Optional[str] = None

        pattern = resolved.action_pattern
        if pattern == ActionPattern.SINGLE_CALL:
            with MonitoredScope("RUNCMD-SINGULAR", monmsg, completion_toctx, notify_delay=monitor_delay):
                status, stdout, stderr = self._dispatch_inner(runner, command, resolved, user)
            self._log_command_result(command, status, stdout, stderr, exp_status, logging_pattern)

        elif pattern == ActionPattern.DO_UNTIL_SUCCESS:
            completion_toctx.mark_begin()
            while True:
                try:
                    with MonitoredScope("RUNCMD-DO_UNTIL_SUCCESS", monmsg, completion_toctx, notify_delay=monitor_delay):
                        status, stdout, stderr = self._dispatch_inner(runner, command, resolved, user)
                    self._log_command_result(command, status, stdout, stderr, exp_status, logging_pattern)
                    if self._status_matches(status, exp_status):
                        break
                except BaseException as cerr:
                    if must_connect and isinstance(cerr, ConnectionError):
                        raise
                    if len(allowed_exceptions) == 0 or not isinstance(cerr, allowed_exceptions):
                        raise
                    self._logger.warning("uefi.base: allowed exception during DO_UNTIL_SUCCESS: %r", cerr)
                    status, stdout, stderr = -1, "", repr(cerr)

                if completion_toctx.final_attempt:
                    details = [
                        f"CMD: {command}",
                        "STDOUT:", indent_lines(stdout, 1),
                        "STDERR:", indent_lines(stderr, 1),
                    ]
                    raise completion_toctx.create_timeout(what_for="command success", detail=details)
                elif not completion_toctx.should_continue():
                    completion_toctx.mark_final_attempt()
                time.sleep(completion_toctx.interval)

        elif pattern == ActionPattern.DO_WHILE_SUCCESS:
            # ... mirrors sshbase.py:590–623 ...

        elif pattern == ActionPattern.SINGLE_CONNECTED_CALL:
            # UEFI-specific extension for UefiSession.
            if not isinstance(runner, UefiChannel):
                err_msg = "SINGLE_CONNECTED_CALL requires a UefiChannel runner (session context)."
                raise SemanticError(err_msg) from None
            with MonitoredScope("RUNCMD-SINGLE_CONNECTED_CALL", monmsg, completion_toctx, notify_delay=monitor_delay):
                status, stdout, stderr = self._dispatch_inner(runner, command, resolved, user)
            self._log_command_result(command, status, stdout, stderr, exp_status, logging_pattern)

        elif pattern == ActionPattern.DO_UNTIL_CONNECTION_FAILURE:
            if not isinstance(runner, UefiChannel):
                err_msg = "DO_UNTIL_CONNECTION_FAILURE requires a UefiChannel runner (session context)."
                raise SemanticError(err_msg) from None
            completion_toctx.mark_begin()
            while True:
                try:
                    with MonitoredScope("RUNCMD-DO_UNTIL_CONNECTION_FAILURE", monmsg, completion_toctx, notify_delay=monitor_delay):
                        status, stdout, stderr = self._dispatch_inner(runner, command, resolved, user)
                    self._log_command_result(command, status, stdout, stderr, exp_status, logging_pattern)
                except BaseException as cerr:
                    if len(allowed_exceptions) > 0 and isinstance(cerr, allowed_exceptions):
                        self._logger.info("uefi.base: DO_UNTIL_CONNECTION_FAILURE observed allowed exception; exiting loop: %r", cerr)
                        break
                    raise
                if completion_toctx.final_attempt:
                    break
                elif not completion_toctx.should_continue():
                    completion_toctx.mark_final_attempt()
                time.sleep(completion_toctx.interval)
        else:
            err_msg = f"UefiBase: Unknown ActionPattern encountered. action_pattern={pattern}"
            raise SemanticError(err_msg) from None

        return status, stdout, stderr
```

### `_dispatch_inner`

```python
    def _dispatch_inner(
        self,
        runner: Union[UefiClient, UefiChannel],
        command: str,
        aspects: UefiCmdAspects,
        user: Optional[str],
    ) -> Tuple[int, str, str]:
        if isinstance(runner, UefiClient):
            result = runner.execute(command=command, aspects=aspects)
            return (result.status, result.stdout, result.stderr)
        if isinstance(runner, UefiChannel):
            runner.send((command + os.linesep).encode(aspects.response_encoding))
            raw = runner.recv_until(pattern=aspects.prompt_pattern, timeout=aspects.inactivity_timeout)
            parsed = parse_shell_response(raw=raw, encoding=aspects.response_encoding)
            return (parsed.status, parsed.stdout, parsed.stderr)
        err_msg = f"UefiBase._dispatch_inner: unsupported runner type {type(runner).__name__}."
        raise SemanticError(err_msg) from None
```

### Pluggable `default_client_factory`

```python
def default_client_factory(transport_kind: str, transport: IUefiTransport,
                           aspects: UefiCmdAspects) -> UefiClient:
    if transport_kind in ("serial", "ipmi-sol"):
        return UefiShellClient(transport=transport, aspects=aspects)
    if transport_kind == "redfish":
        return UefiRedfishClient(transport=transport, aspects=aspects)
    if transport_kind == "ssh-host":
        return UefiHostOsClient(transport=transport, aspects=aspects)
    err_msg = f"Unknown transport_kind for client factory: {transport_kind!r}."
    raise ConfigurationError(err_msg) from None
```

## Data Models

* `_RunContext` — private dataclass bundling per-call parameters
  (`completion_toctx`, `inactivity_timeout`, `inactivity_interval`,
  `monitor_delay`, `logging_pattern`, `allowed_exceptions`, `must_connect`)
  so `_run_cmd` does not repeatedly reach into the aspects object.
* `SecureBootState` — see `5-secure-boot`; imported here only for
  `IUefiSystemContext.secure_boot_state` typing.

All instance variables are initialized in `__init__` per
`/Users/myron/repos/CLAUDE.md` class-variable rule.

## Measured Interop Contract

This feature is the single largest consumer of aspects in the library.
Fields consumed:

| Field                              | Where consumed                                        |
|------------------------------------|-------------------------------------------------------|
| `action_pattern`                   | `_run_cmd` top-level dispatch                         |
| `completion_timeout`               | `TimeoutContext` construction                         |
| `completion_interval`              | `TimeoutContext` interval, `time.sleep` between iters |
| `inactivity_timeout`               | passed through to `_dispatch_inner` / channel         |
| `inactivity_interval`              | passed through to channel `recv_until`                |
| `monitor_delay`                    | `MonitoredScope(notify_delay=...)`                    |
| `logging_pattern`                  | `_log_command_result`                                 |
| `expected_status`                  | `_run_cmd` success/failure comparison                 |
| `allowed_connection_exceptions`    | `_run_cmd` try/except around inner call               |
| `must_connect`                     | `_run_cmd` decision to re-raise `ConnectionError`     |
| `prompt_pattern`                   | `_dispatch_inner` when `runner` is a `UefiChannel`    |
| `response_encoding`                | `_dispatch_inner` byte/string conversion              |
| `user`                             | forwarded to `_dispatch_inner`                        |
| `transport_kind`                   | consulted by `verify_connectivity`                    |
| `logger`                           | forwarded through constructor                         |

`ActionPattern`s supported by the base engine: **all five** —
`SINGLE_CALL`, `SINGLE_CONNECTED_CALL`, `DO_UNTIL_SUCCESS`,
`DO_WHILE_SUCCESS`, `DO_UNTIL_CONNECTION_FAILURE`.

Supported-by-subclass matrix (enforced by subclass overrides of
`run_cmd`):

| Pattern                         | `UefiAgent`               | `UefiSession`                   |
|---------------------------------|---------------------------|---------------------------------|
| `SINGLE_CALL`                   | yes                       | SemanticError (session semantics) |
| `DO_UNTIL_SUCCESS`              | yes                       | SemanticError                   |
| `DO_WHILE_SUCCESS`              | yes                       | SemanticError                   |
| `SINGLE_CONNECTED_CALL`         | SemanticError             | yes                             |
| `DO_UNTIL_CONNECTION_FAILURE`   | SemanticError             | yes                             |

The split mirrors `sshbase.py:625–627` but with the opposite sides
implemented: UEFI's long-lived shell session is the primary use case
for `SINGLE_CONNECTED_CALL` / `DO_UNTIL_CONNECTION_FAILURE`, which the
SSH library does not currently support.

## Error Handling

| Condition                                              | Exception             | Included in `details`              |
|--------------------------------------------------------|-----------------------|------------------------------------|
| Unknown `action_pattern` encountered                   | `SemanticError`       | pattern repr                       |
| Pattern not supported by subclass (e.g., SINGLE_CALL on session) | `SemanticError` | pattern, subclass name             |
| `must_connect` + `ConnectionError`                     | re-raised              | —                                  |
| Non-allowed exception during inner call                | passthrough            | —                                  |
| Credential/transport mismatch in `__init__`            | `ConfigurationError`  | expected/received types            |
| Completion loop exhausted without success              | `TimeoutContext.create_timeout` | command, stdout, stderr, indented |
| `run_cmd` called on base (not subclass)                | `AbstractMethodError` | subclass name                      |
| `_dispatch_inner` with unsupported runner              | `SemanticError`       | runner type                        |
| `verify_connectivity` on unknown transport kind        | `ConfigurationError`  | transport_kind                     |

`SemanticError` is never caught here — consistent with
`/Users/myron/repos/CLAUDE.md`.

## Logging

All base-class log output routes through `get_uefi_logger("uefi.base")`.
Command-result logging uses `_log_command_result` with templates
`TEMPLATE_COMMAND_SUCCESS` / `TEMPLATE_COMMAND_FAILURE` from
`1-constants-and-types`, matching `sshbase.py:42–47`.

| Event                          | Level   | Fields                                            |
|--------------------------------|---------|---------------------------------------------------|
| command.success                | INFO    | target_tag, command, stdout, stderr               |
| command.failure                | ERROR   | target_tag, command, stdout, stderr               |
| action.allowed_exception       | WARNING | action_pattern, exception_repr                    |
| action.until_conn_failure.exit | INFO    | action_pattern, exception_repr                    |
| verify_connectivity.down       | DEBUG   | transport_kind, exception_repr                    |

## Security Considerations

* The base class holds a `UefiCredential` reference.  `__repr__` is
  implemented to redact any attribute named `password`, `token`,
  `keyraw`, `keypasswd`, or matching `*_secret`.
* `_log_command_result` emits the command string verbatim.  Callers
  who pass secrets on the command line are responsible for their own
  redaction — the base class cannot infer what is secret.
* `verify_connectivity` does not emit secrets; `echo Hello` is the
  probe.

## Testing Strategy

### Unit tests (`test_uefibase_init.py`)

* `__init__` sets every documented instance variable.
* `__init__` does not perform any network I/O (verified via a no-network
  monkeypatch that raises on `socket` use).
* `_target_tag` formation: `called_id=None` → host; non-None → `[id]`.
* `_validate_credential` rejects mismatched transport/credential.

### Unit tests (`test_uefibase_action_pattern.py`)

* One test per `ActionPattern`:
  - `SINGLE_CALL` — runs once, returns.
  - `DO_UNTIL_SUCCESS` — loops until match, returns.
  - `DO_UNTIL_SUCCESS` — times out when never matches; raises
    TimeoutContext error with `command`, `STDOUT`, `STDERR` in details.
  - `DO_WHILE_SUCCESS` — loops while match, returns when diverges.
  - `DO_WHILE_SUCCESS` — times out when match is perpetual.
  - `SINGLE_CONNECTED_CALL` — requires `UefiChannel` runner; raises
    `SemanticError` when passed a `UefiClient`.
  - `DO_UNTIL_CONNECTION_FAILURE` — loops until allowed exception
    raises, then exits cleanly.
  - Unknown pattern — raises `SemanticError`.

### Unit tests (`test_uefibase_allowed_exceptions.py`)

* `DO_UNTIL_SUCCESS` tolerates allowed exceptions and continues
  looping.
* `DO_UNTIL_SUCCESS` re-raises non-allowed exceptions.
* `must_connect=True` re-raises `ConnectionError` without retry even
  when it is listed in allowed.
* `DO_UNTIL_CONNECTION_FAILURE` exits cleanly on an allowed exception.

### Unit tests (`test_uefibase_monitored_scope.py`)

* Each pattern wraps the inner call in a `MonitoredScope` with the
  expected label and `notify_delay`.
* `MonitoredScope` fire message contains thread id, thread name, and
  the command — mirroring `sshbase.py:539`.

### Unit tests (`test_uefibase_logging.py`)

* `_log_command_result` obeys each `LoggingPattern` value.
* `_target_tag` is prepended to every log line.
* `expected_status` int vs. sequence comparison.

### Unit tests (`test_uefibase_abstract.py`)

* `reboot`, `run_cmd`, `run_redfish`, `run_variable` raise
  `AbstractMethodError` with the subclass name included.

### Unit tests (`test_uefibase_system_context.py`)

* `isinstance(base, IUefiSystemContext)` is `True`.
* `firmware_vendor` / `firmware_revision` / `secure_boot_state` cache
  after first access.

### Integration tests

* Five-pattern matrix over a fake `UefiClient`/`UefiChannel` pair.
* Coverage target: 95% line coverage; 100% for the action-pattern
  engine dispatch switch.

## Dependencies on Other Features

* `1-aspects`, `1-credentials`, `1-exceptions`, `1-helpers`,
  `1-interfaces`, `1-logging`, `1-constants-and-types`.
* `2-transport-*` — consumed indirectly via clients.
* `3-client`, `3-connection`, `3-channel` — the concrete runners
  driven by the action-pattern engine.
