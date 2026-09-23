"""
    .. module:: uefiipmisoltransport
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Module containing the :class:`UefiIpmiSolTransport` class, a
               layer-2 byte-level transport that reaches a UEFI target's
               console over IPMI Serial-Over-LAN.  Two backends are
               supported: a default ``ipmitool`` subprocess backend that
               drives ``ipmitool sol activate`` / ``ipmitool sol
               deactivate`` and a soft-imported ``pyghmi`` backend that
               negotiates the SOL payload in-process.  The transport
               satisfies :class:`ccai.protocols.uefi.uefiinterfaces.IUefiTransport`.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Code Craft AI LLC"
__credits__ = []


import os
import re
import select
import subprocess
import time

from typing import Any, List, Optional, Union

from ccai.protocols.uefi.uefiaspects import (
    DEFAULT_CMD_ASPECTS,
    UefiCmdAspects,
)
from ccai.protocols.uefi.uefiexceptions import UefiTransportError
from ccai.protocols.uefi.uefiipmicredential import UefiIpmiCredential


# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

SOL_ALREADY_ACTIVE_SENTINEL = "SOL already active"
DEACTIVATION_FAILED_SENTINEL = "Deactivation failed"

SOL_ESCAPE_SEQUENCE = b"~."

TERMINATE_TO_KILL_GRACE_SECONDS = 5.0

DEFAULT_RECV_CHUNK = 4096


# ---------------------------------------------------------------------------
# UefiIpmiSolTransport
# ---------------------------------------------------------------------------

class UefiIpmiSolTransport:
    """
        :class:`UefiIpmiSolTransport` provides an IPMI Serial-Over-LAN
        transport to a UEFI target.  The transport speaks the same byte-
        level protocol as a serial transport but carries bytes over the
        BMC's IPMI SOL payload rather than a direct UART.

        The transport uses one of two backends:

        * A default ``ipmitool`` subprocess backend that spawns
          ``ipmitool sol activate`` and pumps its stdio.
        * An optional ``pyghmi`` backend (selected with
          ``use_pyghmi=True``) that negotiates the SOL payload inside
          this process.  ``pyghmi`` is soft-imported on demand so that
          minimal CI images that do not install the package still work
          with the default subprocess backend.

        The caller may request eviction of an existing SOL session with
        ``evict_existing=True``; when ``False`` (the default) an
        "SOL already active" condition raises
        :class:`ccai.protocols.uefi.uefiexceptions.UefiTransportError`.
    """

    def __init__(self, credential: UefiIpmiCredential,
                       aspects: UefiCmdAspects = DEFAULT_CMD_ASPECTS,
                       evict_existing: bool = False,
                       use_pyghmi: bool = False) -> None:
        """
            Construct a new :class:`UefiIpmiSolTransport`.  All instance
            variables are initialized here; nothing is lazily introduced
            in later methods.

            :param credential: The :class:`UefiIpmiCredential` describing
                               the BMC connection.
            :param aspects: The measured-interop behavior package used
                            when the caller does not pass an explicit
                            ``aspects`` to a public method.
            :param evict_existing: When ``True``, an active SOL session
                                   on the target is first deactivated;
                                   when ``False`` (the default) an
                                   active session causes ``connect()``
                                   to raise.
            :param use_pyghmi: When ``True``, use the ``pyghmi`` backend;
                               when ``False`` (the default) use the
                               ``ipmitool`` subprocess backend.
        """
        self._credential = credential
        self._aspects = aspects
        self._evict_existing = evict_existing
        self._use_pyghmi = use_pyghmi

        self._process: Optional[subprocess.Popen] = None
        self._pyghmi_console: Optional[Any] = None
        self._pyghmi_rx_buffer: bytearray = bytearray()

        self._read_buffer: bytearray = bytearray()
        self._connected: bool = False
        self._session_start_ts: Optional[float] = None
        return

    # -----------------------------------------------------------------
    # Properties
    # -----------------------------------------------------------------

    @property
    def connected(self) -> bool:
        """
            Report the connection state.

            :returns: ``True`` when the transport has an open SOL
                      session; ``False`` otherwise.
        """
        rtnval = self._connected
        return rtnval

    @property
    def transport_kind(self) -> str:
        """
            :returns: The transport-kind string ``"ipmi-sol"``.
        """
        rtnval = "ipmi-sol"
        return rtnval

    # -----------------------------------------------------------------
    # Connect / disconnect
    # -----------------------------------------------------------------

    def connect(self, aspects: Optional[UefiCmdAspects] = None) -> None:
        """
            Activate the SOL session.  Honors
            ``aspects.connection_timeout`` and
            ``aspects.connection_interval`` by retrying activation until
            either success or the deadline.

            :param aspects: Optional per-call overrides.  When ``None``
                            the instance default supplied at
                            construction is used.

            :raises: UefiTransportError
        """
        if aspects is not None:
            effective_aspects = aspects
        else:
            effective_aspects = self._aspects

        deadline = time.monotonic() + float(effective_aspects.connection_timeout)
        interval = float(effective_aspects.connection_interval)
        attempt_count = 0
        last_exc: Optional[BaseException] = None

        while True:
            attempt_count += 1
            try:
                if self._use_pyghmi:
                    self._connect_pyghmi()
                else:
                    self._connect_ipmitool()

                self._session_start_ts = time.monotonic()
                self._connected = True
                return
            except UefiTransportError as xcpt:
                last_exc = xcpt
                message_text = str(xcpt)
                if DEACTIVATION_FAILED_SENTINEL in message_text:
                    # "Deactivation failed" is not retryable; surface immediately.
                    raise
                if SOL_ALREADY_ACTIVE_SENTINEL in message_text and not self._evict_existing:
                    # Caller opted out of eviction; surface immediately.
                    raise

            if time.monotonic() >= deadline:
                err_msg_lines = [
                    "Timed out activating SOL session.",
                    "HOST: {}".format(self._credential.host),
                    "BACKEND: {}".format("pyghmi" if self._use_pyghmi else "ipmitool"),
                    "ATTEMPTS: {}".format(attempt_count),
                    "CONNECTION_TIMEOUT: {}".format(effective_aspects.connection_timeout),
                    "LAST_ERROR: {!r}".format(last_exc),
                ]
                err_msg = os.linesep.join(err_msg_lines)
                raise UefiTransportError(err_msg, details=list(err_msg_lines[1:])) from last_exc

            time.sleep(interval)
        return

    def disconnect(self) -> None:
        """
            Tear down the SOL session.  Never raises; double-disconnect
            is a no-op.  For the subprocess backend the ``~.`` escape is
            sent to request an orderly ``ipmitool`` exit before
            ``Popen.terminate`` / ``Popen.kill`` are applied.  For the pyghmi
            backend the console's ``close`` method is invoked when
            available.
        """
        if not self._connected:
            return

        if self._use_pyghmi:
            self._disconnect_pyghmi()
        else:
            self._disconnect_ipmitool()

        self._connected = False
        self._session_start_ts = None
        return

    # -----------------------------------------------------------------
    # send / recv
    # -----------------------------------------------------------------

    def send(self, data: bytes) -> int:
        """
            Send bytes toward the target over the active SOL session.

            :param data: Bytes to send; outbound bytes are literal and
                         are NOT subject to line-ending normalization.

            :returns: The number of bytes accepted by the underlying
                      backend.
            :raises: UefiTransportError
        """
        if not self._connected:
            err_msg_lines = [
                "Cannot send on a transport that is not connected.",
                "HOST: {}".format(self._credential.host),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiTransportError(err_msg, details=list(err_msg_lines[1:])) from None

        if self._use_pyghmi:
            bytes_written = self._send_pyghmi(data=data)
        else:
            bytes_written = self._send_ipmitool(data=data)

        return bytes_written

    def recv(self, max_bytes: int, timeout: float) -> bytes:
        """
            Receive up to ``max_bytes`` from the target.  Returns early
            with whatever is already buffered; a ``timeout`` of ``0``
            performs a non-blocking poll.

            :param max_bytes: Upper bound on the number of returned
                              bytes.
            :param timeout: Seconds to wait; ``0`` means non-blocking.

            :returns: The bytes observed; may be shorter than
                      ``max_bytes``.
            :raises: UefiTransportError
        """
        if not self._connected:
            err_msg_lines = [
                "Cannot recv on a transport that is not connected.",
                "HOST: {}".format(self._credential.host),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiTransportError(err_msg, details=list(err_msg_lines[1:])) from None

        # Drain the read buffer first; only hit the backend if we still
        # need more bytes to satisfy the caller's max_bytes cap.
        if len(self._read_buffer) >= max_bytes:
            head = bytes(self._read_buffer[:max_bytes])
            del self._read_buffer[:max_bytes]
            return head

        if self._use_pyghmi:
            fresh_bytes = self._recv_pyghmi(max_bytes=max_bytes, timeout=timeout)
        else:
            fresh_bytes = self._recv_ipmitool(max_bytes=max_bytes, timeout=timeout)

        if len(fresh_bytes) > 0:
            self._read_buffer.extend(fresh_bytes)

        available = min(len(self._read_buffer), max_bytes)
        rtnval = bytes(self._read_buffer[:available])
        del self._read_buffer[:available]
        return rtnval

    def recv_until(self, pattern: Union[bytes, "re.Pattern[bytes]"], timeout: float) -> bytes:
        """
            Read bytes from the target until ``pattern`` is observed or
            ``timeout`` elapses.  Partial bytes observed are attached to
            the raised :class:`TimeoutError` as ``partial``.

            :param pattern: The pattern to search for.  A ``bytes``
                            value is matched as a literal substring.  A
                            compiled :class:`re.Pattern` of ``bytes``
                            is matched with :meth:`re.Pattern.search`.
            :param timeout: Seconds to wait.

            :returns: All bytes observed up to and including the match.
            :raises: TimeoutError, UefiTransportError
        """
        if isinstance(pattern, (bytes, bytearray)):
            literal_pattern: Optional[bytes] = bytes(pattern)
            regex_pattern: Optional[re.Pattern] = None
        elif isinstance(pattern, re.Pattern):
            literal_pattern = None
            regex_pattern = pattern
        else:
            err_msg_lines = [
                "'pattern' must be bytes or a compiled re.Pattern of bytes.",
                "GOT: value={!r} type={}".format(pattern, type(pattern).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiTransportError(err_msg, details=list(err_msg_lines[1:])) from None

        deadline = time.monotonic() + float(timeout)
        accumulated = bytearray()

        # First, consume anything already buffered.
        if len(self._read_buffer) > 0:
            accumulated.extend(self._read_buffer)
            self._read_buffer.clear()

            match_index = self._find_match(accumulated, literal_pattern, regex_pattern)
            if match_index is not None:
                matched_bytes = bytes(accumulated[:match_index])
                # Push back bytes after the match so the next recv sees them.
                leftover = accumulated[match_index:]
                if len(leftover) > 0:
                    self._read_buffer.extend(leftover)
                return matched_bytes

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                partial_bytes = bytes(accumulated)
                err_msg_lines = [
                    "recv_until did not observe the expected pattern before timeout.",
                    "PATTERN: {!r}".format(pattern),
                    "TIMEOUT: {}".format(timeout),
                    "PARTIAL_LEN: {}".format(len(partial_bytes)),
                ]
                err_msg = os.linesep.join(err_msg_lines)
                timeout_exc = TimeoutError(err_msg)
                setattr(timeout_exc, "partial", partial_bytes)
                # Push partial bytes back so the caller can drain them.
                if len(partial_bytes) > 0:
                    self._read_buffer.extend(partial_bytes)
                raise timeout_exc

            # Cap the per-call timeout so we poll for the overall deadline.
            per_call_timeout = min(remaining, 0.5)

            if self._use_pyghmi:
                chunk = self._recv_pyghmi(max_bytes=DEFAULT_RECV_CHUNK, timeout=per_call_timeout)
            else:
                chunk = self._recv_ipmitool(max_bytes=DEFAULT_RECV_CHUNK, timeout=per_call_timeout)

            if len(chunk) > 0:
                accumulated.extend(chunk)
                match_index = self._find_match(accumulated, literal_pattern, regex_pattern)
                if match_index is not None:
                    matched_bytes = bytes(accumulated[:match_index])
                    leftover = accumulated[match_index:]
                    if len(leftover) > 0:
                        self._read_buffer.extend(leftover)
                    return matched_bytes

        return b""

    # -----------------------------------------------------------------
    # Internal: match helper
    # -----------------------------------------------------------------

    def _find_match(self, haystack: bytearray,
                          literal_pattern: Optional[bytes],
                          regex_pattern: Optional["re.Pattern[bytes]"]) -> Optional[int]:
        """
            Locate the end index (exclusive) of the first match of
            ``literal_pattern`` or ``regex_pattern`` in ``haystack``.

            :param haystack: The accumulated byte buffer.
            :param literal_pattern: Optional literal bytes to search
                                    for; mutually exclusive with
                                    ``regex_pattern``.
            :param regex_pattern: Optional compiled pattern to search
                                  for; mutually exclusive with
                                  ``literal_pattern``.

            :returns: The end index of the match, or ``None`` when no
                      match is found.
        """
        if literal_pattern is not None:
            position = haystack.find(literal_pattern)
            if position < 0:
                rtnval: Optional[int] = None
            else:
                rtnval = position + len(literal_pattern)
            return rtnval

        if regex_pattern is not None:
            match_obj = regex_pattern.search(bytes(haystack))
            if match_obj is None:
                rtnval = None
            else:
                rtnval = match_obj.end()
            return rtnval

        rtnval = None
        return rtnval

    # -----------------------------------------------------------------
    # Internal: ipmitool backend
    # -----------------------------------------------------------------

    def _build_ipmitool_argv(self, subcommand_tokens: List[str]) -> List[str]:
        """
            Build an ``ipmitool`` argv with the shared interface / host /
            user / password flags plus the caller-supplied subcommand
            tokens.

            :param subcommand_tokens: For example ``["sol", "activate"]``.

            :returns: The fully assembled argv list.
        """
        argv = [
            "ipmitool",
            "-I", self._credential.interface,
            "-H", self._credential.host,
            "-U", self._credential.username,
            "-P", self._credential.password,
        ]
        argv.extend(subcommand_tokens)
        return argv

    def _run_ipmitool_oneshot(self, subcommand_tokens: List[str],
                                    timeout_seconds: float) -> "subprocess.CompletedProcess":
        """
            Run a short-lived ``ipmitool`` invocation (for example
            ``sol deactivate``) and capture both streams.

            :param subcommand_tokens: Subcommand to append after the
                                      shared flags.
            :param timeout_seconds: Hard cap on the invocation.

            :returns: The :class:`subprocess.CompletedProcess`.
            :raises: UefiTransportError
        """
        argv = self._build_ipmitool_argv(subcommand_tokens=subcommand_tokens)
        try:
            completed = subprocess.run(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout_seconds,
                check=False,
            )
        except FileNotFoundError as fnferr:
            err_msg_lines = [
                "The 'ipmitool' binary was not found on PATH.",
                "PATH: {}".format(os.environ.get("PATH", "")),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiTransportError(err_msg, details=list(err_msg_lines[1:])) from fnferr
        except subprocess.TimeoutExpired as texp:
            err_msg_lines = [
                "The 'ipmitool' invocation timed out.",
                "SUBCOMMAND: {}".format(" ".join(subcommand_tokens)),
                "TIMEOUT: {}".format(timeout_seconds),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiTransportError(err_msg, details=list(err_msg_lines[1:])) from texp

        return completed

    def _connect_ipmitool(self) -> None:
        """
            Activate a SOL session using the ``ipmitool`` subprocess
            backend.  Handles optional eviction of an existing session,
            detection of ``SOL already active``, and translation of any
            non-zero exit into :class:`UefiTransportError`.

            :raises: UefiTransportError
        """
        if self._evict_existing:
            deactivate_completed = self._run_ipmitool_oneshot(
                subcommand_tokens=["sol", "deactivate"],
                timeout_seconds=30.0,
            )
            deact_stdout = (deactivate_completed.stdout or b"").decode("utf-8", errors="replace")
            deact_stderr = (deactivate_completed.stderr or b"").decode("utf-8", errors="replace")
            combined_output = deact_stdout + deact_stderr
            if DEACTIVATION_FAILED_SENTINEL in combined_output:
                err_msg_lines = [
                    "Failed to deactivate an existing SOL session.",
                    "HOST: {}".format(self._credential.host),
                    "SENTINEL: {}".format(DEACTIVATION_FAILED_SENTINEL),
                ]
                err_msg = os.linesep.join(err_msg_lines)
                details_lines = [
                    "STDOUT:",
                    deact_stdout,
                    "STDERR:",
                    deact_stderr,
                ]
                raise UefiTransportError(err_msg, details=details_lines) from None

        activate_argv = self._build_ipmitool_argv(subcommand_tokens=["sol", "activate"])

        try:
            process = subprocess.Popen(
                activate_argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except FileNotFoundError as fnferr:
            err_msg_lines = [
                "The 'ipmitool' binary was not found on PATH.",
                "PATH: {}".format(os.environ.get("PATH", "")),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiTransportError(err_msg, details=list(err_msg_lines[1:])) from fnferr

        self._process = process

        # Give ipmitool a moment to either successfully activate the SOL
        # payload or exit with an "already active" diagnostic.  A short
        # grace period covers the common-case latency without blocking
        # for a long time on success.
        early_exit_deadline = time.monotonic() + 1.0
        early_exit_code: Optional[int] = None
        while time.monotonic() < early_exit_deadline:
            poll_result = process.poll()
            if poll_result is not None:
                early_exit_code = poll_result
                break
            time.sleep(0.05)

        if early_exit_code is not None:
            stdout_bytes = b""
            stderr_bytes = b""
            try:
                stdout_bytes, stderr_bytes = process.communicate(timeout=1.0)
            except subprocess.TimeoutExpired:
                # The captured output only enriches the error raised below; the
                # early exit code is already known, so report without the output.
                pass
            except ValueError:
                # The pipes were already closed, so there is no output to collect;
                # the error raised below still reports the early exit code.
                pass

            stdout_text = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes is not None else ""
            stderr_text = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes is not None else ""
            combined_output = stdout_text + stderr_text

            self._process = None

            if SOL_ALREADY_ACTIVE_SENTINEL in combined_output:
                err_msg_lines = [
                    "A SOL session is already active on the target.",
                    "HOST: {}".format(self._credential.host),
                    "CONDITION: {}".format(SOL_ALREADY_ACTIVE_SENTINEL),
                    "EXIT_CODE: {}".format(early_exit_code),
                ]
                err_msg = os.linesep.join(err_msg_lines)
                details_lines = [
                    "STDOUT:",
                    stdout_text,
                    "STDERR:",
                    stderr_text,
                ]
                raise UefiTransportError(err_msg, details=details_lines) from None

            err_msg_lines = [
                "ipmitool exited before SOL activation completed.",
                "HOST: {}".format(self._credential.host),
                "EXIT_CODE: {}".format(early_exit_code),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            details_lines = [
                "STDOUT:",
                stdout_text,
                "STDERR:",
                stderr_text,
            ]
            raise UefiTransportError(err_msg, details=details_lines) from None

        return

    def _disconnect_ipmitool(self) -> None:
        """
            Send the ``~.`` escape sequence to request an orderly
            ``ipmitool`` exit, then apply ``Popen.terminate`` with a
            grace period and finally ``Popen.kill``.  Never raises.

            Only the portable :class:`subprocess.Popen` termination
            methods are used so the same code path runs on POSIX and
            Windows.  On POSIX ``terminate`` sends ``SIGTERM`` and
            ``kill`` sends ``SIGKILL``; on Windows both call
            ``TerminateProcess``.
        """
        process = self._process
        if process is None:
            return

        try:
            if process.stdin is not None:
                try:
                    process.stdin.write(SOL_ESCAPE_SEQUENCE)
                    process.stdin.flush()
                except (BrokenPipeError, OSError, ValueError):
                    # Best-effort orderly exit request; the process may already have
                    # exited, and terminate/kill below still stop it.
                    pass
                try:
                    process.stdin.close()
                except (BrokenPipeError, OSError, ValueError):
                    # Best-effort cleanup; the pipe may already be broken or closed.
                    pass
        finally:
            # Give the tool a brief window to exit on the escape.
            exit_deadline = time.monotonic() + 0.5
            while time.monotonic() < exit_deadline:
                if process.poll() is not None:
                    break
                time.sleep(0.05)

            if process.poll() is None:
                try:
                    process.terminate()
                except (OSError, ValueError):
                    # The process may have exited since the poll; kill below is the
                    # fallback when it is still running.
                    pass

                term_deadline = time.monotonic() + TERMINATE_TO_KILL_GRACE_SECONDS
                while time.monotonic() < term_deadline:
                    if process.poll() is not None:
                        break
                    time.sleep(0.05)

                if process.poll() is None:
                    try:
                        process.kill()
                    except (OSError, ValueError):
                        # The process may have exited since the poll; disconnect
                        # never raises, so there is nothing further to do.
                        pass
                    try:
                        process.wait(timeout=1.0)
                    except (subprocess.TimeoutExpired, OSError, ValueError):
                        # Best-effort reap; disconnect never raises and the process
                        # has already been sent a kill.
                        pass

            # Drain stdout/stderr handles so file descriptors close.
            for handle in (process.stdout, process.stderr):
                if handle is not None:
                    try:
                        handle.close()
                    except (OSError, ValueError):
                        # Best-effort cleanup; a handle that fails to close must not
                        # stop the remaining handle from being closed.
                        pass

        self._process = None
        return

    def _send_ipmitool(self, data: bytes) -> int:
        """
            Write ``data`` to the ``ipmitool`` subprocess stdin.
            Protects against ``BrokenPipeError`` when the subprocess has
            died.

            :param data: Bytes to write.

            :returns: The number of bytes written.
            :raises: UefiTransportError
        """
        process = self._process
        if process is None or process.stdin is None:
            err_msg_lines = [
                "The 'ipmitool' subprocess is not available for send.",
                "HOST: {}".format(self._credential.host),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiTransportError(err_msg, details=list(err_msg_lines[1:])) from None

        try:
            process.stdin.write(data)
            process.stdin.flush()
        except BrokenPipeError as bperr:
            err_msg_lines = [
                "The 'ipmitool' subprocess closed stdin before send completed.",
                "HOST: {}".format(self._credential.host),
                "BYTES_ATTEMPTED: {}".format(len(data)),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiTransportError(err_msg, details=list(err_msg_lines[1:])) from bperr
        except (OSError, ValueError) as oserr:
            err_msg_lines = [
                "Writing to the 'ipmitool' subprocess stdin failed.",
                "HOST: {}".format(self._credential.host),
                "BYTES_ATTEMPTED: {}".format(len(data)),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiTransportError(err_msg, details=list(err_msg_lines[1:])) from oserr

        rtnval = len(data)
        return rtnval

    def _recv_ipmitool(self, max_bytes: int, timeout: float) -> bytes:
        """
            Non-blocking read from the ``ipmitool`` subprocess stdout
            using :func:`select.select`.  Returns whatever is available
            up to ``max_bytes``.

            :param max_bytes: Upper bound on the returned bytes.
            :param timeout: Seconds to wait on :func:`select.select`.

            :returns: The bytes read; may be empty.
            :raises: UefiTransportError
        """
        process = self._process
        if process is None or process.stdout is None:
            err_msg_lines = [
                "The 'ipmitool' subprocess is not available for recv.",
                "HOST: {}".format(self._credential.host),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiTransportError(err_msg, details=list(err_msg_lines[1:])) from None

        try:
            ready_list, _, _ = select.select([process.stdout], [], [], float(timeout))
        except (OSError, ValueError) as selerr:
            err_msg_lines = [
                "select() on the 'ipmitool' subprocess stdout failed.",
                "HOST: {}".format(self._credential.host),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiTransportError(err_msg, details=list(err_msg_lines[1:])) from selerr

        if len(ready_list) == 0:
            rtnval = b""
            return rtnval

        try:
            chunk = process.stdout.read1(max_bytes)
        except AttributeError:
            # Some mocks expose .read but not .read1; fall back.
            chunk = process.stdout.read(max_bytes)
        except (OSError, ValueError) as rderr:
            err_msg_lines = [
                "Reading from the 'ipmitool' subprocess stdout failed.",
                "HOST: {}".format(self._credential.host),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiTransportError(err_msg, details=list(err_msg_lines[1:])) from rderr

        if chunk is None:
            rtnval = b""
        else:
            rtnval = bytes(chunk)
        return rtnval

    # -----------------------------------------------------------------
    # Internal: pyghmi backend
    # -----------------------------------------------------------------

    def _connect_pyghmi(self) -> None:
        """
            Activate a SOL session using the ``pyghmi`` backend.  The
            module is soft-imported so that callers of the default
            subprocess backend do not need it installed.

            :raises: UefiTransportError
        """
        try:
            from pyghmi.ipmi.console import Console  # type: ignore[import-not-found]
        except ImportError as imperr:
            err_msg_lines = [
                "The 'pyghmi' package is not available but use_pyghmi=True was requested.",
                "HINT: pip install pyghmi",
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiTransportError(err_msg, details=list(err_msg_lines[1:])) from imperr

        try:
            console = Console(
                bmc=self._credential.host,
                userid=self._credential.username,
                password=self._credential.password,
                force=bool(self._evict_existing),
                iohandler=self._pyghmi_iohandler,
            )
        except Exception as pyerr:
            err_msg_lines = [
                "Failed to activate SOL session via pyghmi.",
                "HOST: {}".format(self._credential.host),
                "ERROR: {!r}".format(pyerr),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiTransportError(err_msg, details=list(err_msg_lines[1:])) from pyerr

        self._pyghmi_console = console
        return

    def _pyghmi_iohandler(self, data: bytes) -> None:
        """
            Callback invoked by ``pyghmi`` when inbound SOL bytes are
            observed.  Appends to the inbound buffer.

            :param data: The inbound byte block.
        """
        if data is None:
            return
        if isinstance(data, (bytes, bytearray)):
            self._pyghmi_rx_buffer.extend(data)
        else:
            # pyghmi on some versions yields str; coerce defensively.
            encoded = str(data).encode("utf-8", errors="replace")
            self._pyghmi_rx_buffer.extend(encoded)
        return

    def _disconnect_pyghmi(self) -> None:
        """
            Close the ``pyghmi`` console.  Never raises.
        """
        console = self._pyghmi_console
        if console is None:
            return

        close_callable = getattr(console, "close", None)
        if callable(close_callable):
            try:
                close_callable()
            except Exception:
                # disconnect must not raise.
                pass

        self._pyghmi_console = None
        self._pyghmi_rx_buffer.clear()
        return

    def _send_pyghmi(self, data: bytes) -> int:
        """
            Write ``data`` to the ``pyghmi`` console.

            :param data: Bytes to write.

            :returns: The number of bytes accepted.
            :raises: UefiTransportError
        """
        console = self._pyghmi_console
        if console is None:
            err_msg_lines = [
                "The 'pyghmi' console is not available for send.",
                "HOST: {}".format(self._credential.host),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiTransportError(err_msg, details=list(err_msg_lines[1:])) from None

        write_callable = getattr(console, "write", None)
        if not callable(write_callable):
            err_msg_lines = [
                "The 'pyghmi' console does not expose a 'write' method.",
                "HOST: {}".format(self._credential.host),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiTransportError(err_msg, details=list(err_msg_lines[1:])) from None

        try:
            write_callable(data)
        except Exception as pyerr:
            err_msg_lines = [
                "Writing to the 'pyghmi' console failed.",
                "HOST: {}".format(self._credential.host),
                "BYTES_ATTEMPTED: {}".format(len(data)),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiTransportError(err_msg, details=list(err_msg_lines[1:])) from pyerr

        rtnval = len(data)
        return rtnval

    def _recv_pyghmi(self, max_bytes: int, timeout: float) -> bytes:
        """
            Drain up to ``max_bytes`` from the pyghmi inbound buffer.
            Waits up to ``timeout`` seconds for bytes to arrive when
            the buffer is empty.

            :param max_bytes: Upper bound on returned bytes.
            :param timeout: Seconds to wait on arrival.

            :returns: The bytes observed; may be empty.
        """
        deadline = time.monotonic() + float(timeout)
        while len(self._pyghmi_rx_buffer) == 0 and time.monotonic() < deadline:
            time.sleep(0.05)

        if len(self._pyghmi_rx_buffer) == 0:
            rtnval = b""
            return rtnval

        available = min(len(self._pyghmi_rx_buffer), max_bytes)
        head = bytes(self._pyghmi_rx_buffer[:available])
        del self._pyghmi_rx_buffer[:available]
        rtnval = head
        return rtnval


__all__ = [
    "UefiIpmiSolTransport",
    "SOL_ALREADY_ACTIVE_SENTINEL",
    "DEACTIVATION_FAILED_SENTINEL",
    "SOL_ESCAPE_SEQUENCE",
]
