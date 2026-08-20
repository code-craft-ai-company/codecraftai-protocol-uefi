"""
    .. module:: test_uefiipmicredential
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Unit tests for :class:`UefiIpmiCredential`.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Code Craft AI LLC"
__credits__ = []


import dataclasses
import unittest

from ccai.errors.exceptions import ConfigurationError

from ccai.protocols.uefi.uefiipmicredential import (
    ALLOWED_IPMI_INTERFACES,
    MAX_PORT,
    MIN_PORT,
    UefiIpmiCredential,
)


class UefiIpmiCredentialConstructionTestCase(unittest.TestCase):

    def test_defaults_accepted(self) -> None:
        """
            A minimal valid construction must succeed and set documented
            defaults.
        """
        cred = UefiIpmiCredential(host="10.0.0.5", username="ADMIN", password="hunter2")
        self.assertEqual(cred.host, "10.0.0.5")
        self.assertEqual(cred.username, "ADMIN")
        self.assertEqual(cred.password, "hunter2")
        self.assertEqual(cred.interface, "lanplus")
        self.assertEqual(cred.port, 623)
        return

    def test_every_allowed_interface_accepted(self) -> None:
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
            self.assertEqual(cred.interface, value)
        return


class UefiIpmiCredentialRejectionTestCase(unittest.TestCase):

    def test_empty_host_rejected(self) -> None:
        """
            An empty host must raise ConfigurationError.
        """
        with self.assertRaises(ConfigurationError):
            UefiIpmiCredential(host="", username="ADMIN", password="hunter2")
        return

    def test_empty_username_rejected(self) -> None:
        """
            An empty username must raise ConfigurationError.
        """
        with self.assertRaises(ConfigurationError):
            UefiIpmiCredential(host="10.0.0.5", username="", password="hunter2")
        return

    def test_empty_password_rejected(self) -> None:
        """
            An empty password must raise ConfigurationError.
        """
        with self.assertRaises(ConfigurationError):
            UefiIpmiCredential(host="10.0.0.5", username="ADMIN", password="")
        return

    def test_unknown_interface_rejected(self) -> None:
        """
            An unknown interface string must raise ConfigurationError.
        """
        with self.assertRaises(ConfigurationError):
            UefiIpmiCredential(
                host="10.0.0.5",
                username="ADMIN",
                password="hunter2",
                interface="serial",
            )
        return

    def test_port_below_range_rejected(self) -> None:
        """
            A port value below MIN_PORT must raise ConfigurationError.
        """
        with self.assertRaises(ConfigurationError):
            UefiIpmiCredential(
                host="10.0.0.5",
                username="ADMIN",
                password="hunter2",
                port=MIN_PORT - 1,
            )
        return

    def test_port_above_range_rejected(self) -> None:
        """
            A port value above MAX_PORT must raise ConfigurationError.
        """
        with self.assertRaises(ConfigurationError):
            UefiIpmiCredential(
                host="10.0.0.5",
                username="ADMIN",
                password="hunter2",
                port=MAX_PORT + 1,
            )
        return

    def test_port_non_int_rejected(self) -> None:
        """
            A non-int port must raise ConfigurationError.
        """
        with self.assertRaises(ConfigurationError):
            UefiIpmiCredential(
                host="10.0.0.5",
                username="ADMIN",
                password="hunter2",
                port="623",  # type: ignore[arg-type]
            )
        return

    def test_port_bool_rejected(self) -> None:
        """
            A bool in place of port must raise ConfigurationError.
        """
        with self.assertRaises(ConfigurationError):
            UefiIpmiCredential(
                host="10.0.0.5",
                username="ADMIN",
                password="hunter2",
                port=True,  # type: ignore[arg-type]
            )
        return

    def test_port_boundary_values_accepted(self) -> None:
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
        self.assertEqual(low.port, MIN_PORT)
        self.assertEqual(high.port, MAX_PORT)
        return


class UefiIpmiCredentialFrozenTestCase(unittest.TestCase):

    def test_cannot_mutate_password(self) -> None:
        """
            A frozen-dataclass instance must reject password assignment.
        """
        cred = UefiIpmiCredential(host="10.0.0.5", username="ADMIN", password="hunter2")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            cred.password = "other"  # type: ignore[misc]
        return


class UefiIpmiCredentialReprTestCase(unittest.TestCase):

    def test_repr_redacts_password(self) -> None:
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
        self.assertNotIn(plaintext, rendered)
        self.assertIn("***", rendered)
        return

    def test_str_matches_repr(self) -> None:
        """
            str(cred) must equal repr(cred) so interpolation is safe.
        """
        cred = UefiIpmiCredential(host="10.0.0.5", username="ADMIN", password="hunter2")
        self.assertEqual(str(cred), repr(cred))
        return


if __name__ == "__main__":
    unittest.main()
