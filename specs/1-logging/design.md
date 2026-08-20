# Design — Logging

## Overview

The Logging feature delivers a single small module, `uefilogging.py`, that
provides a `get_uefi_logger(channel, strict=True)` factory, re-exports
`LoggingPattern` from `1-aspects`, and exposes a `KNOWN_UEFI_CHANNELS`
frozenset plus a `CHANNEL_*` constant for each documented channel.  The
factory routes through `ccai.xmodules.xlogging.enhancedloggers.getEnhancedLogger`
so that every logger is the same enhanced kind consumed throughout the
`ccai` ecosystem.

The design goal is a tiny, dependency-light surface with zero side effects
on import.  Every log line emitted from any layer of `codecraftai-protocol-uefi` must
be attributable to a channel under `uefi.*`; this module is the single
place where that namespace is enforced and enumerated.

## Architecture

```
ccai.protocols.uefi.logging/
    __init__.py        # re-exports public surface
    uefilogging.py     # get_uefi_logger, KNOWN_UEFI_CHANNELS, CHANNEL_* constants
```

Dependency direction:

```
uefilogging.py  ──▶  ccai.xmodules.xlogging.enhancedloggers.getEnhancedLogger
                ──▶  ccai.protocols.uefi.aspects.LoggingPattern      (re-export)
                ──▶  ccai.errors.exceptions.ConfigurationError
```

## Components and Interfaces

### Channel constants and registry

```python
from typing import Final

CHANNEL_SHELL: Final[str] = "uefi.shell"
CHANNEL_VARIABLE: Final[str] = "uefi.variable"
CHANNEL_CAPSULE: Final[str] = "uefi.capsule"
CHANNEL_TRANSPORT: Final[str] = "uefi.transport"
CHANNEL_BOOTMGR: Final[str] = "uefi.bootmgr"
CHANNEL_SECUREBOOT: Final[str] = "uefi.secureboot"
CHANNEL_DEVICEPATH: Final[str] = "uefi.devicepath"
CHANNEL_GPT: Final[str] = "uefi.gpt"
CHANNEL_ESP: Final[str] = "uefi.esp"
CHANNEL_RESET: Final[str] = "uefi.reset"
CHANNEL_HII: Final[str] = "uefi.hii"
CHANNEL_REDFISH: Final[str] = "uefi.redfish"
CHANNEL_TPM: Final[str] = "uefi.tpm"

KNOWN_UEFI_CHANNELS: Final[frozenset[str]] = frozenset({
    CHANNEL_SHELL,
    CHANNEL_VARIABLE,
    CHANNEL_CAPSULE,
    CHANNEL_TRANSPORT,
    CHANNEL_BOOTMGR,
    CHANNEL_SECUREBOOT,
    CHANNEL_DEVICEPATH,
    CHANNEL_GPT,
    CHANNEL_ESP,
    CHANNEL_RESET,
    CHANNEL_HII,
    CHANNEL_REDFISH,
    CHANNEL_TPM,
})
```

### `get_uefi_logger`

```python
import logging
from typing import Optional

from ccai.errors.exceptions import ConfigurationError
from ccai.xmodules.xlogging.enhancedloggers import getEnhancedLogger


_REQUIRED_LOGGER_METHODS = ("debug", "info", "warning", "error", "critical")


def get_uefi_logger(channel: str, strict: bool = True) -> logging.Logger:
    """
        Return a logger bound to the given UEFI channel.

        :param channel: The channel name.  The ``uefi.`` prefix is added if
                        missing.
        :param strict: When ``True`` (default), raise
                       ``ConfigurationError`` if the resolved channel name
                       is not a member of ``KNOWN_UEFI_CHANNELS``.

        :returns: A logger with the standard method set.
        :raises: TypeError, ConfigurationError
    """
    if not isinstance(channel, str):
        err_msg = f"get_uefi_logger: channel must be str, observed type={type(channel).__name__}."
        raise TypeError(err_msg)

    if channel.startswith("uefi."):
        resolved = channel
    else:
        resolved = f"uefi.{channel}"

    if resolved not in KNOWN_UEFI_CHANNELS:
        if strict:
            err_msg_lines = [
                f"get_uefi_logger: channel {resolved!r} is not a known UEFI channel.",
                f"KNOWN_UEFI_CHANNELS:",
            ]
            for name in sorted(KNOWN_UEFI_CHANNELS):
                err_msg_lines.append(f"    {name}")
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg)
        else:
            bootstrap = getEnhancedLogger("uefi.logging")
            bootstrap.warning("get_uefi_logger called with untracked channel %r (strict=False).", resolved)

    logger = getEnhancedLogger(resolved)

    missing: list[str] = []
    for name in _REQUIRED_LOGGER_METHODS:
        if not callable(getattr(logger, name, None)):
            missing.append(name)
    if len(missing) > 0:
        err_msg_lines = [
            f"get_uefi_logger: logger for channel {resolved!r} lacks required methods.",
            f"MISSING: {missing}",
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise ConfigurationError(err_msg)

    rtnval = logger
    return rtnval
```

### `LoggingPattern` re-export

```python
from ccai.protocols.uefi.aspects import LoggingPattern  # noqa: F401
```

Re-exported so consumers can `from ccai.protocols.uefi.logging import
LoggingPattern, get_uefi_logger` in one line.

## Data Models

No persistent state.  The module-level bindings are:

* Thirteen `Final[str]` channel constants.
* One `Final[frozenset[str]]` registry (`KNOWN_UEFI_CHANNELS`).
* One function (`get_uefi_logger`).
* One re-exported class (`LoggingPattern`).
* One tuple of required method names (`_REQUIRED_LOGGER_METHODS`).

## Measured Interop Contract

This feature does not consume aspects.  It supports the measured-interop
contract by re-exporting `LoggingPattern` from `1-aspects` so higher-layer
log-call sites can decide — in `LoggingPattern.ALL_RESULTS`,
`FAILURE_ONLY`, or `SUCCESS_ONLY` mode — what to emit based on an
aspect-driven result.

## Error Handling

| Condition                                            | Exception              | Details content                        |
|------------------------------------------------------|------------------------|----------------------------------------|
| `channel` argument not a string                      | `TypeError`            | observed type name                     |
| Unknown channel with `strict=True`                   | `ConfigurationError`   | resolved channel + full known set      |
| Unknown channel with `strict=False`                  | —                      | logs a warning on `uefi.logging`       |
| Returned logger missing required methods             | `ConfigurationError`   | list of missing method names           |
| Mutation attempt on `KNOWN_UEFI_CHANNELS`            | `AttributeError`       | raised by `frozenset`                  |

`SemanticError` is not raised from this feature.

## Logging

This feature emits exactly one log call on its own: the `WARNING` on
`uefi.logging` when `get_uefi_logger` is invoked in lax mode with an
untracked channel.  All other log output from the library originates at
higher-layer call sites that *use* the loggers produced here.

## Security Considerations

* Log records may contain sensitive data supplied by callers.  This feature
  does not attempt redaction — that is the caller's responsibility (for
  example, via the redaction discipline built into `1-credentials`).
* The `uefi.logging` channel used for lax-mode warnings is intentionally
  distinct from `uefi.*` capability channels, so filters can mute it
  without muting useful output.

## Testing Strategy

### Unit tests (`test_get_uefi_logger.py`)

* `get_uefi_logger("shell")` returns a logger whose `name == "uefi.shell"`.
* `get_uefi_logger("uefi.shell")` returns the same logger.
* Non-string channel raises `TypeError`.
* Unknown channel with default strict raises `ConfigurationError` whose
  message lists the known channels.
* Unknown channel with `strict=False` returns a logger and emits a
  `WARNING` on `uefi.logging` (captured via `caplog`).
* Returned logger has callable attributes `debug`, `info`, `warning`,
  `error`, `critical`.

### Unit tests (`test_channels.py`)

* `KNOWN_UEFI_CHANNELS` is a frozen set of exactly 13 members.
* Every `CHANNEL_*` constant is in `KNOWN_UEFI_CHANNELS`.
* The module exposes exactly 13 `CHANNEL_*` constants (count derived by
  iterating the module's `dir()`).

### Unit tests (`test_logging_pattern_reexport.py`)

* `LoggingPattern` imported from this module is identical (by `is`) to
  `LoggingPattern` imported from `ccai.protocols.uefi.aspects`.
* `LoggingPattern` has the three expected members.

### Smoke test (`test_logging_smoke.py`)

* For every member of `KNOWN_UEFI_CHANNELS`, invoke `get_uefi_logger` and
  call `.info("smoke")`.
* Use `caplog` to assert one record per channel with the correct name.

### Coverage target

100% line coverage.  This module is tiny; anything less indicates a dead
branch.

## Dependencies on Other Features

* Depends on `1-aspects` for the `LoggingPattern` enum (re-export only).
* Depends on `ccai.xmodules.xlogging.enhancedloggers.getEnhancedLogger`.
* Depends on `ccai.errors.exceptions.ConfigurationError`.
* No dependency on `1-constants-and-types`, `1-exceptions`,
  `1-credentials`, `1-helpers`, or `1-interfaces`.
