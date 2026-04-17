"""
    .. module:: boottoopceonce
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Module containing the :class:`BootToOptionOnceWorkflow`
               layer-6 orchestration and its :class:`BootOnceResult`
               report dataclass.  The workflow boots a specified UEFI
               ``Boot####`` option exactly once without modifying
               ``BootOrder`` by writing ``BootNext`` for the target
               option, driving a reboot through the layer-6
               :class:`automojo.protocols.uefi.uefirebootworkflow.RebootWorkflow`,
               and verifying on the next boot that ``BootCurrent``
               reflects the option the test requested.

               The workflow also enforces the firmware's
               spec-mandated behavior that ``BootNext`` is
               automatically cleared after the next boot (UEFI
               spec §3.1.3 / §3.1.5).  A noncompliant firmware that
               leaves ``BootNext`` set is diagnosed here and raises
               :class:`automojo.protocols.uefi.uefiexceptions.UefiVariableError`
               so the caller never silently lands in a permanent
               one-shot loop.

               The workflow composes
               :class:`automojo.protocols.uefi.uefibootmanager.BootManager`
               (for the ``Boot####`` existence / decode check and
               ``BootNext`` / ``BootCurrent`` reads) and
               :class:`RebootWorkflow` (for the reboot-and-back step).

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Automation Mojo LLC"
__credits__ = []


import os

from dataclasses import dataclass
from typing import Any, Optional

from automojo.protocols.uefi.uefiaspects import (
    DEFAULT_CMD_ASPECTS,
    UefiCmdAspects,
    resolve_aspects,
)
from automojo.protocols.uefi.uefibootmanager import BootManager
from automojo.protocols.uefi.uefiexceptions import UefiVariableError
from automojo.protocols.uefi.uefilogging import (
    UEFI_BOOTMGR_CHANNEL,
    get_uefi_logger,
)
from automojo.protocols.uefi.uefireset import ResetKind
from automojo.protocols.uefi.uefirebootworkflow import RebootWorkflow


__all__ = [
    "BootOnceResult",
    "BootToOptionOnceWorkflow",
]


# ---------------------------------------------------------------------------
# BootOnceResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BootOnceResult:
    """
        Structured outcome of a
        :meth:`BootToOptionOnceWorkflow.boot_once` call.

        :ivar success: ``True`` iff the firmware honored the
                       ``BootNext`` request and ``BootCurrent``
                       post-reset equals ``requested_index``; ``False``
                       when the firmware booted a different option or
                       when ``BootCurrent`` could not be read.
        :ivar observed_boot_current: Value of the ``BootCurrent``
                                     variable observed post-reset, or
                                     ``None`` when the variable was
                                     absent.
        :ivar requested_index: The option index the caller requested be
                               booted exactly once.
    """
    success: bool
    observed_boot_current: Optional[int]
    requested_index: int


# ---------------------------------------------------------------------------
# BootToOptionOnceWorkflow
# ---------------------------------------------------------------------------


class BootToOptionOnceWorkflow:
    """
        Layer-6 workflow that boots a specified ``Boot####`` option
        exactly once without modifying ``BootOrder``.

        Holds opaque references to a
        :class:`automojo.protocols.uefi.uefiagent.UefiAgent`, a
        :class:`automojo.protocols.uefi.uefibootmanager.BootManager`,
        and a
        :class:`automojo.protocols.uefi.uefirebootworkflow.RebootWorkflow`
        along with an instance default :class:`UefiCmdAspects`.  Every
        call to :meth:`boot_once` resolves aspects once via
        :func:`resolve_aspects`, validates the target option exists,
        writes ``BootNext``, drives the reboot through the injected
        :class:`RebootWorkflow`, reads back ``BootCurrent`` and
        ``BootNext``, and returns a structured :class:`BootOnceResult`.

        :param agent: The owning :class:`UefiAgent`.
        :param boot_manager: The layer-5
                             :class:`BootManager` used for every
                             ``Boot####`` / ``BootNext`` /
                             ``BootCurrent`` variable operation.
        :param reboot_workflow: The layer-6 :class:`RebootWorkflow`
                                used for the reboot-and-back step.
        :param aspects: Default :class:`UefiCmdAspects` used when a
                        call omits its own ``aspects`` override.
    """

    def __init__(self, agent: Any,
                       boot_manager: BootManager,
                       reboot_workflow: RebootWorkflow,
                       aspects: UefiCmdAspects = DEFAULT_CMD_ASPECTS) -> None:
        """
            Initialize the :class:`BootToOptionOnceWorkflow`.

            :param agent: The owning :class:`UefiAgent` instance.
            :param boot_manager: The :class:`BootManager` facade.
            :param reboot_workflow: The :class:`RebootWorkflow`
                                    composer.
            :param aspects: Default aspects for calls that omit an
                            override.
        """
        self._agent: Any = agent
        self._boot_manager: BootManager = boot_manager
        self._reboot_workflow: RebootWorkflow = reboot_workflow
        self._aspects: UefiCmdAspects = aspects
        self._logger = get_uefi_logger(channel=UEFI_BOOTMGR_CHANNEL)
        return

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def boot_once(self, index: int,
                        aspects: Optional[UefiCmdAspects] = None) -> BootOnceResult:
        """
            Boot the specified ``Boot####`` option exactly once.

            Orchestration:

            1. Verify ``Boot{index:04X}`` exists via
               :meth:`BootManager.get_boot_option`; raise
               :class:`UefiVariableError` if the variable is absent.
            2. Write ``BootNext = index`` via
               :meth:`BootManager.set_boot_next`.
            3. Reboot via
               :meth:`RebootWorkflow.reboot_and_verify` with
               :class:`ResetKind.WARM`.
            4. Read ``BootCurrent`` via
               :meth:`BootManager.get_boot_current` to check which
               option actually booted.
            5. Verify ``BootNext`` was cleared by the firmware (per
               UEFI spec §3.1.3 / §3.1.5 the firmware SHALL clear
               ``BootNext`` after the next boot).  When ``BootNext``
               still contains the originally-written value the
               workflow raises :class:`UefiVariableError` with a
               ``"firmware did not clear BootNext"`` message.
            6. Return a :class:`BootOnceResult` describing the
               outcome.  ``success`` is ``True`` iff the observed
               ``BootCurrent`` equals the requested ``index``.

            :param index: The ``Boot####`` option index to boot
                          exactly once.
            :param aspects: Optional per-call :class:`UefiCmdAspects`;
                            when ``None`` the instance default is used.

            :returns: A :class:`BootOnceResult` describing the outcome.
            :raises: UefiVariableError
        """
        effective_aspects = resolve_aspects(
            passed=aspects,
            instance_default=self._aspects,
        )

        host = getattr(self._agent, "host", None)
        target_name = "Boot{:04X}".format(index)

        self._logger.info(
            "uefi.workflow.bootnext: begin host=%s target=%s",
            host, target_name,
        )

        # Step 1 — verify Boot#### exists.
        self._require_boot_option_exists(index=index, aspects=effective_aspects)

        # Step 2 — write BootNext.
        self._logger.info(
            "uefi.workflow.bootnext: set_boot_next host=%s target=%s",
            host, target_name,
        )
        self._boot_manager.set_boot_next(index=index, aspects=effective_aspects)

        # Step 3 — reboot via the layer-6 RebootWorkflow.
        self._logger.info(
            "uefi.workflow.bootnext: reboot host=%s target=%s",
            host, target_name,
        )
        self._reboot_workflow.reboot_and_verify(
            kind=ResetKind.WARM,
            wait_for_shell=True,
            aspects=effective_aspects,
        )

        # Step 4 — observe BootCurrent.
        observed_boot_current = self._boot_manager.get_boot_current(
            aspects=effective_aspects,
        )

        self._logger.info(
            "uefi.workflow.bootnext: observed boot_current host=%s observed=%s requested=0x%04X",
            host, observed_boot_current, index,
        )

        # Step 5 — verify the firmware cleared BootNext.
        self._require_boot_next_cleared(
            requested_index=index,
            aspects=effective_aspects,
        )

        # Step 6 — build the final result.  ``success`` hinges on
        # BootCurrent matching the requested index byte-for-byte.
        if observed_boot_current is None:
            success = False
        elif observed_boot_current == index:
            success = True
        else:
            success = False

        result = BootOnceResult(
            success=success,
            observed_boot_current=observed_boot_current,
            requested_index=index,
        )

        self._logger.info(
            "uefi.workflow.bootnext: done host=%s target=%s success=%s",
            host, target_name, success,
        )
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _require_boot_option_exists(self, index: int,
                                          aspects: UefiCmdAspects) -> None:
        """
            Verify the target ``Boot####`` variable is present by
            reading it through :meth:`BootManager.get_boot_option`.
            A missing variable surfaces as a
            :class:`UefiVariableError` with an explicit
            ``"Boot#### not present on target"`` detail line so the
            caller does not have to introspect the underlying error's
            ``efi_status``.

            :param index: The option index to probe.
            :param aspects: Effective aspects for the read.

            :raises: UefiVariableError
        """
        target_name = "Boot{:04X}".format(index)
        try:
            self._boot_manager.get_boot_option(
                index=index,
                aspects=aspects,
            )
        except UefiVariableError as verr:
            err_msg_lines = [
                "BootToOptionOnceWorkflow: target Boot#### not present on target.",
                "TARGET: {}".format(target_name),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiVariableError(
                err_msg,
                details=[
                    "{} not present on target".format(target_name),
                    "inner={!r}".format(verr),
                ],
                name=target_name,
            ) from verr

        return

    def _require_boot_next_cleared(self, requested_index: int,
                                         aspects: UefiCmdAspects) -> None:
        """
            Read ``BootNext`` post-reboot and raise when it is still
            observed to contain the value we wrote.  This enforces the
            UEFI §3.1.3 / §3.1.5 contract that the firmware SHALL
            clear ``BootNext`` after the next boot.

            :param requested_index: The option index that was written
                                    into ``BootNext`` before the
                                    reboot.
            :param aspects: Effective aspects for the read.

            :raises: UefiVariableError
        """
        observed = self._boot_manager.get_boot_next(aspects=aspects)

        if observed is None:
            # Firmware cleared BootNext per UEFI §3.1.3 / §3.1.5.
            return

        if observed == requested_index:
            err_msg_lines = [
                "BootToOptionOnceWorkflow: firmware did not clear BootNext after the one-shot boot.",
                "UEFI SPEC: §3.1.3 / §3.1.5 require BootNext be cleared after the next boot.",
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiVariableError(
                err_msg,
                details=[
                    "firmware did not clear BootNext after the one-shot boot",
                    "UEFI §3.1.3 / §3.1.5 violation",
                    "requested_index=0x{:04X}".format(requested_index),
                    "BootNext observed=0x{:04X}".format(observed),
                ],
                name="BootNext",
            ) from None

        # BootNext is still set but points at a different option; record
        # what we saw so the caller can triage.  We still raise because
        # the one-shot invariant — "BootNext SHALL be cleared after the
        # next boot" — has been violated by the firmware.
        err_msg_lines = [
            "BootToOptionOnceWorkflow: firmware left BootNext set to an unexpected value after the one-shot boot.",
            "UEFI SPEC: §3.1.3 / §3.1.5 require BootNext be cleared after the next boot.",
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiVariableError(
            err_msg,
            details=[
                "firmware did not clear BootNext after the one-shot boot",
                "UEFI §3.1.3 / §3.1.5 violation",
                "requested_index=0x{:04X}".format(requested_index),
                "BootNext observed=0x{:04X}".format(observed),
            ],
            name="BootNext",
        ) from None
