"""
    .. module:: ueficonnection
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Module containing :class:`UefiConnection`, a thin
               lifecycle-managed wrapper around any
               :class:`IUefiTransport` instance.  The wrapper exposes a
               uniform ``connect`` / ``disconnect`` / ``reconnect`` surface,
               a read-only ``connected`` property, a monotonically
               increasing ``reconnect_count`` counter, and emits structured
               log events on the ``uefi.transport`` channel for every
               state transition.  Retry semantics (connection timeout,
               connection interval, allowed-exception handling, and
               must-connect short circuit) are owned by the underlying
               transport; this class is intentionally a thin wrapper so
               that higher layers have a single, predictable lifecycle
               surface regardless of the concrete transport kind.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Automation Mojo LLC"
__credits__ = []


import logging
import os
import time

from typing import Optional

from automojo.errors.exceptions import ConfigurationError

from automojo.protocols.uefi.uefiaspects import (
    DEFAULT_CMD_ASPECTS,
    UefiCmdAspects,
    resolve_aspects,
)
from automojo.protocols.uefi.uefiexceptions import UefiTransportError
from automojo.protocols.uefi.uefiinterfaces import IUefiTransport
from automojo.protocols.uefi.uefilogging import (
    UEFI_TRANSPORT_CHANNEL,
    get_uefi_logger,
)


# ---------------------------------------------------------------------------
# Structured-log event name constants
# ---------------------------------------------------------------------------

EVENT_CONNECTION_OPEN: str = "connection.open"
EVENT_CONNECTION_CLOSE: str = "connection.close"
EVENT_CONNECTION_RECONNECT: str = "connection.reconnect"


# ---------------------------------------------------------------------------
# UefiConnection
# ---------------------------------------------------------------------------

class UefiConnection:
    """
        Lifecycle-managed wrapper around an :class:`IUefiTransport` handle.

        The class exposes a uniform ``connect`` / ``disconnect`` /
        ``reconnect`` API and a read-only ``connected`` property so higher
        layers do not branch on transport type when managing lifecycle.
        A monotonically increasing ``reconnect_count`` is exposed for
        diagnostics and test assertions.  Every state transition produces
        a structured ``INFO``-level log event on the ``uefi.transport``
        channel with a stable ``event`` field — one of
        :data:`EVENT_CONNECTION_OPEN`, :data:`EVENT_CONNECTION_CLOSE`,
        or :data:`EVENT_CONNECTION_RECONNECT`.

        Retry semantics (connection timeout, interval, allowed exception
        handling, and ``must_connect``) are delegated to the underlying
        transport.  This class is intentionally a thin wrapper so the
        retry surface remains in one place.

        :param transport: An :class:`IUefiTransport` to wrap.  Validated
                          structurally via :func:`isinstance` against the
                          runtime-checkable protocol.
        :param aspects: Default :class:`UefiCmdAspects` used when a
                        per-call ``aspects`` argument is ``None``.  The
                        instance default is captured by reference for
                        reuse across ``reconnect`` calls.

        :raises: ConfigurationError
    """

    def __init__(self, transport: IUefiTransport,
                       aspects: UefiCmdAspects = DEFAULT_CMD_ASPECTS) -> None:
        """
            Initialize the connection wrapper.  All instance variables are
            declared here per the project coding standards; no lazy
            attribute creation is permitted elsewhere.

            :param transport: An :class:`IUefiTransport` to wrap.
            :param aspects: Default :class:`UefiCmdAspects` for this
                            connection.

            :raises: ConfigurationError
        """
        if not isinstance(transport, IUefiTransport):
            err_msg_lines = [
                "UefiConnection requires an IUefiTransport instance.",
                "GOT: value={!r} type={}".format(transport, type(transport).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg) from None

        if not isinstance(aspects, UefiCmdAspects):
            err_msg_lines = [
                "UefiConnection requires aspects to be a UefiCmdAspects instance.",
                "GOT: value={!r} type={}".format(aspects, type(aspects).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg) from None

        self._transport: IUefiTransport = transport
        self._aspects: UefiCmdAspects = aspects
        self._reconnect_count: int = 0
        self._last_connect_begin: Optional[float] = None
        self._last_connect_end: Optional[float] = None
        self._logger: logging.Logger = get_uefi_logger(channel=UEFI_TRANSPORT_CHANNEL)
        return

    # -----------------------------------------------------------------------
    # Properties
    # -----------------------------------------------------------------------

    @property
    def connected(self) -> bool:
        """
            :returns: ``True`` when the underlying transport reports an
                      open, usable session; ``False`` otherwise.  The
                      read is a pure data accessor that delegates to the
                      underlying transport's :attr:`connected` property.
        """
        rtnval = bool(self._transport.connected)
        return rtnval

    @property
    def reconnect_count(self) -> int:
        """
            :returns: The number of successful :meth:`reconnect` calls
                      observed on this instance.  Starts at ``0`` and
                      monotonically increases.  Read-only; assignment
                      raises :class:`AttributeError`.
        """
        rtnval = self._reconnect_count
        return rtnval

    @property
    def aspects(self) -> UefiCmdAspects:
        """
            :returns: The instance-default :class:`UefiCmdAspects` captured
                      at construction.  Used by :meth:`reconnect` when a
                      caller does not pass a per-call override.
        """
        rtnval = self._aspects
        return rtnval

    @property
    def transport(self) -> IUefiTransport:
        """
            :returns: The wrapped :class:`IUefiTransport` instance.
        """
        rtnval = self._transport
        return rtnval

    @property
    def transport_kind(self) -> str:
        """
            :returns: The ``transport_kind`` string of the wrapped
                      transport (for example ``"serial"``,
                      ``"redfish"``, ``"ssh-host"``, or ``"ipmi-sol"``).
                      Returns ``"<unknown>"`` when the transport does not
                      expose the attribute.
        """
        rtnval = getattr(self._transport, "transport_kind", "<unknown>")
        return rtnval

    @property
    def host(self) -> str:
        """
            :returns: A stable identifier for the target host, drawn from
                      the underlying transport's ``host`` attribute when
                      available.  Returns ``"<unknown>"`` when the
                      transport does not expose the attribute.
        """
        rtnval = getattr(self._transport, "host", "<unknown>")
        return rtnval

    # -----------------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------------

    def connect(self, aspects: Optional[UefiCmdAspects] = None) -> None:
        """
            Establish the underlying transport connection.  Delegates to
            :meth:`IUefiTransport.connect` with the resolved aspects and
            emits a structured ``INFO`` event
            (:data:`EVENT_CONNECTION_OPEN`) on success.  Retry semantics
            are owned entirely by the underlying transport.

            :param aspects: Optional per-call :class:`UefiCmdAspects`; when
                            ``None`` the instance default is used.

            :raises: UefiTransportError
        """
        effective = resolve_aspects(passed=aspects, instance_default=self._aspects)

        begin_monotonic = time.monotonic()
        self._last_connect_begin = begin_monotonic

        try:
            self._transport.connect(aspects=effective)
        except UefiTransportError:
            raise
        except BaseException as xcpt:
            err_msg_lines = [
                "UefiConnection.connect: underlying transport raised a non-UEFI exception.",
                "TRANSPORT: {}".format(self.transport_kind),
                "HOST: {}".format(self.host),
                "CAUSE: {}: {}".format(type(xcpt).__name__, xcpt),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiTransportError(err_msg, details=list(err_msg_lines[1:])) from xcpt

        end_monotonic = time.monotonic()
        self._last_connect_end = end_monotonic
        elapsed_ms = int((end_monotonic - begin_monotonic) * 1000)

        self._logger.info(
            "uefi.transport: event=%s host=%s transport_kind=%s elapsed_ms=%d",
            EVENT_CONNECTION_OPEN, self.host, self.transport_kind, elapsed_ms,
        )
        return

    def disconnect(self, aspects: Optional[UefiCmdAspects] = None) -> None:
        """
            Tear down the underlying transport connection.  Delegates to
            :meth:`IUefiTransport.disconnect` and emits a structured
            ``INFO`` event (:data:`EVENT_CONNECTION_CLOSE`).  Does not
            suppress exceptions raised by the underlying transport; those
            are reclassified as :class:`UefiTransportError` with
            contextual ``details``.

            :param aspects: Optional per-call :class:`UefiCmdAspects`.  The
                            argument is accepted for symmetry with the
                            other lifecycle methods; the base
                            :class:`IUefiTransport` contract's
                            :meth:`disconnect` does not consume aspects,
                            so the resolved value is retained for
                            diagnostic use only.

            :raises: UefiTransportError
        """
        # Resolve aspects for symmetry; the call is validated even though
        # the underlying transport's disconnect does not consume it.
        resolve_aspects(passed=aspects, instance_default=self._aspects)

        try:
            self._transport.disconnect()
        except UefiTransportError:
            raise
        except BaseException as xcpt:
            err_msg_lines = [
                "UefiConnection.disconnect: underlying transport raised a non-UEFI exception.",
                "TRANSPORT: {}".format(self.transport_kind),
                "HOST: {}".format(self.host),
                "CAUSE: {}: {}".format(type(xcpt).__name__, xcpt),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiTransportError(err_msg, details=list(err_msg_lines[1:])) from xcpt

        self._logger.info(
            "uefi.transport: event=%s host=%s transport_kind=%s",
            EVENT_CONNECTION_CLOSE, self.host, self.transport_kind,
        )
        return

    def reconnect(self, aspects: Optional[UefiCmdAspects] = None) -> None:
        """
            Disconnect if currently connected then reconnect.  On success
            increments :attr:`reconnect_count` by exactly one and emits a
            structured ``INFO`` event (:data:`EVENT_CONNECTION_RECONNECT`)
            carrying the new count.

            The per-call ``aspects`` override is propagated to both the
            delegated :meth:`connect` and :meth:`disconnect` calls for the
            duration of the reconnect only; the stored instance default
            is never mutated.

            :param aspects: Optional per-call :class:`UefiCmdAspects`; when
                            ``None`` the instance default is used.

            :raises: UefiTransportError
        """
        effective = resolve_aspects(passed=aspects, instance_default=self._aspects)

        begin_monotonic = time.monotonic()

        if self.connected:
            self.disconnect(aspects=effective)

        self.connect(aspects=effective)

        self._reconnect_count += 1

        end_monotonic = time.monotonic()
        elapsed_ms = int((end_monotonic - begin_monotonic) * 1000)

        self._logger.info(
            "uefi.transport: event=%s host=%s transport_kind=%s reconnect_count=%d elapsed_ms=%d",
            EVENT_CONNECTION_RECONNECT, self.host, self.transport_kind,
            self._reconnect_count, elapsed_ms,
        )
        return

    # -----------------------------------------------------------------------
    # Context-manager hooks
    # -----------------------------------------------------------------------

    def __enter__(self) -> "UefiConnection":
        """
            Context-manager entry.  Calls :meth:`connect` with the
            instance-default aspects.

            :returns: ``self`` for ``as`` binding.
            :raises: UefiTransportError
        """
        self.connect()
        rtnval = self
        return rtnval

    def __exit__(self, exc_type: Optional[type],
                       exc_val: Optional[BaseException],
                       exc_tb: Optional[object]) -> None:
        """
            Context-manager exit.  Calls :meth:`disconnect` unconditionally
            so that an exception raised inside the ``with`` block does not
            leak a connected transport.  A :class:`UefiTransportError`
            raised by the underlying transport's disconnect is suppressed
            and logged so the original exception from the ``with`` block
            is not masked.
        """
        try:
            self.disconnect()
        except UefiTransportError as xcpt:
            self._logger.warning(
                "uefi.transport: event=%s host=%s transport_kind=%s suppressed_error=%r",
                EVENT_CONNECTION_CLOSE, self.host, self.transport_kind, xcpt,
            )
        return


__all__ = [
    "EVENT_CONNECTION_CLOSE",
    "EVENT_CONNECTION_OPEN",
    "EVENT_CONNECTION_RECONNECT",
    "UefiConnection",
]
