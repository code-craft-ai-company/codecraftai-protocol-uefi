"""
    .. module:: uefisession
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Module containing the :class:`UefiSession` persistent-channel
               wrapper for UEFI interop workloads.  The session holds a
               single :class:`UefiClient` (and its associated
               :class:`UefiChannel`) open for the duration of a ``with``
               block so that callers may issue many commands against a
               shared transport context without paying the per-call
               connection-establishment cost.  Long-running loops are
               driven by the :data:`ActionPattern.DO_UNTIL_CONNECTION_FAILURE`
               pattern; other patterns raise :class:`SemanticError`.  The
               session preserves environment-like state (current map alias,
               current working directory, arbitrary caller-registered
               actions) across automatic reconnects via a
               ``_replay_actions`` list.

               The class is a UEFI-flavored port of :class:`SshSession`
               (``codecraftai-protocol-ssh``).  The ``basis_session``
               parameter clones connection parameters of an existing
               session so a derived session can reuse the same
               :class:`UefiClient` and credentials.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Code Craft AI LLC"
__credits__ = []


import os
import time

from types import TracebackType
from typing import Callable, Dict, List, Optional, Sequence, Tuple, Union

from ccai.errors.exceptions import (
    ConfigurationError,
    SemanticError,
    UnExpectedSessionClosureError,
)

from ccai.protocols.uefi.uefiaspects import (
    ActionPattern,
    DEFAULT_CMD_ASPECTS,
    UefiCmdAspects,
    resolve_aspects,
)
from ccai.protocols.uefi.ueficlient import UefiClient
from ccai.protocols.uefi.uefibase import UefiBase, default_client_factory
from ccai.protocols.uefi.uefiexceptions import UefiError
from ccai.protocols.uefi.uefilogging import (
    UEFI_RESET_CHANNEL,
    UEFI_TRANSPORT_CHANNEL,
    get_uefi_logger,
)


# ---------------------------------------------------------------------------
# Reboot-loop constants (parallel the UefiAgent reboot wait envelope)
# ---------------------------------------------------------------------------

DEFAULT_REBOOT_COMPLETION_TIMEOUT: float = 300.0
DEFAULT_REBOOT_COMPLETION_INTERVAL: float = 10.0
DEFAULT_REBOOT_GRACE_PERIOD: float = 30.0


# ---------------------------------------------------------------------------
# Session-supported ActionPattern set
# ---------------------------------------------------------------------------

SESSION_SUPPORTED_PATTERNS: Tuple[ActionPattern, ...] = (
    ActionPattern.SINGLE_CONNECTED_CALL,
    ActionPattern.DO_UNTIL_CONNECTION_FAILURE,
)

SESSION_UNSUPPORTED_PATTERNS: Tuple[ActionPattern, ...] = (
    ActionPattern.SINGLE_CALL,
    ActionPattern.DO_UNTIL_SUCCESS,
    ActionPattern.DO_WHILE_SUCCESS,
)


# ---------------------------------------------------------------------------
# UefiSession
# ---------------------------------------------------------------------------

class UefiSession(UefiBase):
    """
        Persistent-channel UEFI session.  Holds one :class:`UefiClient`
        (and the channel it produces) open for the lifetime of a ``with``
        block; reconnects automatically on allowed-connection exceptions
        when the caller drives the session with
        :data:`ActionPattern.DO_UNTIL_CONNECTION_FAILURE`; replays
        recorded caller actions after every successful reconnect.

        The class is a UEFI-flavored port of :class:`SshSession`.
        Whereas :class:`UefiAgent` supports the three non-session action
        patterns, :class:`UefiSession` supports the two connected-call
        patterns; this inversion mirrors ``sshbase.py:625-627`` with the
        sides swapped.

        Instance variables initialized in :meth:`__init__`:

        * ``_basis_session`` — optional upstream session whose
          :class:`UefiClient` is shared by this one.
        * ``_interactive`` — flag reserved for future interactive-shell
          framing; carried through for parity with :class:`SshSession`.
        * ``_client`` — the persistent :class:`UefiClient`, ``None`` until
          :meth:`__enter__` succeeds.
        * ``_channel`` — the persistent channel produced by the client;
          ``None`` until :meth:`__enter__` succeeds.
        * ``_replay_actions`` — list of zero-argument callables the
          caller has registered for re-application after reconnect.
        * ``_ssh_agent`` — optional pre-built SSH agent handle used when
          the transport dispatches over SSH.
        * ``_reconnect_count`` — monotonic counter of session-level
          reconnect events.
        * ``_enter_time`` — wall-clock timestamp captured on
          :meth:`__enter__`, used for the ``session.exit`` log line.

        :param host: Target host name or address.
        :param primary_credential: Primary credential for authentication.
        :param transport_kind: One of the transport-kind literals (e.g.
                               ``"serial"``, ``"redfish"``, ``"ssh-host"``,
                               ``"ipmi-sol"``).
        :param users: Optional map of role name to additional credentials.
        :param jump: Optional jump-host descriptor.
        :param called_id: Friendly identifier used in log prefixes.
        :param aspects: Default :class:`UefiCmdAspects` applied when a
                        call omits the ``aspects`` argument.
        :param basis_session: Optional :class:`UefiSession` whose
                              connection parameters (host, credential,
                              transport kind, users, jump, called_id,
                              ``ssh_agent``) are inherited unless the
                              caller overrides the individual field.
                              When supplied, the derived session
                              reuses the basis session's
                              :class:`UefiClient` rather than opening a
                              new one.
        :param interactive: Reserved flag mirroring :class:`SshSession`;
                            currently stored but not acted upon here.
        :param ssh_agent: Optional pre-built SSH agent handle used by
                          the SSH-host transport.  Passed through to the
                          transport factory.
    """

    def __init__(self, host: str,
                       primary_credential: object,
                       transport_kind: str,
                       users: Optional[Dict[str, object]] = None,
                       jump: Optional[object] = None,
                       called_id: Optional[str] = None,
                       aspects: UefiCmdAspects = DEFAULT_CMD_ASPECTS,
                       basis_session: Optional["UefiSession"] = None,
                       interactive: bool = False,
                       ssh_agent: Optional[object] = None) -> None:
        """
            Construct the session.  When ``basis_session`` is supplied the
            session's host / credential / transport_kind / users / jump /
            called_id / ssh_agent default to the basis session's values;
            explicit arguments override per-field.

            :param host: See class docstring.
            :param primary_credential: See class docstring.
            :param transport_kind: See class docstring.
            :param users: See class docstring.
            :param jump: See class docstring.
            :param called_id: See class docstring.
            :param aspects: See class docstring.
            :param basis_session: See class docstring.
            :param interactive: See class docstring.
            :param ssh_agent: See class docstring.

            :raises: ConfigurationError
        """
        # ------------------------------------------------------------------
        # Basis-session defaulting — pull any field the caller did not
        # override from the basis session.
        # ------------------------------------------------------------------
        effective_host = host
        effective_credential = primary_credential
        effective_transport_kind = transport_kind
        effective_users = users
        effective_jump = jump
        effective_called_id = called_id
        effective_ssh_agent = ssh_agent

        if basis_session is not None:
            if effective_host is None:
                effective_host = basis_session._host
            if effective_credential is None:
                effective_credential = basis_session._primary_credential
            if effective_transport_kind is None:
                effective_transport_kind = basis_session._transport_kind
            if effective_users is None:
                effective_users = basis_session._users
            if effective_jump is None:
                effective_jump = basis_session._jump
            if effective_called_id is None:
                effective_called_id = basis_session._called_id
            if effective_ssh_agent is None:
                effective_ssh_agent = basis_session._ssh_agent

        # ------------------------------------------------------------------
        # UefiBase initialization.
        # ------------------------------------------------------------------
        UefiBase.__init__(
            self,
            host=effective_host,
            primary_credential=effective_credential,
            transport_kind=effective_transport_kind,
            jump=effective_jump,
            users=effective_users,
            called_id=effective_called_id,
            aspects=aspects,
        )

        # ------------------------------------------------------------------
        # Session-specific instance state — every variable used anywhere
        # in the class body is initialized here per the repository
        # coding standards.
        # ------------------------------------------------------------------
        self._basis_session: Optional["UefiSession"] = basis_session
        self._interactive: bool = interactive
        self._ssh_agent: Optional[object] = effective_ssh_agent
        self._client: Optional[UefiClient] = None
        self._channel: Optional[object] = None
        self._reconnect_count: int = 0
        self._enter_time: Optional[float] = None

        if basis_session is not None:
            self._replay_actions: List[Callable[[], None]] = list(basis_session._replay_actions)
        else:
            self._replay_actions = []

        # Dedicated logger for session transitions; routed to the
        # transport channel where connection-level events belong.
        self._session_logger = get_uefi_logger(UEFI_TRANSPORT_CHANNEL)
        self._reset_logger = get_uefi_logger(UEFI_RESET_CHANNEL)

        return

    # -----------------------------------------------------------------------
    # Read-only properties
    # -----------------------------------------------------------------------

    @property
    def basis_session(self) -> Optional["UefiSession"]:
        """
            :returns: The basis session supplied at construction, or
                      ``None`` when this session owns its client.
        """
        rtnval = self._basis_session
        return rtnval

    @property
    def client(self) -> Optional[UefiClient]:
        """
            :returns: The persistent :class:`UefiClient`, or ``None`` when
                      the session has not yet been entered.
        """
        rtnval = self._client
        return rtnval

    @property
    def channel(self) -> Optional[object]:
        """
            :returns: The persistent channel, or ``None`` when the session
                      has not yet been entered.
        """
        rtnval = self._channel
        return rtnval

    @property
    def connected(self) -> bool:
        """
            :returns: ``True`` when both the persistent client and channel
                      are present and the client reports connected.
        """
        connected_flag = False
        if self._client is not None and self._channel is not None:
            try:
                connected_flag = bool(self._client.connected)
            except BaseException:
                connected_flag = False
        return connected_flag

    @property
    def reconnect_count(self) -> int:
        """
            :returns: The number of automatic reconnect events observed
                      since construction.
        """
        rtnval = self._reconnect_count
        return rtnval

    @property
    def interactive(self) -> bool:
        """
            :returns: The flag carried for parity with :class:`SshSession`.
        """
        rtnval = self._interactive
        return rtnval

    # -----------------------------------------------------------------------
    # Context manager
    # -----------------------------------------------------------------------

    def __enter__(self) -> "UefiSession":
        """
            Open (or reuse, when ``basis_session`` is set) the persistent
            :class:`UefiClient`, then open a fresh channel on it and
            return ``self``.

            :returns: ``self``.
            :raises: ConnectionError, ConfigurationError, RuntimeError
        """
        self._enter_time = time.time()

        client = self._create_client()
        self._client = client

        channel = client.open_channel()
        self._channel = channel

        self._session_logger.info(
            "uefi.transport: session enter host=%s transport_kind=%s called_id=%s",
            self._host, self._transport_kind, self._called_id,
        )

        return self

    def __exit__(self, ex_type: Optional[type],
                       ex_val: Optional[BaseException],
                       ex_tb: Optional[TracebackType]) -> bool:
        """
            Close the persistent channel (then the client, when this
            session owns it).  Any close-time exception is logged at
            WARNING and suppressed; the in-block exception, if any, is
            NOT suppressed.

            :param ex_type: Exception type from the ``with`` block.
            :param ex_val: Exception instance from the ``with`` block.
            :param ex_tb: Traceback from the ``with`` block.

            :returns: ``False`` so any in-block exception propagates.
        """
        self.close()

        if self._enter_time is not None:
            elapsed_ms = int((time.time() - self._enter_time) * 1000)
        else:
            elapsed_ms = 0

        self._session_logger.info(
            "uefi.transport: session exit host=%s elapsed_ms=%d",
            self._host, elapsed_ms,
        )

        handled = False
        return handled

    # -----------------------------------------------------------------------
    # _create_client — persistent-client factory with basis-session reuse
    # -----------------------------------------------------------------------

    def _create_client(self, user: Optional[str] = None,
                             aspects: Optional[UefiCmdAspects] = None) -> UefiClient:
        """
            Return the persistent :class:`UefiClient` used by this
            session.  The first call builds a new client (or adopts the
            basis session's); subsequent calls return the cached value
            when it is still connected.  When the cached client is
            closed or reports not-connected it is discarded and a new
            one is built.

            Dispatch order:

            1. If ``basis_session`` was supplied, adopt its client
               (raising :class:`RuntimeError` when the basis client has
               been released).
            2. If ``self._client`` is already set and reports connected,
               return it.
            3. Otherwise build a fresh transport + client using the
               ``default_client_factory`` from :mod:`uefibase` and
               connect it.

            :param user: Optional user role name; when supplied the
                         matching entry in ``self._users`` is used as the
                         credential rather than the primary credential.
            :param aspects: Optional per-call :class:`UefiCmdAspects`;
                            ``None`` falls back to the instance default.

            :returns: The persistent :class:`UefiClient`.
            :raises: ConfigurationError, RuntimeError, ConnectionError
        """
        resolved = resolve_aspects(aspects, self._aspects)

        # Basis-session path: adopt the upstream client.
        if self._basis_session is not None and user is None:
            basis_client = self._basis_session._client
            if basis_client is None:
                err_msg_lines = [
                    "Basis session's UefiClient is None.",
                    "The basis session has not been entered or has already been closed.",
                    "BASIS_SESSION: {!r}".format(self._basis_session),
                ]
                err_msg = os.linesep.join(err_msg_lines)
                raise RuntimeError(err_msg) from None

            try:
                basis_connected = bool(basis_client.connected)
            except BaseException:
                basis_connected = False

            if not basis_connected:
                err_msg_lines = [
                    "Basis session's UefiClient is no longer connected.",
                    "BASIS_SESSION: {!r}".format(self._basis_session),
                ]
                err_msg = os.linesep.join(err_msg_lines)
                raise RuntimeError(err_msg) from None

            rtnval = basis_client
            return rtnval

        # Cached-and-still-connected path.
        if self._client is not None:
            try:
                cached_connected = bool(self._client.connected)
            except BaseException:
                cached_connected = False

            if cached_connected:
                rtnval = self._client
                return rtnval

        # Resolve the credential.
        credential = self._primary_credential
        if user is not None:
            if self._users is None or user not in self._users:
                available = []
                if self._users is not None:
                    available = sorted(self._users.keys())
                err_msg_lines = [
                    "Specified 'user' is not present in session users dict.",
                    "USER: {}".format(user),
                    "AVAILABLE: {}".format(available),
                ]
                err_msg = os.linesep.join(err_msg_lines)
                raise ConfigurationError(err_msg) from None
            credential = self._users[user]

        # Build the transport via the transport-kind dispatch.
        transport = self._build_transport(credential=credential, aspects=resolved)

        try:
            transport.connect(aspects=resolved)
        except TypeError:
            # Some transports accept no arguments on connect().  Degrade
            # gracefully.
            transport.connect()

        # Build the client via the base-module's default_client_factory.
        client = default_client_factory(
            transport_kind=self._transport_kind,
            transport=transport,
            aspects=resolved,
        )

        self._client = client
        rtnval = client
        return rtnval

    def _build_transport(self, credential: object, aspects: UefiCmdAspects) -> object:
        """
            Build the layer-2 transport matching ``self._transport_kind``.
            Dispatch matches :meth:`UefiAgent._build_transport` in
            spirit; the concrete transport classes are imported lazily
            so the session module has no hard dependency on all four
            transport implementations at import time.

            :param credential: The credential to pass to the transport
                               constructor.
            :param aspects: The effective :class:`UefiCmdAspects`.

            :returns: An :class:`IUefiTransport` instance.
            :raises: ConfigurationError
        """
        kind = self._transport_kind

        if kind == "serial":
            from ccai.protocols.uefi.uefiserialtransport import UefiSerialTransport
            transport = UefiSerialTransport(
                host=self._host,
                credential=credential,
                aspects=aspects,
            )
            return transport

        if kind == "redfish":
            from ccai.protocols.uefi.uefiredfishtransport import UefiRedfishTransport
            transport = UefiRedfishTransport(
                host=self._host,
                credential=credential,
                aspects=aspects,
            )
            return transport

        if kind == "ipmi-sol":
            from ccai.protocols.uefi.uefiipmisoltransport import UefiIpmiSolTransport
            transport = UefiIpmiSolTransport(
                host=self._host,
                credential=credential,
                aspects=aspects,
            )
            return transport

        if kind == "ssh-host":
            from ccai.protocols.uefi.uefisshhosttransport import UefiSshHostTransport
            transport = UefiSshHostTransport(
                host=self._host,
                credential=credential,
                aspects=aspects,
                jump=self._jump,
                ssh_agent=self._ssh_agent,
            )
            return transport

        err_msg_lines = [
            "Unknown transport_kind for UefiSession._build_transport.",
            "GOT: {!r}".format(kind),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise ConfigurationError(err_msg) from None

    # -----------------------------------------------------------------------
    # run_cmd — persistent-channel command dispatch
    # -----------------------------------------------------------------------

    def run_cmd(self, command: str,
                      exp_status: Union[int, Sequence[int]] = 0,
                      user: Optional[str] = None,
                      aspects: Optional[UefiCmdAspects] = None) -> Tuple[int, str, str]:
        """
            Dispatch a command on the persistent channel.

            The session honors only the two connected-call action
            patterns:

            * :data:`ActionPattern.SINGLE_CONNECTED_CALL` — dispatch
              once and return.
            * :data:`ActionPattern.DO_UNTIL_CONNECTION_FAILURE` — loop
              dispatching on the persistent channel until an
              allowed-connection exception exits the loop (this is the
              caller-intended exit signal); the exception is re-raised
              so the caller observes the transition.

            Any other action pattern raises :class:`SemanticError`.

            On :class:`ConnectionError` under a non-loop pattern the
            session attempts exactly one reconnect (closing the old
            channel and opening a fresh one on the same client, or
            rebuilding the client when needed), then re-raises the
            original exception so the caller can react.

            :param command: The command string to dispatch.
            :param exp_status: Integer or sequence of integers accepted
                               as success.
            :param user: Optional user-role override; currently forwarded
                         to the base-class engine which consults the
                         effective aspects for the dispatch.
            :param aspects: Optional per-call :class:`UefiCmdAspects`.

            :returns: ``(status, stdout, stderr)``.
            :raises: SemanticError, ConnectionError, ConfigurationError
        """
        resolved = resolve_aspects(aspects, self._aspects)
        pattern = resolved.action_pattern

        # Guard: session must be entered before commands are issued.
        if self._channel is None or self._client is None:
            # Lazy open — matches the "if not yet open, open it" guidance.
            self._client = self._create_client(user=user, aspects=resolved)
            self._channel = self._client.open_channel()

        # Reject non-session patterns loudly — the inverse of
        # sshbase.py:625-627.
        if pattern in SESSION_UNSUPPORTED_PATTERNS:
            supported_names = [p.name for p in SESSION_SUPPORTED_PATTERNS]
            err_msg_lines = [
                "UefiSession.run_cmd does not support the requested ActionPattern.",
                "Use UefiAgent for one-shot patterns (SINGLE_CALL / DO_UNTIL_SUCCESS /",
                "DO_WHILE_SUCCESS); sessions are reserved for SINGLE_CONNECTED_CALL",
                "and DO_UNTIL_CONNECTION_FAILURE.",
                "PASSED: {}".format(pattern.name),
                "SUPPORTED: [{}]".format(", ".join(supported_names)),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise SemanticError(err_msg) from None

        if pattern not in SESSION_SUPPORTED_PATTERNS:
            err_msg_lines = [
                "UefiSession.run_cmd observed an unknown ActionPattern.",
                "PATTERN: {!r}".format(pattern),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise SemanticError(err_msg) from None

        # DO_UNTIL_CONNECTION_FAILURE — let the base engine loop.  When a
        # ConnectionError propagates out of the engine (the intended exit
        # signal for this pattern) we surface it to the caller unchanged.
        if pattern == ActionPattern.DO_UNTIL_CONNECTION_FAILURE:
            try:
                status, stdout, stderr = self._run_cmd(
                    runner=self._channel,
                    command=command,
                    exp_status=exp_status,
                    user=user,
                    aspects=resolved,
                )
            except ConnectionError:
                # Mark the session as needing reconnect before surfacing
                # the signal.
                self._mark_needs_reconnect()
                raise

            rtnval = (status, stdout, stderr)
            return rtnval

        # SINGLE_CONNECTED_CALL — one dispatch; on ConnectionError try a
        # single reconnect then re-raise the original exception.
        try:
            status, stdout, stderr = self._run_cmd(
                runner=self._channel,
                command=command,
                exp_status=exp_status,
                user=user,
                aspects=resolved,
            )
        except ConnectionError as cerr:
            self._session_logger.warning(
                "uefi.transport: SINGLE_CONNECTED_CALL observed ConnectionError; "
                "attempting one reconnect host=%s err=%r",
                self._host, cerr,
            )
            self._attempt_single_reconnect(trigger=cerr, aspects=resolved)
            raise

        rtnval = (status, stdout, stderr)
        return rtnval

    # -----------------------------------------------------------------------
    # reboot — reset + reconnect + replay
    # -----------------------------------------------------------------------

    def reboot(self, aspects: Optional[UefiCmdAspects] = None) -> None:
        """
            Issue the transport-native reset command, then reconnect the
            persistent channel and replay every action registered via
            :meth:`record_action`.  The wait loop mirrors the
            :class:`UefiAgent` reboot envelope — 300s default timeout,
            10s polling interval, 30s grace period.

            Expected connection-errors during the reset dispatch are
            swallowed at DEBUG since the reset itself frequently drops
            the transport.  Unexpected exceptions are logged at WARNING
            but do not abort the reconnect attempt.

            On successful reconnect the session remains usable; callers
            do NOT need to exit and re-enter the ``with`` block.

            :param aspects: Optional per-call :class:`UefiCmdAspects`.

            :raises: UnExpectedSessionClosureError when the reconnect
                     loop exhausts the timeout without the verify probe
                     succeeding.
        """
        resolved = resolve_aspects(aspects, self._aspects)

        completion_timeout = DEFAULT_REBOOT_COMPLETION_TIMEOUT
        completion_interval = DEFAULT_REBOOT_COMPLETION_INTERVAL
        grace_period = DEFAULT_REBOOT_GRACE_PERIOD

        begin = time.time()

        self._reset_logger.info(
            "uefi.reset: session reboot begin host=%s completion_timeout=%ds",
            self._host, completion_timeout,
        )

        reset_command = self._transport_reset_command()

        # Dispatch the reset directly through the base engine so the
        # session's run_cmd single-reconnect semantics do not fire.  The
        # reset itself is the trigger for the expected transport drop;
        # the reconnect logic lives in the loop below, not in run_cmd.
        try:
            if self._channel is not None:
                self._run_cmd(
                    runner=self._channel,
                    command=reset_command,
                    exp_status=0,
                    user=None,
                    aspects=resolved,
                )
        except (ConnectionError, TimeoutError, OSError, EOFError) as cerr:
            self._reset_logger.debug(
                "uefi.reset: expected connection error during session reboot: %r",
                cerr,
            )
        except KeyboardInterrupt:
            raise
        except SystemExit:
            raise
        except SemanticError:
            # SemanticError must NEVER be caught per the repository
            # coding standards.
            raise
        except Exception as cerr:
            self._reset_logger.warning(
                "uefi.reset: unexpected exception during session reboot: %r",
                cerr,
            )

        # Drop the stale client/channel references before the grace wait
        # so any subsequent accessor sees the session in the post-reboot
        # transition state.
        self._drop_stale_client_and_channel()

        time.sleep(grace_period)

        # Reconnect loop with the same shape as UefiAgent.reboot.
        end_time = begin + completion_timeout
        last_exception: Optional[BaseException] = None
        reconnect_ok = False
        while time.time() < end_time:
            try:
                self._client = self._create_client(aspects=resolved)
                self._channel = self._client.open_channel()
                reconnect_ok = True
                break
            except BaseException as rerr:
                last_exception = rerr
                self._reset_logger.debug(
                    "uefi.reset: reconnect attempt failed host=%s err=%r",
                    self._host, rerr,
                )
            time.sleep(completion_interval)

        if not reconnect_ok:
            elapsed_ms = int((time.time() - begin) * 1000)
            err_msg_lines = [
                "UefiSession.reboot: target did not return within completion_timeout.",
                "HOST: {}".format(self._host),
                "TRANSPORT: {}".format(self._transport_kind),
                "COMPLETION_TIMEOUT: {}s".format(completion_timeout),
                "ELAPSED_MS: {}".format(elapsed_ms),
                "LAST_ERR: {!r}".format(last_exception),
            ]
            err_msg = os.linesep.join(err_msg_lines)

            self._reset_logger.error(
                "uefi.reset: session reboot timeout host=%s elapsed_ms=%d last_err=%r",
                self._host, elapsed_ms, last_exception,
            )

            # Clear state before raising so the caller sees the closed
            # session shape.
            self._client = None
            self._channel = None
            raise UnExpectedSessionClosureError(err_msg) from last_exception

        # Replay recorded actions against the freshly-opened channel.
        self._replay_recorded_actions()

        self._reconnect_count += 1

        elapsed_ms = int((time.time() - begin) * 1000)
        self._reset_logger.info(
            "uefi.reset: session reboot complete host=%s reconnect_count=%d "
            "replay_actions_count=%d elapsed_ms=%d",
            self._host, self._reconnect_count, len(self._replay_actions), elapsed_ms,
        )

        return

    # -----------------------------------------------------------------------
    # record_action / _replay_recorded_actions
    # -----------------------------------------------------------------------

    def record_action(self, action: Callable[[], None]) -> None:
        """
            Append ``action`` to the session's replay list.  Callers
            register state-changing operations (e.g. ``cd FS0:``,
            ``map -r``) here so the session can re-apply them after a
            reconnect.

            :param action: A zero-argument callable.  The callable is
                           invoked with no arguments from
                           :meth:`_replay_recorded_actions`.

            :raises: ConfigurationError
        """
        if action is None:
            err_msg = "'action' must be a callable, not None."
            raise ConfigurationError(err_msg) from None

        if not callable(action):
            err_msg_lines = [
                "'action' must be a zero-argument callable.",
                "GOT: value={!r} type={}".format(action, type(action).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg) from None

        self._replay_actions.append(action)
        return

    def clear_actions(self) -> None:
        """
            Empty the replay-action list.
        """
        self._replay_actions.clear()
        return

    def _replay_recorded_actions(self) -> None:
        """
            Iterate ``self._replay_actions`` in insertion order and
            invoke each callable.  When any action raises, the failure
            is wrapped in :class:`UefiError` with ``action_index`` and a
            ``repr`` of the failing action in ``details``; the original
            exception is chained via ``__cause__``.

            :raises: UefiError
        """
        for idx, action in enumerate(self._replay_actions):
            try:
                action()
            except BaseException as cerr:
                err_msg_lines = [
                    "UefiSession replay action failed.",
                    "ACTION_INDEX: {}".format(idx),
                    "ACTION: {!r}".format(action),
                    "CAUSE: {!r}".format(cerr),
                ]
                err_msg = os.linesep.join(err_msg_lines)

                self._session_logger.error(
                    "uefi.transport: session replay failed action_index=%d action=%r err=%r",
                    idx, action, cerr,
                )

                raise UefiError(err_msg, details=err_msg_lines[1:]) from cerr

        return

    # -----------------------------------------------------------------------
    # close — idempotent teardown
    # -----------------------------------------------------------------------

    def close(self) -> None:
        """
            Close the persistent channel, then the client (only when
            this session owns it — a basis-session-derived session does
            not close the basis's client).  Any error raised by either
            close is logged at WARNING and suppressed.  Subsequent calls
            are no-ops.
        """
        if self._channel is not None:
            try:
                self._channel.close()
            except BaseException as cerr:
                self._session_logger.warning(
                    "uefi.transport: session channel close suppressed host=%s err=%r",
                    self._host, cerr,
                )
            self._channel = None

        if self._client is not None and self._basis_session is None:
            try:
                self._client.close()
            except BaseException as cerr:
                self._session_logger.warning(
                    "uefi.transport: session client close suppressed host=%s err=%r",
                    self._host, cerr,
                )
            self._client = None
        elif self._client is not None and self._basis_session is not None:
            # Basis-session-derived sessions never close the shared client.
            self._client = None

        return

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    def _attempt_single_reconnect(self, trigger: BaseException,
                                        aspects: UefiCmdAspects) -> None:
        """
            Close the current channel and client, then rebuild both.
            Used by the :data:`SINGLE_CONNECTED_CALL` path to attempt
            exactly one reconnect before the original exception is
            propagated.

            :param trigger: The exception that prompted the reconnect
                            attempt.  Stored in the log line so operators
                            can correlate.
            :param aspects: Effective :class:`UefiCmdAspects`.
        """
        self._session_logger.warning(
            "uefi.transport: session single-shot reconnect host=%s trigger=%r",
            self._host, trigger,
        )

        self._drop_stale_client_and_channel()

        try:
            self._client = self._create_client(aspects=aspects)
            self._channel = self._client.open_channel()
            self._reconnect_count += 1
            self._replay_recorded_actions()
        except BaseException as rerr:
            self._session_logger.warning(
                "uefi.transport: session single-shot reconnect failed host=%s err=%r",
                self._host, rerr,
            )
            # Leave the session in the disconnected state.
            self._client = None
            self._channel = None

        return

    def _mark_needs_reconnect(self) -> None:
        """
            Close (best-effort) the persistent channel so the next call
            will rebuild it.  Used when
            :data:`DO_UNTIL_CONNECTION_FAILURE` exits with a
            :class:`ConnectionError` — the intended exit signal — so a
            subsequent call starts fresh.
        """
        if self._channel is not None:
            try:
                self._channel.close()
            except BaseException:
                pass
            self._channel = None
        return

    def _drop_stale_client_and_channel(self) -> None:
        """
            Close (best-effort) the current channel and client and
            release the references.  Used at the top of
            :meth:`reboot` and :meth:`_attempt_single_reconnect` so the
            subsequent build sees a clean slate.
        """
        if self._channel is not None:
            try:
                self._channel.close()
            except BaseException:
                pass
            self._channel = None

        if self._client is not None:
            if self._basis_session is None:
                try:
                    self._client.close()
                except BaseException:
                    pass
            self._client = None

        return

    def _transport_reset_command(self) -> str:
        """
            Dispatch to the transport-native reset command.  Mirrors
            :meth:`UefiAgent._transport_reset_command`.

            :returns: The reset command string.
            :raises: ConfigurationError
        """
        kind = self._transport_kind
        if kind in ("serial", "ipmi-sol"):
            rtnval = "reset -c"
            return rtnval
        if kind == "redfish":
            rtnval = "POST /redfish/v1/Systems/{id}/Actions/ComputerSystem.Reset " \
                    "{\"ResetType\":\"ForceRestart\"}"
            return rtnval
        if kind == "ssh-host":
            rtnval = "reboot"
            return rtnval

        err_msg_lines = [
            "Unknown transport_kind for UefiSession._transport_reset_command.",
            "GOT: {!r}".format(kind),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise ConfigurationError(err_msg) from None


__all__ = [
    "DEFAULT_REBOOT_COMPLETION_INTERVAL",
    "DEFAULT_REBOOT_COMPLETION_TIMEOUT",
    "DEFAULT_REBOOT_GRACE_PERIOD",
    "SESSION_SUPPORTED_PATTERNS",
    "SESSION_UNSUPPORTED_PATTERNS",
    "UefiSession",
]
