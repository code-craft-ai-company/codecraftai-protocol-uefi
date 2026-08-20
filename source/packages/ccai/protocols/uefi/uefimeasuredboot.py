"""
    .. module:: uefimeasuredboot
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: TPM 2.0 Measured Boot analyzer for the UEFI protocol layer.
               Reads the kernel-preserved TCG 2.0 event log
               (``/sys/kernel/security/tpm0/binary_bios_measurements``) and
               the live PCR banks (via ``tpm2_pcrread``) from a target that
               is reachable over the SSH-to-host transport, parses the
               crypto-agile event log per the TCG EFI Protocol
               Specification and the TCG PC Client Platform Firmware
               Profile, reconstructs the expected PCR values by replaying
               each event through the TCG Extend operation
               ``PCR_new = H(PCR_old || digest)``, and validates the
               reconstruction against the live PCR values.

               The feature is STRICTLY READ-ONLY.  It never extends, resets,
               or otherwise mutates PCR state and never writes to the
               securityfs mount point; callers who need quote generation
               or signature management should reach for a higher-layer
               workflow feature.

               Binary layout references:

               * TCG EFI Protocol Specification (crypto-agile event log).
               * TCG PC Client Platform Firmware Profile §10.2 and §10.4
                 (``TCG_PCR_EVENT`` / ``TCG_PCR_EVENT2`` /
                 ``TCG_EfiSpecIDEvent``).
               * UEFI Specification §38 (TCG platform interface).

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Code Craft AI LLC"
__credits__ = []


import base64
import enum
import hashlib
import os
import re

from dataclasses import dataclass
from typing import Dict, Iterator, List, Optional, Tuple

from ccai.protocols.uefi.uefiaspects import (
    DEFAULT_CMD_ASPECTS,
    UefiCmdAspects,
    resolve_aspects,
)
from ccai.protocols.uefi.uefiagent import UefiAgent
from ccai.protocols.uefi.uefiexceptions import UefiError, UefiShellError
from ccai.protocols.uefi.uefihelpers import (
    unpack_u16_le,
    unpack_u32_le,
    unpack_u8,
)
from ccai.protocols.uefi.uefilogging import (
    UEFI_MEASUREDBOOT_CHANNEL,
    get_uefi_logger,
)


__all__ = [
    "ALG_DIGEST_SIZES",
    "ALG_HASHLIB_NAMES",
    "EventLog",
    "EventType",
    "MeasuredBoot",
    "PcrEvent",
    "TcgSpecIdEvent",
    "TpmAlgorithmId",
    "TpmDigest",
    "SPEC_ID_EVENT_SIGNATURE",
]


# ---------------------------------------------------------------------------
# TPM algorithm enum and companion tables (TCG Algorithm Registry)
# ---------------------------------------------------------------------------

class TpmAlgorithmId(enum.IntEnum):
    """
        Enumeration of the TPM ``TPM_ALG_ID`` values recognized by this
        parser.  Values follow the TCG Algorithm Registry.
    """
    SHA1 = 0x0004
    SHA256 = 0x000B
    SHA384 = 0x000C
    SHA512 = 0x000D
    SM3_256 = 0x0012


#: Digest size in bytes for each recognized :class:`TpmAlgorithmId`.
ALG_DIGEST_SIZES: Dict[TpmAlgorithmId, int] = {
    TpmAlgorithmId.SHA1: 20,
    TpmAlgorithmId.SHA256: 32,
    TpmAlgorithmId.SHA384: 48,
    TpmAlgorithmId.SHA512: 64,
    TpmAlgorithmId.SM3_256: 32,
}


#: :mod:`hashlib` algorithm name for each recognized :class:`TpmAlgorithmId`.
#: ``SM3_256`` is advisory; not every :mod:`hashlib` build ships a SM3
#: implementation.
ALG_HASHLIB_NAMES: Dict[TpmAlgorithmId, str] = {
    TpmAlgorithmId.SHA1: "sha1",
    TpmAlgorithmId.SHA256: "sha256",
    TpmAlgorithmId.SHA384: "sha384",
    TpmAlgorithmId.SHA512: "sha512",
    TpmAlgorithmId.SM3_256: "sm3_256",
}


# ---------------------------------------------------------------------------
# Event type enum
# ---------------------------------------------------------------------------

class EventType(enum.IntEnum):
    """
        Enumeration of the well-known TCG / UEFI event type codes per the
        TCG PC Client Platform Firmware Profile and UEFI §38.4.1.  A
        value outside this set is preserved verbatim as an :class:`int`
        on :attr:`PcrEvent.event_type`.
    """
    EV_PREBOOT_CERT = 0x00000000
    EV_POST_CODE = 0x00000001
    EV_NO_ACTION = 0x00000003
    EV_SEPARATOR = 0x00000004
    EV_EVENT_TAG = 0x00000006
    EV_S_CRTM_CONTENTS = 0x00000007
    EV_S_CRTM_VERSION = 0x00000008
    EV_CPU_MICROCODE = 0x00000009
    EV_PLATFORM_CONFIG_FLAGS = 0x0000000A
    EV_TABLE_OF_DEVICES = 0x0000000B
    EV_COMPACT_HASH = 0x0000000C
    EV_EFI_EVENT_BASE = 0x80000000
    EV_EFI_VARIABLE_DRIVER_CONFIG = 0x80000001
    EV_EFI_VARIABLE_BOOT = 0x80000002
    EV_EFI_BOOT_SERVICES_APPLICATION = 0x80000003
    EV_EFI_BOOT_SERVICES_DRIVER = 0x80000004
    EV_EFI_RUNTIME_SERVICES_DRIVER = 0x80000005
    EV_EFI_GPT_EVENT = 0x80000006
    EV_EFI_ACTION = 0x80000007
    EV_EFI_PLATFORM_FIRMWARE_BLOB = 0x80000008
    EV_EFI_HANDOFF_TABLES = 0x80000009


#: ASCII signature (NUL-padded to 16 bytes) introducing the crypto-agile
#: TCG_EfiSpecIDEvent payload.
SPEC_ID_EVENT_SIGNATURE: bytes = b"Spec ID Event03\x00"


# ---------------------------------------------------------------------------
# Data records
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TpmDigest:
    """
        A single (algorithm, digest) pair taken from a
        ``TPML_DIGEST_VALUES`` field.

        :ivar algorithm: The :class:`TpmAlgorithmId` of the digest.
        :ivar value: The raw digest bytes; length is determined by
                     :data:`ALG_DIGEST_SIZES`.
    """
    algorithm: TpmAlgorithmId
    value: bytes


@dataclass(frozen=True)
class PcrEvent:
    """
        A single event from a TCG 2.0 crypto-agile event log.

        :ivar pcr_index: The destination PCR index (``0`` through ``23``).
        :ivar event_type: The decoded :class:`EventType` (or a raw
                          :class:`int` for an unrecognized type code).
        :ivar digests: Tuple of :class:`TpmDigest` records (one per
                       algorithm bank present for this event).
        :ivar event_data: The raw event-data payload bytes.
    """
    pcr_index: int
    event_type: EventType
    digests: Tuple[TpmDigest, ...]
    event_data: bytes

    def digest_for(self, algorithm: TpmAlgorithmId) -> Optional[bytes]:
        """
            Return the digest value matching ``algorithm`` for this event
            or :data:`None` when the event does not carry a digest for
            that bank.

            :param algorithm: The :class:`TpmAlgorithmId` to look up.

            :returns: The matching digest bytes or :data:`None`.
        """
        match: Optional[bytes] = None
        for entry in self.digests:
            if entry.algorithm == algorithm:
                match = entry.value
                break

        rtnval = match
        return rtnval


@dataclass(frozen=True)
class TcgSpecIdEvent:
    """
        Decoded payload of the first event in a crypto-agile event log,
        the ``TCG_EfiSpecIDEvent``.  The specification ID event is carried
        in a legacy ``TCG_PCR_EVENT`` record (SHA-1 only, zero digest,
        event type ``EV_NO_ACTION``, PCR 0).

        :ivar platform_class: ``0`` = Client, ``1`` = Server.
        :ivar spec_version_minor: Declared TCG spec minor version.
        :ivar spec_version_major: Declared TCG spec major version.
        :ivar spec_errata: Declared TCG spec errata.
        :ivar uint_size: ``1`` = 32-bit, ``2`` = 64-bit.
        :ivar num_algorithms: Number of ``(alg_id, digest_size)`` pairs.
        :ivar algorithms: Tuple of ``(alg_id, digest_size)`` pairs.
        :ivar vendor_info: Vendor-specific bytes.
    """
    platform_class: int
    spec_version_minor: int
    spec_version_major: int
    spec_errata: int
    uint_size: int
    num_algorithms: int
    algorithms: Tuple[Tuple[TpmAlgorithmId, int], ...]
    vendor_info: bytes

    @classmethod
    def from_bytes(cls, data: bytes) -> "TcgSpecIdEvent":
        """
            Parse a ``TCG_EfiSpecIDEvent`` payload.

            :param data: The bytes of the first event's event-data payload.

            :returns: The decoded :class:`TcgSpecIdEvent`.
            :raises: UefiError
        """
        validated = _require_bytes(field_name="data", value=data)

        # Fixed prefix: 16-byte signature + 4-byte platformClass + 4 bytes of
        # version / errata / uintnSize + 4-byte numberOfAlgorithms.
        fixed_prefix_size = 16 + 4 + 1 + 1 + 1 + 1 + 4
        if len(validated) < fixed_prefix_size:
            err_msg_lines = [
                "TcgSpecIdEvent.from_bytes: buffer is shorter than the fixed prefix.",
                "EXPECTED: at least {} bytes".format(fixed_prefix_size),
                "OBSERVED: {}".format(len(validated)),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg) from None

        signature = validated[0:16]
        if signature != SPEC_ID_EVENT_SIGNATURE:
            err_msg_lines = [
                "TcgSpecIdEvent.from_bytes: signature mismatch.",
                "EXPECTED: {!r}".format(SPEC_ID_EVENT_SIGNATURE),
                "OBSERVED: {!r}".format(bytes(signature)),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg) from None

        platform_class = unpack_u32_le(buf=validated[16:20])
        spec_version_minor = unpack_u8(buf=validated[20:21])
        spec_version_major = unpack_u8(buf=validated[21:22])
        spec_errata = unpack_u8(buf=validated[22:23])
        uint_size = unpack_u8(buf=validated[23:24])
        num_algorithms = unpack_u32_le(buf=validated[24:28])

        algorithms_bytes = 4 * num_algorithms
        expected_header_size = fixed_prefix_size + algorithms_bytes + 1
        if len(validated) < expected_header_size:
            err_msg_lines = [
                "TcgSpecIdEvent.from_bytes: buffer is shorter than the declared algorithm vector.",
                "NUM_ALGORITHMS: {}".format(num_algorithms),
                "EXPECTED: at least {} bytes".format(expected_header_size),
                "OBSERVED: {}".format(len(validated)),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg) from None

        algorithms_list: List[Tuple[TpmAlgorithmId, int]] = []
        cursor = fixed_prefix_size
        for index in range(num_algorithms):
            alg_bytes = validated[cursor:cursor + 2]
            size_bytes = validated[cursor + 2:cursor + 4]
            alg_raw = unpack_u16_le(buf=alg_bytes)
            digest_size = unpack_u16_le(buf=size_bytes)
            try:
                alg_id = TpmAlgorithmId(alg_raw)
            except ValueError:
                err_msg_lines = [
                    "TcgSpecIdEvent.from_bytes: unrecognized TPM_ALG_ID.",
                    "INDEX: {}".format(index),
                    "ALG_ID: 0x{:04X}".format(alg_raw),
                    "DIGEST_SIZE: {}".format(digest_size),
                ]
                err_msg = os.linesep.join(err_msg_lines)
                raise UefiError(err_msg) from None

            expected_size = ALG_DIGEST_SIZES[alg_id]
            if digest_size != expected_size:
                err_msg_lines = [
                    "TcgSpecIdEvent.from_bytes: digest size does not match the algorithm registry.",
                    "INDEX: {}".format(index),
                    "ALG_ID: 0x{:04X} ({})".format(int(alg_id), alg_id.name),
                    "EXPECTED: {}".format(expected_size),
                    "OBSERVED: {}".format(digest_size),
                ]
                err_msg = os.linesep.join(err_msg_lines)
                raise UefiError(err_msg) from None

            algorithms_list.append((alg_id, digest_size))
            cursor = cursor + 4

        vendor_info_size = unpack_u8(buf=validated[cursor:cursor + 1])
        cursor = cursor + 1

        expected_total = cursor + vendor_info_size
        if len(validated) < expected_total:
            err_msg_lines = [
                "TcgSpecIdEvent.from_bytes: buffer is shorter than declared vendorInfo.",
                "VENDOR_INFO_SIZE: {}".format(vendor_info_size),
                "EXPECTED: at least {} bytes".format(expected_total),
                "OBSERVED: {}".format(len(validated)),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg) from None

        vendor_info = bytes(validated[cursor:cursor + vendor_info_size])

        rtnval = cls(
            platform_class=platform_class,
            spec_version_minor=spec_version_minor,
            spec_version_major=spec_version_major,
            spec_errata=spec_errata,
            uint_size=uint_size,
            num_algorithms=num_algorithms,
            algorithms=tuple(algorithms_list),
            vendor_info=vendor_info,
        )
        return rtnval


# ---------------------------------------------------------------------------
# EventLog
# ---------------------------------------------------------------------------

class EventLog:
    """
        A parsed TPM 2.0 crypto-agile event log.

        The class holds the decoded :class:`TcgSpecIdEvent` header and a
        tuple of :class:`PcrEvent` records in the order they appeared in
        the binary log.  Construction is usually via
        :meth:`EventLog.from_bytes`; direct construction is useful for
        tests that build synthetic logs.

        :ivar spec_id: The decoded specification-ID event.
        :ivar events: Tuple of :class:`PcrEvent` records (subsequent events).
    """

    def __init__(self, spec_id: TcgSpecIdEvent, events: Tuple[PcrEvent, ...]) -> None:
        """
            Initialize the event log with an already-decoded spec-ID event
            and a tuple of :class:`PcrEvent` records.

            :param spec_id: The decoded :class:`TcgSpecIdEvent`.
            :param events: Tuple of :class:`PcrEvent` records (the ones
                           that follow the spec-ID event).
        """
        self._spec_id: TcgSpecIdEvent = spec_id
        self._events: Tuple[PcrEvent, ...] = tuple(events)
        return

    @property
    def spec_id(self) -> TcgSpecIdEvent:
        """
            The decoded :class:`TcgSpecIdEvent` header.
        """
        rtnval = self._spec_id
        return rtnval

    @property
    def events(self) -> Tuple[PcrEvent, ...]:
        """
            The tuple of :class:`PcrEvent` records following the header.
        """
        rtnval = self._events
        return rtnval

    @classmethod
    def from_bytes(cls, data: bytes) -> "EventLog":
        """
            Parse a binary crypto-agile event log into an :class:`EventLog`.

            The first event is decoded as a legacy ``TCG_PCR_EVENT``
            carrying the ``TCG_EfiSpecIDEvent`` payload; every remaining
            event is decoded as a ``TCG_PCR_EVENT2`` with a
            ``TPML_DIGEST_VALUES`` digest vector.

            :param data: The raw event log bytes.

            :returns: The decoded :class:`EventLog`.
            :raises: UefiError
        """
        validated = _require_bytes(field_name="data", value=data)
        total_length = len(validated)

        # ------------------------------------------------------------------
        # First event: TCG_PCR_EVENT (32-byte fixed header + event_data)
        # ------------------------------------------------------------------
        first_header_size = 32
        if total_length < first_header_size:
            err_msg_lines = [
                "EventLog.from_bytes: buffer is shorter than the first-event header.",
                "EXPECTED: at least {} bytes".format(first_header_size),
                "OBSERVED: {}".format(total_length),
                "FIELD: TCG_PCR_EVENT header",
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg) from None

        pcr_index_0 = unpack_u32_le(buf=validated[0:4])
        event_type_0 = unpack_u32_le(buf=validated[4:8])
        digest_0 = bytes(validated[8:28])
        event_size_0 = unpack_u32_le(buf=validated[28:32])

        if pcr_index_0 != 0:
            err_msg_lines = [
                "EventLog.from_bytes: first event has non-zero PCRIndex.",
                "EXPECTED: 0",
                "OBSERVED: {}".format(pcr_index_0),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg) from None

        if event_type_0 != int(EventType.EV_NO_ACTION):
            err_msg_lines = [
                "EventLog.from_bytes: first event type is not EV_NO_ACTION.",
                "EXPECTED: 0x{:08X} (EV_NO_ACTION)".format(int(EventType.EV_NO_ACTION)),
                "OBSERVED: 0x{:08X}".format(event_type_0),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg) from None

        zero_sha1 = b"\x00" * 20
        if digest_0 != zero_sha1:
            err_msg_lines = [
                "EventLog.from_bytes: first event digest must be 20 zero bytes.",
                "OBSERVED: {}".format(digest_0.hex()),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg) from None

        payload_end = first_header_size + event_size_0
        if total_length < payload_end:
            err_msg_lines = [
                "EventLog.from_bytes: buffer is shorter than first-event payload.",
                "EXPECTED: at least {} bytes".format(payload_end),
                "OBSERVED: {}".format(total_length),
                "FIELD: TCG_EfiSpecIDEvent payload",
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg) from None

        spec_payload = bytes(validated[first_header_size:payload_end])
        spec_id = TcgSpecIdEvent.from_bytes(data=spec_payload)

        # Build a quick-lookup map for digest sizes used by subsequent events.
        digest_size_map: Dict[TpmAlgorithmId, int] = {}
        for alg_pair in spec_id.algorithms:
            alg_id, declared_size = alg_pair
            digest_size_map[alg_id] = declared_size

        # ------------------------------------------------------------------
        # Remaining events: TCG_PCR_EVENT2
        # ------------------------------------------------------------------
        parsed_events: List[PcrEvent] = []
        cursor = payload_end
        event_index = 1

        while cursor < total_length:
            next_event, new_cursor = _parse_pcr_event2(
                buffer=validated,
                offset=cursor,
                event_index=event_index,
                digest_size_map=digest_size_map,
                prior_event_count=len(parsed_events),
            )
            parsed_events.append(next_event)
            cursor = new_cursor
            event_index = event_index + 1

        rtnval = cls(spec_id=spec_id, events=tuple(parsed_events))
        return rtnval

    def events_for_pcr(self, pcr_index: int) -> Iterator[PcrEvent]:
        """
            Yield the subset of :attr:`events` destined for ``pcr_index``,
            in log order.

            :param pcr_index: Target PCR index (``0`` through ``23``).

            :returns: Iterator of matching :class:`PcrEvent` records.
            :raises: UefiError
        """
        if not isinstance(pcr_index, int) or isinstance(pcr_index, bool):
            err_msg_lines = [
                "EventLog.events_for_pcr: 'pcr_index' must be an int.",
                "GOT: value={!r} type={}".format(pcr_index, type(pcr_index).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg) from None

        for event in self._events:
            if event.pcr_index == pcr_index:
                yield event

        return

    def reconstruct_pcrs(self, algorithm: TpmAlgorithmId) -> Dict[int, bytes]:
        """
            Reconstruct the expected PCR values for ``algorithm`` by
            replaying every event through the TCG Extend operation
            ``PCR_new = H(PCR_old || digest)`` starting from a zero
            accumulator per touched PCR.

            :param algorithm: The :class:`TpmAlgorithmId` whose digests
                              are to be extended.

            :returns: Mapping ``{pcr_index: reconstructed_digest}`` for
                      every PCR touched by the log.
            :raises: UefiError
        """
        if not isinstance(algorithm, TpmAlgorithmId):
            err_msg_lines = [
                "EventLog.reconstruct_pcrs: 'algorithm' must be a TpmAlgorithmId.",
                "GOT: value={!r} type={}".format(algorithm, type(algorithm).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg) from None

        hashlib_name = ALG_HASHLIB_NAMES[algorithm]
        digest_size = ALG_DIGEST_SIZES[algorithm]

        try:
            # Probe hashlib.new so an unavailable algorithm surfaces early
            # rather than on the first Extend call.
            hashlib.new(hashlib_name)
        except (ValueError, TypeError) as herr:
            err_msg_lines = [
                "EventLog.reconstruct_pcrs: hashlib does not support the requested algorithm.",
                "ALGORITHM: {} (hashlib_name={!r})".format(algorithm.name, hashlib_name),
                "UNDERLYING: {!r}".format(herr),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg) from herr

        accumulators: Dict[int, bytes] = {}
        zero_seed = b"\x00" * digest_size

        for event_index, event in enumerate(self._events):
            event_digest = event.digest_for(algorithm=algorithm)
            if event_digest is None:
                # An event that lacks the requested bank is not an error
                # in non-strict replay; its PCR accumulator simply is not
                # advanced for this event.
                continue

            if len(event_digest) != digest_size:
                err_msg_lines = [
                    "EventLog.reconstruct_pcrs: event digest length mismatch.",
                    "EVENT_INDEX: {}".format(event_index),
                    "PCR_INDEX: {}".format(event.pcr_index),
                    "ALGORITHM: {}".format(algorithm.name),
                    "EXPECTED: {}".format(digest_size),
                    "OBSERVED: {}".format(len(event_digest)),
                ]
                err_msg = os.linesep.join(err_msg_lines)
                raise UefiError(err_msg) from None

            pcr_index = event.pcr_index
            prior_value = accumulators.get(pcr_index, zero_seed)

            extended = hashlib.new(hashlib_name)
            extended.update(prior_value)
            extended.update(event_digest)
            accumulators[pcr_index] = extended.digest()

        rtnval = accumulators
        return rtnval

    def validate_against(self, observed_pcrs: Dict[int, bytes],
                               algorithm: TpmAlgorithmId) -> None:
        """
            Compare the reconstruction of :meth:`reconstruct_pcrs` against
            a set of observed PCR values.  Raises :class:`UefiError` on
            any mismatch with a per-PCR diff table in ``details``.

            PCR indices present in ``observed_pcrs`` but absent from the
            reconstructed map are compared against a zero accumulator
            (unmeasured PCRs).  PCR indices reconstructed from the log
            but not observed are reported as missing-observation entries.

            :param observed_pcrs: Mapping ``{pcr_index: digest}`` as
                                  returned by
                                  :meth:`MeasuredBoot.read_pcrs`.
            :param algorithm: The :class:`TpmAlgorithmId` to compare.

            :raises: UefiError
        """
        if not isinstance(observed_pcrs, dict):
            err_msg_lines = [
                "EventLog.validate_against: 'observed_pcrs' must be a dict.",
                "GOT: value={!r} type={}".format(observed_pcrs, type(observed_pcrs).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg) from None

        reconstructed = self.reconstruct_pcrs(algorithm=algorithm)
        digest_size = ALG_DIGEST_SIZES[algorithm]
        zero_seed = b"\x00" * digest_size

        mismatch_details: List[str] = []
        all_indices = set(reconstructed.keys()) | set(observed_pcrs.keys())
        for pcr_index in sorted(all_indices):
            expected = reconstructed.get(pcr_index, zero_seed)
            observed_value = observed_pcrs.get(pcr_index)

            if observed_value is None:
                mismatch_details.append(
                    "pcr={} alg={} status=missing_observation expected={}".format(
                        pcr_index, algorithm.name, expected.hex()
                    )
                )
                continue

            if not isinstance(observed_value, (bytes, bytearray, memoryview)):
                mismatch_details.append(
                    "pcr={} alg={} status=non_bytes observed_type={}".format(
                        pcr_index, algorithm.name, type(observed_value).__name__
                    )
                )
                continue

            observed_bytes = bytes(observed_value)
            if observed_bytes != expected:
                mismatch_details.append(
                    "pcr={} alg={} expected={} observed={}".format(
                        pcr_index, algorithm.name, expected.hex(), observed_bytes.hex()
                    )
                )

        if len(mismatch_details) > 0:
            err_msg_lines = [
                "EventLog.validate_against: reconstructed PCR values do not match observed PCRs.",
                "ALGORITHM: {}".format(algorithm.name),
                "MISMATCH_COUNT: {}".format(len(mismatch_details)),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg, details=mismatch_details) from None

        return


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _require_bytes(field_name: str, value: object) -> bytes:
    """
        Validate that ``value`` is a bytes-like object and coerce to
        :class:`bytes`.

        :param field_name: The calling field name for diagnostics.
        :param value: The candidate value.

        :returns: The validated buffer as :class:`bytes`.
        :raises: UefiError
    """
    if not isinstance(value, (bytes, bytearray, memoryview)):
        err_msg_lines = [
            "'{}' must be a bytes-like object.".format(field_name),
            "GOT: value={!r} type={}".format(value, type(value).__name__),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiError(err_msg) from None

    rtnval = bytes(value)
    return rtnval


def _parse_pcr_event2(buffer: bytes, offset: int, event_index: int,
                      digest_size_map: Dict[TpmAlgorithmId, int],
                      prior_event_count: int) -> Tuple[PcrEvent, int]:
    """
        Decode a single ``TCG_PCR_EVENT2`` at ``offset`` in ``buffer``.

        :param buffer: The full binary event log.
        :param offset: Byte offset where the ``TCG_PCR_EVENT2`` begins.
        :param event_index: 1-based index within the whole log (the
                            spec-ID event occupies index 0).
        :param digest_size_map: Per-algorithm digest size map built from
                                the spec-ID event.
        :param prior_event_count: Count of successfully-parsed
                                  ``TCG_PCR_EVENT2`` records ahead of
                                  this one, for truncation diagnostics.

        :returns: A ``(PcrEvent, new_cursor)`` pair.
        :raises: UefiError
    """
    total = len(buffer)
    header_size = 4 + 4 + 4  # PCRIndex + EventType + DigestCount
    if offset + header_size > total:
        err_msg_lines = [
            "EventLog.from_bytes: truncated TCG_PCR_EVENT2 header.",
            "EVENT_INDEX: {}".format(event_index),
            "PRIOR_EVENTS_PARSED: {}".format(prior_event_count),
            "OFFSET: {}".format(offset),
            "EXPECTED_HEADER_BYTES: {}".format(header_size),
            "REMAINING: {}".format(total - offset),
            "FIELD: TCG_PCR_EVENT2 fixed header",
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiError(err_msg) from None

    pcr_index = unpack_u32_le(buf=buffer[offset:offset + 4])
    event_type_raw = unpack_u32_le(buf=buffer[offset + 4:offset + 8])
    digest_count = unpack_u32_le(buf=buffer[offset + 8:offset + 12])

    cursor = offset + header_size

    digests: List[TpmDigest] = []
    for digest_index in range(digest_count):
        if cursor + 2 > total:
            err_msg_lines = [
                "EventLog.from_bytes: truncated TPMT_HA.hashAlg.",
                "EVENT_INDEX: {}".format(event_index),
                "DIGEST_INDEX: {}".format(digest_index),
                "OFFSET: {}".format(cursor),
                "FIELD: hashAlg",
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg) from None

        alg_raw = unpack_u16_le(buf=buffer[cursor:cursor + 2])
        cursor = cursor + 2
        try:
            alg_id = TpmAlgorithmId(alg_raw)
        except ValueError:
            err_msg_lines = [
                "EventLog.from_bytes: unrecognized TPM_ALG_ID in digest vector.",
                "EVENT_INDEX: {}".format(event_index),
                "DIGEST_INDEX: {}".format(digest_index),
                "ALG_ID: 0x{:04X}".format(alg_raw),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg) from None

        if alg_id in digest_size_map:
            declared_size = digest_size_map[alg_id]
        else:
            declared_size = ALG_DIGEST_SIZES[alg_id]

        if cursor + declared_size > total:
            err_msg_lines = [
                "EventLog.from_bytes: truncated digest bytes.",
                "EVENT_INDEX: {}".format(event_index),
                "DIGEST_INDEX: {}".format(digest_index),
                "ALGORITHM: {}".format(alg_id.name),
                "EXPECTED: {}".format(declared_size),
                "REMAINING: {}".format(total - cursor),
                "FIELD: digest",
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg) from None

        digest_bytes = bytes(buffer[cursor:cursor + declared_size])
        cursor = cursor + declared_size

        digest_record = TpmDigest(algorithm=alg_id, value=digest_bytes)
        digests.append(digest_record)

    if cursor + 4 > total:
        err_msg_lines = [
            "EventLog.from_bytes: truncated EventSize field.",
            "EVENT_INDEX: {}".format(event_index),
            "OFFSET: {}".format(cursor),
            "FIELD: EventSize",
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiError(err_msg) from None

    event_size = unpack_u32_le(buf=buffer[cursor:cursor + 4])
    cursor = cursor + 4

    if cursor + event_size > total:
        err_msg_lines = [
            "EventLog.from_bytes: truncated event_data payload.",
            "EVENT_INDEX: {}".format(event_index),
            "OFFSET: {}".format(cursor),
            "EXPECTED: {}".format(event_size),
            "REMAINING: {}".format(total - cursor),
            "FIELD: event_data",
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiError(err_msg) from None

    event_data = bytes(buffer[cursor:cursor + event_size])
    cursor = cursor + event_size

    # Coerce event type — unknown codes fall back to the raw int.  Cast is
    # intentional: users comparing with EventType.X still succeed for
    # known codes; unknown codes compare numerically.
    event_type: EventType
    try:
        event_type = EventType(event_type_raw)
    except ValueError:
        event_type = event_type_raw  # type: ignore[assignment]

    event_record = PcrEvent(
        pcr_index=pcr_index,
        event_type=event_type,
        digests=tuple(digests),
        event_data=event_data,
    )
    rtnval = (event_record, cursor)
    return rtnval


# ---------------------------------------------------------------------------
# tpm2_pcrread output parser
# ---------------------------------------------------------------------------

# tpm2-tools v4/v5 emit stanzas like:
#     sha256:
#       0 : 0x0000...
#       1 : 0xDEADBEEF...
# We tolerate optional whitespace, decimal or hex PCR indices, and a
# '0x'-prefixed or bare hex digest.
_PCR_LINE_RE = re.compile(
    r"^\s*(?P<index>\d+)\s*:\s*(?:0x)?(?P<digest>[0-9A-Fa-f]+)\s*$"
)
_BANK_LINE_RE = re.compile(r"^\s*(?P<bank>[A-Za-z0-9_]+)\s*:\s*$")


def _parse_tpm2_pcrread_stdout(stdout: str, algorithm: TpmAlgorithmId) -> Dict[int, bytes]:
    """
        Parse the stdout of ``tpm2_pcrread <alg>`` into a
        ``{pcr_index: digest_bytes}`` map.

        The parser tolerates both the ``tpm2-tools`` v4 and v5 output
        shapes (bank header + indented index lines).  Lines that do not
        match the PCR-line regex are ignored so that trailing tool
        banners or leading probe output do not break decoding.

        :param stdout: Raw stdout captured from ``tpm2_pcrread``.
        :param algorithm: The algorithm being read (used for diagnostics
                          and for bounding the expected digest length).

        :returns: Mapping ``{pcr_index: digest_bytes}``.
        :raises: UefiError
    """
    if not isinstance(stdout, str):
        err_msg_lines = [
            "_parse_tpm2_pcrread_stdout: 'stdout' must be a str.",
            "GOT: value={!r} type={}".format(stdout, type(stdout).__name__),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiError(err_msg) from None

    expected_digest_bytes = ALG_DIGEST_SIZES[algorithm]
    expected_hex_len = expected_digest_bytes * 2

    current_bank: Optional[str] = None
    pcr_map: Dict[int, bytes] = {}

    for raw_line in stdout.splitlines():
        bank_match = _BANK_LINE_RE.match(raw_line)
        if bank_match is not None:
            current_bank = bank_match.group("bank").lower()
            continue

        pcr_match = _PCR_LINE_RE.match(raw_line)
        if pcr_match is None:
            continue

        pcr_index = int(pcr_match.group("index"), 10)
        digest_hex = pcr_match.group("digest")

        # Drop lines obviously belonging to an unrelated bank when the
        # stdout includes more than one bank stanza.
        if current_bank is not None and current_bank != ALG_HASHLIB_NAMES[algorithm]:
            continue

        if len(digest_hex) != expected_hex_len:
            err_msg_lines = [
                "_parse_tpm2_pcrread_stdout: digest length does not match the requested algorithm.",
                "ALGORITHM: {}".format(algorithm.name),
                "PCR_INDEX: {}".format(pcr_index),
                "EXPECTED_HEX_LEN: {}".format(expected_hex_len),
                "OBSERVED_HEX_LEN: {}".format(len(digest_hex)),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg) from None

        try:
            digest_bytes = bytes.fromhex(digest_hex)
        except ValueError as verr:
            err_msg_lines = [
                "_parse_tpm2_pcrread_stdout: digest is not valid hex.",
                "ALGORITHM: {}".format(algorithm.name),
                "PCR_INDEX: {}".format(pcr_index),
                "DIGEST: {!r}".format(digest_hex),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg) from verr

        pcr_map[pcr_index] = digest_bytes

    if len(pcr_map) == 0:
        err_msg_lines = [
            "_parse_tpm2_pcrread_stdout: no PCR lines decoded from stdout.",
            "ALGORITHM: {}".format(algorithm.name),
            "STDOUT_PREVIEW:",
            stdout if len(stdout) <= 512 else stdout[:512] + "...",
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiError(err_msg) from None

    rtnval = pcr_map
    return rtnval


# ---------------------------------------------------------------------------
# MeasuredBoot facade
# ---------------------------------------------------------------------------

_SUPPORTED_TRANSPORT_KINDS: Tuple[str, ...] = ("ssh-host",)


class MeasuredBoot:
    """
        Facade for TCG 2.0 event-log readout and PCR readback over a
        :class:`UefiAgent`.

        The facade delegates every external read to ``agent.run_cmd`` and
        resolves call-site aspects through :func:`resolve_aspects` so the
        measured-interop contract is respected on every call.

        :param agent: The :class:`UefiAgent` to dispatch through.
        :param aspects: Default :class:`UefiCmdAspects` used when a
                        per-call ``aspects`` argument is :data:`None`.
    """

    def __init__(self, agent: UefiAgent,
                       aspects: UefiCmdAspects = DEFAULT_CMD_ASPECTS) -> None:
        """
            Initialize the Measured Boot facade.

            :param agent: The :class:`UefiAgent` to drive.
            :param aspects: The default aspects.
        """
        self._agent: UefiAgent = agent
        self._aspects: UefiCmdAspects = aspects
        self._logger = get_uefi_logger(channel=UEFI_MEASUREDBOOT_CHANNEL)
        return

    def read_event_log(self, aspects: Optional[UefiCmdAspects] = None) -> EventLog:
        """
            Read the kernel-preserved TCG 2.0 event log from the target
            and return it as a parsed :class:`EventLog`.

            The only supported transport is ``ssh-host``, where the log
            is fetched via ``cat <path> | base64`` so the binary payload
            survives shell transport.  Pre-boot transports (``serial``,
            ``ipmi-sol``) and ``redfish`` do not surface the kernel event
            log and therefore raise :class:`UefiError` with an actionable
            diagnostic.

            :param aspects: Optional per-call :class:`UefiCmdAspects`
                            override.

            :returns: The decoded :class:`EventLog`.
            :raises: UefiError, UefiShellError, ConfigurationError
        """
        effective = resolve_aspects(passed=aspects, instance_default=self._aspects)

        transport_kind = getattr(self._agent, "transport_kind", None)
        if transport_kind is None:
            transport_kind = getattr(self._agent, "_transport_kind", None)

        if transport_kind not in _SUPPORTED_TRANSPORT_KINDS:
            err_msg_lines = [
                "MeasuredBoot.read_event_log: unsupported transport_kind.",
                "TRANSPORT_KIND: {!r}".format(transport_kind),
                "SUPPORTED: {}".format(list(_SUPPORTED_TRANSPORT_KINDS)),
                "HINT: the kernel event log is available only post-boot via ssh-host.",
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg) from None

        log_path = "/sys/kernel/security/tpm0/binary_bios_measurements"
        command = "cat {} | base64".format(log_path)

        self._logger.info(
            "uefi.measuredboot: read_event_log begin path=%s", log_path,
        )

        try:
            status, stdout, stderr = self._agent.run_cmd(
                command=command,
                aspects=effective,
            )
        except UefiShellError:
            raise
        except UefiError:
            raise

        if status != 0:
            err_msg_lines = [
                "MeasuredBoot.read_event_log: base64-cat of the event log failed.",
                "PATH: {}".format(log_path),
                "STATUS: {}".format(status),
                "HINT: ensure securityfs is mounted and the TPM kernel subsystem is enabled.",
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiShellError(
                err_msg,
                command=command,
                status=status,
                stdout=stdout,
                stderr=stderr,
            ) from None

        try:
            raw_bytes = base64.b64decode(stdout, validate=False)
        except (ValueError, TypeError) as berr:
            err_msg_lines = [
                "MeasuredBoot.read_event_log: base64 decode failed.",
                "PATH: {}".format(log_path),
                "STDOUT_LEN: {}".format(len(stdout) if stdout is not None else 0),
                "UNDERLYING: {!r}".format(berr),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg) from berr

        event_log = EventLog.from_bytes(data=raw_bytes)

        self._logger.info(
            "uefi.measuredboot: read_event_log complete events=%d bytes=%d",
            len(event_log.events), len(raw_bytes),
        )

        rtnval = event_log
        return rtnval

    def read_pcrs(self, algorithm: TpmAlgorithmId = TpmAlgorithmId.SHA256,
                        aspects: Optional[UefiCmdAspects] = None) -> Dict[int, bytes]:
        """
            Read the live PCR values from the target's TPM 2.0 via
            ``tpm2_pcrread <alg>`` over the SSH-to-host transport.

            :param algorithm: The :class:`TpmAlgorithmId` to read
                              (defaults to SHA-256).
            :param aspects: Optional per-call :class:`UefiCmdAspects`
                            override.

            :returns: Mapping ``{pcr_index: digest_bytes}`` for the
                      requested algorithm.
            :raises: UefiError, UefiShellError, ConfigurationError
        """
        effective = resolve_aspects(passed=aspects, instance_default=self._aspects)

        if not isinstance(algorithm, TpmAlgorithmId):
            err_msg_lines = [
                "MeasuredBoot.read_pcrs: 'algorithm' must be a TpmAlgorithmId.",
                "GOT: value={!r} type={}".format(algorithm, type(algorithm).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg) from None

        transport_kind = getattr(self._agent, "transport_kind", None)
        if transport_kind is None:
            transport_kind = getattr(self._agent, "_transport_kind", None)

        if transport_kind not in _SUPPORTED_TRANSPORT_KINDS:
            err_msg_lines = [
                "MeasuredBoot.read_pcrs: unsupported transport_kind.",
                "TRANSPORT_KIND: {!r}".format(transport_kind),
                "SUPPORTED: {}".format(list(_SUPPORTED_TRANSPORT_KINDS)),
                "HINT: tpm2_pcrread requires a booted host reachable via ssh-host.",
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg) from None

        alg_name = ALG_HASHLIB_NAMES[algorithm]
        command = "tpm2_pcrread {}".format(alg_name)

        self._logger.info(
            "uefi.measuredboot: read_pcrs begin algorithm=%s", alg_name,
        )

        try:
            status, stdout, stderr = self._agent.run_cmd(
                command=command,
                aspects=effective,
            )
        except UefiShellError:
            raise
        except UefiError:
            raise

        if status != 0:
            err_msg_lines = [
                "MeasuredBoot.read_pcrs: tpm2_pcrread returned a non-zero exit status.",
                "ALGORITHM: {}".format(algorithm.name),
                "STATUS: {}".format(status),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiShellError(
                err_msg,
                command=command,
                status=status,
                stdout=stdout,
                stderr=stderr,
            ) from None

        pcr_map = _parse_tpm2_pcrread_stdout(stdout=stdout, algorithm=algorithm)

        self._logger.info(
            "uefi.measuredboot: read_pcrs complete algorithm=%s count=%d",
            alg_name, len(pcr_map),
        )

        rtnval = pcr_map
        return rtnval
