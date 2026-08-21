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
__copyright__ = "Copyright 2025, Code Craft AI LLC"
__credits__ = []


from ccai import testbase

from ccai.errors.exceptions import SkipError

import dataclasses
import os
import re
import uuid

from ccai.protocols.uefi.ueficonst import (
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


# ----------------------------------------------------------------------
# NumericDefaultsTestCase
# ----------------------------------------------------------------------


def test_default_uefi_timeout_is_positive_int() -> None:
    testbase.assert_true(isinstance(DEFAULT_UEFI_TIMEOUT, int))
    testbase.assert_greater(DEFAULT_UEFI_TIMEOUT, 0)
    return

def test_default_uefi_retry_interval_is_positive_float() -> None:
    testbase.assert_true(isinstance(DEFAULT_UEFI_RETRY_INTERVAL, float))
    testbase.assert_greater(DEFAULT_UEFI_RETRY_INTERVAL, 0)
    return

def test_default_shell_timeout_is_positive_int() -> None:
    testbase.assert_true(isinstance(DEFAULT_SHELL_TIMEOUT, int))
    testbase.assert_greater(DEFAULT_SHELL_TIMEOUT, 0)
    return

def test_default_redfish_timeout_is_positive_int() -> None:
    testbase.assert_true(isinstance(DEFAULT_REDFISH_TIMEOUT, int))
    testbase.assert_greater(DEFAULT_REDFISH_TIMEOUT, 0)
    return

def test_default_reconnect_delay_is_positive_float() -> None:
    testbase.assert_true(isinstance(DEFAULT_RECONNECT_DELAY, float))
    testbase.assert_greater(DEFAULT_RECONNECT_DELAY, 0)
    return

def test_default_chunk_size_is_positive_int() -> None:
    testbase.assert_true(isinstance(DEFAULT_CHUNK_SIZE, int))
    testbase.assert_greater(DEFAULT_CHUNK_SIZE, 0)
    return


# ----------------------------------------------------------------------
# TransportKindsTestCase
# ----------------------------------------------------------------------


def test_frozenset_type() -> None:
    testbase.assert_true(isinstance(TRANSPORT_KINDS, frozenset))
    return

def test_frozenset_membership() -> None:
    expected = {
        TRANSPORT_KIND_SERIAL,
        TRANSPORT_KIND_REDFISH,
        TRANSPORT_KIND_SSH_HOST,
        TRANSPORT_KIND_IPMI_SOL,
    }
    testbase.assert_equal(TRANSPORT_KINDS, expected)
    return

def test_frozenset_rejects_mutation() -> None:
    with testbase.raises(AttributeError):
        TRANSPORT_KINDS.add("nope")  # type: ignore[attr-defined]
    return


# ----------------------------------------------------------------------
# InteractivePromptTestCase
# ----------------------------------------------------------------------


def test_bytes_and_str_agree() -> None:
    testbase.assert_equal(INTERACTIVE_PROMPT_BYTES, INTERACTIVE_PROMPT.encode("utf-8"))
    return

def test_sentinel_aliases_interactive_prompt() -> None:
    testbase.assert_equal(DEFAULT_INTERACTIVE_SENTINEL, INTERACTIVE_PROMPT_BYTES)
    testbase.assert_equal(DEFAULT_INTERACTIVE_SENTINEL_STR, INTERACTIVE_PROMPT)
    return


# ----------------------------------------------------------------------
# PromptRegexTestCase
# ----------------------------------------------------------------------


def test_shell_prompt_bytes_regex_compiles() -> None:
    compiled = re.compile(DEFAULT_SHELL_PROMPT_PATTERN)
    match = compiled.search(b"FS0:\\> ")
    testbase.assert_true(match is not None)
    match = compiled.search(b"Shell> ")
    testbase.assert_true(match is not None)
    return

def test_shell_prompt_line_regex_rejects_non_prompt() -> None:
    match = REGEX_SHELL_PROMPT_LINE.match("not a prompt")
    testbase.assert_true(match is None)
    return

def test_guid_regex_accepts_canonical_strings() -> None:
    match = REGEX_GUID_STRING.match(CANONICAL_GUID_STRING)
    testbase.assert_true(match is not None)
    return

def test_guid_regex_rejects_malformed_string() -> None:
    match = REGEX_GUID_STRING.match("not-a-guid")
    testbase.assert_true(match is None)
    return


# ----------------------------------------------------------------------
# CommandTemplatesTestCase
# ----------------------------------------------------------------------


def test_success_template_contains_linesep() -> None:
    testbase.assert_true(os.linesep in TEMPLATE_COMMAND_SUCCESS)
    return

def test_failure_template_contains_linesep() -> None:
    testbase.assert_true(os.linesep in TEMPLATE_COMMAND_FAILURE)
    return

def test_success_template_has_no_hardcoded_lf_when_linesep_differs() -> None:
    # Skip the assertion on POSIX platforms where os.linesep IS "\n".
    if os.linesep == "\n":
        raise SkipError("os.linesep is '\\n' on this platform; hardcoded-LF check is meaningless.")
        return

    testbase.assert_true("\n" not in TEMPLATE_COMMAND_SUCCESS.replace(os.linesep, ""))
    return

def test_failure_template_has_no_hardcoded_lf_when_linesep_differs() -> None:
    if os.linesep == "\n":
        raise SkipError("os.linesep is '\\n' on this platform; hardcoded-LF check is meaningless.")
        return

    testbase.assert_true("\n" not in TEMPLATE_COMMAND_FAILURE.replace(os.linesep, ""))
    return

def test_success_template_formats() -> None:
    rendered = TEMPLATE_COMMAND_SUCCESS % ("TAG", "ver", "hello", "")
    testbase.assert_true("CMD=ver" in rendered)
    testbase.assert_true("STDOUT:" in rendered)
    testbase.assert_true("STDERR:" in rendered)
    testbase.assert_true(rendered.endswith(os.linesep))
    return

def test_failure_template_formats() -> None:
    rendered = TEMPLATE_COMMAND_FAILURE % ("TAG", "ver", 1, "out", "err")
    testbase.assert_true("STATUS=1" in rendered)
    testbase.assert_true("CMD=ver" in rendered)
    testbase.assert_true(rendered.endswith(os.linesep))
    return


# ----------------------------------------------------------------------
# GuidToBytesTestCase
# ----------------------------------------------------------------------


def test_guid_to_bytes_canonical_vector() -> None:
    g = uuid.UUID(CANONICAL_GUID_STRING)
    result = guid_to_bytes(g=g)
    testbase.assert_equal(result, CANONICAL_GUID_BYTES)
    return

def test_guid_to_bytes_returns_sixteen_bytes() -> None:
    g = uuid.UUID("8be4df61-93ca-11d2-aa0d-00e098032b8c")
    result = guid_to_bytes(g=g)
    testbase.assert_equal(len(result), 16)
    testbase.assert_true(isinstance(result, bytes))
    return

def test_guid_to_bytes_non_uuid_input_raises_type_error() -> None:
    with testbase.raises(TypeError) as ctx:
        guid_to_bytes(g="not-a-uuid")  # type: ignore[arg-type]
    message = str(ctx.exception)
    testbase.assert_true("uuid.UUID" in message)
    testbase.assert_true("str" in message)
    return


# ----------------------------------------------------------------------
# GuidFromBytesTestCase
# ----------------------------------------------------------------------


def test_guid_from_bytes_canonical_vector() -> None:
    result = guid_from_bytes(b=CANONICAL_GUID_BYTES)
    testbase.assert_equal(result, uuid.UUID(CANONICAL_GUID_STRING))
    return

def test_guid_from_bytes_wrong_length_raises_value_error_with_length() -> None:
    with testbase.raises(ValueError) as ctx:
        guid_from_bytes(b=b"\x00" * 8)
    message = str(ctx.exception)
    testbase.assert_true("8" in message)
    return

def test_guid_from_bytes_zero_length_raises_value_error() -> None:
    with testbase.raises(ValueError) as ctx:
        guid_from_bytes(b=b"")
    message = str(ctx.exception)
    testbase.assert_true("0" in message)
    return

def test_guid_from_bytes_non_bytes_input_raises_type_error() -> None:
    with testbase.raises(TypeError) as ctx:
        guid_from_bytes(b=12345)  # type: ignore[arg-type]
    message = str(ctx.exception)
    testbase.assert_true("int" in message)
    return

def test_guid_from_bytes_accepts_bytearray() -> None:
    result = guid_from_bytes(b=bytearray(CANONICAL_GUID_BYTES))
    testbase.assert_equal(result, uuid.UUID(CANONICAL_GUID_STRING))
    return


# ----------------------------------------------------------------------
# GuidRoundTripTestCase
# ----------------------------------------------------------------------


def test_roundtrip_canonical_vector() -> None:
    g = uuid.UUID(CANONICAL_GUID_STRING)
    roundtripped = guid_from_bytes(b=guid_to_bytes(g=g))
    testbase.assert_equal(roundtripped, g)
    return

def test_roundtrip_well_known_guids() -> None:
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
        testbase.assert_equal(len(buf), 16)
        roundtripped = guid_from_bytes(b=buf)
        testbase.assert_equal(roundtripped, g)
    return

def test_bytes_roundtrip_for_arbitrary_buffer() -> None:
    buf = bytes(range(16))
    again = guid_to_bytes(g=guid_from_bytes(b=buf))
    testbase.assert_equal(again, buf)
    return


# ----------------------------------------------------------------------
# UefiJumpParamsTestCase
# ----------------------------------------------------------------------


def test_default_port_is_22() -> None:
    params = UefiJumpParams(host="bastion.example.com", credential=object())
    testbase.assert_equal(params.port, 22)
    return

def test_explicit_port() -> None:
    params = UefiJumpParams(host="bastion.example.com", credential=object(), port=2222)
    testbase.assert_equal(params.port, 2222)
    return

def test_is_frozen_dataclass() -> None:
    testbase.assert_true(dataclasses.is_dataclass(UefiJumpParams))
    params = UefiJumpParams(host="h", credential=object())
    with testbase.raises(dataclasses.FrozenInstanceError):
        params.host = "other"  # type: ignore[misc]
    return

def test_equality_is_fieldwise() -> None:
    cred = object()
    a = UefiJumpParams(host="h", credential=cred)
    b = UefiJumpParams(host="h", credential=cred)
    testbase.assert_equal(a, b)
    return

def test_inequality_when_port_differs() -> None:
    cred = object()
    a = UefiJumpParams(host="h", credential=cred, port=22)
    b = UefiJumpParams(host="h", credential=cred, port=2222)
    testbase.assert_not_equal(a, b)
    return
