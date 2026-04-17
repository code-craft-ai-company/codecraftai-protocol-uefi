"""
    .. module:: uefichannel
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Module containing :class:`UefiChannel` and its three
               transport-specific subclasses — :class:`UefiShellChannel`,
               :class:`UefiRedfishChannel`, and :class:`UefiHostOsChannel`.
               A channel is a single logical command channel that wraps
               an :class:`IUefiTransport` (or an HTTP / SSH facade over
               one) and exposes a uniform ``send`` / ``recv`` /
               ``recv_until`` / ``close`` surface consumed by layer 3
               clients and the layer-4 action-pattern engine.  Channels
               are short-lived; transport lifecycle is the responsibility
               of a higher layer.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Automation Mojo LLC"
__credits__ = []


import os
import re
import uuid

from typing import Any, Dict, Optional, Tuple, Union

from automojo.errors.exceptions import (
    AbstractMethodError,
    ConfigurationError,
    SemanticError,
)

from automojo.protocols.uefi.uefiaspects import (
    DEFAULT_CMD_ASPECTS,
    UefiCmdAspects,
    resolve_aspects,
)
from automojo.protocols.uefi.uefiinterfaces import IUefiTransport
from automojo.protocols.uefi.uefilogging import (
    UEFI_TRANSPORT_CHANNEL,
    get_uefi_logger,
)


# ---------------------------------------------------------------------------
# UefiChannel (abstract base)
# ---------------------------------------------------------------------------

class UefiChannel:
    """
        Abstract single logical command channel.

        A :class:`UefiChannel` presents a uniform byte-pipe surface
        (:meth:`send`, :meth:`recv`, :meth:`recv_until`, :meth:`close`)
        across three very different underlying transports — streaming
        serial / IPMI-SOL, HTTP-oriented Redfish, and SSH-to-host.
        Concrete subclasses either delegate the byte-pipe primitives to a
        streaming transport, or raise :class:`SemanticError` to redirect
        callers to a higher-level facade appropriate to the transport
        (HTTP verbs for Redfish, ``run_cmd`` for SSH-to-host).

        The channel never owns the underlying transport's lifecycle.
        :meth:`close` only marks the channel closed and releases any
        resources the channel itself allocated; it does not disconnect
        the transport.  :meth:`close` never raises — underlying failures
        are logged at ``WARNING`` under ``uefi.transport`` and
        suppressed.

        :param transport: The :class:`IUefiTransport` (or a transport
                          facade) this channel is bound to.
        :param aspects: The :class:`UefiCmdAspects` that supply default
                        timeouts and intervals for the channel.

        :raises: ConfigurationError
    """

    def __init__(self, transport: Any,
                       aspects: UefiCmdAspects = DEFAULT_CMD_ASPECTS) -> None:
        """
            Initialize the channel.  All instance variables are declared
            here per the repository coding standards.

            :param transport: The transport (or transport facade) this
                              channel is bound to.
            :param aspects: Default :class:`UefiCmdAspects` for this
                            channel.

            :raises: ConfigurationError
        """
        if transport is None:
            err_msg_lines = [
                "UefiChannel requires a non-None transport.",
                "GOT: value=None",
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg) from None

        if not isinstance(aspects, UefiCmdAspects):
            err_msg_lines = [
                "UefiChannel requires aspects to be a UefiCmdAspects.",
                "GOT: value={!r} type={}".format(aspects, type(aspects).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg) from None

        self._transport: Any = transport
        self._aspects: UefiCmdAspects = aspects
        self._channel_id: str = uuid.uuid4().hex[:8]
        self._closed: bool = False
        self._logger = get_uefi_logger(channel=UEFI_TRANSPORT_CHANNEL)
        return

    # -----------------------------------------------------------------------
    # Properties
    # -----------------------------------------------------------------------

    @property
    def aspects(self) -> UefiCmdAspects:
        """
            :returns: The :class:`UefiCmdAspects` this channel was
                      constructed with.
        """
        rtnval = self._aspects
        return rtnval

    @property
    def channel_id(self) -> str:
        """
            :returns: The 8-character hex channel identifier used for log
                      correlation.
        """
        rtnval = self._channel_id
        return rtnval

    @property
    def closed(self) -> bool:
        """
            :returns: ``True`` once :meth:`close` has been invoked on this
                      channel; ``False`` otherwise.  Reading this property
                      performs no I/O.
        """
        rtnval = self._closed
        return rtnval

    @property
    def transport(self) -> Any:
        """
            :returns: The transport (or transport facade) this channel is
                      bound to.
        """
        rtnval = self._transport
        return rtnval

    # -----------------------------------------------------------------------
    # Abstract byte-pipe surface
    # -----------------------------------------------------------------------

    def send(self, data: bytes) -> int:
        """
            Send ``data`` toward the target.  Subclasses that wrap a
            streaming transport forward to the transport; subclasses that
            wrap a non-streaming transport raise :class:`SemanticError`.

            :param data: Bytes to send.

            :returns: The number of bytes actually sent.
            :raises: AbstractMethodError
        """
        err_msg_lines = [
            "UefiChannel.send must be overridden by '{}'.".format(type(self).__name__),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise AbstractMethodError(err_msg) from None

    def recv(self, max_bytes: int, timeout: float) -> bytes:
        """
            Receive up to ``max_bytes`` from the target, waiting at most
            ``timeout`` seconds.

            :param max_bytes: Upper bound on the number of returned
                              bytes.
            :param timeout: Seconds to wait for data.

            :returns: The bytes read; may be shorter than ``max_bytes``.
            :raises: AbstractMethodError
        """
        err_msg_lines = [
            "UefiChannel.recv must be overridden by '{}'.".format(type(self).__name__),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise AbstractMethodError(err_msg) from None

    def recv_until(self, pattern: Union[bytes, "re.Pattern[bytes]"],
                         timeout: float) -> bytes:
        """
            Accumulate bytes from the target until ``pattern`` is observed
            or ``timeout`` elapses.

            :param pattern: Either a bytes literal or a compiled
                            :class:`re.Pattern` of bytes.
            :param timeout: Maximum seconds to wait for the pattern.

            :returns: Every byte collected from the start of the call up
                      to and including the first pattern occurrence.
            :raises: AbstractMethodError
        """
        err_msg_lines = [
            "UefiChannel.recv_until must be overridden by '{}'.".format(type(self).__name__),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise AbstractMethodError(err_msg) from None

    def close(self) -> None:
        """
            Release any resources the channel itself owns and mark the
            channel closed.  Never raises — underlying failures are
            logged at ``WARNING`` under ``uefi.transport`` and
            suppressed.

            :raises: AbstractMethodError
        """
        err_msg_lines = [
            "UefiChannel.close must be overridden by '{}'.".format(type(self).__name__),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise AbstractMethodError(err_msg) from None


# ---------------------------------------------------------------------------
# UefiShellChannel
# ---------------------------------------------------------------------------

class UefiShellChannel(UefiChannel):
    """
        Byte-stream channel over a streaming :class:`IUefiTransport`
        (serial or IPMI-SOL).  All four surface methods forward to the
        wrapped transport's same-named methods; no line-ending
        normalization is applied on :meth:`send`.

        :meth:`close` does NOT close the transport — the transport's
        lifecycle is owned by a higher layer (the client).  It only
        marks the channel closed.

        :param transport: A streaming :class:`IUefiTransport`.
        :param aspects: Default :class:`UefiCmdAspects` for this channel.

        :raises: ConfigurationError
    """

    def __init__(self, transport: IUefiTransport,
                       aspects: UefiCmdAspects = DEFAULT_CMD_ASPECTS) -> None:
        """
            Initialize the shell channel.

            :param transport: A streaming :class:`IUefiTransport`.
            :param aspects: Default :class:`UefiCmdAspects` for this
                            channel.

            :raises: ConfigurationError
        """
        UefiChannel.__init__(self, transport=transport, aspects=aspects)
        return

    def send(self, data: bytes) -> int:
        """
            Forward ``data`` to the wrapped transport's :meth:`send`.

            :param data: Bytes to send.

            :returns: The number of bytes actually sent.
            :raises: ConnectionError, UefiTransportError
        """
        if self._closed:
            err_msg_lines = [
                "UefiShellChannel.send called on a closed channel.",
                "CHANNEL: {}".format(self._channel_id),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise ConnectionError(err_msg) from None

        written = self._transport.send(data)
        rtnval = int(written)
        return rtnval

    def recv(self, max_bytes: int, timeout: float) -> bytes:
        """
            Forward to the wrapped transport's :meth:`recv`.

            :param max_bytes: Upper bound on returned bytes.
            :param timeout: Seconds to wait for data.

            :returns: The bytes read; may be empty.
            :raises: UefiTransportError
        """
        chunk = self._transport.recv(max_bytes=max_bytes, timeout=timeout)
        rtnval = chunk
        return rtnval

    def recv_until(self, pattern: Union[bytes, "re.Pattern[bytes]"],
                         timeout: float) -> bytes:
        """
            Forward to the wrapped transport's :meth:`recv_until`.

            :param pattern: Either a bytes literal or a compiled
                            :class:`re.Pattern` of bytes.
            :param timeout: Maximum seconds to wait for the pattern.

            :returns: Every byte collected up to and including the first
                      pattern occurrence.
            :raises: TimeoutError, UefiTransportError, ConfigurationError
        """
        collected = self._transport.recv_until(pattern=pattern, timeout=timeout)
        rtnval = collected
        return rtnval

    def close(self) -> None:
        """
            Mark the channel closed.  Does NOT disconnect the underlying
            transport — that is a higher-layer responsibility.  Never
            raises; if a clean-up action raises, the exception is logged
            at ``WARNING`` under ``uefi.transport`` and suppressed.
        """
        if self._closed:
            return

        try:
            # The channel owns no resources of its own beyond the closed
            # flag.  A subclass-provided clean-up hook could land here in
            # the future; the try/except is kept to honor the "close
            # never raises" contract even against the simplest call.
            pass
        except BaseException as xcpt:
            err_msg_lines = [
                "UefiShellChannel.close: suppressed exception during clean-up.",
                "CHANNEL: {}".format(self._channel_id),
                "CAUSE: {}: {}".format(type(xcpt).__name__, xcpt),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            self._logger.warning(err_msg)

        self._closed = True
        return


# ---------------------------------------------------------------------------
# UefiRedfishChannel
# ---------------------------------------------------------------------------

class UefiRedfishChannel(UefiChannel):
    """
        Channel facade over a :class:`UefiRedfishTransport`.  Redfish is
        not a byte-streaming transport; the :meth:`send`, :meth:`recv`,
        and :meth:`recv_until` byte-pipe methods therefore raise
        :class:`SemanticError` directing the caller to the HTTP-verb
        facade (:meth:`get`, :meth:`post`, :meth:`patch`, :meth:`delete`).

        :meth:`close` is a no-op marker — the transport's HTTP session
        lifecycle is owned by a higher layer.

        :param transport: A Redfish transport instance exposing
                          ``get``/``post``/``patch``/``delete``.
        :param aspects: Default :class:`UefiCmdAspects` for this channel.

        :raises: ConfigurationError
    """

    def __init__(self, transport: Any,
                       aspects: UefiCmdAspects = DEFAULT_CMD_ASPECTS) -> None:
        """
            Initialize the Redfish channel.

            :param transport: A Redfish transport instance.
            :param aspects: Default :class:`UefiCmdAspects` for this
                            channel.

            :raises: ConfigurationError
        """
        UefiChannel.__init__(self, transport=transport, aspects=aspects)
        return

    # -----------------------------------------------------------------------
    # Byte-pipe surface — intentionally unsupported
    # -----------------------------------------------------------------------

    def send(self, data: bytes) -> int:
        """
            Raise :class:`SemanticError` — Redfish is not a byte-stream
            transport.  Use :meth:`post` / :meth:`patch` instead.

            :param data: Ignored.

            :raises: SemanticError
        """
        err_msg_lines = [
            "UefiRedfishChannel.send is not supported — Redfish is not a byte-stream transport.",
            "GUIDANCE: use get(), post(), patch(), or delete() instead.",
            "CHANNEL: {}".format(self._channel_id),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise SemanticError(err_msg) from None

    def recv(self, max_bytes: int, timeout: float) -> bytes:
        """
            Raise :class:`SemanticError` — Redfish is not a byte-stream
            transport.

            :param max_bytes: Ignored.
            :param timeout: Ignored.

            :raises: SemanticError
        """
        err_msg_lines = [
            "UefiRedfishChannel.recv is not supported — Redfish is not a byte-stream transport.",
            "GUIDANCE: use get(), post(), patch(), or delete() instead.",
            "CHANNEL: {}".format(self._channel_id),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise SemanticError(err_msg) from None

    def recv_until(self, pattern: Union[bytes, "re.Pattern[bytes]"],
                         timeout: float) -> bytes:
        """
            Raise :class:`SemanticError` — Redfish is not a byte-stream
            transport.

            :param pattern: Ignored.
            :param timeout: Ignored.

            :raises: SemanticError
        """
        err_msg_lines = [
            "UefiRedfishChannel.recv_until is not supported — Redfish is not a byte-stream transport.",
            "GUIDANCE: use get(), post(), patch(), or delete() instead.",
            "CHANNEL: {}".format(self._channel_id),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise SemanticError(err_msg) from None

    def close(self) -> None:
        """
            Mark the channel closed.  The HTTP session lifecycle is the
            transport's responsibility.  Never raises.
        """
        if self._closed:
            return

        try:
            # The Redfish channel owns no session directly; the transport
            # manages the session.  The try/except honors the "close
            # never raises" contract even against future clean-up work.
            pass
        except BaseException as xcpt:
            err_msg_lines = [
                "UefiRedfishChannel.close: suppressed exception during clean-up.",
                "CHANNEL: {}".format(self._channel_id),
                "CAUSE: {}: {}".format(type(xcpt).__name__, xcpt),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            self._logger.warning(err_msg)

        self._closed = True
        return

    # -----------------------------------------------------------------------
    # HTTP facade
    # -----------------------------------------------------------------------

    def get(self, path: str,
                  aspects: Optional[UefiCmdAspects] = None) -> Dict[str, Any]:
        """
            Forward a Redfish GET to the wrapped transport's :meth:`get`.

            :param path: The Redfish resource path.
            :param aspects: Optional per-call :class:`UefiCmdAspects`.

            :returns: The JSON body as a dict.
            :raises: ConnectionError, UefiTransportError
        """
        if self._closed:
            err_msg_lines = [
                "UefiRedfishChannel.get called on a closed channel.",
                "CHANNEL: {}".format(self._channel_id),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise ConnectionError(err_msg) from None

        effective = resolve_aspects(passed=aspects, instance_default=self._aspects)
        rtnval = self._transport.get(path=path, aspects=effective)
        return rtnval

    def post(self, path: str, body: Dict[str, Any],
                   aspects: Optional[UefiCmdAspects] = None) -> Dict[str, Any]:
        """
            Forward a Redfish POST to the wrapped transport's
            :meth:`post`.

            :param path: The Redfish resource path.
            :param body: The JSON body dict.
            :param aspects: Optional per-call :class:`UefiCmdAspects`.

            :returns: The JSON body as a dict.
            :raises: ConnectionError, UefiTransportError
        """
        if self._closed:
            err_msg_lines = [
                "UefiRedfishChannel.post called on a closed channel.",
                "CHANNEL: {}".format(self._channel_id),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise ConnectionError(err_msg) from None

        effective = resolve_aspects(passed=aspects, instance_default=self._aspects)
        rtnval = self._transport.post(path=path, body=body, aspects=effective)
        return rtnval

    def patch(self, path: str, body: Dict[str, Any],
                    aspects: Optional[UefiCmdAspects] = None) -> Dict[str, Any]:
        """
            Forward a Redfish PATCH to the wrapped transport's
            :meth:`patch`.

            :param path: The Redfish resource path.
            :param body: The JSON body dict.
            :param aspects: Optional per-call :class:`UefiCmdAspects`.

            :returns: The JSON body as a dict.
            :raises: ConnectionError, UefiTransportError
        """
        if self._closed:
            err_msg_lines = [
                "UefiRedfishChannel.patch called on a closed channel.",
                "CHANNEL: {}".format(self._channel_id),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise ConnectionError(err_msg) from None

        effective = resolve_aspects(passed=aspects, instance_default=self._aspects)
        rtnval = self._transport.patch(path=path, body=body, aspects=effective)
        return rtnval

    def delete(self, path: str,
                     aspects: Optional[UefiCmdAspects] = None) -> Dict[str, Any]:
        """
            Forward a Redfish DELETE to the wrapped transport's
            :meth:`delete`.

            :param path: The Redfish resource path.
            :param aspects: Optional per-call :class:`UefiCmdAspects`.

            :returns: The JSON body as a dict.
            :raises: ConnectionError, UefiTransportError
        """
        if self._closed:
            err_msg_lines = [
                "UefiRedfishChannel.delete called on a closed channel.",
                "CHANNEL: {}".format(self._channel_id),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise ConnectionError(err_msg) from None

        effective = resolve_aspects(passed=aspects, instance_default=self._aspects)
        rtnval = self._transport.delete(path=path, aspects=effective)
        return rtnval


# ---------------------------------------------------------------------------
# UefiHostOsChannel
# ---------------------------------------------------------------------------

class UefiHostOsChannel(UefiChannel):
    """
        Channel facade over a :class:`UefiSshHostTransport`.  SSH-to-host
        is exposed through a :meth:`run_cmd` interface rather than a
        byte-pipe; the :meth:`send`, :meth:`recv`, and :meth:`recv_until`
        methods therefore raise :class:`SemanticError` directing the
        caller to :meth:`run_cmd`.

        :meth:`close` is a no-op marker — the SSH session lifecycle is
        owned by a higher layer.

        :param transport: A :class:`UefiSshHostTransport` exposing
                          ``run_cmd``.
        :param aspects: Default :class:`UefiCmdAspects` for this channel.

        :raises: ConfigurationError
    """

    def __init__(self, transport: Any,
                       aspects: UefiCmdAspects = DEFAULT_CMD_ASPECTS) -> None:
        """
            Initialize the SSH-to-host channel.

            :param transport: A :class:`UefiSshHostTransport`.
            :param aspects: Default :class:`UefiCmdAspects` for this
                            channel.

            :raises: ConfigurationError
        """
        UefiChannel.__init__(self, transport=transport, aspects=aspects)
        return

    # -----------------------------------------------------------------------
    # Byte-pipe surface — intentionally unsupported
    # -----------------------------------------------------------------------

    def send(self, data: bytes) -> int:
        """
            Raise :class:`SemanticError` — SSH-to-host is exposed via
            :meth:`run_cmd`, not a byte-pipe.

            :param data: Ignored.

            :raises: SemanticError
        """
        err_msg_lines = [
            "UefiHostOsChannel.send is not supported — SSH-to-host is command-oriented.",
            "GUIDANCE: use run_cmd() instead.",
            "CHANNEL: {}".format(self._channel_id),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise SemanticError(err_msg) from None

    def recv(self, max_bytes: int, timeout: float) -> bytes:
        """
            Raise :class:`SemanticError` — SSH-to-host is exposed via
            :meth:`run_cmd`, not a byte-pipe.

            :param max_bytes: Ignored.
            :param timeout: Ignored.

            :raises: SemanticError
        """
        err_msg_lines = [
            "UefiHostOsChannel.recv is not supported — SSH-to-host is command-oriented.",
            "GUIDANCE: use run_cmd() instead.",
            "CHANNEL: {}".format(self._channel_id),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise SemanticError(err_msg) from None

    def recv_until(self, pattern: Union[bytes, "re.Pattern[bytes]"],
                         timeout: float) -> bytes:
        """
            Raise :class:`SemanticError` — SSH-to-host is exposed via
            :meth:`run_cmd`, not a byte-pipe.

            :param pattern: Ignored.
            :param timeout: Ignored.

            :raises: SemanticError
        """
        err_msg_lines = [
            "UefiHostOsChannel.recv_until is not supported — SSH-to-host is command-oriented.",
            "GUIDANCE: use run_cmd() instead.",
            "CHANNEL: {}".format(self._channel_id),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise SemanticError(err_msg) from None

    def close(self) -> None:
        """
            Mark the channel closed.  The SSH session lifecycle is the
            transport's responsibility.  Never raises.
        """
        if self._closed:
            return

        try:
            # The host-OS channel owns no sub-channel directly in this
            # forwarder implementation; the transport's SshAgent manages
            # sessions.  The try/except honors the "close never raises"
            # contract even against future clean-up work.
            pass
        except BaseException as xcpt:
            err_msg_lines = [
                "UefiHostOsChannel.close: suppressed exception during clean-up.",
                "CHANNEL: {}".format(self._channel_id),
                "CAUSE: {}: {}".format(type(xcpt).__name__, xcpt),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            self._logger.warning(err_msg)

        self._closed = True
        return

    # -----------------------------------------------------------------------
    # Command facade
    # -----------------------------------------------------------------------

    def run_cmd(self, command: str,
                      as_root: bool = False,
                      aspects: Optional[UefiCmdAspects] = None) -> Tuple[int, str, str]:
        """
            Forward to the wrapped transport's :meth:`run_cmd`.

            :param command: The shell command string to execute.
            :param as_root: When ``True``, the transport should prefix
                            the command with ``sudo -n``.
            :param aspects: Optional per-call :class:`UefiCmdAspects`.

            :returns: A tuple ``(status, stdout, stderr)``.
            :raises: ConnectionError, UefiShellError, UefiTransportError
        """
        if self._closed:
            err_msg_lines = [
                "UefiHostOsChannel.run_cmd called on a closed channel.",
                "CHANNEL: {}".format(self._channel_id),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise ConnectionError(err_msg) from None

        effective = resolve_aspects(passed=aspects, instance_default=self._aspects)
        rtnval = self._transport.run_cmd(command=command,
                                         as_root=as_root,
                                         aspects=effective)
        return rtnval


__all__ = [
    "UefiChannel",
    "UefiShellChannel",
    "UefiRedfishChannel",
    "UefiHostOsChannel",
]
