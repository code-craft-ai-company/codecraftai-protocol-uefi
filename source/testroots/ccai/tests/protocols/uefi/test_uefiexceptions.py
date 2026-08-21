"""
    .. module:: test_uefiexceptions
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Unit tests for the UEFI exception hierarchy.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Code Craft AI LLC"
__credits__ = []


from ccai import testbase

import os
import pickle
import uuid

from ccai.errors.exceptions import ProtocolError, SemanticError

from ccai.protocols.uefi.uefiexceptions import (
    SHELL_ERROR_OUTPUT_PREVIEW_LINES,
    UefiCapsuleError,
    UefiError,
    UefiProtocolError,
    UefiSecureBootError,
    UefiShellError,
    UefiTransportError,
    UefiVariableError,
)


# ----------------------------------------------------------------------
# UefiErrorTestCase
# ----------------------------------------------------------------------


def test_uefi_error_message_only() -> None:
    exc = UefiError("boom")
    testbase.assert_equal(str(exc), "boom")
    testbase.assert_equal(exc.details, [])
    return

def test_uefi_error_details_list_is_copied() -> None:
    caller_list = ["a", "b"]
    exc = UefiError("boom", details=caller_list)
    caller_list.append("c")
    testbase.assert_equal(exc.details, ["a", "b"])
    return

def test_uefi_error_details_non_list_raises() -> None:
    with testbase.raises(TypeError):
        UefiError("boom", details="nope")  # type: ignore[arg-type]
    return

def test_uefi_error_str_joins_with_linesep() -> None:
    exc = UefiError("boom", details=["detail1", "detail2"])
    expected = "boom" + os.linesep + "detail1" + os.linesep + "detail2"
    testbase.assert_equal(str(exc), expected)
    return

def test_uefi_error_as_dict() -> None:
    exc = UefiError("boom", details=["detail1"])
    payload = exc.as_dict()
    testbase.assert_equal(payload["type"], "UefiError")
    testbase.assert_equal(payload["message"], "boom")
    testbase.assert_equal(payload["details"], ["detail1"])
    return


# ----------------------------------------------------------------------
# UefiShellErrorTestCase
# ----------------------------------------------------------------------


def test_uefi_shell_error_defaults() -> None:
    exc = UefiShellError("failed")
    testbase.assert_true(exc.command is None)
    testbase.assert_true(exc.status is None)
    testbase.assert_true(exc.stdout is None)
    testbase.assert_true(exc.stderr is None)
    return

def test_uefi_shell_error_non_int_status_raises() -> None:
    with testbase.raises(TypeError):
        UefiShellError("failed", status="0")  # type: ignore[arg-type]
    return

def test_uefi_shell_error_non_str_stdout_raises() -> None:
    with testbase.raises(TypeError):
        UefiShellError("failed", stdout=123)  # type: ignore[arg-type]
    return

def test_uefi_shell_error_str_includes_context() -> None:
    exc = UefiShellError("failed", command="ls FS0:", status=2,
                         stdout="out", stderr="err")
    text = str(exc)
    testbase.assert_true("COMMAND: ls FS0:" in text)
    testbase.assert_true("STATUS: 2" in text)
    testbase.assert_true("STDOUT:" in text)
    testbase.assert_true("STDERR:" in text)
    testbase.assert_true("out" in text)
    testbase.assert_true("err" in text)
    return

def test_uefi_shell_error_stdout_truncation() -> None:
    big = os.linesep.join(["line{}".format(i) for i in range(SHELL_ERROR_OUTPUT_PREVIEW_LINES + 5)])
    exc = UefiShellError("big", stdout=big)
    text = str(exc)
    testbase.assert_true("more lines" in text)
    return

def test_uefi_shell_error_as_dict() -> None:
    exc = UefiShellError("failed", command="echo", status=1,
                         stdout="ok", stderr="oops")
    payload = exc.as_dict()
    testbase.assert_equal(payload["command"], "echo")
    testbase.assert_equal(payload["status"], 1)
    testbase.assert_equal(payload["stdout"], "ok")
    testbase.assert_equal(payload["stderr"], "oops")
    return

def test_uefi_shell_error_is_subclass_of_uefi_error() -> None:
    testbase.assert_true(issubclass(UefiShellError, UefiError))
    return


# ----------------------------------------------------------------------
# UefiVariableErrorTestCase
# ----------------------------------------------------------------------


def test_uefi_variable_error_defaults() -> None:
    exc = UefiVariableError("failed")
    testbase.assert_true(exc.name is None)
    testbase.assert_true(exc.vendor_guid is None)
    testbase.assert_true(exc.attributes is None)
    testbase.assert_true(exc.efi_status is None)
    return

def test_uefi_variable_error_non_uuid_guid_raises() -> None:
    with testbase.raises(TypeError):
        UefiVariableError("failed", vendor_guid="not-a-uuid")  # type: ignore[arg-type]
    return

def test_uefi_variable_error_non_int_efi_status_raises() -> None:
    with testbase.raises(TypeError):
        UefiVariableError("failed", efi_status="bad")  # type: ignore[arg-type]
    return

def test_uefi_variable_error_non_int_attributes_raises() -> None:
    with testbase.raises(TypeError):
        UefiVariableError("failed", attributes="bad")  # type: ignore[arg-type]
    return

def test_uefi_variable_error_str_includes_context() -> None:
    guid = uuid.UUID("8be4df61-93ca-11d2-aa0d-00e098032b8c")
    exc = UefiVariableError("write failed", name="BootOrder",
                            vendor_guid=guid, attributes=0x07,
                            efi_status=0x8000000000000002)
    text = str(exc)
    testbase.assert_true("NAME: BootOrder" in text)
    testbase.assert_true(str(guid) in text)
    testbase.assert_true("ATTRIBUTES: 0x" in text)
    testbase.assert_true("EFI STATUS: 0x" in text)
    return

def test_uefi_variable_error_as_dict_renders_guid_as_string() -> None:
    guid = uuid.UUID("8be4df61-93ca-11d2-aa0d-00e098032b8c")
    exc = UefiVariableError("failed", vendor_guid=guid)
    payload = exc.as_dict()
    testbase.assert_equal(payload["vendor_guid"], str(guid))
    return


# ----------------------------------------------------------------------
# TypedSubclassTestCase
# ----------------------------------------------------------------------


def test_all_inherit_from_uefi_error() -> None:
    for cls in (UefiCapsuleError, UefiSecureBootError, UefiTransportError, UefiProtocolError):
        testbase.assert_true(issubclass(cls, UefiError))
    return

def test_protocol_error_multi_inherits() -> None:
    testbase.assert_true(issubclass(UefiProtocolError, ProtocolError))
    return

def test_each_subclass_constructs_with_only_message() -> None:
    for cls in (UefiCapsuleError, UefiSecureBootError, UefiTransportError, UefiProtocolError):
        exc = cls("boom")
        testbase.assert_equal(str(exc), "boom")
        payload = exc.as_dict()
        testbase.assert_equal(payload["type"], cls.__name__)
    return


# ----------------------------------------------------------------------
# SemanticErrorRuleTestCase
# ----------------------------------------------------------------------


def test_no_subclass_of_semantic_error() -> None:
    from ccai.protocols.uefi import uefiexceptions
    all_names = uefiexceptions.__all__
    for name in all_names:
        obj = getattr(uefiexceptions, name)
        if isinstance(obj, type):
            testbase.assert_false(issubclass(obj, SemanticError),
                             "Class {} must not subclass SemanticError".format(name))
    return


# ----------------------------------------------------------------------
# PickleTestCase
# ----------------------------------------------------------------------


def test_pickle_roundtrip_uefi_error() -> None:
    exc = UefiError("boom", details=["d"])
    blob = pickle.dumps(exc)
    restored = pickle.loads(blob)
    testbase.assert_equal(str(restored), str(exc))
    testbase.assert_equal(restored.as_dict(), exc.as_dict())
    return

def test_pickle_roundtrip_shell_error() -> None:
    exc = UefiShellError("failed", command="x", status=1,
                         stdout="o", stderr="e")
    blob = pickle.dumps(exc)
    restored = pickle.loads(blob)
    testbase.assert_equal(restored.command, "x")
    testbase.assert_equal(restored.status, 1)
    return
