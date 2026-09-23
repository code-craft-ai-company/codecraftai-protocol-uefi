"""
    .. module:: uefireset
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Module containing the :class:`SystemReset` facade, the
               :class:`ResetKind` enumeration, and the
               :class:`ResetFailedError` exception.  :class:`SystemReset`
               wraps the transport-native reset / power-control mechanism
               with a reconnect-verify loop modeled directly on
               :meth:`ccai.protocols.ssh.sshagent.SshAgent.reboot`
               (``sshagent.py:132-185``) and on the peer
               :meth:`ccai.protocols.uefi.uefiagent.UefiAgent.reboot`
               method.  It implements the four UEFI-spec-defined reset
               kinds from UEFI §8.5.1 — cold reset, warm reset, shutdown,
               and platform-specific reset — and dispatches to the
               appropriate per-transport mechanism:

               * ``serial`` / ``ipmi-sol`` — the UEFI Shell ``reset``
                 command with the ``-w`` / ``-c`` / ``-s`` flags.
               * ``ssh-host`` — the host ``reboot`` or ``shutdown -h now``
                 command run as root.
               * ``redfish`` — a POST to
                 ``/redfish/v1/Systems/<id>/Actions/ComputerSystem.Reset``
                 with an appropriate ``ResetType`` body.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Code Craft AI LLC"
__credits__ = []


import enum
import os
import time

from typing import Any, Dict, List, Optional

from ccai.protocols.uefi.uefiaspects import (
    DEFAULT_CMD_ASPECTS,
    UefiCmdAspects,
    resolve_aspects,
)
from ccai.protocols.uefi.uefiexceptions import UefiError
from ccai.protocols.uefi.uefilogging import (
    UEFI_RESET_CHANNEL,
    get_uefi_logger,
)


# ---------------------------------------------------------------------------
# Reset-loop defaults.  These match
# :data:`ccai.protocols.uefi.uefiagent.DEFAULT_REBOOT_COMPLETION_TIMEOUT`
# and ``SshAgent.reboot`` so a system-level reset follows the same cadence
# whichever primitive the caller reaches for.
# ---------------------------------------------------------------------------

DEFAULT_RESET_COMPLETION_TIMEOUT: int = 300
DEFAULT_RESET_COMPLETION_INTERVAL: int = 10
DEFAULT_RESET_GRACE_PERIOD: int = 30


# ---------------------------------------------------------------------------
# ResetKind
# ---------------------------------------------------------------------------

class ResetKind(enum.Enum):
    """
        Enumeration of the reset kinds defined by UEFI §8.5.1 /
        ``EFI_RESET_TYPE``.

        Members:

        * ``COLD`` — ``EfiResetCold``; firmware reinitializes as if the
          system had just been powered on.
        * ``WARM`` — ``EfiResetWarm``; volatile state preserved where the
          platform supports it.
        * ``SHUTDOWN`` — ``EfiResetShutdown``; orderly power-off.
        * ``PLATFORM_SPECIFIC`` — ``EfiResetPlatformSpecific``; an
          OEM-defined reset subtype accompanied by opaque ``ResetData``.
    """
    COLD = "cold"
    WARM = "warm"
    SHUTDOWN = "shutdown"
    PLATFORM_SPECIFIC = "platform-specific"


# ---------------------------------------------------------------------------
# ResetFailedError
# ---------------------------------------------------------------------------

class ResetFailedError(UefiError):
    """
        Raised when the target does not become responsive within
        ``aspects.completion_timeout`` after a reset was issued.

        :param message: Short human-readable summary.
        :param details: Optional list of extra context lines; rendered
                        after the summary via :data:`os.linesep`.
    """


# ---------------------------------------------------------------------------
# Shell / ssh-host command mappings.  Kept as module-level constants so a
# misconfigured :class:`UefiCmdAspects` cannot silently change the command
# emitted for a given reset kind.
# ---------------------------------------------------------------------------

_SHELL_RESET_FLAGS: Dict[ResetKind, str] = {
    ResetKind.WARM: "-w",
    ResetKind.COLD: "-c",
    ResetKind.SHUTDOWN: "-s",
    ResetKind.PLATFORM_SPECIFIC: "-s",
}

_SSH_HOST_COMMANDS: Dict[ResetKind, str] = {
    ResetKind.WARM: "sudo reboot",
    ResetKind.COLD: "sudo reboot",
    ResetKind.SHUTDOWN: "sudo shutdown -h now",
    ResetKind.PLATFORM_SPECIFIC: "sudo reboot",
}

_REDFISH_RESET_TYPES: Dict[ResetKind, str] = {
    ResetKind.WARM: "GracefulRestart",
    ResetKind.COLD: "ForceRestart",
    ResetKind.SHUTDOWN: "GracefulShutdown",
    ResetKind.PLATFORM_SPECIFIC: "PowerCycle",
}

_REDFISH_RESET_PATH_TEMPLATE: str = (
    "/redfish/v1/Systems/{system_id}/Actions/ComputerSystem.Reset"
)

# Connection-class exceptions expected to surface once the reset command
# takes effect and severs the underlying transport.
_EXPECTED_RESET_EXCEPTIONS: tuple = (
    ConnectionError,
    TimeoutError,
    OSError,
    EOFError,
)


# ---------------------------------------------------------------------------
# SystemReset facade
# ---------------------------------------------------------------------------

class SystemReset:
    """
        Reset and power-control facade with reconnect-verify semantics.

        Holds an opaque reference to a
        :class:`ccai.protocols.uefi.uefiagent.UefiAgent` (the type is
        not imported eagerly to avoid a circular import) and an instance
        default :class:`UefiCmdAspects`.  Every call to :meth:`reset`
        dispatches to the transport-appropriate reset mechanism and then,
        unless the caller opts out, polls
        :meth:`UefiAgent.verify_connectivity` until the target returns or
        the completion timeout elapses.

        :param agent: The owning :class:`UefiAgent`.
        :param aspects: Default :class:`UefiCmdAspects` used when a call
                        omits its own ``aspects`` override.
    """

    def __init__(self, agent: Any,
                       aspects: UefiCmdAspects = DEFAULT_CMD_ASPECTS) -> None:
        """
            Initialize the :class:`SystemReset` facade.

            :param agent: The owning :class:`UefiAgent` instance.
            :param aspects: Default aspects for calls that omit an
                            override.
        """
        self._agent = agent
        self._aspects = aspects
        self._logger = get_uefi_logger(channel=UEFI_RESET_CHANNEL)
        return

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def reset(self, kind: ResetKind = ResetKind.WARM,
                    reason: Optional[str] = None,
                    platform_specific_data: Optional[bytes] = None,
                    wait_for_reconnect: bool = True,
                    aspects: Optional[UefiCmdAspects] = None) -> None:
        """
            Reset the target and (when ``wait_for_reconnect=True``) verify
            connectivity before returning.

            :param kind: Reset kind.
            :param reason: Optional human-readable reason recorded in logs.
            :param platform_specific_data: Optional payload for
                                           PLATFORM_SPECIFIC resets.
            :param wait_for_reconnect: When True (default), poll
                                       ``agent.verify_connectivity()``
                                       after the reset until success or
                                       the completion timeout elapses.
            :param aspects: Per-call aspects override.  Defaults to a
                            300-second completion_timeout and
                            10-second completion_interval when caller
                            passes ``None``.

            :raises: ResetFailedError when reconnect fails.
        """
        effective_aspects = resolve_aspects(passed=aspects,
                                            instance_default=self._aspects)

        completion_timeout = self._effective_completion_timeout(
            aspects=effective_aspects,
        )
        completion_interval = self._effective_completion_interval(
            aspects=effective_aspects,
        )

        transport_kind = self._agent.transport_kind
        host = getattr(self._agent, "host", None)

        self._logger.info(
            "uefi.reset: reset.issued host=%s kind=%s transport=%s reason=%s",
            host, kind.value, transport_kind, reason,
        )

        try:
            self._dispatch_reset(
                kind=kind,
                reason=reason,
                platform_specific_data=platform_specific_data,
                aspects=effective_aspects,
            )
        except _EXPECTED_RESET_EXCEPTIONS as cerr:
            # Expected side effect of the reset taking hold — the transport
            # has gone away.  Mirrors SshAgent.reboot:143-145 and
            # UefiAgent.reboot:361-368.
            self._logger.debug(
                "uefi.reset: expected connection error during reset: %r",
                cerr,
            )

        if wait_for_reconnect is False:
            self._logger.info(
                "uefi.reset: wait_for_reconnect=False; returning without polling host=%s",
                host,
            )
            return

        self._wait_for_reconnect(
            kind=kind,
            reason=reason,
            completion_timeout=completion_timeout,
            completion_interval=completion_interval,
        )
        return

    # ------------------------------------------------------------------
    # Dispatch helpers
    # ------------------------------------------------------------------

    def _dispatch_reset(self, kind: ResetKind,
                              reason: Optional[str],
                              platform_specific_data: Optional[bytes],
                              aspects: UefiCmdAspects) -> None:
        """
            Dispatch the reset command to the appropriate transport.

            :param kind: Reset kind.
            :param reason: Optional human-readable reason (forwarded to
                           Redfish payload where supported).
            :param platform_specific_data: Optional PLATFORM_SPECIFIC
                                           payload (currently accepted by
                                           the Shell path as a hex suffix).
            :param aspects: Effective aspects.
        """
        transport_kind = self._agent.transport_kind

        if transport_kind == "serial" or transport_kind == "ipmi-sol":
            self._dispatch_reset_shell(
                kind=kind,
                platform_specific_data=platform_specific_data,
                aspects=aspects,
            )
            return

        if transport_kind == "ssh-host":
            self._dispatch_reset_ssh_host(
                kind=kind,
                aspects=aspects,
            )
            return

        if transport_kind == "redfish":
            self._dispatch_reset_redfish(
                kind=kind,
                reason=reason,
                aspects=aspects,
            )
            return

        err_msg_lines = [
            "SystemReset: unsupported transport_kind for reset dispatch.",
            "TRANSPORT_KIND: {!r}".format(transport_kind),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiError(err_msg, details=[
            "transport_kind={!r}".format(transport_kind),
        ]) from None

    def _dispatch_reset_shell(self, kind: ResetKind,
                                    platform_specific_data: Optional[bytes],
                                    aspects: UefiCmdAspects) -> None:
        """
            Dispatch a UEFI Shell ``reset`` command.

            :param kind: Reset kind.
            :param platform_specific_data: Optional PLATFORM_SPECIFIC
                                           payload, appended as hex.
            :param aspects: Effective aspects.
        """
        flag = _SHELL_RESET_FLAGS[kind]
        command_parts: List[str] = ["reset", flag]

        if kind == ResetKind.PLATFORM_SPECIFIC and platform_specific_data is not None:
            hex_suffix = platform_specific_data.hex()
            command_parts.append(hex_suffix)

        command = " ".join(command_parts)

        self._logger.debug(
            "uefi.reset: shell reset command host=%s command=%s",
            getattr(self._agent, "host", None), command,
        )

        self._agent.run_cmd(command=command, aspects=aspects)
        return

    def _dispatch_reset_ssh_host(self, kind: ResetKind,
                                       aspects: UefiCmdAspects) -> None:
        """
            Dispatch a host-OS reset via the ``ssh-host`` transport.

            :param kind: Reset kind.
            :param aspects: Effective aspects.
        """
        command = _SSH_HOST_COMMANDS[kind]

        self._logger.debug(
            "uefi.reset: ssh-host reset command host=%s command=%s",
            getattr(self._agent, "host", None), command,
        )

        self._agent.run_cmd(command=command, aspects=aspects)
        return

    def _dispatch_reset_redfish(self, kind: ResetKind,
                                      reason: Optional[str],
                                      aspects: UefiCmdAspects) -> None:
        """
            Dispatch a Redfish ``ComputerSystem.Reset`` action.

            :param kind: Reset kind.
            :param reason: Optional reason, included in the payload when
                           the BMC supports the ``@Message.ExtendedInfo``
                           field.
            :param aspects: Effective aspects.
        """
        reset_type = _REDFISH_RESET_TYPES[kind]

        system_id = getattr(self._agent, "system_id", "Self")
        path = _REDFISH_RESET_PATH_TEMPLATE.format(system_id=system_id)

        body_parts: List[str] = ['"ResetType": "{}"'.format(reset_type)]
        if reason is not None:
            truncated_reason = reason[:256]
            body_parts.append(
                '"@Message.ExtendedInfo": [{{"Message": "{}"}}]'.format(
                    truncated_reason
                )
            )
        body = "{" + ", ".join(body_parts) + "}"

        command = "POST {} {}".format(path, body)

        self._logger.debug(
            "uefi.reset: redfish reset command host=%s reset_type=%s path=%s",
            getattr(self._agent, "host", None), reset_type, path,
        )

        redfish_post = getattr(self._agent, "redfish_post", None)
        if callable(redfish_post):
            redfish_post(path=path, body=body, aspects=aspects)
            return

        self._agent.run_cmd(command=command, aspects=aspects)
        return

    # ------------------------------------------------------------------
    # Reconnect-verify loop
    # ------------------------------------------------------------------

    def _wait_for_reconnect(self, kind: ResetKind,
                                  reason: Optional[str],
                                  completion_timeout: float,
                                  completion_interval: float) -> None:
        """
            Poll :meth:`UefiAgent.verify_connectivity` until the target
            returns or ``completion_timeout`` elapses.

            :param kind: Reset kind being observed.
            :param reason: Optional human-readable reason; echoed into the
                           failure details.
            :param completion_timeout: Bound on the reconnect loop in
                                       seconds.
            :param completion_interval: Sleep between verify attempts in
                                        seconds.

            :raises: ResetFailedError
        """
        host = getattr(self._agent, "host", None)
        transport_kind = self._agent.transport_kind

        self._logger.info(
            "uefi.reset: reset.waiting host=%s kind=%s grace=%ss completion_timeout=%ss",
            host, kind.value, DEFAULT_RESET_GRACE_PERIOD, completion_timeout,
        )

        begin_time = time.time()
        end_time = begin_time + completion_timeout

        # Initial grace period — mirrors SshAgent.reboot:169 and
        # UefiAgent.reboot:380.  Gives the target a chance to actually
        # begin tearing down before the first probe.
        time.sleep(DEFAULT_RESET_GRACE_PERIOD)

        attempt_count = 0
        last_error: Optional[BaseException] = None
        while time.time() < end_time:
            attempt_count = attempt_count + 1
            try:
                connected = self._agent.verify_connectivity()
                if connected is True:
                    elapsed_seconds = time.time() - begin_time
                    self._logger.info(
                        "uefi.reset: reset.reconnected host=%s kind=%s elapsed_seconds=%.1f attempts=%d",
                        host, kind.value, elapsed_seconds, attempt_count,
                    )
                    return
            except Exception as cerr:
                last_error = cerr

            time.sleep(completion_interval)

        elapsed_seconds = time.time() - begin_time

        err_msg_lines = [
            "SystemReset.reset: target did not return within completion_timeout.",
        ]
        err_msg_lines.append("HOST: {}".format(host))
        err_msg_lines.append("TRANSPORT: {}".format(transport_kind))
        err_msg_lines.append("KIND: {}".format(kind.value))
        err_msg_lines.append("REASON: {}".format(reason))
        err_msg_lines.append("COMPLETION_TIMEOUT: {}s".format(completion_timeout))
        err_msg_lines.append("ELAPSED_SECONDS: {:.1f}".format(elapsed_seconds))
        err_msg_lines.append("ATTEMPTS: {}".format(attempt_count))
        err_msg_lines.append("LAST_ERROR: {!r}".format(last_error))
        err_msg = os.linesep.join(err_msg_lines)

        self._logger.error(
            "uefi.reset: reset.timeout host=%s kind=%s elapsed_seconds=%.1f attempts=%d last_error=%r",
            host, kind.value, elapsed_seconds, attempt_count, last_error,
        )

        details_list = [
            "kind={}".format(kind.value),
            "reason={}".format(reason),
            "elapsed_seconds={:.1f}".format(elapsed_seconds),
            "host={}".format(host),
            "transport_kind={}".format(transport_kind),
            "attempts={}".format(attempt_count),
            "last_error={!r}".format(last_error),
        ]

        raise ResetFailedError(err_msg, details=details_list) from last_error

    # ------------------------------------------------------------------
    # Aspects-driven defaults
    # ------------------------------------------------------------------

    def _effective_completion_timeout(self, aspects: UefiCmdAspects) -> float:
        """
            Return the larger of ``aspects.completion_timeout`` and
            :data:`DEFAULT_RESET_COMPLETION_TIMEOUT`.

            The aspects object's default completion_timeout is tuned for
            ordinary command execution and can be too short for a
            realistic reset cycle; the reset-specific floor prevents a
            misconfigured aspects instance from reducing the reboot
            budget below the 300-second minimum that both
            :meth:`SshAgent.reboot` and :meth:`UefiAgent.reboot` use.

            :param aspects: Effective aspects for the call.

            :returns: The chosen completion timeout in seconds.
        """
        aspects_value = float(aspects.completion_timeout)
        floor_value = float(DEFAULT_RESET_COMPLETION_TIMEOUT)
        if aspects_value > floor_value:
            rtnval = aspects_value
        else:
            rtnval = floor_value
        return rtnval

    def _effective_completion_interval(self, aspects: UefiCmdAspects) -> float:
        """
            Return the larger of ``aspects.completion_interval`` and
            :data:`DEFAULT_RESET_COMPLETION_INTERVAL`.

            :param aspects: Effective aspects for the call.

            :returns: The chosen completion interval in seconds.
        """
        aspects_value = float(aspects.completion_interval)
        floor_value = float(DEFAULT_RESET_COMPLETION_INTERVAL)
        if aspects_value > floor_value:
            rtnval = aspects_value
        else:
            rtnval = floor_value
        return rtnval


__all__ = [
    "DEFAULT_RESET_COMPLETION_INTERVAL",
    "DEFAULT_RESET_COMPLETION_TIMEOUT",
    "DEFAULT_RESET_GRACE_PERIOD",
    "ResetFailedError",
    "ResetKind",
    "SystemReset",
]
