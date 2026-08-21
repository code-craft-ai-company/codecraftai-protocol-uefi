"""
    .. module:: test_uefisshhosttransport
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Unit tests for :class:`UefiSshHostTransport` covering
               capability probing, required-capability enforcement, sudo
               escalation policy, sudo-prompt detection, the byte-pipe
               SemanticError guards, and the disconnect no-op contract.
               Every test drives the transport with a stubbed
               :class:`SshAgent` that records calls and returns canned
               ``(status, stdout, stderr)`` tuples so that no real SSH
               session is ever opened.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Code Craft AI LLC"
__credits__ = []



from ccai import testbase

from typing import Dict, List, Optional, Tuple
from unittest.mock import MagicMock

from ccai.errors.exceptions import ConfigurationError, SemanticError, SkipError

try:
    from ccai.protocols.ssh.sshagent import SshAgent
    _HAS_SSH_AGENT = True
except ImportError:
    SshAgent = None  # type: ignore[assignment,misc]
    _HAS_SSH_AGENT = False

from ccai.protocols.uefi.uefiaspects import (
    DEFAULT_CMD_ASPECTS,
    UefiCmdAspects,
)
from ccai.protocols.uefi.uefiexceptions import UefiTransportError

from ccai.protocols.uefi.uefisshhosttransport import (
    CAPABILITY_HAS_EFIBOOTMGR,
    CAPABILITY_HAS_EFIVAR,
    CAPABILITY_HAS_EFIVARFS,
    CAPABILITY_HAS_EFIVARFS_RW,
    CAPABILITY_HAS_FWUPDMGR,
    KNOWN_CAPABILITIES,
    PROBE_COMMANDS,
    SUDO_NONINTERACTIVE_PREFIX,
    TRANSPORT_KIND,
    UefiSshHostTransport,
)


# ---------------------------------------------------------------------------
# Stub SshAgent
# ---------------------------------------------------------------------------

class StubSshAgent:
    """
        Stub that records ``run_cmd`` invocations and returns canned
        ``(status, stdout, stderr)`` tuples.  A per-command response map
        may be supplied; commands not present in the map fall back to
        ``default_response``.  ``verify_connectivity`` can be forced to
        raise via ``verify_exc``.

        Use :func:`_make_stub_agent` to build an instance that passes
        :func:`isinstance` checks against :class:`SshAgent`.
    """

    def __init__(self,
                 responses: Optional[Dict[str, Tuple[int, str, str]]] = None,
                 default_response: Tuple[int, str, str] = (0, "", ""),
                 verify_exc: Optional[BaseException] = None,
                 host: str = "test-host") -> None:
        """
            Initialize the stub.
        """
        if responses is None:
            resolved_responses: Dict[str, Tuple[int, str, str]] = {}
        else:
            resolved_responses = dict(responses)

        self._responses: Dict[str, Tuple[int, str, str]] = resolved_responses
        self._default_response: Tuple[int, str, str] = default_response
        self._verify_exc: Optional[BaseException] = verify_exc
        self._host: str = host

        self.run_cmd_calls: List[Tuple[str, Optional[UefiCmdAspects]]] = []
        self.verify_call_count: int = 0
        return

    def verify_connectivity(self) -> bool:
        """
            Record the call, raise :attr:`_verify_exc` if set, else return
            ``True``.
        """
        self.verify_call_count += 1
        if self._verify_exc is not None:
            raise self._verify_exc
        rtnval = True
        return rtnval

    def run_cmd(self, command: str,
                      aspects: Optional[UefiCmdAspects] = None,
                      **kwargs: object) -> Tuple[int, str, str]:
        """
            Record the call and return the canned response for the exact
            ``command`` or :attr:`_default_response` on miss.
        """
        self.run_cmd_calls.append((command, aspects))

        response = self._responses.get(command, self._default_response)
        return response


def _make_stub_agent(**kwargs: object) -> SshAgent:
    """
        Build a :class:`MagicMock` configured with ``spec=SshAgent`` whose
        ``run_cmd`` and ``verify_connectivity`` methods delegate to a
        :class:`StubSshAgent`.  The ``spec`` argument makes the mock pass
        an ``isinstance(value, SshAgent)`` check inside
        :meth:`UefiSshHostTransport.__init__`.

        :returns: A :class:`MagicMock` acting as an :class:`SshAgent`.
    """
    stub = StubSshAgent(**kwargs)  # type: ignore[arg-type]

    agent = MagicMock(spec=SshAgent)
    agent._host = stub._host
    agent.verify_connectivity.side_effect = stub.verify_connectivity
    agent.run_cmd.side_effect = stub.run_cmd

    # Expose the stub so tests can read run_cmd_calls / verify_call_count.
    agent.stub = stub
    return agent  # type: ignore[return-value]


def _all_present_responses() -> Dict[str, Tuple[int, str, str]]:
    """
        Build a response map that reports every capability as present.

        :returns: A dict keyed by probe command string.
    """
    mapping: Dict[str, Tuple[int, str, str]] = {}
    for name in KNOWN_CAPABILITIES:
        command = PROBE_COMMANDS[name]
        mapping[command] = (0, "/usr/bin/{}".format(name), "")
    return mapping


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

def _require_ssh() -> None:
    """
        Skips the calling test when the optional
        'codecraftai-protocol-ssh' package is absent.  The ssh-host
        transport is the only feature that needs it, so its absence is a
        valid configuration rather than a failure.

        testbase has no module-level setup hook, so the gate is an
        explicit call rather than the setUpModule this replaced.

        :raises: SkipError
    """
    if not _HAS_SSH_AGENT:
        raise SkipError("ccai.protocols.ssh not installed")

    return


# ----------------------------------------------------------------------
# UefiSshHostTransportConstructionTestCase
# ----------------------------------------------------------------------


def test_non_sshagent_raises() -> None:
    """
        Passing a non-:class:`SshAgent` value as ``ssh_agent`` must
        raise :class:`ConfigurationError`.
    """
    _require_ssh()
    with testbase.raises(ConfigurationError):
        UefiSshHostTransport(ssh_agent="not-an-agent")  # type: ignore[arg-type]
    return

def test_non_aspects_raises() -> None:
    """
        Passing a non-:class:`UefiCmdAspects` value as ``aspects``
        must raise :class:`ConfigurationError`.
    """
    _require_ssh()
    agent = _make_stub_agent()
    with testbase.raises(ConfigurationError):
        UefiSshHostTransport(ssh_agent=agent, aspects="not-aspects")  # type: ignore[arg-type]
    return

def test_construction_records_state() -> None:
    """
        Construction must stash the agent and aspects and must leave
        the transport marked as not-connected with an empty capability
        dict.
    """
    _require_ssh()
    agent = _make_stub_agent()
    transport = UefiSshHostTransport(ssh_agent=agent)

    testbase.assert_true(transport.ssh_agent is agent)
    testbase.assert_false(transport.connected)
    testbase.assert_equal(transport.capabilities, {})
    testbase.assert_equal(transport.transport_kind, TRANSPORT_KIND)
    return


# ---------------------------------------------------------------------------
# connect() / capability probing
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# UefiSshHostTransportConnectTestCase
# ----------------------------------------------------------------------


def test_connect_all_capabilities_present() -> None:
    """
        When every probe exits 0, every capability flag must be
        ``True`` and the transport must be marked connected.
    """
    _require_ssh()
    agent = _make_stub_agent(responses=_all_present_responses())
    transport = UefiSshHostTransport(ssh_agent=agent)
    transport.connect()

    caps = transport.capabilities
    testbase.assert_true(caps[CAPABILITY_HAS_EFIBOOTMGR])
    testbase.assert_true(caps[CAPABILITY_HAS_EFIVAR])
    testbase.assert_true(caps[CAPABILITY_HAS_FWUPDMGR])
    testbase.assert_true(caps[CAPABILITY_HAS_EFIVARFS])
    testbase.assert_true(caps[CAPABILITY_HAS_EFIVARFS_RW])

    testbase.assert_true(transport.connected)
    testbase.assert_equal(agent.stub.verify_call_count, 1)
    return

def test_connect_capability_keys_are_complete() -> None:
    """
        Every key in :data:`KNOWN_CAPABILITIES` must be populated after
        :meth:`connect`.
    """
    _require_ssh()
    agent = _make_stub_agent(responses=_all_present_responses())
    transport = UefiSshHostTransport(ssh_agent=agent)
    transport.connect()

    for name in KNOWN_CAPABILITIES:
        testbase.assert_true(name in transport.capabilities)
    return

def test_connect_missing_binaries_do_not_raise_by_default() -> None:
    """
        When no capabilities are marked as required, a missing binary
        must set the flag to ``False`` and MUST NOT raise.
    """
    _require_ssh()
    responses: Dict[str, Tuple[int, str, str]] = {
        PROBE_COMMANDS[CAPABILITY_HAS_EFIBOOTMGR]: (1, "", ""),
        PROBE_COMMANDS[CAPABILITY_HAS_EFIVAR]: (1, "", ""),
        PROBE_COMMANDS[CAPABILITY_HAS_FWUPDMGR]: (0, "/usr/bin/fwupdmgr", ""),
        PROBE_COMMANDS[CAPABILITY_HAS_EFIVARFS]: (0, "", ""),
        PROBE_COMMANDS[CAPABILITY_HAS_EFIVARFS_RW]: (1, "", ""),
    }
    agent = _make_stub_agent(responses=responses)
    transport = UefiSshHostTransport(ssh_agent=agent)
    transport.connect()

    caps = transport.capabilities
    testbase.assert_false(caps[CAPABILITY_HAS_EFIBOOTMGR])
    testbase.assert_false(caps[CAPABILITY_HAS_EFIVAR])
    testbase.assert_true(caps[CAPABILITY_HAS_FWUPDMGR])
    testbase.assert_true(caps[CAPABILITY_HAS_EFIVARFS])
    testbase.assert_false(caps[CAPABILITY_HAS_EFIVARFS_RW])

    testbase.assert_true(transport.connected)
    return

def test_connect_require_capabilities_raises_on_missing() -> None:
    """
        When ``require_capabilities`` names a capability whose probe
        returned non-zero, :class:`UefiTransportError` must be raised
        whose ``details`` list the missing subjects.
    """
    _require_ssh()
    responses: Dict[str, Tuple[int, str, str]] = {
        PROBE_COMMANDS[CAPABILITY_HAS_EFIBOOTMGR]: (1, "", ""),
        PROBE_COMMANDS[CAPABILITY_HAS_EFIVAR]: (0, "/usr/bin/efivar", ""),
        PROBE_COMMANDS[CAPABILITY_HAS_FWUPDMGR]: (0, "/usr/bin/fwupdmgr", ""),
        PROBE_COMMANDS[CAPABILITY_HAS_EFIVARFS]: (0, "", ""),
        PROBE_COMMANDS[CAPABILITY_HAS_EFIVARFS_RW]: (0, "", ""),
    }
    agent = _make_stub_agent(responses=responses)
    transport = UefiSshHostTransport(ssh_agent=agent)

    with testbase.raises(UefiTransportError) as ctx:
        transport.connect(require_capabilities=(CAPABILITY_HAS_EFIBOOTMGR,))

    details_text = " ".join(ctx.exception.details)
    testbase.assert_true(CAPABILITY_HAS_EFIBOOTMGR in details_text)
    testbase.assert_true("efibootmgr" in details_text)
    return

def test_connect_require_capabilities_unknown_raises_semantic() -> None:
    """
        ``require_capabilities`` naming a key not in
        :data:`KNOWN_CAPABILITIES` must raise :class:`SemanticError`.
    """
    _require_ssh()
    agent = _make_stub_agent(responses=_all_present_responses())
    transport = UefiSshHostTransport(ssh_agent=agent)

    with testbase.raises(SemanticError):
        transport.connect(require_capabilities=("has_nonexistent_tool",))
    return

def test_connect_verify_connectivity_failure_raises_transport() -> None:
    """
        An exception raised by :meth:`SshAgent.verify_connectivity`
        must surface as :class:`UefiTransportError` chained from the
        underlying cause.
    """
    _require_ssh()
    agent = _make_stub_agent(verify_exc=ConnectionError("unreachable"))
    transport = UefiSshHostTransport(ssh_agent=agent)

    with testbase.raises(UefiTransportError) as ctx:
        transport.connect()

    testbase.assert_true(isinstance(ctx.exception.__cause__, ConnectionError))
    return

def test_connect_probe_exception_sets_flag_false() -> None:
    """
        A probe that raises an unexpected exception must be treated as
        ``False`` — the transport MUST NOT propagate the exception
        past the probe loop for probes that are not in
        ``require_capabilities``.
    """
    _require_ssh()
    base_responses = _all_present_responses()
    base_responses[PROBE_COMMANDS[CAPABILITY_HAS_EFIBOOTMGR]] = (0, "", "")

    def _raising_run_cmd(command: str, **kwargs: object) -> Tuple[int, str, str]:
        if command == PROBE_COMMANDS[CAPABILITY_HAS_EFIBOOTMGR]:
            raise RuntimeError("probe-go-boom")
        response = base_responses.get(command, (0, "", ""))
        return response

    agent = MagicMock(spec=SshAgent)
    agent._host = "test-host"
    agent.verify_connectivity.return_value = True
    agent.run_cmd.side_effect = _raising_run_cmd

    transport = UefiSshHostTransport(ssh_agent=agent)
    transport.connect()

    testbase.assert_false(transport.capabilities[CAPABILITY_HAS_EFIBOOTMGR])
    testbase.assert_true(transport.capabilities[CAPABILITY_HAS_EFIVAR])
    return


# ---------------------------------------------------------------------------
# require()
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# UefiSshHostTransportRequireTestCase
# ----------------------------------------------------------------------


def test_require_returns_on_true() -> None:
    """
        :meth:`require` must return normally when the named capability
        probed ``True``.
    """
    _require_ssh()
    agent = _make_stub_agent(responses=_all_present_responses())
    transport = UefiSshHostTransport(ssh_agent=agent)
    transport.connect()

    result = transport.require(capability_name=CAPABILITY_HAS_EFIBOOTMGR)
    testbase.assert_true(result is None)
    return

def test_require_raises_transport_on_false() -> None:
    """
        :meth:`require` must raise :class:`UefiTransportError` when
        the named capability probed ``False``.
    """
    _require_ssh()
    responses = _all_present_responses()
    responses[PROBE_COMMANDS[CAPABILITY_HAS_EFIVAR]] = (1, "", "")

    agent = _make_stub_agent(responses=responses)
    transport = UefiSshHostTransport(ssh_agent=agent)
    transport.connect()

    with testbase.raises(UefiTransportError):
        transport.require(capability_name=CAPABILITY_HAS_EFIVAR)
    return

def test_require_raises_semantic_on_unknown() -> None:
    """
        :meth:`require` with an unknown key must raise
        :class:`SemanticError`.
    """
    _require_ssh()
    agent = _make_stub_agent(responses=_all_present_responses())
    transport = UefiSshHostTransport(ssh_agent=agent)
    transport.connect()

    with testbase.raises(SemanticError):
        transport.require(capability_name="has_unknown")
    return


# ---------------------------------------------------------------------------
# run_cmd() sudo policy
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# UefiSshHostTransportRunCmdTestCase
# ----------------------------------------------------------------------


def test_run_cmd_no_root_passes_through() -> None:
    """
        :meth:`run_cmd` with ``as_root=False`` must pass the command
        unchanged to :meth:`SshAgent.run_cmd`.
    """
    _require_ssh()
    agent = _make_stub_agent(default_response=(0, "hello", ""))
    transport = UefiSshHostTransport(ssh_agent=agent)

    status, stdout, stderr = transport.run_cmd(command="echo hello")
    testbase.assert_equal(status, 0)
    testbase.assert_equal(stdout, "hello")
    testbase.assert_equal(stderr, "")

    issued_command, _ = agent.stub.run_cmd_calls[-1]
    testbase.assert_equal(issued_command, "echo hello")
    return

def test_run_cmd_as_root_prefixes_sudo_n() -> None:
    """
        :meth:`run_cmd` with ``as_root=True`` must prepend
        ``sudo -n `` to the command before dispatching to the
        :class:`SshAgent`.
    """
    _require_ssh()
    agent = _make_stub_agent(default_response=(0, "", ""))
    transport = UefiSshHostTransport(ssh_agent=agent)

    transport.run_cmd(command="efibootmgr -v", as_root=True)

    issued_command, _ = agent.stub.run_cmd_calls[-1]
    expected = SUDO_NONINTERACTIVE_PREFIX + "efibootmgr -v"
    testbase.assert_equal(issued_command, expected)
    return

def test_run_cmd_as_root_sudo_prompt_raises_configuration() -> None:
    """
        When ``as_root=True`` and stderr contains the sudo password
        sentinel, :meth:`run_cmd` must raise
        :class:`ConfigurationError` (not :class:`UefiTransportError`
        — this is caller misconfiguration).
    """
    _require_ssh()
    prompt_stderr = "sudo: a password is required\n"
    agent = _make_stub_agent(default_response=(1, "", prompt_stderr))
    transport = UefiSshHostTransport(ssh_agent=agent)

    with testbase.raises(ConfigurationError):
        transport.run_cmd(command="efibootmgr -v", as_root=True)
    return

def test_run_cmd_as_root_terminal_required_raises_configuration() -> None:
    """
        The ``a terminal is required`` variant of the sudo refusal
        sentinel must also produce :class:`ConfigurationError`.
    """
    _require_ssh()
    prompt_stderr = "sudo: a terminal is required to read the password\n"
    agent = _make_stub_agent(default_response=(1, "", prompt_stderr))
    transport = UefiSshHostTransport(ssh_agent=agent)

    with testbase.raises(ConfigurationError):
        transport.run_cmd(command="efibootmgr -v", as_root=True)
    return

def test_run_cmd_no_root_does_not_check_sudo() -> None:
    """
        When ``as_root=False`` the sudo prompt sentinel in stderr
        must NOT raise — it is only interpreted as a sudo refusal
        when the caller actually asked for sudo.
    """
    _require_ssh()
    bogus_stderr = "something about a password is required elsewhere\n"
    agent = _make_stub_agent(default_response=(0, "", bogus_stderr))
    transport = UefiSshHostTransport(ssh_agent=agent)

    status, stdout, stderr = transport.run_cmd(command="true", as_root=False)
    testbase.assert_equal(status, 0)
    testbase.assert_equal(stderr, bogus_stderr)
    return

def test_run_cmd_rejects_non_string_command() -> None:
    """
        :meth:`run_cmd` must reject a non-string ``command`` with
        :class:`ConfigurationError`.
    """
    _require_ssh()
    agent = _make_stub_agent()
    transport = UefiSshHostTransport(ssh_agent=agent)

    with testbase.raises(ConfigurationError):
        transport.run_cmd(command=12345)  # type: ignore[arg-type]
    return

def test_run_cmd_rejects_non_bool_as_root() -> None:
    """
        :meth:`run_cmd` must reject a non-bool ``as_root`` with
        :class:`ConfigurationError`.
    """
    _require_ssh()
    agent = _make_stub_agent()
    transport = UefiSshHostTransport(ssh_agent=agent)

    with testbase.raises(ConfigurationError):
        transport.run_cmd(command="true", as_root="yes-please")  # type: ignore[arg-type]
    return


# ---------------------------------------------------------------------------
# send / recv / recv_until — SemanticError guards
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# UefiSshHostTransportBytePipeTestCase
# ----------------------------------------------------------------------


def test_send_raises_semantic() -> None:
    """
        :meth:`send` must raise :class:`SemanticError`.
    """
    _require_ssh()
    agent = _make_stub_agent()
    transport = UefiSshHostTransport(ssh_agent=agent)

    with testbase.raises(SemanticError):
        transport.send(data=b"bytes")
    return

def test_recv_raises_semantic() -> None:
    """
        :meth:`recv` must raise :class:`SemanticError`.
    """
    _require_ssh()
    agent = _make_stub_agent()
    transport = UefiSshHostTransport(ssh_agent=agent)

    with testbase.raises(SemanticError):
        transport.recv(max_bytes=16, timeout=0.5)
    return

def test_recv_until_raises_semantic() -> None:
    """
        :meth:`recv_until` must raise :class:`SemanticError`.
    """
    _require_ssh()
    agent = _make_stub_agent()
    transport = UefiSshHostTransport(ssh_agent=agent)

    with testbase.raises(SemanticError):
        transport.recv_until(pattern=b"\n", timeout=0.5)
    return


# ---------------------------------------------------------------------------
# disconnect() is a no-op on the SshAgent
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# UefiSshHostTransportDisconnectTestCase
# ----------------------------------------------------------------------


def test_disconnect_does_not_close_ssh_agent() -> None:
    """
        :meth:`disconnect` must flip the transport's ``connected``
        flag but must NOT invoke any close / disconnect method on the
        caller-supplied :class:`SshAgent`.
    """
    _require_ssh()
    agent = _make_stub_agent(responses=_all_present_responses())
    transport = UefiSshHostTransport(ssh_agent=agent)
    transport.connect()
    testbase.assert_true(transport.connected)

    transport.disconnect()

    testbase.assert_false(transport.connected)
    # MagicMock's close / disconnect / quit / exit must NOT be invoked.
    testbase.assert_false(agent.close.called)
    testbase.assert_false(agent.disconnect.called)
    return

def test_double_disconnect_is_safe() -> None:
    """
        :meth:`disconnect` must be idempotent.
    """
    _require_ssh()
    agent = _make_stub_agent()
    transport = UefiSshHostTransport(ssh_agent=agent)

    transport.disconnect()
    transport.disconnect()
    testbase.assert_false(transport.connected)
    return


# ---------------------------------------------------------------------------
# Aspects passthrough
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# UefiSshHostTransportAspectsTestCase
# ----------------------------------------------------------------------


def test_run_cmd_falls_back_to_default_aspects() -> None:
    """
        When a caller omits ``aspects``, the instance default must be
        forwarded to :meth:`SshAgent.run_cmd`.
    """
    _require_ssh()
    custom = UefiCmdAspects()
    agent = _make_stub_agent(default_response=(0, "", ""))
    transport = UefiSshHostTransport(ssh_agent=agent, aspects=custom)

    transport.run_cmd(command="true")

    _, aspects_used = agent.stub.run_cmd_calls[-1]
    testbase.assert_true(aspects_used is custom)
    return

def test_run_cmd_uses_explicit_aspects() -> None:
    """
        When a caller supplies an ``aspects`` argument, that exact
        instance must be forwarded to :meth:`SshAgent.run_cmd`.
    """
    _require_ssh()
    explicit = UefiCmdAspects()
    agent = _make_stub_agent(default_response=(0, "", ""))
    transport = UefiSshHostTransport(ssh_agent=agent, aspects=DEFAULT_CMD_ASPECTS)

    transport.run_cmd(command="true", aspects=explicit)

    _, aspects_used = agent.stub.run_cmd_calls[-1]
    testbase.assert_true(aspects_used is explicit)
    return
