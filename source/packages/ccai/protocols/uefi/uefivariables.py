"""
    .. module:: uefivariables
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Module providing backend-agnostic UEFI variable-services CRUD
               (``GetVariable``, ``SetVariable``, ``GetNextVariableName``,
               ``QueryVariableInfo``) across the three variable-capable
               transports supported by this library.  Exposes the
               :class:`VariableStore` facade that selects a backend based on
               ``agent.transport_kind`` (``serial`` / ``ipmi-sol`` ->
               :class:`_ShellVariableBackend`, ``ssh-host`` ->
               :class:`_HostOsVariableBackend`, ``redfish`` ->
               :class:`_RedfishVariableBackend`).  Authenticated writes are
               supported via the :func:`build_auth2_descriptor` helper that
               assembles an ``EFI_VARIABLE_AUTHENTICATION_2`` descriptor per
               UEFI Specification §8.2.6 using ``cryptography``'s PKCS#7
               signer.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Code Craft AI LLC"
__credits__ = []


import datetime
import enum
import fnmatch
import os
import re
import struct
import uuid

from dataclasses import dataclass
from typing import Any, Iterator, List, Optional, Tuple, Union

from ccai.errors.exceptions import ConfigurationError

from ccai.protocols.uefi.uefiaspects import (
    DEFAULT_CMD_ASPECTS,
    UefiCmdAspects,
    resolve_aspects,
)
from ccai.protocols.uefi.ueficonst import guid_to_bytes
from ccai.protocols.uefi.uefiexceptions import (
    UefiTransportError,
    UefiVariableError,
)
from ccai.protocols.uefi.uefihelpers import (
    datetime_to_efi_time,
    ucs2_encode,
)
from ccai.protocols.uefi.uefilogging import (
    UEFI_VARIABLE_CHANNEL,
    get_uefi_logger,
)


__all__ = [
    "EFI_BUFFER_TOO_SMALL",
    "EFI_CERT_TYPE_PKCS7_GUID",
    "EFI_NOT_FOUND",
    "EFI_SECURITY_VIOLATION",
    "EFI_SUCCESS",
    "EFI_UNSUPPORTED",
    "UefiVariable",
    "UefiVariableAttributes",
    "VariableStore",
    "WIN_CERT_TYPE_EFI_GUID",
    "build_auth2_descriptor",
]


# ---------------------------------------------------------------------------
# EFI status constants used by this module (UEFI §Appendix D).
# ---------------------------------------------------------------------------

EFI_SUCCESS: int = 0
EFI_UNSUPPORTED: int = 0x8000_0000_0000_0003
EFI_BUFFER_TOO_SMALL: int = 0x8000_0000_0000_0005
EFI_NOT_FOUND: int = 0x8000_0000_0000_000E
EFI_SECURITY_VIOLATION: int = 0x8000_0000_0000_001A


# ---------------------------------------------------------------------------
# EFI_CERT_TYPE_PKCS7_GUID (UEFI §32.4.1) and WIN_CERTIFICATE_UEFI_GUID
# wrapper constants (UEFI §32.4.3).
# ---------------------------------------------------------------------------

EFI_CERT_TYPE_PKCS7_GUID: uuid.UUID = uuid.UUID("4aafd29d-68df-49ee-8aa9-347d375665a7")

WIN_CERT_TYPE_EFI_GUID: int = 0x0EF1
WIN_CERT_REVISION: int = 0x0200


# ---------------------------------------------------------------------------
# UefiVariableAttributes
# ---------------------------------------------------------------------------

class UefiVariableAttributes(enum.IntFlag):
    """
        Bit-flag enumeration of the attributes carried by a UEFI variable per
        UEFI Specification §8.2.  Values match the on-wire bit positions.
    """
    NON_VOLATILE = 0x00000001
    BOOTSERVICE_ACCESS = 0x00000002
    RUNTIME_ACCESS = 0x00000004
    HARDWARE_ERROR_RECORD = 0x00000008
    AUTHENTICATED_WRITE_ACCESS = 0x00000010
    TIME_BASED_AUTHENTICATED_WRITE_ACCESS = 0x00000020
    APPEND_WRITE = 0x00000040
    ENHANCED_AUTHENTICATED_ACCESS = 0x00000080


def _validate_attributes(attributes: UefiVariableAttributes) -> None:
    """
        Validate cross-field rules between :class:`UefiVariableAttributes`
        flags per UEFI §8.2.

        :param attributes: The attribute bitfield to validate.

        :raises: ConfigurationError
    """
    value = int(attributes)

    runtime_bit = int(UefiVariableAttributes.RUNTIME_ACCESS)
    bootsvc_bit = int(UefiVariableAttributes.BOOTSERVICE_ACCESS)
    if (value & runtime_bit) != 0 and (value & bootsvc_bit) == 0:
        err_msg_lines = [
            "UefiVariableAttributes: RUNTIME_ACCESS requires BOOTSERVICE_ACCESS.",
            "OBSERVED: attributes=0x{:08X}".format(value),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise ConfigurationError(err_msg) from None

    auth_bit = int(UefiVariableAttributes.AUTHENTICATED_WRITE_ACCESS)
    tauth_bit = int(UefiVariableAttributes.TIME_BASED_AUTHENTICATED_WRITE_ACCESS)
    if (value & auth_bit) != 0 and (value & tauth_bit) != 0:
        err_msg_lines = [
            "UefiVariableAttributes: AUTHENTICATED_WRITE_ACCESS and TIME_BASED_AUTHENTICATED_WRITE_ACCESS are mutually exclusive.",
            "OBSERVED: attributes=0x{:08X}".format(value),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise ConfigurationError(err_msg) from None

    enh_bit = int(UefiVariableAttributes.ENHANCED_AUTHENTICATED_ACCESS)
    if (value & enh_bit) != 0 and (value & (auth_bit | tauth_bit)) != 0:
        err_msg_lines = [
            "UefiVariableAttributes: ENHANCED_AUTHENTICATED_ACCESS cannot combine with legacy auth-write flags.",
            "OBSERVED: attributes=0x{:08X}".format(value),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise ConfigurationError(err_msg) from None

    return


# ---------------------------------------------------------------------------
# UefiVariable record
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class UefiVariable:
    """
        Immutable record describing a single UEFI variable.

        :ivar name: The variable name (UCS-2 on the wire; ``str`` here).
        :ivar vendor_guid: The vendor-GUID namespace of the variable.
        :ivar attributes: The attribute bitfield.
        :ivar data: The variable payload bytes (excluding any auth descriptor).
    """
    name: str
    vendor_guid: uuid.UUID
    attributes: UefiVariableAttributes
    data: bytes


# ---------------------------------------------------------------------------
# vendor_guid coercion helper
# ---------------------------------------------------------------------------

def _coerce_vendor_guid(vendor_guid: Union[str, uuid.UUID]) -> uuid.UUID:
    """
        Coerce ``vendor_guid`` to a :class:`uuid.UUID` accepting either a
        string parseable by :class:`uuid.UUID` or an already-constructed
        :class:`uuid.UUID`.

        :param vendor_guid: The vendor GUID.

        :returns: A :class:`uuid.UUID` instance.
        :raises: ConfigurationError
    """
    if isinstance(vendor_guid, uuid.UUID):
        rtnval = vendor_guid
        return rtnval

    if isinstance(vendor_guid, str):
        try:
            rtnval = uuid.UUID(vendor_guid)
        except ValueError as verr:
            err_msg_lines = [
                "vendor_guid string is not parseable as a UUID.",
                "GOT: value={!r}".format(vendor_guid),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg) from verr
        return rtnval

    err_msg_lines = [
        "vendor_guid must be a uuid.UUID or a str.",
        "GOT: value={!r} type={}".format(vendor_guid, type(vendor_guid).__name__),
    ]
    err_msg = os.linesep.join(err_msg_lines)
    raise ConfigurationError(err_msg) from None


# ---------------------------------------------------------------------------
# Authenticated-variable descriptor builder (UEFI §8.2.6)
# ---------------------------------------------------------------------------

def build_auth2_descriptor(data: bytes,
                           time: datetime.datetime,
                           signer_key: Any,
                           signer_cert: Any) -> bytes:
    """
        Assemble an ``EFI_VARIABLE_AUTHENTICATION_2`` descriptor per UEFI
        Specification §8.2.6.

        The descriptor is the concatenation of:

        * 16 bytes of ``EFI_TIME`` for the timestamp;
        * 4 bytes ``dwLength`` (LE);
        * 2 bytes ``wRevision`` (``0x0200``, LE);
        * 2 bytes ``wCertificateType`` (``WIN_CERT_TYPE_EFI_GUID``, LE);
        * 16 bytes ``CertType`` (``EFI_CERT_TYPE_PKCS7_GUID``, mixed-endian);
        * DER-encoded PKCS#7 SignedData over ``data``.

        :param data: The payload to sign.  The caller is responsible for
                     building the signed-digest input per §8.2.6 when a
                     full variable write is performed; for a standalone
                     descriptor (the structural helper form) the signer
                     signs ``data`` directly.
        :param time: Timezone-aware :class:`datetime.datetime` used as the
                     ``EFI_TIME`` timestamp.
        :param signer_key: A ``cryptography`` private key object.
        :param signer_cert: A ``cryptography`` X.509 certificate binding
                            the public key.

        :returns: The descriptor bytes, ready to prepend to the wire
                  payload.
        :raises: ConfigurationError
    """
    # Lazy import so this module remains importable when the cryptography
    # dependency is not installed and the caller never touches the
    # authenticated-write path.
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.serialization import pkcs7
    except ImportError as ierr:
        err_msg_lines = [
            "build_auth2_descriptor requires the 'cryptography' package.",
            "HINT: install 'cryptography' to build authenticated-variable descriptors.",
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise ConfigurationError(err_msg) from ierr

    if not isinstance(data, (bytes, bytearray, memoryview)):
        err_msg_lines = [
            "'data' must be a bytes-like object.",
            "GOT: value={!r} type={}".format(data, type(data).__name__),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise ConfigurationError(err_msg) from None

    payload = bytes(data)

    # datetime_to_efi_time rejects naive datetimes with ConfigurationError.
    efi_time_bytes = datetime_to_efi_time(time)

    # Build the detached-signature PKCS#7 SignedData blob.  The UEFI
    # specification requires:
    #   * Detached signature (``PKCS7Options.DetachedSignature``);
    #   * No SMIME / MIME capabilities (``PKCS7Options.NoCapabilities``);
    #   * DER encoding.
    try:
        builder = pkcs7.PKCS7SignatureBuilder()
        builder = builder.set_data(payload)
        builder = builder.add_signer(signer_cert, signer_key, hashes.SHA256())
        signed_der = builder.sign(
            encoding=serialization.Encoding.DER,
            options=[
                pkcs7.PKCS7Options.DetachedSignature,
                pkcs7.PKCS7Options.NoCapabilities,
            ],
        )
    except BaseException as sigerr:
        err_msg_lines = [
            "build_auth2_descriptor: PKCS#7 signing raised an exception.",
            "EXCEPTION: {!r}".format(sigerr),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise ConfigurationError(err_msg) from sigerr

    cert_type_bytes = guid_to_bytes(EFI_CERT_TYPE_PKCS7_GUID)

    # WIN_CERTIFICATE.dwLength covers the header (8 bytes) + CertType (16)
    # + CertData (PKCS#7 DER).  This is the layout described in
    # UEFI §32.4.3 WIN_CERTIFICATE_UEFI_GUID.
    cert_data = signed_der
    header_len = 4 + 2 + 2  # dwLength + wRevision + wCertificateType
    cert_type_len = len(cert_type_bytes)
    auth_info_total_length = header_len + cert_type_len + len(cert_data)

    auth_info_bytes = (
        struct.pack("<I", auth_info_total_length)
        + struct.pack("<H", WIN_CERT_REVISION)
        + struct.pack("<H", WIN_CERT_TYPE_EFI_GUID)
        + cert_type_bytes
        + cert_data
    )

    rtnval = efi_time_bytes + auth_info_bytes
    return rtnval


# ---------------------------------------------------------------------------
# Backend base + shell helpers
# ---------------------------------------------------------------------------

class _BackendBase:
    """
        Common scaffolding for every variable-services backend.  Holds the
        :class:`UefiAgent` reference and the channel logger.  Subclasses
        implement the four CRUD operations.
    """

    def __init__(self, agent: Any) -> None:
        """
            Initialize the backend.

            :param agent: The :class:`UefiAgent` the backend drives.
        """
        self._agent: Any = agent
        self._logger = get_uefi_logger(UEFI_VARIABLE_CHANNEL)
        return

    @property
    def name(self) -> str:
        """
            Backend name; overridden by subclasses.

            :returns: A short identifying name.
        """
        rtnval = type(self).__name__
        return rtnval


def _attributes_to_shell_flags(attributes: UefiVariableAttributes) -> str:
    """
        Render a :class:`UefiVariableAttributes` bitfield as a space-joined
        string of UEFI-Shell ``setvar`` flag tokens.

        UEFI Shell's ``setvar`` understands the ``-bs`` (boot-service),
        ``-rt`` (runtime), and ``-nv`` (non-volatile) flags; the
        hardware-error-record, append-write, and authenticated-write flags
        are not addressable from the shell directly, so they are rendered
        as an ``-attr`` numeric suffix so the shell layer can pass them
        through unmodified.

        :param attributes: The bitfield.

        :returns: A single space-joined flag string.
    """
    parts: List[str] = []
    value = int(attributes)

    if (value & int(UefiVariableAttributes.NON_VOLATILE)) != 0:
        parts.append("-nv")
    if (value & int(UefiVariableAttributes.BOOTSERVICE_ACCESS)) != 0:
        parts.append("-bs")
    if (value & int(UefiVariableAttributes.RUNTIME_ACCESS)) != 0:
        parts.append("-rt")

    # Render any remaining bits explicitly so the shell layer can pass them
    # through.  This is a convention local to this backend.
    residual_mask = int(
        UefiVariableAttributes.HARDWARE_ERROR_RECORD
        | UefiVariableAttributes.AUTHENTICATED_WRITE_ACCESS
        | UefiVariableAttributes.TIME_BASED_AUTHENTICATED_WRITE_ACCESS
        | UefiVariableAttributes.APPEND_WRITE
        | UefiVariableAttributes.ENHANCED_AUTHENTICATED_ACCESS
    )
    residual = value & residual_mask
    if residual != 0:
        parts.append("-attr=0x{:08X}".format(value))

    rtnval = " ".join(parts)
    return rtnval


def _bytes_to_shell_hex(data: bytes) -> str:
    """
        Render a bytes buffer as UEFI Shell ``=0xNN 0xNN ...`` hex-payload
        syntax accepted by ``setvar``.

        :param data: The buffer to render.

        :returns: A space-joined hex string beginning with ``=`` when
                  ``data`` is non-empty, or the empty string when ``data``
                  is empty.
    """
    if len(data) == 0:
        rtnval = ""
        return rtnval

    tokens = ["0x{:02X}".format(b) for b in data]
    rtnval = "=" + " ".join(tokens)
    return rtnval


# Pattern that isolates a single variable record in a ``dmpstore`` listing.
# The shell's dmpstore output looks roughly like:
#
#   Variable NV+BS '8BE4DF61-93CA-11D2-AA0D-00E098032B8C:Boot0000' DataSize = 0x24
#     00000000: 09 01 00 00 ...  ...
#     00000010: 00 00 ...
#   ...
#
# The regex tolerates variable whitespace and 4- or 8-hex attribute prefix
# forms that some builds emit.

_DMPSTORE_HEADER_RE = re.compile(
    r"^\s*Variable\s+(?P<attr_tokens>[A-Z+]+)\s+"
    r"'(?P<guid>[0-9a-fA-F-]{36}):(?P<name>[^']+)'\s+"
    r"DataSize\s*=\s*0x(?P<size>[0-9a-fA-F]+)\s*$"
)

_DMPSTORE_DATA_RE = re.compile(
    r"^\s*[0-9a-fA-F]{8}:\s+(?P<hex>(?:[0-9a-fA-F]{2}\s*)+?)(?:\s{2,}.*)?$"
)

_DMPSTORE_ATTR_TOKEN_MAP = {
    "NV": UefiVariableAttributes.NON_VOLATILE,
    "BS": UefiVariableAttributes.BOOTSERVICE_ACCESS,
    "RT": UefiVariableAttributes.RUNTIME_ACCESS,
    "HR": UefiVariableAttributes.HARDWARE_ERROR_RECORD,
    "AW": UefiVariableAttributes.AUTHENTICATED_WRITE_ACCESS,
    "AT": UefiVariableAttributes.TIME_BASED_AUTHENTICATED_WRITE_ACCESS,
    "AP": UefiVariableAttributes.APPEND_WRITE,
    "EA": UefiVariableAttributes.ENHANCED_AUTHENTICATED_ACCESS,
}


def _parse_dmpstore_attr_tokens(tokens: str) -> UefiVariableAttributes:
    """
        Parse a ``dmpstore`` attribute-token string such as ``"NV+BS+RT"``
        into a :class:`UefiVariableAttributes` bitfield.

        :param tokens: The ``+``-joined token string.

        :returns: The decoded bitfield.
        :raises: UefiVariableError
    """
    value = 0
    for token in tokens.split("+"):
        trimmed = token.strip().upper()
        if trimmed == "":
            continue
        if trimmed not in _DMPSTORE_ATTR_TOKEN_MAP:
            err_msg_lines = [
                "dmpstore attribute token is not recognized.",
                "TOKEN: {!r}".format(trimmed),
                "ALLOWED: {}".format(sorted(_DMPSTORE_ATTR_TOKEN_MAP.keys())),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiVariableError(err_msg) from None
        value |= int(_DMPSTORE_ATTR_TOKEN_MAP[trimmed])

    rtnval = UefiVariableAttributes(value)
    return rtnval


def _parse_dmpstore_output(output: str) -> List[UefiVariable]:
    """
        Parse the textual output of a UEFI Shell ``dmpstore`` command into
        a list of :class:`UefiVariable` records.

        The parser tolerates minor whitespace variation but is otherwise
        strict about the header / hex-row layout.  Unknown lines are
        ignored so that shell banners or prompts do not destabilize the
        parse.

        :param output: The raw stdout of a ``dmpstore`` invocation.

        :returns: A list of :class:`UefiVariable`; empty when no
                  variables matched.
        :raises: UefiVariableError
    """
    records: List[UefiVariable] = []

    current_name: Optional[str] = None
    current_guid: Optional[uuid.UUID] = None
    current_attrs: Optional[UefiVariableAttributes] = None
    current_bytes: bytearray = bytearray()
    current_size: int = 0

    def _finalize() -> None:
        """
            Push the current in-progress record onto ``records`` if one
            exists.
        """
        if current_name is None:
            return
        if current_guid is None:
            return
        if current_attrs is None:
            return
        record = UefiVariable(
            name=current_name,
            vendor_guid=current_guid,
            attributes=current_attrs,
            data=bytes(current_bytes[:current_size]),
        )
        records.append(record)
        return

    for raw_line in output.splitlines():
        header_match = _DMPSTORE_HEADER_RE.match(raw_line)
        if header_match is not None:
            _finalize()

            attr_tokens = header_match.group("attr_tokens")
            guid_text = header_match.group("guid")
            name_text = header_match.group("name")
            size_hex = header_match.group("size")

            current_attrs = _parse_dmpstore_attr_tokens(attr_tokens)
            try:
                current_guid = uuid.UUID(guid_text)
            except ValueError as verr:
                err_msg_lines = [
                    "dmpstore output contained an invalid GUID.",
                    "OBSERVED: {!r}".format(guid_text),
                ]
                err_msg = os.linesep.join(err_msg_lines)
                raise UefiVariableError(err_msg) from verr

            current_name = name_text
            current_size = int(size_hex, 16)
            current_bytes = bytearray()
            continue

        data_match = _DMPSTORE_DATA_RE.match(raw_line)
        if data_match is not None and current_name is not None:
            hex_text = data_match.group("hex")
            hex_tokens = hex_text.split()
            for token in hex_tokens:
                current_bytes.append(int(token, 16))
            continue

    _finalize()

    return records


# ---------------------------------------------------------------------------
# Shell backend (serial, ipmi-sol)
# ---------------------------------------------------------------------------

class _ShellVariableBackend(_BackendBase):
    """
        Variable-services backend that drives the UEFI Shell through the
        host :class:`UefiAgent`.  Uses ``setvar`` / ``dmpstore`` wrappers
        inlined here because the feature must remain buildable even when
        the 5-shell-commands module is not yet checked in.
    """

    @property
    def name(self) -> str:
        """
            Short identifier for log output.

            :returns: ``"shell"``.
        """
        rtnval = "shell"
        return rtnval

    def get_variable(self, name: str,
                           vendor_guid: uuid.UUID,
                           aspects: UefiCmdAspects) -> UefiVariable:
        """
            Read a variable via ``dmpstore -guid <guid> <name>``.

            :param name: The variable name.
            :param vendor_guid: The vendor GUID.
            :param aspects: The effective aspects for the call.

            :returns: The decoded :class:`UefiVariable`.
            :raises: UefiVariableError
        """
        command = "dmpstore -guid {guid} {name}".format(
            guid=str(vendor_guid), name=name,
        )
        status, stdout, stderr = self._agent.run_cmd(
            command=command, aspects=aspects,
        )

        if status != 0:
            err_msg_lines = [
                "Shell dmpstore returned a non-zero status.",
                "COMMAND: {}".format(command),
                "STATUS: {}".format(status),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiVariableError(
                err_msg,
                name=name,
                vendor_guid=vendor_guid,
                efi_status=EFI_NOT_FOUND,
            )

        parsed = _parse_dmpstore_output(stdout if stdout is not None else "")
        if len(parsed) == 0:
            err_msg_lines = [
                "Shell dmpstore returned no records for the requested variable.",
                "COMMAND: {}".format(command),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiVariableError(
                err_msg,
                name=name,
                vendor_guid=vendor_guid,
                efi_status=EFI_NOT_FOUND,
            )

        rtnval = parsed[0]
        return rtnval

    def set_variable(self, variable: UefiVariable,
                           aspects: UefiCmdAspects) -> None:
        """
            Write a variable via ``setvar``.

            :param variable: The :class:`UefiVariable` describing the write.
            :param aspects: The effective aspects for the call.

            :raises: UefiVariableError
        """
        flags = _attributes_to_shell_flags(variable.attributes)
        hex_payload = _bytes_to_shell_hex(variable.data)

        command_parts = [
            "setvar",
            variable.name,
            "-guid",
            str(variable.vendor_guid),
        ]
        if flags != "":
            command_parts.append(flags)
        if hex_payload != "":
            command_parts.append(hex_payload)

        command = " ".join(command_parts)
        status, stdout, stderr = self._agent.run_cmd(
            command=command, aspects=aspects,
        )

        if status != 0:
            err_msg_lines = [
                "Shell setvar returned a non-zero status.",
                "COMMAND: {}".format(command),
                "STATUS: {}".format(status),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiVariableError(
                err_msg,
                name=variable.name,
                vendor_guid=variable.vendor_guid,
                attributes=int(variable.attributes),
                efi_status=int(status),
            )

        return

    def enumerate_variables(self, aspects: UefiCmdAspects) -> Iterator[Tuple[str, uuid.UUID]]:
        """
            Stream every ``(name, vendor_guid)`` tuple exposed by the
            firmware via ``dmpstore``.  Implemented as a generator so the
            caller may ``break`` early without paying the cost of eagerly
            materializing the listing.

            :param aspects: The effective aspects for the call.

            :returns: An iterator of ``(name, vendor_guid)`` tuples.
            :raises: UefiVariableError
        """
        command = "dmpstore"
        status, stdout, stderr = self._agent.run_cmd(
            command=command, aspects=aspects,
        )

        if status != 0:
            err_msg_lines = [
                "Shell dmpstore returned a non-zero status during enumeration.",
                "COMMAND: {}".format(command),
                "STATUS: {}".format(status),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiVariableError(err_msg, efi_status=int(status))

        parsed = _parse_dmpstore_output(stdout if stdout is not None else "")
        # Sort deterministically by (guid, name) per the design contract.
        sorted_records = sorted(parsed, key=lambda rec: (str(rec.vendor_guid), rec.name))

        for record in sorted_records:
            yield (record.name, record.vendor_guid)

        return

    def query_variable_info(self, attributes: UefiVariableAttributes,
                                  aspects: UefiCmdAspects) -> Tuple[int, int, int]:
        """
            The UEFI Shell does not expose ``QueryVariableInfo`` in a
            machine-readable form.  The operation is refused explicitly.

            :param attributes: The attribute bitfield being queried.
            :param aspects: The effective aspects for the call.

            :returns: Never returns.
            :raises: UefiTransportError
        """
        err_msg_lines = [
            "UEFI Shell does not expose QueryVariableInfo in a machine-readable form.",
            "BACKEND: shell",
            "OPERATION: query_variable_info",
            "HINT: use the Redfish or host-OS backend to query storage statistics.",
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiTransportError(err_msg) from None


# ---------------------------------------------------------------------------
# Host-OS backend (ssh-host via /sys/firmware/efi/efivars)
# ---------------------------------------------------------------------------

class _HostOsVariableBackend(_BackendBase):
    """
        Variable-services backend that reads
        ``/sys/firmware/efi/efivars/<Name>-<GUID>`` on a running Linux
        target.  Writes go through ``efivar -w`` when available and fall
        back to ``printf | dd`` for raw binary payloads; the immutable
        attribute is cleared with ``chattr -i`` around the write and
        re-applied on close.
    """

    @property
    def name(self) -> str:
        """
            Short identifier for log output.

            :returns: ``"host-os"``.
        """
        rtnval = "host-os"
        return rtnval

    @staticmethod
    def _efivarfs_path(name: str, vendor_guid: uuid.UUID) -> str:
        """
            Render the efivarfs path for a variable.

            :param name: The variable name.
            :param vendor_guid: The vendor GUID.

            :returns: The absolute efivarfs path.
        """
        rtnval = "/sys/firmware/efi/efivars/{}-{}".format(name, str(vendor_guid))
        return rtnval

    def get_variable(self, name: str,
                           vendor_guid: uuid.UUID,
                           aspects: UefiCmdAspects) -> UefiVariable:
        """
            Read a variable by catting its efivarfs entry and splitting the
            first four bytes (the little-endian attribute prefix) off the
            payload.

            :param name: The variable name.
            :param vendor_guid: The vendor GUID.
            :param aspects: The effective aspects for the call.

            :returns: The decoded :class:`UefiVariable`.
            :raises: UefiVariableError
        """
        path = self._efivarfs_path(name=name, vendor_guid=vendor_guid)
        # cat into a hex-safe transport: xxd -p -c0 prints the whole file
        # as a single lowercase hex string.
        command = "xxd -p -c0 {}".format(path)
        status, stdout, stderr = self._agent.run_cmd(
            command=command, aspects=aspects,
        )

        if status != 0:
            err_msg_lines = [
                "Host OS read of efivarfs entry failed.",
                "COMMAND: {}".format(command),
                "STATUS: {}".format(status),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiVariableError(
                err_msg,
                name=name,
                vendor_guid=vendor_guid,
                efi_status=EFI_NOT_FOUND,
            )

        hex_text = (stdout if stdout is not None else "").strip().replace("\n", "").replace("\r", "")
        try:
            raw = bytes.fromhex(hex_text)
        except ValueError as verr:
            err_msg_lines = [
                "Host OS efivarfs read did not return valid hex output.",
                "OBSERVED_PREFIX: {!r}".format(hex_text[:64]),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiVariableError(
                err_msg,
                name=name,
                vendor_guid=vendor_guid,
                efi_status=EFI_NOT_FOUND,
            ) from verr

        if len(raw) < 4:
            err_msg_lines = [
                "Host OS efivarfs entry is shorter than the 4-byte attribute prefix.",
                "OBSERVED LENGTH: {}".format(len(raw)),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiVariableError(
                err_msg,
                name=name,
                vendor_guid=vendor_guid,
                efi_status=EFI_BUFFER_TOO_SMALL,
            )

        attr_value = int.from_bytes(raw[:4], byteorder="little", signed=False)
        data_bytes = raw[4:]

        rtnval = UefiVariable(
            name=name,
            vendor_guid=vendor_guid,
            attributes=UefiVariableAttributes(attr_value),
            data=data_bytes,
        )
        return rtnval

    def set_variable(self, variable: UefiVariable,
                           aspects: UefiCmdAspects) -> None:
        """
            Write a variable by concatenating the 4-byte attribute prefix
            with the payload bytes and piping the result into ``dd`` with
            ``chattr -i`` / ``chattr +i`` bracketing.

            :param variable: The :class:`UefiVariable` describing the write.
            :param aspects: The effective aspects for the call.

            :raises: UefiVariableError
        """
        path = self._efivarfs_path(
            name=variable.name, vendor_guid=variable.vendor_guid,
        )
        attr_prefix = int(variable.attributes).to_bytes(length=4, byteorder="little", signed=False)
        payload = attr_prefix + variable.data
        hex_text = payload.hex()

        # We issue: chattr -i <path> && printf '%s' <hex> | xxd -r -p > <path> ; chattr +i <path>
        command = (
            "chattr -i {path} 2>/dev/null ; "
            "printf '%s' {hex} | xxd -r -p > {path} ; "
            "rc=$? ; chattr +i {path} 2>/dev/null ; exit $rc"
        ).format(path=path, hex=hex_text)

        status, stdout, stderr = self._agent.run_cmd(
            command=command, aspects=aspects,
        )

        if status != 0:
            err_msg_lines = [
                "Host OS efivarfs write returned a non-zero status.",
                "PATH: {}".format(path),
                "STATUS: {}".format(status),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiVariableError(
                err_msg,
                name=variable.name,
                vendor_guid=variable.vendor_guid,
                attributes=int(variable.attributes),
                efi_status=int(status),
            )

        return

    def enumerate_variables(self, aspects: UefiCmdAspects) -> Iterator[Tuple[str, uuid.UUID]]:
        """
            Stream the sorted directory listing of
            ``/sys/firmware/efi/efivars/`` as ``(name, vendor_guid)``
            tuples.

            :param aspects: The effective aspects for the call.

            :returns: An iterator of ``(name, vendor_guid)`` tuples.
            :raises: UefiVariableError
        """
        command = "ls -1 /sys/firmware/efi/efivars/"
        status, stdout, stderr = self._agent.run_cmd(
            command=command, aspects=aspects,
        )

        if status != 0:
            err_msg_lines = [
                "Host OS enumeration of efivarfs failed.",
                "COMMAND: {}".format(command),
                "STATUS: {}".format(status),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiVariableError(err_msg, efi_status=int(status))

        lines = [line.strip() for line in (stdout if stdout is not None else "").splitlines()]
        entries = [line for line in lines if len(line) > 0]
        entries.sort()

        guid_suffix_re = re.compile(
            r"^(?P<name>.+?)-(?P<guid>[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$"
        )

        for entry in entries:
            entry_match = guid_suffix_re.match(entry)
            if entry_match is None:
                # Skip lines that are not variable files (README, etc.).
                continue
            try:
                decoded_guid = uuid.UUID(entry_match.group("guid"))
            except ValueError:
                continue
            yield (entry_match.group("name"), decoded_guid)

        return

    def query_variable_info(self, attributes: UefiVariableAttributes,
                                  aspects: UefiCmdAspects) -> Tuple[int, int, int]:
        """
            The Linux efivarfs does not expose ``QueryVariableInfo`` in a
            stable machine-readable form.  The operation is refused
            explicitly so the caller can route to Redfish when needed.

            :param attributes: The attribute bitfield being queried.
            :param aspects: The effective aspects for the call.

            :returns: Never returns.
            :raises: UefiTransportError
        """
        err_msg_lines = [
            "Host OS backend does not expose QueryVariableInfo.",
            "BACKEND: host-os",
            "OPERATION: query_variable_info",
            "HINT: efivarfs does not publish storage statistics; route via Redfish.",
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiTransportError(err_msg) from None


# ---------------------------------------------------------------------------
# Redfish backend
# ---------------------------------------------------------------------------

_REDFISH_REFUSAL_HINT = (
    "Redfish models BIOS attributes but does not expose raw EFI variable "
    "bytes.  Use the shell or host-OS backend for raw variable access."
)


class _RedfishVariableBackend(_BackendBase):
    """
        Variable-services backend that routes through the Redfish
        ``/Systems/{id}/Bios`` subtree.  Because Redfish does not expose
        raw EFI variable byte-for-byte access, ``get_variable`` and
        ``set_variable`` both raise :class:`UefiVariableError` with an
        explanatory ``details`` block.
    """

    @property
    def name(self) -> str:
        """
            Short identifier for log output.

            :returns: ``"redfish"``.
        """
        rtnval = "redfish"
        return rtnval

    def get_variable(self, name: str,
                           vendor_guid: uuid.UUID,
                           aspects: UefiCmdAspects) -> UefiVariable:
        """
            Redfish does not expose raw EFI variable reads; the operation
            is refused with a clear error message.

            :param name: The variable name.
            :param vendor_guid: The vendor GUID.
            :param aspects: The effective aspects for the call.

            :returns: Never returns.
            :raises: UefiVariableError
        """
        err_msg_lines = [
            "Redfish does not expose raw EFI variable access.",
            _REDFISH_REFUSAL_HINT,
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiVariableError(
            err_msg,
            name=name,
            vendor_guid=vendor_guid,
            efi_status=EFI_UNSUPPORTED,
        ) from None

    def set_variable(self, variable: UefiVariable,
                           aspects: UefiCmdAspects) -> None:
        """
            Redfish does not expose raw EFI variable writes; the operation
            is refused with a clear error message.

            :param variable: The :class:`UefiVariable` describing the write.
            :param aspects: The effective aspects for the call.

            :raises: UefiVariableError
        """
        err_msg_lines = [
            "Redfish does not expose raw EFI variable access.",
            _REDFISH_REFUSAL_HINT,
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiVariableError(
            err_msg,
            name=variable.name,
            vendor_guid=variable.vendor_guid,
            attributes=int(variable.attributes),
            efi_status=EFI_UNSUPPORTED,
        ) from None

    def enumerate_variables(self, aspects: UefiCmdAspects) -> Iterator[Tuple[str, uuid.UUID]]:
        """
            Redfish does not expose an EFI-variable enumeration; the
            generator immediately terminates without yielding.

            :param aspects: The effective aspects for the call.

            :returns: An empty iterator.
        """
        # Zero-yield generator.
        if False:
            yield ("", uuid.UUID(int=0))
        return

    def query_variable_info(self, attributes: UefiVariableAttributes,
                                  aspects: UefiCmdAspects) -> Tuple[int, int, int]:
        """
            Redfish does not expose ``QueryVariableInfo``; the operation is
            refused with a clear error message.

            :param attributes: The attribute bitfield being queried.
            :param aspects: The effective aspects for the call.

            :returns: Never returns.
            :raises: UefiTransportError
        """
        err_msg_lines = [
            "Redfish backend does not expose QueryVariableInfo.",
            _REDFISH_REFUSAL_HINT,
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiTransportError(err_msg) from None


# ---------------------------------------------------------------------------
# Facade
# ---------------------------------------------------------------------------

class VariableStore:
    """
        Public facade for UEFI variable-services CRUD.  Hides the
        underlying backend selection behind a single surface and normalizes
        aspects resolution and logging.

        :param agent: The :class:`UefiAgent` through which every backend
                      dispatches.
        :param aspects: The default :class:`UefiCmdAspects` used when a
                        call passes ``aspects=None``.
    """

    def __init__(self, agent: Any,
                       aspects: UefiCmdAspects = DEFAULT_CMD_ASPECTS) -> None:
        """
            Initialize the variable store and select a backend based on
            ``agent.transport_kind``.

            :param agent: The :class:`UefiAgent` instance.
            :param aspects: The default aspects.

            :raises: ConfigurationError
        """
        self._agent: Any = agent
        self._aspects: UefiCmdAspects = aspects
        self._logger = get_uefi_logger(UEFI_VARIABLE_CHANNEL)
        self._backend: _BackendBase = self._select_backend(agent=agent)

        self._logger.info(
            "uefi.variable: VariableStore constructed backend=%s transport_kind=%s",
            self._backend.name, getattr(agent, "transport_kind", None),
        )
        return

    def _select_backend(self, agent: Any) -> _BackendBase:
        """
            Select the appropriate backend based on ``agent.transport_kind``.

            :param agent: The :class:`UefiAgent`.

            :returns: A constructed backend.
            :raises: ConfigurationError
        """
        transport_kind = getattr(agent, "transport_kind", None)

        if transport_kind in ("serial", "ipmi-sol"):
            rtnval: _BackendBase = _ShellVariableBackend(agent=agent)
            return rtnval

        if transport_kind == "ssh-host":
            rtnval = _HostOsVariableBackend(agent=agent)
            return rtnval

        if transport_kind == "redfish":
            rtnval = _RedfishVariableBackend(agent=agent)
            return rtnval

        err_msg_lines = [
            "VariableStore: unknown or missing 'transport_kind' on the agent.",
            "OBSERVED: transport_kind={!r}".format(transport_kind),
            "ALLOWED: 'serial', 'ipmi-sol', 'ssh-host', 'redfish'",
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise ConfigurationError(err_msg) from None

    # ------------------------------------------------------------------
    # Public operations
    # ------------------------------------------------------------------

    def get_variable(self, name: str,
                           vendor_guid: Union[str, uuid.UUID],
                           aspects: Optional[UefiCmdAspects] = None) -> UefiVariable:
        """
            Read a variable by name and vendor GUID.

            :param name: The variable name.
            :param vendor_guid: The vendor GUID (``str`` or ``uuid.UUID``).
            :param aspects: Optional per-call aspects override.

            :returns: The :class:`UefiVariable` record.
            :raises: UefiVariableError, ConfigurationError
        """
        effective = resolve_aspects(passed=aspects, instance_default=self._aspects)
        resolved_guid = _coerce_vendor_guid(vendor_guid=vendor_guid)

        self._logger.debug(
            "uefi.variable: get_variable backend=%s name=%s guid=%s",
            self._backend.name, name, resolved_guid,
        )

        rtnval = self._backend.get_variable(
            name=name, vendor_guid=resolved_guid, aspects=effective,
        )
        return rtnval

    def set_variable(self, variable: UefiVariable,
                           aspects: Optional[UefiCmdAspects] = None,
                           signer_key: Optional[Any] = None,
                           signer_cert: Optional[Any] = None,
                           signing_time: Optional[datetime.datetime] = None) -> None:
        """
            Write a variable through the backend.  When the attribute
            bitfield requests a TIME_BASED_AUTHENTICATED_WRITE_ACCESS write
            and the caller provides ``signer_key``, ``signer_cert``, and
            ``signing_time`` kwargs, assemble the
            ``EFI_VARIABLE_AUTHENTICATION_2`` descriptor and prepend it to
            the payload before dispatching.

            :param variable: The :class:`UefiVariable` to write.
            :param aspects: Optional per-call aspects override.
            :param signer_key: Optional ``cryptography`` private key used
                               when assembling the auth descriptor.
            :param signer_cert: Optional ``cryptography`` X.509 certificate
                                used when assembling the auth descriptor.
            :param signing_time: Optional timezone-aware
                                 :class:`datetime.datetime` used as the
                                 descriptor timestamp.

            :raises: UefiVariableError, ConfigurationError
        """
        effective = resolve_aspects(passed=aspects, instance_default=self._aspects)

        _validate_attributes(attributes=variable.attributes)

        attr_value = int(variable.attributes)
        payload = variable.data

        time_auth_set = (attr_value & int(UefiVariableAttributes.TIME_BASED_AUTHENTICATED_WRITE_ACCESS)) != 0
        legacy_auth_set = (attr_value & int(UefiVariableAttributes.AUTHENTICATED_WRITE_ACCESS)) != 0

        if time_auth_set:
            if signer_key is not None and signer_cert is not None:
                if signing_time is None:
                    err_msg_lines = [
                        "set_variable: TIME_BASED_AUTHENTICATED_WRITE_ACCESS requires 'signing_time' when a signer is provided.",
                        "NAME: {}".format(variable.name),
                        "VENDOR GUID: {}".format(variable.vendor_guid),
                    ]
                    err_msg = os.linesep.join(err_msg_lines)
                    raise ConfigurationError(err_msg) from None

                descriptor = build_auth2_descriptor(
                    data=payload,
                    time=signing_time,
                    signer_key=signer_key,
                    signer_cert=signer_cert,
                )
                payload = descriptor + payload
            elif signer_key is not None or signer_cert is not None:
                err_msg_lines = [
                    "set_variable: both 'signer_key' and 'signer_cert' must be supplied together.",
                    "signer_key: {!r}".format(signer_key is not None),
                    "signer_cert: {!r}".format(signer_cert is not None),
                ]
                err_msg = os.linesep.join(err_msg_lines)
                raise ConfigurationError(err_msg) from None

        if legacy_auth_set and (signer_key is not None or signer_cert is not None):
            err_msg_lines = [
                "set_variable: AUTHENTICATED_WRITE_ACCESS (legacy) does not accept an unwrapped signer.",
                "HINT: pre-build the descriptor or use TIME_BASED_AUTHENTICATED_WRITE_ACCESS.",
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg) from None

        # Rebuild the record with the possibly-prefixed payload so the
        # backend's wire form is correct.  Backends expect the payload
        # bytes as-is.
        wire_variable = UefiVariable(
            name=variable.name,
            vendor_guid=variable.vendor_guid,
            attributes=variable.attributes,
            data=payload,
        )

        self._logger.debug(
            "uefi.variable: set_variable backend=%s name=%s guid=%s attrs=0x%08X bytes=%d",
            self._backend.name, variable.name, variable.vendor_guid,
            attr_value, len(payload),
        )

        try:
            self._backend.set_variable(variable=wire_variable, aspects=effective)
        except UefiVariableError as verr:
            self._logger.error(
                "uefi.variable: set_variable failed backend=%s name=%s guid=%s efi_status=%s",
                self._backend.name, variable.name, variable.vendor_guid,
                "0x{:X}".format(verr.efi_status) if verr.efi_status is not None else "None",
            )
            raise

        return

    def delete_variable(self, name: str,
                              vendor_guid: Union[str, uuid.UUID],
                              aspects: Optional[UefiCmdAspects] = None) -> None:
        """
            Delete a variable.  Per UEFI §8.2.3 a delete is encoded as a
            ``SetVariable`` with zero-length data AND zero attributes.

            :param name: The variable name.
            :param vendor_guid: The vendor GUID (``str`` or ``uuid.UUID``).
            :param aspects: Optional per-call aspects override.

            :raises: UefiVariableError, ConfigurationError
        """
        effective = resolve_aspects(passed=aspects, instance_default=self._aspects)
        resolved_guid = _coerce_vendor_guid(vendor_guid=vendor_guid)

        delete_variable = UefiVariable(
            name=name,
            vendor_guid=resolved_guid,
            attributes=UefiVariableAttributes(0),
            data=b"",
        )

        self._logger.debug(
            "uefi.variable: delete_variable backend=%s name=%s guid=%s",
            self._backend.name, name, resolved_guid,
        )

        self._backend.set_variable(variable=delete_variable, aspects=effective)
        return

    def enumerate_variables(self, aspects: Optional[UefiCmdAspects] = None,
                                  name_filter: Optional[str] = None) -> Iterator[Tuple[str, uuid.UUID]]:
        """
            Stream ``(name, vendor_guid)`` tuples for every variable
            exposed by the backend.  The returned value is a generator so
            the caller may ``break`` early and release any backend cursor.

            :param aspects: Optional per-call aspects override.
            :param name_filter: Optional ``fnmatch``-style glob filter on
                                the variable name.

            :returns: An iterator of ``(name, vendor_guid)`` tuples.
            :raises: UefiVariableError
        """
        effective = resolve_aspects(passed=aspects, instance_default=self._aspects)

        self._logger.debug(
            "uefi.variable: enumerate_variables backend=%s filter=%r",
            self._backend.name, name_filter,
        )

        def _generator() -> Iterator[Tuple[str, uuid.UUID]]:
            """
                Inner generator so :meth:`enumerate_variables` itself
                remains a normal method; the backend call is deferred
                until the caller iterates.
            """
            for entry_name, entry_guid in self._backend.enumerate_variables(aspects=effective):
                if name_filter is not None:
                    if not fnmatch.fnmatchcase(entry_name, name_filter):
                        continue
                yield (entry_name, entry_guid)
            return

        rtnval = _generator()
        return rtnval

    def query_variable_info(self, attributes: UefiVariableAttributes,
                                  aspects: Optional[UefiCmdAspects] = None) -> Tuple[int, int, int]:
        """
            Return the ``(maximum_variable_storage_size,
            remaining_variable_storage_size, maximum_variable_size)`` tuple
            for ``attributes`` per UEFI §8.2.4.

            :param attributes: The attribute bitfield.
            :param aspects: Optional per-call aspects override.

            :returns: The three-tuple of storage sizes in bytes.
            :raises: UefiTransportError, UefiVariableError
        """
        effective = resolve_aspects(passed=aspects, instance_default=self._aspects)

        self._logger.debug(
            "uefi.variable: query_variable_info backend=%s attrs=0x%08X",
            self._backend.name, int(attributes),
        )

        rtnval = self._backend.query_variable_info(
            attributes=attributes, aspects=effective,
        )
        return rtnval
