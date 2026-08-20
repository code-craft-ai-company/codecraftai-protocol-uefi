"""
    .. module:: uefisshhosttransport
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Module containing :class:`UefiSshHostTransport`, a layer-2
               UEFI transport that reaches a *booted* operating system on
               the target via SSH and runs OS-resident UEFI tools
               (``efibootmgr``, ``efivar``, ``fwupdmgr``, and reads of
               ``/sys/firmware/efi/*``).  This transport is strictly
               post-boot; it cannot operate while the target is in UEFI
               pre-boot or in a firmware setup screen because there is no
               OS shell to reach.  The transport acquires its SSH pipe via
               *composition* — the caller constructs an :class:`SshAgent`
               with whatever credential, jump-host, and retry behavior is
               already needed, and this transport holds a reference to
               that agent.  This transport never opens a paramiko session
               of its own, never closes the caller's :class:`SshAgent`,
               and adds only UEFI-specific capability probing, a
               non-interactive sudo policy, and :class:`IUefiTransport`
               conformance on top of :meth:`SshAgent.run_cmd`.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Code Craft AI LLC"
__credits__ = []


import os

from typing import Dict, List, Optional, Sequence, Tuple

from ccai.errors.exceptions import ConfigurationError, SemanticError

try:
    from ccai.protocols.ssh.sshagent import SshAgent
except ImportError:
    # The SSH protocol package has a transitive dependency on
    # ``ccai.interop`` that may be absent in minimal installs.  We
    # soft-import so that merely importing this module does not fail in
    # environments that do not use the SSH-host transport.  Construction
    # of :class:`UefiSshHostTransport` will raise
    # :class:`ConfigurationError` when ``SshAgent`` is not available.
    SshAgent = None  # type: ignore[assignment,misc]

from ccai.protocols.uefi.uefiaspects import (
    DEFAULT_CMD_ASPECTS,
    UefiCmdAspects,
    resolve_aspects,
)
from ccai.protocols.uefi.uefiexceptions import UefiTransportError
from ccai.protocols.uefi.uefilogging import (
    UEFI_TRANSPORT_CHANNEL,
    get_uefi_logger,
)


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

TRANSPORT_KIND: str = "ssh-host"

CAPABILITY_HAS_EFIBOOTMGR: str = "has_efibootmgr"
CAPABILITY_HAS_EFIVAR: str = "has_efivar"
CAPABILITY_HAS_FWUPDMGR: str = "has_fwupdmgr"
CAPABILITY_HAS_EFIVARFS: str = "has_efivarfs"
CAPABILITY_HAS_EFIVARFS_RW: str = "has_efivarfs_rw"

KNOWN_CAPABILITIES: Tuple[str, ...] = (
    CAPABILITY_HAS_EFIBOOTMGR,
    CAPABILITY_HAS_EFIVAR,
    CAPABILITY_HAS_FWUPDMGR,
    CAPABILITY_HAS_EFIVARFS,
    CAPABILITY_HAS_EFIVARFS_RW,
)

# Per-capability probe command.  `command -v <name>` prints the resolved
# path and exits non-zero when the binary is absent; `test -d` / `test -w`
# probe directory existence and writability without requiring elevated
# privileges.
PROBE_COMMANDS: Dict[str, str] = {
    CAPABILITY_HAS_EFIBOOTMGR: "command -v efibootmgr",
    CAPABILITY_HAS_EFIVAR: "command -v efivar",
    CAPABILITY_HAS_FWUPDMGR: "command -v fwupdmgr",
    CAPABILITY_HAS_EFIVARFS: "test -d /sys/firmware/efi/efivars",
    CAPABILITY_HAS_EFIVARFS_RW: (
        "test -d /sys/firmware/efi/efivars && test -w /sys/firmware/efi/efivars"
    ),
}

# Binary / path each capability represents; used when rendering the
# ``details`` of a :class:`UefiTransportError` raised from
# :meth:`UefiSshHostTransport.connect` or
# :meth:`UefiSshHostTransport.require`.
CAPABILITY_SUBJECTS: Dict[str, str] = {
    CAPABILITY_HAS_EFIBOOTMGR: "binary 'efibootmgr'",
    CAPABILITY_HAS_EFIVAR: "binary 'efivar'",
    CAPABILITY_HAS_FWUPDMGR: "binary 'fwupdmgr'",
    CAPABILITY_HAS_EFIVARFS: "path '/sys/firmware/efi/efivars' (directory)",
    CAPABILITY_HAS_EFIVARFS_RW: "path '/sys/firmware/efi/efivars' (writable)",
}

# Substrings that identify a sudo password / terminal prompt on stderr.
# These are the exact sentinels emitted by GNU sudo when ``-n`` (non-
# interactive) refuses to prompt the user.
SUDO_PROMPT_SENTINELS: Tuple[str, ...] = (
    "a password is required",
    "a terminal is required",
    "sudo: a password is required",
    "sudo: no tty present",
)

SUDO_NONINTERACTIVE_PREFIX: str = "sudo -n "


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _detect_sudo_prompt(stderr: Optional[str]) -> bool:
    """
        Return ``True`` when ``stderr`` contains one of the sudo password /
        terminal prompt sentinels in :data:`SUDO_PROMPT_SENTINELS`.

        :param stderr: The captured standard-error text from a remote
                       command, or ``None`` if no stderr was captured.

        :returns: ``True`` when a sudo prompt sentinel is present.
    """
    if stderr is None:
        rtnval = False
        return rtnval

    if not isinstance(stderr, str):
        rtnval = False
        return rtnval

    if len(stderr) == 0:
        rtnval = False
        return rtnval

    lowered = stderr.lower()
    for sentinel in SUDO_PROMPT_SENTINELS:
        if sentinel in lowered:
            rtnval = True
            return rtnval

    rtnval = False
    return rtnval


def _safe_host(ssh_agent: SshAgent) -> str:
    """
        Pull the ``host`` attribute from an :class:`SshAgent` for use in
        log and error messages.  Falls back to a generic placeholder when
        the attribute is absent — this keeps diagnostics robust against
        mock objects used in unit tests.

        :param ssh_agent: The :class:`SshAgent` whose host identifier is
                          being rendered.

        :returns: A short host identifier string.
    """
    host_value = getattr(ssh_agent, "_host", None)
    if host_value is None:
        host_value = getattr(ssh_agent, "host", None)
    if host_value is None:
        rtnval = "<unknown-host>"
        return rtnval

    rtnval = str(host_value)
    return rtnval


# ---------------------------------------------------------------------------
# UefiSshHostTransport
# ---------------------------------------------------------------------------

class UefiSshHostTransport:
    """
        Layer-2 UEFI transport that reaches a booted OS on the target by
        SSH and executes OS-resident UEFI tooling.  Satisfies
        :class:`IUefiTransport` structurally via the
        ``connected``/``connect``/``disconnect``/``send``/``recv``
        surface.  The streaming primitives (:meth:`send`, :meth:`recv`,
        :meth:`recv_until`) are not meaningful for a command-per-call
        transport and therefore raise :class:`SemanticError`; callers
        should use :meth:`run_cmd` instead.

        The transport acquires its SSH pipe via *composition* — it never
        opens its own paramiko session and never closes the caller's
        :class:`SshAgent`.

        :param ssh_agent: Fully-constructed :class:`SshAgent` used to run
                          commands on the target.
        :param aspects: Default :class:`UefiCmdAspects` applied when a
                        caller does not pass an ``aspects`` argument to a
                        method.  Defaults to
                        :data:`DEFAULT_CMD_ASPECTS`.

        :raises: ConfigurationError
    """

    transport_kind: str = TRANSPORT_KIND

    def __init__(self, ssh_agent: SshAgent,
                       aspects: UefiCmdAspects = DEFAULT_CMD_ASPECTS) -> None:
        """
            Initialize the transport.  Every instance variable is declared
            here per the project coding standards; no lazy attribute
            creation occurs elsewhere in the class.

            :param ssh_agent: The :class:`SshAgent` used to run commands
                              against the target.
            :param aspects: Default :class:`UefiCmdAspects` applied when a
                            caller does not pass an ``aspects`` argument.

            :raises: ConfigurationError
        """
        if SshAgent is None:
            err_msg_lines = [
                "UefiSshHostTransport requires the 'ccai.protocols.ssh' package.",
                "Install 'codecraftai-protocol-ssh' (and its 'ccai.interop' transitive dep) to use this transport.",
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg) from None

        if not isinstance(ssh_agent, SshAgent):
            err_msg_lines = [
                "UefiSshHostTransport requires an SshAgent instance for 'ssh_agent'.",
                "GOT: value={!r} type={}".format(ssh_agent, type(ssh_agent).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg) from None

        if not isinstance(aspects, UefiCmdAspects):
            err_msg_lines = [
                "UefiSshHostTransport requires 'aspects' to be a UefiCmdAspects.",
                "GOT: value={!r} type={}".format(aspects, type(aspects).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg) from None

        self._ssh_agent: SshAgent = ssh_agent
        self._aspects: UefiCmdAspects = aspects

        self._capabilities: Dict[str, bool] = {}
        self._connected: bool = False
        self._run_cmd_count: int = 0

        self._logger = get_uefi_logger(channel=UEFI_TRANSPORT_CHANNEL)
        return

    # -----------------------------------------------------------------------
    # Properties
    # -----------------------------------------------------------------------

    @property
    def connected(self) -> bool:
        """
            :returns: ``True`` after :meth:`connect` has populated the
                      capability dictionary, ``False`` otherwise.  This
                      mirrors the :class:`IUefiTransport` protocol's
                      ``connected`` property.
        """
        rtnval = self._connected
        return rtnval

    @property
    def capabilities(self) -> Dict[str, bool]:
        """
            :returns: A shallow copy of the capability dictionary produced
                      by the last successful :meth:`connect`.  Returns an
                      empty dictionary when :meth:`connect` has not yet
                      been invoked.
        """
        rtnval = dict(self._capabilities)
        return rtnval

    @property
    def ssh_agent(self) -> SshAgent:
        """
            :returns: The underlying :class:`SshAgent` supplied at
                      construction.  Useful for callers that need direct
                      access to SSH-specific features (for example
                      ``file_pull`` / ``file_push``) without reaching
                      through this transport.
        """
        rtnval = self._ssh_agent
        return rtnval

    # -----------------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------------

    def connect(self, aspects: Optional[UefiCmdAspects] = None,
                      require_capabilities: Sequence[str] = ()) -> None:
        """
            Verify connectivity to the target and probe the OS-side UEFI
            tooling.  Populates :attr:`capabilities` with a boolean for
            each key in :data:`KNOWN_CAPABILITIES` (``has_efibootmgr``,
            ``has_efivar``, ``has_fwupdmgr``, ``has_efivarfs``,
            ``has_efivarfs_rw``).  When ``require_capabilities`` is a
            non-empty sequence, every capability it names must probe
            ``True``; otherwise a :class:`UefiTransportError` is raised
            whose ``details`` enumerate the missing binaries / paths and
            any probe commands that failed outright.

            :param aspects: Optional per-call :class:`UefiCmdAspects`;
                            when ``None`` the instance default is used.
            :param require_capabilities: Sequence of capability-dict keys
                                         that MUST probe ``True``.  When
                                         empty (the default) no
                                         capability is enforced at
                                         connect time — callers may use
                                         :meth:`require` later.

            :raises: UefiTransportError, ConfigurationError, SemanticError
        """
        effective = resolve_aspects(passed=aspects, instance_default=self._aspects)

        self._validate_require_capabilities(require_capabilities=require_capabilities)

        host = _safe_host(ssh_agent=self._ssh_agent)
        agent_id = id(self._ssh_agent)
        self._logger.info(
            "UefiSshHostTransport.connect: begin host={} ssh_agent_id={}".format(host, agent_id)
        )

        try:
            self._ssh_agent.verify_connectivity()
        except Exception as xcpt:
            err_msg_lines = [
                "UefiSshHostTransport.connect: SshAgent.verify_connectivity() failed.",
                "HOST: {}".format(host),
                "CAUSE: {}: {}".format(type(xcpt).__name__, xcpt),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiTransportError(err_msg, details=list(err_msg_lines[1:])) from xcpt

        self._capabilities = self._probe_capabilities(aspects=effective)

        self._logger.info(
            "UefiSshHostTransport.connect: capabilities host={} caps={}".format(
                host, self._capabilities
            )
        )

        self._connected = True

        if len(require_capabilities) > 0:
            self._enforce_required(required=require_capabilities, host=host)

        return

    def disconnect(self) -> None:
        """
            Mark the transport as disconnected.  The caller-supplied
            :class:`SshAgent` is deliberately NOT closed — lifecycle of
            that agent is owned by the caller.  This method is safe to
            call on an already-disconnected transport.
        """
        host = _safe_host(ssh_agent=self._ssh_agent)
        self._logger.debug(
            "UefiSshHostTransport.disconnect: host={} run_cmd_count={}".format(
                host, self._run_cmd_count
            )
        )

        self._connected = False
        return

    # -----------------------------------------------------------------------
    # Capability enforcement
    # -----------------------------------------------------------------------

    def require(self, capability_name: str) -> None:
        """
            Raise :class:`UefiTransportError` when the named capability
            probed ``False`` at :meth:`connect` time; return normally when
            it probed ``True``.  Names not present in
            :data:`KNOWN_CAPABILITIES` cause a :class:`SemanticError`
            because the caller is asking about a capability the transport
            does not know how to probe.

            :param capability_name: One of the keys in
                                    :data:`KNOWN_CAPABILITIES`.

            :raises: UefiTransportError, SemanticError
        """
        if capability_name not in KNOWN_CAPABILITIES:
            err_msg_lines = [
                "UefiSshHostTransport.require: unknown capability name.",
                "GOT: value={!r}".format(capability_name),
                "ALLOWED: {}".format(", ".join(KNOWN_CAPABILITIES)),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise SemanticError(err_msg) from None

        flag_value = self._capabilities.get(capability_name, False)
        if flag_value is True:
            return

        host = _safe_host(ssh_agent=self._ssh_agent)
        subject = CAPABILITY_SUBJECTS.get(capability_name, capability_name)

        err_msg_lines = [
            "UefiSshHostTransport.require: required capability is absent.",
            "CAPABILITY: {}".format(capability_name),
            "SUBJECT: {}".format(subject),
            "HOST: {}".format(host),
        ]
        err_msg = os.linesep.join(err_msg_lines)

        self._logger.warning(
            "UefiSshHostTransport.require: refusing host={} capability={} subject={}".format(
                host, capability_name, subject
            )
        )

        raise UefiTransportError(err_msg, details=list(err_msg_lines[1:])) from None

    # -----------------------------------------------------------------------
    # Command execution
    # -----------------------------------------------------------------------

    def run_cmd(self, command: str,
                      as_root: bool = False,
                      aspects: Optional[UefiCmdAspects] = None) -> Tuple[int, str, str]:
        """
            Run ``command`` on the target via the underlying
            :class:`SshAgent`.  When ``as_root`` is ``True`` the command
            is prefixed with ``sudo -n`` to force non-interactive sudo.
            If sudo stderr contains a password or terminal sentinel this
            method raises :class:`ConfigurationError` — this is a caller
            misconfiguration (sudoers requires a password) rather than a
            transport fault.

            :param command: The command string to run on the target.
            :param as_root: Whether to prefix the command with
                            ``sudo -n``.  Defaults to ``False``.
            :param aspects: Optional per-call :class:`UefiCmdAspects`;
                            when ``None`` the instance default is used.

            :returns: The ``(status, stdout, stderr)`` tuple from
                      :meth:`SshAgent.run_cmd`.
            :raises: ConfigurationError, UefiTransportError
        """
        if not isinstance(command, str):
            err_msg_lines = [
                "UefiSshHostTransport.run_cmd requires 'command' to be a str.",
                "GOT: value={!r} type={}".format(command, type(command).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg) from None

        if not isinstance(as_root, bool):
            err_msg_lines = [
                "UefiSshHostTransport.run_cmd requires 'as_root' to be a bool.",
                "GOT: value={!r} type={}".format(as_root, type(as_root).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg) from None

        effective = resolve_aspects(passed=aspects, instance_default=self._aspects)

        if as_root:
            effective_command = SUDO_NONINTERACTIVE_PREFIX + command
        else:
            effective_command = command

        self._run_cmd_count += 1

        host = _safe_host(ssh_agent=self._ssh_agent)
        self._logger.debug(
            "UefiSshHostTransport.run_cmd: host={} as_root={} call_number={} command={!r}".format(
                host, as_root, self._run_cmd_count, effective_command
            )
        )

        status, stdout, stderr = self._ssh_agent.run_cmd(effective_command, aspects=effective)

        if as_root and _detect_sudo_prompt(stderr=stderr):
            err_msg_lines = [
                "UefiSshHostTransport.run_cmd: sudo requires a password or a terminal.",
                "HOST: {}".format(host),
                "COMMAND: {}".format(effective_command),
                "STATUS: {}".format(status),
                "HINT: configure passwordless sudo for this operator or run the target",
                "      with an account whose sudoers entry includes NOPASSWD for the",
                "      required UEFI tooling (efibootmgr, efivar, fwupdmgr, tee).",
            ]
            err_msg = os.linesep.join(err_msg_lines)

            self._logger.error(
                "UefiSshHostTransport.run_cmd: sudo prompt detected host={} command={!r}".format(
                    host, effective_command
                )
            )

            raise ConfigurationError(err_msg) from None

        rtnval = (status, stdout, stderr)
        return rtnval

    # -----------------------------------------------------------------------
    # Byte-pipe primitives (not supported on a command-per-call transport)
    # -----------------------------------------------------------------------

    def send(self, data: bytes) -> int:
        """
            Always raises :class:`SemanticError`.  The SSH-to-host
            transport is a command-per-call exec pipe; byte-level
            streaming is not meaningful here.  Callers should use
            :meth:`run_cmd` instead.

            :param data: Ignored.

            :returns: Never returns.
            :raises: SemanticError
        """
        err_msg_lines = [
            "UefiSshHostTransport.send is not supported.",
            "REASON: ssh-host is a command-per-call exec transport, not a byte pipe.",
            "USE: UefiSshHostTransport.run_cmd(command=..., aspects=..., as_root=...).",
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise SemanticError(err_msg) from None

    def recv(self, max_bytes: int, timeout: float) -> bytes:
        """
            Always raises :class:`SemanticError`.  The SSH-to-host
            transport is a command-per-call exec pipe; byte-level
            streaming is not meaningful here.  Callers should use
            :meth:`run_cmd` instead.

            :param max_bytes: Ignored.
            :param timeout: Ignored.

            :returns: Never returns.
            :raises: SemanticError
        """
        err_msg_lines = [
            "UefiSshHostTransport.recv is not supported.",
            "REASON: ssh-host is a command-per-call exec transport, not a byte pipe.",
            "USE: UefiSshHostTransport.run_cmd(command=..., aspects=..., as_root=...).",
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise SemanticError(err_msg) from None

    def recv_until(self, pattern: bytes,
                         timeout: float,
                         aspects: Optional[UefiCmdAspects] = None) -> bytes:
        """
            Always raises :class:`SemanticError`.  The SSH-to-host
            transport is a command-per-call exec pipe; byte-level
            streaming is not meaningful here.  Callers should use
            :meth:`run_cmd` instead.

            :param pattern: Ignored.
            :param timeout: Ignored.
            :param aspects: Ignored.

            :returns: Never returns.
            :raises: SemanticError
        """
        err_msg_lines = [
            "UefiSshHostTransport.recv_until is not supported.",
            "REASON: ssh-host is a command-per-call exec transport, not a byte pipe.",
            "USE: UefiSshHostTransport.run_cmd(command=..., aspects=..., as_root=...).",
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise SemanticError(err_msg) from None

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    def _validate_require_capabilities(self, require_capabilities: Sequence[str]) -> None:
        """
            Ensure every capability name in ``require_capabilities`` is a
            known key.  Unknown names raise :class:`SemanticError` because
            the caller asked about a capability the transport does not
            know how to probe.

            :param require_capabilities: Sequence of capability-dict keys.

            :raises: SemanticError, ConfigurationError
        """
        if not isinstance(require_capabilities, (list, tuple)):
            err_msg_lines = [
                "UefiSshHostTransport.connect requires 'require_capabilities' to be a list or tuple.",
                "GOT: value={!r} type={}".format(
                    require_capabilities, type(require_capabilities).__name__
                ),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg) from None

        unknown: List[str] = []
        for name in require_capabilities:
            if name not in KNOWN_CAPABILITIES:
                unknown.append(name)

        if len(unknown) > 0:
            err_msg_lines = [
                "UefiSshHostTransport.connect: unknown capability name(s) in 'require_capabilities'.",
                "UNKNOWN: {}".format(", ".join(repr(x) for x in unknown)),
                "ALLOWED: {}".format(", ".join(KNOWN_CAPABILITIES)),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise SemanticError(err_msg) from None

        return

    def _probe_capabilities(self, aspects: UefiCmdAspects) -> Dict[str, bool]:
        """
            Run each entry in :data:`PROBE_COMMANDS` via
            :meth:`SshAgent.run_cmd` and map the exit status to a boolean.
            A probe that raises an exception is treated as ``False`` and
            emits a ``WARNING`` — this keeps capability probing resilient
            to transient I/O errors on individual probes.

            :param aspects: The effective :class:`UefiCmdAspects` to use
                            when running each probe.

            :returns: Dict with one boolean per key in
                      :data:`KNOWN_CAPABILITIES`.
        """
        host = _safe_host(ssh_agent=self._ssh_agent)
        result: Dict[str, bool] = {}

        for capability_name in KNOWN_CAPABILITIES:
            probe_command = PROBE_COMMANDS[capability_name]

            try:
                status, _, _ = self._ssh_agent.run_cmd(probe_command, aspects=aspects)
                probe_result = (status == 0)
            except Exception as xcpt:
                self._logger.warning(
                    "UefiSshHostTransport.connect: probe raised host={} capability={} "
                    "command={!r} cause={}: {}".format(
                        host, capability_name, probe_command, type(xcpt).__name__, xcpt
                    )
                )
                probe_result = False

            result[capability_name] = probe_result

        return result

    def _enforce_required(self, required: Sequence[str], host: str) -> None:
        """
            Enforce that every capability in ``required`` probed ``True``.
            Raises :class:`UefiTransportError` whose ``details`` enumerate
            which capabilities are missing and what subject (binary /
            path) each represents.

            :param required: Sequence of required capability-dict keys;
                             every name must be present in
                             :data:`KNOWN_CAPABILITIES` (the caller is
                             responsible for validating this).
            :param host: Host identifier for diagnostics.

            :raises: UefiTransportError
        """
        missing: List[str] = []
        for name in required:
            if self._capabilities.get(name, False) is not True:
                missing.append(name)

        if len(missing) == 0:
            return

        err_msg_lines = [
            "UefiSshHostTransport.connect: one or more required capabilities are absent.",
            "HOST: {}".format(host),
            "REQUIRED: {}".format(", ".join(required)),
            "MISSING:",
        ]
        for name in missing:
            subject = CAPABILITY_SUBJECTS.get(name, name)
            err_msg_lines.append("    {}: {}".format(name, subject))

        err_msg_lines.append("PROBE COMMANDS:")
        for name in missing:
            err_msg_lines.append("    {}: {!r}".format(name, PROBE_COMMANDS[name]))

        err_msg = os.linesep.join(err_msg_lines)

        self._logger.warning(
            "UefiSshHostTransport.connect: required capabilities missing host={} missing={}".format(
                host, missing
            )
        )

        raise UefiTransportError(err_msg, details=list(err_msg_lines[1:])) from None


__all__ = [
    "CAPABILITY_HAS_EFIBOOTMGR",
    "CAPABILITY_HAS_EFIVAR",
    "CAPABILITY_HAS_EFIVARFS",
    "CAPABILITY_HAS_EFIVARFS_RW",
    "CAPABILITY_HAS_FWUPDMGR",
    "CAPABILITY_SUBJECTS",
    "KNOWN_CAPABILITIES",
    "PROBE_COMMANDS",
    "SUDO_NONINTERACTIVE_PREFIX",
    "SUDO_PROMPT_SENTINELS",
    "TRANSPORT_KIND",
    "UefiSshHostTransport",
]
