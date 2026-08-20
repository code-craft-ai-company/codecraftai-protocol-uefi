"""
    .. module:: uefiserialtransport
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Module containing :class:`UefiSerialTransport`, a layer-2
               byte-level transport that reaches a UEFI target's console
               over a locally-attached serial device or a network-wrapped
               serial concentrator exposed as ``socket://host:port`` or
               ``telnet://host:port``.  Satisfies :class:`IUefiTransport`
               structurally and consumes :class:`UefiCmdAspects` for all
               retry / inactivity semantics.  No behavior-modifying
               decorators are used; every retry, timeout, and inactivity
               cadence is driven by the aspects contract.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Code Craft AI LLC"
__credits__ = []


import os
import re
import socket
import time

from typing import Callable, Optional, Tuple, Union
from urllib.parse import urlparse

try:
    from serial import Serial, SerialException
except ImportError:
    # 'pyserial' is an optional extra.  We soft-import so that merely
    # importing this module does not fail in installs that do not use the
    # serial-console transport.  Construction of
    # :class:`UefiSerialTransport` raises :class:`ConfigurationError` when
    # 'pyserial' is not available.
    Serial = None  # type: ignore[assignment,misc]
    SerialException = None  # type: ignore[assignment,misc]

from ccai.protocols.uefi.uefiaspects import (
    DEFAULT_CMD_ASPECTS,
    ActionPattern,
    UefiCmdAspects,
    resolve_aspects,
)
from ccai.protocols.uefi.uefiexceptions import UefiTransportError
from ccai.protocols.uefi.uefilogging import (
    UEFI_TRANSPORT_CHANNEL,
    get_uefi_logger,
)
from ccai.protocols.uefi.uefiserialcredential import UefiSerialCredential

from ccai.errors.exceptions import ConfigurationError, SemanticError


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

ENDPOINT_SCHEME_LOCAL: str = "local"
ENDPOINT_SCHEME_SOCKET: str = "socket"
ENDPOINT_SCHEME_TELNET: str = "telnet"

ACCEPTED_SCHEMES: Tuple[str, ...] = (
    ENDPOINT_SCHEME_LOCAL,
    ENDPOINT_SCHEME_SOCKET,
    ENDPOINT_SCHEME_TELNET,
)

NEWLINE_MODE_AUTO: str = "auto"
NEWLINE_MODE_CRLF_TO_LF: str = "crlf_to_lf"
NEWLINE_MODE_CR_TO_LF: str = "cr_to_lf"
NEWLINE_MODE_NONE: str = "none"

ACCEPTED_NEWLINE_MODES: Tuple[str, ...] = (
    NEWLINE_MODE_AUTO,
    NEWLINE_MODE_CRLF_TO_LF,
    NEWLINE_MODE_CR_TO_LF,
    NEWLINE_MODE_NONE,
)

DEFAULT_READ_CHUNK: int = 4096

# Telnet IAC (Interpret-As-Command) byte.
TELNET_IAC: int = 0xFF


# ---------------------------------------------------------------------------
# Endpoint-parsing helpers
# ---------------------------------------------------------------------------

def _parse_endpoint(device: str) -> Tuple[str, str]:
    """
        Classify ``device`` into an endpoint ``(scheme, address)`` pair.
        Bare paths such as ``/dev/ttyUSB0`` and ``COM3`` are classified as
        the ``local`` scheme; ``socket://`` and ``telnet://`` URLs are
        classified accordingly.

        :param device: The credential's device string.

        :returns: A ``(scheme, address)`` tuple where ``scheme`` is one of
                  the literals in :data:`ACCEPTED_SCHEMES` and ``address``
                  is either the local device path or ``host:port``.
        :raises: ConfigurationError
    """
    if not isinstance(device, str) or len(device) == 0:
        err_msg_lines = [
            "_parse_endpoint requires a non-empty device string.",
            "GOT: value={!r} type={}".format(device, type(device).__name__),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise ConfigurationError(err_msg) from None

    if "://" not in device:
        rtnval = (ENDPOINT_SCHEME_LOCAL, device)
        return rtnval

    parsed = urlparse(device)
    scheme = parsed.scheme.lower()

    if scheme == ENDPOINT_SCHEME_SOCKET or scheme == ENDPOINT_SCHEME_TELNET:
        host = parsed.hostname
        port = parsed.port

        if host is None or port is None:
            err_msg_lines = [
                "Network-wrapped serial endpoint missing host or port.",
                "DEVICE: {}".format(device),
                "HOST: {}".format(host),
                "PORT: {}".format(port),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg) from None

        address = "{}:{}".format(host, port)
        rtnval = (scheme, address)
        return rtnval

    err_msg_lines = [
        "Unrecognized endpoint scheme for UEFI serial transport.",
        "GOT: scheme={!r}".format(scheme),
        "ACCEPTED: {}".format(", ".join(ACCEPTED_SCHEMES)),
    ]
    err_msg = os.linesep.join(err_msg_lines)
    raise ConfigurationError(err_msg) from None


def _split_host_port(address: str) -> Tuple[str, int]:
    """
        Split an ``address`` of form ``host:port`` into its typed parts.

        :param address: The address string returned by :func:`_parse_endpoint`.

        :returns: A ``(host, port)`` tuple.
        :raises: ConfigurationError
    """
    sep_index = address.rfind(":")
    if sep_index < 0:
        err_msg_lines = [
            "Network endpoint address missing ':' separator.",
            "GOT: address={!r}".format(address),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise ConfigurationError(err_msg) from None

    host = address[:sep_index]
    port_text = address[sep_index + 1:]

    try:
        port_value = int(port_text)
    except ValueError as verr:
        err_msg_lines = [
            "Network endpoint port is not an integer.",
            "ADDRESS: {}".format(address),
            "PORT TEXT: {!r}".format(port_text),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise ConfigurationError(err_msg) from verr

    rtnval = (host, port_value)
    return rtnval


# ---------------------------------------------------------------------------
# Line-ending normalization
# ---------------------------------------------------------------------------

def _make_normalizer(mode: str) -> Callable[[bytes], bytes]:
    """
        Build a stateful line-ending normalizer for the given mode.  The
        returned callable holds a single-byte carry buffer in its closure so
        that a ``\\r`` appearing at a chunk boundary is handled correctly.

        :param mode: One of the literals in :data:`ACCEPTED_NEWLINE_MODES`.

        :returns: A ``Callable[[bytes], bytes]`` suitable for inbound chunks.
        :raises: ConfigurationError
    """
    if mode not in ACCEPTED_NEWLINE_MODES:
        err_msg_lines = [
            "Unrecognized newline_mode for UEFI serial transport.",
            "GOT: value={!r}".format(mode),
            "ACCEPTED: {}".format(", ".join(ACCEPTED_NEWLINE_MODES)),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise ConfigurationError(err_msg) from None

    effective_mode = mode
    if effective_mode == NEWLINE_MODE_AUTO:
        effective_mode = NEWLINE_MODE_CRLF_TO_LF

    if effective_mode == NEWLINE_MODE_NONE:
        def _passthrough(chunk: bytes) -> bytes:
            rtnval = chunk
            return rtnval

        return _passthrough

    carry = bytearray()

    if effective_mode == NEWLINE_MODE_CRLF_TO_LF:
        def _crlf_to_lf(chunk: bytes) -> bytes:
            if len(chunk) == 0:
                rtnval = bytes(carry)
                del carry[:]
                return rtnval

            working = bytes(carry) + chunk
            del carry[:]

            # If the last byte is a bare CR, defer its decision to the next chunk.
            if len(working) > 0 and working[-1:] == b"\r":
                carry.append(ord(b"\r"))
                working = working[:-1]

            rtnval = working.replace(b"\r\n", b"\n")
            return rtnval

        return _crlf_to_lf

    # NEWLINE_MODE_CR_TO_LF
    def _cr_to_lf(chunk: bytes) -> bytes:
        if len(chunk) == 0:
            rtnval = bytes(carry).replace(b"\r", b"\n")
            del carry[:]
            return rtnval

        working = bytes(carry) + chunk
        del carry[:]

        # Defer a trailing CR to observe whether the next chunk starts with LF.
        if len(working) > 0 and working[-1:] == b"\r":
            carry.append(ord(b"\r"))
            working = working[:-1]

        # Collapse any \r\n first, then translate lone \r -> \n.
        working = working.replace(b"\r\n", b"\n")
        rtnval = working.replace(b"\r", b"\n")
        return rtnval

    return _cr_to_lf


def _strip_telnet_iac(buffer: bytearray, chunk: bytes) -> bytes:
    """
        Strip telnet IAC (0xFF) negotiation sequences from ``chunk``.
        ``buffer`` holds cross-chunk state: a partial IAC sequence carried
        over from a previous call.

        :param buffer: Mutable carry buffer holding any partial IAC bytes.
        :param chunk: Freshly-received bytes.

        :returns: The chunk with IAC sequences removed.
    """
    pending = bytes(buffer) + chunk
    del buffer[:]

    out = bytearray()
    index = 0
    length = len(pending)
    while index < length:
        byte_value = pending[index]

        if byte_value != TELNET_IAC:
            out.append(byte_value)
            index += 1
            continue

        # Need at least one follow-on byte to classify.
        if index + 1 >= length:
            buffer.extend(pending[index:])
            break

        command = pending[index + 1]

        # Escaped data byte: IAC IAC -> literal 0xFF.
        if command == TELNET_IAC:
            out.append(TELNET_IAC)
            index += 2
            continue

        # SB (0xFA) ... IAC SE (0xF0) — sub-negotiation; skip until IAC SE.
        if command == 0xFA:
            end_index = index + 2
            found_end = False
            while end_index + 1 < length:
                if pending[end_index] == TELNET_IAC and pending[end_index + 1] == 0xF0:
                    found_end = True
                    break
                end_index += 1

            if found_end:
                index = end_index + 2
                continue

            buffer.extend(pending[index:])
            break

        # Three-byte option negotiation (WILL/WONT/DO/DONT) — skip with its option byte.
        # Checked BEFORE the two-byte command range so the option byte is consumed.
        if command in (0xFB, 0xFC, 0xFD, 0xFE):
            if index + 2 >= length:
                buffer.extend(pending[index:])
                break
            index += 3
            continue

        # Two-byte telnet command (NOP, DM, BRK, IP, AO, AYT, EC, EL, GA, SE).
        if 0xF0 <= command <= 0xFA:
            index += 2
            continue

        # Unknown — drop the IAC pair.
        index += 2

    rtnval = bytes(out)
    return rtnval


# ---------------------------------------------------------------------------
# UefiSerialTransport
# ---------------------------------------------------------------------------

class UefiSerialTransport:
    """
        Layer-2 byte-level UEFI transport that speaks local POSIX / Windows
        serial devices via :mod:`pyserial` and network-wrapped serial
        concentrators (``socket://`` and ``telnet://``) via a raw TCP
        socket.  Satisfies :class:`IUefiTransport` structurally.  Retry /
        inactivity semantics are driven entirely by the supplied
        :class:`UefiCmdAspects` — no behavior-modifying decorators are
        used.

        :param credential: :class:`UefiSerialCredential` describing the
                           serial endpoint to attach to.
        :param aspects: Default :class:`UefiCmdAspects` applied when a
                        caller-supplied ``aspects`` argument is ``None``.
        :param newline_mode: Inbound line-ending normalization mode; one
                             of :data:`ACCEPTED_NEWLINE_MODES`.

        :raises: ConfigurationError
    """

    transport_kind: str = "serial"

    def __init__(self, credential: UefiSerialCredential,
                       aspects: UefiCmdAspects = DEFAULT_CMD_ASPECTS,
                       newline_mode: str = NEWLINE_MODE_AUTO) -> None:
        """
            Initialize the transport.  All instance variables are declared
            here per the project coding standards — no lazy attribute
            creation elsewhere.

            :param credential: :class:`UefiSerialCredential` describing the
                               serial endpoint.
            :param aspects: Default :class:`UefiCmdAspects` for this
                            transport.
            :param newline_mode: Inbound line-ending normalization mode.

            :raises: ConfigurationError
        """
        if Serial is None:
            err_msg_lines = [
                "UefiSerialTransport requires the 'pyserial' package.",
                "Install the 'serial' extra to use this transport:",
                "    pip install codecraftai-protocol-uefi[serial]",
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg) from None

        if not isinstance(credential, UefiSerialCredential):
            err_msg_lines = [
                "UefiSerialTransport requires a UefiSerialCredential.",
                "GOT: value={!r} type={}".format(credential, type(credential).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg) from None

        if not isinstance(aspects, UefiCmdAspects):
            err_msg_lines = [
                "UefiSerialTransport requires aspects to be a UefiCmdAspects.",
                "GOT: value={!r} type={}".format(aspects, type(aspects).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg) from None

        scheme, address = _parse_endpoint(device=credential.device)

        self._credential: UefiSerialCredential = credential
        self._aspects: UefiCmdAspects = aspects
        self._newline_mode: str = newline_mode
        self._scheme: str = scheme
        self._address: str = address

        self._serial: Optional[Serial] = None
        self._socket: Optional[socket.socket] = None
        self._telnet_carry: bytearray = bytearray()
        self._read_buffer: bytearray = bytearray()
        self._normalize: Callable[[bytes], bytes] = _make_normalizer(mode=newline_mode)
        self._connected: bool = False
        self._attempt_count: int = 0
        self._last_connect_exc: Optional[BaseException] = None

        self._logger = get_uefi_logger(channel=UEFI_TRANSPORT_CHANNEL)
        return

    # -----------------------------------------------------------------------
    # Properties
    # -----------------------------------------------------------------------

    @property
    def connected(self) -> bool:
        """
            :returns: ``True`` when the transport currently owns an open
                      byte pipe to the target, ``False`` otherwise.
        """
        rtnval = self._connected
        return rtnval

    @property
    def endpoint_scheme(self) -> str:
        """
            :returns: The resolved endpoint scheme literal (``"local"``,
                      ``"socket"``, or ``"telnet"``).
        """
        rtnval = self._scheme
        return rtnval

    # -----------------------------------------------------------------------
    # Connect / disconnect
    # -----------------------------------------------------------------------

    def connect(self, aspects: Optional[UefiCmdAspects] = None) -> None:
        """
            Establish the underlying byte pipe to the target.  Honors
            ``aspects.action_pattern``, ``aspects.connection_timeout``,
            ``aspects.connection_interval``,
            ``aspects.allowed_connection_exceptions``, and
            ``aspects.must_connect``.

            :param aspects: Optional per-call :class:`UefiCmdAspects`; when
                            ``None`` the instance default is used.

            :raises: UefiTransportError, SemanticError, ConfigurationError
        """
        effective = resolve_aspects(passed=aspects, instance_default=self._aspects)

        if effective.action_pattern == ActionPattern.DO_WHILE_SUCCESS:
            err_msg_lines = [
                "ActionPattern.DO_WHILE_SUCCESS is not meaningful for connect().",
                "ENDPOINT: {}".format(self._credential.device),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise SemanticError(err_msg) from None

        if effective.action_pattern == ActionPattern.SINGLE_CONNECTED_CALL and self._connected:
            self._logger.debug(
                "UefiSerialTransport.connect: SINGLE_CONNECTED_CALL on already-connected transport; no-op."
            )
            return

        if effective.action_pattern == ActionPattern.DO_UNTIL_CONNECTION_FAILURE:
            self._wait_for_connection_failure(effective=effective)
            return

        self._logger.info(
            "UefiSerialTransport.connect: begin endpoint={} scheme={} baud={}".format(
                self._credential.device, self._scheme, self._credential.baud
            )
        )

        allowed = tuple(effective.allowed_connection_exceptions)
        start = time.monotonic()
        deadline = start + float(effective.connection_timeout)

        self._attempt_count = 0
        self._last_connect_exc = None

        while True:
            self._attempt_count += 1
            try:
                self._open_backend()
                self._connected = True
                elapsed = time.monotonic() - start
                self._logger.info(
                    "UefiSerialTransport.connect: success endpoint={} attempts={} elapsed={:.3f}s".format(
                        self._credential.device, self._attempt_count, elapsed
                    )
                )
                return

            except Exception as xcpt:
                self._last_connect_exc = xcpt

                if effective.must_connect:
                    err_msg_lines = [
                        "UefiSerialTransport.connect: must_connect=True; failing fast.",
                        "ENDPOINT: {}".format(self._credential.device),
                        "ATTEMPT: {}".format(self._attempt_count),
                        "CAUSE: {}: {}".format(type(xcpt).__name__, xcpt),
                    ]
                    err_msg = os.linesep.join(err_msg_lines)
                    raise UefiTransportError(err_msg, details=list(err_msg_lines[1:])) from xcpt

                if len(allowed) > 0 and not isinstance(xcpt, allowed):
                    err_msg_lines = [
                        "UefiSerialTransport.connect: unhandled exception type.",
                        "ENDPOINT: {}".format(self._credential.device),
                        "ATTEMPT: {}".format(self._attempt_count),
                        "CAUSE: {}: {}".format(type(xcpt).__name__, xcpt),
                    ]
                    err_msg = os.linesep.join(err_msg_lines)
                    raise UefiTransportError(err_msg, details=list(err_msg_lines[1:])) from xcpt

                if effective.action_pattern == ActionPattern.SINGLE_CALL:
                    err_msg_lines = [
                        "UefiSerialTransport.connect: SINGLE_CALL failed.",
                        "ENDPOINT: {}".format(self._credential.device),
                        "ATTEMPT: {}".format(self._attempt_count),
                        "CAUSE: {}: {}".format(type(xcpt).__name__, xcpt),
                    ]
                    err_msg = os.linesep.join(err_msg_lines)
                    raise UefiTransportError(err_msg, details=list(err_msg_lines[1:])) from xcpt

                if effective.action_pattern == ActionPattern.SINGLE_CONNECTED_CALL:
                    err_msg_lines = [
                        "UefiSerialTransport.connect: SINGLE_CONNECTED_CALL attempt failed.",
                        "ENDPOINT: {}".format(self._credential.device),
                        "ATTEMPT: {}".format(self._attempt_count),
                        "CAUSE: {}: {}".format(type(xcpt).__name__, xcpt),
                    ]
                    err_msg = os.linesep.join(err_msg_lines)
                    raise UefiTransportError(err_msg, details=list(err_msg_lines[1:])) from xcpt

                self._logger.warning(
                    "UefiSerialTransport.connect: soft-failure retry endpoint={} attempt={} cause={}: {}".format(
                        self._credential.device, self._attempt_count, type(xcpt).__name__, xcpt
                    )
                )

                now = time.monotonic()
                if now >= deadline:
                    elapsed = now - start
                    err_msg_lines = [
                        "UefiSerialTransport.connect: connection_timeout elapsed.",
                        "ENDPOINT: {}".format(self._credential.device),
                        "ATTEMPTS: {}".format(self._attempt_count),
                        "ELAPSED: {:.3f}s".format(elapsed),
                        "LAST EXCEPTION: {}: {}".format(type(xcpt).__name__, xcpt),
                    ]
                    err_msg = os.linesep.join(err_msg_lines)
                    terr = TimeoutError(err_msg)
                    terr.details = list(err_msg_lines[1:])
                    raise terr from xcpt

                sleep_for = float(effective.connection_interval)
                remaining = deadline - now
                if sleep_for > remaining:
                    sleep_for = max(0.0, remaining)

                time.sleep(sleep_for)

    def disconnect(self) -> None:
        """
            Tear down the byte pipe.  Never raises.  Emits a ``DEBUG`` log
            line on completion.  A double-disconnect is a no-op.
        """
        if not self._connected and self._serial is None and self._socket is None:
            self._logger.debug(
                "UefiSerialTransport.disconnect: already-disconnected endpoint={}; no-op.".format(
                    self._credential.device
                )
            )
            return

        if self._serial is not None:
            try:
                self._serial.close()
            except Exception as xcpt:
                self._logger.warning(
                    "UefiSerialTransport.disconnect: Serial.close() raised endpoint={} cause={}: {}".format(
                        self._credential.device, type(xcpt).__name__, xcpt
                    )
                )
            self._serial = None

        if self._socket is not None:
            try:
                self._socket.shutdown(socket.SHUT_RDWR)
            except Exception as xcpt:
                self._logger.debug(
                    "UefiSerialTransport.disconnect: socket.shutdown() raised endpoint={} cause={}: {}".format(
                        self._credential.device, type(xcpt).__name__, xcpt
                    )
                )
            try:
                self._socket.close()
            except Exception as xcpt:
                self._logger.warning(
                    "UefiSerialTransport.disconnect: socket.close() raised endpoint={} cause={}: {}".format(
                        self._credential.device, type(xcpt).__name__, xcpt
                    )
                )
            self._socket = None

        self._connected = False
        del self._telnet_carry[:]
        self._logger.debug(
            "UefiSerialTransport.disconnect: closed endpoint={}".format(self._credential.device)
        )
        return

    # -----------------------------------------------------------------------
    # Byte I/O
    # -----------------------------------------------------------------------

    def send(self, data: bytes) -> int:
        """
            Send ``data`` toward the target.  Outbound bytes are emitted
            literally — no line-ending normalization is applied on send.

            :param data: Bytes to write to the byte pipe.

            :returns: The number of bytes actually written.
            :raises: UefiTransportError
        """
        if not isinstance(data, (bytes, bytearray, memoryview)):
            err_msg_lines = [
                "UefiSerialTransport.send requires a bytes-like buffer.",
                "GOT: value={!r} type={}".format(data, type(data).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiTransportError(err_msg) from None

        if not self._connected:
            err_msg_lines = [
                "UefiSerialTransport.send called on a disconnected transport.",
                "ENDPOINT: {}".format(self._credential.device),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiTransportError(err_msg) from None

        payload = bytes(data)

        try:
            if self._serial is not None:
                written = self._serial.write(payload)
                if written is None:
                    # Some pyserial versions return None on a successful write.
                    written = len(payload)
            elif self._socket is not None:
                self._socket.sendall(payload)
                written = len(payload)
            else:
                err_msg_lines = [
                    "UefiSerialTransport.send has no open backend.",
                    "ENDPOINT: {}".format(self._credential.device),
                ]
                err_msg = os.linesep.join(err_msg_lines)
                raise UefiTransportError(err_msg) from None

        except UefiTransportError:
            raise
        except Exception as xcpt:
            err_msg_lines = [
                "UefiSerialTransport.send failed writing to the byte pipe.",
                "ENDPOINT: {}".format(self._credential.device),
                "BYTES: {}".format(len(payload)),
                "CAUSE: {}: {}".format(type(xcpt).__name__, xcpt),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiTransportError(err_msg, details=list(err_msg_lines[1:])) from xcpt

        if written > 0:
            self._logger.debug(
                "UefiSerialTransport.send: wrote {} bytes to {}".format(written, self._credential.device)
            )

        rtnval = int(written)
        return rtnval

    def recv(self, max_bytes: int, timeout: float) -> bytes:
        """
            Receive up to ``max_bytes`` from the target, waiting at most
            ``timeout`` seconds.  A ``timeout`` of ``0`` is treated as a
            non-blocking poll.

            :param max_bytes: Upper bound on the number of returned bytes.
            :param timeout: Seconds to wait for data; ``0`` is non-blocking.

            :returns: The accumulated bytes; may be shorter than
                      ``max_bytes`` and may be empty if the timeout
                      elapsed with no bytes seen.
            :raises: UefiTransportError
        """
        if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes <= 0:
            err_msg_lines = [
                "UefiSerialTransport.recv requires a positive int for max_bytes.",
                "GOT: value={!r} type={}".format(max_bytes, type(max_bytes).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiTransportError(err_msg) from None

        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout < 0:
            err_msg_lines = [
                "UefiSerialTransport.recv requires a non-negative number for timeout.",
                "GOT: value={!r} type={}".format(timeout, type(timeout).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiTransportError(err_msg) from None

        if not self._connected:
            err_msg_lines = [
                "UefiSerialTransport.recv called on a disconnected transport.",
                "ENDPOINT: {}".format(self._credential.device),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiTransportError(err_msg) from None

        # First drain the internal buffer.
        buffered = self._drain_buffer(max_bytes=max_bytes)
        if len(buffered) > 0:
            rtnval = buffered
            return rtnval

        deadline = time.monotonic() + float(timeout)
        collected = bytearray()

        while len(collected) < max_bytes:
            remaining = deadline - time.monotonic()
            if remaining < 0:
                remaining = 0.0

            want = max_bytes - len(collected)
            chunk = self._read_chunk(want=want, timeout=remaining)

            if len(chunk) > 0:
                normalized = self._normalize(chunk)
                collected.extend(normalized)

            if remaining <= 0:
                break

        rtnval = bytes(collected[:max_bytes])
        if len(collected) > max_bytes:
            self._read_buffer[:0] = collected[max_bytes:]

        if len(rtnval) > 0:
            self._logger.debug(
                "UefiSerialTransport.recv: read {} bytes from {}".format(len(rtnval), self._credential.device)
            )

        return rtnval

    def recv_until(self, pattern: Union[bytes, "re.Pattern[bytes]"],
                         timeout: float,
                         aspects: Optional[UefiCmdAspects] = None) -> bytes:
        """
            Accumulate bytes from the target until ``pattern`` is observed
            or ``timeout`` elapses.  On timeout, raises :class:`TimeoutError`
            whose ``.details`` attribute carries the partial bytes observed
            during the call (as a ``"PARTIAL: <repr>"`` entry) and whose
            ``.partial`` attribute contains the raw :class:`bytes`.

            :param pattern: Either a bytes literal or a compiled
                            :class:`re.Pattern` of bytes.
            :param timeout: Maximum seconds to wait for the pattern.
            :param aspects: Optional per-call :class:`UefiCmdAspects`; when
                            ``None`` the instance default is used.

            :returns: Every byte collected from the start of the call up
                      to and including the first pattern occurrence.
            :raises: TimeoutError, UefiTransportError, ConfigurationError
        """
        if not isinstance(pattern, (bytes, bytearray, re.Pattern)):
            err_msg_lines = [
                "UefiSerialTransport.recv_until requires bytes or a compiled re.Pattern[bytes].",
                "GOT: value={!r} type={}".format(pattern, type(pattern).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiTransportError(err_msg) from None

        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout < 0:
            err_msg_lines = [
                "UefiSerialTransport.recv_until requires a non-negative number for timeout.",
                "GOT: value={!r} type={}".format(timeout, type(timeout).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiTransportError(err_msg) from None

        if not self._connected:
            err_msg_lines = [
                "UefiSerialTransport.recv_until called on a disconnected transport.",
                "ENDPOINT: {}".format(self._credential.device),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiTransportError(err_msg) from None

        effective = resolve_aspects(passed=aspects, instance_default=self._aspects)

        if isinstance(pattern, (bytes, bytearray)):
            needle: Union[bytes, "re.Pattern[bytes]"] = bytes(pattern)
        else:
            needle = pattern

        deadline = time.monotonic() + float(timeout)
        inactivity_interval = float(effective.inactivity_interval)
        last_progress = time.monotonic()
        call_partial = bytearray()

        # Carry the already-buffered bytes into this call's partial view.
        if len(self._read_buffer) > 0:
            call_partial.extend(self._read_buffer)

        # Check if pattern is already satisfied by buffered bytes.
        match_end = _find_match_end(buffer=self._read_buffer, needle=needle)
        if match_end is not None:
            rtnval = bytes(self._read_buffer[:match_end])
            del self._read_buffer[:match_end]
            self._logger.debug(
                "UefiSerialTransport.recv_until: immediate match bytes={} endpoint={}".format(
                    len(rtnval), self._credential.device
                )
            )
            return rtnval

        while True:
            now = time.monotonic()
            remaining = deadline - now
            if remaining <= 0:
                pattern_text = _render_pattern(needle=needle)
                partial_bytes = bytes(self._read_buffer)
                err_msg_lines = [
                    "UefiSerialTransport.recv_until: timeout before pattern observed.",
                    "ENDPOINT: {}".format(self._credential.device),
                    "PATTERN: {}".format(pattern_text),
                    "TIMEOUT: {:.3f}s".format(float(timeout)),
                    "PARTIAL LENGTH: {}".format(len(partial_bytes)),
                    "PARTIAL: {!r}".format(partial_bytes),
                ]
                err_msg = os.linesep.join(err_msg_lines)

                self._logger.warning(
                    "UefiSerialTransport.recv_until: TIMEOUT endpoint={} pattern={} timeout={:.3f}s partial_length={}".format(
                        self._credential.device, pattern_text, float(timeout), len(partial_bytes)
                    )
                )

                terr = TimeoutError(err_msg)
                terr.details = list(err_msg_lines[1:])
                terr.partial = partial_bytes
                raise terr from None

            slice_timeout = min(remaining, inactivity_interval)
            chunk = self._read_chunk(want=DEFAULT_READ_CHUNK, timeout=slice_timeout)

            if len(chunk) > 0:
                normalized = self._normalize(chunk)
                self._read_buffer.extend(normalized)
                call_partial.extend(normalized)
                last_progress = time.monotonic()

                match_end = _find_match_end(buffer=self._read_buffer, needle=needle)
                if match_end is not None:
                    rtnval = bytes(self._read_buffer[:match_end])
                    del self._read_buffer[:match_end]
                    self._logger.debug(
                        "UefiSerialTransport.recv_until: match bytes={} endpoint={}".format(
                            len(rtnval), self._credential.device
                        )
                    )
                    return rtnval
                continue

            idle_for = time.monotonic() - last_progress
            if idle_for >= inactivity_interval:
                self._logger.debug(
                    "UefiSerialTransport.recv_until: no bytes in last {:.3f}s endpoint={} buffered={}".format(
                        idle_for, self._credential.device, len(self._read_buffer)
                    )
                )
                last_progress = time.monotonic()

    # -----------------------------------------------------------------------
    # Context-manager hooks
    # -----------------------------------------------------------------------

    def __enter__(self) -> "UefiSerialTransport":
        """
            Context-manager entry.  Calls :meth:`connect` with the default
            aspects.

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
            and never raises regardless of whether connect succeeded.
        """
        self.disconnect()
        return

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    def _open_backend(self) -> None:
        """
            Open the backend appropriate to the resolved endpoint scheme.

            :raises: ConfigurationError, SerialException, OSError
        """
        if self._scheme == ENDPOINT_SCHEME_LOCAL:
            self._open_local()
            return

        if self._scheme == ENDPOINT_SCHEME_SOCKET:
            self._open_socket(strip_iac=False)
            return

        if self._scheme == ENDPOINT_SCHEME_TELNET:
            self._open_socket(strip_iac=True)
            return

        err_msg_lines = [
            "UefiSerialTransport._open_backend: unsupported scheme.",
            "GOT: value={!r}".format(self._scheme),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise ConfigurationError(err_msg) from None

    def _open_local(self) -> None:
        """
            Open the pyserial backend for a local device.
        """
        flow_xonxoff = (self._credential.flow_control == "xonxoff")
        flow_rtscts = (self._credential.flow_control == "rtscts")
        flow_dsrdtr = False  # pyserial's Serial has no 'dsrdtr' in the credential allow-list.

        parity_value = self._credential.parity

        handle = Serial(
            port=self._address,
            baudrate=int(self._credential.baud),
            bytesize=int(self._credential.bytesize),
            parity=parity_value,
            stopbits=float(self._credential.stopbits),
            xonxoff=flow_xonxoff,
            rtscts=flow_rtscts,
            dsrdtr=flow_dsrdtr,
            timeout=0,
            write_timeout=None,
        )

        self._serial = handle
        return

    def _open_socket(self, strip_iac: bool) -> None:
        """
            Open a raw TCP socket for a ``socket://`` or ``telnet://``
            endpoint.  The ``strip_iac`` flag is carried implicitly via
            the pre-resolved :attr:`_scheme`; the ``strip_iac`` argument
            is kept for call-site readability.

            :param strip_iac: Whether to strip telnet IAC sequences from
                              the inbound stream.
        """
        host, port = _split_host_port(address=self._address)

        sock = socket.create_connection(address=(host, port))
        sock.settimeout(0.0)

        self._socket = sock
        del self._telnet_carry[:]

        if strip_iac and self._scheme != ENDPOINT_SCHEME_TELNET:
            self._scheme = ENDPOINT_SCHEME_TELNET
        return

    def _wait_for_connection_failure(self, effective: UefiCmdAspects) -> None:
        """
            Implements the DO_UNTIL_CONNECTION_FAILURE action pattern.  The
            transport must already be connected; the method returns normally
            once the connection is observed to be broken.  If called on a
            disconnected transport, raises :class:`UefiTransportError`.

            :param effective: The effective aspects for this call.

            :raises: UefiTransportError
        """
        if not self._connected:
            err_msg_lines = [
                "UefiSerialTransport.connect: DO_UNTIL_CONNECTION_FAILURE requires an established connection.",
                "ENDPOINT: {}".format(self._credential.device),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiTransportError(err_msg) from None

        interval = float(effective.connection_interval)

        while True:
            alive = self._probe_liveness()
            if not alive:
                err_msg_lines = [
                    "UefiSerialTransport.connect: liveness probe detected connection failure.",
                    "ENDPOINT: {}".format(self._credential.device),
                ]
                err_msg = os.linesep.join(err_msg_lines)
                raise UefiTransportError(err_msg, details=list(err_msg_lines[1:])) from None

            time.sleep(interval)

    def _probe_liveness(self) -> bool:
        """
            Best-effort check whether the byte pipe is still open.

            :returns: ``True`` when the backend reports alive; ``False``
                      otherwise.
        """
        if self._serial is not None:
            is_open = bool(getattr(self._serial, "is_open", False))
            return is_open

        if self._socket is not None:
            try:
                self._socket.send(b"")
                rtnval = True
            except Exception:
                rtnval = False
            return rtnval

        rtnval = False
        return rtnval

    def _drain_buffer(self, max_bytes: int) -> bytes:
        """
            Pull up to ``max_bytes`` from the already-buffered bytes.

            :param max_bytes: Upper bound on returned bytes.

            :returns: The drained bytes; may be empty if the buffer is
                      empty.
        """
        buffered_len = len(self._read_buffer)
        if buffered_len == 0:
            rtnval = b""
            return rtnval

        take = min(buffered_len, max_bytes)
        rtnval = bytes(self._read_buffer[:take])
        del self._read_buffer[:take]
        return rtnval

    def _read_chunk(self, want: int, timeout: float) -> bytes:
        """
            Pull a chunk from the active backend, blocking up to ``timeout``
            seconds.  Returns ``b""`` when no bytes arrive in the window.

            :param want: Upper bound on returned bytes.
            :param timeout: Seconds to wait for bytes.

            :returns: The freshly-read bytes; may be empty.
        """
        if self._serial is not None:
            chunk = self._read_from_serial(want=want, timeout=timeout)
            return chunk

        if self._socket is not None:
            chunk = self._read_from_socket(want=want, timeout=timeout)
            if self._scheme == ENDPOINT_SCHEME_TELNET and len(chunk) > 0:
                chunk = _strip_telnet_iac(buffer=self._telnet_carry, chunk=chunk)
            return chunk

        rtnval = b""
        return rtnval

    def _read_from_serial(self, want: int, timeout: float) -> bytes:
        """
            Read from the pyserial backend.  Uses the underlying
            ``Serial.read(size)`` after setting ``Serial.timeout`` to the
            caller's window.

            :param want: Upper bound on returned bytes.
            :param timeout: Seconds to wait.

            :returns: The freshly-read bytes.
        """
        handle = self._serial
        if handle is None:
            rtnval = b""
            return rtnval

        try:
            handle.timeout = float(timeout)
        except Exception:
            # Some backends reject per-call timeout changes; fall through.
            pass

        deadline = time.monotonic() + float(timeout)
        out = bytearray()

        while len(out) < want:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break

            try:
                handle.timeout = float(remaining)
            except Exception:
                pass

            try:
                piece = handle.read(want - len(out))
            except SerialException:
                raise
            except Exception:
                raise

            if piece is None or len(piece) == 0:
                break

            out.extend(piece)

            # Drain anything else immediately available.
            try:
                in_waiting = int(getattr(handle, "in_waiting", 0))
            except Exception:
                in_waiting = 0

            if in_waiting <= 0:
                break

        rtnval = bytes(out)
        return rtnval

    def _read_from_socket(self, want: int, timeout: float) -> bytes:
        """
            Read from the socket backend with a bounded timeout.

            :param want: Upper bound on returned bytes.
            :param timeout: Seconds to wait.

            :returns: The freshly-read bytes.
        """
        sock = self._socket
        if sock is None:
            rtnval = b""
            return rtnval

        try:
            sock.settimeout(float(timeout))
        except Exception:
            pass

        try:
            piece = sock.recv(want)
        except socket.timeout:
            rtnval = b""
            return rtnval
        except BlockingIOError:
            rtnval = b""
            return rtnval
        except OSError:
            raise

        if piece is None:
            rtnval = b""
            return rtnval

        rtnval = bytes(piece)
        return rtnval


# ---------------------------------------------------------------------------
# Pattern-matching helper
# ---------------------------------------------------------------------------

def _find_match_end(buffer: bytearray,
                    needle: Union[bytes, "re.Pattern[bytes]"]) -> Optional[int]:
    """
        Search ``buffer`` for the first occurrence of ``needle`` and return
        the index immediately after the match end, or ``None`` when no
        match is present.

        :param buffer: Accumulated inbound bytes.
        :param needle: Either bytes or a compiled ``re.Pattern[bytes]``.

        :returns: The post-match index or ``None``.
    """
    if isinstance(needle, (bytes, bytearray)):
        as_bytes = bytes(needle)
        if len(as_bytes) == 0:
            rtnval = 0
            return rtnval
        index = buffer.find(as_bytes)
        if index < 0:
            return None
        rtnval = index + len(as_bytes)
        return rtnval

    # Compiled pattern
    view = bytes(buffer)
    match = needle.search(view)
    if match is None:
        return None

    rtnval = match.end()
    return rtnval


def _render_pattern(needle: Union[bytes, "re.Pattern[bytes]"]) -> str:
    """
        Produce a readable representation of ``needle`` for error / log
        messages without embedding credential material (there is none in
        this module).

        :param needle: Either bytes or a compiled ``re.Pattern[bytes]``.

        :returns: A short human-readable string.
    """
    if isinstance(needle, (bytes, bytearray)):
        rtnval = repr(bytes(needle))
        return rtnval

    rtnval = "re.Pattern({!r})".format(needle.pattern)
    return rtnval


__all__ = [
    "ACCEPTED_NEWLINE_MODES",
    "ACCEPTED_SCHEMES",
    "DEFAULT_READ_CHUNK",
    "ENDPOINT_SCHEME_LOCAL",
    "ENDPOINT_SCHEME_SOCKET",
    "ENDPOINT_SCHEME_TELNET",
    "NEWLINE_MODE_AUTO",
    "NEWLINE_MODE_CRLF_TO_LF",
    "NEWLINE_MODE_CR_TO_LF",
    "NEWLINE_MODE_NONE",
    "UefiSerialTransport",
]
