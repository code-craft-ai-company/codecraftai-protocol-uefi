# codecraftai-protocol-uefi — Feature Specification Plan

## Purpose

This document is the authoritative index of features to be implemented in the
`codecraftai-protocol-uefi` interop library.  It organizes those features into six layers of the
software stack and, for each feature, sketches the content that will later be expanded
into a Kiro-style spec folder.

Each feature listed below will be promoted into its own folder of the form:

```
specs/{layer}-{feature-name}/
    requirements.md     # EARS-style requirements + user stories
    design.md           # architecture, components, interfaces, data models
    tasks.md            # hierarchical implementation checklist
```

The `{layer}` prefix is a single digit (`1`–`6`).  The `{feature-name}` is a
kebab-case short name that matches the canonical name given in this plan.

The purpose of this plan is to ensure that the eventual spec folders are *consistent*,
*complete*, and *measured in scope* — this is what we hand to an implementing agent as
the starting point for authoring the per-feature specifications.

---

## Cross-Cutting Convention: Measured Interop

Every layer-3 (client), layer-4 (base / agent / session), layer-5 (domain), and
layer-6 (workflow) feature in this library **must** honor the *measured interop*
pattern established by `codecraftai-protocol-ssh`.  The pattern is:

1. **Behavior is packaged, not decorated.**  Retry, timeout, logging, and
   connection behavior is carried by a `Freezable` `UefiAspects` (or
   `UefiCmdAspects`) object.  Behavior-modifying decorators such as `@retry` are
   forbidden — see the coding standards in `/Users/myron/repos/CLAUDE.md`.
2. **Callers choose persistence.**  An `aspects` parameter is accepted at every
   public interop entry point and takes precedence over the instance default.
   Callers select from `ActionPattern.SINGLE_CALL`,
   `ActionPattern.DO_UNTIL_SUCCESS`, `ActionPattern.DO_WHILE_SUCCESS`,
   `ActionPattern.SINGLE_CONNECTED_CALL`, and
   `ActionPattern.DO_UNTIL_CONNECTION_FAILURE`.
3. **Timeouts and intervals are explicit.**  `completion_timeout`,
   `completion_interval`, `connection_timeout`, `connection_interval`,
   `inactivity_timeout`, `inactivity_interval`, and `monitor_delay` are all
   caller-adjustable.
4. **Logging pattern is configurable.**  `LoggingPattern.ALL_RESULTS`,
   `FAILURE_ONLY`, `SUCCESS_ONLY`.
5. **Allowed error codes and connection exceptions are caller-supplied.**
   Features never silently swallow failures; they require the caller to declare
   what is tolerable via `allowed_error_codes` and
   `allowed_connection_exceptions`.
6. **Freezable after construction.**  All aspects instances freeze on exit from
   `__init__` so they can safely be held as module-level defaults.

Every `design.md` produced from this plan **must** include a section titled
"Measured Interop Contract" that names the exact `aspects` parameters the
feature consumes and the `ActionPattern`s it supports.

Every `requirements.md` produced from this plan **must** include at least one
EARS-style requirement for each action pattern the feature claims to support,
plus a requirement covering behavior when `aspects=None` (the instance default
shall be used).

Every `tasks.md` produced from this plan **must** include a task for authoring
targeted tests against each supported action pattern.

---

## Cross-Cutting Convention: Kiro Spec Shape

Each per-feature `requirements.md` will have this structure:

```
# Requirements — <Feature Name>

## Introduction
(1–2 paragraphs: what this feature is, why it exists, who uses it.)

## Requirements

### Requirement 1 — <Short Title>
**User Story:** As a <role>, I want <capability> so that <outcome>.

#### Acceptance Criteria
1. WHEN <trigger> THEN the system SHALL <observable behavior>.
2. IF <precondition> THEN the system SHALL <observable behavior>.
3. WHILE <state> the system SHALL <invariant>.
4. WHERE <context> the system SHALL <observable behavior>.
(…)

### Requirement 2 — …
```

Each per-feature `design.md` will have this structure:

```
# Design — <Feature Name>

## Overview
## Architecture
## Components and Interfaces
## Data Models
## Measured Interop Contract
## Error Handling
## Logging
## Security Considerations
## Testing Strategy
## Dependencies on Other Features
```

Each per-feature `tasks.md` will have this structure:

```
# Tasks — <Feature Name>

- [ ] 1. <Top-level implementation task>
  - [ ] 1.1 <Sub-task>
  - [ ] 1.2 <Sub-task>
  - References: Requirement 1.1, Requirement 2.3
- [ ] 2. …
```

Tasks must reference the specific requirement numbers they satisfy.

---

## Layer Model

| Layer | Name                         | Depends On     | Examples                                      |
|-------|------------------------------|----------------|-----------------------------------------------|
| 1     | Foundation                   | —              | Aspects, constants, exceptions, credentials   |
| 2     | Transport                    | 1              | Serial, Redfish, SSH-to-host, IPMI-SOL        |
| 3     | Client / Connection          | 1, 2           | `UefiClient`, `UefiConnection`, `UefiChannel` |
| 4     | Agent / Session / Base       | 1, 2, 3        | `UefiBase`, `UefiAgent`, `UefiSession`        |
| 5     | Domain Capabilities          | 1–4            | Shell, variables, boot manager, GPT, capsules |
| 6     | Composite Workflows          | 1–5            | Reboot-and-verify, capsule-update workflow    |

Lower-numbered layers **must not** import from higher-numbered layers.  This is
enforced by placement in the package tree (see individual feature design docs)
and verified by a dependency-direction test in the test suite.

---

# Layer 1 — Foundation

Foundation primitives have no dependencies on other features and form the vocabulary
every higher layer uses.

## 1-aspects

**Description.** The `UefiAspects` and `UefiCmdAspects` `Freezable` behavior-package
classes, plus the `ActionPattern` and `LoggingPattern` enums and the module-level
`DEFAULT_ASPECTS` / `DEFAULT_CMD_ASPECTS` / `UNDER_CONSTRUCTION_ASPECTS` constants.
Mirrors `codecraftai-protocol-ssh`'s `sshaspects.py` almost exactly but parameterized
for UEFI command semantics (no `pty_params`, additional `prompt_pattern`,
`response_encoding`, and `transport_kind` fields).

**Key Requirements.**
- Construction with defaults shall produce an instance that is byte-identical on
  re-construction (deterministic defaults).
- `freeze()` shall prevent further attribute mutation and raise `AttributeError`
  on any assignment.
- Each `ActionPattern` value shall have a documented semantic and at least one
  consumer in layer 4 or 5.
- Passing unsupported `allowed_connection_exceptions` types shall raise
  `ConfigurationError` at construction time.
- The class shall accept an optional `logger` parameter and fall back to
  `getEnhancedLogger()` when omitted.

**Key Design Points.**
- Subclass `Freezable` base (reimplemented here rather than imported, to keep
  layer 1 self-contained).
- `UefiCmdAspects` extends `UefiAspects` with `expected_status`,
  `prompt_pattern`, `response_encoding`, and `user`.
- Provide a `merge(other: UefiCmdAspects) -> UefiCmdAspects` helper that
  produces a new frozen instance with `other`'s non-default values overlaid.
  Needed because callers often want to tweak a single field of a preset.
- All default constants are module-level frozen singletons.

**Key Tasks.**
- [ ] Author `uefiaspects.py` with `Freezable`, `ActionPattern`, `LoggingPattern`,
      `UefiAspects`, `UefiCmdAspects`.
- [ ] Author `DEFAULT_ASPECTS`, `DEFAULT_CMD_ASPECTS`,
      `UNDER_CONSTRUCTION_ASPECTS` constants.
- [ ] Author `merge()` helper with symmetric tests.
- [ ] Author unit tests for: freeze semantics, default determinism,
      `ConfigurationError` on bad inputs, `merge` overlay semantics.
- [ ] Document every field with Sphinx-style `:param:` entries per
      `/Users/myron/repos/CLAUDE.md` coding standards.

## 1-constants-and-types

**Description.** All UEFI-wide constants, typed dataclasses, GUID aliases, and
type aliases used across the library.  Includes `UefiJumpParams`, command-output
template strings, prompt sentinel constants, timeout defaults, and the master
registry of well-known UEFI GUIDs (`EFI_GLOBAL_VARIABLE_GUID`,
`EFI_IMAGE_SECURITY_DATABASE_GUID`, `EFI_SYSTEM_RESOURCE_TABLE_GUID`, etc.).

**Key Requirements.**
- GUID constants shall be represented as `uuid.UUID` objects, not raw strings or
  raw bytes; conversion helpers `guid_to_bytes` / `guid_from_bytes` shall be
  provided.
- All public dataclasses shall be `frozen=True` and `slots=True` where the
  Python version supports it.
- Command-output templates shall embed `os.linesep` rather than hard-coded
  `"\n"`.

**Key Design Points.**
- Single `ueficonst.py` module, split into labelled sections with comment
  banners.
- A `uefiguids.py` submodule that is auto-loadable and importable on its own.
- `UefiJumpParams` parallels `SshJumpParams` — used when Redfish is reachable
  only through a jump-host.

**Key Tasks.**
- [ ] Author `ueficonst.py` and `uefiguids.py`.
- [ ] Add a pytest fixture that snapshots the GUID registry and fails CI if a
      known GUID value drifts.
- [ ] Round-trip tests for `guid_to_bytes` / `guid_from_bytes`.

## 1-exceptions

**Description.** UEFI-specific exception hierarchy layered on top of the shared
`ccai.errors.exceptions` module.  Provides `UefiError`, `UefiShellError`,
`UefiVariableError`, `UefiCapsuleError`, `UefiSecureBootError`,
`UefiTransportError`, and `UefiProtocolError`.

**Key Requirements.**
- Every exception class shall accept a `details: list[str]` kwarg; its
  `__str__` shall join details with `os.linesep` per the multiline-error-message
  coding standard.
- `UefiShellError` shall carry `command`, `status`, `stdout`, `stderr`
  attributes.
- `UefiVariableError` shall carry `name`, `vendor_guid`, `attributes`,
  `efi_status` attributes.
- `SemanticError` (from the shared error module) shall never be caught inside
  this library.

**Key Design Points.**
- Use `ConfigurationError` (shared) for caller-facing misconfiguration rather
  than introducing a UEFI-specific subtype.
- Each exception serializes to a structured dict via an `as_dict()` method — for
  structured-logging consumers.

**Key Tasks.**
- [ ] Author `uefiexceptions.py`.
- [ ] Unit tests covering: multiline formatting, `as_dict` round-trip, attribute
      presence.

## 1-credentials

**Description.** Credential types for each supported transport.  Includes
`UefiSerialCredential` (baud, flow-control, optional login/password for firmware
that gates the shell), `UefiRedfishCredential` (URL base, username, password or
token, CA-bundle, TLS-verify flag), and `UefiIpmiCredential` (host, user,
password, interface).  The SSH-to-host transport reuses the existing
`ccai.credentials.sshcredential.SshCredential`.

**Key Requirements.**
- Each credential class shall provide a `validate()` method that raises
  `ConfigurationError` when required fields are missing or contradictory.
- Sensitive fields (password, token) shall never appear in `__repr__`.
- Credentials shall be immutable after `validate()` succeeds.

**Key Design Points.**
- Use `@dataclass(frozen=True)` with `__post_init__` for validation.
- Token-based Redfish auth shall take precedence over username/password when
  both are provided, and `validate()` shall reject the ambiguous case unless an
  explicit `prefer_token=True` is set.

**Key Tasks.**
- [ ] Author `uefiserialcredential.py`, `uefiredfishcredential.py`,
      `uefiipmicredential.py`.
- [ ] Author validation tests covering missing fields, contradictory fields,
      token/password precedence.
- [ ] Author `__repr__` redaction tests.

## 1-helpers

**Description.** Pure-function primitives used by higher layers: hex-dump,
CRC-32 (for GPT), UCS-2 encode/decode (UEFI string encoding), big/little-endian
integer packers, `Guid` binary packers, `TimeoutContext` (re-export or thin
wrapper), `MonitoredScope` (re-export or thin wrapper), `indent_lines`.

**Key Requirements.**
- UCS-2 encoder shall raise `UnicodeEncodeError` on characters outside the
  BMP — UEFI strings are strict UCS-2, not UTF-16 with surrogates.
- CRC-32 implementation shall match `EFI_BOOT_SERVICES.CalculateCrc32` bit-for-bit
  and be validated against a known-good test vector from the UEFI spec.
- Helpers shall have zero I/O — pure functions only.

**Key Design Points.**
- Keep this module import-light; it is pulled in by every layer.
- No dependency on `paramiko` or any transport library.

**Key Tasks.**
- [ ] Author `uefihelpers.py`.
- [ ] Copy and document the UEFI CRC-32 test vector.
- [ ] Property tests for encode/decode round-trips.

## 1-logging

**Description.** Thin integration layer with `ccai.xmodules.xlogging` that
provides `get_uefi_logger()` and the `LoggingPattern`-driven result templates
used by layer 4.  Mirrors `codecraftai-protocol-ssh`'s logging integration but with
UEFI-specific channel names (`uefi.shell`, `uefi.variable`, `uefi.capsule`,
`uefi.transport`).

**Key Requirements.**
- Every log line emitted from this library shall be attributable to a named
  channel under the `uefi.*` namespace.
- The `LoggingPattern` enum consumed by `UefiBase._log_command_result` shall be
  importable from this module.

**Key Tasks.**
- [ ] Author `uefilogging.py`.
- [ ] Smoke test that exercises each channel.

## 1-interfaces

**Description.** Abstract protocol interfaces that shape the rest of the stack.
Includes `IUefiTransport` (connect/disconnect/send/recv primitives),
`IUefiShellRunner` (run_cmd-style), `IUefiVariableProvider` (get/set/enumerate
variable), `IUefiFileProvider` (ESP file operations), and a thin
`IUefiSystemContext` that extends the shared `ISystemContext` with UEFI-specific
accessors (`firmware_vendor`, `firmware_revision`, `secure_boot_state`).

**Key Requirements.**
- Interfaces shall be defined using `typing.Protocol` with `@runtime_checkable`
  where runtime dispatch is needed.
- Every interface method shall have type hints and a Sphinx-style docstring.

**Key Tasks.**
- [ ] Author `uefiinterfaces.py`.
- [ ] Author `isinstance`-style tests that verify the concrete classes in
      layers 2–4 satisfy their declared interface.

---

# Layer 2 — Transport

Transports abstract how the library reaches a UEFI target.  Each transport
satisfies `IUefiTransport` and exposes connection lifecycle hooks honoring the
measured-interop aspects.

## 2-transport-serial

**Description.** Byte-level transport over a local serial port (typically
`/dev/ttyUSB*` or `/dev/tty.usbserial-*`) or a remote serial port reached via a
network serial concentrator.  Handles baud, parity, flow-control, and includes
a line-buffered read mode with configurable end-of-line normalization
(CR/LF/CRLF).  Exposes a cooked `read_until(pattern, timeout)` primitive for
prompt detection.

**Key Requirements.**
- The transport shall support both local POSIX serial devices and remote
  TCP-wrapped serial (`telnet://host:port`, `socket://host:port`).
- Connect attempts shall be bounded by `aspects.connection_timeout` with retries
  every `aspects.connection_interval` until the timeout elapses.
- `read_until(pattern, timeout)` shall raise `TimeoutError` if the pattern is
  not observed within `timeout` seconds; partial bytes collected shall be
  included on the raised exception.
- The transport shall restore the terminal settings of a local serial device
  on close.

**Key Design Points.**
- Use `pyserial` for local devices; use a built-in `socket.create_connection`
  fallback for TCP-wrapped serial.
- Internal read buffer is a `bytearray`; callers receive immutable copies.
- Connection objects are context managers.

**Key Tasks.**
- [ ] Author `uefiserialtransport.py`.
- [ ] Integration tests using `pty`-backed pseudo-serial pairs on POSIX.
- [ ] Fault-injection tests: flapping connections, truncated reads, CR-only
      line endings.

## 2-transport-redfish

**Description.** Transport over the DMTF Redfish REST API of a BMC or
out-of-band management controller.  Supports UEFI variable access via the
`/Systems/{id}/Bios` subtree, boot-order manipulation via
`/Systems/{id}/Boot`, capsule upload via the `UpdateService`, and a
`VirtualMedia`-based ESP mount for file operations.

**Key Requirements.**
- The transport shall authenticate via Basic or token (`X-Auth-Token`) and
  shall automatically renew the session when Redfish returns 401.
- TLS-verification flag shall default to `True`; callers must explicitly pass
  `verify=False` for lab-grade self-signed BMCs.
- All requests shall include `OData-Version: 4.0` and an `Accept:
  application/json` header.
- Long-running tasks returned by Redfish (`202 Accepted` with a `Location:`
  pointing at a TaskMonitor) shall be polled every
  `aspects.completion_interval` until completion or `aspects.completion_timeout`
  elapses.

**Key Design Points.**
- Use `requests` (or `httpx` if async is desired later — single-threaded sync
  is sufficient for v1).
- Session object owns the TCP connection pool and renews tokens transparently.
- Response bodies are parsed into `dict` eagerly and cached briefly per
  URI when the ETag is present.

**Key Tasks.**
- [ ] Author `uefiredfishtransport.py`.
- [ ] Build a `responses`-library-backed fake Redfish server for tests.
- [ ] Tests for: authentication, token refresh, `202`-task polling, ETag
      caching, TLS-verify default.

## 2-transport-ssh-host

**Description.** Transport that reuses `codecraftai-protocol-ssh` to reach a
*booted* operating system on the target and run OS-resident UEFI tooling
(`efibootmgr`, `efivar`, `efivar-tests`, `fwupdmgr`,
`chattr /sys/firmware/efi/efivars/...`).  This transport cannot be used pre-boot
but is the lowest-friction path for post-boot inspection.

**Key Requirements.**
- The transport shall accept an existing `SshAgent` instance (composition, not
  inheritance) so that caller-side SSH session management is preserved.
- OS capability probing shall run at `connect()` and populate a feature-flag
  dict (`has_efibootmgr`, `has_efivar`, `has_fwupd`,
  `has_efivarfs_rw`).  Features elsewhere in the stack consult this dict.
- The transport shall raise `UefiTransportError` with actionable `details`
  when a required binary is missing.

**Key Design Points.**
- All commands run through `SshAgent.run_cmd`; the transport never opens its
  own paramiko session.
- Sudo-requiring commands use an `as_root: bool` parameter that the transport
  escalates via `sudo -n` (non-interactive).  If sudo prompts, the transport
  fails with `ConfigurationError`.

**Key Tasks.**
- [ ] Author `uefisshtransport.py`.
- [ ] Capability-probe unit tests with mocked `SshAgent.run_cmd`.
- [ ] An integration test runnable against a disposable Linux VM
      (gated by an env var).

## 2-transport-ipmi-sol

**Description.** Transport over Serial-Over-LAN via `ipmitool sol activate` or
the `pyghmi` library.  Functionally equivalent to `2-transport-serial` once the
SOL session is established; shares the read-until / prompt-detection stack with
the serial transport.

**Key Requirements.**
- The transport shall deactivate any existing SOL session on the target before
  activating its own, controlled by an `evict_existing: bool = False` flag
  (default `False` to avoid accidentally kicking another operator off).
- The transport shall surface `Deactivation failed` errors from IPMI as
  `UefiTransportError` with the raw tool output captured in `details`.

**Key Design Points.**
- Prefer `pyghmi` where the caller passes a pre-built IPMI session object;
  fall back to shelling out to `ipmitool` when only credentials are available.
- Reuse the read/prompt-detection machinery of `2-transport-serial` via
  composition.

**Key Tasks.**
- [ ] Author `uefiipmisoltransport.py`.
- [ ] Mocked `ipmitool` subprocess tests.
- [ ] Integration test gated by `CCAI_IPMI_HOST` env var.

---

# Layer 3 — Client / Connection

Layer 3 provides protocol-framing primitives: how bytes become commands, how
commands become framed requests, and how the request/response cycle is
represented as an object.

## 3-client

**Description.** `UefiClient` is the per-transport protocol client.  It
consumes a layer-2 transport and adds prompt synchronization for
shell-oriented transports (serial, IPMI-SOL), HTTP session management for
Redfish, and OS-command dispatch for SSH-to-host.  It exposes
`open_channel()`, `execute()`, and `close()`.

**Key Requirements.**
- `UefiClient.open_channel()` shall always return a fresh `UefiChannel`; the
  client shall not multiplex channels onto a single transport without caller
  opt-in.
- `execute(command, aspects)` shall honor every aspect in
  `UefiCmdAspects`.
- On connection loss mid-execute, the client shall raise `ConnectionError`
  immediately — retry is the caller's responsibility via aspects on layer 4.

**Key Design Points.**
- Abstract base `UefiClient` with three concrete subclasses:
  `UefiShellClient` (serial / SOL), `UefiRedfishClient`,
  `UefiHostOsClient` (SSH).
- Each subclass declares which `ActionPattern`s are supported; unsupported
  patterns raise `SemanticError` rather than silently degrading.

**Key Tasks.**
- [ ] Author `ueficlient.py` with abstract base + three subclasses.
- [ ] Unit tests per subclass.
- [ ] Integration tests using the fake transports from layer 2.

## 3-connection

**Description.** `UefiConnection` wraps the low-level transport socket/port/HTTP
session in a lifecycle-managed object.  Carries reconnection logic bounded by
aspects.

**Key Requirements.**
- A `UefiConnection` shall emit a structured log event on every
  connect/disconnect/reconnect transition under channel `uefi.transport`.
- `connected` shall be a property, never a method.
- `reconnect()` shall preserve the original credential + aspects so the caller
  does not have to rebuild state.

**Key Design Points.**
- Reuses the `connection_timeout` / `connection_interval` /
  `allowed_connection_exceptions` triad from aspects.
- Reconnection attempts are counted and exposed via `.reconnect_count` — used
  by tests and diagnostics.

**Key Tasks.**
- [ ] Author `ueficonnection.py`.
- [ ] Tests for: connect, disconnect, reconnect with backoff, exception
      allow-listing.

## 3-channel

**Description.** `UefiChannel` is a single logical command channel.  For
shell-style transports it is a PTY-like pipe; for Redfish it is a dedicated
`Session` with scoped headers; for SSH-to-host it is the underlying
`SshChannel` forwarded through composition.

**Key Requirements.**
- A `UefiChannel` shall expose `send`, `recv`, `recv_until`, and `close`
  methods with identical signatures across subclasses.
- `recv_until(pattern, timeout)` shall accept `pattern` as `bytes` or a
  compiled `re.Pattern[bytes]`.
- Closing a channel shall never raise — any underlying close failures are
  logged at `uefi.transport` at `WARNING` and suppressed.

**Key Design Points.**
- Channels are short-lived; the layer 4 base class opens/closes channels as
  needed per `ActionPattern`.
- Chunk size and inactivity timeouts flow through from aspects.

**Key Tasks.**
- [ ] Author `uefichannel.py`.
- [ ] Tests for each subclass's send/recv round trip.
- [ ] Inactivity-timeout test using a stalled fake transport.

---

# Layer 4 — Agent / Session / Base

Layer 4 hosts the shared state and the generic run-pattern engine that drives
every domain capability.

## 4-base

**Description.** `UefiBase` is the shared-state/shared-behavior base class for
`UefiAgent` and `UefiSession`.  It owns credentials, aspects, host/transport
config, and implements the `ActionPattern` engine (exact analog of
`SshBase._run_cmd`).  It implements `IUefiSystemContext`.

**Key Requirements.**
- The action-pattern engine shall implement all five patterns (`SINGLE_CALL`,
  `SINGLE_CONNECTED_CALL`, `DO_UNTIL_SUCCESS`, `DO_WHILE_SUCCESS`,
  `DO_UNTIL_CONNECTION_FAILURE`).
- Every retry loop shall wrap the inner call in a `MonitoredScope` so hung
  threads are detectable.
- A `verify_connectivity()` primitive shall be provided and shall default to
  running the UEFI Shell `echo` command (or the transport-native equivalent).
- `AbstractMethodError` shall be raised from any method intended to be
  overridden, following the SSH base-class pattern.

**Key Design Points.**
- Action-pattern engine is a `_run(...)` method shared by both `UefiAgent` and
  `UefiSession`; public `run_cmd` / `run_redfish` / `run_variable` wrappers
  compose onto it.
- Logging of command results uses the `LoggingPattern` aspect exactly as
  `SshBase._log_command_result` does.
- All instance variables are initialized in `__init__` — no lazy attribute
  creation elsewhere, per coding standards.

**Key Tasks.**
- [ ] Author `uefibase.py`.
- [ ] Unit tests for each `ActionPattern`, including the error paths.
- [ ] `MonitoredScope` integration test that verifies hang notification.

## 4-agent

**Description.** `UefiAgent` is the one-shot convenience class.  Every public
method opens a fresh channel, performs the operation, and closes the channel.
Mirrors `SshAgent`.

**Key Requirements.**
- `UefiAgent` shall accept `host`, `primary_credential`, `transport_kind`,
  `jump`, `called_id`, and `aspects` at construction.
- `UefiAgent.reboot()` shall implement the same reconnect-verify pattern as
  `SshAgent.reboot()` — issue reset, wait for port return, verify shell.
- Every public method shall accept an optional `aspects` override.

**Key Design Points.**
- `open_session(...) -> UefiSession` exactly parallels `SshAgent.open_session`.
- `_create_client()` centralizes client construction and respects aspects for
  retry/backoff.

**Key Tasks.**
- [ ] Author `uefiagent.py`.
- [ ] Reboot flow integration test using a fake transport that simulates
      disconnect/reconnect.
- [ ] Tests for `open_session` basis-session reuse.

## 4-session

**Description.** `UefiSession` is the persistent-channel class for workloads
that need to issue many commands against a shared transport context (e.g., a
long UEFI Shell session).  Mirrors `SshSession`.

**Key Requirements.**
- A session shall maintain exactly one open channel between `__enter__` and
  `__exit__`.
- Session re-connection on transient failure shall be driven by the
  `DO_UNTIL_CONNECTION_FAILURE` pattern; other patterns shall raise
  `SemanticError` as in the SSH implementation.
- The session shall preserve environment-like state (current ESP map alias,
  current working directory inside the shell) and replay it on reconnect.

**Key Design Points.**
- `basis_session` parameter permits a new session to clone the connection
  parameters of an existing one — identical semantics to `SshSession`.
- State replay is implemented via a `_replay_actions` list of `Callable`s the
  session records.

**Key Tasks.**
- [ ] Author `uefisession.py`.
- [ ] Tests covering basis-session cloning, state replay, reconnect behavior.

---

# Layer 5 — Domain Capabilities

Layer-5 features implement specific UEFI-spec-derived capabilities.  Each is
scoped so it can be authored by a single agent iteration and tested in
isolation.

## 5-shell-commands

**Description.** High-level wrappers around common UEFI Shell commands
(`map`, `ls`, `cp`, `cd`, `bcfg`, `setvar`, `dmpstore`, `reset`, `dh`,
`memmap`, `smbiosview`, `drivers`, `devtree`).  Each wrapper parses the
command's text output into a typed dataclass so callers do not have to scrape
strings.

**Spec references.** Various (UEFI Shell Specification, not the core UEFI
spec).

**Key Requirements.**
- Each wrapper shall accept an `aspects` override.
- Each wrapper shall return a typed dataclass, never a raw string.
- Unknown output lines shall not be silently dropped — they are collected into
  a `.unparsed_lines` field so debugging remains possible.

**Key Design Points.**
- One module per command family: `shellmap.py`, `shellbcfg.py`,
  `shellsetvar.py`, etc., all under `packages/ccai/protocols/uefi/shell/`.
- Regex parsers are compiled at module-import time and tested against a
  fixtures corpus of captured real-world shell output.

**Key Tasks.**
- [ ] Author per-command modules.
- [ ] Collect a `tests/fixtures/shell/` corpus of captured output.
- [ ] Snapshot tests for every wrapper.

## 5-variable-services

**Description.** UEFI variable CRUD backed by any of the three variable-capable
transports (Shell `setvar` / `dmpstore`, SSH-to-host via `/sys/firmware/efi/efivars`
and `efivar`, Redfish BIOS attribute set).  Implements `GetVariable`,
`SetVariable`, `GetNextVariableName`, and `QueryVariableInfo`.

**Spec references.** §8.2, §8.2.5 (authenticated variables).

**Key Requirements.**
- All five attribute flags (`NON_VOLATILE`, `BOOTSERVICE_ACCESS`,
  `RUNTIME_ACCESS`, `HARDWARE_ERROR_RECORD`,
  `AUTHENTICATED_WRITE_ACCESS`, `TIME_BASED_AUTHENTICATED_WRITE_ACCESS`,
  `APPEND_WRITE`, `ENHANCED_AUTHENTICATED_ACCESS`) shall be round-trippable.
- Writing an authenticated variable shall accept an `EFI_VARIABLE_AUTHENTICATION_2`
  descriptor or an unwrapped signer (key + certificate) and assemble the
  descriptor internally.
- Enumerating variables shall stream results via a generator rather than
  returning a single eagerly-built list.

**Key Design Points.**
- A `VariableStore` facade hides the per-transport backend; callers never have
  to know which transport is underneath.
- Attribute flags represented as a `flag` enum-like `IntFlag`.
- Time-based auth descriptors use `datetime` → `EFI_TIME` converters from
  layer 1 helpers.

**Key Tasks.**
- [ ] Author `uefivariablestore.py` + one backend per transport.
- [ ] Round-trip tests for each attribute-flag combination.
- [ ] Authenticated-write test with a self-signed key fixture.

## 5-boot-manager

**Description.** Boot-option management: `Boot####`, `BootOrder`, `BootNext`,
`BootCurrent`, `Timeout`, `Driver####`, `DriverOrder`, `SysPrep####`.
Structures and codec for `EFI_LOAD_OPTION`.

**Spec references.** §3, §3.1.3 (Load Options), §3.3 (Globally Defined Variables).

**Key Requirements.**
- `BootOption.decode(bytes)` and `BootOption.encode() -> bytes` shall be
  symmetric over a corpus of real-world options.
- `set_boot_next(option_id)` shall verify that `Boot{option_id:04X}` exists
  before writing `BootNext`; raise `UefiVariableError` if it does not.
- Changing `BootOrder` shall read-modify-write atomically (best-effort: detect
  concurrent change via re-read and retry up to `aspects.completion_timeout`).

**Key Design Points.**
- Sits on top of `5-variable-services`.
- `BootOption` is a `frozen=True` dataclass with factory classmethods.
- Human-readable rendering delegates to `5-device-path-codec` for the
  embedded device-path field.

**Key Tasks.**
- [ ] Author `uefibootmanager.py`.
- [ ] Decode fixtures corpus covering Windows Boot Manager, shim, GRUB, rEFInd,
      UEFI Shell load options.
- [ ] Atomic `BootOrder` update tests.

## 5-device-path-codec

**Description.** Full binary ⇄ text codec for UEFI Device Paths.  Covers every
node type in §10.3 and conforms to the text representation in §10.6.

**Spec references.** §10 (all subsections).

**Key Requirements.**
- Encoder and decoder shall round-trip every node type enumerated in §10.3
  without loss.
- Text representation shall produce output byte-identical to
  `ConvertDevicePathToText()` for the canonical examples in §10.6.
- Unknown node types shall be represented using the `Path(type,subtype,data)`
  generic form per §10.6.1.6 and shall not error.

**Key Design Points.**
- One parser function per node type, dispatched via a `(type, subtype)` →
  callable registry.
- Text parser uses a `pyparsing`-free hand-rolled recursive-descent parser so
  this feature has no third-party parser dependency.

**Key Tasks.**
- [ ] Author `uefidevicepath.py`.
- [ ] Author a fixtures corpus derived from the examples in §10.6.
- [ ] Property-based round-trip tests using `hypothesis`.

## 5-gpt-mbr-parser

**Description.** Parser and builder for the GPT layout (header + partition
entry array + protective MBR) and for the legacy MBR.  Includes CRC-32
validation via layer-1 helpers.

**Spec references.** §5.

**Key Requirements.**
- `GptHeader.from_bytes(lba0, lba1)` shall validate both CRC fields and raise
  `UefiError` with both the computed and stored CRCs in `details` on mismatch.
- Primary / backup header comparison shall be exposed via `validate_pair()`.
- Partition type GUIDs (§5.3.3) shall be enumerated as named constants.

**Key Design Points.**
- Pure Python, no `sgdisk` / `parted` shell-outs.
- Read-only by default; builder API is opt-in via `GptHeader.build(...)`.

**Key Tasks.**
- [ ] Author `uefigpt.py` and `uefimbr.py`.
- [ ] Tests using captured GPT images from real disks (small `.img` fixtures).

## 5-esp-file-ops

**Description.** File operations on the EFI System Partition mirroring the
`SshAgent` file API: `file_push`, `file_pull`, `file_exists`,
`directory_exists`, `directory`, `directory_tree`.  Supports all three
transports (Shell via `cp`/`ls`, SSH-to-host via the OS-mounted ESP, Redfish
via VirtualMedia upload).

**Spec references.** §13.3–§13.5.

**Key Requirements.**
- The API surface shall be identical across transports — callers do not switch
  code paths by transport.
- Paths shall be normalized to the UEFI convention (`\` separators, `FS0:`
  volume aliases) in the Shell transport and translated to POSIX paths in the
  SSH-to-host transport.
- A push of a file larger than 16 MiB shall stream chunks rather than loading
  the whole file.

**Key Design Points.**
- The `EspFileProvider` facade hides transport differences.
- Chunked push uses a caller-overridable `chunk_size` (default 64 KiB).

**Key Tasks.**
- [ ] Author `uefiespfiles.py` + transport-specific backends.
- [ ] Large-file streaming test (generated random file).
- [ ] Path-normalization tests.

## 5-system-tables

**Description.** Readout of EFI System Table–adjacent tables that are useful
post-boot: ESRT (§23.4), SMBIOS, ACPI RSDP/XSDT/MADT, and
`EFI_MEMORY_ATTRIBUTES_TABLE`.

**Spec references.** §4.6, §23.4, §4.6.4.

**Key Requirements.**
- Each table parser shall expose a `from_bytes(buf)` classmethod and an
  iterator over child entries.
- ESRT parser shall cross-reference entries against the Firmware Management
  Protocol image-info reported via Redfish or OS where available.

**Key Design Points.**
- Data comes from `/sys/firmware/efi/`, Shell `memmap` / `smbiosview`, or
  Redfish Bios/Chassis resources — selected by transport at runtime.

**Key Tasks.**
- [ ] Author `uefisystemtables.py`.
- [ ] Fixtures from a Linux host under `/sys/firmware/efi/esrt/`.

## 5-firmware-capsules

**Description.** Build, deliver, and verify UEFI firmware-update capsules.
Supports `UpdateCapsule` runtime-service delivery via the OS
(`/dev/efi_capsule_loader`, `fwupdmgr` fallback), mass-storage delivery per
§8.5.5, and Redfish `UpdateService.SimpleUpdate` / multipart upload.

**Spec references.** §8.5.3, §8.5.5, §23.

**Key Requirements.**
- `CapsuleBuilder` shall produce capsules conforming to
  `EFI_CAPSULE_HEADER` with optional FMP payload wrapping per §23.3.
- Delivery shall poll `EFI_CAPSULE_REPORT_GUID` variables after the next boot
  to fetch the outcome per §8.5.6.
- Redfish delivery shall resolve Task completion per the measured-interop
  timeout contract.

**Key Design Points.**
- Separate `CapsuleBuilder`, `CapsuleDelivery`, and `CapsuleReport` classes
  so unit tests can cover building independently from delivery.
- Payload signing is delegated to callers — this feature does not manage
  private keys.

**Key Tasks.**
- [ ] Author `uefacapsule.py` (and subsidiary modules).
- [ ] Known-good capsule test vector from the EDK2 test suite.
- [ ] Redfish multipart-upload test against the fake Redfish server.

## 5-secure-boot

**Description.** Secure Boot state inspection and key enrollment for PK, KEK,
db, dbx, dbt, dbr.  Authenticated-variable write flow using
`EFI_VARIABLE_AUTHENTICATION_2` with time-based signing.

**Spec references.** §32.

**Key Requirements.**
- Enumerate each signature database and surface signatures as typed objects
  with `owner`, `signature_type`, `payload`.
- Inspect `SetupMode`, `AuditMode`, `DeployedMode`, `SecureBoot` variables and
  expose them as a single `SecureBootState` dataclass.
- Enrollment shall refuse to operate when `SetupMode == 0` unless the caller
  passes `force=True` with a structured acknowledgement dict
  `{"operator": str, "ticket": str}` captured in logs.

**Key Design Points.**
- Signing is delegated to `cryptography.hazmat` with pluggable signer
  callables — the library never touches private key material on disk by
  default.
- `dbx` append-only semantics enforced via append-write attribute.

**Key Tasks.**
- [ ] Author `uefisecureboot.py`.
- [ ] Tests using test PK/KEK fixtures generated in a `conftest.py` fixture.

## 5-loaded-image

**Description.** Parsers for UEFI-style PE/COFF images (`.efi` executables) —
header inspection, section enumeration, signature extraction (Authenticode),
and device-path-of-loader read from loaded image protocol metadata when
available.

**Spec references.** §9.1, §2.1.

**Key Requirements.**
- Parse PE32+ (`PE\0\0` → `IMAGE_FILE_MACHINE_X64`,
  `IMAGE_FILE_MACHINE_AARCH64`, `IMAGE_FILE_MACHINE_RISCV64`) and classic
  PE32 images.
- Extract the `WIN_CERTIFICATE` attribute-certificate directory and expose
  embedded Authenticode signatures as DER-encoded PKCS#7.
- Surface the subsystem field (10 = UEFI application, 11 = boot-service
  driver, 12 = runtime driver) as a named enum.

**Key Design Points.**
- Read-only parser; no writer / re-signer in v1.
- No dependency on `pefile` — implement directly to avoid the transitive
  install footprint.

**Key Tasks.**
- [ ] Author `uefiloadedimage.py`.
- [ ] Fixtures: `shim.efi`, `grubx64.efi`, a rust-built UEFI app.

## 5-measured-boot

**Description.** TPM 2.0 event log readout and PCR readback.  Parses the
TCG Event Log 2.0 format and correlates events to PCR banks.

**Spec references.** UEFI §38 (TCG platform interface), TCG EFI Protocol
Specification (referenced externally).

**Key Requirements.**
- Parse the TCG_PCR_EVENT2 format and expose a generator of `PcrEvent`
  records.
- Validate the measured log against the current PCR values and raise
  `UefiError` with a diff in `details` when reconstruction fails.

**Key Design Points.**
- Event log source: `/sys/kernel/security/tpm0/binary_bios_measurements` via
  SSH-to-host; TPM2 reads via `tpm2-tools`.

**Key Tasks.**
- [ ] Author `uefimeasuredboot.py`.
- [ ] Log-reconstruction tests using captured event logs.

## 5-network-boot

**Description.** Build and install `Boot####` entries for HTTP and PXE boot,
inject IPv4/IPv6 URIs, and trigger one-shot network boot via `BootNext`.

**Spec references.** §24.3 (PXE), §24.7 (HTTP Boot).

**Key Requirements.**
- `build_http_boot_option(uri, description, ...)` shall produce an
  `EFI_LOAD_OPTION` with a URI device-path node and install it via
  `5-boot-manager`.
- PXE variants shall accept either IPv4 or IPv6 and set the correct device
  path node type.

**Key Design Points.**
- Delegates to `5-device-path-codec` for URI node construction and to
  `5-boot-manager` for variable installation.

**Key Tasks.**
- [ ] Author `uefinetboot.py`.
- [ ] Round-trip test: build → encode → decode → render → compare.

## 5-system-reset

**Description.** Reset / power control with reconnect-verify semantics.
Supports cold reset, warm reset, shutdown, and platform-specific reset
subtypes via the `ResetSystem` runtime service or Redfish
`ComputerSystem.Reset`.

**Spec references.** §8.5.1.

**Key Requirements.**
- `reset(kind: ResetKind, aspects)` shall default to `aspects.completion_timeout
  = 300s` and shall verify connectivity per-aspects before returning.
- A `ResetFailedError` shall be raised when the target does not come back
  within the timeout.

**Key Design Points.**
- Modeled on `SshAgent.reboot()`.
- Reset reason and platform-specific data exposed as optional parameters.

**Key Tasks.**
- [ ] Author `uefireset.py`.
- [ ] Simulated reset test using fake transport that accepts a reset command
      and reconnects after a configurable delay.

## 5-hii-forms

**Description.** Optional Setup-menu navigation for scripted BIOS configuration.
Drives the firmware Setup UI over serial by recognizing form structure
rendered to the console.

**Spec references.** §33–§36.

**Key Requirements.**
- Provide a `HiiFormNavigator` that can match menu items by label, navigate
  via arrow-key sequences, toggle booleans, and set enumerated and numeric
  options.
- Failure to locate a labeled item shall raise
  `UefiError` with the captured screen snapshot in `details`.

**Key Design Points.**
- Decoupled from the HII binary format — this feature screen-scrapes the
  console, it does not interpret HII binary packages.
- Recognized vendor variants are dispatched via a registry of
  `ScreenShape` detectors.

**Key Tasks.**
- [ ] Author `uefihii.py`.
- [ ] Fixtures captured from AMI, Insyde, Phoenix, and a tianocore-based VM.

## 5-time-services

**Description.** `GetTime` / `SetTime` wrappers for the UEFI RTC, primarily
useful for RTC-drift testing.

**Spec references.** §8.3.

**Key Requirements.**
- `get_time()` shall return a timezone-aware `datetime`.
- `set_time(dt)` shall reject naive `datetime` with `ConfigurationError`.

**Key Design Points.**
- Transport-dependent: Shell `time` command, Redfish DateTime attribute,
  OS-side `hwclock --show` / `hwclock --set`.

**Key Tasks.**
- [ ] Author `uefitime.py`.
- [ ] Tests for tz-aware / tz-naive handling.

---

# Layer 6 — Composite Workflows

Layer-6 features compose multiple layer-5 capabilities into higher-level
operations suited to test authors.  Each takes an `aspects` parameter and is
retry/timeout aware in the same way layer-5 features are.

## 6-reboot-and-verify

**Description.** "Reset the target, wait for it back, verify Shell."  Wraps
`5-system-reset` + `4-agent.verify_connectivity` + state-carry forward so
tests can rely on a single call.

**Key Requirements.**
- Shall expose a `wait_for_shell: bool = True` flag; when `False`, the call
  returns as soon as the target ICMP-responds.
- Shall capture any `dmesg`-like console noise during the reset and attach it
  to the result for triage.

**Key Tasks.**
- [ ] Author `uefirebootworkflow.py`.
- [ ] Integration test against a QEMU/OVMF VM.

## 6-capsule-update-workflow

**Description.** "Deliver capsule, reboot, read back capsule report, verify
ESRT entry advanced."  Combines `5-firmware-capsules`,
`6-reboot-and-verify`, and `5-system-tables`.

**Key Requirements.**
- Shall fail fast if `ESRT` is not present on the target.
- Shall emit a structured `CapsuleUpdateReport` containing input capsule SHA,
  reported status GUID, ESRT-before and ESRT-after snapshots.

**Key Tasks.**
- [ ] Author `capsuleupdateworkflow.py`.
- [ ] Integration test using a test-signed capsule against OVMF.

## 6-secure-boot-enrollment

**Description.** "Put target into SetupMode, enroll PK/KEK/db/dbx in the
correct order, verify each write took effect, exit SetupMode."

**Key Requirements.**
- Shall refuse to operate unless `aspects.must_connect=True` (enrollment is
  not safe to attempt partially).
- Shall perform a dry-run mode that reports what would be written without
  writing.

**Key Tasks.**
- [ ] Author `secureboothenrollment.py`.
- [ ] Test against an OVMF VM with prebuilt test certificates.

## 6-esp-content-sync

**Description.** "Mirror a local directory tree into `/EFI/<vendor>/` on the
ESP."  Combines `5-esp-file-ops` with `rsync`-style delta semantics — push
only changed files, optionally delete files missing locally.

**Key Requirements.**
- Shall checksum remote files before push and skip on match.
- Shall support an `--include` / `--exclude` glob list.

**Key Tasks.**
- [ ] Author `espcontentsync.py`.
- [ ] Tests for: noop-on-match, include/exclude, delete mode.

## 6-boot-to-setup

**Description.** "Reboot directly into firmware Setup UI."  Sets
`OsIndications` with `EFI_OS_INDICATIONS_BOOT_TO_FW_UI`, reboots, then exposes
the HII navigator from `5-hii-forms`.

**Spec references.** §8.2 (OsIndications), §8.5.1 (ResetSystem).

**Key Requirements.**
- Shall verify the firmware supports `BOOT_TO_FW_UI` by reading
  `OsIndicationsSupported` first.
- On exit, shall set `OsIndications` back to its prior value atomically.

**Key Tasks.**
- [ ] Author `boottosetupworkflow.py`.
- [ ] OVMF-backed integration test.

## 6-boot-to-option-once

**Description.** "Boot a specified option exactly once without changing
`BootOrder`."  Writes `BootNext` for the target option, reboots, and verifies
`BootCurrent` on next boot matches.

**Key Requirements.**
- Shall raise `UefiVariableError` if `BootNext` is not automatically cleared
  by firmware after the next boot (per spec) and the option is observed to
  persist.

**Key Tasks.**
- [ ] Author `boottoopceonce.py`.
- [ ] OVMF test with a known Shell boot option.

---

# Authoring Order

The following order is the recommended sequence for expanding this plan into
fully-authored per-feature spec folders and then into code.  Earlier items
unblock later items.

1. All of layer 1.
2. `2-transport-serial`, then `2-transport-ssh-host`.
3. `3-client`, `3-connection`, `3-channel`.
4. `4-base`, `4-agent`, `4-session`.
5. `5-shell-commands` (proves the stack end-to-end).
6. `5-variable-services`, then `5-boot-manager`, then `5-device-path-codec`.
7. `5-gpt-mbr-parser`, `5-esp-file-ops`, `5-system-tables`.
8. `5-system-reset`, then `6-reboot-and-verify`.
9. `5-firmware-capsules`, then `6-capsule-update-workflow`.
10. `5-secure-boot`, then `6-secure-boot-enrollment`.
11. Remaining layer-5 features (`5-loaded-image`, `5-measured-boot`,
    `5-network-boot`, `5-hii-forms`, `5-time-services`).
12. Remaining layer-6 workflows (`6-esp-content-sync`, `6-boot-to-setup`,
    `6-boot-to-option-once`).
13. `2-transport-redfish`, `2-transport-ipmi-sol` (defer unless an early
    user has an OOB-management requirement).

Each numbered item above corresponds to creating the three Kiro spec files
(`requirements.md`, `design.md`, `tasks.md`) under its `specs/{layer}-{feature-name}/`
folder and then implementing against those specs.
