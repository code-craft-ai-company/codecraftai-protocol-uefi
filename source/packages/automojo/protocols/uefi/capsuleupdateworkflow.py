"""
    .. module:: capsuleupdateworkflow
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Layer-6 workflow that composes capsule delivery
               (:class:`CapsuleDelivery`), reboot orchestration
               (:class:`RebootWorkflow`), and ESRT readout
               (:class:`SystemTables`) into a single call that delivers a
               capsule, reboots the target, reads back the firmware's
               capsule report, and verifies the matching ESRT entry
               advanced.  The workflow enforces strict preflight gates
               (ESRT presence, capsule GUID coverage in ESRT, target
               connectivity) before any destructive write so that obvious
               misconfigurations fail loud before the target is rebooted.

               The workflow honors caller-supplied aspects by delegating
               them unchanged to each constituent feature.  It consumes no
               action patterns of its own — retry / timeout behavior is the
               responsibility of the underlying layer-5 features — and
               produces a :class:`CapsuleUpdateReport` that test authors
               can attach to their test-run artifacts.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Automation Mojo LLC"
__credits__ = []


import hashlib
import os
import uuid

from dataclasses import dataclass
from typing import Any, Optional, Tuple

from automojo.protocols.uefi.uefiaspects import (
    DEFAULT_CMD_ASPECTS,
    UefiCmdAspects,
    resolve_aspects,
)
from automojo.protocols.uefi.uefacapsule import (
    Capsule,
    CapsuleDelivery,
    CapsuleReport,
    CapsuleReportEntry,
)
from automojo.protocols.uefi.uefiexceptions import UefiCapsuleError
from automojo.protocols.uefi.uefisystemtables import (
    EsrtEntry,
    EsrtTable,
    SystemTables,
)


__all__ = [
    "CapsuleUpdateReport",
    "CapsuleUpdateWorkflow",
]


# ---------------------------------------------------------------------------
# CapsuleUpdateReport
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CapsuleUpdateReport:
    """
        Structured outcome of a capsule-update workflow run.

        :ivar input_sha256: Lowercase hex SHA-256 of the input capsule bytes
                            (64 characters, no ``0x`` prefix).  Ties a
                            pass / fail outcome to a specific binary.
        :ivar capsule_guid: The capsule GUID decoded from the input
                            capsule's header.
        :ivar reported_status_guid: The firmware-reported status GUID taken
                                    from the matching capsule report
                                    variable per UEFI §8.5.6, when a
                                    matching record was observed.  ``None``
                                    when the workflow did not observe a
                                    matching record (for example on a
                                    preflight failure).
        :ivar report_entry: The decoded :class:`CapsuleReportEntry` that
                            matched ``capsule_guid``, when one was found.
                            ``None`` otherwise.
        :ivar esrt_before: Tuple of :class:`EsrtEntry` values snapshotted
                           before delivery.
        :ivar esrt_after: Tuple of :class:`EsrtEntry` values snapshotted
                          after the reboot.  Empty when the workflow did
                          not reach the post-reboot snapshot.
        :ivar success: ``True`` when an ESRT entry whose
                       ``fw_class`` matches ``capsule_guid`` reports a
                       strictly-higher ``fw_version`` after the reboot.
    """

    input_sha256: str
    capsule_guid: uuid.UUID
    reported_status_guid: Optional[uuid.UUID]
    report_entry: Optional[CapsuleReportEntry]
    esrt_before: Tuple[EsrtEntry, ...]
    esrt_after: Tuple[EsrtEntry, ...]
    success: bool


# ---------------------------------------------------------------------------
# Module-internal helpers
# ---------------------------------------------------------------------------

def _hash_capsule(capsule: Capsule) -> str:
    """
        Compute the SHA-256 of a capsule's on-wire bytes.

        :param capsule: The capsule to hash.

        :returns: The 64-character lowercase hex digest.
        :raises: UefiCapsuleError
    """
    if not isinstance(capsule, Capsule):
        err_msg_lines = [
            "CapsuleUpdateWorkflow: 'capsule' must be a Capsule instance.",
            "GOT: value={!r} type={}".format(capsule, type(capsule).__name__),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiCapsuleError(err_msg) from None

    capsule_bytes = capsule.to_bytes()
    hasher = hashlib.sha256()
    hasher.update(capsule_bytes)
    rtnval = hasher.hexdigest()
    return rtnval


def _find_esrt_entry(entries: Tuple[EsrtEntry, ...],
                     fw_class: uuid.UUID) -> Optional[EsrtEntry]:
    """
        Look up the first :class:`EsrtEntry` whose ``fw_class`` matches
        ``fw_class``.

        :param entries: The ESRT entries to search.
        :param fw_class: The firmware-class GUID to match.

        :returns: The matching entry, or ``None`` when no entry matches.
    """
    match: Optional[EsrtEntry] = None
    for entry in entries:
        if entry.fw_class == fw_class:
            match = entry
            break

    rtnval = match
    return rtnval


# ---------------------------------------------------------------------------
# CapsuleUpdateWorkflow
# ---------------------------------------------------------------------------

class CapsuleUpdateWorkflow:
    """
        Layer-6 orchestration of a full capsule-update cycle.

        Deliver the capsule, reboot the target, read back the firmware's
        capsule report, and verify that the ESRT entry whose ``fw_class``
        matches the capsule's GUID advanced.  On the way in, the workflow
        enforces three preflight safety gates:

        * ESRT must be present on the target (non-empty entry set).  When
          absent the workflow refuses to proceed because a post-reboot
          pass / fail cannot be verified.
        * The capsule's GUID must match at least one ESRT entry's
          ``fw_class``.  A capsule for a component the target does not
          declare would have no observable effect and the workflow
          short-circuits before rebooting.
        * The target must report as reachable (``agent.verify_connectivity()
          is True``).  Rebooting an unreachable target has no recovery
          story.

        :param agent: The :class:`UefiAgent` bound to the target.  Used
                      only to probe connectivity; delivery, report-read,
                      and ESRT reads are delegated to the explicit
                      collaborators passed alongside.
        :param capsule_delivery: Pre-built :class:`CapsuleDelivery` used to
                                 ship the capsule to the target.
        :param capsule_report: Pre-built :class:`CapsuleReport` used to
                               enumerate the firmware's post-reboot
                               capsule-report records.
        :param system_tables: Pre-built :class:`SystemTables` used to
                              snapshot the ESRT before and after the
                              reboot.
        :param reboot_workflow: Pre-built reboot orchestrator exposing
                                ``reboot_and_verify``.  The workflow only
                                calls that one method.
        :param aspects: Default :class:`UefiCmdAspects` used when
                        ``run`` is called without a ``aspects`` override.
    """

    def __init__(self, agent: Any,
                       capsule_delivery: CapsuleDelivery,
                       capsule_report: CapsuleReport,
                       system_tables: SystemTables,
                       reboot_workflow: Any,
                       aspects: UefiCmdAspects = DEFAULT_CMD_ASPECTS) -> None:
        """
            Initialize the workflow.

            :param agent: The :class:`UefiAgent`.
            :param capsule_delivery: The :class:`CapsuleDelivery` collaborator.
            :param capsule_report: The :class:`CapsuleReport` collaborator.
            :param system_tables: The :class:`SystemTables` collaborator.
            :param reboot_workflow: The :class:`RebootWorkflow` collaborator.
            :param aspects: The default :class:`UefiCmdAspects`.

            :raises: UefiCapsuleError
        """
        if agent is None:
            err_msg_lines = [
                "CapsuleUpdateWorkflow: 'agent' must not be None.",
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from None

        if capsule_delivery is None:
            err_msg_lines = [
                "CapsuleUpdateWorkflow: 'capsule_delivery' must not be None.",
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from None

        if capsule_report is None:
            err_msg_lines = [
                "CapsuleUpdateWorkflow: 'capsule_report' must not be None.",
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from None

        if system_tables is None:
            err_msg_lines = [
                "CapsuleUpdateWorkflow: 'system_tables' must not be None.",
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from None

        if reboot_workflow is None:
            err_msg_lines = [
                "CapsuleUpdateWorkflow: 'reboot_workflow' must not be None.",
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from None

        self._agent: Any = agent
        self._capsule_delivery: CapsuleDelivery = capsule_delivery
        self._capsule_report: CapsuleReport = capsule_report
        self._system_tables: SystemTables = system_tables
        self._reboot_workflow: Any = reboot_workflow
        self._aspects: UefiCmdAspects = aspects
        return

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------

    def run(self, capsule: Capsule,
                  aspects: Optional[UefiCmdAspects] = None) -> CapsuleUpdateReport:
        """
            Drive the full capsule-update cycle.

            Sequence:

            1. Preflight safety gates (ESRT present, capsule GUID covered
               by an ESRT entry, target reachable).
            2. Snapshot ESRT via ``system_tables.read_esrt()``.
            3. Deliver capsule via ``capsule_delivery.deliver(capsule)``.
            4. Reboot via ``reboot_workflow.reboot_and_verify()``.
            5. Fetch capsule reports via ``capsule_report.list_reports()``
               and locate the entry whose ``capsule_guid`` matches the
               capsule's header GUID.
            6. Re-read ESRT; compare before / after and set ``success``
               to ``True`` when the matching entry's ``fw_version``
               strictly advanced.
            7. Return a populated :class:`CapsuleUpdateReport`.

            :param capsule: The capsule to deliver.
            :param aspects: Optional per-call :class:`UefiCmdAspects`;
                            falls back to the instance default.

            :returns: A populated :class:`CapsuleUpdateReport`.
            :raises: UefiCapsuleError
        """
        effective = resolve_aspects(passed=aspects, instance_default=self._aspects)

        if not isinstance(capsule, Capsule):
            err_msg_lines = [
                "CapsuleUpdateWorkflow.run: 'capsule' must be a Capsule instance.",
                "GOT: value={!r} type={}".format(capsule, type(capsule).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from None

        input_sha256 = _hash_capsule(capsule=capsule)
        capsule_guid = capsule.header.capsule_guid

        # --- Preflight safety gates ------------------------------------
        esrt_before_entries = self._preflight_esrt(
            capsule_guid=capsule_guid, aspects=effective,
        )
        self._preflight_connectivity(
            capsule_guid=capsule_guid, input_sha256=input_sha256,
        )

        # --- Deliver ----------------------------------------------------
        try:
            self._capsule_delivery.deliver(capsule=capsule, aspects=effective)
        except UefiCapsuleError:
            raise
        except BaseException as derr:
            err_msg_lines = [
                "CapsuleUpdateWorkflow.run: capsule delivery failed.",
                "INPUT_SHA256: {}".format(input_sha256),
                "CAPSULE_GUID: {}".format(capsule_guid),
                "EXCEPTION: {!r}".format(derr),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from derr

        # --- Reboot -----------------------------------------------------
        try:
            self._reboot_workflow.reboot_and_verify(aspects=effective)
        except UefiCapsuleError:
            raise
        except BaseException as rerr:
            err_msg_lines = [
                "CapsuleUpdateWorkflow.run: reboot_and_verify failed after delivery.",
                "INPUT_SHA256: {}".format(input_sha256),
                "CAPSULE_GUID: {}".format(capsule_guid),
                "EXCEPTION: {!r}".format(rerr),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from rerr

        # --- Post-reboot capsule-report fetch --------------------------
        report_entry = self._fetch_matching_report(
            capsule_guid=capsule_guid,
            input_sha256=input_sha256,
            aspects=effective,
        )

        # --- Post-reboot ESRT snapshot + advancement check -------------
        esrt_after_entries = self._snapshot_esrt_after(
            capsule_guid=capsule_guid,
            input_sha256=input_sha256,
            aspects=effective,
        )

        success_flag = self._esrt_advanced(
            before=esrt_before_entries,
            after=esrt_after_entries,
            capsule_guid=capsule_guid,
        )

        reported_status_guid = self._resolve_reported_status_guid(
            report_entry=report_entry,
        )

        rtnval = CapsuleUpdateReport(
            input_sha256=input_sha256,
            capsule_guid=capsule_guid,
            reported_status_guid=reported_status_guid,
            report_entry=report_entry,
            esrt_before=esrt_before_entries,
            esrt_after=esrt_after_entries,
            success=success_flag,
        )
        return rtnval

    # ------------------------------------------------------------------
    # Preflight gates
    # ------------------------------------------------------------------

    def _preflight_esrt(self, capsule_guid: uuid.UUID,
                              aspects: UefiCmdAspects) -> Tuple[EsrtEntry, ...]:
        """
            Read the ESRT and ensure it is non-empty and contains an entry
            whose ``fw_class`` matches ``capsule_guid``.

            :param capsule_guid: The capsule GUID to look up.
            :param aspects: The effective aspects for the call.

            :returns: The ESRT entries as an immutable tuple.
            :raises: UefiCapsuleError
        """
        try:
            esrt_table = self._system_tables.read_esrt(aspects=aspects)
        except UefiCapsuleError:
            raise
        except BaseException as eerr:
            err_msg_lines = [
                "CapsuleUpdateWorkflow preflight: no ESRT on target.",
                "CAPSULE_GUID: {}".format(capsule_guid),
                "EXCEPTION: {!r}".format(eerr),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from eerr

        if not isinstance(esrt_table, EsrtTable):
            err_msg_lines = [
                "CapsuleUpdateWorkflow preflight: read_esrt returned a non-EsrtTable value.",
                "GOT: type={}".format(type(esrt_table).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from None

        entries = tuple(esrt_table.entries)
        if len(entries) == 0:
            err_msg_lines = [
                "CapsuleUpdateWorkflow preflight: no ESRT on target.",
                "CAPSULE_GUID: {}".format(capsule_guid),
                "ESRT_ENTRY_COUNT: 0",
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from None

        matching_entry = _find_esrt_entry(entries=entries, fw_class=capsule_guid)
        if matching_entry is None:
            observed_classes = [str(entry.fw_class) for entry in entries]
            err_msg_lines = [
                "CapsuleUpdateWorkflow preflight: capsule GUID does not match any ESRT entry.",
                "CAPSULE_GUID: {}".format(capsule_guid),
                "OBSERVED_FW_CLASSES: {}".format(observed_classes),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from None

        return entries

    def _preflight_connectivity(self, capsule_guid: uuid.UUID,
                                      input_sha256: str) -> None:
        """
            Verify that the agent reports as reachable before attempting a
            destructive write.

            :param capsule_guid: The capsule GUID (diagnostics only).
            :param input_sha256: The capsule SHA-256 (diagnostics only).

            :raises: UefiCapsuleError
        """
        try:
            connected = self._agent.verify_connectivity()
        except BaseException as cerr:
            err_msg_lines = [
                "CapsuleUpdateWorkflow preflight: verify_connectivity raised.",
                "CAPSULE_GUID: {}".format(capsule_guid),
                "INPUT_SHA256: {}".format(input_sha256),
                "EXCEPTION: {!r}".format(cerr),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from cerr

        if connected is not True:
            err_msg_lines = [
                "CapsuleUpdateWorkflow preflight: target is not reachable.",
                "CAPSULE_GUID: {}".format(capsule_guid),
                "INPUT_SHA256: {}".format(input_sha256),
                "OBSERVED: verify_connectivity() returned {!r}".format(connected),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from None

        return

    # ------------------------------------------------------------------
    # Post-reboot helpers
    # ------------------------------------------------------------------

    def _fetch_matching_report(self, capsule_guid: uuid.UUID,
                                     input_sha256: str,
                                     aspects: UefiCmdAspects) -> Optional[CapsuleReportEntry]:
        """
            Fetch the firmware-reported capsule records and select the one
            whose ``capsule_guid`` matches ``capsule_guid``.

            :param capsule_guid: The capsule GUID to match.
            :param input_sha256: The capsule SHA-256 (diagnostics only).
            :param aspects: The effective aspects for the call.

            :returns: The matching :class:`CapsuleReportEntry`.  When
                      multiple records match, the most recently-enumerated
                      record is returned.
            :raises: UefiCapsuleError
        """
        try:
            records = self._capsule_report.list_reports(aspects=aspects)
        except UefiCapsuleError:
            raise
        except BaseException as rerr:
            err_msg_lines = [
                "CapsuleUpdateWorkflow.run: capsule-report list_reports failed after reboot.",
                "CAPSULE_GUID: {}".format(capsule_guid),
                "INPUT_SHA256: {}".format(input_sha256),
                "EXCEPTION: {!r}".format(rerr),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from rerr

        matching_records: Tuple[CapsuleReportEntry, ...] = tuple(
            entry for entry in records if entry.capsule_guid == capsule_guid
        )

        if len(matching_records) == 0:
            observed_count = len(records)
            err_msg_lines = [
                "CapsuleUpdateWorkflow.run: no capsule-report record matched the capsule GUID after reboot.",
                "CAPSULE_GUID: {}".format(capsule_guid),
                "INPUT_SHA256: {}".format(input_sha256),
                "OBSERVED_RECORD_COUNT: {}".format(observed_count),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from None

        rtnval = matching_records[-1]
        return rtnval

    def _snapshot_esrt_after(self, capsule_guid: uuid.UUID,
                                   input_sha256: str,
                                   aspects: UefiCmdAspects) -> Tuple[EsrtEntry, ...]:
        """
            Re-read the ESRT after the reboot.

            :param capsule_guid: The capsule GUID (diagnostics only).
            :param input_sha256: The capsule SHA-256 (diagnostics only).
            :param aspects: The effective aspects for the call.

            :returns: The post-reboot ESRT entries as an immutable tuple.
            :raises: UefiCapsuleError
        """
        try:
            esrt_after = self._system_tables.read_esrt(aspects=aspects)
        except UefiCapsuleError:
            raise
        except BaseException as eerr:
            err_msg_lines = [
                "CapsuleUpdateWorkflow.run: post-reboot read_esrt failed.",
                "CAPSULE_GUID: {}".format(capsule_guid),
                "INPUT_SHA256: {}".format(input_sha256),
                "EXCEPTION: {!r}".format(eerr),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from eerr

        if not isinstance(esrt_after, EsrtTable):
            err_msg_lines = [
                "CapsuleUpdateWorkflow.run: post-reboot read_esrt returned a non-EsrtTable value.",
                "GOT: type={}".format(type(esrt_after).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from None

        rtnval = tuple(esrt_after.entries)
        return rtnval

    @staticmethod
    def _esrt_advanced(before: Tuple[EsrtEntry, ...],
                       after: Tuple[EsrtEntry, ...],
                       capsule_guid: uuid.UUID) -> bool:
        """
            Decide whether the ESRT entry whose ``fw_class`` matches
            ``capsule_guid`` advanced between the ``before`` and ``after``
            snapshots.

            :param before: ESRT entries captured before delivery.
            :param after: ESRT entries captured after the reboot.
            :param capsule_guid: The firmware-class GUID to compare.

            :returns: ``True`` when a matching ``after`` entry has a
                      strictly-higher ``fw_version`` than the matching
                      ``before`` entry.  ``False`` otherwise.
        """
        before_match = _find_esrt_entry(entries=before, fw_class=capsule_guid)
        after_match = _find_esrt_entry(entries=after, fw_class=capsule_guid)

        if before_match is None:
            rtnval = False
            return rtnval

        if after_match is None:
            rtnval = False
            return rtnval

        rtnval = after_match.fw_version > before_match.fw_version
        return rtnval

    @staticmethod
    def _resolve_reported_status_guid(report_entry: Optional[CapsuleReportEntry]) -> Optional[uuid.UUID]:
        """
            Extract the reported status GUID from the matching capsule
            report entry.

            :param report_entry: The matched :class:`CapsuleReportEntry` or
                                 ``None``.

            :returns: The entry's ``capsule_guid`` when the entry is
                      non-None (this is the firmware-reported status GUID
                      per UEFI §8.5.6).  ``None`` when no entry was found.
        """
        if report_entry is None:
            rtnval: Optional[uuid.UUID] = None
            return rtnval

        rtnval = report_entry.capsule_guid
        return rtnval
