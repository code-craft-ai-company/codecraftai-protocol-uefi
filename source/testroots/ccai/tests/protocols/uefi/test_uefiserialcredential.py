"""
    .. module:: test_uefiserialcredential
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Unit tests for :class:`UefiSerialCredential`.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Code Craft AI LLC"
__credits__ = []


import dataclasses
import unittest

from ccai.errors.exceptions import ConfigurationError

from ccai.protocols.uefi.uefiserialcredential import (
    ALLOWED_FLOW_CONTROL,
    ALLOWED_PARITY,
    ALLOWED_STOPBITS,
    UefiSerialCredential,
)


class UefiSerialCredentialConstructionTestCase(unittest.TestCase):

    def test_defaults_accepted(self) -> None:
        """
            A minimal construction with only 'device' must succeed and set
            the documented defaults.
        """
        cred = UefiSerialCredential(device="/dev/ttyUSB0")
        self.assertEqual(cred.device, "/dev/ttyUSB0")
        self.assertEqual(cred.baud, 115200)
        self.assertEqual(cred.bytesize, 8)
        self.assertEqual(cred.parity, "N")
        self.assertEqual(cred.stopbits, 1)
        self.assertEqual(cred.flow_control, "none")
        self.assertIsNone(cred.login)
        self.assertIsNone(cred.password)
        return

    def test_socket_url_device_accepted(self) -> None:
        """
            URL-form device strings must be accepted verbatim.
        """
        cred = UefiSerialCredential(device="socket://host:4001")
        self.assertEqual(cred.device, "socket://host:4001")
        return

    def test_telnet_url_device_accepted(self) -> None:
        """
            Telnet URL-form device strings must be accepted verbatim.
        """
        cred = UefiSerialCredential(device="telnet://host:23")
        self.assertEqual(cred.device, "telnet://host:23")
        return


class UefiSerialCredentialRejectionTestCase(unittest.TestCase):

    def test_empty_device_rejected(self) -> None:
        """
            An empty device string must raise ConfigurationError.
        """
        with self.assertRaises(ConfigurationError):
            UefiSerialCredential(device="")
        return

    def test_non_string_device_rejected(self) -> None:
        """
            A non-string device value must raise ConfigurationError.
        """
        with self.assertRaises(ConfigurationError):
            UefiSerialCredential(device=None)  # type: ignore[arg-type]
        return

    def test_non_positive_baud_rejected(self) -> None:
        """
            A baud rate of zero or negative must raise ConfigurationError.
        """
        with self.assertRaises(ConfigurationError):
            UefiSerialCredential(device="/dev/ttyUSB0", baud=0)
        with self.assertRaises(ConfigurationError):
            UefiSerialCredential(device="/dev/ttyUSB0", baud=-9600)
        return

    def test_bool_baud_rejected(self) -> None:
        """
            A bool in place of baud must raise ConfigurationError.
        """
        with self.assertRaises(ConfigurationError):
            UefiSerialCredential(device="/dev/ttyUSB0", baud=True)  # type: ignore[arg-type]
        return

    def test_invalid_parity_rejected(self) -> None:
        """
            A parity value outside the allowed set must raise
            ConfigurationError.
        """
        with self.assertRaises(ConfigurationError):
            UefiSerialCredential(device="/dev/ttyUSB0", parity="Z")
        return

    def test_all_allowed_parity_accepted(self) -> None:
        """
            Every allowed parity value must construct cleanly.
        """
        for value in ALLOWED_PARITY:
            cred = UefiSerialCredential(device="/dev/ttyUSB0", parity=value)
            self.assertEqual(cred.parity, value)
        return

    def test_invalid_stopbits_rejected(self) -> None:
        """
            A stopbits value outside the allowed set must raise
            ConfigurationError.
        """
        with self.assertRaises(ConfigurationError):
            UefiSerialCredential(device="/dev/ttyUSB0", stopbits=3)
        return

    def test_all_allowed_stopbits_accepted(self) -> None:
        """
            Every allowed stopbits value must construct cleanly.
        """
        for value in ALLOWED_STOPBITS:
            cred = UefiSerialCredential(device="/dev/ttyUSB0", stopbits=value)
            self.assertEqual(cred.stopbits, value)
        return

    def test_invalid_flow_control_rejected(self) -> None:
        """
            A flow_control value outside the allowed set must raise
            ConfigurationError.
        """
        with self.assertRaises(ConfigurationError):
            UefiSerialCredential(device="/dev/ttyUSB0", flow_control="hardware")
        return

    def test_all_allowed_flow_control_accepted(self) -> None:
        """
            Every allowed flow_control value must construct cleanly.
        """
        for value in ALLOWED_FLOW_CONTROL:
            cred = UefiSerialCredential(device="/dev/ttyUSB0", flow_control=value)
            self.assertEqual(cred.flow_control, value)
        return

    def test_multiple_errors_joined(self) -> None:
        """
            Multiple field-level errors must be joined into a single
            multi-line exception message.
        """
        try:
            UefiSerialCredential(device="/dev/ttyUSB0", baud=-1, parity="Z", stopbits=3, flow_control="hw")
        except ConfigurationError as cerr:
            message = str(cerr)
            self.assertIn("baud", message)
            self.assertIn("parity", message)
            self.assertIn("stopbits", message)
            self.assertIn("flow_control", message)
            return
        self.fail("Expected ConfigurationError was not raised.")
        return


class UefiSerialCredentialFrozenTestCase(unittest.TestCase):

    def test_cannot_mutate_device(self) -> None:
        """
            A frozen-dataclass instance must reject attribute assignment.
        """
        cred = UefiSerialCredential(device="/dev/ttyUSB0")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            cred.device = "/dev/ttyUSB1"  # type: ignore[misc]
        return

    def test_cannot_mutate_password(self) -> None:
        """
            A frozen-dataclass instance must reject password assignment.
        """
        cred = UefiSerialCredential(device="/dev/ttyUSB0", password="hunter2")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            cred.password = "other"  # type: ignore[misc]
        return


class UefiSerialCredentialReprTestCase(unittest.TestCase):

    def test_repr_redacts_password_when_set(self) -> None:
        """
            The password plaintext must never appear in __repr__.
        """
        plaintext = "hunter2-very-secret"
        cred = UefiSerialCredential(device="/dev/ttyUSB0", login="admin", password=plaintext)
        rendered = repr(cred)
        self.assertNotIn(plaintext, rendered)
        self.assertIn("***", rendered)
        return

    def test_repr_shows_none_when_password_unset(self) -> None:
        """
            When password is None, the repr must render the literal token
            'None' rather than '***'.
        """
        cred = UefiSerialCredential(device="/dev/ttyUSB0")
        rendered = repr(cred)
        self.assertIn("password=None", rendered)
        self.assertNotIn("password=***", rendered)
        return

    def test_str_matches_repr(self) -> None:
        """
            str(cred) must equal repr(cred) so interpolation is safe.
        """
        cred = UefiSerialCredential(device="/dev/ttyUSB0", password="s3cret")
        self.assertEqual(str(cred), repr(cred))
        return


if __name__ == "__main__":
    unittest.main()
