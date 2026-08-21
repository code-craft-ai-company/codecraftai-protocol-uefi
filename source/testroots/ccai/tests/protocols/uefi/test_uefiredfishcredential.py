"""
    .. module:: test_uefiredfishcredential
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Unit tests for :class:`UefiRedfishCredential`.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Code Craft AI LLC"
__credits__ = []


from ccai import testbase

import dataclasses

from ccai.errors.exceptions import ConfigurationError

from ccai.protocols.uefi.uefiredfishcredential import UefiRedfishCredential


# ----------------------------------------------------------------------
# UefiRedfishCredentialConstructionTestCase
# ----------------------------------------------------------------------


def test_basic_auth_accepted() -> None:
    """
        username + password alone must be a valid construction.
    """
    cred = UefiRedfishCredential(
        base_url="https://bmc.example.com",
        username="admin",
        password="hunter2",
    )
    testbase.assert_equal(cred.base_url, "https://bmc.example.com")
    testbase.assert_equal(cred.username, "admin")
    testbase.assert_equal(cred.password, "hunter2")
    testbase.assert_true(cred.token is None)
    return

def test_token_only_accepted() -> None:
    """
        A bearer token alone must be a valid construction.
    """
    cred = UefiRedfishCredential(
        base_url="https://bmc.example.com",
        token="AAAA.BBBB.CCCC",
    )
    testbase.assert_equal(cred.token, "AAAA.BBBB.CCCC")
    testbase.assert_true(cred.username is None)
    testbase.assert_true(cred.password is None)
    return

def test_tls_verify_defaults_to_true() -> None:
    """
        verify_tls must default to True.
    """
    cred = UefiRedfishCredential(
        base_url="https://bmc.example.com",
        token="AAAA",
    )
    testbase.assert_true(cred.verify_tls)
    return

def test_prefer_token_defaults_to_false() -> None:
    """
        prefer_token must default to False.
    """
    cred = UefiRedfishCredential(
        base_url="https://bmc.example.com",
        token="AAAA",
    )
    testbase.assert_false(cred.prefer_token)
    return


# ----------------------------------------------------------------------
# UefiRedfishCredentialRejectionTestCase
# ----------------------------------------------------------------------


def test_missing_all_auth_rejected() -> None:
    """
        Neither a token nor username+password must raise
        ConfigurationError.
    """
    with testbase.raises(ConfigurationError):
        UefiRedfishCredential(base_url="https://bmc.example.com")
    return

def test_username_without_password_rejected() -> None:
    """
        username-only (no password and no token) must raise
        ConfigurationError.
    """
    with testbase.raises(ConfigurationError):
        UefiRedfishCredential(
            base_url="https://bmc.example.com",
            username="admin",
        )
    return

def test_bad_url_scheme_rejected() -> None:
    """
        A base_url without http:// or https:// must raise
        ConfigurationError.
    """
    with testbase.raises(ConfigurationError):
        UefiRedfishCredential(
            base_url="ftp://bmc.example.com",
            token="AAAA",
        )
    return

def test_both_auth_without_prefer_token_rejected() -> None:
    """
        When both token and username are supplied, prefer_token must be
        True or the construction raises ConfigurationError.
    """
    with testbase.raises(ConfigurationError):
        UefiRedfishCredential(
            base_url="https://bmc.example.com",
            username="admin",
            password="hunter2",
            token="AAAA",
            prefer_token=False,
        )
    return

def test_both_auth_with_prefer_token_accepted() -> None:
    """
        When both token and username are supplied AND prefer_token=True,
        the construction must succeed.
    """
    cred = UefiRedfishCredential(
        base_url="https://bmc.example.com",
        username="admin",
        password="hunter2",
        token="AAAA",
        prefer_token=True,
    )
    testbase.assert_true(cred.prefer_token)
    return

def test_empty_ca_bundle_rejected() -> None:
    """
        ca_bundle provided but empty-string must raise ConfigurationError.
    """
    with testbase.raises(ConfigurationError):
        UefiRedfishCredential(
            base_url="https://bmc.example.com",
            token="AAAA",
            ca_bundle="",
        )
    return

def test_nonempty_ca_bundle_accepted() -> None:
    """
        A non-empty ca_bundle string must be accepted without a
        filesystem check.
    """
    cred = UefiRedfishCredential(
        base_url="https://bmc.example.com",
        token="AAAA",
        ca_bundle="/etc/ssl/certs/ca-bundle.pem",
    )
    testbase.assert_equal(cred.ca_bundle, "/etc/ssl/certs/ca-bundle.pem")
    return


# ----------------------------------------------------------------------
# UefiRedfishCredentialFrozenTestCase
# ----------------------------------------------------------------------


def test_cannot_mutate_token() -> None:
    """
        A frozen-dataclass instance must reject token assignment.
    """
    cred = UefiRedfishCredential(base_url="https://bmc.example.com", token="AAAA")
    with testbase.raises(dataclasses.FrozenInstanceError):
        cred.token = "BBBB"  # type: ignore[misc]
    return


# ----------------------------------------------------------------------
# UefiRedfishCredentialReprTestCase
# ----------------------------------------------------------------------


def test_repr_redacts_password() -> None:
    """
        The password plaintext must never appear in __repr__.
    """
    plaintext = "super-secret-password-xyz"
    cred = UefiRedfishCredential(
        base_url="https://bmc.example.com",
        username="admin",
        password=plaintext,
    )
    rendered = repr(cred)
    testbase.assert_true(plaintext not in rendered)
    testbase.assert_true("***" in rendered)
    return

def test_repr_redacts_token() -> None:
    """
        The token plaintext must never appear in __repr__.
    """
    token = "tok-abcdef-1234567890"
    cred = UefiRedfishCredential(
        base_url="https://bmc.example.com",
        token=token,
    )
    rendered = repr(cred)
    testbase.assert_true(token not in rendered)
    testbase.assert_true("***" in rendered)
    return

def test_repr_shows_none_when_secret_unset() -> None:
    """
        When token is None, the repr must render the literal token
        'None' rather than '***'.
    """
    cred = UefiRedfishCredential(
        base_url="https://bmc.example.com",
        username="admin",
        password="hunter2",
    )
    rendered = repr(cred)
    testbase.assert_true("token=None" in rendered)
    testbase.assert_true("token=***" not in rendered)
    return

def test_str_matches_repr() -> None:
    """
        str(cred) must equal repr(cred) so interpolation is safe.
    """
    cred = UefiRedfishCredential(
        base_url="https://bmc.example.com",
        token="AAAA",
    )
    testbase.assert_equal(str(cred), repr(cred))
    return
