"""
    .. module:: test_uefibootmanager
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Unit tests for :mod:`ccai.protocols.uefi.uefibootmanager`.

               Exercises:

               * :class:`BootOption` encode / decode round-trip across a
                 realistic corpus (Windows Boot Manager, shim, GRUB,
                 rEFInd, UEFI Shell, HTTP-boot URI).
               * :class:`LoadOptionAttribute` flag round-trip.
               * :class:`BootManager` CRUD against a fake
                 :class:`VariableStore` that records every call and can
                 mutate its own backing dict between reads so atomic
                 retry paths can be observed.
               * ``BootNext`` existence-verification.
               * ``BootOrder`` optimistic read-modify-write retry on
                 concurrent change.
               * ``Timeout`` returns ``None`` when the variable is
                 missing.
               * ``Driver####`` / ``SysPrep####`` counterpart wrappers.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Code Craft AI LLC"
__credits__ = []


from types import SimpleNamespace

from ccai import testbase

import dataclasses
import uuid

from typing import Any, Dict, Iterator, List, Optional, Tuple

from ccai.errors.exceptions import ConfigurationError, SemanticError

from ccai.protocols.uefi.uefiaspects import (
    ActionPattern,
    DEFAULT_CMD_ASPECTS,
    UefiCmdAspects,
)
from ccai.protocols.uefi.uefidevicepath import (
    DevicePath,
    DevicePathNode,
    decode_device_path,
    device_path_from_text,
    encode_device_path,
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
    VariableStore,
)

from ccai.protocols.uefi.uefibootmanager import (
    BOOT_OPTION_WRITE_ATTRIBUTES,
    BootManager,
    BootOption,
    LoadOptionAttribute,
    pack_u16_array,
    unpack_u16_array,
)


# ---------------------------------------------------------------------------
# Fake VariableStore
# ---------------------------------------------------------------------------

class _FakeVariableStore:
    """
        Dict-backed stand-in for :class:`VariableStore`.  Records every
        call and supports scripted concurrent-change injection via
        :attr:`pre_write_mutator`.

        The fake duck-types the public surface consumed by
        :class:`BootManager`: ``get_variable``, ``set_variable``,
        ``delete_variable``, and ``enumerate_variables``.  It also
        exposes ``_aspects`` so :func:`resolve_aspects` can fall back to
        the store's default if the caller passes ``aspects=None`` into
        ``BootManager.__init__`` without specifying its own.
    """

    def __init__(self, aspects: Optional[UefiCmdAspects] = None) -> None:
        """
            Initialize the fake store.

            :param aspects: Default :class:`UefiCmdAspects`.  Falls back
                            to :data:`DEFAULT_CMD_ASPECTS` when ``None``.
        """
        self._aspects: UefiCmdAspects = aspects if aspects is not None else DEFAULT_CMD_ASPECTS
        self._storage: Dict[Tuple[str, uuid.UUID], UefiVariable] = {}
        self.get_calls: List[Tuple[str, uuid.UUID, Optional[UefiCmdAspects]]] = []
        self.set_calls: List[Tuple[UefiVariable, Optional[UefiCmdAspects]]] = []
        self.delete_calls: List[Tuple[str, uuid.UUID, Optional[UefiCmdAspects]]] = []
        self.enumerate_calls: List[Tuple[Optional[UefiCmdAspects], Optional[str]]] = []
        # Optional callable invoked immediately before ``set_variable`` dispatches.
        # Use this to mutate storage between a caller's read and write so we
        # can exercise optimistic-retry paths.
        self.pre_write_mutator: Optional[Any] = None
        return

    # Helpers for tests --------------------------------------------------

    def put(self, variable: UefiVariable) -> None:
        """
            Populate the backing dict directly (no call recording).
        """
        key = (variable.name, variable.vendor_guid)
        self._storage[key] = variable
        return

    def remove(self, name: str, vendor_guid: uuid.UUID) -> None:
        """
            Remove a variable from the backing dict (no call recording).
        """
        key = (name, vendor_guid)
        if key in self._storage:
            del self._storage[key]
        return

    def exists(self, name: str, vendor_guid: uuid.UUID) -> bool:
        """
            Return True if the backing dict has the given variable.
        """
        rtnval = (name, vendor_guid) in self._storage
        return rtnval

    # Public VariableStore surface --------------------------------------

    def get_variable(self, name: str,
                           vendor_guid: uuid.UUID,
                           aspects: Optional[UefiCmdAspects] = None) -> UefiVariable:
        """
            Dict-backed read.
        """
        self.get_calls.append((name, vendor_guid, aspects))
        key = (name, vendor_guid)
        if key not in self._storage:
            err_msg = "Fake store: variable not found: {} / {}".format(name, vendor_guid)
            raise UefiVariableError(
                err_msg,
                name=name,
                vendor_guid=vendor_guid,
                efi_status=EFI_NOT_FOUND,
            )
        rtnval = self._storage[key]
        return rtnval

    def set_variable(self, variable: UefiVariable,
                           aspects: Optional[UefiCmdAspects] = None,
                           signer_key: Optional[Any] = None,
                           signer_cert: Optional[Any] = None,
                           signing_time: Optional[Any] = None) -> None:
        """
            Dict-backed write.  Invokes ``pre_write_mutator`` if set.
        """
        if self.pre_write_mutator is not None:
            self.pre_write_mutator(self, variable, aspects)
        self.set_calls.append((variable, aspects))
        key = (variable.name, variable.vendor_guid)
        self._storage[key] = variable
        return

    def delete_variable(self, name: str,
                              vendor_guid: uuid.UUID,
                              aspects: Optional[UefiCmdAspects] = None) -> None:
        """
            Dict-backed delete.
        """
        self.delete_calls.append((name, vendor_guid, aspects))
        key = (name, vendor_guid)
        if key in self._storage:
            del self._storage[key]
        return

    def enumerate_variables(self, aspects: Optional[UefiCmdAspects] = None,
                                  name_filter: Optional[str] = None) -> Iterator[Tuple[str, uuid.UUID]]:
        """
            Dict-backed enumeration.  Applies a simple ``fnmatch`` glob
            filter to match the real :class:`VariableStore`.
        """
        import fnmatch

        self.enumerate_calls.append((aspects, name_filter))

        names = sorted(self._storage.keys())
        for key in names:
            entry_name, entry_guid = key
            if name_filter is not None:
                if not fnmatch.fnmatchcase(entry_name, name_filter):
                    continue
            yield (entry_name, entry_guid)
        return


# ---------------------------------------------------------------------------
# Test corpus: realistic BootOption bytes
# ---------------------------------------------------------------------------

def _build_boot_option_bytes(attributes: int, description: str,
                             file_path_nodes: Tuple[DevicePathNode, ...],
                             optional_data: bytes) -> bytes:
    """
        Hand-build a byte-for-byte EFI_LOAD_OPTION representation so the
        decode path is exercised against realistic layouts rather than
        the encoder's own output.
    """
    fpl = encode_device_path(path=DevicePath(nodes=file_path_nodes))
    desc_encoded = description.encode("utf-16-le") + b"\x00\x00"

    attr_bytes = attributes.to_bytes(length=4, byteorder="little", signed=False)
    fpl_length_bytes = len(fpl).to_bytes(length=2, byteorder="little", signed=False)

    rtnval = attr_bytes + fpl_length_bytes + desc_encoded + fpl + optional_data
    return rtnval


def _hd_node() -> DevicePathNode:
    """
        Build a representative HardDrive media node (Media/HardDrive
        subtype 1) with a minimal 42-byte payload.
    """
    # Layout per UEFI section 10.3.5.2:
    #   u32 partition number
    #   u64 partition start
    #   u64 partition size
    #   16 bytes signature
    #   u8 mbr_type (0x02 = GPT)
    #   u8 signature_type (0x02 = GUID)
    payload = (
        (1).to_bytes(length=4, byteorder="little", signed=False)
        + (2048).to_bytes(length=8, byteorder="little", signed=False)
        + (262144).to_bytes(length=8, byteorder="little", signed=False)
        + bytes(range(16))
        + bytes([0x02, 0x02])
    )
    rtnval = DevicePathNode(type=0x04, subtype=0x01, data=payload)
    return rtnval


def _file_path_node(path: str) -> DevicePathNode:
    """
        Build a Media/File Path device-path node (subtype 4) with the
        UCS-2 encoded ``path`` + NUL terminator as the payload.
    """
    payload = path.encode("utf-16-le") + b"\x00\x00"
    rtnval = DevicePathNode(type=0x04, subtype=0x04, data=payload)
    return rtnval


def _uri_node(uri: str) -> DevicePathNode:
    """
        Build a Messaging/URI device-path node (type 0x03, subtype 0x18).
    """
    payload = uri.encode("utf-8")
    rtnval = DevicePathNode(type=0x03, subtype=0x18, data=payload)
    return rtnval


# ---------------------------------------------------------------------------
# pack_u16_array / unpack_u16_array
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# U16ArrayCodecTests
# ----------------------------------------------------------------------

"""
    Exercise :func:`pack_u16_array` and :func:`unpack_u16_array`.
"""

def test_empty_round_trip() -> None:
    packed = pack_u16_array(values=[])
    testbase.assert_equal(packed, b"")
    testbase.assert_equal(unpack_u16_array(buf=packed), tuple())
    return

def test_simple_round_trip() -> None:
    values = [0x0001, 0x0002, 0x0007, 0xFFFE]
    packed = pack_u16_array(values=values)
    testbase.assert_equal(len(packed), 2 * len(values))
    unpacked = unpack_u16_array(buf=packed)
    testbase.assert_equal(unpacked, tuple(values))
    return

def test_out_of_range_raises() -> None:
    with testbase.raises(ConfigurationError):
        pack_u16_array(values=[0x1FFFF])
    with testbase.raises(ConfigurationError):
        pack_u16_array(values=[-1])
    return

def test_odd_length_buffer_raises() -> None:
    with testbase.raises(ConfigurationError):
        unpack_u16_array(buf=b"\x01\x02\x03")
    return


# ---------------------------------------------------------------------------
# LoadOptionAttribute flag round-trip
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# LoadOptionAttributeTests
# ----------------------------------------------------------------------

"""
    Verify every :class:`LoadOptionAttribute` flag survives
    serialization through :meth:`BootOption.encode` /
    :meth:`BootOption.decode`.
"""

def _build_option(attrs: int) -> BootOption:
    """
        Construct a minimal :class:`BootOption` carrying ``attrs``.
    """
    device_path = DevicePath(nodes=(_file_path_node(path="\\SHELL.EFI"),))
    rtnval = BootOption(
        attributes=LoadOptionAttribute(attrs),
        description="flag-test",
        file_path=device_path,
        optional_data=b"",
    )
    return rtnval

def test_active_flag_round_trip() -> None:
    option = _build_option(attrs=int(LoadOptionAttribute.LOAD_OPTION_ACTIVE))
    decoded = BootOption.decode(data=option.encode())
    testbase.assert_equal(decoded.attributes, LoadOptionAttribute.LOAD_OPTION_ACTIVE)
    return

def test_force_reconnect_flag_round_trip() -> None:
    option = _build_option(attrs=int(LoadOptionAttribute.LOAD_OPTION_FORCE_RECONNECT))
    decoded = BootOption.decode(data=option.encode())
    testbase.assert_equal(decoded.attributes, LoadOptionAttribute.LOAD_OPTION_FORCE_RECONNECT)
    return

def test_hidden_flag_round_trip() -> None:
    option = _build_option(attrs=int(LoadOptionAttribute.LOAD_OPTION_HIDDEN))
    decoded = BootOption.decode(data=option.encode())
    testbase.assert_equal(decoded.attributes, LoadOptionAttribute.LOAD_OPTION_HIDDEN)
    return

def test_category_app_round_trip() -> None:
    combined = (
        int(LoadOptionAttribute.LOAD_OPTION_ACTIVE)
        | int(LoadOptionAttribute.LOAD_OPTION_CATEGORY_APP)
    )
    option = _build_option(attrs=combined)
    decoded = BootOption.decode(data=option.encode())
    testbase.assert_equal(int(decoded.attributes), combined)
    category = int(decoded.attributes) & int(LoadOptionAttribute.LOAD_OPTION_CATEGORY_MASK)
    testbase.assert_equal(category, int(LoadOptionAttribute.LOAD_OPTION_CATEGORY_APP))
    return

def test_category_boot_is_zero() -> None:
    option = _build_option(attrs=int(LoadOptionAttribute.LOAD_OPTION_ACTIVE))
    decoded = BootOption.decode(data=option.encode())
    category = int(decoded.attributes) & int(LoadOptionAttribute.LOAD_OPTION_CATEGORY_MASK)
    testbase.assert_equal(category, int(LoadOptionAttribute.LOAD_OPTION_CATEGORY_BOOT))
    return


# ---------------------------------------------------------------------------
# BootOption decode / encode corpus
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# BootOptionCorpusTests
# ----------------------------------------------------------------------

"""
    Exercise decode / encode round trip for a realistic BootOption
    corpus covering common loader identities seen on modern systems.
"""

def _round_trip(raw: bytes, description: str,
                      expected_node_count: int) -> BootOption:
    """
        Decode ``raw``, encode, re-decode, and verify equivalence.
    """
    decoded = BootOption.decode(data=raw)
    testbase.assert_equal(decoded.description, description)
    testbase.assert_equal(len(decoded.file_path.nodes), expected_node_count)

    re_encoded = decoded.encode()
    testbase.assert_equal(re_encoded, raw)

    re_decoded = BootOption.decode(data=re_encoded)
    testbase.assert_equal(decoded, re_decoded)

    rtnval = decoded
    return rtnval

def test_windows_boot_manager() -> None:
    raw = _build_boot_option_bytes(
        attributes=int(LoadOptionAttribute.LOAD_OPTION_ACTIVE),
        description="Windows Boot Manager",
        file_path_nodes=(
            _hd_node(),
            _file_path_node(path="\\EFI\\Microsoft\\Boot\\bootmgfw.efi"),
        ),
        optional_data=b"WINDOWS\x00",
    )
    decoded = _round_trip(
        raw=raw, description="Windows Boot Manager",
        expected_node_count=2,
    )
    testbase.assert_equal(decoded.optional_data, b"WINDOWS\x00")
    return

def test_shim() -> None:
    raw = _build_boot_option_bytes(
        attributes=int(LoadOptionAttribute.LOAD_OPTION_ACTIVE),
        description="ubuntu",
        file_path_nodes=(
            _hd_node(),
            _file_path_node(path="\\EFI\\ubuntu\\shimx64.efi"),
        ),
        optional_data=b"",
    )
    _round_trip(raw=raw, description="ubuntu", expected_node_count=2)
    return

def test_grub() -> None:
    raw = _build_boot_option_bytes(
        attributes=int(LoadOptionAttribute.LOAD_OPTION_ACTIVE),
        description="GRUB",
        file_path_nodes=(
            _hd_node(),
            _file_path_node(path="\\EFI\\ubuntu\\grubx64.efi"),
        ),
        optional_data=b"",
    )
    _round_trip(raw=raw, description="GRUB", expected_node_count=2)
    return

def test_refind() -> None:
    raw = _build_boot_option_bytes(
        attributes=int(LoadOptionAttribute.LOAD_OPTION_ACTIVE),
        description="rEFInd Boot Manager",
        file_path_nodes=(
            _hd_node(),
            _file_path_node(path="\\EFI\\refind\\refind_x64.efi"),
        ),
        optional_data=b"",
    )
    _round_trip(
        raw=raw, description="rEFInd Boot Manager",
        expected_node_count=2,
    )
    return

def test_uefi_shell() -> None:
    raw = _build_boot_option_bytes(
        attributes=int(LoadOptionAttribute.LOAD_OPTION_ACTIVE),
        description="UEFI Shell",
        file_path_nodes=(
            _file_path_node(path="\\Shell.efi"),
        ),
        optional_data=b"",
    )
    _round_trip(raw=raw, description="UEFI Shell", expected_node_count=1)
    return

def test_http_boot_uri() -> None:
    raw = _build_boot_option_bytes(
        attributes=int(LoadOptionAttribute.LOAD_OPTION_ACTIVE),
        description="HTTP Boot",
        file_path_nodes=(
            _uri_node(uri="http://example.com/boot/loader.efi"),
        ),
        optional_data=b"\x01\x02\x03\x04",
    )
    decoded = _round_trip(
        raw=raw, description="HTTP Boot", expected_node_count=1,
    )
    testbase.assert_equal(decoded.optional_data, b"\x01\x02\x03\x04")
    return


# ---------------------------------------------------------------------------
# BootOption error paths
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# BootOptionErrorPathTests
# ----------------------------------------------------------------------

"""
    Exercise the defensive error paths in :meth:`BootOption.decode`
    and :meth:`BootOption.encode`.
"""

def test_decode_rejects_short_buffer() -> None:
    with testbase.raises(UefiVariableError):
        BootOption.decode(data=b"\x00\x00\x00")
    return

def test_decode_rejects_missing_null_terminator() -> None:
    # Header says FilePathListLength=0 but description never terminates.
    buf = (
        (1).to_bytes(length=4, byteorder="little", signed=False)
        + (0).to_bytes(length=2, byteorder="little", signed=False)
        + "abc".encode("utf-16-le")
    )
    with testbase.raises(UefiVariableError):
        BootOption.decode(data=buf)
    return

def test_decode_rejects_over_declared_file_path() -> None:
    desc = "X".encode("utf-16-le") + b"\x00\x00"
    over = 256
    buf = (
        (1).to_bytes(length=4, byteorder="little", signed=False)
        + over.to_bytes(length=2, byteorder="little", signed=False)
        + desc
        + b"\x00"  # only 1 byte of file path follows, far less than 256
    )
    with testbase.raises(UefiVariableError):
        BootOption.decode(data=buf)
    return

def test_encode_rejects_non_bmp_description() -> None:
    device_path = DevicePath(nodes=(_file_path_node(path="\\A.EFI"),))
    option = BootOption(
        attributes=LoadOptionAttribute.LOAD_OPTION_ACTIVE,
        description="rocket-\U0001F680",
        file_path=device_path,
        optional_data=b"",
    )
    with testbase.raises(UefiError):
        option.encode()
    return

def test_frozen_instance_rejects_mutation() -> None:
    device_path = DevicePath(nodes=(_file_path_node(path="\\A.EFI"),))
    option = BootOption(
        attributes=LoadOptionAttribute.LOAD_OPTION_ACTIVE,
        description="X",
        file_path=device_path,
        optional_data=b"",
    )
    with testbase.raises(dataclasses.FrozenInstanceError):
        option.description = "Y"  # type: ignore[misc]
    return

def test_repr_includes_device_path_text() -> None:
    device_path = DevicePath(nodes=(_file_path_node(path="\\Shell.efi"),))
    option = BootOption(
        attributes=LoadOptionAttribute.LOAD_OPTION_ACTIVE,
        description="Shell",
        file_path=device_path,
        optional_data=b"",
    )
    rendered = repr(option)
    testbase.assert_true("Shell" in rendered)
    # device_path_to_text for the file-path node includes the path string.
    testbase.assert_true("Shell.efi" in rendered)
    return


# ---------------------------------------------------------------------------
# BootManager CRUD / BootNext / BootOrder
# ---------------------------------------------------------------------------

def _make_boot_option(description: str, path: str,
                      attrs: int = int(LoadOptionAttribute.LOAD_OPTION_ACTIVE)) -> BootOption:
    """
        Shortcut for building a minimal :class:`BootOption` with a
        single File-Path node.
    """
    device_path = DevicePath(nodes=(_file_path_node(path=path),))
    rtnval = BootOption(
        attributes=LoadOptionAttribute(attrs),
        description=description,
        file_path=device_path,
        optional_data=b"",
    )
    return rtnval


def _seed_boot(store: _FakeVariableStore, option_id: int,
               option: BootOption) -> str:
    """
        Seed the fake store with a Boot#### variable.  Returns the
        assembled variable name.
    """
    name = "Boot{:04X}".format(option_id)
    record = UefiVariable(
        name=name,
        vendor_guid=EFI_GLOBAL_VARIABLE_GUID,
        attributes=BOOT_OPTION_WRITE_ATTRIBUTES,
        data=option.encode(),
    )
    store.put(variable=record)
    return name


# ----------------------------------------------------------------------
# BootManagerBootOptionTests
# ----------------------------------------------------------------------

"""
    Exercise Boot#### CRUD, list_boot_options enumeration, and
    option-id validation.
"""

def _setup_boot_manager_boot_option(ctx) -> None:
    """
        Fresh fake store and :class:`BootManager` per test.
    """
    ctx.store = _FakeVariableStore()
    # BootManager __init__ validates the store is a VariableStore; bypass
    # that by using object.__setattr__ on a manually-constructed instance.
    ctx.manager = BootManager.__new__(BootManager)
    ctx.manager._variable_store = ctx.store  # type: ignore[attr-defined]
    ctx.manager._aspects = DEFAULT_CMD_ASPECTS  # type: ignore[attr-defined]
    ctx.manager._vendor_guid = EFI_GLOBAL_VARIABLE_GUID  # type: ignore[attr-defined]
    import logging
    ctx.manager._logger = logging.getLogger("uefi.bootmgr.test")  # type: ignore[attr-defined]
    return

def test_list_boot_options_returns_all_seeded() -> None:
    ctx = SimpleNamespace()
    _setup_boot_manager_boot_option(ctx)
    opt0 = _make_boot_option(description="Windows", path="\\EFI\\bootmgfw.efi")
    opt1 = _make_boot_option(description="Shell", path="\\Shell.efi")
    _seed_boot(store=ctx.store, option_id=0x0000, option=opt0)
    _seed_boot(store=ctx.store, option_id=0x0001, option=opt1)

    options = ctx.manager.list_boot_options()

    testbase.assert_equal(set(options.keys()), {0x0000, 0x0001})
    testbase.assert_equal(options[0x0000].description, "Windows")
    testbase.assert_equal(options[0x0001].description, "Shell")
    return

def test_list_boot_options_skips_malformed_entry() -> None:
    ctx = SimpleNamespace()
    _setup_boot_manager_boot_option(ctx)
    # Seed a valid one plus a malformed Boot0002 with too-short data.
    opt_valid = _make_boot_option(description="Valid", path="\\Valid.efi")
    _seed_boot(store=ctx.store, option_id=0x0001, option=opt_valid)
    ctx.store.put(variable=UefiVariable(
        name="Boot0002",
        vendor_guid=EFI_GLOBAL_VARIABLE_GUID,
        attributes=BOOT_OPTION_WRITE_ATTRIBUTES,
        data=b"\x00\x00",
    ))

    options = ctx.manager.list_boot_options()
    testbase.assert_true(0x0001 in options)
    testbase.assert_true(0x0002 not in options)
    return

def test_get_boot_option_decodes_seeded() -> None:
    ctx = SimpleNamespace()
    _setup_boot_manager_boot_option(ctx)
    opt = _make_boot_option(description="X", path="\\X.efi")
    _seed_boot(store=ctx.store, option_id=0x0005, option=opt)

    decoded = ctx.manager.get_boot_option(index=0x0005)
    testbase.assert_equal(decoded.description, "X")
    return

def test_get_boot_option_rejects_out_of_range_id() -> None:
    ctx = SimpleNamespace()
    _setup_boot_manager_boot_option(ctx)
    with testbase.raises(ConfigurationError):
        ctx.manager.get_boot_option(index=-1)
    with testbase.raises(ConfigurationError):
        ctx.manager.get_boot_option(index=0x10000)
    return

def test_set_boot_option_writes_variable() -> None:
    ctx = SimpleNamespace()
    _setup_boot_manager_boot_option(ctx)
    opt = _make_boot_option(description="New", path="\\new.efi")
    ctx.manager.set_boot_option(index=0x0010, option=opt)

    testbase.assert_true(ctx.store.exists(name="Boot0010", vendor_guid=EFI_GLOBAL_VARIABLE_GUID))
    testbase.assert_equal(len(ctx.store.set_calls), 1)
    written_var, _aspects = ctx.store.set_calls[0]
    testbase.assert_equal(written_var.name, "Boot0010")
    testbase.assert_equal(written_var.attributes, BOOT_OPTION_WRITE_ATTRIBUTES)

    round_trip = BootOption.decode(data=written_var.data)
    testbase.assert_equal(round_trip, opt)
    return

def test_delete_boot_option_removes_variable() -> None:
    ctx = SimpleNamespace()
    _setup_boot_manager_boot_option(ctx)
    opt = _make_boot_option(description="Doomed", path="\\d.efi")
    _seed_boot(store=ctx.store, option_id=0x0020, option=opt)

    ctx.manager.delete_boot_option(index=0x0020)

    testbase.assert_false(ctx.store.exists(name="Boot0020", vendor_guid=EFI_GLOBAL_VARIABLE_GUID))
    testbase.assert_equal(len(ctx.store.delete_calls), 1)
    return


# ----------------------------------------------------------------------
# BootManagerBootNextTests
# ----------------------------------------------------------------------

"""
    Exercise BootNext get/set/clear including the existence-check
    required by :meth:`BootManager.set_boot_next`.
"""

def _setup_boot_manager_boot_next(ctx) -> None:
    """
        Fresh manager per test.
    """
    ctx.store = _FakeVariableStore()
    ctx.manager = BootManager.__new__(BootManager)
    ctx.manager._variable_store = ctx.store  # type: ignore[attr-defined]
    ctx.manager._aspects = DEFAULT_CMD_ASPECTS  # type: ignore[attr-defined]
    ctx.manager._vendor_guid = EFI_GLOBAL_VARIABLE_GUID  # type: ignore[attr-defined]
    import logging
    ctx.manager._logger = logging.getLogger("uefi.bootmgr.test")  # type: ignore[attr-defined]
    return

def test_get_boot_next_returns_none_when_unset() -> None:
    ctx = SimpleNamespace()
    _setup_boot_manager_boot_next(ctx)
    result = ctx.manager.get_boot_next()
    testbase.assert_true(result is None)
    return

def test_set_boot_next_requires_target() -> None:
    ctx = SimpleNamespace()
    _setup_boot_manager_boot_next(ctx)
    with testbase.raises(UefiVariableError) as raised:
        ctx.manager.set_boot_next(index=0x00AB)
    testbase.assert_equal(raised.exception.efi_status, EFI_NOT_FOUND)
    return

def test_set_boot_next_writes_when_target_exists() -> None:
    ctx = SimpleNamespace()
    _setup_boot_manager_boot_next(ctx)
    opt = _make_boot_option(description="Target", path="\\t.efi")
    _seed_boot(store=ctx.store, option_id=0x0007, option=opt)

    ctx.manager.set_boot_next(index=0x0007)

    read_back = ctx.manager.get_boot_next()
    testbase.assert_equal(read_back, 0x0007)
    return

def test_clear_boot_next_deletes_variable() -> None:
    ctx = SimpleNamespace()
    _setup_boot_manager_boot_next(ctx)
    opt = _make_boot_option(description="Target", path="\\t.efi")
    _seed_boot(store=ctx.store, option_id=0x0007, option=opt)
    ctx.manager.set_boot_next(index=0x0007)

    ctx.manager.clear_boot_next()

    testbase.assert_true(ctx.manager.get_boot_next() is None)
    return


# ----------------------------------------------------------------------
# BootManagerBootOrderTests
# ----------------------------------------------------------------------

"""
    Exercise :meth:`BootManager.get_boot_order` /
    :meth:`BootManager.set_boot_order` including the optimistic
    read-modify-write retry on concurrent change.
"""

def _setup_boot_manager_boot_order(ctx) -> None:
    """
        Fresh manager with a fast-retry aspects instance so tests
        that exercise the retry loop do not wait seconds.
    """
    ctx.store = _FakeVariableStore()
    ctx.aspects = UefiCmdAspects(
        action_pattern=ActionPattern.DO_UNTIL_SUCCESS,
        completion_timeout=2.0,
        completion_interval=0.01,
    )
    ctx.manager = BootManager.__new__(BootManager)
    ctx.manager._variable_store = ctx.store  # type: ignore[attr-defined]
    ctx.manager._aspects = ctx.aspects  # type: ignore[attr-defined]
    ctx.manager._vendor_guid = EFI_GLOBAL_VARIABLE_GUID  # type: ignore[attr-defined]
    import logging
    ctx.manager._logger = logging.getLogger("uefi.bootmgr.test")  # type: ignore[attr-defined]
    return

def test_get_boot_order_returns_empty_when_unset() -> None:
    ctx = SimpleNamespace()
    _setup_boot_manager_boot_order(ctx)
    result = ctx.manager.get_boot_order()
    testbase.assert_equal(result, tuple())
    return

def test_set_boot_order_writes_expected_bytes() -> None:
    ctx = SimpleNamespace()
    _setup_boot_manager_boot_order(ctx)
    ctx.manager.set_boot_order(indices=[0x0001, 0x0002, 0x0003])

    order = ctx.manager.get_boot_order()
    testbase.assert_equal(order, (0x0001, 0x0002, 0x0003))
    return

def test_set_boot_order_retries_on_concurrent_change() -> None:
    ctx = SimpleNamespace()
    _setup_boot_manager_boot_order(ctx)
    call_count = {"n": 0}

    def mutator(store: _FakeVariableStore, variable: UefiVariable,
                aspects: Optional[UefiCmdAspects]) -> None:
        """
            On the first write only, overwrite storage with an
            interfering value so the post-write verification fails
            and a retry is forced.
        """
        if variable.name != "BootOrder":
            return
        call_count["n"] = call_count["n"] + 1
        if call_count["n"] == 1:
            # Simulate a concurrent writer clobbering us; the fake
            # stores ``variable.data`` AFTER the mutator returns, so
            # we overwrite it AFTER the fact in a wrapper callback
            # chained via set_variable.  Simpler approach: replace
            # the payload on the variable object before storage.
            object.__setattr__(
                variable, "data",
                pack_u16_array(values=[0x0009, 0x0009]),
            )
        return

    ctx.store.pre_write_mutator = mutator

    ctx.manager.set_boot_order(indices=[0x0001, 0x0002])

    # After the retry the final stored value must match the requested order.
    final = ctx.manager.get_boot_order()
    testbase.assert_equal(final, (0x0001, 0x0002))
    # Two writes: the first was clobbered, the second succeeded.
    set_calls_boot_order = [c for c in ctx.store.set_calls if c[0].name == "BootOrder"]
    testbase.assert_true(len(set_calls_boot_order) >= 2)
    return

def test_set_boot_order_converges_without_retry_when_stable() -> None:
    ctx = SimpleNamespace()
    _setup_boot_manager_boot_order(ctx)
    ctx.manager.set_boot_order(indices=[0x0010, 0x0011])
    set_calls_boot_order = [c for c in ctx.store.set_calls if c[0].name == "BootOrder"]
    testbase.assert_equal(len(set_calls_boot_order), 1)
    return


# ----------------------------------------------------------------------
# BootManagerTimeoutTests
# ----------------------------------------------------------------------

"""
    Exercise :meth:`BootManager.get_boot_timeout` /
    :meth:`BootManager.set_boot_timeout`.
"""

def _setup_boot_manager_timeout(ctx) -> None:
    """
        Fresh manager per test.
    """
    ctx.store = _FakeVariableStore()
    ctx.manager = BootManager.__new__(BootManager)
    ctx.manager._variable_store = ctx.store  # type: ignore[attr-defined]
    ctx.manager._aspects = DEFAULT_CMD_ASPECTS  # type: ignore[attr-defined]
    ctx.manager._vendor_guid = EFI_GLOBAL_VARIABLE_GUID  # type: ignore[attr-defined]
    import logging
    ctx.manager._logger = logging.getLogger("uefi.bootmgr.test")  # type: ignore[attr-defined]
    return

def test_get_boot_timeout_returns_none_when_unset() -> None:
    ctx = SimpleNamespace()
    _setup_boot_manager_timeout(ctx)
    result = ctx.manager.get_boot_timeout()
    testbase.assert_true(result is None)
    return

def test_set_and_get_boot_timeout_round_trip() -> None:
    ctx = SimpleNamespace()
    _setup_boot_manager_timeout(ctx)
    ctx.manager.set_boot_timeout(seconds=5)
    testbase.assert_equal(ctx.manager.get_boot_timeout(), 5)
    return

def test_set_boot_timeout_rejects_out_of_range() -> None:
    ctx = SimpleNamespace()
    _setup_boot_manager_timeout(ctx)
    with testbase.raises(ConfigurationError):
        ctx.manager.set_boot_timeout(seconds=-1)
    with testbase.raises(ConfigurationError):
        ctx.manager.set_boot_timeout(seconds=0x10000)
    return


# ----------------------------------------------------------------------
# BootManagerBootCurrentTests
# ----------------------------------------------------------------------

"""
    Exercise :meth:`BootManager.get_boot_current`.
"""

def _setup_boot_manager_boot_current(ctx) -> None:
    """
        Fresh manager per test.
    """
    ctx.store = _FakeVariableStore()
    ctx.manager = BootManager.__new__(BootManager)
    ctx.manager._variable_store = ctx.store  # type: ignore[attr-defined]
    ctx.manager._aspects = DEFAULT_CMD_ASPECTS  # type: ignore[attr-defined]
    ctx.manager._vendor_guid = EFI_GLOBAL_VARIABLE_GUID  # type: ignore[attr-defined]
    import logging
    ctx.manager._logger = logging.getLogger("uefi.bootmgr.test")  # type: ignore[attr-defined]
    return

def test_get_boot_current_returns_none_when_unset() -> None:
    ctx = SimpleNamespace()
    _setup_boot_manager_boot_current(ctx)
    result = ctx.manager.get_boot_current()
    testbase.assert_true(result is None)
    return

def test_get_boot_current_decodes_stored_value() -> None:
    ctx = SimpleNamespace()
    _setup_boot_manager_boot_current(ctx)
    record = UefiVariable(
        name="BootCurrent",
        vendor_guid=EFI_GLOBAL_VARIABLE_GUID,
        attributes=UefiVariableAttributes(
            UefiVariableAttributes.BOOTSERVICE_ACCESS
            | UefiVariableAttributes.RUNTIME_ACCESS
        ),
        data=(0x0003).to_bytes(length=2, byteorder="little", signed=False),
    )
    ctx.store.put(variable=record)
    testbase.assert_equal(ctx.manager.get_boot_current(), 0x0003)
    return


# ---------------------------------------------------------------------------
# Driver#### / SysPrep#### parity
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# DriverParityTests
# ----------------------------------------------------------------------

"""
    Verify the Driver#### wrappers write to the correct variable name
    prefix and decode correctly.
"""

def _setup_driver_parity(ctx) -> None:
    """
        Fresh manager per test.
    """
    ctx.store = _FakeVariableStore()
    ctx.manager = BootManager.__new__(BootManager)
    ctx.manager._variable_store = ctx.store  # type: ignore[attr-defined]
    ctx.manager._aspects = DEFAULT_CMD_ASPECTS  # type: ignore[attr-defined]
    ctx.manager._vendor_guid = EFI_GLOBAL_VARIABLE_GUID  # type: ignore[attr-defined]
    import logging
    ctx.manager._logger = logging.getLogger("uefi.bootmgr.test")  # type: ignore[attr-defined]
    return

def test_set_driver_option_uses_driver_prefix() -> None:
    ctx = SimpleNamespace()
    _setup_driver_parity(ctx)
    opt = _make_boot_option(description="NicDriver", path="\\drivers\\nic.efi")
    ctx.manager.set_driver_option(index=0x0002, option=opt)

    testbase.assert_true(ctx.store.exists(name="Driver0002", vendor_guid=EFI_GLOBAL_VARIABLE_GUID))
    return

def test_list_driver_options_enumerates_driver_prefix() -> None:
    ctx = SimpleNamespace()
    _setup_driver_parity(ctx)
    opt = _make_boot_option(description="Drv", path="\\d.efi")
    record = UefiVariable(
        name="Driver0001",
        vendor_guid=EFI_GLOBAL_VARIABLE_GUID,
        attributes=BOOT_OPTION_WRITE_ATTRIBUTES,
        data=opt.encode(),
    )
    ctx.store.put(variable=record)

    options = ctx.manager.list_driver_options()
    testbase.assert_true(0x0001 in options)
    return

def test_driver_order_round_trip() -> None:
    ctx = SimpleNamespace()
    _setup_driver_parity(ctx)
    ctx.manager.set_driver_order(indices=[0x0001, 0x0002])
    order = ctx.manager.get_driver_order()
    testbase.assert_equal(order, (0x0001, 0x0002))
    testbase.assert_true(ctx.store.exists(name="DriverOrder", vendor_guid=EFI_GLOBAL_VARIABLE_GUID))
    return


# ----------------------------------------------------------------------
# SysPrepParityTests
# ----------------------------------------------------------------------

"""
    Verify the SysPrep#### wrappers write to the correct variable
    name prefix and decode correctly.
"""

def _setup_sys_prep_parity(ctx) -> None:
    """
        Fresh manager per test.
    """
    ctx.store = _FakeVariableStore()
    ctx.manager = BootManager.__new__(BootManager)
    ctx.manager._variable_store = ctx.store  # type: ignore[attr-defined]
    ctx.manager._aspects = DEFAULT_CMD_ASPECTS  # type: ignore[attr-defined]
    ctx.manager._vendor_guid = EFI_GLOBAL_VARIABLE_GUID  # type: ignore[attr-defined]
    import logging
    ctx.manager._logger = logging.getLogger("uefi.bootmgr.test")  # type: ignore[attr-defined]
    return

def test_set_sys_prep_option_uses_syspprep_prefix() -> None:
    ctx = SimpleNamespace()
    _setup_sys_prep_parity(ctx)
    opt = _make_boot_option(description="Prep", path="\\prep.efi")
    ctx.manager.set_sys_prep_option(index=0x0001, option=opt)

    testbase.assert_true(ctx.store.exists(name="SysPrep0001", vendor_guid=EFI_GLOBAL_VARIABLE_GUID))
    return

def test_list_sys_prep_options_enumerates_sys_prep_prefix() -> None:
    ctx = SimpleNamespace()
    _setup_sys_prep_parity(ctx)
    opt = _make_boot_option(description="Prep", path="\\p.efi")
    record = UefiVariable(
        name="SysPrep0003",
        vendor_guid=EFI_GLOBAL_VARIABLE_GUID,
        attributes=BOOT_OPTION_WRITE_ATTRIBUTES,
        data=opt.encode(),
    )
    ctx.store.put(variable=record)

    options = ctx.manager.list_sys_prep_options()
    testbase.assert_true(0x0003 in options)
    return

def test_sys_prep_order_round_trip() -> None:
    ctx = SimpleNamespace()
    _setup_sys_prep_parity(ctx)
    ctx.manager.set_sys_prep_order(indices=[0x0011, 0x0012])
    order = ctx.manager.get_sys_prep_order()
    testbase.assert_equal(order, (0x0011, 0x0012))
    testbase.assert_true(ctx.store.exists(name="SysPrepOrder", vendor_guid=EFI_GLOBAL_VARIABLE_GUID))
    return


# ---------------------------------------------------------------------------
# ActionPattern semantic rejection
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# ActionPatternSemanticTests
# ----------------------------------------------------------------------

"""
    Verify mutating operations reject action patterns that cannot
    possibly succeed.
"""

def _setup_action_pattern_semantic(ctx) -> None:
    """
        Fresh manager per test.
    """
    ctx.store = _FakeVariableStore()
    ctx.manager = BootManager.__new__(BootManager)
    ctx.manager._variable_store = ctx.store  # type: ignore[attr-defined]
    ctx.manager._aspects = DEFAULT_CMD_ASPECTS  # type: ignore[attr-defined]
    ctx.manager._vendor_guid = EFI_GLOBAL_VARIABLE_GUID  # type: ignore[attr-defined]
    import logging
    ctx.manager._logger = logging.getLogger("uefi.bootmgr.test")  # type: ignore[attr-defined]
    return

def test_do_while_success_rejected_on_set_boot_option() -> None:
    ctx = SimpleNamespace()
    _setup_action_pattern_semantic(ctx)
    opt = _make_boot_option(description="X", path="\\x.efi")
    bad = UefiCmdAspects(action_pattern=ActionPattern.DO_WHILE_SUCCESS)
    with testbase.raises(SemanticError):
        ctx.manager.set_boot_option(index=0x0001, option=opt, aspects=bad)
    return

def test_do_until_connection_failure_rejected_on_set_boot_order() -> None:
    ctx = SimpleNamespace()
    _setup_action_pattern_semantic(ctx)
    bad = UefiCmdAspects(action_pattern=ActionPattern.DO_UNTIL_CONNECTION_FAILURE)
    with testbase.raises(SemanticError):
        ctx.manager.set_boot_order(indices=[0x0001], aspects=bad)
    return
