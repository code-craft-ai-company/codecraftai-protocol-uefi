"""
    .. module:: test_uefinetboot
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Unit tests for :mod:`ccai.protocols.uefi.uefinetboot`.

               Exercises:

               * HTTP and PXE device-path assembly with round-trip
                 encode / decode / text rendering.
               * IPv4 vs IPv6 node-type selection driven by
                 :attr:`HttpBootOption.prefer_ipv6` and
                 :attr:`PxeBootOption.prefer_ipv6`.
               * ``install_http_boot_option`` delegates to
                 :meth:`BootManager.set_boot_option` with the correct
                 index and a :class:`BootOption` whose ``file_path``
                 matches the built device path.
               * ``install_pxe_boot_option`` likewise.
               * ``boot_once`` delegates to :meth:`BootManager.set_boot_next`.
               * Validation: unsupported URI scheme, non-string URI,
                 wrong-length MAC.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Code Craft AI LLC"
__credits__ = []


from types import SimpleNamespace

from ccai import testbase

import logging

from typing import List, Optional, Tuple

from ccai.errors.exceptions import ConfigurationError

from ccai.protocols.uefi.uefiaspects import (
    DEFAULT_CMD_ASPECTS,
    UefiCmdAspects,
)
from ccai.protocols.uefi.uefibootmanager import (
    BootManager,
    BootOption,
    LoadOptionAttribute,
)
from ccai.protocols.uefi.uefidevicepath import (
    decode_device_path,
    device_path_from_text,
    device_path_to_text,
    encode_device_path,
)
from ccai.protocols.uefi.uefinetboot import (
    HttpBootOption,
    NetworkBootManager,
    PxeBootOption,
)


# ---------------------------------------------------------------------------
# Stub BootManager
# ---------------------------------------------------------------------------

class _StubBootManager(BootManager):
    """
        A :class:`BootManager` stub that records
        :meth:`set_boot_option` / :meth:`set_boot_next` calls without
        dispatching to a real :class:`VariableStore`.  Subclassing
        :class:`BootManager` preserves the ``isinstance`` contract used
        by :class:`NetworkBootManager`.
    """

    def __init__(self) -> None:
        """
            Bypass the base-class constructor so no VariableStore is
            required.  The attributes exercised by the tests are set
            directly.
        """
        # Intentionally do not call super().__init__ -- the fake records
        # calls but never speaks to a backend.
        self._aspects: UefiCmdAspects = DEFAULT_CMD_ASPECTS
        self._logger = logging.getLogger("uefi.bootmgr.test_stub")
        self.set_boot_option_calls: List[Tuple[int, BootOption, Optional[UefiCmdAspects]]] = []
        self.set_boot_next_calls: List[Tuple[int, Optional[UefiCmdAspects]]] = []
        return

    def set_boot_option(self, index: int, option: BootOption,
                              aspects: Optional[UefiCmdAspects] = None) -> None:
        """
            Record the ``(index, option, aspects)`` triple.
        """
        self.set_boot_option_calls.append((index, option, aspects))
        return

    def set_boot_next(self, index: int,
                            aspects: Optional[UefiCmdAspects] = None) -> None:
        """
            Record the ``(index, aspects)`` tuple.
        """
        self.set_boot_next_calls.append((index, aspects))
        return


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

_SAMPLE_MAC: bytes = bytes([0x00, 0x13, 0x20, 0xF5, 0xFA, 0x77])
_SAMPLE_HTTP_URI: str = "http://example.com/boot.efi"
_SAMPLE_HTTPS_URI: str = "https://secure.example.com/efi/bootx64.efi"


# ---------------------------------------------------------------------------
# HTTP Boot device-path tests
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# HttpBootDevicePathTests
# ----------------------------------------------------------------------

"""
    Verify :meth:`NetworkBootManager.build_http_boot_device_path`
    assembles a valid device path whose binary / text forms round-trip
    through the 5-device-path-codec.
"""

def _setup_http_boot_device_path(ctx) -> None:
    """
        Build a fresh stub manager per test.
    """
    ctx.stub_boot_mgr = _StubBootManager()
    ctx.netboot = NetworkBootManager(
        boot_manager=ctx.stub_boot_mgr, aspects=DEFAULT_CMD_ASPECTS,
    )
    return

def test_http_ipv4_roundtrip_text_form() -> None:
    """
        HTTP Boot device path with default IPv4 stack encodes, decodes,
        and renders to text carrying MAC, IPv4, and Uri entries.
    """
    ctx = SimpleNamespace()
    _setup_http_boot_device_path(ctx)
    opt = HttpBootOption(
        description="HTTP Boot",
        uri=_SAMPLE_HTTP_URI,
        mac_address=_SAMPLE_MAC,
        prefer_ipv6=False,
    )

    path = ctx.netboot.build_http_boot_device_path(option=opt)

    testbase.assert_equal(len(path.nodes), 3)

    encoded = encode_device_path(path=path)
    decoded = decode_device_path(data=encoded)
    testbase.assert_equal(decoded, path)

    text = device_path_to_text(path=decoded)
    testbase.assert_true("MAC(" in text)
    testbase.assert_true("IPv4(" in text)
    testbase.assert_true("Uri({})".format(_SAMPLE_HTTP_URI) in text)

    reparsed = device_path_from_text(text=text)
    reencoded = encode_device_path(path=reparsed)
    testbase.assert_equal(reencoded, encoded)
    return

def test_http_ipv6_roundtrip_text_form() -> None:
    """
        HTTP Boot device path with ``prefer_ipv6=True`` emits an IPv6
        node instead of an IPv4 node.
    """
    ctx = SimpleNamespace()
    _setup_http_boot_device_path(ctx)
    opt = HttpBootOption(
        description="HTTP IPv6 Boot",
        uri=_SAMPLE_HTTPS_URI,
        mac_address=_SAMPLE_MAC,
        prefer_ipv6=True,
    )

    path = ctx.netboot.build_http_boot_device_path(option=opt)

    encoded = encode_device_path(path=path)
    decoded = decode_device_path(data=encoded)
    testbase.assert_equal(decoded, path)

    text = device_path_to_text(path=decoded)
    testbase.assert_true("MAC(" in text)
    testbase.assert_true("IPv6(" in text)
    testbase.assert_true("IPv4(" not in text)
    testbase.assert_true("Uri({})".format(_SAMPLE_HTTPS_URI) in text)
    return

def test_http_without_mac_emits_zero_mac_node() -> None:
    """
        When no MAC is supplied the builder emits a MAC node with a
        zero MAC so firmware picks its default NIC.
    """
    ctx = SimpleNamespace()
    _setup_http_boot_device_path(ctx)
    opt = HttpBootOption(
        description="HTTP Boot Default NIC",
        uri=_SAMPLE_HTTP_URI,
        mac_address=None,
    )

    path = ctx.netboot.build_http_boot_device_path(option=opt)

    # Node 0 is the MAC node; its first 6 bytes are the MAC.
    mac_node = path.nodes[0]
    testbase.assert_equal(mac_node.type, 0x03)
    testbase.assert_equal(mac_node.subtype, 0x0B)

    observed_mac = mac_node.data[0:6]
    testbase.assert_equal(observed_mac, b"\x00" * 6)
    return

def test_http_non_string_uri_raises() -> None:
    """
        Non-string URIs are rejected with ``ConfigurationError``.
    """
    ctx = SimpleNamespace()
    _setup_http_boot_device_path(ctx)
    opt = HttpBootOption(
        description="Bad",
        uri=12345,  # type: ignore[arg-type]
    )

    with testbase.raises(ConfigurationError):
        _ = ctx.netboot.build_http_boot_device_path(option=opt)

    return

def test_http_unsupported_scheme_raises() -> None:
    """
        Schemes other than http/https are rejected with
        ``ConfigurationError``.
    """
    ctx = SimpleNamespace()
    _setup_http_boot_device_path(ctx)
    opt = HttpBootOption(
        description="Bad scheme",
        uri="tftp://example.com/boot.efi",
    )

    with testbase.raises(ConfigurationError):
        _ = ctx.netboot.build_http_boot_device_path(option=opt)

    return

def test_http_short_mac_raises() -> None:
    """
        A MAC that is not exactly 6 bytes is rejected with
        ``ConfigurationError``.
    """
    ctx = SimpleNamespace()
    _setup_http_boot_device_path(ctx)
    opt = HttpBootOption(
        description="Short MAC",
        uri=_SAMPLE_HTTP_URI,
        mac_address=b"\x01\x02\x03",
    )

    with testbase.raises(ConfigurationError):
        _ = ctx.netboot.build_http_boot_device_path(option=opt)

    return


# ---------------------------------------------------------------------------
# PXE Boot device-path tests
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# PxeBootDevicePathTests
# ----------------------------------------------------------------------

"""
    Verify :meth:`NetworkBootManager.build_pxe_boot_device_path`
    assembles a valid device path.
"""

def _setup_pxe_boot_device_path(ctx) -> None:
    ctx.stub_boot_mgr = _StubBootManager()
    ctx.netboot = NetworkBootManager(
        boot_manager=ctx.stub_boot_mgr, aspects=DEFAULT_CMD_ASPECTS,
    )
    return

def test_pxe_ipv4_roundtrip_text_form() -> None:
    """
        PXE IPv4 device path encodes, decodes, and renders to text.
    """
    ctx = SimpleNamespace()
    _setup_pxe_boot_device_path(ctx)
    opt = PxeBootOption(
        description="PXE IPv4",
        mac_address=_SAMPLE_MAC,
        prefer_ipv6=False,
    )

    path = ctx.netboot.build_pxe_boot_device_path(option=opt)

    testbase.assert_equal(len(path.nodes), 2)

    encoded = encode_device_path(path=path)
    decoded = decode_device_path(data=encoded)
    testbase.assert_equal(decoded, path)

    text = device_path_to_text(path=decoded)
    testbase.assert_true("MAC(" in text)
    testbase.assert_true("IPv4(" in text)

    reparsed = device_path_from_text(text=text)
    reencoded = encode_device_path(path=reparsed)
    testbase.assert_equal(reencoded, encoded)
    return

def test_pxe_ipv6_node_selection() -> None:
    """
        PXE device path with ``prefer_ipv6=True`` emits an IPv6 node.
    """
    ctx = SimpleNamespace()
    _setup_pxe_boot_device_path(ctx)
    opt = PxeBootOption(
        description="PXE IPv6",
        mac_address=_SAMPLE_MAC,
        prefer_ipv6=True,
    )

    path = ctx.netboot.build_pxe_boot_device_path(option=opt)

    text = device_path_to_text(path=path)
    testbase.assert_true("MAC(" in text)
    testbase.assert_true("IPv6(" in text)
    testbase.assert_true("IPv4(" not in text)
    return

def test_pxe_short_mac_raises() -> None:
    """
        PXE builder rejects a MAC that is not exactly 6 bytes.
    """
    ctx = SimpleNamespace()
    _setup_pxe_boot_device_path(ctx)
    opt = PxeBootOption(
        description="PXE short MAC",
        mac_address=b"\x01\x02\x03\x04\x05",
    )

    with testbase.raises(ConfigurationError):
        _ = ctx.netboot.build_pxe_boot_device_path(option=opt)

    return


# ---------------------------------------------------------------------------
# Installation tests
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# InstallHttpBootOptionTests
# ----------------------------------------------------------------------

"""
    Verify :meth:`NetworkBootManager.install_http_boot_option`
    delegates to :meth:`BootManager.set_boot_option` with the correct
    index and a :class:`BootOption` whose ``file_path`` matches the
    built device path.
"""

def _setup_install_http_boot_option(ctx) -> None:
    ctx.stub_boot_mgr = _StubBootManager()
    ctx.netboot = NetworkBootManager(
        boot_manager=ctx.stub_boot_mgr, aspects=DEFAULT_CMD_ASPECTS,
    )
    return

def test_install_http_option_delegates_with_matching_path() -> None:
    """
        Installing an HTTP boot option invokes
        :meth:`BootManager.set_boot_option` once with a
        :class:`BootOption` whose ``file_path`` matches the output of
        :meth:`NetworkBootManager.build_http_boot_device_path`.
    """
    ctx = SimpleNamespace()
    _setup_install_http_boot_option(ctx)
    opt = HttpBootOption(
        description="My HTTP Boot",
        uri=_SAMPLE_HTTP_URI,
        mac_address=_SAMPLE_MAC,
    )

    expected_path = ctx.netboot.build_http_boot_device_path(option=opt)

    ctx.netboot.install_http_boot_option(index=0x0005, option=opt)

    testbase.assert_equal(len(ctx.stub_boot_mgr.set_boot_option_calls), 1)
    index, boot_option, aspects = ctx.stub_boot_mgr.set_boot_option_calls[0]
    testbase.assert_equal(index, 0x0005)
    testbase.assert_equal(boot_option.description, "My HTTP Boot")
    testbase.assert_equal(boot_option.file_path, expected_path)
    testbase.assert_equal(
        int(boot_option.attributes) & int(LoadOptionAttribute.LOAD_OPTION_ACTIVE),
        int(LoadOptionAttribute.LOAD_OPTION_ACTIVE),
    )
    testbase.assert_equal(boot_option.optional_data, b"")
    testbase.assert_true(aspects is not None)
    return

def test_install_pxe_option_delegates_with_matching_path() -> None:
    """
        Installing a PXE boot option invokes
        :meth:`BootManager.set_boot_option` once with a
        :class:`BootOption` whose ``file_path`` matches the output of
        :meth:`NetworkBootManager.build_pxe_boot_device_path`.
    """
    ctx = SimpleNamespace()
    _setup_install_http_boot_option(ctx)
    opt = PxeBootOption(
        description="My PXE Boot",
        mac_address=_SAMPLE_MAC,
        prefer_ipv6=False,
    )

    expected_path = ctx.netboot.build_pxe_boot_device_path(option=opt)

    ctx.netboot.install_pxe_boot_option(index=0x0009, option=opt)

    testbase.assert_equal(len(ctx.stub_boot_mgr.set_boot_option_calls), 1)
    index, boot_option, aspects = ctx.stub_boot_mgr.set_boot_option_calls[0]
    testbase.assert_equal(index, 0x0009)
    testbase.assert_equal(boot_option.description, "My PXE Boot")
    testbase.assert_equal(boot_option.file_path, expected_path)
    testbase.assert_equal(
        int(boot_option.attributes) & int(LoadOptionAttribute.LOAD_OPTION_ACTIVE),
        int(LoadOptionAttribute.LOAD_OPTION_ACTIVE),
    )
    testbase.assert_true(aspects is not None)
    return


# ---------------------------------------------------------------------------
# boot_once delegation
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# BootOnceTests
# ----------------------------------------------------------------------

"""
    Verify :meth:`NetworkBootManager.boot_once` delegates to
    :meth:`BootManager.set_boot_next`.
"""

def _setup_boot_once(ctx) -> None:
    ctx.stub_boot_mgr = _StubBootManager()
    ctx.netboot = NetworkBootManager(
        boot_manager=ctx.stub_boot_mgr, aspects=DEFAULT_CMD_ASPECTS,
    )
    return

def test_boot_once_delegates_to_set_boot_next() -> None:
    """
        Calling :meth:`boot_once` writes ``BootNext`` via
        :meth:`BootManager.set_boot_next` with the same index.
    """
    ctx = SimpleNamespace()
    _setup_boot_once(ctx)
    ctx.netboot.boot_once(index=0x0042)

    testbase.assert_equal(len(ctx.stub_boot_mgr.set_boot_next_calls), 1)
    index, aspects = ctx.stub_boot_mgr.set_boot_next_calls[0]
    testbase.assert_equal(index, 0x0042)
    testbase.assert_true(aspects is not None)
    return

def test_boot_once_passes_custom_aspects() -> None:
    """
        Per-call aspects override the instance default and reach the
        underlying :class:`BootManager`.
    """
    ctx = SimpleNamespace()
    _setup_boot_once(ctx)
    custom_aspects = UefiCmdAspects()

    ctx.netboot.boot_once(index=0x0007, aspects=custom_aspects)

    testbase.assert_equal(len(ctx.stub_boot_mgr.set_boot_next_calls), 1)
    index, aspects = ctx.stub_boot_mgr.set_boot_next_calls[0]
    testbase.assert_equal(index, 0x0007)
    testbase.assert_true(aspects is custom_aspects)
    return


# ---------------------------------------------------------------------------
# Constructor validation
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# NetworkBootManagerInitTests
# ----------------------------------------------------------------------

"""
    Verify the :class:`NetworkBootManager` constructor enforces the
    :class:`BootManager` type.
"""

def test_requires_boot_manager_instance() -> None:
    """
        Non-:class:`BootManager` values are rejected with
        ``ConfigurationError``.
    """
    with testbase.raises(ConfigurationError):
        _ = NetworkBootManager(boot_manager="not a boot manager")  # type: ignore[arg-type]

    return
