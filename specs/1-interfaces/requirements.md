# Requirements — Interfaces

## Introduction

The Interfaces feature defines the abstract `typing.Protocol`-based
interfaces that shape the rest of the `codecraftai-protocol-uefi` stack.  It provides
`IUefiTransport` for layer-2 transports (connect / disconnect / send / recv
primitives plus a `connected` property), `IUefiShellRunner` for the
run_cmd-style execution surface used by layer-3 shell clients,
`IUefiVariableProvider` for the variable-service CRUD surface used by
layer-5 variable services, `IUefiFileProvider` for the ESP file-operations
surface used by layer-5 ESP file ops, and `IUefiSystemContext` which
extends the shared `ISystemContext` with firmware vendor, firmware
revision, and Secure Boot state accessors.

Protocols are used rather than ABCs so concrete classes can satisfy them
structurally, without inheriting.  Where runtime dispatch needs
`isinstance` checks the protocol is decorated with `@runtime_checkable`.
Every method on every interface carries type hints and a Sphinx-style
docstring so that editor hover-documentation is sufficient for consumers.

The feature has no dependencies on other `codecraftai-protocol-uefi` features beyond
referencing classes from `1-exceptions` and `1-constants-and-types` only
in docstrings and type hints.

## Requirements

### Requirement 1 — `IUefiTransport` Protocol

**User Story:** As an author of a new UEFI transport, I want a minimal
contract that describes a transport's lifecycle and byte-level I/O, so
that my implementation is accepted by the layer-3 client without bespoke
glue.

#### Acceptance Criteria

1. WHEN the caller imports `IUefiTransport` THEN the system SHALL expose it
   as a `typing.Protocol` decorated with `@runtime_checkable`.
2. WHEN a concrete class implements the methods `connect(self)`,
   `disconnect(self)`, `send(self, data: bytes) -> int`,
   `recv(self, max_bytes: int, timeout: float) -> bytes`, and the property
   `connected(self) -> bool` THEN `isinstance(instance, IUefiTransport)`
   SHALL return `True`.
3. WHERE `send` returns a non-int value at runtime the system SHALL
   document this as a protocol violation and let it bubble up — the
   Protocol does not enforce return type at runtime.
4. WHERE `recv` is invoked with a `timeout` of zero the system SHALL
   document the semantic as "non-blocking poll; return whatever bytes are
   already buffered and raise `TimeoutError` if none are available".

### Requirement 2 — `IUefiShellRunner` Protocol

**User Story:** As a consumer of the UEFI Shell runner surface, I want a
single-method protocol that accepts a command string and returns a typed
result, so that mock runners and real runners are interchangeable.

#### Acceptance Criteria

1. WHEN the caller imports `IUefiShellRunner` THEN the system SHALL expose
   it as a `typing.Protocol` decorated with `@runtime_checkable`.
2. WHEN a concrete class implements
   `run_cmd(self, command: str, aspects: Optional["UefiCmdAspects"] = None) -> "UefiShellResult"`
   THEN `isinstance(instance, IUefiShellRunner)` SHALL return `True`.
3. WHERE the protocol references `UefiCmdAspects` and `UefiShellResult` the
   system SHALL quote them in type hints (`"..."`) to avoid a hard import
   cycle with higher-layer features.
4. WHEN the protocol is documented the system SHALL include a note that
   the `aspects=None` case means "use the instance default" per the
   measured-interop contract.

### Requirement 3 — `IUefiVariableProvider` Protocol

**User Story:** As a consumer of UEFI variable CRUD, I want a protocol
whose method set covers the five runtime service calls
(`GetVariable`, `SetVariable`, `GetNextVariableName`,
`QueryVariableInfo`), so that backend implementations for Shell, Redfish,
and SSH-to-host are interchangeable.

#### Acceptance Criteria

1. WHEN the caller imports `IUefiVariableProvider` THEN the system SHALL
   expose it as a `typing.Protocol` decorated with `@runtime_checkable`.
2. WHEN a concrete class implements
   `get_variable(self, name: str, vendor_guid: uuid.UUID, aspects=None) -> "UefiVariable"`,
   `set_variable(self, variable: "UefiVariable", aspects=None) -> None`, and
   `enumerate_variables(self, aspects=None) -> Iterator["UefiVariableName"]`
   THEN `isinstance(instance, IUefiVariableProvider)` SHALL return `True`.
3. WHERE `enumerate_variables` is typed as `Iterator[...]` the system SHALL
   document that the method is expected to stream lazily rather than
   return an eagerly built list.
4. WHEN the protocol is documented the system SHALL include method
   docstrings naming `UefiVariableError` as the raised exception type on
   failures.

### Requirement 4 — `IUefiFileProvider` Protocol

**User Story:** As a consumer of ESP file operations, I want a protocol
whose method set matches the `SshAgent` file API, so that ESP-backing
transports are interchangeable.

#### Acceptance Criteria

1. WHEN the caller imports `IUefiFileProvider` THEN the system SHALL
   expose it as a `typing.Protocol` decorated with `@runtime_checkable`.
2. WHEN a concrete class implements `file_push(self, local: str, remote: str,
   aspects=None) -> None`, `file_pull(self, remote: str, local: str,
   aspects=None) -> None`, `file_exists(self, remote: str, aspects=None) -> bool`,
   `directory_exists(self, remote: str, aspects=None) -> bool`,
   `directory(self, remote: str, aspects=None) -> list[str]`, and
   `directory_tree(self, remote: str, aspects=None) -> list[str]` THEN
   `isinstance(instance, IUefiFileProvider)` SHALL return `True`.
3. WHEN the protocol is documented the system SHALL clarify that paths
   follow the UEFI convention (`FS0:\\path`) for Shell transports and
   POSIX conventions for SSH-to-host.

### Requirement 5 — `IUefiSystemContext` Protocol

**User Story:** As a consumer of system-level metadata, I want a protocol
that extends the shared `ISystemContext` with UEFI-specific accessors, so
that test code can check the Secure Boot state or firmware vendor without
reaching into transport internals.

#### Acceptance Criteria

1. WHEN the caller imports `IUefiSystemContext` THEN the system SHALL
   expose it as a `typing.Protocol` that extends the shared
   `ISystemContext` (via protocol composition, not inheritance).
2. WHEN a concrete class implements the properties `firmware_vendor(self)
   -> str`, `firmware_revision(self) -> int`, and
   `secure_boot_state(self) -> "SecureBootState"` THEN
   `isinstance(instance, IUefiSystemContext)` SHALL return `True` (given
   it also satisfies the base `ISystemContext` shape).
3. WHERE the protocol references `SecureBootState` the system SHALL quote
   it to avoid cross-layer import cycles.
4. WHEN the protocol is decorated the system SHALL use
   `@runtime_checkable` so `UefiAgent` / `UefiSession` can advertise
   compliance via `isinstance`.

### Requirement 6 — Type Hints Everywhere

**User Story:** As a consumer relying on editor intelligence, I want every
method on every protocol to be fully type-hinted, so that `mypy` /
Pyright sees the same shape the docstrings describe.

#### Acceptance Criteria

1. WHERE a protocol method is declared the system SHALL annotate every
   parameter with a type hint.
2. WHERE a protocol method is declared the system SHALL annotate the
   return type (use `None` for void).
3. WHERE a protocol declares a property the system SHALL annotate the
   property return type.

### Requirement 7 — Sphinx-Style Docstrings

**User Story:** As a consumer, I want every protocol method and property
to have a Sphinx-style docstring, so that hover documentation in my
editor is sufficient.

#### Acceptance Criteria

1. WHERE a protocol method is declared the system SHALL document it with
   `:param:` entries for every parameter.
2. WHERE a protocol method returns a value the system SHALL document it
   with a `:returns:` entry.
3. WHERE a protocol method may raise the system SHALL document the
   concrete exception types in a `:raises:` entry.

### Requirement 8 — `@runtime_checkable` Opt-In

**User Story:** As an author writing tests that assert interface
compliance, I want to use `isinstance` against protocol classes where
runtime dispatch matters, so that I can gate behavior on protocol
membership without inspecting methods manually.

#### Acceptance Criteria

1. WHERE runtime dispatch on a protocol is expected (all five in this
   feature) the system SHALL decorate the protocol with
   `@runtime_checkable`.
2. WHERE a protocol is used only as a typing hint (none in this feature
   currently) the system SHALL skip the decorator and document the
   rationale.

### Requirement 9 — Implementor-Compliance Tests

**User Story:** As a maintainer, I want a test that asserts every
concrete class in layers 2–4 satisfies its declared interface, so that a
regression that breaks compliance is caught at CI time rather than at
first production failure.

#### Acceptance Criteria

1. WHEN the compliance test runs THEN the system SHALL scan a curated
   registry of `(concrete_class, protocol)` pairs and assert
   `isinstance(concrete(), protocol)` for each.
2. WHEN a concrete class cannot be instantiated without arguments (most
   will require a credential at minimum) the test SHALL use
   `hasattr`-based checks on the class itself rather than an instance.
3. WHEN a new concrete class is introduced the author SHALL add it to the
   compliance registry in the same commit.

### Requirement 10 — Zero Runtime Side Effects on Import

**User Story:** As a consumer, I want importing the interfaces module to
have no observable side effect beyond binding names, so that module load
stays bounded in time and memory.

#### Acceptance Criteria

1. WHEN `ccai.protocols.uefi.interfaces` is imported THEN the system
   SHALL NOT open files, sockets, subprocesses, or register global
   handlers.
2. WHEN the module is imported twice in the same process THEN the system
   SHALL produce identical class identities for each protocol.

### Requirement 11 — Stable Module Surface

**User Story:** As a consumer, I want `__all__` to enumerate every
protocol, so that adding or removing one is a visible diff.

#### Acceptance Criteria

1. WHEN the module is imported THEN the system SHALL expose `__all__`
   listing every public protocol class.
2. WHERE a helper is private the system SHALL prefix its name with a
   single underscore and SHALL NOT include it in `__all__`.
