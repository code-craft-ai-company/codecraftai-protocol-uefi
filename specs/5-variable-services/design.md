# Design — Variable Services

## Overview

Variable Services delivers `VariableStore`, a single facade that implements
the four UEFI variable-service operations (`GetVariable`, `SetVariable`,
`GetNextVariableName`, `QueryVariableInfo`) per UEFI §8.2, atop three
pluggable backends (shell, host-OS via `efivarfs`/`efivar`, Redfish).
Authenticated writes (§8.2.6) accept either a raw
`EFI_VARIABLE_AUTHENTICATION_2` descriptor or an unwrapped signer
(`SignerKeyPair`) and the facade assembles the signed descriptor
internally using `cryptography.hazmat.primitives`.

The facade never silently swallows errors: every backend failure becomes a
`UefiVariableError` carrying `name`, `vendor_guid`, `attributes`, and
`efi_status`.  Retry policy is controlled exclusively by
`UefiCmdAspects.action_pattern`.

## Architecture

```
automojo.protocols.uefi.variables/
    __init__.py               # re-exports public surface
    attributes.py             # VariableAttributes IntFlag
    records.py                # VariableRecord, VariableWriteReceipt,
                              # QueryVariableInfoResult dataclasses
    auth_descriptor.py        # EfiVariableAuthentication2Descriptor, SignerKeyPair
    auth_builder.py           # build_authentication2_descriptor()
    store.py                  # VariableStore facade
    backends/
        __init__.py
        abstract.py           # IVariableBackend Protocol
        shell_backend.py      # ShellVariableBackend (setvar/dmpstore)
        host_backend.py       # HostOsVariableBackend (efivar/efivarfs)
        redfish_backend.py    # RedfishVariableBackend (Bios subtree)
```

Dependency direction (bottom-up):

```
store.py ──▶ backends/*.py ──▶ auth_builder.py ──▶ auth_descriptor.py
         ──▶ attributes.py ──▶ records.py
                                     (all depend on layer 1 aspects/exceptions/helpers)
```

## Components and Interfaces

### `VariableAttributes` (attributes.py)

```python
class VariableAttributes(IntFlag):
    NON_VOLATILE = 0x01
    BOOTSERVICE_ACCESS = 0x02
    RUNTIME_ACCESS = 0x04
    HARDWARE_ERROR_RECORD = 0x08
    AUTHENTICATED_WRITE_ACCESS = 0x10
    TIME_BASED_AUTHENTICATED_WRITE_ACCESS = 0x20
    APPEND_WRITE = 0x40
    ENHANCED_AUTHENTICATED_ACCESS = 0x80

    @classmethod
    def from_bytes(cls, buf: bytes) -> "VariableAttributes":
        """
            Decode the 4-byte little-endian attribute buffer.

            :param buf: A 4-byte buffer carrying the attribute bitfield.

            :returns: A VariableAttributes value.
            :raises: ConfigurationError.
        """
        if len(buf) != 4:
            err_msg = f"VariableAttributes requires 4 bytes; got {len(buf)}."
            raise ConfigurationError(err_msg)
        value = int.from_bytes(buf, byteorder="little", signed=False)
        rtnval = cls(value)
        return rtnval

    def to_bytes(self) -> bytes:
        rtnval = int(self).to_bytes(length=4, byteorder="little", signed=False)
        return rtnval

    def validate(self) -> None:
        """
            Cross-field validation per UEFI §8.2.

            :raises: ConfigurationError.
        """
        if (self & VariableAttributes.RUNTIME_ACCESS) and not (self & VariableAttributes.BOOTSERVICE_ACCESS):
            err_msg = "RUNTIME_ACCESS requires BOOTSERVICE_ACCESS (UEFI §8.2)."
            raise ConfigurationError(err_msg)
        if (self & VariableAttributes.AUTHENTICATED_WRITE_ACCESS) and (self & VariableAttributes.TIME_BASED_AUTHENTICATED_WRITE_ACCESS):
            err_msg = "AUTHENTICATED_WRITE_ACCESS and TIME_BASED_AUTHENTICATED_WRITE_ACCESS are mutually exclusive."
            raise ConfigurationError(err_msg)
        if self & VariableAttributes.ENHANCED_AUTHENTICATED_ACCESS:
            if self & (VariableAttributes.AUTHENTICATED_WRITE_ACCESS | VariableAttributes.TIME_BASED_AUTHENTICATED_WRITE_ACCESS):
                err_msg = "ENHANCED_AUTHENTICATED_ACCESS cannot combine with legacy auth-write flags."
                raise ConfigurationError(err_msg)
        return
```

### Records (records.py)

```python
@dataclass(frozen=True)
class VariableRecord:
    name: str
    vendor_guid: uuid.UUID
    attributes: VariableAttributes
    data: bytes


@dataclass(frozen=True)
class VariableWriteReceipt:
    efi_status: int
    bytes_written: int


@dataclass(frozen=True)
class QueryVariableInfoResult:
    maximum_variable_storage_size: int
    remaining_variable_storage_size: int
    maximum_variable_size: int
```

### `SignerKeyPair` and Descriptor Builder

```python
@dataclass(frozen=True)
class SignerKeyPair:
    """
        Unwrapped signer material for authenticated variable writes.

        :ivar private_key: An RSA or ECDSA private key object from
                           cryptography.hazmat.primitives.asymmetric.
        :ivar certificate: An X.509 certificate binding the public key.
        :ivar timestamp: Optional tz-aware datetime; defaults to now(UTC).
    """
    private_key: "PrivateKeyTypes"
    certificate: "x509.Certificate"
    timestamp: Optional[datetime] = None


@dataclass(frozen=True)
class EfiVariableAuthentication2Descriptor:
    """
        Pre-built descriptor for §8.2.6 authenticated writes.
    """
    timestamp: datetime
    authentication_info: bytes   # WIN_CERTIFICATE_UEFI_GUID + PKCS#7 auth_data
```

```python
def build_authentication2_descriptor(
    name: str,
    vendor_guid: uuid.UUID,
    attributes: VariableAttributes,
    data: bytes,
    signer: SignerKeyPair,
) -> EfiVariableAuthentication2Descriptor:
    """
        Construct a time-based authenticated variable descriptor.

        Per UEFI §8.2.6 the signed digest input is:

            name (UCS-2, no null) || vendor_guid (16 bytes)
            || attributes (4 bytes LE) || timestamp (EFI_TIME, 16 bytes)
            || data

        The signature is detached PKCS#7 with the signer's certificate
        embedded, wrapped in a WIN_CERTIFICATE_UEFI_GUID whose CertType
        is EFI_CERT_TYPE_PKCS7_GUID.

        :param name: The variable name.
        :param vendor_guid: The vendor GUID.
        :param attributes: The attribute flags (must include
                           TIME_BASED_AUTHENTICATED_WRITE_ACCESS).
        :param data: The payload data (not including the descriptor).
        :param signer: The SignerKeyPair to sign with.

        :returns: A descriptor ready to prepend to the wire payload.
        :raises: ConfigurationError.
    """
    ...
```

### `VariableStore` (store.py)

```python
class VariableStore:
    def __init__(
        self,
        agent: "UefiAgent",
        backend_override: Optional["IVariableBackend"] = None,
        aspects: Optional[UefiCmdAspects] = None,
    ) -> None:
        self._agent: "UefiAgent" = agent
        self._aspects: UefiCmdAspects = aspects if aspects is not None else agent.aspects
        self._backend: IVariableBackend = self._select_backend(agent, backend_override)
        return

    def get_variable(
        self,
        name: str,
        vendor_guid: Union[str, uuid.UUID],
        aspects: Optional[UefiCmdAspects] = None,
    ) -> VariableRecord: ...

    def set_variable(
        self,
        name: str,
        vendor_guid: Union[str, uuid.UUID],
        attributes: VariableAttributes,
        data: bytes,
        auth: Optional[Union[EfiVariableAuthentication2Descriptor, SignerKeyPair]] = None,
        aspects: Optional[UefiCmdAspects] = None,
    ) -> VariableWriteReceipt: ...

    def iter_variables(
        self,
        name_filter: Optional[str] = None,
        aspects: Optional[UefiCmdAspects] = None,
    ) -> Iterator[tuple[str, uuid.UUID]]: ...

    def query_variable_info(
        self,
        attributes: VariableAttributes,
        aspects: Optional[UefiCmdAspects] = None,
    ) -> QueryVariableInfoResult: ...
```

### `IVariableBackend` Protocol (backends/abstract.py)

```python
@runtime_checkable
class IVariableBackend(Protocol):
    def get(
        self,
        name: str,
        vendor_guid: uuid.UUID,
        aspects: UefiCmdAspects,
    ) -> VariableRecord: ...

    def set(
        self,
        name: str,
        vendor_guid: uuid.UUID,
        attributes: VariableAttributes,
        body: bytes,
        aspects: UefiCmdAspects,
    ) -> VariableWriteReceipt: ...

    def next(
        self,
        cursor: Optional[tuple[str, uuid.UUID]],
        aspects: UefiCmdAspects,
    ) -> Optional[tuple[str, uuid.UUID]]: ...

    def query(
        self,
        attributes: VariableAttributes,
        aspects: UefiCmdAspects,
    ) -> QueryVariableInfoResult: ...
```

## Data Models

### Attribute Bit Layout (4 bytes, little-endian, UEFI §8.2)

| Bit | Flag                                      | Value      |
|-----|-------------------------------------------|------------|
| 0   | `NON_VOLATILE`                            | `0x01`     |
| 1   | `BOOTSERVICE_ACCESS`                      | `0x02`     |
| 2   | `RUNTIME_ACCESS`                          | `0x04`     |
| 3   | `HARDWARE_ERROR_RECORD`                   | `0x08`     |
| 4   | `AUTHENTICATED_WRITE_ACCESS` (deprecated) | `0x10`     |
| 5   | `TIME_BASED_AUTHENTICATED_WRITE_ACCESS`   | `0x20`     |
| 6   | `APPEND_WRITE`                            | `0x40`     |
| 7   | `ENHANCED_AUTHENTICATED_ACCESS`           | `0x80`     |
| 8–31| Reserved                                  | `0`        |

### `EFI_VARIABLE_AUTHENTICATION_2` Layout (UEFI §8.2.6)

| Offset | Size | Field                                 | Notes                                 |
|--------|------|---------------------------------------|---------------------------------------|
| 0      | 16   | `TimeStamp` (EFI_TIME)                | Year 2-byte LE, Month 1-byte, Day 1   |
| 16     | 4    | `AuthInfo.Hdr.dwLength`               | LE, includes Hdr + CertData           |
| 20     | 2    | `AuthInfo.Hdr.wRevision`              | `0x0200`                              |
| 22     | 2    | `AuthInfo.Hdr.wCertificateType`       | `WIN_CERT_TYPE_EFI_GUID` (`0x0EF1`)   |
| 24     | 16   | `AuthInfo.CertType`                   | `EFI_CERT_TYPE_PKCS7_GUID`            |
| 40     | var  | `AuthInfo.CertData`                   | DER-encoded PKCS#7 SignedData         |

The "data" body of the `SetVariable` call then continues after the
descriptor.

### `EFI_TIME` Layout (UEFI §8.3, 16 bytes)

| Offset | Size | Field        | Notes                                 |
|--------|------|--------------|---------------------------------------|
| 0      | 2    | `Year`       | LE, 1900–9999                         |
| 2      | 1    | `Month`      | 1–12                                  |
| 3      | 1    | `Day`        | 1–31                                  |
| 4      | 1    | `Hour`       | 0–23                                  |
| 5      | 1    | `Minute`     | 0–59                                  |
| 6      | 1    | `Second`     | 0–59                                  |
| 7      | 1    | `Pad1`       | 0                                     |
| 8      | 4    | `Nanosecond` | LE, 0–999_999_999                     |
| 12     | 2    | `TimeZone`   | LE signed, minutes from UTC           |
| 14     | 1    | `Daylight`   | Bitfield                              |
| 15     | 1    | `Pad2`       | 0                                     |

## Measured Interop Contract

Every public method accepts `aspects: Optional[UefiCmdAspects] = None` at
the last positional slot.  `resolve_aspects(aspects, self._aspects)` is
invoked at the top of every public method.

Aspects fields consumed:

* `action_pattern` — dispatched to the backend's retry loop.
* `completion_timeout`, `completion_interval` — used for Redfish Task
  polling and for `DO_UNTIL_SUCCESS` retries on transient backend errors.
* `connection_timeout`, `connection_interval` — for backend
  (re)connection.
* `inactivity_timeout`, `inactivity_interval` — for shell-backend
  prompt-wait governance.
* `monitor_delay` — wraps the inner backend call in `MonitoredScope`.
* `logging_pattern` — consulted when emitting per-op DEBUG/INFO/WARNING.
* `allowed_error_codes` — used to whitelist non-success `efi_status`
  values that the caller wants to tolerate (e.g., `EFI_NOT_FOUND` during
  opportunistic reads).
* `expected_status` — default `0` (EFI_SUCCESS).
* `prompt_pattern` — shell backend only.
* `response_encoding` — shell backend only.

ActionPatterns supported:

| Pattern                          | Read ops | `set_variable` | Enumeration | `query_variable_info` |
|----------------------------------|----------|----------------|-------------|------------------------|
| `SINGLE_CALL`                    | Yes      | Yes            | N/A (generator) | Yes                |
| `SINGLE_CONNECTED_CALL`          | Yes      | Yes            | Yes         | Yes                    |
| `DO_UNTIL_SUCCESS`               | Yes      | Yes            | No          | Yes                    |
| `DO_WHILE_SUCCESS`               | Yes      | No — `SemanticError` | No    | Yes                    |
| `DO_UNTIL_CONNECTION_FAILURE`    | No       | No             | No          | No                     |

## Error Handling

| Condition                                                | Exception              | `details`                                       |
|----------------------------------------------------------|------------------------|-------------------------------------------------|
| Variable not found                                       | `UefiVariableError`    | name, guid, efi_status=EFI_NOT_FOUND            |
| Read buffer too small                                    | `UefiVariableError`    | name, guid, efi_status=EFI_BUFFER_TOO_SMALL     |
| Backend rejects write                                    | `UefiVariableError`    | name, guid, attributes, efi_status (hex)        |
| Signer timestamp non-monotonic                           | `UefiVariableError`    | name, guid, efi_status=EFI_SECURITY_VIOLATION   |
| Attributes cross-field validation fails                  | `ConfigurationError`   | conflicting flag names                          |
| `auth` missing when auth flag set / vice-versa           | `ConfigurationError`   | missing side                                    |
| Unknown `transport_kind`, no override                    | `ConfigurationError`   | observed `transport_kind` value                 |
| Backend cannot honor operation (e.g., Redfish + raw auth)| `UefiTransportError`   | backend name, operation, limitation             |
| Naive `datetime` passed for timestamp                    | `ConfigurationError`   | offending value                                 |
| `DO_WHILE_SUCCESS` on `set_variable`                     | `SemanticError`        | op name, pattern                                |

## Logging

Channel: `uefi.variable`.

* `INFO` on `VariableStore.__init__` — selected backend.
* `DEBUG` per successful op — op name, backend, name, guid, bytes (for
  writes).
* `WARNING` when a backend tolerates a non-zero `efi_status` per
  `allowed_error_codes`.
* `ERROR` on any `UefiVariableError` before re-raise, including the
  `efi_status` in hex.

The auth-descriptor builder logs only the timestamp and signer
certificate subject DN — never the signed bytes nor the variable `data`.

## Security Considerations

* `SignerKeyPair` is consumed in-memory only; the facade NEVER writes key
  material to disk or logs.
* `EfiVariableAuthentication2Descriptor` carries signed bytes; the
  descriptor bytes may be logged only at `DEBUG` under channel
  `uefi.variable.auth` and the payload is truncated to 64 bytes in the
  log.
* Timestamp must be strictly monotonic per UEFI §8.2.6; the library
  does not cache prior timestamps — callers supplying a `SignerKeyPair`
  are responsible for monotonicity across processes.
* Redfish backend credentials are held by the `UefiAgent`; this feature
  does not re-authenticate independently.
* On SSH-to-host, writes to `/sys/firmware/efi/efivars/` require root
  and the `immutable` attribute must be cleared first; the host-OS
  backend performs `chattr -i` explicitly and re-applies `+i` on close.

## Testing Strategy

### Unit — `VariableAttributes`

* Round-trip every value 0x00..0xFF through `to_bytes` / `from_bytes`.
* Cross-field validation: each forbidden combination raises
  `ConfigurationError` with the right `details`.

### Unit — `build_authentication2_descriptor`

* Known-good test vector: name="PK", guid, attributes, data, key, cert
  → byte-for-byte match against a descriptor generated by the EDK2
  `Pkcs7Sign` tool.
* Timestamp null-pad bytes (`Pad1`, `Pad2`) are zero.
* Timezone encoding: `datetime.now(timezone(timedelta(hours=-8)))`
  serializes `TimeZone = -480`.
* Naive `datetime` raises `ConfigurationError`.

### Unit — `VariableStore`

* Backend selection from each `transport_kind`.
* `backend_override` bypasses selection.
* Every public method forwards `aspects` unchanged when non-None.
* `resolve_aspects` error paths propagate without modification.

### Contract — per-backend parity

With a single parametrized suite, the shell, host-OS, and Redfish
backends all pass:

* Read-after-write.
* Delete-then-read raises `UefiVariableError(EFI_NOT_FOUND)`.
* Enumerate-then-read round trip.
* `query_variable_info` returns non-negative fields.

For the authenticated-write path, shell and host-OS backends additionally
verify:

* `SignerKeyPair` path produces a descriptor that the backend accepts.
* Non-monotonic timestamp raises `UefiVariableError(EFI_SECURITY_VIOLATION)`.

### Fault injection

* Backend returns `EFI_BUFFER_TOO_SMALL` on first read, success on
  retry → `DO_UNTIL_SUCCESS` converges.
* Backend raises `TimeoutError` on connect → `DO_UNTIL_CONNECTION_FAILURE`
  raises `SemanticError`.

### Integration

* QEMU/OVMF smoke test that writes, enumerates, and deletes a
  non-authenticated variable through the shell backend.
* Linux VM smoke test through the host-OS backend using a test keypair
  for an authenticated variable.

## Dependencies on Other Features

* `1-aspects` — `UefiCmdAspects`, `resolve_aspects`, `ActionPattern`.
* `1-constants-and-types` — well-known GUIDs (`EFI_GLOBAL_VARIABLE_GUID`,
  `EFI_IMAGE_SECURITY_DATABASE_GUID`, `EFI_CERT_TYPE_PKCS7_GUID`).
* `1-exceptions` — `UefiVariableError`, `UefiTransportError`,
  `ConfigurationError`, `SemanticError`.
* `1-helpers` — UCS-2 encoder, `datetime_to_efi_time()`,
  `efi_time_to_datetime()`.
* `1-logging` — `uefi.variable` channel.
* `4-agent` — transport dispatch.
* `5-shell-commands` — `shell_setvar`, `shell_dmpstore` for the shell
  backend.

Downstream consumers: `5-boot-manager`, `5-secure-boot`, `6-boot-to-setup`,
`6-secure-boot-enrollment`.

Third-party dependency: `cryptography` (already in the project's
dependency set for other signing-adjacent features).
