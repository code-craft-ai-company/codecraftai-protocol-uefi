# Requirements — Exceptions

## Introduction

The Exceptions feature establishes the UEFI-specific exception hierarchy
used by every higher layer in `automojo-uefi`.  It layers on top of the
shared `automojo.errors.exceptions` module — reusing `ConfigurationError` and
`SemanticError` — and adds a set of UEFI-typed classes (`UefiError`,
`UefiShellError`, `UefiVariableError`, `UefiCapsuleError`,
`UefiSecureBootError`, `UefiTransportError`, `UefiProtocolError`) that carry
the domain-specific context needed for diagnosis.

Every exception in this feature accepts a `details: list[str]` keyword
argument and joins those details with `os.linesep` in `__str__`, matching the
repository's multiline-error-message coding standard.  Typed subclasses
surface the fields a diagnostic reader needs: `UefiShellError` carries
`command`, `status`, `stdout`, `stderr`; `UefiVariableError` carries `name`,
`vendor_guid`, `attributes`, `efi_status`.  Every class supports an
`as_dict()` round-trip for structured-logging consumers.

`SemanticError` from the shared module is *never* caught in this library —
this feature does not introduce a UEFI-specific subclass of it, and the
design documents the rule explicitly.

## Requirements

### Requirement 1 — UefiError Root Class

**User Story:** As a diagnostician, I want every exception raised by this
library (other than the shared `ConfigurationError` and `SemanticError`) to
derive from a single root class, so that I can catch all library errors with
one `except` clause when triaging.

#### Acceptance Criteria

1. WHEN the caller imports `UefiError` THEN the system SHALL expose it as a
   direct subclass of `automojo.errors.exceptions.AutomojoError` (or the
   shared root, whatever its canonical name is).
2. WHERE any subclass defined by this feature is raised the system SHALL
   expose it as an instance of `UefiError` via
   `isinstance(exc, UefiError)`.
3. WHEN the caller constructs `UefiError(message)` with only a message THEN
   the system SHALL store `message` on `self.args[0]` exactly as the built-in
   `Exception` does.
4. IF the caller passes `details=[...]` as a kwarg THEN the system SHALL
   store the list as `self.details` and SHALL NOT mutate the caller's list.

### Requirement 2 — Multiline `__str__` Rendering

**User Story:** As a log reader, I want multi-line error messages to render
with real newlines, so that long diagnostic context is not mashed into a
single line.

#### Acceptance Criteria

1. WHEN `str(exc)` is invoked on any UEFI exception whose `details` is
   non-empty THEN the system SHALL return `message + os.linesep +
   os.linesep.join(details)`.
2. WHEN `str(exc)` is invoked on an exception whose `details` is empty THEN
   the system SHALL return `message` unchanged.
3. WHERE the caller passes `details=None` or omits `details` the system
   SHALL treat it as the empty list for rendering purposes.
4. IF the caller passes a non-list for `details` THEN the system SHALL raise
   `TypeError` at construction time whose message names the observed type.

### Requirement 3 — UefiShellError Context

**User Story:** As a test author debugging a failed shell command, I want
the exception to carry the original command, its exit status, and both
output streams, so that I do not have to scroll back through logs.

#### Acceptance Criteria

1. WHEN the caller constructs `UefiShellError(message, command=..., status=...,
   stdout=..., stderr=...)` THEN the system SHALL store each field on the
   instance under the matching attribute name.
2. WHEN any of `command`, `status`, `stdout`, or `stderr` is omitted THEN the
   system SHALL default the missing field to `None`.
3. IF `status` is provided and is not an integer or `None` THEN the system
   SHALL raise `TypeError`.
4. WHEN `str(exc)` is invoked THEN the system SHALL include the command,
   status, and the first N lines of stdout / stderr (N configured by the
   module-level `SHELL_ERROR_OUTPUT_PREVIEW_LINES` constant, default 40) in
   the rendered output.
5. WHEN `as_dict()` is invoked THEN the system SHALL include `command`,
   `status`, `stdout`, and `stderr` keys.

### Requirement 4 — UefiVariableError Context

**User Story:** As a test author debugging a variable-services call, I want
the exception to carry the variable name, vendor GUID, attribute flags, and
EFI status so I do not have to correlate them from separate log lines.

#### Acceptance Criteria

1. WHEN the caller constructs `UefiVariableError(message, name=..., vendor_guid=...,
   attributes=..., efi_status=...)` THEN the system SHALL store each field
   under the matching attribute name.
2. WHEN any of those fields is omitted THEN the system SHALL default it to
   `None`.
3. IF `vendor_guid` is provided and is not a `uuid.UUID` THEN the system
   SHALL raise `TypeError`.
4. IF `efi_status` is provided and is not an `int` THEN the system SHALL
   raise `TypeError`.
5. WHEN `as_dict()` is invoked THEN the system SHALL include `name`,
   `vendor_guid` (rendered as canonical string form), `attributes`, and
   `efi_status` keys.

### Requirement 5 — UefiCapsuleError, UefiSecureBootError, UefiTransportError, UefiProtocolError

**User Story:** As a diagnostician, I want typed exception classes for
capsule delivery, Secure Boot state, transport-level faults, and protocol
framing errors, so that I can write tight `except` clauses per domain.

#### Acceptance Criteria

1. WHEN the caller imports `UefiCapsuleError`, `UefiSecureBootError`,
   `UefiTransportError`, or `UefiProtocolError` THEN the system SHALL
   expose each as a direct subclass of `UefiError`.
2. WHEN any of the above is raised THEN the system SHALL be catchable
   either by its concrete type or by `UefiError`.
3. WHERE a higher-layer feature needs to tag a capsule-flow error the system
   SHALL use `UefiCapsuleError` rather than a generic `UefiError`.
4. WHERE a transport layer raises an error for a connection-level fault the
   system SHALL use `UefiTransportError` rather than `ConnectionError`
   directly, unless the caller supplied `allowed_connection_exceptions` in
   aspects that includes one but not the other.

### Requirement 6 — `as_dict()` Serialization

**User Story:** As a structured-logging integrator, I want each exception
class to serialize to a plain dict of JSON-compatible values, so that I can
forward the error payload to a log aggregator without bespoke adapters.

#### Acceptance Criteria

1. WHEN `exc.as_dict()` is invoked on any exception defined by this feature
   THEN the system SHALL return a `dict[str, Any]` whose keys are strings.
2. WHERE a field is `None` the system SHALL include the key with value
   `None` rather than omit it.
3. WHERE a field is a `uuid.UUID` the system SHALL render it as its
   canonical lowercase string form.
4. WHERE a field is `bytes` the system SHALL render it as a hex string.
5. WHEN the returned dict is serialized via `json.dumps` with a standard
   encoder THEN the system SHALL produce valid JSON.
6. WHEN two instances of the same class are constructed with identical
   arguments THEN the system SHALL produce equal `as_dict()` outputs.

### Requirement 7 — Immutability of `details`

**User Story:** As a library consumer, I want an exception's `details` to be
safe to keep a reference to, so that later mutation by other code cannot
change what I already logged.

#### Acceptance Criteria

1. WHEN `UefiError(details=some_list)` is constructed THEN the system SHALL
   copy the list into a new `list[str]` before storing it.
2. IF the caller later mutates their original list THEN the exception
   instance's `details` SHALL remain unchanged.
3. WHEN the caller reads `exc.details` THEN the system SHALL return the
   stored list (mutation by the reader is tolerated — this is Python, not a
   freeze contract).

### Requirement 8 — `SemanticError` Non-Catching Rule

**User Story:** As a library maintainer, I want the hierarchy here to
explicitly NOT introduce any subclass of `SemanticError`, so that the
never-catch rule from the repository coding standards cannot be accidentally
violated.

#### Acceptance Criteria

1. WHEN the test suite scans the module's defined classes THEN the system
   SHALL find zero classes that are `issubclass(..., SemanticError)`.
2. WHEN the documentation is read THEN the system SHALL contain a rule
   stating that `SemanticError` is raised from higher layers directly using
   the shared class and is never caught in this library.

### Requirement 9 — Exception Pickling

**User Story:** As a distributed-test integrator, I want every exception
here to pickle and unpickle losslessly, so that I can forward errors across
process boundaries.

#### Acceptance Criteria

1. WHEN any exception defined by this feature is pickled and unpickled THEN
   the system SHALL reproduce `exc.args`, `exc.details`, and all typed
   context attributes exactly.
2. WHEN the caller compares `str(original) == str(unpickled)` THEN the
   system SHALL return `True`.

### Requirement 10 — Stable Module Surface

**User Story:** As a consumer, I want the public surface of the exceptions
module to be exhaustively listed in `__all__`, so that adding a new
exception class is a visible diff.

#### Acceptance Criteria

1. WHEN `uefiexceptions` is imported THEN the system SHALL expose
   `__all__` as a tuple of class names.
2. WHEN a new exception class is added to the module THEN the developer
   SHALL add it to `__all__` in the same commit.
3. WHERE a private helper is defined the system SHALL prefix its name with a
   single underscore and SHALL NOT include it in `__all__`.

### Requirement 11 — Documentation of Field Semantics

**User Story:** As a test author, I want each typed exception field to have
a Sphinx-style docstring, so that IDE hover documentation is sufficient
without opening the source file.

#### Acceptance Criteria

1. WHERE a class defines a typed field the system SHALL document the field
   with `:param field:` in the class docstring.
2. WHERE a class overrides `__str__` or `as_dict` the system SHALL document
   the override with `:returns:` in the method docstring.
3. WHEN pydoc is generated for this module THEN the system SHALL include
   every public field's description.
