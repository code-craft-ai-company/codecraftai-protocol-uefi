# Requirements — Redfish Transport

## Introduction

The Redfish Transport feature delivers a layer-2 HTTPS transport that reaches
a UEFI target via the DMTF Redfish REST API exposed by the target's BMC or
out-of-band management controller.  It is the primary *out-of-band* transport
in the library and the only transport that can deliver firmware capsules,
manipulate BIOS attributes, and mount a VirtualMedia ESP image without any
cooperation from a running OS on the target.

The transport implements the `IUefiTransport` protocol defined in
`1-interfaces`, consumes `UefiAspects` / `UefiCmdAspects` from `1-aspects`,
and uses the `requests` library for synchronous HTTP.  It handles Basic and
`X-Auth-Token` authentication (auto-renewing tokens on 401), defaults TLS
verification to `True`, polls `202 Accepted` TaskMonitor endpoints, and
caches response bodies briefly when an ETag is returned.

This feature has no dependencies outside of layer 1 and is a prerequisite for
the Redfish-backed flavors of `5-variable-services` (BIOS attribute set),
`5-boot-manager` (Boot override), `5-firmware-capsules` (UpdateService), and
`5-esp-file-ops` (VirtualMedia).

## Requirements

### Requirement 1 — Base URL and Service Root Discovery

**User Story:** As a test author, I want to point the transport at a BMC
base URL and have it discover the Redfish service-root endpoint, so that I
do not hard-code `/redfish/v1/` paths throughout my tests.

#### Acceptance Criteria

1. WHEN the caller constructs `UefiRedfishTransport(base_url="https://bmc.lab/", ...)`
   THEN the system SHALL treat `https://bmc.lab/redfish/v1/` as the service
   root.
2. WHEN `connect()` is invoked THEN the system SHALL issue a `GET` on the
   service root and SHALL cache the top-level resource map
   (`Systems`, `Chassis`, `Managers`, `UpdateService`, `SessionService`).
3. IF the `GET` on the service root returns a status other than 200 THEN the
   system SHALL raise `UefiTransportError` with `details` naming the status
   and the request URL.
4. IF the service root does not advertise `@odata.type` starting with
   `#ServiceRoot.` THEN the system SHALL raise `UefiTransportError`.

### Requirement 2 — Authentication (Basic and Session Token)

**User Story:** As a test author, I want to choose between Basic and
session-token auth and have the transport transparently renew expired
tokens, so that long-running test runs do not abort mid-execution on 401.

#### Acceptance Criteria

1. WHEN the caller passes `auth_mode="basic"` THEN the system SHALL attach
   an HTTP Basic `Authorization` header on every request.
2. WHEN the caller passes `auth_mode="session"` THEN the system SHALL `POST`
   to `/redfish/v1/SessionService/Sessions` at `connect()` to obtain an
   `X-Auth-Token` and SHALL attach it on every subsequent request.
3. WHEN any request returns 401 with session auth active THEN the system
   SHALL re-issue the session `POST`, capture a new token, and SHALL replay
   the original request exactly once.
4. IF the re-issued session `POST` itself returns 401 THEN the system SHALL
   raise `UefiTransportError` with `details` including both attempt bodies
   (credential material redacted).
5. WHEN `disconnect()` is invoked with an active session token THEN the
   system SHALL `DELETE` the session resource and SHALL discard the token
   even if the `DELETE` fails.

### Requirement 3 — TLS Verification Defaults

**User Story:** As a security-conscious integrator, I want TLS verification
on by default and only turnable off by an explicit flag, so that I do not
accidentally accept self-signed BMC certificates in production runs.

#### Acceptance Criteria

1. WHEN the caller omits `verify` THEN the system SHALL default to `True`.
2. WHEN the caller passes `verify=False` THEN the system SHALL disable
   certificate verification and SHALL emit a single `WARNING`-level log at
   `connect()` naming the base URL.
3. WHEN the caller passes `verify="/path/to/ca-bundle.pem"` THEN the system
   SHALL use that CA bundle for verification.
4. IF the caller passes `verify` with any other type THEN the system SHALL
   raise `ConfigurationError`.

### Requirement 4 — Mandatory Headers

**User Story:** As a conformance-minded integrator, I want every outbound
request to carry the headers the Redfish specification mandates, so that
strict service implementations do not 400 my requests.

#### Acceptance Criteria

1. WHEN any request is built THEN the system SHALL include
   `OData-Version: 4.0`.
2. WHEN any request is built THEN the system SHALL include
   `Accept: application/json`.
3. WHEN a request has a body THEN the system SHALL include
   `Content-Type: application/json`.
4. WHEN the caller supplies extra headers at call-time THEN the system SHALL
   merge them after the mandatory headers so that the mandatory headers win
   on collision.

### Requirement 5 — Task Monitor Polling for 202 Accepted

**User Story:** As a test author, I want long-running Redfish operations to
complete before my code proceeds, so that I do not race the BMC when writing
BIOS attributes or uploading capsules.

#### Acceptance Criteria

1. WHEN a request returns 202 with a `Location:` header THEN the system
   SHALL treat that URL as a TaskMonitor and SHALL poll it.
2. WHILE polling the system SHALL wait `aspects.completion_interval` seconds
   between polls.
3. WHEN the TaskMonitor returns a terminal `TaskState` (`Completed`,
   `Exception`, `Killed`, `Cancelled`) THEN the system SHALL stop polling.
4. WHEN the TaskMonitor returns a non-terminal state and
   `aspects.completion_timeout` has elapsed THEN the system SHALL raise
   `TimeoutError` with `details` naming the task URL, last observed
   `TaskState`, and elapsed time.
5. WHEN the TaskMonitor terminates in `Exception`, `Killed`, or `Cancelled`
   THEN the system SHALL raise `UefiTransportError` with the task `Messages`
   array copied into `details`.

### Requirement 6 — ETag Caching

**User Story:** As a performance-conscious test author, I want repeated
reads of the same resource to avoid refetching unchanged bodies, so that
tight read loops do not saturate the BMC.

#### Acceptance Criteria

1. WHEN a `GET` response carries an `ETag` header THEN the system SHALL
   cache the body keyed by request URL and SHALL store the ETag value.
2. WHEN a subsequent `GET` targets a cached URL THEN the system SHALL send
   `If-None-Match: <etag>`; on 304 the system SHALL return the cached body.
3. WHEN any non-`GET` request targets a URL with a cached ETag THEN the
   system SHALL evict that cache entry before sending the request.
4. WHEN the cache reaches `etag_cache_max_entries` (default 256) THEN the
   system SHALL evict entries in LRU order.

### Requirement 7 — Connection Retry Bounded by Aspects

**User Story:** As a test author, I want connect/reconnect retries to honor
the aspects contract, so that flaky BMCs do not immediately fail my test.

#### Acceptance Criteria

1. WHEN `connect()` is invoked with `aspects.action_pattern ==
   ActionPattern.DO_UNTIL_SUCCESS` THEN the system SHALL retry connection
   attempts every `aspects.connection_interval` seconds until either
   succeeding or `aspects.connection_timeout` elapses.
2. IF a soft failure occurs whose exception type is present in
   `aspects.allowed_connection_exceptions` THEN the system SHALL retry per
   the action pattern.
3. IF a soft failure occurs whose exception type is not in
   `aspects.allowed_connection_exceptions` THEN the system SHALL re-raise
   immediately.
4. WHEN `aspects.must_connect == True` THEN a connection failure SHALL raise
   `ConnectionError` immediately regardless of action pattern.
5. IF `aspects.action_pattern == ActionPattern.DO_WHILE_SUCCESS` THEN
   `connect()` SHALL raise `SemanticError`.

### Requirement 8 — Implements IUefiTransport

**User Story:** As a layer-3 integrator, I want the Redfish transport to be
structurally interchangeable with the other layer-2 transports, so that
layer 3 code does not branch on transport.

#### Acceptance Criteria

1. WHEN a `UefiRedfishTransport` instance is checked against
   `IUefiTransport` via `isinstance` THEN the system SHALL return `True`.
2. WHEN `transport_kind` is read THEN the system SHALL return
   `"redfish"`.
3. WHEN `IUefiTransport.send` / `recv` / `read_until` are invoked THEN the
   system SHALL raise `SemanticError` because byte-pipe primitives are not
   meaningful for a REST transport.

### Requirement 9 — High-Level REST Verbs

**User Story:** As a layer-5 feature author, I want a compact verb surface
on the transport so that Redfish-backed domain features do not each
reimplement HTTP plumbing.

#### Acceptance Criteria

1. WHEN `get(url, aspects=None)` is invoked THEN the system SHALL return a
   `RedfishResponse` with `status`, `headers`, and parsed `body` (dict or
   list) for 200/201/203 or the cached body for 304.
2. WHEN `patch(url, body, aspects=None)` is invoked THEN the system SHALL
   perform the PATCH, honor TaskMonitor polling per Requirement 5, and
   return the final `RedfishResponse`.
3. WHEN `post(url, body, aspects=None)` is invoked THEN the system SHALL
   perform the POST and honor TaskMonitor polling.
4. WHEN `delete(url, aspects=None)` is invoked THEN the system SHALL perform
   the DELETE and honor TaskMonitor polling.
5. WHEN `multipart_upload(url, fields, files, aspects=None)` is invoked THEN
   the system SHALL send a `multipart/form-data` POST with the declared
   fields and file streams and honor TaskMonitor polling.
6. IF any verb receives a non-2xx non-3xx response outside the 202/401
   special cases THEN the system SHALL raise `UefiTransportError` with
   status, URL, method, and body excerpt in `details`.

### Requirement 10 — Capsule Upload and VirtualMedia Mount Capability

**User Story:** As a firmware-update test author, I want the Redfish
transport to expose capsule-upload and VirtualMedia-mount helpers so that
layer-5 features do not need to hard-code Redfish paths.

#### Acceptance Criteria

1. WHEN `upload_capsule(path: Path, aspects=None)` is invoked THEN the
   system SHALL POST to `UpdateService@Actions/SimpleUpdate` or the
   multipart upload endpoint advertised by the service root and SHALL
   return the resulting task URI.
2. WHEN `mount_virtual_media(image_uri, inserted=True, write_protected=True, aspects=None)`
   is invoked THEN the system SHALL PATCH the managers' VirtualMedia
   subresource per DMTF DSP0266.
3. WHEN `unmount_virtual_media(aspects=None)` is invoked THEN the system
   SHALL PATCH `Inserted: False` on the active VirtualMedia slot.
4. IF the service root does not advertise `UpdateService` or `Managers`
   THEN these helpers SHALL raise `UefiTransportError` with `details` naming
   the missing resource.

### Requirement 11 — Connection Pool Ownership

**User Story:** As a long-running test author, I want the transport to reuse
one HTTP connection pool across requests, so that TCP/TLS overhead does not
dominate the measurement of individual calls.

#### Acceptance Criteria

1. WHEN the transport is constructed THEN the system SHALL own exactly one
   `requests.Session` for its lifetime.
2. WHEN `disconnect()` is invoked THEN the system SHALL call
   `Session.close()` and SHALL release the token.
3. WHEN the transport is used as `with UefiRedfishTransport(...) as t:`
   THEN the system SHALL call `connect()` on entry and `disconnect()` on
   exit.
4. IF the session is used after `disconnect()` THEN the system SHALL raise
   `SemanticError`.

### Requirement 12 — Aspects=None Passthrough

**User Story:** As a caller of the transport's verbs, I want to omit
`aspects` per call and have the transport fall back to the instance
default.

#### Acceptance Criteria

1. WHEN any public method is invoked with `aspects=None` THEN the system
   SHALL use the aspects supplied at construction.
2. IF no aspects object was supplied at construction and `aspects=None` is
   passed THEN the system SHALL raise `ConfigurationError`.
