# Design — Interfaces

## Overview

The Interfaces feature delivers a single module, `uefiinterfaces.py`, that
defines the structural contracts used across layers 2–6 of the library.
Each contract is a `typing.Protocol` decorated with `@runtime_checkable`,
so concrete classes satisfy the contract structurally — without inheritance
— and tests can assert compliance via `isinstance`.  Every method and
property carries a full type-hint signature and a Sphinx-style docstring.

This feature is a *shape definition* only.  It contains no runtime logic,
no state, no I/O.  It imports nothing from layers 2+ of the library; all
non-stdlib type names that appear in the protocols are string-quoted
forward references to break import cycles.

## Architecture

```
automojo.protocols.uefi.interfaces/
    __init__.py           # re-exports the five protocols
    uefiinterfaces.py     # Protocol definitions
```

Dependency direction:

```
uefiinterfaces.py  ──▶  typing (stdlib)
                   ──▶  uuid (stdlib)
                   ──▶  automojo.protocols.uefi.ueficonst.uefiguids   (type-only)
                   ──▶  automojo.interop.isystemcontext               (type-only; base protocol)
```

No imports from `1-aspects`, `1-credentials`, `1-exceptions`, `1-helpers`,
or `1-logging`.  Forward references (`"UefiCmdAspects"`, `"UefiShellResult"`,
`"UefiVariable"`, `"UefiVariableName"`, `"SecureBootState"`) resolve at
type-check time, not at import time.

## Components and Interfaces

### `IUefiTransport`

```python
from typing import Protocol, runtime_checkable


@runtime_checkable
class IUefiTransport(Protocol):
    """
        Lifecycle and byte-level I/O contract for a UEFI transport.

        Implementations MUST treat ``recv(timeout=0)`` as a non-blocking
        poll that returns buffered bytes when available and raises
        ``TimeoutError`` otherwise.
    """

    @property
    def connected(self) -> bool:
        """
            :returns: ``True`` when the transport has an open, usable
                      session to the target.
        """
        ...

    def connect(self) -> None:
        """
            Establish the underlying connection.

            :raises: UefiTransportError
        """
        ...

    def disconnect(self) -> None:
        """
            Tear down the connection.  Shall not raise on a
            double-disconnect.
        """
        ...

    def send(self, data: bytes) -> int:
        """
            Send ``data`` toward the target.

            :param data: The bytes to send.

            :returns: The number of bytes actually sent.
            :raises: UefiTransportError
        """
        ...

    def recv(self, max_bytes: int, timeout: float) -> bytes:
        """
            Receive up to ``max_bytes`` from the target.

            :param max_bytes: Upper bound on returned length.
            :param timeout: Seconds to wait; ``0`` means non-blocking poll.

            :returns: The bytes read; may be shorter than ``max_bytes``.
            :raises: TimeoutError, UefiTransportError
        """
        ...
```

### `IUefiShellRunner`

```python
from typing import Optional


@runtime_checkable
class IUefiShellRunner(Protocol):
    """
        Protocol for objects that execute UEFI Shell commands.
    """

    def run_cmd(self, command: str, aspects: Optional["UefiCmdAspects"] = None) -> "UefiShellResult":
        """
            Execute ``command`` and return a typed result.

            :param command: The shell command string.
            :param aspects: Optional aspect override; ``None`` means use
                            the instance default.

            :returns: A ``UefiShellResult`` with status, stdout, stderr.
            :raises: UefiShellError, UefiTransportError
        """
        ...
```

### `IUefiVariableProvider`

```python
import uuid
from typing import Iterator


@runtime_checkable
class IUefiVariableProvider(Protocol):
    """
        Protocol for objects that read, write, and enumerate UEFI
        variables.
    """

    def get_variable(self, name: str, vendor_guid: uuid.UUID,
                     aspects: Optional["UefiCmdAspects"] = None) -> "UefiVariable":
        """
            Read a variable.

            :param name: UEFI variable name.
            :param vendor_guid: Vendor GUID namespace.
            :param aspects: Optional aspects override.

            :returns: A ``UefiVariable`` with data and attribute flags.
            :raises: UefiVariableError
        """
        ...

    def set_variable(self, variable: "UefiVariable",
                     aspects: Optional["UefiCmdAspects"] = None) -> None:
        """
            Write a variable.

            :param variable: The variable to write.
            :param aspects: Optional aspects override.

            :raises: UefiVariableError
        """
        ...

    def enumerate_variables(self,
                            aspects: Optional["UefiCmdAspects"] = None) -> Iterator["UefiVariableName"]:
        """
            Stream the list of variable ``(name, vendor_guid)`` pairs.

            :param aspects: Optional aspects override.

            :returns: A lazy iterator of variable names.
            :raises: UefiVariableError
        """
        ...
```

### `IUefiFileProvider`

```python
@runtime_checkable
class IUefiFileProvider(Protocol):
    """
        Protocol for ESP (EFI System Partition) file operations.
        Paths follow the UEFI convention (``FS0:\\\\...``) in Shell
        transports and POSIX conventions in SSH-to-host transports.
    """

    def file_push(self, local: str, remote: str,
                  aspects: Optional["UefiCmdAspects"] = None) -> None:
        """
            Upload ``local`` to ``remote`` on the ESP.

            :param local: Path on the test host.
            :param remote: Path on the ESP.
            :param aspects: Optional aspects override.

            :raises: UefiShellError, UefiTransportError
        """
        ...

    def file_pull(self, remote: str, local: str,
                  aspects: Optional["UefiCmdAspects"] = None) -> None:
        """
            Download ``remote`` on the ESP to ``local``.

            :param remote: Path on the ESP.
            :param local: Path on the test host.
            :param aspects: Optional aspects override.

            :raises: UefiShellError, UefiTransportError
        """
        ...

    def file_exists(self, remote: str,
                    aspects: Optional["UefiCmdAspects"] = None) -> bool:
        """
            :returns: ``True`` when ``remote`` exists and is a file.
        """
        ...

    def directory_exists(self, remote: str,
                         aspects: Optional["UefiCmdAspects"] = None) -> bool:
        """
            :returns: ``True`` when ``remote`` exists and is a directory.
        """
        ...

    def directory(self, remote: str,
                  aspects: Optional["UefiCmdAspects"] = None) -> list[str]:
        """
            :returns: One-level listing of ``remote``.
        """
        ...

    def directory_tree(self, remote: str,
                       aspects: Optional["UefiCmdAspects"] = None) -> list[str]:
        """
            :returns: Recursive listing rooted at ``remote``.
        """
        ...
```

### `IUefiSystemContext`

```python
from automojo.interop.isystemcontext import ISystemContext


@runtime_checkable
class IUefiSystemContext(ISystemContext, Protocol):
    """
        Extension of the shared ``ISystemContext`` with UEFI-specific
        accessors.
    """

    @property
    def firmware_vendor(self) -> str:
        """
            :returns: The firmware vendor string reported by the target.
        """
        ...

    @property
    def firmware_revision(self) -> int:
        """
            :returns: The firmware revision integer (UEFI spec encoding).
        """
        ...

    @property
    def secure_boot_state(self) -> "SecureBootState":
        """
            :returns: A ``SecureBootState`` dataclass describing
                      ``SetupMode``, ``AuditMode``, ``DeployedMode``,
                      ``SecureBoot``.
        """
        ...
```

### `IMPLEMENTOR_COMPLIANCE_REGISTRY`

A small module-level registry used by tests to enforce compliance:

```python
IMPLEMENTOR_COMPLIANCE_REGISTRY: list[tuple[str, str]] = [
    # (concrete_dotted_path, protocol_name)
    # Populated as concrete classes are authored in layers 2-4.
]
```

The registry is imported by a cross-cutting test (see Testing Strategy
below) that iterates each pair and asserts `isinstance(instance, protocol)`
holds.  Concrete classes are added to this registry by their authoring
features as they land.

## Data Models

The feature declares no value types.  Every name it exports is either a
`typing.Protocol` subclass or the compliance registry.

## Measured Interop Contract

This feature does not consume aspects.  It defines the method signatures by
which higher layers accept `aspects` as a keyword argument — specifically,
`IUefiShellRunner.run_cmd`, `IUefiVariableProvider.get_variable` /
`.set_variable` / `.enumerate_variables`, and every method on
`IUefiFileProvider`.  Each such method types `aspects` as
`Optional["UefiCmdAspects"]` with the `None` default carrying the
well-known "use the instance default" semantic.

## Error Handling

| Condition                                            | Exception              | Notes                                          |
|------------------------------------------------------|------------------------|------------------------------------------------|
| Concrete class misses a required method              | Protocol does not raise | `isinstance(...)` returns `False` silently.   |
| `IUefiTransport.recv` timeout elapses                 | `TimeoutError`         | Documented contract; not enforced by Protocol. |
| `IUefiShellRunner.run_cmd` command fails              | `UefiShellError`       | Documented contract.                           |
| `IUefiVariableProvider.*` failure                     | `UefiVariableError`    | Documented contract.                           |
| `IUefiFileProvider.*` failure                         | `UefiShellError` or `UefiTransportError` | Documented contract. |

Protocol classes themselves raise nothing — they are shape definitions.

## Logging

This feature emits no log output.

## Security Considerations

* Protocols are pure interface declarations; there is no attack surface.
* Forward references to `UefiCmdAspects` and related classes prevent
  import cycles that might otherwise pull in credential-carrying modules
  from this layer.

## Testing Strategy

### Unit tests (`test_uefiinterfaces.py`)

* Each protocol is importable and is a `typing.Protocol` subclass.
* Each protocol is decorated with `@runtime_checkable` (assert via
  `hasattr(proto, "_is_runtime_protocol")` or equivalent).
* Constructing a stand-in class that implements every method satisfies
  `isinstance(stand_in, Protocol)`.
* Constructing a stand-in class that *misses* a method does *not* satisfy
  `isinstance(stand_in, Protocol)`.

### Compliance tests (`test_interface_compliance.py`)

* Iterate `IMPLEMENTOR_COMPLIANCE_REGISTRY`.
* For each `(dotted_path, protocol_name)` pair, import the class, pick
  the declared protocol, and either instantiate with a minimal-fixture
  credential or fall back to a `hasattr`-only class-level check.
* Assert compliance.  Any failure prints the dotted path and the set of
  missing methods.

### No-side-effects test (`test_interfaces_no_io.py`)

* Parse `uefiinterfaces.py` with `ast` and assert zero call expressions
  at module-top level other than decorator applications.
* Snapshot `sys.modules` before/after and assert no extraneous modules
  were loaded.

### Coverage target

Protocol definitions have no executable branches; 100% "line coverage" is
trivially achievable but not especially meaningful.  The compliance
registry is the test that adds real value.

## Dependencies on Other Features

* Depends on the shared `automojo.interop.isystemcontext.ISystemContext`
  protocol class.
* References (via forward-quoted strings) types from `1-aspects`
  (`UefiCmdAspects`), from layer-5 features (`UefiShellResult`,
  `UefiVariable`, `UefiVariableName`, `SecureBootState`), and from
  `1-exceptions` in docstrings only.
* No hard import dependency on `1-aspects`, `1-constants-and-types`,
  `1-credentials`, `1-exceptions`, `1-helpers`, or `1-logging`.
