"""
    .. module:: uefirebootworkflow
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Module containing the :class:`RebootWorkflow` layer-6
               orchestration and its :class:`RebootResult` report dataclass.
               :class:`RebootWorkflow` is a thin composition over the
               layer-5 :class:`automojo.protocols.uefi.uefireset.SystemReset`
               facade and the layer-4
               :meth:`automojo.protocols.uefi.uefibase.UefiBase.verify_connectivity`
               primitive.  A single call to
               :meth:`RebootWorkflow.reboot_and_verify` resets the target,
               waits for the transport to return, optionally verifies the
               UEFI Shell responds, optionally captures console output
               emitted during the reset, and returns a structured
               :class:`RebootResult` suitable for attachment to a test
               report.

               The workflow delegates every per-call behavior decision to
               the :class:`UefiCmdAspects` it receives, forwarding them
               unchanged to :class:`SystemReset.reset` and to
               :meth:`UefiAgent.verify_connectivity`.  The aspects
               resolution is performed once via
               :func:`automojo.protocols.uefi.uefiaspects.resolve_aspects`.

               Relevant UEFI spec sections are §8.5.1 (``ResetSystem``)
               and §3.1 (Boot Manager, for the post-reset shell liveness
               probe).

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Automation Mojo LLC"
__credits__ = []


import os
import time

from dataclasses import dataclass, field
from typing import Any, Optional

from automojo.protocols.uefi.uefiaspects import (
    DEFAULT_CMD_ASPECTS,
    UefiCmdAspects,
    resolve_aspects,
)
from automojo.protocols.uefi.uefilogging import (
    UEFI_RESET_CHANNEL,
    get_uefi_logger,
)
from automojo.protocols.uefi.uefireset import (
    ResetFailedError,
    ResetKind,
    SystemReset,
)


__all__ = [
    "RebootResult",
    "RebootWorkflow",
]


# ---------------------------------------------------------------------------
# RebootResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RebootResult:
    """
        Structured outcome of a :meth:`RebootWorkflow.reboot_and_verify`
        call.

        :ivar success: ``True`` iff the reset completed and (when
                       ``wait_for_shell=True``) the post-reset shell
                       responded; ``False`` otherwise.
        :ivar elapsed_seconds: Wall-clock seconds between the workflow
                               entry and the point at which the result
                               was produced.
        :ivar shell_ready: ``True`` iff the post-reset shell
                           responsiveness probe returned ``True``.  When
                           ``wait_for_shell=False`` this field is always
                           ``False`` because the probe was skipped.
        :ivar captured_console: Captured console bytes decoded as UTF-8
                                with ``errors='replace'`` when
                                ``capture_console=True`` and the
                                transport exposes a raw-read tap;
                                ``None`` otherwise.
        :ivar reset_kind: The :class:`ResetKind` that was requested.
    """
    success: bool
    elapsed_seconds: float
    shell_ready: bool
    captured_console: Optional[str] = None
    reset_kind: ResetKind = field(default=ResetKind.WARM)


# ---------------------------------------------------------------------------
# RebootWorkflow
# ---------------------------------------------------------------------------


class RebootWorkflow:
    """
        Layer-6 workflow that resets the target, waits for it to come
        back, and optionally verifies the UEFI Shell responds.

        Holds an opaque reference to a
        :class:`automojo.protocols.uefi.uefiagent.UefiAgent` (the type is
        not imported eagerly to avoid a circular-import hazard) and an
        instance default :class:`UefiCmdAspects`.  Every call to
        :meth:`reboot_and_verify` dispatches through a fresh
        :class:`SystemReset` bound to the same agent.

        :param agent: The owning :class:`UefiAgent`.
        :param aspects: Default :class:`UefiCmdAspects` used when a call
                        omits its own ``aspects`` override.
    """

    def __init__(self, agent: Any,
                       aspects: UefiCmdAspects = DEFAULT_CMD_ASPECTS) -> None:
        """
            Initialize the :class:`RebootWorkflow`.

            :param agent: The owning :class:`UefiAgent` instance.
            :param aspects: Default aspects for calls that omit an
                            override.
        """
        self._agent: Any = agent
        self._aspects: UefiCmdAspects = aspects
        self._logger = get_uefi_logger(channel=UEFI_RESET_CHANNEL)
        return

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def reboot_and_verify(self, kind: ResetKind = ResetKind.WARM,
                                wait_for_shell: bool = True,
                                capture_console: bool = False,
                                aspects: Optional[UefiCmdAspects] = None) -> RebootResult:
        """
            Reset the target and wait for it to return, optionally
            verifying the UEFI Shell responds post-reset.

            Orchestration:

            1. Optionally begin capturing console output from the
               transport (best-effort; a no-op when the agent's
               transport does not expose a raw-read tap).
            2. Invoke :meth:`SystemReset.reset` with
               ``wait_for_reconnect=True`` so the layer-5 facade
               handles the connect-close / reconnect / grace-period
               cadence.
            3. When ``wait_for_shell=True``, additionally call
               :meth:`UefiAgent.verify_connectivity` to confirm the
               UEFI Shell responds to the canonical echo probe.
            4. Build and return a :class:`RebootResult` with
               ``elapsed_seconds`` and any captured console bytes.

            :param kind: The :class:`ResetKind` to request.  Defaults
                         to :class:`ResetKind.WARM` per UEFI spec
                         §8.5.1.
            :param wait_for_shell: When ``True`` the workflow also
                                   verifies the post-reset Shell via
                                   :meth:`UefiAgent.verify_connectivity`.
                                   When ``False`` the workflow returns
                                   once :meth:`SystemReset.reset`
                                   reports the transport is back.
            :param capture_console: When ``True`` the workflow attempts
                                    to capture console bytes emitted
                                    during the reset window.  The
                                    capture is best-effort; a transport
                                    that does not expose a tap yields a
                                    ``None`` ``captured_console``.
            :param aspects: Optional per-call :class:`UefiCmdAspects`;
                            when ``None`` the instance default is used.

            :returns: A :class:`RebootResult` describing the outcome.
            :raises: ResetFailedError
        """
        effective_aspects = resolve_aspects(
            passed=aspects,
            instance_default=self._aspects,
        )

        host = getattr(self._agent, "host", None)
        transport_kind = getattr(self._agent, "transport_kind", None)

        self._logger.info(
            "uefi.workflow.reboot: begin host=%s transport=%s kind=%s wait_for_shell=%s capture_console=%s",
            host, transport_kind, kind.value, wait_for_shell, capture_console,
        )

        captured_text: Optional[str] = None
        capture_handle: Optional[Any] = None
        if capture_console is True:
            capture_handle = self._begin_console_capture()

        begin_time = time.time()

        system_reset = SystemReset(
            agent=self._agent,
            aspects=effective_aspects,
        )

        reset_failed_error: Optional[ResetFailedError] = None
        shell_ready = False
        success = False
        try:
            system_reset.reset(
                kind=kind,
                wait_for_reconnect=True,
                aspects=effective_aspects,
            )

            if wait_for_shell is True:
                shell_ready = self._verify_shell(aspects=effective_aspects)
            else:
                # wait_for_shell=False — caller opted out of the Shell
                # probe.  The layer-5 reset already validated the
                # transport reconnected, so we consider the workflow
                # successful without touching the Shell.
                shell_ready = False

            if wait_for_shell is True:
                success = shell_ready
            else:
                success = True
        except ResetFailedError as rerr:
            reset_failed_error = rerr
            success = False
            shell_ready = False
            self._logger.error(
                "uefi.workflow.reboot: reset failed host=%s kind=%s error=%r",
                host, kind.value, rerr,
            )
        finally:
            if capture_handle is not None:
                captured_text = self._end_console_capture(handle=capture_handle)

        elapsed_seconds = time.time() - begin_time

        result = RebootResult(
            success=success,
            elapsed_seconds=elapsed_seconds,
            shell_ready=shell_ready,
            captured_console=captured_text,
            reset_kind=kind,
        )

        if reset_failed_error is not None:
            self._logger.info(
                "uefi.workflow.reboot: done host=%s success=%s elapsed_seconds=%.3f",
                host, success, elapsed_seconds,
            )
            return result

        self._logger.info(
            "uefi.workflow.reboot: done host=%s kind=%s success=%s shell_ready=%s elapsed_seconds=%.3f",
            host, kind.value, success, shell_ready, elapsed_seconds,
        )
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _verify_shell(self, aspects: UefiCmdAspects) -> bool:
        """
            Invoke :meth:`UefiAgent.verify_connectivity` and return its
            boolean outcome.  Any unexpected exception from the agent
            is logged and converted to a ``False`` return value so the
            workflow's contract of returning a :class:`RebootResult` is
            preserved.

            :param aspects: Effective aspects for the probe.

            :returns: ``True`` iff the Shell probe succeeded.
        """
        verify_connectivity = getattr(self._agent, "verify_connectivity", None)
        if not callable(verify_connectivity):
            err_msg_lines = [
                "RebootWorkflow: bound agent does not expose verify_connectivity().",
                "AGENT: {!r}".format(self._agent),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            self._logger.error(
                "uefi.workflow.reboot: %s",
                err_msg.replace(os.linesep, " | "),
            )
            rtnval = False
            return rtnval

        try:
            observed = verify_connectivity()
        except BaseException as cerr:
            self._logger.warning(
                "uefi.workflow.reboot: verify_connectivity raised; treating as not-ready. EXCEPTION=%r",
                cerr,
            )
            rtnval = False
            return rtnval

        if observed is True:
            rtnval = True
            return rtnval

        rtnval = False
        return rtnval

    def _begin_console_capture(self) -> Optional[Any]:
        """
            Begin best-effort console capture from the agent's
            transport.  Looks for an optional
            ``begin_console_capture()`` method on the agent; when
            absent returns ``None`` and the capture is silently
            skipped.  This keeps the workflow compatible with
            transports that do not expose a raw-read tap (notably
            Redfish).

            :returns: An opaque handle to be passed to
                      :meth:`_end_console_capture`, or ``None`` when
                      capture is not available.
        """
        beginner = getattr(self._agent, "begin_console_capture", None)
        if not callable(beginner):
            rtnval: Optional[Any] = None
            return rtnval

        try:
            handle = beginner()
        except BaseException as cerr:
            self._logger.warning(
                "uefi.workflow.reboot: begin_console_capture raised; capture skipped. EXCEPTION=%r",
                cerr,
            )
            rtnval = None
            return rtnval

        rtnval = handle
        return rtnval

    def _end_console_capture(self, handle: Any) -> Optional[str]:
        """
            End best-effort console capture and return the captured
            bytes decoded as UTF-8 with ``errors='replace'``.  Looks
            for an optional ``end_console_capture(handle)`` method on
            the agent; when absent returns ``None``.

            :param handle: Opaque handle returned by
                           :meth:`_begin_console_capture`.

            :returns: The captured console text, or ``None`` when the
                      capture path was not taken.
        """
        ender = getattr(self._agent, "end_console_capture", None)
        if not callable(ender):
            rtnval: Optional[str] = None
            return rtnval

        try:
            captured = ender(handle)
        except BaseException as cerr:
            self._logger.warning(
                "uefi.workflow.reboot: end_console_capture raised; no console text available. EXCEPTION=%r",
                cerr,
            )
            rtnval = None
            return rtnval

        if captured is None:
            rtnval = None
            return rtnval

        if isinstance(captured, str):
            rtnval = captured
            return rtnval

        if isinstance(captured, (bytes, bytearray)):
            decoded = bytes(captured).decode(encoding="utf-8", errors="replace")
            rtnval = decoded
            return rtnval

        rtnval = str(captured)
        return rtnval
