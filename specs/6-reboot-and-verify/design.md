# Design — Reboot and Verify Workflow

## Overview

The Reboot and Verify workflow is a single composite entry point that wraps
a system reset with transport-return detection, shell verification, and
state-carry-forward replay.  It is authored as layer 6 because it composes
multiple layer-5 features (`5-system-reset`) and a layer-4 primitive
(`4-agent.verify_connectivity`) into a single API surface with one shared
aspects object.

The workflow is intentionally thin: the bulk of the behavior lives in the
layer-5 `5-system-reset` feature and the layer-4 agent.  What this layer
adds is (1) a structured `RebootAndVerifyReport`, (2) console-noise capture
during the reset window, (3) the `wait_for_shell` convenience, and (4)
state-carry-forward replay.

## Architecture

```
ccai.protocols.uefi.workflows.rebootverify/
    __init__.py               # re-exports public surface
    rebootverifyworkflow.py   # RebootAndVerifyWorkflow class
    rebootverifyreport.py     # RebootAndVerifyReport dataclass
    captureconsole.py         # ConsoleCaptureBuffer helper
    stateforward.py           # SessionStateSnapshot + replay
```

The workflow class is constructed with an agent and an optional default
aspects object; the public method is `run(...)`.  A module-level
`reboot_and_verify(agent, ...)` convenience function is exported for the
common case where the caller does not need to hold the workflow object.

## Components and Interfaces

### `RebootAndVerifyWorkflow`

```python
class RebootAndVerifyWorkflow:
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
        reset_kind: ResetKind = ResetKind.COLD,
        wait_for_shell: bool = True,
        aspects: Optional[UefiCmdAspects] = None,
    ) -> "RebootAndVerifyReport":
        ...
```

Execution order inside `run()`:

1. Resolve aspects via `resolve_aspects(aspects, self._default_aspects)`.
2. Snapshot agent session state via `SessionStateSnapshot.capture(agent)`.
3. Open a `ConsoleCaptureBuffer` bound to the transport's raw-read tap.
4. Invoke `5-system-reset.reset(kind=reset_kind, aspects=effective_aspects)`.
5. If `wait_for_shell` is `True`, call `agent.verify_connectivity(aspects=...)`.
6. Else, run the transport's ICMP/liveness probe.
7. On success with `wait_for_shell=True`, replay `SessionStateSnapshot`.
8. Build and return `RebootAndVerifyReport`.

### `RebootAndVerifyReport`

```python
@dataclass(frozen=True)
class RebootAndVerifyReport:
    succeeded: bool
    reset_kind: ResetKind
    reset_duration_seconds: float
    icmp_verified: bool
    shell_verified: bool
    state_replayed: bool
    console_capture: bytes
    console_capture_truncated: bool
    attempts: int
    started_at: datetime

    def as_dict(self) -> dict[str, object]:
        ...
```

`as_dict()` base64-encodes `console_capture` so the result can be embedded
in JSON-serialized test artifacts.

### `ConsoleCaptureBuffer`

```python
class ConsoleCaptureBuffer:
    def __init__(self, transport: "IUefiTransport", max_bytes: int) -> None: ...
    def __enter__(self) -> "ConsoleCaptureBuffer": ...
    def __exit__(self, exc_type, exc, tb) -> None: ...
    def drain(self) -> tuple[bytes, bool]: ...
```

The buffer attaches a non-consuming tap to the transport on `__enter__`
and removes it on `__exit__`.  For Redfish (no raw console) the tap is a
no-op and `drain()` returns `(b"", False)`.

### `SessionStateSnapshot`

```python
@dataclass(frozen=True)
class SessionStateSnapshot:
    map_alias: Optional[str]
    working_directory: Optional[str]
    environment: tuple[tuple[str, str], ...]

    @classmethod
    def capture(cls, agent: "UefiAgent") -> "SessionStateSnapshot": ...

    def replay(self, agent: "UefiAgent", aspects: UefiCmdAspects) -> None: ...
```

Replay is best-effort per-element: if any element fails we raise
`UefiShellError` with `details` naming the element that failed and listing
the elements already replayed.

### `reboot_and_verify` convenience function

```python
def reboot_and_verify(
    agent: "UefiAgent",
    reset_kind: ResetKind = ResetKind.COLD,
    wait_for_shell: bool = True,
    aspects: Optional[UefiCmdAspects] = None,
) -> RebootAndVerifyReport:
    workflow = RebootAndVerifyWorkflow(agent=agent)
    rtnval = workflow.run(
        reset_kind=reset_kind,
        wait_for_shell=wait_for_shell,
        aspects=aspects,
    )
    return rtnval
```

## Data Models

* `RebootAndVerifyReport` — described above.
* `SessionStateSnapshot` — described above.
* `ResetKind` — re-exported from `5-system-reset`.

## Measured Interop Contract

This workflow is a composite; it does not consume aspects fields directly
beyond forwarding.  Specifically:

* `aspects.action_pattern` — consumed by the workflow itself to decide
  whether to retry the entire composite.  Supported values:
  `SINGLE_CALL`, `DO_UNTIL_SUCCESS`.  Others raise `SemanticError`.
* `aspects.completion_timeout` and `aspects.completion_interval` —
  forwarded to `5-system-reset` and to `agent.verify_connectivity`.
* `aspects.connection_timeout` and `aspects.connection_interval` —
  forwarded to `5-system-reset` for the post-reset reconnect loop.
* `aspects.inactivity_timeout`, `aspects.inactivity_interval`,
  `aspects.monitor_delay` — forwarded unchanged.
* `aspects.logging_pattern` — consumed by the workflow when logging the
  composite outcome in addition to being forwarded.
* `aspects.allowed_error_codes`, `aspects.allowed_connection_exceptions`
  — forwarded.
* `aspects.must_connect` — forwarded; when `True` the workflow's
  post-reset connectivity probe raises on the first failure.
* `aspects.max_capture_bytes` — consumed directly by the workflow's
  `ConsoleCaptureBuffer`.

The workflow delegates aspects unmodified to `5-system-reset` and
`4-agent.verify_connectivity`, but it enforces one invariant of its own:
`action_pattern` must be in the two supported values above.

## Error Handling

| Condition                                        | Exception             | Included in `details`                          |
|--------------------------------------------------|-----------------------|------------------------------------------------|
| Transport does not return within timeout         | `ResetFailedError`    | last transport state, captured console so far  |
| Shell verification fails post-reset              | `UefiShellError`      | shell probe stdout/stderr, attempts            |
| State-replay element fails                       | `UefiShellError`      | failed element, replayed-so-far list           |
| `action_pattern` unsupported                     | `SemanticError`       | offending pattern                              |
| `wait_for_shell=False` on no-ICMP transport      | `ConfigurationError`  | transport kind                                 |
| `aspects` and agent default both `None`          | `ConfigurationError`  | agent repr                                     |
| Reset kind unsupported by transport              | `ConfigurationError`  | transport kind, reset kind                     |

`SemanticError` is propagated — never caught.

## Logging

Channel: `uefi.workflow.reboot`.

* At `INFO`: workflow start with `reset_kind`, `wait_for_shell`, resolved
  aspects summary.
* At `INFO`: reset issued, transport returned, shell verified (per aspect
  `logging_pattern`).
* At `DEBUG`: captured console bytes hex-dumped at 16-bytes-per-line.
* At `ERROR`: structured dict of the partial `RebootAndVerifyReport` when
  the workflow fails.

## Security Considerations

The workflow is destructive — it reboots the target.  It therefore requires
an authenticated agent; unauthenticated use is prevented one layer down in
the agent's transport.  The captured console buffer may contain
firmware-emitted diagnostics; callers who run against production targets
must be aware that these may leak configuration-sensitive strings and should
redact before logging externally.

## Testing Strategy

### Unit tests

* Aspects delegation — mock `5-system-reset.reset` and assert
  forwarded aspects.
* Unsupported `ActionPattern` values raise `SemanticError`.
* `wait_for_shell=False` on Redfish works without ICMP (uses transport
  liveness), and raises `ConfigurationError` on a transport lacking any
  liveness probe.
* Console capture truncation at `max_capture_bytes`.
* State-replay success and partial-failure paths.

### Integration tests

* OVMF: cold reset, warm reset, shutdown-then-start — each with
  `wait_for_shell=True` and `wait_for_shell=False`.
* OVMF: state-carry-forward after cold reset restores map alias.
* Gated on `CCAI_OVMF_TEST=1`.

### Action-pattern tests

* `SINGLE_CALL` — one attempt, propagates errors.
* `DO_UNTIL_SUCCESS` — simulated transient failure retried until success.
* All other patterns — `SemanticError`.

## Composition Diagram

```
caller
  │
  ▼
reboot_and_verify(agent, ...)
  │
  │  1. resolve_aspects(...)
  │  2. SessionStateSnapshot.capture(agent)
  │  3. ConsoleCaptureBuffer.__enter__()
  │
  ├──▶ 5-system-reset.reset(kind, aspects)
  │        │
  │        ▼
  │     transport.close() ─▶ firmware reset ─▶ transport.reconnect()
  │
  ├──▶ (if wait_for_shell)
  │       4-agent.verify_connectivity(aspects)
  │         │
  │         ▼
  │      5-shell-commands: echo probe
  │
  ├──▶ (if wait_for_shell)
  │       SessionStateSnapshot.replay(agent, aspects)
  │
  │  ConsoleCaptureBuffer.__exit__()
  ▼
RebootAndVerifyReport
```

## Dependencies on Other Features

* `1-aspects` — `UefiAspects`, `UefiCmdAspects`, `ActionPattern`,
  `resolve_aspects`.
* `1-exceptions` — `UefiShellError`, `ResetFailedError`,
  `ConfigurationError`, `SemanticError`.
* `1-logging` — logging channel registration.
* `4-agent` — `UefiAgent.verify_connectivity`, session state accessors.
* `5-system-reset` — the underlying reset primitive.
* `5-shell-commands` — echo probe used by `verify_connectivity`.
