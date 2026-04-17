"""
    .. module:: test_uefinetboot
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Unit tests for :mod:`automojo.protocols.uefi.uefinetboot`.

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
__copyright__ = "Copyright 2025, Automation Mojo LLC"
__credits__ = []


import logging
import unittest

from typing import List, Optional, Tuple

from automojo.errors.exceptions import ConfigurationError

from automojo.protocols.uefi.uefiaspects import (
    DEFAULT_CMD_ASPECTS,
    UefiCmdAspects,
)
from automojo.protocols.uefi.uefibootmanager import (
    BootManager,
    BootOption,
    LoadOptionAttribute,
)
from automojo.protocols.uefi.uefidevicepath import (
    DevicePath,
    DevicePathNode,
    decode_device_path,
    device_path_from_text,
    device_path_to_text,
    encode_device_path,
)
from automojo.protocols.uefi.uefinetboot import (
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

class HttpBootDevicePathTests(unittest.TestCase):
    """
        Verify :meth:`NetworkBootManager.build_http_boot_device_path`
        assembles a valid device path whose binary / text forms round-trip
        through the 5-device-path-codec.
    """

    def setUp(self) -> None:
        """
            Build a fresh stub manager per test.
        """
        self.stub_boot_mgr = _StubBootManager()
        self.netboot = NetworkBootManager(
            boot_manager=self.stub_boot_mgr, aspects=DEFAULT_CMD_ASPECTS,
        )
        return

    def test_http_ipv4_roundtrip_text_form(self) -> None:
        """
            HTTP Boot device path with default IPv4 stack encodes, decodes,
            and renders to text carrying MAC, IPv4, and Uri entries.
        """
        opt = HttpBootOption(
            description="HTTP Boot",
            uri=_SAMPLE_HTTP_URI,
            mac_address=_SAMPLE_MAC,
            prefer_ipv6=False,
        )

        path = self.netboot.build_http_boot_device_path(option=opt)

        self.assertEqual(len(path.nodes), 3)

        encoded = encode_device_path(path=path)
        decoded = decode_device_path(data=encoded)
        self.assertEqual(decoded, path)

        text = device_path_to_text(path=decoded)
        self.assertIn("MAC(", text)
        self.assertIn("IPv4(", text)
        self.assertIn("Uri({})".format(_SAMPLE_HTTP_URI), text)

        reparsed = device_path_from_text(text=text)
        reencoded = encode_device_path(path=reparsed)
        self.assertEqual(reencoded, encoded)
        return

    def test_http_ipv6_roundtrip_text_form(self) -> None:
        """
            HTTP Boot device path with ``prefer_ipv6=True`` emits an IPv6
            node instead of an IPv4 node.
        """
        opt = HttpBootOption(
            description="HTTP IPv6 Boot",
            uri=_SAMPLE_HTTPS_URI,
            mac_address=_SAMPLE_MAC,
            prefer_ipv6=True,
        )

        path = self.netboot.build_http_boot_device_path(option=opt)

        encoded = encode_device_path(path=path)
        decoded = decode_device_path(data=encoded)
        self.assertEqual(decoded, path)

        text = device_path_to_text(path=decoded)
        self.assertIn("MAC(", text)
        self.assertIn("IPv6(", text)
        self.assertNotIn("IPv4(", text)
        self.assertIn("Uri({})".format(_SAMPLE_HTTPS_URI), text)
        return

    def test_http_without_mac_emits_zero_mac_node(self) -> None:
        """
            When no MAC is supplied the builder emits a MAC node with a
            zero MAC so firmware picks its default NIC.
        """
        opt = HttpBootOption(
            description="HTTP Boot Default NIC",
            uri=_SAMPLE_HTTP_URI,
            mac_address=None,
        )

        path = self.netboot.build_http_boot_device_path(option=opt)

        # Node 0 is the MAC node; its first 6 bytes are the MAC.
        mac_node = path.nodes[0]
        self.assertEqual(mac_node.type, 0x03)
        self.assertEqual(mac_node.subtype, 0x0B)

        observed_mac = mac_node.data[0:6]
        self.assertEqual(observed_mac, b"\x00" * 6)
        return

    def test_http_non_string_uri_raises(self) -> None:
        """
            Non-string URIs are rejected with ``ConfigurationError``.
        """
        opt = HttpBootOption(
            description="Bad",
            uri=12345,  # type: ignore[arg-type]
        )

        with self.assertRaises(ConfigurationError):
            _ = self.netboot.build_http_boot_device_path(option=opt)

        return

    def test_http_unsupported_scheme_raises(self) -> None:
        """
            Schemes other than http/https are rejected with
            ``ConfigurationError``.
        """
        opt = HttpBootOption(
            description="Bad scheme",
            uri="tftp://example.com/boot.efi",
        )

        with self.assertRaises(ConfigurationError):
            _ = self.netboot.build_http_boot_device_path(option=opt)

        return

    def test_http_short_mac_raises(self) -> None:
        """
            A MAC that is not exactly 6 bytes is rejected with
            ``ConfigurationError``.
        """
        opt = HttpBootOption(
            description="Short MAC",
            uri=_SAMPLE_HTTP_URI,
            mac_address=b"\x01\x02\x03",
        )

        with self.assertRaises(ConfigurationError):
            _ = self.netboot.build_http_boot_device_path(option=opt)

        return


# ---------------------------------------------------------------------------
# PXE Boot device-path tests
# ---------------------------------------------------------------------------

class PxeBootDevicePathTests(unittest.TestCase):
    """
        Verify :meth:`NetworkBootManager.build_pxe_boot_device_path`
        assembles a valid device path.
    """

    def setUp(self) -> None:
        self.stub_boot_mgr = _StubBootManager()
        self.netboot = NetworkBootManager(
            boot_manager=self.stub_boot_mgr, aspects=DEFAULT_CMD_ASPECTS,
        )
        return

    def test_pxe_ipv4_roundtrip_text_form(self) -> None:
        """
            PXE IPv4 device path encodes, decodes, and renders to text.
        """
        opt = PxeBootOption(
            description="PXE IPv4",
            mac_address=_SAMPLE_MAC,
            prefer_ipv6=False,
        )

        path = self.netboot.build_pxe_boot_device_path(option=opt)

        self.assertEqual(len(path.nodes), 2)

        encoded = encode_device_path(path=path)
        decoded = decode_device_path(data=encoded)
        self.assertEqual(decoded, path)

        text = device_path_to_text(path=decoded)
        self.assertIn("MAC(", text)
        self.assertIn("IPv4(", text)

        reparsed = device_path_from_text(text=text)
        reencoded = encode_device_path(path=reparsed)
        self.assertEqual(reencoded, encoded)
        return

    def test_pxe_ipv6_node_selection(self) -> None:
        """
            PXE device path with ``prefer_ipv6=True`` emits an IPv6 node.
        """
        opt = PxeBootOption(
            description="PXE IPv6",
            mac_address=_SAMPLE_MAC,
            prefer_ipv6=True,
        )

        path = self.netboot.build_pxe_boot_device_path(option=opt)

        text = device_path_to_text(path=path)
        self.assertIn("MAC(", text)
        self.assertIn("IPv6(", text)
        self.assertNotIn("IPv4(", text)
        return

    def test_pxe_short_mac_raises(self) -> None:
        """
            PXE builder rejects a MAC that is not exactly 6 bytes.
        """
        opt = PxeBootOption(
            description="PXE short MAC",
            mac_address=b"\x01\x02\x03\x04\x05",
        )

        with self.assertRaises(ConfigurationError):
            _ = self.netboot.build_pxe_boot_device_path(option=opt)

        return


# ---------------------------------------------------------------------------
# Installation tests
# ---------------------------------------------------------------------------

class InstallHttpBootOptionTests(unittest.TestCase):
    """
        Verify :meth:`NetworkBootManager.install_http_boot_option`
        delegates to :meth:`BootManager.set_boot_option` with the correct
        index and a :class:`BootOption` whose ``file_path`` matches the
        built device path.
    """

    def setUp(self) -> None:
        self.stub_boot_mgr = _StubBootManager()
        self.netboot = NetworkBootManager(
            boot_manager=self.stub_boot_mgr, aspects=DEFAULT_CMD_ASPECTS,
        )
        return

    def test_install_http_option_delegates_with_matching_path(self) -> None:
        """
            Installing an HTTP boot option invokes
            :meth:`BootManager.set_boot_option` once with a
            :class:`BootOption` whose ``file_path`` matches the output of
            :meth:`NetworkBootManager.build_http_boot_device_path`.
        """
        opt = HttpBootOption(
            description="My HTTP Boot",
            uri=_SAMPLE_HTTP_URI,
            mac_address=_SAMPLE_MAC,
        )

        expected_path = self.netboot.build_http_boot_device_path(option=opt)

        self.netboot.install_http_boot_option(index=0x0005, option=opt)

        self.assertEqual(len(self.stub_boot_mgr.set_boot_option_calls), 1)
        index, boot_option, aspects = self.stub_boot_mgr.set_boot_option_calls[0]
        self.assertEqual(index, 0x0005)
        self.assertEqual(boot_option.description, "My HTTP Boot")
        self.assertEqual(boot_option.file_path, expected_path)
        self.assertEqual(
            int(boot_option.attributes) & int(LoadOptionAttribute.LOAD_OPTION_ACTIVE),
            int(LoadOptionAttribute.LOAD_OPTION_ACTIVE),
        )
        self.assertEqual(boot_option.optional_data, b"")
        self.assertIsNotNone(aspects)
        return

    def test_install_pxe_option_delegates_with_matching_path(self) -> None:
        """
            Installing a PXE boot option invokes
            :meth:`BootManager.set_boot_option` once with a
            :class:`BootOption` whose ``file_path`` matches the output of
            :meth:`NetworkBootManager.build_pxe_boot_device_path`.
        """
        opt = PxeBootOption(
            description="My PXE Boot",
            mac_address=_SAMPLE_MAC,
            prefer_ipv6=False,
        )

        expected_path = self.netboot.build_pxe_boot_device_path(option=opt)

        self.netboot.install_pxe_boot_option(index=0x0009, option=opt)

        self.assertEqual(len(self.stub_boot_mgr.set_boot_option_calls), 1)
        index, boot_option, aspects = self.stub_boot_mgr.set_boot_option_calls[0]
        self.assertEqual(index, 0x0009)
        self.assertEqual(boot_option.description, "My PXE Boot")
        self.assertEqual(boot_option.file_path, expected_path)
        self.assertEqual(
            int(boot_option.attributes) & int(LoadOptionAttribute.LOAD_OPTION_ACTIVE),
            int(LoadOptionAttribute.LOAD_OPTION_ACTIVE),
        )
        self.assertIsNotNone(aspects)
        return


# ---------------------------------------------------------------------------
# boot_once delegation
# ---------------------------------------------------------------------------

class BootOnceTests(unittest.TestCase):
    """
        Verify :meth:`NetworkBootManager.boot_once` delegates to
        :meth:`BootManager.set_boot_next`.
    """

    def setUp(self) -> None:
        self.stub_boot_mgr = _StubBootManager()
        self.netboot = NetworkBootManager(
            boot_manager=self.stub_boot_mgr, aspects=DEFAULT_CMD_ASPECTS,
        )
        return

    def test_boot_once_delegates_to_set_boot_next(self) -> None:
        """
            Calling :meth:`boot_once` writes ``BootNext`` via
            :meth:`BootManager.set_boot_next` with the same index.
        """
        self.netboot.boot_once(index=0x0042)

        self.assertEqual(len(self.stub_boot_mgr.set_boot_next_calls), 1)
        index, aspects = self.stub_boot_mgr.set_boot_next_calls[0]
        self.assertEqual(index, 0x0042)
        self.assertIsNotNone(aspects)
        return

    def test_boot_once_passes_custom_aspects(self) -> None:
        """
            Per-call aspects override the instance default and reach the
            underlying :class:`BootManager`.
        """
        custom_aspects = UefiCmdAspects()

        self.netboot.boot_once(index=0x0007, aspects=custom_aspects)

        self.assertEqual(len(self.stub_boot_mgr.set_boot_next_calls), 1)
        index, aspects = self.stub_boot_mgr.set_boot_next_calls[0]
        self.assertEqual(index, 0x0007)
        self.assertIs(aspects, custom_aspects)
        return


# ---------------------------------------------------------------------------
# Constructor validation
# ---------------------------------------------------------------------------

class NetworkBootManagerInitTests(unittest.TestCase):
    """
        Verify the :class:`NetworkBootManager` constructor enforces the
        :class:`BootManager` type.
    """

    def test_requires_boot_manager_instance(self) -> None:
        """
            Non-:class:`BootManager` values are rejected with
            ``ConfigurationError``.
        """
        with self.assertRaises(ConfigurationError):
            _ = NetworkBootManager(boot_manager="not a boot manager")  # type: ignore[arg-type]

        return


if __name__ == "__main__":
    unittest.main()
