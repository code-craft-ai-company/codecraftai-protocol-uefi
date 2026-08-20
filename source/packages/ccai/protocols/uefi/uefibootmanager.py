"""
    .. module:: uefibootmanager
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Boot-option management facade wrapping the UEFI
               ``Boot####``, ``BootOrder``, ``BootNext``, ``BootCurrent``,
               ``Timeout``, ``Driver####``, ``DriverOrder``,
               ``SysPrep####``, and ``SysPrepOrder`` global-namespace
               variables defined by UEFI specification sections 3, 3.1.3,
               and 3.3.  Provides :class:`BootOption`, a frozen dataclass
               that models ``EFI_LOAD_OPTION`` per section 3.1.3 with
               symmetric :meth:`BootOption.decode` / :meth:`BootOption.encode`
               methods, plus :class:`BootManager`, a typed facade over
               :class:`VariableStore` for the full boot-catalogue surface.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Code Craft AI LLC"
__credits__ = []


import enum
import os
import time
import uuid

from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

from ccai.errors.exceptions import ConfigurationError, SemanticError

from ccai.protocols.uefi.uefiaspects import (
    ActionPattern,
    DEFAULT_CMD_ASPECTS,
    UefiCmdAspects,
    resolve_aspects,
)
from ccai.protocols.uefi.uefidevicepath import (
    DevicePath,
    decode_device_path,
    device_path_to_text,
    encode_device_path,
)
from ccai.protocols.uefi.uefiexceptions import (
    UefiError,
    UefiVariableError,
)
from ccai.protocols.uefi.uefiguids import EFI_GLOBAL_VARIABLE_GUID
from ccai.protocols.uefi.uefihelpers import (
    ucs2_decode,
    ucs2_encode,
)
from ccai.protocols.uefi.uefilogging import (
    UEFI_BOOTMGR_CHANNEL,
    get_uefi_logger,
)
from ccai.protocols.uefi.uefivariables import (
    EFI_NOT_FOUND,
    UefiVariable,
    UefiVariableAttributes,
    VariableStore,
)


__all__ = [
    "BOOT_OPTION_WRITE_ATTRIBUTES",
    "BootManager",
    "BootOption",
    "LoadOptionAttribute",
    "pack_u16_array",
    "unpack_u16_array",
]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# The attribute bitfield used when writing a Boot####/Driver####/SysPrep####
# variable.  Runtime access implies boot-service access per
# :func:`_validate_attributes` in :mod:`uefivariables`.
BOOT_OPTION_WRITE_ATTRIBUTES: UefiVariableAttributes = (
    UefiVariableAttributes.NON_VOLATILE
    | UefiVariableAttributes.BOOTSERVICE_ACCESS
    | UefiVariableAttributes.RUNTIME_ACCESS
)


# Minimum byte length of an ``EFI_LOAD_OPTION`` header (4-byte attributes +
# 2-byte FilePathListLength).
_EFI_LOAD_OPTION_HEADER_LEN: int = 6


# Variable-name prefixes for the four option families this module manages.
_PREFIX_BOOT: str = "Boot"
_PREFIX_DRIVER: str = "Driver"
_PREFIX_SYS_PREP: str = "SysPrep"


# Companion control-variable names.
_NAME_BOOT_ORDER: str = "BootOrder"
_NAME_BOOT_NEXT: str = "BootNext"
_NAME_BOOT_CURRENT: str = "BootCurrent"
_NAME_TIMEOUT: str = "Timeout"
_NAME_DRIVER_ORDER: str = "DriverOrder"
_NAME_SYS_PREP_ORDER: str = "SysPrepOrder"


# ---------------------------------------------------------------------------
# LoadOptionAttribute
# ---------------------------------------------------------------------------

class LoadOptionAttribute(enum.IntFlag):
    """
        Bit-flag enumeration of the ``EFI_LOAD_OPTION`` Attributes field
        per UEFI specification section 3.1.3.

        The ``LOAD_OPTION_CATEGORY`` sub-field occupies bits 8-12 and is
        masked by :data:`LOAD_OPTION_CATEGORY_MASK`; the specification
        currently defines only two category values, ``BOOT`` (``0``) and
        ``APP`` (``0x00000100``).
    """
    LOAD_OPTION_ACTIVE = 0x00000001
    LOAD_OPTION_FORCE_RECONNECT = 0x00000002
    LOAD_OPTION_HIDDEN = 0x00000008
    LOAD_OPTION_CATEGORY_MASK = 0x00001F00
    LOAD_OPTION_CATEGORY_BOOT = 0x00000000
    LOAD_OPTION_CATEGORY_APP = 0x00000100


# ---------------------------------------------------------------------------
# UINT16 array codec (BootOrder / DriverOrder / SysPrepOrder)
# ---------------------------------------------------------------------------

def pack_u16_array(values: Sequence[int]) -> bytes:
    """
        Pack a sequence of UINT16 option IDs little-endian.

        :param values: The option IDs to pack.

        :returns: Concatenated little-endian UINT16 bytes.
        :raises: ConfigurationError
    """
    out = bytearray()
    for index, value in enumerate(values):
        if not isinstance(value, int) or isinstance(value, bool):
            err_msg_lines = [
                "pack_u16_array: values must be ints.",
                "INDEX: {}".format(index),
                "GOT: value={!r} type={}".format(value, type(value).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg) from None

        if value < 0 or value > 0xFFFF:
            err_msg_lines = [
                "pack_u16_array: value out of UINT16 range.",
                "INDEX: {}".format(index),
                "OBSERVED: {}".format(value),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg) from None

        out.extend(value.to_bytes(length=2, byteorder="little", signed=False))

    rtnval = bytes(out)
    return rtnval


def unpack_u16_array(buf: bytes) -> Tuple[int, ...]:
    """
        Unpack a buffer as a tuple of little-endian UINT16 option IDs.

        :param buf: The packed bytes.

        :returns: Tuple of unpacked integers.
        :raises: ConfigurationError
    """
    if not isinstance(buf, (bytes, bytearray, memoryview)):
        err_msg_lines = [
            "unpack_u16_array: 'buf' must be a bytes-like object.",
            "GOT: value={!r} type={}".format(buf, type(buf).__name__),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise ConfigurationError(err_msg) from None

    buffer = bytes(buf)
    buffer_length = len(buffer)
    if (buffer_length % 2) != 0:
        err_msg_lines = [
            "unpack_u16_array: buffer length must be a multiple of 2.",
            "OBSERVED: {}".format(buffer_length),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise ConfigurationError(err_msg) from None

    result: List[int] = []
    for offset in range(0, buffer_length, 2):
        chunk = buffer[offset:offset + 2]
        value = int.from_bytes(chunk, byteorder="little", signed=False)
        result.append(value)

    rtnval = tuple(result)
    return rtnval


# ---------------------------------------------------------------------------
# BootOption
# ---------------------------------------------------------------------------

def _find_ucs2_null_terminator(buf: bytes, start: int) -> int:
    """
        Locate the offset of the two-byte UCS-2 NUL terminator that ends
        the ``Description`` field within an ``EFI_LOAD_OPTION`` buffer.

        :param buf: The full ``EFI_LOAD_OPTION`` buffer.
        :param start: The offset at which the description begins.

        :returns: The byte offset of the NUL terminator (``U+0000``) within
                  ``buf``.
        :raises: UefiVariableError
    """
    offset = start
    buffer_length = len(buf)
    while offset + 1 < buffer_length:
        lo = buf[offset]
        hi = buf[offset + 1]
        if lo == 0 and hi == 0:
            rtnval = offset
            return rtnval
        offset = offset + 2

    err_msg_lines = [
        "EFI_LOAD_OPTION description field is not null-terminated.",
        "DESCRIPTION START: {}".format(start),
        "BUFFER LENGTH: {}".format(buffer_length),
    ]
    err_msg = os.linesep.join(err_msg_lines)
    raise UefiVariableError(err_msg) from None


@dataclass(frozen=True)
class BootOption:
    """
        Frozen dataclass modelling the ``EFI_LOAD_OPTION`` record per UEFI
        specification section 3.1.3.

        Binary layout::

            u32  attributes
            u16  file_path_list_length
            char16 description[] (null-terminated UCS-2)
            EFI_DEVICE_PATH_PROTOCOL file_path_list[]  (file_path_list_length bytes)
            u8   optional_data[]

        The ``file_path`` field stores the decoded :class:`DevicePath` that
        firmware will hand to ``LoadImage``; the raw wire bytes are
        recovered during encode via :func:`encode_device_path`.
        ``optional_data`` is opaque per UEFI section 3.1.3 and is preserved
        byte-for-byte.

        :ivar attributes: The :class:`LoadOptionAttribute` bitfield.
        :ivar description: The UCS-2 display text (BMP characters only).
        :ivar file_path: The parsed :class:`DevicePath`.
        :ivar optional_data: The opaque trailer bytes.
    """
    attributes: LoadOptionAttribute
    description: str
    file_path: DevicePath
    optional_data: bytes = field(default=b"")

    @classmethod
    def decode(cls, data: bytes) -> "BootOption":
        """
            Decode a ``Boot####``, ``Driver####``, or ``SysPrep####``
            variable body into a :class:`BootOption` instance.

            :param data: The raw variable body bytes per UEFI
                         specification section 3.1.3.

            :returns: The constructed :class:`BootOption`.
            :raises: UefiVariableError
        """
        if not isinstance(data, (bytes, bytearray, memoryview)):
            err_msg_lines = [
                "BootOption.decode: 'data' must be a bytes-like object.",
                "GOT: value={!r} type={}".format(data, type(data).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiVariableError(err_msg) from None

        buffer = bytes(data)
        buffer_length = len(buffer)

        if buffer_length < _EFI_LOAD_OPTION_HEADER_LEN:
            err_msg_lines = [
                "EFI_LOAD_OPTION buffer is shorter than the minimum header.",
                "EXPECTED: >= {} bytes".format(_EFI_LOAD_OPTION_HEADER_LEN),
                "OBSERVED: {} bytes".format(buffer_length),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiVariableError(err_msg) from None

        attr_value = int.from_bytes(buffer[0:4], byteorder="little", signed=False)
        fpl_length = int.from_bytes(buffer[4:6], byteorder="little", signed=False)

        description_start = _EFI_LOAD_OPTION_HEADER_LEN
        nul_offset = _find_ucs2_null_terminator(buf=buffer, start=description_start)
        description_bytes = buffer[description_start:nul_offset]
        description_text = ucs2_decode(description_bytes)

        file_path_start = nul_offset + 2
        file_path_end = file_path_start + fpl_length
        if file_path_end > buffer_length:
            err_msg_lines = [
                "EFI_LOAD_OPTION FilePathListLength exceeds remaining buffer.",
                "FilePathListLength: {}".format(fpl_length),
                "REMAINING: {}".format(buffer_length - file_path_start),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiVariableError(err_msg) from None

        file_path_bytes = bytes(buffer[file_path_start:file_path_end])
        optional_data = bytes(buffer[file_path_end:])

        try:
            decoded_device_path = decode_device_path(data=file_path_bytes)
        except Exception as dexc:
            err_msg_lines = [
                "EFI_LOAD_OPTION FilePathList failed to decode as a device path.",
                "FilePathListLength: {}".format(fpl_length),
                "INNER: {}".format(type(dexc).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiVariableError(err_msg) from dexc

        rtnval = cls(
            attributes=LoadOptionAttribute(attr_value),
            description=description_text,
            file_path=decoded_device_path,
            optional_data=optional_data,
        )
        return rtnval

    def encode(self) -> bytes:
        """
            Serialize this :class:`BootOption` as a
            ``Boot####``/``Driver####``/``SysPrep####`` variable body
            per UEFI specification section 3.1.3.

            :returns: The packed variable-body bytes.
            :raises: UefiError
        """
        # UCS-2 encode the description; ucs2_encode rejects non-BMP and
        # surrogate code units explicitly.
        try:
            description_bytes = ucs2_encode(self.description)
        except UnicodeEncodeError as uerr:
            err_msg_lines = [
                "BootOption.encode: description is not strict UCS-2.",
                "INNER: {}".format(str(uerr)),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg) from uerr

        # Encode the device path to its wire form.
        try:
            file_path_bytes = encode_device_path(path=self.file_path)
        except Exception as dexc:
            err_msg_lines = [
                "BootOption.encode: failed to encode file_path as a device path.",
                "INNER: {}".format(type(dexc).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg) from dexc

        fpl_length = len(file_path_bytes)
        if fpl_length > 0xFFFF:
            err_msg_lines = [
                "BootOption.encode: FilePathListLength exceeds UINT16 max.",
                "OBSERVED: {}".format(fpl_length),
                "MAXIMUM: {}".format(0xFFFF),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg) from None

        attr_bytes = int(self.attributes).to_bytes(length=4, byteorder="little", signed=False)
        fpl_bytes = fpl_length.to_bytes(length=2, byteorder="little", signed=False)
        nul_terminator = b"\x00\x00"

        rtnval = (
            attr_bytes
            + fpl_bytes
            + description_bytes
            + nul_terminator
            + file_path_bytes
            + self.optional_data
        )
        return rtnval

    def __repr__(self) -> str:
        """
            Render the option with the device-path text embedded so log
            lines carry the full boot target without callers having to
            cross-reference raw bytes.

            :returns: A one-line ``repr`` string.
        """
        try:
            file_path_text = device_path_to_text(path=self.file_path)
        except Exception:
            # device_path_to_text is tolerant of unknown node types, but in
            # the face of arbitrary user input we never want repr to raise.
            file_path_text = "<unrenderable>"

        rtnval = (
            "BootOption(attributes=0x{attrs:08X}, description={desc!r}, "
            "file_path={path!r}, optional_data_len={opt_len})"
        ).format(
            attrs=int(self.attributes),
            desc=self.description,
            path=file_path_text,
            opt_len=len(self.optional_data),
        )
        return rtnval


# ---------------------------------------------------------------------------
# BootManager
# ---------------------------------------------------------------------------

def _validate_option_id(option_id: int) -> None:
    """
        Validate that ``option_id`` fits in the UINT16 range
        ``0x0000..0xFFFF``.

        :param option_id: The option index to validate.

        :raises: ConfigurationError
    """
    if not isinstance(option_id, int) or isinstance(option_id, bool):
        err_msg_lines = [
            "option_id must be an int.",
            "GOT: value={!r} type={}".format(option_id, type(option_id).__name__),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise ConfigurationError(err_msg) from None

    if option_id < 0 or option_id > 0xFFFF:
        err_msg_lines = [
            "option_id must be in 0x0000..0xFFFF.",
            "OBSERVED: {}".format(option_id),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise ConfigurationError(err_msg) from None

    return


def _reject_semantic_patterns_on_write(operation_name: str, aspects: UefiCmdAspects) -> None:
    """
        Reject action patterns that are meaningless on a mutating
        operation.  ``DO_WHILE_SUCCESS`` never converges on a write and
        ``DO_UNTIL_CONNECTION_FAILURE`` is a connection-loss idiom, not a
        command-retry one.

        :param operation_name: The mutating operation's public name.
        :param aspects: The effective aspects for the call.

        :raises: SemanticError
    """
    pattern = aspects.action_pattern
    if pattern == ActionPattern.DO_WHILE_SUCCESS:
        err_msg_lines = [
            "BootManager: DO_WHILE_SUCCESS is not meaningful on a mutating operation.",
            "OPERATION: {}".format(operation_name),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise SemanticError(err_msg) from None

    if pattern == ActionPattern.DO_UNTIL_CONNECTION_FAILURE:
        err_msg_lines = [
            "BootManager: DO_UNTIL_CONNECTION_FAILURE is not meaningful on a BootManager operation.",
            "OPERATION: {}".format(operation_name),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise SemanticError(err_msg) from None

    return


def _format_option_name(prefix: str, option_id: int) -> str:
    """
        Assemble a UEFI option variable name from its prefix and index.

        :param prefix: The family prefix (``"Boot"``, ``"Driver"``, or
                       ``"SysPrep"``).
        :param option_id: The option index.

        :returns: The assembled variable name (e.g. ``"Boot0001"``).
    """
    rtnval = "{}{:04X}".format(prefix, option_id)
    return rtnval


class BootManager:
    """
        Facade over :class:`VariableStore` that exposes typed CRUD for the
        UEFI boot-manager variables defined in UEFI specification sections
        3, 3.1.3, and 3.3.

        Every public method accepts ``aspects: Optional[UefiCmdAspects] =
        None`` and resolves the effective aspects via
        :func:`resolve_aspects`.  Writes use the
        :data:`BOOT_OPTION_WRITE_ATTRIBUTES` bitfield and variable names
        follow the UEFI convention of ``Boot####``, ``Driver####``, and
        ``SysPrep####`` with uppercase 4-hex indices.

        :param variable_store: The :class:`VariableStore` through which
                               every variable-services call dispatches.
        :param aspects: Optional default :class:`UefiCmdAspects`.  When not
                        supplied the :data:`DEFAULT_CMD_ASPECTS` singleton
                        is used.
    """

    def __init__(self, variable_store: VariableStore,
                       aspects: UefiCmdAspects = DEFAULT_CMD_ASPECTS) -> None:
        """
            Initialize the :class:`BootManager`.

            :param variable_store: The :class:`VariableStore` facade.
            :param aspects: The default aspects for per-call resolution.

            :raises: ConfigurationError
        """
        if not isinstance(variable_store, VariableStore):
            err_msg_lines = [
                "BootManager: 'variable_store' must be a VariableStore.",
                "GOT: value={!r} type={}".format(
                    variable_store, type(variable_store).__name__,
                ),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg) from None

        self._variable_store: VariableStore = variable_store
        self._aspects: UefiCmdAspects = aspects
        self._vendor_guid: uuid.UUID = EFI_GLOBAL_VARIABLE_GUID
        self._logger = get_uefi_logger(UEFI_BOOTMGR_CHANNEL)
        return

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve(self, aspects: Optional[UefiCmdAspects]) -> UefiCmdAspects:
        """
            Resolve the effective aspects for a call and validate its type.

            :param aspects: The per-call override (may be ``None``).

            :returns: The effective aspects instance.
            :raises: SemanticError
        """
        if aspects is not None and not isinstance(aspects, UefiCmdAspects):
            err_msg_lines = [
                "BootManager: 'aspects' must be a UefiCmdAspects or None.",
                "GOT: value={!r} type={}".format(aspects, type(aspects).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise SemanticError(err_msg) from None

        effective = resolve_aspects(passed=aspects, instance_default=self._aspects)
        return effective

    def _read_option(self, prefix: str, option_id: int,
                           aspects: Optional[UefiCmdAspects]) -> BootOption:
        """
            Read and decode a single option variable.

            :param prefix: The family prefix.
            :param option_id: The option index.
            :param aspects: Optional per-call aspects override.

            :returns: The decoded :class:`BootOption`.
            :raises: UefiVariableError, ConfigurationError
        """
        _validate_option_id(option_id=option_id)
        effective = self._resolve(aspects=aspects)

        variable_name = _format_option_name(prefix=prefix, option_id=option_id)

        self._logger.debug(
            "uefi.bootmgr: read_option prefix=%s id=0x%04X name=%s",
            prefix, option_id, variable_name,
        )

        record = self._variable_store.get_variable(
            name=variable_name,
            vendor_guid=self._vendor_guid,
            aspects=effective,
        )
        rtnval = BootOption.decode(data=record.data)
        return rtnval

    def _write_option(self, prefix: str, option_id: int,
                            option: BootOption,
                            aspects: Optional[UefiCmdAspects]) -> None:
        """
            Encode and write a single option variable.

            :param prefix: The family prefix.
            :param option_id: The option index.
            :param option: The :class:`BootOption` to encode.
            :param aspects: Optional per-call aspects override.

            :raises: UefiVariableError, ConfigurationError, SemanticError
        """
        _validate_option_id(option_id=option_id)
        if not isinstance(option, BootOption):
            err_msg_lines = [
                "BootManager: 'option' must be a BootOption.",
                "GOT: value={!r} type={}".format(option, type(option).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg) from None

        effective = self._resolve(aspects=aspects)
        _reject_semantic_patterns_on_write(
            operation_name="write_{}_option".format(prefix.lower()),
            aspects=effective,
        )

        variable_name = _format_option_name(prefix=prefix, option_id=option_id)
        payload = option.encode()
        record = UefiVariable(
            name=variable_name,
            vendor_guid=self._vendor_guid,
            attributes=BOOT_OPTION_WRITE_ATTRIBUTES,
            data=payload,
        )

        self._logger.info(
            "uefi.bootmgr: write_option prefix=%s id=0x%04X name=%s bytes=%d",
            prefix, option_id, variable_name, len(payload),
        )

        self._variable_store.set_variable(variable=record, aspects=effective)
        return

    def _delete_option(self, prefix: str, option_id: int,
                             aspects: Optional[UefiCmdAspects]) -> None:
        """
            Delete a single option variable.

            :param prefix: The family prefix.
            :param option_id: The option index.
            :param aspects: Optional per-call aspects override.

            :raises: UefiVariableError, ConfigurationError, SemanticError
        """
        _validate_option_id(option_id=option_id)
        effective = self._resolve(aspects=aspects)
        _reject_semantic_patterns_on_write(
            operation_name="delete_{}_option".format(prefix.lower()),
            aspects=effective,
        )

        variable_name = _format_option_name(prefix=prefix, option_id=option_id)

        self._logger.info(
            "uefi.bootmgr: delete_option prefix=%s id=0x%04X name=%s",
            prefix, option_id, variable_name,
        )

        self._variable_store.delete_variable(
            name=variable_name,
            vendor_guid=self._vendor_guid,
            aspects=effective,
        )
        return

    def _list_options(self, prefix: str,
                            aspects: Optional[UefiCmdAspects]) -> Dict[int, BootOption]:
        """
            Enumerate every ``{prefix}####`` variable under
            ``EFI_GLOBAL_VARIABLE_GUID`` and decode each.

            Malformed entries are skipped with a ``WARNING`` log so that a
            single corrupt variable cannot mask the rest of the catalogue.

            :param prefix: The family prefix.
            :param aspects: Optional per-call aspects override.

            :returns: ``{option_id: BootOption}`` ordered by option id.
        """
        effective = self._resolve(aspects=aspects)

        name_filter = "{}[0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f]".format(prefix)

        self._logger.debug(
            "uefi.bootmgr: list_options prefix=%s filter=%s",
            prefix, name_filter,
        )

        matches: List[Tuple[int, str]] = []
        for entry_name, entry_guid in self._variable_store.enumerate_variables(
            aspects=effective, name_filter=name_filter,
        ):
            if entry_guid != self._vendor_guid:
                continue
            if not entry_name.startswith(prefix):
                continue

            suffix = entry_name[len(prefix):]
            if len(suffix) != 4:
                continue

            try:
                parsed_id = int(suffix, 16)
            except ValueError:
                continue

            matches.append((parsed_id, entry_name))

        matches.sort(key=lambda item: item[0])

        rtnval: Dict[int, BootOption] = {}
        for parsed_id, entry_name in matches:
            try:
                record = self._variable_store.get_variable(
                    name=entry_name,
                    vendor_guid=self._vendor_guid,
                    aspects=effective,
                )
                decoded = BootOption.decode(data=record.data)
            except UefiVariableError as verr:
                self._logger.warning(
                    "uefi.bootmgr: list_options skipped malformed entry name=%s error=%s",
                    entry_name, verr.__class__.__name__,
                )
                continue
            except UefiError as uerr:
                self._logger.warning(
                    "uefi.bootmgr: list_options skipped un-decodable entry name=%s error=%s",
                    entry_name, uerr.__class__.__name__,
                )
                continue

            rtnval[parsed_id] = decoded

        return rtnval

    def _read_u16_variable(self, name: str,
                                 aspects: Optional[UefiCmdAspects]) -> Optional[int]:
        """
            Read a 2-byte little-endian UINT16 control variable.

            :param name: The variable name.
            :param aspects: Optional per-call aspects override.

            :returns: The decoded integer, or ``None`` if the variable is
                      absent.
            :raises: UefiVariableError
        """
        effective = self._resolve(aspects=aspects)

        try:
            record = self._variable_store.get_variable(
                name=name,
                vendor_guid=self._vendor_guid,
                aspects=effective,
            )
        except UefiVariableError as verr:
            if verr.efi_status == EFI_NOT_FOUND:
                rtnval: Optional[int] = None
                return rtnval
            raise

        if len(record.data) != 2:
            err_msg_lines = [
                "Control variable length is not 2 bytes.",
                "NAME: {}".format(name),
                "OBSERVED: {}".format(len(record.data)),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiVariableError(
                err_msg,
                name=name,
                vendor_guid=self._vendor_guid,
            ) from None

        value = int.from_bytes(record.data, byteorder="little", signed=False)
        rtnval = value
        return rtnval

    def _write_u16_variable(self, name: str, value: int,
                                  aspects: Optional[UefiCmdAspects]) -> None:
        """
            Write a 2-byte little-endian UINT16 control variable.

            :param name: The variable name.
            :param value: The UINT16 value to write.
            :param aspects: Optional per-call aspects override.

            :raises: UefiVariableError, ConfigurationError
        """
        effective = self._resolve(aspects=aspects)
        _reject_semantic_patterns_on_write(
            operation_name="write_{}".format(name),
            aspects=effective,
        )

        if value < 0 or value > 0xFFFF:
            err_msg_lines = [
                "UINT16 variable value out of range.",
                "NAME: {}".format(name),
                "OBSERVED: {}".format(value),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg) from None

        payload = value.to_bytes(length=2, byteorder="little", signed=False)
        record = UefiVariable(
            name=name,
            vendor_guid=self._vendor_guid,
            attributes=BOOT_OPTION_WRITE_ATTRIBUTES,
            data=payload,
        )

        self._variable_store.set_variable(variable=record, aspects=effective)
        return

    def _delete_variable(self, name: str,
                               aspects: Optional[UefiCmdAspects]) -> None:
        """
            Delete a named variable under ``EFI_GLOBAL_VARIABLE_GUID``.

            :param name: The variable name.
            :param aspects: Optional per-call aspects override.

            :raises: UefiVariableError
        """
        effective = self._resolve(aspects=aspects)
        _reject_semantic_patterns_on_write(
            operation_name="delete_{}".format(name),
            aspects=effective,
        )

        self._variable_store.delete_variable(
            name=name,
            vendor_guid=self._vendor_guid,
            aspects=effective,
        )
        return

    def _read_order(self, name: str,
                          aspects: Optional[UefiCmdAspects]) -> Tuple[int, ...]:
        """
            Read a UINT16-array order variable (``BootOrder``,
            ``DriverOrder``, ``SysPrepOrder``).

            :param name: The order-variable name.
            :param aspects: Optional per-call aspects override.

            :returns: Tuple of option IDs in execution order.
            :raises: UefiVariableError
        """
        effective = self._resolve(aspects=aspects)

        try:
            record = self._variable_store.get_variable(
                name=name,
                vendor_guid=self._vendor_guid,
                aspects=effective,
            )
        except UefiVariableError as verr:
            if verr.efi_status == EFI_NOT_FOUND:
                rtnval: Tuple[int, ...] = tuple()
                return rtnval
            raise

        rtnval = unpack_u16_array(buf=record.data)
        return rtnval

    def _write_order(self, name: str, indices: Sequence[int],
                           aspects: Optional[UefiCmdAspects]) -> None:
        """
            Atomically write an order variable.

            The implementation performs an optimistic read-modify-write
            loop: it reads the current order, writes the new order, then
            re-reads to verify no concurrent change wrote between read and
            write.  On mismatch it retries up to
            ``aspects.completion_timeout`` at
            ``aspects.completion_interval``.

            :param name: The order-variable name.
            :param indices: The desired order.
            :param aspects: Optional per-call aspects override.

            :raises: UefiVariableError, ConfigurationError, SemanticError
        """
        effective = self._resolve(aspects=aspects)
        _reject_semantic_patterns_on_write(
            operation_name="write_{}".format(name),
            aspects=effective,
        )

        # Encode once so per-attempt retries are cheap.
        encoded_new = pack_u16_array(values=indices)
        new_tuple = tuple(int(v) for v in indices)

        deadline = time.monotonic() + float(effective.completion_timeout)
        interval = float(effective.completion_interval)

        observed_history: List[Tuple[int, ...]] = []

        while True:
            # Re-read the current order so we can detect concurrent change.
            try:
                pre_record = self._variable_store.get_variable(
                    name=name,
                    vendor_guid=self._vendor_guid,
                    aspects=effective,
                )
                pre_order = unpack_u16_array(buf=pre_record.data)
            except UefiVariableError as verr:
                if verr.efi_status == EFI_NOT_FOUND:
                    pre_order = tuple()
                else:
                    raise

            observed_history.append(pre_order)

            # Write the desired order.
            record = UefiVariable(
                name=name,
                vendor_guid=self._vendor_guid,
                attributes=BOOT_OPTION_WRITE_ATTRIBUTES,
                data=encoded_new,
            )
            self._variable_store.set_variable(variable=record, aspects=effective)

            # Verify the post-write state matches what we asked for.
            try:
                post_record = self._variable_store.get_variable(
                    name=name,
                    vendor_guid=self._vendor_guid,
                    aspects=effective,
                )
                post_order = unpack_u16_array(buf=post_record.data)
            except UefiVariableError as verr:
                if verr.efi_status == EFI_NOT_FOUND:
                    post_order = tuple()
                else:
                    raise

            if post_order == new_tuple:
                # Success: the value we wrote is present post-write.
                return

            # Otherwise something else wrote in between; retry if we have time.
            observed_history.append(post_order)

            if effective.action_pattern != ActionPattern.DO_UNTIL_SUCCESS:
                err_msg_lines = [
                    "Atomic {name} update observed a concurrent write and ".format(name=name)
                    + "action_pattern does not permit retry.",
                    "REQUESTED: {}".format(new_tuple),
                    "OBSERVED_POST_WRITE: {}".format(post_order),
                    "ACTION_PATTERN: {}".format(effective.action_pattern.name),
                ]
                err_msg = os.linesep.join(err_msg_lines)
                raise UefiVariableError(
                    err_msg,
                    name=name,
                    vendor_guid=self._vendor_guid,
                ) from None

            if time.monotonic() >= deadline:
                err_msg_lines = [
                    "Atomic {name} update did not converge within completion_timeout.".format(name=name),
                    "REQUESTED: {}".format(new_tuple),
                    "ATTEMPTS: {}".format(len(observed_history)),
                ]
                for attempt_index, attempt_value in enumerate(observed_history):
                    err_msg_lines.append("ATTEMPT[{}]: {}".format(attempt_index, attempt_value))
                err_msg = os.linesep.join(err_msg_lines)
                raise UefiVariableError(
                    err_msg,
                    name=name,
                    vendor_guid=self._vendor_guid,
                ) from None

            time.sleep(interval)

        # Unreachable; the loop always returns or raises.

    # ------------------------------------------------------------------
    # Boot####
    # ------------------------------------------------------------------

    def list_boot_options(self, aspects: Optional[UefiCmdAspects] = None) -> Dict[int, BootOption]:
        """
            Enumerate every ``Boot####`` variable under
            ``EFI_GLOBAL_VARIABLE_GUID`` and decode each.

            :param aspects: Optional per-call aspects override.

            :returns: ``{option_id: BootOption}`` sorted by option id.
            :raises: UefiVariableError
        """
        rtnval = self._list_options(prefix=_PREFIX_BOOT, aspects=aspects)
        return rtnval

    def get_boot_option(self, index: int,
                              aspects: Optional[UefiCmdAspects] = None) -> BootOption:
        """
            Read and decode a single ``Boot####`` variable.

            :param index: The option index.
            :param aspects: Optional per-call aspects override.

            :returns: The decoded :class:`BootOption`.
            :raises: UefiVariableError, ConfigurationError
        """
        rtnval = self._read_option(prefix=_PREFIX_BOOT, option_id=index, aspects=aspects)
        return rtnval

    def set_boot_option(self, index: int, option: BootOption,
                              aspects: Optional[UefiCmdAspects] = None) -> None:
        """
            Write a ``Boot####`` variable.

            :param index: The option index.
            :param option: The :class:`BootOption` to encode.
            :param aspects: Optional per-call aspects override.

            :raises: UefiVariableError, ConfigurationError, SemanticError
        """
        self._write_option(prefix=_PREFIX_BOOT, option_id=index,
                           option=option, aspects=aspects)
        return

    def delete_boot_option(self, index: int,
                                 aspects: Optional[UefiCmdAspects] = None) -> None:
        """
            Delete a ``Boot####`` variable.

            :param index: The option index.
            :param aspects: Optional per-call aspects override.

            :raises: UefiVariableError, ConfigurationError, SemanticError
        """
        self._delete_option(prefix=_PREFIX_BOOT, option_id=index, aspects=aspects)
        return

    # ------------------------------------------------------------------
    # BootOrder
    # ------------------------------------------------------------------

    def get_boot_order(self, aspects: Optional[UefiCmdAspects] = None) -> Tuple[int, ...]:
        """
            Read the ``BootOrder`` variable.

            :param aspects: Optional per-call aspects override.

            :returns: Tuple of option IDs in execution order; an empty
                      tuple when the variable is absent.
            :raises: UefiVariableError
        """
        rtnval = self._read_order(name=_NAME_BOOT_ORDER, aspects=aspects)
        return rtnval

    def set_boot_order(self, indices: Sequence[int],
                             aspects: Optional[UefiCmdAspects] = None) -> None:
        """
            Atomically write the ``BootOrder`` variable.

            The write performs a read-modify-write cycle: the post-write
            state is re-read and compared against the requested order; on
            mismatch the write is retried up to
            ``aspects.completion_timeout``.

            :param indices: The desired order.
            :param aspects: Optional per-call aspects override.

            :raises: UefiVariableError, ConfigurationError, SemanticError
        """
        self._write_order(name=_NAME_BOOT_ORDER, indices=indices, aspects=aspects)
        return

    # ------------------------------------------------------------------
    # BootNext
    # ------------------------------------------------------------------

    def get_boot_next(self, aspects: Optional[UefiCmdAspects] = None) -> Optional[int]:
        """
            Read the ``BootNext`` variable.

            :param aspects: Optional per-call aspects override.

            :returns: The next-boot option ID, or ``None`` if unset.
            :raises: UefiVariableError
        """
        rtnval = self._read_u16_variable(name=_NAME_BOOT_NEXT, aspects=aspects)
        return rtnval

    def set_boot_next(self, index: int,
                            aspects: Optional[UefiCmdAspects] = None) -> None:
        """
            Set the ``BootNext`` variable after verifying the target
            ``Boot####`` exists.

            :param index: The option index to run on next reset.
            :param aspects: Optional per-call aspects override.

            :raises: UefiVariableError, ConfigurationError, SemanticError
        """
        _validate_option_id(option_id=index)
        effective = self._resolve(aspects=aspects)
        _reject_semantic_patterns_on_write(
            operation_name="set_boot_next",
            aspects=effective,
        )

        # Verify the target exists so we never land in an unbootable state.
        variable_name = _format_option_name(prefix=_PREFIX_BOOT, option_id=index)
        try:
            self._variable_store.get_variable(
                name=variable_name,
                vendor_guid=self._vendor_guid,
                aspects=effective,
            )
        except UefiVariableError as verr:
            if verr.efi_status == EFI_NOT_FOUND:
                err_msg_lines = [
                    "BootManager.set_boot_next: target Boot#### does not exist.",
                    "TARGET: {}".format(variable_name),
                ]
                err_msg = os.linesep.join(err_msg_lines)
                raise UefiVariableError(
                    err_msg,
                    name=variable_name,
                    vendor_guid=self._vendor_guid,
                    efi_status=EFI_NOT_FOUND,
                ) from verr
            raise

        self._write_u16_variable(
            name=_NAME_BOOT_NEXT, value=index, aspects=aspects,
        )
        return

    def clear_boot_next(self, aspects: Optional[UefiCmdAspects] = None) -> None:
        """
            Delete the ``BootNext`` variable.

            :param aspects: Optional per-call aspects override.

            :raises: UefiVariableError, SemanticError
        """
        self._delete_variable(name=_NAME_BOOT_NEXT, aspects=aspects)
        return

    # ------------------------------------------------------------------
    # BootCurrent (read-only)
    # ------------------------------------------------------------------

    def get_boot_current(self, aspects: Optional[UefiCmdAspects] = None) -> Optional[int]:
        """
            Read the ``BootCurrent`` variable.  Firmware populates this
            read-only variable with the option id of the currently-loaded
            boot option.

            :param aspects: Optional per-call aspects override.

            :returns: The current boot option ID, or ``None`` when absent.
            :raises: UefiVariableError
        """
        rtnval = self._read_u16_variable(name=_NAME_BOOT_CURRENT, aspects=aspects)
        return rtnval

    # ------------------------------------------------------------------
    # Timeout
    # ------------------------------------------------------------------

    def get_boot_timeout(self, aspects: Optional[UefiCmdAspects] = None) -> Optional[int]:
        """
            Read the ``Timeout`` variable (Boot Manager inactivity seconds).

            Per UEFI specification section 3.3, ``0`` means no delay and
            ``0xFFFF`` means no timeout.

            :param aspects: Optional per-call aspects override.

            :returns: The timeout in seconds, or ``None`` when absent.
            :raises: UefiVariableError
        """
        rtnval = self._read_u16_variable(name=_NAME_TIMEOUT, aspects=aspects)
        return rtnval

    def set_boot_timeout(self, seconds: int,
                               aspects: Optional[UefiCmdAspects] = None) -> None:
        """
            Write the ``Timeout`` variable.

            :param seconds: The Boot Manager timeout in seconds.  Must fit
                            in UINT16.  ``0`` means no delay; ``0xFFFF``
                            means never timeout.
            :param aspects: Optional per-call aspects override.

            :raises: UefiVariableError, ConfigurationError, SemanticError
        """
        if not isinstance(seconds, int) or isinstance(seconds, bool):
            err_msg_lines = [
                "BootManager.set_boot_timeout: 'seconds' must be an int.",
                "GOT: value={!r} type={}".format(seconds, type(seconds).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg) from None

        if seconds < 0 or seconds > 0xFFFF:
            err_msg_lines = [
                "BootManager.set_boot_timeout: 'seconds' must be in 0..65535.",
                "OBSERVED: {}".format(seconds),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg) from None

        self._write_u16_variable(
            name=_NAME_TIMEOUT, value=seconds, aspects=aspects,
        )
        return

    # ------------------------------------------------------------------
    # Driver####
    # ------------------------------------------------------------------

    def list_driver_options(self, aspects: Optional[UefiCmdAspects] = None) -> Dict[int, BootOption]:
        """
            Enumerate every ``Driver####`` variable and decode each.

            :param aspects: Optional per-call aspects override.

            :returns: ``{option_id: BootOption}`` sorted by option id.
            :raises: UefiVariableError
        """
        rtnval = self._list_options(prefix=_PREFIX_DRIVER, aspects=aspects)
        return rtnval

    def get_driver_option(self, index: int,
                                aspects: Optional[UefiCmdAspects] = None) -> BootOption:
        """
            Read and decode a single ``Driver####`` variable.

            :param index: The option index.
            :param aspects: Optional per-call aspects override.

            :returns: The decoded :class:`BootOption`.
            :raises: UefiVariableError, ConfigurationError
        """
        rtnval = self._read_option(prefix=_PREFIX_DRIVER, option_id=index, aspects=aspects)
        return rtnval

    def set_driver_option(self, index: int, option: BootOption,
                                aspects: Optional[UefiCmdAspects] = None) -> None:
        """
            Write a ``Driver####`` variable.

            :param index: The option index.
            :param option: The :class:`BootOption` to encode.
            :param aspects: Optional per-call aspects override.

            :raises: UefiVariableError, ConfigurationError, SemanticError
        """
        self._write_option(prefix=_PREFIX_DRIVER, option_id=index,
                           option=option, aspects=aspects)
        return

    def delete_driver_option(self, index: int,
                                   aspects: Optional[UefiCmdAspects] = None) -> None:
        """
            Delete a ``Driver####`` variable.

            :param index: The option index.
            :param aspects: Optional per-call aspects override.

            :raises: UefiVariableError, ConfigurationError, SemanticError
        """
        self._delete_option(prefix=_PREFIX_DRIVER, option_id=index, aspects=aspects)
        return

    def get_driver_order(self, aspects: Optional[UefiCmdAspects] = None) -> Tuple[int, ...]:
        """
            Read the ``DriverOrder`` variable.

            :param aspects: Optional per-call aspects override.

            :returns: Tuple of driver option IDs in execution order.
            :raises: UefiVariableError
        """
        rtnval = self._read_order(name=_NAME_DRIVER_ORDER, aspects=aspects)
        return rtnval

    def set_driver_order(self, indices: Sequence[int],
                               aspects: Optional[UefiCmdAspects] = None) -> None:
        """
            Atomically write the ``DriverOrder`` variable.

            :param indices: The desired driver order.
            :param aspects: Optional per-call aspects override.

            :raises: UefiVariableError, ConfigurationError, SemanticError
        """
        self._write_order(name=_NAME_DRIVER_ORDER, indices=indices, aspects=aspects)
        return

    # ------------------------------------------------------------------
    # SysPrep####
    # ------------------------------------------------------------------

    def list_sys_prep_options(self, aspects: Optional[UefiCmdAspects] = None) -> Dict[int, BootOption]:
        """
            Enumerate every ``SysPrep####`` variable and decode each.

            :param aspects: Optional per-call aspects override.

            :returns: ``{option_id: BootOption}`` sorted by option id.
            :raises: UefiVariableError
        """
        rtnval = self._list_options(prefix=_PREFIX_SYS_PREP, aspects=aspects)
        return rtnval

    def get_sys_prep_option(self, index: int,
                                  aspects: Optional[UefiCmdAspects] = None) -> BootOption:
        """
            Read and decode a single ``SysPrep####`` variable.

            :param index: The option index.
            :param aspects: Optional per-call aspects override.

            :returns: The decoded :class:`BootOption`.
            :raises: UefiVariableError, ConfigurationError
        """
        rtnval = self._read_option(prefix=_PREFIX_SYS_PREP, option_id=index, aspects=aspects)
        return rtnval

    def set_sys_prep_option(self, index: int, option: BootOption,
                                  aspects: Optional[UefiCmdAspects] = None) -> None:
        """
            Write a ``SysPrep####`` variable.

            :param index: The option index.
            :param option: The :class:`BootOption` to encode.
            :param aspects: Optional per-call aspects override.

            :raises: UefiVariableError, ConfigurationError, SemanticError
        """
        self._write_option(prefix=_PREFIX_SYS_PREP, option_id=index,
                           option=option, aspects=aspects)
        return

    def delete_sys_prep_option(self, index: int,
                                     aspects: Optional[UefiCmdAspects] = None) -> None:
        """
            Delete a ``SysPrep####`` variable.

            :param index: The option index.
            :param aspects: Optional per-call aspects override.

            :raises: UefiVariableError, ConfigurationError, SemanticError
        """
        self._delete_option(prefix=_PREFIX_SYS_PREP, option_id=index, aspects=aspects)
        return

    def get_sys_prep_order(self, aspects: Optional[UefiCmdAspects] = None) -> Tuple[int, ...]:
        """
            Read the ``SysPrepOrder`` variable.

            :param aspects: Optional per-call aspects override.

            :returns: Tuple of sys-prep option IDs in execution order.
            :raises: UefiVariableError
        """
        rtnval = self._read_order(name=_NAME_SYS_PREP_ORDER, aspects=aspects)
        return rtnval

    def set_sys_prep_order(self, indices: Sequence[int],
                                 aspects: Optional[UefiCmdAspects] = None) -> None:
        """
            Atomically write the ``SysPrepOrder`` variable.

            :param indices: The desired sys-prep order.
            :param aspects: Optional per-call aspects override.

            :raises: UefiVariableError, ConfigurationError, SemanticError
        """
        self._write_order(name=_NAME_SYS_PREP_ORDER, indices=indices, aspects=aspects)
        return

    # ------------------------------------------------------------------
    # Iteration
    # ------------------------------------------------------------------

    def iter_boot_options(self, aspects: Optional[UefiCmdAspects] = None) -> Iterator[Tuple[int, BootOption]]:
        """
            Iterate ``(option_id, BootOption)`` tuples for every decodable
            ``Boot####`` variable.

            :param aspects: Optional per-call aspects override.

            :returns: An iterator of ``(int, BootOption)`` tuples sorted
                      by option ID.
        """
        options = self._list_options(prefix=_PREFIX_BOOT, aspects=aspects)

        def _generator() -> Iterator[Tuple[int, BootOption]]:
            """
                Inner generator so we release any backend cursor only when
                iteration begins.
            """
            for option_id in sorted(options.keys()):
                yield (option_id, options[option_id])
            return

        rtnval = _generator()
        return rtnval
