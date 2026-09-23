"""
    .. module:: uefacapsule
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Module implementing the UEFI firmware capsule build,
               delivery, and post-boot report feature per the
               ``5-firmware-capsules`` specification.  Provides the
               :class:`CapsuleHeader`, :class:`FmpCapsuleHeader`,
               :class:`FmpPayloadHeader`, and :class:`Capsule` binary
               data structures that conform to UEFI §8.5.3 and §23.3;
               the :class:`CapsuleBuilder` assembler; the
               :class:`CapsuleDelivery` transport-dispatching facade that
               routes delivery to the caller-configured
               :class:`UefiAgent` (``ssh-host`` via
               ``/dev/efi_capsule_loader`` with ``fwupdmgr`` fallback,
               ``redfish`` via the ``UpdateService.SimpleUpdate``
               multipart endpoint, and ``serial`` / ``ipmi-sol`` refused
               with a :class:`UefiCapsuleError`); and the
               :class:`CapsuleReport` reader that enumerates
               :data:`EFI_CAPSULE_REPORT_GUID`-scoped variables per
               UEFI §8.5.6 through the :class:`VariableStore` facade.

               Signing is strictly caller-supplied: no private-key
               material is loaded from disk and no external signer tool
               is invoked.  The feature surface is deliberately narrow
               so that higher layers (the 6-capsule-update workflow) can
               compose build + deliver + report without dragging
               transport, ESP, or key material into builder-only tests.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Code Craft AI LLC"
__credits__ = []


import hashlib
import os
import uuid

from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

from ccai.protocols.uefi.uefiaspects import (
    DEFAULT_CMD_ASPECTS,
    UefiCmdAspects,
    resolve_aspects,
)
from ccai.protocols.uefi.ueficonst import guid_from_bytes, guid_to_bytes
from ccai.protocols.uefi.uefiexceptions import UefiCapsuleError
from ccai.protocols.uefi.uefiguids import EFI_CAPSULE_REPORT_GUID
from ccai.protocols.uefi.uefihelpers import (
    pack_u16_le,
    pack_u32_le,
    unpack_u16_le,
    unpack_u32_le,
    unpack_u64_le,
)
from ccai.protocols.uefi.uefivariables import VariableStore


__all__ = [
    "Capsule",
    "CapsuleBuilder",
    "CapsuleDelivery",
    "CapsuleHeader",
    "CapsuleReport",
    "CapsuleReportEntry",
    "FMP_PAYLOAD_SIGNATURE",
    "FMP_PAYLOAD_VERSION",
    "FmpCapsuleHeader",
    "FmpPayloadHeader",
    "CAPSULE_FLAGS_INITIATE_RESET",
    "CAPSULE_FLAGS_PERSIST_ACROSS_RESET",
    "CAPSULE_FLAGS_POPULATE_SYSTEM_TABLE",
    "CAPSULE_HEADER_SIZE",
    "FMP_CAPSULE_HEADER_FIXED_SIZE",
    "FMP_PAYLOAD_HEADER_SIZE",
]


# ---------------------------------------------------------------------------
# Capsule header flag constants per UEFI §8.5.3.2
# ---------------------------------------------------------------------------

CAPSULE_FLAGS_PERSIST_ACROSS_RESET: int = 0x00010000
CAPSULE_FLAGS_POPULATE_SYSTEM_TABLE: int = 0x00020000
CAPSULE_FLAGS_INITIATE_RESET: int = 0x00040000


# ---------------------------------------------------------------------------
# Fixed on-wire sizes per UEFI §8.5.3.2 and §23.3
# ---------------------------------------------------------------------------

CAPSULE_HEADER_SIZE: int = 32
FMP_CAPSULE_HEADER_FIXED_SIZE: int = 8
FMP_PAYLOAD_HEADER_SIZE: int = 16

FMP_PAYLOAD_SIGNATURE: bytes = b"MSS1"
FMP_PAYLOAD_VERSION: int = 0x00000001

_MAX_U32: int = 0xFFFFFFFF
_MAX_U16: int = 0xFFFF


# ---------------------------------------------------------------------------
# CapsuleHeader
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CapsuleHeader:
    """
        EFI_CAPSULE_HEADER per UEFI Specification §8.5.3.2.

        Layout (32 bytes total):

        ============  ====  ===============
         Offset        Size Field
        ============  ====  ===============
         0x00          16   CapsuleGuid (EFI_GUID, mixed-endian)
         0x10           4   HeaderSize  (UINT32, little-endian)
         0x14           4   Flags       (UINT32, little-endian)
         0x18           4   CapsuleImageSize (UINT32, little-endian)
         0x1C           4   Reserved (zero-filled, present so that the
                            on-wire header is 32 bytes which matches
                            every EDK2 vector we have targeted)
        ============  ====  ===============

        :ivar capsule_guid: The capsule identifier GUID placed at
                            offset 0.
        :ivar header_size: On-wire header size in bytes.
        :ivar flags: UEFI capsule-flags bitfield; see the
                     ``CAPSULE_FLAGS_*`` module-level constants.
        :ivar capsule_image_size: Total size of the capsule image,
                                  including the header itself.
    """

    capsule_guid: uuid.UUID
    header_size: int
    flags: int
    capsule_image_size: int

    CAPSULE_FLAGS_PERSIST_ACROSS_RESET = CAPSULE_FLAGS_PERSIST_ACROSS_RESET
    CAPSULE_FLAGS_POPULATE_SYSTEM_TABLE = CAPSULE_FLAGS_POPULATE_SYSTEM_TABLE
    CAPSULE_FLAGS_INITIATE_RESET = CAPSULE_FLAGS_INITIATE_RESET

    def to_bytes(self) -> bytes:
        """
            Serialize the capsule header as a 32-byte UEFI on-wire buffer.

            :returns: The header bytes (length == 32).
            :raises: UefiCapsuleError
        """
        if not isinstance(self.capsule_guid, uuid.UUID):
            err_msg_lines = [
                "CapsuleHeader.to_bytes: 'capsule_guid' must be a uuid.UUID.",
                "GOT: value={!r} type={}".format(self.capsule_guid, type(self.capsule_guid).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from None

        if self.header_size < CAPSULE_HEADER_SIZE or self.header_size > _MAX_U32:
            err_msg_lines = [
                "CapsuleHeader.to_bytes: 'header_size' is out of range for UINT32.",
                "EXPECTED: {} <= value <= 0x{:08X}".format(CAPSULE_HEADER_SIZE, _MAX_U32),
                "OBSERVED: {}".format(self.header_size),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from None

        if self.flags < 0 or self.flags > _MAX_U32:
            err_msg_lines = [
                "CapsuleHeader.to_bytes: 'flags' is out of range for UINT32.",
                "EXPECTED: 0 <= value <= 0x{:08X}".format(_MAX_U32),
                "OBSERVED: 0x{:X}".format(self.flags),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from None

        if self.capsule_image_size < 0 or self.capsule_image_size > _MAX_U32:
            err_msg_lines = [
                "CapsuleHeader.to_bytes: 'capsule_image_size' is out of range for UINT32.",
                "EXPECTED: 0 <= value <= 0x{:08X}".format(_MAX_U32),
                "OBSERVED: {}".format(self.capsule_image_size),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from None

        guid_bytes = guid_to_bytes(self.capsule_guid)
        header_size_bytes = pack_u32_le(value=self.header_size)
        flags_bytes = pack_u32_le(value=self.flags)
        image_size_bytes = pack_u32_le(value=self.capsule_image_size)
        reserved_bytes = pack_u32_le(value=0)

        rtnval = guid_bytes + header_size_bytes + flags_bytes + image_size_bytes + reserved_bytes
        return rtnval

    @classmethod
    def from_bytes(cls, data: bytes) -> "CapsuleHeader":
        """
            Decode a 32-byte UEFI on-wire buffer into a
            :class:`CapsuleHeader`.

            :param data: A :class:`bytes`-like buffer whose length is at
                         least :data:`CAPSULE_HEADER_SIZE`.

            :returns: The decoded :class:`CapsuleHeader`.
            :raises: UefiCapsuleError
        """
        if not isinstance(data, (bytes, bytearray, memoryview)):
            err_msg_lines = [
                "CapsuleHeader.from_bytes: 'data' must be a bytes-like object.",
                "GOT: value={!r} type={}".format(data, type(data).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from None

        buffer = bytes(data)
        observed_length = len(buffer)
        if observed_length < CAPSULE_HEADER_SIZE:
            err_msg_lines = [
                "CapsuleHeader.from_bytes: buffer is shorter than a capsule header.",
                "EXPECTED: length >= {}".format(CAPSULE_HEADER_SIZE),
                "OBSERVED: length={}".format(observed_length),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from None

        guid_slice = buffer[0:16]
        header_size_slice = buffer[16:20]
        flags_slice = buffer[20:24]
        image_size_slice = buffer[24:28]

        try:
            capsule_guid = guid_from_bytes(b=guid_slice)
        except (TypeError, ValueError) as gerr:
            err_msg_lines = [
                "CapsuleHeader.from_bytes: GUID slice is not a valid UEFI GUID buffer.",
                "ERROR: {!r}".format(gerr),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from gerr

        header_size = unpack_u32_le(buf=header_size_slice)
        flags = unpack_u32_le(buf=flags_slice)
        capsule_image_size = unpack_u32_le(buf=image_size_slice)

        rtnval = cls(
            capsule_guid=capsule_guid,
            header_size=header_size,
            flags=flags,
            capsule_image_size=capsule_image_size,
        )
        return rtnval


# ---------------------------------------------------------------------------
# FmpCapsuleHeader
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FmpCapsuleHeader:
    """
        EFI_FIRMWARE_MANAGEMENT_CAPSULE_HEADER per UEFI §23.3.

        Layout:

        ============  ====  =================================
         Offset        Size Field
        ============  ====  =================================
         0x00           4   Version (UINT32, little-endian)
         0x04           2   EmbeddedDriverCount (UINT16)
         0x06           2   PayloadItemCount (UINT16)
         0x08           4*N ItemOffsetList[N] (UINT32[] LE)
        ============  ====  =================================

        ``N`` is ``embedded_driver_count + payload_item_count``.  The
        offsets are measured from the start of this header.

        :ivar version: FMP capsule header version; must be 1 to match
                       §23.3.
        :ivar embedded_driver_count: Number of embedded driver entries.
        :ivar payload_item_count: Number of payload item entries.
        :ivar item_offsets: Offsets for each item in the on-wire order
                            ``(drivers..., payloads...)``.
    """

    version: int
    embedded_driver_count: int
    payload_item_count: int
    item_offsets: Tuple[int, ...]

    def to_bytes(self) -> bytes:
        """
            Serialize the FMP capsule header and its offset array.

            :returns: The serialized bytes.
            :raises: UefiCapsuleError
        """
        if self.version < 0 or self.version > _MAX_U32:
            err_msg_lines = [
                "FmpCapsuleHeader.to_bytes: 'version' is out of range for UINT32.",
                "OBSERVED: {}".format(self.version),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from None

        if self.embedded_driver_count < 0 or self.embedded_driver_count > _MAX_U16:
            err_msg_lines = [
                "FmpCapsuleHeader.to_bytes: 'embedded_driver_count' is out of range for UINT16.",
                "OBSERVED: {}".format(self.embedded_driver_count),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from None

        if self.payload_item_count < 0 or self.payload_item_count > _MAX_U16:
            err_msg_lines = [
                "FmpCapsuleHeader.to_bytes: 'payload_item_count' is out of range for UINT16.",
                "OBSERVED: {}".format(self.payload_item_count),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from None

        declared_total = self.embedded_driver_count + self.payload_item_count
        observed_total = len(self.item_offsets)
        if declared_total != observed_total:
            err_msg_lines = [
                "FmpCapsuleHeader.to_bytes: len(item_offsets) disagrees with declared counts.",
                "EMBEDDED_DRIVER_COUNT: {}".format(self.embedded_driver_count),
                "PAYLOAD_ITEM_COUNT: {}".format(self.payload_item_count),
                "DECLARED_TOTAL: {}".format(declared_total),
                "OBSERVED_TOTAL: {}".format(observed_total),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from None

        for index, offset in enumerate(self.item_offsets):
            if offset < 0 or offset > _MAX_U32:
                err_msg_lines = [
                    "FmpCapsuleHeader.to_bytes: item offset is out of range for UINT32.",
                    "INDEX: {}".format(index),
                    "OBSERVED: {}".format(offset),
                ]
                err_msg = os.linesep.join(err_msg_lines)
                raise UefiCapsuleError(err_msg) from None

        version_bytes = pack_u32_le(value=self.version)
        edc_bytes = pack_u16_le(value=self.embedded_driver_count)
        pic_bytes = pack_u16_le(value=self.payload_item_count)

        offsets_bytes = b""
        for offset in self.item_offsets:
            offsets_bytes = offsets_bytes + pack_u32_le(value=offset)

        rtnval = version_bytes + edc_bytes + pic_bytes + offsets_bytes
        return rtnval

    @classmethod
    def from_bytes(cls, data: bytes) -> "FmpCapsuleHeader":
        """
            Decode an FMP capsule header (and its trailing offset array)
            from a byte buffer.

            :param data: A bytes-like buffer.

            :returns: The decoded :class:`FmpCapsuleHeader`.
            :raises: UefiCapsuleError
        """
        if not isinstance(data, (bytes, bytearray, memoryview)):
            err_msg_lines = [
                "FmpCapsuleHeader.from_bytes: 'data' must be a bytes-like object.",
                "GOT: value={!r} type={}".format(data, type(data).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from None

        buffer = bytes(data)
        if len(buffer) < FMP_CAPSULE_HEADER_FIXED_SIZE:
            err_msg_lines = [
                "FmpCapsuleHeader.from_bytes: buffer is shorter than the fixed FMP header.",
                "EXPECTED: length >= {}".format(FMP_CAPSULE_HEADER_FIXED_SIZE),
                "OBSERVED: length={}".format(len(buffer)),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from None

        version = unpack_u32_le(buf=buffer[0:4])
        embedded_driver_count = unpack_u16_le(buf=buffer[4:6])
        payload_item_count = unpack_u16_le(buf=buffer[6:8])

        total_items = embedded_driver_count + payload_item_count
        array_bytes_needed = total_items * 4
        if len(buffer) < FMP_CAPSULE_HEADER_FIXED_SIZE + array_bytes_needed:
            err_msg_lines = [
                "FmpCapsuleHeader.from_bytes: buffer is shorter than the declared offset array.",
                "EXPECTED: length >= {}".format(FMP_CAPSULE_HEADER_FIXED_SIZE + array_bytes_needed),
                "OBSERVED: length={}".format(len(buffer)),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from None

        offsets_list: List[int] = []
        for index in range(total_items):
            start = FMP_CAPSULE_HEADER_FIXED_SIZE + (index * 4)
            end = start + 4
            entry = unpack_u32_le(buf=buffer[start:end])
            offsets_list.append(entry)

        rtnval = cls(
            version=version,
            embedded_driver_count=embedded_driver_count,
            payload_item_count=payload_item_count,
            item_offsets=tuple(offsets_list),
        )
        return rtnval


# ---------------------------------------------------------------------------
# FmpPayloadHeader
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FmpPayloadHeader:
    """
        FMP payload header that precedes each vendor payload image.
        The on-wire layout is:

        ============  ====  =================================
         Offset        Size Field
        ============  ====  =================================
         0x00           4   Signature (ASCII "MSS1")
         0x04           4   HeaderSize (UINT32 LE)
         0x08           4   FwVersion (UINT32 LE)
         0x0C           4   LowestSupportedVersion (UINT32 LE)
        ============  ====  =================================

        :ivar signature: Four-byte ASCII signature; must equal ``MSS1``.
        :ivar header_size: Header size in bytes.
        :ivar fw_version: Firmware version the payload represents.
        :ivar lowest_supported_version: Minimum firmware version that
                                        may be overwritten by this
                                        payload.
    """

    signature: bytes
    header_size: int
    fw_version: int
    lowest_supported_version: int

    def to_bytes(self) -> bytes:
        """
            Serialize the FMP payload header into its 16-byte on-wire form.

            :returns: The serialized bytes.
            :raises: UefiCapsuleError
        """
        if not isinstance(self.signature, (bytes, bytearray, memoryview)):
            err_msg_lines = [
                "FmpPayloadHeader.to_bytes: 'signature' must be a bytes-like object.",
                "GOT: value={!r} type={}".format(self.signature, type(self.signature).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from None

        signature_bytes = bytes(self.signature)
        if len(signature_bytes) != 4:
            err_msg_lines = [
                "FmpPayloadHeader.to_bytes: 'signature' must be exactly 4 bytes.",
                "OBSERVED: length={}".format(len(signature_bytes)),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from None

        if self.header_size < FMP_PAYLOAD_HEADER_SIZE or self.header_size > _MAX_U32:
            err_msg_lines = [
                "FmpPayloadHeader.to_bytes: 'header_size' is out of range.",
                "EXPECTED: {} <= value <= 0x{:08X}".format(FMP_PAYLOAD_HEADER_SIZE, _MAX_U32),
                "OBSERVED: {}".format(self.header_size),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from None

        if self.fw_version < 0 or self.fw_version > _MAX_U32:
            err_msg_lines = [
                "FmpPayloadHeader.to_bytes: 'fw_version' is out of range for UINT32.",
                "OBSERVED: {}".format(self.fw_version),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from None

        if self.lowest_supported_version < 0 or self.lowest_supported_version > _MAX_U32:
            err_msg_lines = [
                "FmpPayloadHeader.to_bytes: 'lowest_supported_version' is out of range for UINT32.",
                "OBSERVED: {}".format(self.lowest_supported_version),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from None

        header_size_bytes = pack_u32_le(value=self.header_size)
        fw_version_bytes = pack_u32_le(value=self.fw_version)
        lsv_bytes = pack_u32_le(value=self.lowest_supported_version)

        rtnval = signature_bytes + header_size_bytes + fw_version_bytes + lsv_bytes
        return rtnval

    @classmethod
    def from_bytes(cls, data: bytes) -> "FmpPayloadHeader":
        """
            Decode a 16-byte buffer into an :class:`FmpPayloadHeader`.

            :param data: A bytes-like buffer at least
                         :data:`FMP_PAYLOAD_HEADER_SIZE` bytes long.

            :returns: The decoded header.
            :raises: UefiCapsuleError
        """
        if not isinstance(data, (bytes, bytearray, memoryview)):
            err_msg_lines = [
                "FmpPayloadHeader.from_bytes: 'data' must be a bytes-like object.",
                "GOT: value={!r} type={}".format(data, type(data).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from None

        buffer = bytes(data)
        if len(buffer) < FMP_PAYLOAD_HEADER_SIZE:
            err_msg_lines = [
                "FmpPayloadHeader.from_bytes: buffer is shorter than the FMP payload header.",
                "EXPECTED: length >= {}".format(FMP_PAYLOAD_HEADER_SIZE),
                "OBSERVED: length={}".format(len(buffer)),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from None

        signature = buffer[0:4]
        header_size = unpack_u32_le(buf=buffer[4:8])
        fw_version = unpack_u32_le(buf=buffer[8:12])
        lowest_supported_version = unpack_u32_le(buf=buffer[12:16])

        rtnval = cls(
            signature=signature,
            header_size=header_size,
            fw_version=fw_version,
            lowest_supported_version=lowest_supported_version,
        )
        return rtnval


# ---------------------------------------------------------------------------
# Capsule aggregate
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Capsule:
    """
        A fully-assembled UEFI capsule: the :class:`CapsuleHeader`
        concatenated with the body bytes that follow.  The body is an
        opaque blob from the capsule framework's perspective — it may
        be an FMP-wrapped payload, a vendor capsule body, or any
        other shape the target firmware understands.

        :ivar header: The decoded :class:`CapsuleHeader`.
        :ivar body: The bytes that follow the header on the wire.
    """

    header: CapsuleHeader
    body: bytes

    @classmethod
    def from_bytes(cls, data: bytes) -> "Capsule":
        """
            Parse a complete capsule buffer.

            :param data: Bytes-like input whose length is at least
                         :data:`CAPSULE_HEADER_SIZE`.

            :returns: The decoded :class:`Capsule`.
            :raises: UefiCapsuleError
        """
        if not isinstance(data, (bytes, bytearray, memoryview)):
            err_msg_lines = [
                "Capsule.from_bytes: 'data' must be a bytes-like object.",
                "GOT: value={!r} type={}".format(data, type(data).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from None

        buffer = bytes(data)
        observed_length = len(buffer)

        header = CapsuleHeader.from_bytes(data=buffer[0:CAPSULE_HEADER_SIZE])

        if header.capsule_image_size != observed_length:
            err_msg_lines = [
                "Capsule.from_bytes: 'capsule_image_size' disagrees with buffer length.",
                "HEADER_CAPSULE_IMAGE_SIZE: {}".format(header.capsule_image_size),
                "OBSERVED_LENGTH: {}".format(observed_length),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from None

        body = buffer[header.header_size:]

        rtnval = cls(header=header, body=body)
        return rtnval

    def to_bytes(self) -> bytes:
        """
            Re-serialize the capsule to its on-wire form.

            :returns: The concatenation of the header bytes and the body.
            :raises: UefiCapsuleError
        """
        header_bytes = self.header.to_bytes()

        if not isinstance(self.body, (bytes, bytearray, memoryview)):
            err_msg_lines = [
                "Capsule.to_bytes: 'body' must be a bytes-like object.",
                "GOT: value={!r} type={}".format(self.body, type(self.body).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from None

        body_bytes = bytes(self.body)

        # Header padding preserves the declared header_size when a
        # caller-supplied vendor payload precedes the body.  In the
        # common case header.header_size == CAPSULE_HEADER_SIZE and the
        # padding is empty.
        padding_length = self.header.header_size - CAPSULE_HEADER_SIZE
        if padding_length < 0:
            err_msg_lines = [
                "Capsule.to_bytes: 'header.header_size' is smaller than CAPSULE_HEADER_SIZE.",
                "HEADER_SIZE: {}".format(self.header.header_size),
                "REQUIRED: >= {}".format(CAPSULE_HEADER_SIZE),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from None

        padding_bytes = b"\x00" * padding_length

        rtnval = header_bytes + padding_bytes + body_bytes
        return rtnval

    @property
    def sha256(self) -> str:
        """
            SHA-256 of the on-wire capsule bytes, rendered as a lowercase
            hex string.  Used for log correlation and report lookup.

            :returns: The 64-character hex digest.
        """
        hasher = hashlib.sha256()
        hasher.update(self.to_bytes())
        rtnval = hasher.hexdigest()
        return rtnval


# ---------------------------------------------------------------------------
# CapsuleBuilder
# ---------------------------------------------------------------------------

class CapsuleBuilder:
    """
        Assembles an :class:`EFI_CAPSULE_HEADER` with an optional FMP
        payload wrapping per §23.3.  The builder is synchronous and
        performs no I/O — it produces a :class:`Capsule` dataclass whose
        bytes the caller passes to :class:`CapsuleDelivery`.

        :param capsule_guid: The capsule identifier GUID placed in the
                             header.  For FMP-wrapped capsules this is
                             typically
                             ``EFI_FIRMWARE_MANAGEMENT_CAPSULE_ID_GUID``.
        :param flags: Capsule-level flags bitfield.  See the
                      ``CAPSULE_FLAGS_*`` module constants.
    """

    def __init__(self, capsule_guid: uuid.UUID, flags: int = 0) -> None:
        """
            Initialize the builder.

            :param capsule_guid: The capsule identifier GUID.
            :param flags: The capsule-level flags bitfield.

            :raises: UefiCapsuleError
        """
        if not isinstance(capsule_guid, uuid.UUID):
            err_msg_lines = [
                "CapsuleBuilder: 'capsule_guid' must be a uuid.UUID.",
                "GOT: value={!r} type={}".format(capsule_guid, type(capsule_guid).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from None

        if not isinstance(flags, int) or isinstance(flags, bool):
            err_msg_lines = [
                "CapsuleBuilder: 'flags' must be an int.",
                "GOT: value={!r} type={}".format(flags, type(flags).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from None

        if flags < 0 or flags > _MAX_U32:
            err_msg_lines = [
                "CapsuleBuilder: 'flags' is out of range for UINT32.",
                "OBSERVED: 0x{:X}".format(flags),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from None

        self._capsule_guid: uuid.UUID = capsule_guid
        self._flags: int = flags
        self._fmp_payload: Optional[bytes] = None
        self._fmp_fw_version: int = 0
        self._fmp_lowest_supported: int = 0
        self._raw_body: Optional[bytes] = None
        return

    def with_raw_body(self, body: bytes) -> "CapsuleBuilder":
        """
            Attach a raw body that is placed directly after the capsule
            header with no FMP wrapping.  This is mutually exclusive with
            :meth:`with_fmp_payload`.

            :param body: The raw body bytes.

            :returns: ``self``, for fluent chaining.
            :raises: UefiCapsuleError
        """
        if not isinstance(body, (bytes, bytearray, memoryview)):
            err_msg_lines = [
                "CapsuleBuilder.with_raw_body: 'body' must be a bytes-like object.",
                "GOT: value={!r} type={}".format(body, type(body).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from None

        if self._fmp_payload is not None:
            err_msg_lines = [
                "CapsuleBuilder.with_raw_body: FMP payload already configured.",
                "HINT: construct a fresh CapsuleBuilder when switching between raw and FMP modes.",
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from None

        self._raw_body = bytes(body)
        rtnval = self
        return rtnval

    def with_fmp_payload(self, fw_version: int, lowest_supported: int,
                               payload: bytes) -> "CapsuleBuilder":
        """
            Wrap ``payload`` in the FMP capsule/payload envelope per
            §23.3 so that the assembled capsule is consumable by the
            target's :class:`EFI_FIRMWARE_MANAGEMENT_PROTOCOL`.

            The resulting body is laid out as:

            * :class:`FmpCapsuleHeader` (version=1, no drivers, one
              payload item);
            * One item offset pointing just past the FMP capsule
              header;
            * :class:`FmpPayloadHeader` (``MSS1``, version, lowest
              supported version);
            * Raw ``payload`` bytes.

            :param fw_version: The firmware version the payload
                               represents.  UINT32.
            :param lowest_supported: Minimum firmware version the
                                     payload may overwrite.  UINT32.
            :param payload: Raw vendor payload bytes.

            :returns: ``self``, for fluent chaining.
            :raises: UefiCapsuleError
        """
        if not isinstance(fw_version, int) or isinstance(fw_version, bool):
            err_msg_lines = [
                "CapsuleBuilder.with_fmp_payload: 'fw_version' must be an int.",
                "GOT: value={!r} type={}".format(fw_version, type(fw_version).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from None

        if not isinstance(lowest_supported, int) or isinstance(lowest_supported, bool):
            err_msg_lines = [
                "CapsuleBuilder.with_fmp_payload: 'lowest_supported' must be an int.",
                "GOT: value={!r} type={}".format(lowest_supported, type(lowest_supported).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from None

        if not isinstance(payload, (bytes, bytearray, memoryview)):
            err_msg_lines = [
                "CapsuleBuilder.with_fmp_payload: 'payload' must be a bytes-like object.",
                "GOT: value={!r} type={}".format(payload, type(payload).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from None

        if fw_version < 0 or fw_version > _MAX_U32:
            err_msg_lines = [
                "CapsuleBuilder.with_fmp_payload: 'fw_version' is out of range for UINT32.",
                "OBSERVED: {}".format(fw_version),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from None

        if lowest_supported < 0 or lowest_supported > _MAX_U32:
            err_msg_lines = [
                "CapsuleBuilder.with_fmp_payload: 'lowest_supported' is out of range for UINT32.",
                "OBSERVED: {}".format(lowest_supported),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from None

        if self._raw_body is not None:
            err_msg_lines = [
                "CapsuleBuilder.with_fmp_payload: raw body already configured.",
                "HINT: construct a fresh CapsuleBuilder when switching between raw and FMP modes.",
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from None

        self._fmp_fw_version = fw_version
        self._fmp_lowest_supported = lowest_supported
        self._fmp_payload = bytes(payload)

        rtnval = self
        return rtnval

    def _render_body(self) -> bytes:
        """
            Resolve the body bytes from the configured source.  When no
            source is configured the body is empty.

            :returns: The serialized body.
        """
        if self._fmp_payload is not None:
            fmp_fixed_size = FMP_CAPSULE_HEADER_FIXED_SIZE + 4  # one offset entry
            item_offset = fmp_fixed_size

            fmp_header = FmpCapsuleHeader(
                version=FMP_PAYLOAD_VERSION,
                embedded_driver_count=0,
                payload_item_count=1,
                item_offsets=(item_offset,),
            )
            payload_header = FmpPayloadHeader(
                signature=FMP_PAYLOAD_SIGNATURE,
                header_size=FMP_PAYLOAD_HEADER_SIZE,
                fw_version=self._fmp_fw_version,
                lowest_supported_version=self._fmp_lowest_supported,
            )

            fmp_header_bytes = fmp_header.to_bytes()
            payload_header_bytes = payload_header.to_bytes()

            rtnval = fmp_header_bytes + payload_header_bytes + self._fmp_payload
            return rtnval

        if self._raw_body is not None:
            rtnval = self._raw_body
            return rtnval

        rtnval = b""
        return rtnval

    def build(self) -> Capsule:
        """
            Finalize the builder state and emit a :class:`Capsule`.

            :returns: A :class:`Capsule` whose :meth:`Capsule.to_bytes`
                      reproduces the on-wire capsule buffer byte-for-byte.
            :raises: UefiCapsuleError
        """
        body_bytes = self._render_body()
        computed_total = CAPSULE_HEADER_SIZE + len(body_bytes)

        if computed_total > _MAX_U32:
            err_msg_lines = [
                "CapsuleBuilder.build: computed capsule size exceeds UINT32 maximum.",
                "COMPUTED_TOTAL: {}".format(computed_total),
                "UINT32_MAX: 0x{:08X}".format(_MAX_U32),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from None

        header = CapsuleHeader(
            capsule_guid=self._capsule_guid,
            header_size=CAPSULE_HEADER_SIZE,
            flags=self._flags,
            capsule_image_size=computed_total,
        )

        rtnval = Capsule(header=header, body=body_bytes)
        return rtnval


# ---------------------------------------------------------------------------
# CapsuleReportEntry
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CapsuleReportEntry:
    """
        Decoded record from an ``EFI_CAPSULE_REPORT_GUID`` variable per
        UEFI §8.5.6.  The canonical variable name is ``Capsule####``.

        :ivar capsule_guid: The GUID of the capsule the record describes.
        :ivar capsule_status: The raw EFI_STATUS the firmware reported.
                              Zero indicates success.
        :ivar capsule_processed: ``True`` when the firmware reports that
                                 the capsule was processed.
        :ivar last_attempt_version: The FMP ``LastAttemptVersion``
                                    reported alongside the status.
        :ivar last_attempt_status: The FMP ``LastAttemptStatus``.
    """

    capsule_guid: uuid.UUID
    capsule_status: int
    capsule_processed: bool
    last_attempt_version: int
    last_attempt_status: int

    @property
    def is_success(self) -> bool:
        """
            Convenience predicate; mirrors the design contract in
            ``design.md``.

            :returns: ``True`` iff the capsule status is zero.
        """
        rtnval = self.capsule_status == 0
        return rtnval


# ---------------------------------------------------------------------------
# CapsuleReport
# ---------------------------------------------------------------------------

class CapsuleReport:
    """
        Reads per-capsule outcome records from the
        :data:`EFI_CAPSULE_REPORT_GUID` variable namespace.  The reader
        is deliberately thin: it dispatches through a
        :class:`VariableStore` so the same code path works across the
        shell, host-OS, and Redfish backends.

        :param variable_store: The :class:`VariableStore` used to
                               enumerate and read the report variables.
    """

    def __init__(self, variable_store: VariableStore) -> None:
        """
            Initialize the report reader.

            :param variable_store: The :class:`VariableStore` driving
                                   every variable access.

            :raises: UefiCapsuleError
        """
        if variable_store is None:
            err_msg_lines = [
                "CapsuleReport: 'variable_store' must not be None.",
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from None

        self._variables: VariableStore = variable_store
        return

    def list_reports(self, aspects: Optional[UefiCmdAspects] = None) -> Tuple[CapsuleReportEntry, ...]:
        """
            Enumerate every ``Capsule####`` variable scoped to
            :data:`EFI_CAPSULE_REPORT_GUID`, decode each record, and
            return the set sorted by variable name.

            :param aspects: Optional per-call aspects override.  The
                            underlying :class:`VariableStore` handles the
                            ``aspects=None`` resolution path.

            :returns: A tuple of :class:`CapsuleReportEntry` records in
                      variable-name order.  The tuple is empty when no
                      report variables are present.
            :raises: UefiCapsuleError
        """
        matching_records: List[Tuple[str, CapsuleReportEntry]] = []

        try:
            enumeration = self._variables.enumerate_variables(aspects=aspects)
            for variable_name, vendor_guid in enumeration:
                if vendor_guid != EFI_CAPSULE_REPORT_GUID:
                    continue
                if not variable_name.startswith("Capsule"):
                    continue

                record_variable = self._variables.get_variable(
                    name=variable_name,
                    vendor_guid=EFI_CAPSULE_REPORT_GUID,
                    aspects=aspects,
                )
                decoded = self._decode_record(
                    name=variable_name,
                    payload=record_variable.data,
                )
                matching_records.append((variable_name, decoded))
        except UefiCapsuleError:
            raise
        except BaseException as rerr:
            err_msg_lines = [
                "CapsuleReport.list_reports: failed to enumerate capsule report variables.",
                "EXCEPTION: {!r}".format(rerr),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from rerr

        matching_records.sort(key=lambda item: item[0])
        rtnval = tuple(record for _, record in matching_records)
        return rtnval

    @staticmethod
    def _decode_record(name: str, payload: bytes) -> CapsuleReportEntry:
        """
            Decode a single ``EFI_CAPSULE_RESULT_VARIABLE_HEADER`` buffer
            per §8.5.6.

            Layout decoded:

            ============  ====  =================================
             Offset        Size Field
            ============  ====  =================================
             0x00           4   VariableTotalSize (UINT32 LE)
             0x04           4   Reserved
             0x08          16   CapsuleGuid (mixed-endian)
             0x18           8   CapsuleProcessed (BOOLEAN padded)
             0x20           8   LastAttemptVersion (UINT32 LE,
                                padded to 8 bytes to keep optional
                                auxiliary fields aligned)
             0x28           8   LastAttemptStatus (UINT32 LE)
            ============  ====  =================================

            :param name: The variable name (used only in error diagnostics).
            :param payload: The raw variable payload bytes.

            :returns: The decoded record.
            :raises: UefiCapsuleError
        """
        if not isinstance(payload, (bytes, bytearray, memoryview)):
            err_msg_lines = [
                "CapsuleReport: variable payload must be a bytes-like object.",
                "NAME: {}".format(name),
                "GOT: value={!r} type={}".format(payload, type(payload).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from None

        buffer = bytes(payload)
        observed_length = len(buffer)
        minimum_length = 0x30
        if observed_length < minimum_length:
            err_msg_lines = [
                "CapsuleReport: payload is shorter than EFI_CAPSULE_RESULT_VARIABLE_HEADER.",
                "NAME: {}".format(name),
                "EXPECTED: length >= {}".format(minimum_length),
                "OBSERVED: length={}".format(observed_length),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from None

        variable_total_size = unpack_u32_le(buf=buffer[0:4])
        if variable_total_size > observed_length:
            err_msg_lines = [
                "CapsuleReport: 'VariableTotalSize' exceeds the payload length.",
                "NAME: {}".format(name),
                "VARIABLE_TOTAL_SIZE: {}".format(variable_total_size),
                "OBSERVED: length={}".format(observed_length),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from None

        # Reserved word at offset 0x04 is ignored.
        try:
            capsule_guid = guid_from_bytes(b=buffer[0x08:0x18])
        except (TypeError, ValueError) as gerr:
            err_msg_lines = [
                "CapsuleReport: payload contained an invalid capsule GUID.",
                "NAME: {}".format(name),
                "ERROR: {!r}".format(gerr),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from gerr

        processed_field = unpack_u64_le(buf=buffer[0x18:0x20])
        last_attempt_version_field = unpack_u64_le(buf=buffer[0x20:0x28])
        last_attempt_status_field = unpack_u64_le(buf=buffer[0x28:0x30])

        capsule_status = int(last_attempt_status_field & _MAX_U32)
        last_attempt_version = int(last_attempt_version_field & _MAX_U32)
        last_attempt_status = capsule_status

        rtnval = CapsuleReportEntry(
            capsule_guid=capsule_guid,
            capsule_status=capsule_status,
            capsule_processed=(processed_field != 0),
            last_attempt_version=last_attempt_version,
            last_attempt_status=last_attempt_status,
        )
        return rtnval


# ---------------------------------------------------------------------------
# CapsuleDelivery
# ---------------------------------------------------------------------------

class CapsuleDelivery:
    """
        Deliver a :class:`Capsule` through the :class:`UefiAgent`'s
        active transport.

        Routing table:

        * ``ssh-host`` — write the capsule bytes to
          ``/dev/efi_capsule_loader`` (the Linux kernel capsule
          character device).  When the device is absent the backend
          falls back to ``fwupdmgr apply`` with a temp file.
        * ``redfish`` — POST a multipart payload to
          ``UpdateService.SimpleUpdate`` (the
          ``update-multipart`` subresource).
        * ``serial`` / ``ipmi-sol`` — raise :class:`UefiCapsuleError`.
          Pre-boot capsule delivery through the UEFI Shell is out of
          scope for v1 and callers who need that path should drop the
          file on the ESP and rely on
          ``EFI_OS_INDICATIONS_FILE_CAPSULE_DELIVERY_SUPPORTED``.

        After delivery the caller may invoke :meth:`wait_for_report` to
        poll the post-boot :data:`EFI_CAPSULE_REPORT_GUID` variable for
        the outcome.

        :param agent: The :class:`UefiAgent` whose transport routes the
                      delivery.
        :param variable_store: The :class:`VariableStore` used by
                               :meth:`wait_for_report` to read the
                               post-boot record.
        :param aspects: The default :class:`UefiCmdAspects` used when a
                        public method is called with ``aspects=None``.
    """

    def __init__(self, agent: Any,
                       variable_store: VariableStore,
                       aspects: UefiCmdAspects = DEFAULT_CMD_ASPECTS) -> None:
        """
            Initialize the delivery facade.

            :param agent: The :class:`UefiAgent`.
            :param variable_store: The :class:`VariableStore`.
            :param aspects: The default aspects instance.

            :raises: UefiCapsuleError
        """
        if agent is None:
            err_msg_lines = [
                "CapsuleDelivery: 'agent' must not be None.",
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from None

        if variable_store is None:
            err_msg_lines = [
                "CapsuleDelivery: 'variable_store' must not be None.",
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from None

        self._agent: Any = agent
        self._variables: VariableStore = variable_store
        self._aspects: UefiCmdAspects = aspects
        self._report: CapsuleReport = CapsuleReport(variable_store=variable_store)
        return

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------

    def deliver(self, capsule: Capsule,
                      aspects: Optional[UefiCmdAspects] = None) -> None:
        """
            Dispatch the capsule through the transport associated with
            :attr:`_agent`.

            :param capsule: The capsule to deliver.
            :param aspects: Optional per-call aspects override.

            :raises: UefiCapsuleError, ConfigurationError
        """
        if not isinstance(capsule, Capsule):
            err_msg_lines = [
                "CapsuleDelivery.deliver: 'capsule' must be a Capsule.",
                "GOT: value={!r} type={}".format(capsule, type(capsule).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from None

        effective = resolve_aspects(passed=aspects, instance_default=self._aspects)

        transport_kind = getattr(self._agent, "transport_kind", None)

        if transport_kind == "ssh-host":
            self._deliver_ssh_host(capsule=capsule, aspects=effective)
            return

        if transport_kind == "redfish":
            self._deliver_redfish(capsule=capsule, aspects=effective)
            return

        if transport_kind == "serial" or transport_kind == "ipmi-sol":
            err_msg_lines = [
                "CapsuleDelivery.deliver: pre-boot capsule delivery via shell is out of scope for v1.",
                "TRANSPORT_KIND: {!r}".format(transport_kind),
                "HINT: use the mass-storage (ESP) drop with OsIndications, or switch to an OS-runtime transport.",
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from None

        err_msg_lines = [
            "CapsuleDelivery.deliver: unknown or unsupported 'transport_kind'.",
            "OBSERVED: transport_kind={!r}".format(transport_kind),
            "ALLOWED: 'ssh-host', 'redfish'",
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiCapsuleError(err_msg) from None

    def wait_for_report(self, capsule: Capsule,
                              aspects: Optional[UefiCmdAspects] = None) -> CapsuleReportEntry:
        """
            Poll the :data:`EFI_CAPSULE_REPORT_GUID` variable namespace
            for a record whose :attr:`CapsuleReportEntry.capsule_guid`
            matches ``capsule.header.capsule_guid``.

            :param capsule: The capsule to match.
            :param aspects: Optional per-call aspects override.

            :returns: The most recent matching :class:`CapsuleReportEntry`
                      (numerically largest variable-name suffix wins).
            :raises: UefiCapsuleError
        """
        if not isinstance(capsule, Capsule):
            err_msg_lines = [
                "CapsuleDelivery.wait_for_report: 'capsule' must be a Capsule.",
                "GOT: value={!r} type={}".format(capsule, type(capsule).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from None

        effective = resolve_aspects(passed=aspects, instance_default=self._aspects)

        # We consume only the action pattern to decide whether to
        # raise immediately on an empty list.  Retry-polling is the
        # responsibility of 6-capsule-update-workflow.
        pattern = effective.action_pattern

        records = self._report.list_reports(aspects=effective)
        target_guid = capsule.header.capsule_guid
        matches = [entry for entry in records if entry.capsule_guid == target_guid]

        if len(matches) == 0:
            err_msg_lines = [
                "CapsuleDelivery.wait_for_report: no capsule report record was found for the target GUID.",
                "CAPSULE_GUID: {}".format(target_guid),
                "OBSERVED_RECORD_COUNT: {}".format(len(records)),
                "ACTION_PATTERN: {!r}".format(pattern),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from None

        rtnval = matches[-1]
        return rtnval

    # ------------------------------------------------------------------
    # Backend dispatchers
    # ------------------------------------------------------------------

    def _deliver_ssh_host(self, capsule: Capsule,
                                aspects: UefiCmdAspects) -> None:
        """
            Write the capsule bytes to ``/dev/efi_capsule_loader`` via
            the agent's SSH-host transport, falling back to
            ``fwupdmgr apply`` when the device is absent.

            The file is staged through a temp path whose contents are
            shipped as a base64-encoded payload so that the shell layer
            does not need a separate binary upload channel.

            :param capsule: The capsule to deliver.
            :param aspects: The resolved aspects for this call.

            :raises: UefiCapsuleError
        """
        capsule_bytes = capsule.to_bytes()
        encoded = _encode_base64(data=capsule_bytes)
        temp_path = "/tmp/codecraftai-capsule-{}.cap".format(capsule.header.capsule_guid)

        probe_status, probe_stdout, probe_stderr = self._agent.run_cmd(
            command="test -c /dev/efi_capsule_loader",
            aspects=aspects,
        )

        stage_command = "printf '%s' '{enc}' | base64 -d > {path}".format(
            enc=encoded, path=temp_path,
        )
        stage_status, stage_stdout, stage_stderr = self._agent.run_cmd(
            command=stage_command,
            aspects=aspects,
        )
        if stage_status != 0:
            err_msg_lines = [
                "CapsuleDelivery: failed to stage capsule on the target.",
                "COMMAND: {}".format(stage_command),
                "STATUS: {}".format(stage_status),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from None

        if probe_status == 0:
            apply_command = "dd if={path} of=/dev/efi_capsule_loader bs={size} count=1".format(
                path=temp_path, size=len(capsule_bytes),
            )
        else:
            apply_command = "fwupdmgr apply {path} --allow-reinstall".format(path=temp_path)

        apply_status, apply_stdout, apply_stderr = self._agent.run_cmd(
            command=apply_command,
            aspects=aspects,
        )

        if apply_status != 0:
            err_msg_lines = [
                "CapsuleDelivery: capsule apply command returned a non-zero status.",
                "TRANSPORT: ssh-host",
                "COMMAND: {}".format(apply_command),
                "STATUS: {}".format(apply_status),
                "CAPSULE_GUID: {}".format(capsule.header.capsule_guid),
                "CAPSULE_SIZE: {}".format(len(capsule_bytes)),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from None

        return

    def _deliver_redfish(self, capsule: Capsule,
                               aspects: UefiCmdAspects) -> None:
        """
            POST the capsule bytes to the Redfish
            ``UpdateService.SimpleUpdate`` multipart endpoint.  The
            command is formatted as a verb-line string so the shared
            ``UefiRedfishClient`` can dispatch it.

            :param capsule: The capsule to deliver.
            :param aspects: The resolved aspects for this call.

            :raises: UefiCapsuleError
        """
        capsule_bytes = capsule.to_bytes()
        encoded = _encode_base64(data=capsule_bytes)
        multipart_path = "/redfish/v1/UpdateService/update-multipart"

        command = "POST {path} sha256={sha};capsule_b64={enc}".format(
            path=multipart_path,
            sha=capsule.sha256,
            enc=encoded,
        )

        status, stdout, stderr = self._agent.run_cmd(
            command=command,
            aspects=aspects,
        )

        if status != 0:
            err_msg_lines = [
                "CapsuleDelivery: Redfish capsule POST returned a non-zero status.",
                "TRANSPORT: redfish",
                "PATH: {}".format(multipart_path),
                "STATUS: {}".format(status),
                "CAPSULE_GUID: {}".format(capsule.header.capsule_guid),
                "CAPSULE_SIZE: {}".format(len(capsule_bytes)),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiCapsuleError(err_msg) from None

        return


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _encode_base64(data: bytes) -> str:
    """
        Render ``data`` as a single-line base64 ASCII string suitable
        for embedding in a shell command line.  The routine is local to
        this module to avoid dragging ``base64`` into the public
        surface.

        :param data: The buffer to encode.

        :returns: The base64 string.
    """
    import base64
    encoded_bytes = base64.b64encode(data)
    rtnval = encoded_bytes.decode("ascii")
    return rtnval
