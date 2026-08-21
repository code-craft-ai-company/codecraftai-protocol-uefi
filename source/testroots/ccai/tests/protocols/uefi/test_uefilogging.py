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


from ccai import testbase

from ccai.errors.exceptions import ConfigurationError

from ccai.protocols.uefi import uefiaspects

from ccai.protocols.uefi.uefilogging import (
    LoggingPattern,
    UEFI_LOGGING_CHANNELS,
    UEFI_SHELL_CHANNEL,
    get_uefi_logger,
)


# ----------------------------------------------------------------------
# UefiLoggingChannelsTestCase
# ----------------------------------------------------------------------


def test_every_channel_has_uefi_prefix() -> None:
    """
        Every documented channel name must begin with the ``uefi.``
        namespace marker so log records are attributable to this
        library.
    """
    for channel in UEFI_LOGGING_CHANNELS:
        testbase.assert_true(channel.startswith("uefi."),
                        "channel {!r} does not start with 'uefi.'".format(channel))
    return

def test_shell_channel_constant_value() -> None:
    """
        The individual channel-name constants must match the entries
        in :data:`UEFI_LOGGING_CHANNELS`.
    """
    testbase.assert_equal(UEFI_SHELL_CHANNEL, "uefi.shell")
    testbase.assert_true(UEFI_SHELL_CHANNEL in UEFI_LOGGING_CHANNELS)
    return


# ----------------------------------------------------------------------
# GetUefiLoggerTestCase
# ----------------------------------------------------------------------


def test_known_channel_returns_named_logger() -> None:
    """
        :func:`get_uefi_logger` returns a logger whose ``name``
        equals the requested channel.
    """
    logger = get_uefi_logger(UEFI_SHELL_CHANNEL)
    observed_name = logger._logger.name
    testbase.assert_equal(observed_name, "uefi.shell")
    return

def test_unknown_channel_raises_configuration_error() -> None:
    """
        An unknown channel name must raise
        :class:`ConfigurationError`.
    """
    with testbase.raises(ConfigurationError) as ctx:
        get_uefi_logger("not.a.channel")

    err_text = str(ctx.exception)
    testbase.assert_true("not.a.channel" in err_text)
    return

def test_every_known_channel_is_acceptable() -> None:
    """
        Every member of :data:`UEFI_LOGGING_CHANNELS` must be
        accepted by :func:`get_uefi_logger`.
    """
    for channel in UEFI_LOGGING_CHANNELS:
        logger = get_uefi_logger(channel)
        observed_name = logger._logger.name
        testbase.assert_equal(observed_name, channel)
    return


# ----------------------------------------------------------------------
# LoggingPatternReexportTestCase
# ----------------------------------------------------------------------


def test_logging_pattern_is_same_class() -> None:
    """
        The :class:`LoggingPattern` re-exported from
        :mod:`uefilogging` must be the same class identity as the
        one in :mod:`uefiaspects`; it must not be wrapped or
        subclassed.
    """
    result = LoggingPattern is uefiaspects.LoggingPattern
    testbase.assert_true(result)
    return
