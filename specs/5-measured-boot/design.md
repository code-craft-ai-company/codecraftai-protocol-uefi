# Design — Measured Boot

## Overview

Measured Boot is a read-only analyzer for the TCG 2.0 event log recorded by
UEFI firmware.  It ingests the `binary_bios_measurements` blob, parses it into
typed `PcrEvent` records, reads live PCR values via `tpm2-tools`, and
reconstructs PCR accumulators to validate that the log has not been tampered
with.  The feature references UEFI specification §38 ("TCG Platform Interface")
and the TCG PC Client Platform Firmware Profile (TCG_PCR_EVENT2 layout).

All I/O flows through the 4-agent / 4-session layer so the
measured-interop aspects contract (action patterns, timeouts, intervals,
logging pattern) governs every external call.  The pure parsing code is
transport-free and therefore unit-testable against captured fixtures.

## Architecture

```
ccai.protocols.uefi.measured/
    __init__.py           # re-exports MeasuredBoot facade + types
    measuredboot.py       # MeasuredBoot facade; wires agent → parser/reader
    eventlog.py           # parse_event_log + specification ID decoding
    pcrevent.py           # PcrEvent dataclass and well-known event data types
    pcrreader.py          # read_pcrs (tpm2_pcrread shell-out)
    validator.py          # validate_log_against_pcrs + ValidationReport
    eventtypes.py         # EventType IntEnum + TPM_ALG_ID IntEnum
    errors.py             # measured-boot-specific UefiError subclasses
```

Dependency direction (lower rows depend on higher rows, never upward):

```
eventtypes.py ── pcrevent.py ── eventlog.py ── validator.py ──┐
                                                              ├── measuredboot.py
                                             pcrreader.py ────┘
errors.py ────────────────────────────────────────────────────┘
```

No module in this feature imports from layer 5 siblings at top level;
the GPT event-data passthrough (Requirement 9.3) declares its
`GptEventData.raw_bytes` field but does not import `5-gpt-mbr-parser`.

## Components and Interfaces

### `MeasuredBoot` facade (measuredboot.py)

```python
class MeasuredBoot:
    """
        Facade for TCG 2.0 event log readout, PCR readback, and
        log-vs-PCR validation over a UEFI agent or session.
    """

    def __init__(
        self,
        agent: "UefiAgent",
        aspects: Optional[UefiCmdAspects] = None,
    ) -> None:
        self._agent = agent
        self._aspects = aspects
        return

    def load_event_log_from_host(
        self,
        path: str = "/sys/kernel/security/tpm0/binary_bios_measurements",
        aspects: Optional[UefiCmdAspects] = None,
    ) -> bytes:
        """
            Read the kernel-preserved TCG event log over the SSH-to-host
            transport.

            :param path: Securityfs path to the event log.
            :param aspects: Optional aspects override.

            :returns: Raw event log bytes.
            :raises: UefiError, UefiShellError
        """
        effective = resolve_aspects(aspects, self._aspects)
        rtnval = self._agent.file_pull_bytes(path=path, aspects=effective)
        return rtnval

    def parse_event_log(
        self,
        buf: bytes,
    ) -> Iterator["PcrEvent"]:
        """
            Parse a TCG 2.0 event log buffer into PcrEvent records.

            :param buf: Raw event log bytes.

            :returns: Generator of PcrEvent records.
            :raises: UefiError
        """
        rtnval = _parse_event_log_stream(buf=buf)
        return rtnval

    def read_pcrs(
        self,
        banks: Sequence[str] = ("sha256",),
        aspects: Optional[UefiCmdAspects] = None,
    ) -> dict[str, dict[int, bytes]]:
        """
            Read live PCR values from the target TPM 2.0 via tpm2_pcrread.

            :param banks: Algorithm names to read.
            :param aspects: Optional aspects override.

            :returns: Mapping algorithm → PCR index → digest bytes.
            :raises: UefiError, UefiShellError, TimeoutError
        """
        effective = resolve_aspects(aspects, self._aspects)
        rtnval = _read_pcrs_via_agent(
            agent=self._agent,
            banks=banks,
            aspects=effective,
        )
        return rtnval

    def validate_log_against_pcrs(
        self,
        events: Sequence["PcrEvent"],
        pcrs: dict[str, dict[int, bytes]],
        strict_algorithms: bool = False,
    ) -> "ValidationReport":
        """
            Replay the log and compare accumulators to live PCR values.

            :param events: Parsed PcrEvent sequence.
            :param pcrs: Live PCR map as returned by read_pcrs.
            :param strict_algorithms: Require every bank in every event.

            :returns: ValidationReport on success.
            :raises: UefiError on mismatch.
        """
        rtnval = _replay_and_diff(
            events=events,
            pcrs=pcrs,
            strict_algorithms=strict_algorithms,
        )
        return rtnval
```

### `parse_event_log` (eventlog.py)

Streaming parser; reads one event at a time, yields as it goes.  Uses
layer-1 helpers `u8_le`, `u16_le`, `u32_le` for integer decoding.  Performs
the specification-ID-event validation per Requirement 10 before yielding
subsequent events.

### `PcrEvent` (pcrevent.py)

```python
@dataclass(frozen=True)
class PcrEvent:
    """
        A single parsed event from a TCG 2.0 event log.

        :param pcr_index: Target PCR index [0-23].
        :param event_type: TCG event type code.
        :param digests: Mapping TPM_ALG_ID → digest bytes.
        :param event_data: Decoded well-known event data, or raw bytes.
        :param offset: Byte offset of this event in the source log.
    """
    pcr_index: int
    event_type: int
    digests: dict[int, bytes]
    event_data: Union[
        "UefiVariableEventData",
        "UefiImageLoadEventData",
        "GptEventData",
        "FirmwareBlob2EventData",
        bytes,
    ]
    offset: int
```

Well-known event-data records are declared as separate `@dataclass(frozen=True)`
types in the same module.  Selection is via a registry dispatched by
`event_type`.

### `read_pcrs` (pcrreader.py)

Shell-outs to `tpm2_pcrread <alg>` once per requested bank.  Parses the
tool's stdout which has the form:

```
  sha256:
    0  : 0xDEADBEEF...
    1  : 0x...
```

The parser tolerates both `tpm2-tools` v4 and v5 output formats via two
regexes compiled at module import.

### `validate_log_against_pcrs` (validator.py)

```python
def _replay_and_diff(
    events: Sequence[PcrEvent],
    pcrs: dict[str, dict[int, bytes]],
    strict_algorithms: bool,
) -> "ValidationReport":
    accumulators = _initialize_accumulators(pcrs=pcrs)
    for ev in events:
        _extend_accumulators(
            accumulators=accumulators,
            event=ev,
            strict=strict_algorithms,
        )

    mismatches = _compare(accumulators=accumulators, pcrs=pcrs)

    if len(mismatches) > 0:
        err_msg_lines = ["Measured log does not reconstruct live PCRs."]
        err_msg_lines.append(f"MISMATCHES: ({len(mismatches)})")
        for mm in mismatches:
            err_msg_lines.append(f"    pcr={mm.pcr_index} alg={mm.algorithm}")
            err_msg_lines.append(f"        expected: {mm.expected.hex()}")
            err_msg_lines.append(f"        computed: {mm.computed.hex()}")
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiError(err_msg, details=[mm.as_detail() for mm in mismatches])

    rtnval = ValidationReport(success=True, mismatches=(), skipped_banks=_skipped(accumulators, pcrs))
    return rtnval
```

The extension primitive is the TCG Extend operation:
`PCR_new = H(PCR_old || event_digest)` per TCG §3.4.

### `ValidationReport` (validator.py)

```python
@dataclass(frozen=True)
class ValidationReport:
    success: bool
    mismatches: tuple["PcrMismatch", ...]
    skipped_banks: tuple[str, ...]


@dataclass(frozen=True)
class PcrMismatch:
    pcr_index: int
    algorithm: str
    expected: bytes
    computed: bytes

    def as_detail(self) -> str:
        rtnval = f"pcr={self.pcr_index} alg={self.algorithm} expected={self.expected.hex()} computed={self.computed.hex()}"
        return rtnval
```

## Data Models

### TCG_PCR_EVENT2 binary layout

Per TCG PC Client Platform Firmware Profile §10.2.1 and UEFI §38.4.1.

| Offset | Size (bytes) | Field           | Notes                                                        |
|-------:|-------------:|-----------------|--------------------------------------------------------------|
| 0      | 4            | PCRIndex        | UINT32 little-endian; target PCR index [0-23].               |
| 4      | 4            | EventType       | UINT32 little-endian; TCG event type code.                   |
| 8      | 4            | DigestCount     | UINT32 little-endian; number of digest entries following.    |
| 12     | variable     | Digests[]       | Array of `TPMT_HA` entries (see below), `DigestCount` total. |
| var    | 4            | EventSize       | UINT32 little-endian; length of `Event[]` in bytes.          |
| var+4  | EventSize    | Event[]         | Event-type-specific payload bytes.                           |

Each `TPMT_HA` entry in the `Digests[]` array:

| Offset | Size (bytes) | Field      | Notes                                                           |
|-------:|-------------:|------------|-----------------------------------------------------------------|
| 0      | 2            | hashAlg    | UINT16 little-endian; `TPM_ALG_ID` (SHA1=0x0004, SHA256=0x000B, SHA384=0x000C, SHA512=0x000D, SM3_256=0x0012). |
| 2      | N            | digest     | Raw digest bytes; length determined by `hashAlg`:               |
|        |              |            |    SHA1=20, SHA256=32, SHA384=48, SHA512=64, SM3_256=32.        |

### TCG_PCR_EVENT (first event only) binary layout

Per TCG §10.2.

| Offset | Size (bytes) | Field       | Notes                                                      |
|-------:|-------------:|-------------|------------------------------------------------------------|
| 0      | 4            | PCRIndex    | UINT32 little-endian.                                      |
| 4      | 4            | EventType   | UINT32 little-endian; must be `EV_NO_ACTION` (0x00000003). |
| 8      | 20           | Digest      | SHA-1 digest of the event data.                            |
| 28     | 4            | EventSize   | UINT32 little-endian; length of `Event[]`.                 |
| 32     | EventSize    | Event[]     | Contains the `EFI_SPEC_ID_EVENT_STRUCT`.                   |

### `EFI_SPEC_ID_EVENT_STRUCT` (first-event payload)

| Offset | Size (bytes)        | Field                | Notes                                                 |
|-------:|--------------------:|----------------------|-------------------------------------------------------|
| 0      | 16                  | Signature            | ASCII `"Spec ID Event03"` + NUL terminator.           |
| 16     | 4                   | platformClass        | UINT32; 0x0 = Client, 0x1 = Server.                   |
| 20     | 1                   | specVersionMinor     | UINT8.                                                |
| 21     | 1                   | specVersionMajor     | UINT8.                                                |
| 22     | 1                   | specErrata           | UINT8.                                                |
| 23     | 1                   | uintnSize            | UINT8; 1 = 32-bit, 2 = 64-bit.                        |
| 24     | 4                   | numberOfAlgorithms   | UINT32 little-endian.                                 |
| 28     | 4 × numberOfAlgorithms | digestSizes[]     | Array of `TCG_EfiSpecIdEventAlgorithmSize` pairs (UINT16 hashAlg, UINT16 digestSize). |
| var    | 1                   | vendorInfoSize       | UINT8.                                                |
| var+1  | vendorInfoSize      | vendorInfo[]         | Vendor-specific bytes.                                |

## Measured Interop Contract

Consumed `aspects` fields:

* `action_pattern` — every public method routes through the 4-base
  action-pattern engine.
* `completion_timeout`, `completion_interval` — drive the
  `DO_UNTIL_SUCCESS` retry loop for `read_pcrs` when the TPM is not yet
  available after boot.
* `connection_timeout`, `connection_interval`,
  `allowed_connection_exceptions` — drive `SINGLE_CONNECTED_CALL`
  behavior.
* `inactivity_timeout`, `inactivity_interval` — passed through to the
  SSH-to-host transport for the subprocess read.
* `allowed_error_codes` — non-zero `tpm2_pcrread` exit codes in this set
  are not treated as failures.
* `logging_pattern` — routes log emission on channel `uefi.measured`.
* `logger` — injected logger; if `None`, `get_uefi_logger("uefi.measured")`
  is used.

Supported `ActionPattern` values:

* `ActionPattern.SINGLE_CALL` — one-shot read; fail fast.
* `ActionPattern.SINGLE_CONNECTED_CALL` — retry only on connection-class
  exceptions listed in `aspects.allowed_connection_exceptions`.
* `ActionPattern.DO_UNTIL_SUCCESS` — poll `tpm2_pcrread` until success or
  `completion_timeout` elapses.

`DO_WHILE_SUCCESS` and `DO_UNTIL_CONNECTION_FAILURE` are not meaningful for
a read-only operation and SHALL raise `SemanticError` if requested.

Pure parsing functions (`parse_event_log`, `validate_log_against_pcrs`) do
not consume aspects — they operate on in-memory buffers.

## Error Handling

| Condition                                                        | Exception         | Included in `details`                                                        |
|------------------------------------------------------------------|-------------------|------------------------------------------------------------------------------|
| Truncated event log                                              | `UefiError`       | byte offset, field name being decoded, count of prior events parsed          |
| Unsupported spec-ID version                                      | `UefiError`       | observed major/minor/errata, supported versions                              |
| Spec-ID signature mismatch                                       | `UefiError`       | observed signature bytes                                                     |
| Digest length mismatch                                           | `UefiError`       | event index, alg ID, expected size, observed size                            |
| `tpm2_pcrread` not installed                                     | `UefiError`       | missing binary name, transport probe evidence                                |
| `tpm2_pcrread` non-zero exit                                     | `UefiShellError`  | command, status, stdout, stderr                                              |
| Requested bank not implemented                                   | `UefiError`       | implemented banks reported by `tpm2_getcap pcrs`                             |
| Securityfs event log missing                                     | `UefiError`       | path, remediation hint (mount securityfs, enable TPM kernel subsystem)       |
| Log replay diverges from PCR                                     | `UefiError`       | per-mismatch PCR index, algorithm, expected, computed                        |
| Unsupported `ActionPattern`                                      | `SemanticError`   | requested pattern, supported patterns                                        |
| Write-oriented helper attempted                                  | `SemanticError`   | helper name, "measured boot is read-only"                                    |

`SemanticError` must never be caught inside this feature.

## Logging

* Channel: `uefi.measured`.
* INFO: event count parsed, PCR bank read completion, successful validation.
* DEBUG: per-event byte offset, per-PCR accumulator transitions.
* WARNING: retry attempts in `DO_UNTIL_SUCCESS`, skipped banks under
  non-strict mode.
* ERROR: terminal failures before re-raise.

Log-format selection follows `aspects.logging_pattern` exactly as
4-base's `_log_command_result` does.

## Security Considerations

* **Read-only contract.**  The feature never extends, resets, or writes any
  PCR; tests enforce this via a subprocess-call recording harness.
* **No key material.**  This feature does not sign, verify signatures, or
  manage keys.  Attestation quoting is a layer-6 workflow feature.
* **Log trust boundary.**  A successful `validate_log_against_pcrs` call
  proves only that the log reconstructs the current PCR values; it does not
  prove the firmware measurements themselves are correct.  Callers must
  compare digests against known-good reference values to establish integrity.
* **`tpm2-tools` output parsing.**  The parser tolerates both v4 and v5
  output formats; if a new format is encountered the unrecognized output is
  preserved in the raised `UefiShellError.stdout` for forensic review
  rather than silently ignored.

## Testing Strategy

### Unit tests (`test_eventlog.py`)

* Spec-ID-event signature validation (positive and tampered).
* Multi-algorithm digest decoding (SHA-1 + SHA-256 + SHA-384 in one event).
* Truncated-log-at-each-field offset tests (driven by a truncation-sweep
  fixture that cuts the log one byte shorter each iteration).
* Well-known event-type decoders (`EV_EFI_VARIABLE_DRIVER_CONFIG`,
  `EV_EFI_BOOT_SERVICES_APPLICATION`, `EV_EFI_GPT_EVENT`,
  `EV_EFI_PLATFORM_FIRMWARE_BLOB2`).
* Unknown event type passes through as raw bytes.

### Unit tests (`test_validator.py`)

* Replay against hand-computed accumulator for a captured 30-event fixture.
* Mismatch injection: mutate one event digest, assert `UefiError` with the
  mismatch in `details`.
* `strict_algorithms=True` flags missing banks; `strict_algorithms=False`
  skips and records in the report.

### Unit tests (`test_pcrreader.py`)

* Tolerates both `tpm2-tools` v4 and v5 output formats.
* Missing binary raises `UefiError` with remediation hint.
* Non-zero status not in `allowed_error_codes` raises `UefiShellError`.

### Action-pattern tests (`test_actionpatterns.py`)

* `SINGLE_CALL` — one invocation, fail fast.
* `SINGLE_CONNECTED_CALL` — retries connection class, fails on app error.
* `DO_UNTIL_SUCCESS` — polls and eventually succeeds within timeout.
* `DO_WHILE_SUCCESS` / `DO_UNTIL_CONNECTION_FAILURE` raise `SemanticError`.
* `aspects=None` routes through `resolve_aspects` to the instance default.

### Integration tests (`test_measured_integration.py`)

* End-to-end against a captured OVMF+vTPM event log and a live `tpm2_pcrread`
  stub, asserting `validate_log_against_pcrs` returns `success=True`.
* Gated by `CCAI_UEFI_OVMF_VM` env var.

### Fixtures corpus

`tests/fixtures/measured/` — captured `binary_bios_measurements` blobs
and matching `tpm2_pcrread --all` stdout dumps from:

* OVMF + swtpm SHA-256 only.
* A bare-metal AMI system with SHA-1 + SHA-256 + SHA-384 banks.
* A Phoenix/Insyde system with SM3_256 present.

### Coverage target

≥ 95% line coverage for `eventlog.py`, `validator.py`, `pcrreader.py`.

## Dependencies on Other Features

* 1-aspects — consumes `UefiCmdAspects`, `resolve_aspects`,
  `ActionPattern`, `LoggingPattern`.
* 1-exceptions — raises `UefiError`, `UefiShellError`, `SemanticError`.
* 1-helpers — uses integer packers for TCG_PCR_EVENT2 decoding.
* 1-logging — emits on `uefi.measured` channel.
* 2-transport-ssh-host — reads securityfs, runs `tpm2-tools`.
* 4-base / 4-agent — hosts the action-pattern engine and provides
  `file_pull_bytes` and shell dispatch.

No dependency on layer 5 siblings at top level.  The GPT-event-data record
holds an opaque `raw_bytes` field that a caller may pass to
`5-gpt-mbr-parser` if deeper decoding is desired; the coupling is via bytes
only.
