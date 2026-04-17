"""
    .. module:: test_ueficonst
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Unit tests for the shared UEFI constants module, covering
               GUID binary round-trip helpers, the :class:`UefiJumpParams`
               frozen dataclass, and the ``os.linesep``-aware command-output
               templates.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Automation Mojo LLC"
__credits__ = []


import dataclasses
import os
import re
import unittest
import uuid

from automojo.protocols.uefi.ueficonst import (
    DEFAULT_CHUNK_SIZE,
    DEFAULT_INTERACTIVE_SENTINEL,
    DEFAULT_INTERACTIVE_SENTINEL_STR,
    DEFAULT_RECONNECT_DELAY,
    DEFAULT_REDFISH_TIMEOUT,
    DEFAULT_SHELL_PROMPT_PATTERN,
    DEFAULT_SHELL_TIMEOUT,
    DEFAULT_UEFI_RETRY_INTERVAL,
    DEFAULT_UEFI_TIMEOUT,
    INTERACTIVE_PROMPT,
    INTERACTIVE_PROMPT_BYTES,
    REGEX_GUID_STRING,
    REGEX_SHELL_PROMPT_LINE,
    TEMPLATE_COMMAND_FAILURE,
    TEMPLATE_COMMAND_SUCCESS,
    TRANSPORT_KINDS,
    TRANSPORT_KIND_IPMI_SOL,
    TRANSPORT_KIND_REDFISH,
    TRANSPORT_KIND_SERIAL,
    TRANSPORT_KIND_SSH_HOST,
    UefiJumpParams,
    guid_from_bytes,
    guid_to_bytes,
)


# ---------------------------------------------------------------------------
# Canonical UEFI-spec test vector
#
# The UEFI specification documents the mixed-endian layout with this example:
# the canonical GUID string "aabbccdd-eeff-0011-2233-445566778899" is stored
# on disk as the bytes:
#     DD CC BB AA  FF EE  11 00  22 33 44 55 66 77 88 99
# ---------------------------------------------------------------------------

CANONICAL_GUID_STRING = "aabbccdd-eeff-0011-2233-445566778899"
CANONICAL_GUID_BYTES = bytes([
    0xDD, 0xCC, 0xBB, 0xAA,
    0xFF, 0xEE,
    0x11, 0x00,
    0x22, 0x33,
    0x44, 0x55, 0x66, 0x77, 0x88, 0x99,
])


class NumericDefaultsTestCase(unittest.TestCase):

    def test_default_uefi_timeout_is_positive_int(self) -> None:
        self.assertIsInstance(DEFAULT_UEFI_TIMEOUT, int)
        self.assertGreater(DEFAULT_UEFI_TIMEOUT, 0)
        return

    def test_default_uefi_retry_interval_is_positive_float(self) -> None:
        self.assertIsInstance(DEFAULT_UEFI_RETRY_INTERVAL, float)
        self.assertGreater(DEFAULT_UEFI_RETRY_INTERVAL, 0)
        return

    def test_default_shell_timeout_is_positive_int(self) -> None:
        self.assertIsInstance(DEFAULT_SHELL_TIMEOUT, int)
        self.assertGreater(DEFAULT_SHELL_TIMEOUT, 0)
        return

    def test_default_redfish_timeout_is_positive_int(self) -> None:
        self.assertIsInstance(DEFAULT_REDFISH_TIMEOUT, int)
        self.assertGreater(DEFAULT_REDFISH_TIMEOUT, 0)
        return

    def test_default_reconnect_delay_is_positive_float(self) -> None:
        self.assertIsInstance(DEFAULT_RECONNECT_DELAY, float)
        self.assertGreater(DEFAULT_RECONNECT_DELAY, 0)
        return

    def test_default_chunk_size_is_positive_int(self) -> None:
        self.assertIsInstance(DEFAULT_CHUNK_SIZE, int)
        self.assertGreater(DEFAULT_CHUNK_SIZE, 0)
        return


class TransportKindsTestCase(unittest.TestCase):

    def test_frozenset_type(self) -> None:
        self.assertIsInstance(TRANSPORT_KINDS, frozenset)
        return

    def test_frozenset_membership(self) -> None:
        expected = {
            TRANSPORT_KIND_SERIAL,
            TRANSPORT_KIND_REDFISH,
            TRANSPORT_KIND_SSH_HOST,
            TRANSPORT_KIND_IPMI_SOL,
        }
        self.assertEqual(TRANSPORT_KINDS, expected)
        return

    def test_frozenset_rejects_mutation(self) -> None:
        with self.assertRaises(AttributeError):
            TRANSPORT_KINDS.add("nope")  # type: ignore[attr-defined]
        return


class InteractivePromptTestCase(unittest.TestCase):

    def test_bytes_and_str_agree(self) -> None:
        self.assertEqual(INTERACTIVE_PROMPT_BYTES, INTERACTIVE_PROMPT.encode("utf-8"))
        return

    def test_sentinel_aliases_interactive_prompt(self) -> None:
        self.assertEqual(DEFAULT_INTERACTIVE_SENTINEL, INTERACTIVE_PROMPT_BYTES)
        self.assertEqual(DEFAULT_INTERACTIVE_SENTINEL_STR, INTERACTIVE_PROMPT)
        return


class PromptRegexTestCase(unittest.TestCase):

    def test_shell_prompt_bytes_regex_compiles(self) -> None:
        compiled = re.compile(DEFAULT_SHELL_PROMPT_PATTERN)
        match = compiled.search(b"FS0:\\> ")
        self.assertIsNotNone(match)
        match = compiled.search(b"Shell> ")
        self.assertIsNotNone(match)
        return

    def test_shell_prompt_line_regex_rejects_non_prompt(self) -> None:
        match = REGEX_SHELL_PROMPT_LINE.match("not a prompt")
        self.assertIsNone(match)
        return

    def test_guid_regex_accepts_canonical_strings(self) -> None:
        match = REGEX_GUID_STRING.match(CANONICAL_GUID_STRING)
        self.assertIsNotNone(match)
        return

    def test_guid_regex_rejects_malformed_string(self) -> None:
        match = REGEX_GUID_STRING.match("not-a-guid")
        self.assertIsNone(match)
        return


class CommandTemplatesTestCase(unittest.TestCase):

    def test_success_template_contains_linesep(self) -> None:
        self.assertIn(os.linesep, TEMPLATE_COMMAND_SUCCESS)
        return

    def test_failure_template_contains_linesep(self) -> None:
        self.assertIn(os.linesep, TEMPLATE_COMMAND_FAILURE)
        return

    def test_success_template_has_no_hardcoded_lf_when_linesep_differs(self) -> None:
        # Skip the assertion on POSIX platforms where os.linesep IS "\n".
        if os.linesep == "\n":
            self.skipTest("os.linesep is '\\n' on this platform; hardcoded-LF check is meaningless.")
            return

        self.assertNotIn("\n", TEMPLATE_COMMAND_SUCCESS.replace(os.linesep, ""))
        return

    def test_failure_template_has_no_hardcoded_lf_when_linesep_differs(self) -> None:
        if os.linesep == "\n":
            self.skipTest("os.linesep is '\\n' on this platform; hardcoded-LF check is meaningless.")
            return

        self.assertNotIn("\n", TEMPLATE_COMMAND_FAILURE.replace(os.linesep, ""))
        return

    def test_success_template_formats(self) -> None:
        rendered = TEMPLATE_COMMAND_SUCCESS % ("TAG", "ver", "hello", "")
        self.assertIn("CMD=ver", rendered)
        self.assertIn("STDOUT:", rendered)
        self.assertIn("STDERR:", rendered)
        self.assertTrue(rendered.endswith(os.linesep))
        return

    def test_failure_template_formats(self) -> None:
        rendered = TEMPLATE_COMMAND_FAILURE % ("TAG", "ver", 1, "out", "err")
        self.assertIn("STATUS=1", rendered)
        self.assertIn("CMD=ver", rendered)
        self.assertTrue(rendered.endswith(os.linesep))
        return


class GuidToBytesTestCase(unittest.TestCase):

    def test_canonical_vector(self) -> None:
        g = uuid.UUID(CANONICAL_GUID_STRING)
        result = guid_to_bytes(g=g)
        self.assertEqual(result, CANONICAL_GUID_BYTES)
        return

    def test_returns_sixteen_bytes(self) -> None:
        g = uuid.UUID("8be4df61-93ca-11d2-aa0d-00e098032b8c")
        result = guid_to_bytes(g=g)
        self.assertEqual(len(result), 16)
        self.assertIsInstance(result, bytes)
        return

    def test_non_uuid_input_raises_type_error(self) -> None:
        with self.assertRaises(TypeError) as ctx:
            guid_to_bytes(g="not-a-uuid")  # type: ignore[arg-type]
        message = str(ctx.exception)
        self.assertIn("uuid.UUID", message)
        self.assertIn("str", message)
        return


class GuidFromBytesTestCase(unittest.TestCase):

    def test_canonical_vector(self) -> None:
        result = guid_from_bytes(b=CANONICAL_GUID_BYTES)
        self.assertEqual(result, uuid.UUID(CANONICAL_GUID_STRING))
        return

    def test_wrong_length_raises_value_error_with_length(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            guid_from_bytes(b=b"\x00" * 8)
        message = str(ctx.exception)
        self.assertIn("8", message)
        return

    def test_zero_length_raises_value_error(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            guid_from_bytes(b=b"")
        message = str(ctx.exception)
        self.assertIn("0", message)
        return

    def test_non_bytes_input_raises_type_error(self) -> None:
        with self.assertRaises(TypeError) as ctx:
            guid_from_bytes(b=12345)  # type: ignore[arg-type]
        message = str(ctx.exception)
        self.assertIn("int", message)
        return

    def test_accepts_bytearray(self) -> None:
        result = guid_from_bytes(b=bytearray(CANONICAL_GUID_BYTES))
        self.assertEqual(result, uuid.UUID(CANONICAL_GUID_STRING))
        return


class GuidRoundTripTestCase(unittest.TestCase):

    def test_roundtrip_canonical_vector(self) -> None:
        g = uuid.UUID(CANONICAL_GUID_STRING)
        roundtripped = guid_from_bytes(b=guid_to_bytes(g=g))
        self.assertEqual(roundtripped, g)
        return

    def test_roundtrip_well_known_guids(self) -> None:
        well_known_strings = [
            "8be4df61-93ca-11d2-aa0d-00e098032b8c",
            "d719b2cb-3d3a-4596-a3bc-dad00e67656f",
            "b122a263-3661-4f68-9929-78f8b0d62180",
            "dcfa911d-26eb-469f-a220-38b7dc461220",
            "39b68c46-f7fb-441b-b6ec-16b0f69821f3",
            "36122546-f7e7-4c8f-bd9b-eb8525b50c0b",
            "8868e871-e4f1-11d3-bc22-0080c73c8881",
            "eb9d2d31-2d88-11d3-9a16-0090273fc14d",
            "f2fd1544-9794-4a2c-992e-e5bbcf20e394",
            "eb66918a-7eef-402a-842e-931d21c38ae9",
        ]
        for gstr in well_known_strings:
            g = uuid.UUID(gstr)
            buf = guid_to_bytes(g=g)
            self.assertEqual(len(buf), 16)
            roundtripped = guid_from_bytes(b=buf)
            self.assertEqual(roundtripped, g)
        return

    def test_bytes_roundtrip_for_arbitrary_buffer(self) -> None:
        buf = bytes(range(16))
        again = guid_to_bytes(g=guid_from_bytes(b=buf))
        self.assertEqual(again, buf)
        return


class UefiJumpParamsTestCase(unittest.TestCase):

    def test_default_port_is_22(self) -> None:
        params = UefiJumpParams(host="bastion.example.com", credential=object())
        self.assertEqual(params.port, 22)
        return

    def test_explicit_port(self) -> None:
        params = UefiJumpParams(host="bastion.example.com", credential=object(), port=2222)
        self.assertEqual(params.port, 2222)
        return

    def test_is_frozen_dataclass(self) -> None:
        self.assertTrue(dataclasses.is_dataclass(UefiJumpParams))
        params = UefiJumpParams(host="h", credential=object())
        with self.assertRaises(dataclasses.FrozenInstanceError):
            params.host = "other"  # type: ignore[misc]
        return

    def test_equality_is_fieldwise(self) -> None:
        cred = object()
        a = UefiJumpParams(host="h", credential=cred)
        b = UefiJumpParams(host="h", credential=cred)
        self.assertEqual(a, b)
        return

    def test_inequality_when_port_differs(self) -> None:
        cred = object()
        a = UefiJumpParams(host="h", credential=cred, port=22)
        b = UefiJumpParams(host="h", credential=cred, port=2222)
        self.assertNotEqual(a, b)
        return


if __name__ == "__main__":
    unittest.main()
