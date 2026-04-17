"""
    .. module:: uefinetboot
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Network-boot facade that builds, installs, and triggers
               ``Boot####`` entries for UEFI HTTP Boot (UEFI section 24.7)
               and PXE Boot (UEFI section 24.3) over IPv4 and IPv6.  The
               module composes :mod:`automojo.protocols.uefi.uefidevicepath`
               (for MAC / IPv4 / IPv6 / URI device-path node
               construction) and :mod:`automojo.protocols.uefi.uefibootmanager`
               (for ``Boot####`` / ``BootNext`` variable writes) into a
               focused surface consumed by test authors and provisioning
               engineers.  All construction is pure-Python; I/O is
               delegated through :class:`BootManager`.  The module exposes
               :class:`HttpBootOption`, :class:`PxeBootOption`, and
               :class:`NetworkBootManager`.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Automation Mojo LLC"
__credits__ = []


import os

from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import urlparse

from automojo.errors.exceptions import ConfigurationError

from automojo.protocols.uefi.uefiaspects import (
    DEFAULT_CMD_ASPECTS,
    UefiCmdAspects,
    resolve_aspects,
)
from automojo.protocols.uefi.uefibootmanager import (
    BootManager,
    BootOption,
    LoadOptionAttribute,
)
from automojo.protocols.uefi.uefidevicepath import (
    DevicePath,
    DevicePathNode,
)
from automojo.protocols.uefi.uefilogging import (
    UEFI_NETBOOT_CHANNEL,
    get_uefi_logger,
)


__all__ = [
    "HttpBootOption",
    "NetworkBootManager",
    "PxeBootOption",
]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# UEFI Messaging device-path type.
_DP_TYPE_MESSAGING: int = 0x03

# UEFI Messaging subtype values.
_DP_SUBTYPE_MAC: int = 0x0B
_DP_SUBTYPE_IPV4: int = 0x0C
_DP_SUBTYPE_IPV6: int = 0x0D
_DP_SUBTYPE_URI: int = 0x18


# Payload lengths consumed / emitted by the 5-device-path-codec companion
# formatters / parsers.  The IPv4 node short form is 23 bytes:
# LocalIp(4) + RemoteIp(4) + LocalPort(2) + RemotePort(2) + Protocol(2) +
# StaticIpAddress(1) + GatewayIp(4) + SubnetMask(4).  The IPv6 node short
# form is 56 bytes: LocalIp(16) + RemoteIp(16) + LocalPort(2) + RemotePort(2)
# + Protocol(2) + IpAddressOrigin(1) + PrefixLength(1) + GatewayIp(16).
_MAC_PADDING_LEN: int = 32
_MAC_PAYLOAD_LEN: int = 33
_IPV4_PAYLOAD_LEN: int = 23
_IPV6_PAYLOAD_LEN: int = 56


# IANA protocol numbers used by HTTP/PXE boot nodes.
_PROTO_TCP: int = 6


# MAC length in bytes.
_MAC_LEN: int = 6


# IP address lengths.
_IPV4_LEN: int = 4
_IPV6_LEN: int = 16


# Default IfType for Ethernet when the caller does not specify one
# (RFC 1700 -- ``1 = Ethernet``).
_DEFAULT_IF_TYPE_ETHERNET: int = 0x01


# Accepted URI schemes for HTTP Boot.
_SUPPORTED_HTTP_SCHEMES: tuple = ("http", "https")


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HttpBootOption:
    """
        Frozen dataclass describing a UEFI HTTP Boot option prior to
        installation.  Mirrors the inputs needed by
        :meth:`NetworkBootManager.build_http_boot_device_path` and the
        subsequent :meth:`NetworkBootManager.install_http_boot_option`
        call.

        :ivar description: Human-readable option description (UCS-2 BMP).
        :ivar uri: The ``http://`` or ``https://`` URI to boot from.
        :ivar mac_address: Optional 6-byte MAC to bind the boot attempt to
                           a specific NIC.  When ``None`` a MAC node with
                           a zero MAC is emitted so firmware picks the
                           default interface.
        :ivar prefer_ipv6: When ``True`` emit an IPv6 node instead of an
                           IPv4 node in the assembled device path.
        :ivar interface_type: RFC 1700 interface type.  ``0`` selects the
                              module default (``0x01`` = Ethernet).
    """
    description: str
    uri: str
    mac_address: Optional[bytes] = None
    prefer_ipv6: bool = False
    interface_type: int = 0


@dataclass(frozen=True)
class PxeBootOption:
    """
        Frozen dataclass describing a UEFI PXE Boot option prior to
        installation.

        :ivar description: Human-readable option description (UCS-2 BMP).
        :ivar mac_address: Six-byte MAC of the NIC to PXE-boot from.
        :ivar prefer_ipv6: When ``True`` emit an IPv6 node instead of an
                           IPv4 node (PXE over IPv6).
    """
    description: str
    mac_address: bytes
    prefer_ipv6: bool = False


# ---------------------------------------------------------------------------
# Device-path node builders
# ---------------------------------------------------------------------------

def _validate_mac_address(mac_address: bytes, field_name: str) -> bytes:
    """
        Validate and normalise a MAC address to exactly six bytes.

        :param mac_address: The caller-supplied MAC.
        :param field_name: The field name used in error messages.

        :returns: The normalised six-byte MAC.
        :raises: ConfigurationError
    """
    if mac_address is None:
        err_msg_lines = [
            "MAC address must not be None.",
            "FIELD: {}".format(field_name),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise ConfigurationError(err_msg) from None

    if not isinstance(mac_address, (bytes, bytearray, memoryview)):
        err_msg_lines = [
            "MAC address must be a bytes-like object.",
            "FIELD: {}".format(field_name),
            "GOT: type={}".format(type(mac_address).__name__),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise ConfigurationError(err_msg) from None

    mac_bytes = bytes(mac_address)
    if len(mac_bytes) != _MAC_LEN:
        err_msg_lines = [
            "MAC address must be exactly 6 bytes.",
            "FIELD: {}".format(field_name),
            "OBSERVED: {} bytes".format(len(mac_bytes)),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise ConfigurationError(err_msg) from None

    rtnval = mac_bytes
    return rtnval


def _validate_uri(uri: str) -> str:
    """
        Validate that ``uri`` is a string with an HTTP or HTTPS scheme.

        :param uri: The URI to validate.

        :returns: The validated URI string (unchanged).
        :raises: ConfigurationError
    """
    if not isinstance(uri, str):
        err_msg_lines = [
            "HTTP Boot 'uri' must be a str.",
            "GOT: type={}".format(type(uri).__name__),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise ConfigurationError(err_msg) from None

    if len(uri) == 0:
        err_msg_lines = [
            "HTTP Boot 'uri' must not be empty.",
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise ConfigurationError(err_msg) from None

    parsed = urlparse(uri)
    scheme = parsed.scheme.lower()
    if scheme not in _SUPPORTED_HTTP_SCHEMES:
        err_msg_lines = [
            "HTTP Boot 'uri' has an unsupported scheme.",
            "OBSERVED: {!r}".format(parsed.scheme),
            "SUPPORTED: {}".format(", ".join(_SUPPORTED_HTTP_SCHEMES)),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise ConfigurationError(err_msg) from None

    rtnval = uri
    return rtnval


def _resolve_if_type(interface_type: int) -> int:
    """
        Resolve the RFC 1700 interface-type byte for the MAC node.

        :param interface_type: The caller-supplied value.  ``0`` selects
                               the module default (Ethernet).

        :returns: The byte to place in the MAC node's ``IfType`` field.
        :raises: ConfigurationError
    """
    if not isinstance(interface_type, int) or isinstance(interface_type, bool):
        err_msg_lines = [
            "interface_type must be an int.",
            "GOT: type={}".format(type(interface_type).__name__),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise ConfigurationError(err_msg) from None

    if interface_type < 0 or interface_type > 0xFF:
        err_msg_lines = [
            "interface_type must be in 0..255.",
            "OBSERVED: {}".format(interface_type),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise ConfigurationError(err_msg) from None

    if interface_type == 0:
        rtnval = _DEFAULT_IF_TYPE_ETHERNET
    else:
        rtnval = interface_type

    return rtnval


def _build_mac_node(mac_address: bytes, if_type: int) -> DevicePathNode:
    """
        Build a Messaging MAC-Address device-path node per UEFI section
        10.3.4.10.

        The on-wire payload is exactly 33 bytes: 6 bytes of MAC, 26 bytes
        of zero padding (32 total for the ``MacAddress`` field), and a
        single ``IfType`` byte.

        :param mac_address: Six-byte MAC address.  Validated by caller.
        :param if_type: RFC 1700 interface type byte.

        :returns: The constructed :class:`DevicePathNode`.
    """
    padding_len = _MAC_PADDING_LEN - len(mac_address)
    payload = mac_address + (b"\x00" * padding_len) + bytes([if_type & 0xFF])

    rtnval = DevicePathNode(
        type=_DP_TYPE_MESSAGING,
        subtype=_DP_SUBTYPE_MAC,
        data=payload,
    )
    return rtnval


def _build_ipv4_node(local_ip: bytes, remote_ip: bytes,
                          local_port: int, remote_port: int,
                          protocol: int, static_flag: int,
                          gateway_ip: bytes, subnet_mask: bytes) -> DevicePathNode:
    """
        Build a Messaging IPv4 device-path node per UEFI section 10.3.4.11.

        The on-wire payload is the 23-byte short form accepted by the
        5-device-path-codec IPv4 formatter.  Big-endian 4-byte IP fields
        and little-endian 2-byte port / protocol fields mirror the UEFI
        spec layout.

        :param local_ip: 4-byte big-endian local IPv4.
        :param remote_ip: 4-byte big-endian remote IPv4.
        :param local_port: UINT16 local TCP/UDP port.
        :param remote_port: UINT16 remote TCP/UDP port.
        :param protocol: IANA protocol number (UINT16).
        :param static_flag: 1 if local IP is statically assigned, else 0.
        :param gateway_ip: 4-byte big-endian gateway IP.
        :param subnet_mask: 4-byte big-endian subnet mask.

        :returns: The constructed :class:`DevicePathNode`.
    """
    payload = bytearray()
    payload.extend(local_ip)
    payload.extend(remote_ip)
    payload.extend(local_port.to_bytes(length=2, byteorder="little", signed=False))
    payload.extend(remote_port.to_bytes(length=2, byteorder="little", signed=False))
    payload.extend(protocol.to_bytes(length=2, byteorder="little", signed=False))
    payload.append(static_flag & 0xFF)
    payload.extend(gateway_ip)
    payload.extend(subnet_mask)

    payload_bytes = bytes(payload)
    if len(payload_bytes) != _IPV4_PAYLOAD_LEN:
        err_msg_lines = [
            "IPv4 node payload length mismatch.",
            "EXPECTED: {}".format(_IPV4_PAYLOAD_LEN),
            "OBSERVED: {}".format(len(payload_bytes)),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise ConfigurationError(err_msg) from None

    rtnval = DevicePathNode(
        type=_DP_TYPE_MESSAGING,
        subtype=_DP_SUBTYPE_IPV4,
        data=payload_bytes,
    )
    return rtnval


def _build_ipv6_node(local_ip: bytes, remote_ip: bytes,
                          local_port: int, remote_port: int,
                          protocol: int, address_origin: int,
                          prefix_length: int,
                          gateway_ip: bytes) -> DevicePathNode:
    """
        Build a Messaging IPv6 device-path node per UEFI section 10.3.4.10.2.

        The on-wire payload is 56 bytes: LocalIp(16) + RemoteIp(16) +
        LocalPort(2) + RemotePort(2) + Protocol(2) + IpAddressOrigin(1) +
        PrefixLength(1) + GatewayIp(16).

        :param local_ip: 16-byte IPv6 local address.
        :param remote_ip: 16-byte IPv6 remote address.
        :param local_port: UINT16 local TCP/UDP port.
        :param remote_port: UINT16 remote TCP/UDP port.
        :param protocol: IANA protocol number (UINT16).
        :param address_origin: 0 = manual, 1 = stateless, 2 = stateful.
        :param prefix_length: UINT8 prefix length in bits.
        :param gateway_ip: 16-byte IPv6 gateway.

        :returns: The constructed :class:`DevicePathNode`.
    """
    payload = bytearray()
    payload.extend(local_ip)
    payload.extend(remote_ip)
    payload.extend(local_port.to_bytes(length=2, byteorder="little", signed=False))
    payload.extend(remote_port.to_bytes(length=2, byteorder="little", signed=False))
    payload.extend(protocol.to_bytes(length=2, byteorder="little", signed=False))
    payload.append(address_origin & 0xFF)
    payload.append(prefix_length & 0xFF)
    payload.extend(gateway_ip)

    payload_bytes = bytes(payload)
    if len(payload_bytes) != _IPV6_PAYLOAD_LEN:
        err_msg_lines = [
            "IPv6 node payload length mismatch.",
            "EXPECTED: {}".format(_IPV6_PAYLOAD_LEN),
            "OBSERVED: {}".format(len(payload_bytes)),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise ConfigurationError(err_msg) from None

    rtnval = DevicePathNode(
        type=_DP_TYPE_MESSAGING,
        subtype=_DP_SUBTYPE_IPV6,
        data=payload_bytes,
    )
    return rtnval


def _build_uri_node(uri: str) -> DevicePathNode:
    """
        Build a Messaging URI device-path node per UEFI section 10.3.4.22.

        The payload is the UTF-8 encoding of the URI string with no null
        terminator.

        :param uri: The URI to encode.

        :returns: The constructed :class:`DevicePathNode`.
    """
    try:
        payload = uri.encode("utf-8")
    except UnicodeEncodeError as uerr:
        err_msg_lines = [
            "URI node payload failed UTF-8 encoding.",
            "INNER: {}".format(str(uerr)),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise ConfigurationError(err_msg) from uerr

    rtnval = DevicePathNode(
        type=_DP_TYPE_MESSAGING,
        subtype=_DP_SUBTYPE_URI,
        data=payload,
    )
    return rtnval


def _default_ipv4_node() -> DevicePathNode:
    """
        Build a fully-zero IPv4 node.  Suitable for HTTP / PXE boot paths
        where the caller wants the firmware DHCP client to fill in local
        and remote addresses at boot time.

        :returns: The constructed :class:`DevicePathNode`.
    """
    zero4 = b"\x00" * _IPV4_LEN
    rtnval = _build_ipv4_node(
        local_ip=zero4,
        remote_ip=zero4,
        local_port=0,
        remote_port=0,
        protocol=_PROTO_TCP,
        static_flag=0,
        gateway_ip=zero4,
        subnet_mask=zero4,
    )
    return rtnval


def _default_ipv6_node() -> DevicePathNode:
    """
        Build a fully-zero IPv6 node.  Suitable for HTTP / PXE boot paths
        where the caller wants the firmware DHCPv6 / RA stack to fill in
        local and remote addresses at boot time.

        :returns: The constructed :class:`DevicePathNode`.
    """
    zero16 = b"\x00" * _IPV6_LEN
    rtnval = _build_ipv6_node(
        local_ip=zero16,
        remote_ip=zero16,
        local_port=0,
        remote_port=0,
        protocol=_PROTO_TCP,
        address_origin=0,
        prefix_length=0,
        gateway_ip=zero16,
    )
    return rtnval


# ---------------------------------------------------------------------------
# NetworkBootManager
# ---------------------------------------------------------------------------

class NetworkBootManager:
    """
        Build and install network-boot ``Boot####`` entries and trigger a
        one-shot network boot via the ``BootNext`` global variable.

        The class is a thin composition on top of :class:`BootManager`: it
        owns no transport, it only assembles :class:`DevicePath` structures
        for HTTP Boot (UEFI section 24.7) and PXE Boot (UEFI section 24.3)
        and delegates the actual variable writes through :class:`BootManager`.

        :param boot_manager: The underlying :class:`BootManager` that
                             performs all variable I/O.
        :param aspects: Optional default :class:`UefiCmdAspects`.  When
                        not supplied :data:`DEFAULT_CMD_ASPECTS` is used.
    """

    def __init__(self, boot_manager: BootManager,
                       aspects: UefiCmdAspects = DEFAULT_CMD_ASPECTS) -> None:
        """
            Initialize the :class:`NetworkBootManager`.

            :param boot_manager: The underlying :class:`BootManager`.
            :param aspects: The default aspects for per-call resolution.

            :raises: ConfigurationError
        """
        if not isinstance(boot_manager, BootManager):
            err_msg_lines = [
                "NetworkBootManager: 'boot_manager' must be a BootManager.",
                "GOT: value={!r} type={}".format(
                    boot_manager, type(boot_manager).__name__,
                ),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg) from None

        self._boot_manager: BootManager = boot_manager
        self._aspects: UefiCmdAspects = aspects
        self._logger = get_uefi_logger(UEFI_NETBOOT_CHANNEL)
        return

    # ------------------------------------------------------------------
    # Device-path construction
    # ------------------------------------------------------------------

    def build_http_boot_device_path(self, option: HttpBootOption) -> DevicePath:
        """
            Assemble an HTTP Boot device path.

            The resulting path shape is
            ``MAC(<mac>,<iftype>)/IPv{4,6}(...)/Uri(<uri>)``.  When the
            caller omits ``mac_address`` a zero-MAC node is emitted so
            firmware binds to its default NIC.  The IPv4/IPv6 node is
            selected by ``option.prefer_ipv6``.  The End-Entire terminator
            is auto-appended by :func:`encode_device_path` and is not
            stored on the returned :class:`DevicePath`.

            :param option: The :class:`HttpBootOption` describing the
                           target.

            :returns: The assembled :class:`DevicePath`.
            :raises: ConfigurationError
        """
        if not isinstance(option, HttpBootOption):
            err_msg_lines = [
                "build_http_boot_device_path: 'option' must be an HttpBootOption.",
                "GOT: type={}".format(type(option).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg) from None

        validated_uri = _validate_uri(uri=option.uri)
        if_type_byte = _resolve_if_type(interface_type=option.interface_type)

        if option.mac_address is not None:
            mac_bytes = _validate_mac_address(
                mac_address=option.mac_address, field_name="HttpBootOption.mac_address",
            )
        else:
            mac_bytes = b"\x00" * _MAC_LEN

        mac_node = _build_mac_node(mac_address=mac_bytes, if_type=if_type_byte)

        if option.prefer_ipv6 is True:
            ip_node = _default_ipv6_node()
        else:
            ip_node = _default_ipv4_node()

        uri_node = _build_uri_node(uri=validated_uri)

        nodes: List[DevicePathNode] = [mac_node, ip_node, uri_node]
        device_path = DevicePath(nodes=tuple(nodes))

        self._logger.debug(
            "uefi.netboot: built HTTP device path description=%r uri=%r prefer_ipv6=%s",
            option.description, validated_uri, option.prefer_ipv6,
        )

        rtnval = device_path
        return rtnval

    def build_pxe_boot_device_path(self, option: PxeBootOption) -> DevicePath:
        """
            Assemble a PXE Boot device path.

            The resulting path shape is
            ``MAC(<mac>,<iftype>)/IPv{4,6}(...)``.  The IPv4/IPv6 node is
            selected by ``option.prefer_ipv6``.  The End-Entire terminator
            is auto-appended by :func:`encode_device_path`.

            :param option: The :class:`PxeBootOption` describing the target.

            :returns: The assembled :class:`DevicePath`.
            :raises: ConfigurationError
        """
        if not isinstance(option, PxeBootOption):
            err_msg_lines = [
                "build_pxe_boot_device_path: 'option' must be a PxeBootOption.",
                "GOT: type={}".format(type(option).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg) from None

        mac_bytes = _validate_mac_address(
            mac_address=option.mac_address, field_name="PxeBootOption.mac_address",
        )

        mac_node = _build_mac_node(
            mac_address=mac_bytes, if_type=_DEFAULT_IF_TYPE_ETHERNET,
        )

        if option.prefer_ipv6 is True:
            ip_node = _default_ipv6_node()
        else:
            ip_node = _default_ipv4_node()

        nodes: List[DevicePathNode] = [mac_node, ip_node]
        device_path = DevicePath(nodes=tuple(nodes))

        self._logger.debug(
            "uefi.netboot: built PXE device path description=%r prefer_ipv6=%s",
            option.description, option.prefer_ipv6,
        )

        rtnval = device_path
        return rtnval

    # ------------------------------------------------------------------
    # Installation
    # ------------------------------------------------------------------

    def install_http_boot_option(self, index: int,
                                       option: HttpBootOption,
                                       aspects: Optional[UefiCmdAspects] = None) -> None:
        """
            Install a ``Boot####`` variable backing an HTTP Boot option.

            The load option carries :data:`LoadOptionAttribute.LOAD_OPTION_ACTIVE`
            and an empty ``OptionalData`` region.  Installation is
            delegated to :meth:`BootManager.set_boot_option`.

            :param index: The ``Boot####`` slot index (``0x0000..0xFFFF``).
            :param option: The :class:`HttpBootOption` to install.
            :param aspects: Optional per-call aspects override.

            :raises: UefiVariableError, ConfigurationError, SemanticError
        """
        effective = resolve_aspects(passed=aspects, instance_default=self._aspects)

        device_path = self.build_http_boot_device_path(option=option)

        boot_option = BootOption(
            attributes=LoadOptionAttribute.LOAD_OPTION_ACTIVE,
            description=option.description,
            file_path=device_path,
            optional_data=b"",
        )

        self._logger.info(
            "uefi.netboot: install HTTP boot option index=0x%04X description=%r uri=%r",
            index, option.description, option.uri,
        )

        self._boot_manager.set_boot_option(
            index=index, option=boot_option, aspects=effective,
        )
        return

    def install_pxe_boot_option(self, index: int,
                                      option: PxeBootOption,
                                      aspects: Optional[UefiCmdAspects] = None) -> None:
        """
            Install a ``Boot####`` variable backing a PXE Boot option.

            The load option carries :data:`LoadOptionAttribute.LOAD_OPTION_ACTIVE`
            and an empty ``OptionalData`` region.  Installation is
            delegated to :meth:`BootManager.set_boot_option`.

            :param index: The ``Boot####`` slot index (``0x0000..0xFFFF``).
            :param option: The :class:`PxeBootOption` to install.
            :param aspects: Optional per-call aspects override.

            :raises: UefiVariableError, ConfigurationError, SemanticError
        """
        effective = resolve_aspects(passed=aspects, instance_default=self._aspects)

        device_path = self.build_pxe_boot_device_path(option=option)

        boot_option = BootOption(
            attributes=LoadOptionAttribute.LOAD_OPTION_ACTIVE,
            description=option.description,
            file_path=device_path,
            optional_data=b"",
        )

        self._logger.info(
            "uefi.netboot: install PXE boot option index=0x%04X description=%r prefer_ipv6=%s",
            index, option.description, option.prefer_ipv6,
        )

        self._boot_manager.set_boot_option(
            index=index, option=boot_option, aspects=effective,
        )
        return

    # ------------------------------------------------------------------
    # One-shot boot trigger
    # ------------------------------------------------------------------

    def boot_once(self, index: int,
                        aspects: Optional[UefiCmdAspects] = None) -> None:
        """
            Set the ``BootNext`` global variable to ``index`` so that
            firmware runs the corresponding ``Boot####`` exactly once on
            the next reset per UEFI section 3.3.  This call does not
            reboot the target; reboot is the caller's responsibility.

            :param index: The ``Boot####`` slot to run next.
            :param aspects: Optional per-call aspects override.

            :raises: UefiVariableError, ConfigurationError, SemanticError
        """
        effective = resolve_aspects(passed=aspects, instance_default=self._aspects)

        self._logger.info(
            "uefi.netboot: set BootNext index=0x%04X", index,
        )

        self._boot_manager.set_boot_next(index=index, aspects=effective)
        return
