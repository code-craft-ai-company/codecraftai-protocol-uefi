"""
    .. module:: test_uefiinterfaces
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Unit tests for the :class:`typing.Protocol` based
               structural-typing contracts in
               :mod:`ccai.protocols.uefi.uefiinterfaces`.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Code Craft AI LLC"
__credits__ = []


from ccai import testbase

import uuid

from typing import Iterator, Optional, Tuple

from ccai.protocols.uefi.uefiaspects import UefiCmdAspects

from ccai.protocols.uefi.uefiinterfaces import (
    IUefiFileProvider,
    IUefiShellRunner,
    IUefiSystemContext,
    IUefiTransport,
    IUefiVariableProvider,
)


# ---------------------------------------------------------------------------
# Stub implementations that fully satisfy each protocol
# ---------------------------------------------------------------------------

class _FullTransport:
    """
        Stub that fully implements :class:`IUefiTransport`.
    """

    def __init__(self) -> None:
        self._connected = False
        return

    @property
    def connected(self) -> bool:
        rtnval = self._connected
        return rtnval

    def connect(self, aspects: UefiCmdAspects) -> None:
        self._connected = True
        return

    def disconnect(self) -> None:
        self._connected = False
        return

    def send(self, data: bytes) -> int:
        rtnval = len(data)
        return rtnval

    def recv(self, max_bytes: int, timeout: float) -> bytes:
        rtnval = b""
        return rtnval


class _FullShellRunner:
    """
        Stub that fully implements :class:`IUefiShellRunner`.
    """

    def run_cmd(self, command: str, aspects: UefiCmdAspects) -> Tuple[int, str, str]:
        rtnval = (0, "", "")
        return rtnval


class _FullVariableProvider:
    """
        Stub that fully implements :class:`IUefiVariableProvider`.
    """

    def get_variable(self, name: str, vendor_guid: uuid.UUID) -> Tuple[int, bytes]:
        rtnval = (0, b"")
        return rtnval

    def set_variable(self, name: str, vendor_guid: uuid.UUID, attributes: int, data: bytes) -> None:
        return

    def enumerate_variables(self) -> Iterator[Tuple[str, uuid.UUID]]:
        empty: Tuple[Tuple[str, uuid.UUID], ...] = ()
        rtnval = iter(empty)
        return rtnval


class _FullFileProvider:
    """
        Stub that fully implements :class:`IUefiFileProvider`.
    """

    def file_push(self, local: str, remote: str) -> None:
        return

    def file_pull(self, remote: str, local: str) -> None:
        return

    def file_exists(self, path: str) -> bool:
        rtnval = False
        return rtnval

    def directory_exists(self, path: str) -> bool:
        rtnval = False
        return rtnval


class _FullSystemContext:
    """
        Stub that fully implements :class:`IUefiSystemContext`.
    """

    @property
    def host(self) -> str:
        rtnval = "test-host"
        return rtnval

    @property
    def firmware_vendor(self) -> Optional[str]:
        rtnval: Optional[str] = None
        return rtnval

    @property
    def firmware_revision(self) -> Optional[int]:
        rtnval: Optional[int] = None
        return rtnval

    @property
    def secure_boot_state(self) -> Optional[str]:
        rtnval: Optional[str] = None
        return rtnval

    def verify_connectivity(self) -> bool:
        rtnval = True
        return rtnval


# ---------------------------------------------------------------------------
# Stub implementations that are missing one required member
# ---------------------------------------------------------------------------

class _PartialTransport:
    """
        Transport stub missing :meth:`recv`.
    """

    def __init__(self) -> None:
        self._connected = False
        return

    @property
    def connected(self) -> bool:
        rtnval = self._connected
        return rtnval

    def connect(self, aspects: UefiCmdAspects) -> None:
        return

    def disconnect(self) -> None:
        return

    def send(self, data: bytes) -> int:
        rtnval = 0
        return rtnval


class _PartialShellRunner:
    """
        Shell runner stub with no :meth:`run_cmd` method.
    """

    def some_other_method(self) -> None:
        return


class _PartialVariableProvider:
    """
        Variable provider stub missing :meth:`enumerate_variables`.
    """

    def get_variable(self, name: str, vendor_guid: uuid.UUID) -> Tuple[int, bytes]:
        rtnval = (0, b"")
        return rtnval

    def set_variable(self, name: str, vendor_guid: uuid.UUID, attributes: int, data: bytes) -> None:
        return


class _PartialFileProvider:
    """
        File provider stub missing :meth:`directory_exists`.
    """

    def file_push(self, local: str, remote: str) -> None:
        return

    def file_pull(self, remote: str, local: str) -> None:
        return

    def file_exists(self, path: str) -> bool:
        rtnval = False
        return rtnval


class _PartialSystemContext:
    """
        System context stub missing :meth:`verify_connectivity`.
    """

    @property
    def host(self) -> str:
        rtnval = "test-host"
        return rtnval

    @property
    def firmware_vendor(self) -> Optional[str]:
        rtnval: Optional[str] = None
        return rtnval

    @property
    def firmware_revision(self) -> Optional[int]:
        rtnval: Optional[int] = None
        return rtnval

    @property
    def secure_boot_state(self) -> Optional[str]:
        rtnval: Optional[str] = None
        return rtnval


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# IUefiTransportProtocolTestCase
# ----------------------------------------------------------------------


def test_i_uefi_transport_protocol_full_stub_is_instance() -> None:
    stub = _FullTransport()
    result = isinstance(stub, IUefiTransport)
    testbase.assert_true(result)
    return

def test_i_uefi_transport_protocol_partial_stub_is_not_instance() -> None:
    stub = _PartialTransport()
    result = isinstance(stub, IUefiTransport)
    testbase.assert_false(result)
    return


# ----------------------------------------------------------------------
# IUefiShellRunnerProtocolTestCase
# ----------------------------------------------------------------------


def test_i_uefi_shell_runner_protocol_full_stub_is_instance() -> None:
    stub = _FullShellRunner()
    result = isinstance(stub, IUefiShellRunner)
    testbase.assert_true(result)
    return

def test_i_uefi_shell_runner_protocol_partial_stub_is_not_instance() -> None:
    stub = _PartialShellRunner()
    result = isinstance(stub, IUefiShellRunner)
    testbase.assert_false(result)
    return


# ----------------------------------------------------------------------
# IUefiVariableProviderProtocolTestCase
# ----------------------------------------------------------------------


def test_i_uefi_variable_provider_protocol_full_stub_is_instance() -> None:
    stub = _FullVariableProvider()
    result = isinstance(stub, IUefiVariableProvider)
    testbase.assert_true(result)
    return

def test_i_uefi_variable_provider_protocol_partial_stub_is_not_instance() -> None:
    stub = _PartialVariableProvider()
    result = isinstance(stub, IUefiVariableProvider)
    testbase.assert_false(result)
    return


# ----------------------------------------------------------------------
# IUefiFileProviderProtocolTestCase
# ----------------------------------------------------------------------


def test_i_uefi_file_provider_protocol_full_stub_is_instance() -> None:
    stub = _FullFileProvider()
    result = isinstance(stub, IUefiFileProvider)
    testbase.assert_true(result)
    return

def test_i_uefi_file_provider_protocol_partial_stub_is_not_instance() -> None:
    stub = _PartialFileProvider()
    result = isinstance(stub, IUefiFileProvider)
    testbase.assert_false(result)
    return


# ----------------------------------------------------------------------
# IUefiSystemContextProtocolTestCase
# ----------------------------------------------------------------------


def test_i_uefi_system_context_protocol_full_stub_is_instance() -> None:
    stub = _FullSystemContext()
    result = isinstance(stub, IUefiSystemContext)
    testbase.assert_true(result)
    return

def test_i_uefi_system_context_protocol_partial_stub_is_not_instance() -> None:
    stub = _PartialSystemContext()
    result = isinstance(stub, IUefiSystemContext)
    testbase.assert_false(result)
    return
