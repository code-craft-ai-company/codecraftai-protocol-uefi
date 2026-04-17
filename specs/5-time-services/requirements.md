# Requirements — Time Services

## Introduction

The Time Services feature provides typed wrappers around the UEFI
`GetTime` and `SetTime` runtime services (UEFI §8.3) for reading and
setting the real-time clock (RTC).  Primary use cases are RTC-drift
regression testing, wall-clock setup in lab automation, and
capsule/Authenticode timestamp validation.

The feature is transport-aware: over serial / IPMI-SOL it uses the
UEFI Shell `time` command, over Redfish it reads and writes the BMC's
`DateTime` attribute on `/redfish/v1/Managers/{id}`, and over
SSH-to-host it calls `hwclock --show` / `hwclock --set`.  All three
transports produce and consume timezone-aware `datetime` objects;
naive `datetime` inputs are rejected per Requirement 2 to make timezone
bugs loud rather than silent.

This feature depends on the measured-interop aspects contract carried
by `UefiCmdAspects`, the layer-1 exception hierarchy (`UefiError`,
`ConfigurationError`), and the 4-agent action-pattern engine.

## Requirements

### Requirement 1 — `get_time` Returns Timezone-Aware datetime

**User Story:** As a test author, I want `get_time` to return a timezone-
aware `datetime` so that my subsequent arithmetic is unambiguous.

#### Acceptance Criteria

1. WHEN `get_time(aspects)` is called THEN the system SHALL return a
   `datetime` instance whose `tzinfo` is not `None`.
2. WHEN the transport reports a UTC time THEN the system SHALL attach
   `datetime.timezone.utc` as the `tzinfo`.
3. WHEN the transport reports a local time with a named zone (e.g.,
   Redfish `DateTimeLocalOffset`) THEN the system SHALL construct a
   `datetime.timezone(timedelta(minutes=offset))` and attach it.
4. WHEN the transport reports a local time with no zone information
   THEN the system SHALL raise `UefiError` with `details` explaining
   that the transport's output is ambiguous; callers must select a
   transport that preserves timezone.

### Requirement 2 — `set_time` Rejects Naive datetime

**User Story:** As a test author, I want the library to loudly reject
naive `datetime` inputs to `set_time` so that timezone bugs surface at
the API boundary rather than in downstream consumers.

#### Acceptance Criteria

1. WHEN `set_time(dt, aspects)` is called with `dt.tzinfo is None` THEN
   the system SHALL raise `ConfigurationError` with `details` naming
   the offending `datetime` and a remediation hint to attach
   `datetime.timezone.utc`.
2. WHEN `dt.tzinfo` is any `tzinfo`-compatible value THEN the system
   SHALL proceed.
3. IF `dt` is not a `datetime` (e.g., `date`, `str`, `float`) THEN the
   system SHALL raise `ConfigurationError` with `details` naming the
   observed type.

### Requirement 3 — Shell `time` Command Flow

**User Story:** As a test author operating over serial, I want
`get_time` and `set_time` to work through the UEFI Shell `time`
command.

#### Acceptance Criteria

1. WHEN the agent's `transport_kind` is `"serial"` or `"ipmi-sol"` AND
   `get_time` is called THEN the system SHALL execute the Shell
   `time` command, parse its output, and return a timezone-aware
   `datetime` in UTC.
2. WHEN `set_time(dt)` is called on a shell transport THEN the system
   SHALL convert `dt` to UTC and emit `time -d MM/DD/YYYY HH:MM:SS`
   (or the Shell-spec-mandated form).
3. IF the Shell `time` command reports a non-zero status not in
   `aspects.allowed_error_codes` THEN the system SHALL raise
   `UefiShellError` carrying `command`, `status`, `stdout`, `stderr`.

### Requirement 4 — Redfish DateTime Flow

**User Story:** As a test author operating over Redfish, I want
`get_time` and `set_time` to use the BMC's Manager `DateTime`
attribute.

#### Acceptance Criteria

1. WHEN the agent's `transport_kind` is `"redfish"` AND `get_time` is
   called THEN the system SHALL `GET /redfish/v1/Managers/{id}` and
   parse the `DateTime` and `DateTimeLocalOffset` fields per DSP0266.
2. WHEN `set_time(dt)` is called on Redfish THEN the system SHALL
   `PATCH /redfish/v1/Managers/{id}` with a body containing
   `DateTime` in ISO 8601 form (e.g., `2026-04-17T12:34:56+00:00`).
3. IF the Redfish response is a `202 Accepted` long-running task THEN
   the system SHALL poll the TaskMonitor per
   `aspects.completion_interval` until success or
   `aspects.completion_timeout`.
4. IF the BMC rejects the write (4xx / 5xx) THEN the system SHALL
   raise `UefiError` with `details` including the response status
   and body.

### Requirement 5 — SSH-to-Host `hwclock` Flow

**User Story:** As a test author operating OS-side, I want `get_time`
and `set_time` to use `hwclock` on the booted operating system.

#### Acceptance Criteria

1. WHEN the agent's `transport_kind` is `"ssh-host"` AND `get_time` is
   called THEN the system SHALL execute `hwclock --show --utc
   --output-format=iso` and parse the ISO-8601 result as a
   UTC-aware `datetime`.
2. WHEN `set_time(dt)` is called on SSH-to-host THEN the system SHALL
   execute `sudo hwclock --set --date '<iso>' --utc` where `<iso>` is
   the UTC conversion of `dt` formatted as `YYYY-MM-DD HH:MM:SS`.
3. IF `hwclock` is not installed THEN the system SHALL raise
   `UefiError` with `details` listing the missing binary and a
   remediation hint (install `util-linux`).
4. IF `sudo` prompts for a password THEN the system SHALL raise
   `ConfigurationError` per the 2-transport-ssh-host sudo-prompt
   contract.

### Requirement 6 — SINGLE_CALL Action Pattern

**User Story:** As a test author, I want one-shot behavior when I know
the target is responsive.

#### Acceptance Criteria

1. WHEN `get_time` or `set_time` is called with
   `aspects.action_pattern == ActionPattern.SINGLE_CALL` THEN the
   system SHALL execute the read/write exactly once.

### Requirement 7 — SINGLE_CONNECTED_CALL Action Pattern

**User Story:** As a test author on a flaky link, I want retryable
connection failures to be handled automatically.

#### Acceptance Criteria

1. WHEN `get_time` or `set_time` is called with
   `aspects.action_pattern == ActionPattern.SINGLE_CONNECTED_CALL`
   THEN the system SHALL retry only exceptions in
   `aspects.allowed_connection_exceptions`.
2. IF an application error is raised THEN the system SHALL raise
   immediately.

### Requirement 8 — DO_UNTIL_SUCCESS Action Pattern

**User Story:** As a test author operating right after a reboot, I
want the read to poll until the RTC driver is available.

#### Acceptance Criteria

1. WHEN `get_time` is called with
   `aspects.action_pattern == ActionPattern.DO_UNTIL_SUCCESS` THEN the
   system SHALL poll every `aspects.completion_interval` until success
   or `aspects.completion_timeout` elapses.
2. IF the timeout elapses THEN the system SHALL raise `TimeoutError`
   with `details` summarizing the last observed error.

### Requirement 9 — Aspects=None Passthrough

**User Story:** As a caller, I want `aspects=None` to delegate to the
owning agent's instance default.

#### Acceptance Criteria

1. WHEN `get_time` or `set_time` is called with `aspects=None` THEN
   the system SHALL call `resolve_aspects(None, self._aspects)` and
   use the returned instance.
2. IF both the passed `aspects` and the instance default are `None`
   THEN the system SHALL raise `ConfigurationError`.

### Requirement 10 — Round-Trip Fidelity

**User Story:** As a test author validating RTC behavior, I want
`set_time(x)` followed by `get_time()` to return the same moment
(modulo clock drift during the call).

#### Acceptance Criteria

1. WHEN `set_time(x)` returns and `get_time()` is called within
   `aspects.completion_timeout` THEN the returned `datetime` SHALL be
   within a caller-configurable tolerance (`verify_tolerance_seconds`,
   default 2 seconds) of `x`.
2. WHERE the caller passes `verify=True` to `set_time` THEN the
   system SHALL perform a readback and raise `UefiError` if the
   readback is outside the tolerance.
3. WHERE the caller passes `verify=False` (default) THEN the system
   SHALL not perform a readback.

### Requirement 11 — Logging Channel

**User Story:** As an operator, I want time-service activity to log on
a dedicated channel so I can filter it independently.

#### Acceptance Criteria

1. WHEN any time-services API emits a log line THEN the system SHALL
   emit it on the `uefi.time` channel.
2. WHEN `aspects.logging_pattern` is `FAILURE_ONLY` THEN successful
   reads/writes SHALL NOT emit INFO-level log lines.
3. WHEN `aspects.logging_pattern` is `SUCCESS_ONLY` THEN retryable
   failures SHALL NOT emit WARNING-level log lines.

### Requirement 12 — Day-Light-Saving Semantics

**User Story:** As a test author, I want unambiguous DST handling so
that my tests do not silently drift around DST boundaries.

#### Acceptance Criteria

1. WHEN a `datetime` with a DST-aware `tzinfo` (e.g., `zoneinfo.ZoneInfo("US/Pacific")`)
   is passed to `set_time` THEN the system SHALL convert to UTC before
   dispatching to the transport.
2. WHEN `get_time` returns from a transport that reports UTC (shell,
   `hwclock --utc`) THEN the returned `datetime.tzinfo` SHALL be
   `datetime.timezone.utc`, never a local zone.
3. WHERE the caller wants a local-zoned result THEN the caller SHALL
   apply `.astimezone(zone)` on the returned value; this feature SHALL
   NOT perform zone conversion on the caller's behalf.
