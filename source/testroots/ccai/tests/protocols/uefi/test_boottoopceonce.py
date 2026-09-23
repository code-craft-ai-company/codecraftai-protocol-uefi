"""
    .. module:: test_boottoopceonce
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Unit tests for the :class:`BootToOptionOnceWorkflow`
               layer-6 orchestration defined in
               :mod:`ccai.protocols.uefi.boottoopceonce`.  The
               tests use three lightweight fixtures:

               * ``_FakeUefiAgent`` — a minimal agent shim exposing
                 only the attributes the workflow reads.
               * ``_FakeBootManager`` — a duck-typed
                 :class:`BootManager` replacement with controllable
                 ``get_boot_option`` / ``set_boot_next`` /
                 ``get_boot_current`` / ``get_boot_next`` responses.
                 Inherits from :class:`BootManager` via a thin
                 bypass-``__init__`` path so it satisfies type-check
                 sites that ``isinstance`` against :class:`BootManager`.
               * ``_FakeRebootWorkflow`` — a do-nothing replacement
                 for the layer-6 reboot workflow that records every
                 call without actually rebooting.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Code Craft AI LLC"
__credits__ = []


from ccai import testbase

from typing import Any, Dict, List, Optional

from ccai.protocols.uefi.uefiaspects import UefiCmdAspects
from ccai.protocols.uefi.uefibootmanager import BootManager, BootOption
from ccai.protocols.uefi.uefidevicepath import DevicePath
from ccai.protocols.uefi.uefiexceptions import UefiVariableError
from ccai.protocols.uefi.uefireset import ResetKind
from ccai.protocols.uefi.uefirebootworkflow import RebootResult
from ccai.protocols.uefi.boottoopceonce import (
    BootOnceResult,
    BootToOptionOnceWorkflow,
)


# ---------------------------------------------------------------------------
# Fake agent fixture
# ---------------------------------------------------------------------------


class _FakeUefiAgent:
    """
        Minimal stand-in for
        :class:`ccai.protocols.uefi.uefiagent.UefiAgent`.  Only
        the ``host`` and ``transport_kind`` attributes are read by
        the workflow under test.
    """

    def __init__(self, host: str = "target.example",
                       transport_kind: str = "serial") -> None:
        """
            Initialize the fake agent.

            :param host: Target host string.
            :param transport_kind: Transport kind selector.
        """
        self.host: str = host
        self.transport_kind: str = transport_kind
        return


# ---------------------------------------------------------------------------
# Fake BootManager fixture
# ---------------------------------------------------------------------------


class _FakeBootManager(BootManager):
    """
        Minimal stand-in for
        :class:`ccai.protocols.uefi.uefibootmanager.BootManager`.

        Provides controllable return values for the four methods the
        workflow exercises:

        * ``get_boot_option(index, aspects)``
        * ``set_boot_next(index, aspects)``
        * ``get_boot_current(aspects)``
        * ``get_boot_next(aspects)``

        Inherits from :class:`BootManager` so
        :class:`BootToOptionOnceWorkflow`'s type hints line up;
        constructor dispatch is bypassed so no real
        :class:`VariableStore` is required.
    """

    def __init__(self) -> None:
        """
            Initialize the fake boot manager with empty recording
            surfaces and default return values.
        """
        # Deliberately skip :class:`BootManager.__init__` — we do not
        # want to construct a real :class:`VariableStore` just to be
        # an instance.
        self.get_boot_option_calls: List[Dict[str, Any]] = []
        self.set_boot_next_calls: List[Dict[str, Any]] = []
        self.get_boot_current_calls: List[Dict[str, Any]] = []
        self.get_boot_next_calls: List[Dict[str, Any]] = []

        self.get_boot_option_exception: Optional[BaseException] = None
        self.get_boot_option_return: Optional[BootOption] = None

        self.set_boot_next_exception: Optional[BaseException] = None

        self.get_boot_current_return: Optional[int] = None
        self.get_boot_next_return: Optional[int] = None
        return

    def get_boot_option(self, index: int,
                              aspects: Optional[UefiCmdAspects] = None) -> BootOption:
        """
            Record the call and return the programmed
            :class:`BootOption` (or raise the programmed exception).

            :param index: Option index.
            :param aspects: Per-call aspects.

            :returns: The programmed :class:`BootOption`.
            :raises: The programmed exception, if any.
        """
        self.get_boot_option_calls.append({
            "index": index,
            "aspects": aspects,
        })
        if self.get_boot_option_exception is not None:
            raise self.get_boot_option_exception
        if self.get_boot_option_return is not None:
            rtnval = self.get_boot_option_return
            return rtnval
        # Build a throwaway BootOption when no programmed value is set.
        rtnval = BootOption(
            attributes=0,
            description="test",
            file_path=DevicePath(nodes=tuple()),
            optional_data=b"",
        )
        return rtnval

    def set_boot_next(self, index: int,
                            aspects: Optional[UefiCmdAspects] = None) -> None:
        """
            Record the call and optionally raise the programmed
            exception.

            :param index: Option index to write into ``BootNext``.
            :param aspects: Per-call aspects.

            :raises: The programmed exception, if any.
        """
        self.set_boot_next_calls.append({
            "index": index,
            "aspects": aspects,
        })
        if self.set_boot_next_exception is not None:
            raise self.set_boot_next_exception
        return

    def get_boot_current(self, aspects: Optional[UefiCmdAspects] = None) -> Optional[int]:
        """
            Record the call and return the programmed value.

            :param aspects: Per-call aspects.

            :returns: Programmed ``BootCurrent`` value.
        """
        self.get_boot_current_calls.append({
            "aspects": aspects,
        })
        rtnval = self.get_boot_current_return
        return rtnval

    def get_boot_next(self, aspects: Optional[UefiCmdAspects] = None) -> Optional[int]:
        """
            Record the call and return the programmed value.

            :param aspects: Per-call aspects.

            :returns: Programmed ``BootNext`` value.
        """
        self.get_boot_next_calls.append({
            "aspects": aspects,
        })
        rtnval = self.get_boot_next_return
        return rtnval


# ---------------------------------------------------------------------------
# Fake RebootWorkflow fixture
# ---------------------------------------------------------------------------


class _FakeRebootWorkflow:
    """
        Minimal stand-in for
        :class:`ccai.protocols.uefi.uefirebootworkflow.RebootWorkflow`.

        Records every ``reboot_and_verify`` call without actually
        rebooting.  Returns a pre-configured
        :class:`RebootResult` so the caller observes the shape it
        would from the real workflow.
    """

    def __init__(self) -> None:
        """
            Initialize the fake reboot workflow with an empty
            call-recording list and a default success result.
        """
        self.reboot_calls: List[Dict[str, Any]] = []
        self.result: RebootResult = RebootResult(
            success=True,
            elapsed_seconds=0.0,
            shell_ready=True,
            captured_console=None,
            reset_kind=ResetKind.WARM,
        )
        return

    def reboot_and_verify(self, kind: ResetKind = ResetKind.WARM,
                                wait_for_shell: bool = True,
                                capture_console: bool = False,
                                aspects: Optional[UefiCmdAspects] = None) -> RebootResult:
        """
            Record the call and return the programmed result.

            :param kind: Reset kind.
            :param wait_for_shell: Whether to verify the Shell.
            :param capture_console: Whether to capture console bytes.
            :param aspects: Per-call aspects.

            :returns: The programmed :class:`RebootResult`.
        """
        self.reboot_calls.append({
            "kind": kind,
            "wait_for_shell": wait_for_shell,
            "capture_console": capture_console,
            "aspects": aspects,
        })
        rtnval = self.result
        return rtnval


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _build_workflow() -> Dict[str, Any]:
    """
        Build a :class:`BootToOptionOnceWorkflow` plus its three
        collaborators for use in a single test.

        :returns: A mapping with keys ``agent``, ``boot_manager``,
                  ``reboot_workflow``, and ``workflow``.
    """
    agent = _FakeUefiAgent()
    boot_manager = _FakeBootManager()
    reboot_workflow = _FakeRebootWorkflow()
    workflow = BootToOptionOnceWorkflow(
        agent=agent,
        boot_manager=boot_manager,
        reboot_workflow=reboot_workflow,
    )
    rtnval: Dict[str, Any] = {
        "agent": agent,
        "boot_manager": boot_manager,
        "reboot_workflow": reboot_workflow,
        "workflow": workflow,
    }
    return rtnval


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


# ----------------------------------------------------------------------
# TestBootOnceResult
# ----------------------------------------------------------------------

"""
    Verify the :class:`BootOnceResult` dataclass shape.
"""

def test_fields_populated() -> None:
    """
        All fields can be populated explicitly and are readable.
    """
    result = BootOnceResult(
        success=True,
        observed_boot_current=0x0001,
        requested_index=0x0001,
    )
    testbase.assert_true(result.success is True)
    testbase.assert_equal(result.observed_boot_current, 0x0001)
    testbase.assert_equal(result.requested_index, 0x0001)
    return

def test_observed_boot_current_may_be_none() -> None:
    """
        ``observed_boot_current`` may be ``None`` when the
        variable was absent post-reboot.
    """
    result = BootOnceResult(
        success=False,
        observed_boot_current=None,
        requested_index=0x0001,
    )
    testbase.assert_true(result.observed_boot_current is None)
    return


# ----------------------------------------------------------------------
# TestBootToOptionOnceHappyPath
# ----------------------------------------------------------------------

"""
    Verify the happy-path orchestration.
"""

def test_boot_once_success() -> None:
    """
        Happy path: option exists, ``BootNext`` written, reboot
        invoked, ``BootCurrent`` matches, ``BootNext`` cleared.
        Result reports ``success=True``.
    """
    parts = _build_workflow()
    boot_manager: _FakeBootManager = parts["boot_manager"]
    reboot_workflow: _FakeRebootWorkflow = parts["reboot_workflow"]
    workflow: BootToOptionOnceWorkflow = parts["workflow"]

    boot_manager.get_boot_current_return = 0x0000
    boot_manager.get_boot_next_return = None  # firmware cleared it

    result = workflow.boot_once(index=0x0000)

    testbase.assert_true(isinstance(result, BootOnceResult))
    testbase.assert_true(result.success is True)
    testbase.assert_equal(result.observed_boot_current, 0x0000)
    testbase.assert_equal(result.requested_index, 0x0000)

    # Existence check issued with the requested index.
    testbase.assert_equal(len(boot_manager.get_boot_option_calls), 1)
    testbase.assert_equal(
        boot_manager.get_boot_option_calls[0]["index"],
        0x0000,
    )
    # BootNext write issued with the requested index.
    testbase.assert_equal(len(boot_manager.set_boot_next_calls), 1)
    testbase.assert_equal(
        boot_manager.set_boot_next_calls[0]["index"],
        0x0000,
    )
    # Reboot invoked exactly once with WARM kind and wait_for_shell.
    testbase.assert_equal(len(reboot_workflow.reboot_calls), 1)
    testbase.assert_equal(
        reboot_workflow.reboot_calls[0]["kind"],
        ResetKind.WARM,
    )
    testbase.assert_true(reboot_workflow.reboot_calls[0]["wait_for_shell"] is True)
    # BootCurrent / BootNext each read exactly once post-reboot.
    testbase.assert_equal(len(boot_manager.get_boot_current_calls), 1)
    testbase.assert_equal(len(boot_manager.get_boot_next_calls), 1)
    return

def test_boot_once_with_nonzero_index() -> None:
    """
        A non-zero option index is forwarded unchanged to every
        collaborator.
    """
    parts = _build_workflow()
    boot_manager: _FakeBootManager = parts["boot_manager"]
    workflow: BootToOptionOnceWorkflow = parts["workflow"]

    boot_manager.get_boot_current_return = 0x0005
    boot_manager.get_boot_next_return = None

    result = workflow.boot_once(index=0x0005)

    testbase.assert_true(result.success is True)
    testbase.assert_equal(result.requested_index, 0x0005)
    testbase.assert_equal(result.observed_boot_current, 0x0005)
    testbase.assert_equal(
        boot_manager.set_boot_next_calls[0]["index"],
        0x0005,
    )
    return


# ----------------------------------------------------------------------
# TestBootToOptionOnceMissingOption
# ----------------------------------------------------------------------

"""
    Verify the existence-check failure path.
"""

def test_missing_boot_option_raises_uefi_variable_error() -> None:
    """
        When :meth:`BootManager.get_boot_option` raises
        :class:`UefiVariableError` the workflow re-raises with
        an explanatory message naming the missing variable and
        does NOT write ``BootNext`` or invoke the reboot.
    """
    parts = _build_workflow()
    boot_manager: _FakeBootManager = parts["boot_manager"]
    reboot_workflow: _FakeRebootWorkflow = parts["reboot_workflow"]
    workflow: BootToOptionOnceWorkflow = parts["workflow"]

    boot_manager.get_boot_option_exception = UefiVariableError(
        "variable not found",
        name="Boot0007",
    )

    caught: Optional[BaseException] = None
    try:
        workflow.boot_once(index=0x0007)
    except UefiVariableError as verr:
        caught = verr

    testbase.assert_true(isinstance(caught, UefiVariableError))
    message_text = str(caught)
    testbase.assert_true("Boot0007" in message_text)
    testbase.assert_true("not present" in message_text)

    testbase.assert_equal(len(boot_manager.set_boot_next_calls), 0)
    testbase.assert_equal(len(reboot_workflow.reboot_calls), 0)
    return


# ----------------------------------------------------------------------
# TestBootToOptionOnceBootNextNotCleared
# ----------------------------------------------------------------------

"""
    Verify the UEFI §3.1.3 / §3.1.5 auto-clear enforcement.
"""

def test_boot_next_still_set_to_same_value_raises() -> None:
    """
        When ``BootNext`` is still set to the originally-written
        value post-reboot the workflow raises
        :class:`UefiVariableError` citing UEFI §3.1.3 / §3.1.5.
    """
    parts = _build_workflow()
    boot_manager: _FakeBootManager = parts["boot_manager"]
    workflow: BootToOptionOnceWorkflow = parts["workflow"]

    boot_manager.get_boot_current_return = 0x0002
    boot_manager.get_boot_next_return = 0x0002  # firmware did not clear

    caught: Optional[BaseException] = None
    try:
        workflow.boot_once(index=0x0002)
    except UefiVariableError as verr:
        caught = verr

    testbase.assert_true(isinstance(caught, UefiVariableError))
    message_text = str(caught)
    testbase.assert_true("firmware did not clear BootNext" in message_text)
    return

def test_boot_next_persists_with_different_value_also_raises() -> None:
    """
        When ``BootNext`` persists but points at a different
        option the workflow still raises — the UEFI §3.1.3 /
        §3.1.5 invariant is that ``BootNext`` SHALL be cleared
        after the next boot, period.
    """
    parts = _build_workflow()
    boot_manager: _FakeBootManager = parts["boot_manager"]
    workflow: BootToOptionOnceWorkflow = parts["workflow"]

    boot_manager.get_boot_current_return = 0x0001
    boot_manager.get_boot_next_return = 0x0003  # unexpected residual

    caught: Optional[BaseException] = None
    try:
        workflow.boot_once(index=0x0001)
    except UefiVariableError as verr:
        caught = verr

    testbase.assert_true(isinstance(caught, UefiVariableError))
    message_text = str(caught)
    testbase.assert_true("BootNext" in message_text)
    testbase.assert_true("0x0003" in message_text)
    return


# ----------------------------------------------------------------------
# TestBootToOptionOnceBootCurrentMismatch
# ----------------------------------------------------------------------

"""
    Verify the BootCurrent-mismatch result path.  Per the design
    the workflow does not raise on a BootCurrent mismatch; it
    returns ``success=False`` with the observed value so the
    caller can triage.
"""

def test_boot_current_differs_yields_success_false() -> None:
    """
        Firmware booted a different option: ``BootCurrent !=
        requested``.  The workflow returns
        ``BootOnceResult(success=False)`` with the observed value
        on the result.
    """
    parts = _build_workflow()
    boot_manager: _FakeBootManager = parts["boot_manager"]
    workflow: BootToOptionOnceWorkflow = parts["workflow"]

    boot_manager.get_boot_current_return = 0x0004
    boot_manager.get_boot_next_return = None  # firmware cleared

    result = workflow.boot_once(index=0x0001)

    testbase.assert_true(result.success is False)
    testbase.assert_equal(result.observed_boot_current, 0x0004)
    testbase.assert_equal(result.requested_index, 0x0001)
    return

def test_boot_current_absent_yields_success_false() -> None:
    """
        Firmware did not expose ``BootCurrent``; the workflow
        returns ``success=False`` with ``observed_boot_current=None``.
    """
    parts = _build_workflow()
    boot_manager: _FakeBootManager = parts["boot_manager"]
    workflow: BootToOptionOnceWorkflow = parts["workflow"]

    boot_manager.get_boot_current_return = None
    boot_manager.get_boot_next_return = None

    result = workflow.boot_once(index=0x0001)

    testbase.assert_true(result.success is False)
    testbase.assert_true(result.observed_boot_current is None)
    return


# ----------------------------------------------------------------------
# TestBootToOptionOnceAspectsForwarding
# ----------------------------------------------------------------------

"""
    Verify that a per-call ``aspects`` override is resolved once
    and forwarded to every collaborator.
"""

def test_per_call_aspects_forwarded() -> None:
    """
        A per-call aspects override is forwarded verbatim to
        ``BootManager.get_boot_option``, ``BootManager.set_boot_next``,
        ``RebootWorkflow.reboot_and_verify``, and the two
        post-reboot reads.
    """
    parts = _build_workflow()
    boot_manager: _FakeBootManager = parts["boot_manager"]
    reboot_workflow: _FakeRebootWorkflow = parts["reboot_workflow"]
    workflow: BootToOptionOnceWorkflow = parts["workflow"]

    boot_manager.get_boot_current_return = 0x0000
    boot_manager.get_boot_next_return = None

    per_call_aspects = UefiCmdAspects()

    workflow.boot_once(index=0x0000, aspects=per_call_aspects)

    testbase.assert_true(boot_manager.get_boot_option_calls[0]["aspects"] is per_call_aspects)
    testbase.assert_true(boot_manager.set_boot_next_calls[0]["aspects"] is per_call_aspects)
    testbase.assert_true(reboot_workflow.reboot_calls[0]["aspects"] is per_call_aspects)
    testbase.assert_true(boot_manager.get_boot_current_calls[0]["aspects"] is per_call_aspects)
    testbase.assert_true(boot_manager.get_boot_next_calls[0]["aspects"] is per_call_aspects)
    return
