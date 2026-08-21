"""
    .. module:: test_uefirebootworkflow
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Unit tests for the :class:`RebootWorkflow` layer-6
               orchestration defined in
               :mod:`ccai.protocols.uefi.uefirebootworkflow`.  The
               tests use two lightweight fixtures: a
               ``_FakeUefiAgent`` that exposes a controllable
               ``verify_connectivity`` return value and optional
               console-capture hooks, and a patched
               :class:`ccai.protocols.uefi.uefireset.SystemReset`
               that records every ``reset`` call without driving a
               real transport.  ``time.sleep`` is patched on the
               :mod:`ccai.protocols.uefi.uefireset` module so the
               reconnect-verify loop completes instantly.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Code Craft AI LLC"
__credits__ = []



from types import SimpleNamespace

from ccai import testbase

from typing import Any, Dict, List, Optional
from unittest.mock import patch

from ccai.protocols.uefi import uefireset
from ccai.protocols.uefi import uefirebootworkflow
from ccai.protocols.uefi.uefiaspects import UefiCmdAspects
from ccai.protocols.uefi.uefireset import (
    ResetFailedError,
    ResetKind,
)
from ccai.protocols.uefi.uefirebootworkflow import (
    RebootResult,
    RebootWorkflow,
)


# ---------------------------------------------------------------------------
# Fake agent fixture
# ---------------------------------------------------------------------------


class _FakeUefiAgent:
    """
        Minimal stand-in for
        :class:`ccai.protocols.uefi.uefiagent.UefiAgent`.

        Exposes a controllable ``verify_connectivity`` return value,
        records every call, and optionally exposes
        ``begin_console_capture`` / ``end_console_capture`` hooks so
        the workflow's best-effort capture path can be exercised.
    """

    def __init__(self, transport_kind: str = "serial",
                       host: str = "target.example") -> None:
        """
            Initialize the fake agent with a configurable
            ``transport_kind`` and recording surfaces.

            :param transport_kind: One of ``'serial'``, ``'redfish'``,
                                   ``'ssh-host'``, ``'ipmi-sol'``.
            :param host: Target host string.
        """
        self.transport_kind: str = transport_kind
        self.host: str = host

        self.verify_calls: int = 0
        self.verify_sequence: List[bool] = []
        self.verify_default: bool = True

        self.run_cmd_calls: List[Dict[str, Any]] = []
        self.run_cmd_return = (0, "", "")

        self.begin_capture_calls: int = 0
        self.end_capture_calls: int = 0
        self.capture_bytes: Optional[bytes] = None
        self.enable_capture_hooks: bool = False
        return

    def verify_connectivity(self) -> bool:
        """
            Replay the next boolean from ``verify_sequence``; when the
            sequence is exhausted return ``verify_default``.

            :returns: Programmed boolean outcome of the probe.
        """
        self.verify_calls = self.verify_calls + 1
        if len(self.verify_sequence) == 0:
            rtnval = self.verify_default
        else:
            rtnval = self.verify_sequence.pop(0)
        return rtnval

    def run_cmd(self, command: str,
                      aspects: Optional[UefiCmdAspects] = None) -> Any:
        """
            Record a ``run_cmd`` invocation.  Needed because the
            patched :class:`SystemReset` replacement drives its reset
            dispatch through this method.

            :param command: The command string.
            :param aspects: Per-call aspects.

            :returns: The programmed three-tuple.
        """
        self.run_cmd_calls.append({
            "command": command,
            "aspects": aspects,
        })
        rtnval = self.run_cmd_return
        return rtnval

    def begin_console_capture(self) -> Any:
        """
            Record a best-effort console-capture start.  Only bound on
            the agent when ``enable_capture_hooks=True``; otherwise
            ``__getattr__`` hides it from
            :meth:`RebootWorkflow._begin_console_capture`.

            :returns: An opaque handle (a sentinel string for the test).
        """
        self.begin_capture_calls = self.begin_capture_calls + 1
        rtnval = "handle-{}".format(self.begin_capture_calls)
        return rtnval

    def end_console_capture(self, handle: Any) -> Optional[bytes]:
        """
            Record a best-effort console-capture end and return the
            programmed ``capture_bytes``.

            :param handle: The handle returned by
                           :meth:`begin_console_capture`.

            :returns: The programmed capture bytes, or ``None``.
        """
        self.end_capture_calls = self.end_capture_calls + 1
        rtnval = self.capture_bytes
        return rtnval

    def __getattr__(self, name: str) -> Any:
        """
            Hide ``begin_console_capture`` / ``end_console_capture``
            unless ``enable_capture_hooks=True``.  The workflow's
            capture path performs a ``getattr(agent, ...)`` lookup and
            silently skips capture when the attribute is absent.

            :param name: Attribute name being looked up.

            :returns: The bound method when the hook is enabled.
            :raises: AttributeError when the hook is disabled.
        """
        hook_names = ("begin_console_capture", "end_console_capture")
        if name in hook_names:
            use_flag = self.__dict__.get("enable_capture_hooks", False)
            if use_flag is False:
                err_msg = "_FakeUefiAgent has no attribute {!r}".format(name)
                raise AttributeError(err_msg)
            bound = super().__getattribute__(name)
            return bound

        err_msg = "_FakeUefiAgent has no attribute {!r}".format(name)
        raise AttributeError(err_msg)


# ---------------------------------------------------------------------------
# Mock SystemReset
# ---------------------------------------------------------------------------


class _MockSystemReset:
    """
        Stand-in for
        :class:`ccai.protocols.uefi.uefireset.SystemReset` that
        records every constructor and ``reset`` invocation without
        driving a real transport.  A class-level recording list is
        used so tests can introspect all instances created by the
        workflow during a single call.
    """

    construction_calls: List[Dict[str, Any]] = []
    reset_calls: List[Dict[str, Any]] = []
    reset_side_effect: Optional[BaseException] = None

    def __init__(self, agent: Any,
                       aspects: Optional[UefiCmdAspects] = None) -> None:
        """
            Record the construction call.

            :param agent: The owning agent.
            :param aspects: Default aspects.
        """
        _MockSystemReset.construction_calls.append({
            "agent": agent,
            "aspects": aspects,
        })
        self._agent = agent
        self._aspects = aspects
        return

    def reset(self, kind: ResetKind = ResetKind.WARM,
                    reason: Optional[str] = None,
                    platform_specific_data: Optional[bytes] = None,
                    wait_for_reconnect: bool = True,
                    aspects: Optional[UefiCmdAspects] = None) -> None:
        """
            Record the ``reset`` invocation and optionally raise the
            programmed side-effect exception.

            :param kind: Reset kind.
            :param reason: Optional reason string.
            :param platform_specific_data: Optional payload.
            :param wait_for_reconnect: Whether to wait for reconnect.
            :param aspects: Per-call aspects.
        """
        _MockSystemReset.reset_calls.append({
            "kind": kind,
            "reason": reason,
            "platform_specific_data": platform_specific_data,
            "wait_for_reconnect": wait_for_reconnect,
            "aspects": aspects,
        })
        if _MockSystemReset.reset_side_effect is not None:
            raise _MockSystemReset.reset_side_effect
        return

    @classmethod
    def reset_recording(cls) -> None:
        """
            Clear class-level call recording and side-effect state.
        """
        cls.construction_calls = []
        cls.reset_calls = []
        cls.reset_side_effect = None
        return


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _build_workflow(agent: _FakeUefiAgent,
                    aspects: Optional[UefiCmdAspects] = None) -> RebootWorkflow:
    """
        Construct a :class:`RebootWorkflow` bound to the given fake
        agent.  Mirrors ``_build_reset`` from ``test_uefireset`` so
        the two test modules read similarly.

        :param agent: The fake agent.
        :param aspects: Optional instance-default aspects.

        :returns: A ready-to-drive :class:`RebootWorkflow`.
    """
    if aspects is None:
        rtnval = RebootWorkflow(agent=agent)
    else:
        rtnval = RebootWorkflow(agent=agent, aspects=aspects)
    return rtnval


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


# ----------------------------------------------------------------------
# TestRebootResult
# ----------------------------------------------------------------------

"""
    Verify the :class:`RebootResult` dataclass shape and defaults.
"""

def test_default_reset_kind_is_warm() -> None:
    """
        Omitting ``reset_kind`` yields :class:`ResetKind.WARM`.
    """
    result = RebootResult(
        success=True,
        elapsed_seconds=0.0,
        shell_ready=True,
    )
    testbase.assert_equal(result.reset_kind, ResetKind.WARM)
    testbase.assert_true(result.captured_console is None)
    return

def test_all_fields_populated() -> None:
    """
        All fields can be populated explicitly and are readable.
    """
    result = RebootResult(
        success=True,
        elapsed_seconds=1.25,
        shell_ready=True,
        captured_console="boot log",
        reset_kind=ResetKind.COLD,
    )
    testbase.assert_true(result.success is True)
    testbase.assert_true(abs(result.elapsed_seconds - 1.25) < 1e-6)
    testbase.assert_true(result.shell_ready is True)
    testbase.assert_equal(result.captured_console, "boot log")
    testbase.assert_equal(result.reset_kind, ResetKind.COLD)
    return


# ----------------------------------------------------------------------
# TestRebootWorkflowHappyPath
# ----------------------------------------------------------------------

"""
    Verify that the happy-path orchestration drives SystemReset
    and verify_connectivity in the documented order.
"""

def _setup_reboot_workflow_happy_path(ctx) -> None:
    """
        Reset the class-level mock recording before every test.
    """
    _MockSystemReset.reset_recording()
    return

def test_reset_succeeds_and_verify_returns_true() -> None:
    """
        Successful reset and ``verify_connectivity=True`` yields
        ``RebootResult(success=True, shell_ready=True)``.
    """
    ctx = SimpleNamespace()
    _setup_reboot_workflow_happy_path(ctx)
    agent = _FakeUefiAgent(transport_kind="serial")
    agent.verify_sequence = [True]

    workflow = _build_workflow(agent=agent)

    with patch.object(uefirebootworkflow, "SystemReset", _MockSystemReset):
        with patch.object(uefireset.time, "sleep"):
            result = workflow.reboot_and_verify(kind=ResetKind.WARM)

    testbase.assert_true(isinstance(result, RebootResult))
    testbase.assert_true(result.success is True)
    testbase.assert_true(result.shell_ready is True)
    testbase.assert_equal(result.reset_kind, ResetKind.WARM)
    testbase.assert_equal(agent.verify_calls, 1)
    testbase.assert_equal(len(_MockSystemReset.reset_calls), 1)
    recorded = _MockSystemReset.reset_calls[0]
    testbase.assert_equal(recorded["kind"], ResetKind.WARM)
    testbase.assert_true(recorded["wait_for_reconnect"] is True)
    return

def test_default_kind_is_warm() -> None:
    """
        Omitting ``kind`` defaults to :class:`ResetKind.WARM`.
    """
    ctx = SimpleNamespace()
    _setup_reboot_workflow_happy_path(ctx)
    agent = _FakeUefiAgent(transport_kind="serial")
    agent.verify_sequence = [True]

    workflow = _build_workflow(agent=agent)

    with patch.object(uefirebootworkflow, "SystemReset", _MockSystemReset):
        with patch.object(uefireset.time, "sleep"):
            result = workflow.reboot_and_verify()

    testbase.assert_equal(result.reset_kind, ResetKind.WARM)
    testbase.assert_equal(_MockSystemReset.reset_calls[0]["kind"], ResetKind.WARM)
    return

def test_cold_reset_forwarded_to_system_reset() -> None:
    """
        A caller-supplied :class:`ResetKind.COLD` is forwarded to
        the underlying :class:`SystemReset`.
    """
    ctx = SimpleNamespace()
    _setup_reboot_workflow_happy_path(ctx)
    agent = _FakeUefiAgent(transport_kind="serial")
    agent.verify_sequence = [True]

    workflow = _build_workflow(agent=agent)

    with patch.object(uefirebootworkflow, "SystemReset", _MockSystemReset):
        with patch.object(uefireset.time, "sleep"):
            result = workflow.reboot_and_verify(kind=ResetKind.COLD)

    testbase.assert_equal(result.reset_kind, ResetKind.COLD)
    testbase.assert_equal(_MockSystemReset.reset_calls[0]["kind"], ResetKind.COLD)
    return


# ----------------------------------------------------------------------
# TestRebootWorkflowResetFailure
# ----------------------------------------------------------------------

"""
    Verify that a :class:`ResetFailedError` raised by the
    underlying reset is captured and surfaced as a failed
    :class:`RebootResult` rather than propagated.
"""

def _setup_reboot_workflow_reset_failure(ctx) -> None:
    """
        Reset the class-level mock recording before every test.
    """
    _MockSystemReset.reset_recording()
    return

def test_reset_failed_yields_failed_result() -> None:
    """
        A :class:`ResetFailedError` raised by
        :meth:`SystemReset.reset` results in a
        ``RebootResult(success=False, shell_ready=False)`` with
        no propagation.
    """
    ctx = SimpleNamespace()
    _setup_reboot_workflow_reset_failure(ctx)
    agent = _FakeUefiAgent(transport_kind="serial")
    agent.verify_sequence = [True]

    reset_exception = ResetFailedError(
        "target did not return within completion_timeout",
        details=["kind=warm", "host=target.example"],
    )
    _MockSystemReset.reset_side_effect = reset_exception

    workflow = _build_workflow(agent=agent)

    with patch.object(uefirebootworkflow, "SystemReset", _MockSystemReset):
        with patch.object(uefireset.time, "sleep"):
            result = workflow.reboot_and_verify(kind=ResetKind.WARM)

    testbase.assert_true(isinstance(result, RebootResult))
    testbase.assert_true(result.success is False)
    testbase.assert_true(result.shell_ready is False)
    testbase.assert_equal(result.reset_kind, ResetKind.WARM)
    # verify_connectivity must not be called when the reset
    # failed, because there is no transport to probe.
    testbase.assert_equal(agent.verify_calls, 0)
    return


# ----------------------------------------------------------------------
# TestRebootWorkflowWaitForShellFlag
# ----------------------------------------------------------------------

"""
    Verify the ``wait_for_shell`` flag controls the post-reset
    Shell probe.
"""

def _setup_reboot_workflow_wait_for_shell_flag(ctx) -> None:
    """
        Reset the class-level mock recording before every test.
    """
    _MockSystemReset.reset_recording()
    return

def test_wait_for_shell_false_skips_verify() -> None:
    """
        ``wait_for_shell=False`` skips the
        ``verify_connectivity`` call entirely.  The result's
        ``shell_ready`` field is ``False`` and ``success`` is
        ``True`` once the reset-side-effect-free path returns.
    """
    ctx = SimpleNamespace()
    _setup_reboot_workflow_wait_for_shell_flag(ctx)
    agent = _FakeUefiAgent(transport_kind="serial")

    workflow = _build_workflow(agent=agent)

    with patch.object(uefirebootworkflow, "SystemReset", _MockSystemReset):
        with patch.object(uefireset.time, "sleep"):
            result = workflow.reboot_and_verify(
                kind=ResetKind.WARM,
                wait_for_shell=False,
            )

    testbase.assert_equal(agent.verify_calls, 0)
    testbase.assert_true(result.shell_ready is False)
    testbase.assert_true(result.success is True)
    return

def test_wait_for_shell_true_invokes_verify() -> None:
    """
        ``wait_for_shell=True`` (default) invokes
        ``verify_connectivity`` exactly once.
    """
    ctx = SimpleNamespace()
    _setup_reboot_workflow_wait_for_shell_flag(ctx)
    agent = _FakeUefiAgent(transport_kind="serial")
    agent.verify_sequence = [True]

    workflow = _build_workflow(agent=agent)

    with patch.object(uefirebootworkflow, "SystemReset", _MockSystemReset):
        with patch.object(uefireset.time, "sleep"):
            workflow.reboot_and_verify(
                kind=ResetKind.WARM,
                wait_for_shell=True,
            )

    testbase.assert_equal(agent.verify_calls, 1)
    return

def test_verify_returns_false_marks_result_failed() -> None:
    """
        ``wait_for_shell=True`` with ``verify_connectivity``
        returning ``False`` yields a failed result.
    """
    ctx = SimpleNamespace()
    _setup_reboot_workflow_wait_for_shell_flag(ctx)
    agent = _FakeUefiAgent(transport_kind="serial")
    agent.verify_sequence = [False]
    agent.verify_default = False

    workflow = _build_workflow(agent=agent)

    with patch.object(uefirebootworkflow, "SystemReset", _MockSystemReset):
        with patch.object(uefireset.time, "sleep"):
            result = workflow.reboot_and_verify(
                kind=ResetKind.WARM,
                wait_for_shell=True,
            )

    testbase.assert_true(result.shell_ready is False)
    testbase.assert_true(result.success is False)
    return


# ----------------------------------------------------------------------
# TestRebootWorkflowElapsedSeconds
# ----------------------------------------------------------------------

"""
    Verify that ``elapsed_seconds`` is computed via
    :func:`time.time` bracketing the core orchestration.
"""

def _setup_reboot_workflow_elapsed_seconds(ctx) -> None:
    """
        Reset the class-level mock recording before every test.
    """
    _MockSystemReset.reset_recording()
    return

def test_elapsed_seconds_computed_from_time_time() -> None:
    """
        Bracketing :func:`time.time` yields an
        ``elapsed_seconds`` equal to the difference between the
        two programmed values.
    """
    ctx = SimpleNamespace()
    _setup_reboot_workflow_elapsed_seconds(ctx)
    agent = _FakeUefiAgent(transport_kind="serial")
    agent.verify_sequence = [True]

    # Programmed time.time sequence: begin_time=100.0, end_time=102.5.
    # The patch below replaces the attribute on the stdlib time module, so
    # every caller inside the block shares this clock -- under testbase the
    # logging machinery is one of them.  Keying the reading off workflow
    # progress rather than off call count keeps the bracket deterministic
    # no matter how many times anything else asks for the time.
    def _fake_time() -> float:
        """
            Return the bracketing time.time() value for the current phase.

            :returns: 100.0 before the reset is issued, 102.5 afterwards.
        """
        if len(_MockSystemReset.reset_calls) == 0:
            rtnval = 100.0
        else:
            rtnval = 102.5

        return rtnval

    workflow = _build_workflow(agent=agent)

    with patch.object(uefirebootworkflow, "SystemReset", _MockSystemReset):
        with patch.object(uefirebootworkflow.time, "time", _fake_time):
            with patch.object(uefireset.time, "sleep"):
                result = workflow.reboot_and_verify(kind=ResetKind.WARM)

    testbase.assert_true(abs(result.elapsed_seconds - 2.5) < 1e-6)
    return

def test_elapsed_seconds_non_negative_on_failure() -> None:
    """
        Even on a failed reset the ``elapsed_seconds`` field is a
        non-negative float.
    """
    ctx = SimpleNamespace()
    _setup_reboot_workflow_elapsed_seconds(ctx)
    agent = _FakeUefiAgent(transport_kind="serial")
    agent.verify_sequence = [True]

    _MockSystemReset.reset_side_effect = ResetFailedError(
        "target did not return",
    )

    workflow = _build_workflow(agent=agent)

    with patch.object(uefirebootworkflow, "SystemReset", _MockSystemReset):
        with patch.object(uefireset.time, "sleep"):
            result = workflow.reboot_and_verify(kind=ResetKind.WARM)

    testbase.assert_true(result.elapsed_seconds >= 0.0)
    return


# ----------------------------------------------------------------------
# TestRebootWorkflowCaptureConsole
# ----------------------------------------------------------------------

"""
    Verify the best-effort console-capture path.
"""

def _setup_reboot_workflow_capture_console(ctx) -> None:
    """
        Reset the class-level mock recording before every test.
    """
    _MockSystemReset.reset_recording()
    return

def test_capture_console_false_never_calls_hooks() -> None:
    """
        With ``capture_console=False`` the begin / end hooks are
        never invoked and ``captured_console`` is ``None``.
    """
    ctx = SimpleNamespace()
    _setup_reboot_workflow_capture_console(ctx)
    agent = _FakeUefiAgent(transport_kind="serial")
    agent.verify_sequence = [True]
    agent.enable_capture_hooks = True
    agent.capture_bytes = b"firmware-diag"

    workflow = _build_workflow(agent=agent)

    with patch.object(uefirebootworkflow, "SystemReset", _MockSystemReset):
        with patch.object(uefireset.time, "sleep"):
            result = workflow.reboot_and_verify(
                kind=ResetKind.WARM,
                capture_console=False,
            )

    testbase.assert_equal(agent.begin_capture_calls, 0)
    testbase.assert_equal(agent.end_capture_calls, 0)
    testbase.assert_true(result.captured_console is None)
    return

def test_capture_console_true_invokes_hooks_and_decodes() -> None:
    """
        With ``capture_console=True`` and the hooks present the
        captured bytes are decoded as UTF-8 and placed on the
        result.
    """
    ctx = SimpleNamespace()
    _setup_reboot_workflow_capture_console(ctx)
    agent = _FakeUefiAgent(transport_kind="serial")
    agent.verify_sequence = [True]
    agent.enable_capture_hooks = True
    agent.capture_bytes = b"hello\n"

    workflow = _build_workflow(agent=agent)

    with patch.object(uefirebootworkflow, "SystemReset", _MockSystemReset):
        with patch.object(uefireset.time, "sleep"):
            result = workflow.reboot_and_verify(
                kind=ResetKind.WARM,
                capture_console=True,
            )

    testbase.assert_equal(agent.begin_capture_calls, 1)
    testbase.assert_equal(agent.end_capture_calls, 1)
    testbase.assert_equal(result.captured_console, "hello\n")
    return

def test_capture_console_true_without_hooks_is_noop() -> None:
    """
        With ``capture_console=True`` on an agent that does not
        expose the hooks the workflow silently skips capture.
    """
    ctx = SimpleNamespace()
    _setup_reboot_workflow_capture_console(ctx)
    agent = _FakeUefiAgent(transport_kind="redfish")
    agent.verify_sequence = [True]
    agent.enable_capture_hooks = False

    workflow = _build_workflow(agent=agent)

    with patch.object(uefirebootworkflow, "SystemReset", _MockSystemReset):
        with patch.object(uefireset.time, "sleep"):
            result = workflow.reboot_and_verify(
                kind=ResetKind.WARM,
                capture_console=True,
            )

    testbase.assert_true(result.captured_console is None)
    return


# ----------------------------------------------------------------------
# TestRebootWorkflowAspectsForwarding
# ----------------------------------------------------------------------

"""
    Verify that a per-call ``aspects`` override is resolved once
    and forwarded to :class:`SystemReset` construction and
    :meth:`SystemReset.reset`.
"""

def _setup_reboot_workflow_aspects_forwarding(ctx) -> None:
    """
        Reset the class-level mock recording before every test.
    """
    _MockSystemReset.reset_recording()
    return

def test_per_call_aspects_forwarded() -> None:
    """
        Per-call aspects supplied to ``reboot_and_verify`` are
        forwarded verbatim to the underlying :class:`SystemReset`.
    """
    ctx = SimpleNamespace()
    _setup_reboot_workflow_aspects_forwarding(ctx)
    agent = _FakeUefiAgent(transport_kind="serial")
    agent.verify_sequence = [True]

    instance_aspects = UefiCmdAspects()
    per_call_aspects = UefiCmdAspects()

    workflow = _build_workflow(agent=agent, aspects=instance_aspects)

    with patch.object(uefirebootworkflow, "SystemReset", _MockSystemReset):
        with patch.object(uefireset.time, "sleep"):
            workflow.reboot_and_verify(
                kind=ResetKind.WARM,
                aspects=per_call_aspects,
            )

    testbase.assert_equal(len(_MockSystemReset.construction_calls), 1)
    testbase.assert_true(_MockSystemReset.construction_calls[0]["aspects"] is per_call_aspects)
    testbase.assert_equal(len(_MockSystemReset.reset_calls), 1)
    testbase.assert_true(_MockSystemReset.reset_calls[0]["aspects"] is per_call_aspects)
    return
