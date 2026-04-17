"""
    .. module:: uefiaspects
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Module containing the measured-interop :class:`UefiAspects` and
               :class:`UefiCmdAspects` behavior-package classes and the
               :class:`ActionPattern` / :class:`LoggingPattern` enums.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Automation Mojo LLC"
__credits__ = []


import codecs
import logging

from enum import IntEnum
from typing import Final, Optional, Sequence, Tuple, Type, Union

from automojo.errors.exceptions import ConfigurationError
from automojo.xmodules.xlogging.enhancedloggers import getEnhancedLogger


# ---------------------------------------------------------------------------
# Freezable base
# ---------------------------------------------------------------------------

class Freezable:
    """
        Base class that allows an instance to be marked read-only after its
        constructor has finished populating attributes.  Consumers call
        :meth:`freeze` at the end of ``__init__``; any subsequent attribute
        assignment raises :class:`AttributeError`.
    """

    def __init__(self) -> None:
        """
            Initialize the freezable state marker.
        """
        object.__setattr__(self, "__frozen__", False)
        return

    def freeze(self) -> None:
        """
            Transition the instance into the frozen state.  After this call
            any attribute assignment will raise :class:`AttributeError`.
        """
        object.__setattr__(self, "__frozen__", True)
        return

    def __setattr__(self, name: str, value: object) -> None:
        """
            Gate attribute assignment based on the frozen flag.
        """
        is_frozen = getattr(self, "__frozen__", False)
        if is_frozen:
            err_msg = "Freezable instance is frozen; cannot set attribute '{}'.".format(name)
            raise AttributeError(err_msg)

        object.__setattr__(self, name, value)
        return


# ---------------------------------------------------------------------------
# Pattern enums
# ---------------------------------------------------------------------------

class ActionPattern(IntEnum):
    """
        Enumeration of the retry / looping patterns supported by the measured
        interop engine in layer 4.  Consumers declare which patterns they
        support; unsupported patterns raise :class:`SemanticError` at call
        time.
    """
    SINGLE_CALL = 0
    SINGLE_CONNECTED_CALL = 1
    DO_UNTIL_SUCCESS = 2
    DO_WHILE_SUCCESS = 3
    DO_UNTIL_CONNECTION_FAILURE = 4


class LoggingPattern(IntEnum):
    """
        Enumeration of the logging filters applied to run-command results.
    """
    ALL_RESULTS = 0
    FAILURE_ONLY = 1
    SUCCESS_ONLY = 2


# ---------------------------------------------------------------------------
# Default constants
# ---------------------------------------------------------------------------

DEFAULT_COMPLETION_TIMEOUT = 600
DEFAULT_COMPLETION_INTERVAL = 10

DEFAULT_CONNECTION_TIMEOUT = 16
DEFAULT_CONNECTION_INTERVAL = 2

DEFAULT_INACTIVITY_TIMEOUT = 600
DEFAULT_INACTIVITY_INTERVAL = 0.5

DEFAULT_MONITOR_DELAY = 30

DEFAULT_RETRY_LOGGING_INTERVAL = 5

DEFAULT_LOGGING_PATTERN = LoggingPattern.ALL_RESULTS

DEFAULT_MUST_CONNECT = False

DEFAULT_ALLOWED_ERROR_CODES: Tuple[int, ...] = ()
DEFAULT_ALLOWED_CONNECTION_EXCEPTIONS: Tuple[Type[BaseException], ...] = ()

DEFAULT_PROMPT_PATTERN: bytes = rb"Shell>\s*$"
DEFAULT_RESPONSE_ENCODING = "utf-8"

VALID_TRANSPORT_KINDS: Tuple[str, ...] = (
    "serial",
    "redfish",
    "ssh-host",
    "ipmi-sol",
)


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _validate_action_pattern(value: object) -> ActionPattern:
    """
        Validate that ``value`` is an :class:`ActionPattern`.

        :param value: Value to validate.

        :returns: The validated :class:`ActionPattern`.
        :raises: ConfigurationError
    """
    if not isinstance(value, ActionPattern):
        err_msg_lines = [
            "'action_pattern' must be an ActionPattern member.",
            "GOT: value={!r} type={}".format(value, type(value).__name__),
        ]
        err_msg = "\n".join(err_msg_lines)
        raise ConfigurationError(err_msg) from None

    return value


def _validate_logging_pattern(value: object) -> LoggingPattern:
    """
        Validate that ``value`` is a :class:`LoggingPattern`.

        :param value: Value to validate.

        :returns: The validated :class:`LoggingPattern`.
        :raises: ConfigurationError
    """
    if not isinstance(value, LoggingPattern):
        err_msg_lines = [
            "'logging_pattern' must be a LoggingPattern member.",
            "GOT: value={!r} type={}".format(value, type(value).__name__),
        ]
        err_msg = "\n".join(err_msg_lines)
        raise ConfigurationError(err_msg) from None

    return value


def _validate_positive_number(field_name: str, value: object) -> float:
    """
        Validate that ``value`` is a positive number.

        :param field_name: Name of the field being validated (for diagnostics).
        :param value: Value to validate.

        :returns: The value coerced to float.
        :raises: ConfigurationError
    """
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        err_msg_lines = [
            "'{}' must be an int or float.".format(field_name),
            "GOT: value={!r} type={}".format(value, type(value).__name__),
        ]
        err_msg = "\n".join(err_msg_lines)
        raise ConfigurationError(err_msg) from None

    coerced = float(value)
    if coerced <= 0:
        err_msg_lines = [
            "'{}' must be greater than zero.".format(field_name),
            "GOT: value={}".format(coerced),
        ]
        err_msg = "\n".join(err_msg_lines)
        raise ConfigurationError(err_msg) from None

    return coerced


def _validate_interval_vs_timeout(interval_name: str, interval_value: float,
                                  timeout_name: str, timeout_value: float) -> None:
    """
        Validate that an interval does not exceed its paired timeout.

        :param interval_name: Field name of the interval.
        :param interval_value: Value of the interval.
        :param timeout_name: Field name of the timeout.
        :param timeout_value: Value of the timeout.

        :raises: ConfigurationError
    """
    if interval_value > timeout_value:
        err_msg_lines = [
            "Interval must be less than or equal to its paired timeout.",
            "INTERVAL: {}={}".format(interval_name, interval_value),
            "TIMEOUT:  {}={}".format(timeout_name, timeout_value),
        ]
        err_msg = "\n".join(err_msg_lines)
        raise ConfigurationError(err_msg) from None

    return


def _validate_allowed_error_codes(value: object) -> Tuple[int, ...]:
    """
        Validate and coerce the ``allowed_error_codes`` field to a tuple of
        ints.

        :param value: Candidate value.

        :returns: Validated tuple of ints.
        :raises: ConfigurationError
    """
    try:
        candidate = tuple(value)  # type: ignore[arg-type]
    except TypeError as terr:
        err_msg_lines = [
            "'allowed_error_codes' must be an iterable of ints.",
            "GOT: value={!r} type={}".format(value, type(value).__name__),
        ]
        err_msg = "\n".join(err_msg_lines)
        raise ConfigurationError(err_msg) from terr

    for index, element in enumerate(candidate):
        if not isinstance(element, int) or isinstance(element, bool):
            err_msg_lines = [
                "'allowed_error_codes' elements must be ints.",
                "INDEX: {}".format(index),
                "GOT: value={!r} type={}".format(element, type(element).__name__),
            ]
            err_msg = "\n".join(err_msg_lines)
            raise ConfigurationError(err_msg) from None

    result = tuple(int(x) for x in candidate)
    return result


def _validate_allowed_connection_exceptions(value: object) -> Tuple[Type[BaseException], ...]:
    """
        Validate and coerce the ``allowed_connection_exceptions`` field to a
        tuple of :class:`BaseException` subclasses.

        :param value: Candidate value.

        :returns: Validated tuple of exception classes.
        :raises: ConfigurationError
    """
    try:
        candidate = tuple(value)  # type: ignore[arg-type]
    except TypeError as terr:
        err_msg_lines = [
            "'allowed_connection_exceptions' must be an iterable of exception types.",
            "GOT: value={!r} type={}".format(value, type(value).__name__),
        ]
        err_msg = "\n".join(err_msg_lines)
        raise ConfigurationError(err_msg) from terr

    for index, element in enumerate(candidate):
        if not (isinstance(element, type) and issubclass(element, BaseException)):
            err_msg_lines = [
                "'allowed_connection_exceptions' elements must be BaseException subclasses.",
                "INDEX: {}".format(index),
                "GOT: value={!r}".format(element),
            ]
            err_msg = "\n".join(err_msg_lines)
            raise ConfigurationError(err_msg) from None

    return candidate


def _validate_logger(value: object) -> logging.Logger:
    """
        Validate that ``value`` duck-types as a :class:`logging.Logger`.

        :param value: Candidate logger.

        :returns: The validated logger.
        :raises: ConfigurationError
    """
    required_methods = ("info", "error", "warning", "debug")
    missing = [name for name in required_methods if not callable(getattr(value, name, None))]
    if len(missing) > 0:
        err_msg_lines = [
            "'logger' does not duck-type as a logging.Logger.",
            "MISSING METHODS: {}".format(", ".join(missing)),
            "GOT: value={!r} type={}".format(value, type(value).__name__),
        ]
        err_msg = "\n".join(err_msg_lines)
        raise ConfigurationError(err_msg) from None

    return value  # type: ignore[return-value]


def _validate_bool(field_name: str, value: object) -> bool:
    """
        Validate that ``value`` is a :class:`bool`.

        :param field_name: Field name for diagnostics.
        :param value: Candidate value.

        :returns: The boolean value.
        :raises: ConfigurationError
    """
    if not isinstance(value, bool):
        err_msg_lines = [
            "'{}' must be a bool.".format(field_name),
            "GOT: value={!r} type={}".format(value, type(value).__name__),
        ]
        err_msg = "\n".join(err_msg_lines)
        raise ConfigurationError(err_msg) from None

    return value


# ---------------------------------------------------------------------------
# UefiAspects
# ---------------------------------------------------------------------------

class UefiAspects(Freezable):
    """
        The :class:`UefiAspects` class packages measured-interop behavior
        parameters into a single frozen container that callers pass to any
        public UEFI interop API.  It controls retry patterning, completion
        timeouts, connection retry semantics, inactivity monitoring, logging
        pattern, and the set of tolerable error codes and connection
        exceptions.

        An instance is frozen at the end of ``__init__``; mutation raises
        :class:`AttributeError`.  This allows callers to build presets once
        at module scope and share them across threads.
    """

    def __init__(self, action_pattern: ActionPattern = ActionPattern.SINGLE_CALL,
                       completion_timeout: float = DEFAULT_COMPLETION_TIMEOUT,
                       completion_interval: float = DEFAULT_COMPLETION_INTERVAL,
                       connection_timeout: float = DEFAULT_CONNECTION_TIMEOUT,
                       connection_interval: float = DEFAULT_CONNECTION_INTERVAL,
                       inactivity_timeout: float = DEFAULT_INACTIVITY_TIMEOUT,
                       inactivity_interval: float = DEFAULT_INACTIVITY_INTERVAL,
                       monitor_delay: float = DEFAULT_MONITOR_DELAY,
                       logging_pattern: LoggingPattern = DEFAULT_LOGGING_PATTERN,
                       retry_logging_interval: int = DEFAULT_RETRY_LOGGING_INTERVAL,
                       allowed_error_codes: Sequence[int] = DEFAULT_ALLOWED_ERROR_CODES,
                       allowed_connection_exceptions: Sequence[Type[BaseException]] = DEFAULT_ALLOWED_CONNECTION_EXCEPTIONS,
                       must_connect: bool = DEFAULT_MUST_CONNECT,
                       logger: Optional[logging.Logger] = None) -> None:
        """
            Create a :class:`UefiAspects` instance.

            :param action_pattern: The retry / looping pattern consumers should apply.
            :param completion_timeout: Max seconds for an activity to complete.
            :param completion_interval: Seconds between completion-retry attempts.
            :param connection_timeout: Max seconds for a connection to establish.
            :param connection_interval: Seconds between connection-retry attempts.
            :param inactivity_timeout: Max seconds without progress before timeout.
            :param inactivity_interval: Seconds between activity polls.
            :param monitor_delay: Seconds before monitoring begins after startup.
            :param logging_pattern: Filter for logging command results.
            :param retry_logging_interval: Seconds between retry-loop log lines.
            :param allowed_error_codes: Error codes treated as non-terminal.
            :param allowed_connection_exceptions: Exceptions that do not count
                                                  as terminal connection failures.
            :param must_connect: When ``True``, connection failures raise rather
                                 than retry.
            :param logger: Optional :class:`logging.Logger`; defaults to the
                           enhanced logger.

            :raises: ConfigurationError
        """
        Freezable.__init__(self)

        validated_action_pattern = _validate_action_pattern(action_pattern)

        validated_completion_timeout = _validate_positive_number("completion_timeout", completion_timeout)
        validated_completion_interval = _validate_positive_number("completion_interval", completion_interval)
        validated_connection_timeout = _validate_positive_number("connection_timeout", connection_timeout)
        validated_connection_interval = _validate_positive_number("connection_interval", connection_interval)
        validated_inactivity_timeout = _validate_positive_number("inactivity_timeout", inactivity_timeout)
        validated_inactivity_interval = _validate_positive_number("inactivity_interval", inactivity_interval)
        validated_monitor_delay = _validate_positive_number("monitor_delay", monitor_delay)
        validated_retry_logging_interval = _validate_positive_number("retry_logging_interval", retry_logging_interval)

        _validate_interval_vs_timeout("completion_interval", validated_completion_interval,
                                      "completion_timeout", validated_completion_timeout)
        _validate_interval_vs_timeout("connection_interval", validated_connection_interval,
                                      "connection_timeout", validated_connection_timeout)
        _validate_interval_vs_timeout("inactivity_interval", validated_inactivity_interval,
                                      "inactivity_timeout", validated_inactivity_timeout)

        validated_logging_pattern = _validate_logging_pattern(logging_pattern)
        validated_allowed_error_codes = _validate_allowed_error_codes(allowed_error_codes)
        validated_allowed_connection_exceptions = _validate_allowed_connection_exceptions(allowed_connection_exceptions)
        validated_must_connect = _validate_bool("must_connect", must_connect)

        if logger is None:
            resolved_logger = getEnhancedLogger()
        else:
            resolved_logger = _validate_logger(logger)

        self.action_pattern = validated_action_pattern
        self.completion_timeout = validated_completion_timeout
        self.completion_interval = validated_completion_interval
        self.connection_timeout = validated_connection_timeout
        self.connection_interval = validated_connection_interval
        self.inactivity_timeout = validated_inactivity_timeout
        self.inactivity_interval = validated_inactivity_interval
        self.monitor_delay = validated_monitor_delay
        self.logging_pattern = validated_logging_pattern
        self.retry_logging_interval = validated_retry_logging_interval
        self.allowed_error_codes = validated_allowed_error_codes
        self.allowed_connection_exceptions = validated_allowed_connection_exceptions
        self.must_connect = validated_must_connect
        self.logger = resolved_logger

        self.freeze()
        return

    def _comparable_fields(self) -> Tuple[object, ...]:
        """
            Produce a hashable tuple of the fields participating in equality
            and hashing.  Excludes the ``logger`` attribute because loggers are
            process-local singletons and cannot be reliably compared.

            :returns: Tuple of field values.
        """
        fields = (
            type(self).__name__,
            self.action_pattern,
            self.completion_timeout,
            self.completion_interval,
            self.connection_timeout,
            self.connection_interval,
            self.inactivity_timeout,
            self.inactivity_interval,
            self.monitor_delay,
            self.logging_pattern,
            self.retry_logging_interval,
            self.allowed_error_codes,
            self.allowed_connection_exceptions,
            self.must_connect,
        )
        return fields

    def __eq__(self, other: object) -> bool:
        """
            Equality based on the comparable-fields tuple.
        """
        if not isinstance(other, UefiAspects):
            return NotImplemented

        result = self._comparable_fields() == other._comparable_fields()
        return result

    def __hash__(self) -> int:
        """
            Hash based on the comparable-fields tuple.
        """
        result = hash(self._comparable_fields())
        return result

    def __repr__(self) -> str:
        """
            Render an easily-diffable multi-line representation.
        """
        lines = ["{}(".format(type(self).__name__)]
        field_names = [
            "action_pattern",
            "completion_timeout",
            "completion_interval",
            "connection_timeout",
            "connection_interval",
            "inactivity_timeout",
            "inactivity_interval",
            "monitor_delay",
            "logging_pattern",
            "retry_logging_interval",
            "allowed_error_codes",
            "allowed_connection_exceptions",
            "must_connect",
        ]
        for name in field_names:
            value = getattr(self, name)
            lines.append("    {}={!r},".format(name, value))
        lines.append(")")

        result = "\n".join(lines)
        return result

    def merge(self, overlay: Optional["UefiAspects"]) -> "UefiAspects":
        """
            Return a new frozen aspects instance whose fields come from
            ``overlay`` where ``overlay`` differs from its default, and from
            ``self`` otherwise.  When ``overlay`` is ``None`` the method
            returns ``self`` unchanged.

            When either operand is a :class:`UefiCmdAspects` the result is of
            that type; otherwise the result is a :class:`UefiAspects`.

            :param overlay: Optional aspects instance whose non-default
                            fields win over the base.

            :returns: A new frozen aspects instance.
        """
        if overlay is None:
            return self

        result = _merge_aspects(self, overlay)
        return result


# ---------------------------------------------------------------------------
# UefiCmdAspects
# ---------------------------------------------------------------------------

DEFAULT_EXPECTED_STATUS: int = 0


def _validate_expected_status(value: object) -> Union[int, Tuple[int, ...]]:
    """
        Validate and coerce the ``expected_status`` field.  A scalar ``int``
        is returned unchanged; an iterable of ints is returned as a tuple.

        :param value: Candidate value.

        :returns: Validated int or tuple of ints.
        :raises: ConfigurationError
    """
    if isinstance(value, int) and not isinstance(value, bool):
        return value

    try:
        candidate = tuple(value)  # type: ignore[arg-type]
    except TypeError as terr:
        err_msg_lines = [
            "'expected_status' must be an int or an iterable of ints.",
            "GOT: value={!r} type={}".format(value, type(value).__name__),
        ]
        err_msg = "\n".join(err_msg_lines)
        raise ConfigurationError(err_msg) from terr

    for index, element in enumerate(candidate):
        if not isinstance(element, int) or isinstance(element, bool):
            err_msg_lines = [
                "'expected_status' elements must be ints.",
                "INDEX: {}".format(index),
                "GOT: value={!r} type={}".format(element, type(element).__name__),
            ]
            err_msg = "\n".join(err_msg_lines)
            raise ConfigurationError(err_msg) from None

    result = tuple(int(x) for x in candidate)
    return result


def _validate_prompt_pattern(value: object) -> bytes:
    """
        Validate the ``prompt_pattern`` field; must be :class:`bytes`.

        :param value: Candidate value.

        :returns: The validated bytes value.
        :raises: ConfigurationError
    """
    if not isinstance(value, (bytes, bytearray)):
        err_msg_lines = [
            "'prompt_pattern' must be bytes.",
            "GOT: value={!r} type={}".format(value, type(value).__name__),
        ]
        err_msg = "\n".join(err_msg_lines)
        raise ConfigurationError(err_msg) from None

    result = bytes(value)
    return result


def _validate_response_encoding(value: object) -> str:
    """
        Validate that ``value`` is a name of a registered codec.

        :param value: Candidate value.

        :returns: The validated encoding name.
        :raises: ConfigurationError
    """
    if not isinstance(value, str):
        err_msg_lines = [
            "'response_encoding' must be a str.",
            "GOT: value={!r} type={}".format(value, type(value).__name__),
        ]
        err_msg = "\n".join(err_msg_lines)
        raise ConfigurationError(err_msg) from None

    try:
        codecs.lookup(value)
    except LookupError as lerr:
        err_msg_lines = [
            "'response_encoding' is not a registered codec.",
            "GOT: value={!r}".format(value),
        ]
        err_msg = "\n".join(err_msg_lines)
        raise ConfigurationError(err_msg) from lerr

    return value


def _validate_transport_kind(value: object) -> Optional[str]:
    """
        Validate the ``transport_kind`` field; accepts ``None`` or a value
        from :data:`VALID_TRANSPORT_KINDS`.

        :param value: Candidate value.

        :returns: The validated transport kind or ``None``.
        :raises: ConfigurationError
    """
    if value is None:
        return None

    if not isinstance(value, str):
        err_msg_lines = [
            "'transport_kind' must be a str or None.",
            "GOT: value={!r} type={}".format(value, type(value).__name__),
        ]
        err_msg = "\n".join(err_msg_lines)
        raise ConfigurationError(err_msg) from None

    if value not in VALID_TRANSPORT_KINDS:
        err_msg_lines = [
            "'transport_kind' is not a known transport kind.",
            "GOT: value={!r}".format(value),
            "ALLOWED: {}".format(", ".join(VALID_TRANSPORT_KINDS)),
        ]
        err_msg = "\n".join(err_msg_lines)
        raise ConfigurationError(err_msg) from None

    return value


class UefiCmdAspects(UefiAspects):
    """
        Extension of :class:`UefiAspects` with command-specific parameters
        (expected status codes, prompt pattern, response encoding, transport
        kind, user role).  Consumers of shell-style commands accept this type
        rather than the base :class:`UefiAspects`.
    """

    def __init__(self, expected_status: Union[int, Sequence[int]] = DEFAULT_EXPECTED_STATUS,
                       user: Optional[str] = None,
                       prompt_pattern: Optional[bytes] = None,
                       response_encoding: str = DEFAULT_RESPONSE_ENCODING,
                       transport_kind: Optional[str] = None,
                       action_pattern: ActionPattern = ActionPattern.SINGLE_CALL,
                       completion_timeout: float = DEFAULT_COMPLETION_TIMEOUT,
                       completion_interval: float = DEFAULT_COMPLETION_INTERVAL,
                       connection_timeout: float = DEFAULT_CONNECTION_TIMEOUT,
                       connection_interval: float = DEFAULT_CONNECTION_INTERVAL,
                       inactivity_timeout: float = DEFAULT_INACTIVITY_TIMEOUT,
                       inactivity_interval: float = DEFAULT_INACTIVITY_INTERVAL,
                       monitor_delay: float = DEFAULT_MONITOR_DELAY,
                       logging_pattern: LoggingPattern = DEFAULT_LOGGING_PATTERN,
                       retry_logging_interval: int = DEFAULT_RETRY_LOGGING_INTERVAL,
                       allowed_error_codes: Sequence[int] = DEFAULT_ALLOWED_ERROR_CODES,
                       allowed_connection_exceptions: Sequence[Type[BaseException]] = DEFAULT_ALLOWED_CONNECTION_EXCEPTIONS,
                       must_connect: bool = DEFAULT_MUST_CONNECT,
                       logger: Optional[logging.Logger] = None) -> None:
        """
            Create a :class:`UefiCmdAspects` instance.

            :param expected_status: Status code or sequence of acceptable status codes.
            :param user: Optional user role name used by multi-credential transports.
            :param prompt_pattern: Regex pattern (as bytes) that marks shell prompts.
                                   Defaults to :data:`DEFAULT_PROMPT_PATTERN`.
            :param response_encoding: Codec used to decode command output bytes.
            :param transport_kind: Optional transport selector override
                                   (``'serial'``, ``'redfish'``, ``'ssh-host'``,
                                   ``'ipmi-sol'``).
            :param action_pattern: See :class:`UefiAspects`.
            :param completion_timeout: See :class:`UefiAspects`.
            :param completion_interval: See :class:`UefiAspects`.
            :param connection_timeout: See :class:`UefiAspects`.
            :param connection_interval: See :class:`UefiAspects`.
            :param inactivity_timeout: See :class:`UefiAspects`.
            :param inactivity_interval: See :class:`UefiAspects`.
            :param monitor_delay: See :class:`UefiAspects`.
            :param logging_pattern: See :class:`UefiAspects`.
            :param retry_logging_interval: See :class:`UefiAspects`.
            :param allowed_error_codes: See :class:`UefiAspects`.
            :param allowed_connection_exceptions: See :class:`UefiAspects`.
            :param must_connect: See :class:`UefiAspects`.
            :param logger: See :class:`UefiAspects`.

            :raises: ConfigurationError
        """
        # Note: we bypass UefiAspects.__init__ to interleave validation of the
        # command-specific fields before the freeze step.  Freezable state is
        # still established exactly once at the end.
        Freezable.__init__(self)

        validated_action_pattern = _validate_action_pattern(action_pattern)

        validated_completion_timeout = _validate_positive_number("completion_timeout", completion_timeout)
        validated_completion_interval = _validate_positive_number("completion_interval", completion_interval)
        validated_connection_timeout = _validate_positive_number("connection_timeout", connection_timeout)
        validated_connection_interval = _validate_positive_number("connection_interval", connection_interval)
        validated_inactivity_timeout = _validate_positive_number("inactivity_timeout", inactivity_timeout)
        validated_inactivity_interval = _validate_positive_number("inactivity_interval", inactivity_interval)
        validated_monitor_delay = _validate_positive_number("monitor_delay", monitor_delay)
        validated_retry_logging_interval = _validate_positive_number("retry_logging_interval", retry_logging_interval)

        _validate_interval_vs_timeout("completion_interval", validated_completion_interval,
                                      "completion_timeout", validated_completion_timeout)
        _validate_interval_vs_timeout("connection_interval", validated_connection_interval,
                                      "connection_timeout", validated_connection_timeout)
        _validate_interval_vs_timeout("inactivity_interval", validated_inactivity_interval,
                                      "inactivity_timeout", validated_inactivity_timeout)

        validated_logging_pattern = _validate_logging_pattern(logging_pattern)
        validated_allowed_error_codes = _validate_allowed_error_codes(allowed_error_codes)
        validated_allowed_connection_exceptions = _validate_allowed_connection_exceptions(allowed_connection_exceptions)
        validated_must_connect = _validate_bool("must_connect", must_connect)

        if logger is None:
            resolved_logger = getEnhancedLogger()
        else:
            resolved_logger = _validate_logger(logger)

        validated_expected_status = _validate_expected_status(expected_status)

        if prompt_pattern is None:
            resolved_prompt_pattern = DEFAULT_PROMPT_PATTERN
        else:
            resolved_prompt_pattern = _validate_prompt_pattern(prompt_pattern)

        validated_response_encoding = _validate_response_encoding(response_encoding)
        validated_transport_kind = _validate_transport_kind(transport_kind)

        if user is not None and not isinstance(user, str):
            err_msg_lines = [
                "'user' must be a str or None.",
                "GOT: value={!r} type={}".format(user, type(user).__name__),
            ]
            err_msg = "\n".join(err_msg_lines)
            raise ConfigurationError(err_msg) from None

        self.action_pattern = validated_action_pattern
        self.completion_timeout = validated_completion_timeout
        self.completion_interval = validated_completion_interval
        self.connection_timeout = validated_connection_timeout
        self.connection_interval = validated_connection_interval
        self.inactivity_timeout = validated_inactivity_timeout
        self.inactivity_interval = validated_inactivity_interval
        self.monitor_delay = validated_monitor_delay
        self.logging_pattern = validated_logging_pattern
        self.retry_logging_interval = validated_retry_logging_interval
        self.allowed_error_codes = validated_allowed_error_codes
        self.allowed_connection_exceptions = validated_allowed_connection_exceptions
        self.must_connect = validated_must_connect
        self.logger = resolved_logger

        self.expected_status = validated_expected_status
        self.user = user
        self.prompt_pattern = resolved_prompt_pattern
        self.response_encoding = validated_response_encoding
        self.transport_kind = validated_transport_kind

        self.freeze()
        return

    def _comparable_fields(self) -> Tuple[object, ...]:
        """
            Include the command-specific fields in the equality / hash tuple.
        """
        base = UefiAspects._comparable_fields(self)
        extra = (
            self.expected_status,
            self.user,
            self.prompt_pattern,
            self.response_encoding,
            self.transport_kind,
        )
        result = base + extra
        return result

    def __repr__(self) -> str:
        """
            Render the extended field set.
        """
        lines = ["{}(".format(type(self).__name__)]
        field_names = [
            "action_pattern",
            "completion_timeout",
            "completion_interval",
            "connection_timeout",
            "connection_interval",
            "inactivity_timeout",
            "inactivity_interval",
            "monitor_delay",
            "logging_pattern",
            "retry_logging_interval",
            "allowed_error_codes",
            "allowed_connection_exceptions",
            "must_connect",
            "expected_status",
            "user",
            "prompt_pattern",
            "response_encoding",
            "transport_kind",
        ]
        for name in field_names:
            value = getattr(self, name)
            lines.append("    {}={!r},".format(name, value))
        lines.append(")")

        result = "\n".join(lines)
        return result


# ---------------------------------------------------------------------------
# Merge helper (module-level; supports cross-type merging)
# ---------------------------------------------------------------------------

_BASE_FIELDS: Tuple[str, ...] = (
    "action_pattern",
    "completion_timeout",
    "completion_interval",
    "connection_timeout",
    "connection_interval",
    "inactivity_timeout",
    "inactivity_interval",
    "monitor_delay",
    "logging_pattern",
    "retry_logging_interval",
    "allowed_error_codes",
    "allowed_connection_exceptions",
    "must_connect",
    "logger",
)

_CMD_FIELDS: Tuple[str, ...] = (
    "expected_status",
    "user",
    "prompt_pattern",
    "response_encoding",
    "transport_kind",
)

_BASE_DEFAULTS = {
    "action_pattern": ActionPattern.SINGLE_CALL,
    "completion_timeout": float(DEFAULT_COMPLETION_TIMEOUT),
    "completion_interval": float(DEFAULT_COMPLETION_INTERVAL),
    "connection_timeout": float(DEFAULT_CONNECTION_TIMEOUT),
    "connection_interval": float(DEFAULT_CONNECTION_INTERVAL),
    "inactivity_timeout": float(DEFAULT_INACTIVITY_TIMEOUT),
    "inactivity_interval": float(DEFAULT_INACTIVITY_INTERVAL),
    "monitor_delay": float(DEFAULT_MONITOR_DELAY),
    "logging_pattern": DEFAULT_LOGGING_PATTERN,
    "retry_logging_interval": float(DEFAULT_RETRY_LOGGING_INTERVAL),
    "allowed_error_codes": DEFAULT_ALLOWED_ERROR_CODES,
    "allowed_connection_exceptions": DEFAULT_ALLOWED_CONNECTION_EXCEPTIONS,
    "must_connect": DEFAULT_MUST_CONNECT,
}

_CMD_DEFAULTS = {
    "expected_status": DEFAULT_EXPECTED_STATUS,
    "user": None,
    "prompt_pattern": DEFAULT_PROMPT_PATTERN,
    "response_encoding": DEFAULT_RESPONSE_ENCODING,
    "transport_kind": None,
}


def _merge_aspects(base: UefiAspects, overlay: UefiAspects) -> UefiAspects:
    """
        Merge two aspects instances.  Fields whose ``overlay`` value equals
        the default are taken from ``base``; other fields are taken from
        ``overlay``.  The returned type is the more specific of the two
        (:class:`UefiCmdAspects` wins over :class:`UefiAspects`).

        :param base: Base aspects instance.
        :param overlay: Overlay aspects instance.

        :returns: A new frozen aspects instance.
    """
    result_is_cmd = isinstance(base, UefiCmdAspects) or isinstance(overlay, UefiCmdAspects)

    merged_kwargs: dict = {}
    for name in _BASE_FIELDS:
        if name == "logger":
            # Logger is never considered "default"; always take overlay's if it differs by identity.
            base_value = getattr(base, name)
            overlay_value = getattr(overlay, name)
            chosen = overlay_value if overlay_value is not base_value else base_value
            merged_kwargs[name] = chosen
            continue

        overlay_value = getattr(overlay, name)
        default_value = _BASE_DEFAULTS[name]
        if overlay_value == default_value:
            merged_kwargs[name] = getattr(base, name)
        else:
            merged_kwargs[name] = overlay_value

    if result_is_cmd:
        for name in _CMD_FIELDS:
            overlay_value = getattr(overlay, name, _CMD_DEFAULTS[name])
            default_value = _CMD_DEFAULTS[name]
            if overlay_value == default_value:
                base_value = getattr(base, name, _CMD_DEFAULTS[name])
                merged_kwargs[name] = base_value
            else:
                merged_kwargs[name] = overlay_value

        result = UefiCmdAspects(**merged_kwargs)
        return result

    result = UefiAspects(**merged_kwargs)
    return result


# ---------------------------------------------------------------------------
# resolve_aspects helper
# ---------------------------------------------------------------------------

def resolve_aspects(passed: Optional[UefiAspects], instance_default: Optional[UefiAspects]) -> UefiAspects:
    """
        Resolve the effective aspects instance for a call.  Returns ``passed``
        if non-None; otherwise ``instance_default`` if non-None; otherwise
        raises :class:`ConfigurationError`.

        :param passed: Aspects explicitly passed to the call.
        :param instance_default: Aspects held by the instance as a default.

        :returns: The effective aspects instance.
        :raises: ConfigurationError
    """
    if passed is not None:
        return passed

    if instance_default is not None:
        return instance_default

    err_msg_lines = [
        "resolve_aspects was called with both 'passed' and 'instance_default' set to None.",
        "At least one of the two arguments must be a UefiAspects (or subclass) instance.",
    ]
    err_msg = "\n".join(err_msg_lines)
    raise ConfigurationError(err_msg) from None


# ---------------------------------------------------------------------------
# Module-level default singletons
# ---------------------------------------------------------------------------

DEFAULT_ASPECTS: Final[UefiAspects] = UefiAspects()
DEFAULT_CMD_ASPECTS: Final[UefiCmdAspects] = UefiCmdAspects()

UNDER_CONSTRUCTION_CONNECTION_TIMEOUT = 60
UNDER_CONSTRUCTION_CONNECTION_INTERVAL = 5
UNDER_CONSTRUCTION_ALLOWED_EXCEPTIONS: Tuple[Type[BaseException], ...] = (
    ConnectionError,
    TimeoutError,
    OSError,
)

UNDER_CONSTRUCTION_ASPECTS: Final[UefiCmdAspects] = UefiCmdAspects(
    connection_timeout=UNDER_CONSTRUCTION_CONNECTION_TIMEOUT,
    connection_interval=UNDER_CONSTRUCTION_CONNECTION_INTERVAL,
    allowed_connection_exceptions=UNDER_CONSTRUCTION_ALLOWED_EXCEPTIONS,
)

DEFAULT_UEFI_ASPECTS: Final[UefiCmdAspects] = DEFAULT_CMD_ASPECTS
