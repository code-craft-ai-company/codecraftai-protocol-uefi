"""
    .. module:: test_uefiserialtransport
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Unit tests for :class:`UefiSerialTransport`.  Uses
               ``pty.openpty()`` pseudo-serial pairs on POSIX for
               read/write integration tests and mocks
               :class:`serial.Serial` for the connection-retry tests.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Code Craft AI LLC"
__credits__ = []


import os
import re
import time
import unittest

from typing import List, Optional
from unittest import mock

import serial

from ccai.errors.exceptions import ConfigurationError, SemanticError

from ccai.protocols.uefi.uefiaspects import (
    ActionPattern,
    UefiCmdAspects,
)
from ccai.protocols.uefi.uefiexceptions import UefiTransportError
from ccai.protocols.uefi.uefiinterfaces import IUefiTransport
from ccai.protocols.uefi.uefiserialcredential import UefiSerialCredential

from ccai.protocols.uefi.uefiserialtransport import (
    ACCEPTED_NEWLINE_MODES,
    ACCEPTED_SCHEMES,
    ENDPOINT_SCHEME_LOCAL,
    ENDPOINT_SCHEME_SOCKET,
    ENDPOINT_SCHEME_TELNET,
    NEWLINE_MODE_CRLF_TO_LF,
    NEWLINE_MODE_CR_TO_LF,
    NEWLINE_MODE_NONE,
    UefiSerialTransport,
    _find_match_end,
    _make_normalizer,
    _parse_endpoint,
    _strip_telnet_iac,
)


# ---------------------------------------------------------------------------
# PTY support gate
# ---------------------------------------------------------------------------

HAS_PTY = hasattr(os, "openpty")


# ---------------------------------------------------------------------------
# Fake Serial used for retry / lifecycle tests (avoids touching real hardware)
# ---------------------------------------------------------------------------

class _FakeSerialBase:
    """
        Baseline fake :class:`serial.Serial` used by the mock-based retry
        tests.  Subclasses override the constructor behavior.
    """

    def __init__(self, *args, **kwargs) -> None:
        """
            Initialize the fake serial handle.
        """
        self.is_open = True
        self.timeout: Optional[float] = kwargs.get("timeout", 0)
        self.in_waiting = 0
        self._read_queue: List[bytes] = []
        return

    def close(self) -> None:
        """
            Mark the handle closed.
        """
        self.is_open = False
        return

    def read(self, size: int) -> bytes:
        """
            Return the next queued chunk, truncated to ``size``.
        """
        if len(self._read_queue) == 0:
            rtnval = b""
            return rtnval

        head = self._read_queue.pop(0)
        if len(head) > size:
            self._read_queue.insert(0, head[size:])
            rtnval = head[:size]
            return rtnval

        rtnval = head
        return rtnval

    def write(self, data: bytes) -> int:
        """
            Count the bytes written.
        """
        rtnval = len(data)
        return rtnval


def _build_default_aspects(action_pattern: ActionPattern = ActionPattern.SINGLE_CALL,
                           connection_timeout: float = 1.0,
                           connection_interval: float = 0.05,
                           inactivity_interval: float = 0.05,
                           inactivity_timeout: float = 10.0,
                           allowed_connection_exceptions=(serial.SerialException, ConnectionError),
                           must_connect: bool = False) -> UefiCmdAspects:
    """
        Build a small :class:`UefiCmdAspects` preset with short intervals
        so tests run quickly.
    """
    rtnval = UefiCmdAspects(
        action_pattern=action_pattern,
        connection_timeout=connection_timeout,
        connection_interval=connection_interval,
        inactivity_interval=inactivity_interval,
        inactivity_timeout=inactivity_timeout,
        allowed_connection_exceptions=allowed_connection_exceptions,
        must_connect=must_connect,
    )
    return rtnval


# ---------------------------------------------------------------------------
# Endpoint-parsing tests
# ---------------------------------------------------------------------------

class ParseEndpointTestCase(unittest.TestCase):

    def test_local_posix_device(self) -> None:
        """
            Bare POSIX device paths classify as 'local'.
        """
        scheme, address = _parse_endpoint(device="/dev/ttyUSB0")
        self.assertEqual(scheme, ENDPOINT_SCHEME_LOCAL)
        self.assertEqual(address, "/dev/ttyUSB0")
        return

    def test_local_com_device(self) -> None:
        """
            Bare Windows COM device paths classify as 'local'.
        """
        scheme, address = _parse_endpoint(device="COM3")
        self.assertEqual(scheme, ENDPOINT_SCHEME_LOCAL)
        self.assertEqual(address, "COM3")
        return

    def test_socket_scheme(self) -> None:
        """
            socket:// endpoints classify as 'socket' and expose host:port.
        """
        scheme, address = _parse_endpoint(device="socket://concentrator.lab:7001")
        self.assertEqual(scheme, ENDPOINT_SCHEME_SOCKET)
        self.assertEqual(address, "concentrator.lab:7001")
        return

    def test_telnet_scheme(self) -> None:
        """
            telnet:// endpoints classify as 'telnet' and expose host:port.
        """
        scheme, address = _parse_endpoint(device="telnet://concentrator.lab:23")
        self.assertEqual(scheme, ENDPOINT_SCHEME_TELNET)
        self.assertEqual(address, "concentrator.lab:23")
        return

    def test_unknown_scheme_raises(self) -> None:
        """
            An unrecognized scheme must raise ConfigurationError.
        """
        with self.assertRaises(ConfigurationError):
            _parse_endpoint(device="rfc2217://concentrator.lab:7001")
        return


# ---------------------------------------------------------------------------
# Line-ending normalizer tests
# ---------------------------------------------------------------------------

class NormalizerTestCase(unittest.TestCase):

    def test_crlf_to_lf_single_chunk(self) -> None:
        """
            CRLF sequences collapse to LF in a single chunk.
        """
        normalize = _make_normalizer(mode=NEWLINE_MODE_CRLF_TO_LF)
        result = normalize(b"abc\r\ndef\r\n")
        self.assertEqual(result, b"abc\ndef\n")
        return

    def test_crlf_to_lf_split_boundary(self) -> None:
        """
            A CRLF split across two chunks must still collapse.
        """
        normalize = _make_normalizer(mode=NEWLINE_MODE_CRLF_TO_LF)
        first = normalize(b"abc\r")
        second = normalize(b"\nxyz\r\n")
        combined = first + second
        self.assertEqual(combined, b"abc\nxyz\n")
        return

    def test_cr_to_lf(self) -> None:
        """
            Lone CR is translated to LF; CRLF still becomes LF.
        """
        normalize = _make_normalizer(mode=NEWLINE_MODE_CR_TO_LF)
        result = normalize(b"abc\rdef\r\nghi")
        self.assertEqual(result, b"abc\ndef\nghi")
        return

    def test_none_passthrough(self) -> None:
        """
            none mode passes bytes through unchanged.
        """
        normalize = _make_normalizer(mode=NEWLINE_MODE_NONE)
        result = normalize(b"abc\r\ndef\r")
        self.assertEqual(result, b"abc\r\ndef\r")
        return

    def test_invalid_mode_raises(self) -> None:
        """
            An unknown normalization mode raises ConfigurationError.
        """
        with self.assertRaises(ConfigurationError):
            _make_normalizer(mode="rot13")
        return


# ---------------------------------------------------------------------------
# Telnet IAC stripping
# ---------------------------------------------------------------------------

class TelnetIacTestCase(unittest.TestCase):

    def test_strip_three_byte_negotiation(self) -> None:
        """
            IAC WILL ECHO (0xFF 0xFB 0x01) must be stripped.
        """
        carry = bytearray()
        data = b"hi\xff\xfb\x01there"
        result = _strip_telnet_iac(buffer=carry, chunk=data)
        self.assertEqual(result, b"hithere")
        self.assertEqual(len(carry), 0)
        return

    def test_escape_iac_iac(self) -> None:
        """
            IAC IAC (0xFF 0xFF) is an escaped literal 0xFF byte.
        """
        carry = bytearray()
        data = b"a\xff\xffb"
        result = _strip_telnet_iac(buffer=carry, chunk=data)
        self.assertEqual(result, b"a\xffb")
        return

    def test_partial_sequence_carries(self) -> None:
        """
            A partial IAC sequence at end-of-chunk carries into the next.
        """
        carry = bytearray()
        first = _strip_telnet_iac(buffer=carry, chunk=b"abc\xff")
        self.assertEqual(first, b"abc")
        self.assertEqual(bytes(carry), b"\xff")
        second = _strip_telnet_iac(buffer=carry, chunk=b"\xfb\x01rest")
        self.assertEqual(second, b"rest")
        return


# ---------------------------------------------------------------------------
# _find_match_end
# ---------------------------------------------------------------------------

class FindMatchEndTestCase(unittest.TestCase):

    def test_bytes_needle_found(self) -> None:
        """
            A bytes needle returns the post-match index.
        """
        buf = bytearray(b"hello, Shell> ")
        end = _find_match_end(buffer=buf, needle=b"Shell>")
        self.assertEqual(end, len(b"hello, Shell>"))
        return

    def test_bytes_needle_missing(self) -> None:
        """
            A missing bytes needle returns None.
        """
        buf = bytearray(b"hello, world")
        end = _find_match_end(buffer=buf, needle=b"Shell>")
        self.assertIsNone(end)
        return

    def test_regex_needle_found(self) -> None:
        """
            A compiled regex returns the match end index.
        """
        buf = bytearray(b"hello, FS0:\\> ")
        pattern = re.compile(rb"FS\d+:\\[^>]*>\s*")
        end = _find_match_end(buffer=buf, needle=pattern)
        self.assertEqual(end, len(buf))
        return


# ---------------------------------------------------------------------------
# Construction / protocol compliance
# ---------------------------------------------------------------------------

class ConstructionTestCase(unittest.TestCase):

    def test_satisfies_iuefitransport(self) -> None:
        """
            UefiSerialTransport must satisfy the runtime-checkable
            IUefiTransport protocol.
        """
        cred = UefiSerialCredential(device="/dev/null-fake")
        transport = UefiSerialTransport(credential=cred)
        self.assertIsInstance(transport, IUefiTransport)
        return

    def test_transport_kind_is_serial(self) -> None:
        """
            transport_kind must equal the literal 'serial'.
        """
        cred = UefiSerialCredential(device="/dev/null-fake")
        transport = UefiSerialTransport(credential=cred)
        self.assertEqual(transport.transport_kind, "serial")
        return

    def test_endpoint_scheme_classification(self) -> None:
        """
            The transport exposes a resolved endpoint_scheme property.
        """
        cred = UefiSerialCredential(device="socket://host.lab:7001")
        transport = UefiSerialTransport(credential=cred)
        self.assertEqual(transport.endpoint_scheme, ENDPOINT_SCHEME_SOCKET)
        return

    def test_rejects_non_credential(self) -> None:
        """
            A non-UefiSerialCredential must be rejected at construction.
        """
        with self.assertRaises(ConfigurationError):
            UefiSerialTransport(credential="/dev/ttyUSB0")  # type: ignore[arg-type]
        return


# ---------------------------------------------------------------------------
# Retry / aspects-driven connect tests
# ---------------------------------------------------------------------------

class _FailThenSucceedSerial(_FakeSerialBase):
    """
        Fake serial that raises ``SerialException`` on the first
        ``FAIL_COUNT`` calls and succeeds thereafter.
    """
    FAIL_COUNT: int = 0
    invocations: int = 0

    @classmethod
    def reset(cls, fail_count: int) -> None:
        """
            Reset class-level state for a fresh test.
        """
        cls.FAIL_COUNT = fail_count
        cls.invocations = 0
        return

    def __init__(self, *args, **kwargs) -> None:
        """
            Record the construction attempt and raise if still in the
            failing window.
        """
        cls = type(self)
        cls.invocations += 1
        if cls.invocations <= cls.FAIL_COUNT:
            raise serial.SerialException("fake failure #{}".format(cls.invocations))
        _FakeSerialBase.__init__(self, *args, **kwargs)
        return


class ConnectRetryTestCase(unittest.TestCase):

    def test_do_until_success_retries_until_open(self) -> None:
        """
            DO_UNTIL_SUCCESS must retry past allowed SerialException
            failures and eventually succeed.
        """
        _FailThenSucceedSerial.reset(fail_count=3)

        cred = UefiSerialCredential(device="/dev/null-fake")
        aspects = _build_default_aspects(
            action_pattern=ActionPattern.DO_UNTIL_SUCCESS,
            connection_timeout=2.0,
            connection_interval=0.01,
        )
        transport = UefiSerialTransport(credential=cred, aspects=aspects)

        with mock.patch(
            "ccai.protocols.uefi.uefiserialtransport.Serial",
            _FailThenSucceedSerial,
        ):
            transport.connect()

        self.assertTrue(transport.connected)
        self.assertEqual(_FailThenSucceedSerial.invocations, 4)
        transport.disconnect()
        return

    def test_do_until_success_bounded_by_timeout(self) -> None:
        """
            When the failures outlast connection_timeout, DO_UNTIL_SUCCESS
            must raise TimeoutError with informative details.
        """
        _FailThenSucceedSerial.reset(fail_count=10_000)  # never recover

        cred = UefiSerialCredential(device="/dev/null-fake")
        aspects = _build_default_aspects(
            action_pattern=ActionPattern.DO_UNTIL_SUCCESS,
            connection_timeout=0.3,
            connection_interval=0.05,
        )
        transport = UefiSerialTransport(credential=cred, aspects=aspects)

        with mock.patch(
            "ccai.protocols.uefi.uefiserialtransport.Serial",
            _FailThenSucceedSerial,
        ):
            with self.assertRaises(TimeoutError) as ctx:
                transport.connect()

        terr = ctx.exception
        self.assertTrue(hasattr(terr, "details"))
        self.assertTrue(_FailThenSucceedSerial.invocations >= 1)
        self.assertFalse(transport.connected)
        return

    def test_single_call_raises_on_first_failure(self) -> None:
        """
            SINGLE_CALL performs exactly one attempt and raises on failure.
        """
        _FailThenSucceedSerial.reset(fail_count=1)

        cred = UefiSerialCredential(device="/dev/null-fake")
        aspects = _build_default_aspects(
            action_pattern=ActionPattern.SINGLE_CALL,
            connection_timeout=1.0,
            connection_interval=0.05,
        )
        transport = UefiSerialTransport(credential=cred, aspects=aspects)

        with mock.patch(
            "ccai.protocols.uefi.uefiserialtransport.Serial",
            _FailThenSucceedSerial,
        ):
            with self.assertRaises(UefiTransportError):
                transport.connect()

        self.assertEqual(_FailThenSucceedSerial.invocations, 1)
        self.assertFalse(transport.connected)
        return

    def test_must_connect_true_raises_immediately(self) -> None:
        """
            must_connect=True escalates a soft-failure into a hard failure
            on the first attempt.
        """
        _FailThenSucceedSerial.reset(fail_count=5)

        cred = UefiSerialCredential(device="/dev/null-fake")
        aspects = _build_default_aspects(
            action_pattern=ActionPattern.DO_UNTIL_SUCCESS,
            connection_timeout=2.0,
            connection_interval=0.05,
            must_connect=True,
        )
        transport = UefiSerialTransport(credential=cred, aspects=aspects)

        with mock.patch(
            "ccai.protocols.uefi.uefiserialtransport.Serial",
            _FailThenSucceedSerial,
        ):
            with self.assertRaises(UefiTransportError):
                transport.connect()

        self.assertEqual(_FailThenSucceedSerial.invocations, 1)
        self.assertFalse(transport.connected)
        return

    def test_do_while_success_raises_semantic_error(self) -> None:
        """
            DO_WHILE_SUCCESS is not meaningful for connect(); the transport
            must raise SemanticError.
        """
        cred = UefiSerialCredential(device="/dev/null-fake")
        aspects = _build_default_aspects(
            action_pattern=ActionPattern.DO_WHILE_SUCCESS,
        )
        transport = UefiSerialTransport(credential=cred, aspects=aspects)

        with self.assertRaises(SemanticError):
            transport.connect()
        return

    def test_single_connected_call_noop_on_connected(self) -> None:
        """
            SINGLE_CONNECTED_CALL is a no-op when already connected; no
            additional backend open must occur.
        """
        _FailThenSucceedSerial.reset(fail_count=0)

        cred = UefiSerialCredential(device="/dev/null-fake")
        aspects_single_call = _build_default_aspects(
            action_pattern=ActionPattern.SINGLE_CALL,
        )
        transport = UefiSerialTransport(credential=cred, aspects=aspects_single_call)

        with mock.patch(
            "ccai.protocols.uefi.uefiserialtransport.Serial",
            _FailThenSucceedSerial,
        ):
            transport.connect()

        self.assertTrue(transport.connected)
        first_invocations = _FailThenSucceedSerial.invocations

        aspects_single_connected = _build_default_aspects(
            action_pattern=ActionPattern.SINGLE_CONNECTED_CALL,
        )
        with mock.patch(
            "ccai.protocols.uefi.uefiserialtransport.Serial",
            _FailThenSucceedSerial,
        ):
            transport.connect(aspects=aspects_single_connected)

        self.assertEqual(_FailThenSucceedSerial.invocations, first_invocations)
        transport.disconnect()
        return


# ---------------------------------------------------------------------------
# PTY-backed integration tests
# ---------------------------------------------------------------------------

@unittest.skipUnless(HAS_PTY, "os.openpty not available on this platform")
class PtyRoundTripTestCase(unittest.TestCase):
    """
        Integration tests that back the transport against a real pseudo
        terminal pair so that pyserial's path is exercised end-to-end.
    """

    def setUp(self) -> None:
        """
            Open a master / slave PTY pair and wrap the slave side in a
            UefiSerialTransport.
        """
        self._master_fd, self._slave_fd = os.openpty()
        self._slave_name = os.ttyname(self._slave_fd)

        cred = UefiSerialCredential(device=self._slave_name)
        aspects = _build_default_aspects(
            action_pattern=ActionPattern.SINGLE_CALL,
            connection_timeout=2.0,
            connection_interval=0.05,
            inactivity_interval=0.05,
        )
        self._transport = UefiSerialTransport(credential=cred, aspects=aspects)
        self._transport.connect()
        return

    def tearDown(self) -> None:
        """
            Close both ends of the PTY pair.
        """
        try:
            self._transport.disconnect()
        except Exception:
            pass

        for fd in (self._master_fd, self._slave_fd):
            try:
                os.close(fd)
            except OSError:
                pass
        return

    def _write_master(self, data: bytes) -> None:
        """
            Push bytes from the 'device' side so the transport can read them.
        """
        written = 0
        while written < len(data):
            count = os.write(self._master_fd, data[written:])
            if count <= 0:
                break
            written += count
        return

    def test_recv_until_bytes_pattern(self) -> None:
        """
            recv_until must return every byte from call start through the
            first occurrence of a bytes pattern, inclusive.
        """
        self._write_master(b"hello world\r\nShell> ")
        result = self._transport.recv_until(pattern=b"Shell> ", timeout=2.0)
        self.assertIn(b"hello world", result)
        self.assertTrue(result.endswith(b"Shell> "))
        return

    def test_recv_until_regex_pattern(self) -> None:
        """
            recv_until must accept a compiled re.Pattern[bytes] needle.
        """
        self._write_master(b"greetings\r\nFS0:\\> ")
        pattern = re.compile(rb"FS\d+:\\[^>]*>\s*")
        result = self._transport.recv_until(pattern=pattern, timeout=2.0)
        self.assertIn(b"greetings", result)
        self.assertTrue(result.rstrip().endswith(b"FS0:\\>"))
        return

    def test_recv_until_timeout_populates_partial(self) -> None:
        """
            A missing pattern must raise TimeoutError with the observed
            partial bytes attached.
        """
        self._write_master(b"chatter without prompt")

        with self.assertRaises(TimeoutError) as ctx:
            self._transport.recv_until(pattern=b"NEVER_APPEARS>", timeout=0.5)

        terr = ctx.exception
        self.assertTrue(hasattr(terr, "partial"))
        self.assertIsInstance(terr.partial, bytes)
        self.assertIn(b"chatter", terr.partial)
        self.assertTrue(hasattr(terr, "details"))
        self.assertTrue(len(terr.details) > 0)
        return

    def test_send_returns_byte_count(self) -> None:
        """
            send() returns the count of bytes written to the pipe.
        """
        payload = b"hello device\n"
        count = self._transport.send(data=payload)
        self.assertEqual(count, len(payload))

        # Drain the master side to avoid PTY buffer back-pressure in tearDown.
        deadline = time.monotonic() + 1.0
        drained = b""
        while time.monotonic() < deadline and len(drained) < len(payload):
            try:
                chunk = os.read(self._master_fd, 1024)
            except OSError:
                break
            if len(chunk) == 0:
                break
            drained += chunk

        self.assertIn(b"hello device", drained)
        return


# ---------------------------------------------------------------------------
# Context-manager tests
# ---------------------------------------------------------------------------

class ContextManagerTestCase(unittest.TestCase):

    def test_context_manager_clean_exit_on_failed_connect(self) -> None:
        """
            Entering a with-block where connect() fails must still run
            __exit__ without raising from __exit__.
        """
        _FailThenSucceedSerial.reset(fail_count=10_000)

        cred = UefiSerialCredential(device="/dev/null-fake")
        aspects = _build_default_aspects(
            action_pattern=ActionPattern.SINGLE_CALL,
            connection_timeout=0.2,
            connection_interval=0.05,
        )
        transport = UefiSerialTransport(credential=cred, aspects=aspects)

        with mock.patch(
            "ccai.protocols.uefi.uefiserialtransport.Serial",
            _FailThenSucceedSerial,
        ):
            try:
                with transport:
                    self.fail("with-block should not have entered; connect() must have raised")
            except UefiTransportError:
                # Expected — connect raised.
                pass

        # A follow-up disconnect must be a no-op.
        transport.disconnect()
        self.assertFalse(transport.connected)
        return

    def test_disconnect_without_connect_is_noop(self) -> None:
        """
            disconnect() before connect() must be silent and idempotent.
        """
        cred = UefiSerialCredential(device="/dev/null-fake")
        transport = UefiSerialTransport(credential=cred)
        transport.disconnect()
        transport.disconnect()
        self.assertFalse(transport.connected)
        return

    @unittest.skipUnless(HAS_PTY, "os.openpty not available on this platform")
    def test_context_manager_on_pty(self) -> None:
        """
            A clean with-block on a real PTY pair must connect on entry
            and disconnect on exit.
        """
        master_fd, slave_fd = os.openpty()
        try:
            slave_name = os.ttyname(slave_fd)
            cred = UefiSerialCredential(device=slave_name)
            aspects = _build_default_aspects(
                action_pattern=ActionPattern.SINGLE_CALL,
                connection_timeout=2.0,
            )
            transport = UefiSerialTransport(credential=cred, aspects=aspects)

            with transport as t:
                self.assertIs(t, transport)
                self.assertTrue(transport.connected)

            self.assertFalse(transport.connected)
        finally:
            for fd in (master_fd, slave_fd):
                try:
                    os.close(fd)
                except OSError:
                    pass
        return


# ---------------------------------------------------------------------------
# Accepted-scheme / newline-mode surface
# ---------------------------------------------------------------------------

class SurfaceTestCase(unittest.TestCase):

    def test_accepted_schemes_set(self) -> None:
        """
            The published accepted-scheme tuple contains the documented
            values.
        """
        self.assertIn(ENDPOINT_SCHEME_LOCAL, ACCEPTED_SCHEMES)
        self.assertIn(ENDPOINT_SCHEME_SOCKET, ACCEPTED_SCHEMES)
        self.assertIn(ENDPOINT_SCHEME_TELNET, ACCEPTED_SCHEMES)
        return

    def test_accepted_newline_modes_set(self) -> None:
        """
            The published accepted-newline-mode tuple contains every
            documented value.
        """
        self.assertIn("auto", ACCEPTED_NEWLINE_MODES)
        self.assertIn(NEWLINE_MODE_CRLF_TO_LF, ACCEPTED_NEWLINE_MODES)
        self.assertIn(NEWLINE_MODE_CR_TO_LF, ACCEPTED_NEWLINE_MODES)
        self.assertIn(NEWLINE_MODE_NONE, ACCEPTED_NEWLINE_MODES)
        return


if __name__ == "__main__":
    unittest.main()
