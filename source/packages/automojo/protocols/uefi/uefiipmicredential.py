"""
    .. module:: uefiipmicredential
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Module containing the :class:`UefiIpmiCredential` frozen
               dataclass that describes credentials for an IPMI / SOL
               transport.  The class validates its fields in
               ``__post_init__`` and redacts the ``password`` field from
               ``__repr__`` / ``__str__``.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Automation Mojo LLC"
__credits__ = []


import dataclasses
import os

from typing import Tuple

from automojo.errors.exceptions import ConfigurationError


# ---------------------------------------------------------------------------
# Allowed-value constants
# ---------------------------------------------------------------------------

ALLOWED_IPMI_INTERFACES: Tuple[str, ...] = ("lan", "lanplus", "open", "imb")

MIN_PORT = 1
MAX_PORT = 65535


# ---------------------------------------------------------------------------
# Redaction helper
# ---------------------------------------------------------------------------

SENSITIVE_REDACTED = "***"


def _redacted(value) -> str:
    """
        Produce the redaction rendering used by
        :meth:`UefiIpmiCredential.__repr__`.

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
# UefiIpmiCredential
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class UefiIpmiCredential:
    """
        Credential describing an IPMI / SOL attachment to a BMC.

        :param host: BMC hostname or IP.
        :param username: Account name on the BMC.
        :param password: Account password.
        :param interface: IPMI interface; one of ``"lan"``, ``"lanplus"``,
                          ``"open"``, ``"imb"``.
        :param port: UDP port; must be in the range 1..65535.
    """
    host: str
    username: str
    password: str
    interface: str = "lanplus"
    port: int = 623

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

        if not isinstance(self.host, str) or len(self.host) == 0:
            err_msg_lines.append(
                "UefiIpmiCredential.host must be a non-empty string.  observed={!r}".format(self.host)
            )

        if not isinstance(self.username, str) or len(self.username) == 0:
            err_msg_lines.append(
                "UefiIpmiCredential.username must be a non-empty string.  observed={!r}".format(self.username)
            )

        if not isinstance(self.password, str) or len(self.password) == 0:
            err_msg_lines.append(
                "UefiIpmiCredential.password must be a non-empty string."
            )

        if self.interface not in ALLOWED_IPMI_INTERFACES:
            err_msg_lines.append(
                "UefiIpmiCredential.interface={!r} not in {}.".format(self.interface, ALLOWED_IPMI_INTERFACES)
            )

        port_is_int = isinstance(self.port, int) and not isinstance(self.port, bool)
        if not port_is_int:
            err_msg_lines.append(
                "UefiIpmiCredential.port must be an int.  observed={!r}".format(self.port)
            )
        else:
            if self.port < MIN_PORT or self.port > MAX_PORT:
                err_msg_lines.append(
                    "UefiIpmiCredential.port={} is not in the range {}..{}.".format(self.port, MIN_PORT, MAX_PORT)
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
            "UefiIpmiCredential("
            "host={host!r}, "
            "username={username!r}, "
            "password={password}, "
            "interface={interface!r}, "
            "port={port!r})"
        ).format(
            host=self.host,
            username=self.username,
            password=_redacted(self.password),
            interface=self.interface,
            port=self.port,
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
    "UefiIpmiCredential",
    "ALLOWED_IPMI_INTERFACES",
    "MIN_PORT",
    "MAX_PORT",
]
