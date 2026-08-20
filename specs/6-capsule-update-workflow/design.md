# Design — Capsule Update Workflow

## Overview

The Capsule Update Workflow is a composite layer-6 feature that chains a
capsule delivery (`5-firmware-capsules`) through a reboot-and-verify
(`6-reboot-and-verify`) and an ESRT diff (`5-system-tables`) into a single
call returning a `CapsuleUpdateReport`.  The workflow's job is to sequence
these features correctly, gate them on preflight checks, and produce a
structured result test authors can attach to their test-run artifacts.

The workflow enforces strict preconditions (ESRT present,
`OsIndicationsSupported` reports capsule capability, capsule header valid,
sufficient NV storage) before any destructive write, honoring the safety
philosophy that destructive operations fail early, fail loud, and leave the
target in a known state when they do fail.  A dry-run mode exists so tests
can validate their scaffolding without flashing firmware.

## Architecture

```
ccai.protocols.uefi.workflows.capsuleupdate/
    __init__.py                 # re-exports
    capsuleupdateworkflow.py    # CapsuleUpdateWorkflow + convenience fn
    capsuleupdatereport.py      # CapsuleUpdateReport dataclass
    safetygates.py              # preflight checks
    esrtdiff.py                 # ESRT before/after diff logic
```

## Components and Interfaces

### `CapsuleUpdateWorkflow`

```python
class CapsuleUpdateWorkflow:
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
        capsule: Union[bytes, os.PathLike[str]],
        delivery_mode: CapsuleDeliveryMode = CapsuleDeliveryMode.RUNTIME_SERVICE,
        require_esrt: bool = True,
        aspects: Optional[UefiCmdAspects] = None,
    ) -> "CapsuleUpdateReport":
        ...
```

Execution order:

1. Resolve aspects.
2. Load and hash capsule bytes.
3. `SafetyGates.run(agent, capsule_bytes, delivery_mode, require_esrt)`.
4. Snapshot ESRT via `5-system-tables`.
5. If `aspects.dry_run` — build partial report and return.
6. Deliver capsule via `5-firmware-capsules` with `delivery_mode`.
7. Invoke `6-reboot-and-verify` with `wait_for_shell=True`.
8. Read capsule report variables per UEFI spec §8.5.6.
9. Snapshot ESRT post-reboot.
10. Diff ESRT via `esrtdiff.advanced(before, after, firmware_class_guid=…)`.
11. Build and return `CapsuleUpdateReport`.

### `CapsuleUpdateReport`

```python
@dataclass(frozen=True)
class CapsuleUpdateReport:
    succeeded: bool
    input_capsule_sha256: str
    delivery_mode: CapsuleDeliveryMode
    reported_status_guid: Optional[uuid.UUID]
    esrt_before: Optional["EsrtSnapshot"]
    esrt_after: Optional["EsrtSnapshot"]
    esrt_advanced: bool
    reboot_report: Optional["RebootAndVerifyReport"]
    dry_run: bool
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
        capsule_bytes: bytes,
        delivery_mode: CapsuleDeliveryMode,
        require_esrt: bool,
        aspects: UefiCmdAspects,
    ) -> None:
        # 1. Validate capsule header per UEFI spec §23.3.
        # 2. Check OsIndicationsSupported reports capsule capability.
        # 3. If require_esrt, ensure ESRT is non-empty.
        # 4. QueryVariableInfo free-space >= capsule size for
        #    non-runtime-service delivery.
        # 5. Verify delivery_mode is supported by active transport.
        return
```

Each gate raises `UefiCapsuleError` or `ConfigurationError` with actionable
`details`.  Gates run in order; the first failure short-circuits.

### `esrtdiff.advanced`

```python
def advanced(
    before: EsrtSnapshot,
    after: EsrtSnapshot,
    firmware_class_guid: Optional[uuid.UUID] = None,
) -> bool:
    ...
```

Returns `True` if any entry (optionally filtered by `firmware_class_guid`)
has a strictly higher `firmware_version` post-reboot.

### `capsule_update_workflow` convenience function

```python
def capsule_update_workflow(
    agent: "UefiAgent",
    capsule: Union[bytes, os.PathLike[str]],
    delivery_mode: CapsuleDeliveryMode = CapsuleDeliveryMode.RUNTIME_SERVICE,
    require_esrt: bool = True,
    aspects: Optional[UefiCmdAspects] = None,
) -> CapsuleUpdateReport:
    workflow = CapsuleUpdateWorkflow(agent=agent)
    rtnval = workflow.run(
        capsule=capsule,
        delivery_mode=delivery_mode,
        require_esrt=require_esrt,
        aspects=aspects,
    )
    return rtnval
```

## Data Models

* `CapsuleUpdateReport` — described above.
* `CapsuleDeliveryMode` — re-exported from `5-firmware-capsules`.
* `EsrtSnapshot` — re-exported from `5-system-tables`.

## Measured Interop Contract

The workflow delegates aspects unchanged to its constituent layer-5 /
layer-6 features.  Fields consumed:

* `aspects.action_pattern` — consumed locally.  Supported:
  `SINGLE_CALL`, `DO_UNTIL_SUCCESS`.  Others raise `SemanticError`.
* `aspects.completion_timeout` / `aspects.completion_interval` —
  forwarded to capsule delivery, reboot-and-verify, and capsule-report
  polling.
* `aspects.connection_timeout` / `aspects.connection_interval` —
  forwarded to reboot-and-verify.
* `aspects.allowed_error_codes` / `aspects.allowed_connection_exceptions`
  — forwarded.
* `aspects.logging_pattern` — consumed locally for composite-outcome
  logging and forwarded.
* `aspects.must_connect` — forwarded to reboot-and-verify; the workflow
  additionally enforces that all preflight gates run before any
  destructive write.
* `aspects.must_advance` (workflow-specific) — if `True`, the workflow
  raises when `esrt_advanced is False`.
* `aspects.dry_run` (workflow-specific) — enables dry-run mode.

The workflow enforces invariants of its own: ESRT must be present unless
`require_esrt=False`, `OsIndicationsSupported` must report capsule
capability, and the capsule header must be valid — regardless of what the
aspects say.  These invariants cannot be waived by aspects.

## Safety Gates

Preflight gates run in this order before any destructive write.  Every gate
that fails raises before `UpdateCapsule` is invoked.

1. **Capsule header validity.** Parse `EFI_CAPSULE_HEADER` per UEFI spec
   §23.3; verify magic, size fields, and (when present) FMP payload
   structure.  Raises `UefiCapsuleError` with `details=["header parse
   failed at offset N", ...]`.
2. **Firmware capability probe.** Read `OsIndicationsSupported` per
   UEFI spec §8.5.1.  Verify
   `EFI_OS_INDICATIONS_FMP_CAPSULE_SUPPORTED` when the capsule is an FMP
   capsule, and `EFI_OS_INDICATIONS_FILE_CAPSULE_DELIVERY_SUPPORTED` when
   `delivery_mode == MASS_STORAGE`.
3. **ESRT presence.** Read ESRT per UEFI spec §23.4.  If absent and
   `require_esrt=True`, raise `UefiCapsuleError`.
4. **NV storage sufficiency.** For non-runtime-service delivery call
   `QueryVariableInfo` and compare
   `MaximumVariableStorageSize - RemainingVariableStorageSize` against the
   capsule size; raise `UefiCapsuleError` when insufficient.
5. **Delivery-mode / transport compatibility.** Verify the chosen
   `delivery_mode` is supported by the active transport; raise
   `ConfigurationError` otherwise.
6. **Dry-run short-circuit.** When `aspects.dry_run=True` the gates run
   but the delivery and reboot are skipped.

Safety gates are idempotent and have no side effects beyond reading
variables and tables.

## Error Handling

| Condition                                    | Exception              | Included in `details`                            |
|----------------------------------------------|------------------------|--------------------------------------------------|
| ESRT absent and `require_esrt=True`          | `UefiCapsuleError`     | "ESRT precondition failed"                       |
| Capsule header malformed                     | `UefiCapsuleError`     | offset, observed magic vs. expected              |
| OsIndicationsSupported lacks capability      | `UefiCapsuleError`     | missing capability bit, observed bitmask         |
| Insufficient NV storage                      | `UefiCapsuleError`     | capsule size, free bytes                         |
| Delivery mode unsupported                    | `ConfigurationError`   | transport kind, requested mode                   |
| No matching Capsule Report variable          | `UefiCapsuleError`     | enumerated variables, expected SHA               |
| `esrt_advanced is False` and `must_advance`  | `UefiCapsuleError`     | before/after entry deltas                        |
| `action_pattern` unsupported                 | `SemanticError`        | offending pattern                                |
| Input neither bytes nor path                 | `ConfigurationError`   | received type                                    |
| `aspects` and agent default both `None`      | `ConfigurationError`   | agent repr                                       |

## Logging

Channel: `uefi.workflow.capsule`.

* At `INFO`: workflow start with capsule SHA-256, delivery mode, aspects
  summary.
* At `INFO`: each safety gate pass/fail.
* At `INFO`: capsule delivered, reboot issued, capsule report observed.
* At `DEBUG`: ESRT before and after full dumps.
* At `ERROR`: structured dict of partial `CapsuleUpdateReport` on failure.

## Security Considerations

The workflow is destructive — it flashes firmware.  Signed-capsule
verification is the firmware's responsibility; this workflow does not
validate signatures, but it does hash the input so that a run can be tied
to a specific binary.  Callers who run against production targets must
ensure the capsule they pass is the capsule they intend — the hash in the
report is the audit trail.

Dry-run mode exercises every read path and no write path; it is safe to
run against production targets for staging validation.

## Testing Strategy

### Unit tests

* Safety-gate ordering — mock each gate and verify earlier gates
  short-circuit later gates.
* ESRT-advancement diff — parametrized by before/after entries.
* Capsule identity hashing — SHA-256 matches golden vectors.
* Aspects delegation — forwarded unchanged.
* Dry-run mode — gates run, delivery and reboot not invoked.

### Integration tests

* OVMF with test-signed capsule (gated by `CCAI_OVMF_TEST=1` and
  `CCAI_CAPSULE_FIXTURE`).
* OVMF without ESRT — assert `UefiCapsuleError` at preflight.
* OVMF with malformed capsule — assert `UefiCapsuleError` at preflight.

### Action-pattern tests

* `SINGLE_CALL` — one attempt.
* `DO_UNTIL_SUCCESS` — simulated transient delivery failure retried.
* Three rejected patterns — `SemanticError`.

## Composition Diagram

```
caller
  │
  ▼
capsule_update_workflow(agent, capsule, ...)
  │
  │  1. resolve_aspects(...)
  │  2. hash capsule (SHA-256)
  │
  ├──▶ SafetyGates.run(...)
  │        │
  │        ├──▶ 5-variable-services: read OsIndicationsSupported
  │        ├──▶ 5-system-tables: read ESRT (precondition)
  │        └──▶ 5-variable-services: QueryVariableInfo
  │
  ├──▶ 5-system-tables: snapshot ESRT (before)
  │
  ├──▶ 5-firmware-capsules: deliver(capsule, delivery_mode, aspects)
  │        │
  │        ├─[RUNTIME_SERVICE]─▶ UpdateCapsule runtime
  │        ├─[MASS_STORAGE]   ─▶ stage on ESP
  │        └─[REDFISH]        ─▶ UpdateService multipart upload
  │
  ├──▶ 6-reboot-and-verify(wait_for_shell=True, aspects)
  │        │
  │        └──▶ 5-system-reset + verify_connectivity
  │
  ├──▶ 5-variable-services: enumerate Capsule Report variables (§8.5.6)
  │
  ├──▶ 5-system-tables: snapshot ESRT (after)
  │
  ├──▶ esrtdiff.advanced(before, after)
  ▼
CapsuleUpdateReport
```

## Dependencies on Other Features

* `1-aspects` — aspects, action patterns, resolver.
* `1-exceptions` — `UefiCapsuleError`, `ConfigurationError`,
  `SemanticError`.
* `1-logging` — channel registration.
* `1-constants-and-types` — `EFI_CAPSULE_REPORT_GUID`,
  `EFI_OS_INDICATIONS_*` bit constants.
* `4-agent` — transport and session context.
* `5-variable-services` — variable reads for capability probe and
  Capsule Report.
* `5-system-tables` — ESRT snapshots.
* `5-firmware-capsules` — capsule delivery.
* `6-reboot-and-verify` — reboot step with shell verification.
