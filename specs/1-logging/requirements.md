# Requirements — Logging

## Introduction

The Logging feature provides a thin integration layer with the shared
`automojo.xmodules.xlogging` package.  It exposes a single factory,
`get_uefi_logger(channel)`, that returns a configured logger bound to a
channel under the `uefi.*` namespace; re-exports `LoggingPattern` from
`1-aspects` as a convenience so log-call sites do not have to reach across
packages; and documents the set of named channels the rest of the library
emits on.

Every log line produced by `automojo-uefi` must be attributable to a named
channel under `uefi.*`.  The documented channels are `uefi.shell`,
`uefi.variable`, `uefi.capsule`, `uefi.transport`, `uefi.bootmgr`,
`uefi.secureboot`, `uefi.devicepath`, `uefi.gpt`, `uefi.esp`,
`uefi.reset`, `uefi.hii`, `uefi.redfish`, and `uefi.tpm`.  The feature
does not prescribe log levels or formatters — those come from the shared
logging configuration.

The feature has no dependencies on other `automojo-uefi` features other
than re-exporting `LoggingPattern` from `1-aspects`.

## Requirements

### Requirement 1 — `get_uefi_logger` Factory

**User Story:** As a library author writing a new interop method, I want to
call a single-argument factory to obtain a logger bound to the right
channel, so that I do not have to spell out the full shared-logging API at
every call site.

#### Acceptance Criteria

1. WHEN the caller invokes `get_uefi_logger("shell")` THEN the system SHALL
   return a `logging.Logger` (or compatible enhanced logger) whose name is
   `"uefi.shell"`.
2. WHEN the caller invokes `get_uefi_logger("uefi.shell")` THEN the system
   SHALL return the same logger as `get_uefi_logger("shell")` — the
   `uefi.` prefix SHALL be added if missing and preserved if present.
3. IF the `channel` argument is not a string THEN the system SHALL raise
   `TypeError` whose message names the observed type.
4. IF the `channel` argument resolves to a name outside the
   `KNOWN_UEFI_CHANNELS` set (see Requirement 2) and the
   `strict: bool = True` kwarg is in effect THEN the system SHALL raise
   `ConfigurationError` whose message lists the known channels.
5. WHEN the caller invokes `get_uefi_logger("shell", strict=False)` with an
   unknown channel THEN the system SHALL return a logger and emit a
   `WARNING` on channel `uefi.logging` noting the untracked name.
6. WHEN the caller invokes `get_uefi_logger("shell")` twice THEN the system
   SHALL return the same underlying logger instance (the logging module's
   identity-by-name behavior).

### Requirement 2 — Known Channel Registry

**User Story:** As a maintainer, I want the list of valid `uefi.*` channels
to live in one place, so that adding a new channel is a single-file diff.

#### Acceptance Criteria

1. WHEN `KNOWN_UEFI_CHANNELS` is imported THEN the system SHALL return a
   `frozenset[str]` containing exactly the following thirteen names:
   `"uefi.shell"`, `"uefi.variable"`, `"uefi.capsule"`,
   `"uefi.transport"`, `"uefi.bootmgr"`, `"uefi.secureboot"`,
   `"uefi.devicepath"`, `"uefi.gpt"`, `"uefi.esp"`, `"uefi.reset"`,
   `"uefi.hii"`, `"uefi.redfish"`, `"uefi.tpm"`.
2. WHEN a new channel is added by a future feature THEN the author SHALL
   update `KNOWN_UEFI_CHANNELS` in the same commit.
3. IF a caller mutates `KNOWN_UEFI_CHANNELS` THEN the system SHALL raise
   `AttributeError` because the set is frozen.

### Requirement 3 — Constant Shortcuts per Channel

**User Story:** As a library author, I want a `CHANNEL_*` constant per
documented channel, so that IDE autocomplete guides me to a valid name.

#### Acceptance Criteria

1. WHERE a channel is listed in `KNOWN_UEFI_CHANNELS` the system SHALL
   expose a matching module-level constant whose name is the channel's
   suffix uppercased (`CHANNEL_SHELL = "uefi.shell"`, `CHANNEL_VARIABLE =
   "uefi.variable"`, and so on for all thirteen channels).
2. WHEN a test iterates the module's `dir()` the system SHALL expose
   thirteen such constants plus `KNOWN_UEFI_CHANNELS` itself.

### Requirement 4 — Re-export of `LoggingPattern`

**User Story:** As a library author, I want to import `LoggingPattern` from
the same module I call `get_uefi_logger`, so that log-related imports stay
co-located.

#### Acceptance Criteria

1. WHEN the caller imports `LoggingPattern` from
   `automojo.protocols.uefi.logging` THEN the system SHALL return the same
   class that `1-aspects` exposes.
2. WHERE this feature re-exports `LoggingPattern` the system SHALL NOT wrap
   or subclass it.

### Requirement 5 — Logger Duck-Type Sanity

**User Story:** As a structured-logging integrator, I want an assurance
that the returned logger has the standard method set (`debug`, `info`,
`warning`, `error`, `critical`), so that my downstream code does not
encounter a surprise missing method.

#### Acceptance Criteria

1. WHEN `get_uefi_logger(channel)` returns a logger THEN the system SHALL
   guarantee the returned object has attributes `debug`, `info`,
   `warning`, `error`, and `critical` each bound to a callable.
2. IF the underlying shared-logging factory returns an object lacking any
   of those attributes THEN the system SHALL raise
   `ConfigurationError` whose message lists the missing methods.

### Requirement 6 — Smoke Test per Channel

**User Story:** As a maintainer, I want a smoke test that exercises every
documented channel, so that a silent breakage of the shared-logging
integration is caught early.

#### Acceptance Criteria

1. WHEN the smoke test runs THEN the system SHALL invoke
   `get_uefi_logger(channel)` for every member of `KNOWN_UEFI_CHANNELS`
   and SHALL assert the result has the required method set.
2. WHEN the smoke test runs THEN the system SHALL emit one `INFO` log call
   per channel using `caplog`-style capture and verify that the captured
   record's name equals the requested channel name.

### Requirement 7 — Idempotent Module Import

**User Story:** As an integrator, I want importing this module to have no
observable side effect beyond binding the public names, so that library
startup cost stays bounded.

#### Acceptance Criteria

1. WHEN `automojo.protocols.uefi.logging` is imported THEN the system
   SHALL NOT open files, sockets, subprocesses, or register global handlers.
2. WHEN the module is imported twice in the same process THEN the system
   SHALL produce identical `KNOWN_UEFI_CHANNELS` set identity and identical
   constant values.

### Requirement 8 — Stable Module Surface

**User Story:** As a consumer, I want `__all__` to enumerate the full
public surface, so that adding a new channel is a visible diff.

#### Acceptance Criteria

1. WHEN the caller imports the module THEN the system SHALL expose
   `__all__` containing `get_uefi_logger`, `LoggingPattern`,
   `KNOWN_UEFI_CHANNELS`, and every `CHANNEL_*` constant.
2. WHERE a helper is private the system SHALL prefix its name with a
   single underscore and SHALL NOT include it in `__all__`.

### Requirement 9 — Strict-Mode Default

**User Story:** As a maintainer, I want `get_uefi_logger` to default to
strict-mode channel validation, so that typos at call sites are caught.

#### Acceptance Criteria

1. WHEN `get_uefi_logger(channel)` is called without `strict=` THEN the
   system SHALL default `strict` to `True`.
2. WHEN the caller wants to relax strict-mode for an experimental channel
   THEN the caller SHALL pass `strict=False` explicitly.
3. WHEN strict-mode is in effect and the channel is unknown THEN the system
   SHALL raise `ConfigurationError` before returning a logger.

### Requirement 10 — Consistent Log-Result Rendering

**User Story:** As a diagnostician, I want the `LoggingPattern` re-export to
be the same class consumers use to decide what to log, so that switching
between success-only and failure-only rendering is a single-line change.

#### Acceptance Criteria

1. WHEN the caller imports `LoggingPattern` from this module THEN the
   system SHALL return a class with members `ALL_RESULTS`, `FAILURE_ONLY`,
   and `SUCCESS_ONLY`.
2. WHEN consumers in higher layers consult aspects for a
   `LoggingPattern` value THEN the system SHALL use the same class
   identity as re-exported here.
