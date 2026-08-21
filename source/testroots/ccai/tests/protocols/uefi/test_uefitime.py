"""
    .. module:: test_uefitime
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Unit tests for the :mod:`ccai.protocols.uefi.uefitime`
               module.  Exercises :class:`TimeServices` dispatch for the
               four transport kinds, the shell / hwclock / Redfish output
               parsers, the tz-aware contract enforced by
               :meth:`TimeServices.set_time`, and the round-trip fidelity
               between :meth:`TimeServices.set_time` and
               :meth:`TimeServices.get_time`.

               A minimal :class:`_FakeUefiAgent` stands in for
               :class:`ccai.protocols.uefi.uefiagent.UefiAgent`.  It
               exposes a configurable ``transport_kind`` and a canned
               ``run_cmd`` responder so each test can assert the exact
               command string issued per transport.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Code Craft AI LLC"
__credits__ = []


from ccai import testbase

import datetime
import json

from typing import Any, Callable, List, Optional, Tuple

from ccai.errors.exceptions import ConfigurationError

from ccai.protocols.uefi.uefiaspects import UefiCmdAspects
from ccai.protocols.uefi.uefiexceptions import UefiError
from ccai.protocols.uefi.uefitime import (
    DEFAULT_MANAGER_ID,
    DEFAULT_RTC_ACCURACY_PPM,
    DEFAULT_RTC_RESOLUTION_HZ,
    TimeServices,
    UefiTimeCapabilities,
    _parse_adjtimex_output,
    _parse_hwclock_iso_output,
    _parse_redfish_manager_datetime,
    _parse_shell_time_output,
    _validate_set_time_input,
)


# ---------------------------------------------------------------------------
# Fake UefiAgent
# ---------------------------------------------------------------------------

class _FakeUefiAgent:
    """
        Minimal :class:`UefiAgent` stand-in.  Records every ``run_cmd``
        call and returns canned ``(status, stdout, stderr)`` tuples based
        on a caller-supplied ``responder`` callable.
    """

    def __init__(self, transport_kind: str,
                       responder: Optional[Callable[[str, Optional[UefiCmdAspects]], Tuple[int, str, str]]] = None,
                       manager_id: Optional[str] = None) -> None:
        """
            Construct the fake agent.

            :param transport_kind: Transport kind selector string.
            :param responder: Callable mapping ``(command, aspects)`` to
                              ``(status, stdout, stderr)``.  Defaults to a
                              responder that returns an empty-success
                              tuple.
            :param manager_id: Optional Redfish Manager id.
        """
        self.transport_kind: str = transport_kind
        self.manager_id: Optional[str] = manager_id
        self.calls: List[Tuple[str, Optional[UefiCmdAspects]]] = []

        if responder is None:
            default_responder: Callable[[str, Optional[UefiCmdAspects]], Tuple[int, str, str]] = (
                lambda command, aspects: (0, "", "")
            )
            self._responder = default_responder
        else:
            self._responder = responder
        return

    def run_cmd(self, command: str,
                      exp_status: Any = 0,
                      user: Optional[str] = None,
                      aspects: Optional[UefiCmdAspects] = None) -> Tuple[int, str, str]:
        """
            Record the call and delegate to the canned responder.
        """
        self.calls.append((command, aspects))
        rtnval = self._responder(command, aspects)
        return rtnval


# ---------------------------------------------------------------------------
# Fake in-memory clock
# ---------------------------------------------------------------------------

class _FakeClockAgent(_FakeUefiAgent):
    """
        Fake agent that holds a mutable in-memory UTC clock.  Used for the
        round-trip fidelity test — writes update the clock, reads return
        it through the transport's expected output format.
    """

    def __init__(self, transport_kind: str,
                       initial: datetime.datetime) -> None:
        """
            Construct the fake-clock agent.

            :param transport_kind: Transport kind selector string.
            :param initial: Timezone-aware UTC :class:`datetime.datetime`
                            that seeds the in-memory clock.
        """
        self._clock: datetime.datetime = initial.astimezone(datetime.timezone.utc)

        def _responder(command: str,
                       aspects: Optional[UefiCmdAspects]) -> Tuple[int, str, str]:
            """
                Dispatch canned responses based on the command and the
                current clock value.
            """
            return self._dispatch(command=command)

        _FakeUefiAgent.__init__(
            self,
            transport_kind=transport_kind,
            responder=_responder,
            manager_id="Self",
        )
        return

    def _dispatch(self, command: str) -> Tuple[int, str, str]:
        """
            Decode and apply / answer the command.
        """
        if self.transport_kind in ("serial", "ipmi-sol"):
            if command == "time":
                stdout_text = self._clock.strftime("%H:%M:%S %m/%d/%Y")
                rtnval = (0, stdout_text, "")
                return rtnval
            if command.startswith("time "):
                time_part = command[len("time "):].strip()
                hour_str, minute_str, second_str = time_part.split(":")
                self._clock = self._clock.replace(
                    hour=int(hour_str),
                    minute=int(minute_str),
                    second=int(second_str),
                )
                rtnval = (0, "", "")
                return rtnval
            if command.startswith("date "):
                date_part = command[len("date "):].strip()
                month_str, day_str, year_str = date_part.split("/")
                self._clock = self._clock.replace(
                    year=int(year_str),
                    month=int(month_str),
                    day=int(day_str),
                )
                rtnval = (0, "", "")
                return rtnval

        if self.transport_kind == "ssh-host":
            if command == "hwclock --show --utc":
                stdout_text = self._clock.strftime("%Y-%m-%d %H:%M:%S.000000+00:00")
                rtnval = (0, stdout_text, "")
                return rtnval
            if command.startswith("sudo hwclock --set --date '"):
                opening = command.index("'")
                closing = command.rindex("'")
                iso_str = command[opening + 1:closing]
                parsed = datetime.datetime.strptime(iso_str, "%Y-%m-%d %H:%M:%S")
                self._clock = parsed.replace(tzinfo=datetime.timezone.utc)
                rtnval = (0, "", "")
                return rtnval

        if self.transport_kind == "redfish":
            if command.startswith("GET /redfish/v1/Managers/"):
                body = {
                    "DateTime": self._clock.isoformat(),
                    "DateTimeLocalOffset": "+00:00",
                }
                rtnval = (0, json.dumps(body), "")
                return rtnval
            if command.startswith("PATCH /redfish/v1/Managers/"):
                body_start = command.index("{")
                body_text = command[body_start:]
                decoded = json.loads(body_text)
                iso_text = decoded["DateTime"]
                parsed = datetime.datetime.fromisoformat(iso_text)
                self._clock = parsed.astimezone(datetime.timezone.utc)
                rtnval = (0, "", "")
                return rtnval

        rtnval = (1, "", "unknown command: {}".format(command))
        return rtnval


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# ParseShellTimeOutputTests
# ----------------------------------------------------------------------

"""
    Verify :func:`_parse_shell_time_output` across AMI / tianocore
    shell output variations.
"""

def test_ami_format_is_parsed() -> None:
    output = "12:34:56 04/17/2026"
    result = _parse_shell_time_output(stdout=output)
    testbase.assert_equal(result.year, 2026)
    testbase.assert_equal(result.month, 4)
    testbase.assert_equal(result.day, 17)
    testbase.assert_equal(result.hour, 12)
    testbase.assert_equal(result.minute, 34)
    testbase.assert_equal(result.second, 56)
    testbase.assert_true(result.tzinfo is datetime.timezone.utc)
    return

def test_reversed_format_is_parsed() -> None:
    output = "04/17/2026 12:34:56"
    result = _parse_shell_time_output(stdout=output)
    testbase.assert_equal(result.year, 2026)
    testbase.assert_equal(result.month, 4)
    testbase.assert_equal(result.day, 17)
    testbase.assert_equal(result.hour, 12)
    testbase.assert_equal(result.minute, 34)
    testbase.assert_equal(result.second, 56)
    return

def test_tolerant_whitespace_is_parsed() -> None:
    output = "Shell> time\r\n 01 : 02 : 03  01 / 02 / 2030 \r\nShell>"
    result = _parse_shell_time_output(stdout=output)
    testbase.assert_equal(result.year, 2030)
    testbase.assert_equal(result.month, 1)
    testbase.assert_equal(result.day, 2)
    testbase.assert_equal(result.hour, 1)
    testbase.assert_equal(result.minute, 2)
    testbase.assert_equal(result.second, 3)
    return

def test_unrecognized_output_raises() -> None:
    with testbase.raises(UefiError):
        _parse_shell_time_output(stdout="no digits here")
    return

def test_non_string_raises() -> None:
    with testbase.raises(UefiError):
        _parse_shell_time_output(stdout=12345)  # type: ignore[arg-type]
    return


# ----------------------------------------------------------------------
# ParseHwclockIsoOutputTests
# ----------------------------------------------------------------------

"""
    Verify :func:`_parse_hwclock_iso_output` for classic hwclock
    output shapes.
"""

def test_classic_output_with_microseconds() -> None:
    output = "2026-04-17 12:34:56.123456+00:00"
    result = _parse_hwclock_iso_output(stdout=output)
    testbase.assert_equal(result.year, 2026)
    testbase.assert_equal(result.month, 4)
    testbase.assert_equal(result.day, 17)
    testbase.assert_equal(result.hour, 12)
    testbase.assert_equal(result.minute, 34)
    testbase.assert_equal(result.second, 56)
    testbase.assert_equal(result.microsecond, 123456)
    testbase.assert_true(result.tzinfo is not None)
    testbase.assert_equal(result.utcoffset(), datetime.timedelta(0))
    return

def test_output_without_fractional_seconds() -> None:
    output = "2026-04-17 12:34:56+00:00"
    result = _parse_hwclock_iso_output(stdout=output)
    testbase.assert_equal(result.microsecond, 0)
    testbase.assert_true(result.tzinfo is not None)
    return

def test_empty_output_raises() -> None:
    with testbase.raises(UefiError):
        _parse_hwclock_iso_output(stdout="   \n ")
    return

def test_garbage_output_raises() -> None:
    with testbase.raises(UefiError):
        _parse_hwclock_iso_output(stdout="not-a-date")
    return


# ----------------------------------------------------------------------
# ParseRedfishManagerDatetimeTests
# ----------------------------------------------------------------------

"""
    Verify :func:`_parse_redfish_manager_datetime` handles Zulu,
    fixed-offset, and naive-plus-offset combinations.
"""

def test_datetime_with_zulu_suffix() -> None:
    body = json.dumps({"DateTime": "2026-04-17T12:34:56Z"})
    result = _parse_redfish_manager_datetime(body_text=body)
    testbase.assert_equal(result.utcoffset(), datetime.timedelta(0))
    testbase.assert_equal(result.year, 2026)
    return

def test_datetime_with_fixed_offset() -> None:
    body = json.dumps({"DateTime": "2026-04-17T12:34:56-07:00"})
    result = _parse_redfish_manager_datetime(body_text=body)
    testbase.assert_equal(result.utcoffset(), datetime.timedelta(hours=-7))
    return

def test_naive_datetime_plus_offset_field() -> None:
    body = json.dumps({
        "DateTime": "2026-04-17T12:34:56",
        "DateTimeLocalOffset": "+05:30",
    })
    result = _parse_redfish_manager_datetime(body_text=body)
    expected_offset = datetime.timedelta(hours=5, minutes=30)
    testbase.assert_equal(result.utcoffset(), expected_offset)
    return

def test_missing_offset_raises() -> None:
    body = json.dumps({"DateTime": "2026-04-17T12:34:56"})
    with testbase.raises(UefiError):
        _parse_redfish_manager_datetime(body_text=body)
    return

def test_missing_datetime_field_raises() -> None:
    body = json.dumps({"SomeOtherField": "value"})
    with testbase.raises(UefiError):
        _parse_redfish_manager_datetime(body_text=body)
    return

def test_non_json_body_raises() -> None:
    with testbase.raises(UefiError):
        _parse_redfish_manager_datetime(body_text="not json")
    return

def test_empty_body_raises() -> None:
    with testbase.raises(UefiError):
        _parse_redfish_manager_datetime(body_text="")
    return


# ----------------------------------------------------------------------
# ParseAdjtimexOutputTests
# ----------------------------------------------------------------------

"""
    Verify :func:`_parse_adjtimex_output` produces sensible
    ``(resolution_hz, accuracy_ppm)`` tuples.
"""

def test_missing_fields_returns_defaults() -> None:
    resolution_hz, accuracy_ppm = _parse_adjtimex_output(stdout="")
    testbase.assert_equal(resolution_hz, DEFAULT_RTC_RESOLUTION_HZ)
    testbase.assert_equal(accuracy_ppm, DEFAULT_RTC_ACCURACY_PPM)
    return

def test_non_string_returns_defaults() -> None:
    resolution_hz, accuracy_ppm = _parse_adjtimex_output(stdout=None)  # type: ignore[arg-type]
    testbase.assert_equal(resolution_hz, DEFAULT_RTC_RESOLUTION_HZ)
    testbase.assert_equal(accuracy_ppm, DEFAULT_RTC_ACCURACY_PPM)
    return


# ---------------------------------------------------------------------------
# Validation tests
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# ValidateSetTimeInputTests
# ----------------------------------------------------------------------

"""
    Verify :func:`_validate_set_time_input` enforces the tz-aware
    contract at the API boundary.
"""

def test_aware_datetime_passes() -> None:
    aware_dt = datetime.datetime(2026, 4, 17, 12, 0, 0, tzinfo=datetime.timezone.utc)
    _validate_set_time_input(dt=aware_dt)
    return

def test_naive_datetime_raises() -> None:
    naive_dt = datetime.datetime(2026, 4, 17, 12, 0, 0)
    with testbase.raises(ConfigurationError):
        _validate_set_time_input(dt=naive_dt)
    return

def test_non_datetime_raises() -> None:
    with testbase.raises(ConfigurationError):
        _validate_set_time_input(dt="2026-04-17")  # type: ignore[arg-type]
    return

def test_date_object_raises() -> None:
    with testbase.raises(ConfigurationError):
        _validate_set_time_input(dt=datetime.date(2026, 4, 17))  # type: ignore[arg-type]
    return

def test_float_raises() -> None:
    with testbase.raises(ConfigurationError):
        _validate_set_time_input(dt=1.0)  # type: ignore[arg-type]
    return


# ---------------------------------------------------------------------------
# get_time dispatch tests
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# GetTimeShellTests
# ----------------------------------------------------------------------

"""
    Verify :meth:`TimeServices.get_time` over serial / ipmi-sol.
"""

def test_serial_get_time_parses_output() -> None:
    responder = lambda command, aspects: (0, "12:34:56 04/17/2026", "")
    agent = _FakeUefiAgent(transport_kind="serial", responder=responder)
    svc = TimeServices(agent=agent)

    result = svc.get_time()

    testbase.assert_equal(len(agent.calls), 1)
    command, aspects = agent.calls[0]
    testbase.assert_equal(command, "time")
    testbase.assert_equal(result.year, 2026)
    testbase.assert_equal(result.month, 4)
    testbase.assert_equal(result.day, 17)
    testbase.assert_true(result.tzinfo is datetime.timezone.utc)
    return

def test_ipmi_sol_get_time_parses_output() -> None:
    responder = lambda command, aspects: (0, "01:02:03 01/02/2030", "")
    agent = _FakeUefiAgent(transport_kind="ipmi-sol", responder=responder)
    svc = TimeServices(agent=agent)

    result = svc.get_time()
    testbase.assert_equal(result.year, 2030)
    testbase.assert_equal(result.hour, 1)
    return

def test_shell_nonzero_status_raises() -> None:
    responder = lambda command, aspects: (1, "", "error")
    agent = _FakeUefiAgent(transport_kind="serial", responder=responder)
    svc = TimeServices(agent=agent)

    with testbase.raises(UefiError):
        svc.get_time()
    return


# ----------------------------------------------------------------------
# GetTimeHwclockTests
# ----------------------------------------------------------------------

"""
    Verify :meth:`TimeServices.get_time` over ssh-host.
"""

def test_ssh_host_get_time_parses_output() -> None:
    canned = "2026-04-17 12:34:56.000000+00:00"
    responder = lambda command, aspects: (0, canned, "")
    agent = _FakeUefiAgent(transport_kind="ssh-host", responder=responder)
    svc = TimeServices(agent=agent)

    result = svc.get_time()

    testbase.assert_equal(len(agent.calls), 1)
    command, aspects = agent.calls[0]
    testbase.assert_equal(command, "hwclock --show --utc")
    testbase.assert_equal(result.year, 2026)
    testbase.assert_equal(result.utcoffset(), datetime.timedelta(0))
    return


# ----------------------------------------------------------------------
# GetTimeRedfishTests
# ----------------------------------------------------------------------

"""
    Verify :meth:`TimeServices.get_time` over redfish parses the
    ``DateTime`` JSON field.
"""

def test_redfish_get_time_parses_datetime_field() -> None:
    body = json.dumps({
        "DateTime": "2026-04-17T12:34:56+00:00",
        "DateTimeLocalOffset": "+00:00",
    })
    responder = lambda command, aspects: (0, body, "")
    agent = _FakeUefiAgent(transport_kind="redfish", responder=responder, manager_id="BMC1")
    svc = TimeServices(agent=agent)

    result = svc.get_time()

    testbase.assert_equal(len(agent.calls), 1)
    command, aspects = agent.calls[0]
    testbase.assert_equal(command, "GET /redfish/v1/Managers/BMC1")
    testbase.assert_equal(result.year, 2026)
    testbase.assert_equal(result.utcoffset(), datetime.timedelta(0))
    return

def test_redfish_default_manager_id_is_self() -> None:
    body = json.dumps({"DateTime": "2026-04-17T12:34:56Z"})
    responder = lambda command, aspects: (0, body, "")
    agent = _FakeUefiAgent(transport_kind="redfish", responder=responder)
    svc = TimeServices(agent=agent)

    svc.get_time()
    command, aspects = agent.calls[0]
    expected_command = "GET /redfish/v1/Managers/{}".format(DEFAULT_MANAGER_ID)
    testbase.assert_equal(command, expected_command)
    return


# ---------------------------------------------------------------------------
# set_time dispatch tests
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# SetTimeValidationTests
# ----------------------------------------------------------------------

"""
    Verify that :meth:`TimeServices.set_time` enforces the tz-aware
    contract and dispatches no commands when validation fails.
"""

def test_naive_datetime_raises_configuration_error() -> None:
    agent = _FakeUefiAgent(transport_kind="serial")
    svc = TimeServices(agent=agent)

    naive = datetime.datetime(2026, 4, 17, 12, 0, 0)
    with testbase.raises(ConfigurationError):
        svc.set_time(dt=naive)
    testbase.assert_equal(len(agent.calls), 0)
    return

def test_wrong_type_raises_configuration_error() -> None:
    agent = _FakeUefiAgent(transport_kind="serial")
    svc = TimeServices(agent=agent)

    with testbase.raises(ConfigurationError):
        svc.set_time(dt="2026-04-17")  # type: ignore[arg-type]
    testbase.assert_equal(len(agent.calls), 0)
    return


# ----------------------------------------------------------------------
# SetTimeShellTests
# ----------------------------------------------------------------------

"""
    Verify :meth:`TimeServices.set_time` issues the expected shell
    commands.
"""

def test_serial_set_time_issues_time_and_date_commands() -> None:
    agent = _FakeUefiAgent(transport_kind="serial")
    svc = TimeServices(agent=agent)

    aware_dt = datetime.datetime(2026, 4, 17, 12, 34, 56, tzinfo=datetime.timezone.utc)
    svc.set_time(dt=aware_dt)

    testbase.assert_equal(len(agent.calls), 2)
    command_time, _ = agent.calls[0]
    command_date, _ = agent.calls[1]
    testbase.assert_equal(command_time, "time 12:34:56")
    testbase.assert_equal(command_date, "date 04/17/2026")
    return

def test_set_time_converts_non_utc_to_utc() -> None:
    agent = _FakeUefiAgent(transport_kind="serial")
    svc = TimeServices(agent=agent)

    offset_tz = datetime.timezone(datetime.timedelta(hours=-5))
    aware_local = datetime.datetime(2026, 4, 17, 7, 34, 56, tzinfo=offset_tz)
    svc.set_time(dt=aware_local)

    command_time, _ = agent.calls[0]
    command_date, _ = agent.calls[1]
    testbase.assert_equal(command_time, "time 12:34:56")
    testbase.assert_equal(command_date, "date 04/17/2026")
    return


# ----------------------------------------------------------------------
# SetTimeHwclockTests
# ----------------------------------------------------------------------

"""
    Verify :meth:`TimeServices.set_time` over ssh-host.
"""

def test_ssh_host_set_time_issues_hwclock_set() -> None:
    agent = _FakeUefiAgent(transport_kind="ssh-host")
    svc = TimeServices(agent=agent)

    aware_dt = datetime.datetime(2026, 4, 17, 12, 34, 56, tzinfo=datetime.timezone.utc)
    svc.set_time(dt=aware_dt)

    testbase.assert_equal(len(agent.calls), 1)
    command, _ = agent.calls[0]
    expected = "sudo hwclock --set --date '2026-04-17 12:34:56' --utc"
    testbase.assert_equal(command, expected)
    return


# ----------------------------------------------------------------------
# SetTimeRedfishTests
# ----------------------------------------------------------------------

"""
    Verify :meth:`TimeServices.set_time` over redfish.
"""

def test_redfish_set_time_issues_patch_with_iso_body() -> None:
    agent = _FakeUefiAgent(transport_kind="redfish", manager_id="BMC1")
    svc = TimeServices(agent=agent)

    aware_dt = datetime.datetime(2026, 4, 17, 12, 34, 56, tzinfo=datetime.timezone.utc)
    svc.set_time(dt=aware_dt)

    testbase.assert_equal(len(agent.calls), 1)
    command, _ = agent.calls[0]
    testbase.assert_true(command.startswith("PATCH /redfish/v1/Managers/BMC1 "))
    body_start = command.index("{")
    decoded = json.loads(command[body_start:])
    testbase.assert_equal(decoded["DateTime"], "2026-04-17T12:34:56+00:00")
    return


# ---------------------------------------------------------------------------
# Round-trip tests
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# RoundTripTests
# ----------------------------------------------------------------------

"""
    Verify that :meth:`TimeServices.set_time` followed by
    :meth:`TimeServices.get_time` returns the same moment.
"""

def test_shell_round_trip() -> None:
    initial = datetime.datetime(2026, 1, 1, 0, 0, 0, tzinfo=datetime.timezone.utc)
    agent = _FakeClockAgent(transport_kind="serial", initial=initial)
    svc = TimeServices(agent=agent)

    written = datetime.datetime(2026, 4, 17, 12, 34, 56, tzinfo=datetime.timezone.utc)
    svc.set_time(dt=written)
    read_back = svc.get_time()

    testbase.assert_equal(read_back, written)
    testbase.assert_true(read_back.tzinfo is not None)
    return

def test_hwclock_round_trip() -> None:
    initial = datetime.datetime(2026, 1, 1, 0, 0, 0, tzinfo=datetime.timezone.utc)
    agent = _FakeClockAgent(transport_kind="ssh-host", initial=initial)
    svc = TimeServices(agent=agent)

    written = datetime.datetime(2026, 4, 17, 12, 34, 56, tzinfo=datetime.timezone.utc)
    svc.set_time(dt=written)
    read_back = svc.get_time()

    testbase.assert_equal(read_back, written)
    return

def test_redfish_round_trip() -> None:
    initial = datetime.datetime(2026, 1, 1, 0, 0, 0, tzinfo=datetime.timezone.utc)
    agent = _FakeClockAgent(transport_kind="redfish", initial=initial)
    svc = TimeServices(agent=agent)

    written = datetime.datetime(2026, 4, 17, 12, 34, 56, tzinfo=datetime.timezone.utc)
    svc.set_time(dt=written)
    read_back = svc.get_time()

    testbase.assert_equal(read_back, written)
    return

def test_hwclock_round_trip_with_nonutc_input() -> None:
    initial = datetime.datetime(2026, 1, 1, 0, 0, 0, tzinfo=datetime.timezone.utc)
    agent = _FakeClockAgent(transport_kind="ssh-host", initial=initial)
    svc = TimeServices(agent=agent)

    offset_tz = datetime.timezone(datetime.timedelta(hours=-5))
    written_local = datetime.datetime(2026, 4, 17, 7, 34, 56, tzinfo=offset_tz)
    svc.set_time(dt=written_local)
    read_back = svc.get_time()

    testbase.assert_equal(read_back, written_local.astimezone(datetime.timezone.utc))
    return


# ---------------------------------------------------------------------------
# Dispatch / transport-kind validation tests
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# TransportKindValidationTests
# ----------------------------------------------------------------------

"""
    Verify that :class:`TimeServices` raises
    :class:`ConfigurationError` for unknown transport kinds.
"""

def test_unknown_transport_kind_raises_on_get() -> None:
    agent = _FakeUefiAgent(transport_kind="nonsense")
    svc = TimeServices(agent=agent)
    with testbase.raises(ConfigurationError):
        svc.get_time()
    return

def test_unknown_transport_kind_raises_on_set() -> None:
    agent = _FakeUefiAgent(transport_kind="nonsense")
    svc = TimeServices(agent=agent)
    aware_dt = datetime.datetime(2026, 4, 17, 12, 0, 0, tzinfo=datetime.timezone.utc)
    with testbase.raises(ConfigurationError):
        svc.set_time(dt=aware_dt)
    return


# ---------------------------------------------------------------------------
# Capability tests
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# CapabilityTests
# ----------------------------------------------------------------------

"""
    Verify :meth:`TimeServices.get_capabilities` across transports.
"""

def test_serial_returns_default_capabilities() -> None:
    agent = _FakeUefiAgent(transport_kind="serial")
    svc = TimeServices(agent=agent)
    caps = svc.get_capabilities()
    testbase.assert_true(isinstance(caps, UefiTimeCapabilities))
    testbase.assert_equal(caps.resolution_hz, DEFAULT_RTC_RESOLUTION_HZ)
    testbase.assert_equal(caps.accuracy_ppm, DEFAULT_RTC_ACCURACY_PPM)
    return

def test_redfish_returns_default_capabilities() -> None:
    agent = _FakeUefiAgent(transport_kind="redfish")
    svc = TimeServices(agent=agent)
    caps = svc.get_capabilities()
    testbase.assert_equal(caps.resolution_hz, DEFAULT_RTC_RESOLUTION_HZ)
    return

def test_ssh_host_missing_adjtimex_returns_defaults() -> None:
    responder = lambda command, aspects: (127, "", "command not found")
    agent = _FakeUefiAgent(transport_kind="ssh-host", responder=responder)
    svc = TimeServices(agent=agent)
    caps = svc.get_capabilities()
    testbase.assert_equal(caps.resolution_hz, DEFAULT_RTC_RESOLUTION_HZ)
    testbase.assert_equal(caps.accuracy_ppm, DEFAULT_RTC_ACCURACY_PPM)
    return
