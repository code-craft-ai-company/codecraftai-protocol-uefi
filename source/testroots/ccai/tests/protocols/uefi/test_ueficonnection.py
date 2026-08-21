"""
    .. module:: test_ueficonnection
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Unit tests for :class:`UefiConnection`.  A hand-rolled
               :class:`_StubTransport` satisfies :class:`IUefiTransport`
               structurally and records every connect / disconnect
               invocation so the tests can assert the wrapper's
               lifecycle, aspects passthrough, ``reconnect_count``
               accounting, context-manager behavior, and structured log
               emission on the ``uefi.transport`` channel.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Code Craft AI LLC"
__credits__ = []



import logging
import contextlib
from ccai import testbase

from typing import List, Optional

from ccai.errors.exceptions import ConfigurationError

from ccai.protocols.uefi.uefiaspects import (
    DEFAULT_CMD_ASPECTS,
    UefiCmdAspects,
)
from ccai.protocols.uefi.uefiexceptions import UefiTransportError
from ccai.protocols.uefi.uefiinterfaces import IUefiTransport

from ccai.protocols.uefi.ueficonnection import (
    EVENT_CONNECTION_CLOSE,
    EVENT_CONNECTION_OPEN,
    EVENT_CONNECTION_RECONNECT,
    UefiConnection,
)


# ---------------------------------------------------------------------------
# Stub transport
# ---------------------------------------------------------------------------

class _CapturedLogs:
    """
        Holds the records collected by :func:`_capture_logs`.

        :ivar output: Formatted "LEVEL:name:message" strings, the same
                      shape unittest's assertLogs produced, so the
                      assertions that consume it are unchanged.
    """

    def __init__(self) -> None:
        self.records = []
        self.output = []
        return


@contextlib.contextmanager
def _capture_logs(logger: str, level: int):
    """
        Capture records emitted to a logger, replacing assertLogs.

        testbase has no assertLogs equivalent, so this reproduces the
        part of it these tests rely on: the records emitted inside the
        block, exposed as 'output' once the block exits.

        :param logger: Name of the logger to capture.
        :param level: Minimum level to record.

        :returns: A :class:`_CapturedLogs` populated on exit.
    """
    target = logging.getLogger(logger)
    captured = _CapturedLogs()

    class _Collector(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.records.append(record)
            return

    handler = _Collector(level=level)
    prior_level = target.level
    target.addHandler(handler)
    target.setLevel(level)

    try:
        yield captured
    finally:
        target.removeHandler(handler)
        target.setLevel(prior_level)
        captured.output = [
            "{}:{}:{}".format(rec.levelname, rec.name, rec.getMessage())
            for rec in captured.records
        ]

class _StubTransport:
    """
        Hand-rolled stub that structurally satisfies
        :class:`IUefiTransport`.  Records every ``connect`` and
        ``disconnect`` invocation so tests can assert call order, call
        count, and aspects propagation.  The ``connect_fail_times``
        counter simulates transient connect failures: each call to
        :meth:`connect` decrements the counter and raises
        :class:`UefiTransportError` until the counter reaches zero, then
        succeeds.
    """

    transport_kind: str = "serial"

    def __init__(self, host: str = "stub-host",
                       connect_fail_times: int = 0) -> None:
        """
            Initialize stub state.

            :param host: Value to expose from the ``host`` attribute.
            :param connect_fail_times: Number of leading
                                       :meth:`connect` calls that will
                                       raise before the first success.
        """
        self.host: str = host
        self._connected: bool = False
        self.connect_call_count: int = 0
        self.disconnect_call_count: int = 0
        self.connect_fail_times: int = connect_fail_times
        self.connect_aspects_seen: List[Optional[UefiCmdAspects]] = []
        self.raise_on_disconnect: Optional[BaseException] = None
        return

    @property
    def connected(self) -> bool:
        """
            :returns: The current stub connection flag.
        """
        rtnval = self._connected
        return rtnval

    def connect(self, aspects: UefiCmdAspects) -> None:
        """
            Record the invocation; fail the first ``connect_fail_times``
            calls then succeed on subsequent calls.

            :param aspects: The effective :class:`UefiCmdAspects`.
        """
        self.connect_call_count += 1
        self.connect_aspects_seen.append(aspects)

        if self.connect_fail_times > 0:
            self.connect_fail_times -= 1
            err_msg = "stub transport simulated transient connect failure"
            raise UefiTransportError(err_msg) from None

        self._connected = True
        return

    def disconnect(self) -> None:
        """
            Record the invocation; optionally re-raise a pre-armed
            exception for tests that need to verify error reclassification.
        """
        self.disconnect_call_count += 1

        if self.raise_on_disconnect is not None:
            arm = self.raise_on_disconnect
            self.raise_on_disconnect = None
            raise arm

        self._connected = False
        return

    def send(self, data: bytes) -> int:
        """
            Not exercised by these tests; present so the stub satisfies
            :class:`IUefiTransport` structurally.
        """
        rtnval = len(data)
        return rtnval

    def recv(self, max_bytes: int, timeout: float) -> bytes:
        """
            Not exercised by these tests; present so the stub satisfies
            :class:`IUefiTransport` structurally.
        """
        rtnval = b""
        return rtnval


# ---------------------------------------------------------------------------
# Aspects builder
# ---------------------------------------------------------------------------

def _build_aspects(connection_timeout: float = 1.0,
                   connection_interval: float = 0.05) -> UefiCmdAspects:
    """
        Produce a lightweight :class:`UefiCmdAspects` suitable for tests.

        :param connection_timeout: Connection-retry budget.
        :param connection_interval: Sleep between retry attempts.

        :returns: A frozen :class:`UefiCmdAspects` instance.
    """
    rtnval = UefiCmdAspects(
        connection_timeout=connection_timeout,
        connection_interval=connection_interval,
    )
    return rtnval


# ---------------------------------------------------------------------------
# Construction and validation
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# ConstructionTests
# ----------------------------------------------------------------------

"""
    Construction-time validation: non-``IUefiTransport`` arguments must
    raise :class:`ConfigurationError` with a descriptive message.
"""

def test_reconnect_count_starts_at_zero() -> None:
    """
        A freshly-constructed :class:`UefiConnection` has
        ``reconnect_count == 0``.
    """
    stub = _StubTransport()
    conn = UefiConnection(transport=stub, aspects=_build_aspects())

    testbase.assert_equal(conn.reconnect_count, 0)
    return

def test_non_transport_argument_raises_configuration_error() -> None:
    """
        Passing any object that does not structurally satisfy
        :class:`IUefiTransport` raises :class:`ConfigurationError`.
    """
    # A plain object() is missing every protocol method / property.
    with testbase.raises(ConfigurationError):
        UefiConnection(transport=object(), aspects=_build_aspects())

    # A dict is also not a transport.
    with testbase.raises(ConfigurationError):
        UefiConnection(transport={"not": "a transport"}, aspects=_build_aspects())
    return

def test_non_cmd_aspects_raises_configuration_error() -> None:
    """
        Passing an aspects object that is not a
        :class:`UefiCmdAspects` instance raises
        :class:`ConfigurationError`.
    """
    stub = _StubTransport()
    with testbase.raises(ConfigurationError):
        UefiConnection(transport=stub, aspects="not-aspects")  # type: ignore[arg-type]
    return

def test_default_aspects_used_when_not_passed() -> None:
    """
        When no aspects argument is supplied the connection uses the
        module-level :data:`DEFAULT_CMD_ASPECTS` default.
    """
    stub = _StubTransport()
    conn = UefiConnection(transport=stub)

    testbase.assert_true(conn.aspects is DEFAULT_CMD_ASPECTS)
    return


# ---------------------------------------------------------------------------
# Happy-path lifecycle
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# LifecycleTests
# ----------------------------------------------------------------------

"""
    Happy-path lifecycle: connect, disconnect, and read-only property
    semantics.
"""

def test_connect_then_disconnect_calls_delegate_exactly_once() -> None:
    """
        ``connect`` then ``disconnect`` delegate to the underlying
        transport exactly once each.
    """
    stub = _StubTransport()
    conn = UefiConnection(transport=stub, aspects=_build_aspects())

    conn.connect()
    testbase.assert_equal(stub.connect_call_count, 1)
    testbase.assert_true(conn.connected)

    conn.disconnect()
    testbase.assert_equal(stub.disconnect_call_count, 1)
    testbase.assert_false(conn.connected)
    return

def test_connected_property_reflects_transport_state() -> None:
    """
        The :attr:`connected` property returns the underlying
        transport's own ``connected`` flag.
    """
    stub = _StubTransport()
    conn = UefiConnection(transport=stub, aspects=_build_aspects())

    testbase.assert_false(conn.connected)

    conn.connect()
    testbase.assert_true(conn.connected)

    conn.disconnect()
    testbase.assert_false(conn.connected)
    return

def test_connected_property_is_read_only() -> None:
    """
        Assignment to :attr:`connected` raises :class:`AttributeError`.
    """
    stub = _StubTransport()
    conn = UefiConnection(transport=stub, aspects=_build_aspects())

    with testbase.raises(AttributeError):
        conn.connected = True  # type: ignore[misc]
    return

def test_reconnect_count_is_read_only() -> None:
    """
        Assignment to :attr:`reconnect_count` raises
        :class:`AttributeError`.
    """
    stub = _StubTransport()
    conn = UefiConnection(transport=stub, aspects=_build_aspects())

    with testbase.raises(AttributeError):
        conn.reconnect_count = 99  # type: ignore[misc]
    return

def test_transport_kind_reflects_underlying_transport() -> None:
    """
        The :attr:`transport_kind` property is drawn from the
        underlying transport.
    """
    stub = _StubTransport()
    conn = UefiConnection(transport=stub, aspects=_build_aspects())

    testbase.assert_equal(conn.transport_kind, "serial")
    return


# ---------------------------------------------------------------------------
# Reconnect semantics
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# ReconnectTests
# ----------------------------------------------------------------------

"""
    Reconnect increments the reconnect counter, delegates
    disconnect-then-connect, and preserves aspects.
"""

def test_reconnect_disconnects_then_connects_and_increments_counter() -> None:
    """
        ``reconnect()`` on an already-connected transport calls
        ``disconnect`` then ``connect`` exactly once each and
        increments :attr:`reconnect_count` by one.
    """
    stub = _StubTransport()
    conn = UefiConnection(transport=stub, aspects=_build_aspects())

    conn.connect()
    baseline_connect_calls = stub.connect_call_count
    baseline_disconnect_calls = stub.disconnect_call_count

    conn.reconnect()

    testbase.assert_equal(stub.connect_call_count, baseline_connect_calls + 1)
    testbase.assert_equal(stub.disconnect_call_count, baseline_disconnect_calls + 1)
    testbase.assert_equal(conn.reconnect_count, 1)
    testbase.assert_true(conn.connected)
    return

def test_reconnect_when_disconnected_skips_disconnect() -> None:
    """
        ``reconnect()`` on a disconnected transport does not call
        ``disconnect`` (there is nothing to tear down) and still
        increments :attr:`reconnect_count` on success.
    """
    stub = _StubTransport()
    conn = UefiConnection(transport=stub, aspects=_build_aspects())

    conn.reconnect()

    testbase.assert_equal(stub.disconnect_call_count, 0)
    testbase.assert_equal(stub.connect_call_count, 1)
    testbase.assert_equal(conn.reconnect_count, 1)
    return

def test_reconnect_preserves_passed_aspects() -> None:
    """
        ``reconnect(aspects=X)`` uses ``X`` for the delegated
        connect; the instance-default aspects are not mutated.
    """
    instance_default = _build_aspects(connection_timeout=1.0, connection_interval=0.05)
    override = _build_aspects(connection_timeout=5.0, connection_interval=0.25)

    stub = _StubTransport()
    conn = UefiConnection(transport=stub, aspects=instance_default)

    conn.reconnect(aspects=override)

    # The most recent aspects seen by the stub's connect() must be the override.
    testbase.assert_true(conn.aspects is instance_default)
    testbase.assert_true(len(stub.connect_aspects_seen) >= 1)
    testbase.assert_true(stub.connect_aspects_seen[-1] is override)
    return

def test_reconnect_none_aspects_uses_instance_default() -> None:
    """
        ``reconnect(aspects=None)`` resolves to the instance-default
        aspects; the default is forwarded unchanged to the underlying
        transport.
    """
    instance_default = _build_aspects()
    stub = _StubTransport()
    conn = UefiConnection(transport=stub, aspects=instance_default)

    conn.reconnect(aspects=None)

    testbase.assert_true(stub.connect_aspects_seen[-1] is instance_default)
    return


# ---------------------------------------------------------------------------
# Structured log events
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# StructuredLogTests
# ----------------------------------------------------------------------

"""
    Connect / disconnect / reconnect each emit an INFO-level event on
    the ``uefi.transport`` channel carrying the expected event name.
"""

def test_connect_emits_connection_open_event() -> None:
    """
        A successful :meth:`connect` emits an event whose message
        contains ``event=connection.open``.
    """
    stub = _StubTransport()
    conn = UefiConnection(transport=stub, aspects=_build_aspects())

    with _capture_logs(logger="uefi.transport", level=logging.INFO) as captured:
        conn.connect()

    formatted = "\n".join(captured.output)
    testbase.assert_true("event={}".format(EVENT_CONNECTION_OPEN) in formatted)
    return

def test_disconnect_emits_connection_close_event() -> None:
    """
        A successful :meth:`disconnect` emits an event whose message
        contains ``event=connection.close``.
    """
    stub = _StubTransport()
    conn = UefiConnection(transport=stub, aspects=_build_aspects())
    conn.connect()

    with _capture_logs(logger="uefi.transport", level=logging.INFO) as captured:
        conn.disconnect()

    formatted = "\n".join(captured.output)
    testbase.assert_true("event={}".format(EVENT_CONNECTION_CLOSE) in formatted)
    return

def test_reconnect_emits_all_three_events() -> None:
    """
        A successful :meth:`reconnect` emits all three event names:
        ``connection.close``, ``connection.open``, and
        ``connection.reconnect``.
    """
    stub = _StubTransport()
    conn = UefiConnection(transport=stub, aspects=_build_aspects())
    conn.connect()

    with _capture_logs(logger="uefi.transport", level=logging.INFO) as captured:
        conn.reconnect()

    formatted = "\n".join(captured.output)
    testbase.assert_true("event={}".format(EVENT_CONNECTION_CLOSE) in formatted)
    testbase.assert_true("event={}".format(EVENT_CONNECTION_OPEN) in formatted)
    testbase.assert_true("event={}".format(EVENT_CONNECTION_RECONNECT) in formatted)
    testbase.assert_true("reconnect_count=1" in formatted)
    return


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# ContextManagerTests
# ----------------------------------------------------------------------

"""
    ``UefiConnection`` supports the ``with`` protocol; ``__enter__``
    calls ``connect`` and ``__exit__`` calls ``disconnect`` even when
    the ``with`` body raises.
"""

def test_with_block_connects_then_disconnects() -> None:
    """
        Entering a ``with`` block connects; exiting cleanly
        disconnects.
    """
    stub = _StubTransport()
    conn = UefiConnection(transport=stub, aspects=_build_aspects())

    with conn as bound:
        testbase.assert_true(bound is conn)
        testbase.assert_true(conn.connected)
        testbase.assert_equal(stub.connect_call_count, 1)

    testbase.assert_false(conn.connected)
    testbase.assert_equal(stub.disconnect_call_count, 1)
    return

def test_with_block_disconnects_after_exception() -> None:
    """
        An exception raised inside a ``with`` block still triggers
        ``disconnect`` on exit and does not suppress the exception.
    """
    stub = _StubTransport()
    conn = UefiConnection(transport=stub, aspects=_build_aspects())

    class _Boom(RuntimeError):
        """Sentinel exception for this test."""

    with testbase.raises(_Boom):
        with conn:
            testbase.assert_true(conn.connected)
            raise _Boom("body exploded")

    testbase.assert_false(conn.connected)
    testbase.assert_equal(stub.disconnect_call_count, 1)
    return
