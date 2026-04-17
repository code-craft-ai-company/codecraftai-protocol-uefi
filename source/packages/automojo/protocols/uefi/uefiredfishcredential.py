"""
    .. module:: uefiredfishcredential
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Module containing the :class:`UefiRedfishCredential` frozen
               dataclass that describes credentials for a Redfish transport.
               The class validates its fields in ``__post_init__`` and
               redacts ``password`` and ``token`` from ``__repr__`` /
               ``__str__``.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Automation Mojo LLC"
__credits__ = []


import dataclasses
import os

from typing import Optional

from automojo.errors.exceptions import ConfigurationError


# ---------------------------------------------------------------------------
# Redaction helper
# ---------------------------------------------------------------------------

SENSITIVE_REDACTED = "***"


def _redacted(value: Optional[str]) -> str:
    """
        Produce the redaction rendering used by
        :meth:`UefiRedfishCredential.__repr__`.

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
# UefiRedfishCredential
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class UefiRedfishCredential:
    """
        Credential describing a Redfish-over-HTTPS attachment.  Supports
        either basic auth (``username`` + ``password``) or token auth
        (``token``).  When both are supplied, ``prefer_token`` must be
        ``True`` or validation fails.

        :param base_url: Base URL of the Redfish service, including scheme.
                         Must start with ``http://`` or ``https://``.
        :param username: Optional username for basic auth.
        :param password: Optional password for basic auth.
        :param token: Optional bearer / ``X-Auth-Token`` value.
        :param ca_bundle: Optional path (as a string) to a CA bundle file.
                          Path existence is NOT checked here.
        :param verify_tls: When ``True`` (default), verify the service's TLS
                           certificate at connection time.
        :param prefer_token: When ``True``, the token is considered the
                             primary credential even if username/password is
                             also supplied.
    """
    base_url: str
    username: Optional[str] = None
    password: Optional[str] = None
    token: Optional[str] = None
    ca_bundle: Optional[str] = None
    verify_tls: bool = True
    prefer_token: bool = False

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

        url_ok = isinstance(self.base_url, str) and (
            self.base_url.startswith("http://") or self.base_url.startswith("https://")
        )
        if not url_ok:
            err_msg_lines.append(
                "UefiRedfishCredential.base_url must start with 'http://' or 'https://'.  observed={!r}".format(self.base_url)
            )

        have_basic = self.username is not None and self.password is not None
        have_token = self.token is not None

        if not have_basic and not have_token:
            err_msg_lines.append(
                "UefiRedfishCredential requires either a token or a username+password pair."
            )

        have_username_only = self.username is not None and self.token is not None
        if have_username_only and not self.prefer_token:
            err_msg_lines.append(
                "UefiRedfishCredential: both 'token' and 'username' are supplied; set prefer_token=True to disambiguate."
            )

        if self.ca_bundle is not None:
            if not isinstance(self.ca_bundle, str) or len(self.ca_bundle) == 0:
                err_msg_lines.append(
                    "UefiRedfishCredential.ca_bundle must be a non-empty string when supplied.  observed={!r}".format(self.ca_bundle)
                )

        if len(err_msg_lines) > 0:
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg) from None

        return

    def __repr__(self) -> str:
        """
            Render a representation with ``password`` and ``token`` redacted.

            :returns: The representation string.
        """
        rtnval = (
            "UefiRedfishCredential("
            "base_url={base_url!r}, "
            "username={username!r}, "
            "password={password}, "
            "token={token}, "
            "ca_bundle={ca_bundle!r}, "
            "verify_tls={verify_tls!r}, "
            "prefer_token={prefer_token!r})"
        ).format(
            base_url=self.base_url,
            username=self.username,
            password=_redacted(self.password),
            token=_redacted(self.token),
            ca_bundle=self.ca_bundle,
            verify_tls=self.verify_tls,
            prefer_token=self.prefer_token,
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
    "UefiRedfishCredential",
]
