# Design — Boot To Setup Workflow

## Overview

The Boot To Setup workflow drives the target directly into its firmware
Setup UI by setting the `EFI_OS_INDICATIONS_BOOT_TO_FW_UI` bit in the
`OsIndications` EFI global variable (§8.2), issuing a reset (§8.5.1 via
`6-reboot-and-verify`), and exposing a `HiiFormNavigator` from
`5-hii-forms` for programmatic interaction with the Setup screens.  The
workflow returns a context manager whose `__exit__` restores the original
`OsIndications` value and reboots back to the normal boot path — so a
test can scope its "I need to be in Setup" window with `with`.

The workflow verifies firmware support via `OsIndicationsSupported` before
writing any bits, avoiding the silent no-op where the firmware ignores
`BOOT_TO_FW_UI`.  It also preserves other `OsIndications` bits (OS
recovery requests, capsule delivery) so co-existing OS features are not
disrupted.

## Architecture

```
automojo.protocols.uefi.workflows.boottosetup/
    __init__.py              # re-exports
    boottosetupworkflow.py   # workflow class + convenience fn
    boottosetupreport.py     # report dataclass
    boottosetupcontext.py    # BootToSetupContext context manager
    setupreadyprobe.py       # transport-specific Setup-banner detector
```

## Components and Interfaces

### `BootToSetupWorkflow`

```python
class BootToSetupWorkflow:
    def __init__(
        self,
        agent: "UefiAgent",
        default_aspects: Optional[UefiCmdAspects] = None,
    ) -> None:
        self._agent = agent
        self._default_aspects = default_aspects
        return

    def enter(
        self,
        aspects: Optional[UefiCmdAspects] = None,
    ) -> "BootToSetupContext":
        ...
```

Enter phase execution order:

1. Resolve aspects.
2. Read `OsIndicationsSupported` via `5-variable-services`.
3. Validate `EFI_OS_INDICATIONS_BOOT_TO_FW_UI` bit is set; raise
   `UefiCapabilityError` otherwise.
4. Read current `OsIndications`; record as `os_indications_prior`.
5. Compute `os_indications_written = prior | BOOT_TO_FW_UI`.
6. Write `OsIndications` with attributes `NV | BS | RT`.
7. Invoke `6-reboot-and-verify` with `wait_for_shell=False`.
8. Run `setupreadyprobe.wait_for_setup(...)` bounded by
   `aspects.setup_ready_timeout`.
9. Construct `HiiFormNavigator` from `5-hii-forms`.
10. Return `BootToSetupContext` carrying navigator and the
    `os_indications_prior` value for restore.

### `BootToSetupContext`

```python
class BootToSetupContext:
    def __init__(
        self,
        workflow: "BootToSetupWorkflow",
        navigator: "HiiFormNavigator",
        os_indications_prior: int,
        report: "BootToSetupReport",
        aspects: UefiCmdAspects,
    ) -> None: ...

    def __enter__(self) -> "BootToSetupContext":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        # 1. navigator.save_and_exit() or navigator.discard_and_exit()
        # 2. read OsIndications current_after
        # 3. compute target = (current_after & ~BOOT_TO_FW_UI) |
        #                     (os_indications_prior & BOOT_TO_FW_UI)
        # 4. write OsIndications = target
        # 5. 6-reboot-and-verify(wait_for_shell=True)
        # 6. populate report.os_indications_restored and
        #    report.reboot_to_normal_report
        return

    @property
    def navigator(self) -> "HiiFormNavigator":
        return self._navigator

    @property
    def report(self) -> "BootToSetupReport":
        return self._report
```

### `BootToSetupReport`

```python
@dataclass
class BootToSetupReport:
    succeeded: bool
    os_indications_supported: int
    os_indications_prior: int
    os_indications_written: int
    os_indications_restored: Optional[int]
    setup_ready_at: Optional[datetime]
    reboot_to_setup_report: Optional["RebootAndVerifyReport"]
    reboot_to_normal_report: Optional["RebootAndVerifyReport"]
    started_at: datetime

    def as_dict(self) -> dict[str, object]:
        ...
```

Note: intentionally not `frozen=True` because the context updates fields
on `__exit__`.  Callers receive a reference to the live object and can
inspect it after `with` exits.

### `setupreadyprobe.wait_for_setup`

```python
def wait_for_setup(
    agent: "UefiAgent",
    timeout_seconds: float,
    aspects: UefiCmdAspects,
) -> datetime:
    # Poll the transport for a vendor Setup-banner pattern.
    # Supported transports: serial, IPMI-SOL (both via the same read-until
    # machinery).  Redfish does not expose a textual banner — use
    # /Systems/{id}/Bios state instead.
    ...
```

### `boot_to_setup` convenience function

```python
def boot_to_setup(
    agent: "UefiAgent",
    aspects: Optional[UefiCmdAspects] = None,
) -> BootToSetupContext:
    workflow = BootToSetupWorkflow(agent=agent)
    rtnval = workflow.enter(aspects=aspects)
    return rtnval
```

Typical caller shape:

```python
with boot_to_setup(agent=my_agent) as ctx:
    ctx.navigator.navigate_to("Advanced > Boot > Boot Order")
    ctx.navigator.move_item(item="Shell", direction="up")
# OsIndications is restored and target has rebooted to shell here.
```

## Data Models

* `BootToSetupReport` — described above.
* `HiiFormNavigator` — re-exported from `5-hii-forms`.
* `RebootAndVerifyReport` — re-exported from `6-reboot-and-verify`.

## Measured Interop Contract

The workflow delegates aspects unchanged to its constituent features:

* `aspects.action_pattern` — consumed locally.  Supported: `SINGLE_CALL`,
  `DO_UNTIL_SUCCESS` (enter phase only).  Others raise `SemanticError`.
* `aspects.completion_timeout` / `aspects.completion_interval` —
  forwarded to variable ops and to `6-reboot-and-verify`.
* `aspects.connection_timeout` / `aspects.connection_interval` —
  forwarded to both reboots.
* `aspects.setup_ready_timeout` (workflow-specific) — default 120s;
  bounds the Setup-banner wait.
* `aspects.inactivity_timeout` / `aspects.inactivity_interval` —
  forwarded to HII navigation (long screens, slow vendor repaint).
* `aspects.logging_pattern` — consumed and forwarded.
* `aspects.must_connect` — forwarded; enforced during both reboots.

The workflow enforces one invariant of its own: it refuses to write
`BOOT_TO_FW_UI` unless `OsIndicationsSupported` declares support.  This
invariant cannot be waived by aspects.

## Error Handling

| Condition                                      | Exception              | Included in `details`                        |
|------------------------------------------------|------------------------|----------------------------------------------|
| `BOOT_TO_FW_UI` not supported                  | `UefiCapabilityError`  | observed `OsIndicationsSupported` bitmask    |
| `OsIndications` write failed                   | `UefiVariableError`    | name, vendor_guid, attributes, efi_status    |
| Setup banner not observed within timeout       | `UefiShellError`       | captured console bytes                       |
| HII navigator unavailable for transport        | `ConfigurationError`   | transport kind                               |
| Restore write failed                           | `UefiVariableError`    | observed and target values                   |
| `action_pattern` unsupported                   | `SemanticError`        | offending pattern                            |
| `aspects` and agent default both `None`        | `ConfigurationError`   | agent repr                                   |

## Logging

Channel: `uefi.workflow.boottosetup`.

* At `INFO`: workflow start, supported-bit check, prior and written
  `OsIndications` values in hex.
* At `INFO`: reboot-to-Setup issued, Setup banner detected.
* At `DEBUG`: setup-banner detection details (matched pattern, transport
  read buffer snippet).
* At `INFO`: on `__exit__`, restore write and reboot-to-normal events.
* At `ERROR`: structured dict of partial report on failure.

## Security Considerations

* `OsIndications` is persistent and boot-relevant.  Failing to restore it
  causes the target to loop into Setup on every boot until cleared
  manually.  The context manager's `__exit__` restore is therefore
  critical; the workflow raises loudly if restore fails.
* Setup UI may expose sensitive platform configuration.  The workflow
  does not read or log unrequested fields; it only surfaces a navigator
  the caller drives explicitly.

## Testing Strategy

### Unit tests

* Capability probe — missing bit raises `UefiCapabilityError`.
* Preserve-other-bits semantics — prior `0x4` + BOOT_TO_FW_UI → write
  `0x5`, restore back to `0x4` regardless of intermediate firmware bits.
* Setup banner timeout raises `UefiShellError` with captured bytes.
* Action pattern rejection — three patterns raise `SemanticError`.
* Aspects forwarding — mock each constituent feature and assert
  forwarded aspects.

### Integration tests

* OVMF with `BOOT_TO_FW_UI` enabled — full round-trip.
* OVMF without `BOOT_TO_FW_UI` — capability failure path.
* HII navigator smoke — read the root form, assert at least one menu
  item visible.
* Gated by `AUTOMOJO_OVMF_TEST=1`.

### Action-pattern tests

* `SINGLE_CALL` — one attempt.
* `DO_UNTIL_SUCCESS` — retry enter phase on transient failure.
* Three rejected patterns raise `SemanticError`.

## Composition Diagram

```
caller
  │
  ▼
boot_to_setup(agent, aspects)   [enter phase]
  │
  │  1. resolve_aspects(...)
  │
  ├──▶ 5-variable-services: read OsIndicationsSupported (§8.2)
  │        │  verify EFI_OS_INDICATIONS_BOOT_TO_FW_UI bit set
  │
  ├──▶ 5-variable-services: read OsIndications (record prior)
  │
  ├──▶ 5-variable-services: write OsIndications
  │        value = prior | BOOT_TO_FW_UI
  │        attrs = NV | BS | RT
  │
  ├──▶ 6-reboot-and-verify(wait_for_shell=False)
  │        │
  │        └──▶ 5-system-reset + transport liveness probe (§8.5.1)
  │
  ├──▶ setupreadyprobe.wait_for_setup(...)
  │
  ├──▶ 5-hii-forms: construct HiiFormNavigator
  │
  ▼
BootToSetupContext  (returned to caller)
  │
  │   ... caller drives navigator ...
  │
  ▼
BootToSetupContext.__exit__   [exit phase]
  │
  ├──▶ 5-hii-forms: save_and_exit / discard_and_exit
  │
  ├──▶ 5-variable-services: read OsIndications (current_after)
  │
  ├──▶ 5-variable-services: write OsIndications
  │        value = (current_after & ~BOOT_TO_FW_UI) |
  │                (prior & BOOT_TO_FW_UI)
  │
  ├──▶ 6-reboot-and-verify(wait_for_shell=True)
  ▼
BootToSetupReport (populated)
```

## Dependencies on Other Features

* `1-aspects` — aspects, action patterns, resolver.
* `1-exceptions` — `UefiCapabilityError`, `UefiVariableError`,
  `UefiShellError`, `ConfigurationError`, `SemanticError`.
* `1-logging` — channel registration.
* `1-constants-and-types` — `EFI_GLOBAL_VARIABLE_GUID`,
  `EFI_OS_INDICATIONS_BOOT_TO_FW_UI`, attribute flag constants.
* `4-agent` — session and transport.
* `5-variable-services` — `OsIndications` / `OsIndicationsSupported`
  reads and writes.
* `5-hii-forms` — `HiiFormNavigator`.
* `6-reboot-and-verify` — both reboot steps.
