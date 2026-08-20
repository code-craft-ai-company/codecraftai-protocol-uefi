"""
    .. module:: test_uefisecureboot
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Unit tests for the
               :mod:`ccai.protocols.uefi.uefisecureboot` module.
               Exercises the :class:`SignatureEntry` /
               :class:`SignatureList` / :class:`SignatureDatabase` codecs,
               the :meth:`SecureBootManager.read_state` mode derivation,
               the ``setup_mode`` / ``force`` / ``ack`` gate in
               :meth:`SecureBootManager.enroll_signature_database`, and
               the ``dbx`` ``APPEND_WRITE`` shortcut via
               :meth:`SecureBootManager.append_to_dbx`.

               Tests do not open serial ports or talk to real firmware;
               a minimal :class:`_FakeVariableStore` records every
               ``set_variable`` call and returns canned ``UefiVariable``
               records for ``get_variable``.  The authenticated-write
               path uses a pytest fixture that generates a small RSA-2048
               self-signed key / certificate pair in-memory only.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Code Craft AI LLC"
__credits__ = []


import datetime
import struct
import unittest
import uuid

from typing import Any, Dict, List, Optional, Tuple

from ccai.errors.exceptions import ConfigurationError

from ccai.protocols.uefi.uefiaspects import (
    DEFAULT_CMD_ASPECTS,
    UefiCmdAspects,
)
from ccai.protocols.uefi.ueficonst import guid_to_bytes
from ccai.protocols.uefi.uefiexceptions import (
    UefiSecureBootError,
    UefiVariableError,
)
from ccai.protocols.uefi.uefiguids import (
    EFI_GLOBAL_VARIABLE_GUID,
    EFI_IMAGE_SECURITY_DATABASE_GUID,
)
from ccai.protocols.uefi.uefisecureboot import (
    EFI_CERT_SHA256_GUID,
    EFI_CERT_X509_GUID,
    SIGNATURE_DATABASE_MAP,
    SecureBootManager,
    SecureBootMode,
    SecureBootState,
    SignatureDatabase,
    SignatureEntry,
    SignatureList,
)
from ccai.protocols.uefi.uefivariables import (
    UefiVariable,
    UefiVariableAttributes,
)


# ---------------------------------------------------------------------------
# Fake VariableStore stand-in
# ---------------------------------------------------------------------------

_TEST_OWNER_GUID = uuid.UUID("11111111-2222-3333-4444-555566667777")


class _FakeVariableStore:
    """
        Minimal :class:`VariableStore` stand-in backed by a dict keyed on
        ``(name, vendor_guid)``.  Records every ``set_variable`` call in
        :attr:`set_calls` for assertion by tests.
    """

    def __init__(self, initial: Optional[Dict[Tuple[str, uuid.UUID], UefiVariable]] = None) -> None:
        """
            Initialize the fake store.

            :param initial: Optional pre-seeded variable map.
        """
        if initial is None:
            self._store: Dict[Tuple[str, uuid.UUID], UefiVariable] = {}
        else:
            self._store = dict(initial)

        self.set_calls: List[Dict[str, Any]] = []
        return

    def get_variable(self, name: str,
                           vendor_guid: uuid.UUID,
                           aspects: Optional[UefiCmdAspects] = None) -> UefiVariable:
        """
            Return the previously-seeded :class:`UefiVariable` or raise
            :class:`UefiVariableError` when absent.
        """
        key = (name, vendor_guid)
        if key not in self._store:
            err_msg = "fake store: {!r} not found in seeded map.".format(key)
            raise UefiVariableError(
                err_msg,
                name=name,
                vendor_guid=vendor_guid,
                efi_status=0x8000_0000_0000_000E,
            ) from None

        rtnval = self._store[key]
        return rtnval

    def set_variable(self, variable: UefiVariable,
                           aspects: Optional[UefiCmdAspects] = None,
                           signer_key: Optional[Any] = None,
                           signer_cert: Optional[Any] = None,
                           signing_time: Optional[datetime.datetime] = None) -> None:
        """
            Record the call.  Does not actually produce an auth descriptor.
        """
        recorded: Dict[str, Any] = {
            "name": variable.name,
            "vendor_guid": variable.vendor_guid,
            "attributes": variable.attributes,
            "data": variable.data,
            "aspects": aspects,
            "signer_key": signer_key,
            "signer_cert": signer_cert,
            "signing_time": signing_time,
        }
        self.set_calls.append(recorded)

        self._store[(variable.name, variable.vendor_guid)] = variable
        return


# ---------------------------------------------------------------------------
# Helpers for building mode-variable fixtures
# ---------------------------------------------------------------------------

def _flag_variable(name: str, flag: int) -> UefiVariable:
    """
        Build a single-byte Secure Boot mode :class:`UefiVariable`.

        :param name: The variable name.
        :param flag: ``0`` or ``1``.

        :returns: A :class:`UefiVariable` with the flag as its payload.
    """
    payload_bytes = bytes([flag])
    rtnval = UefiVariable(
        name=name,
        vendor_guid=EFI_GLOBAL_VARIABLE_GUID,
        attributes=(
            UefiVariableAttributes.NON_VOLATILE
            | UefiVariableAttributes.BOOTSERVICE_ACCESS
            | UefiVariableAttributes.RUNTIME_ACCESS
        ),
        data=payload_bytes,
    )
    return rtnval


def _seed_mode_variables(secure_boot: int, setup_mode: int,
                         audit_mode: int, deployed_mode: int) -> Dict[Tuple[str, uuid.UUID], UefiVariable]:
    """
        Build a seed map for ``_FakeVariableStore`` covering the four
        Secure Boot mode variables.

        :param secure_boot: ``SecureBoot`` flag value.
        :param setup_mode: ``SetupMode`` flag value.
        :param audit_mode: ``AuditMode`` flag value.
        :param deployed_mode: ``DeployedMode`` flag value.

        :returns: Dict keyed on ``(name, EFI_GLOBAL_VARIABLE_GUID)``.
    """
    seed: Dict[Tuple[str, uuid.UUID], UefiVariable] = {
        ("SecureBoot", EFI_GLOBAL_VARIABLE_GUID): _flag_variable(name="SecureBoot", flag=secure_boot),
        ("SetupMode", EFI_GLOBAL_VARIABLE_GUID): _flag_variable(name="SetupMode", flag=setup_mode),
        ("AuditMode", EFI_GLOBAL_VARIABLE_GUID): _flag_variable(name="AuditMode", flag=audit_mode),
        ("DeployedMode", EFI_GLOBAL_VARIABLE_GUID): _flag_variable(name="DeployedMode", flag=deployed_mode),
    }
    return seed


# ---------------------------------------------------------------------------
# Hand-crafted signature-list buffer builder
# ---------------------------------------------------------------------------

def _build_sha256_esl(owner_entries: List[Tuple[uuid.UUID, bytes]]) -> bytes:
    """
        Hand-craft an ``EFI_SIGNATURE_LIST`` buffer for SHA-256 entries.

        :param owner_entries: List of ``(owner_guid, 32-byte digest)`` pairs.

        :returns: The encoded ESL bytes.
    """
    sig_size = 16 + 32
    sig_header_size = 0
    list_size = 28 + sig_header_size + sig_size * len(owner_entries)

    parts: List[bytes] = []
    parts.append(guid_to_bytes(EFI_CERT_SHA256_GUID))
    parts.append(struct.pack("<I", list_size))
    parts.append(struct.pack("<I", sig_header_size))
    parts.append(struct.pack("<I", sig_size))
    for owner_guid, digest_bytes in owner_entries:
        if len(digest_bytes) != 32:
            raise AssertionError("test helper requires 32-byte payloads")
        parts.append(guid_to_bytes(owner_guid))
        parts.append(digest_bytes)

    rtnval = b"".join(parts)
    return rtnval


def _build_x509_esl(owner_guid: uuid.UUID, cert_bytes: bytes) -> bytes:
    """
        Hand-craft an ``EFI_SIGNATURE_LIST`` buffer carrying a single
        X.509 certificate payload.

        :param owner_guid: The ``SignatureOwner`` GUID.
        :param cert_bytes: The certificate DER bytes.

        :returns: The encoded ESL bytes.
    """
    sig_size = 16 + len(cert_bytes)
    sig_header_size = 0
    list_size = 28 + sig_header_size + sig_size

    parts: List[bytes] = []
    parts.append(guid_to_bytes(EFI_CERT_X509_GUID))
    parts.append(struct.pack("<I", list_size))
    parts.append(struct.pack("<I", sig_header_size))
    parts.append(struct.pack("<I", sig_size))
    parts.append(guid_to_bytes(owner_guid))
    parts.append(cert_bytes)

    rtnval = b"".join(parts)
    return rtnval


# ---------------------------------------------------------------------------
# Lazy self-signed key / certificate pair for auth-write tests
# ---------------------------------------------------------------------------

_KEY_CERT_CACHE: Dict[str, Any] = {}


def _ephemeral_key_and_cert() -> Tuple[Any, Any]:
    """
        Build a small RSA-2048 self-signed key / cert pair lazily and
        cache it for subsequent tests in the same process.  Key material
        never touches disk.

        :returns: Tuple of ``(private_key, certificate)``.
    """
    if "key" in _KEY_CERT_CACHE and "cert" in _KEY_CERT_CACHE:
        rtnval = (_KEY_CERT_CACHE["key"], _KEY_CERT_CACHE["cert"])
        return rtnval

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    subject_name = x509.Name(attributes=[
        x509.NameAttribute(NameOID.COMMON_NAME, "codecraftai-protocol-uefi test signer"),
    ])
    issuer_name = subject_name

    now_utc = datetime.datetime.now(tz=datetime.timezone.utc)
    not_before = now_utc - datetime.timedelta(hours=1)
    not_after = now_utc + datetime.timedelta(days=1)

    builder = x509.CertificateBuilder()
    builder = builder.subject_name(subject_name)
    builder = builder.issuer_name(issuer_name)
    builder = builder.public_key(private_key.public_key())
    builder = builder.serial_number(x509.random_serial_number())
    builder = builder.not_valid_before(not_before)
    builder = builder.not_valid_after(not_after)

    certificate = builder.sign(private_key=private_key, algorithm=hashes.SHA256())

    _KEY_CERT_CACHE["key"] = private_key
    _KEY_CERT_CACHE["cert"] = certificate

    rtnval = (private_key, certificate)
    return rtnval


# ---------------------------------------------------------------------------
# SignatureList / SignatureDatabase codec tests
# ---------------------------------------------------------------------------

class SignatureListRoundTripTests(unittest.TestCase):
    """
        Verify :class:`SignatureList` round-trips through
        :meth:`from_bytes` and :meth:`to_bytes` for SHA-256 and X.509
        payload types.
    """

    def test_sha256_list_roundtrips(self) -> None:
        entries = [
            (_TEST_OWNER_GUID, bytes(range(32))),
            (_TEST_OWNER_GUID, bytes(range(1, 33))),
        ]
        raw_buffer = _build_sha256_esl(owner_entries=entries)

        parsed = SignatureList.from_bytes(raw_buffer)
        self.assertEqual(parsed.signature_type, EFI_CERT_SHA256_GUID)
        self.assertEqual(parsed.header, b"")
        self.assertEqual(len(parsed.signatures), 2)
        self.assertEqual(parsed.signatures[0].owner, _TEST_OWNER_GUID)
        self.assertEqual(parsed.signatures[0].payload, bytes(range(32)))
        self.assertEqual(parsed.signatures[1].payload, bytes(range(1, 33)))

        re_encoded = parsed.to_bytes()
        self.assertEqual(re_encoded, raw_buffer)
        return

    def test_x509_list_roundtrips(self) -> None:
        fake_cert_bytes = bytes(range(256))  # 256 bytes of pseudo-DER
        raw_buffer = _build_x509_esl(
            owner_guid=_TEST_OWNER_GUID, cert_bytes=fake_cert_bytes,
        )

        parsed = SignatureList.from_bytes(raw_buffer)
        self.assertEqual(parsed.signature_type, EFI_CERT_X509_GUID)
        self.assertEqual(len(parsed.signatures), 1)
        self.assertEqual(parsed.signatures[0].payload, fake_cert_bytes)

        re_encoded = parsed.to_bytes()
        self.assertEqual(re_encoded, raw_buffer)
        return

    def test_malformed_size_raises(self) -> None:
        entries = [(_TEST_OWNER_GUID, bytes(range(32)))]
        raw_buffer = _build_sha256_esl(owner_entries=entries)

        # Truncate the buffer but leave the declared list size unchanged.
        bad_buffer = raw_buffer[:-4]

        with self.assertRaises(UefiSecureBootError):
            SignatureList.from_bytes(bad_buffer)
        return

    def test_too_short_buffer_raises(self) -> None:
        with self.assertRaises(UefiSecureBootError):
            SignatureList.from_bytes(b"\x00\x01\x02")
        return


class SignatureDatabaseMultiListTests(unittest.TestCase):
    """
        Verify :meth:`SignatureDatabase.from_bytes` parses multiple
        concatenated ESLs and round-trips through :meth:`to_bytes`.
    """

    def test_multiple_lists_decode_and_reencode(self) -> None:
        sha_buffer = _build_sha256_esl(owner_entries=[
            (_TEST_OWNER_GUID, bytes(range(32))),
        ])
        x509_buffer = _build_x509_esl(
            owner_guid=_TEST_OWNER_GUID, cert_bytes=bytes(range(128)),
        )
        combined = sha_buffer + x509_buffer

        database = SignatureDatabase.from_bytes(combined)
        self.assertEqual(len(database.lists), 2)
        self.assertEqual(database.lists[0].signature_type, EFI_CERT_SHA256_GUID)
        self.assertEqual(database.lists[1].signature_type, EFI_CERT_X509_GUID)

        re_encoded = database.to_bytes()
        self.assertEqual(re_encoded, combined)
        return

    def test_empty_buffer_decodes_to_empty_database(self) -> None:
        database = SignatureDatabase.from_bytes(b"")
        self.assertEqual(database.lists, ())
        self.assertEqual(database.to_bytes(), b"")
        return

    def test_trailing_garbage_raises(self) -> None:
        sha_buffer = _build_sha256_esl(owner_entries=[
            (_TEST_OWNER_GUID, bytes(range(32))),
        ])
        bad_buffer = sha_buffer + b"\x01\x02"

        with self.assertRaises(UefiSecureBootError):
            SignatureDatabase.from_bytes(bad_buffer)
        return


# ---------------------------------------------------------------------------
# SecureBootManager.read_state tests
# ---------------------------------------------------------------------------

class SecureBootManagerReadStateTests(unittest.TestCase):
    """
        Verify :meth:`SecureBootManager.read_state` reads the four mode
        variables and derives :class:`SecureBootMode` correctly.
    """

    def _build_manager(self, secure_boot: int, setup_mode: int,
                             audit_mode: int, deployed_mode: int) -> Tuple[SecureBootManager, _FakeVariableStore]:
        """
            Construct a manager plus fake store seeded with the four mode
            variables.
        """
        seed = _seed_mode_variables(
            secure_boot=secure_boot,
            setup_mode=setup_mode,
            audit_mode=audit_mode,
            deployed_mode=deployed_mode,
        )
        store = _FakeVariableStore(initial=seed)
        manager = SecureBootManager(variable_store=store, aspects=DEFAULT_CMD_ASPECTS)
        rtnval = (manager, store)
        return rtnval

    def test_setup_mode_precedes_other_flags(self) -> None:
        manager, _ = self._build_manager(
            secure_boot=0, setup_mode=1, audit_mode=0, deployed_mode=0,
        )
        state = manager.read_state()
        self.assertIsInstance(state, SecureBootState)
        self.assertEqual(state.mode, SecureBootMode.SETUP)
        self.assertTrue(state.setup_mode)
        self.assertFalse(state.secure_boot)
        return

    def test_audit_mode_without_setup(self) -> None:
        manager, _ = self._build_manager(
            secure_boot=0, setup_mode=0, audit_mode=1, deployed_mode=0,
        )
        state = manager.read_state()
        self.assertEqual(state.mode, SecureBootMode.AUDIT)
        self.assertTrue(state.audit_mode)
        return

    def test_deployed_mode_without_setup_or_audit(self) -> None:
        manager, _ = self._build_manager(
            secure_boot=1, setup_mode=0, audit_mode=0, deployed_mode=1,
        )
        state = manager.read_state()
        self.assertEqual(state.mode, SecureBootMode.DEPLOYED)
        self.assertTrue(state.deployed_mode)
        self.assertTrue(state.secure_boot)
        return

    def test_user_mode_is_default(self) -> None:
        manager, _ = self._build_manager(
            secure_boot=1, setup_mode=0, audit_mode=0, deployed_mode=0,
        )
        state = manager.read_state()
        self.assertEqual(state.mode, SecureBootMode.USER)
        self.assertTrue(state.secure_boot)
        return

    def test_missing_variable_raises(self) -> None:
        # Seed only three of the four mode variables.
        seed = _seed_mode_variables(
            secure_boot=1, setup_mode=0, audit_mode=0, deployed_mode=0,
        )
        del seed[("DeployedMode", EFI_GLOBAL_VARIABLE_GUID)]
        store = _FakeVariableStore(initial=seed)
        manager = SecureBootManager(variable_store=store, aspects=DEFAULT_CMD_ASPECTS)

        with self.assertRaises(UefiSecureBootError):
            manager.read_state()
        return


# ---------------------------------------------------------------------------
# enroll_signature_database — SetupMode guard tests
# ---------------------------------------------------------------------------

class SecureBootManagerEnrollGateTests(unittest.TestCase):
    """
        Verify the SetupMode / force / ack decision tree in
        :meth:`SecureBootManager.enroll_signature_database`.
    """

    def _build_signing_time(self) -> datetime.datetime:
        """
            Produce a timezone-aware :class:`datetime.datetime`.
        """
        rtnval = datetime.datetime(2030, 1, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
        return rtnval

    def _build_small_database(self) -> SignatureDatabase:
        """
            Build a minimal single-entry :class:`SignatureDatabase` for
            use in enrollment write tests.
        """
        entry = SignatureEntry(
            signature_type=EFI_CERT_SHA256_GUID,
            owner=_TEST_OWNER_GUID,
            payload=bytes(range(32)),
        )
        sig_list = SignatureList(
            signature_type=EFI_CERT_SHA256_GUID,
            header=b"",
            signatures=(entry,),
        )
        rtnval = SignatureDatabase(lists=(sig_list,))
        return rtnval

    def test_refuses_outside_setup_mode_without_force(self) -> None:
        seed = _seed_mode_variables(
            secure_boot=1, setup_mode=0, audit_mode=0, deployed_mode=1,
        )
        store = _FakeVariableStore(initial=seed)
        manager = SecureBootManager(variable_store=store)

        database = self._build_small_database()
        signing_time = self._build_signing_time()

        with self.assertRaises(UefiSecureBootError) as ctx:
            manager.enroll_signature_database(
                name="db",
                database=database,
                signer_key=object(),
                signer_cert=object(),
                signing_time=signing_time,
            )
        rendered = str(ctx.exception)
        self.assertIn("db", rendered)
        self.assertEqual(len(store.set_calls), 0)
        return

    def test_force_without_ack_raises(self) -> None:
        seed = _seed_mode_variables(
            secure_boot=1, setup_mode=0, audit_mode=0, deployed_mode=1,
        )
        store = _FakeVariableStore(initial=seed)
        manager = SecureBootManager(variable_store=store)

        database = self._build_small_database()
        signing_time = self._build_signing_time()

        with self.assertRaises(UefiSecureBootError) as ctx:
            manager.enroll_signature_database(
                name="db",
                database=database,
                signer_key=object(),
                signer_cert=object(),
                signing_time=signing_time,
                force=True,
                ack=None,
            )
        rendered = str(ctx.exception)
        self.assertIn("operator", rendered)
        self.assertIn("ticket", rendered)
        self.assertEqual(len(store.set_calls), 0)
        return

    def test_force_with_empty_ack_fields_raises(self) -> None:
        seed = _seed_mode_variables(
            secure_boot=1, setup_mode=0, audit_mode=0, deployed_mode=1,
        )
        store = _FakeVariableStore(initial=seed)
        manager = SecureBootManager(variable_store=store)

        database = self._build_small_database()
        signing_time = self._build_signing_time()

        bad_ack: Dict[str, str] = {"operator": "", "ticket": ""}
        with self.assertRaises(UefiSecureBootError):
            manager.enroll_signature_database(
                name="db",
                database=database,
                signer_key=object(),
                signer_cert=object(),
                signing_time=signing_time,
                force=True,
                ack=bad_ack,
            )
        self.assertEqual(len(store.set_calls), 0)
        return

    def test_force_with_ack_writes(self) -> None:
        seed = _seed_mode_variables(
            secure_boot=1, setup_mode=0, audit_mode=0, deployed_mode=1,
        )
        store = _FakeVariableStore(initial=seed)
        manager = SecureBootManager(variable_store=store)

        database = self._build_small_database()
        signing_time = self._build_signing_time()

        signer_key, signer_cert = _ephemeral_key_and_cert()

        manager.enroll_signature_database(
            name="db",
            database=database,
            signer_key=signer_key,
            signer_cert=signer_cert,
            signing_time=signing_time,
            force=True,
            ack={"operator": "alice", "ticket": "JIRA-1234"},
        )

        self.assertEqual(len(store.set_calls), 1)
        recorded = store.set_calls[0]
        self.assertEqual(recorded["name"], "db")
        self.assertEqual(recorded["vendor_guid"], EFI_IMAGE_SECURITY_DATABASE_GUID)
        time_bit = int(UefiVariableAttributes.TIME_BASED_AUTHENTICATED_WRITE_ACCESS)
        self.assertNotEqual(int(recorded["attributes"]) & time_bit, 0)
        append_bit = int(UefiVariableAttributes.APPEND_WRITE)
        self.assertEqual(int(recorded["attributes"]) & append_bit, 0)
        self.assertEqual(recorded["signer_key"], signer_key)
        self.assertEqual(recorded["signer_cert"], signer_cert)
        self.assertEqual(recorded["signing_time"], signing_time)
        self.assertEqual(recorded["data"], database.to_bytes())
        return

    def test_setup_mode_enrollment_does_not_require_force(self) -> None:
        seed = _seed_mode_variables(
            secure_boot=0, setup_mode=1, audit_mode=0, deployed_mode=0,
        )
        store = _FakeVariableStore(initial=seed)
        manager = SecureBootManager(variable_store=store)

        database = self._build_small_database()
        signing_time = self._build_signing_time()

        signer_key, signer_cert = _ephemeral_key_and_cert()

        manager.enroll_signature_database(
            name="KEK",
            database=database,
            signer_key=signer_key,
            signer_cert=signer_cert,
            signing_time=signing_time,
        )

        self.assertEqual(len(store.set_calls), 1)
        recorded = store.set_calls[0]
        self.assertEqual(recorded["name"], "KEK")
        self.assertEqual(recorded["vendor_guid"], EFI_GLOBAL_VARIABLE_GUID)
        return

    def test_unknown_database_name_raises_configuration_error(self) -> None:
        seed = _seed_mode_variables(
            secure_boot=0, setup_mode=1, audit_mode=0, deployed_mode=0,
        )
        store = _FakeVariableStore(initial=seed)
        manager = SecureBootManager(variable_store=store)

        database = self._build_small_database()
        signing_time = self._build_signing_time()

        with self.assertRaises(ConfigurationError):
            manager.enroll_signature_database(
                name="nonsense",
                database=database,
                signer_key=object(),
                signer_cert=object(),
                signing_time=signing_time,
            )
        return


# ---------------------------------------------------------------------------
# append_to_dbx — APPEND_WRITE attribute tests
# ---------------------------------------------------------------------------

class SecureBootManagerAppendToDbxTests(unittest.TestCase):
    """
        Verify :meth:`SecureBootManager.append_to_dbx` sets the
        ``APPEND_WRITE`` attribute and writes to the
        :data:`EFI_IMAGE_SECURITY_DATABASE_GUID` namespace.
    """

    def test_dbx_append_sets_append_write_bit(self) -> None:
        seed = _seed_mode_variables(
            secure_boot=0, setup_mode=1, audit_mode=0, deployed_mode=0,
        )
        store = _FakeVariableStore(initial=seed)
        manager = SecureBootManager(variable_store=store)

        entry = SignatureEntry(
            signature_type=EFI_CERT_SHA256_GUID,
            owner=_TEST_OWNER_GUID,
            payload=bytes(range(32)),
        )
        sig_list = SignatureList(
            signature_type=EFI_CERT_SHA256_GUID,
            header=b"",
            signatures=(entry,),
        )
        database = SignatureDatabase(lists=(sig_list,))

        signing_time = datetime.datetime(2030, 6, 15, 9, 30, 0, tzinfo=datetime.timezone.utc)
        signer_key, signer_cert = _ephemeral_key_and_cert()

        manager.append_to_dbx(
            database=database,
            signer_key=signer_key,
            signer_cert=signer_cert,
            signing_time=signing_time,
        )

        self.assertEqual(len(store.set_calls), 1)
        recorded = store.set_calls[0]
        self.assertEqual(recorded["name"], "dbx")
        self.assertEqual(recorded["vendor_guid"], EFI_IMAGE_SECURITY_DATABASE_GUID)

        attribute_value = int(recorded["attributes"])
        append_bit = int(UefiVariableAttributes.APPEND_WRITE)
        time_bit = int(UefiVariableAttributes.TIME_BASED_AUTHENTICATED_WRITE_ACCESS)
        self.assertNotEqual(attribute_value & append_bit, 0)
        self.assertNotEqual(attribute_value & time_bit, 0)
        return


# ---------------------------------------------------------------------------
# SIGNATURE_DATABASE_MAP shape sanity check
# ---------------------------------------------------------------------------

class SignatureDatabaseMapShapeTests(unittest.TestCase):
    """
        Verify :data:`SIGNATURE_DATABASE_MAP` covers the six expected
        names and routes each to the correct vendor GUID.
    """

    def test_map_covers_expected_names(self) -> None:
        expected_names = {"PK", "KEK", "db", "dbx", "dbt", "dbr"}
        self.assertEqual(set(SIGNATURE_DATABASE_MAP.keys()), expected_names)
        self.assertEqual(SIGNATURE_DATABASE_MAP["PK"], EFI_GLOBAL_VARIABLE_GUID)
        self.assertEqual(SIGNATURE_DATABASE_MAP["KEK"], EFI_GLOBAL_VARIABLE_GUID)
        self.assertEqual(SIGNATURE_DATABASE_MAP["db"], EFI_IMAGE_SECURITY_DATABASE_GUID)
        self.assertEqual(SIGNATURE_DATABASE_MAP["dbx"], EFI_IMAGE_SECURITY_DATABASE_GUID)
        self.assertEqual(SIGNATURE_DATABASE_MAP["dbt"], EFI_IMAGE_SECURITY_DATABASE_GUID)
        self.assertEqual(SIGNATURE_DATABASE_MAP["dbr"], EFI_IMAGE_SECURITY_DATABASE_GUID)
        return


if __name__ == "__main__":
    unittest.main()
