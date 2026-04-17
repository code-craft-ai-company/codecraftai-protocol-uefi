"""
    .. module:: test_uefiexceptions
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Unit tests for the UEFI exception hierarchy.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Automation Mojo LLC"
__credits__ = []


import os
import pickle
import unittest
import uuid

from automojo.errors.exceptions import ProtocolError, SemanticError

from automojo.protocols.uefi.uefiexceptions import (
    SHELL_ERROR_OUTPUT_PREVIEW_LINES,
    UefiCapsuleError,
    UefiError,
    UefiProtocolError,
    UefiSecureBootError,
    UefiShellError,
    UefiTransportError,
    UefiVariableError,
)


class UefiErrorTestCase(unittest.TestCase):

    def test_message_only(self) -> None:
        exc = UefiError("boom")
        self.assertEqual(str(exc), "boom")
        self.assertEqual(exc.details, [])
        return

    def test_details_list_is_copied(self) -> None:
        caller_list = ["a", "b"]
        exc = UefiError("boom", details=caller_list)
        caller_list.append("c")
        self.assertEqual(exc.details, ["a", "b"])
        return

    def test_details_non_list_raises(self) -> None:
        with self.assertRaises(TypeError):
            UefiError("boom", details="nope")  # type: ignore[arg-type]
        return

    def test_str_joins_with_linesep(self) -> None:
        exc = UefiError("boom", details=["detail1", "detail2"])
        expected = "boom" + os.linesep + "detail1" + os.linesep + "detail2"
        self.assertEqual(str(exc), expected)
        return

    def test_as_dict(self) -> None:
        exc = UefiError("boom", details=["detail1"])
        payload = exc.as_dict()
        self.assertEqual(payload["type"], "UefiError")
        self.assertEqual(payload["message"], "boom")
        self.assertEqual(payload["details"], ["detail1"])
        return


class UefiShellErrorTestCase(unittest.TestCase):

    def test_defaults(self) -> None:
        exc = UefiShellError("failed")
        self.assertIsNone(exc.command)
        self.assertIsNone(exc.status)
        self.assertIsNone(exc.stdout)
        self.assertIsNone(exc.stderr)
        return

    def test_non_int_status_raises(self) -> None:
        with self.assertRaises(TypeError):
            UefiShellError("failed", status="0")  # type: ignore[arg-type]
        return

    def test_non_str_stdout_raises(self) -> None:
        with self.assertRaises(TypeError):
            UefiShellError("failed", stdout=123)  # type: ignore[arg-type]
        return

    def test_str_includes_context(self) -> None:
        exc = UefiShellError("failed", command="ls FS0:", status=2,
                             stdout="out", stderr="err")
        text = str(exc)
        self.assertIn("COMMAND: ls FS0:", text)
        self.assertIn("STATUS: 2", text)
        self.assertIn("STDOUT:", text)
        self.assertIn("STDERR:", text)
        self.assertIn("out", text)
        self.assertIn("err", text)
        return

    def test_stdout_truncation(self) -> None:
        big = os.linesep.join(["line{}".format(i) for i in range(SHELL_ERROR_OUTPUT_PREVIEW_LINES + 5)])
        exc = UefiShellError("big", stdout=big)
        text = str(exc)
        self.assertIn("more lines", text)
        return

    def test_as_dict(self) -> None:
        exc = UefiShellError("failed", command="echo", status=1,
                             stdout="ok", stderr="oops")
        payload = exc.as_dict()
        self.assertEqual(payload["command"], "echo")
        self.assertEqual(payload["status"], 1)
        self.assertEqual(payload["stdout"], "ok")
        self.assertEqual(payload["stderr"], "oops")
        return

    def test_is_subclass_of_uefi_error(self) -> None:
        self.assertTrue(issubclass(UefiShellError, UefiError))
        return


class UefiVariableErrorTestCase(unittest.TestCase):

    def test_defaults(self) -> None:
        exc = UefiVariableError("failed")
        self.assertIsNone(exc.name)
        self.assertIsNone(exc.vendor_guid)
        self.assertIsNone(exc.attributes)
        self.assertIsNone(exc.efi_status)
        return

    def test_non_uuid_guid_raises(self) -> None:
        with self.assertRaises(TypeError):
            UefiVariableError("failed", vendor_guid="not-a-uuid")  # type: ignore[arg-type]
        return

    def test_non_int_efi_status_raises(self) -> None:
        with self.assertRaises(TypeError):
            UefiVariableError("failed", efi_status="bad")  # type: ignore[arg-type]
        return

    def test_non_int_attributes_raises(self) -> None:
        with self.assertRaises(TypeError):
            UefiVariableError("failed", attributes="bad")  # type: ignore[arg-type]
        return

    def test_str_includes_context(self) -> None:
        guid = uuid.UUID("8be4df61-93ca-11d2-aa0d-00e098032b8c")
        exc = UefiVariableError("write failed", name="BootOrder",
                                vendor_guid=guid, attributes=0x07,
                                efi_status=0x8000000000000002)
        text = str(exc)
        self.assertIn("NAME: BootOrder", text)
        self.assertIn(str(guid), text)
        self.assertIn("ATTRIBUTES: 0x", text)
        self.assertIn("EFI STATUS: 0x", text)
        return

    def test_as_dict_renders_guid_as_string(self) -> None:
        guid = uuid.UUID("8be4df61-93ca-11d2-aa0d-00e098032b8c")
        exc = UefiVariableError("failed", vendor_guid=guid)
        payload = exc.as_dict()
        self.assertEqual(payload["vendor_guid"], str(guid))
        return


class TypedSubclassTestCase(unittest.TestCase):

    def test_all_inherit_from_uefi_error(self) -> None:
        for cls in (UefiCapsuleError, UefiSecureBootError, UefiTransportError, UefiProtocolError):
            self.assertTrue(issubclass(cls, UefiError))
        return

    def test_protocol_error_multi_inherits(self) -> None:
        self.assertTrue(issubclass(UefiProtocolError, ProtocolError))
        return

    def test_each_subclass_constructs_with_only_message(self) -> None:
        for cls in (UefiCapsuleError, UefiSecureBootError, UefiTransportError, UefiProtocolError):
            exc = cls("boom")
            self.assertEqual(str(exc), "boom")
            payload = exc.as_dict()
            self.assertEqual(payload["type"], cls.__name__)
        return


class SemanticErrorRuleTestCase(unittest.TestCase):

    def test_no_subclass_of_semantic_error(self) -> None:
        from automojo.protocols.uefi import uefiexceptions
        all_names = uefiexceptions.__all__
        for name in all_names:
            obj = getattr(uefiexceptions, name)
            if isinstance(obj, type):
                self.assertFalse(issubclass(obj, SemanticError),
                                 "Class {} must not subclass SemanticError".format(name))
        return


class PickleTestCase(unittest.TestCase):

    def test_pickle_roundtrip_uefi_error(self) -> None:
        exc = UefiError("boom", details=["d"])
        blob = pickle.dumps(exc)
        restored = pickle.loads(blob)
        self.assertEqual(str(restored), str(exc))
        self.assertEqual(restored.as_dict(), exc.as_dict())
        return

    def test_pickle_roundtrip_shell_error(self) -> None:
        exc = UefiShellError("failed", command="x", status=1,
                             stdout="o", stderr="e")
        blob = pickle.dumps(exc)
        restored = pickle.loads(blob)
        self.assertEqual(restored.command, "x")
        self.assertEqual(restored.status, 1)
        return


if __name__ == "__main__":
    unittest.main()
