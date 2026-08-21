"""
    .. module:: test_capsuleupdateworkflow
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Unit tests for the
               :mod:`ccai.protocols.uefi.capsuleupdateworkflow` layer-6
               orchestration module.  Exercises the preflight gates (ESRT
               presence, capsule-GUID coverage, target connectivity), the
               happy path through delivery / reboot / report / ESRT
               re-read, post-reboot report-fetch failure handling, and the
               version-not-advanced path that yields ``success=False``.

               Tests never open a real transport or reboot a real target.
               A minimal set of stub collaborators records each call and
               returns caller-configured responses; all assertions are
               purely against the stubs' recorded call logs and the
               returned :class:`CapsuleUpdateReport`.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Code Craft AI LLC"
__credits__ = []


from ccai import testbase

import uuid

from typing import Any, Dict, List, Optional, Tuple

from ccai.protocols.uefi.uefiaspects import (
    DEFAULT_CMD_ASPECTS,
    UefiCmdAspects,
)
from ccai.protocols.uefi.uefacapsule import (
    CAPSULE_HEADER_SIZE,
    Capsule,
    CapsuleHeader,
    CapsuleReportEntry,
)
from ccai.protocols.uefi.uefiexceptions import UefiCapsuleError
from ccai.protocols.uefi.uefisystemtables import (
    EsrtEntry,
    EsrtTable,
)
from ccai.protocols.uefi.capsuleupdateworkflow import (
    CapsuleUpdateReport,
    CapsuleUpdateWorkflow,
)


# ---------------------------------------------------------------------------
# Canonical test fixtures
# ---------------------------------------------------------------------------

SAMPLE_CAPSULE_GUID: uuid.UUID = uuid.UUID("11111111-2222-3333-4444-555555555555")
OTHER_CAPSULE_GUID: uuid.UUID = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
REPORT_STATUS_GUID: uuid.UUID = uuid.UUID("11111111-2222-3333-4444-555555555555")


# ---------------------------------------------------------------------------
# Helpers for building the inputs
# ---------------------------------------------------------------------------

def _build_capsule(capsule_guid: uuid.UUID = SAMPLE_CAPSULE_GUID,
                   body_length: int = 16) -> Capsule:
    """
        Build a minimal :class:`Capsule` whose header carries
        ``capsule_guid`` and whose body is ``body_length`` zero bytes.

        :param capsule_guid: The capsule GUID.
        :param body_length: The body length in bytes.

        :returns: A well-formed :class:`Capsule`.
    """
    body_bytes = b"\x00" * body_length
    header = CapsuleHeader(
        capsule_guid=capsule_guid,
        header_size=CAPSULE_HEADER_SIZE,
        flags=0,
        capsule_image_size=CAPSULE_HEADER_SIZE + body_length,
    )
    rtnval = Capsule(header=header, body=body_bytes)
    return rtnval


def _build_esrt_entry(fw_class: uuid.UUID = SAMPLE_CAPSULE_GUID,
                      fw_version: int = 1) -> EsrtEntry:
    """
        Build an :class:`EsrtEntry` for fixture construction.

        :param fw_class: The firmware-class GUID.
        :param fw_version: The firmware version.

        :returns: A populated :class:`EsrtEntry`.
    """
    rtnval = EsrtEntry(
        fw_class=fw_class,
        fw_type=2,
        fw_version=fw_version,
        lowest_supported_version=0,
        capsule_flags=0,
        last_attempt_version=0,
        last_attempt_status=0,
    )
    return rtnval


def _build_esrt_table(entries: Tuple[EsrtEntry, ...]) -> EsrtTable:
    """
        Build an :class:`EsrtTable` from a tuple of entries.

        :param entries: The entries in table order.

        :returns: A populated :class:`EsrtTable`.
    """
    rtnval = EsrtTable(
        fw_resource_count=len(entries),
        fw_resource_count_max=max(len(entries), 1),
        fw_resource_version=1,
        entries=entries,
    )
    return rtnval


def _build_report_entry(capsule_guid: uuid.UUID,
                        capsule_status: int = 0,
                        last_attempt_version: int = 2) -> CapsuleReportEntry:
    """
        Build a :class:`CapsuleReportEntry` for fixture construction.

        :param capsule_guid: The capsule GUID the record describes.
        :param capsule_status: The EFI_STATUS value.
        :param last_attempt_version: The ``LastAttemptVersion`` value.

        :returns: A populated :class:`CapsuleReportEntry`.
    """
    rtnval = CapsuleReportEntry(
        capsule_guid=capsule_guid,
        capsule_status=capsule_status,
        capsule_processed=True,
        last_attempt_version=last_attempt_version,
        last_attempt_status=capsule_status,
    )
    return rtnval


# ---------------------------------------------------------------------------
# Stub collaborators
# ---------------------------------------------------------------------------

class _StubAgent:
    """
        Minimal :class:`UefiAgent` stand-in exposing only
        :meth:`verify_connectivity`.

        :ivar connectivity_return: Canned return value for
                                   ``verify_connectivity``.
        :ivar connectivity_raises: Optional exception to raise from
                                    ``verify_connectivity``.
        :ivar verify_calls: Count of times ``verify_connectivity`` was
                            invoked.
    """

    def __init__(self, connectivity_return: bool = True,
                       connectivity_raises: Optional[BaseException] = None) -> None:
        """
            Initialize the stub agent.

            :param connectivity_return: Value returned by
                                        ``verify_connectivity``.
            :param connectivity_raises: Exception to raise instead of
                                        returning a value.
        """
        self.connectivity_return: bool = connectivity_return
        self.connectivity_raises: Optional[BaseException] = connectivity_raises
        self.verify_calls: int = 0
        return

    def verify_connectivity(self) -> bool:
        """
            Return the canned value or raise the canned exception.

            :returns: ``connectivity_return``.
        """
        self.verify_calls = self.verify_calls + 1
        if self.connectivity_raises is not None:
            raise self.connectivity_raises

        rtnval = self.connectivity_return
        return rtnval


class _StubCapsuleDelivery:
    """
        Minimal :class:`CapsuleDelivery` stand-in.  Records each
        ``deliver`` call and optionally raises a caller-supplied exception.

        :ivar calls: List of ``(capsule, aspects)`` tuples captured per
                     call.
        :ivar exception_to_raise: Optional exception to raise from
                                   :meth:`deliver`.
    """

    def __init__(self, exception_to_raise: Optional[BaseException] = None) -> None:
        """
            Initialize the stub delivery.

            :param exception_to_raise: Optional exception to raise.
        """
        self.calls: List[Tuple[Capsule, Optional[UefiCmdAspects]]] = []
        self.exception_to_raise: Optional[BaseException] = exception_to_raise
        return

    def deliver(self, capsule: Capsule,
                      aspects: Optional[UefiCmdAspects] = None) -> None:
        """
            Record the call.  Raise the canned exception when configured.

            :param capsule: The capsule to deliver.
            :param aspects: The effective aspects for the call.
        """
        self.calls.append((capsule, aspects))
        if self.exception_to_raise is not None:
            raise self.exception_to_raise

        return


class _StubCapsuleReport:
    """
        Minimal :class:`CapsuleReport` stand-in.  Returns a caller-supplied
        tuple of records from :meth:`list_reports` and optionally raises.

        :ivar records: Canned records to return.
        :ivar calls: Count of times ``list_reports`` was invoked.
        :ivar exception_to_raise: Optional exception to raise instead.
    """

    def __init__(self, records: Tuple[CapsuleReportEntry, ...] = (),
                       exception_to_raise: Optional[BaseException] = None) -> None:
        """
            Initialize the stub.

            :param records: Records to return from ``list_reports``.
            :param exception_to_raise: Optional exception to raise.
        """
        self.records: Tuple[CapsuleReportEntry, ...] = records
        self.calls: int = 0
        self.exception_to_raise: Optional[BaseException] = exception_to_raise
        return

    def list_reports(self, aspects: Optional[UefiCmdAspects] = None) -> Tuple[CapsuleReportEntry, ...]:
        """
            Return the canned records or raise the canned exception.

            :param aspects: Ignored.

            :returns: The canned records.
        """
        self.calls = self.calls + 1
        if self.exception_to_raise is not None:
            raise self.exception_to_raise

        rtnval = self.records
        return rtnval


class _StubSystemTables:
    """
        Minimal :class:`SystemTables` stand-in.  Returns a queue of
        :class:`EsrtTable` values from :meth:`read_esrt` (one per call) or
        raises a queue of exceptions.

        :ivar tables: Queue of :class:`EsrtTable` values.
        :ivar exceptions: Queue of exceptions aligned to ``tables``; an
                          entry of ``None`` means "return the matching
                          table".
        :ivar calls: Count of times :meth:`read_esrt` was invoked.
    """

    def __init__(self, tables: List[Optional[EsrtTable]],
                       exceptions: Optional[List[Optional[BaseException]]] = None) -> None:
        """
            Initialize the stub.

            :param tables: Queue of ``EsrtTable`` values.  ``None``
                           positions in the queue are allowed only when
                           the matching ``exceptions`` slot is non-None.
            :param exceptions: Optional queue of exceptions.  When the
                               slot is non-None the exception is raised;
                               otherwise the matching table is returned.
        """
        self.tables: List[Optional[EsrtTable]] = list(tables)
        if exceptions is None:
            self.exceptions: List[Optional[BaseException]] = [None] * len(self.tables)
        else:
            self.exceptions = list(exceptions)

        self.calls: int = 0
        return

    def read_esrt(self, aspects: Optional[UefiCmdAspects] = None) -> EsrtTable:
        """
            Return the next queued table or raise the queued exception.

            :param aspects: Ignored.

            :returns: The next queued :class:`EsrtTable`.
        """
        index = self.calls
        self.calls = self.calls + 1

        if index < len(self.exceptions):
            candidate_exception = self.exceptions[index]
            if candidate_exception is not None:
                raise candidate_exception

        if index >= len(self.tables):
            raise AssertionError("_StubSystemTables: read_esrt called more times than queued tables.")

        candidate_table = self.tables[index]
        if candidate_table is None:
            raise AssertionError("_StubSystemTables: queued table slot is None with no exception.")

        rtnval = candidate_table
        return rtnval


class _StubRebootWorkflow:
    """
        Minimal :class:`RebootWorkflow` stand-in exposing
        :meth:`reboot_and_verify`.

        :ivar calls: Count of invocations.
        :ivar exception_to_raise: Optional exception to raise.
    """

    def __init__(self, exception_to_raise: Optional[BaseException] = None) -> None:
        """
            Initialize the stub.

            :param exception_to_raise: Optional exception to raise.
        """
        self.calls: int = 0
        self.exception_to_raise: Optional[BaseException] = exception_to_raise
        return

    def reboot_and_verify(self, aspects: Optional[UefiCmdAspects] = None) -> None:
        """
            Record the call or raise the canned exception.

            :param aspects: Ignored.
        """
        self.calls = self.calls + 1
        if self.exception_to_raise is not None:
            raise self.exception_to_raise

        return


# ---------------------------------------------------------------------------
# Workflow factory
# ---------------------------------------------------------------------------

def _build_workflow(agent: _StubAgent,
                    delivery: _StubCapsuleDelivery,
                    report: _StubCapsuleReport,
                    tables: _StubSystemTables,
                    reboot: _StubRebootWorkflow) -> CapsuleUpdateWorkflow:
    """
        Assemble a :class:`CapsuleUpdateWorkflow` bound to the stub
        collaborators.

        :param agent: The stub agent.
        :param delivery: The stub delivery.
        :param report: The stub report reader.
        :param tables: The stub system-tables facade.
        :param reboot: The stub reboot workflow.

        :returns: A ready-to-call :class:`CapsuleUpdateWorkflow`.
    """
    rtnval = CapsuleUpdateWorkflow(
        agent=agent,
        capsule_delivery=delivery,
        capsule_report=report,
        system_tables=tables,
        reboot_workflow=reboot,
        aspects=DEFAULT_CMD_ASPECTS,
    )
    return rtnval


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# CapsuleUpdateWorkflowHappyPathTests
# ----------------------------------------------------------------------

"""
    Verify the happy path through the full workflow sequence.
"""

def test_full_workflow_reports_success() -> None:
    capsule = _build_capsule()
    before_table = _build_esrt_table(entries=(
        _build_esrt_entry(fw_class=SAMPLE_CAPSULE_GUID, fw_version=1),
    ))
    after_table = _build_esrt_table(entries=(
        _build_esrt_entry(fw_class=SAMPLE_CAPSULE_GUID, fw_version=2),
    ))
    report_entry = _build_report_entry(capsule_guid=SAMPLE_CAPSULE_GUID)

    agent = _StubAgent(connectivity_return=True)
    delivery = _StubCapsuleDelivery()
    report = _StubCapsuleReport(records=(report_entry,))
    tables = _StubSystemTables(tables=[before_table, after_table])
    reboot = _StubRebootWorkflow()

    workflow = _build_workflow(
        agent=agent, delivery=delivery, report=report,
        tables=tables, reboot=reboot,
    )

    result = workflow.run(capsule=capsule)

    testbase.assert_true(isinstance(result, CapsuleUpdateReport))
    testbase.assert_true(result.success)
    testbase.assert_equal(result.capsule_guid, SAMPLE_CAPSULE_GUID)
    testbase.assert_equal(result.reported_status_guid, REPORT_STATUS_GUID)
    testbase.assert_equal(len(result.input_sha256), 64)
    testbase.assert_equal(result.esrt_before, before_table.entries)
    testbase.assert_equal(result.esrt_after, after_table.entries)
    testbase.assert_true(result.report_entry is report_entry)

    testbase.assert_equal(len(delivery.calls), 1)
    testbase.assert_equal(reboot.calls, 1)
    testbase.assert_equal(report.calls, 1)
    testbase.assert_equal(tables.calls, 2)
    testbase.assert_equal(agent.verify_calls, 1)
    return


# ---------------------------------------------------------------------------
# Preflight failures
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# CapsuleUpdateWorkflowPreflightTests
# ----------------------------------------------------------------------

"""
    Verify the ESRT / connectivity preflight gates.
"""

def test_empty_esrt_raises() -> None:
    capsule = _build_capsule()
    empty_table = _build_esrt_table(entries=())

    agent = _StubAgent(connectivity_return=True)
    delivery = _StubCapsuleDelivery()
    report = _StubCapsuleReport()
    tables = _StubSystemTables(tables=[empty_table])
    reboot = _StubRebootWorkflow()

    workflow = _build_workflow(
        agent=agent, delivery=delivery, report=report,
        tables=tables, reboot=reboot,
    )

    with testbase.raises(UefiCapsuleError) as context:
        workflow.run(capsule=capsule)

    message = str(context.exception)
    testbase.assert_true("no ESRT on target" in message)
    testbase.assert_equal(len(delivery.calls), 0)
    testbase.assert_equal(reboot.calls, 0)
    return

def test_esrt_read_raises_wraps_error() -> None:
    capsule = _build_capsule()

    agent = _StubAgent(connectivity_return=True)
    delivery = _StubCapsuleDelivery()
    report = _StubCapsuleReport()
    tables = _StubSystemTables(
        tables=[None],
        exceptions=[RuntimeError("esrt sysfs missing")],
    )
    reboot = _StubRebootWorkflow()

    workflow = _build_workflow(
        agent=agent, delivery=delivery, report=report,
        tables=tables, reboot=reboot,
    )

    with testbase.raises(UefiCapsuleError) as context:
        workflow.run(capsule=capsule)

    message = str(context.exception)
    testbase.assert_true("no ESRT on target" in message)
    testbase.assert_equal(len(delivery.calls), 0)
    return

def test_guid_mismatch_raises() -> None:
    capsule = _build_capsule(capsule_guid=SAMPLE_CAPSULE_GUID)
    # ESRT only declares a different firmware class.
    before_table = _build_esrt_table(entries=(
        _build_esrt_entry(fw_class=OTHER_CAPSULE_GUID, fw_version=1),
    ))

    agent = _StubAgent(connectivity_return=True)
    delivery = _StubCapsuleDelivery()
    report = _StubCapsuleReport()
    tables = _StubSystemTables(tables=[before_table])
    reboot = _StubRebootWorkflow()

    workflow = _build_workflow(
        agent=agent, delivery=delivery, report=report,
        tables=tables, reboot=reboot,
    )

    with testbase.raises(UefiCapsuleError) as context:
        workflow.run(capsule=capsule)

    message = str(context.exception)
    testbase.assert_true("does not match any ESRT entry" in message)
    testbase.assert_equal(len(delivery.calls), 0)
    testbase.assert_equal(reboot.calls, 0)
    return

def test_target_unreachable_raises() -> None:
    capsule = _build_capsule()
    before_table = _build_esrt_table(entries=(
        _build_esrt_entry(fw_class=SAMPLE_CAPSULE_GUID, fw_version=1),
    ))

    agent = _StubAgent(connectivity_return=False)
    delivery = _StubCapsuleDelivery()
    report = _StubCapsuleReport()
    tables = _StubSystemTables(tables=[before_table])
    reboot = _StubRebootWorkflow()

    workflow = _build_workflow(
        agent=agent, delivery=delivery, report=report,
        tables=tables, reboot=reboot,
    )

    with testbase.raises(UefiCapsuleError) as context:
        workflow.run(capsule=capsule)

    message = str(context.exception)
    testbase.assert_true("not reachable" in message)
    testbase.assert_equal(len(delivery.calls), 0)
    testbase.assert_equal(reboot.calls, 0)
    return


# ---------------------------------------------------------------------------
# Post-reboot report fetch failure
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# CapsuleUpdateWorkflowPostRebootTests
# ----------------------------------------------------------------------

"""
    Verify the post-reboot stages of the workflow.
"""

def test_missing_report_after_reboot_raises() -> None:
    capsule = _build_capsule()
    before_table = _build_esrt_table(entries=(
        _build_esrt_entry(fw_class=SAMPLE_CAPSULE_GUID, fw_version=1),
    ))
    # after_table will never be read because report lookup raises
    # first; queue only the preflight table so the test asserts the
    # correct call count.

    agent = _StubAgent(connectivity_return=True)
    delivery = _StubCapsuleDelivery()
    # Report has no matching record.
    report = _StubCapsuleReport(records=())
    tables = _StubSystemTables(tables=[before_table])
    reboot = _StubRebootWorkflow()

    workflow = _build_workflow(
        agent=agent, delivery=delivery, report=report,
        tables=tables, reboot=reboot,
    )

    with testbase.raises(UefiCapsuleError) as context:
        workflow.run(capsule=capsule)

    message = str(context.exception)
    testbase.assert_true("no capsule-report record matched" in message)
    testbase.assert_equal(len(delivery.calls), 1)
    testbase.assert_equal(reboot.calls, 1)
    testbase.assert_equal(report.calls, 1)
    return

def test_report_fetch_exception_wraps() -> None:
    capsule = _build_capsule()
    before_table = _build_esrt_table(entries=(
        _build_esrt_entry(fw_class=SAMPLE_CAPSULE_GUID, fw_version=1),
    ))

    agent = _StubAgent(connectivity_return=True)
    delivery = _StubCapsuleDelivery()
    report = _StubCapsuleReport(
        exception_to_raise=RuntimeError("transport down"),
    )
    tables = _StubSystemTables(tables=[before_table])
    reboot = _StubRebootWorkflow()

    workflow = _build_workflow(
        agent=agent, delivery=delivery, report=report,
        tables=tables, reboot=reboot,
    )

    with testbase.raises(UefiCapsuleError) as context:
        workflow.run(capsule=capsule)

    message = str(context.exception)
    testbase.assert_true("list_reports failed after reboot" in message)
    return

def test_version_not_advanced_yields_success_false() -> None:
    capsule = _build_capsule()
    before_table = _build_esrt_table(entries=(
        _build_esrt_entry(fw_class=SAMPLE_CAPSULE_GUID, fw_version=5),
    ))
    # fw_version did not advance.
    after_table = _build_esrt_table(entries=(
        _build_esrt_entry(fw_class=SAMPLE_CAPSULE_GUID, fw_version=5),
    ))
    report_entry = _build_report_entry(capsule_guid=SAMPLE_CAPSULE_GUID)

    agent = _StubAgent(connectivity_return=True)
    delivery = _StubCapsuleDelivery()
    report = _StubCapsuleReport(records=(report_entry,))
    tables = _StubSystemTables(tables=[before_table, after_table])
    reboot = _StubRebootWorkflow()

    workflow = _build_workflow(
        agent=agent, delivery=delivery, report=report,
        tables=tables, reboot=reboot,
    )

    result = workflow.run(capsule=capsule)

    testbase.assert_false(result.success)
    testbase.assert_equal(result.esrt_before, before_table.entries)
    testbase.assert_equal(result.esrt_after, after_table.entries)
    testbase.assert_equal(result.reported_status_guid, SAMPLE_CAPSULE_GUID)
    return
