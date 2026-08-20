# Design — Secure Boot Enrollment Workflow

## Overview

The Secure Boot Enrollment workflow drives the target from SetupMode to
User Mode with Secure Boot enabled by enrolling the Platform Key (PK), Key
Exchange Keys (KEK), signature database (db), and forbidden signature
database (dbx) in the order required by the UEFI specification, verifying
each write, and exiting SetupMode.  It is the highest-risk operation in the
library and therefore enforces stronger invariants than any other layer-6
workflow: `must_connect=True` is required; `DO_UNTIL_SUCCESS` and other
"retry blindly" patterns are refused; every write is verified before the
next begins; and a dry-run mode exists for staging validation.

The workflow composes `5-secure-boot` (for authenticated-variable assembly
and signature parsing) and `5-variable-services` (for the actual reads and
writes) into a single sequenced call, and produces a detailed
`SecureBootEnrollmentReport` suitable for compliance audit trails.

## Architecture

```
ccai.protocols.uefi.workflows.secureboot/
    __init__.py                       # re-exports
    secureboothenrollmentworkflow.py  # main workflow class + fn
    secureboothenrollmentreport.py    # report dataclass
    secureboothbundle.py              # SecureBootBundle + VariableWrite
    safetygates.py                    # preflight checks
    verifywrite.py                    # per-write re-read + compare logic
```

## Components and Interfaces

### `SecureBootEnrollmentWorkflow`

```python
class SecureBootEnrollmentWorkflow:
    def __init__(
        self,
        agent: "UefiAgent",
        default_aspects: Optional[UefiCmdAspects] = None,
    ) -> None:
        self._agent = agent
        self._default_aspects = default_aspects
        return

    def run(
        self,
        bundle: "SecureBootBundle",
        aspects: Optional[UefiCmdAspects] = None,
    ) -> "SecureBootEnrollmentReport":
        ...
```

Execution order:

1. Resolve aspects.
2. Enforce `aspects.must_connect is True` (raise `ConfigurationError`
   otherwise).
3. Validate bundle shape.
4. Run `SafetyGates.run(agent, bundle, aspects)`.
5. Read `SetupMode`, `AuditMode`, `DeployedMode`, `SecureBoot`.
6. If `aspects.dry_run=True` — build plan, return report.
7. For each write in order `(dbx, db, KEK, PK)`:
   a. Invoke `5-secure-boot.write_authenticated_variable(...)`.
   b. Invoke `verifywrite.verify(agent, variable, expected)`.
   c. On failure, raise `UefiSecureBootError`.
8. Re-read `SetupMode` and `SecureBoot`; verify transition.
9. Build and return `SecureBootEnrollmentReport`.

### `SecureBootBundle`

```python
@dataclass(frozen=True)
class SecureBootBundle:
    pk: Optional["BundleEntry"]
    kek: tuple["BundleEntry", ...]
    db: tuple["BundleEntry", ...]
    dbx: tuple["BundleEntry", ...]

@dataclass(frozen=True)
class BundleEntry:
    owner: uuid.UUID
    signature_type: uuid.UUID
    payload: bytes
    signer: Optional["SignerCallable"] = None
    certificate: Optional[bytes] = None
    prewrapped: Optional[bytes] = None   # EFI_VARIABLE_AUTHENTICATION_2
```

Either `prewrapped` is set, or the `(signer, certificate, payload)` triple
is used to assemble the descriptor through `5-secure-boot`.

### `VariableWrite`

```python
@dataclass(frozen=True)
class VariableWrite:
    name: str
    vendor_guid: uuid.UUID
    attributes: int
    payload_sha256: str
    entries: int
```

One instance per planned or verified write, embedded in the report.

### `SecureBootEnrollmentReport`

```python
@dataclass(frozen=True)
class SecureBootEnrollmentReport:
    succeeded: bool
    dry_run: bool
    writes_planned: tuple[VariableWrite, ...]
    writes_verified: tuple[VariableWrite, ...]
    setup_mode_before: int
    setup_mode_after: int
    secure_boot_before: int
    secure_boot_after: int
    deployed_mode_before: int
    started_at: datetime

    def as_dict(self) -> dict[str, object]:
        ...
```

### `SafetyGates`

```python
class SafetyGates:
    @staticmethod
    def run(
        agent: "UefiAgent",
        bundle: SecureBootBundle,
        aspects: UefiCmdAspects,
    ) -> None:
        # 1. Validate bundle not empty
        # 2. Validate PK is exactly one entry
        # 3. Read SecureBoot state; verify SetupMode == 1
        # 4. Reject DeployedMode == 1
        # 5. Verify every bundle entry wraps correctly (prewrapped OR
        #    signer+cert+payload)
        # 6. Verify signature algorithms are supported by the firmware
        #    (best-effort: read QueryVariableInfo and sbatSupported hints)
        return
```

### `verifywrite.verify`

```python
def verify(
    agent: "UefiAgent",
    variable_name: str,
    vendor_guid: uuid.UUID,
    expected_entries: Sequence[SignatureListEntry],
    aspects: UefiCmdAspects,
) -> None:
    # Re-reads the variable, parses the signature list via
    # 5-secure-boot, and asserts every expected entry is present.
    # Raises UefiSecureBootError on mismatch.
    return
```

### `secure_boot_enrollment` convenience function

```python
def secure_boot_enrollment(
    agent: "UefiAgent",
    bundle: SecureBootBundle,
    aspects: Optional[UefiCmdAspects] = None,
) -> SecureBootEnrollmentReport:
    workflow = SecureBootEnrollmentWorkflow(agent=agent)
    rtnval = workflow.run(bundle=bundle, aspects=aspects)
    return rtnval
```

## Data Models

* `SecureBootBundle`, `BundleEntry` — described above.
* `VariableWrite` — described above.
* `SecureBootEnrollmentReport` — described above.
* `SignatureListEntry` — re-exported from `5-secure-boot`.

## Measured Interop Contract

The workflow delegates aspects unchanged to its constituent features, but
enforces these invariants regardless of what the caller passes:

* `aspects.must_connect` — **MUST** be `True`.  If not, the workflow
  raises `ConfigurationError` before any read.  This is a workflow-level
  invariant that cannot be relaxed.
* `aspects.action_pattern` — supported values: `SINGLE_CALL`,
  `SINGLE_CONNECTED_CALL`.  Others raise `SemanticError`.
* `aspects.dry_run` — consumed locally.
* `aspects.allow_user_mode_exit` (workflow-specific) — permits attempting
  to transition User Mode → SetupMode by deleting PK with the current PK's
  authenticated-write key.  Default `False`.

Forwarded unchanged:

* `aspects.completion_timeout`, `aspects.completion_interval`,
  `aspects.connection_timeout`, `aspects.connection_interval`,
  `aspects.inactivity_timeout`, `aspects.inactivity_interval`,
  `aspects.monitor_delay` — to every variable read and write.
* `aspects.logging_pattern` — to variable ops; also consumed locally for
  composite-outcome logging.
* `aspects.allowed_error_codes`, `aspects.allowed_connection_exceptions`
  — forwarded.

## Safety Gates

Gates run in this order before any destructive write.  Every gate raises
a specific exception on failure; later gates do not execute after an
earlier failure.

1. **must_connect invariant.** Verify `aspects.must_connect is True`.
   Raise `ConfigurationError` otherwise.  This runs first so a
   mis-configured aspects never triggers any variable read.
2. **Bundle validity.** Bundle is non-empty; PK is exactly one entry;
   every entry wraps correctly (prewrapped XOR signer+cert+payload); all
   GUIDs are valid.
3. **SetupMode precondition.** Read `SetupMode`; must be `1`.  If
   `DeployedMode == 1`, refuse unconditionally.
4. **Capability probe.** Read `SecureBootEnable` / `CustomMode` hints
   where firmware exposes them.  Warn (INFO log) rather than fail when
   non-standard modes are active.
5. **Signature algorithm support.** For each bundle entry's
   `signature_type`, verify the firmware has declared support via
   `QueryVariableInfo` hints or fall back to assuming standard
   `EFI_CERT_X509_GUID` / `EFI_CERT_SHA256_GUID` are supported.
6. **Dry-run short-circuit.** When `aspects.dry_run=True` gates run but
   no writes are attempted.

Gate results are recorded on the report's `writes_planned` list regardless
of execution path, so dry-run and real-run produce comparable plans.

## Error Handling

| Condition                                        | Exception              | Included in `details`                              |
|--------------------------------------------------|------------------------|----------------------------------------------------|
| `aspects.must_connect is not True`               | `ConfigurationError`   | message + "partial enrollment not safe"            |
| Bundle empty                                     | `ConfigurationError`   | "no PK, no KEK, no db, no dbx"                     |
| PK not exactly one entry                         | `ConfigurationError`   | observed entry count                                |
| Entry lacks both prewrapped and signer triple    | `ConfigurationError`   | entry index, observed fields                        |
| `SetupMode != 1` at start                        | `UefiSecureBootError`  | all four state variable values                      |
| `DeployedMode == 1`                              | `UefiSecureBootError`  | "DeployedMode exit not supported"                   |
| Re-read missing expected entry                   | `UefiSecureBootError`  | variable name, expected vs. observed                |
| `SetupMode` did not transition after PK enroll   | `UefiSecureBootError`  | final state                                         |
| `SecureBoot` is `0` after PK enroll              | `UefiSecureBootError`  | final state                                         |
| `action_pattern` unsupported                     | `SemanticError`        | offending pattern                                   |
| Connection failure mid-workflow                  | `ConnectionError`      | write position at failure                           |

## Logging

Channel: `uefi.workflow.secureboot`.

* At `INFO`: workflow start, dry-run flag, bundle entry counts (no key
  material).
* At `INFO`: each gate pass/fail.
* At `INFO`: each write initiated, verified, or failed.
* At `DEBUG`: payload SHA-256 digests and `EFI_TIME` used for time-based
  auth.
* At `ERROR`: structured dict of partial report on failure.
* **Never** log raw key material, signatures, or certificate bytes.

## Security Considerations

* The workflow receives key material (either prewrapped descriptors or
  signer callables with certificates).  It does not persist that material
  beyond the call; callers own the lifecycle of key files.
* The workflow logs SHA-256 digests of payloads, never raw bytes.
* `aspects.must_connect=True` is enforced to prevent partial enrollment
  caused by a silent connection retry.
* Dry-run mode reads firmware state but does not write; it is safe to run
  against production targets.
* `DeployedMode == 1` refusal is unconditional; exiting DeployedMode
  requires platform-specific mechanisms outside this library's scope.

## Safety Gates

See section above.

## Testing Strategy

### Unit tests

* `must_connect=False` raises `ConfigurationError` before any read.
* Bundle validation — empty, missing PK, multiple PKs, malformed entry.
* Gate ordering short-circuits.
* `DeployedMode == 1` unconditionally rejected.
* Dry-run mode — plan is built; no writes executed (mock the variable
  backend and assert zero write calls).
* Per-write verification — mismatches raise with actionable details.
* Action patterns — three rejected patterns raise `SemanticError`.
* Aspects forwarding — `must_connect=True` layered on forwarded aspects.

### Integration tests

* OVMF in SetupMode with test PK/KEK/db/dbx — full enrollment.
* OVMF already in User Mode — precondition failure.
* OVMF with dbx-only update (preserving PK/KEK/db) — append-write
  semantics.
* Gated by `CCAI_OVMF_TEST=1` and `CCAI_SECURE_BOOT_FIXTURES`.

### Fixture setup

* `conftest.py` generates test PK/KEK/db/dbx certificates via
  `cryptography.hazmat` once per test session.
* Certificates are persisted under a tmpdir and never committed.

## Composition Diagram

```
caller
  │
  ▼
secure_boot_enrollment(agent, bundle, aspects)
  │
  │  1. resolve_aspects(...)
  │  2. enforce aspects.must_connect is True
  │
  ├──▶ SafetyGates.run(...)
  │        ├──▶ 5-variable-services: read SetupMode/AuditMode/
  │        │    DeployedMode/SecureBoot (§32.3)
  │        └──▶ 5-secure-boot: validate bundle wraps
  │
  │  (if aspects.dry_run: build plan and return here)
  │
  ├──▶ for variable in (dbx, db, KEK, PK):
  │       │
  │       ├──▶ 5-secure-boot: assemble
  │       │    EFI_VARIABLE_AUTHENTICATION_2 descriptor (§8.2.5)
  │       ├──▶ 5-variable-services: SetVariable
  │       │    with APPEND_WRITE (dbx) or time-based auth
  │       └──▶ verifywrite.verify(...)
  │              │
  │              └──▶ 5-variable-services: GetVariable + 5-secure-boot
  │                   signature list parse
  │
  ├──▶ 5-variable-services: re-read SetupMode, SecureBoot
  ▼
SecureBootEnrollmentReport
```

## Dependencies on Other Features

* `1-aspects` — aspects, action patterns, resolver.
* `1-exceptions` — `UefiSecureBootError`, `ConfigurationError`,
  `SemanticError`, `ConnectionError`.
* `1-logging` — channel registration.
* `1-constants-and-types` — `EFI_IMAGE_SECURITY_DATABASE_GUID`,
  `EFI_GLOBAL_VARIABLE_GUID`, signature-type GUID constants.
* `1-credentials` — no direct use, but agent uses them upstream.
* `4-agent` — session and transport.
* `5-variable-services` — variable reads and writes.
* `5-secure-boot` — authenticated-variable assembly, signature list
  parsing, state inspection.
