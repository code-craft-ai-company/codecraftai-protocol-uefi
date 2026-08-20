"""
    .. module:: test_uefilogging
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Unit tests for the UEFI logging factory, channel registry,
               and :class:`LoggingPattern` re-export.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Code Craft AI LLC"
__credits__ = []


import unittest

from ccai.errors.exceptions import ConfigurationError

from ccai.protocols.uefi import uefiaspects

from ccai.protocols.uefi.uefilogging import (
    LoggingPattern,
    UEFI_LOGGING_CHANNELS,
    UEFI_SHELL_CHANNEL,
    get_uefi_logger,
)


class UefiLoggingChannelsTestCase(unittest.TestCase):

    def test_every_channel_has_uefi_prefix(self) -> None:
        """
            Every documented channel name must begin with the ``uefi.``
            namespace marker so log records are attributable to this
            library.
        """
        for channel in UEFI_LOGGING_CHANNELS:
            self.assertTrue(channel.startswith("uefi."),
                            "channel {!r} does not start with 'uefi.'".format(channel))
        return

    def test_shell_channel_constant_value(self) -> None:
        """
            The individual channel-name constants must match the entries
            in :data:`UEFI_LOGGING_CHANNELS`.
        """
        self.assertEqual(UEFI_SHELL_CHANNEL, "uefi.shell")
        self.assertIn(UEFI_SHELL_CHANNEL, UEFI_LOGGING_CHANNELS)
        return


class GetUefiLoggerTestCase(unittest.TestCase):

    def test_known_channel_returns_named_logger(self) -> None:
        """
            :func:`get_uefi_logger` returns a logger whose ``name``
            equals the requested channel.
        """
        logger = get_uefi_logger(UEFI_SHELL_CHANNEL)
        observed_name = logger._logger.name
        self.assertEqual(observed_name, "uefi.shell")
        return

    def test_unknown_channel_raises_configuration_error(self) -> None:
        """
            An unknown channel name must raise
            :class:`ConfigurationError`.
        """
        with self.assertRaises(ConfigurationError) as ctx:
            get_uefi_logger("not.a.channel")

        err_text = str(ctx.exception)
        self.assertIn("not.a.channel", err_text)
        return

    def test_every_known_channel_is_acceptable(self) -> None:
        """
            Every member of :data:`UEFI_LOGGING_CHANNELS` must be
            accepted by :func:`get_uefi_logger`.
        """
        for channel in UEFI_LOGGING_CHANNELS:
            logger = get_uefi_logger(channel)
            observed_name = logger._logger.name
            self.assertEqual(observed_name, channel)
        return


class LoggingPatternReexportTestCase(unittest.TestCase):

    def test_logging_pattern_is_same_class(self) -> None:
        """
            The :class:`LoggingPattern` re-exported from
            :mod:`uefilogging` must be the same class identity as the
            one in :mod:`uefiaspects`; it must not be wrapped or
            subclassed.
        """
        result = LoggingPattern is uefiaspects.LoggingPattern
        self.assertTrue(result)
        return


if __name__ == "__main__":
    unittest.main()
