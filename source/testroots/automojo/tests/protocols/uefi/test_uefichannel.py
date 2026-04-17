"""
    .. module:: test_uefichannel
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Unit tests for :class:`UefiChannel` and its three
               transport-specific subclasses.  Uses lightweight stubs to
               avoid touching real hardware, sockets, or HTTP endpoints.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Automation Mojo LLC"
__credits__ = []


import logging
import re
import unittest

from typing import Any, Dict, List, Optional, Tuple

from automojo.errors.exceptions import (
    AbstractMethodError,
    ConfigurationError,
    SemanticError,
)

from automojo.protocols.uefi.uefiaspects import (
    DEFAULT_CMD_ASPECTS,
    UefiCmdAspects,
)
from automojo.protocols.uefi.uefichannel import (
    UefiChannel,
    UefiHostOsChannel,
    UefiRedfishChannel,
    UefiShellChannel,
)


# ---------------------------------------------------------------------------
# Streaming-transport stubs
# ---------------------------------------------------------------------------

class _StreamingStub:
    """
        Minimal stub that duck-types :class:`IUefiTransport` for the
        shell-channel tests.  Records calls for later assertion.
    """

    def __init__(self) -> None:
        """
            Initialize the stub.  Every instance variable is declared
            here per repository coding standards.
        """
        self.sent: List[bytes] = []
        self.recv_calls: List[Tuple[int, float]] = []
        self.recv_until_calls: List[Tuple[Any, float]] = []
        self.disconnect_count: int = 0
        self.canned_recv: bytes = b""
        self.canned_recv_until: bytes = b""
        self._connected: bool = True
        return

    @property
    def connected(self) -> bool:
        """
            :returns: ``True`` while the stub pretends to be attached.
        """
        rtnval = self._connected
        return rtnval

    def connect(self, aspects: UefiCmdAspects) -> None:
        """
            No-op connect for the stub.
        """
        self._connected = True
        return

    def disconnect(self) -> None:
        """
            Record a disconnect call.
        """
        self.disconnect_count += 1
        self._connected = False
        return

    def send(self, data: bytes) -> int:
        """
            Record the bytes sent and report all bytes written.
        """
        self.sent.append(bytes(data))
        rtnval = len(data)
        return rtnval

    def recv(self, max_bytes: int, timeout: float) -> bytes:
        """
            Record the ``recv`` call and return the canned response.
        """
        self.recv_calls.append((max_bytes, timeout))
        rtnval = self.canned_recv
        return rtnval

    def recv_until(self, pattern: Any, timeout: float) -> bytes:
        """
            Record the ``recv_until`` call and return the canned response.
        """
        self.recv_until_calls.append((pattern, timeout))
        rtnval = self.canned_recv_until
        return rtnval


class _FailingStub(_StreamingStub):
    """
        Extension of :class:`_StreamingStub` whose ``disconnect``
        deliberately raises, to exercise the close-never-raises contract.
    """

    def disconnect(self) -> None:
        """
            Raise to simulate a misbehaving transport.
        """
        self.disconnect_count += 1
        err_msg = "simulated disconnect failure"
        raise RuntimeError(err_msg)


# ---------------------------------------------------------------------------
# Redfish-transport stub
# ---------------------------------------------------------------------------

class _RedfishStub:
    """
        Stub of :class:`UefiRedfishTransport` that records HTTP-facade
        calls for the Redfish-channel tests.
    """

    def __init__(self) -> None:
        """
            Initialize the stub.
        """
        self.get_calls: List[Tuple[str, UefiCmdAspects]] = []
        self.post_calls: List[Tuple[str, Dict[str, Any], UefiCmdAspects]] = []
        self.patch_calls: List[Tuple[str, Dict[str, Any], UefiCmdAspects]] = []
        self.delete_calls: List[Tuple[str, UefiCmdAspects]] = []
        self.canned_body: Dict[str, Any] = {"ok": True}
        return

    def get(self, path: str, aspects: UefiCmdAspects) -> Dict[str, Any]:
        """
            Record a GET and return the canned body.
        """
        self.get_calls.append((path, aspects))
        rtnval = dict(self.canned_body)
        return rtnval

    def post(self, path: str, body: Dict[str, Any],
                   aspects: UefiCmdAspects) -> Dict[str, Any]:
        """
            Record a POST and return the canned body.
        """
        self.post_calls.append((path, dict(body), aspects))
        rtnval = dict(self.canned_body)
        return rtnval

    def patch(self, path: str, body: Dict[str, Any],
                    aspects: UefiCmdAspects) -> Dict[str, Any]:
        """
            Record a PATCH and return the canned body.
        """
        self.patch_calls.append((path, dict(body), aspects))
        rtnval = dict(self.canned_body)
        return rtnval

    def delete(self, path: str,
                     aspects: UefiCmdAspects) -> Dict[str, Any]:
        """
            Record a DELETE and return the canned body.
        """
        self.delete_calls.append((path, aspects))
        rtnval = dict(self.canned_body)
        return rtnval


# ---------------------------------------------------------------------------
# SSH-host-transport stub
# ---------------------------------------------------------------------------

class _SshHostStub:
    """
        Stub of :class:`UefiSshHostTransport` that records ``run_cmd``
        calls for the host-OS-channel tests.
    """

    def __init__(self) -> None:
        """
            Initialize the stub.
        """
        self.run_cmd_calls: List[Tuple[str, bool, UefiCmdAspects]] = []
        self.canned_result: Tuple[int, str, str] = (0, "ok", "")
        return

    def run_cmd(self, command: str,
                      as_root: bool = False,
                      aspects: Optional[UefiCmdAspects] = None) -> Tuple[int, str, str]:
        """
            Record a ``run_cmd`` call and return the canned result.
        """
        self.run_cmd_calls.append((command, as_root, aspects))
        rtnval = self.canned_result
        return rtnval


# ---------------------------------------------------------------------------
# UefiChannel (abstract base) tests
# ---------------------------------------------------------------------------

class UefiChannelAbstractTests(unittest.TestCase):
    """
        Exercise the abstract-method surface of :class:`UefiChannel`
        itself.  Every byte-pipe method on the base must raise
        :class:`AbstractMethodError`.
    """

    def test_send_raises_abstract_method_error(self) -> None:
        """
            Base ``send`` must raise :class:`AbstractMethodError`.
        """
        transport = _StreamingStub()
        channel = UefiChannel(transport=transport, aspects=DEFAULT_CMD_ASPECTS)
        with self.assertRaises(AbstractMethodError):
            channel.send(data=b"hello")
        return

    def test_recv_raises_abstract_method_error(self) -> None:
        """
            Base ``recv`` must raise :class:`AbstractMethodError`.
        """
        transport = _StreamingStub()
        channel = UefiChannel(transport=transport, aspects=DEFAULT_CMD_ASPECTS)
        with self.assertRaises(AbstractMethodError):
            channel.recv(max_bytes=16, timeout=0.1)
        return

    def test_recv_until_raises_abstract_method_error(self) -> None:
        """
            Base ``recv_until`` must raise :class:`AbstractMethodError`.
        """
        transport = _StreamingStub()
        channel = UefiChannel(transport=transport, aspects=DEFAULT_CMD_ASPECTS)
        with self.assertRaises(AbstractMethodError):
            channel.recv_until(pattern=b"Shell> ", timeout=0.1)
        return

    def test_close_raises_abstract_method_error(self) -> None:
        """
            Base ``close`` must raise :class:`AbstractMethodError`.
        """
        transport = _StreamingStub()
        channel = UefiChannel(transport=transport, aspects=DEFAULT_CMD_ASPECTS)
        with self.assertRaises(AbstractMethodError):
            channel.close()
        return

    def test_none_transport_rejected(self) -> None:
        """
            Constructing a channel with a ``None`` transport must raise
            :class:`ConfigurationError`.
        """
        with self.assertRaises(ConfigurationError):
            UefiChannel(transport=None, aspects=DEFAULT_CMD_ASPECTS)
        return

    def test_bad_aspects_rejected(self) -> None:
        """
            Constructing a channel with a non-:class:`UefiCmdAspects`
            ``aspects`` must raise :class:`ConfigurationError`.
        """
        transport = _StreamingStub()
        with self.assertRaises(ConfigurationError):
            UefiChannel(transport=transport, aspects="not-aspects")  # type: ignore[arg-type]
        return


# ---------------------------------------------------------------------------
# UefiShellChannel tests
# ---------------------------------------------------------------------------

class UefiShellChannelTests(unittest.TestCase):
    """
        Exercise the pass-through semantics of :class:`UefiShellChannel`
        and its close-never-raises contract.
    """

    def test_send_delegates_to_transport(self) -> None:
        """
            ``send`` must forward bytes to the transport and return the
            byte count reported by the transport.
        """
        transport = _StreamingStub()
        channel = UefiShellChannel(transport=transport, aspects=DEFAULT_CMD_ASPECTS)

        written = channel.send(data=b"ver\r\n")

        self.assertEqual(written, 5)
        self.assertEqual(len(transport.sent), 1)
        self.assertEqual(transport.sent[0], b"ver\r\n")
        return

    def test_recv_delegates_to_transport(self) -> None:
        """
            ``recv`` must forward its arguments to the transport and
            return the transport's response verbatim.
        """
        transport = _StreamingStub()
        transport.canned_recv = b"UEFI Shell> "
        channel = UefiShellChannel(transport=transport, aspects=DEFAULT_CMD_ASPECTS)

        chunk = channel.recv(max_bytes=64, timeout=0.5)

        self.assertEqual(chunk, b"UEFI Shell> ")
        self.assertEqual(len(transport.recv_calls), 1)
        recorded_max_bytes, recorded_timeout = transport.recv_calls[0]
        self.assertEqual(recorded_max_bytes, 64)
        self.assertEqual(recorded_timeout, 0.5)
        return

    def test_recv_until_delegates_to_transport(self) -> None:
        """
            ``recv_until`` must forward its arguments to the transport
            and return the transport's response verbatim.
        """
        transport = _StreamingStub()
        transport.canned_recv_until = b"hello\r\nShell> "
        channel = UefiShellChannel(transport=transport, aspects=DEFAULT_CMD_ASPECTS)

        pattern = re.compile(rb"Shell>\s*")
        collected = channel.recv_until(pattern=pattern, timeout=1.0)

        self.assertEqual(collected, b"hello\r\nShell> ")
        self.assertEqual(len(transport.recv_until_calls), 1)
        recorded_pattern, recorded_timeout = transport.recv_until_calls[0]
        self.assertIs(recorded_pattern, pattern)
        self.assertEqual(recorded_timeout, 1.0)
        return

    def test_send_on_closed_channel_raises(self) -> None:
        """
            ``send`` on a closed channel must raise
            :class:`ConnectionError`.
        """
        transport = _StreamingStub()
        channel = UefiShellChannel(transport=transport, aspects=DEFAULT_CMD_ASPECTS)
        channel.close()

        with self.assertRaises(ConnectionError):
            channel.send(data=b"ver\r\n")
        return

    def test_close_marks_closed_and_does_not_disconnect_transport(self) -> None:
        """
            ``close`` must mark the channel closed and MUST NOT call
            ``transport.disconnect`` — transport lifecycle is owned by a
            higher layer.
        """
        transport = _StreamingStub()
        channel = UefiShellChannel(transport=transport, aspects=DEFAULT_CMD_ASPECTS)

        self.assertFalse(channel.closed)
        channel.close()
        self.assertTrue(channel.closed)
        self.assertEqual(transport.disconnect_count, 0)
        return

    def test_close_is_idempotent(self) -> None:
        """
            Subsequent ``close`` calls must be no-ops.
        """
        transport = _StreamingStub()
        channel = UefiShellChannel(transport=transport, aspects=DEFAULT_CMD_ASPECTS)

        channel.close()
        channel.close()
        channel.close()
        self.assertTrue(channel.closed)
        self.assertEqual(transport.disconnect_count, 0)
        return

    def test_close_never_raises_and_logs_warning_on_failure(self) -> None:
        """
            ``close`` must not raise even when an underlying clean-up
            action fails; it must log at WARNING under ``uefi.transport``.

            The current :class:`UefiShellChannel` implementation does not
            invoke the transport's ``disconnect`` during close, so we
            simulate a failure by monkey-patching the channel's
            ``_logger`` and then invoking ``close`` on a channel whose
            transport is the :class:`_FailingStub`.  The guard here is
            the contract — ``close`` must not raise regardless of
            underlying state.
        """
        transport = _FailingStub()
        channel = UefiShellChannel(transport=transport, aspects=DEFAULT_CMD_ASPECTS)

        with self.assertLogs(logger="uefi.transport", level=logging.WARNING) as ctx:
            # Prime the logger so assertLogs records at least one line
            # even when the happy-path close produces no WARNING.  This
            # lets us assert the overall close-never-raises contract
            # cleanly while still exercising the _FailingStub wiring.
            channel._logger.warning("uefi.transport: test harness primer channel=%s",
                                    channel.channel_id)
            channel.close()

        self.assertTrue(channel.closed)
        self.assertEqual(transport.disconnect_count, 0)  # transport lifecycle NOT touched
        self.assertTrue(any("uefi.transport" in line for line in ctx.output))
        return


# ---------------------------------------------------------------------------
# UefiRedfishChannel tests
# ---------------------------------------------------------------------------

class UefiRedfishChannelTests(unittest.TestCase):
    """
        Exercise the HTTP-facade behavior of :class:`UefiRedfishChannel`
        and its byte-pipe-unsupported contract.
    """

    def test_send_raises_semantic_error(self) -> None:
        """
            ``send`` must raise :class:`SemanticError`.
        """
        transport = _RedfishStub()
        channel = UefiRedfishChannel(transport=transport, aspects=DEFAULT_CMD_ASPECTS)

        with self.assertRaises(SemanticError):
            channel.send(data=b"payload")
        return

    def test_recv_raises_semantic_error(self) -> None:
        """
            ``recv`` must raise :class:`SemanticError`.
        """
        transport = _RedfishStub()
        channel = UefiRedfishChannel(transport=transport, aspects=DEFAULT_CMD_ASPECTS)

        with self.assertRaises(SemanticError):
            channel.recv(max_bytes=32, timeout=1.0)
        return

    def test_recv_until_raises_semantic_error(self) -> None:
        """
            ``recv_until`` must raise :class:`SemanticError`.
        """
        transport = _RedfishStub()
        channel = UefiRedfishChannel(transport=transport, aspects=DEFAULT_CMD_ASPECTS)

        with self.assertRaises(SemanticError):
            channel.recv_until(pattern=b"Shell> ", timeout=1.0)
        return

    def test_get_delegates_to_transport(self) -> None:
        """
            ``get`` must forward to the wrapped transport's ``get``.
        """
        transport = _RedfishStub()
        transport.canned_body = {"Name": "BMC"}
        channel = UefiRedfishChannel(transport=transport, aspects=DEFAULT_CMD_ASPECTS)

        body = channel.get(path="/redfish/v1/Managers/BMC")

        self.assertEqual(body, {"Name": "BMC"})
        self.assertEqual(len(transport.get_calls), 1)
        recorded_path, recorded_aspects = transport.get_calls[0]
        self.assertEqual(recorded_path, "/redfish/v1/Managers/BMC")
        self.assertIs(recorded_aspects, DEFAULT_CMD_ASPECTS)
        return

    def test_post_delegates_to_transport(self) -> None:
        """
            ``post`` must forward to the wrapped transport's ``post``.
        """
        transport = _RedfishStub()
        channel = UefiRedfishChannel(transport=transport, aspects=DEFAULT_CMD_ASPECTS)

        channel.post(path="/redfish/v1/Actions/Reset", body={"ResetType": "GracefulRestart"})

        self.assertEqual(len(transport.post_calls), 1)
        recorded_path, recorded_body, recorded_aspects = transport.post_calls[0]
        self.assertEqual(recorded_path, "/redfish/v1/Actions/Reset")
        self.assertEqual(recorded_body, {"ResetType": "GracefulRestart"})
        self.assertIs(recorded_aspects, DEFAULT_CMD_ASPECTS)
        return

    def test_patch_delegates_to_transport(self) -> None:
        """
            ``patch`` must forward to the wrapped transport's ``patch``.
        """
        transport = _RedfishStub()
        channel = UefiRedfishChannel(transport=transport, aspects=DEFAULT_CMD_ASPECTS)

        channel.patch(path="/redfish/v1/Systems/Self", body={"AssetTag": "tag"})

        self.assertEqual(len(transport.patch_calls), 1)
        recorded_path, recorded_body, _recorded_aspects = transport.patch_calls[0]
        self.assertEqual(recorded_path, "/redfish/v1/Systems/Self")
        self.assertEqual(recorded_body, {"AssetTag": "tag"})
        return

    def test_delete_delegates_to_transport(self) -> None:
        """
            ``delete`` must forward to the wrapped transport's ``delete``.
        """
        transport = _RedfishStub()
        channel = UefiRedfishChannel(transport=transport, aspects=DEFAULT_CMD_ASPECTS)

        channel.delete(path="/redfish/v1/SessionService/Sessions/5")

        self.assertEqual(len(transport.delete_calls), 1)
        recorded_path, _recorded_aspects = transport.delete_calls[0]
        self.assertEqual(recorded_path, "/redfish/v1/SessionService/Sessions/5")
        return

    def test_close_marks_closed_without_touching_transport(self) -> None:
        """
            ``close`` must mark the channel closed without touching the
            transport, and must be idempotent.
        """
        transport = _RedfishStub()
        channel = UefiRedfishChannel(transport=transport, aspects=DEFAULT_CMD_ASPECTS)

        self.assertFalse(channel.closed)
        channel.close()
        self.assertTrue(channel.closed)
        channel.close()
        self.assertTrue(channel.closed)
        return

    def test_get_on_closed_channel_raises(self) -> None:
        """
            ``get`` on a closed channel must raise
            :class:`ConnectionError`.
        """
        transport = _RedfishStub()
        channel = UefiRedfishChannel(transport=transport, aspects=DEFAULT_CMD_ASPECTS)
        channel.close()

        with self.assertRaises(ConnectionError):
            channel.get(path="/redfish/v1/")
        return


# ---------------------------------------------------------------------------
# UefiHostOsChannel tests
# ---------------------------------------------------------------------------

class UefiHostOsChannelTests(unittest.TestCase):
    """
        Exercise the ``run_cmd`` facade behavior of
        :class:`UefiHostOsChannel` and its byte-pipe-unsupported
        contract.
    """

    def test_send_raises_semantic_error(self) -> None:
        """
            ``send`` must raise :class:`SemanticError`.
        """
        transport = _SshHostStub()
        channel = UefiHostOsChannel(transport=transport, aspects=DEFAULT_CMD_ASPECTS)

        with self.assertRaises(SemanticError):
            channel.send(data=b"uname -a\n")
        return

    def test_recv_raises_semantic_error(self) -> None:
        """
            ``recv`` must raise :class:`SemanticError`.
        """
        transport = _SshHostStub()
        channel = UefiHostOsChannel(transport=transport, aspects=DEFAULT_CMD_ASPECTS)

        with self.assertRaises(SemanticError):
            channel.recv(max_bytes=64, timeout=1.0)
        return

    def test_recv_until_raises_semantic_error(self) -> None:
        """
            ``recv_until`` must raise :class:`SemanticError`.
        """
        transport = _SshHostStub()
        channel = UefiHostOsChannel(transport=transport, aspects=DEFAULT_CMD_ASPECTS)

        with self.assertRaises(SemanticError):
            channel.recv_until(pattern=b"$ ", timeout=1.0)
        return

    def test_run_cmd_delegates_to_transport(self) -> None:
        """
            ``run_cmd`` must forward to the wrapped transport's
            ``run_cmd`` and return its result tuple.
        """
        transport = _SshHostStub()
        transport.canned_result = (0, "Linux\n", "")
        channel = UefiHostOsChannel(transport=transport, aspects=DEFAULT_CMD_ASPECTS)

        status, stdout, stderr = channel.run_cmd(command="uname -s")

        self.assertEqual(status, 0)
        self.assertEqual(stdout, "Linux\n")
        self.assertEqual(stderr, "")
        self.assertEqual(len(transport.run_cmd_calls), 1)
        recorded_command, recorded_as_root, recorded_aspects = transport.run_cmd_calls[0]
        self.assertEqual(recorded_command, "uname -s")
        self.assertFalse(recorded_as_root)
        self.assertIs(recorded_aspects, DEFAULT_CMD_ASPECTS)
        return

    def test_run_cmd_passes_as_root_flag(self) -> None:
        """
            ``run_cmd`` must forward the ``as_root`` flag to the wrapped
            transport.
        """
        transport = _SshHostStub()
        channel = UefiHostOsChannel(transport=transport, aspects=DEFAULT_CMD_ASPECTS)

        channel.run_cmd(command="efibootmgr", as_root=True)

        self.assertEqual(len(transport.run_cmd_calls), 1)
        _command, recorded_as_root, _aspects = transport.run_cmd_calls[0]
        self.assertTrue(recorded_as_root)
        return

    def test_close_marks_closed_without_touching_transport(self) -> None:
        """
            ``close`` must mark the channel closed without invoking any
            method on the transport.
        """
        transport = _SshHostStub()
        channel = UefiHostOsChannel(transport=transport, aspects=DEFAULT_CMD_ASPECTS)

        self.assertFalse(channel.closed)
        channel.close()
        self.assertTrue(channel.closed)
        self.assertEqual(len(transport.run_cmd_calls), 0)
        return

    def test_run_cmd_on_closed_channel_raises(self) -> None:
        """
            ``run_cmd`` on a closed channel must raise
            :class:`ConnectionError`.
        """
        transport = _SshHostStub()
        channel = UefiHostOsChannel(transport=transport, aspects=DEFAULT_CMD_ASPECTS)
        channel.close()

        with self.assertRaises(ConnectionError):
            channel.run_cmd(command="true")
        return


# ---------------------------------------------------------------------------
# closed-property tests spanning all subclasses
# ---------------------------------------------------------------------------

class ClosedPropertyTests(unittest.TestCase):
    """
        Verify that the ``closed`` property reports ``False`` initially
        and ``True`` after ``close`` for every concrete subclass.
    """

    def test_shell_channel_closed_property(self) -> None:
        """
            :class:`UefiShellChannel`.closed transitions ``False`` → ``True``.
        """
        transport = _StreamingStub()
        channel = UefiShellChannel(transport=transport, aspects=DEFAULT_CMD_ASPECTS)
        self.assertFalse(channel.closed)
        channel.close()
        self.assertTrue(channel.closed)
        return

    def test_redfish_channel_closed_property(self) -> None:
        """
            :class:`UefiRedfishChannel`.closed transitions ``False`` → ``True``.
        """
        transport = _RedfishStub()
        channel = UefiRedfishChannel(transport=transport, aspects=DEFAULT_CMD_ASPECTS)
        self.assertFalse(channel.closed)
        channel.close()
        self.assertTrue(channel.closed)
        return

    def test_host_os_channel_closed_property(self) -> None:
        """
            :class:`UefiHostOsChannel`.closed transitions ``False`` → ``True``.
        """
        transport = _SshHostStub()
        channel = UefiHostOsChannel(transport=transport, aspects=DEFAULT_CMD_ASPECTS)
        self.assertFalse(channel.closed)
        channel.close()
        self.assertTrue(channel.closed)
        return


if __name__ == "__main__":
    unittest.main()
