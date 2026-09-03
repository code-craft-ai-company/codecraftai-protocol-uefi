"""
    .. module:: uefibase
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Module containing the :class:`UefiBase` class.  :class:`UefiBase`
               is the shared-state / shared-behavior base class for
               :class:`UefiAgent` and :class:`UefiSession`.  It owns the
               credentials, default aspects, host / transport configuration
               and implements the measured-interop action-pattern engine
               (the UEFI analog of ``SshBase._run_cmd``).  :class:`UefiBase`
               satisfies :class:`IUefiSystemContext` structurally and
               therefore any derived instance is immediately usable as a
               ``system_context`` argument to higher-layer capability
               modules.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Code Craft AI LLC"
__credits__ = []


import logging
import os
import threading
import time

from typing import Dict, Optional, Sequence, Tuple, Union

from ccai.errors.exceptions import (
    AbstractMethodError,
    ConfigurationError,
    SemanticError,
)

from ccai.protocols.uefi.uefiaspects import (
    ActionPattern,
    DEFAULT_CMD_ASPECTS,
    LoggingPattern,
    UefiCmdAspects,
    resolve_aspects,
)
from ccai.protocols.uefi.ueficlient import UefiClient
from ccai.protocols.uefi.ueficonst import (
    TEMPLATE_COMMAND_FAILURE,
    TEMPLATE_COMMAND_SUCCESS,
    UefiJumpParams,
)
from ccai.protocols.uefi.uefilogging import (
    UEFI_SHELL_CHANNEL,
    get_uefi_logger,
)


# ---------------------------------------------------------------------------
# Soft imports of measured-interop deadline / scope primitives.  These
# packages are part of the ccai ecosystem and should be installed in any
# working environment; we import them soft so that mere module import does
# not fail in constrained environments (e.g. static analyzers).  First use
# by :meth:`UefiBase._run` raises :class:`ConfigurationError` with a clear
# install hint if either dependency is missing.
# ---------------------------------------------------------------------------

try:
    from ccai.waiting.waitmodel import TimeoutContext as _TimeoutContext
except ImportError:
    _TimeoutContext = None  # type: ignore[assignment]

class _NoOpMonitoredScope:
    """
        Fallback context manager used in place of the upstream
        :class:`ccai.xmodules.xlogging.monitoredscope.MonitoredScope`
        when the upstream implementation is missing or broken.  The class
        takes the same constructor shape but performs no monitoring — it
        is a pure pass-through so that the measured-interop retry loops
        keep working without a hang-detection watchdog.

        :param label: Human-readable label (ignored).
        :param message: Message rendered if the scope expired (ignored).
        :param timeout_ctx: :class:`TimeoutContext` for the scope (ignored).
        :param notify_delay: Delay before notification (ignored).
    """

    def __init__(self, label: str, message: str, timeout_ctx: object,
                       notify_delay: object = None) -> None:
        """
            Capture constructor arguments for compatibility.  None of them
            participate in behavior.
        """
        self._label = label
        self._message = message
        self._timeout_ctx = timeout_ctx
        self._notify_delay = notify_delay
        return

    def __enter__(self) -> "_NoOpMonitoredScope":
        """
            Enter the scope; no-op.
        """
        return self

    def __exit__(self, ex_type: object, ex_inst: object, ex_tb: object) -> bool:
        """
            Exit the scope; no-op, does not suppress in-block exceptions.
        """
        return False


def _import_monitored_scope() -> type:
    """
        Return the upstream :class:`MonitoredScope` class when it imports
        cleanly AND has its internal ``global_scope_monitor`` /
        ``ScopeMonitor`` references defined; otherwise return the local
        no-op fallback.

        :returns: A context-manager class compatible with the expected
                  ``(label, message, timeout_ctx, notify_delay)`` shape.
    """
    try:
        from ccai.xmodules.xlogging import monitoredscope as _mod
        upstream_cls = getattr(_mod, "MonitoredScope", None)
        has_global = hasattr(_mod, "global_scope_monitor")
        has_scope_monitor_cls = hasattr(_mod, "ScopeMonitor")

        if upstream_cls is not None and has_global and has_scope_monitor_cls:
            return upstream_cls

        return _NoOpMonitoredScope

    except ImportError:
        return _NoOpMonitoredScope


_MonitoredScope = _import_monitored_scope()


# ---------------------------------------------------------------------------
# Valid transport kinds accepted by UefiBase.  Duplicated from
# uefiaspects.VALID_TRANSPORT_KINDS to avoid importing the private constant.
# ---------------------------------------------------------------------------

_VALID_TRANSPORT_KINDS: Tuple[str, ...] = (
    "serial",
    "redfish",
    "ssh-host",
    "ipmi-sol",
)


def _format_stream_for_details(text: Optional[str]) -> str:
    """
        Render a captured stdout/stderr stream for inclusion in a multi-line
        exception's ``details`` section.  ``None`` is rendered as an empty
        string so the caller can always treat the return value as a
        concatenable ``str``.

        :param text: The captured stream text or ``None``.

        :returns: The text, or an empty string when ``text`` is ``None``.
    """
    if text is None:
        rtnval = ""
    else:
        rtnval = text

    return rtnval


class UefiBase:
    """
        The :class:`UefiBase` object provides for the sharing of state and
        functional patterns between :class:`UefiAgent` and
        :class:`UefiSession`.  It owns the credentials, default aspects,
        host / transport configuration, implements the measured-interop
        action-pattern engine, and satisfies :class:`IUefiSystemContext`
        structurally.

        The class is a faithful analog of
        ``ccai.protocols.ssh.sshbase.SshBase`` with three extensions:

        1. Five-pattern engine (``SINGLE_CALL``, ``DO_UNTIL_SUCCESS``,
           ``DO_WHILE_SUCCESS`` supported; ``SINGLE_CONNECTED_CALL`` and
           ``DO_UNTIL_CONNECTION_FAILURE`` raise :class:`SemanticError`
           at this base-class layer and are opted-in by subclasses that
           hold long-lived channel state).
        2. Pluggable :class:`UefiClient` factory via subclass override of
           :meth:`_create_client`.
        3. Transport-aware cached system-context metadata
           (``firmware_vendor``, ``firmware_revision``,
           ``secure_boot_state``).

        All instance variables are initialized in :meth:`__init__` per the
        project coding standards.  :meth:`__init__` performs no network I/O.
    """

    def __init__(self, host: str,
                       primary_credential: object,
                       transport_kind: str,
                       users: Optional[Dict[str, object]] = None,
                       jump: Optional[UefiJumpParams] = None,
                       called_id: Optional[str] = None,
                       aspects: UefiCmdAspects = DEFAULT_CMD_ASPECTS) -> None:
        """
            Initialize the base UEFI helper.

            :param host: The target host identifier (DNS name, IP address,
                         BMC URI, or serial device path).
            :param primary_credential: The credential used for the primary
                                       role on the target.  Accepted types
                                       are the UEFI credential classes
                                       (``UefiSerialCredential``,
                                       ``UefiRedfishCredential``,
                                       ``UefiIpmiCredential``) or an
                                       :class:`SshCredential` when
                                       ``transport_kind == 'ssh-host'``.
            :param transport_kind: One of ``'serial'``, ``'redfish'``,
                                   ``'ssh-host'``, ``'ipmi-sol'``.
            :param users: Optional mapping of user-role name to credential.
            :param jump: Optional :class:`UefiJumpParams` for a jump host.
            :param called_id: Optional correlation identifier; when set, log
                              lines are tagged ``[called_id]`` instead of
                              ``host``.
            :param aspects: Instance-default :class:`UefiCmdAspects` used
                            whenever a call site passes ``aspects=None``.

            :raises: ConfigurationError
        """
        if not isinstance(host, str) or len(host) == 0:
            err_msg_lines = [
                "'host' must be a non-empty str.",
                "GOT: value={!r} type={}".format(host, type(host).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg) from None

        if not isinstance(transport_kind, str) or transport_kind not in _VALID_TRANSPORT_KINDS:
            err_msg_lines = [
                "'transport_kind' is not a known transport kind.",
                "GOT: value={!r}".format(transport_kind),
                "ALLOWED: {}".format(", ".join(_VALID_TRANSPORT_KINDS)),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg) from None

        if primary_credential is None:
            err_msg_lines = [
                "'primary_credential' must not be None.",
                "TRANSPORT_KIND: {}".format(transport_kind),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg) from None

        if not isinstance(aspects, UefiCmdAspects):
            err_msg_lines = [
                "'aspects' must be a UefiCmdAspects instance.",
                "GOT: value={!r} type={}".format(aspects, type(aspects).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg) from None

        if called_id is not None and not isinstance(called_id, str):
            err_msg_lines = [
                "'called_id' must be a str or None.",
                "GOT: value={!r} type={}".format(called_id, type(called_id).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg) from None

        if users is not None and not isinstance(users, dict):
            err_msg_lines = [
                "'users' must be a dict or None.",
                "GOT: value={!r} type={}".format(users, type(users).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg) from None

        # Core fields --------------------------------------------------------
        self._host = host
        self._primary_credential = primary_credential
        self._transport_kind = transport_kind
        self._users = users
        self._jump = jump
        self._called_id = called_id

        if called_id is None:
            self._target_tag = host
        else:
            self._target_tag = "[{}]".format(called_id)

        self._aspects = aspects
        self._logger: logging.Logger = get_uefi_logger(UEFI_SHELL_CHANNEL)

        # System-context metadata caches ------------------------------------
        self._firmware_vendor: Optional[str] = None
        self._firmware_revision: Optional[int] = None
        self._secure_boot_state: Optional[str] = None

        # Helper lookup tables ----------------------------------------------
        self._user_lookup_table: Dict[int, str] = {}
        self._group_lookup_table: Dict[int, str] = {}

        return

    # -----------------------------------------------------------------------
    # Read-only properties
    # -----------------------------------------------------------------------

    @property
    def host(self) -> str:
        """
            :returns: The target host identifier.
        """
        rtnval = self._host
        return rtnval

    @property
    def primary_credential(self) -> object:
        """
            :returns: The primary credential for the target.
        """
        rtnval = self._primary_credential
        return rtnval

    @property
    def transport_kind(self) -> str:
        """
            :returns: The transport-kind literal for the target.
        """
        rtnval = self._transport_kind
        return rtnval

    @property
    def users(self) -> Optional[Dict[str, object]]:
        """
            :returns: The optional user-role-to-credential mapping.
        """
        rtnval = self._users
        return rtnval

    @property
    def jump(self) -> Optional[UefiJumpParams]:
        """
            :returns: The optional jump-host parameters.
        """
        rtnval = self._jump
        return rtnval

    @property
    def called_id(self) -> Optional[str]:
        """
            :returns: The optional log-correlation identifier.
        """
        rtnval = self._called_id
        return rtnval

    @property
    def target_tag(self) -> str:
        """
            :returns: The computed log-prefix tag (``host`` when
                      ``called_id`` is ``None``, ``"[called_id]"``
                      otherwise).
        """
        rtnval = self._target_tag
        return rtnval

    @property
    def aspects(self) -> UefiCmdAspects:
        """
            :returns: The instance-default :class:`UefiCmdAspects`.
        """
        rtnval = self._aspects
        return rtnval

    @property
    def firmware_vendor(self) -> Optional[str]:
        """
            :returns: The cached firmware-vendor string, or ``None`` when
                      not yet resolved.  The cache is populated on first
                      read by a derived class; the base class returns the
                      current cache value without probing.
        """
        rtnval = self._firmware_vendor
        return rtnval

    @property
    def firmware_revision(self) -> Optional[int]:
        """
            :returns: The cached firmware-revision integer, or ``None`` when
                      not yet resolved.
        """
        rtnval = self._firmware_revision
        return rtnval

    @property
    def secure_boot_state(self) -> Optional[str]:
        """
            :returns: The cached Secure Boot state summary, or ``None`` when
                      not yet resolved.
        """
        rtnval = self._secure_boot_state
        return rtnval

    # -----------------------------------------------------------------------
    # Methods intended for override by derived classes
    # -----------------------------------------------------------------------

    def _create_client(self, user: Optional[str] = None,
                             aspects: Optional[UefiCmdAspects] = None) -> UefiClient:
        """
            Factory for the per-call :class:`UefiClient`.  Derived classes
            override this method to supply a concrete client appropriate
            for the configured transport.

            :param user: Optional user role; used to look up an alternate
                         credential from :attr:`users`.
            :param aspects: Optional per-call :class:`UefiCmdAspects`.

            :returns: A :class:`UefiClient` subclass instance.
            :raises: AbstractMethodError
        """
        err_msg_lines = [
            "UefiBase._create_client must be overloaded by derived class '{}'.".format(
                type(self).__name__,
            ),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise AbstractMethodError(err_msg) from None

    def run_cmd(self, command: str,
                      exp_status: Union[int, Sequence[int]] = 0,
                      user: Optional[str] = None,
                      aspects: Optional[UefiCmdAspects] = None) -> Tuple[int, str, str]:
        """
            Run a single command against the target and return a
            ``(status, stdout, stderr)`` three-tuple.  Derived classes
            override this method to bind their specific
            :class:`UefiClient` / :class:`UefiChannel` runner to
            :meth:`_run`.

            :param command: The command string.
            :param exp_status: The expected status code or a sequence of
                               acceptable status codes.
            :param user: The optional user-role name.
            :param aspects: Optional per-call :class:`UefiCmdAspects`.

            :returns: A ``(status, stdout, stderr)`` three-tuple.
            :raises: AbstractMethodError
        """
        err_msg_lines = [
            "UefiBase.run_cmd must be overloaded by derived class '{}'.".format(
                type(self).__name__,
            ),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise AbstractMethodError(err_msg) from None

    def reboot(self, aspects: Optional[UefiCmdAspects] = None) -> None:
        """
            Reboot the target.  Derived classes override this method so
            that reconnect-after-reboot workflows can be implemented
            uniformly across :class:`UefiAgent` and :class:`UefiSession`.

            :param aspects: Optional per-call :class:`UefiCmdAspects`.

            :raises: AbstractMethodError
        """
        err_msg_lines = [
            "UefiBase.reboot must be overloaded by derived class '{}'.".format(
                type(self).__name__,
            ),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise AbstractMethodError(err_msg) from None

    # -----------------------------------------------------------------------
    # Concrete public methods
    # -----------------------------------------------------------------------

    def verify_connectivity(self) -> bool:
        """
            Probe the target for basic reachability.  The probe is chosen
            based on :attr:`transport_kind`:

            * ``'serial'`` / ``'ipmi-sol'`` / ``'ssh-host'`` — runs the
              shell command ``echo Hello`` via :meth:`run_cmd` and
              returns ``True`` iff the command exits with status ``0`` and
              the stdout contains the literal string ``"Hello"``.  For
              the SSH-host transport the stdout is additionally stripped
              and compared for equality, matching
              ``SshBase.verify_connectivity``.
            * ``'redfish'`` — issues a GET against ``/redfish/v1/`` via
              the derived class's :meth:`run_cmd` (which the derived
              class maps to an HTTP GET) and returns ``True`` iff the
              status code is ``200``.

            :returns: ``True`` when the probe succeeded; ``False`` when
                      the probe failed or the underlying transport raised
                      :class:`ConnectionError`.
            :raises: ConfigurationError
        """
        vok = False

        try:
            if self._transport_kind == "ssh-host":
                status, stdout, _ = self.run_cmd(command="echo Hello")
                if status == 0 and stdout.strip() == "Hello":
                    vok = True
            elif self._transport_kind in ("serial", "ipmi-sol"):
                status, stdout, _ = self.run_cmd(command="echo Hello")
                if status == 0 and "Hello" in stdout:
                    vok = True
            elif self._transport_kind == "redfish":
                status, _, _ = self.run_cmd(command="/redfish/v1/")
                if status == 200:
                    vok = True
            else:
                err_msg_lines = [
                    "Unknown transport_kind for verify_connectivity.",
                    "GOT: value={!r}".format(self._transport_kind),
                    "ALLOWED: {}".format(", ".join(_VALID_TRANSPORT_KINDS)),
                ]
                err_msg = os.linesep.join(err_msg_lines)
                raise ConfigurationError(err_msg) from None
        except ConnectionError as cerr:
            self._logger.debug(
                "uefi.base: verify_connectivity observed ConnectionError: %r", cerr,
            )
            vok = False

        return vok

    def lookup_user_by_uid(self, uid: int, update: bool = True) -> Optional[str]:
        """
            Helper to resolve a user name from a numeric UID by querying
            the target.  Results are cached for subsequent lookups.

            :param uid: The UID of the user to resolve.
            :param update: When ``True`` (default), the table is updated
                           via ``id -n -u <uid>`` when the UID is not
                           already cached.  When ``False``, only the
                           cache is consulted.

            :returns: The user name string, or ``None`` when the lookup
                      was not resolved.
        """
        if not isinstance(uid, int) or isinstance(uid, bool):
            err_msg_lines = [
                "'uid' must be an int.",
                "GOT: value={!r} type={}".format(uid, type(uid).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg) from None

        username: Optional[str] = None

        if uid in self._user_lookup_table:
            username = self._user_lookup_table[uid]
        elif update:
            status, stdout, _ = self.run_cmd(command="id -n -u {}".format(uid))
            if status == 0:
                username = stdout.strip()
                self._user_lookup_table[uid] = username

        return username

    def lookup_group_by_gid(self, gid: int, update: bool = True) -> Optional[str]:
        """
            Helper to resolve a group name from a numeric GID by querying
            the target.  Results are cached for subsequent lookups.

            :param gid: The GID of the group to resolve.
            :param update: When ``True`` (default), the table is updated
                           via ``id -n -g <gid>`` when the GID is not
                           already cached.  When ``False``, only the
                           cache is consulted.

            :returns: The group name string, or ``None`` when the lookup
                      was not resolved.
        """
        if not isinstance(gid, int) or isinstance(gid, bool):
            err_msg_lines = [
                "'gid' must be an int.",
                "GOT: value={!r} type={}".format(gid, type(gid).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg) from None

        groupname: Optional[str] = None

        if gid in self._group_lookup_table:
            groupname = self._group_lookup_table[gid]
        elif update:
            status, stdout, _ = self.run_cmd(command="id -n -g {}".format(gid))
            if status == 0:
                groupname = stdout.strip()
                self._group_lookup_table[gid] = groupname

        return groupname

    # -----------------------------------------------------------------------
    # Private helpers -- command-result logging
    # -----------------------------------------------------------------------

    def _log_command_result(self, command: str, status: int,
                                  stdout: str, stderr: str,
                                  exp_status: Union[int, Sequence[int]],
                                  logging_pattern: LoggingPattern) -> None:
        """
            Log the result of a command invocation.  The logger level and
            decision to log at all are controlled by ``logging_pattern``:

            * :data:`LoggingPattern.ALL_RESULTS` — log both success and
              failure cases.
            * :data:`LoggingPattern.SUCCESS_ONLY` — log only when the
              command's status matches ``exp_status``.
            * :data:`LoggingPattern.FAILURE_ONLY` — log only when the
              command's status does not match ``exp_status``.

            When ``exp_status`` is an ``int`` the match is ``==``; when it
            is a sequence of ints the match is ``in``.  The ``_target_tag``
            is prepended to every emitted line.

            :param command: The command string that was issued.
            :param status: The integer status code returned by the command.
            :param stdout: The captured stdout text.
            :param stderr: The captured stderr text.
            :param exp_status: The expected status code or sequence thereof.
            :param logging_pattern: The active :class:`LoggingPattern`.
        """
        if self._target_tag is not None:
            tgt_tag = self._target_tag
        else:
            tgt_tag = ""

        if isinstance(exp_status, int):
            matched = (status == exp_status)
        else:
            matched = (status in exp_status)

        stdout_text = stdout if stdout is not None else ""
        stderr_text = stderr if stderr is not None else ""

        if matched:
            if logging_pattern in (LoggingPattern.ALL_RESULTS, LoggingPattern.SUCCESS_ONLY):
                success_line = TEMPLATE_COMMAND_SUCCESS % (
                    tgt_tag, command, stdout_text, stderr_text,
                )
                self._logger.info(success_line)
        else:
            if logging_pattern in (LoggingPattern.ALL_RESULTS, LoggingPattern.FAILURE_ONLY):
                failure_line = TEMPLATE_COMMAND_FAILURE % (
                    tgt_tag, command, status, stdout_text, stderr_text,
                )
                self._logger.error(failure_line)

        return

    def _status_matches(self, status: int,
                              exp_status: Union[int, Sequence[int]]) -> bool:
        """
            Compare an observed ``status`` against the caller-declared
            ``exp_status``.  When ``exp_status`` is an ``int`` the match is
            ``==``; when it is a sequence of ints the match is ``in``.

            :param status: The observed integer status code.
            :param exp_status: The expected status code or sequence thereof.

            :returns: ``True`` when the status matches, ``False`` otherwise.
        """
        if isinstance(exp_status, int):
            matched = (status == exp_status)
        else:
            matched = (status in exp_status)

        return matched

    # -----------------------------------------------------------------------
    # Private helpers -- action pattern engine
    # -----------------------------------------------------------------------

    def _ensure_runtime_dependencies(self) -> None:
        """
            Validate that the soft-imported :class:`TimeoutContext` and
            :class:`MonitoredScope` dependencies loaded successfully at
            module import time.  :meth:`_run` invokes this helper at the
            top of every call so a missing dependency surfaces with a
            clear remediation hint.

            :raises: ConfigurationError
        """
        if _TimeoutContext is None or _MonitoredScope is None:
            err_msg_lines = [
                "UefiBase runtime dependencies are not available.",
                "MISSING:",
            ]
            if _TimeoutContext is None:
                err_msg_lines.append("    ccai.waiting.waitmodel.TimeoutContext")
            if _MonitoredScope is None:
                err_msg_lines.append("    ccai.xmodules.xlogging.monitoredscope.MonitoredScope")
            err_msg_lines.append(
                "HINT: install 'codecraftai-waiting' and 'codecraftai-xmodules' into "
                "the active environment (e.g. 'poetry install')."
            )
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg) from None

        return

    def _dispatch_inner(self, runner: UefiClient, command: str,
                              aspects: UefiCmdAspects,
                              user: Optional[str]) -> Tuple[int, str, str]:
        """
            Dispatch one inner command through the runner and normalize
            the result to a ``(status, stdout, stderr)`` three-tuple.

            :param runner: The :class:`UefiClient` that will execute the
                           command.  Non-client runners (e.g. long-lived
                           channels) are handled by derived classes that
                           override this method.
            :param command: The command string to dispatch.
            :param aspects: The effective :class:`UefiCmdAspects`.
            :param user: The optional user-role name (forwarded for use
                         by derived classes; unused here).

            :returns: A ``(status, stdout, stderr)`` three-tuple.
            :raises: SemanticError
        """
        if isinstance(runner, UefiClient):
            result = runner.execute(command=command, aspects=aspects)
            status, stdout, stderr = result
            rtnval = (status, stdout, stderr)
            return rtnval

        err_msg_lines = [
            "UefiBase._dispatch_inner: unsupported runner type.",
            "RUNNER_TYPE: {}".format(type(runner).__name__),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise SemanticError(err_msg) from None

    def _raise_completion_timeout(self, completion_toctx: object,
                                        what_for: str,
                                        command: str,
                                        stdout: Optional[str],
                                        stderr: Optional[str]) -> None:
        """
            Compose the timeout-error details and raise the timeout built
            by :meth:`TimeoutContext.create_timeout`.

            :param completion_toctx: The active :class:`TimeoutContext`.
            :param what_for: A short phrase describing what was being
                             awaited (used by
                             :meth:`TimeoutContext.create_timeout`).
            :param command: The command that was being retried.
            :param stdout: The last observed stdout; may be ``None`` when
                           every iteration raised before returning.
            :param stderr: The last observed stderr; may be ``None`` when
                           every iteration raised before returning.

            :raises: TimeoutError
        """
        details = [
            "CMD: {}".format(command),
            "STDOUT:",
            _format_stream_for_details(stdout),
            "STDERR:",
            _format_stream_for_details(stderr),
        ]

        toerr = completion_toctx.create_timeout(what_for=what_for, detail=details)
        raise toerr

    def _run(self, runner: UefiClient, command: str,
                   exp_status: Union[int, Sequence[int]] = 0,
                   user: Optional[str] = None,
                   aspects: Optional[UefiCmdAspects] = None) -> Tuple[int, str, str]:
        """
            Measured-interop action-pattern engine.  Dispatches a command
            on the caller-supplied ``runner`` according to
            ``aspects.action_pattern`` and wraps every inner call in a
            :class:`MonitoredScope` so hung threads can be detected.

            Dispatch matrix:

            * :data:`ActionPattern.SINGLE_CALL` — one call, log, return.
            * :data:`ActionPattern.DO_UNTIL_SUCCESS` — loop until status
              matches ``exp_status`` or ``aspects.completion_timeout``
              elapses; on timeout raise
              :class:`TimeoutError` with CMD / STDOUT / STDERR details.
            * :data:`ActionPattern.DO_WHILE_SUCCESS` — loop while status
              matches ``exp_status``; same timeout semantics.
            * :data:`ActionPattern.SINGLE_CONNECTED_CALL` — raises
              :class:`SemanticError` (base-class layer; sessions override).
            * :data:`ActionPattern.DO_UNTIL_CONNECTION_FAILURE` — raises
              :class:`SemanticError` (base-class layer; sessions override).
            * Unknown — raises :class:`SemanticError`.

            :param runner: The :class:`UefiClient` whose :meth:`execute`
                           is invoked on every iteration.
            :param command: The command string to run.
            :param exp_status: The expected status code (int) or a
                               sequence of acceptable status codes.
            :param user: The optional user-role name, forwarded to
                         :meth:`_dispatch_inner`.
            :param aspects: Optional per-call :class:`UefiCmdAspects`.
                            ``None`` means "use the instance default"
                            via :func:`resolve_aspects`.

            :returns: A ``(status, stdout, stderr)`` three-tuple.
            :raises: ConfigurationError, SemanticError, TimeoutError
        """
        self._ensure_runtime_dependencies()

        resolved = resolve_aspects(passed=aspects, instance_default=self._aspects)

        completion_toctx = _TimeoutContext(
            resolved.completion_timeout,
            resolved.completion_interval,
        )
        monitor_delay = resolved.monitor_delay
        logging_pattern = resolved.logging_pattern

        this_thr = threading.current_thread()
        monmsg = "Thread failed to exit monitored scope. thid={} thname={} cmd={}".format(
            this_thr.ident, this_thr.name, command,
        )

        status: Optional[int] = None
        stdout: Optional[str] = None
        stderr: Optional[str] = None

        pattern = resolved.action_pattern

        if pattern == ActionPattern.SINGLE_CALL:

            singular_toctx = _TimeoutContext(
                resolved.inactivity_timeout, resolved.inactivity_interval,
            )
            with _MonitoredScope("RUNCMD-SINGULAR", monmsg, singular_toctx,
                                 notify_delay=monitor_delay):
                status, stdout, stderr = self._dispatch_inner(
                    runner=runner, command=command, aspects=resolved, user=user,
                )

            self._log_command_result(
                command=command, status=status, stdout=stdout, stderr=stderr,
                exp_status=exp_status, logging_pattern=logging_pattern,
            )

        elif pattern == ActionPattern.DO_UNTIL_SUCCESS:

            completion_toctx.mark_begin()

            while True:

                iter_toctx = _TimeoutContext(
                    resolved.inactivity_timeout, resolved.inactivity_interval,
                )
                with _MonitoredScope("RUNCMD-DO_UNTIL_SUCCESS", monmsg, iter_toctx,
                                     notify_delay=monitor_delay):
                    status, stdout, stderr = self._dispatch_inner(
                        runner=runner, command=command, aspects=resolved, user=user,
                    )

                self._log_command_result(
                    command=command, status=status, stdout=stdout, stderr=stderr,
                    exp_status=exp_status, logging_pattern=logging_pattern,
                )

                if self._status_matches(status=status, exp_status=exp_status):
                    break

                if completion_toctx.final_attempt:
                    self._raise_completion_timeout(
                        completion_toctx=completion_toctx,
                        what_for="command success",
                        command=command,
                        stdout=stdout,
                        stderr=stderr,
                    )
                elif not completion_toctx.should_continue():
                    completion_toctx.mark_final_attempt()

                time.sleep(completion_toctx.interval)

        elif pattern == ActionPattern.DO_WHILE_SUCCESS:

            completion_toctx.mark_begin()

            while True:

                iter_toctx = _TimeoutContext(
                    resolved.inactivity_timeout, resolved.inactivity_interval,
                )
                with _MonitoredScope("RUNCMD-DO_WHILE_SUCCESS", monmsg, iter_toctx,
                                     notify_delay=monitor_delay):
                    status, stdout, stderr = self._dispatch_inner(
                        runner=runner, command=command, aspects=resolved, user=user,
                    )

                self._log_command_result(
                    command=command, status=status, stdout=stdout, stderr=stderr,
                    exp_status=exp_status, logging_pattern=logging_pattern,
                )

                if not self._status_matches(status=status, exp_status=exp_status):
                    break

                if completion_toctx.final_attempt:
                    self._raise_completion_timeout(
                        completion_toctx=completion_toctx,
                        what_for="command continued to succeed",
                        command=command,
                        stdout=stdout,
                        stderr=stderr,
                    )
                elif not completion_toctx.should_continue():
                    completion_toctx.mark_final_attempt()

                time.sleep(completion_toctx.interval)

        elif pattern == ActionPattern.SINGLE_CONNECTED_CALL:
            err_msg_lines = [
                "UefiBase does not support the SINGLE_CONNECTED_CALL action pattern.",
                "REASON: SINGLE_CONNECTED_CALL requires a long-lived channel runner,",
                "        which is a responsibility of UefiSession (not UefiBase).",
                "CLASS: {}".format(type(self).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise SemanticError(err_msg) from None

        elif pattern == ActionPattern.DO_UNTIL_CONNECTION_FAILURE:
            err_msg_lines = [
                "UefiBase does not support the DO_UNTIL_CONNECTION_FAILURE action pattern.",
                "REASON: DO_UNTIL_CONNECTION_FAILURE requires a long-lived channel runner,",
                "        which is a responsibility of UefiSession (not UefiBase).",
                "CLASS: {}".format(type(self).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise SemanticError(err_msg) from None

        else:
            err_msg_lines = [
                "UefiBase: Unknown ActionPattern encountered.",
                "ACTION_PATTERN: {!r}".format(pattern),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise SemanticError(err_msg) from None

        rtnval = (status, stdout, stderr)
        return rtnval


def default_client_factory(transport_kind: str, transport: object,
                           aspects: UefiCmdAspects) -> UefiClient:
    """
        Builds the :class:`UefiClient` subclass that matches an already
        constructed transport.  :class:`UefiSession` uses this after it has
        built and connected its own transport; the dispatch mirrors
        :meth:`UefiAgent._create_client`, which builds transport and client
        together.

        :param transport_kind: One of ``"serial"``, ``"ipmi-sol"``,
                               ``"redfish"`` or ``"ssh-host"``.
        :param transport: The connected transport the client will drive.
        :param aspects: The :class:`UefiCmdAspects` the client should use.

        :returns: An unconnected-by-this-function client wrapping ``transport``.

        :raises ConfigurationError: When ``transport_kind`` is not one of the
                                    supported kinds.
    """
    # Imported here so uefibase stays importable before the client subclasses are.
    from ccai.protocols.uefi.ueficlient import UefiHostOsClient, UefiRedfishClient, UefiShellClient

    if transport_kind in ("serial", "ipmi-sol"):
        rtnval: UefiClient = UefiShellClient(transport=transport, aspects=aspects)
    elif transport_kind == "redfish":
        rtnval = UefiRedfishClient(transport=transport, aspects=aspects)
    elif transport_kind == "ssh-host":
        rtnval = UefiHostOsClient(transport=transport, aspects=aspects)
    else:
        err_msg_lines = [
            "default_client_factory: unknown transport_kind.",
            "TRANSPORT_KIND: {!r}".format(transport_kind),
            "ALLOWED: serial, ipmi-sol, redfish, ssh-host",
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise ConfigurationError(err_msg) from None

    return rtnval


__all__ = [
    "UefiBase",
    "default_client_factory",
]
