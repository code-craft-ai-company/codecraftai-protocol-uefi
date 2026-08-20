"""
    .. module:: test_uefiredfishcredential
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Unit tests for :class:`UefiRedfishCredential`.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Code Craft AI LLC"
__credits__ = []


import dataclasses
import unittest

from ccai.errors.exceptions import ConfigurationError

from ccai.protocols.uefi.uefiredfishcredential import UefiRedfishCredential


class UefiRedfishCredentialConstructionTestCase(unittest.TestCase):

    def test_basic_auth_accepted(self) -> None:
        """
            username + password alone must be a valid construction.
        """
        cred = UefiRedfishCredential(
            base_url="https://bmc.example.com",
            username="admin",
            password="hunter2",
        )
        self.assertEqual(cred.base_url, "https://bmc.example.com")
        self.assertEqual(cred.username, "admin")
        self.assertEqual(cred.password, "hunter2")
        self.assertIsNone(cred.token)
        return

    def test_token_only_accepted(self) -> None:
        """
            A bearer token alone must be a valid construction.
        """
        cred = UefiRedfishCredential(
            base_url="https://bmc.example.com",
            token="AAAA.BBBB.CCCC",
        )
        self.assertEqual(cred.token, "AAAA.BBBB.CCCC")
        self.assertIsNone(cred.username)
        self.assertIsNone(cred.password)
        return

    def test_tls_verify_defaults_to_true(self) -> None:
        """
            verify_tls must default to True.
        """
        cred = UefiRedfishCredential(
            base_url="https://bmc.example.com",
            token="AAAA",
        )
        self.assertTrue(cred.verify_tls)
        return

    def test_prefer_token_defaults_to_false(self) -> None:
        """
            prefer_token must default to False.
        """
        cred = UefiRedfishCredential(
            base_url="https://bmc.example.com",
            token="AAAA",
        )
        self.assertFalse(cred.prefer_token)
        return


class UefiRedfishCredentialRejectionTestCase(unittest.TestCase):

    def test_missing_all_auth_rejected(self) -> None:
        """
            Neither a token nor username+password must raise
            ConfigurationError.
        """
        with self.assertRaises(ConfigurationError):
            UefiRedfishCredential(base_url="https://bmc.example.com")
        return

    def test_username_without_password_rejected(self) -> None:
        """
            username-only (no password and no token) must raise
            ConfigurationError.
        """
        with self.assertRaises(ConfigurationError):
            UefiRedfishCredential(
                base_url="https://bmc.example.com",
                username="admin",
            )
        return

    def test_bad_url_scheme_rejected(self) -> None:
        """
            A base_url without http:// or https:// must raise
            ConfigurationError.
        """
        with self.assertRaises(ConfigurationError):
            UefiRedfishCredential(
                base_url="ftp://bmc.example.com",
                token="AAAA",
            )
        return

    def test_both_auth_without_prefer_token_rejected(self) -> None:
        """
            When both token and username are supplied, prefer_token must be
            True or the construction raises ConfigurationError.
        """
        with self.assertRaises(ConfigurationError):
            UefiRedfishCredential(
                base_url="https://bmc.example.com",
                username="admin",
                password="hunter2",
                token="AAAA",
                prefer_token=False,
            )
        return

    def test_both_auth_with_prefer_token_accepted(self) -> None:
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
        self.assertTrue(cred.prefer_token)
        return

    def test_empty_ca_bundle_rejected(self) -> None:
        """
            ca_bundle provided but empty-string must raise ConfigurationError.
        """
        with self.assertRaises(ConfigurationError):
            UefiRedfishCredential(
                base_url="https://bmc.example.com",
                token="AAAA",
                ca_bundle="",
            )
        return

    def test_nonempty_ca_bundle_accepted(self) -> None:
        """
            A non-empty ca_bundle string must be accepted without a
            filesystem check.
        """
        cred = UefiRedfishCredential(
            base_url="https://bmc.example.com",
            token="AAAA",
            ca_bundle="/etc/ssl/certs/ca-bundle.pem",
        )
        self.assertEqual(cred.ca_bundle, "/etc/ssl/certs/ca-bundle.pem")
        return


class UefiRedfishCredentialFrozenTestCase(unittest.TestCase):

    def test_cannot_mutate_token(self) -> None:
        """
            A frozen-dataclass instance must reject token assignment.
        """
        cred = UefiRedfishCredential(base_url="https://bmc.example.com", token="AAAA")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            cred.token = "BBBB"  # type: ignore[misc]
        return


class UefiRedfishCredentialReprTestCase(unittest.TestCase):

    def test_repr_redacts_password(self) -> None:
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
        self.assertNotIn(plaintext, rendered)
        self.assertIn("***", rendered)
        return

    def test_repr_redacts_token(self) -> None:
        """
            The token plaintext must never appear in __repr__.
        """
        token = "tok-abcdef-1234567890"
        cred = UefiRedfishCredential(
            base_url="https://bmc.example.com",
            token=token,
        )
        rendered = repr(cred)
        self.assertNotIn(token, rendered)
        self.assertIn("***", rendered)
        return

    def test_repr_shows_none_when_secret_unset(self) -> None:
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
        self.assertIn("token=None", rendered)
        self.assertNotIn("token=***", rendered)
        return

    def test_str_matches_repr(self) -> None:
        """
            str(cred) must equal repr(cred) so interpolation is safe.
        """
        cred = UefiRedfishCredential(
            base_url="https://bmc.example.com",
            token="AAAA",
        )
        self.assertEqual(str(cred), repr(cred))
        return


if __name__ == "__main__":
    unittest.main()
