# Design — Boot To Option Once Workflow

## Overview

The Boot To Option Once workflow boots a specified UEFI boot option
exactly once without modifying `BootOrder` by writing `BootNext` for the
target option, resetting the target, and verifying on the next boot that
`BootCurrent` matches.  It also verifies the firmware cleared `BootNext`
as required by UEFI §3.1.5; a firmware that leaves `BootNext` set is
declared noncompliant and raises `UefiVariableError`.

The workflow composes `5-variable-services` (for the actual `BootNext`
read and write), `5-boot-manager` (for `Boot####` existence validation
and decode), and `6-reboot-and-verify` (for the reboot-and-back step).
It produces a structured `BootToOptionOnceReport` with before/after
observations of all four boot-related variables.

## Architecture

```
ccai.protocols.uefi.workflows.boottooptiononce/
    __init__.py              # re-exports
    boottooptiononce.py      # workflow class + convenience fn
    boottooptiononcereport.py# report dataclass
    cleanup.py               # failure-path BootNext cleanup helper
```

## Components and Interfaces

### `BootToOptionOnceWorkflow`

```python
class BootToOptionOnceWorkflow:
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
        option_id: int,
        aspects: Optional[UefiCmdAspects] = None,
    ) -> "BootToOptionOnceReport":
        ...
```

Execution order:

1. Resolve aspects.
2. Validate `option_id` is `0x0000..0xFFFF`.
3. Read `Boot{option_id:04X}` via `5-boot-manager.get_boot_option(...)`;
   raise `UefiVariableError` if absent.
4. Read `BootOrder` and record as `boot_order_before`.
5. Read existing `BootNext` if any and record as `boot_next_before`.
6. Write `BootNext = option_id` as `UINT16` little-endian with attrs
   `NV | BS | RT`.
7. Invoke `6-reboot-and-verify` with `wait_for_shell=True`.
8. On post-reboot shell: read `BootCurrent`, `BootNext`, `BootOrder`.
9. Validate:
   * `BootCurrent == option_id`.
   * `BootOrder` byte-equal to `boot_order_before`.
   * `BootNext` absent (or its value differs and workflow raises).
10. Build and return report.
11. On failure between step 6 and step 7, invoke `cleanup.delete_boot_next`.

### `BootToOptionOnceReport`

```python
@dataclass(frozen=True)
class BootToOptionOnceReport:
    succeeded: bool
    option_id: int
    boot_order_before: bytes
    boot_next_before: Optional[int]
    boot_current_after: Optional[int]
    boot_next_after: Optional[int]
    boot_next_cleared: bool
    reboot_report: Optional["RebootAndVerifyReport"]
    started_at: datetime

    def as_dict(self) -> dict[str, object]:
        ...
```

### `cleanup.delete_boot_next`

```python
def delete_boot_next(
    agent: "UefiAgent",
    aspects: UefiCmdAspects,
) -> Optional[str]:
    # Best-effort delete of BootNext; returns None on success or a
    # string describing the failure.  Never raises.
    ...
```

### `boot_to_option_once` convenience function

```python
def boot_to_option_once(
    agent: "UefiAgent",
    option_id: int,
    aspects: Optional[UefiCmdAspects] = None,
) -> BootToOptionOnceReport:
    workflow = BootToOptionOnceWorkflow(agent=agent)
    rtnval = workflow.run(option_id=option_id, aspects=aspects)
    return rtnval
```

## Data Models

* `BootToOptionOnceReport` — described above.
* `EFI_LOAD_OPTION` — re-exported from `5-boot-manager`.
* `RebootAndVerifyReport` — re-exported from `6-reboot-and-verify`.

## Measured Interop Contract

The workflow delegates aspects unchanged to its constituent features:

* `aspects.action_pattern` — consumed locally.  Supported: `SINGLE_CALL`,
  `DO_UNTIL_SUCCESS`.  Others raise `SemanticError`.
* `aspects.completion_timeout` / `aspects.completion_interval` —
  forwarded to variable ops and to `6-reboot-and-verify`.
* `aspects.connection_timeout` / `aspects.connection_interval` —
  forwarded to the reboot step.
* `aspects.allowed_error_codes` / `aspects.allowed_connection_exceptions`
  — forwarded.
* `aspects.logging_pattern` — consumed and forwarded.
* `aspects.must_connect` — forwarded; enforced during the reboot step.
* `aspects.skip_decode` (workflow-specific) — permits skipping
  `EFI_LOAD_OPTION` decode when the caller only cares about presence.

The workflow enforces one invariant of its own: `BootOrder` must be
byte-identical before and after the workflow.  This invariant cannot be
waived by aspects.

## Error Handling

| Condition                                          | Exception               | Included in `details`                              |
|----------------------------------------------------|-------------------------|----------------------------------------------------|
| `option_id` out of range                           | `ConfigurationError`    | received value                                     |
| `Boot####` variable absent                         | `UefiVariableError`     | `Boot####` name                                    |
| `EFI_LOAD_OPTION` decode failed                    | `UefiVariableError`     | decode error                                       |
| `BootNext` write failed                            | `UefiVariableError`     | name, vendor_guid, efi_status                      |
| `BootCurrent != option_id` post-reset              | `UefiVariableError`     | option_id, observed BootCurrent                    |
| `BootOrder` mutated                                | `UefiVariableError`     | before and after hex                               |
| `BootNext` not cleared (UEFI §3.1.5 violation)     | `UefiVariableError`     | observed value, spec citation                      |
| `action_pattern` unsupported                       | `SemanticError`         | offending pattern                                  |
| `aspects` and agent default both `None`            | `ConfigurationError`    | agent repr                                         |
| Cleanup-delete failed post-failure                 | appended to primary err | primary exception + cleanup error                  |

## Logging

Channel: `uefi.workflow.bootnext`.

* At `INFO`: workflow start with `option_id` in hex, aspects summary.
* At `INFO`: existence-check pass/fail, `BootNext` write, reboot,
  `BootCurrent` read, `BootOrder` invariant check, `BootNext` cleared
  check.
* At `DEBUG`: `BootOrder` before and after as hex.
* At `INFO`: cleanup-delete outcome on failure path.
* At `ERROR`: structured dict of partial report on failure.

## Security Considerations

* `BootNext` is a persistent, boot-relevant variable.  A failure that
  leaves `BootNext` set can redirect the next normal boot.  The cleanup
  step on failure mitigates this but cannot guarantee success if the
  transport is down.
* The workflow does not require elevated privileges beyond what
  `5-variable-services` requires.

## Testing Strategy

### Unit tests

* Option-existence check — missing variable raises `UefiVariableError`.
* Decode failure raises `UefiVariableError`; `skip_decode=True` bypasses
  the decode path.
* `BootOrder` invariant check — mutated BootOrder raises.
* `BootNext` auto-clear — absent → `boot_next_cleared=True`; present
  with matching value → raise UEFI §3.1.5 violation; present with
  differing value → raise with observed value.
* Cleanup path — failure after BootNext write triggers best-effort
  delete; cleanup failures appended to primary exception.
* Aspects forwarding.

### Integration tests

* OVMF with a known UEFI Shell `Boot####` entry — full round-trip.
* OVMF with an invalid `option_id` — existence failure path.
* OVMF with a simulated noncompliant firmware that leaves `BootNext`
  set — §3.1.5-violation path (can be simulated by clearing and
  re-setting BootNext in a test hook).
* Gated by `CCAI_OVMF_TEST=1`.

### Action-pattern tests

* `SINGLE_CALL` — one attempt.
* `DO_UNTIL_SUCCESS` — retry on simulated transient failure.
* Three rejected patterns raise `SemanticError`.

## Composition Diagram

```
caller
  │
  ▼
boot_to_option_once(agent, option_id, aspects)
  │
  │  1. resolve_aspects(...)
  │  2. validate option_id in 0x0000..0xFFFF
  │
  ├──▶ 5-boot-manager: get_boot_option(option_id)  [§3.1.3]
  │        │  uses 5-variable-services under the hood
  │
  ├──▶ 5-variable-services: read BootOrder (record before)  [§3.3]
  ├──▶ 5-variable-services: read BootNext  (record before)
  │
  ├──▶ 5-variable-services: SetVariable BootNext = option_id
  │        attrs = NV | BS | RT                       [§3.3]
  │
  ├──▶ 6-reboot-and-verify(wait_for_shell=True)
  │        │
  │        └──▶ 5-system-reset + verify_connectivity  [§8.5.1]
  │
  ├──▶ 5-variable-services: read BootCurrent   [§3.3]
  ├──▶ 5-variable-services: read BootNext      (must be absent §3.1.5)
  ├──▶ 5-variable-services: read BootOrder     (must be unchanged)
  │
  │  (on failure before reboot: cleanup.delete_boot_next)
  ▼
BootToOptionOnceReport
```

## Dependencies on Other Features

* `1-aspects` — aspects, action patterns, resolver.
* `1-exceptions` — `UefiVariableError`, `ConfigurationError`,
  `SemanticError`.
* `1-logging` — channel registration.
* `1-constants-and-types` — `EFI_GLOBAL_VARIABLE_GUID`, `Boot####` /
  `BootNext` / `BootCurrent` / `BootOrder` variable name constants.
* `4-agent` — session and transport.
* `5-variable-services` — all variable reads and writes.
* `5-boot-manager` — `Boot####` existence and decode, `EFI_LOAD_OPTION`.
* `6-reboot-and-verify` — the reboot step.
