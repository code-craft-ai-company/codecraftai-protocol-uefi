"""
    .. module:: uefiagent
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Module containing the :class:`UefiAgent` class, the PRIMARY
               user-facing entry point into the layer-4 UEFI interop
               machinery.  A :class:`UefiAgent` binds a ``host``, a
               ``primary_credential``, a ``transport_kind``, an optional
               ``users`` dictionary, optional jump-host parameters, an
               optional correlation ``called_id``, and an instance default
               :class:`UefiCmdAspects` to the protocol layer.  Every public
               method (``run_cmd``, ``reboot``, ``open_session``) opens a
               fresh :class:`UefiClient` per call, performs the operation,
               and closes the client before returning — the same one-shot
               pattern used by :class:`automojo.protocols.ssh.sshagent.SshAgent`.

               :class:`UefiAgent` extends :class:`UefiBase` and implements
               the abstract methods ``run_cmd``, ``reboot``, and
               ``open_session``.  The SSH-to-host kind additionally composes
               an :class:`automojo.protocols.ssh.sshagent.SshAgent` instance
               that the caller supplies; the agent holds that reference
               opaquely so this module never unconditionally imports the
               SSH package.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Automation Mojo LLC"
__credits__ = []


import os
import time

from typing import Any, Dict, Optional, Sequence, Tuple, Union

from automojo.errors.exceptions import ConfigurationError

from automojo.protocols.uefi.uefiaspects import (
    DEFAULT_CMD_ASPECTS,
    UefiCmdAspects,
    VALID_TRANSPORT_KINDS,
    resolve_aspects,
)
from automojo.protocols.uefi.uefibase import UefiBase
from automojo.protocols.uefi.ueficlient import (
    UefiClient,
    UefiHostOsClient,
    UefiRedfishClient,
    UefiShellClient,
)
from automojo.protocols.uefi.ueficonst import UefiJumpParams
from automojo.protocols.uefi.uefiipmicredential import UefiIpmiCredential
from automojo.protocols.uefi.uefiipmisoltransport import UefiIpmiSolTransport
from automojo.protocols.uefi.uefiredfishcredential import UefiRedfishCredential
from automojo.protocols.uefi.uefiredfishtransport import UefiRedfishTransport
from automojo.protocols.uefi.uefiserialcredential import UefiSerialCredential
from automojo.protocols.uefi.uefiserialtransport import UefiSerialTransport
from automojo.protocols.uefi.uefisshhosttransport import UefiSshHostTransport


# ---------------------------------------------------------------------------
# Per-transport reboot defaults (reboot-specific constants kept local to the
# module so that a misconfigured aspects object does not silently change the
# reboot-reconnect cadence).
# ---------------------------------------------------------------------------

DEFAULT_REBOOT_COMPLETION_TIMEOUT: int = 300
DEFAULT_REBOOT_RETRY_INTERVAL: int = 10
DEFAULT_REBOOT_GRACE_PERIOD: int = 30

DEFAULT_REDFISH_RESET_PATH_TEMPLATE: str = (
    "/redfish/v1/Systems/{system_id}/Actions/ComputerSystem.Reset"
)
DEFAULT_REDFISH_RESET_BODY: str = '{"ResetType": "ForceRestart"}'
DEFAULT_REDFISH_RESET_COMMAND: str = (
    "POST " + DEFAULT_REDFISH_RESET_PATH_TEMPLATE.format(system_id="Self")
    + " " + DEFAULT_REDFISH_RESET_BODY
)

SHELL_RESET_COMMAND: str = "reset"
SSH_HOST_RESET_COMMAND: str = "reboot"


# ---------------------------------------------------------------------------
# Credential-to-transport-kind validation table
# ---------------------------------------------------------------------------

_CREDENTIAL_TYPE_BY_KIND: Dict[str, type] = {
    "serial": UefiSerialCredential,
    "redfish": UefiRedfishCredential,
    "ipmi-sol": UefiIpmiCredential,
}


class UefiAgent(UefiBase):
    """
        The :class:`UefiAgent` class is the one-shot, primary user-facing
        entry point into the UEFI interop library.  It binds a target
        ``host``, a ``primary_credential``, a ``transport_kind``, an
        optional ``users`` role-to-credential dictionary, an optional
        :class:`UefiJumpParams` jump descriptor, an optional correlation
        ``called_id``, and a default :class:`UefiCmdAspects` into a single
        object.

        Every public method (``run_cmd``, ``reboot``, ``open_session``)
        opens a fresh :class:`UefiClient` per call, performs the
        operation, and closes the client before returning — the same
        one-shot pattern used by
        :class:`automojo.protocols.ssh.sshagent.SshAgent`.

        :param host: Hostname or IP address of the UEFI target.
        :param primary_credential: One of
                                   :class:`UefiSerialCredential`,
                                   :class:`UefiRedfishCredential`,
                                   :class:`UefiIpmiCredential`; for
                                   ``transport_kind="ssh-host"`` the value
                                   is advisory and ``ssh_agent`` must also
                                   be supplied.
        :param transport_kind: One of ``"serial"``, ``"redfish"``,
                               ``"ssh-host"``, ``"ipmi-sol"``.
        :param users: Optional role-to-credential dictionary.  When a call
                      passes ``user=...`` the agent looks the role up in
                      this mapping and uses the returned credential in
                      place of ``primary_credential`` for that call only.
        :param jump: Optional :class:`UefiJumpParams` for reaching a
                     target on a private management network.
        :param called_id: Friendly correlation identifier included in
                          log lines as ``[called_id]``.
        :param aspects: Default :class:`UefiCmdAspects`; per-call overrides
                        are supplied via each method's ``aspects``
                        parameter.
        :param ssh_agent: For ``transport_kind="ssh-host"`` ONLY — the
                          fully-constructed
                          :class:`automojo.protocols.ssh.sshagent.SshAgent`
                          that the composed
                          :class:`UefiSshHostTransport` will drive.  The
                          agent holds the reference opaquely so this
                          module never unconditionally imports the SSH
                          package.

        :raises: ConfigurationError
    """

    def __init__(self, host: str,
                       primary_credential: Any,
                       transport_kind: str,
                       users: Optional[Dict[str, Any]] = None,
                       jump: Optional[UefiJumpParams] = None,
                       called_id: Optional[str] = None,
                       aspects: UefiCmdAspects = DEFAULT_CMD_ASPECTS,
                       ssh_agent: Optional[object] = None) -> None:
        """
            Initialize the :class:`UefiAgent`.

            :param host: Hostname or IP address of the UEFI target.
            :param primary_credential: Primary credential bundle matching
                                       ``transport_kind``.
            :param transport_kind: Transport kind selector.
            :param users: Optional role-to-credential dictionary.
            :param jump: Optional :class:`UefiJumpParams`.
            :param called_id: Friendly correlation identifier.
            :param aspects: Default :class:`UefiCmdAspects` for this agent.
            :param ssh_agent: For ``ssh-host`` kind ONLY — the
                              fully-constructed SSH agent.

            :raises: ConfigurationError
        """
        # Validate transport_kind first so the UefiBase constructor receives a
        # known value.  The uefiaspects module exposes the authoritative list.
        if transport_kind not in VALID_TRANSPORT_KINDS:
            err_msg_lines = [
                "UefiAgent: 'transport_kind' is not a known transport kind.",
                "GOT: value={!r} type={}".format(transport_kind, type(transport_kind).__name__),
                "ALLOWED: {}".format(", ".join(VALID_TRANSPORT_KINDS)),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg) from None

        # Credential type must match transport_kind.  The ssh-host kind is
        # special-cased because its primary_credential is advisory — the
        # real driver is the SshAgent the caller supplies.
        if transport_kind == "ssh-host":
            if ssh_agent is None:
                err_msg_lines = [
                    "UefiAgent: 'ssh-host' transport requires an 'ssh_agent' argument.",
                    "TRANSPORT_KIND: {!r}".format(transport_kind),
                    "SSH_AGENT: None",
                    "HINT: pass a fully-constructed automojo.protocols.ssh.sshagent.SshAgent instance.",
                ]
                err_msg = os.linesep.join(err_msg_lines)
                raise ConfigurationError(err_msg) from None
        else:
            expected_cred_type = _CREDENTIAL_TYPE_BY_KIND[transport_kind]
            if not isinstance(primary_credential, expected_cred_type):
                err_msg_lines = [
                    "UefiAgent: 'primary_credential' does not match 'transport_kind'.",
                    "TRANSPORT_KIND: {!r}".format(transport_kind),
                    "EXPECTED_CREDENTIAL: {}".format(expected_cred_type.__name__),
                    "GOT_CREDENTIAL: value={!r} type={}".format(
                        primary_credential, type(primary_credential).__name__
                    ),
                ]
                err_msg = os.linesep.join(err_msg_lines)
                raise ConfigurationError(err_msg) from None

        UefiBase.__init__(
            self,
            host=host,
            primary_credential=primary_credential,
            transport_kind=transport_kind,
            jump=jump,
            users=users,
            called_id=called_id,
            aspects=aspects,
        )

        # Held-opaque reference to the SshAgent, if any.  We deliberately
        # avoid isinstance-checking against SshAgent here — the SSH package
        # may not be importable in every deployment, and the user-facing
        # contract already stated the caller passes a ready-made instance.
        self._ssh_agent: Optional[object] = ssh_agent

        self._logger.debug(
            "uefi.agent: constructed host=%s transport_kind=%s called_id=%s",
            host, transport_kind, called_id,
        )
        return

    # ------------------------------------------------------------------
    # Context manager protocol — lazy by design, no eager connect
    # ------------------------------------------------------------------

    def __enter__(self) -> "UefiAgent":
        """
            Context-manager entry.  Agents are lazy — no eager connect.

            :returns: ``self``
        """
        rtnval = self
        return rtnval

    def __exit__(self, ex_type: Optional[type],
                       ex_val: Optional[BaseException],
                       ex_tb: Optional[object]) -> bool:
        """
            Context-manager exit.  Does not suppress exceptions.

            :param ex_type: Exception type or ``None``.
            :param ex_val: Exception value or ``None``.
            :param ex_tb: Traceback or ``None``.

            :returns: Always ``False`` — exceptions are never suppressed.
        """
        rtnval = False
        return rtnval

    # ------------------------------------------------------------------
    # Public methods — one-shot dispatch
    # ------------------------------------------------------------------

    def run_cmd(self, command: str,
                      exp_status: Union[int, Sequence[int]] = 0,
                      user: Optional[str] = None,
                      aspects: Optional[UefiCmdAspects] = None) -> Tuple[int, str, str]:
        """
            Run a single command against the UEFI target.  Opens a fresh
            :class:`UefiClient`, dispatches the command through
            :meth:`UefiBase._run`, and closes the client before returning.

            :param command: The command string to dispatch.  Semantics are
                            transport-specific (UEFI Shell for serial /
                            ipmi-sol; Redfish verb-line for redfish; host
                            shell for ssh-host).
            :param exp_status: Integer or sequence of integers denoting
                               the acceptable return status set.
            :param user: Optional role name; when provided the agent
                         resolves the credential via ``self._users``.
            :param aspects: Optional per-call :class:`UefiCmdAspects`.
                            Falls back to the instance default when
                            ``None``.

            :returns: A ``(status, stdout, stderr)`` three-tuple.
            :raises: ConfigurationError, SemanticError, ConnectionError,
                     TimeoutError
        """
        effective = resolve_aspects(passed=aspects, instance_default=self._aspects)

        client: Optional[UefiClient] = None
        status: Optional[int] = None
        stdout: Optional[str] = None
        stderr: Optional[str] = None
        try:
            client = self._create_client(user=user, aspects=effective)
            status, stdout, stderr = self._run(
                client=client,
                command=command,
                exp_status=exp_status,
                user=user,
                aspects=effective,
            )
        finally:
            if client is not None:
                try:
                    client.close()
                except BaseException as cerr:
                    self._logger.warning(
                        "uefi.agent: run_cmd client close raised; suppressed. EXCEPTION=%r",
                        cerr,
                    )

        rtnval = (status, stdout, stderr)
        return rtnval

    def reboot(self, aspects: Optional[UefiCmdAspects] = None) -> None:
        """
            Reboot the UEFI target and wait for it to return.

            Mirrors :meth:`automojo.protocols.ssh.sshagent.SshAgent.reboot`
            at ``sshagent.py:132-184``.  Issues the transport-native reset
            command, swallows any connection-related exception observed
            during the reset dispatch (expected — the target is going
            down), sleeps a grace period, then polls
            :meth:`UefiBase.verify_connectivity` every
            ``DEFAULT_REBOOT_RETRY_INTERVAL`` seconds until
            ``aspects.completion_timeout`` elapses.  On timeout raises
            :class:`TimeoutError` with a multiline details block.

            :param aspects: Optional per-call :class:`UefiCmdAspects`;
                            when ``None`` the instance default is used.

            :raises: TimeoutError
        """
        reboot_timeout = DEFAULT_REBOOT_COMPLETION_TIMEOUT
        if aspects is not None and hasattr(aspects, "completion_timeout"):
            reboot_timeout = aspects.completion_timeout

        retry_interval = DEFAULT_REBOOT_RETRY_INTERVAL
        grace_period = DEFAULT_REBOOT_GRACE_PERIOD

        self._logger.info(
            "uefi.agent: reboot begin host=%s transport_kind=%s completion_timeout=%s",
            self._host, self._transport_kind, reboot_timeout,
        )

        reset_command = self._resolve_reset_command()
        begin = time.time()

        try:
            status, stdout, stderr = self.run_cmd(
                command=reset_command,
                aspects=aspects,
            )
            # Log-only; a clean return here is unusual but possible for
            # ssh-host targets that background the reboot.
            self._logger.debug(
                "uefi.agent: reboot command returned cleanly status=%s",
                status,
            )
        except (ConnectionError, TimeoutError, OSError, EOFError) as cerr:
            # These are the expected shapes — the target is going down
            # while we are trying to dispatch.  Mirrors
            # sshagent.py:143-145.
            self._logger.debug(
                "uefi.agent: expected connection error during reboot: %r",
                cerr,
            )
        except KeyboardInterrupt:
            raise
        except SystemExit:
            raise
        except Exception as cerr:
            self._logger.warning(
                "uefi.agent: unexpected exception during reboot command: %r",
                cerr,
            )

        # Wait a grace period for the target to drop before polling.
        time.sleep(grace_period)

        end_time = begin + reboot_timeout
        last_exception: Optional[BaseException] = None
        while time.time() < end_time:
            try:
                connected = self.verify_connectivity()
                if connected:
                    elapsed_ms = int((time.time() - begin) * 1000)
                    self._logger.info(
                        "uefi.agent: reboot complete host=%s elapsed_ms=%d",
                        self._host, elapsed_ms,
                    )
                    return
            except BaseException as cerr:
                last_exception = cerr
            time.sleep(retry_interval)

        elapsed_ms = int((time.time() - begin) * 1000)
        err_msg_lines = [
            "UefiAgent.reboot: target did not return within completion_timeout.",
            "HOST: {}".format(self._host),
            "TRANSPORT: {}".format(self._transport_kind),
            "COMPLETION_TIMEOUT: {}s".format(reboot_timeout),
            "ELAPSED_MS: {}".format(elapsed_ms),
            "LAST_ERR: {!r}".format(last_exception),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        self._logger.error(
            "uefi.agent: reboot timeout host=%s elapsed_ms=%d last_err=%r",
            self._host, elapsed_ms, last_exception,
        )
        raise TimeoutError(err_msg) from last_exception

    def open_session(self, user: Optional[str] = None,
                           aspects: Optional[UefiCmdAspects] = None,
                           basis_session: Optional["UefiSession"] = None) -> "UefiSession":
        """
            Open a new :class:`UefiSession` bound to this agent's host,
            credential, transport kind, jump, and aspects.  Parallels
            :meth:`automojo.protocols.ssh.sshagent.SshAgent.open_session`.

            :param user: Optional role name; when provided the session's
                         primary credential is resolved via
                         ``self._users``.
            :param aspects: Optional per-session :class:`UefiCmdAspects`;
                            falls back to the instance default when
                            ``None``.
            :param basis_session: Optional basis session whose parameters
                                  are cloned (mirroring
                                  ``sshagent.py:87-96``).

            :returns: A newly-constructed :class:`UefiSession`.
            :raises: ConfigurationError
        """
        # Lazy import so that uefisession.py may import uefiagent.py and
        # uefibase.py at module load without a circular import chain.
        from automojo.protocols.uefi.uefisession import UefiSession  # type: ignore

        effective = aspects if aspects is not None else self._aspects

        session_credential = self._primary_credential
        if user is not None:
            if self._users is None or len(self._users) == 0:
                err_msg_lines = [
                    "UefiAgent.open_session: 'user' was provided but no 'users' dict was supplied to the agent.",
                    "USER: {!r}".format(user),
                    "HINT: pass a 'users' dict at UefiAgent construction time.",
                ]
                err_msg = os.linesep.join(err_msg_lines)
                raise ConfigurationError(err_msg) from None

            if user not in self._users:
                err_msg_lines = [
                    "UefiAgent.open_session: 'user' role not found in agent users dict.",
                    "USER: {!r}".format(user),
                    "AVAILABLE: {}".format(sorted(self._users.keys())),
                ]
                err_msg = os.linesep.join(err_msg_lines)
                raise ConfigurationError(err_msg) from None

            session_credential = self._users[user]

        if basis_session is not None:
            session = UefiSession(
                host=basis_session._host,
                primary_credential=session_credential,
                transport_kind=basis_session._transport_kind,
                users=basis_session._users,
                jump=basis_session._jump,
                called_id=basis_session._called_id,
                aspects=effective,
                basis_session=basis_session,
                ssh_agent=self._ssh_agent,
            )
        else:
            session = UefiSession(
                host=self._host,
                primary_credential=session_credential,
                transport_kind=self._transport_kind,
                users=self._users,
                jump=self._jump,
                called_id=self._called_id,
                aspects=effective,
                ssh_agent=self._ssh_agent,
            )

        self._logger.info(
            "uefi.agent: session opened host=%s user=%s basis_session=%s",
            self._host, user, basis_session is not None,
        )
        return session

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _create_client(self, user: Optional[str] = None,
                             aspects: Optional[UefiCmdAspects] = None) -> UefiClient:
        """
            Build a fresh :class:`UefiClient` appropriate for this
            agent's ``transport_kind``.  Dispatches to the correct
            transport constructor, wraps the transport in the matching
            client subclass, opens the underlying transport via
            ``transport.connect(aspects=...)``, and returns the client.

            Ownership of the returned client belongs to the caller;
            the caller MUST invoke :meth:`UefiClient.close` when done.

            :param user: Optional role name used to resolve the
                         credential via ``self._users``.
            :param aspects: Optional :class:`UefiCmdAspects`; when
                            ``None`` the instance default is used.

            :returns: A connected :class:`UefiClient` subclass instance.
            :raises: ConfigurationError
        """
        effective = resolve_aspects(passed=aspects, instance_default=self._aspects)

        credential = self._resolve_credential_for_user(user=user)

        if self._transport_kind == "serial":
            transport = UefiSerialTransport(
                credential=credential,
                aspects=effective,
            )
            client: UefiClient = UefiShellClient(
                transport=transport,
                aspects=effective,
            )
        elif self._transport_kind == "ipmi-sol":
            transport = UefiIpmiSolTransport(
                credential=credential,
                aspects=effective,
            )
            client = UefiShellClient(
                transport=transport,
                aspects=effective,
            )
        elif self._transport_kind == "redfish":
            transport = UefiRedfishTransport(
                credential=credential,
                aspects=effective,
            )
            client = UefiRedfishClient(
                transport=transport,
                aspects=effective,
            )
        elif self._transport_kind == "ssh-host":
            transport = UefiSshHostTransport(
                ssh_agent=self._ssh_agent,
                aspects=effective,
            )
            client = UefiHostOsClient(
                transport=transport,
                aspects=effective,
            )
        else:
            err_msg_lines = [
                "UefiAgent._create_client: unknown transport_kind.",
                "TRANSPORT_KIND: {!r}".format(self._transport_kind),
                "ALLOWED: {}".format(", ".join(VALID_TRANSPORT_KINDS)),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg) from None

        # Open the underlying transport now so the caller receives a client
        # that is ready to execute.  The client's close() method calls
        # transport.disconnect() so no ad-hoc cleanup is required on error.
        try:
            client._transport.connect(aspects=effective)
        except BaseException:
            try:
                client.close()
            except BaseException as derr:
                self._logger.warning(
                    "uefi.agent: _create_client connect failed, subsequent close raised; suppressed. EXCEPTION=%r",
                    derr,
                )
            raise

        return client

    def _resolve_credential_for_user(self, user: Optional[str]) -> Any:
        """
            Resolve the credential to use for a call.  When ``user`` is
            ``None`` the primary credential is returned.  When ``user``
            is non-None the agent's ``users`` dictionary is consulted.

            :param user: Optional role name.

            :returns: The resolved credential object.  For the ssh-host
                      kind this may be the primary (advisory) credential;
                      the concrete transport is driven by
                      :attr:`_ssh_agent`.
            :raises: ConfigurationError
        """
        if user is None:
            rtnval = self._primary_credential
            return rtnval

        if self._users is None or len(self._users) == 0:
            err_msg_lines = [
                "UefiAgent: 'user' was provided but no 'users' dict was supplied to the agent.",
                "USER: {!r}".format(user),
                "HINT: pass a 'users' dict at UefiAgent construction time.",
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg) from None

        if user not in self._users:
            err_msg_lines = [
                "UefiAgent: 'user' role not found in agent users dict.",
                "USER: {!r}".format(user),
                "AVAILABLE: {}".format(sorted(self._users.keys())),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg) from None

        rtnval = self._users[user]
        return rtnval

    def _resolve_reset_command(self) -> str:
        """
            Resolve the transport-native reset command used by
            :meth:`reboot`.

            :returns: The command string to dispatch through
                      :meth:`run_cmd`.
            :raises: ConfigurationError
        """
        if self._transport_kind in ("serial", "ipmi-sol"):
            rtnval = SHELL_RESET_COMMAND
            return rtnval

        if self._transport_kind == "redfish":
            rtnval = DEFAULT_REDFISH_RESET_COMMAND
            return rtnval

        if self._transport_kind == "ssh-host":
            rtnval = SSH_HOST_RESET_COMMAND
            return rtnval

        err_msg_lines = [
            "UefiAgent._resolve_reset_command: unknown transport_kind.",
            "TRANSPORT_KIND: {!r}".format(self._transport_kind),
            "ALLOWED: {}".format(", ".join(VALID_TRANSPORT_KINDS)),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise ConfigurationError(err_msg) from None


__all__ = [
    "DEFAULT_REBOOT_COMPLETION_TIMEOUT",
    "DEFAULT_REBOOT_GRACE_PERIOD",
    "DEFAULT_REBOOT_RETRY_INTERVAL",
    "DEFAULT_REDFISH_RESET_COMMAND",
    "SHELL_RESET_COMMAND",
    "SSH_HOST_RESET_COMMAND",
    "UefiAgent",
]
