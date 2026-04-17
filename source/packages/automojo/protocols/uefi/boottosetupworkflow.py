"""
    .. module:: boottosetupworkflow
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Module containing the :class:`BootToSetupWorkflow` which
               drives a UEFI target directly into its firmware Setup UI by
               setting the ``EFI_OS_INDICATIONS_BOOT_TO_FW_UI`` bit in the
               ``OsIndications`` EFI global variable (UEFI §8.2), issuing a
               reset (UEFI §8.5.1), and — when the caller opts in —
               returning a :class:`HiiFormNavigator` wired to the serial
               transport so a test can programmatically interact with the
               Setup screens.

               The workflow verifies firmware support via
               ``OsIndicationsSupported`` before writing any bits, avoiding
               the silent no-op where the firmware ignores
               ``BOOT_TO_FW_UI``.  It also preserves other ``OsIndications``
               bits (OS recovery requests, capsule delivery) so co-existing
               OS features are not disrupted.  The
               :meth:`BootToSetupWorkflow.restore_indications` companion
               method writes the prior value back atomically on cleanup.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Automation Mojo LLC"
__credits__ = []


import os

from dataclasses import dataclass
from typing import Any, Optional

from automojo.protocols.uefi.uefiaspects import (
    DEFAULT_CMD_ASPECTS,
    UefiCmdAspects,
    resolve_aspects,
)
from automojo.protocols.uefi.uefiexceptions import UefiError
from automojo.protocols.uefi.uefiguids import EFI_GLOBAL_VARIABLE_GUID
from automojo.protocols.uefi.uefihii import HiiFormNavigator
from automojo.protocols.uefi.uefivariables import (
    UefiVariable,
    UefiVariableAttributes,
    VariableStore,
)


__all__ = [
    "BootToSetupResult",
    "BootToSetupWorkflow",
    "EFI_OS_INDICATIONS_BOOT_TO_FW_UI",
    "EFI_OS_INDICATIONS_CAPSULE_RESULT_VAR_SUPPORTED",
    "EFI_OS_INDICATIONS_FILE_CAPSULE_DELIVERY_SUPPORTED",
    "EFI_OS_INDICATIONS_FMP_CAPSULE_SUPPORTED",
    "EFI_OS_INDICATIONS_JSON_CONFIG_DATA_REFRESH",
    "EFI_OS_INDICATIONS_START_OS_RECOVERY",
    "EFI_OS_INDICATIONS_START_PLATFORM_RECOVERY",
    "EFI_OS_INDICATIONS_TIMESTAMP_REVOCATION",
    "OS_INDICATIONS_VARIABLE_NAME",
    "OS_INDICATIONS_SUPPORTED_VARIABLE_NAME",
]


# ---------------------------------------------------------------------------
# OsIndications bit flags per UEFI Specification §8.2
# ---------------------------------------------------------------------------

EFI_OS_INDICATIONS_BOOT_TO_FW_UI: int = 0x0000000000000001
EFI_OS_INDICATIONS_TIMESTAMP_REVOCATION: int = 0x0000000000000002
EFI_OS_INDICATIONS_FILE_CAPSULE_DELIVERY_SUPPORTED: int = 0x0000000000000004
EFI_OS_INDICATIONS_FMP_CAPSULE_SUPPORTED: int = 0x0000000000000008
EFI_OS_INDICATIONS_CAPSULE_RESULT_VAR_SUPPORTED: int = 0x0000000000000010
EFI_OS_INDICATIONS_START_OS_RECOVERY: int = 0x0000000000000020
EFI_OS_INDICATIONS_START_PLATFORM_RECOVERY: int = 0x0000000000000040
EFI_OS_INDICATIONS_JSON_CONFIG_DATA_REFRESH: int = 0x0000000000000080


# ---------------------------------------------------------------------------
# Well-known variable names
# ---------------------------------------------------------------------------

OS_INDICATIONS_VARIABLE_NAME: str = "OsIndications"
OS_INDICATIONS_SUPPORTED_VARIABLE_NAME: str = "OsIndicationsSupported"


# ---------------------------------------------------------------------------
# Attribute bitmask used for the OsIndications write
# ---------------------------------------------------------------------------

_OS_INDICATIONS_ATTRS: UefiVariableAttributes = (
    UefiVariableAttributes.NON_VOLATILE
    | UefiVariableAttributes.BOOTSERVICE_ACCESS
    | UefiVariableAttributes.RUNTIME_ACCESS
)


# ---------------------------------------------------------------------------
# BootToSetupResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BootToSetupResult:
    """
        Summary record returned by :meth:`BootToSetupWorkflow.enter_setup`.

        :ivar success: ``True`` when the workflow reached the reset step
                       without raising.
        :ivar prior_os_indications: The value of ``OsIndications`` read
                                    before the BOOT_TO_FW_UI bit was set;
                                    the caller is expected to pass this
                                    value back to
                                    :meth:`BootToSetupWorkflow.restore_indications`.
        :ivar hii_navigator: Optional :class:`HiiFormNavigator` when the
                             caller requested ``expose_navigator=True``
                             and the agent's transport supports screen
                             scraping; otherwise ``None``.
    """
    success: bool
    prior_os_indications: int
    hii_navigator: Optional[HiiFormNavigator] = None


# ---------------------------------------------------------------------------
# BootToSetupWorkflow
# ---------------------------------------------------------------------------


class BootToSetupWorkflow:
    """
        Reboot directly into the firmware Setup UI and optionally return a
        :class:`HiiFormNavigator` wired to the agent's serial transport.

        The workflow composes :class:`VariableStore` for
        ``OsIndicationsSupported`` / ``OsIndications`` CRUD, a caller-owned
        reboot workflow for the actual reset, and optionally
        :class:`HiiFormNavigator` for post-reset screen scraping.  It does
        not itself manage the HII session lifecycle beyond construction;
        callers drive the navigator and pair the :meth:`enter_setup` call
        with a matching :meth:`restore_indications` when leaving Setup.

        :param agent: The :class:`UefiAgent` whose transport the workflow
                      binds the navigator to.  The agent must expose a
                      ``transport_kind`` attribute.
        :param variable_store: The :class:`VariableStore` used for both
                               ``OsIndicationsSupported`` reads and the
                               ``OsIndications`` read-modify-write.
        :param reboot_workflow: An object exposing a ``reboot`` method with
                                the signature ``reboot(wait_for_shell: bool,
                                aspects: UefiCmdAspects) -> None`` or
                                compatible.  Typically a
                                ``RebootWorkflow`` instance from
                                ``6-reboot-and-verify``.
        :param aspects: Default :class:`UefiCmdAspects` used when a per-call
                        override is not supplied.
    """

    def __init__(self, agent: Any,
                       variable_store: VariableStore,
                       reboot_workflow: Any,
                       aspects: UefiCmdAspects = DEFAULT_CMD_ASPECTS) -> None:
        """
            Initialize the workflow.

            :param agent: The :class:`UefiAgent`.
            :param variable_store: The :class:`VariableStore` facade.
            :param reboot_workflow: A caller-owned reboot workflow object.
            :param aspects: Instance-default aspects.
        """
        self._agent: Any = agent
        self._variable_store: VariableStore = variable_store
        self._reboot_workflow: Any = reboot_workflow
        self._aspects: UefiCmdAspects = aspects
        return

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def enter_setup(self, expose_navigator: bool = False,
                          aspects: Optional[UefiCmdAspects] = None) -> BootToSetupResult:
        """
            Execute the enter-Setup sequence.

            The workflow:

            1. Reads ``OsIndicationsSupported`` (EFI_GLOBAL_VARIABLE_GUID)
               and verifies that :data:`EFI_OS_INDICATIONS_BOOT_TO_FW_UI`
               is set.  If not, raises :class:`UefiError`.
            2. Reads the current ``OsIndications`` value (treated as ``0``
               when the variable does not exist); stores it as
               ``prior_os_indications`` on the returned result.
            3. Writes ``OsIndications = prior | BOOT_TO_FW_UI`` with
               attributes ``NV | BS | RT``.
            4. Invokes ``reboot_workflow.reboot(wait_for_shell=False,
               aspects=effective)`` because the Shell will not be reachable
               inside the Setup UI.
            5. When ``expose_navigator=True``, constructs a
               :class:`HiiFormNavigator` bound to the agent's serial
               transport.  Raises :class:`UefiError` if
               ``agent.transport_kind`` is not ``'serial'``.

            :param expose_navigator: When ``True``, include a
                                     :class:`HiiFormNavigator` in the
                                     returned result.
            :param aspects: Optional per-call aspects override.

            :returns: A :class:`BootToSetupResult`.
            :raises: UefiError
        """
        effective_aspects = resolve_aspects(
            passed=aspects, instance_default=self._aspects,
        )

        supported_mask = self._read_os_indications_supported(
            aspects=effective_aspects,
        )
        self._require_boot_to_fw_ui_supported(supported_mask=supported_mask)

        prior_value = self._read_os_indications(aspects=effective_aspects)

        target_value = prior_value | EFI_OS_INDICATIONS_BOOT_TO_FW_UI

        self._write_os_indications(
            value=target_value, aspects=effective_aspects,
        )

        self._issue_reboot(aspects=effective_aspects)

        navigator: Optional[HiiFormNavigator] = None
        if expose_navigator is True:
            navigator = self._build_navigator(aspects=effective_aspects)

        rtnval = BootToSetupResult(
            success=True,
            prior_os_indications=prior_value,
            hii_navigator=navigator,
        )
        return rtnval

    def restore_indications(self, prior_os_indications: int,
                                  aspects: Optional[UefiCmdAspects] = None) -> None:
        """
            Restore ``OsIndications`` to its prior value atomically.

            This is the companion operation to :meth:`enter_setup`.
            Callers invoke it after they finish driving the Setup UI (and
            after any intervening reboot back to the shell) to make sure
            subsequent boots behave normally.

            :param prior_os_indications: The value returned in
                                         :attr:`BootToSetupResult.prior_os_indications`.
            :param aspects: Optional per-call aspects override.

            :raises: UefiError
        """
        effective_aspects = resolve_aspects(
            passed=aspects, instance_default=self._aspects,
        )

        if not isinstance(prior_os_indications, int) or isinstance(prior_os_indications, bool):
            err_msg_lines = [
                "BootToSetupWorkflow.restore_indications: 'prior_os_indications' must be an int.",
                "GOT: value={!r} type={}".format(
                    prior_os_indications, type(prior_os_indications).__name__,
                ),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg)

        if prior_os_indications < 0:
            err_msg_lines = [
                "BootToSetupWorkflow.restore_indications: 'prior_os_indications' must be >= 0.",
                "GOT: value={}".format(prior_os_indications),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg)

        self._write_os_indications(
            value=prior_os_indications, aspects=effective_aspects,
        )
        return

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _read_os_indications_supported(self, aspects: UefiCmdAspects) -> int:
        """
            Read ``OsIndicationsSupported`` and return its little-endian
            unsigned integer form.

            :param aspects: Effective aspects.

            :returns: The bitmask value.
            :raises: UefiError
        """
        variable = self._variable_store.get_variable(
            name=OS_INDICATIONS_SUPPORTED_VARIABLE_NAME,
            vendor_guid=EFI_GLOBAL_VARIABLE_GUID,
            aspects=aspects,
        )

        rtnval = self._decode_uint64_le(data=variable.data)
        return rtnval

    def _read_os_indications(self, aspects: UefiCmdAspects) -> int:
        """
            Read ``OsIndications``.  When the variable does not exist, the
            UEFI spec allows the caller to treat the prior value as ``0``;
            this helper catches the ``UefiError`` raised by the backend and
            returns ``0`` in that case.

            :param aspects: Effective aspects.

            :returns: The bitmask value, or ``0`` when absent.
        """
        try:
            variable = self._variable_store.get_variable(
                name=OS_INDICATIONS_VARIABLE_NAME,
                vendor_guid=EFI_GLOBAL_VARIABLE_GUID,
                aspects=aspects,
            )
        except UefiError:
            rtnval = 0
            return rtnval

        rtnval = self._decode_uint64_le(data=variable.data)
        return rtnval

    def _write_os_indications(self, value: int,
                                    aspects: UefiCmdAspects) -> None:
        """
            Write ``OsIndications`` with the ``NV | BS | RT`` attribute
            triple and an 8-byte little-endian payload.

            :param value: The bitmask value to write.
            :param aspects: Effective aspects.

            :raises: UefiError
        """
        payload = int(value).to_bytes(
            length=8, byteorder="little", signed=False,
        )
        variable = UefiVariable(
            name=OS_INDICATIONS_VARIABLE_NAME,
            vendor_guid=EFI_GLOBAL_VARIABLE_GUID,
            attributes=_OS_INDICATIONS_ATTRS,
            data=payload,
        )
        self._variable_store.set_variable(
            variable=variable, aspects=aspects,
        )
        return

    def _issue_reboot(self, aspects: UefiCmdAspects) -> None:
        """
            Call ``reboot_workflow.reboot(wait_for_shell=False, aspects)``.
            The ``wait_for_shell=False`` argument is critical: the Shell
            will not be reachable inside the Setup UI, so the reboot
            primitive must not poll for a Shell prompt.

            :param aspects: Effective aspects.

            :raises: UefiError
        """
        reboot_callable = getattr(self._reboot_workflow, "reboot", None)
        if not callable(reboot_callable):
            err_msg_lines = [
                "BootToSetupWorkflow: 'reboot_workflow' does not expose a 'reboot' callable.",
                "GOT: type={}".format(type(self._reboot_workflow).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg)

        reboot_callable(wait_for_shell=False, aspects=aspects)
        return

    def _build_navigator(self, aspects: UefiCmdAspects) -> HiiFormNavigator:
        """
            Construct a :class:`HiiFormNavigator` bound to the agent's
            serial transport.  Only the ``'serial'`` transport exposes the
            read/send primitives the navigator expects; other transport
            kinds raise :class:`UefiError` to surface the mismatch up
            front.

            :param aspects: Effective aspects.

            :returns: A constructed navigator.
            :raises: UefiError
        """
        transport_kind = getattr(self._agent, "transport_kind", None)
        if transport_kind != "serial":
            err_msg_lines = [
                "BootToSetupWorkflow: expose_navigator=True requires agent.transport_kind == 'serial'.",
                "OBSERVED: transport_kind={!r}".format(transport_kind),
                "HINT: drive Setup via a serial-attached agent or set expose_navigator=False.",
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg)

        send_callable = getattr(self._agent, "send", None)
        read_screen_callable = getattr(self._agent, "read_screen", None)
        shape = getattr(self._agent, "screen_shape", None)

        missing: list = []
        if not callable(send_callable):
            missing.append("send")
        if not callable(read_screen_callable):
            missing.append("read_screen")
        if shape is None:
            missing.append("screen_shape")

        if len(missing) > 0:
            err_msg_lines = [
                "BootToSetupWorkflow: agent is missing attributes required for HiiFormNavigator.",
                "MISSING: {}".format(", ".join(missing)),
                "GOT: type={}".format(type(self._agent).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg)

        rtnval = HiiFormNavigator(
            send=send_callable,
            read_screen=read_screen_callable,
            shape=shape,
            aspects=aspects,
        )
        return rtnval

    def _require_boot_to_fw_ui_supported(self, supported_mask: int) -> None:
        """
            Validate that ``EFI_OS_INDICATIONS_BOOT_TO_FW_UI`` is set in
            ``supported_mask``.  Raises :class:`UefiError` otherwise.

            :param supported_mask: The value read from
                                   ``OsIndicationsSupported``.

            :raises: UefiError
        """
        if (supported_mask & EFI_OS_INDICATIONS_BOOT_TO_FW_UI) == 0:
            err_msg_lines = [
                "BootToSetupWorkflow: firmware does not advertise BOOT_TO_FW_UI support.",
                "OsIndicationsSupported: 0x{:016X}".format(supported_mask),
                "REQUIRED_BIT: EFI_OS_INDICATIONS_BOOT_TO_FW_UI (0x{:016X})".format(
                    EFI_OS_INDICATIONS_BOOT_TO_FW_UI,
                ),
                "HINT: this firmware cannot honor a boot-to-Setup request; skip the test or run on a capable target.",
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg)

        return

    def _decode_uint64_le(self, data: bytes) -> int:
        """
            Decode ``data`` as a little-endian unsigned integer.  The
            backend may return fewer than 8 bytes (some firmware trims
            leading zero bytes in ``OsIndications``); the helper
            zero-pads to 8 bytes before decoding.

            :param data: The raw payload bytes.

            :returns: The decoded integer.
            :raises: UefiError
        """
        if data is None:
            err_msg_lines = [
                "BootToSetupWorkflow: variable payload was None.",
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg)

        if len(data) > 8:
            err_msg_lines = [
                "BootToSetupWorkflow: OsIndications payload is longer than the 8-byte UINT64 layout.",
                "OBSERVED_LENGTH: {}".format(len(data)),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg)

        padded = bytes(data) + b"\x00" * (8 - len(data))
        rtnval = int.from_bytes(padded, byteorder="little", signed=False)
        return rtnval
