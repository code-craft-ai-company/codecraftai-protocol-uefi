"""
    .. module:: secureboothenrollment
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Layer-6 workflow that composes :class:`SecureBootManager` to
               enroll the Platform Key (PK), Key Exchange Key (KEK),
               signature database (db) and forbidden signature database
               (dbx) in a single call.  Writes are performed in the order
               mandated by UEFI §32 so that PK enrollment happens last and
               correctly transitions the target from SetupMode to User
               Mode.  The workflow enforces strict safety gates — it
               refuses to run unless ``aspects.must_connect=True`` and
               unless the caller has supplied an operator / ticket
               acknowledgement dictionary (or has requested ``dry_run``) —
               because partial enrollment leaves the target unable to
               boot.

               Enrollment is the highest-risk operation in the library.
               No private-key material is ever persisted or logged; every
               write is followed by a re-read verification that compares
               the observed signature database against the expected
               payload before moving on to the next variable.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Automation Mojo LLC"
__credits__ = []


import datetime
import os

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from automojo.protocols.uefi.uefiaspects import (
    DEFAULT_CMD_ASPECTS,
    UefiCmdAspects,
    resolve_aspects,
)
from automojo.protocols.uefi.uefiexceptions import UefiSecureBootError
from automojo.protocols.uefi.uefisecureboot import (
    SecureBootManager,
    SignatureDatabase,
)


__all__ = [
    "EnrollmentPlan",
    "EnrollmentResult",
    "SecureBootEnrollment",
]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Enrollment order per UEFI §32: KEK first, then db, then dbx, then PK.
# Writing PK last is mandatory because writing PK transitions the
# SetupMode→User Mode; writing KEK / db / dbx after that transition would
# require authenticated writes signed by PK which complicates rollback.
_ENROLLMENT_ORDER: Tuple[str, ...] = ("KEK", "db", "dbx", "PK")

_REQUIRED_ACK_KEYS: Tuple[str, ...] = ("operator", "ticket")


# ---------------------------------------------------------------------------
# EnrollmentPlan
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EnrollmentPlan:
    """
        Declarative specification of a Secure Boot enrollment.  The plan
        carries zero or one :class:`SignatureDatabase` per well-known
        Secure Boot variable plus the signing credentials used to
        authenticate each write.

        :ivar pk_database: The PK database to enroll, or ``None`` to skip
                           the PK write (PK is required for a
                           SetupMode→User Mode transition; callers that
                           only want to top up KEK / db / dbx pass ``None``
                           here).
        :ivar kek_database: The KEK database to enroll, or ``None`` to
                            skip.
        :ivar db_database: The db database to enroll, or ``None`` to skip.
        :ivar dbx_database: The dbx database to append, or ``None`` to
                            skip.
        :ivar signer_key: A ``cryptography`` private-key object used to
                          sign each authenticated write.  Never persisted.
        :ivar signer_cert: The matching ``cryptography`` X.509 certificate.
        :ivar signing_time: Timezone-aware :class:`datetime.datetime` used
                            as the ``EFI_TIME`` timestamp on each
                            authenticated write.
    """

    pk_database: Optional[SignatureDatabase]
    kek_database: Optional[SignatureDatabase]
    db_database: Optional[SignatureDatabase]
    dbx_database: Optional[SignatureDatabase]
    signer_key: object
    signer_cert: object
    signing_time: datetime.datetime


# ---------------------------------------------------------------------------
# EnrollmentResult
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EnrollmentResult:
    """
        Structured result of an enrollment call.

        :ivar success: ``True`` when every planned write completed
                       successfully (or when ``dry_run`` was ``True`` and
                       every safety gate passed).
        :ivar enrolled: Tuple of variable names that were written (or
                        would have been written, for dry-run).  Ordered by
                        the canonical enrollment order.
        :ivar dry_run: Mirrors the ``dry_run`` parameter passed to
                       :meth:`SecureBootEnrollment.enroll`.
    """

    success: bool
    enrolled: Tuple[str, ...]
    dry_run: bool


# ---------------------------------------------------------------------------
# Module-internal helpers
# ---------------------------------------------------------------------------

def _validate_enrollment_ack(ack: Optional[Dict[str, str]]) -> Dict[str, str]:
    """
        Validate an enrollment acknowledgement dictionary.  The dictionary
        must carry non-empty string values for every entry in
        :data:`_REQUIRED_ACK_KEYS`.

        :param ack: Candidate acknowledgement dictionary.

        :returns: The validated dictionary (a fresh copy).
        :raises: UefiSecureBootError
    """
    if ack is None:
        err_msg_lines = [
            "SecureBootEnrollment: destructive enrollment requires an 'ack' dictionary.",
            "MISSING KEYS: {}".format(list(_REQUIRED_ACK_KEYS)),
            "HINT: pass ack={'operator': ..., 'ticket': ...} or dry_run=True.",
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiSecureBootError(err_msg) from None

    if not isinstance(ack, dict):
        err_msg_lines = [
            "SecureBootEnrollment: 'ack' must be a dict of str -> str.",
            "GOT: value={!r} type={}".format(ack, type(ack).__name__),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiSecureBootError(err_msg) from None

    validated: Dict[str, str] = {}
    missing_keys: list = []
    for key in _REQUIRED_ACK_KEYS:
        if key not in ack:
            missing_keys.append(key)
            continue
        candidate_value = ack[key]
        if not isinstance(candidate_value, str):
            err_msg_lines = [
                "SecureBootEnrollment: 'ack' entries must be non-empty strings.",
                "KEY: {}".format(key),
                "GOT: value={!r} type={}".format(candidate_value, type(candidate_value).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiSecureBootError(err_msg) from None
        if len(candidate_value) == 0:
            missing_keys.append(key)
            continue
        validated[key] = candidate_value

    if len(missing_keys) > 0:
        err_msg_lines = [
            "SecureBootEnrollment: 'ack' dictionary is missing or has empty required keys.",
            "MISSING_OR_EMPTY_KEYS: {}".format(missing_keys),
            "REQUIRED_KEYS: {}".format(list(_REQUIRED_ACK_KEYS)),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiSecureBootError(err_msg) from None

    return validated


def _database_for(plan: EnrollmentPlan, variable_name: str) -> Optional[SignatureDatabase]:
    """
        Resolve the plan field that corresponds to a Secure Boot variable
        name.

        :param plan: The enrollment plan.
        :param variable_name: One of ``"PK"``, ``"KEK"``, ``"db"``,
                              ``"dbx"``.

        :returns: The paired :class:`SignatureDatabase` or ``None`` when
                  the plan skips this variable.
        :raises: UefiSecureBootError
    """
    if variable_name == "PK":
        rtnval = plan.pk_database
        return rtnval

    if variable_name == "KEK":
        rtnval = plan.kek_database
        return rtnval

    if variable_name == "db":
        rtnval = plan.db_database
        return rtnval

    if variable_name == "dbx":
        rtnval = plan.dbx_database
        return rtnval

    err_msg_lines = [
        "SecureBootEnrollment: unknown enrollment variable name.",
        "GOT: value={!r}".format(variable_name),
        "ALLOWED: {}".format(list(_ENROLLMENT_ORDER)),
    ]
    err_msg = os.linesep.join(err_msg_lines)
    raise UefiSecureBootError(err_msg) from None


def _plan_is_empty(plan: EnrollmentPlan) -> bool:
    """
        Return ``True`` when every database slot on the plan is ``None``.

        :param plan: The enrollment plan.

        :returns: ``True`` iff the plan carries no databases to write.
    """
    if plan.pk_database is not None:
        rtnval = False
        return rtnval

    if plan.kek_database is not None:
        rtnval = False
        return rtnval

    if plan.db_database is not None:
        rtnval = False
        return rtnval

    if plan.dbx_database is not None:
        rtnval = False
        return rtnval

    rtnval = True
    return rtnval


def _verify_write(manager: SecureBootManager, variable_name: str,
                  expected: SignatureDatabase,
                  aspects: Optional[UefiCmdAspects]) -> None:
    """
        Re-read the named Secure Boot variable and verify every expected
        signature-list entry is present.

        :param manager: The :class:`SecureBootManager` to drive.
        :param variable_name: The Secure Boot variable name.
        :param expected: The expected :class:`SignatureDatabase`.
        :param aspects: Optional per-call aspects.

        :raises: UefiSecureBootError
    """
    try:
        observed = manager.read_signature_database(
            name=variable_name, aspects=aspects,
        )
    except UefiSecureBootError:
        raise
    except BaseException as rerr:
        err_msg_lines = [
            "SecureBootEnrollment: post-write read-back failed.",
            "VARIABLE: {}".format(variable_name),
            "EXCEPTION: {!r}".format(rerr),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiSecureBootError(err_msg) from rerr

    missing_entries: list = []
    for expected_list in expected.lists:
        for expected_entry in expected_list.signatures:
            found = False
            for observed_list in observed.lists:
                for observed_entry in observed_list.signatures:
                    if observed_entry.signature_type != expected_entry.signature_type:
                        continue
                    if observed_entry.owner != expected_entry.owner:
                        continue
                    if observed_entry.payload != expected_entry.payload:
                        continue
                    found = True
                    break
                if found:
                    break
            if not found:
                missing_entries.append(
                    (
                        str(expected_entry.signature_type),
                        str(expected_entry.owner),
                    )
                )

    if len(missing_entries) > 0:
        err_msg_lines = [
            "SecureBootEnrollment: post-write verification found missing entries.",
            "VARIABLE: {}".format(variable_name),
            "MISSING_COUNT: {}".format(len(missing_entries)),
        ]
        for index, entry_descriptor in enumerate(missing_entries):
            err_msg_lines.append(
                "MISSING[{}]: signature_type={} owner={}".format(
                    index, entry_descriptor[0], entry_descriptor[1],
                )
            )
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiSecureBootError(err_msg) from None

    return


# ---------------------------------------------------------------------------
# SecureBootEnrollment
# ---------------------------------------------------------------------------

class SecureBootEnrollment:
    """
        Layer-6 orchestration of a full Secure Boot key-hierarchy
        enrollment.

        The workflow drives a :class:`SecureBootManager` to enroll the
        databases carried by a caller-supplied :class:`EnrollmentPlan` in
        the canonical order ``(KEK, db, dbx, PK)``.  Every write is
        verified by re-reading the variable before the next write
        commences.  Safety gates enforce that destructive operations
        require explicit commitment:

        * ``aspects.must_connect`` must be ``True`` — connection failures
          mid-enrollment leave the target in an ambiguous state, so silent
          retry is forbidden.
        * Either ``dry_run=True`` is set, or the caller must pass an
          ``ack`` dictionary carrying non-empty ``operator`` and
          ``ticket`` strings.  The acknowledgement is recorded in the
          audit log.

        :param secure_boot_manager: The :class:`SecureBootManager` that
                                    performs the reads and writes.
        :param aspects: Default :class:`UefiCmdAspects` used when
                        :meth:`enroll` is called without a ``aspects``
                        override.
    """

    def __init__(self, secure_boot_manager: SecureBootManager,
                       aspects: UefiCmdAspects = DEFAULT_CMD_ASPECTS) -> None:
        """
            Initialize the workflow.

            :param secure_boot_manager: The :class:`SecureBootManager`
                                        collaborator.
            :param aspects: The default :class:`UefiCmdAspects`.

            :raises: UefiSecureBootError
        """
        if secure_boot_manager is None:
            err_msg_lines = [
                "SecureBootEnrollment: 'secure_boot_manager' must not be None.",
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiSecureBootError(err_msg) from None

        self._manager: SecureBootManager = secure_boot_manager
        self._aspects: UefiCmdAspects = aspects
        return

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------

    def enroll(self, plan: EnrollmentPlan,
                     dry_run: bool = False,
                     ack: Optional[Dict[str, str]] = None,
                     aspects: Optional[UefiCmdAspects] = None) -> EnrollmentResult:
        """
            Drive the enrollment.

            :param plan: The :class:`EnrollmentPlan` describing which
                         databases to enroll.
            :param dry_run: When ``True`` the workflow runs every safety
                            gate and produces a plan but does NOT write
                            any variable.  When ``False`` the caller must
                            supply a valid ``ack`` dictionary.
            :param ack: Operator acknowledgement dictionary (``operator``,
                        ``ticket`` non-empty strings).  Ignored when
                        ``dry_run=True``.
            :param aspects: Optional per-call :class:`UefiCmdAspects`;
                            falls back to the instance default.

            :returns: A populated :class:`EnrollmentResult`.
            :raises: UefiSecureBootError
        """
        effective = resolve_aspects(passed=aspects, instance_default=self._aspects)

        # --- Gate 0: plan shape ----------------------------------------
        if not isinstance(plan, EnrollmentPlan):
            err_msg_lines = [
                "SecureBootEnrollment.enroll: 'plan' must be an EnrollmentPlan.",
                "GOT: value={!r} type={}".format(plan, type(plan).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiSecureBootError(err_msg) from None

        if _plan_is_empty(plan=plan):
            err_msg_lines = [
                "SecureBootEnrollment.enroll: 'plan' carries no databases to enroll.",
                "HINT: populate at least one of pk / kek / db / dbx databases.",
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiSecureBootError(err_msg) from None

        if not isinstance(dry_run, bool):
            err_msg_lines = [
                "SecureBootEnrollment.enroll: 'dry_run' must be a bool.",
                "GOT: value={!r} type={}".format(dry_run, type(dry_run).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiSecureBootError(err_msg) from None

        # --- Gate 1: must_connect invariant ----------------------------
        must_connect_value = getattr(effective, "must_connect", False)
        if must_connect_value is not True:
            err_msg_lines = [
                "SecureBootEnrollment.enroll: destructive enrollment requires aspects.must_connect=True.",
                "OBSERVED: must_connect={!r}".format(must_connect_value),
                "HINT: partial enrollment is not safe under silent retry; build aspects with must_connect=True.",
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiSecureBootError(err_msg) from None

        # --- Gate 2: ack unless dry_run --------------------------------
        validated_ack: Optional[Dict[str, str]] = None
        if not dry_run:
            validated_ack = _validate_enrollment_ack(ack=ack)

        # --- Dry-run short-circuit -------------------------------------
        if dry_run:
            planned_names: list = []
            for variable_name in _ENROLLMENT_ORDER:
                candidate_database = _database_for(plan=plan, variable_name=variable_name)
                if candidate_database is None:
                    continue
                planned_names.append(variable_name)

            rtnval = EnrollmentResult(
                success=True,
                enrolled=tuple(planned_names),
                dry_run=True,
            )
            return rtnval

        # --- Execute enrollments ---------------------------------------
        enrolled_names: list = []
        for variable_name in _ENROLLMENT_ORDER:
            candidate_database = _database_for(plan=plan, variable_name=variable_name)
            if candidate_database is None:
                continue

            self._write_one(
                variable_name=variable_name,
                database=candidate_database,
                plan=plan,
                ack=validated_ack,
                aspects=effective,
            )

            _verify_write(
                manager=self._manager,
                variable_name=variable_name,
                expected=candidate_database,
                aspects=effective,
            )

            enrolled_names.append(variable_name)

        rtnval = EnrollmentResult(
            success=True,
            enrolled=tuple(enrolled_names),
            dry_run=False,
        )
        return rtnval

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _write_one(self, variable_name: str,
                         database: SignatureDatabase,
                         plan: EnrollmentPlan,
                         ack: Optional[Dict[str, str]],
                         aspects: UefiCmdAspects) -> None:
        """
            Dispatch a single authenticated enrollment write to the
            :class:`SecureBootManager`.

            When the target is already in User Mode (which is the state
            any subsequent enrollment attempt would encounter after the
            first successful PK enrollment), :meth:`SecureBootManager.enroll_signature_database`
            rejects writes unless ``force=True`` is passed alongside the
            ack dictionary.  The workflow forwards the ack for that case
            unconditionally: writes are safe to replay with the operator
            authorization in hand.

            :param variable_name: The Secure Boot variable name.
            :param database: The :class:`SignatureDatabase` to write.
            :param plan: The enrollment plan (used for signer credentials
                         and timestamp).
            :param ack: Operator acknowledgement dictionary.  May be
                        ``None`` only on dry-run paths (the caller
                        short-circuits before reaching this method in
                        that case).
            :param aspects: The effective aspects for the call.

            :raises: UefiSecureBootError
        """
        force_flag = True

        try:
            self._manager.enroll_signature_database(
                name=variable_name,
                database=database,
                signer_key=plan.signer_key,
                signer_cert=plan.signer_cert,
                signing_time=plan.signing_time,
                force=force_flag,
                ack=ack,
                aspects=aspects,
            )
        except UefiSecureBootError:
            raise
        except BaseException as werr:
            err_msg_lines = [
                "SecureBootEnrollment: enroll_signature_database raised.",
                "VARIABLE: {}".format(variable_name),
                "EXCEPTION: {!r}".format(werr),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiSecureBootError(err_msg) from werr

        return
