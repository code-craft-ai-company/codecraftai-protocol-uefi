"""
    .. module:: uefisecureboot
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Module providing Secure Boot state inspection and key
               enrollment for the platform trust anchors (PK, KEK) and
               image signature databases (``db``, ``dbx``, ``dbt``,
               ``dbr``) per UEFI Specification §32.  Parses and emits
               ``EFI_SIGNATURE_LIST`` and ``EFI_SIGNATURE_DATA`` buffers
               via the :class:`SignatureList` / :class:`SignatureDatabase`
               round-trippable codecs, and assembles authenticated-write
               payloads through :func:`build_auth2_descriptor` supplied by
               :mod:`ccai.protocols.uefi.uefivariables`.

               The key-handling boundary is strict: the library never
               reads a private key from disk.  Callers supply a
               ``cryptography`` private-key object and matching X.509
               certificate to :meth:`SecureBootManager.enroll_signature_database`;
               signing happens inside the caller's process memory only.
               Writes outside ``SetupMode`` are refused unless the caller
               explicitly passes ``force=True`` together with an
               acknowledgement dictionary carrying the operator name and
               ticket reference, which is recorded in the audit log.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Code Craft AI LLC"
__credits__ = []


import datetime
import enum
import hashlib
import os
import struct
import uuid

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from ccai.errors.exceptions import ConfigurationError

from ccai.protocols.uefi.uefiaspects import (
    DEFAULT_CMD_ASPECTS,
    UefiCmdAspects,
    resolve_aspects,
)
from ccai.protocols.uefi.ueficonst import guid_from_bytes, guid_to_bytes
from ccai.protocols.uefi.uefiexceptions import (
    UefiSecureBootError,
    UefiVariableError,
)
from ccai.protocols.uefi.uefiguids import (
    EFI_GLOBAL_VARIABLE_GUID,
    EFI_IMAGE_SECURITY_DATABASE_GUID,
)
from ccai.protocols.uefi.uefilogging import (
    UEFI_SECUREBOOT_CHANNEL,
    get_uefi_logger,
)
from ccai.protocols.uefi.uefivariables import (
    UefiVariable,
    UefiVariableAttributes,
    VariableStore,
)


__all__ = [
    "EFI_CERT_RSA2048_GUID",
    "EFI_CERT_SHA256_GUID",
    "EFI_CERT_SHA384_GUID",
    "EFI_CERT_SHA512_GUID",
    "EFI_CERT_X509_GUID",
    "SIGNATURE_DATABASE_MAP",
    "SecureBootManager",
    "SecureBootMode",
    "SecureBootState",
    "SignatureDatabase",
    "SignatureEntry",
    "SignatureList",
]


# ---------------------------------------------------------------------------
# Well-known EFI_SIGNATURE_DATA type GUIDs (UEFI §32.4.1)
# ---------------------------------------------------------------------------

EFI_CERT_SHA256_GUID: uuid.UUID = uuid.UUID("c1c41626-504c-4092-aca9-41f936934328")
EFI_CERT_SHA384_GUID: uuid.UUID = uuid.UUID("ff3e5307-9fd0-48c9-85f1-8ad56c701e01")
EFI_CERT_SHA512_GUID: uuid.UUID = uuid.UUID("093e0fae-a6c4-4f50-9f1b-d41e2b89c19a")
EFI_CERT_RSA2048_GUID: uuid.UUID = uuid.UUID("3c5766e8-269c-4e34-aa14-ed776e85b3b6")
EFI_CERT_X509_GUID: uuid.UUID = uuid.UUID("a5c059a1-94e4-4aa7-87b5-ab155c2bf072")


# ---------------------------------------------------------------------------
# Signature record types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SignatureEntry:
    """
        A single ``EFI_SIGNATURE_DATA`` record within an
        ``EFI_SIGNATURE_LIST``.

        :ivar signature_type: The signature-type GUID that classifies
                              ``payload`` (for example
                              :data:`EFI_CERT_SHA256_GUID` or
                              :data:`EFI_CERT_X509_GUID`).
        :ivar owner: The ``SignatureOwner`` GUID that identifies the
                     signing authority.
        :ivar payload: The raw ``SignatureData`` payload bytes.
    """
    signature_type: uuid.UUID
    owner: uuid.UUID
    payload: bytes


@dataclass(frozen=True)
class SignatureList:
    """
        A single ``EFI_SIGNATURE_LIST`` record.  All entries in the list
        share ``signature_type`` and therefore share ``SignatureSize``.

        :ivar signature_type: The shared signature-type GUID.
        :ivar header: The optional ``SignatureHeader`` bytes (usually
                      empty; defined per signature-type).
        :ivar signatures: Tuple of :class:`SignatureEntry` records.
    """
    signature_type: uuid.UUID
    header: bytes
    signatures: Tuple[SignatureEntry, ...]

    @classmethod
    def from_bytes(cls, data: bytes) -> "SignatureList":
        """
            Parse a single ``EFI_SIGNATURE_LIST`` buffer.

            The buffer must begin with the 28-byte ESL header followed by
            ``SignatureHeaderSize`` bytes of header and one or more
            ``SignatureSize``-byte entries.  Extra bytes after the list's
            declared size are rejected by this helper; use
            :meth:`SignatureDatabase.from_bytes` to decode concatenated
            lists.

            :param data: The buffer to parse.

            :returns: A :class:`SignatureList`.
            :raises: UefiSecureBootError
        """
        buffer = _require_bytes_like(field_name="data", value=data)

        if len(buffer) < _ESL_HEADER_SIZE:
            err_msg_lines = [
                "SignatureList.from_bytes: buffer is shorter than the ESL header.",
                "EXPECTED: at least {} bytes".format(_ESL_HEADER_SIZE),
                "OBSERVED: {}".format(len(buffer)),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiSecureBootError(err_msg) from None

        sig_type_bytes = buffer[0:16]
        list_size = struct.unpack("<I", buffer[16:20])[0]
        sig_header_size = struct.unpack("<I", buffer[20:24])[0]
        sig_size = struct.unpack("<I", buffer[24:28])[0]

        if list_size != len(buffer):
            err_msg_lines = [
                "SignatureList.from_bytes: declared SignatureListSize does not match buffer length.",
                "DECLARED: {}".format(list_size),
                "OBSERVED: {}".format(len(buffer)),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiSecureBootError(err_msg) from None

        sig_type = guid_from_bytes(sig_type_bytes)

        parsed_list = _parse_esl_body(
            list_buffer=buffer,
            signature_type=sig_type,
            list_size=list_size,
            sig_header_size=sig_header_size,
            sig_size=sig_size,
        )
        return parsed_list

    def to_bytes(self) -> bytes:
        """
            Serialize the :class:`SignatureList` to its on-wire
            ``EFI_SIGNATURE_LIST`` layout.

            :returns: The encoded buffer.
            :raises: UefiSecureBootError
        """
        if len(self.signatures) == 0:
            err_msg_lines = [
                "SignatureList.to_bytes: refusing to emit an empty list.",
                "SIGNATURE TYPE: {}".format(self.signature_type),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiSecureBootError(err_msg) from None

        first_payload_len = len(self.signatures[0].payload)
        for index, entry in enumerate(self.signatures):
            if entry.signature_type != self.signature_type:
                err_msg_lines = [
                    "SignatureList.to_bytes: entry signature_type differs from list signature_type.",
                    "LIST TYPE: {}".format(self.signature_type),
                    "ENTRY INDEX: {}".format(index),
                    "ENTRY TYPE: {}".format(entry.signature_type),
                ]
                err_msg = os.linesep.join(err_msg_lines)
                raise UefiSecureBootError(err_msg) from None

            if len(entry.payload) != first_payload_len:
                err_msg_lines = [
                    "SignatureList.to_bytes: entry payload sizes are not uniform.",
                    "FIRST PAYLOAD SIZE: {}".format(first_payload_len),
                    "ENTRY INDEX: {}".format(index),
                    "ENTRY PAYLOAD SIZE: {}".format(len(entry.payload)),
                ]
                err_msg = os.linesep.join(err_msg_lines)
                raise UefiSecureBootError(err_msg) from None

        sig_size = 16 + first_payload_len
        sig_header_size = len(self.header)
        list_size = _ESL_HEADER_SIZE + sig_header_size + sig_size * len(self.signatures)

        sig_type_bytes = guid_to_bytes(self.signature_type)

        parts = [
            sig_type_bytes,
            struct.pack("<I", list_size),
            struct.pack("<I", sig_header_size),
            struct.pack("<I", sig_size),
            self.header,
        ]
        for entry in self.signatures:
            parts.append(guid_to_bytes(entry.owner))
            parts.append(entry.payload)

        rtnval = b"".join(parts)
        return rtnval


@dataclass(frozen=True)
class SignatureDatabase:
    """
        An ordered collection of :class:`SignatureList` records.  Mirrors
        the on-wire layout of a Secure Boot variable such as ``db`` or
        ``dbx`` which is a concatenation of zero or more
        ``EFI_SIGNATURE_LIST`` structures.

        :ivar lists: Tuple of :class:`SignatureList` records (may be
                     empty when the database is cleared).
    """
    lists: Tuple[SignatureList, ...]

    @classmethod
    def from_bytes(cls, data: bytes) -> "SignatureDatabase":
        """
            Parse concatenated ``EFI_SIGNATURE_LIST`` records until the
            input buffer is exhausted.  An empty buffer decodes to an
            empty database.

            :param data: The buffer to parse.

            :returns: A :class:`SignatureDatabase`.
            :raises: UefiSecureBootError
        """
        buffer = _require_bytes_like(field_name="data", value=data)

        lists: list = []
        offset = 0
        total = len(buffer)

        while offset < total:
            remaining = total - offset
            if remaining < _ESL_HEADER_SIZE:
                err_msg_lines = [
                    "SignatureDatabase.from_bytes: trailing bytes are too short to contain an ESL header.",
                    "OFFSET: {}".format(offset),
                    "REMAINING BYTES: {}".format(remaining),
                ]
                err_msg = os.linesep.join(err_msg_lines)
                raise UefiSecureBootError(err_msg) from None

            list_size = struct.unpack("<I", buffer[offset + 16:offset + 20])[0]

            if list_size < _ESL_HEADER_SIZE:
                err_msg_lines = [
                    "SignatureDatabase.from_bytes: SignatureListSize is smaller than the ESL header.",
                    "OFFSET: {}".format(offset),
                    "DECLARED SIZE: {}".format(list_size),
                ]
                err_msg = os.linesep.join(err_msg_lines)
                raise UefiSecureBootError(err_msg) from None

            if list_size > remaining:
                err_msg_lines = [
                    "SignatureDatabase.from_bytes: SignatureListSize exceeds remaining buffer.",
                    "OFFSET: {}".format(offset),
                    "DECLARED SIZE: {}".format(list_size),
                    "REMAINING BYTES: {}".format(remaining),
                ]
                err_msg = os.linesep.join(err_msg_lines)
                raise UefiSecureBootError(err_msg) from None

            list_bytes = buffer[offset:offset + list_size]
            parsed = SignatureList.from_bytes(list_bytes)
            lists.append(parsed)
            offset = offset + list_size

        rtnval = cls(lists=tuple(lists))
        return rtnval

    def to_bytes(self) -> bytes:
        """
            Serialize the :class:`SignatureDatabase` as the concatenation
            of each :meth:`SignatureList.to_bytes` result.  An empty
            database serializes to an empty buffer.

            :returns: The encoded buffer.
            :raises: UefiSecureBootError
        """
        parts = []
        for sig_list in self.lists:
            parts.append(sig_list.to_bytes())

        rtnval = b"".join(parts)
        return rtnval


# ---------------------------------------------------------------------------
# Secure Boot mode enum and state record
# ---------------------------------------------------------------------------

class SecureBootMode(enum.Enum):
    """
        High-level classification of the platform Secure Boot mode
        derived from the three mode flags published by firmware.
    """
    SETUP = "setup"
    USER = "user"
    AUDIT = "audit"
    DEPLOYED = "deployed"


@dataclass(frozen=True)
class SecureBootState:
    """
        Snapshot of the platform Secure Boot posture at a single instant.

        :ivar secure_boot: ``True`` when ``SecureBoot == 1`` (enforcing).
        :ivar setup_mode: ``True`` when ``SetupMode == 1``.
        :ivar audit_mode: ``True`` when ``AuditMode == 1``.
        :ivar deployed_mode: ``True`` when ``DeployedMode == 1``.
        :ivar mode: The derived high-level :class:`SecureBootMode`.
    """
    secure_boot: bool
    setup_mode: bool
    audit_mode: bool
    deployed_mode: bool
    mode: SecureBootMode


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_ESL_HEADER_SIZE: int = 28
_ESL_ENTRY_OWNER_SIZE: int = 16

_SECURE_BOOT_VARIABLE_NAME: str = "SecureBoot"
_SETUP_MODE_VARIABLE_NAME: str = "SetupMode"
_AUDIT_MODE_VARIABLE_NAME: str = "AuditMode"
_DEPLOYED_MODE_VARIABLE_NAME: str = "DeployedMode"

_SECURE_BOOT_MODE_VARIABLES: Tuple[str, ...] = (
    _SECURE_BOOT_VARIABLE_NAME,
    _SETUP_MODE_VARIABLE_NAME,
    _AUDIT_MODE_VARIABLE_NAME,
    _DEPLOYED_MODE_VARIABLE_NAME,
)

SIGNATURE_DATABASE_MAP: Dict[str, uuid.UUID] = {
    "PK": EFI_GLOBAL_VARIABLE_GUID,
    "KEK": EFI_GLOBAL_VARIABLE_GUID,
    "db": EFI_IMAGE_SECURITY_DATABASE_GUID,
    "dbx": EFI_IMAGE_SECURITY_DATABASE_GUID,
    "dbt": EFI_IMAGE_SECURITY_DATABASE_GUID,
    "dbr": EFI_IMAGE_SECURITY_DATABASE_GUID,
}

_REQUIRED_ACK_KEYS: Tuple[str, ...] = ("operator", "ticket")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _require_bytes_like(field_name: str, value: object) -> bytes:
    """
        Validate that ``value`` is bytes-like and coerce to :class:`bytes`.

        :param field_name: The calling field name for diagnostics.
        :param value: The candidate value.

        :returns: The validated buffer as :class:`bytes`.
        :raises: UefiSecureBootError
    """
    if not isinstance(value, (bytes, bytearray, memoryview)):
        err_msg_lines = [
            "'{}' must be a bytes-like object.".format(field_name),
            "GOT: value={!r} type={}".format(value, type(value).__name__),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiSecureBootError(err_msg) from None

    rtnval = bytes(value)
    return rtnval


def _parse_esl_body(list_buffer: bytes,
                    signature_type: uuid.UUID,
                    list_size: int,
                    sig_header_size: int,
                    sig_size: int) -> "SignatureList":
    """
        Parse the body of an :class:`EFI_SIGNATURE_LIST` whose header
        fields have already been extracted.

        :param list_buffer: The full list buffer (header + body).
        :param signature_type: Decoded signature-type GUID.
        :param list_size: Declared SignatureListSize.
        :param sig_header_size: Declared SignatureHeaderSize.
        :param sig_size: Declared SignatureSize.

        :returns: The parsed :class:`SignatureList`.
        :raises: UefiSecureBootError
    """
    if sig_size < _ESL_ENTRY_OWNER_SIZE + 1:
        err_msg_lines = [
            "SignatureList: SignatureSize must be larger than the 16-byte owner GUID.",
            "OBSERVED: SignatureSize={}".format(sig_size),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiSecureBootError(err_msg) from None

    body_offset = _ESL_HEADER_SIZE + sig_header_size
    if body_offset > list_size:
        err_msg_lines = [
            "SignatureList: SignatureHeaderSize extends past SignatureListSize.",
            "HEADER SIZE: {}".format(sig_header_size),
            "LIST SIZE: {}".format(list_size),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiSecureBootError(err_msg) from None

    entries_total = list_size - body_offset
    if entries_total == 0:
        err_msg_lines = [
            "SignatureList: ESL body has zero entries.",
            "LIST SIZE: {}".format(list_size),
            "HEADER SIZE: {}".format(sig_header_size),
            "SIGNATURE SIZE: {}".format(sig_size),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiSecureBootError(err_msg) from None

    if (entries_total % sig_size) != 0:
        err_msg_lines = [
            "SignatureList: entry region size is not a multiple of SignatureSize.",
            "ENTRY REGION SIZE: {}".format(entries_total),
            "SIGNATURE SIZE: {}".format(sig_size),
            "LIST SIZE: {}".format(list_size),
            "HEADER SIZE: {}".format(sig_header_size),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiSecureBootError(err_msg) from None

    header_bytes = bytes(list_buffer[_ESL_HEADER_SIZE:body_offset])

    entry_count = entries_total // sig_size
    entries: list = []
    cursor = body_offset
    for _ in range(entry_count):
        owner_bytes = bytes(list_buffer[cursor:cursor + _ESL_ENTRY_OWNER_SIZE])
        payload_bytes = bytes(list_buffer[cursor + _ESL_ENTRY_OWNER_SIZE:cursor + sig_size])
        owner_guid = guid_from_bytes(owner_bytes)
        entry = SignatureEntry(
            signature_type=signature_type,
            owner=owner_guid,
            payload=payload_bytes,
        )
        entries.append(entry)
        cursor = cursor + sig_size

    rtnval = SignatureList(
        signature_type=signature_type,
        header=header_bytes,
        signatures=tuple(entries),
    )
    return rtnval


def _decode_mode_flag(variable: UefiVariable, variable_name: str) -> bool:
    """
        Decode a Secure Boot mode variable whose payload is a single
        ``u8`` flag.

        :param variable: The :class:`UefiVariable` record.
        :param variable_name: Name for diagnostics.

        :returns: ``True`` when the flag byte is ``1``.
        :raises: UefiSecureBootError
    """
    data = variable.data
    if len(data) < 1:
        err_msg_lines = [
            "SecureBoot mode variable has an empty payload.",
            "VARIABLE: {}".format(variable_name),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiSecureBootError(err_msg) from None

    flag_byte = data[0]
    rtnval = flag_byte == 1
    return rtnval


def _derive_mode(setup_mode: bool, audit_mode: bool, deployed_mode: bool) -> SecureBootMode:
    """
        Derive the high-level :class:`SecureBootMode` from the three
        mode flags.  Precedence order is ``SETUP`` > ``AUDIT`` >
        ``DEPLOYED`` > ``USER``.

        :param setup_mode: Decoded ``SetupMode`` flag.
        :param audit_mode: Decoded ``AuditMode`` flag.
        :param deployed_mode: Decoded ``DeployedMode`` flag.

        :returns: The derived :class:`SecureBootMode`.
    """
    if setup_mode:
        rtnval = SecureBootMode.SETUP
        return rtnval

    if audit_mode:
        rtnval = SecureBootMode.AUDIT
        return rtnval

    if deployed_mode:
        rtnval = SecureBootMode.DEPLOYED
        return rtnval

    rtnval = SecureBootMode.USER
    return rtnval


def _validate_ack(ack: Optional[Dict[str, str]]) -> Dict[str, str]:
    """
        Validate an acknowledgement dictionary required for ``force=True``
        writes outside SetupMode.  The dictionary must contain non-empty
        string values for every entry in :data:`_REQUIRED_ACK_KEYS`.

        :param ack: Candidate acknowledgement dictionary.

        :returns: The validated dictionary.
        :raises: UefiSecureBootError
    """
    if ack is None:
        missing = list(_REQUIRED_ACK_KEYS)
        err_msg_lines = [
            "force=True requires an 'ack' dictionary naming the operator and ticket.",
            "MISSING KEYS: {}".format(missing),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiSecureBootError(err_msg) from None

    if not isinstance(ack, dict):
        err_msg_lines = [
            "'ack' must be a dict of str -> str.",
            "GOT: value={!r} type={}".format(ack, type(ack).__name__),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiSecureBootError(err_msg) from None

    missing_keys: list = []
    for key in _REQUIRED_ACK_KEYS:
        if key not in ack:
            missing_keys.append(key)
            continue
        candidate_value = ack[key]
        if not isinstance(candidate_value, str):
            err_msg_lines = [
                "'ack' entries must be non-empty strings.",
                "KEY: {}".format(key),
                "GOT: value={!r} type={}".format(candidate_value, type(candidate_value).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiSecureBootError(err_msg) from None
        if len(candidate_value) == 0:
            missing_keys.append(key)

    if len(missing_keys) > 0:
        err_msg_lines = [
            "force=True requires a fully-populated 'ack' dictionary.",
            "MISSING OR EMPTY KEYS: {}".format(missing_keys),
            "REQUIRED KEYS: {}".format(list(_REQUIRED_ACK_KEYS)),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiSecureBootError(err_msg) from None

    validated: Dict[str, str] = {}
    for key in _REQUIRED_ACK_KEYS:
        validated[key] = ack[key]
    return validated


# ---------------------------------------------------------------------------
# SecureBootManager facade
# ---------------------------------------------------------------------------

class SecureBootManager:
    """
        Facade for Secure Boot state inspection and signature-database
        enrollment.  Reads ``SecureBoot``, ``SetupMode``, ``AuditMode``,
        and ``DeployedMode`` through a :class:`VariableStore` to produce
        a :class:`SecureBootState` snapshot, enumerates the six well-known
        signature databases (``PK``, ``KEK``, ``db``, ``dbx``, ``dbt``,
        ``dbr``), and performs authenticated writes for enrollment.

        :param variable_store: The :class:`VariableStore` through which
                               reads and writes dispatch.
        :param aspects: Default :class:`UefiCmdAspects` used when a per-call
                        ``aspects`` parameter is ``None``.
    """

    SIGNATURE_DATABASE_MAP: Dict[str, uuid.UUID] = SIGNATURE_DATABASE_MAP

    def __init__(self, variable_store: VariableStore,
                       aspects: UefiCmdAspects = DEFAULT_CMD_ASPECTS) -> None:
        """
            Initialize the Secure Boot manager.

            :param variable_store: The :class:`VariableStore` to drive.
            :param aspects: The default aspects.
        """
        self._variable_store: VariableStore = variable_store
        self._aspects: UefiCmdAspects = aspects
        self._logger = get_uefi_logger(UEFI_SECUREBOOT_CHANNEL)
        return

    def read_state(self, aspects: Optional[UefiCmdAspects] = None) -> SecureBootState:
        """
            Read the four Secure Boot mode variables and return a
            :class:`SecureBootState` snapshot.

            :param aspects: Optional per-call aspects override.

            :returns: The decoded :class:`SecureBootState`.
            :raises: UefiSecureBootError
        """
        effective = resolve_aspects(passed=aspects, instance_default=self._aspects)

        decoded: Dict[str, bool] = {}
        for variable_name in _SECURE_BOOT_MODE_VARIABLES:
            try:
                record = self._variable_store.get_variable(
                    name=variable_name,
                    vendor_guid=EFI_GLOBAL_VARIABLE_GUID,
                    aspects=effective,
                )
            except UefiVariableError as verr:
                err_msg_lines = [
                    "read_state: failed to read a required Secure Boot variable.",
                    "VARIABLE: {}".format(variable_name),
                    "VENDOR GUID: {}".format(EFI_GLOBAL_VARIABLE_GUID),
                    "UNDERLYING: {!r}".format(verr),
                ]
                err_msg = os.linesep.join(err_msg_lines)
                raise UefiSecureBootError(err_msg) from verr

            decoded[variable_name] = _decode_mode_flag(
                variable=record, variable_name=variable_name,
            )

        secure_boot = decoded[_SECURE_BOOT_VARIABLE_NAME]
        setup_mode = decoded[_SETUP_MODE_VARIABLE_NAME]
        audit_mode = decoded[_AUDIT_MODE_VARIABLE_NAME]
        deployed_mode = decoded[_DEPLOYED_MODE_VARIABLE_NAME]

        derived_mode = _derive_mode(
            setup_mode=setup_mode,
            audit_mode=audit_mode,
            deployed_mode=deployed_mode,
        )

        state = SecureBootState(
            secure_boot=secure_boot,
            setup_mode=setup_mode,
            audit_mode=audit_mode,
            deployed_mode=deployed_mode,
            mode=derived_mode,
        )

        self._logger.info(
            "uefi.secureboot: read_state secure_boot=%s setup_mode=%s audit_mode=%s deployed_mode=%s mode=%s",
            secure_boot, setup_mode, audit_mode, deployed_mode, derived_mode.value,
        )

        rtnval = state
        return rtnval

    def read_signature_database(self, name: str,
                                      aspects: Optional[UefiCmdAspects] = None) -> SignatureDatabase:
        """
            Read a named signature database and decode it into a
            :class:`SignatureDatabase`.

            :param name: One of ``"PK"``, ``"KEK"``, ``"db"``, ``"dbx"``,
                         ``"dbt"``, ``"dbr"``.
            :param aspects: Optional per-call aspects override.

            :returns: The decoded :class:`SignatureDatabase`.  An empty
                      or absent variable decodes to an empty database.
            :raises: UefiSecureBootError, ConfigurationError
        """
        effective = resolve_aspects(passed=aspects, instance_default=self._aspects)
        vendor_guid = self._resolve_database_guid(name=name)

        try:
            record = self._variable_store.get_variable(
                name=name,
                vendor_guid=vendor_guid,
                aspects=effective,
            )
        except UefiVariableError as verr:
            # An absent variable is legal for an uninitialized db/dbx; we
            # surface the upstream error because the backend's 'not found'
            # classification is dispositive.
            err_msg_lines = [
                "read_signature_database: underlying get_variable failed.",
                "NAME: {}".format(name),
                "VENDOR GUID: {}".format(vendor_guid),
                "UNDERLYING: {!r}".format(verr),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiSecureBootError(err_msg) from verr

        database = SignatureDatabase.from_bytes(record.data)

        self._logger.info(
            "uefi.secureboot: read_signature_database name=%s guid=%s lists=%d",
            name, vendor_guid, len(database.lists),
        )

        rtnval = database
        return rtnval

    def enroll_signature_database(self, name: str,
                                        database: SignatureDatabase,
                                        signer_key: Any,
                                        signer_cert: Any,
                                        signing_time: datetime.datetime,
                                        force: bool = False,
                                        ack: Optional[Dict[str, str]] = None,
                                        aspects: Optional[UefiCmdAspects] = None) -> None:
        """
            Issue an authenticated ``SetVariable`` write that enrolls
            ``database`` into the named Secure Boot variable.

            The write uses ``TIME_BASED_AUTHENTICATED_WRITE_ACCESS`` and
            is rejected by the library when ``SetupMode`` is clear unless
            ``force=True`` is passed together with an ``ack`` dictionary
            naming the operator and ticket (both recorded at audit-log
            level).  For ``dbx`` writes the ``APPEND_WRITE`` bit is
            added to preserve append-only semantics.

            :param name: One of ``"PK"``, ``"KEK"``, ``"db"``, ``"dbx"``,
                         ``"dbt"``, ``"dbr"``.
            :param database: The :class:`SignatureDatabase` to write.
            :param signer_key: A ``cryptography`` private key object used
                               by :func:`build_auth2_descriptor` to sign
                               the payload.  Never read from disk by this
                               module.
            :param signer_cert: Matching ``cryptography`` X.509 certificate.
            :param signing_time: Timezone-aware :class:`datetime.datetime`
                                 used as the descriptor timestamp.
            :param force: Override the SetupMode guard.  Requires ``ack``.
            :param ack: Operator acknowledgement dictionary with keys
                        ``operator`` and ``ticket`` (non-empty strings).
            :param aspects: Optional per-call aspects override.

            :raises: UefiSecureBootError, ConfigurationError
        """
        effective = resolve_aspects(passed=aspects, instance_default=self._aspects)
        vendor_guid = self._resolve_database_guid(name=name)

        payload = database.to_bytes()

        # Consult platform state so we can gate writes outside SetupMode.
        current_state = self.read_state(aspects=effective)

        if not current_state.setup_mode:
            if not force:
                err_msg_lines = [
                    "enroll_signature_database: refusing to write outside SetupMode without force=True.",
                    "VARIABLE: {}".format(name),
                    "VENDOR GUID: {}".format(vendor_guid),
                    "SETUP MODE: {}".format(current_state.setup_mode),
                    "MODE: {}".format(current_state.mode.value),
                    "HINT: pass force=True with an ack={{'operator': ..., 'ticket': ...}} to proceed.",
                ]
                err_msg = os.linesep.join(err_msg_lines)
                raise UefiSecureBootError(err_msg) from None

            validated_ack = _validate_ack(ack=ack)
            payload_digest = hashlib.sha256(payload).hexdigest()
            self._logger.warning(
                "uefi.secureboot: force-enroll name=%s guid=%s operator=%s ticket=%s payload_sha256=%s timestamp=%s",
                name, vendor_guid,
                validated_ack["operator"], validated_ack["ticket"],
                payload_digest, signing_time.isoformat(),
            )

        # Build the attribute bitfield.  All Secure Boot variables require
        # NV + BS + RT and time-based authenticated write access per §32;
        # dbx additionally carries APPEND_WRITE.
        attribute_bits = (
            UefiVariableAttributes.NON_VOLATILE
            | UefiVariableAttributes.BOOTSERVICE_ACCESS
            | UefiVariableAttributes.RUNTIME_ACCESS
            | UefiVariableAttributes.TIME_BASED_AUTHENTICATED_WRITE_ACCESS
        )
        if name == "dbx":
            attribute_bits = attribute_bits | UefiVariableAttributes.APPEND_WRITE

        wire_variable = UefiVariable(
            name=name,
            vendor_guid=vendor_guid,
            attributes=attribute_bits,
            data=payload,
        )

        self._logger.info(
            "uefi.secureboot: enroll name=%s guid=%s setup_mode=%s attrs=0x%08X bytes=%d",
            name, vendor_guid, current_state.setup_mode,
            int(attribute_bits), len(payload),
        )

        try:
            self._variable_store.set_variable(
                variable=wire_variable,
                aspects=effective,
                signer_key=signer_key,
                signer_cert=signer_cert,
                signing_time=signing_time,
            )
        except UefiVariableError as verr:
            err_msg_lines = [
                "enroll_signature_database: set_variable failed.",
                "NAME: {}".format(name),
                "VENDOR GUID: {}".format(vendor_guid),
                "EFI STATUS: {}".format(
                    "0x{:X}".format(verr.efi_status) if verr.efi_status is not None else "None",
                ),
                "UNDERLYING: {!r}".format(verr),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiSecureBootError(err_msg) from verr

        return

    def append_to_dbx(self, database: SignatureDatabase,
                            signer_key: Any,
                            signer_cert: Any,
                            signing_time: datetime.datetime,
                            force: bool = False,
                            ack: Optional[Dict[str, str]] = None,
                            aspects: Optional[UefiCmdAspects] = None) -> None:
        """
            Convenience wrapper around :meth:`enroll_signature_database`
            that targets ``dbx`` and therefore sets the ``APPEND_WRITE``
            attribute.

            :param database: The :class:`SignatureDatabase` of new
                             revocation entries.
            :param signer_key: A ``cryptography`` private key object.
            :param signer_cert: Matching ``cryptography`` X.509 certificate.
            :param signing_time: Timezone-aware :class:`datetime.datetime`.
            :param force: Override the SetupMode guard.
            :param ack: Operator acknowledgement dictionary.
            :param aspects: Optional per-call aspects override.

            :raises: UefiSecureBootError, ConfigurationError
        """
        self.enroll_signature_database(
            name="dbx",
            database=database,
            signer_key=signer_key,
            signer_cert=signer_cert,
            signing_time=signing_time,
            force=force,
            ack=ack,
            aspects=aspects,
        )
        return

    def _resolve_database_guid(self, name: str) -> uuid.UUID:
        """
            Validate ``name`` and return the companion vendor GUID.

            :param name: Candidate signature-database name.

            :returns: The vendor :class:`uuid.UUID`.
            :raises: ConfigurationError
        """
        if not isinstance(name, str):
            err_msg_lines = [
                "'name' must be a str.",
                "GOT: value={!r} type={}".format(name, type(name).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg) from None

        if name not in SIGNATURE_DATABASE_MAP:
            allowed = sorted(SIGNATURE_DATABASE_MAP.keys())
            err_msg_lines = [
                "'name' is not a recognized Secure Boot signature database.",
                "GOT: value={!r}".format(name),
                "ALLOWED: {}".format(allowed),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg) from None

        rtnval = SIGNATURE_DATABASE_MAP[name]
        return rtnval
