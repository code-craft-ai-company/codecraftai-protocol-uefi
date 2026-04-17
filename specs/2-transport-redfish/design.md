# Design — Redfish Transport

## Overview

`UefiRedfishTransport` is a layer-2 HTTPS transport that reaches a UEFI
target through its BMC or out-of-band management controller via the DMTF
Redfish REST API (DSP0266).  It implements `IUefiTransport`, honors the
measured-interop contract defined by `UefiAspects` (see `1-aspects`), and is
the exclusive pre-boot path for capsule delivery, BIOS-attribute manipulation,
and VirtualMedia ESP operations.

The implementation is built on `requests` for synchronous HTTPS.  A single
`requests.Session` object owns the TCP connection pool and the mutable
authentication state (Basic credentials or an `X-Auth-Token`).  The session
is wrapped by `UefiRedfishTransport`, which layers on: retry-bounded connect,
401 auto-renewal, mandatory Redfish headers, TaskMonitor polling for
`202 Accepted`, and a small ETag-based body cache.

All retry semantics are driven by the caller-supplied `UefiAspects` — no
decorator-based retry is used (per the coding standards in
`/Users/myron/repos/CLAUDE.md`).

## Architecture

```
automojo.protocols.uefi.transport.redfish/
    __init__.py            # re-exports UefiRedfishTransport, RedfishResponse
    redfishtransport.py    # UefiRedfishTransport (IUefiTransport)
    auth.py                # BasicAuth, SessionAuth, token lifecycle
    headers.py             # mandatory header assembly
    taskmonitor.py         # 202 TaskMonitor polling loop
    etagcache.py           # LRU ETag body cache
    response.py            # RedfishResponse dataclass
    capsule.py             # upload_capsule helper
    virtualmedia.py        # mount/unmount VirtualMedia helpers
    serviceroot.py         # service-root discovery + resource map
```

Dependency direction (nothing imports upward):

```
redfishtransport.py ──▶ auth.py
                    ──▶ headers.py
                    ──▶ taskmonitor.py
                    ──▶ etagcache.py
                    ──▶ response.py
                    ──▶ serviceroot.py
                    ──▶ capsule.py       ──▶ taskmonitor.py
                    ──▶ virtualmedia.py  ──▶ taskmonitor.py
```

## Components and Interfaces

### `UefiRedfishTransport` (redfishtransport.py)

Public constructor:

```python
class UefiRedfishTransport(IUefiTransport):
    def __init__(
        self,
        base_url: str,
        credential: UefiRedfishCredential,
        auth_mode: str = "session",
        verify: Union[bool, str] = True,
        etag_cache_max_entries: int = 256,
        aspects: Optional[UefiAspects] = None,
    ) -> None:
        super().__init__()
        self._base_url = base_url.rstrip("/")
        self._credential = credential
        self._auth_mode = auth_mode
        self._verify = verify
        self._aspects = aspects if aspects is not None else DEFAULT_ASPECTS
        self._session: Optional[requests.Session] = None
        self._auth: Optional[_RedfishAuth] = None
        self._service_root: Optional[dict] = None
        self._etag_cache = EtagCache(max_entries=etag_cache_max_entries)
        self._connected = False
        self._reconnect_count = 0
        return
```

Public methods (satisfying `IUefiTransport`):

```python
    def connect(self, aspects: Optional[UefiAspects] = None) -> None: ...
    def disconnect(self) -> None: ...
    def get(self, url: str, aspects: Optional[UefiAspects] = None) -> RedfishResponse: ...
    def patch(self, url: str, body: dict, aspects: Optional[UefiAspects] = None) -> RedfishResponse: ...
    def post(self, url: str, body: Optional[dict] = None, aspects: Optional[UefiAspects] = None) -> RedfishResponse: ...
    def delete(self, url: str, aspects: Optional[UefiAspects] = None) -> RedfishResponse: ...
    def multipart_upload(self, url: str, fields: dict, files: dict, aspects: Optional[UefiAspects] = None) -> RedfishResponse: ...
    @property
    def is_connected(self) -> bool: ...
    @property
    def transport_kind(self) -> str:
        rtnval = "redfish"
        return rtnval
```

Byte-pipe primitives (`send`, `recv`, `read_until`) raise `SemanticError`
because they are not meaningful for a REST transport.

### `_RedfishAuth` Hierarchy (auth.py)

```python
class _RedfishAuth(abc.ABC):
    @abc.abstractmethod
    def apply(self, request_kwargs: dict) -> None: ...
    @abc.abstractmethod
    def handle_401(self, session: requests.Session, base_url: str) -> bool: ...
    @abc.abstractmethod
    def teardown(self, session: requests.Session, base_url: str) -> None: ...


class BasicAuth(_RedfishAuth): ...
class SessionAuth(_RedfishAuth):
    # owns _token, _session_uri, _last_renew_ts
    ...
```

`handle_401` on `SessionAuth` re-`POST`s to `SessionService/Sessions`,
captures the new `X-Auth-Token`, and returns `True`.  On `BasicAuth` it
returns `False` (caller must surface the 401).

### `taskmonitor.py`

```python
def poll_task_monitor(
    session: requests.Session,
    monitor_url: str,
    aspects: UefiAspects,
    logger: logging.Logger,
) -> RedfishResponse:
    deadline = time.monotonic() + aspects.completion_timeout
    last_state: Optional[str] = None
    while True:
        resp = session.get(monitor_url, headers=MANDATORY_HEADERS, verify=...)
        last_state = resp.json().get("TaskState")
        if last_state in TERMINAL_STATES:
            if last_state == "Completed":
                return build_response(resp)
            err_msg_lines = [...]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiTransportError(err_msg, details=[...])

        if time.monotonic() >= deadline:
            err_msg_lines = [...]
            err_msg = os.linesep.join(err_msg_lines)
            raise TimeoutError(err_msg)

        time.sleep(aspects.completion_interval)
```

Terminal states: `{"Completed", "Exception", "Killed", "Cancelled",
"Suspended", "Interrupted"}`.  Non-terminal states: `{"New", "Starting",
"Running", "Pending", "Stopping", "Service"}`.

### `etagcache.py`

```python
class EtagCache:
    def __init__(self, max_entries: int) -> None: ...
    def get(self, url: str) -> Optional[tuple[str, dict]]: ...   # (etag, body)
    def put(self, url: str, etag: str, body: dict) -> None: ...
    def evict(self, url: str) -> None: ...
```

Backed by `collections.OrderedDict` for LRU behavior.  Non-GET verbs call
`evict(url)` before sending.

### `RedfishResponse` (response.py)

```python
@dataclass(frozen=True)
class RedfishResponse:
    status: int
    headers: dict
    body: Optional[Union[dict, list]]
    from_cache: bool
    task_url: Optional[str]
```

### Connection Loop

Mirrors `UefiSerialTransport.connect()` in structure:

```python
def connect(self, aspects: Optional[UefiAspects] = None) -> None:
    effective = resolve_aspects(aspects, self._aspects)
    if effective.action_pattern == ActionPattern.DO_WHILE_SUCCESS:
        err_msg = "DO_WHILE_SUCCESS is not meaningful for connect()."
        raise SemanticError(err_msg)

    if effective.action_pattern == ActionPattern.SINGLE_CONNECTED_CALL and self._connected:
        return

    deadline = time.monotonic() + effective.connection_timeout
    attempt_count = 0
    last_exc: Optional[BaseException] = None

    while True:
        attempt_count += 1
        try:
            self._open_session_and_discover()
            self._connected = True
            return
        except Exception as xcpt:
            last_exc = xcpt
            if not isinstance(xcpt, tuple(effective.allowed_connection_exceptions)):
                raise
            if effective.must_connect:
                raise ConnectionError(...) from xcpt
            if effective.action_pattern == ActionPattern.SINGLE_CALL:
                raise
            if time.monotonic() >= deadline:
                err_msg_lines = [...]
                err_msg = os.linesep.join(err_msg_lines)
                raise TimeoutError(err_msg) from last_exc
        time.sleep(effective.connection_interval)
```

## Data Models

### Mandatory Outbound Headers

| Header           | Value                |
|------------------|----------------------|
| `OData-Version`  | `4.0`                |
| `Accept`         | `application/json`   |
| `Content-Type`   | `application/json` (when body is present) |
| `Authorization`  | Basic — when `BasicAuth` active           |
| `X-Auth-Token`   | — when `SessionAuth` active               |

### `TaskState` Classification

| Terminal states                                       | Non-terminal states                       |
|-------------------------------------------------------|-------------------------------------------|
| `Completed`, `Exception`, `Killed`, `Cancelled`,      | `New`, `Starting`, `Running`, `Pending`,  |
| `Suspended`, `Interrupted`                            | `Stopping`, `Service`                     |

### Service-Root Resource Map

On connect the transport stores a dict of the form:

```python
{
    "Systems": "/redfish/v1/Systems",
    "Chassis": "/redfish/v1/Chassis",
    "Managers": "/redfish/v1/Managers",
    "UpdateService": "/redfish/v1/UpdateService",
    "SessionService": "/redfish/v1/SessionService",
    "Registries": "/redfish/v1/Registries",
}
```

## Measured Interop Contract

`UefiRedfishTransport` consumes the following fields from `UefiAspects`:

| Field                             | Consumed By                           |
|-----------------------------------|---------------------------------------|
| `action_pattern`                  | `connect()` retry-loop shape          |
| `connection_timeout`              | `connect()` overall deadline          |
| `connection_interval`             | `connect()` sleep between attempts    |
| `allowed_connection_exceptions`   | `connect()` soft-failure filter       |
| `must_connect`                    | `connect()` hard-fail switch          |
| `completion_timeout`              | TaskMonitor overall deadline          |
| `completion_interval`             | TaskMonitor poll sleep                |
| `inactivity_timeout`              | per-request socket-level timeout      |
| `inactivity_interval`             | forwarded; not acted on here          |
| `logger`                          | all log emission                      |
| `logging_pattern`                 | request-result log selector           |

Supported `ActionPattern` values on `connect()`:

* `SINGLE_CALL`, `SINGLE_CONNECTED_CALL`, `DO_UNTIL_SUCCESS`,
  `DO_UNTIL_CONNECTION_FAILURE`.
* `DO_WHILE_SUCCESS` — raises `SemanticError`.

Per-verb methods (`get` / `patch` / `post` / `delete` / `multipart_upload`)
all support `SINGLE_CALL` (the default when no retry is implied) and rely on
layer 4's action-pattern engine for retry loops — the transport itself does
not retry verbs.  The one exception is the internal 401-retry-once, which is
an auth concern, not a retry pattern.

## Error Handling

| Condition                                                 | Exception              | Included in `details`                           |
|-----------------------------------------------------------|------------------------|-------------------------------------------------|
| Service-root GET returns non-200                          | `UefiTransportError`   | status, URL                                     |
| Service-root `@odata.type` not `#ServiceRoot.*`           | `UefiTransportError`   | observed `@odata.type`                          |
| Session `POST` 401 then 401 on retry                      | `UefiTransportError`   | redacted bodies of both attempts                |
| Invalid `verify` type                                     | `ConfigurationError`   | passed type                                     |
| `connection_timeout` elapsed                              | `TimeoutError`         | base URL, attempts, elapsed, last exc           |
| `must_connect=True` + connect failure                     | `ConnectionError`      | base URL, last exc                              |
| Non-2xx non-3xx response (outside 202/401 handling)       | `UefiTransportError`   | status, URL, method, body excerpt               |
| TaskMonitor terminal-failure state                        | `UefiTransportError`   | task URL, `TaskState`, `Messages[]`             |
| TaskMonitor completion_timeout elapsed                    | `TimeoutError`         | task URL, last `TaskState`, elapsed             |
| `send`/`recv`/`read_until` called on a REST transport     | `SemanticError`        | method name                                     |
| Missing `UpdateService` / `Managers` in service root      | `UefiTransportError`   | missing resource                                |
| Use after `disconnect()`                                  | `SemanticError`        | `transport_kind`, reason                        |

`SemanticError` escapes unchanged per the coding standards.

## Logging

All log lines route to channel `uefi.transport.redfish`:

* `INFO` — connect begin, connect success, disconnect, token renewal.
* `WARNING` — `verify=False`, 401 auto-renewal, TaskMonitor failure,
  retry due to `allowed_connection_exceptions` match.
* `DEBUG` — every request URL+method+status, ETag cache hit/miss.

The `Authorization` header and the `X-Auth-Token` header are **never**
logged.  A helper `redact_headers(headers: dict) -> dict` replaces those
keys' values with the sentinel `"***REDACTED***"` before any log emission.
Request bodies that contain a `"Password"` field are likewise redacted.

## Security Considerations

* TLS verification defaults to `True`.  Disabling it emits a `WARNING`
  log at every `connect()` so that a disabled-verify configuration is
  visible in every run log.
* Credentials are held on the `UefiRedfishCredential` object and never
  serialized via `__repr__` (enforced by `1-credentials`).
* Session tokens are treated as credentials — redacted in logs and wiped
  from memory on `disconnect()`.
* The transport honors `REQUESTS_CA_BUNDLE` when `verify=True` and no
  explicit CA bundle path is provided, matching `requests`' default.
* Multipart capsule uploads stream from disk rather than loading the whole
  capsule into memory — a 256 MiB capsule does not pin 256 MiB of RSS.

## Testing Strategy

### Unit tests (`test_redfishtransport.py`)

* Service-root discovery happy path and the two failure paths.
* Basic vs. Session auth header construction.
* 401 auto-renewal — session expires, transport re-POSTs, replays original
  request exactly once.
* Double-401 raises `UefiTransportError`.
* `verify=True` / `verify=False` / `verify=<path>` / `verify=<bad type>`
  all covered.
* Mandatory headers present on every verb.
* Caller-supplied headers merged with mandatory headers (mandatory wins).

### Unit tests (`test_taskmonitor.py`)

* Happy path — `Running` → `Completed`.
* Timeout path — `Running` indefinitely raises `TimeoutError` once
  `completion_timeout` elapses.
* Terminal-failure path — `Exception`, `Killed`, `Cancelled` each raise
  `UefiTransportError` with `Messages` in `details`.

### Unit tests (`test_etagcache.py`)

* Cache hit returns stored body when 304 received.
* Cache miss on first GET.
* Non-GET evicts the URL entry.
* LRU eviction at `max_entries + 1`.

### Unit tests (`test_capsule.py` / `test_virtualmedia.py`)

* `upload_capsule` selects multipart when the service advertises it,
  falls back to `SimpleUpdate` otherwise.
* `mount_virtual_media` PATCHes the expected subresource.
* `unmount_virtual_media` PATCHes `Inserted: False`.
* Missing-resource paths raise `UefiTransportError`.

### Integration-style tests

* `responses`-library-backed fake Redfish server covering the full happy
  path (service root → session → BIOS GET → BIOS PATCH → TaskMonitor →
  disconnect).
* Deliberately slow fake endpoint to exercise `completion_timeout`.
* Self-signed-cert fixture to exercise `verify=False`.

### Coverage target

95% line coverage.  Sockets-level error branches in `requests.Session` are
exempted.

## Dependencies on Other Features

* **1-aspects** — `UefiAspects`, `ActionPattern`, `LoggingPattern`,
  `resolve_aspects`, `DEFAULT_ASPECTS`.
* **1-credentials** — `UefiRedfishCredential`.
* **1-exceptions** — `UefiTransportError`, `ConfigurationError` (shared),
  `SemanticError` (shared).
* **1-interfaces** — `IUefiTransport`.
* **1-logging** — `get_uefi_logger("uefi.transport.redfish")`.
* **1-constants-and-types** — Redfish path literals, terminal-state set.

External:

* `requests` (≥ 2.32).
* `urllib3` (pinned indirectly via `requests`).

## Transport Matrix

| Capability                         | Redfish                                       |
|------------------------------------|-----------------------------------------------|
| Pre-boot (no OS required)          | Yes (BMC is independent of host OS)           |
| Post-boot (OS running)             | Yes                                           |
| UEFI variable read                 | Limited (BIOS attributes subset only)         |
| UEFI variable write                | Limited (BIOS attributes subset only)         |
| Capsule delivery                   | Yes (UpdateService `SimpleUpdate` / multipart)|
| Boot-option management             | Yes (`/Systems/{id}/Boot`)                    |
| ESP file operations                | Yes (VirtualMedia ISO/IMG mount)              |
| System reset                       | Yes (`ComputerSystem.Reset`)                  |
| Firmware setup navigation          | No (HII is out of band from Redfish)          |
| Required operator credentials      | BMC credentials (basic or session token)      |
| Bandwidth                          | Moderate (HTTPS/JSON)                         |
