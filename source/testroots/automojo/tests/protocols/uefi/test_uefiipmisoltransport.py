"""
    .. module:: test_uefiipmisoltransport
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Unit tests for
               :mod:`automojo.protocols.uefi.uefiipmisoltransport`.
               The tests mock :class:`subprocess.Popen` and
               :func:`subprocess.run` so the suite runs without an
               ``ipmitool`` binary or a real BMC.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Automation Mojo LLC"
__credits__ = []


import io
import re
import signal
import subprocess
import unittest

from typing import List, Optional
from unittest import mock

from automojo.protocols.uefi.uefiexceptions import UefiTransportError
from automojo.protocols.uefi.uefiinterfaces import IUefiTransport
from automojo.protocols.uefi.uefiipmicredential import UefiIpmiCredential
from automojo.protocols.uefi.uefiipmisoltransport import (
    DEACTIVATION_FAILED_SENTINEL,
    SOL_ALREADY_ACTIVE_SENTINEL,
    SOL_ESCAPE_SEQUENCE,
    UefiIpmiSolTransport,
)


# ---------------------------------------------------------------------------
# Fake Popen
# ---------------------------------------------------------------------------

class _NonClosingBytesIO(io.BytesIO):
    """
        ``io.BytesIO`` subclass that ignores :meth:`close` so tests can
        inspect the recorded contents after the transport has closed its
        end of the pipe.
    """

    def close(self) -> None:
        """
            No-op close; keeps the underlying buffer readable.
        """
        return


class _FakePopen:
    """
        Test fake for :class:`subprocess.Popen`.  Simulates an
        ``ipmitool sol activate`` process: provides a stdin pipe that
        records what was written, a stdout pipe that replays a scripted
        sequence of byte chunks, a stderr pipe, and a configurable exit
        code.  The ``_FakePopen`` class registers all constructed
        instances on a class-level list so tests may inspect them after
        the transport has been torn down.
    """

    instances: "List[_FakePopen]" = []

    def __init__(self, argv: List[str], stdin: Optional[int] = None,
                       stdout: Optional[int] = None,
                       stderr: Optional[int] = None) -> None:
        """
            Capture the subprocess constructor arguments and initialize
            the fake streams.
        """
        self.argv = list(argv)
        self.stdin = _NonClosingBytesIO()
        self.stdout = _NonClosingBytesIO()
        self.stderr = _NonClosingBytesIO()
        self._exit_code: Optional[int] = None
        self._signals_received: List[int] = []
        self._early_exit_after: Optional[float] = None
        self._early_exit_output: bytes = b""
        self._early_exit_stderr: bytes = b""
        _FakePopen.instances.append(self)
        return

    def configure_early_exit(self, exit_code: int, stdout_text: bytes = b"",
                                   stderr_text: bytes = b"") -> None:
        """
            Program the fake to behave as if ``ipmitool`` exited before
            SOL activation completed.

            :param exit_code: The exit code to report.
            :param stdout_text: Bytes to surface on the stdout pipe.
            :param stderr_text: Bytes to surface on the stderr pipe.
        """
        self._exit_code = exit_code
        self._early_exit_output = stdout_text
        self._early_exit_stderr = stderr_text
        # Pre-load stdout/stderr pipes with the scripted text.
        self.stdout = _NonClosingBytesIO(stdout_text)
        self.stderr = _NonClosingBytesIO(stderr_text)
        return

    def poll(self) -> Optional[int]:
        """
            Report the current exit code, or ``None`` when still
            running.
        """
        rtnval = self._exit_code
        return rtnval

    def communicate(self, timeout: Optional[float] = None):
        """
            Return the scripted stdout / stderr bytes.
        """
        out = self._early_exit_output
        err = self._early_exit_stderr
        return (out, err)

    def send_signal(self, sig: int) -> None:
        """
            Record a signal and treat ``SIGTERM`` / ``SIGKILL`` as
            immediate exits.
        """
        self._signals_received.append(sig)
        if sig in (signal.SIGTERM, signal.SIGKILL):
            self._exit_code = -sig
        return

    def kill(self) -> None:
        """
            Treat ``kill()`` as ``SIGKILL``.
        """
        self.send_signal(sig=signal.SIGKILL)
        return

    def terminate(self) -> None:
        """
            Treat ``terminate()`` as ``SIGTERM``.
        """
        self.send_signal(sig=signal.SIGTERM)
        return

    def wait(self, timeout: Optional[float] = None) -> int:
        """
            Return the exit code, defaulting to zero when none set.
        """
        if self._exit_code is None:
            self._exit_code = 0
        rtnval = self._exit_code
        return rtnval


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_credential() -> UefiIpmiCredential:
    """
        Build a simple credential suitable for test use.

        :returns: A :class:`UefiIpmiCredential`.
    """
    cred = UefiIpmiCredential(
        host="10.0.0.5",
        username="admin",
        password="secret",
        interface="lanplus",
        port=623,
    )
    return cred


def _make_completed(stdout_bytes: bytes = b"", stderr_bytes: bytes = b"",
                    returncode: int = 0) -> subprocess.CompletedProcess:
    """
        Build a :class:`subprocess.CompletedProcess` for mocking
        :func:`subprocess.run`.

        :param stdout_bytes: Bytes to report as stdout.
        :param stderr_bytes: Bytes to report as stderr.
        :param returncode: Exit status to report.

        :returns: The :class:`subprocess.CompletedProcess` instance.
    """
    rtnval = subprocess.CompletedProcess(
        args=["ipmitool"],
        returncode=returncode,
        stdout=stdout_bytes,
        stderr=stderr_bytes,
    )
    return rtnval


# ---------------------------------------------------------------------------
# IUefiTransport compliance
# ---------------------------------------------------------------------------

class ProtocolComplianceTestCase(unittest.TestCase):
    """
        Tests that :class:`UefiIpmiSolTransport` satisfies the
        :class:`IUefiTransport` structural contract.
    """

    def test_satisfies_iuefitransport(self) -> None:
        """
            A constructed transport is an instance of
            :class:`IUefiTransport` via runtime protocol check.
        """
        cred = _build_credential()
        transport = UefiIpmiSolTransport(credential=cred)
        result = isinstance(transport, IUefiTransport)
        self.assertTrue(result)
        return

    def test_transport_kind_is_ipmi_sol(self) -> None:
        """
            The ``transport_kind`` property is the documented literal.
        """
        cred = _build_credential()
        transport = UefiIpmiSolTransport(credential=cred)
        observed = transport.transport_kind
        self.assertEqual(observed, "ipmi-sol")
        return


# ---------------------------------------------------------------------------
# Activation behavior
# ---------------------------------------------------------------------------

class ActivationTestCase(unittest.TestCase):
    """
        Tests around the ``connect()`` code path.
    """

    def setUp(self) -> None:
        """
            Reset the fake Popen registry before each test.
        """
        _FakePopen.instances = []
        return

    def test_sol_already_active_raises_without_evict(self) -> None:
        """
            ``evict_existing=False`` + "SOL already active" surfaces a
            :class:`UefiTransportError` naming the condition.
        """
        cred = _build_credential()
        transport = UefiIpmiSolTransport(
            credential=cred,
            evict_existing=False,
        )

        def _popen_factory(argv, stdin=None, stdout=None, stderr=None):
            fake = _FakePopen(argv=argv, stdin=stdin, stdout=stdout, stderr=stderr)
            fake.configure_early_exit(
                exit_code=1,
                stdout_text=b"",
                stderr_text=b"Info: SOL already active on another session\n",
            )
            return fake

        with mock.patch("subprocess.Popen", side_effect=_popen_factory):
            # Keep retries bounded by using short connection parameters.
            from automojo.protocols.uefi.uefiaspects import UefiCmdAspects
            short_aspects = UefiCmdAspects(
                connection_timeout=2,
                connection_interval=1,
            )
            raised = False
            try:
                transport.connect(aspects=short_aspects)
            except UefiTransportError as xcpt:
                raised = True
                message = str(xcpt)
                self.assertIn(SOL_ALREADY_ACTIVE_SENTINEL, message)

            self.assertTrue(raised)
            self.assertFalse(transport.connected)
        return

    def test_evict_existing_runs_deactivate_first(self) -> None:
        """
            ``evict_existing=True`` triggers a ``sol deactivate`` call
            ahead of the ``sol activate`` subprocess.
        """
        cred = _build_credential()
        transport = UefiIpmiSolTransport(
            credential=cred,
            evict_existing=True,
        )

        run_calls: List[List[str]] = []

        def _run_factory(argv, **kwargs):
            run_calls.append(list(argv))
            completed = _make_completed(
                stdout_bytes=b"Info: SOL payload deactivated\n",
                stderr_bytes=b"",
                returncode=0,
            )
            return completed

        def _popen_factory(argv, stdin=None, stdout=None, stderr=None):
            fake = _FakePopen(argv=argv, stdin=stdin, stdout=stdout, stderr=stderr)
            # Leave _exit_code = None so Popen remains "running" and
            # activation is considered successful.
            return fake

        with mock.patch("subprocess.run", side_effect=_run_factory):
            with mock.patch("subprocess.Popen", side_effect=_popen_factory):
                transport.connect()

        self.assertEqual(len(run_calls), 1)
        deactivate_argv = run_calls[0]
        self.assertIn("sol", deactivate_argv)
        self.assertIn("deactivate", deactivate_argv)
        self.assertTrue(transport.connected)
        # Cleanup -- avoid leaving fake subprocess "running".
        transport.disconnect()
        return

    def test_deactivate_failed_raises_with_raw_output_in_details(self) -> None:
        """
            A ``sol deactivate`` returning the "Deactivation failed"
            sentinel surfaces :class:`UefiTransportError` whose
            ``details`` carry the raw tool output.
        """
        cred = _build_credential()
        transport = UefiIpmiSolTransport(
            credential=cred,
            evict_existing=True,
        )

        raw_stdout = b"Error: Unable to deactivate SOL payload\n"
        raw_stderr = b"Deactivation failed: session not found\n"

        def _run_factory(argv, **kwargs):
            completed = _make_completed(
                stdout_bytes=raw_stdout,
                stderr_bytes=raw_stderr,
                returncode=1,
            )
            return completed

        def _popen_factory(argv, stdin=None, stdout=None, stderr=None):
            fake = _FakePopen(argv=argv, stdin=stdin, stdout=stdout, stderr=stderr)
            return fake

        raised = False
        with mock.patch("subprocess.run", side_effect=_run_factory):
            with mock.patch("subprocess.Popen", side_effect=_popen_factory):
                try:
                    transport.connect()
                except UefiTransportError as xcpt:
                    raised = True
                    joined_details = "\n".join(xcpt.details)
                    self.assertIn(DEACTIVATION_FAILED_SENTINEL, str(xcpt))
                    self.assertIn(raw_stderr.decode(), joined_details)

        self.assertTrue(raised)
        self.assertFalse(transport.connected)
        return


# ---------------------------------------------------------------------------
# recv_until behavior
# ---------------------------------------------------------------------------

class RecvUntilTestCase(unittest.TestCase):
    """
        Tests around the :meth:`recv_until` code path.
    """

    def setUp(self) -> None:
        """
            Reset the fake Popen registry before each test.
        """
        _FakePopen.instances = []
        return

    def _make_connected_transport(self) -> UefiIpmiSolTransport:
        """
            Build a transport whose ``connected`` property is ``True``
            and whose internal subprocess is a fresh :class:`_FakePopen`.

            :returns: A connected :class:`UefiIpmiSolTransport`.
        """
        cred = _build_credential()
        transport = UefiIpmiSolTransport(credential=cred)

        fake = _FakePopen(argv=["ipmitool"], stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        transport._process = fake  # type: ignore[attr-defined]
        transport._connected = True  # type: ignore[attr-defined]
        return transport

    def test_recv_until_matches_bytes_pattern(self) -> None:
        """
            A plain bytes pattern is matched via literal substring.
        """
        transport = self._make_connected_transport()
        # Pre-seed the transport's read buffer so _recv_ipmitool is not
        # needed to satisfy the match.
        transport._read_buffer.extend(b"hello world Shell> extra")  # type: ignore[attr-defined]

        observed = transport.recv_until(pattern=b"Shell>", timeout=1.0)
        self.assertTrue(observed.endswith(b"Shell>"))
        self.assertIn(b"hello world", observed)
        # Leftover bytes should be recoverable via recv().
        leftover_bytes = transport._read_buffer  # type: ignore[attr-defined]
        self.assertIn(b"extra", bytes(leftover_bytes))
        transport.disconnect()
        return

    def test_recv_until_matches_compiled_regex(self) -> None:
        """
            A compiled :class:`re.Pattern` of bytes is matched via
            :meth:`re.Pattern.search`.
        """
        transport = self._make_connected_transport()
        transport._read_buffer.extend(b"boot banner\nFS0:\\> done")  # type: ignore[attr-defined]

        pattern = re.compile(rb"FS\d+:\\>")
        observed = transport.recv_until(pattern=pattern, timeout=1.0)
        self.assertTrue(observed.endswith(b"FS0:\\>"))
        transport.disconnect()
        return

    def test_recv_until_raises_timeout_with_partial(self) -> None:
        """
            When the pattern does not arrive before ``timeout``,
            :class:`TimeoutError` is raised with ``partial`` bytes
            attached.
        """
        transport = self._make_connected_transport()
        # Patch the backend reader to always return no bytes, so the
        # loop exhausts its deadline.
        with mock.patch.object(transport, "_recv_ipmitool", return_value=b""):
            transport._read_buffer.extend(b"prefix-without-match")  # type: ignore[attr-defined]

            raised = False
            try:
                transport.recv_until(pattern=b"NEVER-ARRIVES", timeout=0.15)
            except TimeoutError as terr:
                raised = True
                self.assertTrue(hasattr(terr, "partial"))
                partial_bytes = getattr(terr, "partial")
                self.assertIsInstance(partial_bytes, bytes)
                self.assertIn(b"prefix-without-match", partial_bytes)

            self.assertTrue(raised)
        transport.disconnect()
        return


# ---------------------------------------------------------------------------
# disconnect() behavior
# ---------------------------------------------------------------------------

class DisconnectTestCase(unittest.TestCase):
    """
        Tests around the :meth:`disconnect` code path.
    """

    def setUp(self) -> None:
        """
            Reset the fake Popen registry before each test.
        """
        _FakePopen.instances = []
        return

    def test_disconnect_sends_escape_and_terminates(self) -> None:
        """
            :meth:`disconnect` writes the ``~.`` escape to the
            subprocess stdin and then sends ``SIGTERM`` when the process
            has not yet exited.
        """
        cred = _build_credential()
        transport = UefiIpmiSolTransport(credential=cred)

        fake = _FakePopen(argv=["ipmitool"], stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        transport._process = fake  # type: ignore[attr-defined]
        transport._connected = True  # type: ignore[attr-defined]

        transport.disconnect()

        # The escape sequence should have been written to stdin.
        written_bytes = fake.stdin.getvalue()
        self.assertEqual(written_bytes, SOL_ESCAPE_SEQUENCE)
        # The subprocess was not exiting on escape; SIGTERM should have
        # been applied.
        self.assertIn(signal.SIGTERM, fake._signals_received)
        # Transport should be disconnected now.
        self.assertFalse(transport.connected)
        return

    def test_disconnect_is_noop_when_not_connected(self) -> None:
        """
            Calling :meth:`disconnect` on a fresh transport does not
            raise.
        """
        cred = _build_credential()
        transport = UefiIpmiSolTransport(credential=cred)
        # No exception should escape.
        transport.disconnect()
        self.assertFalse(transport.connected)
        return


if __name__ == "__main__":
    unittest.main()
