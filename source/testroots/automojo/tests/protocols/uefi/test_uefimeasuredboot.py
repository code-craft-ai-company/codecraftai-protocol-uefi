"""
    .. module:: test_uefimeasuredboot
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Unit tests for the
               :mod:`automojo.protocols.uefi.uefimeasuredboot` module.

               Every test builds a synthetic crypto-agile TPM 2.0 event
               log in memory (a ``TCG_EfiSpecIDEvent`` header plus two
               ``TCG_PCR_EVENT2`` entries with SHA-256 digests) and
               exercises :meth:`EventLog.from_bytes`,
               :meth:`EventLog.events_for_pcr`,
               :meth:`EventLog.reconstruct_pcrs`, and
               :meth:`EventLog.validate_against`.  A minimal
               :class:`_FakeUefiAgent` stands in for
               :class:`automojo.protocols.uefi.uefiagent.UefiAgent`
               so :meth:`MeasuredBoot.read_event_log` and
               :meth:`MeasuredBoot.read_pcrs` may be exercised without
               touching a transport.  Unsupported transports raise
               :class:`UefiError`; a captured ``tpm2_pcrread`` stdout
               tolerates both ``tpm2-tools`` v4 and v5 layouts.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Automation Mojo LLC"
__credits__ = []


import base64
import hashlib
import struct
import unittest

from typing import Any, Callable, Dict, List, Optional, Tuple

from automojo.protocols.uefi.uefiaspects import UefiCmdAspects
from automojo.protocols.uefi.uefiexceptions import UefiError, UefiShellError
from automojo.protocols.uefi.uefimeasuredboot import (
    ALG_DIGEST_SIZES,
    ALG_HASHLIB_NAMES,
    EventLog,
    EventType,
    MeasuredBoot,
    PcrEvent,
    SPEC_ID_EVENT_SIGNATURE,
    TcgSpecIdEvent,
    TpmAlgorithmId,
    TpmDigest,
    _parse_tpm2_pcrread_stdout,
)


# ---------------------------------------------------------------------------
# Binary-log builders
# ---------------------------------------------------------------------------

SHA256_SIZE: int = 32


def _build_spec_id_event_payload(algorithms: List[Tuple[int, int]],
                                 vendor_info: bytes = b"") -> bytes:
    """
        Build a ``TCG_EfiSpecIDEvent`` payload.

        :param algorithms: List of ``(alg_id, digest_size)`` pairs.
        :param vendor_info: Vendor-specific trailing bytes.

        :returns: Encoded payload bytes.
    """
    parts: List[bytes] = []
    parts.append(SPEC_ID_EVENT_SIGNATURE)           # 16 bytes
    parts.append(struct.pack("<I", 0))              # platformClass = Client
    parts.append(struct.pack("<B", 0))              # specVersionMinor
    parts.append(struct.pack("<B", 2))              # specVersionMajor
    parts.append(struct.pack("<B", 0))              # specErrata
    parts.append(struct.pack("<B", 2))              # uintnSize (64-bit)
    parts.append(struct.pack("<I", len(algorithms)))

    for alg_pair in algorithms:
        alg_id, digest_size = alg_pair
        parts.append(struct.pack("<H", alg_id))
        parts.append(struct.pack("<H", digest_size))

    parts.append(struct.pack("<B", len(vendor_info)))
    parts.append(vendor_info)

    rtnval = b"".join(parts)
    return rtnval


def _build_first_event(spec_payload: bytes) -> bytes:
    """
        Build a legacy ``TCG_PCR_EVENT`` header carrying ``spec_payload``
        as its ``TCG_EfiSpecIDEvent`` event_data.

        :param spec_payload: The payload bytes from
                             :func:`_build_spec_id_event_payload`.

        :returns: 32-byte header + payload.
    """
    parts: List[bytes] = []
    parts.append(struct.pack("<I", 0))                # PCRIndex
    parts.append(struct.pack("<I", 0x00000003))       # EV_NO_ACTION
    parts.append(b"\x00" * 20)                        # Digest[20]
    parts.append(struct.pack("<I", len(spec_payload)))
    parts.append(spec_payload)

    rtnval = b"".join(parts)
    return rtnval


def _build_pcr_event2(pcr_index: int, event_type: int,
                     digests: List[Tuple[int, bytes]],
                     event_data: bytes) -> bytes:
    """
        Build a ``TCG_PCR_EVENT2`` record.

        :param pcr_index: Target PCR.
        :param event_type: Event type code.
        :param digests: List of ``(alg_id, digest_bytes)`` pairs.
        :param event_data: The event-data payload bytes.

        :returns: Encoded record.
    """
    parts: List[bytes] = []
    parts.append(struct.pack("<I", pcr_index))
    parts.append(struct.pack("<I", event_type))
    parts.append(struct.pack("<I", len(digests)))

    for digest_pair in digests:
        alg_id, digest_bytes = digest_pair
        parts.append(struct.pack("<H", alg_id))
        parts.append(digest_bytes)

    parts.append(struct.pack("<I", len(event_data)))
    parts.append(event_data)

    rtnval = b"".join(parts)
    return rtnval


def _build_synthetic_log() -> Tuple[bytes, bytes, bytes, int, int]:
    """
        Build a synthetic 2-event crypto-agile log.

        The log advertises a SHA-256-only algorithm vector and extends
        PCR 0 twice with two distinct per-event digests.

        :returns: ``(log_bytes, digest_a, digest_b, pcr_index_a, pcr_index_b)``.
    """
    digest_a = hashlib.sha256(b"event-a").digest()
    digest_b = hashlib.sha256(b"event-b").digest()

    spec_payload = _build_spec_id_event_payload(
        algorithms=[(int(TpmAlgorithmId.SHA256), SHA256_SIZE)],
        vendor_info=b"",
    )
    first_event = _build_first_event(spec_payload=spec_payload)

    event_a = _build_pcr_event2(
        pcr_index=0,
        event_type=int(EventType.EV_S_CRTM_VERSION),
        digests=[(int(TpmAlgorithmId.SHA256), digest_a)],
        event_data=b"A-data",
    )
    event_b = _build_pcr_event2(
        pcr_index=1,
        event_type=int(EventType.EV_EFI_VARIABLE_DRIVER_CONFIG),
        digests=[(int(TpmAlgorithmId.SHA256), digest_b)],
        event_data=b"B-data",
    )

    log_bytes = first_event + event_a + event_b
    rtnval = (log_bytes, digest_a, digest_b, 0, 1)
    return rtnval


def _extend_sha256(current: bytes, incoming: bytes) -> bytes:
    """
        Apply one TCG Extend operation: ``H(current || incoming)``.

        :param current: Current PCR value.
        :param incoming: Incoming event digest.

        :returns: The new PCR value.
    """
    hasher = hashlib.sha256()
    hasher.update(current)
    hasher.update(incoming)
    rtnval = hasher.digest()
    return rtnval


# ---------------------------------------------------------------------------
# Fake UefiAgent
# ---------------------------------------------------------------------------

class _FakeUefiAgent:
    """
        Minimal :class:`UefiAgent` stand-in.  Holds a configurable
        ``transport_kind`` and a callable responder keyed by the command
        string.
    """

    def __init__(self, transport_kind: str,
                       responder: Optional[Callable[[str, Optional[UefiCmdAspects]], Tuple[int, str, str]]] = None) -> None:
        """
            Construct the fake agent.

            :param transport_kind: The ``transport_kind`` string.
            :param responder: Callable mapping ``(command, aspects)`` to
                              ``(status, stdout, stderr)``.
        """
        self.transport_kind: str = transport_kind
        self.calls: List[Tuple[str, Optional[UefiCmdAspects]]] = []

        if responder is None:
            default_responder: Callable[[str, Optional[UefiCmdAspects]], Tuple[int, str, str]] = (
                lambda command, aspects: (0, "", "")
            )
            self._responder = default_responder
        else:
            self._responder = responder
        return

    def run_cmd(self, command: str,
                      exp_status: Any = 0,
                      user: Optional[str] = None,
                      aspects: Optional[UefiCmdAspects] = None) -> Tuple[int, str, str]:
        """
            Record the call and delegate to the canned responder.
        """
        self.calls.append((command, aspects))
        rtnval = self._responder(command, aspects)
        return rtnval


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TcgSpecIdEventTests(unittest.TestCase):
    """
        Unit tests for the :class:`TcgSpecIdEvent` decoder.
    """

    def test_signature_accept(self) -> None:
        """
            The canonical SHA-256 spec-ID payload decodes cleanly.
        """
        payload = _build_spec_id_event_payload(
            algorithms=[(int(TpmAlgorithmId.SHA256), SHA256_SIZE)],
        )

        spec = TcgSpecIdEvent.from_bytes(data=payload)

        self.assertEqual(spec.platform_class, 0)
        self.assertEqual(spec.spec_version_major, 2)
        self.assertEqual(spec.num_algorithms, 1)
        self.assertEqual(spec.algorithms, ((TpmAlgorithmId.SHA256, SHA256_SIZE),))
        self.assertEqual(spec.vendor_info, b"")
        return

    def test_signature_reject(self) -> None:
        """
            A tampered signature is rejected with a descriptive
            :class:`UefiError`.
        """
        payload = _build_spec_id_event_payload(
            algorithms=[(int(TpmAlgorithmId.SHA256), SHA256_SIZE)],
        )
        tampered = b"BadSig!!Event03\x00" + payload[16:]

        with self.assertRaises(UefiError) as caught:
            TcgSpecIdEvent.from_bytes(data=tampered)

        rendered = str(caught.exception)
        self.assertIn("signature mismatch", rendered)
        return

    def test_digest_size_mismatch_rejected(self) -> None:
        """
            A declared digest size that disagrees with the algorithm
            registry is rejected.
        """
        payload = _build_spec_id_event_payload(
            algorithms=[(int(TpmAlgorithmId.SHA256), SHA256_SIZE + 1)],
        )

        with self.assertRaises(UefiError) as caught:
            TcgSpecIdEvent.from_bytes(data=payload)

        rendered = str(caught.exception)
        self.assertIn("digest size", rendered)
        return


class EventLogFromBytesTests(unittest.TestCase):
    """
        Unit tests for :meth:`EventLog.from_bytes`.
    """

    def test_parses_synthetic_log(self) -> None:
        """
            A synthetic 2-event log parses into a 1-item spec-ID header
            + 2 ``TCG_PCR_EVENT2`` records.
        """
        log_bytes, digest_a, digest_b, pcr_a, pcr_b = _build_synthetic_log()

        log = EventLog.from_bytes(data=log_bytes)

        self.assertEqual(log.spec_id.num_algorithms, 1)
        self.assertEqual(len(log.events), 2)

        event_a = log.events[0]
        self.assertEqual(event_a.pcr_index, pcr_a)
        self.assertEqual(event_a.event_type, EventType.EV_S_CRTM_VERSION)
        self.assertEqual(len(event_a.digests), 1)
        self.assertEqual(event_a.digests[0].algorithm, TpmAlgorithmId.SHA256)
        self.assertEqual(event_a.digests[0].value, digest_a)
        self.assertEqual(event_a.event_data, b"A-data")

        event_b = log.events[1]
        self.assertEqual(event_b.pcr_index, pcr_b)
        self.assertEqual(event_b.event_type, EventType.EV_EFI_VARIABLE_DRIVER_CONFIG)
        self.assertEqual(event_b.digests[0].value, digest_b)
        return

    def test_truncated_first_event_raises(self) -> None:
        """
            A log truncated inside the first-event header raises
            :class:`UefiError`.
        """
        log_bytes, _, _, _, _ = _build_synthetic_log()
        truncated = log_bytes[:20]

        with self.assertRaises(UefiError) as caught:
            EventLog.from_bytes(data=truncated)

        rendered = str(caught.exception)
        self.assertIn("first-event header", rendered)
        return

    def test_truncated_pcr_event2_raises(self) -> None:
        """
            A log truncated inside a ``TCG_PCR_EVENT2`` record raises
            :class:`UefiError` with the event index in ``details``.
        """
        log_bytes, _, _, _, _ = _build_synthetic_log()
        truncated = log_bytes[:-4]  # shear off the last 4 bytes of event_b

        with self.assertRaises(UefiError):
            EventLog.from_bytes(data=truncated)
        return

    def test_non_bytes_input_raises(self) -> None:
        """
            Passing a non-bytes argument is rejected with a
            :class:`UefiError`.
        """
        with self.assertRaises(UefiError):
            EventLog.from_bytes(data="not-bytes")  # type: ignore[arg-type]
        return


class EventsForPcrTests(unittest.TestCase):
    """
        Unit tests for :meth:`EventLog.events_for_pcr`.
    """

    def test_filters_by_pcr_index(self) -> None:
        """
            ``events_for_pcr`` yields only the events whose ``pcr_index``
            equals the supplied value and preserves log order.
        """
        log_bytes, _, _, pcr_a, pcr_b = _build_synthetic_log()
        log = EventLog.from_bytes(data=log_bytes)

        events_for_a = list(log.events_for_pcr(pcr_index=pcr_a))
        events_for_b = list(log.events_for_pcr(pcr_index=pcr_b))
        events_for_23 = list(log.events_for_pcr(pcr_index=23))

        self.assertEqual(len(events_for_a), 1)
        self.assertEqual(events_for_a[0].pcr_index, pcr_a)

        self.assertEqual(len(events_for_b), 1)
        self.assertEqual(events_for_b[0].pcr_index, pcr_b)

        self.assertEqual(len(events_for_23), 0)
        return

    def test_non_int_pcr_raises(self) -> None:
        """
            A non-int ``pcr_index`` is rejected.  Generator bodies raise
            on first iteration, so we materialize the iterator.
        """
        log_bytes, _, _, _, _ = _build_synthetic_log()
        log = EventLog.from_bytes(data=log_bytes)

        with self.assertRaises(UefiError):
            list(log.events_for_pcr(pcr_index="zero"))  # type: ignore[arg-type]
        return


class ReconstructPcrsTests(unittest.TestCase):
    """
        Unit tests for :meth:`EventLog.reconstruct_pcrs`.
    """

    def test_reconstructs_extend_chain(self) -> None:
        """
            Each PCR is reconstructed by extending a zero accumulator
            through each event destined for that PCR, in order.
        """
        log_bytes, digest_a, digest_b, pcr_a, pcr_b = _build_synthetic_log()
        log = EventLog.from_bytes(data=log_bytes)

        reconstructed = log.reconstruct_pcrs(algorithm=TpmAlgorithmId.SHA256)

        zero = b"\x00" * SHA256_SIZE
        expected_a = _extend_sha256(current=zero, incoming=digest_a)
        expected_b = _extend_sha256(current=zero, incoming=digest_b)

        self.assertEqual(reconstructed[pcr_a], expected_a)
        self.assertEqual(reconstructed[pcr_b], expected_b)
        return

    def test_reconstructs_multiple_events_same_pcr(self) -> None:
        """
            Two events destined for the same PCR extend the same
            accumulator in order.
        """
        digest_x = hashlib.sha256(b"x").digest()
        digest_y = hashlib.sha256(b"y").digest()

        spec_payload = _build_spec_id_event_payload(
            algorithms=[(int(TpmAlgorithmId.SHA256), SHA256_SIZE)],
        )
        first_event = _build_first_event(spec_payload=spec_payload)
        event_x = _build_pcr_event2(
            pcr_index=7,
            event_type=int(EventType.EV_SEPARATOR),
            digests=[(int(TpmAlgorithmId.SHA256), digest_x)],
            event_data=b"",
        )
        event_y = _build_pcr_event2(
            pcr_index=7,
            event_type=int(EventType.EV_SEPARATOR),
            digests=[(int(TpmAlgorithmId.SHA256), digest_y)],
            event_data=b"",
        )
        log_bytes = first_event + event_x + event_y

        log = EventLog.from_bytes(data=log_bytes)
        reconstructed = log.reconstruct_pcrs(algorithm=TpmAlgorithmId.SHA256)

        zero = b"\x00" * SHA256_SIZE
        after_x = _extend_sha256(current=zero, incoming=digest_x)
        after_y = _extend_sha256(current=after_x, incoming=digest_y)

        self.assertEqual(reconstructed[7], after_y)
        return

    def test_algorithm_type_enforced(self) -> None:
        """
            Passing a non-:class:`TpmAlgorithmId` raises
            :class:`UefiError`.
        """
        log_bytes, _, _, _, _ = _build_synthetic_log()
        log = EventLog.from_bytes(data=log_bytes)

        with self.assertRaises(UefiError):
            log.reconstruct_pcrs(algorithm="sha256")  # type: ignore[arg-type]
        return


class ValidateAgainstTests(unittest.TestCase):
    """
        Unit tests for :meth:`EventLog.validate_against`.
    """

    def test_match_returns_none(self) -> None:
        """
            When observed PCRs match the reconstruction the method
            returns without raising.
        """
        log_bytes, digest_a, digest_b, pcr_a, pcr_b = _build_synthetic_log()
        log = EventLog.from_bytes(data=log_bytes)

        zero = b"\x00" * SHA256_SIZE
        observed: Dict[int, bytes] = {
            pcr_a: _extend_sha256(current=zero, incoming=digest_a),
            pcr_b: _extend_sha256(current=zero, incoming=digest_b),
        }

        # No assertion needed: the call must not raise.
        log.validate_against(observed_pcrs=observed, algorithm=TpmAlgorithmId.SHA256)
        return

    def test_mismatch_raises_with_details(self) -> None:
        """
            A single-PCR mismatch is reported with the diff populated in
            ``UefiError.details``.
        """
        log_bytes, digest_a, digest_b, pcr_a, pcr_b = _build_synthetic_log()
        log = EventLog.from_bytes(data=log_bytes)

        zero = b"\x00" * SHA256_SIZE
        expected_b = _extend_sha256(current=zero, incoming=digest_b)

        tampered = b"\xff" * SHA256_SIZE
        observed: Dict[int, bytes] = {
            pcr_a: tampered,
            pcr_b: expected_b,
        }

        with self.assertRaises(UefiError) as caught:
            log.validate_against(
                observed_pcrs=observed,
                algorithm=TpmAlgorithmId.SHA256,
            )

        uefi_error = caught.exception
        self.assertEqual(len(uefi_error.details), 1)
        detail_line = uefi_error.details[0]
        self.assertIn("pcr={}".format(pcr_a), detail_line)
        self.assertIn("alg=SHA256", detail_line)
        self.assertIn(tampered.hex(), detail_line)
        return

    def test_missing_observation_reported(self) -> None:
        """
            A PCR touched by the log but absent from the observation set
            is reported as ``missing_observation``.
        """
        log_bytes, digest_a, _, pcr_a, _ = _build_synthetic_log()
        log = EventLog.from_bytes(data=log_bytes)

        zero = b"\x00" * SHA256_SIZE
        only_a: Dict[int, bytes] = {
            pcr_a: _extend_sha256(current=zero, incoming=digest_a),
        }

        with self.assertRaises(UefiError) as caught:
            log.validate_against(
                observed_pcrs=only_a,
                algorithm=TpmAlgorithmId.SHA256,
            )

        combined = "\n".join(caught.exception.details)
        self.assertIn("missing_observation", combined)
        return


class Tpm2PcrreadParserTests(unittest.TestCase):
    """
        Unit tests for :func:`_parse_tpm2_pcrread_stdout`.
    """

    def test_v5_output_with_bank_header(self) -> None:
        """
            Parse ``tpm2-tools`` v5 output (bank header + indented
            ``index : 0xDEAD...`` lines).
        """
        digest_0 = hashlib.sha256(b"pcr0").hexdigest()
        digest_1 = hashlib.sha256(b"pcr1").hexdigest()
        stdout = (
            "sha256:\n"
            "  0 : 0x{}\n"
            "  1 : 0x{}\n".format(digest_0.upper(), digest_1.upper())
        )

        parsed = _parse_tpm2_pcrread_stdout(
            stdout=stdout, algorithm=TpmAlgorithmId.SHA256,
        )

        self.assertEqual(parsed[0], bytes.fromhex(digest_0))
        self.assertEqual(parsed[1], bytes.fromhex(digest_1))
        return

    def test_v4_output_no_prefix(self) -> None:
        """
            Parse ``tpm2-tools`` v4 output without the ``0x`` prefix.
        """
        digest_0 = hashlib.sha256(b"x").hexdigest()
        stdout = (
            "sha256:\n"
            "0: {}\n".format(digest_0)
        )

        parsed = _parse_tpm2_pcrread_stdout(
            stdout=stdout, algorithm=TpmAlgorithmId.SHA256,
        )

        self.assertEqual(parsed[0], bytes.fromhex(digest_0))
        return

    def test_empty_output_raises(self) -> None:
        """
            Stdout that carries no PCR lines raises :class:`UefiError`.
        """
        with self.assertRaises(UefiError):
            _parse_tpm2_pcrread_stdout(
                stdout="no pcr lines here\n",
                algorithm=TpmAlgorithmId.SHA256,
            )
        return


class MeasuredBootFacadeTests(unittest.TestCase):
    """
        Unit tests for the :class:`MeasuredBoot` facade.
    """

    def test_read_event_log_over_ssh_host(self) -> None:
        """
            On the ssh-host transport the facade issues a
            ``cat ... | base64`` command and returns a decoded
            :class:`EventLog`.
        """
        log_bytes, _, _, _, _ = _build_synthetic_log()
        encoded = base64.b64encode(log_bytes).decode("ascii")

        def responder(command: str,
                      aspects: Optional[UefiCmdAspects]) -> Tuple[int, str, str]:
            rtnval: Tuple[int, str, str] = (0, encoded, "")
            return rtnval

        fake_agent = _FakeUefiAgent(transport_kind="ssh-host", responder=responder)
        facade = MeasuredBoot(agent=fake_agent)

        log = facade.read_event_log()

        self.assertEqual(len(log.events), 2)
        self.assertEqual(len(fake_agent.calls), 1)
        issued_command, _ = fake_agent.calls[0]
        self.assertIn("binary_bios_measurements", issued_command)
        self.assertIn("base64", issued_command)
        return

    def test_read_event_log_serial_raises(self) -> None:
        """
            Serial transport is not supported for event-log readout.
        """
        fake_agent = _FakeUefiAgent(transport_kind="serial")
        facade = MeasuredBoot(agent=fake_agent)

        with self.assertRaises(UefiError) as caught:
            facade.read_event_log()

        self.assertIn("unsupported transport_kind", str(caught.exception))
        return

    def test_read_event_log_ipmi_sol_raises(self) -> None:
        """
            IPMI-SOL transport is not supported for event-log readout.
        """
        fake_agent = _FakeUefiAgent(transport_kind="ipmi-sol")
        facade = MeasuredBoot(agent=fake_agent)

        with self.assertRaises(UefiError):
            facade.read_event_log()
        return

    def test_read_event_log_redfish_raises(self) -> None:
        """
            Redfish transport is not supported for event-log readout.
        """
        fake_agent = _FakeUefiAgent(transport_kind="redfish")
        facade = MeasuredBoot(agent=fake_agent)

        with self.assertRaises(UefiError):
            facade.read_event_log()
        return

    def test_read_event_log_non_zero_status_raises_shell_error(self) -> None:
        """
            Non-zero exit status from the ``cat | base64`` shell-out is
            converted to :class:`UefiShellError` with the raw capture
            attached.
        """
        def responder(command: str,
                      aspects: Optional[UefiCmdAspects]) -> Tuple[int, str, str]:
            rtnval: Tuple[int, str, str] = (1, "", "cat: no such file")
            return rtnval

        fake_agent = _FakeUefiAgent(transport_kind="ssh-host", responder=responder)
        facade = MeasuredBoot(agent=fake_agent)

        with self.assertRaises(UefiShellError) as caught:
            facade.read_event_log()

        self.assertEqual(caught.exception.status, 1)
        self.assertEqual(caught.exception.stderr, "cat: no such file")
        return

    def test_read_pcrs_parses_stdout(self) -> None:
        """
            The ``read_pcrs`` facade issues ``tpm2_pcrread sha256`` and
            returns the decoded PCR map.
        """
        digest_0 = hashlib.sha256(b"pcr0").hexdigest()
        digest_1 = hashlib.sha256(b"pcr1").hexdigest()
        fake_stdout = (
            "sha256:\n"
            "  0 : 0x{}\n"
            "  1 : 0x{}\n".format(digest_0, digest_1)
        )

        def responder(command: str,
                      aspects: Optional[UefiCmdAspects]) -> Tuple[int, str, str]:
            rtnval: Tuple[int, str, str] = (0, fake_stdout, "")
            return rtnval

        fake_agent = _FakeUefiAgent(transport_kind="ssh-host", responder=responder)
        facade = MeasuredBoot(agent=fake_agent)

        pcrs = facade.read_pcrs(algorithm=TpmAlgorithmId.SHA256)

        self.assertEqual(pcrs[0], bytes.fromhex(digest_0))
        self.assertEqual(pcrs[1], bytes.fromhex(digest_1))
        self.assertEqual(len(fake_agent.calls), 1)
        issued_command, _ = fake_agent.calls[0]
        self.assertEqual(issued_command, "tpm2_pcrread sha256")
        return

    def test_read_pcrs_non_ssh_host_raises(self) -> None:
        """
            ``read_pcrs`` is refused on non-ssh-host transports.
        """
        fake_agent = _FakeUefiAgent(transport_kind="redfish")
        facade = MeasuredBoot(agent=fake_agent)

        with self.assertRaises(UefiError):
            facade.read_pcrs(algorithm=TpmAlgorithmId.SHA256)
        return

    def test_read_pcrs_non_zero_status_raises_shell_error(self) -> None:
        """
            Non-zero exit of ``tpm2_pcrread`` raises
            :class:`UefiShellError`.
        """
        def responder(command: str,
                      aspects: Optional[UefiCmdAspects]) -> Tuple[int, str, str]:
            rtnval: Tuple[int, str, str] = (127, "", "command not found")
            return rtnval

        fake_agent = _FakeUefiAgent(transport_kind="ssh-host", responder=responder)
        facade = MeasuredBoot(agent=fake_agent)

        with self.assertRaises(UefiShellError) as caught:
            facade.read_pcrs(algorithm=TpmAlgorithmId.SHA256)

        self.assertEqual(caught.exception.status, 127)
        return


# ---------------------------------------------------------------------------
# Algorithm-table sanity
# ---------------------------------------------------------------------------

class AlgorithmTableTests(unittest.TestCase):
    """
        Sanity checks for :data:`ALG_DIGEST_SIZES` and
        :data:`ALG_HASHLIB_NAMES`.
    """

    def test_digest_size_map_covers_enum(self) -> None:
        """
            Every :class:`TpmAlgorithmId` has a digest-size entry.
        """
        for alg in TpmAlgorithmId:
            self.assertIn(alg, ALG_DIGEST_SIZES)
            self.assertGreater(ALG_DIGEST_SIZES[alg], 0)
        return

    def test_hashlib_name_map_covers_enum(self) -> None:
        """
            Every :class:`TpmAlgorithmId` has a hashlib-name entry.
        """
        for alg in TpmAlgorithmId:
            self.assertIn(alg, ALG_HASHLIB_NAMES)
            self.assertIsInstance(ALG_HASHLIB_NAMES[alg], str)
        return

    def test_tpm_digest_record_stores_value(self) -> None:
        """
            The :class:`TpmDigest` frozen dataclass stores the raw value
            verbatim.
        """
        digest_bytes = b"\x01" * SHA256_SIZE
        record = TpmDigest(algorithm=TpmAlgorithmId.SHA256, value=digest_bytes)

        self.assertEqual(record.algorithm, TpmAlgorithmId.SHA256)
        self.assertEqual(record.value, digest_bytes)
        return

    def test_pcr_event_digest_for_lookup(self) -> None:
        """
            :meth:`PcrEvent.digest_for` returns the matching digest or
            :data:`None`.
        """
        sha256_value = b"\x02" * SHA256_SIZE
        sha1_value = b"\x03" * 20

        event = PcrEvent(
            pcr_index=0,
            event_type=EventType.EV_SEPARATOR,
            digests=(
                TpmDigest(algorithm=TpmAlgorithmId.SHA1, value=sha1_value),
                TpmDigest(algorithm=TpmAlgorithmId.SHA256, value=sha256_value),
            ),
            event_data=b"",
        )

        self.assertEqual(event.digest_for(algorithm=TpmAlgorithmId.SHA256), sha256_value)
        self.assertEqual(event.digest_for(algorithm=TpmAlgorithmId.SHA1), sha1_value)
        self.assertIsNone(event.digest_for(algorithm=TpmAlgorithmId.SHA512))
        return


if __name__ == "__main__":
    unittest.main()
