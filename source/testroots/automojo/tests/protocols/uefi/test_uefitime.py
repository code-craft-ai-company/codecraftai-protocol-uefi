"""
    .. module:: test_uefitime
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Unit tests for the :mod:`automojo.protocols.uefi.uefitime`
               module.  Exercises :class:`TimeServices` dispatch for the
               four transport kinds, the shell / hwclock / Redfish output
               parsers, the tz-aware contract enforced by
               :meth:`TimeServices.set_time`, and the round-trip fidelity
               between :meth:`TimeServices.set_time` and
               :meth:`TimeServices.get_time`.

               A minimal :class:`_FakeUefiAgent` stands in for
               :class:`automojo.protocols.uefi.uefiagent.UefiAgent`.  It
               exposes a configurable ``transport_kind`` and a canned
               ``run_cmd`` responder so each test can assert the exact
               command string issued per transport.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Automation Mojo LLC"
__credits__ = []


import datetime
import json
import unittest

from typing import Any, Callable, List, Optional, Tuple

from automojo.errors.exceptions import ConfigurationError

from automojo.protocols.uefi.uefiaspects import UefiCmdAspects
from automojo.protocols.uefi.uefiexceptions import UefiError
from automojo.protocols.uefi.uefitime import (
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

class ParseShellTimeOutputTests(unittest.TestCase):
    """
        Verify :func:`_parse_shell_time_output` across AMI / tianocore
        shell output variations.
    """

    def test_ami_format_is_parsed(self) -> None:
        output = "12:34:56 04/17/2026"
        result = _parse_shell_time_output(stdout=output)
        self.assertEqual(result.year, 2026)
        self.assertEqual(result.month, 4)
        self.assertEqual(result.day, 17)
        self.assertEqual(result.hour, 12)
        self.assertEqual(result.minute, 34)
        self.assertEqual(result.second, 56)
        self.assertIs(result.tzinfo, datetime.timezone.utc)
        return

    def test_reversed_format_is_parsed(self) -> None:
        output = "04/17/2026 12:34:56"
        result = _parse_shell_time_output(stdout=output)
        self.assertEqual(result.year, 2026)
        self.assertEqual(result.month, 4)
        self.assertEqual(result.day, 17)
        self.assertEqual(result.hour, 12)
        self.assertEqual(result.minute, 34)
        self.assertEqual(result.second, 56)
        return

    def test_tolerant_whitespace_is_parsed(self) -> None:
        output = "Shell> time\r\n 01 : 02 : 03  01 / 02 / 2030 \r\nShell>"
        result = _parse_shell_time_output(stdout=output)
        self.assertEqual(result.year, 2030)
        self.assertEqual(result.month, 1)
        self.assertEqual(result.day, 2)
        self.assertEqual(result.hour, 1)
        self.assertEqual(result.minute, 2)
        self.assertEqual(result.second, 3)
        return

    def test_unrecognized_output_raises(self) -> None:
        with self.assertRaises(UefiError):
            _parse_shell_time_output(stdout="no digits here")
        return

    def test_non_string_raises(self) -> None:
        with self.assertRaises(UefiError):
            _parse_shell_time_output(stdout=12345)  # type: ignore[arg-type]
        return


class ParseHwclockIsoOutputTests(unittest.TestCase):
    """
        Verify :func:`_parse_hwclock_iso_output` for classic hwclock
        output shapes.
    """

    def test_classic_output_with_microseconds(self) -> None:
        output = "2026-04-17 12:34:56.123456+00:00"
        result = _parse_hwclock_iso_output(stdout=output)
        self.assertEqual(result.year, 2026)
        self.assertEqual(result.month, 4)
        self.assertEqual(result.day, 17)
        self.assertEqual(result.hour, 12)
        self.assertEqual(result.minute, 34)
        self.assertEqual(result.second, 56)
        self.assertEqual(result.microsecond, 123456)
        self.assertIsNotNone(result.tzinfo)
        self.assertEqual(result.utcoffset(), datetime.timedelta(0))
        return

    def test_output_without_fractional_seconds(self) -> None:
        output = "2026-04-17 12:34:56+00:00"
        result = _parse_hwclock_iso_output(stdout=output)
        self.assertEqual(result.microsecond, 0)
        self.assertIsNotNone(result.tzinfo)
        return

    def test_empty_output_raises(self) -> None:
        with self.assertRaises(UefiError):
            _parse_hwclock_iso_output(stdout="   \n ")
        return

    def test_garbage_output_raises(self) -> None:
        with self.assertRaises(UefiError):
            _parse_hwclock_iso_output(stdout="not-a-date")
        return


class ParseRedfishManagerDatetimeTests(unittest.TestCase):
    """
        Verify :func:`_parse_redfish_manager_datetime` handles Zulu,
        fixed-offset, and naive-plus-offset combinations.
    """

    def test_datetime_with_zulu_suffix(self) -> None:
        body = json.dumps({"DateTime": "2026-04-17T12:34:56Z"})
        result = _parse_redfish_manager_datetime(body_text=body)
        self.assertEqual(result.utcoffset(), datetime.timedelta(0))
        self.assertEqual(result.year, 2026)
        return

    def test_datetime_with_fixed_offset(self) -> None:
        body = json.dumps({"DateTime": "2026-04-17T12:34:56-07:00"})
        result = _parse_redfish_manager_datetime(body_text=body)
        self.assertEqual(result.utcoffset(), datetime.timedelta(hours=-7))
        return

    def test_naive_datetime_plus_offset_field(self) -> None:
        body = json.dumps({
            "DateTime": "2026-04-17T12:34:56",
            "DateTimeLocalOffset": "+05:30",
        })
        result = _parse_redfish_manager_datetime(body_text=body)
        expected_offset = datetime.timedelta(hours=5, minutes=30)
        self.assertEqual(result.utcoffset(), expected_offset)
        return

    def test_missing_offset_raises(self) -> None:
        body = json.dumps({"DateTime": "2026-04-17T12:34:56"})
        with self.assertRaises(UefiError):
            _parse_redfish_manager_datetime(body_text=body)
        return

    def test_missing_datetime_field_raises(self) -> None:
        body = json.dumps({"SomeOtherField": "value"})
        with self.assertRaises(UefiError):
            _parse_redfish_manager_datetime(body_text=body)
        return

    def test_non_json_body_raises(self) -> None:
        with self.assertRaises(UefiError):
            _parse_redfish_manager_datetime(body_text="not json")
        return

    def test_empty_body_raises(self) -> None:
        with self.assertRaises(UefiError):
            _parse_redfish_manager_datetime(body_text="")
        return


class ParseAdjtimexOutputTests(unittest.TestCase):
    """
        Verify :func:`_parse_adjtimex_output` produces sensible
        ``(resolution_hz, accuracy_ppm)`` tuples.
    """

    def test_missing_fields_returns_defaults(self) -> None:
        resolution_hz, accuracy_ppm = _parse_adjtimex_output(stdout="")
        self.assertEqual(resolution_hz, DEFAULT_RTC_RESOLUTION_HZ)
        self.assertEqual(accuracy_ppm, DEFAULT_RTC_ACCURACY_PPM)
        return

    def test_non_string_returns_defaults(self) -> None:
        resolution_hz, accuracy_ppm = _parse_adjtimex_output(stdout=None)  # type: ignore[arg-type]
        self.assertEqual(resolution_hz, DEFAULT_RTC_RESOLUTION_HZ)
        self.assertEqual(accuracy_ppm, DEFAULT_RTC_ACCURACY_PPM)
        return


# ---------------------------------------------------------------------------
# Validation tests
# ---------------------------------------------------------------------------

class ValidateSetTimeInputTests(unittest.TestCase):
    """
        Verify :func:`_validate_set_time_input` enforces the tz-aware
        contract at the API boundary.
    """

    def test_aware_datetime_passes(self) -> None:
        aware_dt = datetime.datetime(2026, 4, 17, 12, 0, 0, tzinfo=datetime.timezone.utc)
        _validate_set_time_input(dt=aware_dt)
        return

    def test_naive_datetime_raises(self) -> None:
        naive_dt = datetime.datetime(2026, 4, 17, 12, 0, 0)
        with self.assertRaises(ConfigurationError):
            _validate_set_time_input(dt=naive_dt)
        return

    def test_non_datetime_raises(self) -> None:
        with self.assertRaises(ConfigurationError):
            _validate_set_time_input(dt="2026-04-17")  # type: ignore[arg-type]
        return

    def test_date_object_raises(self) -> None:
        with self.assertRaises(ConfigurationError):
            _validate_set_time_input(dt=datetime.date(2026, 4, 17))  # type: ignore[arg-type]
        return

    def test_float_raises(self) -> None:
        with self.assertRaises(ConfigurationError):
            _validate_set_time_input(dt=1.0)  # type: ignore[arg-type]
        return


# ---------------------------------------------------------------------------
# get_time dispatch tests
# ---------------------------------------------------------------------------

class GetTimeShellTests(unittest.TestCase):
    """
        Verify :meth:`TimeServices.get_time` over serial / ipmi-sol.
    """

    def test_serial_get_time_parses_output(self) -> None:
        responder = lambda command, aspects: (0, "12:34:56 04/17/2026", "")
        agent = _FakeUefiAgent(transport_kind="serial", responder=responder)
        svc = TimeServices(agent=agent)

        result = svc.get_time()

        self.assertEqual(len(agent.calls), 1)
        command, aspects = agent.calls[0]
        self.assertEqual(command, "time")
        self.assertEqual(result.year, 2026)
        self.assertEqual(result.month, 4)
        self.assertEqual(result.day, 17)
        self.assertIs(result.tzinfo, datetime.timezone.utc)
        return

    def test_ipmi_sol_get_time_parses_output(self) -> None:
        responder = lambda command, aspects: (0, "01:02:03 01/02/2030", "")
        agent = _FakeUefiAgent(transport_kind="ipmi-sol", responder=responder)
        svc = TimeServices(agent=agent)

        result = svc.get_time()
        self.assertEqual(result.year, 2030)
        self.assertEqual(result.hour, 1)
        return

    def test_shell_nonzero_status_raises(self) -> None:
        responder = lambda command, aspects: (1, "", "error")
        agent = _FakeUefiAgent(transport_kind="serial", responder=responder)
        svc = TimeServices(agent=agent)

        with self.assertRaises(UefiError):
            svc.get_time()
        return


class GetTimeHwclockTests(unittest.TestCase):
    """
        Verify :meth:`TimeServices.get_time` over ssh-host.
    """

    def test_ssh_host_get_time_parses_output(self) -> None:
        canned = "2026-04-17 12:34:56.000000+00:00"
        responder = lambda command, aspects: (0, canned, "")
        agent = _FakeUefiAgent(transport_kind="ssh-host", responder=responder)
        svc = TimeServices(agent=agent)

        result = svc.get_time()

        self.assertEqual(len(agent.calls), 1)
        command, aspects = agent.calls[0]
        self.assertEqual(command, "hwclock --show --utc")
        self.assertEqual(result.year, 2026)
        self.assertEqual(result.utcoffset(), datetime.timedelta(0))
        return


class GetTimeRedfishTests(unittest.TestCase):
    """
        Verify :meth:`TimeServices.get_time` over redfish parses the
        ``DateTime`` JSON field.
    """

    def test_redfish_get_time_parses_datetime_field(self) -> None:
        body = json.dumps({
            "DateTime": "2026-04-17T12:34:56+00:00",
            "DateTimeLocalOffset": "+00:00",
        })
        responder = lambda command, aspects: (0, body, "")
        agent = _FakeUefiAgent(transport_kind="redfish", responder=responder, manager_id="BMC1")
        svc = TimeServices(agent=agent)

        result = svc.get_time()

        self.assertEqual(len(agent.calls), 1)
        command, aspects = agent.calls[0]
        self.assertEqual(command, "GET /redfish/v1/Managers/BMC1")
        self.assertEqual(result.year, 2026)
        self.assertEqual(result.utcoffset(), datetime.timedelta(0))
        return

    def test_redfish_default_manager_id_is_self(self) -> None:
        body = json.dumps({"DateTime": "2026-04-17T12:34:56Z"})
        responder = lambda command, aspects: (0, body, "")
        agent = _FakeUefiAgent(transport_kind="redfish", responder=responder)
        svc = TimeServices(agent=agent)

        svc.get_time()
        command, aspects = agent.calls[0]
        expected_command = "GET /redfish/v1/Managers/{}".format(DEFAULT_MANAGER_ID)
        self.assertEqual(command, expected_command)
        return


# ---------------------------------------------------------------------------
# set_time dispatch tests
# ---------------------------------------------------------------------------

class SetTimeValidationTests(unittest.TestCase):
    """
        Verify that :meth:`TimeServices.set_time` enforces the tz-aware
        contract and dispatches no commands when validation fails.
    """

    def test_naive_datetime_raises_configuration_error(self) -> None:
        agent = _FakeUefiAgent(transport_kind="serial")
        svc = TimeServices(agent=agent)

        naive = datetime.datetime(2026, 4, 17, 12, 0, 0)
        with self.assertRaises(ConfigurationError):
            svc.set_time(dt=naive)
        self.assertEqual(len(agent.calls), 0)
        return

    def test_wrong_type_raises_configuration_error(self) -> None:
        agent = _FakeUefiAgent(transport_kind="serial")
        svc = TimeServices(agent=agent)

        with self.assertRaises(ConfigurationError):
            svc.set_time(dt="2026-04-17")  # type: ignore[arg-type]
        self.assertEqual(len(agent.calls), 0)
        return


class SetTimeShellTests(unittest.TestCase):
    """
        Verify :meth:`TimeServices.set_time` issues the expected shell
        commands.
    """

    def test_serial_set_time_issues_time_and_date_commands(self) -> None:
        agent = _FakeUefiAgent(transport_kind="serial")
        svc = TimeServices(agent=agent)

        aware_dt = datetime.datetime(2026, 4, 17, 12, 34, 56, tzinfo=datetime.timezone.utc)
        svc.set_time(dt=aware_dt)

        self.assertEqual(len(agent.calls), 2)
        command_time, _ = agent.calls[0]
        command_date, _ = agent.calls[1]
        self.assertEqual(command_time, "time 12:34:56")
        self.assertEqual(command_date, "date 04/17/2026")
        return

    def test_set_time_converts_non_utc_to_utc(self) -> None:
        agent = _FakeUefiAgent(transport_kind="serial")
        svc = TimeServices(agent=agent)

        offset_tz = datetime.timezone(datetime.timedelta(hours=-5))
        aware_local = datetime.datetime(2026, 4, 17, 7, 34, 56, tzinfo=offset_tz)
        svc.set_time(dt=aware_local)

        command_time, _ = agent.calls[0]
        command_date, _ = agent.calls[1]
        self.assertEqual(command_time, "time 12:34:56")
        self.assertEqual(command_date, "date 04/17/2026")
        return


class SetTimeHwclockTests(unittest.TestCase):
    """
        Verify :meth:`TimeServices.set_time` over ssh-host.
    """

    def test_ssh_host_set_time_issues_hwclock_set(self) -> None:
        agent = _FakeUefiAgent(transport_kind="ssh-host")
        svc = TimeServices(agent=agent)

        aware_dt = datetime.datetime(2026, 4, 17, 12, 34, 56, tzinfo=datetime.timezone.utc)
        svc.set_time(dt=aware_dt)

        self.assertEqual(len(agent.calls), 1)
        command, _ = agent.calls[0]
        expected = "sudo hwclock --set --date '2026-04-17 12:34:56' --utc"
        self.assertEqual(command, expected)
        return


class SetTimeRedfishTests(unittest.TestCase):
    """
        Verify :meth:`TimeServices.set_time` over redfish.
    """

    def test_redfish_set_time_issues_patch_with_iso_body(self) -> None:
        agent = _FakeUefiAgent(transport_kind="redfish", manager_id="BMC1")
        svc = TimeServices(agent=agent)

        aware_dt = datetime.datetime(2026, 4, 17, 12, 34, 56, tzinfo=datetime.timezone.utc)
        svc.set_time(dt=aware_dt)

        self.assertEqual(len(agent.calls), 1)
        command, _ = agent.calls[0]
        self.assertTrue(command.startswith("PATCH /redfish/v1/Managers/BMC1 "))
        body_start = command.index("{")
        decoded = json.loads(command[body_start:])
        self.assertEqual(decoded["DateTime"], "2026-04-17T12:34:56+00:00")
        return


# ---------------------------------------------------------------------------
# Round-trip tests
# ---------------------------------------------------------------------------

class RoundTripTests(unittest.TestCase):
    """
        Verify that :meth:`TimeServices.set_time` followed by
        :meth:`TimeServices.get_time` returns the same moment.
    """

    def test_shell_round_trip(self) -> None:
        initial = datetime.datetime(2026, 1, 1, 0, 0, 0, tzinfo=datetime.timezone.utc)
        agent = _FakeClockAgent(transport_kind="serial", initial=initial)
        svc = TimeServices(agent=agent)

        written = datetime.datetime(2026, 4, 17, 12, 34, 56, tzinfo=datetime.timezone.utc)
        svc.set_time(dt=written)
        read_back = svc.get_time()

        self.assertEqual(read_back, written)
        self.assertIsNotNone(read_back.tzinfo)
        return

    def test_hwclock_round_trip(self) -> None:
        initial = datetime.datetime(2026, 1, 1, 0, 0, 0, tzinfo=datetime.timezone.utc)
        agent = _FakeClockAgent(transport_kind="ssh-host", initial=initial)
        svc = TimeServices(agent=agent)

        written = datetime.datetime(2026, 4, 17, 12, 34, 56, tzinfo=datetime.timezone.utc)
        svc.set_time(dt=written)
        read_back = svc.get_time()

        self.assertEqual(read_back, written)
        return

    def test_redfish_round_trip(self) -> None:
        initial = datetime.datetime(2026, 1, 1, 0, 0, 0, tzinfo=datetime.timezone.utc)
        agent = _FakeClockAgent(transport_kind="redfish", initial=initial)
        svc = TimeServices(agent=agent)

        written = datetime.datetime(2026, 4, 17, 12, 34, 56, tzinfo=datetime.timezone.utc)
        svc.set_time(dt=written)
        read_back = svc.get_time()

        self.assertEqual(read_back, written)
        return

    def test_hwclock_round_trip_with_nonutc_input(self) -> None:
        initial = datetime.datetime(2026, 1, 1, 0, 0, 0, tzinfo=datetime.timezone.utc)
        agent = _FakeClockAgent(transport_kind="ssh-host", initial=initial)
        svc = TimeServices(agent=agent)

        offset_tz = datetime.timezone(datetime.timedelta(hours=-5))
        written_local = datetime.datetime(2026, 4, 17, 7, 34, 56, tzinfo=offset_tz)
        svc.set_time(dt=written_local)
        read_back = svc.get_time()

        self.assertEqual(read_back, written_local.astimezone(datetime.timezone.utc))
        return


# ---------------------------------------------------------------------------
# Dispatch / transport-kind validation tests
# ---------------------------------------------------------------------------

class TransportKindValidationTests(unittest.TestCase):
    """
        Verify that :class:`TimeServices` raises
        :class:`ConfigurationError` for unknown transport kinds.
    """

    def test_unknown_transport_kind_raises_on_get(self) -> None:
        agent = _FakeUefiAgent(transport_kind="nonsense")
        svc = TimeServices(agent=agent)
        with self.assertRaises(ConfigurationError):
            svc.get_time()
        return

    def test_unknown_transport_kind_raises_on_set(self) -> None:
        agent = _FakeUefiAgent(transport_kind="nonsense")
        svc = TimeServices(agent=agent)
        aware_dt = datetime.datetime(2026, 4, 17, 12, 0, 0, tzinfo=datetime.timezone.utc)
        with self.assertRaises(ConfigurationError):
            svc.set_time(dt=aware_dt)
        return


# ---------------------------------------------------------------------------
# Capability tests
# ---------------------------------------------------------------------------

class CapabilityTests(unittest.TestCase):
    """
        Verify :meth:`TimeServices.get_capabilities` across transports.
    """

    def test_serial_returns_default_capabilities(self) -> None:
        agent = _FakeUefiAgent(transport_kind="serial")
        svc = TimeServices(agent=agent)
        caps = svc.get_capabilities()
        self.assertIsInstance(caps, UefiTimeCapabilities)
        self.assertEqual(caps.resolution_hz, DEFAULT_RTC_RESOLUTION_HZ)
        self.assertEqual(caps.accuracy_ppm, DEFAULT_RTC_ACCURACY_PPM)
        return

    def test_redfish_returns_default_capabilities(self) -> None:
        agent = _FakeUefiAgent(transport_kind="redfish")
        svc = TimeServices(agent=agent)
        caps = svc.get_capabilities()
        self.assertEqual(caps.resolution_hz, DEFAULT_RTC_RESOLUTION_HZ)
        return

    def test_ssh_host_missing_adjtimex_returns_defaults(self) -> None:
        responder = lambda command, aspects: (127, "", "command not found")
        agent = _FakeUefiAgent(transport_kind="ssh-host", responder=responder)
        svc = TimeServices(agent=agent)
        caps = svc.get_capabilities()
        self.assertEqual(caps.resolution_hz, DEFAULT_RTC_RESOLUTION_HZ)
        self.assertEqual(caps.accuracy_ppm, DEFAULT_RTC_ACCURACY_PPM)
        return


if __name__ == "__main__":
    unittest.main()
