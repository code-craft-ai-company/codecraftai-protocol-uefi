# Requirements — Credentials

## Introduction

The Credentials feature provides per-transport credential dataclasses used by
every layer-2 transport in `automojo-uefi`.  It delivers three new types —
`UefiSerialCredential`, `UefiRedfishCredential`, and `UefiIpmiCredential` —
and re-uses the existing `automojo.credentials.sshcredential.SshCredential`
for the SSH-to-host transport.  Each credential class is a frozen dataclass
that validates itself in `__post_init__` by calling a `validate()` method,
raises `ConfigurationError` on any misconfiguration, redacts sensitive
fields from `__repr__`, and is effectively immutable after construction.

Token-based Redfish authentication is the common ambiguous case (both a
token and a password/user pair supplied).  The feature resolves ambiguity
with an explicit `prefer_token: bool` flag: if both are supplied and
`prefer_token` is `True`, the token wins; otherwise `validate()` raises.

The feature has no dependencies on other `automojo-uefi` features other
than `1-exceptions` (for `ConfigurationError` re-export, though the class
itself lives in the shared errors module) and the standard library.

## Requirements

### Requirement 1 — UefiSerialCredential Fields and Validation

**User Story:** As a test author, I want to describe a serial-console login
with baud, flow-control, and optional username/password, so that
firmware-gated shells can be unlocked automatically.

#### Acceptance Criteria

1. WHEN the caller constructs `UefiSerialCredential(baud, flow_control)` THEN
   the system SHALL accept it and set `login` and `password` to `None` by
   default.
2. WHEN the caller omits `baud` THEN the system SHALL default to `115200`.
3. WHEN the caller omits `flow_control` THEN the system SHALL default to
   `"none"`.
4. IF `baud` is not a member of the closed set
   `{9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600}` THEN the
   system SHALL raise `ConfigurationError`.
5. IF `flow_control` is not a member of `{"none", "xonxoff", "rtscts"}` THEN
   the system SHALL raise `ConfigurationError`.
6. IF `login` is provided but `password` is not (or vice versa) THEN the
   system SHALL raise `ConfigurationError` unless the caller explicitly sets
   `allow_partial_login=True`.
7. WHEN `__repr__` is invoked THEN the system SHALL render `password` as
   `"<redacted>"` and never include the plaintext value.

### Requirement 2 — UefiRedfishCredential Fields and Validation

**User Story:** As a test author, I want to describe a Redfish endpoint's
credentials (URL base, username/password or bearer token, CA bundle, TLS
verification) in a single object, so that transport construction does not
require me to pass five separate parameters.

#### Acceptance Criteria

1. WHEN the caller constructs `UefiRedfishCredential(url_base, username,
   password)` THEN the system SHALL accept it and default `token` to `None`,
   `ca_bundle` to `None`, `tls_verify` to `True`, and `prefer_token` to
   `False`.
2. IF `url_base` does not start with `"http://"` or `"https://"` THEN the
   system SHALL raise `ConfigurationError`.
3. IF `url_base` uses `"http://"` AND `tls_verify=True` THEN the system SHALL
   raise `ConfigurationError` because TLS verification is meaningless on
   plaintext HTTP.
4. IF neither `token` nor (`username` AND `password`) is provided THEN the
   system SHALL raise `ConfigurationError`.
5. IF both `token` AND `username`/`password` are provided AND `prefer_token`
   is `False` THEN the system SHALL raise `ConfigurationError` with
   `details` listing both authentication modes as the ambiguity.
6. IF both `token` AND `username`/`password` are provided AND `prefer_token`
   is `True` THEN the system SHALL accept the construction and treat the
   token as the primary credential.
7. IF `ca_bundle` is provided AND `tls_verify=False` THEN the system SHALL
   raise `ConfigurationError` because the bundle would be ignored.
8. WHEN `__repr__` is invoked THEN the system SHALL render `password` and
   `token` as `"<redacted>"`.

### Requirement 3 — UefiIpmiCredential Fields and Validation

**User Story:** As a test author, I want to describe an IPMI connection to
a BMC (host, user, password, interface) in one object, so that both the
`ipmitool` fallback and the `pyghmi` path receive consistent inputs.

#### Acceptance Criteria

1. WHEN the caller constructs `UefiIpmiCredential(host, user, password)` THEN
   the system SHALL accept it and default `interface` to `"lanplus"`.
2. IF `interface` is not a member of `{"lan", "lanplus"}` THEN the system
   SHALL raise `ConfigurationError`.
3. IF `host` is an empty string THEN the system SHALL raise
   `ConfigurationError`.
4. IF `user` is an empty string THEN the system SHALL raise
   `ConfigurationError`.
5. WHEN `__repr__` is invoked THEN the system SHALL render `password` as
   `"<redacted>"`.

### Requirement 4 — Frozen Dataclass Immutability

**User Story:** As a library consumer, I want credential objects to be
immutable after construction, so that a credential passed into a transport
cannot be mutated out from under it by a misbehaving caller.

#### Acceptance Criteria

1. WHERE any credential class defined by this feature is declared the system
   SHALL use `@dataclass(frozen=True, slots=True)` (or `frozen=True` alone
   when `slots=True` is unsupported).
2. IF a caller assigns to any attribute of a constructed credential THEN the
   system SHALL raise `dataclasses.FrozenInstanceError`.
3. WHEN `__eq__` is invoked on two credentials of the same type with
   identical fields THEN the system SHALL return `True`.
4. WHEN `__hash__` is invoked on any credential instance THEN the system
   SHALL return a value consistent with `__eq__`.

### Requirement 5 — `validate()` Idempotence and Explicit Call

**User Story:** As a library author, I want to re-run validation deliberately
(for example, after deserializing from JSON), so that I can re-verify a
credential without re-constructing it.

#### Acceptance Criteria

1. WHEN the caller invokes `credential.validate()` on an already-validated
   instance THEN the system SHALL succeed silently (idempotent).
2. WHEN the caller invokes `validate()` on an instance whose fields were
   produced by deserialization (dataclass unpickle or similar) THEN the
   system SHALL either accept the instance or raise `ConfigurationError` —
   the same contract as `__post_init__`.
3. WHERE `__post_init__` invokes `validate()` the system SHALL run validation
   exactly once per construction.

### Requirement 6 — Redaction in `__repr__` Always Applies

**User Story:** As a security reviewer, I want to be certain that a
credential object can never leak a plaintext secret into logs, so that
defensive redaction is not left to individual call sites.

#### Acceptance Criteria

1. WHEN `repr(credential)` is invoked on any credential defined by this
   feature THEN the system SHALL omit every sensitive field's plaintext
   value.
2. WHEN the sensitive-field value is `None` THEN the system SHALL render it
   as `"None"` rather than `"<redacted>"` so the absence is visible.
3. WHEN `str(credential)` is invoked THEN the system SHALL return the same
   redacted rendering as `__repr__`.

### Requirement 7 — ConfigurationError Multiline Details

**User Story:** As a diagnostician reading a setup-time failure, I want all
validation failures to render as a multi-line error message, so that
multiple independent issues can be reported in one exception.

#### Acceptance Criteria

1. WHEN `validate()` detects more than one configuration problem THEN the
   system SHALL collect all problems into a list of lines.
2. WHEN `validate()` raises `ConfigurationError` THEN the system SHALL join
   the collected lines with `os.linesep` before passing them to the
   exception.
3. WHERE a single problem is detected the system SHALL still construct the
   exception with a one-line message.
4. WHEN the caller reads `str(exc)` on the raised exception THEN the system
   SHALL show each problem on its own line.

### Requirement 8 — SSH-to-Host Reuses Existing SshCredential

**User Story:** As a library maintainer, I want the SSH-to-host transport to
reuse the existing `SshCredential` so that I do not fragment the credential
surface between two libraries.

#### Acceptance Criteria

1. WHERE the SSH-to-host transport accepts a credential the system SHALL
   accept an instance of
   `automojo.credentials.sshcredential.SshCredential`.
2. WHEN the caller passes any other type the system SHALL raise
   `TypeError` whose message lists the expected type's canonical dotted path.
3. WHERE this feature documents credential types the system SHALL name
   `SshCredential` alongside the three new types and point to the existing
   `automojo.credentials.sshcredential` module.

### Requirement 9 — Token vs. Password Disambiguation

**User Story:** As a test author using Redfish, I want to express "use the
token; ignore the username/password if present" in one flag, so that shared
credential fixtures with both forms can still be passed into a
token-preferring transport.

#### Acceptance Criteria

1. WHEN `UefiRedfishCredential(prefer_token=True, token=..., username=...,
   password=...)` is constructed THEN the system SHALL accept it and SHALL
   treat the token as primary.
2. WHEN a transport reads `credential.effective_auth_mode` THEN the system
   SHALL return the string `"token"` when the token is present and
   `prefer_token=True`, `"basic"` when only username/password are present,
   and SHALL raise `ConfigurationError` when neither condition holds.
3. WHEN `prefer_token=False` (default) AND only a token is supplied THEN the
   system SHALL still use the token (no ambiguity exists).

### Requirement 10 — Pickling and Serialization

**User Story:** As a distributed-test integrator, I want credentials to
pickle and unpickle losslessly for transport to a runner process, so that
fixtures can be shared across process boundaries.

#### Acceptance Criteria

1. WHEN any credential defined by this feature is pickled and unpickled THEN
   the system SHALL produce an object that compares equal to the original.
2. WHEN the unpickled instance's `validate()` is invoked THEN the system
   SHALL succeed (the frozen dataclass is still valid).
3. WHERE a credential contains a `ca_bundle` path the system SHALL pickle
   the path string, not the file contents.

### Requirement 11 — Stable Module Surface

**User Story:** As a consumer, I want the public surface exposed by this
feature to be exhaustively listed in `__all__`, so that adding a new
credential class is a visible diff.

#### Acceptance Criteria

1. WHEN `automojo.protocols.uefi.credentials` is imported THEN the system
   SHALL expose `__all__` listing the three new credential classes.
2. WHERE a helper is private the system SHALL prefix its name with a single
   underscore and SHALL NOT include it in `__all__`.
