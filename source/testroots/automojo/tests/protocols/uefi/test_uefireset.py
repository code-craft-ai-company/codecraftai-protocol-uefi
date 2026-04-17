"""
    .. module:: test_uefireset
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Unit tests for the :class:`SystemReset` facade, the
               :class:`ResetKind` enum, and the :class:`ResetFailedError`
               exception.  A lightweight ``_FakeUefiAgent`` stands in for
               the real agent: it records every ``run_cmd`` / ``redfish_post``
               invocation, returns a canned ``(status, stdout, stderr)``
               tuple or raises a programmed exception, and replays a
               programmed sequence of boolean values from
               ``verify_connectivity``.  ``time.sleep`` is patched on the
               module under test so reconnect polling completes
               instantly.  The module's grace-period constant and the
               completion floor constants are monkey-patched to tiny
               values so timeout-path tests run in well under a second.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Automation Mojo LLC"
__credits__ = []


import unittest

from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import patch

from automojo.protocols.uefi import uefireset
from automojo.protocols.uefi.uefiaspects import UefiCmdAspects
from automojo.protocols.uefi.uefireset import (
    ResetFailedError,
    ResetKind,
    SystemReset,
)


# ---------------------------------------------------------------------------
# Fake agent fixture
# ---------------------------------------------------------------------------


class _FakeUefiAgent:
    """
        Minimal stand-in for :class:`UefiAgent`.  Records
        ``run_cmd`` and ``redfish_post`` invocations and replays a
        programmed sequence of boolean values from
        ``verify_connectivity``.
    """

    def __init__(self, transport_kind: str,
                       host: str = "target.example",
                       system_id: str = "Self") -> None:
        """
            Initialize the fake agent with a configurable
            ``transport_kind`` and recording surfaces.

            :param transport_kind: One of ``'serial'``, ``'redfish'``,
                                   ``'ssh-host'``, ``'ipmi-sol'``.
            :param host: Target host string.
            :param system_id: Redfish system identifier.
        """
        self.transport_kind = transport_kind
        self.host = host
        self.system_id = system_id

        self.run_cmd_calls: List[Dict[str, Any]] = []
        self.run_cmd_return: Tuple[int, str, str] = (0, "", "")
        self.run_cmd_exception: Optional[BaseException] = None

        self.redfish_post_calls: List[Dict[str, Any]] = []
        self.redfish_post_exception: Optional[BaseException] = None
        self.use_redfish_post: bool = False

        self.verify_calls: int = 0
        self.verify_sequence: List[bool] = []

        # Shadow the class-level ``redfish_post`` with ``None`` on the
        # instance so ``getattr(agent, "redfish_post", None)`` returns
        # ``None`` and the production dispatch falls through to
        # ``run_cmd``.  When ``use_redfish_post=True`` is requested, the
        # ``enable_redfish_post()`` helper restores the bound method.
        self.redfish_post = None
        return

    def enable_redfish_post(self) -> None:
        """
            Restore the class-level ``redfish_post`` method on this
            instance so ``getattr`` returns the callable and the
            production dispatch prefers it over ``run_cmd``.
        """
        self.use_redfish_post = True
        del self.redfish_post
        return

    def run_cmd(self, command: str,
                      aspects: Optional[UefiCmdAspects] = None) -> Tuple[int, str, str]:
        """
            Record a ``run_cmd`` invocation and return the primed result
            (or raise the primed exception).

            :param command: The command string.
            :param aspects: Aspects object passed by the caller.

            :returns: The primed three-tuple.
            :raises: The primed exception, if any.
        """
        self.run_cmd_calls.append({
            "command": command,
            "aspects": aspects,
        })
        if self.run_cmd_exception is not None:
            raise self.run_cmd_exception
        rtnval = self.run_cmd_return
        return rtnval

    def redfish_post(self, path: str,
                           body: str,
                           aspects: Optional[UefiCmdAspects] = None) -> None:
        """
            Record a ``redfish_post`` invocation.  Only bound on the
            agent when ``use_redfish_post`` is True (see
            ``__getattr__``).

            :param path: Redfish URL path.
            :param body: JSON body string.
            :param aspects: Aspects object passed by the caller.
        """
        self.redfish_post_calls.append({
            "path": path,
            "body": body,
            "aspects": aspects,
        })
        if self.redfish_post_exception is not None:
            raise self.redfish_post_exception
        return

    def __getattr__(self, name: str) -> Any:
        """
            Hide ``redfish_post`` unless ``use_redfish_post`` is True.

            ``SystemReset._dispatch_reset_redfish`` prefers
            ``agent.redfish_post`` when it exists; tests that want the
            ``run_cmd`` fallback disable the attribute.
        """
        if name == "redfish_post":
            use_flag = self.__dict__.get("use_redfish_post", False)
            if use_flag is False:
                err_msg = "_FakeUefiAgent has no attribute 'redfish_post'"
                raise AttributeError(err_msg)
            bound = self.__dict__.get("_redfish_post_bound")
            if bound is None:
                bound = super().__getattribute__("redfish_post")
                self.__dict__["_redfish_post_bound"] = bound
            return bound

        err_msg = "_FakeUefiAgent has no attribute {!r}".format(name)
        raise AttributeError(err_msg)

    def verify_connectivity(self) -> bool:
        """
            Replay the next boolean from ``verify_sequence``.

            :returns: ``True`` or ``False`` per the programmed sequence;
                      ``False`` once the sequence is exhausted.
        """
        self.verify_calls = self.verify_calls + 1
        if len(self.verify_sequence) == 0:
            rtnval = False
        else:
            rtnval = self.verify_sequence.pop(0)
        return rtnval


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _build_reset(agent: _FakeUefiAgent,
                 aspects: Optional[UefiCmdAspects] = None) -> SystemReset:
    """
        Construct a :class:`SystemReset` bound to the given fake agent.

        :param agent: The fake agent instance.
        :param aspects: Optional aspects override.

        :returns: A ready-to-drive :class:`SystemReset`.
    """
    if aspects is None:
        rtnval = SystemReset(agent=agent)
    else:
        rtnval = SystemReset(agent=agent, aspects=aspects)
    return rtnval


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestResetKind(unittest.TestCase):
    """
        Verify that :class:`ResetKind` exposes exactly the members required
        by UEFI §8.5.1.
    """

    def test_members(self) -> None:
        """
            Exactly four members with the expected ``value`` strings.
        """
        expected = {"cold", "warm", "shutdown", "platform-specific"}
        observed = {member.value for member in ResetKind}
        self.assertEqual(observed, expected)

        self.assertEqual(ResetKind.COLD.value, "cold")
        self.assertEqual(ResetKind.WARM.value, "warm")
        self.assertEqual(ResetKind.SHUTDOWN.value, "shutdown")
        self.assertEqual(ResetKind.PLATFORM_SPECIFIC.value, "platform-specific")
        return


class TestResetSerialDispatch(unittest.TestCase):
    """
        Verify command mapping for the Shell-based transports.
    """

    def test_warm_reset_sends_reset_w(self) -> None:
        """
            :class:`ResetKind.WARM` on serial issues ``reset -w``.
        """
        agent = _FakeUefiAgent(transport_kind="serial")
        agent.verify_sequence = [True]
        reset = _build_reset(agent=agent)

        with patch.object(uefireset.time, "sleep"):
            reset.reset(kind=ResetKind.WARM)

        self.assertEqual(len(agent.run_cmd_calls), 1)
        self.assertEqual(agent.run_cmd_calls[0]["command"], "reset -w")
        return

    def test_cold_reset_sends_reset_c(self) -> None:
        """
            :class:`ResetKind.COLD` on serial issues ``reset -c``.
        """
        agent = _FakeUefiAgent(transport_kind="serial")
        agent.verify_sequence = [True]
        reset = _build_reset(agent=agent)

        with patch.object(uefireset.time, "sleep"):
            reset.reset(kind=ResetKind.COLD)

        self.assertEqual(len(agent.run_cmd_calls), 1)
        self.assertEqual(agent.run_cmd_calls[0]["command"], "reset -c")
        return

    def test_shutdown_reset_sends_reset_s(self) -> None:
        """
            :class:`ResetKind.SHUTDOWN` on serial issues ``reset -s``.
        """
        agent = _FakeUefiAgent(transport_kind="serial")
        agent.verify_sequence = [True]
        reset = _build_reset(agent=agent)

        with patch.object(uefireset.time, "sleep"):
            reset.reset(kind=ResetKind.SHUTDOWN)

        self.assertEqual(len(agent.run_cmd_calls), 1)
        self.assertEqual(agent.run_cmd_calls[0]["command"], "reset -s")
        return

    def test_ipmi_sol_transport_uses_shell_dispatch(self) -> None:
        """
            The ``ipmi-sol`` transport kind follows the same Shell path.
        """
        agent = _FakeUefiAgent(transport_kind="ipmi-sol")
        agent.verify_sequence = [True]
        reset = _build_reset(agent=agent)

        with patch.object(uefireset.time, "sleep"):
            reset.reset(kind=ResetKind.WARM)

        self.assertEqual(len(agent.run_cmd_calls), 1)
        self.assertEqual(agent.run_cmd_calls[0]["command"], "reset -w")
        return

    def test_platform_specific_with_payload_appends_hex(self) -> None:
        """
            PLATFORM_SPECIFIC with a payload appends the hex encoding.
        """
        agent = _FakeUefiAgent(transport_kind="serial")
        agent.verify_sequence = [True]
        reset = _build_reset(agent=agent)
        payload = b"\x01\x02\xab"

        with patch.object(uefireset.time, "sleep"):
            reset.reset(
                kind=ResetKind.PLATFORM_SPECIFIC,
                platform_specific_data=payload,
            )

        self.assertEqual(len(agent.run_cmd_calls), 1)
        self.assertEqual(agent.run_cmd_calls[0]["command"], "reset -s 0102ab")
        return


class TestResetSshHostDispatch(unittest.TestCase):
    """
        Verify command mapping for the ``ssh-host`` transport.
    """

    def test_warm_and_cold_reset_send_reboot(self) -> None:
        """
            WARM and COLD both issue ``sudo reboot`` on ssh-host.
        """
        for kind in (ResetKind.WARM, ResetKind.COLD):
            agent = _FakeUefiAgent(transport_kind="ssh-host")
            agent.verify_sequence = [True]
            reset = _build_reset(agent=agent)

            with patch.object(uefireset.time, "sleep"):
                reset.reset(kind=kind)

            self.assertEqual(len(agent.run_cmd_calls), 1)
            self.assertEqual(agent.run_cmd_calls[0]["command"], "sudo reboot")
        return

    def test_shutdown_sends_shutdown_command(self) -> None:
        """
            SHUTDOWN on ssh-host issues ``sudo shutdown -h now``.
        """
        agent = _FakeUefiAgent(transport_kind="ssh-host")
        agent.verify_sequence = [True]
        reset = _build_reset(agent=agent)

        with patch.object(uefireset.time, "sleep"):
            reset.reset(kind=ResetKind.SHUTDOWN)

        self.assertEqual(len(agent.run_cmd_calls), 1)
        self.assertEqual(
            agent.run_cmd_calls[0]["command"],
            "sudo shutdown -h now",
        )
        return


class TestResetRedfishDispatch(unittest.TestCase):
    """
        Verify Redfish ``ComputerSystem.Reset`` dispatch.
    """

    def test_cold_reset_posts_force_restart_via_run_cmd(self) -> None:
        """
            COLD on redfish maps to ``ResetType: ForceRestart`` via
            ``run_cmd`` when the agent has no ``redfish_post`` method.
        """
        agent = _FakeUefiAgent(transport_kind="redfish")
        agent.verify_sequence = [True]
        reset = _build_reset(agent=agent)

        with patch.object(uefireset.time, "sleep"):
            reset.reset(kind=ResetKind.COLD)

        self.assertEqual(len(agent.run_cmd_calls), 1)
        command = agent.run_cmd_calls[0]["command"]
        self.assertIn("POST", command)
        self.assertIn("/redfish/v1/Systems/Self/Actions/ComputerSystem.Reset", command)
        self.assertIn('"ResetType": "ForceRestart"', command)
        return

    def test_warm_reset_maps_to_graceful_restart(self) -> None:
        """
            WARM on redfish maps to ``ResetType: GracefulRestart``.
        """
        agent = _FakeUefiAgent(transport_kind="redfish")
        agent.verify_sequence = [True]
        reset = _build_reset(agent=agent)

        with patch.object(uefireset.time, "sleep"):
            reset.reset(kind=ResetKind.WARM)

        self.assertEqual(len(agent.run_cmd_calls), 1)
        command = agent.run_cmd_calls[0]["command"]
        self.assertIn('"ResetType": "GracefulRestart"', command)
        return

    def test_shutdown_maps_to_graceful_shutdown(self) -> None:
        """
            SHUTDOWN on redfish maps to ``ResetType: GracefulShutdown``.
        """
        agent = _FakeUefiAgent(transport_kind="redfish")
        agent.verify_sequence = [True]
        reset = _build_reset(agent=agent)

        with patch.object(uefireset.time, "sleep"):
            reset.reset(kind=ResetKind.SHUTDOWN)

        self.assertEqual(len(agent.run_cmd_calls), 1)
        command = agent.run_cmd_calls[0]["command"]
        self.assertIn('"ResetType": "GracefulShutdown"', command)
        return

    def test_redfish_post_method_preferred_when_available(self) -> None:
        """
            When the agent exposes ``redfish_post`` the facade uses it
            instead of ``run_cmd``.
        """
        agent = _FakeUefiAgent(transport_kind="redfish")
        agent.enable_redfish_post()
        agent.verify_sequence = [True]
        reset = _build_reset(agent=agent)

        with patch.object(uefireset.time, "sleep"):
            reset.reset(kind=ResetKind.COLD)

        self.assertEqual(len(agent.run_cmd_calls), 0)
        self.assertEqual(len(agent.redfish_post_calls), 1)
        observed = agent.redfish_post_calls[0]
        self.assertEqual(
            observed["path"],
            "/redfish/v1/Systems/Self/Actions/ComputerSystem.Reset",
        )
        self.assertIn('"ResetType": "ForceRestart"', observed["body"])
        return


class TestResetSwallowsConnectionError(unittest.TestCase):
    """
        The reset command is expected to sever the transport; the
        resulting :class:`ConnectionError` must not propagate.
    """

    def test_connection_error_swallowed(self) -> None:
        """
            A :class:`ConnectionError` raised by ``run_cmd`` is caught
            and does not abort the reconnect path.
        """
        agent = _FakeUefiAgent(transport_kind="serial")
        agent.run_cmd_exception = ConnectionError("transport dropped")
        agent.verify_sequence = [True]
        reset = _build_reset(agent=agent)

        with patch.object(uefireset.time, "sleep"):
            reset.reset(kind=ResetKind.WARM)

        # The reset command was attempted once; verify_connectivity ran
        # after the grace sleep and reported success on the first attempt.
        self.assertEqual(len(agent.run_cmd_calls), 1)
        self.assertGreaterEqual(agent.verify_calls, 1)
        return

    def test_os_error_swallowed(self) -> None:
        """
            :class:`OSError` is also in the expected-exception allow list.
        """
        agent = _FakeUefiAgent(transport_kind="serial")
        agent.run_cmd_exception = OSError("transport closed")
        agent.verify_sequence = [True]
        reset = _build_reset(agent=agent)

        with patch.object(uefireset.time, "sleep"):
            reset.reset(kind=ResetKind.WARM)

        self.assertEqual(len(agent.run_cmd_calls), 1)
        return


class TestResetWaitForReconnect(unittest.TestCase):
    """
        Exercise the :meth:`SystemReset.reset` ``wait_for_reconnect``
        parameter and the reconnect-verify loop.
    """

    def test_wait_for_reconnect_false_returns_immediately(self) -> None:
        """
            With ``wait_for_reconnect=False`` the facade returns without
            polling ``verify_connectivity``.
        """
        agent = _FakeUefiAgent(transport_kind="serial")
        reset = _build_reset(agent=agent)

        with patch.object(uefireset.time, "sleep") as sleep_mock:
            reset.reset(kind=ResetKind.WARM, wait_for_reconnect=False)

        self.assertEqual(agent.verify_calls, 0)
        self.assertEqual(sleep_mock.call_count, 0)
        return

    def test_reconnect_succeeds_on_third_attempt(self) -> None:
        """
            ``verify_connectivity`` returning True on the third call
            yields a clean return from :meth:`reset`.
        """
        agent = _FakeUefiAgent(transport_kind="serial")
        agent.verify_sequence = [False, False, True]
        reset = _build_reset(agent=agent)

        with patch.object(uefireset.time, "sleep"):
            reset.reset(kind=ResetKind.WARM)

        self.assertEqual(agent.verify_calls, 3)
        return

    def test_reconnect_timeout_raises_reset_failed_error(self) -> None:
        """
            When ``verify_connectivity`` never returns True the loop
            raises :class:`ResetFailedError` after the completion
            timeout elapses.
        """
        agent = _FakeUefiAgent(transport_kind="serial")
        agent.verify_sequence = []  # always False
        # Use aspects with a tiny completion_timeout so the reset loop
        # exits quickly — the SystemReset computes the effective timeout
        # as max(aspects.completion_timeout, DEFAULT_RESET_COMPLETION_TIMEOUT)
        # and both must be small for the test to run fast.
        fast_aspects = UefiCmdAspects(completion_timeout=1, completion_interval=1)
        reset = _build_reset(agent=agent, aspects=fast_aspects)

        patches = [
            patch.object(uefireset, "DEFAULT_RESET_COMPLETION_TIMEOUT", 1),
            patch.object(uefireset, "DEFAULT_RESET_COMPLETION_INTERVAL", 1),
            patch.object(uefireset, "DEFAULT_RESET_GRACE_PERIOD", 0),
            patch.object(uefireset.time, "sleep"),
        ]
        for single_patch in patches:
            single_patch.start()

        try:
            caught: Optional[BaseException] = None
            try:
                reset.reset(kind=ResetKind.WARM, reason="test-path")
            except ResetFailedError as rerr:
                caught = rerr

            self.assertIsInstance(caught, ResetFailedError)
            message_text = str(caught)
            self.assertIn("completion_timeout", message_text)
            self.assertIn("warm", message_text)
        finally:
            for single_patch in patches:
                single_patch.stop()
        return

    def test_reset_failed_error_details_populated(self) -> None:
        """
            :class:`ResetFailedError.details` contains the required
            fields.
        """
        agent = _FakeUefiAgent(transport_kind="serial")
        agent.verify_sequence = []
        fast_aspects = UefiCmdAspects(completion_timeout=1, completion_interval=1)
        reset = _build_reset(agent=agent, aspects=fast_aspects)

        patches = [
            patch.object(uefireset, "DEFAULT_RESET_COMPLETION_TIMEOUT", 1),
            patch.object(uefireset, "DEFAULT_RESET_COMPLETION_INTERVAL", 1),
            patch.object(uefireset, "DEFAULT_RESET_GRACE_PERIOD", 0),
            patch.object(uefireset.time, "sleep"),
        ]
        for single_patch in patches:
            single_patch.start()

        try:
            caught: Optional[ResetFailedError] = None
            try:
                reset.reset(kind=ResetKind.COLD, reason="diag")
            except ResetFailedError as rerr:
                caught = rerr

            self.assertIsNotNone(caught)
            joined_details = "\n".join(caught.details)
            self.assertIn("kind=cold", joined_details)
            self.assertIn("reason=diag", joined_details)
            self.assertIn("transport_kind=serial", joined_details)
            self.assertIn("host=target.example", joined_details)
        finally:
            for single_patch in patches:
                single_patch.stop()
        return


class TestSystemResetWithCustomAspects(unittest.TestCase):
    """
        Exercise the aspects-resolution path.
    """

    def test_custom_aspects_forwarded_to_run_cmd(self) -> None:
        """
            A per-call aspects override is passed through to
            ``run_cmd``.
        """
        agent = _FakeUefiAgent(transport_kind="serial")
        agent.verify_sequence = [True]

        instance_aspects = UefiCmdAspects()
        per_call_aspects = UefiCmdAspects()
        reset = _build_reset(agent=agent, aspects=instance_aspects)

        with patch.object(uefireset.time, "sleep"):
            reset.reset(kind=ResetKind.WARM, aspects=per_call_aspects)

        self.assertIs(agent.run_cmd_calls[0]["aspects"], per_call_aspects)
        return


if __name__ == "__main__":
    unittest.main()
