"""
    .. module:: uefitime
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Module providing the :class:`TimeServices` facade and the
               :class:`UefiTimeCapabilities` record for reading and writing
               the UEFI real-time clock (UEFI Specification §8.3 GetTime /
               SetTime).  Three transport backends are dispatched from the
               same facade:

               * ``serial`` / ``ipmi-sol`` — the UEFI Shell ``time``
                 command.  Output of the form ``HH:MM:SS MM/DD/YYYY`` is
                 parsed with a regex.  The returned datetime is attached
                 to :class:`datetime.timezone.utc`.
               * ``ssh-host`` — ``hwclock --show --utc`` on the booted OS.
                 Output is the ISO-8601 form
                 ``YYYY-MM-DD HH:MM:SS.NNNNNN+00:00`` which is parsed with
                 :meth:`datetime.datetime.fromisoformat`.
               * ``redfish`` — ``GET /redfish/v1/Managers/<id>`` returning
                 a JSON document whose ``DateTime`` field is ISO-8601.

               The facade mandates a strict tz-aware contract on both the
               input to :meth:`TimeServices.set_time` (naive datetimes are
               rejected with :class:`ConfigurationError`) and the output of
               :meth:`TimeServices.get_time` (the returned datetime always
               carries a non-``None`` ``tzinfo``).  This surfaces DST and
               transport-reported-local-time ambiguities at the library
               edge rather than in downstream consumers.

               Every public method accepts an ``aspects`` override and
               resolves it via
               :func:`ccai.protocols.uefi.uefiaspects.resolve_aspects`.
               Log lines are emitted on the ``uefi.time`` channel.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Code Craft AI LLC"
__credits__ = []


import datetime
import json
import os
import re

from dataclasses import dataclass
from typing import Any, Optional, Tuple

from ccai.errors.exceptions import ConfigurationError

from ccai.protocols.uefi.uefiaspects import (
    DEFAULT_CMD_ASPECTS,
    UefiCmdAspects,
    resolve_aspects,
)
from ccai.protocols.uefi.uefiexceptions import UefiError
from ccai.protocols.uefi.uefihelpers import (
    datetime_to_efi_time,
    efi_time_to_datetime,
)
from ccai.protocols.uefi.uefilogging import (
    UEFI_TIME_CHANNEL,
    get_uefi_logger,
)


__all__ = [
    "DEFAULT_MANAGER_ID",
    "DEFAULT_RTC_ACCURACY_PPM",
    "DEFAULT_RTC_RESOLUTION_HZ",
    "TimeServices",
    "UefiTimeCapabilities",
]


# ---------------------------------------------------------------------------
# Module constants.  Default RTC capability values used when the transport
# does not report concrete numbers.  Sourced from UEFI §8.3.2 which defines
# a resolution of 1 Hz and an accuracy of 50 ppm as typical RTC values.
# ---------------------------------------------------------------------------

DEFAULT_RTC_RESOLUTION_HZ: int = 1
DEFAULT_RTC_ACCURACY_PPM: int = 50000
DEFAULT_MANAGER_ID: str = "Self"


# ---------------------------------------------------------------------------
# Shell ``time`` command output parser.
#
# The UEFI Shell ``time`` command output varies slightly between AMI-derived
# and tianocore-derived shell implementations but always includes an
# ``HH:MM:SS`` time and ``MM/DD/YYYY`` date block.  The regex is anchored on
# the digit groups so that embedded banner text (e.g., the verbose ``-v``
# flag's timezone display) does not destabilize the parse.
# ---------------------------------------------------------------------------

_SHELL_TIME_RE = re.compile(
    r"(?P<hour>\d{1,2})\s*:\s*(?P<minute>\d{2})\s*:\s*(?P<second>\d{2})"
    r".*?"
    r"(?P<month>\d{1,2})\s*/\s*(?P<day>\d{1,2})\s*/\s*(?P<year>\d{4})",
    re.DOTALL,
)

# Fallback regex that accepts the reversed order ``MM/DD/YYYY HH:MM:SS``
# emitted by some tianocore builds.
_SHELL_TIME_REVERSED_RE = re.compile(
    r"(?P<month>\d{1,2})\s*/\s*(?P<day>\d{1,2})\s*/\s*(?P<year>\d{4})"
    r".*?"
    r"(?P<hour>\d{1,2})\s*:\s*(?P<minute>\d{2})\s*:\s*(?P<second>\d{2})",
    re.DOTALL,
)


# ---------------------------------------------------------------------------
# adjtimex output parser for ssh-host capability discovery.  We look for
# the "frequency" line (PPM adjustment) and the "precision" line that most
# BSD / Linux ``adjtimex`` variants print.
# ---------------------------------------------------------------------------

_ADJTIMEX_PPM_RE = re.compile(r"frequency\s*:\s*([\-\+]?\d+)", re.IGNORECASE)
_ADJTIMEX_PRECISION_RE = re.compile(r"precision\s*:\s*(\d+)", re.IGNORECASE)


# ---------------------------------------------------------------------------
# UefiTimeCapabilities
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class UefiTimeCapabilities:
    """
        Immutable record carrying the three RTC capability fields defined
        by UEFI §8.3.2 ``EFI_TIME_CAPABILITIES``.

        :ivar resolution_hz: The reporting resolution of the RTC in Hz.
        :ivar accuracy_ppm: The accuracy in parts per million.
        :ivar sets_to_zero: ``True`` when setting the time clears sub-second
                            precision, ``False`` otherwise.
    """
    resolution_hz: int
    accuracy_ppm: int
    sets_to_zero: bool


# ---------------------------------------------------------------------------
# Internal parse helpers
# ---------------------------------------------------------------------------

def _parse_shell_time_output(stdout: str) -> datetime.datetime:
    """
        Parse the output of the UEFI Shell ``time`` command and return a
        UTC-aware :class:`datetime.datetime`.  The shell reports its time
        in local wall-clock but without a timezone marker; this parser
        treats the reported time as UTC per the design-decision in
        ``specs/5-time-services/design.md`` and Requirement 12.

        :param stdout: Captured stdout from the ``time`` command.

        :returns: A :class:`datetime.datetime` in :class:`datetime.timezone.utc`.
        :raises: UefiError
    """
    if not isinstance(stdout, str):
        err_msg_lines = [
            "_parse_shell_time_output: 'stdout' must be a str.",
            "OBSERVED_TYPE: {}".format(type(stdout).__name__),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiError(err_msg)

    match = _SHELL_TIME_RE.search(stdout)
    if match is None:
        match = _SHELL_TIME_REVERSED_RE.search(stdout)

    if match is None:
        preview = stdout if len(stdout) <= 512 else stdout[:512] + "..."
        err_msg_lines = [
            "Shell 'time' output did not match any recognized pattern.",
            "OBSERVED:",
            preview,
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiError(err_msg, details=["observed={!r}".format(preview)])

    year = int(match.group("year"))
    month = int(match.group("month"))
    day = int(match.group("day"))
    hour = int(match.group("hour"))
    minute = int(match.group("minute"))
    second = int(match.group("second"))

    try:
        rtnval = datetime.datetime(
            year, month, day, hour, minute, second,
            tzinfo=datetime.timezone.utc,
        )
    except ValueError as verr:
        err_msg_lines = [
            "Shell 'time' output parsed to an invalid calendar date.",
            "YEAR: {}".format(year),
            "MONTH: {}".format(month),
            "DAY: {}".format(day),
            "HOUR: {}".format(hour),
            "MINUTE: {}".format(minute),
            "SECOND: {}".format(second),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiError(err_msg) from verr

    return rtnval


def _parse_hwclock_iso_output(stdout: str) -> datetime.datetime:
    """
        Parse the output of ``hwclock --show --utc`` and return a
        timezone-aware :class:`datetime.datetime`.  The classic ``hwclock``
        format is ``YYYY-MM-DD HH:MM:SS.NNNNNN+00:00`` which
        :meth:`datetime.datetime.fromisoformat` accepts once the leading /
        trailing whitespace is stripped and the separating space is
        replaced with ``T``.

        :param stdout: Captured stdout from ``hwclock --show --utc``.

        :returns: A timezone-aware :class:`datetime.datetime`.
        :raises: UefiError
    """
    if not isinstance(stdout, str):
        err_msg_lines = [
            "_parse_hwclock_iso_output: 'stdout' must be a str.",
            "OBSERVED_TYPE: {}".format(type(stdout).__name__),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiError(err_msg)

    text = stdout.strip()
    if len(text) == 0:
        err_msg_lines = [
            "hwclock output was empty.",
            "OBSERVED: {!r}".format(stdout),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiError(err_msg)

    # datetime.fromisoformat accepts ``YYYY-MM-DD HH:MM:SS`` forms directly
    # in Python 3.11+; we normalize ``T`` separation to keep older Pythons
    # happy as well.
    candidate = text
    space_index = candidate.find(" ")
    if space_index != -1:
        candidate_t = candidate[:space_index] + "T" + candidate[space_index + 1:]
    else:
        candidate_t = candidate

    parse_candidates = (candidate, candidate_t)

    last_error: Optional[ValueError] = None
    parsed: Optional[datetime.datetime] = None
    for candidate_form in parse_candidates:
        try:
            parsed = datetime.datetime.fromisoformat(candidate_form)
            break
        except ValueError as verr:
            last_error = verr
            continue

    if parsed is None:
        err_msg_lines = [
            "hwclock output did not parse as ISO-8601.",
            "OBSERVED: {!r}".format(stdout),
            "LAST_ERROR: {!r}".format(last_error),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiError(err_msg) from last_error

    if parsed.tzinfo is None:
        # hwclock --utc always emits an offset; if we somehow lost it, assume UTC.
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)

    rtnval = parsed
    return rtnval


def _parse_redfish_manager_datetime(body_text: str) -> datetime.datetime:
    """
        Parse the ``DateTime`` field out of a Redfish ``Managers/{id}`` body
        and return a timezone-aware :class:`datetime.datetime`.

        When the ``DateTime`` string ends in ``Z`` the returned tzinfo is
        :class:`datetime.timezone.utc`; when it carries a fixed offset the
        parser uses the offset directly; when the BMC emits a naive
        ``DateTime`` plus a separate ``DateTimeLocalOffset`` field, the
        parser attaches the offset.  When no zone information is present
        at all, the parser raises :class:`UefiError` (Requirement 1, AC 4).

        :param body_text: The JSON body as a string.

        :returns: A timezone-aware :class:`datetime.datetime`.
        :raises: UefiError
    """
    if not isinstance(body_text, str):
        err_msg_lines = [
            "_parse_redfish_manager_datetime: 'body_text' must be a str.",
            "OBSERVED_TYPE: {}".format(type(body_text).__name__),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiError(err_msg)

    stripped = body_text.strip()
    if len(stripped) == 0:
        err_msg_lines = [
            "Redfish Managers body was empty.",
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiError(err_msg)

    try:
        decoded = json.loads(stripped)
    except ValueError as verr:
        err_msg_lines = [
            "Redfish Managers body did not parse as JSON.",
            "OBSERVED_PREFIX: {!r}".format(stripped[:256]),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiError(err_msg) from verr

    if not isinstance(decoded, dict):
        err_msg_lines = [
            "Redfish Managers body is not a JSON object.",
            "OBSERVED_TYPE: {}".format(type(decoded).__name__),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiError(err_msg)

    datetime_value = decoded.get("DateTime")
    if datetime_value is None:
        err_msg_lines = [
            "Redfish Managers body did not contain a 'DateTime' field.",
            "KEYS: {}".format(sorted(decoded.keys())),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiError(err_msg)

    if not isinstance(datetime_value, str):
        err_msg_lines = [
            "Redfish Managers 'DateTime' field is not a string.",
            "OBSERVED_TYPE: {}".format(type(datetime_value).__name__),
            "VALUE: {!r}".format(datetime_value),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiError(err_msg)

    # Normalize trailing Z to +00:00 so fromisoformat accepts it.
    normalized = datetime_value
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"

    try:
        parsed = datetime.datetime.fromisoformat(normalized)
    except ValueError as verr:
        err_msg_lines = [
            "Redfish 'DateTime' did not parse as ISO-8601.",
            "OBSERVED: {!r}".format(datetime_value),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiError(err_msg) from verr

    if parsed.tzinfo is not None:
        rtnval = parsed
        return rtnval

    # No zone on the DateTime field — consult DateTimeLocalOffset.
    offset_value = decoded.get("DateTimeLocalOffset")
    if offset_value is None:
        err_msg_lines = [
            "Redfish 'DateTime' lacks timezone and no 'DateTimeLocalOffset' was present.",
            "HINT: the BMC's Manager record is ambiguous; prefer a BMC that sets 'DateTime' with an explicit offset.",
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiError(err_msg, details=["observed={!r}".format(datetime_value)])

    if not isinstance(offset_value, str):
        err_msg_lines = [
            "Redfish 'DateTimeLocalOffset' is not a string.",
            "OBSERVED_TYPE: {}".format(type(offset_value).__name__),
            "VALUE: {!r}".format(offset_value),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiError(err_msg)

    offset_match = re.match(r"^([\-\+])(\d{2}):(\d{2})$", offset_value.strip())
    if offset_match is None:
        err_msg_lines = [
            "Redfish 'DateTimeLocalOffset' is not in [+|-]HH:MM form.",
            "OBSERVED: {!r}".format(offset_value),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiError(err_msg)

    sign = offset_match.group(1)
    offset_hours = int(offset_match.group(2))
    offset_minutes = int(offset_match.group(3))
    total_minutes = (offset_hours * 60) + offset_minutes
    if sign == "-":
        total_minutes = -total_minutes

    offset_delta = datetime.timedelta(minutes=total_minutes)
    resolved_tz = datetime.timezone(offset_delta)
    rtnval = parsed.replace(tzinfo=resolved_tz)
    return rtnval


def _parse_adjtimex_output(stdout: str) -> Tuple[int, int]:
    """
        Best-effort parse of the output of ``adjtimex --print`` for
        capability discovery over ssh-host.  Returns
        ``(resolution_hz, accuracy_ppm)`` using
        :data:`DEFAULT_RTC_RESOLUTION_HZ` and :data:`DEFAULT_RTC_ACCURACY_PPM`
        when the fields cannot be located.

        :param stdout: Captured output of ``adjtimex --print``.

        :returns: Two-tuple of ints.
    """
    resolution_hz = DEFAULT_RTC_RESOLUTION_HZ
    accuracy_ppm = DEFAULT_RTC_ACCURACY_PPM

    if not isinstance(stdout, str):
        rtnval = (resolution_hz, accuracy_ppm)
        return rtnval

    ppm_match = _ADJTIMEX_PPM_RE.search(stdout)
    if ppm_match is not None:
        # adjtimex reports frequency in units of 2^-16 ppm.  The absolute
        # value gives the accuracy in those units; we coerce to ppm.
        raw_ppm = abs(int(ppm_match.group(1)))
        accuracy_ppm = max(1, raw_ppm // 65536) if raw_ppm > 0 else DEFAULT_RTC_ACCURACY_PPM

    precision_match = _ADJTIMEX_PRECISION_RE.search(stdout)
    if precision_match is not None:
        precision_value = int(precision_match.group(1))
        if precision_value > 0:
            resolution_hz = max(1, 1_000_000 // precision_value)

    rtnval = (resolution_hz, accuracy_ppm)
    return rtnval


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

def _validate_set_time_input(dt: Any) -> None:
    """
        Validate that ``dt`` is a timezone-aware :class:`datetime.datetime`.
        Rejects non-datetime inputs and naive datetimes per Requirement 2.

        :param dt: The candidate value passed to :meth:`TimeServices.set_time`.

        :raises: ConfigurationError
    """
    if not isinstance(dt, datetime.datetime):
        err_msg_lines = [
            "set_time: 'dt' must be a datetime.datetime.",
            "OBSERVED_TYPE: {}".format(type(dt).__name__),
            "OBSERVED_VALUE: {!r}".format(dt),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise ConfigurationError(
            err_msg,
        ) from None

    if dt.tzinfo is None or dt.utcoffset() is None:
        err_msg_lines = [
            "set_time: naive datetime is not accepted.",
            "OFFENDING: {!r}".format(dt),
            "REMEDIATION: attach datetime.timezone.utc (or another tzinfo) to the datetime before calling set_time.",
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise ConfigurationError(err_msg) from None

    return


# ---------------------------------------------------------------------------
# TimeServices facade
# ---------------------------------------------------------------------------

class TimeServices:
    """
        The :class:`TimeServices` facade wraps UEFI §8.3 ``GetTime`` /
        ``SetTime`` behind a transport-aware Python API.  A single instance
        is bound to a :class:`ccai.protocols.uefi.uefiagent.UefiAgent`
        and an instance-default :class:`UefiCmdAspects`; every public
        method accepts a per-call ``aspects`` override.

        :param agent: The :class:`UefiAgent` through which the facade
                      dispatches shell commands and Redfish requests.
        :param aspects: Default :class:`UefiCmdAspects`.
    """

    def __init__(self, agent: Any,
                       aspects: UefiCmdAspects = DEFAULT_CMD_ASPECTS) -> None:
        """
            Initialize the :class:`TimeServices` facade.

            :param agent: The owning :class:`UefiAgent`.
            :param aspects: Default :class:`UefiCmdAspects` for this facade.
        """
        self._agent: Any = agent
        self._aspects: UefiCmdAspects = aspects
        self._logger = get_uefi_logger(UEFI_TIME_CHANNEL)

        transport_kind = getattr(agent, "transport_kind", None)
        self._logger.debug(
            "uefi.time: TimeServices constructed transport_kind=%s",
            transport_kind,
        )
        return

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_time(self, aspects: Optional[UefiCmdAspects] = None) -> datetime.datetime:
        """
            Read the UEFI RTC and return a timezone-aware
            :class:`datetime.datetime`.

            Transport dispatch:

            * ``serial`` / ``ipmi-sol`` — runs the UEFI Shell ``time``
              command and parses its output (``HH:MM:SS MM/DD/YYYY``).
              The returned datetime is attached to
              :class:`datetime.timezone.utc`.
            * ``ssh-host`` — runs ``hwclock --show --utc`` and parses the
              ISO-8601 output with :meth:`datetime.datetime.fromisoformat`.
            * ``redfish`` — ``GET /redfish/v1/Managers/<id>`` and parses
              the ``DateTime`` / ``DateTimeLocalOffset`` fields.

            :param aspects: Optional per-call aspects override.  Falls
                            back to the instance default when ``None``.

            :returns: A timezone-aware :class:`datetime.datetime`.
            :raises: UefiError, ConfigurationError
        """
        effective = resolve_aspects(passed=aspects, instance_default=self._aspects)
        transport_kind = self._require_transport_kind()

        if transport_kind in ("serial", "ipmi-sol"):
            rtnval = self._get_time_shell(aspects=effective)
        elif transport_kind == "ssh-host":
            rtnval = self._get_time_hwclock(aspects=effective)
        elif transport_kind == "redfish":
            rtnval = self._get_time_redfish(aspects=effective)
        else:
            err_msg_lines = [
                "TimeServices.get_time: transport_kind is not supported.",
                "OBSERVED: {!r}".format(transport_kind),
                "ALLOWED: 'serial', 'ipmi-sol', 'ssh-host', 'redfish'",
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg) from None

        self._logger.debug(
            "uefi.time: get_time result transport_kind=%s value=%s",
            transport_kind, rtnval.isoformat(),
        )
        return rtnval

    def set_time(self, dt: datetime.datetime,
                       aspects: Optional[UefiCmdAspects] = None) -> None:
        """
            Set the UEFI RTC.  ``dt`` MUST be timezone-aware; naive
            datetimes raise :class:`ConfigurationError`.

            Transport dispatch:

            * ``serial`` / ``ipmi-sol`` — converts ``dt`` to UTC and
              issues the UEFI Shell ``time HH:MM:SS`` command.
            * ``ssh-host`` — converts ``dt`` to UTC and issues
              ``sudo hwclock --set --date '<iso>' --utc``.
            * ``redfish`` — ``PATCH /redfish/v1/Managers/<id>`` with a
              body of the form ``{"DateTime": "<iso>"}``.

            :param dt: Timezone-aware :class:`datetime.datetime`.
            :param aspects: Optional per-call aspects override.  Falls
                            back to the instance default when ``None``.

            :raises: ConfigurationError, UefiError
        """
        _validate_set_time_input(dt=dt)
        effective = resolve_aspects(passed=aspects, instance_default=self._aspects)
        transport_kind = self._require_transport_kind()

        utc_dt = dt.astimezone(datetime.timezone.utc)

        self._logger.debug(
            "uefi.time: set_time request transport_kind=%s utc=%s",
            transport_kind, utc_dt.isoformat(),
        )

        if transport_kind in ("serial", "ipmi-sol"):
            self._set_time_shell(utc_dt=utc_dt, aspects=effective)
        elif transport_kind == "ssh-host":
            self._set_time_hwclock(utc_dt=utc_dt, aspects=effective)
        elif transport_kind == "redfish":
            self._set_time_redfish(utc_dt=utc_dt, aspects=effective)
        else:
            err_msg_lines = [
                "TimeServices.set_time: transport_kind is not supported.",
                "OBSERVED: {!r}".format(transport_kind),
                "ALLOWED: 'serial', 'ipmi-sol', 'ssh-host', 'redfish'",
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg) from None

        return

    def get_capabilities(self, aspects: Optional[UefiCmdAspects] = None) -> UefiTimeCapabilities:
        """
            Return the RTC capabilities as a :class:`UefiTimeCapabilities`
            record.  ``ssh-host`` parses ``adjtimex --print`` when that
            binary is available; other transports return a best-effort
            default based on UEFI §8.3.2 typical values.

            :param aspects: Optional per-call aspects override.  Falls
                            back to the instance default when ``None``.

            :returns: A :class:`UefiTimeCapabilities` instance.
            :raises: ConfigurationError
        """
        effective = resolve_aspects(passed=aspects, instance_default=self._aspects)
        transport_kind = self._require_transport_kind()

        if transport_kind == "ssh-host":
            rtnval = self._get_capabilities_ssh_host(aspects=effective)
            return rtnval

        if transport_kind in ("serial", "ipmi-sol", "redfish"):
            rtnval = UefiTimeCapabilities(
                resolution_hz=DEFAULT_RTC_RESOLUTION_HZ,
                accuracy_ppm=DEFAULT_RTC_ACCURACY_PPM,
                sets_to_zero=True,
            )
            return rtnval

        err_msg_lines = [
            "TimeServices.get_capabilities: transport_kind is not supported.",
            "OBSERVED: {!r}".format(transport_kind),
            "ALLOWED: 'serial', 'ipmi-sol', 'ssh-host', 'redfish'",
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise ConfigurationError(err_msg) from None

    # ------------------------------------------------------------------
    # Shell / ipmi-sol backend
    # ------------------------------------------------------------------

    def _get_time_shell(self, aspects: UefiCmdAspects) -> datetime.datetime:
        """
            Run the UEFI Shell ``time`` command and parse its output.

            :param aspects: Effective aspects.

            :returns: A UTC-aware :class:`datetime.datetime`.
            :raises: UefiError
        """
        command = "time"
        status, stdout, stderr = self._agent.run_cmd(
            command=command, aspects=aspects,
        )

        if status != 0 and status not in tuple(aspects.allowed_error_codes):
            err_msg_lines = [
                "Shell 'time' command returned a non-zero status.",
                "COMMAND: {}".format(command),
                "STATUS: {}".format(status),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg, details=[
                "command={!r}".format(command),
                "status={}".format(status),
                "stdout={!r}".format(stdout),
                "stderr={!r}".format(stderr),
            ]) from None

        rtnval = _parse_shell_time_output(stdout=stdout if stdout is not None else "")
        return rtnval

    def _set_time_shell(self, utc_dt: datetime.datetime,
                              aspects: UefiCmdAspects) -> None:
        """
            Issue the UEFI Shell ``time HH:MM:SS`` command.  The UEFI Shell
            ``time`` command sets only the time-of-day; the date is set via
            a separate ``date`` command.

            :param utc_dt: UTC-aware :class:`datetime.datetime`.
            :param aspects: Effective aspects.

            :raises: UefiError
        """
        time_str = utc_dt.strftime("%H:%M:%S")
        time_command = "time {}".format(time_str)
        status, stdout, stderr = self._agent.run_cmd(
            command=time_command, aspects=aspects,
        )
        if status != 0 and status not in tuple(aspects.allowed_error_codes):
            err_msg_lines = [
                "Shell 'time' set command returned a non-zero status.",
                "COMMAND: {}".format(time_command),
                "STATUS: {}".format(status),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg, details=[
                "command={!r}".format(time_command),
                "status={}".format(status),
                "stdout={!r}".format(stdout),
                "stderr={!r}".format(stderr),
            ]) from None

        date_str = utc_dt.strftime("%m/%d/%Y")
        date_command = "date {}".format(date_str)
        status, stdout, stderr = self._agent.run_cmd(
            command=date_command, aspects=aspects,
        )
        if status != 0 and status not in tuple(aspects.allowed_error_codes):
            err_msg_lines = [
                "Shell 'date' set command returned a non-zero status.",
                "COMMAND: {}".format(date_command),
                "STATUS: {}".format(status),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg, details=[
                "command={!r}".format(date_command),
                "status={}".format(status),
                "stdout={!r}".format(stdout),
                "stderr={!r}".format(stderr),
            ]) from None

        return

    # ------------------------------------------------------------------
    # ssh-host (hwclock) backend
    # ------------------------------------------------------------------

    def _get_time_hwclock(self, aspects: UefiCmdAspects) -> datetime.datetime:
        """
            Run ``hwclock --show --utc`` on the booted host OS and parse
            the ISO-8601 output.

            :param aspects: Effective aspects.

            :returns: A timezone-aware :class:`datetime.datetime`.
            :raises: UefiError
        """
        command = "hwclock --show --utc"
        status, stdout, stderr = self._agent.run_cmd(
            command=command, aspects=aspects,
        )

        if status != 0 and status not in tuple(aspects.allowed_error_codes):
            err_msg_lines = [
                "hwclock --show returned a non-zero status.",
                "COMMAND: {}".format(command),
                "STATUS: {}".format(status),
                "HINT: ensure util-linux is installed on the target and the hwclock binary is on PATH.",
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg, details=[
                "command={!r}".format(command),
                "status={}".format(status),
                "stdout={!r}".format(stdout),
                "stderr={!r}".format(stderr),
            ]) from None

        rtnval = _parse_hwclock_iso_output(stdout=stdout if stdout is not None else "")
        return rtnval

    def _set_time_hwclock(self, utc_dt: datetime.datetime,
                                aspects: UefiCmdAspects) -> None:
        """
            Issue ``sudo hwclock --set --date '<iso>' --utc`` on the
            booted host OS.  ``utc_dt`` is rendered as
            ``YYYY-MM-DD HH:MM:SS``.

            :param utc_dt: UTC-aware :class:`datetime.datetime`.
            :param aspects: Effective aspects.

            :raises: UefiError
        """
        iso_str = utc_dt.strftime("%Y-%m-%d %H:%M:%S")
        command = "sudo hwclock --set --date '{}' --utc".format(iso_str)

        status, stdout, stderr = self._agent.run_cmd(
            command=command, aspects=aspects,
        )

        if status != 0 and status not in tuple(aspects.allowed_error_codes):
            err_msg_lines = [
                "hwclock --set returned a non-zero status.",
                "COMMAND: {}".format(command),
                "STATUS: {}".format(status),
                "HINT: verify passwordless sudo is configured for the calling user on the target.",
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg, details=[
                "command={!r}".format(command),
                "status={}".format(status),
                "stdout={!r}".format(stdout),
                "stderr={!r}".format(stderr),
            ]) from None

        return

    # ------------------------------------------------------------------
    # Redfish backend
    # ------------------------------------------------------------------

    def _get_time_redfish(self, aspects: UefiCmdAspects) -> datetime.datetime:
        """
            ``GET /redfish/v1/Managers/<id>`` and parse the ``DateTime``
            field.

            :param aspects: Effective aspects.

            :returns: A timezone-aware :class:`datetime.datetime`.
            :raises: UefiError
        """
        manager_id = self._resolve_manager_id()
        path = "/redfish/v1/Managers/{}".format(manager_id)
        command = "GET {}".format(path)

        status, stdout, stderr = self._agent.run_cmd(
            command=command, aspects=aspects,
        )

        if status != 0 and status not in tuple(aspects.allowed_error_codes):
            err_msg_lines = [
                "Redfish GET of Managers failed.",
                "COMMAND: {}".format(command),
                "STATUS: {}".format(status),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg, details=[
                "command={!r}".format(command),
                "status={}".format(status),
                "body={!r}".format(stdout),
                "stderr={!r}".format(stderr),
            ]) from None

        rtnval = _parse_redfish_manager_datetime(body_text=stdout if stdout is not None else "")
        return rtnval

    def _set_time_redfish(self, utc_dt: datetime.datetime,
                                aspects: UefiCmdAspects) -> None:
        """
            ``PATCH /redfish/v1/Managers/<id>`` with ``{"DateTime": "<iso>"}``.

            :param utc_dt: UTC-aware :class:`datetime.datetime`.
            :param aspects: Effective aspects.

            :raises: UefiError
        """
        manager_id = self._resolve_manager_id()
        path = "/redfish/v1/Managers/{}".format(manager_id)
        iso_value = utc_dt.isoformat()
        body_payload = {"DateTime": iso_value}
        body_text = json.dumps(body_payload)
        command = "PATCH {} {}".format(path, body_text)

        status, stdout, stderr = self._agent.run_cmd(
            command=command, aspects=aspects,
        )

        if status != 0 and status not in tuple(aspects.allowed_error_codes):
            err_msg_lines = [
                "Redfish PATCH of Managers failed.",
                "COMMAND: {}".format(command),
                "STATUS: {}".format(status),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg, details=[
                "command={!r}".format(command),
                "status={}".format(status),
                "body={!r}".format(stdout),
                "stderr={!r}".format(stderr),
            ]) from None

        return

    # ------------------------------------------------------------------
    # Capability helpers
    # ------------------------------------------------------------------

    def _get_capabilities_ssh_host(self, aspects: UefiCmdAspects) -> UefiTimeCapabilities:
        """
            Best-effort capability discovery over ssh-host by invoking
            ``adjtimex --print``.  When the binary is missing or the call
            fails, returns a default capability record.

            :param aspects: Effective aspects.

            :returns: A :class:`UefiTimeCapabilities` instance.
        """
        command = "adjtimex --print"
        try:
            status, stdout, stderr = self._agent.run_cmd(
                command=command, aspects=aspects,
            )
        except Exception as cerr:
            self._logger.debug(
                "uefi.time: adjtimex invocation raised; returning defaults. EXCEPTION=%r",
                cerr,
            )
            rtnval = UefiTimeCapabilities(
                resolution_hz=DEFAULT_RTC_RESOLUTION_HZ,
                accuracy_ppm=DEFAULT_RTC_ACCURACY_PPM,
                sets_to_zero=True,
            )
            return rtnval

        if status != 0 or stdout is None:
            self._logger.debug(
                "uefi.time: adjtimex returned status=%s; returning defaults.",
                status,
            )
            rtnval = UefiTimeCapabilities(
                resolution_hz=DEFAULT_RTC_RESOLUTION_HZ,
                accuracy_ppm=DEFAULT_RTC_ACCURACY_PPM,
                sets_to_zero=True,
            )
            return rtnval

        resolution_hz, accuracy_ppm = _parse_adjtimex_output(stdout=stdout)
        rtnval = UefiTimeCapabilities(
            resolution_hz=resolution_hz,
            accuracy_ppm=accuracy_ppm,
            sets_to_zero=True,
        )
        return rtnval

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _require_transport_kind(self) -> str:
        """
            Read ``transport_kind`` off the bound agent and validate.

            :returns: The transport kind string.
            :raises: ConfigurationError
        """
        transport_kind = getattr(self._agent, "transport_kind", None)
        if transport_kind is None:
            err_msg_lines = [
                "TimeServices: agent does not expose a 'transport_kind' attribute.",
                "AGENT_TYPE: {}".format(type(self._agent).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg) from None

        if not isinstance(transport_kind, str):
            err_msg_lines = [
                "TimeServices: agent 'transport_kind' must be a str.",
                "OBSERVED_TYPE: {}".format(type(transport_kind).__name__),
                "OBSERVED_VALUE: {!r}".format(transport_kind),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg) from None

        rtnval = transport_kind
        return rtnval

    def _resolve_manager_id(self) -> str:
        """
            Resolve the Redfish Manager id from the bound agent, falling
            back to :data:`DEFAULT_MANAGER_ID` when the agent does not
            publish one.

            :returns: A non-empty Manager id string.
        """
        candidate = getattr(self._agent, "manager_id", None)
        if isinstance(candidate, str) and len(candidate) > 0:
            rtnval = candidate
            return rtnval

        rtnval = DEFAULT_MANAGER_ID
        return rtnval


# ---------------------------------------------------------------------------
# Compatibility exports — efi_time helpers are re-exported so callers that
# need to round-trip through the EFI_TIME wire form do not need a second
# import.
# ---------------------------------------------------------------------------

_ = datetime_to_efi_time  # re-exported for downstream callers
_ = efi_time_to_datetime  # re-exported for downstream callers
