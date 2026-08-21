"""
    .. module:: test_uefiserialcredential
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Unit tests for :class:`UefiSerialCredential`.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Code Craft AI LLC"
__credits__ = []


from ccai import testbase

import dataclasses

from ccai.errors.exceptions import ConfigurationError

from ccai.protocols.uefi.uefiserialcredential import (
    ALLOWED_FLOW_CONTROL,
    ALLOWED_PARITY,
    ALLOWED_STOPBITS,
    UefiSerialCredential,
)


# ----------------------------------------------------------------------
# UefiSerialCredentialConstructionTestCase
# ----------------------------------------------------------------------


def test_defaults_accepted() -> None:
    """
        A minimal construction with only 'device' must succeed and set
        the documented defaults.
    """
    cred = UefiSerialCredential(device="/dev/ttyUSB0")
    testbase.assert_equal(cred.device, "/dev/ttyUSB0")
    testbase.assert_equal(cred.baud, 115200)
    testbase.assert_equal(cred.bytesize, 8)
    testbase.assert_equal(cred.parity, "N")
    testbase.assert_equal(cred.stopbits, 1)
    testbase.assert_equal(cred.flow_control, "none")
    testbase.assert_true(cred.login is None)
    testbase.assert_true(cred.password is None)
    return

def test_socket_url_device_accepted() -> None:
    """
        URL-form device strings must be accepted verbatim.
    """
    cred = UefiSerialCredential(device="socket://host:4001")
    testbase.assert_equal(cred.device, "socket://host:4001")
    return

def test_telnet_url_device_accepted() -> None:
    """
        Telnet URL-form device strings must be accepted verbatim.
    """
    cred = UefiSerialCredential(device="telnet://host:23")
    testbase.assert_equal(cred.device, "telnet://host:23")
    return


# ----------------------------------------------------------------------
# UefiSerialCredentialRejectionTestCase
# ----------------------------------------------------------------------


def test_empty_device_rejected() -> None:
    """
        An empty device string must raise ConfigurationError.
    """
    with testbase.raises(ConfigurationError):
        UefiSerialCredential(device="")
    return

def test_non_string_device_rejected() -> None:
    """
        A non-string device value must raise ConfigurationError.
    """
    with testbase.raises(ConfigurationError):
        UefiSerialCredential(device=None)  # type: ignore[arg-type]
    return

def test_non_positive_baud_rejected() -> None:
    """
        A baud rate of zero or negative must raise ConfigurationError.
    """
    with testbase.raises(ConfigurationError):
        UefiSerialCredential(device="/dev/ttyUSB0", baud=0)
    with testbase.raises(ConfigurationError):
        UefiSerialCredential(device="/dev/ttyUSB0", baud=-9600)
    return

def test_bool_baud_rejected() -> None:
    """
        A bool in place of baud must raise ConfigurationError.
    """
    with testbase.raises(ConfigurationError):
        UefiSerialCredential(device="/dev/ttyUSB0", baud=True)  # type: ignore[arg-type]
    return

def test_invalid_parity_rejected() -> None:
    """
        A parity value outside the allowed set must raise
        ConfigurationError.
    """
    with testbase.raises(ConfigurationError):
        UefiSerialCredential(device="/dev/ttyUSB0", parity="Z")
    return

def test_all_allowed_parity_accepted() -> None:
    """
        Every allowed parity value must construct cleanly.
    """
    for value in ALLOWED_PARITY:
        cred = UefiSerialCredential(device="/dev/ttyUSB0", parity=value)
        testbase.assert_equal(cred.parity, value)
    return

def test_invalid_stopbits_rejected() -> None:
    """
        A stopbits value outside the allowed set must raise
        ConfigurationError.
    """
    with testbase.raises(ConfigurationError):
        UefiSerialCredential(device="/dev/ttyUSB0", stopbits=3)
    return

def test_all_allowed_stopbits_accepted() -> None:
    """
        Every allowed stopbits value must construct cleanly.
    """
    for value in ALLOWED_STOPBITS:
        cred = UefiSerialCredential(device="/dev/ttyUSB0", stopbits=value)
        testbase.assert_equal(cred.stopbits, value)
    return

def test_invalid_flow_control_rejected() -> None:
    """
        A flow_control value outside the allowed set must raise
        ConfigurationError.
    """
    with testbase.raises(ConfigurationError):
        UefiSerialCredential(device="/dev/ttyUSB0", flow_control="hardware")
    return

def test_all_allowed_flow_control_accepted() -> None:
    """
        Every allowed flow_control value must construct cleanly.
    """
    for value in ALLOWED_FLOW_CONTROL:
        cred = UefiSerialCredential(device="/dev/ttyUSB0", flow_control=value)
        testbase.assert_equal(cred.flow_control, value)
    return

def test_multiple_errors_joined() -> None:
    """
        Multiple field-level errors must be joined into a single
        multi-line exception message.
    """
    try:
        UefiSerialCredential(device="/dev/ttyUSB0", baud=-1, parity="Z", stopbits=3, flow_control="hw")
    except ConfigurationError as cerr:
        message = str(cerr)
        testbase.assert_true("baud" in message)
        testbase.assert_true("parity" in message)
        testbase.assert_true("stopbits" in message)
        testbase.assert_true("flow_control" in message)
        return
    raise AssertionError("Expected ConfigurationError was not raised.")
    return


# ----------------------------------------------------------------------
# UefiSerialCredentialFrozenTestCase
# ----------------------------------------------------------------------


def test_cannot_mutate_device() -> None:
    """
        A frozen-dataclass instance must reject attribute assignment.
    """
    cred = UefiSerialCredential(device="/dev/ttyUSB0")
    with testbase.raises(dataclasses.FrozenInstanceError):
        cred.device = "/dev/ttyUSB1"  # type: ignore[misc]
    return

def test_cannot_mutate_password() -> None:
    """
        A frozen-dataclass instance must reject password assignment.
    """
    cred = UefiSerialCredential(device="/dev/ttyUSB0", password="hunter2")
    with testbase.raises(dataclasses.FrozenInstanceError):
        cred.password = "other"  # type: ignore[misc]
    return


# ----------------------------------------------------------------------
# UefiSerialCredentialReprTestCase
# ----------------------------------------------------------------------


def test_repr_redacts_password_when_set() -> None:
    """
        The password plaintext must never appear in __repr__.
    """
    plaintext = "hunter2-very-secret"
    cred = UefiSerialCredential(device="/dev/ttyUSB0", login="admin", password=plaintext)
    rendered = repr(cred)
    testbase.assert_true(plaintext not in rendered)
    testbase.assert_true("***" in rendered)
    return

def test_repr_shows_none_when_password_unset() -> None:
    """
        When password is None, the repr must render the literal token
        'None' rather than '***'.
    """
    cred = UefiSerialCredential(device="/dev/ttyUSB0")
    rendered = repr(cred)
    testbase.assert_true("password=None" in rendered)
    testbase.assert_true("password=***" not in rendered)
    return

def test_str_matches_repr() -> None:
    """
        str(cred) must equal repr(cred) so interpolation is safe.
    """
    cred = UefiSerialCredential(device="/dev/ttyUSB0", password="s3cret")
    testbase.assert_equal(str(cred), repr(cred))
    return
