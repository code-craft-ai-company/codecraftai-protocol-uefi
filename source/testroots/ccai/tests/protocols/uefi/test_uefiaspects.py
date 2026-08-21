"""
    .. module:: test_uefiaspects
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Unit tests for the measured-interop aspects module.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Code Craft AI LLC"
__credits__ = []


from ccai import testbase

import logging
import pickle
import threading

from ccai.errors.exceptions import ConfigurationError

from ccai.protocols.uefi.uefiaspects import (
    ActionPattern,
    DEFAULT_ASPECTS,
    DEFAULT_CMD_ASPECTS,
    DEFAULT_COMPLETION_INTERVAL,
    DEFAULT_COMPLETION_TIMEOUT,
    DEFAULT_CONNECTION_INTERVAL,
    DEFAULT_CONNECTION_TIMEOUT,
    DEFAULT_INACTIVITY_INTERVAL,
    DEFAULT_INACTIVITY_TIMEOUT,
    DEFAULT_MONITOR_DELAY,
    DEFAULT_PROMPT_PATTERN,
    DEFAULT_RESPONSE_ENCODING,
    Freezable,
    LoggingPattern,
    UNDER_CONSTRUCTION_ASPECTS,
    UefiAspects,
    UefiCmdAspects,
    resolve_aspects,
)


# ----------------------------------------------------------------------
# FreezableTestCase
# ----------------------------------------------------------------------


def test_pre_freeze_mutation_allowed() -> None:
    instance = Freezable()
    instance.foo = 1  # type: ignore[attr-defined]
    result = instance.foo  # type: ignore[attr-defined]
    testbase.assert_equal(result, 1)
    return

def test_post_freeze_mutation_rejected() -> None:
    instance = Freezable()
    instance.foo = 1  # type: ignore[attr-defined]
    instance.freeze()
    with testbase.raises(AttributeError) as ctx:
        instance.foo = 2  # type: ignore[attr-defined]
    testbase.assert_true("foo" in str(ctx.exception))
    return

def test_subclass_inherits_freeze() -> None:

    class Derived(Freezable):

        def __init__(self) -> None:
            Freezable.__init__(self)
            self.a = 10
            self.freeze()
            return

    instance = Derived()
    with testbase.raises(AttributeError):
        instance.a = 20
    return


# ----------------------------------------------------------------------
# ActionPatternTestCase
# ----------------------------------------------------------------------


def test_action_pattern_members() -> None:
    expected = {
        "SINGLE_CALL",
        "SINGLE_CONNECTED_CALL",
        "DO_UNTIL_SUCCESS",
        "DO_WHILE_SUCCESS",
        "DO_UNTIL_CONNECTION_FAILURE",
    }
    actual = {m.name for m in ActionPattern}
    testbase.assert_equal(actual, expected)
    return

def test_action_pattern_pickle_roundtrip() -> None:
    for member in ActionPattern:
        blob = pickle.dumps(member)
        restored = pickle.loads(blob)
        testbase.assert_true(restored is member)
    return


# ----------------------------------------------------------------------
# LoggingPatternTestCase
# ----------------------------------------------------------------------


def test_logging_pattern_members() -> None:
    expected = {"ALL_RESULTS", "FAILURE_ONLY", "SUCCESS_ONLY"}
    actual = {m.name for m in LoggingPattern}
    testbase.assert_equal(actual, expected)
    return


# ----------------------------------------------------------------------
# UefiAspectsConstructionTestCase
# ----------------------------------------------------------------------


def test_uefi_aspects_construction_defaults() -> None:
    aspects = UefiAspects()
    testbase.assert_equal(aspects.action_pattern, ActionPattern.SINGLE_CALL)
    testbase.assert_equal(aspects.completion_timeout, float(DEFAULT_COMPLETION_TIMEOUT))
    testbase.assert_equal(aspects.completion_interval, float(DEFAULT_COMPLETION_INTERVAL))
    testbase.assert_equal(aspects.connection_timeout, float(DEFAULT_CONNECTION_TIMEOUT))
    testbase.assert_equal(aspects.connection_interval, float(DEFAULT_CONNECTION_INTERVAL))
    testbase.assert_equal(aspects.inactivity_timeout, float(DEFAULT_INACTIVITY_TIMEOUT))
    testbase.assert_equal(aspects.inactivity_interval, float(DEFAULT_INACTIVITY_INTERVAL))
    testbase.assert_equal(aspects.monitor_delay, float(DEFAULT_MONITOR_DELAY))
    testbase.assert_equal(aspects.logging_pattern, LoggingPattern.ALL_RESULTS)
    testbase.assert_equal(aspects.allowed_error_codes, ())
    testbase.assert_equal(aspects.allowed_connection_exceptions, ())
    testbase.assert_equal(aspects.must_connect, False)
    return

def test_uefi_aspects_construction_default_equality_across_instances() -> None:
    a = UefiAspects()
    b = UefiAspects()
    testbase.assert_equal(a, b)
    testbase.assert_equal(hash(a), hash(b))
    return

def test_uefi_aspects_construction_frozen_after_construction() -> None:
    aspects = UefiAspects()
    with testbase.raises(AttributeError):
        aspects.completion_timeout = 1.0
    return

def test_uefi_aspects_construction_repr_contains_all_fields() -> None:
    aspects = UefiAspects()
    text = repr(aspects)
    for field in ("action_pattern", "completion_timeout", "logging_pattern", "must_connect"):
        testbase.assert_true(field in text)
    return


# ----------------------------------------------------------------------
# UefiAspectsValidationTestCase
# ----------------------------------------------------------------------


def test_invalid_action_pattern() -> None:
    with testbase.raises(ConfigurationError):
        UefiAspects(action_pattern="single")  # type: ignore[arg-type]
    return

def test_non_positive_timeout() -> None:
    with testbase.raises(ConfigurationError):
        UefiAspects(completion_timeout=0)
    with testbase.raises(ConfigurationError):
        UefiAspects(completion_timeout=-1)
    return

def test_interval_exceeds_timeout() -> None:
    with testbase.raises(ConfigurationError) as ctx:
        UefiAspects(completion_timeout=5, completion_interval=10)
    message = str(ctx.exception)
    testbase.assert_true("completion_interval" in message)
    testbase.assert_true("completion_timeout" in message)
    return

def test_invalid_logging_pattern() -> None:
    with testbase.raises(ConfigurationError):
        UefiAspects(logging_pattern="all")  # type: ignore[arg-type]
    return

def test_allowed_error_codes_rejects_non_int() -> None:
    with testbase.raises(ConfigurationError):
        UefiAspects(allowed_error_codes=[1, "2"])  # type: ignore[list-item]
    return

def test_allowed_error_codes_rejects_non_iterable() -> None:
    with testbase.raises(ConfigurationError):
        UefiAspects(allowed_error_codes=42)  # type: ignore[arg-type]
    return

def test_allowed_error_codes_accepts_list() -> None:
    aspects = UefiAspects(allowed_error_codes=[1, 2, 3])
    testbase.assert_equal(aspects.allowed_error_codes, (1, 2, 3))
    return

def test_allowed_connection_exceptions_rejects_non_class() -> None:
    with testbase.raises(ConfigurationError):
        UefiAspects(allowed_connection_exceptions=[42])  # type: ignore[list-item]
    return

def test_allowed_connection_exceptions_rejects_non_exception_class() -> None:
    with testbase.raises(ConfigurationError):
        UefiAspects(allowed_connection_exceptions=[str])  # type: ignore[list-item]
    return

def test_allowed_connection_exceptions_accepts_valid() -> None:
    aspects = UefiAspects(allowed_connection_exceptions=(ConnectionError, TimeoutError))
    testbase.assert_equal(aspects.allowed_connection_exceptions, (ConnectionError, TimeoutError))
    return

def test_must_connect_rejects_non_bool() -> None:
    with testbase.raises(ConfigurationError):
        UefiAspects(must_connect=1)  # type: ignore[arg-type]
    return

def test_logger_rejects_non_logger() -> None:
    with testbase.raises(ConfigurationError):
        UefiAspects(logger="not-a-logger")  # type: ignore[arg-type]
    return

def test_logger_accepts_duck_type() -> None:
    lg = logging.getLogger("test.uefi.aspects")
    aspects = UefiAspects(logger=lg)
    testbase.assert_true(aspects.logger is lg)
    return


# ----------------------------------------------------------------------
# UefiCmdAspectsTestCase
# ----------------------------------------------------------------------


def test_uefi_cmd_aspects_defaults() -> None:
    aspects = UefiCmdAspects()
    testbase.assert_equal(aspects.expected_status, 0)
    testbase.assert_true(aspects.user is None)
    testbase.assert_equal(aspects.prompt_pattern, DEFAULT_PROMPT_PATTERN)
    testbase.assert_equal(aspects.response_encoding, DEFAULT_RESPONSE_ENCODING)
    testbase.assert_true(aspects.transport_kind is None)
    return

def test_uefi_cmd_aspects_expected_status_int() -> None:
    aspects = UefiCmdAspects(expected_status=42)
    testbase.assert_equal(aspects.expected_status, 42)
    return

def test_uefi_cmd_aspects_expected_status_sequence_coerced_to_tuple() -> None:
    aspects = UefiCmdAspects(expected_status=[0, 1, 2])
    testbase.assert_equal(aspects.expected_status, (0, 1, 2))
    return

def test_uefi_cmd_aspects_expected_status_rejects_non_int_elements() -> None:
    with testbase.raises(ConfigurationError):
        UefiCmdAspects(expected_status=[0, "1"])  # type: ignore[list-item]
    return

def test_uefi_cmd_aspects_prompt_pattern_accepts_bytes() -> None:
    aspects = UefiCmdAspects(prompt_pattern=b"FS0:\\>")
    testbase.assert_equal(aspects.prompt_pattern, b"FS0:\\>")
    return

def test_uefi_cmd_aspects_prompt_pattern_rejects_str() -> None:
    with testbase.raises(ConfigurationError):
        UefiCmdAspects(prompt_pattern="Shell>")  # type: ignore[arg-type]
    return

def test_uefi_cmd_aspects_response_encoding_rejects_unknown() -> None:
    with testbase.raises(ConfigurationError):
        UefiCmdAspects(response_encoding="not-a-codec")
    return

def test_uefi_cmd_aspects_transport_kind_rejects_unknown() -> None:
    with testbase.raises(ConfigurationError):
        UefiCmdAspects(transport_kind="tcp")
    return

def test_uefi_cmd_aspects_transport_kind_accepts_known() -> None:
    aspects = UefiCmdAspects(transport_kind="serial")
    testbase.assert_equal(aspects.transport_kind, "serial")
    return

def test_uefi_cmd_aspects_user_must_be_str_or_none() -> None:
    with testbase.raises(ConfigurationError):
        UefiCmdAspects(user=42)  # type: ignore[arg-type]
    aspects = UefiCmdAspects(user="root")
    testbase.assert_equal(aspects.user, "root")
    return

def test_uefi_cmd_aspects_frozen() -> None:
    aspects = UefiCmdAspects()
    with testbase.raises(AttributeError):
        aspects.expected_status = 1
    return

def test_uefi_cmd_aspects_equality_includes_cmd_fields() -> None:
    a = UefiCmdAspects(expected_status=0)
    b = UefiCmdAspects(expected_status=1)
    testbase.assert_not_equal(a, b)
    return


# ----------------------------------------------------------------------
# MergeTestCase
# ----------------------------------------------------------------------


def test_merge_none_returns_self() -> None:
    base = UefiAspects()
    result = base.merge(None)
    testbase.assert_true(result is base)
    return

def test_merge_overlays_non_default() -> None:
    base = UefiAspects(completion_timeout=100)
    overlay = UefiAspects(connection_timeout=30)
    result = base.merge(overlay)
    testbase.assert_equal(result.completion_timeout, 100.0)
    testbase.assert_equal(result.connection_timeout, 30.0)
    return

def test_merge_of_cmd_and_base_returns_cmd() -> None:
    base = UefiCmdAspects(expected_status=(0, 1))
    overlay = UefiAspects(completion_timeout=50)
    result = base.merge(overlay)
    testbase.assert_true(isinstance(result, UefiCmdAspects))
    testbase.assert_equal(result.expected_status, (0, 1))
    testbase.assert_equal(result.completion_timeout, 50.0)
    return

def test_merge_returns_frozen() -> None:
    base = UefiAspects()
    overlay = UefiAspects(must_connect=True)
    result = base.merge(overlay)
    with testbase.raises(AttributeError):
        result.must_connect = False
    return


# ----------------------------------------------------------------------
# ResolveAspectsTestCase
# ----------------------------------------------------------------------


def test_passed_wins() -> None:
    passed = UefiAspects()
    default = UefiAspects(completion_timeout=99)
    result = resolve_aspects(passed, default)
    testbase.assert_true(result is passed)
    return

def test_fallback_to_default() -> None:
    default = UefiAspects()
    result = resolve_aspects(None, default)
    testbase.assert_true(result is default)
    return

def test_both_none_raises() -> None:
    with testbase.raises(ConfigurationError):
        resolve_aspects(None, None)
    return


# ----------------------------------------------------------------------
# DefaultsTestCase
# ----------------------------------------------------------------------


def test_default_aspects_is_frozen() -> None:
    with testbase.raises(AttributeError):
        DEFAULT_ASPECTS.must_connect = True
    return

def test_default_cmd_aspects_is_frozen() -> None:
    with testbase.raises(AttributeError):
        DEFAULT_CMD_ASPECTS.expected_status = 1
    return

def test_under_construction_presets() -> None:
    testbase.assert_equal(UNDER_CONSTRUCTION_ASPECTS.connection_timeout, 60.0)
    testbase.assert_equal(UNDER_CONSTRUCTION_ASPECTS.connection_interval, 5.0)
    testbase.assert_true(ConnectionError in UNDER_CONSTRUCTION_ASPECTS.allowed_connection_exceptions)
    testbase.assert_true(TimeoutError in UNDER_CONSTRUCTION_ASPECTS.allowed_connection_exceptions)
    return


# ----------------------------------------------------------------------
# ThreadSafetyTestCase
# ----------------------------------------------------------------------


def test_concurrent_reads() -> None:
    aspects = UefiCmdAspects(
        completion_timeout=100,
        allowed_error_codes=(1, 2, 3),
        allowed_connection_exceptions=(ConnectionError,),
    )

    readings = []
    lock = threading.Lock()

    def reader() -> None:
        local = []
        for _ in range(1000):
            local.append(aspects.completion_timeout)
            local.append(aspects.allowed_error_codes)
        with lock:
            readings.extend(local)
        return

    threads = [threading.Thread(target=reader) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    for value in readings[::2]:
        testbase.assert_equal(value, 100.0)
    for value in readings[1::2]:
        testbase.assert_equal(value, (1, 2, 3))
    return
