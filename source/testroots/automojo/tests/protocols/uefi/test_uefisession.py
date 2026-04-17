"""
    .. module:: test_uefisession
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Unit tests for the :class:`UefiSession` persistent-channel
               wrapper.  Because the peer :mod:`uefibase` module is still
               being authored in parallel, this test module installs a
               stub ``uefibase`` module (with a minimal
               :class:`UefiBase` and a ``default_client_factory``) into
               :mod:`sys.modules` *before* importing the module under
               test.  The stubs record every inbound call and return
               canned data so the session's lifecycle, dispatch, and
               reconnect behavior can be observed deterministically and
               independently of the real base class.

               The tests exercise:

               * ``__enter__`` / ``__exit__`` opens and closes the
                 persistent client and channel exactly once.
               * ``run_cmd`` reuses a single client across many calls
                 (persistent-channel guarantee).
               * ``record_action`` + ``reboot`` replays the registered
                 actions after the reconnect loop succeeds.
               * ``basis_session`` cloning inherits host / transport
                 kind / credential from the upstream session and reuses
                 its :class:`UefiClient`.
               * :data:`ActionPattern.DO_UNTIL_CONNECTION_FAILURE`
                 surfaces :class:`ConnectionError` to the caller as the
                 intended exit signal.
               * :data:`ActionPattern.SINGLE_CONNECTED_CALL` attempts
                 exactly one reconnect on :class:`ConnectionError` and
                 then re-raises.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Automation Mojo LLC"
__credits__ = []


import sys
import types
import unittest

from typing import Any, Dict, List, Optional, Sequence, Tuple, Union
from unittest.mock import patch

from automojo.errors.exceptions import SemanticError

from automojo.protocols.uefi.uefiaspects import (
    ActionPattern,
    DEFAULT_CMD_ASPECTS,
    UefiCmdAspects,
)
from automojo.protocols.uefi.uefiexceptions import UefiError


# ---------------------------------------------------------------------------
# Stub UefiClient / UefiChannel / transport
# ---------------------------------------------------------------------------


class _StubChannel:
    """
        Minimal :class:`UefiChannel`-shaped stub.  Records ``close``
        calls so the test can assert exactly when the session tears
        down its channel.
    """

    def __init__(self, owner: "_StubClient") -> None:
        """
            Initialize the stub channel.

            :param owner: The :class:`_StubClient` that produced this
                          channel.  Stored so the test can cross-check
                          the ownership chain.
        """
        self.owner = owner
        self.closed_count = 0
        return

    def close(self) -> None:
        """
            Record the close call.
        """
        self.closed_count += 1
        return


class _StubClient:
    """
        Minimal :class:`UefiClient`-shaped stub.  Produces
        :class:`_StubChannel` instances; records every ``open_channel``
        and ``close`` call; exposes a toggleable ``connected`` flag so
        tests can simulate transport-drop conditions.
    """

    def __init__(self) -> None:
        """
            Initialize the stub client.
        """
        self.connected_flag = True
        self.channels: List[_StubChannel] = []
        self.closed_count = 0
        self.execute_calls: List[Tuple[str, Optional[UefiCmdAspects]]] = []
        self.execute_response: Tuple[int, str, str] = (0, "ok", "")
        self.execute_exception: Optional[BaseException] = None
        return

    @property
    def connected(self) -> bool:
        """
            :returns: The current value of ``connected_flag``.
        """
        rtnval = self.connected_flag
        return rtnval

    def open_channel(self, aspects: Optional[UefiCmdAspects] = None) -> _StubChannel:
        """
            Produce a fresh :class:`_StubChannel` and record the call.

            :param aspects: Ignored; accepted for signature parity.

            :returns: A new :class:`_StubChannel`.
        """
        channel = _StubChannel(owner=self)
        self.channels.append(channel)
        return channel

    def execute(self, command: str,
                      aspects: Optional[UefiCmdAspects] = None) -> Tuple[int, str, str]:
        """
            Record the call and return the canned response (or raise
            the canned exception).

            :param command: The command string.
            :param aspects: The effective :class:`UefiCmdAspects`.

            :returns: The canned ``(status, stdout, stderr)`` tuple.
            :raises: The canned exception, when one is primed.
        """
        self.execute_calls.append((command, aspects))
        if self.execute_exception is not None:
            exc = self.execute_exception
            raise exc
        rtnval = self.execute_response
        return rtnval

    def close(self) -> None:
        """
            Record the close call and flip ``connected_flag``.
        """
        self.closed_count += 1
        self.connected_flag = False
        return


class _StubTransport:
    """
        Minimal :class:`IUefiTransport`-shaped stub.  Not directly used
        by most tests because the session's
        ``default_client_factory`` is patched, but kept here so tests
        that hit ``_build_transport`` can rely on a local stand-in.
    """

    def __init__(self, host: str, credential: Any, aspects: UefiCmdAspects,
                       **kwargs: Any) -> None:
        """
            Initialize the stub transport.
        """
        self.host = host
        self.credential = credential
        self.aspects = aspects
        self.connected_flag = False
        self.connect_calls = 0
        return

    @property
    def connected(self) -> bool:
        rtnval = self.connected_flag
        return rtnval

    def connect(self, aspects: Optional[UefiCmdAspects] = None) -> None:
        """
            Flip the connected flag.
        """
        self.connect_calls += 1
        self.connected_flag = True
        return

    def disconnect(self) -> None:
        """
            Flip the connected flag back.
        """
        self.connected_flag = False
        return


# ---------------------------------------------------------------------------
# Stub UefiBase + default_client_factory module
# ---------------------------------------------------------------------------


_UEFIBASE_MODULE_NAME = "automojo.protocols.uefi.uefibase"


class _StubUefiBase:
    """
        Minimal :class:`UefiBase` stand-in.  Establishes the instance
        variables the real base constructor would set, and provides a
        ``_run_cmd`` entry point the session invokes.  Tests prime
        ``_run_cmd`` behavior via per-instance attributes.
    """

    def __init__(self, host: str,
                       primary_credential: Any,
                       transport_kind: str,
                       jump: Optional[Any] = None,
                       users: Optional[Dict[str, Any]] = None,
                       called_id: Optional[str] = None,
                       aspects: UefiCmdAspects = DEFAULT_CMD_ASPECTS) -> None:
        """
            Initialize the stub base.  Accepts the same arguments the
            real :class:`UefiBase` would.
        """
        self._host = host
        self._primary_credential = primary_credential
        self._transport_kind = transport_kind
        self._jump = jump
        self._users = users if users is not None else {}
        self._called_id = called_id
        self._target_tag = host if called_id is None else "[{}]".format(called_id)
        self._aspects = aspects
        self._logger = _make_null_logger()

        # Test-steered behavior for _run_cmd:
        self._run_cmd_calls: List[Dict[str, Any]] = []
        self._run_cmd_response: Tuple[int, str, str] = (0, "ok", "")
        self._run_cmd_exception_sequence: List[Optional[BaseException]] = []
        return

    def _run_cmd(self, runner: Any, command: str,
                       exp_status: Union[int, Sequence[int]] = 0,
                       user: Optional[str] = None,
                       aspects: Optional[UefiCmdAspects] = None) -> Tuple[int, str, str]:
        """
            Record the call; raise from the primed exception sequence
            when non-empty; otherwise return the canned response.
        """
        self._run_cmd_calls.append({
            "runner": runner,
            "command": command,
            "exp_status": exp_status,
            "user": user,
            "aspects": aspects,
        })

        if len(self._run_cmd_exception_sequence) > 0:
            next_exc = self._run_cmd_exception_sequence.pop(0)
            if next_exc is not None:
                raise next_exc

        rtnval = self._run_cmd_response
        return rtnval


def _make_null_logger() -> Any:
    """
        Build a quiet :class:`logging.Logger`-duck-type that swallows
        every output.  Used so the session's INFO / WARNING / ERROR
        lines do not pollute test output.

        :returns: A duck-typed logger.
    """
    class _NullLogger:
        def debug(self, *args: Any, **kwargs: Any) -> None:
            return

        def info(self, *args: Any, **kwargs: Any) -> None:
            return

        def warning(self, *args: Any, **kwargs: Any) -> None:
            return

        def error(self, *args: Any, **kwargs: Any) -> None:
            return

    logger = _NullLogger()
    return logger


# Build the stub uefibase module holding _StubUefiBase + a stub
# default_client_factory.  Tests install this BEFORE importing
# uefisession so the production module's ``from ... uefibase import
# UefiBase, default_client_factory`` line resolves to our stubs.

_factory_clients: List[_StubClient] = []


def _stub_default_client_factory(transport_kind: str, transport: Any,
                                       aspects: UefiCmdAspects) -> _StubClient:
    """
        Stand-in for the real ``default_client_factory``.  Returns a
        fresh :class:`_StubClient` on every call and records the result
        in the module-level ``_factory_clients`` list so tests can
        cross-check.

        :param transport_kind: Ignored.
        :param transport: Ignored.
        :param aspects: Ignored.

        :returns: A fresh :class:`_StubClient`.
    """
    client = _StubClient()
    _factory_clients.append(client)
    return client


def _install_uefibase_stub() -> types.ModuleType:
    """
        Install the stub ``uefibase`` module into :mod:`sys.modules`
        before the production ``uefisession`` module is imported.

        :returns: The stub module.
    """
    stub_module = types.ModuleType(_UEFIBASE_MODULE_NAME)
    stub_module.UefiBase = _StubUefiBase
    stub_module.default_client_factory = _stub_default_client_factory

    sys.modules[_UEFIBASE_MODULE_NAME] = stub_module
    return stub_module


_install_uefibase_stub()


# ---------------------------------------------------------------------------
# Import the module under test AFTER the stub is installed.
# ---------------------------------------------------------------------------

from automojo.protocols.uefi import uefisession  # noqa: E402

# Force-rebind in case uefisession was already loaded by another test.
uefisession.UefiBase = _StubUefiBase
uefisession.default_client_factory = _stub_default_client_factory

from automojo.protocols.uefi.uefisession import (  # noqa: E402
    SESSION_SUPPORTED_PATTERNS,
    SESSION_UNSUPPORTED_PATTERNS,
    UefiSession,
)

# Remove the stub module from sys.modules so that sibling test files
# importing automojo.protocols.uefi.uefibase get the real module.
sys.modules.pop(_UEFIBASE_MODULE_NAME, None)


# ---------------------------------------------------------------------------
# Redirect UefiSession._build_transport so tests never touch the real
# transport classes.  The stub transport carries just enough shape for
# connect() to succeed.
# ---------------------------------------------------------------------------


def _stub_build_transport(self: UefiSession, credential: Any,
                                aspects: UefiCmdAspects) -> _StubTransport:
    """
        Stand-in for :meth:`UefiSession._build_transport`.

        :param self: The :class:`UefiSession` instance (bound).
        :param credential: The credential to pass through.
        :param aspects: The effective :class:`UefiCmdAspects`.

        :returns: A :class:`_StubTransport`.
    """
    transport = _StubTransport(
        host=self._host,
        credential=credential,
        aspects=aspects,
    )
    return transport


UefiSession._build_transport = _stub_build_transport  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _make_aspects(action_pattern: ActionPattern = ActionPattern.SINGLE_CONNECTED_CALL,
                        allowed_connection_exceptions: Tuple[type, ...] = ()
                        ) -> UefiCmdAspects:
    """
        Build a :class:`UefiCmdAspects` preset tailored for a test.

        :param action_pattern: The action pattern under test.
        :param allowed_connection_exceptions: Tuple of exception
                                              classes treated as
                                              allowed by the engine.

        :returns: A frozen :class:`UefiCmdAspects`.
    """
    aspects = UefiCmdAspects(
        action_pattern=action_pattern,
        allowed_connection_exceptions=allowed_connection_exceptions,
    )
    return aspects


def _make_session(basis_session: Optional[UefiSession] = None,
                        aspects: Optional[UefiCmdAspects] = None) -> UefiSession:
    """
        Construct a session bound to canned host/transport defaults.

        :param basis_session: Optional basis session.
        :param aspects: Optional :class:`UefiCmdAspects` override.

        :returns: A fresh :class:`UefiSession`.
    """
    if aspects is None:
        aspects = DEFAULT_CMD_ASPECTS

    session = UefiSession(
        host="10.0.0.5",
        primary_credential=object(),
        transport_kind="serial",
        aspects=aspects,
        basis_session=basis_session,
    )
    return session


# ---------------------------------------------------------------------------
# Tests: enter / exit lifecycle
# ---------------------------------------------------------------------------


class TestUefiSessionEnterExit(unittest.TestCase):
    """
        Verify that ``__enter__`` opens exactly one client and channel,
        that ``__exit__`` closes them, and that an in-block exception
        propagates unchanged.
    """

    def setUp(self) -> None:
        """
            Reset the module-level factory bookkeeping between tests.
        """
        _factory_clients.clear()
        return

    def test_enter_opens_single_client_and_channel(self) -> None:
        """
            ``with UefiSession(...) as s:`` must produce exactly one
            client and one channel.
        """
        session = _make_session()

        with session as s:
            self.assertIs(s, session)
            self.assertIsNotNone(session._client)
            self.assertIsNotNone(session._channel)
            self.assertEqual(len(_factory_clients), 1)
            self.assertEqual(len(_factory_clients[0].channels), 1)

        return

    def test_exit_closes_channel_and_client(self) -> None:
        """
            Leaving the ``with`` block closes both the channel and the
            owned client.
        """
        session = _make_session()
        with session:
            created_client = _factory_clients[0]
            created_channel = created_client.channels[0]

        self.assertEqual(created_channel.closed_count, 1)
        self.assertEqual(created_client.closed_count, 1)
        self.assertIsNone(session._client)
        self.assertIsNone(session._channel)
        return

    def test_exit_propagates_inner_exception(self) -> None:
        """
            An exception raised inside the ``with`` block is NOT
            suppressed by ``__exit__``.
        """
        session = _make_session()

        class _InBlockError(RuntimeError):
            pass

        raised = False
        try:
            with session:
                raise _InBlockError("boom")
        except _InBlockError:
            raised = True

        self.assertTrue(raised)
        # Teardown must still have happened.
        self.assertIsNone(session._client)
        self.assertIsNone(session._channel)
        return

    def test_close_is_idempotent(self) -> None:
        """
            Second and subsequent :meth:`close` calls are no-ops.
        """
        session = _make_session()
        with session:
            pass
        session.close()
        session.close()
        # No exception; still clean.
        self.assertIsNone(session._client)
        self.assertIsNone(session._channel)
        return


# ---------------------------------------------------------------------------
# Tests: persistent client across run_cmd calls
# ---------------------------------------------------------------------------


class TestUefiSessionPersistentClient(unittest.TestCase):
    """
        Verify that :meth:`run_cmd` reuses the client built at
        :meth:`__enter__` rather than constructing a new one on every
        call.
    """

    def setUp(self) -> None:
        _factory_clients.clear()
        return

    def test_run_cmd_reuses_single_client(self) -> None:
        """
            Multiple :meth:`run_cmd` invocations on one open session
            must keep the same :class:`_StubClient` reference.
        """
        aspects = _make_aspects(action_pattern=ActionPattern.SINGLE_CONNECTED_CALL)
        session = _make_session(aspects=aspects)

        with session:
            client_before = session._client

            status1, stdout1, _ = session.run_cmd(command="ver")
            status2, stdout2, _ = session.run_cmd(command="map -r")
            status3, stdout3, _ = session.run_cmd(command="cd FS0:")

            self.assertIs(session._client, client_before)
            # Exactly one call to the factory — no additional clients.
            self.assertEqual(len(_factory_clients), 1)

        self.assertEqual(status1, 0)
        self.assertEqual(status2, 0)
        self.assertEqual(status3, 0)
        return

    def test_run_cmd_forwards_to_base_run_cmd(self) -> None:
        """
            The session must dispatch through ``_run_cmd`` with the
            persistent channel as the runner.
        """
        aspects = _make_aspects(action_pattern=ActionPattern.SINGLE_CONNECTED_CALL)
        session = _make_session(aspects=aspects)

        with session:
            session._run_cmd_response = (0, "hello", "")
            status, stdout, _ = session.run_cmd(command="echo Hello")

        self.assertEqual(status, 0)
        self.assertEqual(stdout, "hello")
        self.assertEqual(len(session._run_cmd_calls), 1)
        recorded = session._run_cmd_calls[0]
        self.assertIs(recorded["runner"], session._channel or _factory_clients[0].channels[0])
        self.assertEqual(recorded["command"], "echo Hello")
        return


# ---------------------------------------------------------------------------
# Tests: record_action + reboot replay
# ---------------------------------------------------------------------------


class TestUefiSessionReplay(unittest.TestCase):
    """
        Verify :meth:`record_action` + :meth:`reboot` replay semantics.
    """

    def setUp(self) -> None:
        _factory_clients.clear()
        return

    def test_record_action_appends(self) -> None:
        """
            :meth:`record_action` must append the callable to the
            replay list.
        """
        session = _make_session()

        calls: List[str] = []

        def action_one() -> None:
            calls.append("one")
            return

        def action_two() -> None:
            calls.append("two")
            return

        session.record_action(action=action_one)
        session.record_action(action=action_two)

        self.assertEqual(len(session._replay_actions), 2)
        return

    def test_reboot_replays_actions_in_order(self) -> None:
        """
            After :meth:`reboot` completes, every registered action is
            invoked in insertion order.
        """
        aspects = _make_aspects(action_pattern=ActionPattern.SINGLE_CONNECTED_CALL)
        session = _make_session(aspects=aspects)

        invocations: List[str] = []

        def action_one() -> None:
            invocations.append("cd FS0:")
            return

        def action_two() -> None:
            invocations.append("map -r")
            return

        # Skip the grace-period and polling waits so the test runs
        # without sleeping.
        with patch.object(uefisession.time, "sleep", lambda _s: None):
            with session:
                session.record_action(action=action_one)
                session.record_action(action=action_two)

                # Prime the reset dispatch to drop — this is the
                # expected shape.
                session._run_cmd_exception_sequence = [ConnectionError("dropped")]

                # Before-reboot reconnect count is zero.
                self.assertEqual(session.reconnect_count, 0)

                session.reboot()

                self.assertEqual(invocations, ["cd FS0:", "map -r"])
                self.assertEqual(session.reconnect_count, 1)
                # Reconnect produced a second client.
                self.assertGreaterEqual(len(_factory_clients), 2)

        return

    def test_replay_action_failure_raises_uefi_error(self) -> None:
        """
            A replay action that raises must surface as
            :class:`UefiError` with the ``action_index`` in details and
            the original exception chained via ``__cause__``.
        """
        session = _make_session()

        class _ReplayBoom(RuntimeError):
            pass

        def action_bad() -> None:
            raise _ReplayBoom("replay failed")

        session.record_action(action=action_bad)

        raised: Optional[BaseException] = None
        try:
            session._replay_recorded_actions()
        except UefiError as uerr:
            raised = uerr

        self.assertIsNotNone(raised)
        self.assertIsInstance(raised, UefiError)
        self.assertIsInstance(raised.__cause__, _ReplayBoom)
        detail_text = "\n".join(raised.details)
        self.assertIn("ACTION_INDEX: 0", detail_text)
        return


# ---------------------------------------------------------------------------
# Tests: basis_session cloning
# ---------------------------------------------------------------------------


class TestUefiSessionBasisCloning(unittest.TestCase):
    """
        Verify that ``basis_session`` cloning inherits connection
        parameters and reuses the upstream :class:`UefiClient`.
    """

    def setUp(self) -> None:
        _factory_clients.clear()
        return

    def test_basis_inherits_connection_fields(self) -> None:
        """
            A derived session inherits host, transport kind, credential,
            users, jump, called_id, and ssh_agent from the basis.
        """
        primary = object()
        users = {"admin": object()}
        jump = object()

        basis = UefiSession(
            host="10.0.0.9",
            primary_credential=primary,
            transport_kind="ssh-host",
            users=users,
            jump=jump,
            called_id="server-A",
            ssh_agent=object(),
        )

        # NOTE: Because _StubUefiBase demands both host and credential be
        # passed through, the derived session explicitly passes None to
        # trigger basis fall-through.
        derived = UefiSession(
            host=None,  # type: ignore[arg-type]
            primary_credential=None,
            transport_kind=None,  # type: ignore[arg-type]
            basis_session=basis,
        )

        self.assertEqual(derived._host, "10.0.0.9")
        self.assertIs(derived._primary_credential, primary)
        self.assertEqual(derived._transport_kind, "ssh-host")
        self.assertIs(derived._users, users)
        self.assertIs(derived._jump, jump)
        self.assertEqual(derived._called_id, "server-A")
        self.assertIs(derived._ssh_agent, basis._ssh_agent)
        return

    def test_basis_replay_actions_seeded(self) -> None:
        """
            The derived session starts with a copy of the basis's
            replay actions.
        """
        basis = _make_session()

        def action() -> None:
            return

        basis.record_action(action=action)

        derived = UefiSession(
            host=None,  # type: ignore[arg-type]
            primary_credential=None,
            transport_kind=None,  # type: ignore[arg-type]
            basis_session=basis,
        )

        self.assertEqual(len(derived._replay_actions), 1)
        # A new list reference — derived mutation must not touch basis.
        self.assertIsNot(derived._replay_actions, basis._replay_actions)
        return

    def test_derived_session_reuses_basis_client(self) -> None:
        """
            When the derived session is entered, it reuses the basis
            session's client rather than building a fresh one.
        """
        aspects = _make_aspects(action_pattern=ActionPattern.SINGLE_CONNECTED_CALL)
        basis = _make_session(aspects=aspects)

        with basis:
            basis_client = basis._client
            derived = UefiSession(
                host=None,  # type: ignore[arg-type]
                primary_credential=None,
                transport_kind=None,  # type: ignore[arg-type]
                aspects=aspects,
                basis_session=basis,
            )

            with derived:
                self.assertIs(derived._client, basis_client)
                # Derived still opened its own channel.
                self.assertIsNotNone(derived._channel)
                self.assertIsNot(derived._channel, basis._channel)

            # Exiting derived must NOT close the basis client.
            self.assertEqual(basis_client.closed_count, 0)
            self.assertTrue(basis_client.connected_flag)

        # Basis's own __exit__ closes the owned client.
        self.assertEqual(basis_client.closed_count, 1)
        return

    def test_basis_with_released_client_raises_runtime_error(self) -> None:
        """
            When the basis session's client is ``None`` at the moment
            the derived session tries to enter, ``RuntimeError`` is
            raised.
        """
        basis = _make_session()
        # Basis never entered — its _client is None.

        derived = UefiSession(
            host=None,  # type: ignore[arg-type]
            primary_credential=None,
            transport_kind=None,  # type: ignore[arg-type]
            basis_session=basis,
        )

        raised: Optional[BaseException] = None
        try:
            derived.__enter__()
        except RuntimeError as rerr:
            raised = rerr

        self.assertIsNotNone(raised)
        self.assertIsInstance(raised, RuntimeError)
        return


# ---------------------------------------------------------------------------
# Tests: action-pattern dispatch
# ---------------------------------------------------------------------------


class TestUefiSessionActionPatterns(unittest.TestCase):
    """
        Verify session-only action-pattern semantics.
    """

    def setUp(self) -> None:
        _factory_clients.clear()
        return

    def test_unsupported_patterns_raise_semantic_error(self) -> None:
        """
            Each of :data:`SESSION_UNSUPPORTED_PATTERNS` must raise
            :class:`SemanticError` at call time.
        """
        for pattern in SESSION_UNSUPPORTED_PATTERNS:
            with self.subTest(pattern=pattern):
                aspects = _make_aspects(action_pattern=pattern)
                session = _make_session(aspects=aspects)

                with session:
                    raised: Optional[BaseException] = None
                    try:
                        session.run_cmd(command="echo Hello")
                    except SemanticError as serr:
                        raised = serr

                    self.assertIsNotNone(raised)
                    self.assertIsInstance(raised, SemanticError)
        return

    def test_do_until_connection_failure_surfaces_connection_error(self) -> None:
        """
            Under :data:`ActionPattern.DO_UNTIL_CONNECTION_FAILURE` a
            mid-loop :class:`ConnectionError` is the intended exit
            signal — it propagates to the caller, and the channel is
            marked cleared so a subsequent call rebuilds it.
        """
        aspects = _make_aspects(
            action_pattern=ActionPattern.DO_UNTIL_CONNECTION_FAILURE,
            allowed_connection_exceptions=(ConnectionError,),
        )
        session = _make_session(aspects=aspects)

        with session:
            session._run_cmd_exception_sequence = [ConnectionError("drop")]

            raised: Optional[BaseException] = None
            try:
                session.run_cmd(command="tail -f logs")
            except ConnectionError as cerr:
                raised = cerr

            self.assertIsNotNone(raised)
            self.assertIsInstance(raised, ConnectionError)
            # Channel reference cleared so the next call reopens it.
            self.assertIsNone(session._channel)
        return

    def test_single_connected_call_attempts_one_reconnect_on_connection_error(self) -> None:
        """
            :data:`ActionPattern.SINGLE_CONNECTED_CALL` attempts exactly
            one reconnect on :class:`ConnectionError` and then
            re-raises the original exception.  A successful reconnect
            increments ``reconnect_count``.
        """
        aspects = _make_aspects(action_pattern=ActionPattern.SINGLE_CONNECTED_CALL)
        session = _make_session(aspects=aspects)

        with session:
            session._run_cmd_exception_sequence = [ConnectionError("single drop")]
            initial_client = session._client

            raised: Optional[BaseException] = None
            try:
                session.run_cmd(command="echo Hello")
            except ConnectionError as cerr:
                raised = cerr

            self.assertIsNotNone(raised)
            self.assertIsInstance(raised, ConnectionError)
            # Single reconnect attempted -> new client built.
            self.assertEqual(session.reconnect_count, 1)
            self.assertIsNotNone(session._client)
            self.assertIsNot(session._client, initial_client)
        return

    def test_supported_pattern_list_is_correct(self) -> None:
        """
            The module-level constant exposes exactly the two
            session-supported patterns.
        """
        self.assertIn(ActionPattern.SINGLE_CONNECTED_CALL, SESSION_SUPPORTED_PATTERNS)
        self.assertIn(ActionPattern.DO_UNTIL_CONNECTION_FAILURE, SESSION_SUPPORTED_PATTERNS)
        self.assertEqual(len(SESSION_SUPPORTED_PATTERNS), 2)
        return


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    unittest.main()
