# Design — Aspects

## Overview

The Aspects feature delivers two `Freezable` dataclass-style classes —
`UefiAspects` and `UefiCmdAspects` — along with two enums (`ActionPattern`,
`LoggingPattern`), a set of module-level default singletons, a `merge`
helper on each class, and a `resolve_aspects` convenience.  The classes are
constructed once and then frozen so they can safely be pinned as module-level
constants and passed across threads.

The design closely mirrors `codecraftai-protocol-ssh`'s `sshaspects.py`
(`/Users/myron/repos/codecraftai-protocol-ssh/source/packages/ccai/protocols/ssh/sshaspects.py`)
so that callers familiar with the SSH library transfer their mental model
without friction.  Differences from the SSH version are intentional and
listed in the "Differences from SshAspects" section below.

## Architecture

```
ccai.protocols.uefi.aspects/
    __init__.py        # re-exports public surface
    freezable.py       # Freezable base class
    patterns.py        # ActionPattern, LoggingPattern enums
    aspects.py         # UefiAspects
    cmdaspects.py      # UefiCmdAspects
    defaults.py        # module-level frozen singletons
    resolver.py        # resolve_aspects() helper
```

Split into multiple modules rather than one monolithic file so each concept
is independently testable and does not import more than it needs.

Dependency direction (nothing imports upward):

```
defaults.py  ──▶  cmdaspects.py  ──▶  aspects.py  ──▶  freezable.py
                                                       patterns.py
resolver.py  ──▶  aspects.py
```

## Components and Interfaces

### `Freezable` (freezable.py)

```python
class Freezable:
    def __init__(self) -> None:
        object.__setattr__(self, "__frozen__", False)
        return

    def freeze(self) -> None:
        object.__setattr__(self, "__frozen__", True)
        return

    def __setattr__(self, name: str, value: object) -> None:
        if getattr(self, "__frozen__", False):
            err_msg = f"Freezable instance is frozen; cannot set attribute '{name}'."
            raise AttributeError(err_msg)
        object.__setattr__(self, name, value)
        return
```

### `ActionPattern` and `LoggingPattern` (patterns.py)

```python
class ActionPattern(IntEnum):
    SINGLE_CALL = 0
    SINGLE_CONNECTED_CALL = 1
    DO_UNTIL_SUCCESS = 2
    DO_WHILE_SUCCESS = 3
    DO_UNTIL_CONNECTION_FAILURE = 4


class LoggingPattern(IntEnum):
    ALL_RESULTS = 0
    FAILURE_ONLY = 1
    SUCCESS_ONLY = 2
```

Both are `IntEnum` so they are cheap to hash and stable across pickle.

### `UefiAspects` (aspects.py)

Public constructor signature:

```python
class UefiAspects(Freezable):
    def __init__(
        self,
        action_pattern: ActionPattern = ActionPattern.SINGLE_CALL,
        completion_timeout: float = DEFAULT_COMPLETION_TIMEOUT,
        completion_interval: float = DEFAULT_COMPLETION_INTERVAL,
        connection_timeout: float = DEFAULT_CONNECTION_TIMEOUT,
        connection_interval: float = DEFAULT_CONNECTION_INTERVAL,
        inactivity_timeout: float = DEFAULT_INACTIVITY_TIMEOUT,
        inactivity_interval: float = DEFAULT_INACTIVITY_INTERVAL,
        monitor_delay: float = DEFAULT_MONITOR_DELAY,
        logging_pattern: LoggingPattern = LoggingPattern.ALL_RESULTS,
        retry_logging_interval: int = DEFAULT_RETRY_LOGGING_INTERVAL,
        allowed_error_codes: Sequence[int] = (),
        allowed_connection_exceptions: Sequence[type[BaseException]] = (),
        must_connect: bool = False,
        logger: Optional[logging.Logger] = None,
    ) -> None: ...
```

All fields are public read-only attributes after construction.  The class
exposes:

* `merge(overlay: Optional["UefiAspects"]) -> "UefiAspects"` — returns a new
  frozen instance.
* `__eq__` / `__hash__` — field-wise equality based on the frozen tuple of
  values.
* `__repr__` — renders each field on its own line, aligned for readability.

Validation performed in `__init__` (in this order):

1. Validate `action_pattern` is an `ActionPattern`.
2. Validate each numeric field is > 0 (raise `ConfigurationError`).
3. Validate `completion_interval <= completion_timeout`,
   `connection_interval <= connection_timeout`,
   `inactivity_interval <= inactivity_timeout`.
4. Validate `logging_pattern` is a `LoggingPattern`.
5. Coerce `allowed_error_codes` to `tuple[int, ...]` and validate all ints.
6. Coerce `allowed_connection_exceptions` to tuple of types and validate
   each is a `BaseException` subclass.
7. Validate `must_connect` is `bool`.
8. Validate `logger` duck-types (has `info`/`error`/`warning`/`debug`).
9. Call `self.freeze()`.

### `UefiCmdAspects` (cmdaspects.py)

Extends `UefiAspects` with four additional fields:

```python
class UefiCmdAspects(UefiAspects):
    def __init__(
        self,
        expected_status: Union[int, Sequence[int]] = 0,
        user: Optional[str] = None,
        prompt_pattern: Optional[bytes] = None,   # defaults to DEFAULT_PROMPT_PATTERN
        response_encoding: str = "utf-8",
        transport_kind: Optional[str] = None,
        # ...all UefiAspects fields repeated here for explicitness...
    ) -> None: ...
```

Additional validation:

* `expected_status` coerced to either `int` or `tuple[int, ...]`.
* `prompt_pattern`, when not `None`, must be `bytes`.
* `response_encoding` must be a valid codec name (validate via `codecs.lookup`).
* `transport_kind`, when not `None`, must match one of the registered
  transport-kind string literals (`"serial"`, `"redfish"`, `"ssh-host"`,
  `"ipmi-sol"`).

### `DEFAULT_*` Constants (defaults.py)

```python
DEFAULT_COMPLETION_TIMEOUT = 600
DEFAULT_COMPLETION_INTERVAL = 10
DEFAULT_CONNECTION_TIMEOUT = 16
DEFAULT_CONNECTION_INTERVAL = 2
DEFAULT_INACTIVITY_TIMEOUT = 600
DEFAULT_INACTIVITY_INTERVAL = 0.5
DEFAULT_MONITOR_DELAY = 30
DEFAULT_RETRY_LOGGING_INTERVAL = 5
DEFAULT_PROMPT_PATTERN = rb"Shell>\s*$"

DEFAULT_ASPECTS: Final[UefiAspects] = UefiAspects()
DEFAULT_CMD_ASPECTS: Final[UefiCmdAspects] = UefiCmdAspects()

UNDER_CONSTRUCTION_ASPECTS: Final[UefiCmdAspects] = UefiCmdAspects(
    connection_timeout=60,
    connection_interval=5,
    allowed_connection_exceptions=(ConnectionError, TimeoutError, OSError),
)
```

Imported once at module load, then re-exported by `__init__.py`.

### `resolve_aspects` (resolver.py)

```python
def resolve_aspects(
    passed: Optional[UefiCmdAspects],
    instance_default: Optional[UefiCmdAspects],
) -> UefiCmdAspects:
    if passed is not None:
        return passed
    if instance_default is not None:
        return instance_default
    err_msg = "resolve_aspects called with both arguments None."
    raise ConfigurationError(err_msg)
```

Every consumer in layers 3–6 is expected to call `resolve_aspects` at the
top of every public method that takes an `aspects` parameter.

## Data Models

The aspects classes are value objects.  No persistence, no serialization
beyond `__repr__`.  Storage layout is ordinary instance attributes — `slots`
is *not* used because `Freezable.__setattr__` needs `__dict__` to live with
the runtime-mutable `__frozen__` flag.  This is a deliberate trade: one
extra `__dict__` per instance in exchange for cleaner Freezable ergonomics.

## Measured Interop Contract

This feature *defines* the measured-interop contract that the rest of the
library consumes.  It does not itself consume aspects.

## Error Handling

| Condition                                       | Exception            | Included in `details`           |
|-------------------------------------------------|----------------------|---------------------------------|
| Invalid `action_pattern` value                  | `ConfigurationError` | field name, offending value     |
| Non-positive numeric                            | `ConfigurationError` | field name, offending value     |
| Interval > timeout                              | `ConfigurationError` | both field names and values     |
| Invalid `logging_pattern`                       | `ConfigurationError` | offending value                 |
| `allowed_error_codes` contains non-int          | `ConfigurationError` | element index and value         |
| `allowed_connection_exceptions` has non-type    | `ConfigurationError` | element index                   |
| `logger` lacks required methods                 | `ConfigurationError` | list of missing method names    |
| Assignment to frozen aspects                    | `AttributeError`     | attribute name                  |
| `resolve_aspects(None, None)`                   | `ConfigurationError` | caller frame info if available  |

`SemanticError` is never raised from this feature because misuse is
configuration-time, not API-abuse-time.

## Logging

The aspects classes themselves do not emit log output.  They carry a
`logger` reference for *consumers* to use.

## Security Considerations

None.  Aspects hold no credentials, no secrets, no network handles.

## Testing Strategy

### Unit tests (`test_aspects.py`)

* Default construction determinism — two `UefiAspects()` instances compare
  equal.
* Freeze semantics — assignment after construction raises `AttributeError`.
* Each validation rule raises `ConfigurationError` with a message that
  identifies the offending field.
* `merge` semantics — overlay wins where it differs from default, base wins
  elsewhere.
* `merge(None)` returns `self`.
* `merge` across types returns the more specific type.
* `__eq__` / `__hash__` consistency.
* `__repr__` renders all fields.

### Unit tests (`test_cmdaspects.py`)

* All `UefiAspects` tests inherited/parametrized for `UefiCmdAspects`.
* `expected_status` int vs. tuple-of-int coercion.
* `prompt_pattern` accepts bytes only.
* `response_encoding` validated via `codecs.lookup`.
* `transport_kind` validated against known literals.

### Unit tests (`test_patterns.py`)

* `ActionPattern` exposes exactly five members.
* `LoggingPattern` exposes exactly three members.
* Membership is stable under pickle.

### Unit tests (`test_defaults.py`)

* `DEFAULT_ASPECTS is DEFAULT_ASPECTS` (identity on re-import).
* Re-importing the module returns the same instances.

### Unit tests (`test_resolver.py`)

* `resolve_aspects(x, y)` with `x is not None` returns `x`.
* `resolve_aspects(None, y)` returns `y`.
* `resolve_aspects(None, None)` raises `ConfigurationError`.

### Coverage target

100% line coverage for this feature — it is small, foundational, and any
uncovered branch is a bug.

## Differences from SshAspects

* **Additional `UefiCmdAspects` fields** — `prompt_pattern`,
  `response_encoding`, `transport_kind`.
* **No `pty_params`** — UEFI Shell does not have PTY semantics in the same
  way; the serial transport handles line-mode framing instead.
* **`merge()` helper** — SSH does not provide this; callers rebuild from
  scratch.  We add it because the UEFI library has substantially more
  configuration surface.
* **Split into multiple files** — SSH keeps everything in one file; we
  split for testability and to avoid circular imports when the defaults
  module is extended.
* **Stricter validation** — every field is validated; the SSH version is
  more permissive.

## Dependencies on Other Features

None.  This is a layer 1 foundation feature.

Downstream features that consume aspects include every layer 3, 4, 5, and 6
feature, but those features are not *imported* from here.
