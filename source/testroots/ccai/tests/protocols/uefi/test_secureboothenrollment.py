"""
    .. module:: test_secureboothenrollment
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Unit tests for the
               :mod:`ccai.protocols.uefi.secureboothenrollment` layer-6
               orchestration module.  Exercises the must_connect
               invariant gate, the ack requirement for non-dry-run
               enrollments, the canonical enrollment order
               ``(KEK, db, dbx, PK)``, the dry-run short-circuit, and the
               already-in-User-Mode + ack path.

               Tests do not talk to firmware; a :class:`_FakeSecureBootManager`
               records each write and serves observed databases back to
               the verification pass by default.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Code Craft AI LLC"
__credits__ = []


from ccai import testbase

import datetime
import uuid

from typing import Any, Dict, List, Optional

from ccai.protocols.uefi.uefiaspects import (
    UefiCmdAspects,
)
from ccai.protocols.uefi.uefiexceptions import UefiSecureBootError
from ccai.protocols.uefi.uefisecureboot import (
    EFI_CERT_SHA256_GUID,
    SignatureDatabase,
    SignatureEntry,
    SignatureList,
)
from ccai.protocols.uefi.secureboothenrollment import (
    EnrollmentPlan,
    EnrollmentResult,
    SecureBootEnrollment,
)


# ---------------------------------------------------------------------------
# Canonical fixtures
# ---------------------------------------------------------------------------

_TEST_OWNER_GUID: uuid.UUID = uuid.UUID("11111111-2222-3333-4444-555566667777")


def _build_sha256_database(payload_seed: int) -> SignatureDatabase:
    """
        Build a simple :class:`SignatureDatabase` carrying a single SHA-256
        entry whose 32-byte payload encodes ``payload_seed``.

        :param payload_seed: A small integer (0-255) written into every
                             byte of the payload.

        :returns: A populated :class:`SignatureDatabase`.
    """
    payload_bytes = bytes([payload_seed & 0xFF] * 32)
    entry = SignatureEntry(
        signature_type=EFI_CERT_SHA256_GUID,
        owner=_TEST_OWNER_GUID,
        payload=payload_bytes,
    )
    sig_list = SignatureList(
        signature_type=EFI_CERT_SHA256_GUID,
        header=b"",
        signatures=(entry,),
    )
    rtnval = SignatureDatabase(lists=(sig_list,))
    return rtnval


def _build_aspects(must_connect: bool) -> UefiCmdAspects:
    """
        Build a :class:`UefiCmdAspects` instance with the caller-chosen
        ``must_connect`` flag.

        :param must_connect: Value of ``must_connect``.

        :returns: A frozen aspects instance.
    """
    rtnval = UefiCmdAspects(must_connect=must_connect)
    return rtnval


def _build_plan(pk: Optional[SignatureDatabase] = None,
                kek: Optional[SignatureDatabase] = None,
                db: Optional[SignatureDatabase] = None,
                dbx: Optional[SignatureDatabase] = None) -> EnrollmentPlan:
    """
        Build an :class:`EnrollmentPlan` from the supplied databases.

        :param pk: Optional PK database.
        :param kek: Optional KEK database.
        :param db: Optional db database.
        :param dbx: Optional dbx database.

        :returns: A populated :class:`EnrollmentPlan` with a sentinel
                  signer key / cert and timestamp.
    """
    rtnval = EnrollmentPlan(
        pk_database=pk,
        kek_database=kek,
        db_database=db,
        dbx_database=dbx,
        signer_key=object(),
        signer_cert=object(),
        signing_time=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
    )
    return rtnval


def _build_full_plan() -> EnrollmentPlan:
    """
        Build a plan populating all four databases with distinct payloads.

        :returns: A fully-populated :class:`EnrollmentPlan`.
    """
    rtnval = _build_plan(
        pk=_build_sha256_database(payload_seed=1),
        kek=_build_sha256_database(payload_seed=2),
        db=_build_sha256_database(payload_seed=3),
        dbx=_build_sha256_database(payload_seed=4),
    )
    return rtnval


# ---------------------------------------------------------------------------
# Fake SecureBootManager
# ---------------------------------------------------------------------------

class _FakeSecureBootManager:
    """
        Fake :class:`SecureBootManager` stand-in.  Records every
        ``enroll_signature_database`` call in :attr:`write_calls` and
        serves observed databases back through
        :meth:`read_signature_database`.

        :ivar write_calls: List of recorded write-call descriptors (dicts).
        :ivar enrollment_exception: Optional exception to raise from the
                                    next ``enroll_signature_database``
                                    call.
        :ivar read_override: Optional mapping of variable name to
                             :class:`SignatureDatabase` used to shadow the
                             auto-served observed data (lets tests inject
                             a mismatch and force verification failure).
    """

    def __init__(self) -> None:
        """
            Initialize the fake manager.
        """
        self.write_calls: List[Dict[str, Any]] = []
        self.enrollment_exception: Optional[BaseException] = None
        self.read_override: Dict[str, SignatureDatabase] = {}
        self._store: Dict[str, SignatureDatabase] = {}
        return

    def enroll_signature_database(self, name: str,
                                        database: SignatureDatabase,
                                        signer_key: Any,
                                        signer_cert: Any,
                                        signing_time: datetime.datetime,
                                        force: bool = False,
                                        ack: Optional[Dict[str, str]] = None,
                                        aspects: Optional[UefiCmdAspects] = None) -> None:
        """
            Record the call.  Raise a queued exception when configured.

            :param name: Variable name.
            :param database: The signature database to write.
            :param signer_key: The private-key object.
            :param signer_cert: The certificate object.
            :param signing_time: The time.
            :param force: The force flag.
            :param ack: The acknowledgement dictionary.
            :param aspects: The aspects.
        """
        recorded: Dict[str, Any] = {
            "name": name,
            "database": database,
            "signer_key": signer_key,
            "signer_cert": signer_cert,
            "signing_time": signing_time,
            "force": force,
            "ack": ack,
            "aspects": aspects,
        }
        self.write_calls.append(recorded)

        if self.enrollment_exception is not None:
            pending = self.enrollment_exception
            self.enrollment_exception = None
            raise pending

        self._store[name] = database
        return

    def read_signature_database(self, name: str,
                                      aspects: Optional[UefiCmdAspects] = None) -> SignatureDatabase:
        """
            Return the shadowed or recorded :class:`SignatureDatabase`.

            :param name: The variable name.
            :param aspects: Ignored.

            :returns: The observed :class:`SignatureDatabase`.
        """
        if name in self.read_override:
            rtnval = self.read_override[name]
            return rtnval

        if name in self._store:
            rtnval = self._store[name]
            return rtnval

        # Absent variable -> empty database.
        rtnval = SignatureDatabase(lists=())
        return rtnval


# ---------------------------------------------------------------------------
# Must-connect invariant
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# SecureBootEnrollmentMustConnectTests
# ----------------------------------------------------------------------

"""
    Verify that ``aspects.must_connect is not True`` is a hard stop.
"""

def test_must_connect_false_raises() -> None:
    manager = _FakeSecureBootManager()
    aspects = _build_aspects(must_connect=False)
    workflow = SecureBootEnrollment(
        secure_boot_manager=manager, aspects=aspects,
    )

    plan = _build_full_plan()

    with testbase.raises(UefiSecureBootError) as context:
        workflow.enroll(plan=plan, ack={"operator": "op", "ticket": "T-1"})

    message = str(context.exception)
    testbase.assert_true("must_connect=True" in message)
    testbase.assert_equal(len(manager.write_calls), 0)
    return


# ---------------------------------------------------------------------------
# Dry-run mode
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# SecureBootEnrollmentDryRunTests
# ----------------------------------------------------------------------

"""
    Verify dry-run builds a plan and writes nothing.
"""

def test_dry_run_reports_plan_without_writing() -> None:
    manager = _FakeSecureBootManager()
    aspects = _build_aspects(must_connect=True)
    workflow = SecureBootEnrollment(
        secure_boot_manager=manager, aspects=aspects,
    )

    plan = _build_full_plan()

    result = workflow.enroll(plan=plan, dry_run=True, ack=None)

    testbase.assert_true(isinstance(result, EnrollmentResult))
    testbase.assert_true(result.dry_run)
    testbase.assert_true(result.success)
    testbase.assert_equal(result.enrolled, ("KEK", "db", "dbx", "PK"))
    testbase.assert_equal(len(manager.write_calls), 0)
    return

def test_dry_run_skips_missing_databases() -> None:
    manager = _FakeSecureBootManager()
    aspects = _build_aspects(must_connect=True)
    workflow = SecureBootEnrollment(
        secure_boot_manager=manager, aspects=aspects,
    )

    plan = _build_plan(
        kek=_build_sha256_database(payload_seed=7),
        db=_build_sha256_database(payload_seed=8),
    )

    result = workflow.enroll(plan=plan, dry_run=True, ack=None)

    testbase.assert_equal(result.enrolled, ("KEK", "db"))
    testbase.assert_equal(len(manager.write_calls), 0)
    return


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# SecureBootEnrollmentHappyPathTests
# ----------------------------------------------------------------------

"""
    Verify enrollment order and content under the happy path.
"""

def test_full_enrollment_order_is_kek_db_dbx_pk() -> None:
    manager = _FakeSecureBootManager()
    aspects = _build_aspects(must_connect=True)
    workflow = SecureBootEnrollment(
        secure_boot_manager=manager, aspects=aspects,
    )

    plan = _build_full_plan()
    ack = {"operator": "myron", "ticket": "SB-42"}

    result = workflow.enroll(plan=plan, ack=ack)

    testbase.assert_true(result.success)
    testbase.assert_false(result.dry_run)
    testbase.assert_equal(result.enrolled, ("KEK", "db", "dbx", "PK"))

    observed_order = tuple(call["name"] for call in manager.write_calls)
    testbase.assert_equal(observed_order, ("KEK", "db", "dbx", "PK"))

    # Each write carried the caller's ack verbatim.
    for call in manager.write_calls:
        testbase.assert_equal(call["ack"], ack)
        testbase.assert_true(call["force"])
    return

def test_partial_plan_writes_only_configured_databases() -> None:
    manager = _FakeSecureBootManager()
    aspects = _build_aspects(must_connect=True)
    workflow = SecureBootEnrollment(
        secure_boot_manager=manager, aspects=aspects,
    )

    plan = _build_plan(
        kek=_build_sha256_database(payload_seed=2),
        pk=_build_sha256_database(payload_seed=5),
    )
    ack = {"operator": "ops", "ticket": "JIRA-1"}

    result = workflow.enroll(plan=plan, ack=ack)

    testbase.assert_equal(result.enrolled, ("KEK", "PK"))
    observed_order = tuple(call["name"] for call in manager.write_calls)
    testbase.assert_equal(observed_order, ("KEK", "PK"))
    return


# ---------------------------------------------------------------------------
# Ack required
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# SecureBootEnrollmentAckRequirementTests
# ----------------------------------------------------------------------

"""
    Verify the ack is required for destructive writes.
"""

def test_missing_ack_when_not_dry_run_raises() -> None:
    manager = _FakeSecureBootManager()
    aspects = _build_aspects(must_connect=True)
    workflow = SecureBootEnrollment(
        secure_boot_manager=manager, aspects=aspects,
    )

    plan = _build_full_plan()

    with testbase.raises(UefiSecureBootError) as context:
        workflow.enroll(plan=plan, ack=None)

    message = str(context.exception)
    testbase.assert_true("ack" in message)
    testbase.assert_equal(len(manager.write_calls), 0)
    return

def test_empty_ack_value_raises() -> None:
    manager = _FakeSecureBootManager()
    aspects = _build_aspects(must_connect=True)
    workflow = SecureBootEnrollment(
        secure_boot_manager=manager, aspects=aspects,
    )

    plan = _build_full_plan()
    bad_ack = {"operator": "", "ticket": "T-1"}

    with testbase.raises(UefiSecureBootError):
        workflow.enroll(plan=plan, ack=bad_ack)

    testbase.assert_equal(len(manager.write_calls), 0)
    return

def test_already_user_mode_with_ack_proceeds() -> None:
    # This test exercises the "force via ack" path — in the production
    # manager, writes outside SetupMode require force=True plus a
    # valid ack.  The workflow always passes force=True together with
    # the caller's ack, so a User-Mode target is not a hard stop when
    # the caller has supplied authorization.
    manager = _FakeSecureBootManager()
    aspects = _build_aspects(must_connect=True)
    workflow = SecureBootEnrollment(
        secure_boot_manager=manager, aspects=aspects,
    )

    plan = _build_full_plan()
    ack = {"operator": "myron", "ticket": "PROD-7"}

    result = workflow.enroll(plan=plan, ack=ack)

    testbase.assert_true(result.success)
    testbase.assert_equal(result.enrolled, ("KEK", "db", "dbx", "PK"))
    for call in manager.write_calls:
        testbase.assert_true(call["force"])
        testbase.assert_equal(call["ack"], ack)
    return


# ---------------------------------------------------------------------------
# Plan-shape gates
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# SecureBootEnrollmentPlanShapeTests
# ----------------------------------------------------------------------

"""
    Verify the plan-shape gates.
"""

def test_empty_plan_raises() -> None:
    manager = _FakeSecureBootManager()
    aspects = _build_aspects(must_connect=True)
    workflow = SecureBootEnrollment(
        secure_boot_manager=manager, aspects=aspects,
    )

    plan = _build_plan()
    ack = {"operator": "op", "ticket": "T-1"}

    with testbase.raises(UefiSecureBootError) as context:
        workflow.enroll(plan=plan, ack=ack)

    message = str(context.exception)
    testbase.assert_true("no databases" in message)
    testbase.assert_equal(len(manager.write_calls), 0)
    return


# ---------------------------------------------------------------------------
# Verification after write
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# SecureBootEnrollmentVerificationTests
# ----------------------------------------------------------------------

"""
    Verify the post-write verification step catches a missing entry.
"""

def test_missing_entry_after_write_raises() -> None:
    manager = _FakeSecureBootManager()
    aspects = _build_aspects(must_connect=True)
    workflow = SecureBootEnrollment(
        secure_boot_manager=manager, aspects=aspects,
    )

    plan = _build_plan(kek=_build_sha256_database(payload_seed=11))
    ack = {"operator": "op", "ticket": "T-1"}

    # Inject a read override that returns an empty database for KEK
    # so the verification step finds a missing entry.
    manager.read_override["KEK"] = SignatureDatabase(lists=())

    with testbase.raises(UefiSecureBootError) as context:
        workflow.enroll(plan=plan, ack=ack)

    message = str(context.exception)
    testbase.assert_true("missing entries" in message)
    # The write itself was attempted, even though verification failed.
    testbase.assert_equal(len(manager.write_calls), 1)
    return
