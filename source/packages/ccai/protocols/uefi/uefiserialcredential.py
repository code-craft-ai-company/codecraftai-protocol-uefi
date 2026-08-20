"""
    .. module:: uefiserialcredential
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Module containing the :class:`UefiSerialCredential` frozen
               dataclass that describes the credential for a serial console
               transport.  The class validates its fields in
               ``__post_init__`` and redacts the ``password`` field from
               ``__repr__`` / ``__str__``.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Code Craft AI LLC"
__credits__ = []


import dataclasses
import os

from typing import Optional, Tuple, Union

from ccai.errors.exceptions import ConfigurationError


# ---------------------------------------------------------------------------
# Allowed-value constants
# ---------------------------------------------------------------------------

ALLOWED_PARITY: Tuple[str, ...] = ("N", "E", "O", "M", "S")
ALLOWED_STOPBITS: Tuple[Union[int, float], ...] = (1, 1.5, 2)
ALLOWED_FLOW_CONTROL: Tuple[str, ...] = ("none", "rtscts", "xonxoff")


# ---------------------------------------------------------------------------
# Redaction helper
# ---------------------------------------------------------------------------

SENSITIVE_REDACTED = "***"


def _redacted(value: Optional[str]) -> str:
    """
        Produce the redaction rendering used by :meth:`UefiSerialCredential.__repr__`.

        :param value: The sensitive value to render.

        :returns: The string ``"***"`` when ``value`` is not ``None``, or the
                  string ``"None"`` when ``value`` is ``None``.
    """
    if value is None:
        rtnval = "None"
    else:
        rtnval = SENSITIVE_REDACTED

    return rtnval


# ---------------------------------------------------------------------------
# UefiSerialCredential
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class UefiSerialCredential:
    """
        Credential describing a serial-console attachment.  The ``device``
        field accepts a local device path (``/dev/ttyUSB0``) or a URL form
        such as ``socket://host:port`` or ``telnet://host:port``.  The
        ``login`` / ``password`` fields are optional and are used only when
        firmware gates the shell behind a prompt.

        :param device: Serial device path or URL.
        :param baud: Serial baud rate; must be a positive integer.
        :param bytesize: Byte size; typically 8.
        :param parity: Parity setting; one of ``"N"``, ``"E"``, ``"O"``,
                       ``"M"``, ``"S"``.
        :param stopbits: Stop bits; one of ``1``, ``1.5``, ``2``.
        :param flow_control: One of ``"none"``, ``"rtscts"``, ``"xonxoff"``.
        :param login: Optional login name for a firmware-gated shell.
        :param password: Optional password for a firmware-gated shell.
    """
    device: str
    baud: int = 115200
    bytesize: int = 8
    parity: str = "N"
    stopbits: Union[int, float] = 1
    flow_control: str = "none"
    login: Optional[str] = None
    password: Optional[str] = None

    def __post_init__(self) -> None:
        """
            Run field-level validation after dataclass initialization.

            :raises: ConfigurationError
        """
        self.validate()
        return

    def validate(self) -> None:
        """
            Validate every field on this credential.  Collect every problem
            into a list of lines and raise a single
            :class:`ConfigurationError` whose message is those lines joined
            with :data:`os.linesep`.

            :raises: ConfigurationError
        """
        err_msg_lines = []

        if not isinstance(self.device, str) or len(self.device) == 0:
            err_msg_lines.append(
                "UefiSerialCredential.device must be a non-empty string.  observed={!r}".format(self.device)
            )

        if not isinstance(self.baud, int) or isinstance(self.baud, bool) or self.baud <= 0:
            err_msg_lines.append(
                "UefiSerialCredential.baud must be a positive int.  observed={!r}".format(self.baud)
            )

        if self.parity not in ALLOWED_PARITY:
            err_msg_lines.append(
                "UefiSerialCredential.parity={!r} not in {}.".format(self.parity, ALLOWED_PARITY)
            )

        if self.stopbits not in ALLOWED_STOPBITS:
            err_msg_lines.append(
                "UefiSerialCredential.stopbits={!r} not in {}.".format(self.stopbits, ALLOWED_STOPBITS)
            )

        if self.flow_control not in ALLOWED_FLOW_CONTROL:
            err_msg_lines.append(
                "UefiSerialCredential.flow_control={!r} not in {}.".format(self.flow_control, ALLOWED_FLOW_CONTROL)
            )

        if len(err_msg_lines) > 0:
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg) from None

        return

    def __repr__(self) -> str:
        """
            Render a representation with the ``password`` field redacted.

            :returns: The representation string.
        """
        rtnval = (
            "UefiSerialCredential("
            "device={device!r}, "
            "baud={baud!r}, "
            "bytesize={bytesize!r}, "
            "parity={parity!r}, "
            "stopbits={stopbits!r}, "
            "flow_control={flow_control!r}, "
            "login={login!r}, "
            "password={password})"
        ).format(
            device=self.device,
            baud=self.baud,
            bytesize=self.bytesize,
            parity=self.parity,
            stopbits=self.stopbits,
            flow_control=self.flow_control,
            login=self.login,
            password=_redacted(self.password),
        )
        return rtnval

    def __str__(self) -> str:
        """
            Mirror :meth:`__repr__` so that ``f"{cred}"`` is also redacted.

            :returns: The representation string.
        """
        rtnval = self.__repr__()
        return rtnval


__all__ = [
    "UefiSerialCredential",
    "ALLOWED_PARITY",
    "ALLOWED_STOPBITS",
    "ALLOWED_FLOW_CONTROL",
]
