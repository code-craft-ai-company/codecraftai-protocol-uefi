"""
    .. module:: test_uefibase
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Unit tests for :class:`UefiBase`.  Exercises the
               action-pattern engine, the :class:`LoggingPattern`-driven
               result logger, the ``verify_connectivity`` dispatch, the
               abstract-method surface, the read-only property set, and
               the uid/gid lookup helpers.

               Because :class:`UefiBase` is abstract in three of its
               methods, a minimal concrete subclass ``_TestableBase`` is
               defined locally to allow instantiation.  A programmable
               stub :class:`UefiClient` (``_StubClient``) records
               ``execute`` calls and replays a pre-programmed sequence of
               ``(status, stdout, stderr)`` tuples so each retry loop's
               dynamics can be observed deterministically.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Automation Mojo LLC"
__credits__ = []


import logging
import unittest

from typing import List, Optional, Sequence, Tuple, Union

from automojo.errors.exceptions import (
    AbstractMethodError,
    ConfigurationError,
    SemanticError,
)

from automojo.protocols.uefi.uefiaspects import (
    ActionPattern,
    DEFAULT_CMD_ASPECTS,
    LoggingPattern,
    UefiCmdAspects,
)
from automojo.protocols.uefi.ueficlient import UefiClient
from automojo.protocols.uefi.uefibase import UefiBase


# ---------------------------------------------------------------------------
# Test helper classes
# ---------------------------------------------------------------------------


class _StubClient(UefiClient):
    """
        Programmable :class:`UefiClient` stand-in.  Each call to
        :meth:`execute` consumes the next primed
        ``(status, stdout, stderr)`` triple from
        :attr:`response_sequence` and records the dispatched command.
        When :attr:`exception_sequence` is primed with a non-``None``
        element, that exception is raised instead of returning a triple.

        This stub is used to exercise the :class:`UefiBase` action-pattern
        engine without involving a real transport.
    """

    supported_action_patterns: Tuple[ActionPattern, ...] = (
        ActionPattern.SINGLE_CALL,
        ActionPattern.SINGLE_CONNECTED_CALL,
        ActionPattern.DO_UNTIL_SUCCESS,
        ActionPattern.DO_WHILE_SUCCESS,
        ActionPattern.DO_UNTIL_CONNECTION_FAILURE,
    )

    def __init__(self) -> None:
        """
            Initialize the stub with empty recorders and response queues.
        """
        # Deliberately do NOT call UefiClient.__init__ because we do not
        # have a real IUefiTransport.  The base class's instance variables
        # are not consumed by these tests.
        self.execute_calls: List[Tuple[str, UefiCmdAspects]] = []
        self.response_sequence: List[Tuple[int, str, str]] = []
        self.exception_sequence: List[Optional[BaseException]] = []
        self.default_response: Tuple[int, str, str] = (0, "", "")
        return

    def program(self, responses: Sequence[Tuple[int, str, str]]) -> None:
        """
            Prime the stub's response queue.

            :param responses: Sequence of ``(status, stdout, stderr)``
                              tuples to return from successive
                              :meth:`execute` calls.
        """
        self.response_sequence = list(responses)
        return

    def program_exceptions(self, exceptions: Sequence[Optional[BaseException]]) -> None:
        """
            Prime the stub's exception queue.  Elements that are
            non-``None`` are raised on the corresponding call; elements
            that are ``None`` fall through to the response queue.

            :param exceptions: Sequence of optional exceptions.
        """
        self.exception_sequence = list(exceptions)
        return

    def execute(self, command: str,
                      aspects: Optional[UefiCmdAspects] = None) -> Tuple[int, str, str]:
        """
            Record the call and return the next primed triple, or raise
            the next primed exception.

            :param command: The command string dispatched by the engine.
            :param aspects: The effective :class:`UefiCmdAspects`.

            :returns: The next primed ``(status, stdout, stderr)`` tuple.
        """
        effective = aspects if aspects is not None else DEFAULT_CMD_ASPECTS
        self.execute_calls.append((command, effective))

        if len(self.exception_sequence) > 0:
            next_exc = self.exception_sequence.pop(0)
            if next_exc is not None:
                raise next_exc

        if len(self.response_sequence) > 0:
            rtnval = self.response_sequence.pop(0)
        else:
            rtnval = self.default_response

        return rtnval


class _TestableBase(UefiBase):
    """
        Minimal concrete :class:`UefiBase` subclass that wires the
        abstract methods up to the engine using a programmable
        :class:`_StubClient`.  The subclass keeps the stub client as an
        instance attribute so tests can prime it before each call.
    """

    def __init__(self, host: str = "test-host",
                       transport_kind: str = "serial",
                       called_id: Optional[str] = None,
                       aspects: UefiCmdAspects = DEFAULT_CMD_ASPECTS,
                       primary_credential: object = "fake-credential",
                       users: Optional[dict] = None) -> None:
        """
            Initialize the testable subclass with a hand-rolled stub
            client so tests can program response sequences.

            :param host: Host identifier; defaults to ``"test-host"``.
            :param transport_kind: Transport kind; defaults to
                                   ``"serial"``.
            :param called_id: Optional log-correlation id.
            :param aspects: Default aspects.
            :param primary_credential: Any non-``None`` value; not
                                       validated at this layer.
            :param users: Optional user-role mapping.
        """
        UefiBase.__init__(
            self,
            host=host,
            primary_credential=primary_credential,
            transport_kind=transport_kind,
            users=users,
            jump=None,
            called_id=called_id,
            aspects=aspects,
        )
        self._stub_client = _StubClient()
        return

    @property
    def stub_client(self) -> _StubClient:
        """
            :returns: The attached :class:`_StubClient`.
        """
        rtnval = self._stub_client
        return rtnval

    def _create_client(self, user: Optional[str] = None,
                             aspects: Optional[UefiCmdAspects] = None) -> UefiClient:
        """
            Return the attached stub client.
        """
        rtnval = self._stub_client
        return rtnval

    def run_cmd(self, command: str,
                      exp_status: Union[int, Sequence[int]] = 0,
                      user: Optional[str] = None,
                      aspects: Optional[UefiCmdAspects] = None) -> Tuple[int, str, str]:
        """
            Drive :meth:`UefiBase._run` with the attached stub client.
        """
        rtnval = self._run(
            runner=self._stub_client, command=command, exp_status=exp_status,
            user=user, aspects=aspects,
        )
        return rtnval

    def reboot(self, aspects: Optional[UefiCmdAspects] = None) -> None:
        """
            No-op reboot override for the testable subclass.
        """
        return


# ---------------------------------------------------------------------------
# Small fixture helpers
# ---------------------------------------------------------------------------


def _fast_aspects(action_pattern: ActionPattern = ActionPattern.SINGLE_CALL,
                  completion_timeout: float = 0.2,
                  completion_interval: float = 0.01,
                  logging_pattern: LoggingPattern = LoggingPattern.ALL_RESULTS,
                  ) -> UefiCmdAspects:
    """
        Build a :class:`UefiCmdAspects` with small timeouts suitable for
        fast retry-loop tests.

        :param action_pattern: The :class:`ActionPattern` to set.
        :param completion_timeout: Completion timeout in seconds.
        :param completion_interval: Completion interval in seconds.
        :param logging_pattern: The :class:`LoggingPattern` to set.

        :returns: The built :class:`UefiCmdAspects`.
    """
    rtnval = UefiCmdAspects(
        action_pattern=action_pattern,
        completion_timeout=completion_timeout,
        completion_interval=completion_interval,
        logging_pattern=logging_pattern,
    )
    return rtnval


# ---------------------------------------------------------------------------
# Construction tests
# ---------------------------------------------------------------------------


class UefiBaseConstructionTestCase(unittest.TestCase):
    """
        Verify that :class:`UefiBase` construction initializes every
        documented instance variable, that invalid inputs raise
        :class:`ConfigurationError`, and that ``_target_tag`` follows
        the documented formation rule.
    """

    def test_construct_with_valid_inputs(self) -> None:
        """
            A minimal valid construction must populate every documented
            instance attribute.
        """
        base = _TestableBase(host="host-1")

        self.assertEqual(base.host, "host-1")
        self.assertEqual(base.transport_kind, "serial")
        self.assertEqual(base.primary_credential, "fake-credential")
        self.assertIsNone(base.jump)
        self.assertIsNone(base.users)
        self.assertIsNone(base.called_id)
        self.assertEqual(base.target_tag, "host-1")
        self.assertIsInstance(base.aspects, UefiCmdAspects)
        self.assertIsNone(base.firmware_vendor)
        self.assertIsNone(base.firmware_revision)
        self.assertIsNone(base.secure_boot_state)

        return

    def test_target_tag_uses_called_id_when_set(self) -> None:
        """
            When ``called_id`` is set, ``_target_tag`` must be
            ``"[called_id]"``.
        """
        base = _TestableBase(host="host-1", called_id="primary")

        self.assertEqual(base.target_tag, "[primary]")
        self.assertEqual(base.called_id, "primary")

        return

    def test_target_tag_defaults_to_host_when_called_id_none(self) -> None:
        """
            When ``called_id`` is ``None`` the tag equals the host.
        """
        base = _TestableBase(host="target-host")

        self.assertEqual(base.target_tag, "target-host")

        return

    def test_construct_rejects_empty_host(self) -> None:
        """
            An empty host string must raise :class:`ConfigurationError`.
        """
        with self.assertRaises(ConfigurationError):
            _TestableBase(host="")

        return

    def test_construct_rejects_non_string_host(self) -> None:
        """
            A non-string host must raise :class:`ConfigurationError`.
        """
        with self.assertRaises(ConfigurationError):
            _TestableBase(host=12345)  # type: ignore[arg-type]

        return

    def test_construct_rejects_unknown_transport_kind(self) -> None:
        """
            An unknown transport-kind literal must raise
            :class:`ConfigurationError`.
        """
        with self.assertRaises(ConfigurationError):
            _TestableBase(transport_kind="telepathy")

        return

    def test_construct_rejects_none_credential(self) -> None:
        """
            A ``None`` primary credential must raise
            :class:`ConfigurationError`.
        """
        with self.assertRaises(ConfigurationError):
            _TestableBase(primary_credential=None)

        return

    def test_construct_rejects_non_cmd_aspects(self) -> None:
        """
            A non-:class:`UefiCmdAspects` ``aspects`` value must raise
            :class:`ConfigurationError`.
        """
        with self.assertRaises(ConfigurationError):
            _TestableBase(aspects="not-a-aspects")  # type: ignore[arg-type]

        return

    def test_construct_rejects_non_string_called_id(self) -> None:
        """
            A non-string ``called_id`` must raise
            :class:`ConfigurationError`.
        """
        with self.assertRaises(ConfigurationError):
            _TestableBase(called_id=17)  # type: ignore[arg-type]

        return

    def test_construct_rejects_non_dict_users(self) -> None:
        """
            A non-dict ``users`` must raise :class:`ConfigurationError`.
        """
        with self.assertRaises(ConfigurationError):
            _TestableBase(users=["not", "a", "dict"])  # type: ignore[arg-type]

        return


# ---------------------------------------------------------------------------
# Abstract method tests
# ---------------------------------------------------------------------------


class _AbstractBase(UefiBase):
    """
        Thin :class:`UefiBase` subclass that does NOT override the abstract
        methods.  Used to assert each abstract method raises
        :class:`AbstractMethodError` when called on a subclass that does
        not provide an override.
    """


class UefiBaseAbstractMethodTestCase(unittest.TestCase):
    """
        Verify that the three abstract methods raise
        :class:`AbstractMethodError` with the subclass name included.
    """

    def _make_abstract(self) -> "_AbstractBase":
        """
            Construct a minimal abstract-base instance for the tests.

            :returns: An :class:`_AbstractBase` instance.
        """
        instance = _AbstractBase(
            host="abstract-host",
            primary_credential="cred",
            transport_kind="serial",
        )
        return instance

    def test_create_client_raises_abstract(self) -> None:
        """
            :meth:`UefiBase._create_client` must raise
            :class:`AbstractMethodError` naming the subclass.
        """
        base = self._make_abstract()

        with self.assertRaises(AbstractMethodError) as ctx:
            base._create_client()

        rendered = str(ctx.exception)
        self.assertIn("_AbstractBase", rendered)

        return

    def test_run_cmd_raises_abstract(self) -> None:
        """
            :meth:`UefiBase.run_cmd` must raise
            :class:`AbstractMethodError` naming the subclass.
        """
        base = self._make_abstract()

        with self.assertRaises(AbstractMethodError) as ctx:
            base.run_cmd(command="ver")

        rendered = str(ctx.exception)
        self.assertIn("_AbstractBase", rendered)

        return

    def test_reboot_raises_abstract(self) -> None:
        """
            :meth:`UefiBase.reboot` must raise
            :class:`AbstractMethodError` naming the subclass.
        """
        base = self._make_abstract()

        with self.assertRaises(AbstractMethodError) as ctx:
            base.reboot()

        rendered = str(ctx.exception)
        self.assertIn("_AbstractBase", rendered)

        return


# ---------------------------------------------------------------------------
# Property immutability tests
# ---------------------------------------------------------------------------


class UefiBasePropertyImmutabilityTestCase(unittest.TestCase):
    """
        Verify every public property on :class:`UefiBase` is read-only;
        assignment must raise :class:`AttributeError`.
    """

    def _assert_readonly(self, base: _TestableBase, name: str) -> None:
        """
            Assert that ``setattr(base, name, ...)`` raises
            :class:`AttributeError`.

            :param base: The base instance.
            :param name: The property name.
        """
        with self.assertRaises(AttributeError):
            setattr(base, name, object())
        return

    def test_all_properties_are_read_only(self) -> None:
        """
            Assignment to any public property must raise
            :class:`AttributeError`.
        """
        base = _TestableBase(host="host-1", called_id="id-1")

        readonly_names = (
            "host",
            "primary_credential",
            "transport_kind",
            "users",
            "jump",
            "called_id",
            "target_tag",
            "aspects",
            "firmware_vendor",
            "firmware_revision",
            "secure_boot_state",
        )
        for name in readonly_names:
            self._assert_readonly(base=base, name=name)

        return


# ---------------------------------------------------------------------------
# _log_command_result tests
# ---------------------------------------------------------------------------


class _LogCapturingHandler(logging.Handler):
    """
        In-memory :class:`logging.Handler` that records every emitted
        record so tests can assert on its contents.
    """

    def __init__(self) -> None:
        """
            Initialize the capturing handler.
        """
        logging.Handler.__init__(self)
        self.records: List[logging.LogRecord] = []
        return

    def emit(self, record: logging.LogRecord) -> None:
        """
            Append the record to the in-memory list.
        """
        self.records.append(record)
        return


class UefiBaseLogCommandResultTestCase(unittest.TestCase):
    """
        Verify that :meth:`UefiBase._log_command_result` honors every
        :class:`LoggingPattern` value and handles int / sequence
        ``exp_status`` comparison correctly.
    """

    def _install_capture(self, base: _TestableBase) -> _LogCapturingHandler:
        """
            Attach a :class:`_LogCapturingHandler` to the base's logger.

            :param base: The base instance whose logger should be
                         captured.

            :returns: The installed capturing handler.
        """
        handler = _LogCapturingHandler()
        handler.setLevel(logging.DEBUG)
        base._logger.addHandler(handler)
        base._logger.setLevel(logging.DEBUG)
        return handler

    def _uninstall_capture(self, base: _TestableBase,
                                 handler: _LogCapturingHandler) -> None:
        """
            Remove the capturing handler.
        """
        base._logger.removeHandler(handler)
        return

    def test_success_all_results_logs_info(self) -> None:
        """
            A status that matches ``exp_status`` under
            :data:`LoggingPattern.ALL_RESULTS` must emit an INFO record.
        """
        base = _TestableBase()
        handler = self._install_capture(base=base)
        try:
            base._log_command_result(
                command="ver", status=0, stdout="UEFI", stderr="",
                exp_status=0, logging_pattern=LoggingPattern.ALL_RESULTS,
            )
        finally:
            self._uninstall_capture(base=base, handler=handler)

        info_records = [r for r in handler.records if r.levelno == logging.INFO]
        self.assertEqual(len(info_records), 1)

        return

    def test_success_failure_only_suppresses_log(self) -> None:
        """
            A success under :data:`LoggingPattern.FAILURE_ONLY` must not
            emit any record.
        """
        base = _TestableBase()
        handler = self._install_capture(base=base)
        try:
            base._log_command_result(
                command="ver", status=0, stdout="UEFI", stderr="",
                exp_status=0, logging_pattern=LoggingPattern.FAILURE_ONLY,
            )
        finally:
            self._uninstall_capture(base=base, handler=handler)

        self.assertEqual(len(handler.records), 0)

        return

    def test_failure_success_only_suppresses_log(self) -> None:
        """
            A failure under :data:`LoggingPattern.SUCCESS_ONLY` must not
            emit any record.
        """
        base = _TestableBase()
        handler = self._install_capture(base=base)
        try:
            base._log_command_result(
                command="ver", status=5, stdout="bad", stderr="boom",
                exp_status=0, logging_pattern=LoggingPattern.SUCCESS_ONLY,
            )
        finally:
            self._uninstall_capture(base=base, handler=handler)

        self.assertEqual(len(handler.records), 0)

        return

    def test_failure_failure_only_logs_error(self) -> None:
        """
            A failure under :data:`LoggingPattern.FAILURE_ONLY` must emit
            an ERROR record.
        """
        base = _TestableBase()
        handler = self._install_capture(base=base)
        try:
            base._log_command_result(
                command="ver", status=5, stdout="bad", stderr="boom",
                exp_status=0, logging_pattern=LoggingPattern.FAILURE_ONLY,
            )
        finally:
            self._uninstall_capture(base=base, handler=handler)

        error_records = [r for r in handler.records if r.levelno == logging.ERROR]
        self.assertEqual(len(error_records), 1)

        return

    def test_exp_status_as_tuple_matches_in(self) -> None:
        """
            A status that is ``in`` the tuple-form ``exp_status`` counts
            as a match.
        """
        base = _TestableBase()
        handler = self._install_capture(base=base)
        try:
            base._log_command_result(
                command="ver", status=2, stdout="", stderr="",
                exp_status=(0, 1, 2), logging_pattern=LoggingPattern.SUCCESS_ONLY,
            )
        finally:
            self._uninstall_capture(base=base, handler=handler)

        info_records = [r for r in handler.records if r.levelno == logging.INFO]
        self.assertEqual(len(info_records), 1)

        return

    def test_exp_status_as_tuple_mismatch_counts_failure(self) -> None:
        """
            A status that is NOT ``in`` the tuple-form ``exp_status`` is
            a failure; under :data:`LoggingPattern.FAILURE_ONLY` this
            must emit an ERROR record.
        """
        base = _TestableBase()
        handler = self._install_capture(base=base)
        try:
            base._log_command_result(
                command="ver", status=99, stdout="", stderr="",
                exp_status=(0, 1, 2), logging_pattern=LoggingPattern.FAILURE_ONLY,
            )
        finally:
            self._uninstall_capture(base=base, handler=handler)

        error_records = [r for r in handler.records if r.levelno == logging.ERROR]
        self.assertEqual(len(error_records), 1)

        return


# ---------------------------------------------------------------------------
# _run action pattern tests
# ---------------------------------------------------------------------------


class UefiBaseRunSingleCallTestCase(unittest.TestCase):
    """
        Verify that :data:`ActionPattern.SINGLE_CALL` runs the command
        exactly once and returns the programmed triple.
    """

    def test_single_call_runs_once(self) -> None:
        """
            ``SINGLE_CALL`` must invoke ``execute`` exactly once and
            return the programmed triple.
        """
        aspects = _fast_aspects(action_pattern=ActionPattern.SINGLE_CALL)
        base = _TestableBase(aspects=aspects)
        base.stub_client.program(responses=[(0, "hello-stdout", "hello-stderr")])

        status, stdout, stderr = base.run_cmd(command="ver")

        self.assertEqual(len(base.stub_client.execute_calls), 1)
        self.assertEqual(status, 0)
        self.assertEqual(stdout, "hello-stdout")
        self.assertEqual(stderr, "hello-stderr")

        return


class UefiBaseRunDoUntilSuccessTestCase(unittest.TestCase):
    """
        Verify the happy-path and timeout-path behavior of
        :data:`ActionPattern.DO_UNTIL_SUCCESS`.
    """

    def test_do_until_success_happy_path(self) -> None:
        """
            Two failures followed by a success must produce exactly
            three ``execute`` calls and return the final triple.
        """
        aspects = _fast_aspects(action_pattern=ActionPattern.DO_UNTIL_SUCCESS)
        base = _TestableBase(aspects=aspects)
        base.stub_client.program(responses=[
            (1, "try-1", ""),
            (2, "try-2", ""),
            (0, "win", ""),
        ])

        status, stdout, _ = base.run_cmd(command="probe")

        self.assertEqual(len(base.stub_client.execute_calls), 3)
        self.assertEqual(status, 0)
        self.assertEqual(stdout, "win")

        return

    def test_do_until_success_timeout_raises(self) -> None:
        """
            A perpetually failing command must raise :class:`TimeoutError`
            after ``completion_timeout`` elapses.  The error details must
            include the command, STDOUT, and STDERR markers.
        """
        aspects = _fast_aspects(action_pattern=ActionPattern.DO_UNTIL_SUCCESS)
        base = _TestableBase(aspects=aspects)

        # Prime enough fallback default_response triples; also set
        # response_sequence to a repeating failure.  The default_response
        # keeps feeding failures once the sequence is exhausted.
        base.stub_client.default_response = (42, "nope", "boom")

        with self.assertRaises(TimeoutError) as ctx:
            base.run_cmd(command="probe-cmd")

        rendered = str(ctx.exception)
        self.assertIn("CMD: probe-cmd", rendered)
        self.assertIn("STDOUT:", rendered)
        self.assertIn("STDERR:", rendered)
        self.assertIn("nope", rendered)
        self.assertIn("boom", rendered)

        return


class UefiBaseRunDoWhileSuccessTestCase(unittest.TestCase):
    """
        Verify the happy-path and timeout-path behavior of
        :data:`ActionPattern.DO_WHILE_SUCCESS`.
    """

    def test_do_while_success_exits_on_divergence(self) -> None:
        """
            Two successes followed by a divergence must produce exactly
            three ``execute`` calls and return the final (divergent)
            triple.
        """
        aspects = _fast_aspects(action_pattern=ActionPattern.DO_WHILE_SUCCESS)
        base = _TestableBase(aspects=aspects)
        base.stub_client.program(responses=[
            (0, "ok-1", ""),
            (0, "ok-2", ""),
            (7, "fail", "details"),
        ])

        status, stdout, _ = base.run_cmd(command="watch")

        self.assertEqual(len(base.stub_client.execute_calls), 3)
        self.assertEqual(status, 7)
        self.assertEqual(stdout, "fail")

        return

    def test_do_while_success_timeout_raises(self) -> None:
        """
            A perpetually succeeding command must raise
            :class:`TimeoutError` after ``completion_timeout`` elapses.
        """
        aspects = _fast_aspects(action_pattern=ActionPattern.DO_WHILE_SUCCESS)
        base = _TestableBase(aspects=aspects)
        base.stub_client.default_response = (0, "still-ok", "")

        with self.assertRaises(TimeoutError) as ctx:
            base.run_cmd(command="watch-cmd")

        rendered = str(ctx.exception)
        self.assertIn("CMD: watch-cmd", rendered)
        self.assertIn("STDOUT:", rendered)
        self.assertIn("STDERR:", rendered)

        return


class UefiBaseRunSingleConnectedCallTestCase(unittest.TestCase):
    """
        Verify that :data:`ActionPattern.SINGLE_CONNECTED_CALL` raises
        :class:`SemanticError` at this base-class layer (it is a
        session-only pattern).
    """

    def test_single_connected_call_raises_semantic_error(self) -> None:
        """
            ``SINGLE_CONNECTED_CALL`` must raise :class:`SemanticError`
            when dispatched through :class:`UefiBase`.
        """
        aspects = _fast_aspects(action_pattern=ActionPattern.SINGLE_CONNECTED_CALL)
        base = _TestableBase(aspects=aspects)

        with self.assertRaises(SemanticError) as ctx:
            base.run_cmd(command="connected")

        rendered = str(ctx.exception)
        self.assertIn("SINGLE_CONNECTED_CALL", rendered)

        return


class UefiBaseRunDoUntilConnectionFailureTestCase(unittest.TestCase):
    """
        Verify that :data:`ActionPattern.DO_UNTIL_CONNECTION_FAILURE`
        raises :class:`SemanticError` at this base-class layer (it is a
        session-only pattern).
    """

    def test_do_until_connection_failure_raises_semantic_error(self) -> None:
        """
            ``DO_UNTIL_CONNECTION_FAILURE`` must raise
            :class:`SemanticError` when dispatched through
            :class:`UefiBase`.
        """
        aspects = _fast_aspects(action_pattern=ActionPattern.DO_UNTIL_CONNECTION_FAILURE)
        base = _TestableBase(aspects=aspects)

        with self.assertRaises(SemanticError) as ctx:
            base.run_cmd(command="loop-until-disconnect")

        rendered = str(ctx.exception)
        self.assertIn("DO_UNTIL_CONNECTION_FAILURE", rendered)

        return


# ---------------------------------------------------------------------------
# verify_connectivity tests
# ---------------------------------------------------------------------------


class UefiBaseVerifyConnectivityTestCase(unittest.TestCase):
    """
        Verify :meth:`UefiBase.verify_connectivity` for the happy-path
        shell transport.  The probe issues ``echo Hello`` and expects
        the response to contain ``"Hello"`` with exit status ``0``.
    """

    def test_verify_connectivity_happy_path(self) -> None:
        """
            A clean ``(0, "Hello\\n", "")`` response must return
            ``True``.
        """
        aspects = _fast_aspects(action_pattern=ActionPattern.SINGLE_CALL)
        base = _TestableBase(transport_kind="serial", aspects=aspects)
        base.stub_client.program(responses=[(0, "Hello\n", "")])

        result = base.verify_connectivity()

        self.assertTrue(result)
        self.assertEqual(len(base.stub_client.execute_calls), 1)
        dispatched_command, _ = base.stub_client.execute_calls[0]
        self.assertEqual(dispatched_command, "echo Hello")

        return

    def test_verify_connectivity_ssh_host_exact_match(self) -> None:
        """
            Under ``transport_kind='ssh-host'`` the stdout must equal
            ``"Hello"`` after ``strip()``.
        """
        aspects = _fast_aspects(action_pattern=ActionPattern.SINGLE_CALL)
        base = _TestableBase(transport_kind="ssh-host", aspects=aspects)
        base.stub_client.program(responses=[(0, "Hello\n", "")])

        result = base.verify_connectivity()

        self.assertTrue(result)

        return

    def test_verify_connectivity_connection_error_returns_false(self) -> None:
        """
            When the underlying runner raises :class:`ConnectionError`
            the method must return ``False``.
        """
        aspects = _fast_aspects(action_pattern=ActionPattern.SINGLE_CALL)
        base = _TestableBase(transport_kind="serial", aspects=aspects)
        base.stub_client.program_exceptions(exceptions=[ConnectionError("down")])

        result = base.verify_connectivity()

        self.assertFalse(result)

        return


# ---------------------------------------------------------------------------
# Lookup helper tests
# ---------------------------------------------------------------------------


class UefiBaseLookupHelpersTestCase(unittest.TestCase):
    """
        Verify the ``lookup_user_by_uid`` and ``lookup_group_by_gid``
        helpers.  Both dispatch ``id -n -u <id>`` / ``id -n -g <id>`` via
        :meth:`run_cmd` and cache the result on success.
    """

    def test_lookup_user_by_uid_happy_path(self) -> None:
        """
            A clean ``status==0`` response must set the cache and
            return the stripped username.
        """
        aspects = _fast_aspects(action_pattern=ActionPattern.SINGLE_CALL)
        base = _TestableBase(transport_kind="ssh-host", aspects=aspects)
        base.stub_client.program(responses=[(0, "alice\n", "")])

        resolved = base.lookup_user_by_uid(uid=1001)

        self.assertEqual(resolved, "alice")
        cached = base.lookup_user_by_uid(uid=1001, update=False)
        self.assertEqual(cached, "alice")
        # Second call must hit the cache, so execute_calls is still 1.
        self.assertEqual(len(base.stub_client.execute_calls), 1)

        return

    def test_lookup_group_by_gid_happy_path(self) -> None:
        """
            A clean ``status==0`` response must set the cache and
            return the stripped group name.
        """
        aspects = _fast_aspects(action_pattern=ActionPattern.SINGLE_CALL)
        base = _TestableBase(transport_kind="ssh-host", aspects=aspects)
        base.stub_client.program(responses=[(0, "staff\n", "")])

        resolved = base.lookup_group_by_gid(gid=50)

        self.assertEqual(resolved, "staff")
        cached = base.lookup_group_by_gid(gid=50, update=False)
        self.assertEqual(cached, "staff")
        self.assertEqual(len(base.stub_client.execute_calls), 1)

        return

    def test_lookup_user_by_uid_update_false_does_not_query(self) -> None:
        """
            When ``update=False`` and the UID is not already cached,
            the helper must return ``None`` without issuing a command.
        """
        aspects = _fast_aspects(action_pattern=ActionPattern.SINGLE_CALL)
        base = _TestableBase(transport_kind="ssh-host", aspects=aspects)

        resolved = base.lookup_user_by_uid(uid=9999, update=False)

        self.assertIsNone(resolved)
        self.assertEqual(len(base.stub_client.execute_calls), 0)

        return


# ---------------------------------------------------------------------------
# Entry-point: let tests run under `python -m unittest`
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    unittest.main()
