# Design — Exceptions

## Overview

The Exceptions feature delivers a single module, `uefiexceptions.py`, that
defines the UEFI-specific exception hierarchy layered atop the shared
`ccai.errors.exceptions` module.  The design goals are:

1. Every library error is catchable by `except UefiError`.
2. Domain-specific context (command / status / stdout / stderr for shell
   errors; name / vendor-GUID / attributes / EFI status for variable errors)
   lives on typed fields, not in freeform strings.
3. Every exception class renders its multi-line message with `os.linesep`
   per the repository coding standard, and offers an `as_dict()` method so
   structured-logging consumers can forward the error without string
   scraping.
4. No subclass of `SemanticError` is defined here — the library never
   catches `SemanticError` and therefore never needs to refine it.

The module reuses `ConfigurationError` and `SemanticError` directly from
the shared module rather than redeclaring them.

## Architecture

```
ccai.protocols.uefi.errors/
    __init__.py           # re-exports public surface
    uefiexceptions.py     # UefiError + subclasses
```

Dependency direction:

```
uefiexceptions.py  ──▶  ccai.errors.exceptions   (shared root + ConfigurationError)
                    ──▶  ccai.protocols.uefi.ueficonst.uefiguids  (typing only)
```

This module is imported by nearly every other feature in the library but
imports almost nothing itself.

## Components and Interfaces

### `UefiError` base class

```python
import os
from typing import Any, Optional

from ccai.errors.exceptions import CodeCraftAIError


class UefiError(CodeCraftAIError):
    """
        Root exception for every UEFI-specific error raised by this library.

        :param message: A short human-readable summary of the error.
        :param details: Optional list of context lines rendered after the
                        summary, joined with ``os.linesep``.
    """

    def __init__(self, message: str, details: Optional[list[str]] = None) -> None:
        super().__init__(message)
        if details is None:
            self.details = []
        elif isinstance(details, list):
            self.details = list(details)
        else:
            err_msg = f"UefiError.details must be list[str] or None, observed type={type(details).__name__}."
            raise TypeError(err_msg)
        return

    def __str__(self) -> str:
        message = self.args[0] if len(self.args) > 0 else ""
        if len(self.details) == 0:
            rtnval = message
        else:
            rtnval = message + os.linesep + os.linesep.join(self.details)
        return rtnval

    def as_dict(self) -> dict[str, Any]:
        """
            Serialize the exception to a JSON-compatible dictionary.

            :returns: A dict with ``type``, ``message``, ``details`` keys at
                      minimum; subclasses extend this.
        """
        rtnval: dict[str, Any] = {
            "type": type(self).__name__,
            "message": self.args[0] if len(self.args) > 0 else "",
            "details": list(self.details),
        }
        return rtnval
```

### `UefiShellError`

```python
class UefiShellError(UefiError):
    """
        Raised when a UEFI Shell command fails or returns an unexpected
        status code.

        :param message: Short summary.
        :param details: Optional extra context lines.
        :param command: The command string that was issued.
        :param status: The integer exit status returned by the shell.
        :param stdout: The captured standard-output text.
        :param stderr: The captured standard-error text.
    """

    def __init__(self, message: str, details: Optional[list[str]] = None,
                 command: Optional[str] = None,
                 status: Optional[int] = None,
                 stdout: Optional[str] = None,
                 stderr: Optional[str] = None) -> None:
        super().__init__(message=message, details=details)
        if status is not None and not isinstance(status, int):
            err_msg = f"UefiShellError.status must be int or None, observed={type(status).__name__}."
            raise TypeError(err_msg)
        self.command = command
        self.status = status
        self.stdout = stdout
        self.stderr = stderr
        return

    def __str__(self) -> str:
        base = super().__str__()
        lines: list[str] = [base]
        if self.command is not None:
            lines.append(f"COMMAND: {self.command}")
        if self.status is not None:
            lines.append(f"STATUS: {self.status}")
        if self.stdout is not None:
            lines.append("STDOUT:")
            lines.append(_preview(self.stdout))
        if self.stderr is not None:
            lines.append("STDERR:")
            lines.append(_preview(self.stderr))
        rtnval = os.linesep.join(lines)
        return rtnval

    def as_dict(self) -> dict[str, Any]:
        rtnval = super().as_dict()
        rtnval["command"] = self.command
        rtnval["status"] = self.status
        rtnval["stdout"] = self.stdout
        rtnval["stderr"] = self.stderr
        return rtnval
```

### `UefiVariableError`

```python
class UefiVariableError(UefiError):
    """
        Raised when a UEFI variable-services operation fails.

        :param message: Short summary.
        :param details: Optional extra context lines.
        :param name: The UEFI variable name.
        :param vendor_guid: The vendor GUID namespace of the variable.
        :param attributes: The attribute flags for the variable.
        :param efi_status: The raw EFI_STATUS numeric value, if available.
    """

    def __init__(self, message: str, details: Optional[list[str]] = None,
                 name: Optional[str] = None,
                 vendor_guid: Optional[uuid.UUID] = None,
                 attributes: Optional[int] = None,
                 efi_status: Optional[int] = None) -> None:
        super().__init__(message=message, details=details)
        if vendor_guid is not None and not isinstance(vendor_guid, uuid.UUID):
            err_msg = f"UefiVariableError.vendor_guid must be uuid.UUID or None."
            raise TypeError(err_msg)
        if efi_status is not None and not isinstance(efi_status, int):
            err_msg = f"UefiVariableError.efi_status must be int or None."
            raise TypeError(err_msg)
        self.name = name
        self.vendor_guid = vendor_guid
        self.attributes = attributes
        self.efi_status = efi_status
        return

    def as_dict(self) -> dict[str, Any]:
        rtnval = super().as_dict()
        rtnval["name"] = self.name
        rtnval["vendor_guid"] = str(self.vendor_guid) if self.vendor_guid is not None else None
        rtnval["attributes"] = self.attributes
        rtnval["efi_status"] = self.efi_status
        return rtnval
```

### Other typed subclasses

```python
class UefiCapsuleError(UefiError):
    """Raised when a UEFI capsule build, delivery, or report-read fails."""


class UefiSecureBootError(UefiError):
    """Raised when a Secure Boot state read or key-enrollment write fails."""


class UefiTransportError(UefiError):
    """Raised when a transport-level fault is observed (connect, read, write, framing)."""


class UefiProtocolError(UefiError):
    """Raised when the remote side violates the expected protocol framing."""
```

Each of the four above inherits `__init__`, `__str__`, and `as_dict` from
`UefiError` unchanged.  They exist as distinct classes solely to support
tight `except` clauses at call sites.

### `_preview` helper

```python
SHELL_ERROR_OUTPUT_PREVIEW_LINES = 40


def _preview(text: str) -> str:
    """
        Trim ``text`` to the first ``SHELL_ERROR_OUTPUT_PREVIEW_LINES`` lines
        and append an ellipsis when truncated.

        :param text: The full captured stream contents.

        :returns: A trimmed string suitable for inclusion in an exception's
                  ``__str__`` rendering.
    """
    if text is None:
        rtnval = ""
    else:
        lines = text.splitlines()
        if len(lines) <= SHELL_ERROR_OUTPUT_PREVIEW_LINES:
            rtnval = os.linesep.join(lines)
        else:
            kept = lines[:SHELL_ERROR_OUTPUT_PREVIEW_LINES]
            rtnval = os.linesep.join(kept) + os.linesep + f"... ({len(lines) - SHELL_ERROR_OUTPUT_PREVIEW_LINES} more lines)"
    return rtnval
```

## Data Models

No persistent data model.  Each exception is a Python object with:

* `args` — tuple of the summary message (from `Exception`).
* `details` — `list[str]`.
* Typed context fields on `UefiShellError` and `UefiVariableError`.
* No state on `UefiCapsuleError`, `UefiSecureBootError`,
  `UefiTransportError`, `UefiProtocolError` beyond what `UefiError` defines.

## Measured Interop Contract

This feature does not consume aspects.  It *supports* the measured-interop
contract in two ways:

* `UefiTransportError` is the canonical type a layer-3 client raises when a
  transport-level fault occurs, and the measured-interop engine in layer 4
  consults `aspects.allowed_connection_exceptions` to decide whether to
  swallow-and-retry or bubble.
* `UefiShellError.status` is the typed field the measured-interop engine
  consults against `aspects.allowed_error_codes` to decide retry vs. bubble.

## Error Handling

| Condition                                                 | Exception              | Notes                               |
|-----------------------------------------------------------|------------------------|-------------------------------------|
| `details` constructed with non-list                       | `TypeError`            | Raised from `UefiError.__init__`.   |
| `status` on `UefiShellError` not int and not None         | `TypeError`            | Raised from `__init__`.             |
| `vendor_guid` on `UefiVariableError` not UUID and not None| `TypeError`            | Raised from `__init__`.             |
| `efi_status` on `UefiVariableError` not int and not None  | `TypeError`            | Raised from `__init__`.             |

`SemanticError` is raised from higher-layer call sites directly using the
shared `ccai.errors.exceptions.SemanticError` class — never caught by
this library, never subclassed here.

## Logging

Exception classes emit no log output themselves.  The `as_dict()` method is
the structured-logging affordance.  Consumers typically log on channel
`uefi.<subsystem>` and include `exc.as_dict()` as a payload field.

## Security Considerations

* `UefiShellError` may carry command-line arguments and output that contain
  secrets (for example, a `setvar` command that writes a KEK blob).  Callers
  are responsible for sanitizing inputs before raising.  This module does
  not attempt to auto-redact because there is no reliable way to identify
  secrets at exception-construction time.
* `as_dict()` returns everything the instance holds; consumers that forward
  to a remote log aggregator are responsible for transport-level encryption.

## Testing Strategy

### Unit tests (`test_uefiexceptions.py`)

* Each concrete class is a subclass of `UefiError`.
* `UefiError(message)` stores message on `args[0]` and sets empty
  `details`.
* `UefiError(message, details=[...])` stores a *copy* of the list, provable
  by mutating the caller's list and re-reading `exc.details`.
* `UefiError(message, details=<non-list>)` raises `TypeError`.
* `__str__` returns `message` alone when `details` is empty and joins with
  `os.linesep` otherwise.
* `UefiShellError`: every field defaults to `None`, non-int `status` raises
  `TypeError`, `__str__` contains the command and status when present,
  `as_dict()` contains all four context keys.
* `UefiVariableError`: non-UUID `vendor_guid` raises `TypeError`, non-int
  `efi_status` raises `TypeError`, `as_dict()` renders `vendor_guid` as a
  canonical string.
* Each of `UefiCapsuleError`, `UefiSecureBootError`, `UefiTransportError`,
  `UefiProtocolError` is constructible with only a message and inherits
  `as_dict()` behavior.

### Pickle round-trip (`test_uefiexceptions_pickle.py`)

* Pickle and unpickle each class, compare `__str__` and `as_dict()` before
  and after.

### No-SemanticError-subclass rule (`test_semantic_error_rule.py`)

* Import the `uefiexceptions` module, iterate `__all__`, assert none is a
  subclass of `SemanticError`.

### Coverage target

100% line coverage.  The module is small and any uncovered branch is a
typed-field omission bug.

## Dependencies on Other Features

* Depends on the shared `ccai.errors.exceptions` module for the root
  class and for `SemanticError` / `ConfigurationError`.
* Depends on `uuid` from the standard library for the `UefiVariableError`
  `vendor_guid` type check.
* No dependency on `1-aspects`, `1-constants-and-types`, `1-credentials`,
  `1-helpers`, `1-logging`, or `1-interfaces`.
