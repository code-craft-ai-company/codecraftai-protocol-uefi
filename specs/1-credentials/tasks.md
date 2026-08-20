# Tasks — Credentials

- [ ] 1. Create the package skeleton at
      `source/packages/ccai/protocols/uefi/credentials/`.
  - [ ] 1.1 Add `__init__.py` that re-exports the three credential classes
        plus the `ALLOWED_BAUD`, `ALLOWED_FLOW_CONTROL`, and
        `ALLOWED_IPMI_INTERFACES` constants.
  - [ ] 1.2 Populate `__all__`.
  - [ ] 1.3 Add a module-level docstring summarizing the frozen-dataclass
        convention and the redaction contract.
  - References: Requirement 11.

- [ ] 2. Author `_redaction.py`.
  - [ ] 2.1 Define `SENSITIVE_REDACTED = "<redacted>"`.
  - [ ] 2.2 Implement `_redacted(value)` that returns the token when value
        is not `None` and the string `"None"` otherwise.
  - [ ] 2.3 Add a Sphinx-style docstring.
  - References: Requirement 6.

- [ ] 3. Implement `uefiserialcredential.py`.
  - [ ] 3.1 Declare the frozen dataclass with fields `baud`,
        `flow_control`, `login`, `password`, `allow_partial_login`.
  - [ ] 3.2 Implement `validate()` that collects all issues into a list
        and raises `ConfigurationError` joined with `os.linesep`.
  - [ ] 3.3 Call `validate()` from `__post_init__`.
  - [ ] 3.4 Override `__repr__` and `__str__` to redact `password`.
  - References: Requirement 1, Requirement 4, Requirement 5,
        Requirement 6, Requirement 7.

- [ ] 4. Implement `uefiredfishcredential.py`.
  - [ ] 4.1 Declare the frozen dataclass with fields `url_base`,
        `username`, `password`, `token`, `ca_bundle`, `tls_verify`,
        `prefer_token`.
  - [ ] 4.2 Implement `validate()` covering the six rule violations
        enumerated in the requirements.
  - [ ] 4.3 Implement the `effective_auth_mode` property.
  - [ ] 4.4 Override `__repr__` and `__str__` to redact `password` and
        `token`.
  - References: Requirement 2, Requirement 4, Requirement 5,
        Requirement 6, Requirement 7, Requirement 9.

- [ ] 5. Implement `uefiipmicredential.py`.
  - [ ] 5.1 Declare the frozen dataclass with fields `host`, `user`,
        `password`, `interface`.
  - [ ] 5.2 Implement `validate()` covering empty `host`, empty `user`, and
        interface literal validation.
  - [ ] 5.3 Override `__repr__` and `__str__` to redact `password`.
  - References: Requirement 3, Requirement 4, Requirement 5,
        Requirement 6, Requirement 7.

- [ ] 6. Author unit tests for `UefiSerialCredential`.
  - [ ] 6.1 Construction with defaults.
  - [ ] 6.2 Every allowed `baud` and `flow_control`.
  - [ ] 6.3 Invalid `baud` raises `ConfigurationError` with the observed
        value named.
  - [ ] 6.4 Invalid `flow_control` raises `ConfigurationError`.
  - [ ] 6.5 Partial login without override raises `ConfigurationError`.
  - [ ] 6.6 Partial login with `allow_partial_login=True` is accepted.
  - [ ] 6.7 `__repr__` redacts `password` and shows `"None"` when unset.
  - [ ] 6.8 Attribute assignment raises `FrozenInstanceError`.
  - [ ] 6.9 Pickle round-trip preserves equality.
  - References: Requirement 1, Requirement 4, Requirement 6, Requirement 10.

- [ ] 7. Author unit tests for `UefiRedfishCredential`.
  - [ ] 7.1 Basic-auth construction returns `effective_auth_mode ==
        "basic"`.
  - [ ] 7.2 Token-only construction returns `effective_auth_mode ==
        "token"`.
  - [ ] 7.3 Both credentials + `prefer_token=True` returns `"token"`.
  - [ ] 7.4 Both credentials + `prefer_token=False` raises
        `ConfigurationError`.
  - [ ] 7.5 Neither raises `ConfigurationError`.
  - [ ] 7.6 `http://` + `tls_verify=True` raises `ConfigurationError`.
  - [ ] 7.7 `ca_bundle` + `tls_verify=False` raises `ConfigurationError`.
  - [ ] 7.8 `__repr__` redacts both `password` and `token`.
  - [ ] 7.9 Pickle round-trip.
  - References: Requirement 2, Requirement 9, Requirement 6, Requirement 10.

- [ ] 8. Author unit tests for `UefiIpmiCredential`.
  - [ ] 8.1 Valid construction.
  - [ ] 8.2 Empty `host` raises `ConfigurationError`.
  - [ ] 8.3 Empty `user` raises `ConfigurationError`.
  - [ ] 8.4 Invalid `interface` raises `ConfigurationError`.
  - [ ] 8.5 `__repr__` redacts `password`.
  - [ ] 8.6 Pickle round-trip.
  - References: Requirement 3, Requirement 4, Requirement 6, Requirement 10.

- [ ] 9. Author `test_credentials_common.py`.
  - [ ] 9.1 For every credential class, construct an instance with a known
        sensitive value and assert `repr(instance)` does not contain that
        plaintext.
  - [ ] 9.2 Assert `str(instance) == repr(instance)`.
  - [ ] 9.3 Assert `dataclasses.is_dataclass(cls)` and the dataclass is
        frozen.
  - References: Requirement 4, Requirement 6, Requirement 11.

- [ ] 10. Author `test_ssh_credential_passthrough.py`.
  - [ ] 10.1 Assert `SshCredential` is not re-declared in this package.
  - [ ] 10.2 Assert the `1-credentials` user-guide documents the SSH-to-host
        transport's use of `SshCredential` with its canonical dotted path.
  - References: Requirement 8.

- [ ] 11. Coverage gate.
  - [ ] 11.1 Enforce 100% line coverage for
        `ccai.protocols.uefi.credentials`.
  - [ ] 11.2 Add a CI step that fails on any coverage regression.
  - References: Testing Strategy.

- [ ] 12. Documentation.
  - [ ] 12.1 Author `userguide/20-04-uefi-credentials.rst` listing each
        credential class, its fields, and short examples per transport.
  - [ ] 12.2 Document the redaction contract and the token/password
        ambiguity rule in the same guide.
  - [ ] 12.3 Cross-link from `AGENTS.md` Features section to the guide.
  - References: Introduction, Requirement 6, Requirement 9.
