# Design — Time Services

## Overview

Time Services wraps UEFI §8.3 `GetTime` / `SetTime` in a typed Python
facade.  Three transport backends are supported: UEFI Shell `time`
command (serial / IPMI-SOL), Redfish `Managers/{id}.DateTime` (Redfish),
and `hwclock --show` / `hwclock --set` (SSH-to-host).  All three backends
produce and consume timezone-aware `datetime.datetime` objects; naive
inputs are rejected at the API boundary.

The feature is small: two public methods on a `TimeServices` facade and
three transport backends.  The most interesting design decision is the
strict timezone contract — `set_time` requires `tzinfo is not None` and
`get_time` always returns an aware `datetime` — so that DST boundaries
and transport-reported-local-time ambiguities surface at the library
edge.

## Architecture

```
ccai.protocols.uefi.time/
    __init__.py           # re-exports TimeServices
    timeservices.py       # TimeServices facade
    shellbackend.py       # Shell `time` command backend
    redfishbackend.py     # Redfish Managers.DateTime backend
    hwclockbackend.py     # SSH-to-host hwclock backend
    parsers.py            # Shared ISO-8601 / Shell-time parsers
```

Dependency direction:

```
parsers.py ── shellbackend.py ───┐
           ── redfishbackend.py ─┼── timeservices.py
           ── hwclockbackend.py ─┘
```

## Components and Interfaces

### `TimeServices` facade (timeservices.py)

```python
class TimeServices:
    """
        UEFI RTC read/write facade spanning Shell / Redfish / hwclock
        transports.
    """

    def __init__(
        self,
        agent: "UefiAgent",
        aspects: Optional[UefiCmdAspects] = None,
    ) -> None:
        self._agent = agent
        self._aspects = aspects
        return

    def get_time(
        self,
        aspects: Optional[UefiCmdAspects] = None,
    ) -> datetime:
        """
            Read the UEFI RTC.

            :param aspects: Optional aspects override.

            :returns: Timezone-aware datetime.
            :raises: UefiError, UefiShellError, TimeoutError
        """
        effective = resolve_aspects(aspects, self._aspects)
        rtnval = _dispatch_get_time(agent=self._agent, aspects=effective)
        return rtnval

    def set_time(
        self,
        dt: datetime,
        verify: bool = False,
        verify_tolerance_seconds: float = 2.0,
        aspects: Optional[UefiCmdAspects] = None,
    ) -> None:
        """
            Set the UEFI RTC.

            :param dt: Timezone-aware datetime to set.
            :param verify: If True, read back and validate within
                tolerance.
            :param verify_tolerance_seconds: Readback tolerance.
            :param aspects: Optional aspects override.

            :raises: ConfigurationError, UefiError, UefiShellError
        """
        _validate_set_time_input(dt=dt)
        effective = resolve_aspects(aspects, self._aspects)
        _dispatch_set_time(agent=self._agent, dt=dt, aspects=effective)
        if verify is True:
            _verify_readback(
                agent=self._agent,
                written=dt,
                tolerance_seconds=verify_tolerance_seconds,
                aspects=effective,
            )
        return
```

### Dispatch (timeservices.py)

```python
def _dispatch_get_time(
    agent: "UefiAgent",
    aspects: UefiCmdAspects,
) -> datetime:
    if agent.transport_kind in ("serial", "ipmi-sol"):
        rtnval = _get_time_shell(agent=agent, aspects=aspects)
    elif agent.transport_kind == "redfish":
        rtnval = _get_time_redfish(agent=agent, aspects=aspects)
    elif agent.transport_kind == "ssh-host":
        rtnval = _get_time_hwclock(agent=agent, aspects=aspects)
    else:
        err_msg = f"Time services not supported for transport_kind={agent.transport_kind!r}."
        raise ConfigurationError(err_msg)
    return rtnval
```

`_dispatch_set_time` follows the same pattern.

### Input validation (timeservices.py)

```python
def _validate_set_time_input(dt: object) -> None:
    if isinstance(dt, datetime) is False:
        err_msg_lines = ["set_time: argument is not a datetime."]
        err_msg_lines.append(f"OBSERVED_TYPE: {type(dt).__name__}")
        err_msg = os.linesep.join(err_msg_lines)
        raise ConfigurationError(err_msg, details=[f"observed_type={type(dt).__name__}"])

    if dt.tzinfo is None:
        err_msg_lines = ["set_time: naive datetime not accepted."]
        err_msg_lines.append(f"OFFENDING: {dt!r}")
        err_msg_lines.append("REMEDIATION: attach datetime.timezone.utc (or another tzinfo).")
        err_msg = os.linesep.join(err_msg_lines)
        raise ConfigurationError(err_msg, details=[f"offending={dt!r}"])

    return
```

### Shell backend (shellbackend.py)

```python
def _get_time_shell(
    agent: "UefiAgent",
    aspects: UefiCmdAspects,
) -> datetime:
    status, stdout, stderr = agent.run_cmd(command="time", aspects=aspects)
    rtnval = _parse_shell_time_output(stdout=stdout)
    return rtnval


def _set_time_shell(
    agent: "UefiAgent",
    dt: datetime,
    aspects: UefiCmdAspects,
) -> None:
    utc = dt.astimezone(timezone.utc)
    formatted = utc.strftime("%m/%d/%Y %H:%M:%S")
    status, stdout, stderr = agent.run_cmd(
        command=f"time -d {formatted}",
        aspects=aspects,
    )
    return
```

`_parse_shell_time_output` recognizes the Shell `time` command output
(format documented in the UEFI Shell Specification) and returns a
UTC-aware `datetime`.

### Redfish backend (redfishbackend.py)

```python
def _get_time_redfish(
    agent: "UefiAgent",
    aspects: UefiCmdAspects,
) -> datetime:
    body = agent.redfish_get(path=f"/redfish/v1/Managers/{agent.manager_id}", aspects=aspects)
    rtnval = _parse_redfish_datetime(body=body)
    return rtnval


def _set_time_redfish(
    agent: "UefiAgent",
    dt: datetime,
    aspects: UefiCmdAspects,
) -> None:
    iso = dt.isoformat()
    payload = {"DateTime": iso}
    agent.redfish_patch(
        path=f"/redfish/v1/Managers/{agent.manager_id}",
        body=payload,
        aspects=aspects,
    )
    return
```

`_parse_redfish_datetime` reads `body["DateTime"]` and attaches either
`timezone.utc` (when the ISO string ends with `Z` or `+00:00`) or a
fixed-offset `timezone(timedelta(minutes=offset))` parsed from
`DateTimeLocalOffset`.

### SSH-to-host hwclock backend (hwclockbackend.py)

```python
def _get_time_hwclock(
    agent: "UefiAgent",
    aspects: UefiCmdAspects,
) -> datetime:
    status, stdout, stderr = agent.run_cmd(
        command="hwclock --show --utc --output-format=iso",
        aspects=aspects,
    )
    rtnval = _parse_iso_utc(text=stdout.strip())
    return rtnval


def _set_time_hwclock(
    agent: "UefiAgent",
    dt: datetime,
    aspects: UefiCmdAspects,
) -> None:
    utc = dt.astimezone(timezone.utc)
    iso = utc.strftime("%Y-%m-%d %H:%M:%S")
    command = f"sudo hwclock --set --date '{iso}' --utc"
    status, stdout, stderr = agent.run_cmd(command=command, aspects=aspects, as_root=True)
    return
```

### Readback verify (timeservices.py)

```python
def _verify_readback(
    agent: "UefiAgent",
    written: datetime,
    tolerance_seconds: float,
    aspects: UefiCmdAspects,
) -> None:
    readback = _dispatch_get_time(agent=agent, aspects=aspects)
    delta = abs((readback - written).total_seconds())
    if delta > tolerance_seconds:
        err_msg_lines = ["RTC readback outside tolerance after set_time."]
        err_msg_lines.append(f"WRITTEN: {written.isoformat()}")
        err_msg_lines.append(f"READ:    {readback.isoformat()}")
        err_msg_lines.append(f"DELTA_S: {delta:.3f}")
        err_msg_lines.append(f"TOLERANCE_S: {tolerance_seconds:.3f}")
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiError(err_msg, details=[
            f"written={written.isoformat()}",
            f"read={readback.isoformat()}",
            f"delta_s={delta:.3f}",
        ])
    return
```

## Data Models

### Shell `time` command output

The UEFI Shell `time` command emits a single line of the form:

```
hh:mm:ss (LOCAL)
```

Parsed as a `datetime.time`; the date is read separately via the
Shell `date` command when the caller invokes `get_time`.  If the Shell
does not implement `date` separately, the parser uses the combined
`time -v` verbose output which includes year/month/day on AMI-derived
shells.

### Redfish `DateTime` / `DateTimeLocalOffset`

Per DSP0266 §Manager schema:

* `DateTime`: ISO 8601 date-time string, e.g., `"2026-04-17T12:34:56+00:00"`.
* `DateTimeLocalOffset`: ISO 8601 offset, e.g., `"+00:00"`.

When `DateTime` already carries a tz offset (all modern BMCs), the
parser uses it directly.  When `DateTime` is zulu (`Z`) and
`DateTimeLocalOffset` is present, the parser attaches
`datetime.timezone.utc`.  Never both sources — `DateTime` wins.

### `hwclock --show --utc --output-format=iso` output

Format: `YYYY-MM-DDThh:mm:ss,fffffffff+hh:mm`.  The parser strips the
fractional seconds if present and reads the trailing offset.

## Measured Interop Contract

Consumed `aspects` fields:

* `action_pattern` — governs retry at the read/write level.
* `completion_timeout`, `completion_interval` — used by
  `DO_UNTIL_SUCCESS` and by Redfish `202 Accepted` task polling.
* `connection_timeout`, `connection_interval`,
  `allowed_connection_exceptions` — consumed by `SINGLE_CONNECTED_CALL`
  retries.
* `allowed_error_codes` — non-zero exit codes from the Shell `time`
  command or `hwclock` in this set are not treated as failures.
* `logging_pattern` — routes log emission on channel `uefi.time`.
* `logger` — injected logger; if `None`, `get_uefi_logger("uefi.time")`.
* `user` (via `UefiCmdAspects`) — passed through to `run_cmd` for
  the SSH-to-host backend.

Supported `ActionPattern` values:

* `ActionPattern.SINGLE_CALL`
* `ActionPattern.SINGLE_CONNECTED_CALL`
* `ActionPattern.DO_UNTIL_SUCCESS`

`DO_WHILE_SUCCESS` and `DO_UNTIL_CONNECTION_FAILURE` SHALL raise
`SemanticError` — they are not meaningful for a short-lived time
read/write.

## Error Handling

| Condition                                                         | Exception            | Included in `details`                                |
|-------------------------------------------------------------------|----------------------|------------------------------------------------------|
| `set_time` with non-`datetime` input                              | `ConfigurationError` | observed type                                        |
| `set_time` with naive `datetime`                                  | `ConfigurationError` | offending repr + remediation hint                    |
| Transport reports unambiguous time with no tz                     | `UefiError`          | transport, raw transport output                      |
| Shell `time` non-zero status not in `allowed_error_codes`         | `UefiShellError`     | command, status, stdout, stderr                      |
| Redfish 4xx / 5xx on PATCH                                        | `UefiError`          | status, response body                                |
| `hwclock` not installed                                           | `UefiError`          | missing binary, install hint                         |
| `sudo` prompts for password                                       | `ConfigurationError` | as raised by 2-transport-ssh-host                    |
| `verify=True` readback outside tolerance                          | `UefiError`          | written, read, delta, tolerance                      |
| Unsupported `transport_kind`                                       | `ConfigurationError` | transport_kind, supported list                       |
| Unsupported `ActionPattern`                                        | `SemanticError`      | requested pattern, supported patterns                |

## Logging

* Channel: `uefi.time`.
* INFO: `get_time` result (ISO timestamp), `set_time` request (ISO
  timestamp), `set_time` readback success.
* DEBUG: raw transport output, parsed fields.
* WARNING: retry attempts in `DO_UNTIL_SUCCESS`, `SINGLE_CONNECTED_CALL`
  connection-class retries.
* ERROR: terminal failures before re-raise.

## Security Considerations

* **No RTC write under runtime protection.**  Some platforms configure
  the RTC as read-only after boot services exit.  This feature raises
  `UefiError` with the transport's rejection evidence rather than
  silently retrying.
* **Authentication-related timestamps.**  Changing the RTC can break
  Secure Boot authenticated-variable timestamp ordering and Kerberos
  tickets on the host OS.  Callers are responsible for restoring time
  after tests; this feature does not auto-restore.
* **DST traps.**  By mandating tz-aware input and returning tz-aware
  output, this feature avoids DST surprises at the API boundary.  Bugs
  in caller zone handling surface immediately.
* **`sudo` password.**  The SSH-to-host backend follows the
  2-transport-ssh-host contract: passwordless `sudo` is required, and
  a prompt raises `ConfigurationError`.

## Testing Strategy

### Unit tests (`test_validation.py`)

* `set_time(naive_dt)` raises `ConfigurationError`.
* `set_time("2026-01-01")` raises `ConfigurationError` (wrong type).
* `set_time(aware_dt)` passes validation.

### Unit tests (`test_parsers.py`)

* Shell `time` output parsing for AMI-derived and tianocore shell
  variants.
* Redfish `DateTime` + `DateTimeLocalOffset` parsing including Zulu,
  positive offset, negative offset.
* `hwclock` ISO output parsing including and excluding fractional
  seconds.

### Unit tests per backend

* `test_shellbackend.py` — fake agent that returns a known `time`
  output; assert UTC `datetime` returned.  `set_time` formats the
  command correctly for a known input.
* `test_redfishbackend.py` — fake agent that returns a canned
  Managers body; assert tz-aware parse.  `set_time` PATCH body is
  ISO 8601 and matches the input.
* `test_hwclockbackend.py` — fake agent that returns known `hwclock`
  output; assert UTC parse.  `set_time` emits the expected `sudo
  hwclock --set` command.

### Round-trip test (`test_roundtrip.py`)

* Fake agent with an in-memory clock: `set_time(x)` → `get_time()` →
  equal within tolerance.
* `verify=True` happy path and out-of-tolerance error path.

### Action-pattern tests (`test_actionpatterns.py`)

* `SINGLE_CALL` — one invocation.
* `SINGLE_CONNECTED_CALL` — retries connection-class.
* `DO_UNTIL_SUCCESS` — polls until success or timeout.
* `DO_WHILE_SUCCESS` / `DO_UNTIL_CONNECTION_FAILURE` raise
  `SemanticError`.
* `aspects=None` falls back to instance default via `resolve_aspects`.

### TZ-handling tests (`test_tzhandling.py`)

* `datetime` with `zoneinfo.ZoneInfo("US/Pacific")` during DST:
  `set_time` converts to UTC correctly.
* `datetime` with fixed offset: same.
* Returned `datetime.tzinfo` is always non-`None` (via parametrized
  test across backends).

### Integration tests

* OVMF VM + Shell: set a known time, read back, assert within tolerance.
* Redfish against a test BMC simulator.
* Linux VM + `hwclock`.

Gate integration tests on env vars.

### Coverage target

≥ 95% line coverage for `timeservices.py`, each backend, and
`parsers.py`.

## Dependencies on Other Features

* 1-aspects — consumes `UefiCmdAspects`, `resolve_aspects`,
  `ActionPattern`.
* 1-exceptions — raises `UefiError`, `UefiShellError`,
  `ConfigurationError`, `SemanticError`.
* 1-logging — emits on `uefi.time` channel.
* 2-transport-serial / 2-transport-ipmi-sol — Shell `time` command.
* 2-transport-redfish — `GET` / `PATCH` on `Managers/{id}`.
* 2-transport-ssh-host — `hwclock` command.
* 4-agent — `run_cmd`, `redfish_get`, `redfish_patch`.

No dependency on other layer-5 features.
