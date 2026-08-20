"""
    .. module:: ueficonst
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Module containing shared UEFI constants, textual templates,
               prompt sentinels, transport-kind literals, GUID binary
               round-trip helpers, and the :class:`UefiJumpParams` frozen
               dataclass.  Every value in this module is intended to be
               read-only by convention and is safe to import from early
               bootstrap paths because the module has zero third-party
               dependencies.

               Duration defaults are expressed in whole seconds unless the
               constant name ends with ``_INTERVAL`` (which also denotes
               seconds, typically fractional).  Size defaults are expressed
               in bytes.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Code Craft AI LLC"
__credits__ = []


import dataclasses
import os
import re
import uuid

from typing import Any, Final, FrozenSet, Pattern


# ---------------------------------------------------------------------------
# Numeric defaults (durations in seconds, sizes in bytes)
# ---------------------------------------------------------------------------

DEFAULT_UEFI_TIMEOUT: Final[int] = 300
DEFAULT_UEFI_RETRY_INTERVAL: Final[float] = 0.5

DEFAULT_SHELL_TIMEOUT: Final[int] = 120
DEFAULT_REDFISH_TIMEOUT: Final[int] = 120
DEFAULT_RECONNECT_DELAY: Final[float] = 2.0
DEFAULT_CHUNK_SIZE: Final[int] = 65536


# ---------------------------------------------------------------------------
# Transport kind literals
# ---------------------------------------------------------------------------

TRANSPORT_KIND_SERIAL: Final[str] = "serial"
TRANSPORT_KIND_REDFISH: Final[str] = "redfish"
TRANSPORT_KIND_SSH_HOST: Final[str] = "ssh-host"
TRANSPORT_KIND_IPMI_SOL: Final[str] = "ipmi-sol"

TRANSPORT_KINDS: Final[FrozenSet[str]] = frozenset({
    TRANSPORT_KIND_SERIAL,
    TRANSPORT_KIND_REDFISH,
    TRANSPORT_KIND_SSH_HOST,
    TRANSPORT_KIND_IPMI_SOL,
})


# ---------------------------------------------------------------------------
# Prompt sentinels / interactive markers
# ---------------------------------------------------------------------------

DEFAULT_SHELL_PROMPT_PATTERN: Final[bytes] = rb"(?:Shell|FS\d+:\\[^>]*)>\s*$"
DEFAULT_SHELL_PROMPT_PATTERN_STR: Final[str] = DEFAULT_SHELL_PROMPT_PATTERN.decode("utf-8")

DEFAULT_SETUP_PROMPT_PATTERN: Final[bytes] = rb"(?:Setup|BIOS Setup)\s*>\s*$"
DEFAULT_SETUP_PROMPT_PATTERN_STR: Final[str] = DEFAULT_SETUP_PROMPT_PATTERN.decode("utf-8")

INTERACTIVE_PROMPT: Final[str] = "UEFI_CCAI_PROMPT%:%:%"
INTERACTIVE_PROMPT_BYTES: Final[bytes] = INTERACTIVE_PROMPT.encode("utf-8")

DEFAULT_INTERACTIVE_SENTINEL: Final[bytes] = INTERACTIVE_PROMPT_BYTES
DEFAULT_INTERACTIVE_SENTINEL_STR: Final[str] = INTERACTIVE_PROMPT


# ---------------------------------------------------------------------------
# Labels for command-output rendering
# ---------------------------------------------------------------------------

DEFAULT_SUCCESS_LABEL: Final[str] = "Success"
DEFAULT_FAILURE_LABEL: Final[str] = "Failure"


# ---------------------------------------------------------------------------
# Command-output templates
#
# Each template uses printf-style ``%s`` substitution and embeds
# :data:`os.linesep` between lines rather than a hard-coded ``"\n"`` so that
# rendered log output is legible on every host platform.
#
# TEMPLATE_COMMAND_SUCCESS placeholders (in order):
#     1. tag            - caller-supplied correlation tag
#     2. command        - the command string that was issued
#     3. stdout block   - captured stdout
#     4. stderr block   - captured stderr
#
# TEMPLATE_COMMAND_FAILURE placeholders (in order):
#     1. tag            - caller-supplied correlation tag
#     2. command        - the command string that was issued
#     3. status         - numeric exit / completion status
#     4. stdout block   - captured stdout
#     5. stderr block   - captured stderr
# ---------------------------------------------------------------------------

TEMPLATE_COMMAND_SUCCESS: Final[str] = (
    "UEFI%s: " + DEFAULT_SUCCESS_LABEL + " running CMD=%s" + os.linesep
    + "STDOUT:" + os.linesep + "%s" + os.linesep
    + "STDERR:" + os.linesep + "%s" + os.linesep
)

TEMPLATE_COMMAND_FAILURE: Final[str] = (
    "UEFI%s: " + DEFAULT_FAILURE_LABEL + " running CMD=%s STATUS=%s" + os.linesep
    + "STDOUT:" + os.linesep + "%s" + os.linesep
    + "STDERR:" + os.linesep + "%s" + os.linesep
)


# ---------------------------------------------------------------------------
# Compiled regex constants for common UEFI Shell output formats
# ---------------------------------------------------------------------------

#                              DATETIME                 SIZE         NAME
#                    (e.g. "01/01/2024  12:00")       "   4,096"    "BOOTX64.EFI"
REGEX_SHELL_LS_ENTRY: Final[Pattern[str]] = re.compile(
    r"^(?P<datetime>\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2})"
    r"\s+(?P<attrs>[DRHSA-]+)?"
    r"\s*(?P<size>[0-9,]+)"
    r"\s+(?P<name>.+?)\s*$"
)

#  matches a canonical GUID string "8be4df61-93ca-11d2-aa0d-00e098032b8c"
REGEX_GUID_STRING: Final[Pattern[str]] = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

#  matches a UEFI Shell command prompt line, e.g. "FS0:\> " or "Shell> "
REGEX_SHELL_PROMPT_LINE: Final[Pattern[str]] = re.compile(
    r"^(?:Shell|FS\d+:\\[^>]*)>\s*$"
)


# ---------------------------------------------------------------------------
# GUID binary round-trip helpers (UEFI mixed-endian layout)
# ---------------------------------------------------------------------------

_GUID_BUF_LEN: Final[int] = 16


def guid_to_bytes(g: uuid.UUID) -> bytes:
    """
        Serialize a :class:`uuid.UUID` into the UEFI 16-byte mixed-endian
        GUID layout (``data1``, ``data2``, ``data3`` little-endian;
        ``data4`` big-endian).  This is the layout written to EFI variable
        storage and table fields.

        :param g: The UUID value to serialize.

        :returns: A 16-byte :class:`bytes` object containing the UEFI
                  mixed-endian representation.
        :raises: TypeError
    """
    if not isinstance(g, uuid.UUID):
        err_msg_lines = [
            "'guid_to_bytes' requires a uuid.UUID instance.",
            "GOT: value={!r} type={}".format(g, type(g).__name__),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise TypeError(err_msg) from None

    rtnval = g.bytes_le
    return rtnval


def guid_from_bytes(b: bytes) -> uuid.UUID:
    """
        Deserialize a 16-byte UEFI mixed-endian GUID buffer into a
        :class:`uuid.UUID`.

        :param b: A 16-byte buffer in UEFI mixed-endian layout.

        :returns: The decoded :class:`uuid.UUID`.
        :raises: TypeError, ValueError
    """
    if not isinstance(b, (bytes, bytearray, memoryview)):
        err_msg_lines = [
            "'guid_from_bytes' requires a bytes-like buffer.",
            "GOT: value={!r} type={}".format(b, type(b).__name__),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise TypeError(err_msg) from None

    buf = bytes(b)
    observed_length = len(buf)
    if observed_length != _GUID_BUF_LEN:
        err_msg_lines = [
            "'guid_from_bytes' requires a 16-byte buffer.",
            "OBSERVED LENGTH: {}".format(observed_length),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise ValueError(err_msg) from None

    rtnval = uuid.UUID(bytes_le=buf)
    return rtnval


# ---------------------------------------------------------------------------
# UefiJumpParams frozen dataclass
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class UefiJumpParams:
    """
        Parameters describing a jump host used to reach a UEFI target that
        is on a private management network (typical for Redfish BMCs).
        Parallels :class:`SshJumpParams` from the SSH protocol library.

        The dataclass is frozen: any attribute assignment after construction
        raises :class:`dataclasses.FrozenInstanceError`.  Instances are
        value objects - equality is field-wise.

        :param host: Hostname or IP address of the jump server.  Must be a
                     non-empty string.
        :param credential: Credential used when connecting to the jump
                           host.  The type is left as :class:`Any` to keep
                           this module free of a hard import on the
                           credentials package; callers typically pass an
                           :class:`SshCredential`.
        :param port: TCP port of the jump SSH service.  Defaults to 22.
    """
    host: str
    credential: Any
    port: int = 22


# ---------------------------------------------------------------------------
# __all__ - public surface
# ---------------------------------------------------------------------------

__all__ = [
    "DEFAULT_CHUNK_SIZE",
    "DEFAULT_FAILURE_LABEL",
    "DEFAULT_INTERACTIVE_SENTINEL",
    "DEFAULT_INTERACTIVE_SENTINEL_STR",
    "DEFAULT_RECONNECT_DELAY",
    "DEFAULT_REDFISH_TIMEOUT",
    "DEFAULT_SETUP_PROMPT_PATTERN",
    "DEFAULT_SETUP_PROMPT_PATTERN_STR",
    "DEFAULT_SHELL_PROMPT_PATTERN",
    "DEFAULT_SHELL_PROMPT_PATTERN_STR",
    "DEFAULT_SHELL_TIMEOUT",
    "DEFAULT_SUCCESS_LABEL",
    "DEFAULT_UEFI_RETRY_INTERVAL",
    "DEFAULT_UEFI_TIMEOUT",
    "INTERACTIVE_PROMPT",
    "INTERACTIVE_PROMPT_BYTES",
    "REGEX_GUID_STRING",
    "REGEX_SHELL_LS_ENTRY",
    "REGEX_SHELL_PROMPT_LINE",
    "TEMPLATE_COMMAND_FAILURE",
    "TEMPLATE_COMMAND_SUCCESS",
    "TRANSPORT_KINDS",
    "TRANSPORT_KIND_IPMI_SOL",
    "TRANSPORT_KIND_REDFISH",
    "TRANSPORT_KIND_SERIAL",
    "TRANSPORT_KIND_SSH_HOST",
    "UefiJumpParams",
    "guid_from_bytes",
    "guid_to_bytes",
]
