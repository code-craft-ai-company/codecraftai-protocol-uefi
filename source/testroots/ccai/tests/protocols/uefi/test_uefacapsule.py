"""
    .. module:: test_uefacapsule
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Unit tests for the :mod:`ccai.protocols.uefi.uefacapsule`
               module.  Covers the :class:`CapsuleHeader` binary round
               trip, the :class:`CapsuleBuilder` raw-body and FMP-payload
               assembly paths, the :class:`CapsuleDelivery` backend
               dispatch across the three supported transport kinds, and
               the :class:`CapsuleReport` variable-parsing path.

               The tests never touch real transports or filesystems; a
               pair of fake collaborators stand in for
               :class:`UefiAgent` and :class:`VariableStore`.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Code Craft AI LLC"
__credits__ = []


from ccai import testbase

import base64
import uuid

from typing import Any, Callable, Iterator, List, Optional, Tuple

from ccai.protocols.uefi.uefiaspects import (
    DEFAULT_CMD_ASPECTS,
    UefiCmdAspects,
)
from ccai.protocols.uefi.ueficonst import guid_to_bytes
from ccai.protocols.uefi.uefiexceptions import UefiCapsuleError
from ccai.protocols.uefi.uefiguids import EFI_CAPSULE_REPORT_GUID
from ccai.protocols.uefi.uefihelpers import (
    pack_u32_le,
    pack_u64_le,
)
from ccai.protocols.uefi.uefivariables import (
    UefiVariable,
    UefiVariableAttributes,
)
from ccai.protocols.uefi.uefacapsule import (
    CAPSULE_FLAGS_INITIATE_RESET,
    CAPSULE_FLAGS_PERSIST_ACROSS_RESET,
    CAPSULE_FLAGS_POPULATE_SYSTEM_TABLE,
    CAPSULE_HEADER_SIZE,
    FMP_CAPSULE_HEADER_FIXED_SIZE,
    FMP_PAYLOAD_HEADER_SIZE,
    FMP_PAYLOAD_SIGNATURE,
    Capsule,
    CapsuleBuilder,
    CapsuleDelivery,
    CapsuleHeader,
    CapsuleReport,
    CapsuleReportEntry,
    FmpCapsuleHeader,
    FmpPayloadHeader,
)


# ---------------------------------------------------------------------------
# Well-known capsule GUIDs used throughout the tests
# ---------------------------------------------------------------------------

SAMPLE_CAPSULE_GUID: uuid.UUID = uuid.UUID("11111111-2222-3333-4444-555555555555")
OTHER_CAPSULE_GUID: uuid.UUID = uuid.UUID("99998888-7777-6666-5555-444444444444")


# ---------------------------------------------------------------------------
# Fake collaborators
# ---------------------------------------------------------------------------

class _FakeUefiAgent:
    """
        Minimal agent stand-in.  Records each ``run_cmd`` invocation and
        returns a canned ``(status, stdout, stderr)`` tuple via a
        caller-supplied responder.
    """

    def __init__(self, transport_kind: str,
                       responder: Optional[Callable[[str, Optional[UefiCmdAspects]], Tuple[int, str, str]]] = None) -> None:
        """
            Construct the fake agent.

            :param transport_kind: The transport-kind selector.
            :param responder: Optional canned-response producer.
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


class _FakeVariableStore:
    """
        Minimal :class:`VariableStore` stand-in providing
        :meth:`enumerate_variables` and :meth:`get_variable`.
    """

    def __init__(self, variables: List[UefiVariable]) -> None:
        """
            Initialize the store with a list of canned variables.

            :param variables: The variable records that
                              :meth:`enumerate_variables` yields.
        """
        self._variables: List[UefiVariable] = list(variables)
        return

    def enumerate_variables(self, aspects: Optional[UefiCmdAspects] = None,
                                  name_filter: Optional[str] = None) -> Iterator[Tuple[str, uuid.UUID]]:
        """
            Stream the canned ``(name, vendor_guid)`` tuples.
        """
        for variable in self._variables:
            yield (variable.name, variable.vendor_guid)
        return

    def get_variable(self, name: str,
                           vendor_guid: uuid.UUID,
                           aspects: Optional[UefiCmdAspects] = None) -> UefiVariable:
        """
            Return the canned variable whose ``(name, guid)`` match.
        """
        for variable in self._variables:
            if variable.name == name and variable.vendor_guid == vendor_guid:
                rtnval = variable
                return rtnval

        err_msg = "Variable not found: name={!r} guid={!r}".format(name, vendor_guid)
        raise AssertionError(err_msg)


def _build_report_payload(capsule_guid: uuid.UUID,
                          processed: bool,
                          last_attempt_version: int,
                          last_attempt_status: int) -> bytes:
    """
        Build a UEFI §8.5.6 ``EFI_CAPSULE_RESULT_VARIABLE_HEADER``
        payload suitable for feeding to :meth:`CapsuleReport.list_reports`.

        :param capsule_guid: The capsule GUID.
        :param processed: Whether the firmware reports the capsule as
                          processed.
        :param last_attempt_version: The reported last-attempt version.
        :param last_attempt_status: The reported last-attempt status.

        :returns: A 0x30-byte payload.
    """
    processed_field = 1 if processed else 0
    variable_total_size = 0x30

    total_size_bytes = pack_u32_le(value=variable_total_size)
    reserved_bytes = pack_u32_le(value=0)
    guid_bytes = guid_to_bytes(capsule_guid)
    processed_bytes = pack_u64_le(value=processed_field)
    version_bytes = pack_u64_le(value=last_attempt_version)
    status_bytes = pack_u64_le(value=last_attempt_status)

    rtnval = (
        total_size_bytes + reserved_bytes + guid_bytes +
        processed_bytes + version_bytes + status_bytes
    )
    return rtnval


# ---------------------------------------------------------------------------
# CapsuleHeader
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# CapsuleHeaderTestCase
# ----------------------------------------------------------------------

"""
    Exercise the on-wire round trip of :class:`CapsuleHeader`.
"""

def test_header_round_trip_through_to_bytes_and_from_bytes() -> None:
    """
        Verify that ``to_bytes -> from_bytes`` is the identity.
    """
    header = CapsuleHeader(
        capsule_guid=SAMPLE_CAPSULE_GUID,
        header_size=CAPSULE_HEADER_SIZE,
        flags=CAPSULE_FLAGS_PERSIST_ACROSS_RESET,
        capsule_image_size=CAPSULE_HEADER_SIZE + 64,
    )

    serialized = header.to_bytes()
    testbase.assert_equal(len(serialized), CAPSULE_HEADER_SIZE)

    decoded = CapsuleHeader.from_bytes(data=serialized)

    testbase.assert_equal(decoded.capsule_guid, SAMPLE_CAPSULE_GUID)
    testbase.assert_equal(decoded.header_size, CAPSULE_HEADER_SIZE)
    testbase.assert_equal(decoded.flags, CAPSULE_FLAGS_PERSIST_ACROSS_RESET)
    testbase.assert_equal(decoded.capsule_image_size, CAPSULE_HEADER_SIZE + 64)
    return

def test_header_layout_places_guid_at_offset_zero() -> None:
    """
        The GUID slice must occupy bytes 0..15 of the header buffer.
    """
    header = CapsuleHeader(
        capsule_guid=SAMPLE_CAPSULE_GUID,
        header_size=CAPSULE_HEADER_SIZE,
        flags=0,
        capsule_image_size=CAPSULE_HEADER_SIZE,
    )

    serialized = header.to_bytes()
    expected_guid_bytes = guid_to_bytes(SAMPLE_CAPSULE_GUID)

    testbase.assert_equal(serialized[0:16], expected_guid_bytes)
    return

def test_header_rejects_undersized_buffer() -> None:
    """
        ``from_bytes`` raises :class:`UefiCapsuleError` when the
        buffer is shorter than 32 bytes.
    """
    with testbase.raises(UefiCapsuleError):
        CapsuleHeader.from_bytes(data=b"\x00" * 10)
    return


# ---------------------------------------------------------------------------
# CapsuleBuilder
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# CapsuleBuilderTestCase
# ----------------------------------------------------------------------

"""
    Exercise :class:`CapsuleBuilder` assembly, size accounting, and
    round-trip through :class:`Capsule.from_bytes`.
"""

def test_build_empty_capsule_has_header_size_total() -> None:
    """
        A builder with no body attached produces a capsule whose
        total size equals the fixed header size.
    """
    builder = CapsuleBuilder(capsule_guid=SAMPLE_CAPSULE_GUID, flags=0)
    capsule = builder.build()

    testbase.assert_equal(capsule.header.capsule_image_size, CAPSULE_HEADER_SIZE)
    testbase.assert_equal(len(capsule.to_bytes()), CAPSULE_HEADER_SIZE)
    return

def test_build_round_trips_through_from_bytes() -> None:
    """
        ``CapsuleBuilder.build().to_bytes()`` -> ``Capsule.from_bytes``
        is lossless for the header fields.
    """
    flags = CAPSULE_FLAGS_PERSIST_ACROSS_RESET | CAPSULE_FLAGS_INITIATE_RESET
    builder = CapsuleBuilder(capsule_guid=SAMPLE_CAPSULE_GUID, flags=flags)
    builder = builder.with_raw_body(body=b"ABCD" * 4)

    capsule = builder.build()
    serialized = capsule.to_bytes()

    parsed = Capsule.from_bytes(data=serialized)

    testbase.assert_equal(parsed.header.capsule_guid, SAMPLE_CAPSULE_GUID)
    testbase.assert_equal(parsed.header.flags, flags)
    testbase.assert_equal(parsed.header.header_size, CAPSULE_HEADER_SIZE)
    testbase.assert_equal(parsed.header.capsule_image_size, len(serialized))
    testbase.assert_equal(parsed.body, b"ABCD" * 4)
    return

def test_build_with_fmp_payload_embeds_signature_and_versions() -> None:
    """
        A capsule built with an FMP payload body contains the FMP
        capsule header, a payload-header ``MSS1`` signature, and the
        caller's version fields in little-endian layout.
    """
    fw_version = 0x12345678
    lowest_supported = 0x01020304
    payload = b"PAYLOAD"

    builder = CapsuleBuilder(capsule_guid=SAMPLE_CAPSULE_GUID, flags=0)
    builder = builder.with_fmp_payload(
        fw_version=fw_version,
        lowest_supported=lowest_supported,
        payload=payload,
    )

    capsule = builder.build()
    full_bytes = capsule.to_bytes()

    # Decode the FMP capsule header.
    fmp_capsule_header_bytes = capsule.body[0:FMP_CAPSULE_HEADER_FIXED_SIZE + 4]
    fmp_capsule_header = FmpCapsuleHeader.from_bytes(data=fmp_capsule_header_bytes)

    testbase.assert_equal(fmp_capsule_header.version, 1)
    testbase.assert_equal(fmp_capsule_header.embedded_driver_count, 0)
    testbase.assert_equal(fmp_capsule_header.payload_item_count, 1)
    testbase.assert_equal(len(fmp_capsule_header.item_offsets), 1)

    # Decode the FMP payload header.
    payload_header_start = fmp_capsule_header.item_offsets[0]
    payload_header_end = payload_header_start + FMP_PAYLOAD_HEADER_SIZE
    payload_header_bytes = capsule.body[payload_header_start:payload_header_end]
    payload_header = FmpPayloadHeader.from_bytes(data=payload_header_bytes)

    testbase.assert_equal(payload_header.signature, FMP_PAYLOAD_SIGNATURE)
    testbase.assert_equal(payload_header.fw_version, fw_version)
    testbase.assert_equal(payload_header.lowest_supported_version, lowest_supported)

    # Trailing bytes are the verbatim payload.
    testbase.assert_equal(capsule.body[payload_header_end:], payload)

    # The full capsule is also valid under Capsule.from_bytes.
    reparsed = Capsule.from_bytes(data=full_bytes)
    testbase.assert_equal(reparsed.header.capsule_guid, SAMPLE_CAPSULE_GUID)
    return

def test_with_fmp_payload_and_with_raw_body_are_mutually_exclusive() -> None:
    """
        Combining raw-body and FMP wrapping on the same builder is
        refused with a :class:`UefiCapsuleError`.
    """
    builder = CapsuleBuilder(capsule_guid=SAMPLE_CAPSULE_GUID, flags=0)
    builder = builder.with_raw_body(body=b"X")

    with testbase.raises(UefiCapsuleError):
        builder.with_fmp_payload(fw_version=1, lowest_supported=1, payload=b"Y")
    return

def test_sha256_matches_emitted_bytes() -> None:
    """
        The capsule's ``sha256`` property matches a SHA-256 of its
        serialized bytes.
    """
    import hashlib

    builder = CapsuleBuilder(capsule_guid=SAMPLE_CAPSULE_GUID, flags=0)
    builder = builder.with_raw_body(body=b"BINARY-IMAGE")
    capsule = builder.build()

    expected_digest = hashlib.sha256(capsule.to_bytes()).hexdigest()
    testbase.assert_equal(capsule.sha256, expected_digest)
    return


# ---------------------------------------------------------------------------
# CapsuleDelivery: ssh-host backend
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# CapsuleDeliverySshHostTestCase
# ----------------------------------------------------------------------

"""
    Exercise the ssh-host backend which stages the capsule to a
    temp path and then writes it to ``/dev/efi_capsule_loader`` or
    falls back to ``fwupdmgr``.
"""

def _build_capsule() -> Capsule:
    """
        Build a small capsule usable by every test in the class.
    """
    builder = CapsuleBuilder(capsule_guid=SAMPLE_CAPSULE_GUID, flags=0)
    builder = builder.with_raw_body(body=b"RAW-BODY")
    capsule = builder.build()
    return capsule

def test_ssh_host_delivery_writes_to_capsule_loader_when_device_present() -> None:
    """
        When the probe succeeds, the delivery issues a ``dd``
        command writing to ``/dev/efi_capsule_loader``.
    """
    def responder(command: str, aspects: Optional[UefiCmdAspects]) -> Tuple[int, str, str]:
        # Probe succeeds, stage succeeds, apply succeeds.
        rtnval = (0, "", "")
        return rtnval

    agent = _FakeUefiAgent(transport_kind="ssh-host", responder=responder)
    variables = _FakeVariableStore(variables=[])

    delivery = CapsuleDelivery(agent=agent, variable_store=variables)
    capsule = _build_capsule()

    delivery.deliver(capsule=capsule)

    commands = [command for command, _ in agent.calls]

    testbase.assert_equal(len(commands), 3)
    testbase.assert_true("test -c /dev/efi_capsule_loader" in commands[0])
    testbase.assert_true("base64 -d" in commands[1])
    testbase.assert_true("/dev/efi_capsule_loader" in commands[2])
    testbase.assert_true(commands[2].startswith("dd "))
    return

def test_ssh_host_delivery_falls_back_to_fwupdmgr_when_device_missing() -> None:
    """
        When the probe fails the backend issues a ``fwupdmgr apply``
        command instead.
    """
    def responder(command: str, aspects: Optional[UefiCmdAspects]) -> Tuple[int, str, str]:
        if command.startswith("test -c"):
            rtnval = (1, "", "")
            return rtnval
        rtnval = (0, "", "")
        return rtnval

    agent = _FakeUefiAgent(transport_kind="ssh-host", responder=responder)
    variables = _FakeVariableStore(variables=[])

    delivery = CapsuleDelivery(agent=agent, variable_store=variables)
    capsule = _build_capsule()

    delivery.deliver(capsule=capsule)

    commands = [command for command, _ in agent.calls]
    apply_command = commands[-1]

    testbase.assert_true(apply_command.startswith("fwupdmgr apply"))
    testbase.assert_true("--allow-reinstall" in apply_command)
    return

def test_ssh_host_delivery_raises_when_apply_fails() -> None:
    """
        A non-zero status from the apply command surfaces as
        :class:`UefiCapsuleError`.
    """
    def responder(command: str, aspects: Optional[UefiCmdAspects]) -> Tuple[int, str, str]:
        if command.startswith("test -c"):
            rtnval = (0, "", "")
            return rtnval
        if command.startswith("dd "):
            rtnval = (22, "", "EINVAL")
            return rtnval
        rtnval = (0, "", "")
        return rtnval

    agent = _FakeUefiAgent(transport_kind="ssh-host", responder=responder)
    variables = _FakeVariableStore(variables=[])

    delivery = CapsuleDelivery(agent=agent, variable_store=variables)
    capsule = _build_capsule()

    with testbase.raises(UefiCapsuleError):
        delivery.deliver(capsule=capsule)
    return

def test_ssh_host_delivery_stages_base64_payload_that_decodes_to_capsule_bytes() -> None:
    """
        The base64 blob embedded in the stage command must decode
        back to the capsule's on-wire bytes.
    """
    recorded_commands: List[str] = []

    def responder(command: str, aspects: Optional[UefiCmdAspects]) -> Tuple[int, str, str]:
        recorded_commands.append(command)
        rtnval = (0, "", "")
        return rtnval

    agent = _FakeUefiAgent(transport_kind="ssh-host", responder=responder)
    variables = _FakeVariableStore(variables=[])

    delivery = CapsuleDelivery(agent=agent, variable_store=variables)
    capsule = _build_capsule()

    delivery.deliver(capsule=capsule)

    stage_command = recorded_commands[1]
    # The stage command has the shape:
    #   printf '%s' '<base64>' | base64 -d > /tmp/...
    # The base64 payload is between the SECOND pair of single quotes.
    # Walk the command and find quote indices.
    quote_positions: List[int] = []
    for idx, ch in enumerate(stage_command):
        if ch == "'":
            quote_positions.append(idx)

    encoded_text = stage_command[quote_positions[2] + 1:quote_positions[3]]

    decoded_capsule = base64.b64decode(encoded_text)
    testbase.assert_equal(decoded_capsule, capsule.to_bytes())
    return


# ---------------------------------------------------------------------------
# CapsuleDelivery: redfish backend
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# CapsuleDeliveryRedfishTestCase
# ----------------------------------------------------------------------

"""
    Exercise the Redfish backend which POSTs a multipart-shaped
    verb-line command to the agent.
"""

def test_redfish_delivery_posts_to_multipart_endpoint() -> None:
    """
        The Redfish backend issues a single ``POST /redfish/v1/..``
        command with a ``sha256=`` and ``capsule_b64=`` key-value
        body suffix.
    """
    def responder(command: str, aspects: Optional[UefiCmdAspects]) -> Tuple[int, str, str]:
        rtnval = (0, "", "")
        return rtnval

    agent = _FakeUefiAgent(transport_kind="redfish", responder=responder)
    variables = _FakeVariableStore(variables=[])

    delivery = CapsuleDelivery(agent=agent, variable_store=variables)

    builder = CapsuleBuilder(capsule_guid=SAMPLE_CAPSULE_GUID, flags=0)
    builder = builder.with_raw_body(body=b"RFSH")
    capsule = builder.build()

    delivery.deliver(capsule=capsule)

    testbase.assert_equal(len(agent.calls), 1)
    command, _ = agent.calls[0]

    testbase.assert_true(command.startswith("POST "))
    testbase.assert_true("/redfish/v1/UpdateService/update-multipart" in command)
    testbase.assert_true("sha256={}".format(capsule.sha256) in command)
    testbase.assert_true("capsule_b64=" in command)
    return

def test_redfish_delivery_raises_on_non_zero_status() -> None:
    """
        A non-zero status from the POST surfaces as
        :class:`UefiCapsuleError`.
    """
    def responder(command: str, aspects: Optional[UefiCmdAspects]) -> Tuple[int, str, str]:
        rtnval = (500, "", "")
        return rtnval

    agent = _FakeUefiAgent(transport_kind="redfish", responder=responder)
    variables = _FakeVariableStore(variables=[])

    delivery = CapsuleDelivery(agent=agent, variable_store=variables)

    builder = CapsuleBuilder(capsule_guid=SAMPLE_CAPSULE_GUID, flags=0)
    capsule = builder.build()

    with testbase.raises(UefiCapsuleError):
        delivery.deliver(capsule=capsule)
    return


# ---------------------------------------------------------------------------
# CapsuleDelivery: serial backend (refused)
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# CapsuleDeliverySerialRefusalTestCase
# ----------------------------------------------------------------------

"""
    The serial / ipmi-sol transports refuse capsule delivery with a
    :class:`UefiCapsuleError`.
"""

def test_serial_transport_raises() -> None:
    """
        ``transport_kind='serial'`` raises :class:`UefiCapsuleError`.
    """
    agent = _FakeUefiAgent(transport_kind="serial")
    variables = _FakeVariableStore(variables=[])

    delivery = CapsuleDelivery(agent=agent, variable_store=variables)

    builder = CapsuleBuilder(capsule_guid=SAMPLE_CAPSULE_GUID, flags=0)
    capsule = builder.build()

    with testbase.raises(UefiCapsuleError):
        delivery.deliver(capsule=capsule)

    testbase.assert_equal(len(agent.calls), 0)
    return

def test_ipmi_sol_transport_raises() -> None:
    """
        ``transport_kind='ipmi-sol'`` also refuses delivery.
    """
    agent = _FakeUefiAgent(transport_kind="ipmi-sol")
    variables = _FakeVariableStore(variables=[])

    delivery = CapsuleDelivery(agent=agent, variable_store=variables)

    builder = CapsuleBuilder(capsule_guid=SAMPLE_CAPSULE_GUID, flags=0)
    capsule = builder.build()

    with testbase.raises(UefiCapsuleError):
        delivery.deliver(capsule=capsule)
    return


# ---------------------------------------------------------------------------
# CapsuleReport
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# CapsuleReportTestCase
# ----------------------------------------------------------------------

"""
    Exercise :class:`CapsuleReport.list_reports` enumeration and
    the :class:`CapsuleDelivery.wait_for_report` filter path.
"""

def test_list_reports_parses_capsule_variables() -> None:
    """
        Variables under :data:`EFI_CAPSULE_REPORT_GUID` whose name
        begins with ``Capsule`` are decoded into
        :class:`CapsuleReportEntry` records.
    """
    payload_zero = _build_report_payload(
        capsule_guid=SAMPLE_CAPSULE_GUID,
        processed=True,
        last_attempt_version=7,
        last_attempt_status=0,
    )
    payload_one = _build_report_payload(
        capsule_guid=OTHER_CAPSULE_GUID,
        processed=False,
        last_attempt_version=2,
        last_attempt_status=0x20,
    )

    variables = [
        UefiVariable(
            name="Capsule0001",
            vendor_guid=EFI_CAPSULE_REPORT_GUID,
            attributes=UefiVariableAttributes.NON_VOLATILE,
            data=payload_one,
        ),
        UefiVariable(
            name="Capsule0000",
            vendor_guid=EFI_CAPSULE_REPORT_GUID,
            attributes=UefiVariableAttributes.NON_VOLATILE,
            data=payload_zero,
        ),
        # Unrelated variable — must be ignored.
        UefiVariable(
            name="OsIndicationsSupported",
            vendor_guid=uuid.UUID("8be4df61-93ca-11d2-aa0d-00e098032b8c"),
            attributes=UefiVariableAttributes.NON_VOLATILE,
            data=b"\x00\x00\x00\x00",
        ),
    ]

    store = _FakeVariableStore(variables=variables)
    report = CapsuleReport(variable_store=store)

    records = report.list_reports()

    testbase.assert_equal(len(records), 2)
    # Sorted by name: Capsule0000, Capsule0001.
    testbase.assert_equal(records[0].capsule_guid, SAMPLE_CAPSULE_GUID)
    testbase.assert_true(records[0].capsule_processed)
    testbase.assert_equal(records[0].last_attempt_version, 7)
    testbase.assert_equal(records[0].capsule_status, 0)
    testbase.assert_true(records[0].is_success)

    testbase.assert_equal(records[1].capsule_guid, OTHER_CAPSULE_GUID)
    testbase.assert_false(records[1].capsule_processed)
    testbase.assert_equal(records[1].last_attempt_status, 0x20)
    testbase.assert_false(records[1].is_success)
    return

def test_list_reports_rejects_short_payload() -> None:
    """
        A payload that is shorter than the documented header layout
        raises :class:`UefiCapsuleError`.
    """
    short_payload = b"\x00" * 16  # Well below 0x30 bytes.

    variables = [
        UefiVariable(
            name="Capsule0000",
            vendor_guid=EFI_CAPSULE_REPORT_GUID,
            attributes=UefiVariableAttributes.NON_VOLATILE,
            data=short_payload,
        ),
    ]

    store = _FakeVariableStore(variables=variables)
    report = CapsuleReport(variable_store=store)

    with testbase.raises(UefiCapsuleError):
        report.list_reports()
    return

def test_wait_for_report_returns_latest_matching_entry() -> None:
    """
        :meth:`CapsuleDelivery.wait_for_report` returns the entry
        with the numerically largest variable name that matches the
        capsule's GUID.
    """
    payload_a = _build_report_payload(
        capsule_guid=SAMPLE_CAPSULE_GUID,
        processed=True,
        last_attempt_version=1,
        last_attempt_status=0,
    )
    payload_b = _build_report_payload(
        capsule_guid=SAMPLE_CAPSULE_GUID,
        processed=True,
        last_attempt_version=2,
        last_attempt_status=0,
    )

    variables = [
        UefiVariable(
            name="Capsule0000",
            vendor_guid=EFI_CAPSULE_REPORT_GUID,
            attributes=UefiVariableAttributes.NON_VOLATILE,
            data=payload_a,
        ),
        UefiVariable(
            name="Capsule0005",
            vendor_guid=EFI_CAPSULE_REPORT_GUID,
            attributes=UefiVariableAttributes.NON_VOLATILE,
            data=payload_b,
        ),
    ]

    agent = _FakeUefiAgent(transport_kind="ssh-host")
    store = _FakeVariableStore(variables=variables)

    delivery = CapsuleDelivery(agent=agent, variable_store=store)

    builder = CapsuleBuilder(capsule_guid=SAMPLE_CAPSULE_GUID, flags=0)
    capsule = builder.build()

    entry = delivery.wait_for_report(capsule=capsule)

    testbase.assert_equal(entry.capsule_guid, SAMPLE_CAPSULE_GUID)
    testbase.assert_equal(entry.last_attempt_version, 2)
    return

def test_wait_for_report_raises_when_no_match() -> None:
    """
        No matching variable results in :class:`UefiCapsuleError`.
    """
    payload = _build_report_payload(
        capsule_guid=OTHER_CAPSULE_GUID,
        processed=True,
        last_attempt_version=1,
        last_attempt_status=0,
    )

    variables = [
        UefiVariable(
            name="Capsule0000",
            vendor_guid=EFI_CAPSULE_REPORT_GUID,
            attributes=UefiVariableAttributes.NON_VOLATILE,
            data=payload,
        ),
    ]

    agent = _FakeUefiAgent(transport_kind="ssh-host")
    store = _FakeVariableStore(variables=variables)

    delivery = CapsuleDelivery(agent=agent, variable_store=store)

    builder = CapsuleBuilder(capsule_guid=SAMPLE_CAPSULE_GUID, flags=0)
    capsule = builder.build()

    with testbase.raises(UefiCapsuleError):
        delivery.wait_for_report(capsule=capsule)
    return


# ---------------------------------------------------------------------------
# FmpCapsuleHeader / FmpPayloadHeader round trip
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# FmpStructRoundTripTestCase
# ----------------------------------------------------------------------

"""
    Exercise the FMP header round-trip and consistency checks.
"""

def test_fmp_capsule_header_round_trip() -> None:
    """
        ``to_bytes -> from_bytes`` preserves every field.
    """
    header = FmpCapsuleHeader(
        version=1,
        embedded_driver_count=1,
        payload_item_count=2,
        item_offsets=(0x10, 0x20, 0x40),
    )

    serialized = header.to_bytes()
    decoded = FmpCapsuleHeader.from_bytes(data=serialized)

    testbase.assert_equal(decoded.version, 1)
    testbase.assert_equal(decoded.embedded_driver_count, 1)
    testbase.assert_equal(decoded.payload_item_count, 2)
    testbase.assert_equal(decoded.item_offsets, (0x10, 0x20, 0x40))
    return

def test_fmp_capsule_header_rejects_count_mismatch() -> None:
    """
        A mismatch between declared counts and offset array length
        raises :class:`UefiCapsuleError`.
    """
    header = FmpCapsuleHeader(
        version=1,
        embedded_driver_count=0,
        payload_item_count=2,
        item_offsets=(0x10,),  # only one offset for a declared two
    )

    with testbase.raises(UefiCapsuleError):
        header.to_bytes()
    return

def test_fmp_payload_header_round_trip() -> None:
    """
        Payload header serializes and parses back to the same
        dataclass.
    """
    header = FmpPayloadHeader(
        signature=FMP_PAYLOAD_SIGNATURE,
        header_size=FMP_PAYLOAD_HEADER_SIZE,
        fw_version=0xCAFEBABE,
        lowest_supported_version=0x0000000A,
    )

    serialized = header.to_bytes()
    decoded = FmpPayloadHeader.from_bytes(data=serialized)

    testbase.assert_equal(decoded.signature, FMP_PAYLOAD_SIGNATURE)
    testbase.assert_equal(decoded.header_size, FMP_PAYLOAD_HEADER_SIZE)
    testbase.assert_equal(decoded.fw_version, 0xCAFEBABE)
    testbase.assert_equal(decoded.lowest_supported_version, 0x0000000A)
    return
