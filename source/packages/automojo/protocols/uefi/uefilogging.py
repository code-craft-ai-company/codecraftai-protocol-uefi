"""
    .. module:: uefilogging
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Module providing the :func:`get_uefi_logger` factory, the
               :data:`UEFI_LOGGING_CHANNELS` tuple of documented channel
               names, and a per-channel ``UEFI_*_CHANNEL`` constant for
               IDE autocomplete.  Re-exports :class:`LoggingPattern` from
               :mod:`automojo.protocols.uefi.uefiaspects` so that log
               related imports stay co-located at higher-layer call
               sites.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Automation Mojo LLC"
__credits__ = []


import logging
import os

from typing import Tuple

from automojo.errors.exceptions import ConfigurationError
from automojo.xmodules.xlogging.enhancedloggers import getEnhancedLogger

from automojo.protocols.uefi.uefiaspects import LoggingPattern


# ---------------------------------------------------------------------------
# Per-channel constants
# ---------------------------------------------------------------------------

UEFI_SHELL_CHANNEL: str = "uefi.shell"
UEFI_VARIABLE_CHANNEL: str = "uefi.variable"
UEFI_CAPSULE_CHANNEL: str = "uefi.capsule"
UEFI_TRANSPORT_CHANNEL: str = "uefi.transport"
UEFI_BOOTMGR_CHANNEL: str = "uefi.bootmgr"
UEFI_SECUREBOOT_CHANNEL: str = "uefi.secureboot"
UEFI_DEVICEPATH_CHANNEL: str = "uefi.devicepath"
UEFI_GPT_CHANNEL: str = "uefi.gpt"
UEFI_ESP_CHANNEL: str = "uefi.esp"
UEFI_RESET_CHANNEL: str = "uefi.reset"
UEFI_HII_CHANNEL: str = "uefi.hii"
UEFI_REDFISH_CHANNEL: str = "uefi.redfish"
UEFI_TPM_CHANNEL: str = "uefi.tpm"
UEFI_TIME_CHANNEL: str = "uefi.time"
UEFI_SYSTEMTABLES_CHANNEL: str = "uefi.systemtables"
UEFI_LOADEDIMAGE_CHANNEL: str = "uefi.loadedimage"
UEFI_MEASUREDBOOT_CHANNEL: str = "uefi.measuredboot"
UEFI_NETBOOT_CHANNEL: str = "uefi.netboot"


# ---------------------------------------------------------------------------
# Channel registry
# ---------------------------------------------------------------------------

UEFI_LOGGING_CHANNELS: Tuple[str, ...] = (
    UEFI_SHELL_CHANNEL,
    UEFI_VARIABLE_CHANNEL,
    UEFI_CAPSULE_CHANNEL,
    UEFI_TRANSPORT_CHANNEL,
    UEFI_BOOTMGR_CHANNEL,
    UEFI_SECUREBOOT_CHANNEL,
    UEFI_DEVICEPATH_CHANNEL,
    UEFI_GPT_CHANNEL,
    UEFI_ESP_CHANNEL,
    UEFI_RESET_CHANNEL,
    UEFI_HII_CHANNEL,
    UEFI_REDFISH_CHANNEL,
    UEFI_TPM_CHANNEL,
    UEFI_TIME_CHANNEL,
    UEFI_SYSTEMTABLES_CHANNEL,
    UEFI_LOADEDIMAGE_CHANNEL,
    UEFI_MEASUREDBOOT_CHANNEL,
    UEFI_NETBOOT_CHANNEL,
)


# ---------------------------------------------------------------------------
# get_uefi_logger
# ---------------------------------------------------------------------------

def get_uefi_logger(channel: str) -> logging.Logger:
    """
        Return an enhanced logger bound to the given UEFI channel.

        The ``channel`` argument must be a full ``uefi.*`` name that
        appears in :data:`UEFI_LOGGING_CHANNELS`.  Delegates to
        :func:`automojo.xmodules.xlogging.enhancedloggers.getEnhancedLogger`
        so every logger produced by this factory is of the same enhanced
        kind consumed throughout the ``automojo`` ecosystem.

        :param channel: The fully-qualified channel name (for example
                        ``"uefi.shell"``).

        :returns: A :class:`logging.Logger` compatible enhanced logger
                  whose ``name`` equals ``channel``.
        :raises: ConfigurationError
    """
    if not isinstance(channel, str):
        err_msg_lines = [
            "'channel' must be a str.",
            "GOT: value={!r} type={}".format(channel, type(channel).__name__),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise ConfigurationError(err_msg) from None

    if channel not in UEFI_LOGGING_CHANNELS:
        err_msg_lines = [
            "'channel' is not a known UEFI logging channel.",
            "GOT: value={!r}".format(channel),
            "ALLOWED:",
        ]
        for name in UEFI_LOGGING_CHANNELS:
            err_msg_lines.append("    {}".format(name))
        err_msg = os.linesep.join(err_msg_lines)
        raise ConfigurationError(err_msg) from None

    rtnval = getEnhancedLogger(channel)
    return rtnval


__all__ = [
    "LoggingPattern",
    "UEFI_LOGGING_CHANNELS",
    "UEFI_SHELL_CHANNEL",
    "UEFI_VARIABLE_CHANNEL",
    "UEFI_CAPSULE_CHANNEL",
    "UEFI_TRANSPORT_CHANNEL",
    "UEFI_BOOTMGR_CHANNEL",
    "UEFI_SECUREBOOT_CHANNEL",
    "UEFI_DEVICEPATH_CHANNEL",
    "UEFI_GPT_CHANNEL",
    "UEFI_ESP_CHANNEL",
    "UEFI_RESET_CHANNEL",
    "UEFI_HII_CHANNEL",
    "UEFI_REDFISH_CHANNEL",
    "UEFI_TPM_CHANNEL",
    "UEFI_TIME_CHANNEL",
    "UEFI_SYSTEMTABLES_CHANNEL",
    "UEFI_LOADEDIMAGE_CHANNEL",
    "UEFI_MEASUREDBOOT_CHANNEL",
    "UEFI_NETBOOT_CHANNEL",
    "get_uefi_logger",
]
