# Design — System Reset

## Overview

System Reset issues UEFI-spec-defined resets (UEFI §8.5.1) and blocks
until the target is verified responsive.  The design is a direct port of
the `SshAgent.reboot()` pattern
(`/Users/myron/repos/automojo-protocol-ssh/source/packages/automojo/protocols/ssh/sshagent.py:132-185`):
issue the reset, sleep a bounded "tearing down" window, poll
`verify_connectivity()` until success or timeout, raise on elapse.

Dispatch is transport-aware: the UEFI Shell `reset` command on
serial/IPMI-SOL, the Redfish `ComputerSystem.Reset` action over Redfish,
and an OS-mediated `EFI_RUNTIME_SERVICES.ResetSystem` shim (via
`efi-reset` or `/sys/firmware/efi/reset_type` fallback) on SSH-to-host.
Platform-specific reset subtypes pack a GUID plus opaque data per UEFI
§8.5.1's `ResetData` convention.

## Architecture

```
automojo.protocols.uefi.reset/
    __init__.py           # re-exports SystemReset, ResetKind, ResetFailedError
    resetkind.py          # ResetKind enum
    errors.py             # ResetFailedError
    resetshell.py         # UEFI Shell reset implementation
    resetredfish.py       # Redfish ComputerSystem.Reset implementation
    resetsshhost.py       # SSH-to-host ResetSystem shim
    verifyloop.py         # reconnect_verify_loop helper (the core SshAgent.reboot port)
    systemreset.py        # SystemReset facade; dispatch by transport kind
```

Dependency direction:

```
resetkind.py ── errors.py ──┐
                            ├── resetshell.py ───┐
                            ├── resetredfish.py ─┼── systemreset.py
                            └── resetsshhost.py ─┤
verifyloop.py ──────────────────────────────────┘
```

## Components and Interfaces

### `SystemReset` facade (systemreset.py)

```python
class SystemReset:
    """
        Reset and power-control facade with reconnect-verify semantics.
    """

    def __init__(
        self,
        agent: "UefiAgent",
        aspects: Optional[UefiCmdAspects] = None,
    ) -> None:
        self._agent = agent
        self._aspects = aspects
        return

    def reset(
        self,
        kind: ResetKind,
        reason: Optional[str] = None,
        reset_subtype_guid: Optional[uuid.UUID] = None,
        reset_data: bytes = b"",
        redfish_reset_type: Optional[str] = None,
        aspects: Optional[UefiCmdAspects] = None,
    ) -> None:
        """
            Issue a reset and (for non-shutdown kinds) wait until the
            target is responsive.

            :param kind: One of ResetKind.COLD/WARM/SHUTDOWN/PLATFORM_SPECIFIC.
            :param reason: Optional human-readable reason for logs.
            :param reset_subtype_guid: Required for PLATFORM_SPECIFIC.
            :param reset_data: Opaque ResetData payload.
            :param redfish_reset_type: Redfish ResetType override for
                PLATFORM_SPECIFIC on the Redfish transport.
            :param aspects: Optional aspects override.

            :raises: ConfigurationError, UefiError, ResetFailedError, TimeoutError
        """
        effective = resolve_aspects(aspects, self._aspects)
        _validate_reset_args(
            kind=kind,
            reset_subtype_guid=reset_subtype_guid,
            redfish_reset_type=redfish_reset_type,
            transport_kind=self._agent.transport_kind,
        )
        _issue_reset(
            agent=self._agent,
            kind=kind,
            reason=reason,
            reset_subtype_guid=reset_subtype_guid,
            reset_data=reset_data,
            redfish_reset_type=redfish_reset_type,
            aspects=effective,
        )
        if kind == ResetKind.SHUTDOWN:
            _wait_until_unreachable(agent=self._agent, aspects=effective)
            return

        _reconnect_verify_loop(
            agent=self._agent,
            kind=kind,
            aspects=effective,
        )
        return
```

### `ResetKind` (resetkind.py)

```python
class ResetKind(IntEnum):
    COLD = 0               # EfiResetCold
    WARM = 1               # EfiResetWarm
    SHUTDOWN = 2           # EfiResetShutdown
    PLATFORM_SPECIFIC = 3  # EfiResetPlatformSpecific
```

### `ResetFailedError` (errors.py)

```python
class ResetFailedError(UefiError):
    """
        Raised when reconnect-verify does not observe the target return
        within aspects.completion_timeout.
    """

    def __init__(
        self,
        message: str,
        host: str,
        kind: "ResetKind",
        elapsed_seconds: float,
        attempt_count: int,
        last_error: Optional[BaseException],
        transport: str,
        details: Optional[list[str]] = None,
    ) -> None:
        super().__init__(message, details=details)
        self.host = host
        self.kind = kind
        self.elapsed_seconds = elapsed_seconds
        self.attempt_count = attempt_count
        self.last_error = last_error
        self.transport = transport
        return
```

### `reconnect_verify_loop` (verifyloop.py) — the `SshAgent.reboot` port

```python
def _reconnect_verify_loop(
    agent: "UefiAgent",
    kind: ResetKind,
    aspects: UefiCmdAspects,
) -> None:
    logger = aspects.logger
    start_time = time.time()
    end_time = start_time + aspects.completion_timeout
    attempt_count = 0
    last_error: Optional[BaseException] = None

    logger.info(
        "reset issued host=%s kind=%s waiting monitor_delay=%ss",
        agent.host,
        kind.name,
        aspects.monitor_delay,
    )
    time.sleep(aspects.monitor_delay)

    while time.time() < end_time:
        attempt_count = attempt_count + 1
        try:
            verified = agent.verify_connectivity(aspects=aspects)
            if verified is True:
                elapsed = time.time() - start_time
                logger.info(
                    "reset verified host=%s kind=%s elapsed=%.1fs attempts=%d",
                    agent.host,
                    kind.name,
                    elapsed,
                    attempt_count,
                )
                return
        except BaseException as ex:
            if _is_allowed_connection_exception(ex=ex, aspects=aspects):
                last_error = ex
            else:
                raise

        time.sleep(aspects.connection_interval)

    elapsed = time.time() - start_time
    err_msg_lines = [
        f"Host did not return within {aspects.completion_timeout}s after {kind.name} reset.",
    ]
    err_msg_lines.append(f"HOST: {agent.host}")
    err_msg_lines.append(f"TRANSPORT: {agent.transport_kind}")
    err_msg_lines.append(f"ELAPSED: {elapsed:.1f}s")
    err_msg_lines.append(f"ATTEMPTS: {attempt_count}")
    err_msg_lines.append(f"LAST_ERROR: {last_error!r}")
    err_msg = os.linesep.join(err_msg_lines)
    raise ResetFailedError(
        err_msg,
        host=agent.host,
        kind=kind,
        elapsed_seconds=elapsed,
        attempt_count=attempt_count,
        last_error=last_error,
        transport=agent.transport_kind,
        details=[f"host={agent.host}", f"kind={kind.name}", f"elapsed={elapsed:.1f}"],
    )
```

`_is_allowed_connection_exception` tests
`isinstance(ex, tuple(aspects.allowed_connection_exceptions))` and returns
`True` only when the exception type is in the allow-list.

### `_issue_reset` dispatch (systemreset.py)

```python
def _issue_reset(
    agent: "UefiAgent",
    kind: ResetKind,
    reason: Optional[str],
    reset_subtype_guid: Optional[uuid.UUID],
    reset_data: bytes,
    redfish_reset_type: Optional[str],
    aspects: UefiCmdAspects,
) -> None:
    if agent.transport_kind in ("serial", "ipmi-sol"):
        _issue_reset_shell(agent=agent, kind=kind, reset_data=_pack_reset_data(reset_subtype_guid=reset_subtype_guid, reset_data=reset_data), aspects=aspects)
    elif agent.transport_kind == "redfish":
        _issue_reset_redfish(agent=agent, kind=kind, redfish_reset_type=redfish_reset_type, reason=reason, aspects=aspects)
    elif agent.transport_kind == "ssh-host":
        _issue_reset_ssh_host(agent=agent, kind=kind, reset_subtype_guid=reset_subtype_guid, reset_data=reset_data, aspects=aspects)
    else:
        err_msg = f"System reset not supported for transport_kind={agent.transport_kind!r}."
        raise ConfigurationError(err_msg)
    return
```

### Shell reset (resetshell.py)

Maps `ResetKind` to the corresponding UEFI Shell `reset` flags:

| ResetKind         | Shell command                 |
|-------------------|-------------------------------|
| COLD              | `reset -c`                    |
| WARM              | `reset -w`                    |
| SHUTDOWN          | `reset -s`                    |
| PLATFORM_SPECIFIC | `reset -s <hex-data>` (GUID+data as hex) |

Because the Shell `reset` command terminates the shell before returning,
the agent's underlying transport is expected to raise a
connection-class exception once the reset fires; this exception must be
included in `aspects.allowed_connection_exceptions`.

### Redfish reset (resetredfish.py)

Posts to `ComputerSystem.Reset` on `/redfish/v1/Systems/{id}/Actions/ComputerSystem.Reset`
with a body of the form:

```json
{"ResetType": "ForceRestart"}
```

Mapping:

| ResetKind         | Redfish ResetType          |
|-------------------|----------------------------|
| COLD              | `"ForceRestart"`           |
| WARM              | `"GracefulRestart"`        |
| SHUTDOWN          | `"GracefulShutdown"`       |
| PLATFORM_SPECIFIC | caller-supplied via `redfish_reset_type` |

Long-running task (`202 Accepted`) monitors are polled per
`aspects.completion_interval`.

### SSH-to-host reset (resetsshhost.py)

Uses, in order of availability:

1. `efi-reset --kind <cold|warm|shutdown|platform>` — an EDK2 EfiReset
   userspace helper when present.
2. `echo <kind> | sudo tee /sys/firmware/efi/reset_type` followed by
   `sudo reboot` / `sudo poweroff` — kernel fallback.
3. `sudo systemctl reboot` / `sudo systemctl poweroff` — final fallback
   for cold/shutdown only (warm and platform-specific raise
   `UefiError` if no better mechanism is available).

Capability probe happens once at first call and results cache on the
transport's feature-flag dict (populated by 2-transport-ssh-host).

## Data Models

### `ResetData` packing (UEFI §8.5.1)

For `ResetKind.PLATFORM_SPECIFIC`:

| Offset | Size        | Field               |
|-------:|------------:|---------------------|
| 0      | 16          | ResetSubtypeGuid    |
| 16     | remainder   | CallerResetData     |

For non-platform-specific kinds the spec permits a UCS-2 null-terminated
reason string in `ResetData`; this feature does not use that facility to
avoid UCS-2 encoding complications — the reason is logged locally only.

## Measured Interop Contract

Consumed `aspects` fields:

* `action_pattern` — governs retry behavior of the **reset command
  issue** step; the reconnect-verify loop is its own contract.
* `completion_timeout` — bounds the reconnect-verify loop.  Default 300s
  per Requirement 7.1 (matching `SshAgent.reboot()`).
* `completion_interval` — used by `DO_UNTIL_SUCCESS` command-issue retry.
* `connection_timeout` — used by each `verify_connectivity()` attempt.
* `connection_interval` — sleep between reconnect-verify probes.
* `monitor_delay` — sleep between reset issue and first verify attempt
  (default 30s, again matching `SshAgent.reboot()`'s hard-coded
  `time.sleep(30)`).
* `allowed_connection_exceptions` — exceptions tolerated during
  reconnect-verify and during the reset-command-issue step (because the
  shell reset command necessarily severs the connection).
* `allowed_error_codes` — allowed exit codes for the reset command
  before the transport drops.
* `logging_pattern` — routes log emission on channel `uefi.reset`.
* `logger` — injected logger; if `None`, `get_uefi_logger("uefi.reset")`.

Supported `ActionPattern` values for the reset-command-issue step:

* `ActionPattern.SINGLE_CALL` — issue once.
* `ActionPattern.SINGLE_CONNECTED_CALL` — retry connection-class errors.
* `ActionPattern.DO_UNTIL_SUCCESS` — poll until the command is accepted.

`DO_WHILE_SUCCESS` and `DO_UNTIL_CONNECTION_FAILURE` SHALL raise
`SemanticError`.

### Reconnect-verify loop contract (explicit)

Exact algorithm, modeled on `SshAgent.reboot()`:

1. Emit INFO log: `reset issued host=<h> kind=<k> waiting monitor_delay=<d>s`.
2. Compute `start_time = time.time()`, `end_time = start_time + aspects.completion_timeout`.
3. Sleep `aspects.monitor_delay` seconds (default 30).
4. Loop `while time.time() < end_time`:
   a. Increment `attempt_count`.
   b. Call `agent.verify_connectivity(aspects=aspects)`.
      * Verification command per transport:
        * `serial` / `ipmi-sol`: UEFI Shell `echo AUTOMOJO_VERIFY` then
          match prompt pattern within `aspects.connection_timeout`.
        * `redfish`: `GET /redfish/v1/Systems/{id}` returns 200 with a
          `PowerState == "On"` body.
        * `ssh-host`: SSH connect + `echo AUTOMOJO_VERIFY` matching
          output, mirroring `SshAgent.verify_connectivity`.
      * Return `True` on success.
   c. If success → log success, return.
   d. If exception `e` and `type(e) ∈ aspects.allowed_connection_exceptions`
      → record `last_error = e`, continue.
   e. If exception `e` not in allow-list → re-raise immediately.
   f. Sleep `aspects.connection_interval` seconds.
5. On elapse: emit ERROR log, raise `ResetFailedError(host, kind,
   elapsed_seconds, attempt_count, last_error, transport)`.

For `ResetKind.SHUTDOWN` this loop is replaced by a **wait-until-unreachable**
loop that expects `verify_connectivity()` to *fail* (inverse polarity)
within `aspects.completion_timeout`; otherwise `ResetFailedError` is
raised with `details` naming the evidence that the target is still up.

## Error Handling

| Condition                                                        | Exception            | Included in `details`                                        |
|------------------------------------------------------------------|----------------------|--------------------------------------------------------------|
| Non-`ResetKind` `kind` argument                                   | `ConfigurationError` | observed type                                                |
| `PLATFORM_SPECIFIC` without `reset_subtype_guid`                  | `ConfigurationError` | explanation referencing UEFI §8.5.1                          |
| Redfish + `PLATFORM_SPECIFIC` without `redfish_reset_type`        | `ConfigurationError` | explanation                                                  |
| Reset command itself fails (non-connection)                       | `UefiError`          | transport, command, output                                   |
| Reconnect-verify timeout                                          | `ResetFailedError`   | host, kind, elapsed_seconds, attempt_count, last_error, transport |
| Shutdown target still responsive after timeout                    | `ResetFailedError`   | host, kind, last verification evidence                       |
| Unsupported `transport_kind`                                      | `ConfigurationError` | transport_kind, supported list                               |
| Unsupported `ActionPattern`                                       | `SemanticError`      | requested pattern, supported patterns                        |
| Non-allowed exception during reconnect-verify                     | propagated           | as raised                                                    |

## Logging

* Channel: `uefi.reset`.
* INFO: reset issued (host, kind, reason, transport); reset verified
  (elapsed, attempts); shutdown verified unreachable.
* DEBUG: per-attempt verification outcome; redfish `202` task progress;
  monitor-delay sleep start/end.
* WARNING: allowed-connection-class exception observed during the loop.
* ERROR: reconnect-verify elapsed without success, before raise.

Log format selection follows `aspects.logging_pattern`.

## Security Considerations

* **No persisted reset intent.**  This feature does not persist
  "remember to reset on next boot" state; callers who need that use the
  `OsIndications` variable via 5-variable-services.
* **Platform-specific reset opacity.**  `reset_data` is passed through
  verbatim.  Callers are responsible for the correctness of the packed
  payload.  Firmware bugs around oversized `ResetData` have been observed;
  this feature does not apply a size ceiling beyond what the transport
  layer imposes.
* **Redfish credentials.**  The Redfish transport's session token is
  refreshed by the 2-transport-redfish layer; this feature does not log
  tokens.
* **Shutdown safety.**  Shutdown does NOT run the reconnect-verify loop
  (Requirement 4.2).  Instead it inverts the check.  This prevents an
  infinite-loop-on-shutdown-after-poweroff hang.

## Testing Strategy

### Unit tests (`test_resetkind.py`)

* `ResetKind` exposes exactly the four members.
* Non-`ResetKind` input raises `ConfigurationError`.

### Unit tests (`test_verifyloop.py`)

* Success on first attempt → single `verify_connectivity` call.
* Success after N attempts → `N` `verify_connectivity` calls, sleeps
  between each equal to `aspects.connection_interval`.
* Allowed exception during attempt → loop continues.
* Non-allowed exception during attempt → immediate re-raise.
* Timeout elapsed → `ResetFailedError` with populated fields.
* `monitor_delay` is sleep-controlled (use `time.sleep` mock).

### Unit tests per transport

* `test_resetshell.py` — maps `ResetKind` to correct shell command; the
  PLATFORM_SPECIFIC case appends hex-encoded GUID+data.
* `test_resetredfish.py` — maps `ResetKind` to correct Redfish
  `ResetType`; PLATFORM_SPECIFIC requires `redfish_reset_type`.
* `test_resetsshhost.py` — capability probe chooses `efi-reset`,
  `/sys/firmware/efi/reset_type`, or `systemctl` fallbacks in
  documented order; unavailable mechanism raises `UefiError`.

### Simulated-reset integration test (`test_reset_simulated.py`)

A fake transport accepts a reset command, drops the connection, and
re-accepts connections after a configurable delay.  Parametrize:

* `delay=5s, completion_timeout=60s` → success, `attempt_count >= 1`.
* `delay=120s, completion_timeout=60s` → `ResetFailedError` with
  `elapsed_seconds >= 60`.
* Fake transport raises a non-allowed exception mid-loop →
  loop aborts and re-raises.

### Action-pattern tests (`test_actionpatterns.py`)

* `SINGLE_CALL` issues the command once.
* `SINGLE_CONNECTED_CALL` retries only allowed connection exceptions at
  issue time.
* `DO_UNTIL_SUCCESS` retries the command on rejection until accepted.
* `DO_WHILE_SUCCESS` and `DO_UNTIL_CONNECTION_FAILURE` raise
  `SemanticError`.
* `aspects=None` falls back to instance default via `resolve_aspects`.

### Integration test (OVMF)

* Cold reset an OVMF VM, verify Shell returns within
  `aspects.completion_timeout`.
* Shutdown an OVMF VM, verify power-off confirmation.

### Coverage target

≥ 95% line coverage for `verifyloop.py`, `systemreset.py`, the three
transport-specific issue modules.

## Dependencies on Other Features

* 1-aspects — consumes `UefiCmdAspects`, `resolve_aspects`,
  `ActionPattern`.
* 1-exceptions — raises `UefiError`, `ConfigurationError`,
  `SemanticError`, and introduces `ResetFailedError(UefiError)`.
* 1-logging — emits on `uefi.reset` channel.
* 1-constants-and-types — consumes the `uuid.UUID` GUID helpers for
  `reset_subtype_guid` packing.
* 2-transport-serial / 2-transport-ipmi-sol — Shell `reset` command
  dispatch.
* 2-transport-redfish — `ComputerSystem.Reset` POST + task polling.
* 2-transport-ssh-host — OS-mediated reset + capability probe.
* 4-agent — owns `verify_connectivity()`; modeled on `SshAgent.reboot()`
  at `/Users/myron/repos/automojo-protocol-ssh/source/packages/automojo/protocols/ssh/sshagent.py:132-185`.

Downstream workflow features (`6-reboot-and-verify`,
`6-capsule-update-workflow`, `6-boot-to-setup`, `6-boot-to-option-once`)
compose this feature.
