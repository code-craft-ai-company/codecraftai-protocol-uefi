"""
    .. module:: test_uefiaspects
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Unit tests for the measured-interop aspects module.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Code Craft AI LLC"
__credits__ = []


import logging
import pickle
import threading
import unittest

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


class FreezableTestCase(unittest.TestCase):

    def test_pre_freeze_mutation_allowed(self) -> None:
        instance = Freezable()
        instance.foo = 1  # type: ignore[attr-defined]
        result = instance.foo  # type: ignore[attr-defined]
        self.assertEqual(result, 1)
        return

    def test_post_freeze_mutation_rejected(self) -> None:
        instance = Freezable()
        instance.foo = 1  # type: ignore[attr-defined]
        instance.freeze()
        with self.assertRaises(AttributeError) as ctx:
            instance.foo = 2  # type: ignore[attr-defined]
        self.assertIn("foo", str(ctx.exception))
        return

    def test_subclass_inherits_freeze(self) -> None:

        class Derived(Freezable):

            def __init__(self) -> None:
                Freezable.__init__(self)
                self.a = 10
                self.freeze()
                return

        instance = Derived()
        with self.assertRaises(AttributeError):
            instance.a = 20
        return


class ActionPatternTestCase(unittest.TestCase):

    def test_members(self) -> None:
        expected = {
            "SINGLE_CALL",
            "SINGLE_CONNECTED_CALL",
            "DO_UNTIL_SUCCESS",
            "DO_WHILE_SUCCESS",
            "DO_UNTIL_CONNECTION_FAILURE",
        }
        actual = {m.name for m in ActionPattern}
        self.assertEqual(actual, expected)
        return

    def test_pickle_roundtrip(self) -> None:
        for member in ActionPattern:
            blob = pickle.dumps(member)
            restored = pickle.loads(blob)
            self.assertIs(restored, member)
        return


class LoggingPatternTestCase(unittest.TestCase):

    def test_members(self) -> None:
        expected = {"ALL_RESULTS", "FAILURE_ONLY", "SUCCESS_ONLY"}
        actual = {m.name for m in LoggingPattern}
        self.assertEqual(actual, expected)
        return


class UefiAspectsConstructionTestCase(unittest.TestCase):

    def test_defaults(self) -> None:
        aspects = UefiAspects()
        self.assertEqual(aspects.action_pattern, ActionPattern.SINGLE_CALL)
        self.assertEqual(aspects.completion_timeout, float(DEFAULT_COMPLETION_TIMEOUT))
        self.assertEqual(aspects.completion_interval, float(DEFAULT_COMPLETION_INTERVAL))
        self.assertEqual(aspects.connection_timeout, float(DEFAULT_CONNECTION_TIMEOUT))
        self.assertEqual(aspects.connection_interval, float(DEFAULT_CONNECTION_INTERVAL))
        self.assertEqual(aspects.inactivity_timeout, float(DEFAULT_INACTIVITY_TIMEOUT))
        self.assertEqual(aspects.inactivity_interval, float(DEFAULT_INACTIVITY_INTERVAL))
        self.assertEqual(aspects.monitor_delay, float(DEFAULT_MONITOR_DELAY))
        self.assertEqual(aspects.logging_pattern, LoggingPattern.ALL_RESULTS)
        self.assertEqual(aspects.allowed_error_codes, ())
        self.assertEqual(aspects.allowed_connection_exceptions, ())
        self.assertEqual(aspects.must_connect, False)
        return

    def test_default_equality_across_instances(self) -> None:
        a = UefiAspects()
        b = UefiAspects()
        self.assertEqual(a, b)
        self.assertEqual(hash(a), hash(b))
        return

    def test_frozen_after_construction(self) -> None:
        aspects = UefiAspects()
        with self.assertRaises(AttributeError):
            aspects.completion_timeout = 1.0
        return

    def test_repr_contains_all_fields(self) -> None:
        aspects = UefiAspects()
        text = repr(aspects)
        for field in ("action_pattern", "completion_timeout", "logging_pattern", "must_connect"):
            self.assertIn(field, text)
        return


class UefiAspectsValidationTestCase(unittest.TestCase):

    def test_invalid_action_pattern(self) -> None:
        with self.assertRaises(ConfigurationError):
            UefiAspects(action_pattern="single")  # type: ignore[arg-type]
        return

    def test_non_positive_timeout(self) -> None:
        with self.assertRaises(ConfigurationError):
            UefiAspects(completion_timeout=0)
        with self.assertRaises(ConfigurationError):
            UefiAspects(completion_timeout=-1)
        return

    def test_interval_exceeds_timeout(self) -> None:
        with self.assertRaises(ConfigurationError) as ctx:
            UefiAspects(completion_timeout=5, completion_interval=10)
        message = str(ctx.exception)
        self.assertIn("completion_interval", message)
        self.assertIn("completion_timeout", message)
        return

    def test_invalid_logging_pattern(self) -> None:
        with self.assertRaises(ConfigurationError):
            UefiAspects(logging_pattern="all")  # type: ignore[arg-type]
        return

    def test_allowed_error_codes_rejects_non_int(self) -> None:
        with self.assertRaises(ConfigurationError):
            UefiAspects(allowed_error_codes=[1, "2"])  # type: ignore[list-item]
        return

    def test_allowed_error_codes_rejects_non_iterable(self) -> None:
        with self.assertRaises(ConfigurationError):
            UefiAspects(allowed_error_codes=42)  # type: ignore[arg-type]
        return

    def test_allowed_error_codes_accepts_list(self) -> None:
        aspects = UefiAspects(allowed_error_codes=[1, 2, 3])
        self.assertEqual(aspects.allowed_error_codes, (1, 2, 3))
        return

    def test_allowed_connection_exceptions_rejects_non_class(self) -> None:
        with self.assertRaises(ConfigurationError):
            UefiAspects(allowed_connection_exceptions=[42])  # type: ignore[list-item]
        return

    def test_allowed_connection_exceptions_rejects_non_exception_class(self) -> None:
        with self.assertRaises(ConfigurationError):
            UefiAspects(allowed_connection_exceptions=[str])  # type: ignore[list-item]
        return

    def test_allowed_connection_exceptions_accepts_valid(self) -> None:
        aspects = UefiAspects(allowed_connection_exceptions=(ConnectionError, TimeoutError))
        self.assertEqual(aspects.allowed_connection_exceptions, (ConnectionError, TimeoutError))
        return

    def test_must_connect_rejects_non_bool(self) -> None:
        with self.assertRaises(ConfigurationError):
            UefiAspects(must_connect=1)  # type: ignore[arg-type]
        return

    def test_logger_rejects_non_logger(self) -> None:
        with self.assertRaises(ConfigurationError):
            UefiAspects(logger="not-a-logger")  # type: ignore[arg-type]
        return

    def test_logger_accepts_duck_type(self) -> None:
        lg = logging.getLogger("test.uefi.aspects")
        aspects = UefiAspects(logger=lg)
        self.assertIs(aspects.logger, lg)
        return


class UefiCmdAspectsTestCase(unittest.TestCase):

    def test_defaults(self) -> None:
        aspects = UefiCmdAspects()
        self.assertEqual(aspects.expected_status, 0)
        self.assertIsNone(aspects.user)
        self.assertEqual(aspects.prompt_pattern, DEFAULT_PROMPT_PATTERN)
        self.assertEqual(aspects.response_encoding, DEFAULT_RESPONSE_ENCODING)
        self.assertIsNone(aspects.transport_kind)
        return

    def test_expected_status_int(self) -> None:
        aspects = UefiCmdAspects(expected_status=42)
        self.assertEqual(aspects.expected_status, 42)
        return

    def test_expected_status_sequence_coerced_to_tuple(self) -> None:
        aspects = UefiCmdAspects(expected_status=[0, 1, 2])
        self.assertEqual(aspects.expected_status, (0, 1, 2))
        return

    def test_expected_status_rejects_non_int_elements(self) -> None:
        with self.assertRaises(ConfigurationError):
            UefiCmdAspects(expected_status=[0, "1"])  # type: ignore[list-item]
        return

    def test_prompt_pattern_accepts_bytes(self) -> None:
        aspects = UefiCmdAspects(prompt_pattern=b"FS0:\\>")
        self.assertEqual(aspects.prompt_pattern, b"FS0:\\>")
        return

    def test_prompt_pattern_rejects_str(self) -> None:
        with self.assertRaises(ConfigurationError):
            UefiCmdAspects(prompt_pattern="Shell>")  # type: ignore[arg-type]
        return

    def test_response_encoding_rejects_unknown(self) -> None:
        with self.assertRaises(ConfigurationError):
            UefiCmdAspects(response_encoding="not-a-codec")
        return

    def test_transport_kind_rejects_unknown(self) -> None:
        with self.assertRaises(ConfigurationError):
            UefiCmdAspects(transport_kind="tcp")
        return

    def test_transport_kind_accepts_known(self) -> None:
        aspects = UefiCmdAspects(transport_kind="serial")
        self.assertEqual(aspects.transport_kind, "serial")
        return

    def test_user_must_be_str_or_none(self) -> None:
        with self.assertRaises(ConfigurationError):
            UefiCmdAspects(user=42)  # type: ignore[arg-type]
        aspects = UefiCmdAspects(user="root")
        self.assertEqual(aspects.user, "root")
        return

    def test_frozen(self) -> None:
        aspects = UefiCmdAspects()
        with self.assertRaises(AttributeError):
            aspects.expected_status = 1
        return

    def test_equality_includes_cmd_fields(self) -> None:
        a = UefiCmdAspects(expected_status=0)
        b = UefiCmdAspects(expected_status=1)
        self.assertNotEqual(a, b)
        return


class MergeTestCase(unittest.TestCase):

    def test_merge_none_returns_self(self) -> None:
        base = UefiAspects()
        result = base.merge(None)
        self.assertIs(result, base)
        return

    def test_merge_overlays_non_default(self) -> None:
        base = UefiAspects(completion_timeout=100)
        overlay = UefiAspects(connection_timeout=30)
        result = base.merge(overlay)
        self.assertEqual(result.completion_timeout, 100.0)
        self.assertEqual(result.connection_timeout, 30.0)
        return

    def test_merge_of_cmd_and_base_returns_cmd(self) -> None:
        base = UefiCmdAspects(expected_status=(0, 1))
        overlay = UefiAspects(completion_timeout=50)
        result = base.merge(overlay)
        self.assertIsInstance(result, UefiCmdAspects)
        self.assertEqual(result.expected_status, (0, 1))
        self.assertEqual(result.completion_timeout, 50.0)
        return

    def test_merge_returns_frozen(self) -> None:
        base = UefiAspects()
        overlay = UefiAspects(must_connect=True)
        result = base.merge(overlay)
        with self.assertRaises(AttributeError):
            result.must_connect = False
        return


class ResolveAspectsTestCase(unittest.TestCase):

    def test_passed_wins(self) -> None:
        passed = UefiAspects()
        default = UefiAspects(completion_timeout=99)
        result = resolve_aspects(passed, default)
        self.assertIs(result, passed)
        return

    def test_fallback_to_default(self) -> None:
        default = UefiAspects()
        result = resolve_aspects(None, default)
        self.assertIs(result, default)
        return

    def test_both_none_raises(self) -> None:
        with self.assertRaises(ConfigurationError):
            resolve_aspects(None, None)
        return


class DefaultsTestCase(unittest.TestCase):

    def test_default_aspects_is_frozen(self) -> None:
        with self.assertRaises(AttributeError):
            DEFAULT_ASPECTS.must_connect = True
        return

    def test_default_cmd_aspects_is_frozen(self) -> None:
        with self.assertRaises(AttributeError):
            DEFAULT_CMD_ASPECTS.expected_status = 1
        return

    def test_under_construction_presets(self) -> None:
        self.assertEqual(UNDER_CONSTRUCTION_ASPECTS.connection_timeout, 60.0)
        self.assertEqual(UNDER_CONSTRUCTION_ASPECTS.connection_interval, 5.0)
        self.assertIn(ConnectionError, UNDER_CONSTRUCTION_ASPECTS.allowed_connection_exceptions)
        self.assertIn(TimeoutError, UNDER_CONSTRUCTION_ASPECTS.allowed_connection_exceptions)
        return


class ThreadSafetyTestCase(unittest.TestCase):

    def test_concurrent_reads(self) -> None:
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
            self.assertEqual(value, 100.0)
        for value in readings[1::2]:
            self.assertEqual(value, (1, 2, 3))
        return


if __name__ == "__main__":
    unittest.main()
