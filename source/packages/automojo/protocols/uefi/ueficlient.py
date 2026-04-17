"""
    .. module:: ueficlient
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Module containing the abstract :class:`UefiClient` base and the
               three concrete per-transport subclasses
               (:class:`UefiShellClient`, :class:`UefiRedfishClient`,
               :class:`UefiHostOsClient`) that turn a layer-2
               :class:`IUefiTransport` into a uniform command-dispatch
               surface for consumption by layer-4 ``UefiBase`` style
               orchestrators.  The client layer is responsible for protocol
               framing and per-issuance dispatch; it explicitly does NOT
               implement retry, looping, or must-connect semantics.  Those
               live at layer-4 and are communicated to this layer via the
               :class:`UefiCmdAspects` ``action_pattern`` field, which each
               subclass validates against its declared
               :data:`supported_action_patterns` tuple before any bytes are
               sent.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Automation Mojo LLC"
__credits__ = []


import logging
import os

from typing import Optional, Tuple

from automojo.errors.exceptions import AbstractMethodError, SemanticError

from automojo.protocols.uefi.uefiaspects import (
    ActionPattern,
    DEFAULT_CMD_ASPECTS,
    UefiCmdAspects,
    resolve_aspects,
)
from automojo.protocols.uefi.uefiinterfaces import IUefiTransport
from automojo.protocols.uefi.uefilogging import (
    UEFI_TRANSPORT_CHANNEL,
    get_uefi_logger,
)

from automojo.protocols.uefi.uefichannel import (
    UefiChannel,
    UefiShellChannel,
    UefiRedfishChannel,
    UefiHostOsChannel,
)


# ---------------------------------------------------------------------------
# UEFI Shell status-probe command.  The UEFI Shell exposes the last command's
# exit status via the ``%lasterror%`` shell variable; echoing it produces a
# deterministic ``EXIT_STATUS:<hex>`` marker that the client parses.
# ---------------------------------------------------------------------------

SHELL_STATUS_PROBE: str = "echo EXIT_STATUS:%lasterror%"
SHELL_STATUS_MARKER: str = "EXIT_STATUS:"
SHELL_LINE_TERMINATOR: str = "\r\n"


# ---------------------------------------------------------------------------
# UefiClient (abstract base)
# ---------------------------------------------------------------------------

class UefiClient:
    """
        Abstract per-transport protocol client.

        Each concrete subclass pairs a single :class:`IUefiTransport` kind
        with the protocol-framing logic required to dispatch a single
        command issuance on that transport.  Subclasses declare exactly
        which :class:`ActionPattern` values they support; unsupported
        patterns raise :class:`SemanticError` before any bytes are sent.

        Retry, looping, must-connect, and allowed-exception semantics are
        NOT implemented here — they are layer-4 concerns.  The client
        fails fast on connection loss so layer-4 retry remains the single
        source of truth.

        :param transport: The layer-2 transport to dispatch through.
        :param aspects: Instance default :class:`UefiCmdAspects`; callers
                        may pass per-call overrides to
                        :meth:`open_channel` and :meth:`execute`.
    """

    supported_action_patterns: Tuple[ActionPattern, ...] = ()

    def __init__(self, transport: IUefiTransport,
                       aspects: UefiCmdAspects = DEFAULT_CMD_ASPECTS) -> None:
        """
            Initialize the client.  Every instance variable consumed
            anywhere in the class body is declared here so the attribute
            surface is knowable by inspection.

            :param transport: The layer-2 transport to dispatch through.
            :param aspects: Instance default :class:`UefiCmdAspects`.
        """
        self._transport = transport
        self._aspects = aspects
        self._logger: logging.Logger = get_uefi_logger(UEFI_TRANSPORT_CHANNEL)
        self._closed = False
        return

    # -----------------------------------------------------------------------
    # Public read-only properties
    # -----------------------------------------------------------------------

    @property
    def transport(self) -> IUefiTransport:
        """
            :returns: The wrapped transport instance.
        """
        rtnval = self._transport
        return rtnval

    @property
    def aspects(self) -> UefiCmdAspects:
        """
            :returns: The instance default :class:`UefiCmdAspects`.
        """
        rtnval = self._aspects
        return rtnval

    @property
    def connected(self) -> bool:
        """
            :returns: ``True`` when the wrapped transport reports that it
                      is connected.  Delegates to
                      :attr:`IUefiTransport.connected`.
        """
        rtnval = self._transport.connected
        return rtnval

    @property
    def closed(self) -> bool:
        """
            :returns: ``True`` after :meth:`close` has been called at
                      least once.
        """
        rtnval = self._closed
        return rtnval

    # -----------------------------------------------------------------------
    # Abstract operations
    # -----------------------------------------------------------------------

    def open_channel(self, aspects: Optional[UefiCmdAspects] = None) -> UefiChannel:
        """
            Open a fresh :class:`UefiChannel` on the wrapped transport.

            Subclasses MUST override this method.  The base implementation
            raises :class:`AbstractMethodError`.

            :param aspects: Optional per-call :class:`UefiCmdAspects`;
                            when ``None`` the instance default is used
                            via :func:`resolve_aspects`.

            :returns: A concrete :class:`UefiChannel` subclass instance.
            :raises: AbstractMethodError
        """
        err_msg_lines = [
            "UefiClient.open_channel must be overloaded by the concrete subclass.",
            "SUBCLASS: {}".format(type(self).__name__),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise AbstractMethodError(err_msg) from None

    def execute(self, command: str,
                      aspects: Optional[UefiCmdAspects] = None) -> Tuple[int, str, str]:
        """
            Dispatch a single command issuance over the wrapped transport.

            Subclasses MUST override this method.  The base implementation
            raises :class:`AbstractMethodError`.

            :param command: The command string to dispatch.  Semantics are
                            subclass-specific.
            :param aspects: Optional per-call :class:`UefiCmdAspects`;
                            when ``None`` the instance default is used
                            via :func:`resolve_aspects`.

            :returns: A ``(status, stdout, stderr)`` three-tuple.
            :raises: AbstractMethodError
        """
        err_msg_lines = [
            "UefiClient.execute must be overloaded by the concrete subclass.",
            "SUBCLASS: {}".format(type(self).__name__),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise AbstractMethodError(err_msg) from None

    # -----------------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------------

    def close(self) -> None:
        """
            Close the wrapped transport.  Idempotent — safe to call
            multiple times.  This method never raises; any exception
            produced by :meth:`IUefiTransport.disconnect` is captured and
            logged at WARNING on the ``uefi.transport`` channel so a
            ``finally`` block cleanup does not mask the original exception.

            :returns: ``None``
        """
        if self._closed:
            return

        self._closed = True

        try:
            self._transport.disconnect()
        except BaseException as derr:
            self._logger.warning(
                "UefiClient.close: transport disconnect raised; suppressed. "
                "CLIENT={} EXCEPTION={!r}".format(type(self).__name__, derr)
            )

        return

    # -----------------------------------------------------------------------
    # Protected helpers
    # -----------------------------------------------------------------------

    def _validate_action_pattern(self, aspects: UefiCmdAspects) -> None:
        """
            Validate that ``aspects.action_pattern`` is contained in this
            subclass's :data:`supported_action_patterns` tuple.  Raises
            :class:`SemanticError` otherwise.  :class:`SemanticError` is
            intentionally NOT caught anywhere in this library per the
            ``/Users/myron/repos/CLAUDE.md`` guidance.

            :param aspects: The effective :class:`UefiCmdAspects` whose
                            ``action_pattern`` field is being validated.

            :raises: SemanticError
        """
        actual_pattern = aspects.action_pattern
        supported = self.supported_action_patterns

        if actual_pattern not in supported:
            supported_names = [p.name for p in supported]

            err_msg_lines = [
                "Unsupported ActionPattern for this client.",
                "CLIENT: {}".format(type(self).__name__),
                "PATTERN: {}".format(actual_pattern.name),
                "SUPPORTED: [{}]".format(", ".join(supported_names)),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise SemanticError(err_msg) from None

        return

    def _resolve_aspects(self, aspects: Optional[UefiCmdAspects]) -> UefiCmdAspects:
        """
            Resolve the effective :class:`UefiCmdAspects` for a call.

            :param aspects: Optional per-call aspects.

            :returns: The effective aspects instance.
            :raises: ConfigurationError
        """
        resolved = resolve_aspects(passed=aspects, instance_default=self._aspects)
        return resolved


# ---------------------------------------------------------------------------
# UefiShellClient
# ---------------------------------------------------------------------------

class UefiShellClient(UefiClient):
    """
        Concrete :class:`UefiClient` for the UEFI Shell transports
        (serial and IPMI-SOL).  Dispatches a command by opening a
        :class:`UefiShellChannel`, writing the command followed by
        ``\\r\\n``, reading until the configured prompt pattern is
        observed, and then issuing a deterministic
        ``echo EXIT_STATUS:%lasterror%`` status probe to recover the
        shell's last-error integer.

        On any mid-execute :class:`ConnectionError` the client re-raises
        immediately without retry; retry is layer-4's responsibility.
    """

    supported_action_patterns: Tuple[ActionPattern, ...] = (
        ActionPattern.SINGLE_CALL,
        ActionPattern.SINGLE_CONNECTED_CALL,
        ActionPattern.DO_UNTIL_SUCCESS,
        ActionPattern.DO_WHILE_SUCCESS,
    )

    def open_channel(self, aspects: Optional[UefiCmdAspects] = None) -> UefiChannel:
        """
            Open a fresh :class:`UefiShellChannel` bound to the wrapped
            serial / IPMI-SOL transport.

            :param aspects: Optional per-call :class:`UefiCmdAspects`;
                            when ``None`` the instance default is used.

            :returns: A :class:`UefiShellChannel` instance.
            :raises: ConfigurationError
        """
        effective = self._resolve_aspects(aspects=aspects)
        channel = UefiShellChannel(transport=self._transport, aspects=effective)
        return channel

    def execute(self, command: str,
                      aspects: Optional[UefiCmdAspects] = None) -> Tuple[int, str, str]:
        """
            Dispatch a single UEFI Shell command.

            The command is written to the channel as
            ``command + "\\r\\n"`` (the UEFI Shell idiom), followed by
            reading until the configured ``prompt_pattern``.  A second
            ``echo EXIT_STATUS:%lasterror%`` probe is then issued so the
            integer exit status can be recovered deterministically.

            :param command: The UEFI Shell command line to dispatch.
            :param aspects: Optional per-call :class:`UefiCmdAspects`.

            :returns: ``(status, stdout, stderr)`` — ``stderr`` is empty
                      because the UEFI Shell does not separate streams.
            :raises: ConnectionError, TimeoutError, SemanticError,
                     ConfigurationError
        """
        effective = self._resolve_aspects(aspects=aspects)
        self._validate_action_pattern(aspects=effective)

        encoding = effective.response_encoding
        prompt = effective.prompt_pattern
        timeout = effective.inactivity_timeout

        channel = UefiShellChannel(transport=self._transport, aspects=effective)

        try:
            command_line = command + SHELL_LINE_TERMINATOR
            command_bytes = command_line.encode(encoding)
            channel.send(data=command_bytes)

            try:
                raw_response = channel.recv_until(pattern=prompt, timeout=timeout)
            except ConnectionError:
                raise
            except TimeoutError:
                raise

            decoded_response = raw_response.decode(encoding, errors="replace")
            stdout_text = self._strip_echo_and_prompt(
                response=decoded_response,
                command=command,
                prompt_pattern=prompt,
            )

            probe_line = SHELL_STATUS_PROBE + SHELL_LINE_TERMINATOR
            probe_bytes = probe_line.encode(encoding)
            channel.send(data=probe_bytes)

            try:
                raw_probe = channel.recv_until(pattern=prompt, timeout=timeout)
            except ConnectionError:
                raise
            except TimeoutError:
                raise

            decoded_probe = raw_probe.decode(encoding, errors="replace")
            status = self._parse_exit_status(probe_response=decoded_probe)

            rtnval = (status, stdout_text, "")
        finally:
            try:
                channel.close()
            except BaseException as cerr:
                self._logger.warning(
                    "UefiShellClient.execute: channel close raised; suppressed. "
                    "EXCEPTION={!r}".format(cerr)
                )

        return rtnval

    def _strip_echo_and_prompt(self, response: str, command: str,
                                     prompt_pattern: bytes) -> str:
        """
            Best-effort normalization of a captured shell response.
            Removes the leading command echo (if present) and the trailing
            prompt so the caller sees only the command's own output.

            :param response: Decoded response including any command echo
                             and the trailing shell prompt.
            :param command: The command string that was dispatched.
            :param prompt_pattern: The raw prompt-pattern bytes (used
                                   purely for length-based trimming).

            :returns: The trimmed stdout text.
        """
        working = response

        if len(working) > 0 and working.startswith(command):
            working = working[len(command):]
            if working.startswith("\r\n"):
                working = working[2:]
            elif working.startswith("\n"):
                working = working[1:]

        if len(working) > 0:
            last_newline = working.rfind("\n")
            if last_newline >= 0:
                tail = working[last_newline + 1:]
                try:
                    decoded_prompt = prompt_pattern.decode("ascii", errors="replace")
                except AttributeError:
                    decoded_prompt = ""

                if len(tail) > 0 and len(decoded_prompt) > 0:
                    working = working[:last_newline + 1]

        rtnval = working
        return rtnval

    def _parse_exit_status(self, probe_response: str) -> int:
        """
            Parse the integer exit status from the response to the
            :data:`SHELL_STATUS_PROBE` command.

            The UEFI Shell's ``%lasterror%`` is rendered as a hex value
            (for example ``0x0`` on success, ``0x8000000000000002`` on
            ``Invalid Parameter``).  An all-digit token is also accepted
            in case the target echoes a decimal form.  A value that fails
            to parse yields ``-1`` so callers can still observe the
            anomaly without crashing the dispatch pipeline.

            :param probe_response: The captured response to the status
                                   probe command, including the echoed
                                   probe line and the trailing prompt.

            :returns: The integer status, or ``-1`` when parsing fails.
        """
        marker_index = probe_response.rfind(SHELL_STATUS_MARKER)
        if marker_index < 0:
            rtnval = -1
            return rtnval

        after_marker = probe_response[marker_index + len(SHELL_STATUS_MARKER):]

        token_chars = []
        for ch in after_marker:
            if ch.isspace():
                break
            token_chars.append(ch)

        token = "".join(token_chars)

        if len(token) == 0:
            rtnval = -1
            return rtnval

        try:
            if token.lower().startswith("0x"):
                rtnval = int(token, 16)
            else:
                rtnval = int(token, 10)
        except ValueError:
            rtnval = -1

        return rtnval


# ---------------------------------------------------------------------------
# UefiRedfishClient
# ---------------------------------------------------------------------------

class UefiRedfishClient(UefiClient):
    """
        Concrete :class:`UefiClient` for the Redfish HTTP transport.

        Redfish is not a command-oriented transport — callers do not
        send shell command strings; they dispatch HTTP verbs against
        well-known resource paths.  Accordingly, :meth:`execute` on this
        subclass raises :class:`SemanticError` to loudly signal misuse.
        Callers must obtain a :class:`UefiRedfishChannel` via
        :meth:`open_channel` and use its HTTP surface
        (``get`` / ``post`` / ``patch`` / ``delete``) directly.
    """

    supported_action_patterns: Tuple[ActionPattern, ...] = (
        ActionPattern.SINGLE_CALL,
        ActionPattern.DO_UNTIL_SUCCESS,
    )

    def open_channel(self, aspects: Optional[UefiCmdAspects] = None) -> UefiChannel:
        """
            Open a fresh :class:`UefiRedfishChannel` bound to the wrapped
            Redfish transport.

            :param aspects: Optional per-call :class:`UefiCmdAspects`.

            :returns: A :class:`UefiRedfishChannel` instance.
            :raises: ConfigurationError
        """
        effective = self._resolve_aspects(aspects=aspects)
        channel = UefiRedfishChannel(transport=self._transport, aspects=effective)
        return channel

    def execute(self, command: str,
                      aspects: Optional[UefiCmdAspects] = None) -> Tuple[int, str, str]:
        """
            Always raises :class:`SemanticError`.  Redfish is not a
            command-oriented transport; callers must drive it via the
            HTTP surface on :class:`UefiRedfishChannel`.

            :param command: Ignored.
            :param aspects: Ignored.

            :returns: Never returns.
            :raises: SemanticError
        """
        err_msg_lines = [
            "UefiRedfishClient.execute is not supported.",
            "REASON: Redfish is not a command-oriented transport; no shell",
            "        semantics apply.  Callers MUST obtain a channel via",
            "        UefiRedfishClient.open_channel() and drive the HTTP",
            "        surface (get/post/patch/delete) directly.",
            "COMMAND: {!r}".format(command),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise SemanticError(err_msg) from None


# ---------------------------------------------------------------------------
# UefiHostOsClient
# ---------------------------------------------------------------------------

class UefiHostOsClient(UefiClient):
    """
        Concrete :class:`UefiClient` for the SSH-to-host transport.

        Forwards :meth:`execute` to the wrapped transport's ``run_cmd``
        method, which is the canonical three-tuple-returning exec
        surface on :class:`UefiSshHostTransport`.  Channel opening
        produces a :class:`UefiHostOsChannel` that wraps an SSH channel
        for byte-level streaming; most consumers use :meth:`execute`
        rather than the channel.
    """

    supported_action_patterns: Tuple[ActionPattern, ...] = (
        ActionPattern.SINGLE_CALL,
        ActionPattern.DO_UNTIL_SUCCESS,
        ActionPattern.DO_WHILE_SUCCESS,
    )

    def open_channel(self, aspects: Optional[UefiCmdAspects] = None) -> UefiChannel:
        """
            Open a fresh :class:`UefiHostOsChannel` bound to the wrapped
            SSH-to-host transport.

            :param aspects: Optional per-call :class:`UefiCmdAspects`.

            :returns: A :class:`UefiHostOsChannel` instance.
            :raises: ConfigurationError
        """
        effective = self._resolve_aspects(aspects=aspects)
        channel = UefiHostOsChannel(transport=self._transport, aspects=effective)
        return channel

    def execute(self, command: str,
                      aspects: Optional[UefiCmdAspects] = None) -> Tuple[int, str, str]:
        """
            Forward a command to the wrapped transport's ``run_cmd``
            method and return the resulting ``(status, stdout, stderr)``
            three-tuple unchanged.

            :param command: The shell command to run on the host.
            :param aspects: Optional per-call :class:`UefiCmdAspects`.

            :returns: ``(status, stdout, stderr)`` as returned by the
                      transport's ``run_cmd``.
            :raises: ConnectionError, TimeoutError, SemanticError,
                     ConfigurationError
        """
        effective = self._resolve_aspects(aspects=aspects)
        self._validate_action_pattern(aspects=effective)

        try:
            status, stdout, stderr = self._transport.run_cmd(
                command=command, aspects=effective,
            )
        except ConnectionError:
            raise
        except TimeoutError:
            raise

        rtnval = (status, stdout, stderr)
        return rtnval


__all__ = [
    "SHELL_LINE_TERMINATOR",
    "SHELL_STATUS_MARKER",
    "SHELL_STATUS_PROBE",
    "UefiClient",
    "UefiHostOsClient",
    "UefiRedfishClient",
    "UefiShellClient",
]
