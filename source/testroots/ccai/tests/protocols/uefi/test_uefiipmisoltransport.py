"""
    .. module:: test_uefiipmisoltransport
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Unit tests for
               :mod:`ccai.protocols.uefi.uefiipmisoltransport`.
               The tests mock :class:`subprocess.Popen` and
               :func:`subprocess.run` so the suite runs without an
               ``ipmitool`` binary or a real BMC.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Code Craft AI LLC"
__credits__ = []


from types import SimpleNamespace

from ccai import testbase

import io
import re
import signal
import subprocess

from typing import List, Optional
from unittest import mock

from ccai.protocols.uefi.uefiexceptions import UefiTransportError
from ccai.protocols.uefi.uefiinterfaces import IUefiTransport
from ccai.protocols.uefi.uefiipmicredential import UefiIpmiCredential
from ccai.protocols.uefi.uefiipmisoltransport import (
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

# ----------------------------------------------------------------------
# ProtocolComplianceTestCase
# ----------------------------------------------------------------------

"""
    Tests that :class:`UefiIpmiSolTransport` satisfies the
    :class:`IUefiTransport` structural contract.
"""

def test_satisfies_iuefitransport() -> None:
    """
        A constructed transport is an instance of
        :class:`IUefiTransport` via runtime protocol check.
    """
    cred = _build_credential()
    transport = UefiIpmiSolTransport(credential=cred)
    result = isinstance(transport, IUefiTransport)
    testbase.assert_true(result)
    return

def test_transport_kind_is_ipmi_sol() -> None:
    """
        The ``transport_kind`` property is the documented literal.
    """
    cred = _build_credential()
    transport = UefiIpmiSolTransport(credential=cred)
    observed = transport.transport_kind
    testbase.assert_equal(observed, "ipmi-sol")
    return


# ---------------------------------------------------------------------------
# Activation behavior
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# ActivationTestCase
# ----------------------------------------------------------------------

"""
    Tests around the ``connect()`` code path.
"""

def _setup_activation(ctx) -> None:
    """
        Reset the fake Popen registry before each test.
    """
    _FakePopen.instances = []
    return

def test_sol_already_active_raises_without_evict() -> None:
    """
        ``evict_existing=False`` + "SOL already active" surfaces a
        :class:`UefiTransportError` naming the condition.
    """
    ctx = SimpleNamespace()
    _setup_activation(ctx)
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
        from ccai.protocols.uefi.uefiaspects import UefiCmdAspects
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
            testbase.assert_true(SOL_ALREADY_ACTIVE_SENTINEL in message)

        testbase.assert_true(raised)
        testbase.assert_false(transport.connected)
    return

def test_evict_existing_runs_deactivate_first() -> None:
    """
        ``evict_existing=True`` triggers a ``sol deactivate`` call
        ahead of the ``sol activate`` subprocess.
    """
    ctx = SimpleNamespace()
    _setup_activation(ctx)
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

    testbase.assert_equal(len(run_calls), 1)
    deactivate_argv = run_calls[0]
    testbase.assert_true("sol" in deactivate_argv)
    testbase.assert_true("deactivate" in deactivate_argv)
    testbase.assert_true(transport.connected)
    # Cleanup -- avoid leaving fake subprocess "running".
    transport.disconnect()
    return

def test_deactivate_failed_raises_with_raw_output_in_details() -> None:
    """
        A ``sol deactivate`` returning the "Deactivation failed"
        sentinel surfaces :class:`UefiTransportError` whose
        ``details`` carry the raw tool output.
    """
    ctx = SimpleNamespace()
    _setup_activation(ctx)
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
                testbase.assert_true(DEACTIVATION_FAILED_SENTINEL in str(xcpt))
                testbase.assert_true(raw_stderr.decode() in joined_details)

    testbase.assert_true(raised)
    testbase.assert_false(transport.connected)
    return


# ---------------------------------------------------------------------------
# recv_until behavior
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# RecvUntilTestCase
# ----------------------------------------------------------------------

"""
    Tests around the :meth:`recv_until` code path.
"""

def _setup_recv_until(ctx) -> None:
    """
        Reset the fake Popen registry before each test.
    """
    _FakePopen.instances = []
    return

def _make_connected_transport() -> UefiIpmiSolTransport:
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

def test_recv_until_matches_bytes_pattern() -> None:
    """
        A plain bytes pattern is matched via literal substring.
    """
    ctx = SimpleNamespace()
    _setup_recv_until(ctx)
    transport = _make_connected_transport()
    # Pre-seed the transport's read buffer so _recv_ipmitool is not
    # needed to satisfy the match.
    transport._read_buffer.extend(b"hello world Shell> extra")  # type: ignore[attr-defined]

    observed = transport.recv_until(pattern=b"Shell>", timeout=1.0)
    testbase.assert_true(observed.endswith(b"Shell>"))
    testbase.assert_true(b"hello world" in observed)
    # Leftover bytes should be recoverable via recv().
    leftover_bytes = transport._read_buffer  # type: ignore[attr-defined]
    testbase.assert_true(b"extra" in bytes(leftover_bytes))
    transport.disconnect()
    return

def test_recv_until_matches_compiled_regex() -> None:
    """
        A compiled :class:`re.Pattern` of bytes is matched via
        :meth:`re.Pattern.search`.
    """
    ctx = SimpleNamespace()
    _setup_recv_until(ctx)
    transport = _make_connected_transport()
    transport._read_buffer.extend(b"boot banner\nFS0:\\> done")  # type: ignore[attr-defined]

    pattern = re.compile(rb"FS\d+:\\>")
    observed = transport.recv_until(pattern=pattern, timeout=1.0)
    testbase.assert_true(observed.endswith(b"FS0:\\>"))
    transport.disconnect()
    return

def test_recv_until_raises_timeout_with_partial() -> None:
    """
        When the pattern does not arrive before ``timeout``,
        :class:`TimeoutError` is raised with ``partial`` bytes
        attached.
    """
    ctx = SimpleNamespace()
    _setup_recv_until(ctx)
    transport = _make_connected_transport()
    # Patch the backend reader to always return no bytes, so the
    # loop exhausts its deadline.
    with mock.patch.object(transport, "_recv_ipmitool", return_value=b""):
        transport._read_buffer.extend(b"prefix-without-match")  # type: ignore[attr-defined]

        raised = False
        try:
            transport.recv_until(pattern=b"NEVER-ARRIVES", timeout=0.15)
        except TimeoutError as terr:
            raised = True
            testbase.assert_true(hasattr(terr, "partial"))
            partial_bytes = getattr(terr, "partial")
            testbase.assert_true(isinstance(partial_bytes, bytes))
            testbase.assert_true(b"prefix-without-match" in partial_bytes)

        testbase.assert_true(raised)
    transport.disconnect()
    return


# ---------------------------------------------------------------------------
# disconnect() behavior
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# DisconnectTestCase
# ----------------------------------------------------------------------

"""
    Tests around the :meth:`disconnect` code path.
"""

def _setup_disconnect(ctx) -> None:
    """
        Reset the fake Popen registry before each test.
    """
    _FakePopen.instances = []
    return

def test_disconnect_sends_escape_and_terminates() -> None:
    """
        :meth:`disconnect` writes the ``~.`` escape to the
        subprocess stdin and then sends ``SIGTERM`` when the process
        has not yet exited.
    """
    ctx = SimpleNamespace()
    _setup_disconnect(ctx)
    cred = _build_credential()
    transport = UefiIpmiSolTransport(credential=cred)

    fake = _FakePopen(argv=["ipmitool"], stdin=subprocess.PIPE,
                      stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    transport._process = fake  # type: ignore[attr-defined]
    transport._connected = True  # type: ignore[attr-defined]

    transport.disconnect()

    # The escape sequence should have been written to stdin.
    written_bytes = fake.stdin.getvalue()
    testbase.assert_equal(written_bytes, SOL_ESCAPE_SEQUENCE)
    # The subprocess was not exiting on escape; SIGTERM should have
    # been applied.
    testbase.assert_true(signal.SIGTERM in fake._signals_received)
    # Transport should be disconnected now.
    testbase.assert_false(transport.connected)
    return

def test_disconnect_is_noop_when_not_connected() -> None:
    """
        Calling :meth:`disconnect` on a fresh transport does not
        raise.
    """
    ctx = SimpleNamespace()
    _setup_disconnect(ctx)
    cred = _build_credential()
    transport = UefiIpmiSolTransport(credential=cred)
    # No exception should escape.
    transport.disconnect()
    testbase.assert_false(transport.connected)
    return
