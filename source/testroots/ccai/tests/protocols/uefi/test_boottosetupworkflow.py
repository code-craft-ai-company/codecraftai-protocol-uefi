"""
    .. module:: test_boottosetupworkflow
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Unit tests for the
               :mod:`ccai.protocols.uefi.boottosetupworkflow` module.
               Exercises the :class:`BootToSetupWorkflow` capability probe,
               the atomic OsIndications mutation (bit ORed into the prior
               value while preserving other bits), the reboot invocation
               with ``wait_for_shell=False``, and the
               :meth:`BootToSetupWorkflow.restore_indications` round trip
               through a fake :class:`VariableStore` and a fake reboot
               workflow.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Code Craft AI LLC"
__credits__ = []


from ccai import testbase

import uuid

from typing import Any, Dict, List, Optional, Tuple, Union

from ccai.protocols.uefi.uefiaspects import (
    DEFAULT_CMD_ASPECTS,
    UefiCmdAspects,
)
from ccai.protocols.uefi.uefiexceptions import (
    UefiError,
    UefiVariableError,
)
from ccai.protocols.uefi.uefiguids import EFI_GLOBAL_VARIABLE_GUID
from ccai.protocols.uefi.uefivariables import (
    EFI_NOT_FOUND,
    UefiVariable,
    UefiVariableAttributes,
)

from ccai.protocols.uefi.boottosetupworkflow import (
    EFI_OS_INDICATIONS_BOOT_TO_FW_UI,
    EFI_OS_INDICATIONS_START_OS_RECOVERY,
    OS_INDICATIONS_SUPPORTED_VARIABLE_NAME,
    OS_INDICATIONS_VARIABLE_NAME,
    BootToSetupResult,
    BootToSetupWorkflow,
)


# ---------------------------------------------------------------------------
# Fake VariableStore
# ---------------------------------------------------------------------------


class _FakeVariableStore:
    """
        Minimal stand-in for :class:`VariableStore`.  Stores variables in a
        dict keyed by ``(name, vendor_guid)`` and records every call for
        later assertion.
    """

    def __init__(self) -> None:
        """
            Initialize an empty store.
        """
        self._store: Dict[Tuple[str, uuid.UUID], UefiVariable] = {}
        self.get_calls: List[Tuple[str, uuid.UUID]] = []
        self.set_calls: List[UefiVariable] = []
        self.force_set_error: Optional[Exception] = None
        return

    # ------------------------------------------------------------------
    # Seeding helpers used by tests
    # ------------------------------------------------------------------

    def seed(self, name: str,
                   vendor_guid: uuid.UUID,
                   attributes: UefiVariableAttributes,
                   data: bytes) -> None:
        """
            Insert a variable directly into the store.
        """
        key = (name, vendor_guid)
        self._store[key] = UefiVariable(
            name=name,
            vendor_guid=vendor_guid,
            attributes=attributes,
            data=data,
        )
        return

    # ------------------------------------------------------------------
    # VariableStore-shaped API
    # ------------------------------------------------------------------

    def get_variable(self, name: str,
                           vendor_guid: Union[str, uuid.UUID],
                           aspects: Optional[UefiCmdAspects] = None) -> UefiVariable:
        """
            Retrieve a seeded variable or raise :class:`UefiVariableError`
            when the variable is not seeded.
        """
        if isinstance(vendor_guid, str):
            resolved_guid = uuid.UUID(vendor_guid)
        else:
            resolved_guid = vendor_guid

        self.get_calls.append((name, resolved_guid))

        key = (name, resolved_guid)
        if key not in self._store:
            raise UefiVariableError(
                "fake store: variable not found",
                name=name,
                vendor_guid=resolved_guid,
                efi_status=EFI_NOT_FOUND,
            )

        rtnval = self._store[key]
        return rtnval

    def set_variable(self, variable: UefiVariable,
                           aspects: Optional[UefiCmdAspects] = None,
                           signer_key: Optional[Any] = None,
                           signer_cert: Optional[Any] = None,
                           signing_time: Optional[Any] = None) -> None:
        """
            Insert ``variable`` into the store (overwriting any existing
            entry).  A test may preset ``force_set_error`` to force a
            failure.
        """
        if self.force_set_error is not None:
            raise self.force_set_error

        self.set_calls.append(variable)
        key = (variable.name, variable.vendor_guid)
        self._store[key] = variable
        return

    def get_stored(self, name: str,
                         vendor_guid: uuid.UUID) -> Optional[UefiVariable]:
        """
            Return a stored variable without recording a ``get_variable``
            call — used by tests to inspect the post-write state.
        """
        key = (name, vendor_guid)
        rtnval = self._store.get(key)
        return rtnval


# ---------------------------------------------------------------------------
# Fake reboot workflow
# ---------------------------------------------------------------------------


class _FakeRebootWorkflow:
    """
        Records :meth:`reboot` invocations.
    """

    def __init__(self) -> None:
        """
            Initialize the recorder.
        """
        self.reboot_calls: List[Dict[str, Any]] = []
        return

    def reboot(self, wait_for_shell: bool,
                     aspects: UefiCmdAspects) -> None:
        """
            Record the call.  No side effects.
        """
        self.reboot_calls.append({
            "wait_for_shell": wait_for_shell,
            "aspects": aspects,
        })
        return


# ---------------------------------------------------------------------------
# Fake UefiAgent
# ---------------------------------------------------------------------------


class _FakeAgent:
    """
        Minimal :class:`UefiAgent` stand-in exposing only the attributes
        the workflow reads: ``transport_kind``.
    """

    def __init__(self, transport_kind: str = "serial") -> None:
        """
            Initialize the fake.
        """
        self.transport_kind: str = transport_kind
        return


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _encode_u64_le(value: int) -> bytes:
    """
        Encode an integer as an 8-byte little-endian bytes buffer.
    """
    rtnval = int(value).to_bytes(length=8, byteorder="little", signed=False)
    return rtnval


def _make_workflow() -> Tuple[BootToSetupWorkflow, _FakeAgent, _FakeVariableStore, _FakeRebootWorkflow]:
    """
        Construct a :class:`BootToSetupWorkflow` wired to freshly-built
        fakes and return the workflow along with each fake so tests can
        assert against them.
    """
    agent = _FakeAgent(transport_kind="serial")
    variable_store = _FakeVariableStore()
    reboot_workflow = _FakeRebootWorkflow()
    workflow = BootToSetupWorkflow(
        agent=agent,
        variable_store=variable_store,
        reboot_workflow=reboot_workflow,
        aspects=DEFAULT_CMD_ASPECTS,
    )

    rtnval = (workflow, agent, variable_store, reboot_workflow)
    return rtnval


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


# ----------------------------------------------------------------------
# TestEnterSetupCapabilityProbe
# ----------------------------------------------------------------------

"""
    Tests covering the capability-probe step of :meth:`enter_setup`.
"""

def test_reads_os_indications_supported_first() -> None:
    """The workflow reads ``OsIndicationsSupported`` before writing."""
    workflow, _, variable_store, _ = _make_workflow()

    variable_store.seed(
        name=OS_INDICATIONS_SUPPORTED_VARIABLE_NAME,
        vendor_guid=EFI_GLOBAL_VARIABLE_GUID,
        attributes=UefiVariableAttributes.BOOTSERVICE_ACCESS
        | UefiVariableAttributes.RUNTIME_ACCESS,
        data=_encode_u64_le(EFI_OS_INDICATIONS_BOOT_TO_FW_UI),
    )

    workflow.enter_setup()

    # First get_call must be OsIndicationsSupported.
    testbase.assert_greater(len(variable_store.get_calls), 0)
    first_name, first_guid = variable_store.get_calls[0]
    testbase.assert_equal(first_name, OS_INDICATIONS_SUPPORTED_VARIABLE_NAME)
    testbase.assert_equal(first_guid, EFI_GLOBAL_VARIABLE_GUID)
    return

def test_missing_boot_to_fw_ui_raises() -> None:
    """A supported mask without BOOT_TO_FW_UI raises :class:`UefiError`."""
    workflow, _, variable_store, reboot_workflow = _make_workflow()

    # Seed supported WITHOUT the BOOT_TO_FW_UI bit.
    variable_store.seed(
        name=OS_INDICATIONS_SUPPORTED_VARIABLE_NAME,
        vendor_guid=EFI_GLOBAL_VARIABLE_GUID,
        attributes=UefiVariableAttributes.BOOTSERVICE_ACCESS
        | UefiVariableAttributes.RUNTIME_ACCESS,
        data=_encode_u64_le(EFI_OS_INDICATIONS_START_OS_RECOVERY),
    )

    with testbase.raises(UefiError):
        workflow.enter_setup()

    # Workflow must not have attempted to write or reboot.
    testbase.assert_equal(len(variable_store.set_calls), 0)
    testbase.assert_equal(len(reboot_workflow.reboot_calls), 0)
    return


# ----------------------------------------------------------------------
# TestEnterSetupMutation
# ----------------------------------------------------------------------

"""
    Tests covering the read-modify-write of ``OsIndications``.
"""

def test_bit_is_ored_with_prior_value() -> None:
    """
        When ``OsIndications`` already carries unrelated bits, the
        workflow preserves them and ORs in BOOT_TO_FW_UI.
    """
    workflow, _, variable_store, _ = _make_workflow()

    variable_store.seed(
        name=OS_INDICATIONS_SUPPORTED_VARIABLE_NAME,
        vendor_guid=EFI_GLOBAL_VARIABLE_GUID,
        attributes=UefiVariableAttributes.BOOTSERVICE_ACCESS
        | UefiVariableAttributes.RUNTIME_ACCESS,
        data=_encode_u64_le(
            EFI_OS_INDICATIONS_BOOT_TO_FW_UI
            | EFI_OS_INDICATIONS_START_OS_RECOVERY,
        ),
    )

    # OsIndications carries a non-BOOT_TO_FW_UI bit.
    prior = EFI_OS_INDICATIONS_START_OS_RECOVERY
    variable_store.seed(
        name=OS_INDICATIONS_VARIABLE_NAME,
        vendor_guid=EFI_GLOBAL_VARIABLE_GUID,
        attributes=UefiVariableAttributes.NON_VOLATILE
        | UefiVariableAttributes.BOOTSERVICE_ACCESS
        | UefiVariableAttributes.RUNTIME_ACCESS,
        data=_encode_u64_le(prior),
    )

    result = workflow.enter_setup()

    testbase.assert_true(isinstance(result, BootToSetupResult))
    testbase.assert_true(result.success)
    testbase.assert_equal(result.prior_os_indications, prior)
    testbase.assert_true(result.hii_navigator is None)

    # Exactly one write of OsIndications.
    testbase.assert_equal(len(variable_store.set_calls), 1)
    written = variable_store.set_calls[0]
    testbase.assert_equal(written.name, OS_INDICATIONS_VARIABLE_NAME)
    testbase.assert_equal(written.vendor_guid, EFI_GLOBAL_VARIABLE_GUID)

    expected_attrs = (
        UefiVariableAttributes.NON_VOLATILE
        | UefiVariableAttributes.BOOTSERVICE_ACCESS
        | UefiVariableAttributes.RUNTIME_ACCESS
    )
    testbase.assert_equal(written.attributes, expected_attrs)

    written_value = int.from_bytes(
        written.data, byteorder="little", signed=False,
    )
    expected_value = prior | EFI_OS_INDICATIONS_BOOT_TO_FW_UI
    testbase.assert_equal(written_value, expected_value)
    return

def test_missing_os_indications_treated_as_zero() -> None:
    """
        When ``OsIndications`` does not exist the workflow treats the
        prior value as ``0`` and writes ``BOOT_TO_FW_UI`` verbatim.
    """
    workflow, _, variable_store, _ = _make_workflow()

    variable_store.seed(
        name=OS_INDICATIONS_SUPPORTED_VARIABLE_NAME,
        vendor_guid=EFI_GLOBAL_VARIABLE_GUID,
        attributes=UefiVariableAttributes.BOOTSERVICE_ACCESS,
        data=_encode_u64_le(EFI_OS_INDICATIONS_BOOT_TO_FW_UI),
    )
    # OsIndications intentionally NOT seeded.

    result = workflow.enter_setup()

    testbase.assert_equal(result.prior_os_indications, 0)

    testbase.assert_equal(len(variable_store.set_calls), 1)
    written_value = int.from_bytes(
        variable_store.set_calls[0].data,
        byteorder="little",
        signed=False,
    )
    testbase.assert_equal(written_value, EFI_OS_INDICATIONS_BOOT_TO_FW_UI)
    return


# ----------------------------------------------------------------------
# TestEnterSetupReboot
# ----------------------------------------------------------------------

"""
    Tests covering the reboot invocation.
"""

def test_reboot_is_called_with_wait_for_shell_false() -> None:
    """The workflow calls reboot exactly once with ``wait_for_shell=False``."""
    workflow, _, variable_store, reboot_workflow = _make_workflow()

    variable_store.seed(
        name=OS_INDICATIONS_SUPPORTED_VARIABLE_NAME,
        vendor_guid=EFI_GLOBAL_VARIABLE_GUID,
        attributes=UefiVariableAttributes.BOOTSERVICE_ACCESS,
        data=_encode_u64_le(EFI_OS_INDICATIONS_BOOT_TO_FW_UI),
    )

    workflow.enter_setup()

    testbase.assert_equal(len(reboot_workflow.reboot_calls), 1)
    call = reboot_workflow.reboot_calls[0]
    testbase.assert_false(call["wait_for_shell"])
    # The aspects forwarded to reboot should be the effective aspects
    # (instance-default in this test since no per-call override was
    # supplied).
    testbase.assert_true(call["aspects"] is DEFAULT_CMD_ASPECTS)
    return

def test_reboot_workflow_without_reboot_method_raises() -> None:
    """A reboot workflow that lacks ``reboot()`` raises :class:`UefiError`."""

    class _Broken:
        pass

    agent = _FakeAgent(transport_kind="serial")
    variable_store = _FakeVariableStore()
    variable_store.seed(
        name=OS_INDICATIONS_SUPPORTED_VARIABLE_NAME,
        vendor_guid=EFI_GLOBAL_VARIABLE_GUID,
        attributes=UefiVariableAttributes.BOOTSERVICE_ACCESS,
        data=_encode_u64_le(EFI_OS_INDICATIONS_BOOT_TO_FW_UI),
    )
    workflow = BootToSetupWorkflow(
        agent=agent,
        variable_store=variable_store,
        reboot_workflow=_Broken(),
    )

    with testbase.raises(UefiError):
        workflow.enter_setup()
    return


# ----------------------------------------------------------------------
# TestRestoreIndications
# ----------------------------------------------------------------------

"""
    Tests covering :meth:`BootToSetupWorkflow.restore_indications`.
"""

def test_writes_prior_value() -> None:
    """The prior value is written back verbatim on restore."""
    workflow, _, variable_store, _ = _make_workflow()

    prior = EFI_OS_INDICATIONS_START_OS_RECOVERY
    workflow.restore_indications(prior_os_indications=prior)

    testbase.assert_equal(len(variable_store.set_calls), 1)
    written = variable_store.set_calls[0]
    testbase.assert_equal(written.name, OS_INDICATIONS_VARIABLE_NAME)
    testbase.assert_equal(written.vendor_guid, EFI_GLOBAL_VARIABLE_GUID)

    written_value = int.from_bytes(
        written.data, byteorder="little", signed=False,
    )
    testbase.assert_equal(written_value, prior)
    return

def test_zero_prior_is_written() -> None:
    """``0`` is a valid prior value and is written back verbatim."""
    workflow, _, variable_store, _ = _make_workflow()
    workflow.restore_indications(prior_os_indications=0)

    testbase.assert_equal(len(variable_store.set_calls), 1)
    written_value = int.from_bytes(
        variable_store.set_calls[0].data,
        byteorder="little",
        signed=False,
    )
    testbase.assert_equal(written_value, 0)
    return

def test_negative_prior_rejected() -> None:
    """A negative prior value raises :class:`UefiError`."""
    workflow, _, _, _ = _make_workflow()
    with testbase.raises(UefiError):
        workflow.restore_indications(prior_os_indications=-1)
    return

def test_non_int_prior_rejected() -> None:
    """A non-int prior value raises :class:`UefiError`."""
    workflow, _, _, _ = _make_workflow()
    with testbase.raises(UefiError):
        workflow.restore_indications(prior_os_indications="zero")  # type: ignore[arg-type]
    return


# ----------------------------------------------------------------------
# TestBootToSetupResultShape
# ----------------------------------------------------------------------

"""
    Tests covering the :class:`BootToSetupResult` fields returned by
    :meth:`enter_setup` when ``expose_navigator`` is not requested.
"""

def test_default_result_has_no_navigator() -> None:
    """Omitting expose_navigator yields a ``None`` navigator."""
    workflow, _, variable_store, _ = _make_workflow()
    variable_store.seed(
        name=OS_INDICATIONS_SUPPORTED_VARIABLE_NAME,
        vendor_guid=EFI_GLOBAL_VARIABLE_GUID,
        attributes=UefiVariableAttributes.BOOTSERVICE_ACCESS,
        data=_encode_u64_le(EFI_OS_INDICATIONS_BOOT_TO_FW_UI),
    )

    result = workflow.enter_setup()
    testbase.assert_true(result.hii_navigator is None)
    testbase.assert_true(result.success)
    return
