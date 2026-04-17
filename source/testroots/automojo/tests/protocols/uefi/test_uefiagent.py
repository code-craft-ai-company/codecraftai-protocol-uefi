"""
    .. module:: test_uefiagent
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Unit tests for the :class:`UefiAgent` class, the primary
               user-facing entry point into the layer-4 UEFI interop
               machinery.

               Because the peer :mod:`automojo.protocols.uefi.uefibase`
               module may not yet be present when these tests are executed
               in isolation, the test module installs a hand-rolled
               :class:`UefiBase` stub into :mod:`sys.modules` before
               importing :mod:`uefiagent`.  The stub records every
               ``_run`` and ``verify_connectivity`` invocation so the
               agent's orchestration behavior can be observed
               deterministically.  Each transport class is patched via
               :mod:`unittest.mock` so the tests never touch real serial
               ports, BMC endpoints, IPMI subprocesses, or SSH sessions.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Automation Mojo LLC"
__credits__ = []


import sys
import types
import unittest

from typing import Any, Dict, List, Optional, Sequence, Tuple, Union
from unittest.mock import MagicMock, patch

from automojo.errors.exceptions import ConfigurationError

from automojo.protocols.uefi.uefiaspects import (
    DEFAULT_CMD_ASPECTS,
    UefiCmdAspects,
)
from automojo.protocols.uefi.ueficonst import UefiJumpParams
from automojo.protocols.uefi.uefiipmicredential import UefiIpmiCredential
from automojo.protocols.uefi.uefiredfishcredential import UefiRedfishCredential
from automojo.protocols.uefi.uefiserialcredential import UefiSerialCredential


# ---------------------------------------------------------------------------
# Install a stub uefibase module into sys.modules so that the production
# uefiagent module imports cleanly even when the peer base feature has not
# yet been authored.  The stub defines a minimal UefiBase class that accepts
# the same constructor kwargs as the real one and records _run /
# verify_connectivity invocations for assertion.
# ---------------------------------------------------------------------------


class _StubUefiBase:
    """
        Minimal stand-in for :class:`UefiBase`.  Accepts the same kwargs
        that :class:`UefiAgent` forwards via ``UefiBase.__init__`` and
        records ``_run`` / ``verify_connectivity`` calls.
    """

    def __init__(self, host: str,
                       primary_credential: Any,
                       transport_kind: str,
                       jump: Optional[UefiJumpParams] = None,
                       users: Optional[Dict[str, Any]] = None,
                       called_id: Optional[str] = None,
                       aspects: UefiCmdAspects = DEFAULT_CMD_ASPECTS) -> None:
        """
            Record the construction kwargs into instance variables that
            mirror the real UefiBase's underscore-prefixed private state.

            :param host: Target host.
            :param primary_credential: Primary credential.
            :param transport_kind: Transport selector string.
            :param jump: Optional jump params.
            :param users: Optional role-to-credential dictionary.
            :param called_id: Optional correlation id.
            :param aspects: Default aspects.
        """
        self._host = host
        self._primary_credential = primary_credential
        self._transport_kind = transport_kind
        self._jump = jump
        self._users = users if users is not None else {}
        self._called_id = called_id
        self._target_tag = host if called_id is None else "[{}]".format(called_id)
        self._aspects = aspects
        self._logger = _RecordingLogger()

        # Recording surfaces for tests.
        self._run_calls: List[Dict[str, Any]] = []
        self._run_return: Tuple[int, str, str] = (0, "", "")
        self._run_exception: Optional[BaseException] = None

        self._verify_return_sequence: List[bool] = []
        self._verify_calls: int = 0
        return

    def _run(self, client: Any, command: str,
                   exp_status: Union[int, Sequence[int]],
                   user: Optional[str],
                   aspects: UefiCmdAspects) -> Tuple[int, str, str]:
        """
            Record a ``_run`` invocation and return the primed result
            (or raise the primed exception).

            :param client: Runner passed by the agent.
            :param command: Command dispatched.
            :param exp_status: Expected status.
            :param user: Optional role name.
            :param aspects: Effective aspects for this call.

            :returns: The primed three-tuple.
            :raises: The primed exception, if any.
        """
        self._run_calls.append({
            "client": client,
            "command": command,
            "exp_status": exp_status,
            "user": user,
            "aspects": aspects,
        })
        if self._run_exception is not None:
            raise self._run_exception
        return self._run_return

    def verify_connectivity(self) -> bool:
        """
            Record a ``verify_connectivity`` invocation and return the
            next primed boolean from ``_verify_return_sequence``.

            :returns: ``True`` once the primed sequence reports success,
                      ``False`` otherwise.
        """
        self._verify_calls += 1
        if len(self._verify_return_sequence) == 0:
            rtnval = False
        else:
            rtnval = self._verify_return_sequence.pop(0)
        return rtnval


class _RecordingLogger:
    """
        Minimal logging shim that records every invocation without
        emitting anything to a real logging backend.
    """

    def __init__(self) -> None:
        """
            Initialize an empty recording list.
        """
        self.records: List[Tuple[str, str, tuple]] = []
        return

    def _record(self, level: str, msg: str, args: tuple) -> None:
        """
            Record a single log call.

            :param level: Log level name.
            :param msg: Format string.
            :param args: Positional arguments.
        """
        self.records.append((level, msg, args))
        return

    def debug(self, msg: str, *args: Any) -> None:
        self._record("debug", msg, args)
        return

    def info(self, msg: str, *args: Any) -> None:
        self._record("info", msg, args)
        return

    def warning(self, msg: str, *args: Any) -> None:
        self._record("warning", msg, args)
        return

    def error(self, msg: str, *args: Any) -> None:
        self._record("error", msg, args)
        return


_UEFIBASE_MODULE_NAME = "automojo.protocols.uefi.uefibase"


def _install_uefibase_stub() -> types.ModuleType:
    """
        Install (or refresh) the stub
        :mod:`automojo.protocols.uefi.uefibase` module.

        :returns: The stub module.
    """
    stub_module = types.ModuleType(_UEFIBASE_MODULE_NAME)
    stub_module.UefiBase = _StubUefiBase
    sys.modules[_UEFIBASE_MODULE_NAME] = stub_module
    return stub_module


_install_uefibase_stub()


# ---------------------------------------------------------------------------
# Import the module under test AFTER the stub is installed.
# ---------------------------------------------------------------------------

from automojo.protocols.uefi import uefiagent  # noqa: E402

# Force-rebind the UefiBase name on the uefiagent module in case the real
# uefibase was imported earlier in the process by a sibling test.  We also
# re-parent UefiAgent to our stub so UefiBase.__init__(self, ...) inside
# UefiAgent.__init__ resolves to our stub even when the production
# uefibase module exists elsewhere in sys.modules.
uefiagent.UefiBase = _StubUefiBase
try:
    uefiagent.UefiAgent.__bases__ = (_StubUefiBase,)
except TypeError:
    # If layout-incompatible, rely on the module-level rebinding above —
    # the explicit UefiBase.__init__(self, ...) call inside the agent will
    # still dispatch to the stub.
    pass

# Remove the stub module from sys.modules so that sibling test files
# importing automojo.protocols.uefi.uefibase get the real module.  The
# uefiagent module has already captured its reference to _StubUefiBase
# above, so this only affects fresh imports elsewhere.
sys.modules.pop(_UEFIBASE_MODULE_NAME, None)

from automojo.protocols.uefi.uefiagent import (  # noqa: E402
    DEFAULT_REBOOT_COMPLETION_TIMEOUT,
    DEFAULT_REBOOT_GRACE_PERIOD,
    DEFAULT_REBOOT_RETRY_INTERVAL,
    SHELL_RESET_COMMAND,
    SSH_HOST_RESET_COMMAND,
    UefiAgent,
)


# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------


def _build_serial_credential() -> UefiSerialCredential:
    """
        Build a valid :class:`UefiSerialCredential` for tests.

        :returns: A valid serial credential.
    """
    rtnval = UefiSerialCredential(
        device="/dev/ttyUSB0",
        baud=115200,
    )
    return rtnval


def _build_redfish_credential() -> UefiRedfishCredential:
    """
        Build a valid :class:`UefiRedfishCredential` for tests.

        :returns: A valid Redfish credential.
    """
    rtnval = UefiRedfishCredential(
        base_url="https://bmc.example/redfish/v1",
        username="admin",
        password="s3cret",
    )
    return rtnval


def _build_ipmi_credential() -> UefiIpmiCredential:
    """
        Build a valid :class:`UefiIpmiCredential` for tests.

        :returns: A valid IPMI credential.
    """
    rtnval = UefiIpmiCredential(
        host="10.0.0.1",
        username="admin",
        password="s3cret",
    )
    return rtnval


def _install_time_stubs(monkey: unittest.TestCase,
                               sleep_record: Optional[List[float]] = None,
                               time_sequence: Optional[List[float]] = None) -> None:
    """
        Swap in deterministic ``time.sleep`` and ``time.time`` stubs that
        the :mod:`uefiagent` module will resolve through its ``time``
        module attribute.  Installs a fake sub-module that the agent sees
        in place of the real :mod:`time` module and restores the original
        on cleanup.

        :param monkey: The test case (used to register cleanup).
        :param sleep_record: If non-None, each ``time.sleep(seconds)``
                             call appends ``seconds`` to this list and
                             returns immediately.
        :param time_sequence: If non-None, each ``time.time()`` call
                              pops the next value from this list; when
                              the list is empty a monotonically
                              incrementing counter is used.
    """
    original_time_module = uefiagent.time

    counter = [0.0]

    def _sleep(seconds: float) -> None:
        if sleep_record is not None:
            sleep_record.append(seconds)
        return

    def _time() -> float:
        if time_sequence is not None and len(time_sequence) > 0:
            rtnval = time_sequence.pop(0)
        else:
            counter[0] += 1.0
            rtnval = counter[0]
        return rtnval

    fake_time_module = types.SimpleNamespace(sleep=_sleep, time=_time)
    uefiagent.time = fake_time_module

    def _cleanup() -> None:
        uefiagent.time = original_time_module
        return

    monkey.addCleanup(_cleanup)
    return


# ---------------------------------------------------------------------------
# Construction tests
# ---------------------------------------------------------------------------


class UefiAgentConstructionTests(unittest.TestCase):
    """
        Exercises :meth:`UefiAgent.__init__` — valid pairings, invalid
        pairings, unknown transport kinds, missing SSH agent.
    """

    def test_serial_construct_accepts_serial_credential(self) -> None:
        """
            Serial transport + :class:`UefiSerialCredential` constructs
            successfully and stores all attributes.
        """
        cred = _build_serial_credential()
        agent = UefiAgent(
            host="target-a",
            primary_credential=cred,
            transport_kind="serial",
            called_id="node-01",
        )
        self.assertEqual(agent._host, "target-a")
        self.assertIs(agent._primary_credential, cred)
        self.assertEqual(agent._transport_kind, "serial")
        self.assertEqual(agent._called_id, "node-01")
        self.assertEqual(agent._target_tag, "[node-01]")
        self.assertIs(agent._aspects, DEFAULT_CMD_ASPECTS)
        self.assertIsNone(agent._ssh_agent)
        return

    def test_redfish_construct_accepts_redfish_credential(self) -> None:
        """
            Redfish transport + :class:`UefiRedfishCredential` constructs
            successfully.
        """
        cred = _build_redfish_credential()
        agent = UefiAgent(
            host="bmc-a",
            primary_credential=cred,
            transport_kind="redfish",
        )
        self.assertEqual(agent._transport_kind, "redfish")
        self.assertIs(agent._primary_credential, cred)
        return

    def test_ipmi_sol_construct_accepts_ipmi_credential(self) -> None:
        """
            IPMI-SOL transport + :class:`UefiIpmiCredential` constructs
            successfully.
        """
        cred = _build_ipmi_credential()
        agent = UefiAgent(
            host="bmc-b",
            primary_credential=cred,
            transport_kind="ipmi-sol",
        )
        self.assertEqual(agent._transport_kind, "ipmi-sol")
        self.assertIs(agent._primary_credential, cred)
        return

    def test_ssh_host_construct_requires_ssh_agent(self) -> None:
        """
            The ``ssh-host`` transport kind accepts any credential as
            advisory but requires a non-None ``ssh_agent``.
        """
        sentinel_ssh_agent = object()
        cred = _build_serial_credential()  # advisory — ssh-host accepts any
        agent = UefiAgent(
            host="host-a",
            primary_credential=cred,
            transport_kind="ssh-host",
            ssh_agent=sentinel_ssh_agent,
        )
        self.assertEqual(agent._transport_kind, "ssh-host")
        self.assertIs(agent._ssh_agent, sentinel_ssh_agent)
        return

    def test_ssh_host_without_ssh_agent_raises(self) -> None:
        """
            Constructing an ``ssh-host`` agent without ``ssh_agent`` raises
            :class:`ConfigurationError` with an informative message.
        """
        cred = _build_serial_credential()
        with self.assertRaises(ConfigurationError) as ctx:
            UefiAgent(
                host="host-a",
                primary_credential=cred,
                transport_kind="ssh-host",
            )
        self.assertIn("ssh_agent", str(ctx.exception))
        return

    def test_serial_with_redfish_credential_raises(self) -> None:
        """
            Passing a :class:`UefiRedfishCredential` to a ``serial``
            transport raises :class:`ConfigurationError`; the message
            names both sides.
        """
        cred = _build_redfish_credential()
        with self.assertRaises(ConfigurationError) as ctx:
            UefiAgent(
                host="target-a",
                primary_credential=cred,
                transport_kind="serial",
            )
        message = str(ctx.exception)
        self.assertIn("serial", message)
        self.assertIn("UefiSerialCredential", message)
        self.assertIn("UefiRedfishCredential", message)
        return

    def test_redfish_with_ipmi_credential_raises(self) -> None:
        """
            Passing a :class:`UefiIpmiCredential` to a ``redfish`` transport
            raises :class:`ConfigurationError`.
        """
        cred = _build_ipmi_credential()
        with self.assertRaises(ConfigurationError) as ctx:
            UefiAgent(
                host="bmc-a",
                primary_credential=cred,
                transport_kind="redfish",
            )
        self.assertIn("redfish", str(ctx.exception))
        return

    def test_ipmi_sol_with_serial_credential_raises(self) -> None:
        """
            Passing a :class:`UefiSerialCredential` to an ``ipmi-sol``
            transport raises :class:`ConfigurationError`.
        """
        cred = _build_serial_credential()
        with self.assertRaises(ConfigurationError) as ctx:
            UefiAgent(
                host="bmc-a",
                primary_credential=cred,
                transport_kind="ipmi-sol",
            )
        self.assertIn("ipmi-sol", str(ctx.exception))
        return

    def test_unknown_transport_kind_raises(self) -> None:
        """
            An unknown ``transport_kind`` string raises
            :class:`ConfigurationError` whose message lists the allowed
            values.
        """
        cred = _build_serial_credential()
        with self.assertRaises(ConfigurationError) as ctx:
            UefiAgent(
                host="target-a",
                primary_credential=cred,
                transport_kind="bogus-kind",
            )
        message = str(ctx.exception)
        self.assertIn("bogus-kind", message)
        self.assertIn("serial", message)
        self.assertIn("redfish", message)
        return

    def test_default_aspects_stored(self) -> None:
        """
            When no ``aspects`` is passed the instance default is
            :data:`DEFAULT_CMD_ASPECTS`.
        """
        cred = _build_serial_credential()
        agent = UefiAgent(
            host="target-a",
            primary_credential=cred,
            transport_kind="serial",
        )
        self.assertIs(agent._aspects, DEFAULT_CMD_ASPECTS)
        return

    def test_custom_aspects_stored(self) -> None:
        """
            A caller-supplied :class:`UefiCmdAspects` is stored as the
            instance default.
        """
        cred = _build_serial_credential()
        custom = UefiCmdAspects()
        agent = UefiAgent(
            host="target-a",
            primary_credential=cred,
            transport_kind="serial",
            aspects=custom,
        )
        self.assertIs(agent._aspects, custom)
        return


# ---------------------------------------------------------------------------
# _create_client tests
# ---------------------------------------------------------------------------


class UefiAgentCreateClientTests(unittest.TestCase):
    """
        Verifies :meth:`UefiAgent._create_client` dispatches to the
        correct transport + client combination per ``transport_kind`` and
        honors the ``user`` role lookup.
    """

    def _build_serial_agent(self, users: Optional[Dict[str, Any]] = None) -> UefiAgent:
        """
            Build a serial-transport agent for testing.

            :param users: Optional role-to-credential dictionary.
            :returns: A serial :class:`UefiAgent`.
        """
        cred = _build_serial_credential()
        rtnval = UefiAgent(
            host="target-a",
            primary_credential=cred,
            transport_kind="serial",
            users=users,
        )
        return rtnval

    def test_serial_dispatches_to_serial_transport_and_shell_client(self) -> None:
        """
            For the ``serial`` kind, :meth:`_create_client` constructs a
            :class:`UefiSerialTransport` and wraps it in a
            :class:`UefiShellClient`; the transport's ``connect`` is
            called with the effective aspects.
        """
        agent = self._build_serial_agent()

        with patch("automojo.protocols.uefi.uefiagent.UefiSerialTransport") as mock_transport, \
             patch("automojo.protocols.uefi.uefiagent.UefiShellClient") as mock_client:

            transport_instance = MagicMock()
            mock_transport.return_value = transport_instance
            client_instance = MagicMock()
            client_instance._transport = transport_instance
            mock_client.return_value = client_instance

            result = agent._create_client()

            self.assertIs(result, client_instance)
            mock_transport.assert_called_once()
            mock_client.assert_called_once()
            transport_instance.connect.assert_called_once()
            connect_kwargs = transport_instance.connect.call_args.kwargs
            self.assertIs(connect_kwargs.get("aspects"), agent._aspects)
        return

    def test_ipmi_sol_dispatches_to_ipmi_transport_and_shell_client(self) -> None:
        """
            For the ``ipmi-sol`` kind, :meth:`_create_client` constructs
            a :class:`UefiIpmiSolTransport` and wraps it in a
            :class:`UefiShellClient`.
        """
        cred = _build_ipmi_credential()
        agent = UefiAgent(
            host="bmc-a",
            primary_credential=cred,
            transport_kind="ipmi-sol",
        )

        with patch("automojo.protocols.uefi.uefiagent.UefiIpmiSolTransport") as mock_transport, \
             patch("automojo.protocols.uefi.uefiagent.UefiShellClient") as mock_client:

            transport_instance = MagicMock()
            mock_transport.return_value = transport_instance
            client_instance = MagicMock()
            client_instance._transport = transport_instance
            mock_client.return_value = client_instance

            result = agent._create_client()

            self.assertIs(result, client_instance)
            mock_transport.assert_called_once()
            mock_client.assert_called_once()
            transport_instance.connect.assert_called_once()
        return

    def test_redfish_dispatches_to_redfish_transport_and_redfish_client(self) -> None:
        """
            For the ``redfish`` kind, :meth:`_create_client` constructs
            a :class:`UefiRedfishTransport` and wraps it in a
            :class:`UefiRedfishClient`.
        """
        cred = _build_redfish_credential()
        agent = UefiAgent(
            host="bmc-a",
            primary_credential=cred,
            transport_kind="redfish",
        )

        with patch("automojo.protocols.uefi.uefiagent.UefiRedfishTransport") as mock_transport, \
             patch("automojo.protocols.uefi.uefiagent.UefiRedfishClient") as mock_client:

            transport_instance = MagicMock()
            mock_transport.return_value = transport_instance
            client_instance = MagicMock()
            client_instance._transport = transport_instance
            mock_client.return_value = client_instance

            result = agent._create_client()

            self.assertIs(result, client_instance)
            mock_transport.assert_called_once()
            mock_client.assert_called_once()
            transport_instance.connect.assert_called_once()
        return

    def test_ssh_host_dispatches_to_ssh_host_transport_and_host_os_client(self) -> None:
        """
            For the ``ssh-host`` kind, :meth:`_create_client` constructs
            a :class:`UefiSshHostTransport` from the held SshAgent and
            wraps it in a :class:`UefiHostOsClient`.
        """
        sentinel_ssh_agent = object()
        cred = _build_serial_credential()  # advisory for ssh-host
        agent = UefiAgent(
            host="host-a",
            primary_credential=cred,
            transport_kind="ssh-host",
            ssh_agent=sentinel_ssh_agent,
        )

        with patch("automojo.protocols.uefi.uefiagent.UefiSshHostTransport") as mock_transport, \
             patch("automojo.protocols.uefi.uefiagent.UefiHostOsClient") as mock_client:

            transport_instance = MagicMock()
            mock_transport.return_value = transport_instance
            client_instance = MagicMock()
            client_instance._transport = transport_instance
            mock_client.return_value = client_instance

            result = agent._create_client()

            self.assertIs(result, client_instance)
            mock_transport.assert_called_once()
            call_kwargs = mock_transport.call_args.kwargs
            self.assertIs(call_kwargs.get("ssh_agent"), sentinel_ssh_agent)
            mock_client.assert_called_once()
            transport_instance.connect.assert_called_once()
        return

    def test_create_client_with_user_selects_user_credential(self) -> None:
        """
            When ``user="admin"`` is passed, :meth:`_create_client` uses
            ``self._users["admin"]`` as the transport credential.
        """
        primary = _build_serial_credential()
        admin_cred = UefiSerialCredential(device="/dev/ttyUSB1", baud=9600)
        users = {"admin": admin_cred}
        agent = UefiAgent(
            host="target-a",
            primary_credential=primary,
            transport_kind="serial",
            users=users,
        )

        with patch("automojo.protocols.uefi.uefiagent.UefiSerialTransport") as mock_transport, \
             patch("automojo.protocols.uefi.uefiagent.UefiShellClient") as mock_client:

            transport_instance = MagicMock()
            mock_transport.return_value = transport_instance
            client_instance = MagicMock()
            client_instance._transport = transport_instance
            mock_client.return_value = client_instance

            agent._create_client(user="admin")

            call_kwargs = mock_transport.call_args.kwargs
            self.assertIs(call_kwargs.get("credential"), admin_cred)
        return

    def test_create_client_with_user_no_users_dict_raises(self) -> None:
        """
            When ``user`` is passed but no ``users`` dict was supplied
            at construction time, :class:`ConfigurationError` is raised.
        """
        agent = self._build_serial_agent(users=None)
        with self.assertRaises(ConfigurationError):
            agent._create_client(user="admin")
        return

    def test_create_client_with_unknown_user_raises(self) -> None:
        """
            When ``user`` is not in ``self._users`` a
            :class:`ConfigurationError` is raised and the message lists
            the available roles.
        """
        admin_cred = UefiSerialCredential(device="/dev/ttyUSB1", baud=9600)
        agent = self._build_serial_agent(users={"admin": admin_cred})

        with self.assertRaises(ConfigurationError) as ctx:
            agent._create_client(user="nope")

        message = str(ctx.exception)
        self.assertIn("nope", message)
        self.assertIn("admin", message)
        return


# ---------------------------------------------------------------------------
# run_cmd tests
# ---------------------------------------------------------------------------


class UefiAgentRunCmdTests(unittest.TestCase):
    """
        Exercises :meth:`UefiAgent.run_cmd` — client lifecycle, delegation
        to :meth:`UefiBase._run`, and ``finally``-block cleanup.
    """

    def _build_agent(self) -> UefiAgent:
        """
            Build a serial agent for run_cmd tests.

            :returns: A fresh :class:`UefiAgent`.
        """
        cred = _build_serial_credential()
        rtnval = UefiAgent(
            host="target-a",
            primary_credential=cred,
            transport_kind="serial",
        )
        return rtnval

    def test_run_cmd_opens_client_delegates_to_run_and_closes_client(self) -> None:
        """
            Happy path: :meth:`run_cmd` opens a client, calls
            :meth:`UefiBase._run` with that client, and closes the client
            before returning.
        """
        agent = self._build_agent()
        agent._run_return = (0, "hello", "")

        fake_client = MagicMock()

        with patch.object(UefiAgent, "_create_client", return_value=fake_client) as mock_create:
            status, stdout, stderr = agent.run_cmd(command="echo hi", exp_status=0)

            self.assertEqual(status, 0)
            self.assertEqual(stdout, "hello")
            self.assertEqual(stderr, "")

            mock_create.assert_called_once()
            fake_client.close.assert_called_once()

            self.assertEqual(len(agent._run_calls), 1)
            call = agent._run_calls[0]
            self.assertIs(call["client"], fake_client)
            self.assertEqual(call["command"], "echo hi")
            self.assertEqual(call["exp_status"], 0)
            self.assertIsNone(call["user"])
        return

    def test_run_cmd_closes_client_even_when_run_raises(self) -> None:
        """
            Failure path: when :meth:`UefiBase._run` raises, the client
            is still closed in the ``finally`` block and the original
            exception propagates.
        """
        agent = self._build_agent()
        agent._run_exception = RuntimeError("boom")

        fake_client = MagicMock()

        with patch.object(UefiAgent, "_create_client", return_value=fake_client):
            with self.assertRaises(RuntimeError):
                agent.run_cmd(command="echo hi")

        fake_client.close.assert_called_once()
        return

    def test_run_cmd_passes_user_through_to_create_client_and_run(self) -> None:
        """
            When ``user`` is passed it is forwarded both to
            :meth:`_create_client` and into the recorded ``_run`` call.
        """
        primary = _build_serial_credential()
        admin_cred = UefiSerialCredential(device="/dev/ttyUSB1", baud=9600)
        users = {"admin": admin_cred}
        cred = primary
        agent = UefiAgent(
            host="target-a",
            primary_credential=cred,
            transport_kind="serial",
            users=users,
        )
        agent._run_return = (0, "hi", "")

        fake_client = MagicMock()

        with patch.object(UefiAgent, "_create_client", return_value=fake_client) as mock_create:
            agent.run_cmd(command="whoami", user="admin")

            mock_create.assert_called_once()
            create_kwargs = mock_create.call_args.kwargs
            self.assertEqual(create_kwargs.get("user"), "admin")

            self.assertEqual(agent._run_calls[0]["user"], "admin")
        return

    def test_run_cmd_uses_instance_default_when_aspects_none(self) -> None:
        """
            When ``aspects=None`` the instance default is resolved and
            forwarded to both :meth:`_create_client` and
            :meth:`UefiBase._run`.
        """
        agent = self._build_agent()
        agent._run_return = (0, "", "")

        fake_client = MagicMock()
        with patch.object(UefiAgent, "_create_client", return_value=fake_client) as mock_create:
            agent.run_cmd(command="echo hi")

            create_kwargs = mock_create.call_args.kwargs
            self.assertIs(create_kwargs.get("aspects"), agent._aspects)

            self.assertIs(agent._run_calls[0]["aspects"], agent._aspects)
        return

    def test_run_cmd_honors_explicit_aspects_override(self) -> None:
        """
            A non-None ``aspects`` argument is used for that call without
            mutating ``self._aspects``.
        """
        agent = self._build_agent()
        agent._run_return = (0, "", "")
        override = UefiCmdAspects()

        fake_client = MagicMock()
        with patch.object(UefiAgent, "_create_client", return_value=fake_client) as mock_create:
            agent.run_cmd(command="echo hi", aspects=override)

            create_kwargs = mock_create.call_args.kwargs
            self.assertIs(create_kwargs.get("aspects"), override)

            self.assertIs(agent._run_calls[0]["aspects"], override)
        # Instance default is unchanged.
        self.assertIsNot(agent._aspects, override)
        return


# ---------------------------------------------------------------------------
# reboot tests
# ---------------------------------------------------------------------------


class UefiAgentRebootTests(unittest.TestCase):
    """
        Exercises :meth:`UefiAgent.reboot` — happy path reconnect, timeout
        path, and transport-native reset dispatch.
    """

    def _build_agent(self, transport_kind: str = "serial") -> UefiAgent:
        """
            Build a test agent.  For ``ssh-host`` a sentinel ssh_agent is
            supplied.

            :param transport_kind: Transport kind selector.
            :returns: A fresh :class:`UefiAgent`.
        """
        if transport_kind == "serial":
            cred = _build_serial_credential()
            rtnval = UefiAgent(
                host="target-a",
                primary_credential=cred,
                transport_kind="serial",
            )
        elif transport_kind == "redfish":
            cred = _build_redfish_credential()
            rtnval = UefiAgent(
                host="bmc-a",
                primary_credential=cred,
                transport_kind="redfish",
            )
        elif transport_kind == "ipmi-sol":
            cred = _build_ipmi_credential()
            rtnval = UefiAgent(
                host="bmc-b",
                primary_credential=cred,
                transport_kind="ipmi-sol",
            )
        elif transport_kind == "ssh-host":
            cred = _build_serial_credential()
            rtnval = UefiAgent(
                host="host-a",
                primary_credential=cred,
                transport_kind="ssh-host",
                ssh_agent=object(),
            )
        else:
            raise AssertionError("unknown transport_kind: {!r}".format(transport_kind))
        return rtnval

    def test_reboot_happy_path_returns_after_reconnect(self) -> None:
        """
            Happy path: ``run_cmd`` raises :class:`ConnectionError` (the
            target dropped during reset) — swallowed.  The polling loop
            observes ``verify_connectivity() == True`` on the third call
            and returns without raising.
        """
        agent = self._build_agent(transport_kind="serial")
        agent._verify_return_sequence = [False, False, True]

        sleep_record: List[float] = []
        _install_time_stubs(self, sleep_record=sleep_record)

        with patch.object(UefiAgent, "run_cmd", side_effect=ConnectionError("drop")) as mock_run_cmd:
            agent.reboot()

            mock_run_cmd.assert_called_once()
            call_kwargs = mock_run_cmd.call_args.kwargs
            self.assertEqual(call_kwargs.get("command"), SHELL_RESET_COMMAND)

        self.assertEqual(agent._verify_calls, 3)
        # First sleep is the grace period; subsequent sleeps are retry intervals.
        self.assertEqual(sleep_record[0], DEFAULT_REBOOT_GRACE_PERIOD)
        for sleep_value in sleep_record[1:]:
            self.assertEqual(sleep_value, DEFAULT_REBOOT_RETRY_INTERVAL)
        return

    def test_reboot_timeout_raises_timeouterror(self) -> None:
        """
            Timeout path: ``verify_connectivity`` never returns ``True``
            within ``completion_timeout``; a :class:`TimeoutError` is
            raised whose details name host, transport, and elapsed_ms.
        """
        agent = self._build_agent(transport_kind="serial")
        agent._verify_return_sequence = []  # always False

        # Fake time to burn through the reboot window quickly.
        time_values = [0.0]
        for idx in range(1, 400):
            time_values.append(float(idx * 50))

        sleep_record: List[float] = []
        _install_time_stubs(
            self,
            sleep_record=sleep_record,
            time_sequence=time_values,
        )

        with patch.object(UefiAgent, "run_cmd", side_effect=ConnectionError("drop")):
            with self.assertRaises(TimeoutError) as ctx:
                agent.reboot()

        message = str(ctx.exception)
        self.assertIn("target-a", message)
        self.assertIn("serial", message)
        self.assertIn("COMPLETION_TIMEOUT", message)
        self.assertIn("ELAPSED_MS", message)
        return

    def test_reboot_swallows_expected_connection_error_from_reset(self) -> None:
        """
            An :class:`OSError` raised by ``run_cmd`` is treated as an
            expected side effect of the reset command; the polling loop
            still runs and, when ``verify_connectivity`` eventually
            returns ``True``, the reboot returns cleanly.
        """
        agent = self._build_agent(transport_kind="serial")
        agent._verify_return_sequence = [True]

        _install_time_stubs(self)

        with patch.object(UefiAgent, "run_cmd", side_effect=OSError("broken pipe")):
            agent.reboot()

        self.assertEqual(agent._verify_calls, 1)
        return

    def test_reboot_uses_redfish_reset_command_for_redfish_kind(self) -> None:
        """
            For the ``redfish`` kind the reset command is the canonical
            Redfish POST verb line.
        """
        agent = self._build_agent(transport_kind="redfish")
        agent._verify_return_sequence = [True]

        _install_time_stubs(self)

        with patch.object(UefiAgent, "run_cmd", side_effect=ConnectionError("drop")) as mock_run_cmd:
            agent.reboot()

            call_kwargs = mock_run_cmd.call_args.kwargs
            command = call_kwargs.get("command")
            self.assertIn("POST", command)
            self.assertIn("ComputerSystem.Reset", command)
            self.assertIn("ForceRestart", command)
        return

    def test_reboot_uses_ssh_reset_command_for_ssh_host_kind(self) -> None:
        """
            For the ``ssh-host`` kind the reset command is ``"reboot"``.
        """
        agent = self._build_agent(transport_kind="ssh-host")
        agent._verify_return_sequence = [True]

        _install_time_stubs(self)

        with patch.object(UefiAgent, "run_cmd", side_effect=ConnectionError("drop")) as mock_run_cmd:
            agent.reboot()

            call_kwargs = mock_run_cmd.call_args.kwargs
            self.assertEqual(call_kwargs.get("command"), SSH_HOST_RESET_COMMAND)
        return

    def test_reboot_uses_shell_reset_command_for_ipmi_sol_kind(self) -> None:
        """
            For the ``ipmi-sol`` kind the reset command is the UEFI
            Shell ``reset`` command.
        """
        agent = self._build_agent(transport_kind="ipmi-sol")
        agent._verify_return_sequence = [True]

        _install_time_stubs(self)

        with patch.object(UefiAgent, "run_cmd", side_effect=ConnectionError("drop")) as mock_run_cmd:
            agent.reboot()

            call_kwargs = mock_run_cmd.call_args.kwargs
            self.assertEqual(call_kwargs.get("command"), SHELL_RESET_COMMAND)
        return


# ---------------------------------------------------------------------------
# open_session tests (guarded)
# ---------------------------------------------------------------------------


class UefiAgentOpenSessionTests(unittest.TestCase):
    """
        Exercises :meth:`UefiAgent.open_session`.  The UefiSession class
        may not yet exist in the codebase; tests guard against ImportError
        by installing a stub uefisession module for the duration of the
        test run.
    """

    def setUp(self) -> None:
        """
            Install a stub :mod:`automojo.protocols.uefi.uefisession`
            module providing a minimal :class:`UefiSession` that simply
            records the construction kwargs.
        """
        self._module_name = "automojo.protocols.uefi.uefisession"
        self._prior_module = sys.modules.get(self._module_name)

        recorded_kwargs: List[Dict[str, Any]] = []

        class _StubUefiSession:
            """
                Stand-in for :class:`UefiSession`.
            """

            def __init__(self, **kwargs: Any) -> None:
                recorded_kwargs.append(dict(kwargs))
                self.kwargs = dict(kwargs)
                self._host = kwargs.get("host")
                self._transport_kind = kwargs.get("transport_kind")
                self._users = kwargs.get("users")
                self._jump = kwargs.get("jump")
                self._called_id = kwargs.get("called_id")
                return

        stub_module = types.ModuleType(self._module_name)
        stub_module.UefiSession = _StubUefiSession
        sys.modules[self._module_name] = stub_module

        self._stub_session_cls = _StubUefiSession
        self._recorded = recorded_kwargs
        return

    def tearDown(self) -> None:
        """
            Restore the previous module entry (or drop the stub).
        """
        if self._prior_module is not None:
            sys.modules[self._module_name] = self._prior_module
        else:
            sys.modules.pop(self._module_name, None)
        return

    def test_open_session_returns_session_with_agent_params(self) -> None:
        """
            Without arguments, :meth:`open_session` forwards the agent's
            host, primary credential, transport kind, jump, called_id,
            and aspects to the new session.
        """
        cred = _build_serial_credential()
        agent = UefiAgent(
            host="target-a",
            primary_credential=cred,
            transport_kind="serial",
            called_id="node-01",
        )
        session = agent.open_session()

        self.assertIsInstance(session, self._stub_session_cls)
        kwargs = self._recorded[-1]
        self.assertEqual(kwargs["host"], "target-a")
        self.assertIs(kwargs["primary_credential"], cred)
        self.assertEqual(kwargs["transport_kind"], "serial")
        self.assertEqual(kwargs["called_id"], "node-01")
        self.assertIs(kwargs["aspects"], agent._aspects)
        return

    def test_open_session_with_user_resolves_user_credential(self) -> None:
        """
            :meth:`open_session` with ``user="admin"`` uses
            ``self._users["admin"]`` as the session's primary credential.
        """
        primary = _build_serial_credential()
        admin_cred = UefiSerialCredential(device="/dev/ttyUSB1", baud=9600)
        users = {"admin": admin_cred}
        agent = UefiAgent(
            host="target-a",
            primary_credential=primary,
            transport_kind="serial",
            users=users,
        )
        agent.open_session(user="admin")

        kwargs = self._recorded[-1]
        self.assertIs(kwargs["primary_credential"], admin_cred)
        return

    def test_open_session_unknown_user_raises(self) -> None:
        """
            :meth:`open_session` with an unknown ``user`` raises
            :class:`ConfigurationError`.
        """
        cred = _build_serial_credential()
        agent = UefiAgent(
            host="target-a",
            primary_credential=cred,
            transport_kind="serial",
            users={"admin": cred},
        )
        with self.assertRaises(ConfigurationError):
            agent.open_session(user="nope")
        return

    def test_open_session_honors_custom_aspects(self) -> None:
        """
            A non-None ``aspects`` argument is forwarded to the new
            session.
        """
        cred = _build_serial_credential()
        agent = UefiAgent(
            host="target-a",
            primary_credential=cred,
            transport_kind="serial",
        )
        custom = UefiCmdAspects()
        agent.open_session(aspects=custom)

        kwargs = self._recorded[-1]
        self.assertIs(kwargs["aspects"], custom)
        return


if __name__ == "__main__":
    unittest.main()
