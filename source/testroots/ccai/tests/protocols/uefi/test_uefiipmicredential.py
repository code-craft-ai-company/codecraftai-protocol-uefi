"""
    .. module:: test_uefiipmicredential
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Unit tests for :class:`UefiIpmiCredential`.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Code Craft AI LLC"
__credits__ = []


from ccai import testbase

import dataclasses

from ccai.errors.exceptions import ConfigurationError

from ccai.protocols.uefi.uefiipmicredential import (
    ALLOWED_IPMI_INTERFACES,
    MAX_PORT,
    MIN_PORT,
    UefiIpmiCredential,
)


# ----------------------------------------------------------------------
# UefiIpmiCredentialConstructionTestCase
# ----------------------------------------------------------------------


def test_defaults_accepted() -> None:
    """
        A minimal valid construction must succeed and set documented
        defaults.
    """
    cred = UefiIpmiCredential(host="10.0.0.5", username="ADMIN", password="hunter2")
    testbase.assert_equal(cred.host, "10.0.0.5")
    testbase.assert_equal(cred.username, "ADMIN")
    testbase.assert_equal(cred.password, "hunter2")
    testbase.assert_equal(cred.interface, "lanplus")
    testbase.assert_equal(cred.port, 623)
    return

def test_every_allowed_interface_accepted() -> None:
    """
        Every member of ALLOWED_IPMI_INTERFACES must construct cleanly.
    """
    for value in ALLOWED_IPMI_INTERFACES:
        cred = UefiIpmiCredential(
            host="10.0.0.5",
            username="ADMIN",
            password="hunter2",
            interface=value,
        )
        testbase.assert_equal(cred.interface, value)
    return


# ----------------------------------------------------------------------
# UefiIpmiCredentialRejectionTestCase
# ----------------------------------------------------------------------


def test_empty_host_rejected() -> None:
    """
        An empty host must raise ConfigurationError.
    """
    with testbase.raises(ConfigurationError):
        UefiIpmiCredential(host="", username="ADMIN", password="hunter2")
    return

def test_empty_username_rejected() -> None:
    """
        An empty username must raise ConfigurationError.
    """
    with testbase.raises(ConfigurationError):
        UefiIpmiCredential(host="10.0.0.5", username="", password="hunter2")
    return

def test_empty_password_rejected() -> None:
    """
        An empty password must raise ConfigurationError.
    """
    with testbase.raises(ConfigurationError):
        UefiIpmiCredential(host="10.0.0.5", username="ADMIN", password="")
    return

def test_unknown_interface_rejected() -> None:
    """
        An unknown interface string must raise ConfigurationError.
    """
    with testbase.raises(ConfigurationError):
        UefiIpmiCredential(
            host="10.0.0.5",
            username="ADMIN",
            password="hunter2",
            interface="serial",
        )
    return

def test_port_below_range_rejected() -> None:
    """
        A port value below MIN_PORT must raise ConfigurationError.
    """
    with testbase.raises(ConfigurationError):
        UefiIpmiCredential(
            host="10.0.0.5",
            username="ADMIN",
            password="hunter2",
            port=MIN_PORT - 1,
        )
    return

def test_port_above_range_rejected() -> None:
    """
        A port value above MAX_PORT must raise ConfigurationError.
    """
    with testbase.raises(ConfigurationError):
        UefiIpmiCredential(
            host="10.0.0.5",
            username="ADMIN",
            password="hunter2",
            port=MAX_PORT + 1,
        )
    return

def test_port_non_int_rejected() -> None:
    """
        A non-int port must raise ConfigurationError.
    """
    with testbase.raises(ConfigurationError):
        UefiIpmiCredential(
            host="10.0.0.5",
            username="ADMIN",
            password="hunter2",
            port="623",  # type: ignore[arg-type]
        )
    return

def test_port_bool_rejected() -> None:
    """
        A bool in place of port must raise ConfigurationError.
    """
    with testbase.raises(ConfigurationError):
        UefiIpmiCredential(
            host="10.0.0.5",
            username="ADMIN",
            password="hunter2",
            port=True,  # type: ignore[arg-type]
        )
    return

def test_port_boundary_values_accepted() -> None:
    """
        MIN_PORT and MAX_PORT must both be accepted.
    """
    low = UefiIpmiCredential(
        host="10.0.0.5",
        username="ADMIN",
        password="hunter2",
        port=MIN_PORT,
    )
    high = UefiIpmiCredential(
        host="10.0.0.5",
        username="ADMIN",
        password="hunter2",
        port=MAX_PORT,
    )
    testbase.assert_equal(low.port, MIN_PORT)
    testbase.assert_equal(high.port, MAX_PORT)
    return


# ----------------------------------------------------------------------
# UefiIpmiCredentialFrozenTestCase
# ----------------------------------------------------------------------


def test_cannot_mutate_password() -> None:
    """
        A frozen-dataclass instance must reject password assignment.
    """
    cred = UefiIpmiCredential(host="10.0.0.5", username="ADMIN", password="hunter2")
    with testbase.raises(dataclasses.FrozenInstanceError):
        cred.password = "other"  # type: ignore[misc]
    return


# ----------------------------------------------------------------------
# UefiIpmiCredentialReprTestCase
# ----------------------------------------------------------------------


def test_repr_redacts_password() -> None:
    """
        The password plaintext must never appear in __repr__.
    """
    plaintext = "very-secret-ipmi-password"
    cred = UefiIpmiCredential(
        host="10.0.0.5",
        username="ADMIN",
        password=plaintext,
    )
    rendered = repr(cred)
    testbase.assert_true(plaintext not in rendered)
    testbase.assert_true("***" in rendered)
    return

def test_str_matches_repr() -> None:
    """
        str(cred) must equal repr(cred) so interpolation is safe.
    """
    cred = UefiIpmiCredential(host="10.0.0.5", username="ADMIN", password="hunter2")
    testbase.assert_equal(str(cred), repr(cred))
    return
