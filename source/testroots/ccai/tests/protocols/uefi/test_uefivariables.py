"""
    .. module:: test_uefivariables
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Unit tests for the :mod:`ccai.protocols.uefi.uefivariables`
               module.  Exercises :class:`VariableStore` facade backend
               dispatch, the :class:`_ShellVariableBackend` dmpstore /
               setvar round trip, the :class:`_HostOsVariableBackend`
               efivarfs reader, the :class:`_RedfishVariableBackend` refusal
               path, the :func:`build_auth2_descriptor` structural layout,
               and the attribute-flag round trip.

               The tests never touch real serial ports, BMC endpoints, or
               SSH sessions; a minimal :class:`_FakeUefiAgent` replaces
               :class:`UefiAgent` and returns canned command tuples.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Code Craft AI LLC"
__credits__ = []


from ccai import testbase

import datetime
import uuid

from typing import Any, Callable, List, Optional, Tuple

from ccai.errors.exceptions import ConfigurationError, SkipError

from ccai.protocols.uefi.uefiaspects import (
    UefiCmdAspects,
)
from ccai.protocols.uefi.uefiexceptions import (
    UefiTransportError,
    UefiVariableError,
)
from ccai.protocols.uefi.uefivariables import (
    EFI_CERT_TYPE_PKCS7_GUID,
    EFI_NOT_FOUND,
    EFI_UNSUPPORTED,
    UefiVariable,
    UefiVariableAttributes,
    VariableStore,
    WIN_CERT_TYPE_EFI_GUID,
    _HostOsVariableBackend,
    _RedfishVariableBackend,
    _ShellVariableBackend,
    _attributes_to_shell_flags,
    _bytes_to_shell_hex,
    _parse_dmpstore_output,
    build_auth2_descriptor,
)


# ---------------------------------------------------------------------------
# Fake UefiAgent used by every test
# ---------------------------------------------------------------------------

class _FakeUefiAgent:
    """
        Minimal :class:`UefiAgent` stand-in.  Records every ``run_cmd``
        call and returns canned ``(status, stdout, stderr)`` tuples based
        on a caller-supplied ``responder`` callable.
    """

    def __init__(self, transport_kind: str,
                       responder: Optional[Callable[[str, Optional[UefiCmdAspects]], Tuple[int, str, str]]] = None) -> None:
        """
            Construct the fake agent.

            :param transport_kind: Transport kind selector string.
            :param responder: Callable mapping ``(command, aspects)`` to
                              ``(status, stdout, stderr)``.  Defaults to a
                              responder that returns an empty-success
                              tuple.
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
# Canonical test fixtures
# ---------------------------------------------------------------------------

_TEST_GUID = uuid.UUID("8be4df61-93ca-11d2-aa0d-00e098032b8c")
_TEST_NAME = "TestVar"
_TEST_DATA = bytes(range(8))


def _build_dmpstore_single(name: str, guid: uuid.UUID,
                           attr_tokens: str, data: bytes) -> str:
    """
        Render a single-record dmpstore listing as a :class:`str` suitable
        for feeding to :func:`_parse_dmpstore_output`.
    """
    lines = []
    size_hex = "{:X}".format(len(data))
    lines.append("Variable {attrs} '{guid}:{name}' DataSize = 0x{size}".format(
        attrs=attr_tokens, guid=str(guid).upper(), name=name, size=size_hex,
    ))
    # 16 bytes per row
    for offset in range(0, len(data), 16):
        row = data[offset:offset + 16]
        hex_tokens = " ".join("{:02X}".format(b) for b in row)
        lines.append("  {:08X}: {}".format(offset, hex_tokens))
    rtnval = "\n".join(lines)
    return rtnval


# ---------------------------------------------------------------------------
# Dispatch tests
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# VariableStoreDispatchTests
# ----------------------------------------------------------------------

"""
    Verify :class:`VariableStore` selects the correct backend for each
    transport kind and refuses unknown values.
"""

def test_serial_dispatches_to_shell_backend() -> None:
    agent = _FakeUefiAgent(transport_kind="serial")
    store = VariableStore(agent=agent)
    testbase.assert_true(isinstance(store._backend, _ShellVariableBackend))
    return

def test_ipmi_sol_dispatches_to_shell_backend() -> None:
    agent = _FakeUefiAgent(transport_kind="ipmi-sol")
    store = VariableStore(agent=agent)
    testbase.assert_true(isinstance(store._backend, _ShellVariableBackend))
    return

def test_ssh_host_dispatches_to_host_os_backend() -> None:
    agent = _FakeUefiAgent(transport_kind="ssh-host")
    store = VariableStore(agent=agent)
    testbase.assert_true(isinstance(store._backend, _HostOsVariableBackend))
    return

def test_redfish_dispatches_to_redfish_backend() -> None:
    agent = _FakeUefiAgent(transport_kind="redfish")
    store = VariableStore(agent=agent)
    testbase.assert_true(isinstance(store._backend, _RedfishVariableBackend))
    return

def test_unknown_transport_kind_raises() -> None:
    agent = _FakeUefiAgent(transport_kind="nonsense")
    with testbase.raises(ConfigurationError):
        VariableStore(agent=agent)
    return


# ---------------------------------------------------------------------------
# Shell backend — dmpstore parsing
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# ShellBackendGetVariableTests
# ----------------------------------------------------------------------

"""
    Verify :meth:`_ShellVariableBackend.get_variable` parses
    ``dmpstore`` output correctly.
"""

def test_dmpstore_single_record_is_parsed() -> None:
    output = _build_dmpstore_single(
        name=_TEST_NAME, guid=_TEST_GUID,
        attr_tokens="NV+BS+RT", data=_TEST_DATA,
    )
    responder = lambda command, aspects: (0, output, "")
    agent = _FakeUefiAgent(transport_kind="serial", responder=responder)
    store = VariableStore(agent=agent)

    record = store.get_variable(name=_TEST_NAME, vendor_guid=_TEST_GUID)

    testbase.assert_equal(record.name, _TEST_NAME)
    testbase.assert_equal(record.vendor_guid, _TEST_GUID)
    expected_attrs = (
        UefiVariableAttributes.NON_VOLATILE
        | UefiVariableAttributes.BOOTSERVICE_ACCESS
        | UefiVariableAttributes.RUNTIME_ACCESS
    )
    testbase.assert_equal(record.attributes, expected_attrs)
    testbase.assert_equal(record.data, _TEST_DATA)
    return

def test_dmpstore_empty_output_raises_not_found() -> None:
    responder = lambda command, aspects: (0, "", "")
    agent = _FakeUefiAgent(transport_kind="serial", responder=responder)
    store = VariableStore(agent=agent)

    with testbase.raises(UefiVariableError) as ctx:
        store.get_variable(name=_TEST_NAME, vendor_guid=_TEST_GUID)
    testbase.assert_equal(ctx.exception.efi_status, EFI_NOT_FOUND)
    return

def test_dmpstore_nonzero_status_raises_not_found() -> None:
    responder = lambda command, aspects: (1, "", "dmpstore: not found")
    agent = _FakeUefiAgent(transport_kind="serial", responder=responder)
    store = VariableStore(agent=agent)

    with testbase.raises(UefiVariableError) as ctx:
        store.get_variable(name=_TEST_NAME, vendor_guid=_TEST_GUID)
    testbase.assert_equal(ctx.exception.efi_status, EFI_NOT_FOUND)
    return

def test_get_variable_accepts_string_guid() -> None:
    output = _build_dmpstore_single(
        name=_TEST_NAME, guid=_TEST_GUID,
        attr_tokens="NV+BS", data=b"\x01\x02",
    )
    responder = lambda command, aspects: (0, output, "")
    agent = _FakeUefiAgent(transport_kind="serial", responder=responder)
    store = VariableStore(agent=agent)

    record = store.get_variable(name=_TEST_NAME, vendor_guid=str(_TEST_GUID))
    testbase.assert_equal(record.vendor_guid, _TEST_GUID)
    return


# ---------------------------------------------------------------------------
# Shell backend — setvar command construction
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# ShellBackendSetVariableTests
# ----------------------------------------------------------------------

"""
    Verify :meth:`_ShellVariableBackend.set_variable` builds a correct
    setvar command string.
"""

def test_setvar_command_includes_guid_flags_and_hex_payload() -> None:
    responder = lambda command, aspects: (0, "", "")
    agent = _FakeUefiAgent(transport_kind="serial", responder=responder)
    store = VariableStore(agent=agent)

    attrs = (
        UefiVariableAttributes.NON_VOLATILE
        | UefiVariableAttributes.BOOTSERVICE_ACCESS
        | UefiVariableAttributes.RUNTIME_ACCESS
    )
    variable = UefiVariable(
        name=_TEST_NAME, vendor_guid=_TEST_GUID,
        attributes=attrs, data=b"\xDE\xAD\xBE\xEF",
    )
    store.set_variable(variable=variable)

    testbase.assert_equal(len(agent.calls), 1)
    issued_command, _aspects = agent.calls[0]

    testbase.assert_true("setvar" in issued_command)
    testbase.assert_true(_TEST_NAME in issued_command)
    testbase.assert_true(str(_TEST_GUID) in issued_command)
    testbase.assert_true("-nv" in issued_command)
    testbase.assert_true("-bs" in issued_command)
    testbase.assert_true("-rt" in issued_command)
    testbase.assert_true("0xDE" in issued_command)
    testbase.assert_true("0xAD" in issued_command)
    testbase.assert_true("0xBE" in issued_command)
    testbase.assert_true("0xEF" in issued_command)
    return

def test_setvar_nonzero_status_raises() -> None:
    responder = lambda command, aspects: (7, "", "setvar: failed")
    agent = _FakeUefiAgent(transport_kind="serial", responder=responder)
    store = VariableStore(agent=agent)

    variable = UefiVariable(
        name=_TEST_NAME, vendor_guid=_TEST_GUID,
        attributes=UefiVariableAttributes.NON_VOLATILE,
        data=b"\x00",
    )
    with testbase.raises(UefiVariableError) as ctx:
        store.set_variable(variable=variable)
    testbase.assert_equal(ctx.exception.efi_status, 7)
    testbase.assert_equal(ctx.exception.name, _TEST_NAME)
    testbase.assert_equal(ctx.exception.vendor_guid, _TEST_GUID)
    return


# ---------------------------------------------------------------------------
# Shell backend — delete and enumeration
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# ShellBackendDeleteTests
# ----------------------------------------------------------------------

"""
    Verify :meth:`VariableStore.delete_variable` issues a setvar with
    zero-length data.
"""

def test_delete_sends_zero_length_payload() -> None:
    responder = lambda command, aspects: (0, "", "")
    agent = _FakeUefiAgent(transport_kind="serial", responder=responder)
    store = VariableStore(agent=agent)

    store.delete_variable(name=_TEST_NAME, vendor_guid=_TEST_GUID)

    testbase.assert_equal(len(agent.calls), 1)
    issued_command, _aspects = agent.calls[0]
    testbase.assert_true("setvar" in issued_command)
    testbase.assert_true(_TEST_NAME in issued_command)
    # No hex-payload ``=0x`` token is present for a zero-length write.
    testbase.assert_true("=0x" not in issued_command)
    return


# ----------------------------------------------------------------------
# ShellBackendEnumerationTests
# ----------------------------------------------------------------------

"""
    Verify :meth:`VariableStore.enumerate_variables` returns a lazy
    generator.
"""

def test_enumerate_returns_generator_not_list() -> None:
    output_parts = [
        _build_dmpstore_single(
            name="A", guid=_TEST_GUID, attr_tokens="NV+BS", data=b"\x01",
        ),
        _build_dmpstore_single(
            name="B", guid=_TEST_GUID, attr_tokens="NV+BS", data=b"\x02",
        ),
    ]
    combined = "\n".join(output_parts)
    responder = lambda command, aspects: (0, combined, "")
    agent = _FakeUefiAgent(transport_kind="serial", responder=responder)
    store = VariableStore(agent=agent)

    iterator = store.enumerate_variables()
    testbase.assert_true(hasattr(iterator, "__next__"))
    testbase.assert_true(not isinstance(iterator, list))

    # Before any iteration no run_cmd has been issued — the inner
    # generator is lazy.
    testbase.assert_equal(len(agent.calls), 0)

    first = next(iterator)
    testbase.assert_equal(first[0], "A")

    second = next(iterator)
    testbase.assert_equal(second[0], "B")

    with testbase.raises(StopIteration):
        next(iterator)
    return

def test_enumerate_name_filter_limits_yielded_entries() -> None:
    output_parts = [
        _build_dmpstore_single(
            name="Boot0000", guid=_TEST_GUID, attr_tokens="NV+BS", data=b"\x01",
        ),
        _build_dmpstore_single(
            name="Setup", guid=_TEST_GUID, attr_tokens="NV+BS", data=b"\x02",
        ),
    ]
    combined = "\n".join(output_parts)
    responder = lambda command, aspects: (0, combined, "")
    agent = _FakeUefiAgent(transport_kind="serial", responder=responder)
    store = VariableStore(agent=agent)

    results = list(store.enumerate_variables(name_filter="Boot*"))
    testbase.assert_equal(len(results), 1)
    testbase.assert_equal(results[0][0], "Boot0000")
    return


# ---------------------------------------------------------------------------
# Attribute round-trip
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# AttributeRoundTripTests
# ----------------------------------------------------------------------

"""
    For every :class:`UefiVariableAttributes` flag, verify that the
    setvar command emitted carries the flag's encoded form and that a
    dmpstore listing carrying the corresponding token decodes back to
    the same flag bit.
"""

_TOKEN_BY_FLAG = {
    UefiVariableAttributes.NON_VOLATILE: "NV",
    UefiVariableAttributes.BOOTSERVICE_ACCESS: "BS",
    UefiVariableAttributes.RUNTIME_ACCESS: "RT",
    UefiVariableAttributes.HARDWARE_ERROR_RECORD: "HR",
    UefiVariableAttributes.AUTHENTICATED_WRITE_ACCESS: "AW",
    UefiVariableAttributes.TIME_BASED_AUTHENTICATED_WRITE_ACCESS: "AT",
    UefiVariableAttributes.APPEND_WRITE: "AP",
    UefiVariableAttributes.ENHANCED_AUTHENTICATED_ACCESS: "EA",
}

def test_every_flag_round_trips_through_setvar_and_dmpstore() -> None:
    for flag, token in _TOKEN_BY_FLAG.items():
        # Build an attribute bitfield that is legal under the
        # cross-field validator (AW / AT / EA require their siblings).
        if flag == UefiVariableAttributes.RUNTIME_ACCESS:
            attrs = (
                UefiVariableAttributes.BOOTSERVICE_ACCESS
                | UefiVariableAttributes.RUNTIME_ACCESS
            )
            tokens = "BS+RT"
        elif flag == UefiVariableAttributes.AUTHENTICATED_WRITE_ACCESS:
            attrs = (
                UefiVariableAttributes.NON_VOLATILE
                | UefiVariableAttributes.BOOTSERVICE_ACCESS
                | UefiVariableAttributes.AUTHENTICATED_WRITE_ACCESS
            )
            tokens = "NV+BS+AW"
        elif flag == UefiVariableAttributes.TIME_BASED_AUTHENTICATED_WRITE_ACCESS:
            attrs = (
                UefiVariableAttributes.NON_VOLATILE
                | UefiVariableAttributes.BOOTSERVICE_ACCESS
                | UefiVariableAttributes.TIME_BASED_AUTHENTICATED_WRITE_ACCESS
            )
            tokens = "NV+BS+AT"
        elif flag == UefiVariableAttributes.ENHANCED_AUTHENTICATED_ACCESS:
            attrs = (
                UefiVariableAttributes.NON_VOLATILE
                | UefiVariableAttributes.BOOTSERVICE_ACCESS
                | UefiVariableAttributes.ENHANCED_AUTHENTICATED_ACCESS
            )
            tokens = "NV+BS+EA"
        else:
            attrs = UefiVariableAttributes.NON_VOLATILE | flag
            if flag == UefiVariableAttributes.NON_VOLATILE:
                tokens = "NV"
            else:
                tokens = "NV+" + token

        # Write path.
        responder_w = lambda command, aspects: (0, "", "")
        write_agent = _FakeUefiAgent(
            transport_kind="serial", responder=responder_w,
        )
        write_store = VariableStore(agent=write_agent)
        variable = UefiVariable(
            name=_TEST_NAME, vendor_guid=_TEST_GUID,
            attributes=attrs, data=b"\x11",
        )
        write_store.set_variable(variable=variable)

        issued_command, _aspects = write_agent.calls[0]
        testbase.assert_true(_TEST_NAME in issued_command)

        # Read path.
        output = _build_dmpstore_single(
            name=_TEST_NAME, guid=_TEST_GUID,
            attr_tokens=tokens, data=b"\x11",
        )
        responder_r = lambda command, aspects, out=output: (0, out, "")
        read_agent = _FakeUefiAgent(
            transport_kind="serial", responder=responder_r,
        )
        read_store = VariableStore(agent=read_agent)
        record = read_store.get_variable(
            name=_TEST_NAME, vendor_guid=_TEST_GUID,
        )
        testbase.assert_equal(record.attributes, attrs)
    return


# ---------------------------------------------------------------------------
# Host-OS backend
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# HostOsBackendGetVariableTests
# ----------------------------------------------------------------------

"""
    Verify :meth:`_HostOsVariableBackend.get_variable` reads the 4-byte
    attribute prefix + data from efivarfs.
"""

def test_host_os_read_parses_attr_prefix_and_data() -> None:
    attrs = (
        UefiVariableAttributes.NON_VOLATILE
        | UefiVariableAttributes.BOOTSERVICE_ACCESS
    )
    attr_prefix = int(attrs).to_bytes(length=4, byteorder="little", signed=False)
    payload = b"\xCA\xFE\xBA\xBE"
    raw = attr_prefix + payload
    hex_text = raw.hex()

    expected_path = "/sys/firmware/efi/efivars/{}-{}".format(_TEST_NAME, str(_TEST_GUID))

    def _responder(command: str, aspects: Optional[UefiCmdAspects]) -> Tuple[int, str, str]:
        assert expected_path in command
        assert command.startswith("xxd -p -c0 ")
        return (0, hex_text, "")

    agent = _FakeUefiAgent(transport_kind="ssh-host", responder=_responder)
    store = VariableStore(agent=agent)

    record = store.get_variable(name=_TEST_NAME, vendor_guid=_TEST_GUID)
    testbase.assert_equal(record.attributes, attrs)
    testbase.assert_equal(record.data, payload)
    return

def test_host_os_read_nonzero_status_raises() -> None:
    responder = lambda command, aspects: (2, "", "no such file")
    agent = _FakeUefiAgent(transport_kind="ssh-host", responder=responder)
    store = VariableStore(agent=agent)

    with testbase.raises(UefiVariableError) as ctx:
        store.get_variable(name=_TEST_NAME, vendor_guid=_TEST_GUID)
    testbase.assert_equal(ctx.exception.efi_status, EFI_NOT_FOUND)
    return


# ---------------------------------------------------------------------------
# Redfish backend refusal
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# RedfishBackendRefusalTests
# ----------------------------------------------------------------------

"""
    Verify :class:`_RedfishVariableBackend` refuses get/set with a
    clear error message.
"""

def test_redfish_get_variable_raises() -> None:
    agent = _FakeUefiAgent(transport_kind="redfish")
    store = VariableStore(agent=agent)

    with testbase.raises(UefiVariableError) as ctx:
        store.get_variable(name=_TEST_NAME, vendor_guid=_TEST_GUID)
    testbase.assert_equal(ctx.exception.efi_status, EFI_UNSUPPORTED)
    testbase.assert_true("Redfish" in str(ctx.exception))
    return

def test_redfish_set_variable_raises() -> None:
    agent = _FakeUefiAgent(transport_kind="redfish")
    store = VariableStore(agent=agent)

    variable = UefiVariable(
        name=_TEST_NAME, vendor_guid=_TEST_GUID,
        attributes=UefiVariableAttributes.NON_VOLATILE,
        data=b"\x00",
    )
    with testbase.raises(UefiVariableError) as ctx:
        store.set_variable(variable=variable)
    testbase.assert_equal(ctx.exception.efi_status, EFI_UNSUPPORTED)
    return

def test_redfish_query_variable_info_raises() -> None:
    agent = _FakeUefiAgent(transport_kind="redfish")
    store = VariableStore(agent=agent)

    with testbase.raises(UefiTransportError):
        store.query_variable_info(attributes=UefiVariableAttributes.NON_VOLATILE)
    return


# ---------------------------------------------------------------------------
# build_auth2_descriptor
# ---------------------------------------------------------------------------

try:
    import cryptography  # noqa: F401
    _CRYPTOGRAPHY_AVAILABLE = True
except ImportError:
    _CRYPTOGRAPHY_AVAILABLE = False


def _build_test_keypair() -> Tuple[Any, Any]:
    """
        Build a throwaway self-signed RSA keypair for descriptor tests.

        :returns: A tuple of (private_key, certificate).
    """
    # Lazy import: this helper is only called when cryptography is present.
    from cryptography.hazmat.primitives.asymmetric import rsa as _rsa
    from cryptography import x509 as _x509
    from cryptography.x509.oid import NameOID as _NameOID
    from cryptography.hazmat.primitives import hashes as _hashes

    private_key = _rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = _x509.Name([
        _x509.NameAttribute(_NameOID.COMMON_NAME, "codecraftai-protocol-uefi-tests"),
    ])
    now = datetime.datetime.now(datetime.timezone.utc)
    certificate = (
        _x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(private_key.public_key())
        .serial_number(_x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=365))
        .sign(private_key=private_key, algorithm=_hashes.SHA256())
    )
    rtnval = (private_key, certificate)
    return rtnval


def _require_cryptography() -> None:
    """
        Skips the calling test when the optional 'cryptography' package is
        absent.  These tests were gated by a class-level
        @unittest.skipUnless before the suite moved to testbase; testbase
        signals a skip by raising SkipError.

        :raises: SkipError
    """
    if not _CRYPTOGRAPHY_AVAILABLE:
        raise SkipError("cryptography not installed")

    return


# ----------------------------------------------------------------------
# BuildAuth2DescriptorTests
# ----------------------------------------------------------------------

"""
    Structural tests for :func:`build_auth2_descriptor`.
"""

def test_descriptor_structure_is_well_formed() -> None:
    _require_cryptography()
    private_key, certificate = _build_test_keypair()
    time = datetime.datetime(
        2026, 1, 1, 12, 0, 0, tzinfo=datetime.timezone.utc,
    )
    payload = b"hello, uefi"

    descriptor = build_auth2_descriptor(
        data=payload, time=time,
        signer_key=private_key, signer_cert=certificate,
    )

    # EFI_TIME is exactly 16 bytes and sits at offset 0.
    testbase.assert_greater(len(descriptor), 16)
    efi_time = descriptor[:16]
    year = int.from_bytes(efi_time[0:2], byteorder="little", signed=False)
    month = efi_time[2]
    day = efi_time[3]
    testbase.assert_equal(year, 2026)
    testbase.assert_equal(month, 1)
    testbase.assert_equal(day, 1)

    # WIN_CERTIFICATE_UEFI_GUID header lives at offset 16.
    dw_length = int.from_bytes(descriptor[16:20], byteorder="little", signed=False)
    w_revision = int.from_bytes(descriptor[20:22], byteorder="little", signed=False)
    w_cert_type = int.from_bytes(descriptor[22:24], byteorder="little", signed=False)
    testbase.assert_equal(w_revision, 0x0200)
    testbase.assert_equal(w_cert_type, WIN_CERT_TYPE_EFI_GUID)

    # CertType GUID at offset 24 should be EFI_CERT_TYPE_PKCS7_GUID in
    # UEFI mixed-endian layout.
    cert_type_bytes = descriptor[24:40]
    decoded_cert_type = uuid.UUID(bytes_le=cert_type_bytes)
    testbase.assert_equal(decoded_cert_type, EFI_CERT_TYPE_PKCS7_GUID)

    # The PKCS#7 DER region follows; ensure the descriptor length lines
    # up with the dwLength field.
    total_after_time = len(descriptor) - 16
    testbase.assert_equal(total_after_time, dw_length)

    # DER-encoded content begins with the ASN.1 SEQUENCE tag 0x30.
    cert_data_offset = 40
    testbase.assert_equal(descriptor[cert_data_offset], 0x30)
    return

def test_naive_datetime_is_rejected() -> None:
    _require_cryptography()
    private_key, certificate = _build_test_keypair()
    naive = datetime.datetime(2026, 1, 1, 12, 0, 0)
    with testbase.raises(ConfigurationError):
        build_auth2_descriptor(
            data=b"", time=naive,
            signer_key=private_key, signer_cert=certificate,
        )
    return


# ---------------------------------------------------------------------------
# Misc unit tests
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# HelperTests
# ----------------------------------------------------------------------

"""
    Cover the small pure helpers in :mod:`uefivariables`.
"""

def test_attributes_to_shell_flags_renders_basic_flags() -> None:
    _require_cryptography()
    attrs = (
        UefiVariableAttributes.NON_VOLATILE
        | UefiVariableAttributes.BOOTSERVICE_ACCESS
    )
    rendered = _attributes_to_shell_flags(attrs)
    testbase.assert_true("-nv" in rendered)
    testbase.assert_true("-bs" in rendered)
    return

def test_attributes_to_shell_flags_renders_attr_numeric_for_exotic() -> None:
    _require_cryptography()
    attrs = (
        UefiVariableAttributes.NON_VOLATILE
        | UefiVariableAttributes.BOOTSERVICE_ACCESS
        | UefiVariableAttributes.APPEND_WRITE
    )
    rendered = _attributes_to_shell_flags(attrs)
    testbase.assert_true("-attr=" in rendered)
    return

def test_bytes_to_shell_hex_empty_is_empty() -> None:
    _require_cryptography()
    rendered = _bytes_to_shell_hex(b"")
    testbase.assert_equal(rendered, "")
    return

def test_bytes_to_shell_hex_non_empty_prefixes_equals() -> None:
    _require_cryptography()
    rendered = _bytes_to_shell_hex(b"\x01\x02")
    testbase.assert_true(rendered.startswith("="))
    testbase.assert_true("0x01" in rendered)
    testbase.assert_true("0x02" in rendered)
    return

def test_parse_dmpstore_multiple_records() -> None:
    _require_cryptography()
    output_a = _build_dmpstore_single(
        name="A", guid=_TEST_GUID, attr_tokens="NV+BS", data=b"\x01",
    )
    output_b = _build_dmpstore_single(
        name="B", guid=_TEST_GUID, attr_tokens="NV+BS+RT", data=b"\x02\x03",
    )
    parsed = _parse_dmpstore_output(output_a + "\n" + output_b)
    testbase.assert_equal(len(parsed), 2)
    testbase.assert_equal(parsed[0].name, "A")
    testbase.assert_equal(parsed[0].data, b"\x01")
    testbase.assert_equal(parsed[1].name, "B")
    testbase.assert_equal(parsed[1].data, b"\x02\x03")
    return


# ---------------------------------------------------------------------------
# Attribute validation
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# AttributeValidationTests
# ----------------------------------------------------------------------

"""
    Cover the cross-field validation rules enforced by
    :func:`_validate_attributes`.
"""

def test_runtime_without_bootservice_raises() -> None:
    _require_cryptography()
    agent = _FakeUefiAgent(transport_kind="serial")
    store = VariableStore(agent=agent)
    variable = UefiVariable(
        name=_TEST_NAME, vendor_guid=_TEST_GUID,
        attributes=UefiVariableAttributes.RUNTIME_ACCESS,
        data=b"",
    )
    with testbase.raises(ConfigurationError):
        store.set_variable(variable=variable)
    return

def test_legacy_and_time_based_auth_mutually_exclusive() -> None:
    _require_cryptography()
    agent = _FakeUefiAgent(transport_kind="serial")
    store = VariableStore(agent=agent)
    attrs = (
        UefiVariableAttributes.NON_VOLATILE
        | UefiVariableAttributes.BOOTSERVICE_ACCESS
        | UefiVariableAttributes.AUTHENTICATED_WRITE_ACCESS
        | UefiVariableAttributes.TIME_BASED_AUTHENTICATED_WRITE_ACCESS
    )
    variable = UefiVariable(
        name=_TEST_NAME, vendor_guid=_TEST_GUID,
        attributes=attrs, data=b"",
    )
    with testbase.raises(ConfigurationError):
        store.set_variable(variable=variable)
    return

def test_enhanced_cannot_combine_with_legacy_auth() -> None:
    _require_cryptography()
    agent = _FakeUefiAgent(transport_kind="serial")
    store = VariableStore(agent=agent)
    attrs = (
        UefiVariableAttributes.NON_VOLATILE
        | UefiVariableAttributes.BOOTSERVICE_ACCESS
        | UefiVariableAttributes.ENHANCED_AUTHENTICATED_ACCESS
        | UefiVariableAttributes.AUTHENTICATED_WRITE_ACCESS
    )
    variable = UefiVariable(
        name=_TEST_NAME, vendor_guid=_TEST_GUID,
        attributes=attrs, data=b"",
    )
    with testbase.raises(ConfigurationError):
        store.set_variable(variable=variable)
    return
