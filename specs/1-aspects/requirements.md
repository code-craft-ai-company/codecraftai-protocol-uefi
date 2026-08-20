# Requirements — Aspects

## Introduction

The Aspects feature provides `UefiAspects` and `UefiCmdAspects` — `Freezable`
behavior-package classes that callers use to control the *measured-interop*
behavior of every higher layer in the library.  Aspects package together
action pattern (retry semantics), completion timeouts, connection timeouts,
inactivity timeouts, logging pattern, allowed error codes, allowed connection
exceptions, and an optional logger into a single parameter that can be passed
to any public interop API.

Aspects are the contract by which callers decide *how persistent* the library
should be when interacting with a UEFI target — one-shot, retry-until-success,
retry-while-success, or retry-until-connection-fails.  This mirrors the
`codecraftai-protocol-ssh` library's `SshAspects` / `SshCmdAspects` pattern
exactly so that callers who already use that library can transfer their mental
model directly.

This feature has no dependencies on other features and forms the vocabulary
that every layer 3, 4, 5, and 6 feature reads from.

## Requirements

### Requirement 1 — Freezable Aspects Containers

**User Story:** As a test author, I want to build aspects objects once at
module load time and share them across many calls, so that I do not pay
construction cost per call and can trust the aspects are tamper-proof.

#### Acceptance Criteria

1. WHEN `UefiAspects.__init__` returns THEN the system SHALL mark the instance
   as frozen.
2. IF a caller assigns any attribute on a frozen `UefiAspects` or
   `UefiCmdAspects` instance THEN the system SHALL raise `AttributeError` with
   a message naming the attribute and indicating the instance is frozen.
3. WHEN `UefiCmdAspects.__init__` returns THEN the system SHALL mark the
   instance as frozen.
4. WHERE the caller subclasses `Freezable` the system SHALL inherit the
   freeze-after-construction behavior without additional code in the subclass
   beyond calling `self.freeze()` at the end of `__init__`.

### Requirement 2 — Action Pattern Support

**User Story:** As a test author, I want to choose the retry pattern for each
operation, so that I can make appropriate trade-offs between fast-fail and
resilience per call.

#### Acceptance Criteria

1. WHEN the caller constructs `UefiAspects(action_pattern=ActionPattern.SINGLE_CALL)`
   THEN the system SHALL accept it without error.
2. WHEN the caller constructs `UefiAspects(action_pattern=ActionPattern.SINGLE_CONNECTED_CALL)`
   THEN the system SHALL accept it without error.
3. WHEN the caller constructs `UefiAspects(action_pattern=ActionPattern.DO_UNTIL_SUCCESS)`
   THEN the system SHALL accept it without error.
4. WHEN the caller constructs `UefiAspects(action_pattern=ActionPattern.DO_WHILE_SUCCESS)`
   THEN the system SHALL accept it without error.
5. WHEN the caller constructs `UefiAspects(action_pattern=ActionPattern.DO_UNTIL_CONNECTION_FAILURE)`
   THEN the system SHALL accept it without error.
6. IF the caller passes a non-`ActionPattern` value as `action_pattern` THEN
   the system SHALL raise `ConfigurationError` with `details` identifying the
   offending value and type.
7. WHEN `ActionPattern` is imported THEN the system SHALL expose exactly five
   members named above and no others.

### Requirement 3 — Timeout and Interval Parameters

**User Story:** As a test author, I want to specify every retry timeout and
interval explicitly, so that there are no hidden hard-coded waits in the
interop path.

#### Acceptance Criteria

1. WHEN the caller omits `completion_timeout` THEN the system SHALL default to
   `DEFAULT_COMPLETION_TIMEOUT` (600 seconds).
2. WHEN the caller omits `completion_interval` THEN the system SHALL default
   to `DEFAULT_COMPLETION_INTERVAL` (10 seconds).
3. WHEN the caller omits `connection_timeout` THEN the system SHALL default
   to `DEFAULT_CONNECTION_TIMEOUT` (16 seconds).
4. WHEN the caller omits `connection_interval` THEN the system SHALL default
   to `DEFAULT_CONNECTION_INTERVAL` (2 seconds).
5. WHEN the caller omits `inactivity_timeout` THEN the system SHALL default
   to `DEFAULT_INACTIVITY_TIMEOUT` (600 seconds).
6. WHEN the caller omits `inactivity_interval` THEN the system SHALL default
   to `DEFAULT_INACTIVITY_INTERVAL` (0.5 seconds).
7. WHEN the caller omits `monitor_delay` THEN the system SHALL default to
   `DEFAULT_MONITOR_DELAY` (30 seconds).
8. IF any timeout or interval value is less than or equal to zero THEN the
   system SHALL raise `ConfigurationError`.
9. IF any interval value is greater than its paired timeout THEN the system
   SHALL raise `ConfigurationError` with `details` naming both fields.

### Requirement 4 — Logging Pattern

**User Story:** As a test author, I want to choose whether successes,
failures, or both are logged, so that my test output is not drowned by noise
from healthy retry loops.

#### Acceptance Criteria

1. WHEN `LoggingPattern` is imported THEN the system SHALL expose exactly the
   members `ALL_RESULTS`, `FAILURE_ONLY`, and `SUCCESS_ONLY`.
2. WHEN the caller omits `logging_pattern` THEN the system SHALL default to
   `LoggingPattern.ALL_RESULTS`.
3. IF the caller passes a non-`LoggingPattern` value as `logging_pattern`
   THEN the system SHALL raise `ConfigurationError`.

### Requirement 5 — Allowed Error Codes and Connection Exceptions

**User Story:** As a test author, I want to declare which error codes and
connection exceptions are tolerable for a given operation, so that retry
loops do not either swallow real failures or give up on transient ones.

#### Acceptance Criteria

1. WHEN the caller omits `allowed_error_codes` THEN the system SHALL default
   to an empty tuple.
2. WHEN the caller omits `allowed_connection_exceptions` THEN the system
   SHALL default to an empty tuple.
3. WHEN the caller passes a list for either field THEN the system SHALL
   convert it to a tuple before freezing.
4. IF the caller passes a non-iterable value for either field THEN the system
   SHALL raise `ConfigurationError`.
5. IF any element of `allowed_connection_exceptions` is not a subclass of
   `BaseException` THEN the system SHALL raise `ConfigurationError` with
   `details` identifying the offending element.

### Requirement 6 — Must-Connect Flag

**User Story:** As a test author, I want to declare that connection failures
must be raised rather than silently retried, so that destructive operations
can never proceed partially-connected.

#### Acceptance Criteria

1. WHEN the caller omits `must_connect` THEN the system SHALL default to
   `False`.
2. WHEN `must_connect=True` THEN consumers of this aspects instance SHALL
   raise `ConnectionError` on connection failure rather than retrying.

### Requirement 7 — Logger Injection

**User Story:** As an integrator, I want to inject my own logger so that
library output routes through my structured logging pipeline.

#### Acceptance Criteria

1. WHEN the caller omits `logger` THEN the system SHALL obtain one from
   `ccai.xmodules.xlogging.enhancedloggers.getEnhancedLogger()`.
2. WHEN the caller provides a `logging.Logger` (or compatible duck-type)
   THEN the system SHALL store it as-is without wrapping.
3. IF the caller provides a value that is not logger-shaped (lacks `info`,
   `error`, `warning`, `debug`) THEN the system SHALL raise
   `ConfigurationError`.

### Requirement 8 — Command-Specific Aspects Extension

**User Story:** As a test author, I want command-oriented operations to accept
additional parameters such as the expected status code and UEFI Shell prompt
pattern, so that per-command configuration does not leak into general
`UefiAspects`.

#### Acceptance Criteria

1. WHEN `UefiCmdAspects` is constructed without `expected_status` THEN the
   system SHALL default to the integer `0`.
2. WHEN the caller passes `expected_status` as `int` THEN the system SHALL
   store it unchanged.
3. WHEN the caller passes `expected_status` as a sequence of ints THEN the
   system SHALL store it as a `tuple[int, ...]`.
4. IF `expected_status` contains a non-integer THEN the system SHALL raise
   `ConfigurationError`.
5. WHEN the caller omits `prompt_pattern` THEN the system SHALL default to
   the module constant `DEFAULT_PROMPT_PATTERN`.
6. WHEN the caller omits `response_encoding` THEN the system SHALL default to
   `"utf-8"`.
7. WHEN the caller omits `user` THEN the system SHALL default to `None`.
8. WHEN the caller omits `transport_kind` THEN the system SHALL default to
   `None` (meaning "use the instance default").

### Requirement 9 — Deterministic Defaults

**User Story:** As a maintainer, I want the default aspects instances to be
byte-identical across imports and processes, so that tests and diagnostics
can rely on them.

#### Acceptance Criteria

1. WHEN `DEFAULT_ASPECTS`, `DEFAULT_CMD_ASPECTS`, or
   `UNDER_CONSTRUCTION_ASPECTS` is imported more than once THEN the system
   SHALL return the same instance object each time.
2. WHEN the caller constructs `UefiAspects()` with no arguments twice in the
   same process THEN each resulting instance SHALL compare equal under
   `==`.
3. WHEN the caller constructs `UefiCmdAspects()` with no arguments twice in
   the same process THEN each resulting instance SHALL compare equal under
   `==`.

### Requirement 10 — Merge Helper

**User Story:** As a test author, I want to take a preset aspects object and
tweak one or two fields without re-stating every field, so that my call
sites stay readable.

#### Acceptance Criteria

1. WHEN the caller invokes `base_aspects.merge(overlay)` THEN the system
   SHALL return a new frozen `UefiCmdAspects` (or `UefiAspects`) whose field
   values are taken from `overlay` where `overlay` differs from its default
   and from `base_aspects` otherwise.
2. WHEN `base_aspects` and `overlay` are different concrete types (e.g.,
   `UefiAspects` merged with `UefiCmdAspects`) THEN the system SHALL return
   an instance of the more specific type.
3. IF the caller invokes `base.merge(None)` THEN the system SHALL return
   `base` unchanged.
4. WHEN the resulting merged instance is inspected THEN the system SHALL
   reflect the merged values and SHALL itself be frozen.

### Requirement 11 — Aspects-None Passthrough

**User Story:** As a consumer in layers 3–6, I want to be able to accept
`aspects=None` at any public API and interpret that as "use the instance
default," so that callers are never forced to repeat themselves.

#### Acceptance Criteria

1. WHEN any consumer module calls
   `resolve_aspects(passed_aspects, instance_default)` with `passed_aspects
   is None` THEN the system SHALL return `instance_default`.
2. WHEN both arguments are non-None THEN the system SHALL return
   `passed_aspects`.
3. IF both arguments are `None` THEN the system SHALL raise
   `ConfigurationError`.
