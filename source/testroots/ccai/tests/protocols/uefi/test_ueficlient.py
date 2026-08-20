"""
    .. module:: test_ueficlient
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Unit tests for the :class:`UefiClient` abstract base and the
               three concrete subclasses (:class:`UefiShellClient`,
               :class:`UefiRedfishClient`, :class:`UefiHostOsClient`).

               Because the peer :mod:`ccai.protocols.uefi.uefichannel`
               module may not yet be present when these tests are executed
               in isolation, the test module installs hand-rolled stub
               :class:`UefiChannel` subclasses into ``sys.modules`` before
               importing :mod:`ueficlient`.  The stubs record their inputs
               and return canned data so the client's framing behavior can
               be observed deterministically.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Code Craft AI LLC"
__credits__ = []


import os
import sys
import types
import unittest

from typing import List, Optional, Tuple

from ccai.errors.exceptions import AbstractMethodError, SemanticError

from ccai.protocols.uefi.uefiaspects import (
    ActionPattern,
    DEFAULT_CMD_ASPECTS,
    UefiCmdAspects,
)


# ---------------------------------------------------------------------------
# Install a stub uefichannel module into sys.modules so that the production
# ueficlient module imports cleanly even when the peer channel feature has
# not yet been authored.  The stub defines four channel classes that record
# their construction arguments and the sequence of send / recv_until calls.
# ---------------------------------------------------------------------------


class _RecordedChannel:
    """
        Base stub :class:`UefiChannel` used by the ueficlient tests.

        Records every ``send``, ``recv_until``, and ``close`` invocation
        so tests can assert exactly what the client dispatched.  Tests
        prime ``recv_until_responses`` with the bytes each successive
        ``recv_until`` call should return.  When the queue is exhausted
        the stub returns an empty :class:`bytes` object.
    """

    def __init__(self, transport: object, aspects: UefiCmdAspects) -> None:
        """
            Initialize the recorded stub channel.

            :param transport: The transport passed in by the client.
            :param aspects: The effective :class:`UefiCmdAspects`.
        """
        self.transport = transport
        self.aspects = aspects
        self.sent_payloads: List[bytes] = []
        self.recv_until_calls: List[Tuple[bytes, Optional[float]]] = []
        self.recv_until_responses: List[bytes] = []
        self.recv_until_exceptions: List[Optional[BaseException]] = []
        self.closed_count = 0
        return

    def send(self, data: bytes) -> int:
        """
            Record the sent payload.

            :param data: The bytes to record.

            :returns: The number of bytes recorded.
        """
        self.sent_payloads.append(bytes(data))
        rtnval = len(data)
        return rtnval

    def recv_until(self, pattern: bytes, timeout: Optional[float] = None) -> bytes:
        """
            Record the call and return the next primed response (or raise
            the next primed exception).

            :param pattern: The prompt pattern passed by the client.
            :param timeout: The timeout passed by the client.

            :returns: The next primed response bytes.
            :raises: Any exception primed via
                     ``recv_until_exceptions``.
        """
        self.recv_until_calls.append((bytes(pattern), timeout))

        if len(self.recv_until_exceptions) > 0:
            next_exc = self.recv_until_exceptions.pop(0)
            if next_exc is not None:
                raise next_exc

        if len(self.recv_until_responses) > 0:
            rtnval = self.recv_until_responses.pop(0)
        else:
            rtnval = b""

        return rtnval

    def close(self) -> None:
        """
            Record the close call.
        """
        self.closed_count += 1
        return


class _StubUefiChannel(_RecordedChannel):
    """
        Stand-in for :class:`UefiChannel`.
    """


class _StubUefiShellChannel(_RecordedChannel):
    """
        Stand-in for :class:`UefiShellChannel`.
    """


class _StubUefiRedfishChannel(_RecordedChannel):
    """
        Stand-in for :class:`UefiRedfishChannel`.
    """


class _StubUefiHostOsChannel(_RecordedChannel):
    """
        Stand-in for :class:`UefiHostOsChannel`.
    """


_UEFICHANNEL_MODULE_NAME = "ccai.protocols.uefi.uefichannel"


def _install_uefichannel_stub() -> types.ModuleType:
    """
        Install (or return the already-installed) stub
        :mod:`ccai.protocols.uefi.uefichannel` module.

        :returns: The stub module.
    """
    # Force-install even if the real uefichannel module is already loaded by
    # another test; ueficlient's bound names must point at our stubs for
    # these tests.  We capture the prior binding on the stub module so it
    # can be restored in tearDown if needed.
    stub_module = types.ModuleType(_UEFICHANNEL_MODULE_NAME)
    stub_module.UefiChannel = _StubUefiChannel
    stub_module.UefiShellChannel = _StubUefiShellChannel
    stub_module.UefiRedfishChannel = _StubUefiRedfishChannel
    stub_module.UefiHostOsChannel = _StubUefiHostOsChannel

    sys.modules[_UEFICHANNEL_MODULE_NAME] = stub_module

    return stub_module


_install_uefichannel_stub()


# ---------------------------------------------------------------------------
# Import the module under test AFTER the stub is installed so the
# ``from ccai.protocols.uefi.uefichannel import ...`` line in
# ueficlient.py resolves to our stubs.
# ---------------------------------------------------------------------------

from ccai.protocols.uefi import ueficlient  # noqa: E402

# Force-rebind the channel names on the ueficlient module to our stubs, in
# case the real uefichannel module was already imported (and thus bound
# into ueficlient at its module-load time) before the stub install above
# had a chance to swap it out.
ueficlient.UefiChannel = _StubUefiChannel
ueficlient.UefiShellChannel = _StubUefiShellChannel
ueficlient.UefiRedfishChannel = _StubUefiRedfishChannel
ueficlient.UefiHostOsChannel = _StubUefiHostOsChannel

from ccai.protocols.uefi.ueficlient import (  # noqa: E402
    SHELL_LINE_TERMINATOR,
    SHELL_STATUS_MARKER,
    SHELL_STATUS_PROBE,
    UefiClient,
    UefiHostOsClient,
    UefiRedfishClient,
    UefiShellClient,
)


# ---------------------------------------------------------------------------
# Transport stubs
# ---------------------------------------------------------------------------


class _StubShellTransport:
    """
        Hand-rolled :class:`IUefiTransport`-shaped stub for
        :class:`UefiShellClient` tests.  Records ``disconnect`` calls and
        reports ``connected`` via a toggleable flag.
    """

    def __init__(self) -> None:
        """
            Initialize the stub shell transport.
        """
        self.connected_flag = True
        self.disconnect_count = 0
        self.disconnect_exception: Optional[BaseException] = None
        return

    @property
    def connected(self) -> bool:
        rtnval = self.connected_flag
        return rtnval

    def connect(self, aspects: UefiCmdAspects) -> None:
        self.connected_flag = True
        return

    def disconnect(self) -> None:
        self.disconnect_count += 1
        if self.disconnect_exception is not None:
            raise self.disconnect_exception
        self.connected_flag = False
        return

    def send(self, data: bytes) -> int:
        rtnval = len(data)
        return rtnval

    def recv(self, max_bytes: int, timeout: float) -> bytes:
        rtnval = b""
        return rtnval


class _StubRedfishTransport:
    """
        Hand-rolled :class:`IUefiTransport`-shaped stub for
        :class:`UefiRedfishClient` tests.
    """

    def __init__(self) -> None:
        """
            Initialize the stub redfish transport.
        """
        self.connected_flag = True
        self.disconnect_count = 0
        return

    @property
    def connected(self) -> bool:
        rtnval = self.connected_flag
        return rtnval

    def connect(self, aspects: UefiCmdAspects) -> None:
        self.connected_flag = True
        return

    def disconnect(self) -> None:
        self.disconnect_count += 1
        self.connected_flag = False
        return

    def send(self, data: bytes) -> int:
        rtnval = len(data)
        return rtnval

    def recv(self, max_bytes: int, timeout: float) -> bytes:
        rtnval = b""
        return rtnval


class _StubHostOsTransport:
    """
        Hand-rolled :class:`IUefiTransport`-shaped stub for
        :class:`UefiHostOsClient` tests.  Records ``run_cmd`` calls and
        returns a canned three-tuple or raises a configured exception.
    """

    def __init__(self) -> None:
        """
            Initialize the stub ssh-host transport.
        """
        self.connected_flag = True
        self.disconnect_count = 0
        self.run_cmd_calls: List[Tuple[str, UefiCmdAspects]] = []
        self.run_cmd_response: Tuple[int, str, str] = (0, "ok", "")
        self.run_cmd_exception: Optional[BaseException] = None
        return

    @property
    def connected(self) -> bool:
        rtnval = self.connected_flag
        return rtnval

    def connect(self, aspects: UefiCmdAspects) -> None:
        self.connected_flag = True
        return

    def disconnect(self) -> None:
        self.disconnect_count += 1
        self.connected_flag = False
        return

    def send(self, data: bytes) -> int:
        rtnval = len(data)
        return rtnval

    def recv(self, max_bytes: int, timeout: float) -> bytes:
        rtnval = b""
        return rtnval

    def run_cmd(self, command: str,
                      aspects: Optional[UefiCmdAspects] = None) -> Tuple[int, str, str]:
        self.run_cmd_calls.append((command, aspects))
        if self.run_cmd_exception is not None:
            raise self.run_cmd_exception
        rtnval = self.run_cmd_response
        return rtnval


# ---------------------------------------------------------------------------
# A deliberately-misconfigured concrete UefiClient whose
# ``supported_action_patterns`` excludes a known member.  Used to exercise
# ``_validate_action_pattern``'s SemanticError branch.
# ---------------------------------------------------------------------------


class _NarrowActionPatternClient(UefiClient):
    """
        Minimal concrete :class:`UefiClient` that supports only a single
        action pattern.  Used to assert the protected
        :meth:`UefiClient._validate_action_pattern` helper raises on
        unsupported patterns.
    """

    supported_action_patterns: Tuple[ActionPattern, ...] = (
        ActionPattern.SINGLE_CALL,
    )

    def open_channel(self, aspects: Optional[UefiCmdAspects] = None):
        """
            Return a recording stub channel so the tests can inspect it.
        """
        effective = self._resolve_aspects(aspects=aspects)
        channel = _StubUefiChannel(transport=self._transport, aspects=effective)
        return channel

    def execute(self, command: str,
                      aspects: Optional[UefiCmdAspects] = None) -> Tuple[int, str, str]:
        """
            Validate the action pattern and return a canned three-tuple.
        """
        effective = self._resolve_aspects(aspects=aspects)
        self._validate_action_pattern(aspects=effective)
        rtnval = (0, "", "")
        return rtnval


# ---------------------------------------------------------------------------
# UefiClient — abstract base tests
# ---------------------------------------------------------------------------


class UefiClientAbstractTestCase(unittest.TestCase):
    """
        Verify that the abstract :class:`UefiClient` methods raise
        :class:`AbstractMethodError` when invoked directly on the base
        class.
    """

    def test_open_channel_raises_abstract(self) -> None:
        """
            :meth:`UefiClient.open_channel` must raise
            :class:`AbstractMethodError` with the subclass name included.
        """
        transport = _StubShellTransport()
        client = UefiClient(transport=transport, aspects=DEFAULT_CMD_ASPECTS)

        with self.assertRaises(AbstractMethodError) as ctx:
            client.open_channel()

        rendered = str(ctx.exception)
        self.assertIn("UefiClient", rendered)

        return

    def test_execute_raises_abstract(self) -> None:
        """
            :meth:`UefiClient.execute` must raise
            :class:`AbstractMethodError`.
        """
        transport = _StubShellTransport()
        client = UefiClient(transport=transport, aspects=DEFAULT_CMD_ASPECTS)

        with self.assertRaises(AbstractMethodError) as ctx:
            client.execute(command="ver")

        rendered = str(ctx.exception)
        self.assertIn("UefiClient", rendered)

        return

    def test_connected_property_delegates_to_transport(self) -> None:
        """
            The ``connected`` property MUST delegate to the transport.
        """
        transport = _StubShellTransport()
        client = UefiClient(transport=transport, aspects=DEFAULT_CMD_ASPECTS)

        transport.connected_flag = True
        self.assertTrue(client.connected)

        transport.connected_flag = False
        self.assertFalse(client.connected)

        return

    def test_close_is_idempotent_and_never_raises(self) -> None:
        """
            :meth:`UefiClient.close` must be idempotent and must NEVER
            raise, even when the transport's disconnect raises.
        """
        transport = _StubShellTransport()
        transport.disconnect_exception = RuntimeError("disconnect boom")

        client = UefiClient(transport=transport, aspects=DEFAULT_CMD_ASPECTS)

        # First close: must not raise despite transport failure.
        client.close()
        self.assertTrue(client.closed)
        self.assertEqual(transport.disconnect_count, 1)

        # Second close: must be a no-op; disconnect count does not increase.
        client.close()
        self.assertEqual(transport.disconnect_count, 1)

        return


# ---------------------------------------------------------------------------
# UefiClient — _validate_action_pattern tests
# ---------------------------------------------------------------------------


class UefiClientValidateActionPatternTestCase(unittest.TestCase):
    """
        Exercise the protected
        :meth:`UefiClient._validate_action_pattern` helper via a
        narrowly-configured concrete subclass.
    """

    def test_supported_pattern_is_accepted(self) -> None:
        """
            A pattern present in ``supported_action_patterns`` must NOT
            raise.
        """
        transport = _StubShellTransport()
        client = _NarrowActionPatternClient(transport=transport)

        aspects = UefiCmdAspects(action_pattern=ActionPattern.SINGLE_CALL)

        # Should not raise.
        client._validate_action_pattern(aspects=aspects)

        return

    def test_unsupported_pattern_raises_semantic_error(self) -> None:
        """
            A pattern NOT in ``supported_action_patterns`` must raise
            :class:`SemanticError` with the subclass, pattern, and
            supported-list all present in the message.
        """
        transport = _StubShellTransport()
        client = _NarrowActionPatternClient(transport=transport)

        unsupported_patterns = [
            ActionPattern.SINGLE_CONNECTED_CALL,
            ActionPattern.DO_UNTIL_SUCCESS,
            ActionPattern.DO_WHILE_SUCCESS,
            ActionPattern.DO_UNTIL_CONNECTION_FAILURE,
        ]

        for pattern in unsupported_patterns:
            aspects = UefiCmdAspects(action_pattern=pattern)

            with self.assertRaises(SemanticError) as ctx:
                client._validate_action_pattern(aspects=aspects)

            rendered = str(ctx.exception)
            self.assertIn("_NarrowActionPatternClient", rendered)
            self.assertIn(pattern.name, rendered)
            self.assertIn("SINGLE_CALL", rendered)

        return

    def test_execute_validates_action_pattern_before_dispatch(self) -> None:
        """
            :meth:`UefiClient.execute` implementations MUST validate the
            action pattern BEFORE dispatching any work.
        """
        transport = _StubShellTransport()
        client = _NarrowActionPatternClient(transport=transport)

        bad_aspects = UefiCmdAspects(action_pattern=ActionPattern.DO_UNTIL_SUCCESS)

        with self.assertRaises(SemanticError):
            client.execute(command="ver", aspects=bad_aspects)

        return


# ---------------------------------------------------------------------------
# UefiShellClient tests
# ---------------------------------------------------------------------------


class UefiShellClientTestCase(unittest.TestCase):
    """
        Tests for the :class:`UefiShellClient` concrete subclass.
    """

    def test_supported_action_patterns_match_spec(self) -> None:
        """
            :class:`UefiShellClient` must declare exactly the four
            patterns required by the task specification.
        """
        expected = (
            ActionPattern.SINGLE_CALL,
            ActionPattern.SINGLE_CONNECTED_CALL,
            ActionPattern.DO_UNTIL_SUCCESS,
            ActionPattern.DO_WHILE_SUCCESS,
        )

        self.assertEqual(UefiShellClient.supported_action_patterns, expected)

        return

    def test_open_channel_returns_shell_channel(self) -> None:
        """
            :meth:`UefiShellClient.open_channel` must return a
            :class:`UefiShellChannel` stand-in bound to the wrapped
            transport.
        """
        transport = _StubShellTransport()
        client = UefiShellClient(transport=transport, aspects=DEFAULT_CMD_ASPECTS)

        channel = client.open_channel()

        self.assertIsInstance(channel, _StubUefiShellChannel)
        self.assertIs(channel.transport, transport)

        return

    def test_execute_dispatches_command_reads_until_prompt_and_parses_status(self) -> None:
        """
            :meth:`UefiShellClient.execute` must:
            1. open a :class:`UefiShellChannel`,
            2. send ``command + \\r\\n``,
            3. read until the configured prompt,
            4. issue the ``EXIT_STATUS`` probe,
            5. read until the prompt again,
            6. parse the integer exit status,
            7. close the channel,
            8. return the ``(status, stdout, stderr)`` three-tuple.
        """
        transport = _StubShellTransport()

        # Track the channels handed out by the stub module.
        created_channels: List[_StubUefiShellChannel] = []

        original_cls = sys.modules[_UEFICHANNEL_MODULE_NAME].UefiShellChannel

        def factory(transport, aspects):
            ch = original_cls(transport=transport, aspects=aspects)
            created_channels.append(ch)

            # Prime the channel with a plausible captured capture.
            ch.recv_until_responses = [
                b"ver\r\nUEFI Shell version 2.2\r\nFS0:\\> ",
                b"echo EXIT_STATUS:%lasterror%\r\nEXIT_STATUS:0x0\r\nFS0:\\> ",
            ]
            return ch

        # Monkey-patch the module-level UefiShellChannel used by the
        # production client so we can observe the channel.
        sys.modules[_UEFICHANNEL_MODULE_NAME].UefiShellChannel = factory
        ueficlient.UefiShellChannel = factory
        try:
            client = UefiShellClient(transport=transport, aspects=DEFAULT_CMD_ASPECTS)
            status, stdout, stderr = client.execute(command="ver")
        finally:
            sys.modules[_UEFICHANNEL_MODULE_NAME].UefiShellChannel = original_cls
            ueficlient.UefiShellChannel = original_cls

        self.assertEqual(len(created_channels), 1)
        channel = created_channels[0]

        # Command + probe were each sent.
        self.assertEqual(len(channel.sent_payloads), 2)

        expected_command_bytes = ("ver" + SHELL_LINE_TERMINATOR).encode(
            DEFAULT_CMD_ASPECTS.response_encoding
        )
        self.assertEqual(channel.sent_payloads[0], expected_command_bytes)

        expected_probe_bytes = (SHELL_STATUS_PROBE + SHELL_LINE_TERMINATOR).encode(
            DEFAULT_CMD_ASPECTS.response_encoding
        )
        self.assertEqual(channel.sent_payloads[1], expected_probe_bytes)

        # recv_until was called twice.
        self.assertEqual(len(channel.recv_until_calls), 2)

        # Channel was closed afterward.
        self.assertEqual(channel.closed_count, 1)

        # Status parsed correctly.
        self.assertEqual(status, 0)
        self.assertEqual(stderr, "")
        # Stdout contains the shell version echo.
        self.assertIn("UEFI Shell", stdout)

        return

    def test_execute_parses_nonzero_hex_exit_status(self) -> None:
        """
            A non-zero hex ``%lasterror%`` must be parsed into the
            corresponding integer.
        """
        transport = _StubShellTransport()

        original_cls = sys.modules[_UEFICHANNEL_MODULE_NAME].UefiShellChannel
        captured: List[_StubUefiShellChannel] = []

        def factory(transport, aspects):
            ch = original_cls(transport=transport, aspects=aspects)
            ch.recv_until_responses = [
                b"fs0:\\> bogus\r\n'bogus' is not recognized\r\nFS0:\\> ",
                b"echo EXIT_STATUS:%lasterror%\r\nEXIT_STATUS:0xF\r\nFS0:\\> ",
            ]
            captured.append(ch)
            return ch

        sys.modules[_UEFICHANNEL_MODULE_NAME].UefiShellChannel = factory
        ueficlient.UefiShellChannel = factory
        try:
            client = UefiShellClient(transport=transport, aspects=DEFAULT_CMD_ASPECTS)
            status, _, _ = client.execute(command="bogus")
        finally:
            sys.modules[_UEFICHANNEL_MODULE_NAME].UefiShellChannel = original_cls
            ueficlient.UefiShellChannel = original_cls

        self.assertEqual(status, 15)

        return

    def test_execute_raises_connection_error_on_mid_read_loss(self) -> None:
        """
            A :class:`ConnectionError` raised from the transport during
            ``recv_until`` must propagate out of :meth:`execute`; the
            channel must still be closed.
        """
        transport = _StubShellTransport()

        original_cls = sys.modules[_UEFICHANNEL_MODULE_NAME].UefiShellChannel
        captured: List[_StubUefiShellChannel] = []

        def factory(transport, aspects):
            ch = original_cls(transport=transport, aspects=aspects)
            ch.recv_until_exceptions = [ConnectionError("transport dropped")]
            captured.append(ch)
            return ch

        sys.modules[_UEFICHANNEL_MODULE_NAME].UefiShellChannel = factory
        ueficlient.UefiShellChannel = factory
        try:
            client = UefiShellClient(transport=transport, aspects=DEFAULT_CMD_ASPECTS)
            with self.assertRaises(ConnectionError):
                client.execute(command="ver")
        finally:
            sys.modules[_UEFICHANNEL_MODULE_NAME].UefiShellChannel = original_cls
            ueficlient.UefiShellChannel = original_cls

        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0].closed_count, 1)

        return

    def test_execute_rejects_unsupported_action_pattern(self) -> None:
        """
            Only the four declared action patterns are accepted; any
            other member raises :class:`SemanticError`.
        """
        transport = _StubShellTransport()
        client = UefiShellClient(transport=transport, aspects=DEFAULT_CMD_ASPECTS)

        bad_aspects = UefiCmdAspects(
            action_pattern=ActionPattern.DO_UNTIL_CONNECTION_FAILURE,
        )

        with self.assertRaises(SemanticError):
            client.execute(command="ver", aspects=bad_aspects)

        return


# ---------------------------------------------------------------------------
# UefiRedfishClient tests
# ---------------------------------------------------------------------------


class UefiRedfishClientTestCase(unittest.TestCase):
    """
        Tests for the :class:`UefiRedfishClient` concrete subclass.
    """

    def test_supported_action_patterns_match_spec(self) -> None:
        """
            :class:`UefiRedfishClient` must declare exactly the two
            patterns required by the task specification.
        """
        expected = (
            ActionPattern.SINGLE_CALL,
            ActionPattern.DO_UNTIL_SUCCESS,
        )

        self.assertEqual(UefiRedfishClient.supported_action_patterns, expected)

        return

    def test_open_channel_returns_redfish_channel(self) -> None:
        """
            :meth:`UefiRedfishClient.open_channel` must return a
            :class:`UefiRedfishChannel` stand-in bound to the wrapped
            transport.
        """
        transport = _StubRedfishTransport()
        client = UefiRedfishClient(transport=transport, aspects=DEFAULT_CMD_ASPECTS)

        channel = client.open_channel()

        self.assertIsInstance(channel, _StubUefiRedfishChannel)
        self.assertIs(channel.transport, transport)

        return

    def test_execute_always_raises_semantic_error(self) -> None:
        """
            :meth:`UefiRedfishClient.execute` must raise
            :class:`SemanticError` — Redfish is not command-oriented.
        """
        transport = _StubRedfishTransport()
        client = UefiRedfishClient(transport=transport, aspects=DEFAULT_CMD_ASPECTS)

        with self.assertRaises(SemanticError) as ctx:
            client.execute(command="GET /redfish/v1/")

        rendered = str(ctx.exception)
        self.assertIn("UefiRedfishClient", rendered)

        return


# ---------------------------------------------------------------------------
# UefiHostOsClient tests
# ---------------------------------------------------------------------------


class UefiHostOsClientTestCase(unittest.TestCase):
    """
        Tests for the :class:`UefiHostOsClient` concrete subclass.
    """

    def test_supported_action_patterns_match_spec(self) -> None:
        """
            :class:`UefiHostOsClient` must declare exactly the three
            patterns required by the task specification.
        """
        expected = (
            ActionPattern.SINGLE_CALL,
            ActionPattern.DO_UNTIL_SUCCESS,
            ActionPattern.DO_WHILE_SUCCESS,
        )

        self.assertEqual(UefiHostOsClient.supported_action_patterns, expected)

        return

    def test_open_channel_returns_host_os_channel(self) -> None:
        """
            :meth:`UefiHostOsClient.open_channel` must return a
            :class:`UefiHostOsChannel` stand-in bound to the wrapped
            transport.
        """
        transport = _StubHostOsTransport()
        client = UefiHostOsClient(transport=transport, aspects=DEFAULT_CMD_ASPECTS)

        channel = client.open_channel()

        self.assertIsInstance(channel, _StubUefiHostOsChannel)
        self.assertIs(channel.transport, transport)

        return

    def test_execute_forwards_to_transport_run_cmd(self) -> None:
        """
            :meth:`UefiHostOsClient.execute` must forward the command and
            effective aspects to :meth:`transport.run_cmd` and return
            the resulting three-tuple unchanged.
        """
        transport = _StubHostOsTransport()
        transport.run_cmd_response = (0, "ok", "")

        client = UefiHostOsClient(transport=transport, aspects=DEFAULT_CMD_ASPECTS)
        status, stdout, stderr = client.execute(command="uname -a")

        self.assertEqual(status, 0)
        self.assertEqual(stdout, "ok")
        self.assertEqual(stderr, "")

        self.assertEqual(len(transport.run_cmd_calls), 1)
        forwarded_command, forwarded_aspects = transport.run_cmd_calls[0]
        self.assertEqual(forwarded_command, "uname -a")
        self.assertIs(forwarded_aspects, DEFAULT_CMD_ASPECTS)

        return

    def test_execute_forwards_per_call_aspects(self) -> None:
        """
            A non-None per-call aspects MUST be forwarded to
            :meth:`transport.run_cmd` rather than the instance default.
        """
        transport = _StubHostOsTransport()
        instance_default = DEFAULT_CMD_ASPECTS

        client = UefiHostOsClient(transport=transport, aspects=instance_default)

        per_call = UefiCmdAspects(action_pattern=ActionPattern.DO_UNTIL_SUCCESS)
        client.execute(command="true", aspects=per_call)

        self.assertEqual(len(transport.run_cmd_calls), 1)
        _, forwarded_aspects = transport.run_cmd_calls[0]
        self.assertIs(forwarded_aspects, per_call)

        return

    def test_execute_rejects_unsupported_action_pattern(self) -> None:
        """
            Patterns outside the declared three must raise
            :class:`SemanticError`.
        """
        transport = _StubHostOsTransport()
        client = UefiHostOsClient(transport=transport, aspects=DEFAULT_CMD_ASPECTS)

        bad_aspects = UefiCmdAspects(
            action_pattern=ActionPattern.SINGLE_CONNECTED_CALL,
        )

        with self.assertRaises(SemanticError):
            client.execute(command="true", aspects=bad_aspects)

        return


# ---------------------------------------------------------------------------
# Shared-surface tests (aspects passthrough, close delegation)
# ---------------------------------------------------------------------------


class UefiClientSharedSurfaceTestCase(unittest.TestCase):
    """
        Tests for shared surface behavior across every concrete subclass.
    """

    def test_close_delegates_to_transport_disconnect(self) -> None:
        """
            :meth:`close` must call :meth:`transport.disconnect` exactly
            once, on the first invocation.
        """
        transport = _StubHostOsTransport()
        client = UefiHostOsClient(transport=transport, aspects=DEFAULT_CMD_ASPECTS)

        self.assertEqual(transport.disconnect_count, 0)

        client.close()
        self.assertEqual(transport.disconnect_count, 1)
        self.assertTrue(client.closed)

        # Idempotent.
        client.close()
        self.assertEqual(transport.disconnect_count, 1)

        return


if __name__ == "__main__":
    unittest.main()
