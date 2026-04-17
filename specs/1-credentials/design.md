# Design — Credentials

## Overview

The Credentials feature delivers three frozen dataclasses —
`UefiSerialCredential`, `UefiRedfishCredential`, and `UefiIpmiCredential` —
plus a convention for reusing the existing `SshCredential` for the
SSH-to-host transport.  Each class is immutable after construction, validates
itself via a dedicated `validate()` method called from `__post_init__`,
redacts sensitive fields (password, token) from `__repr__` / `__str__`, and
raises `ConfigurationError` with multi-line `details` on any misconfiguration.

The design mirrors the pattern established by
`automojo.credentials.sshcredential.SshCredential`: construction is the
primary validation gate, storage is frozen, and there is no additional
setter API.

## Architecture

```
automojo.protocols.uefi.credentials/
    __init__.py                  # re-exports the three classes
    uefiserialcredential.py
    uefiredfishcredential.py
    uefiipmicredential.py
    _redaction.py                # _redacted() helper used by __repr__
```

Dependency direction:

```
uefiserialcredential.py   ──▶  automojo.errors.exceptions
uefiredfishcredential.py  ──▶  automojo.errors.exceptions
uefiipmicredential.py     ──▶  automojo.errors.exceptions
*credential.py            ──▶  _redaction.py
```

The feature does not depend on `1-aspects`, `1-constants-and-types`,
`1-helpers`, `1-logging`, or `1-interfaces`.  It imports `SshCredential`
only for type hints in docstrings and does not construct it.

## Components and Interfaces

### `_redaction.py`

```python
SENSITIVE_REDACTED = "<redacted>"


def _redacted(value: object) -> str:
    """
        Produce the redaction token used by every ``__repr__`` override in
        this package.

        :param value: The field value to redact.

        :returns: The string ``"<redacted>"`` when ``value`` is not ``None``,
                  or the string ``"None"`` when ``value`` is ``None``.
    """
    if value is None:
        rtnval = "None"
    else:
        rtnval = SENSITIVE_REDACTED
    return rtnval
```

### `UefiSerialCredential`

```python
import dataclasses
import os
from typing import Optional

from automojo.errors.exceptions import ConfigurationError
from ._redaction import _redacted


ALLOWED_BAUD = (9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600)
ALLOWED_FLOW_CONTROL = ("none", "xonxoff", "rtscts")


@dataclasses.dataclass(frozen=True, slots=True)
class UefiSerialCredential:
    """
        Credential for a serial-console transport.

        :param baud: Baud rate; must be one of ``ALLOWED_BAUD``.
        :param flow_control: One of ``"none"``, ``"xonxoff"``, ``"rtscts"``.
        :param login: Optional login name for a firmware-gated shell.
        :param password: Optional password for the firmware-gated shell.
        :param allow_partial_login: When ``True``, skips the
                                    ``login`` / ``password`` pairing check.
    """
    baud: int = 115200
    flow_control: str = "none"
    login: Optional[str] = None
    password: Optional[str] = None
    allow_partial_login: bool = False

    def __post_init__(self) -> None:
        self.validate()
        return

    def validate(self) -> None:
        """
            Re-run validation.  Raises ``ConfigurationError`` on any
            problem; returns silently on success.

            :raises: ConfigurationError
        """
        err_msg_lines: list[str] = []

        if self.baud not in ALLOWED_BAUD:
            err_msg_lines.append(
                f"UefiSerialCredential.baud={self.baud!r} not in {ALLOWED_BAUD}."
            )

        if self.flow_control not in ALLOWED_FLOW_CONTROL:
            err_msg_lines.append(
                f"UefiSerialCredential.flow_control={self.flow_control!r} not in {ALLOWED_FLOW_CONTROL}."
            )

        partial = (self.login is None) ^ (self.password is None)
        if partial and not self.allow_partial_login:
            err_msg_lines.append(
                "UefiSerialCredential.login and .password must both be set or both be None "
                "(set allow_partial_login=True to override)."
            )

        if len(err_msg_lines) > 0:
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg)

        return

    def __repr__(self) -> str:
        rtnval = (
            f"UefiSerialCredential(baud={self.baud!r}, flow_control={self.flow_control!r}, "
            f"login={self.login!r}, password={_redacted(self.password)}, "
            f"allow_partial_login={self.allow_partial_login!r})"
        )
        return rtnval

    def __str__(self) -> str:
        rtnval = self.__repr__()
        return rtnval
```

### `UefiRedfishCredential`

```python
@dataclasses.dataclass(frozen=True, slots=True)
class UefiRedfishCredential:
    """
        Credential for a Redfish transport.

        :param url_base: Base URL including scheme (``http://`` or ``https://``).
        :param username: Optional username for Basic auth.
        :param password: Optional password for Basic auth.
        :param token: Optional bearer / X-Auth-Token value.
        :param ca_bundle: Optional path to a CA bundle file.
        :param tls_verify: When ``True`` (default), verify the BMC's TLS cert.
        :param prefer_token: When ``True``, token overrides username/password
                             if both are supplied.
    """
    url_base: str
    username: Optional[str] = None
    password: Optional[str] = None
    token: Optional[str] = None
    ca_bundle: Optional[str] = None
    tls_verify: bool = True
    prefer_token: bool = False

    def __post_init__(self) -> None:
        self.validate()
        return

    def validate(self) -> None:
        err_msg_lines: list[str] = []

        if not isinstance(self.url_base, str) or not (self.url_base.startswith("http://") or self.url_base.startswith("https://")):
            err_msg_lines.append(f"UefiRedfishCredential.url_base must start with http:// or https://.  observed={self.url_base!r}")

        if self.url_base.startswith("http://") and self.tls_verify:
            err_msg_lines.append("UefiRedfishCredential: tls_verify=True is meaningless with http:// url_base.")

        have_basic = self.username is not None and self.password is not None
        have_token = self.token is not None

        if not have_basic and not have_token:
            err_msg_lines.append("UefiRedfishCredential: either (username+password) or token must be provided.")

        if have_basic and have_token and not self.prefer_token:
            err_msg_lines.append(
                "UefiRedfishCredential: both token and username/password supplied; set prefer_token=True to disambiguate."
            )

        if self.ca_bundle is not None and not self.tls_verify:
            err_msg_lines.append("UefiRedfishCredential: ca_bundle is set but tls_verify=False; the bundle will be ignored.")

        if len(err_msg_lines) > 0:
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg)

        return

    @property
    def effective_auth_mode(self) -> str:
        """
            :returns: ``"token"`` if the token should be used, ``"basic"`` if
                      username/password should be used.
            :raises: ConfigurationError
        """
        have_basic = self.username is not None and self.password is not None
        have_token = self.token is not None
        if have_token and (self.prefer_token or not have_basic):
            rtnval = "token"
        elif have_basic:
            rtnval = "basic"
        else:
            err_msg = "UefiRedfishCredential: no effective auth mode (neither token nor username/password set)."
            raise ConfigurationError(err_msg)
        return rtnval

    def __repr__(self) -> str:
        rtnval = (
            f"UefiRedfishCredential(url_base={self.url_base!r}, username={self.username!r}, "
            f"password={_redacted(self.password)}, token={_redacted(self.token)}, "
            f"ca_bundle={self.ca_bundle!r}, tls_verify={self.tls_verify!r}, "
            f"prefer_token={self.prefer_token!r})"
        )
        return rtnval
```

### `UefiIpmiCredential`

```python
ALLOWED_IPMI_INTERFACES = ("lan", "lanplus")


@dataclasses.dataclass(frozen=True, slots=True)
class UefiIpmiCredential:
    """
        Credential for an IPMI / SOL transport.

        :param host: BMC hostname or IP.
        :param user: Account name on the BMC.
        :param password: Account password.
        :param interface: IPMI interface; one of ``"lan"``, ``"lanplus"``.
    """
    host: str
    user: str
    password: str
    interface: str = "lanplus"

    def __post_init__(self) -> None:
        self.validate()
        return

    def validate(self) -> None:
        err_msg_lines: list[str] = []

        if not isinstance(self.host, str) or len(self.host) == 0:
            err_msg_lines.append(f"UefiIpmiCredential.host must be a non-empty string.  observed={self.host!r}")

        if not isinstance(self.user, str) or len(self.user) == 0:
            err_msg_lines.append(f"UefiIpmiCredential.user must be a non-empty string.  observed={self.user!r}")

        if self.interface not in ALLOWED_IPMI_INTERFACES:
            err_msg_lines.append(f"UefiIpmiCredential.interface={self.interface!r} not in {ALLOWED_IPMI_INTERFACES}.")

        if len(err_msg_lines) > 0:
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg)

        return

    def __repr__(self) -> str:
        rtnval = (
            f"UefiIpmiCredential(host={self.host!r}, user={self.user!r}, "
            f"password={_redacted(self.password)}, interface={self.interface!r})"
        )
        return rtnval
```

## Data Models

| Class                      | Frozen | Sensitive Fields                |
|----------------------------|--------|---------------------------------|
| `UefiSerialCredential`     | yes    | `password`                      |
| `UefiRedfishCredential`    | yes    | `password`, `token`             |
| `UefiIpmiCredential`       | yes    | `password`                      |
| `SshCredential` (reused)   | —      | as declared by the SSH library  |

Every class is a `@dataclass(frozen=True, slots=True)`.  All sequence-typed
fields (none currently) would be declared as `tuple[...]`.

## Measured Interop Contract

This feature does not consume aspects.  Credentials are passed alongside
aspects into transports in layer 2, and the transports use the
`aspects.allowed_connection_exceptions` and `aspects.connection_timeout`
fields to drive the connect loop.  The credentials themselves do no retry
logic.

## Error Handling

| Condition                                                | Exception              | Details content                       |
|----------------------------------------------------------|------------------------|---------------------------------------|
| Invalid `baud` in serial credential                      | `ConfigurationError`   | observed value, allowed set           |
| Invalid `flow_control` in serial credential              | `ConfigurationError`   | observed value, allowed set           |
| Partial login in serial credential                       | `ConfigurationError`   | pairing rule                          |
| `url_base` without scheme in Redfish credential          | `ConfigurationError`   | observed value                        |
| `http://` + `tls_verify=True`                            | `ConfigurationError`   | rule violation                        |
| Neither basic-auth nor token supplied                    | `ConfigurationError`   | rule violation                        |
| Both auth modes without `prefer_token`                   | `ConfigurationError`   | rule violation                        |
| `ca_bundle` with `tls_verify=False`                      | `ConfigurationError`   | rule violation                        |
| Empty `host` or `user` in IPMI credential                | `ConfigurationError`   | field name and observed value         |
| Invalid `interface` in IPMI credential                   | `ConfigurationError`   | observed value, allowed set           |
| Mutation of any credential attribute                     | `FrozenInstanceError`  | raised by dataclass                   |
| SSH-to-host transport handed a non-`SshCredential`       | `TypeError`            | expected dotted path                  |

`SemanticError` is not raised by this feature.

## Logging

Credentials emit no log output.  They are values, not behaviors.

## Security Considerations

* **Redaction is mandatory.**  Every `__repr__` renders sensitive fields as
  `"<redacted>"`.  The `__str__` override returns the same redacted
  rendering so that naive `f"{credential}"` interpolation is safe.
* **Pickling exposes secrets.**  A pickled credential is as sensitive as the
  underlying secret.  Consumers that serialize credentials are responsible
  for transport-level encryption.
* **CA bundle path is not validated for existence at construction time.**
  That check happens in the transport when the TLS connection is built,
  because a credential may be constructed in one process and used in
  another where the path is mounted differently.
* **No environment-variable hooks.**  This feature never reads
  `PASSWORD` / `TOKEN` from the environment.  Callers wire that in.
* **Validation failures include field values other than secrets.**  The
  multiline error constructed in `validate()` never embeds a password or
  token value.

## Testing Strategy

### Unit tests (`test_uefiserialcredential.py`)

* Construct with defaults; assert `baud=115200`, `flow_control="none"`.
* Construct with every allowed `baud` and `flow_control`.
* Assert invalid `baud` raises `ConfigurationError`.
* Assert invalid `flow_control` raises `ConfigurationError`.
* Assert partial login without `allow_partial_login` raises
  `ConfigurationError`.
* Assert partial login with `allow_partial_login=True` is accepted.
* Assert `__repr__` contains `"<redacted>"` when `password` is set and
  `"None"` when it is `None`.
* Assert attribute assignment raises `FrozenInstanceError`.
* Assert pickle round-trip preserves equality.

### Unit tests (`test_uefiredfishcredential.py`)

* Construct with `username`+`password`; assert `effective_auth_mode ==
  "basic"`.
* Construct with `token` only; assert `effective_auth_mode == "token"`.
* Construct with both + `prefer_token=True`; assert
  `effective_auth_mode == "token"`.
* Construct with both + `prefer_token=False`; assert
  `ConfigurationError`.
* Construct with neither; assert `ConfigurationError`.
* Construct with `http://` + `tls_verify=True`; assert
  `ConfigurationError`.
* Construct with `ca_bundle` + `tls_verify=False`; assert
  `ConfigurationError`.
* Assert `__repr__` redacts both `password` and `token`.
* Assert pickle round-trip.

### Unit tests (`test_uefiipmicredential.py`)

* Construct with valid inputs.
* Empty `host` / `user`: assert `ConfigurationError`.
* Invalid `interface`: assert `ConfigurationError`.
* `__repr__` redacts password.
* Pickle round-trip.

### Cross-class tests (`test_credentials_common.py`)

* Iterate every credential class; assert `__repr__` never contains any
  sensitive field's plaintext string.
* Assert `str(cred) == repr(cred)`.
* Iterate every credential class; assert `dataclasses.is_dataclass(cls) and
  cls.__dataclass_params__.frozen`.

### Coverage target

100% line coverage for the credentials package.

## Dependencies on Other Features

* Depends on `automojo.errors.exceptions.ConfigurationError`.
* Documents reuse of `automojo.credentials.sshcredential.SshCredential` for
  the SSH-to-host transport; does not import it.
* No dependency on `1-aspects`, `1-constants-and-types`, `1-exceptions`,
  `1-helpers`, `1-logging`, or `1-interfaces`.
