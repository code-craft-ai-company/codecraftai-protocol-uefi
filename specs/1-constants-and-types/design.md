# Design — Constants and Types

## Overview

The Constants and Types feature delivers two importable modules —
`ueficonst.py` and the subsidiary `uefiguids.py` — plus a small set of
frozen dataclasses (`UefiJumpParams`, and thin wrappers over GUID groupings
that the higher layers reuse).  The design goal is to provide a stable,
zero-cost-to-import vocabulary for every layer above.  No I/O, no third-party
dependency, and no dynamic configuration — everything here is an immutable
Python value decided at module load time.

The design treats UEFI GUIDs as first-class `uuid.UUID` values (never as
bare strings or bytes) and exposes two pure helpers (`guid_to_bytes`,
`guid_from_bytes`) that encapsulate the UEFI mixed-endian layout so callers
never re-derive it.  Templates for rendering command outcomes embed
`os.linesep` per the repository's multi-line error-message convention.

## Architecture

```
automojo.protocols.uefi.ueficonst/
    __init__.py          # re-exports public surface from ueficonst.py + uefiguids.py
    ueficonst.py         # numeric defaults, templates, prompt sentinels, transport kinds
    uefiguids.py         # well-known GUID registry + guid_to_bytes / guid_from_bytes
    uefijumpparams.py    # UefiJumpParams frozen dataclass
```

Dependency direction:

```
__init__.py  ──▶  ueficonst.py
              ──▶  uefiguids.py
              ──▶  uefijumpparams.py  ──▶  automojo.credentials  (for credential typing)
```

`ueficonst.py` does not import `uefiguids.py`; the reverse also holds.  This
allows either module to be imported alone.

## Components and Interfaces

### `uefiguids.py`

```python
import uuid
from types import MappingProxyType
from typing import Final


EFI_GLOBAL_VARIABLE_GUID: Final[uuid.UUID] = uuid.UUID("8be4df61-93ca-11d2-aa0d-00e098032b8c")
EFI_IMAGE_SECURITY_DATABASE_GUID: Final[uuid.UUID] = uuid.UUID("d719b2cb-3d3a-4596-a3bc-dad00e67656f")
EFI_SYSTEM_RESOURCE_TABLE_GUID: Final[uuid.UUID] = uuid.UUID("b122a263-3661-4f68-9929-78f8b0d62180")
EFI_CAPSULE_REPORT_GUID: Final[uuid.UUID] = uuid.UUID("39b68c46-f7fb-441b-b6ec-16b0f69821f3")
EFI_DEVICE_PATH_PROTOCOL_GUID: Final[uuid.UUID] = uuid.UUID("09576e91-6d3f-11d2-8e39-00a0c969723b")
EFI_LOADED_IMAGE_PROTOCOL_GUID: Final[uuid.UUID] = uuid.UUID("5b1b31a1-9562-11d2-8e3f-00a0c969723b")
EFI_FIRMWARE_MANAGEMENT_PROTOCOL_GUID: Final[uuid.UUID] = uuid.UUID("86c77a67-0b97-4633-a187-49104d0685c7")


WELL_KNOWN_GUIDS: Final[MappingProxyType[str, uuid.UUID]] = MappingProxyType({
    "EFI_GLOBAL_VARIABLE_GUID": EFI_GLOBAL_VARIABLE_GUID,
    "EFI_IMAGE_SECURITY_DATABASE_GUID": EFI_IMAGE_SECURITY_DATABASE_GUID,
    "EFI_SYSTEM_RESOURCE_TABLE_GUID": EFI_SYSTEM_RESOURCE_TABLE_GUID,
    "EFI_CAPSULE_REPORT_GUID": EFI_CAPSULE_REPORT_GUID,
    "EFI_DEVICE_PATH_PROTOCOL_GUID": EFI_DEVICE_PATH_PROTOCOL_GUID,
    "EFI_LOADED_IMAGE_PROTOCOL_GUID": EFI_LOADED_IMAGE_PROTOCOL_GUID,
    "EFI_FIRMWARE_MANAGEMENT_PROTOCOL_GUID": EFI_FIRMWARE_MANAGEMENT_PROTOCOL_GUID,
})


def guid_to_bytes(guid: uuid.UUID) -> bytes:
    """
        Serialize a `uuid.UUID` into the UEFI 16-byte mixed-endian layout.

        :param guid: The UUID to serialize.

        :returns: A 16-byte `bytes` object.
        :raises: TypeError
    """
    if not isinstance(guid, uuid.UUID):
        err_msg = f"guid_to_bytes requires uuid.UUID, observed type={type(guid).__name__}."
        raise TypeError(err_msg)

    rtnval = guid.bytes_le
    return rtnval


def guid_from_bytes(buf: bytes) -> uuid.UUID:
    """
        Deserialize a 16-byte UEFI mixed-endian GUID into a `uuid.UUID`.

        :param buf: A 16-byte `bytes` object.

        :returns: The decoded `uuid.UUID`.
        :raises: ValueError
    """
    if len(buf) != 16:
        err_msg = f"guid_from_bytes requires a 16-byte buffer, observed length={len(buf)}."
        raise ValueError(err_msg)

    rtnval = uuid.UUID(bytes_le=bytes(buf))
    return rtnval
```

### `ueficonst.py`

```python
import os
import re
from typing import Final


DEFAULT_SHELL_TIMEOUT: Final[int] = 120
DEFAULT_REDFISH_TIMEOUT: Final[int] = 120
DEFAULT_RECONNECT_DELAY: Final[float] = 2.0
DEFAULT_CHUNK_SIZE: Final[int] = 65536

TRANSPORT_KIND_SERIAL: Final[str] = "serial"
TRANSPORT_KIND_REDFISH: Final[str] = "redfish"
TRANSPORT_KIND_SSH_HOST: Final[str] = "ssh-host"
TRANSPORT_KIND_IPMI_SOL: Final[str] = "ipmi-sol"

TRANSPORT_KINDS: Final[frozenset[str]] = frozenset({
    TRANSPORT_KIND_SERIAL,
    TRANSPORT_KIND_REDFISH,
    TRANSPORT_KIND_SSH_HOST,
    TRANSPORT_KIND_IPMI_SOL,
})

DEFAULT_SHELL_PROMPT_PATTERN: Final[bytes] = rb"(?:Shell|FS\d+:\\[^>]*)>\s*$"
DEFAULT_INTERACTIVE_SENTINEL: Final[bytes] = b"UEFI_AUTOMOJO_PROMPT%:%:%"
DEFAULT_INTERACTIVE_SENTINEL_STR: Final[str] = DEFAULT_INTERACTIVE_SENTINEL.decode("utf-8")

DEFAULT_SUCCESS_LABEL: Final[str] = "Success"
DEFAULT_FAILURE_LABEL: Final[str] = "Failure"

TEMPLATE_COMMAND_SUCCESS: Final[str] = (
    "UEFI%s: " + DEFAULT_SUCCESS_LABEL + " running CMD=%s" + os.linesep
    + "STDOUT:" + os.linesep + "%s" + os.linesep
    + "STDERR:" + os.linesep + "%s" + os.linesep
)

TEMPLATE_COMMAND_FAILURE: Final[str] = (
    "UEFI%s: " + DEFAULT_FAILURE_LABEL + " running CMD=%s STATUS=%s" + os.linesep
    + "STDOUT:" + os.linesep + "%s" + os.linesep
    + "STDERR:" + os.linesep + "%s" + os.linesep
)
```

### `uefijumpparams.py`

```python
import dataclasses
import os
from typing import Optional

from automojo.credentials.sshcredential import SshCredential
from automojo.errors.exceptions import ConfigurationError


@dataclasses.dataclass(frozen=True, slots=True)
class UefiJumpParams:
    """
        Parameters describing a jump host used to reach a UEFI target over
        Redfish or IPMI when the management network is not directly routable.

        :param host: Hostname or IP address of the jump server.
        :param credential: Credential used when connecting to the jump host.
        :param port: TCP port of the jump SSH service.  Defaults to 22.
    """
    host: str
    credential: SshCredential
    port: int = 22

    def __post_init__(self) -> None:
        err_msg_lines: list[str] = []

        if not isinstance(self.host, str) or len(self.host) == 0:
            err_msg_lines.append(f"UefiJumpParams.host must be a non-empty string, observed={self.host!r}.")

        if not isinstance(self.port, int) or self.port < 1 or self.port > 65535:
            err_msg_lines.append(f"UefiJumpParams.port must be an integer in [1, 65535], observed={self.port!r}.")

        if len(err_msg_lines) > 0:
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg)

        return

    def __repr__(self) -> str:
        rtnval = f"UefiJumpParams(host={self.host!r}, credential=<redacted>, port={self.port!r})"
        return rtnval
```

## Data Models

The feature contributes these value types to the wider library:

* `UefiJumpParams` — `frozen=True, slots=True` dataclass.
* `WELL_KNOWN_GUIDS` — `MappingProxyType[str, uuid.UUID]`.
* Module-level `Final` constants (numeric, `bytes`, `str`, `frozenset`).

No dynamic state.  No mutable containers are exposed to callers — a `list`
or `dict` leaking out of this module is a bug.

## Measured Interop Contract

This feature does not itself consume aspects; it only defines constants and
typed values that aspects-driven layers reference.  The feature's only
interaction with the measured-interop contract is to provide
`DEFAULT_SHELL_PROMPT_PATTERN` as a candidate default for
`UefiCmdAspects.prompt_pattern` and `TRANSPORT_KINDS` as the valid value set
for `UefiCmdAspects.transport_kind`.

## Error Handling

| Condition                                              | Exception              | Included in `details` or message  |
|--------------------------------------------------------|------------------------|-----------------------------------|
| `guid_to_bytes` called with non-`uuid.UUID`            | `TypeError`            | offending type name               |
| `guid_from_bytes` called with wrong-size buffer        | `ValueError`           | observed length                   |
| `UefiJumpParams` empty `host`                          | `ConfigurationError`   | field name, offending value       |
| `UefiJumpParams` out-of-range `port`                   | `ConfigurationError`   | field name, offending value       |
| Mutation of `WELL_KNOWN_GUIDS`                         | `TypeError`            | raised by `MappingProxyType`      |
| Mutation of `TRANSPORT_KINDS`                          | `AttributeError`       | raised by `frozenset`             |
| Assignment on a frozen dataclass                       | `FrozenInstanceError`  | raised by dataclasses machinery   |

Errors in this feature are always construction-time.  `SemanticError` is not
raised here because there is no runtime API surface to misuse.

## Logging

This feature emits no log output.  It supplies constants — `os.linesep`-aware
templates and prompt patterns — that higher layers embed into their own log
calls on channels under the `uefi.*` namespace.

## Security Considerations

* `UefiJumpParams.__repr__` redacts the embedded credential.  The same
  discipline is enforced by the 1-credentials feature at its layer; this
  module merely ensures that a `UefiJumpParams` rendered via `repr()` never
  leaks whatever secret material its embedded `SshCredential` carries.
* GUID constants are public; they are not secrets.

## Testing Strategy

### Unit tests (`test_uefiguids.py`)

* Each well-known GUID constant is a `uuid.UUID` with the canonical string
  matching the value asserted in the requirements.
* `WELL_KNOWN_GUIDS` is a `MappingProxyType`; mutation attempts raise
  `TypeError`.
* `guid_to_bytes` / `guid_from_bytes` round-trip for every value in
  `WELL_KNOWN_GUIDS`.
* `guid_to_bytes` on non-UUID raises `TypeError`.
* `guid_from_bytes` on wrong-length buffer raises `ValueError` with the
  observed length in the message.

### Unit tests (`test_ueficonst.py`)

* `TRANSPORT_KINDS` has exactly four members and matches the four
  `TRANSPORT_KIND_*` constants.
* Templates, when formatted with representative arguments, produce output
  whose inner line separators are `os.linesep` — asserted via a string
  search.
* `DEFAULT_SHELL_PROMPT_PATTERN` compiles as a `bytes` regex and matches
  representative prompt strings.

### Unit tests (`test_uefijumpparams.py`)

* Construction with valid inputs succeeds.
* Empty `host` raises `ConfigurationError`.
* Out-of-range `port` raises `ConfigurationError`.
* `__repr__` does not contain the credential's password field.
* Attribute assignment raises `FrozenInstanceError`.

### Snapshot / drift test (`test_guid_snapshot.py`)

* Compares `WELL_KNOWN_GUIDS` against
  `tests/fixtures/guid_registry.json`.  Any drift fails the test with a diff.

### Coverage target

100% line coverage.  This feature is small, foundational, and any uncovered
branch indicates dead constants or dead branches.

## Dependencies on Other Features

* None on other `automojo-uefi` features.
* Depends on `automojo.credentials.sshcredential.SshCredential` (for
  `UefiJumpParams.credential`) and on `automojo.errors.exceptions.ConfigurationError`.
