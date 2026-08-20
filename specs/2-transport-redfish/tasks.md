# Tasks — Redfish Transport

- [ ] 1. Create package skeleton at
      `source/packages/ccai/protocols/uefi/transport/redfish/`.
  - [ ] 1.1 Add `__init__.py` re-exporting `UefiRedfishTransport` and
        `RedfishResponse`.
  - [ ] 1.2 Add a module-level docstring summarizing the measured-interop
        contract and citing DSP0266.
  - [ ] 1.3 Add the package to the dependency-direction test so it cannot
        import from layers 3+.
  - References: Requirement 8.

- [ ] 2. Implement `response.py` and `headers.py`.
  - [ ] 2.1 Author `RedfishResponse` frozen dataclass.
  - [ ] 2.2 Author `MANDATORY_HEADERS` constant and `build_headers(extra,
        has_body) -> dict` helper.
  - [ ] 2.3 Author `redact_headers(headers)` for safe logging.
  - [ ] 2.4 Add `test_headers.py` covering merge-order (mandatory wins) and
        redaction.
  - References: Requirement 4.

- [ ] 3. Implement `etagcache.py`.
  - [ ] 3.1 Author `EtagCache` with `get` / `put` / `evict` using
        `collections.OrderedDict`.
  - [ ] 3.2 Implement LRU eviction at `max_entries + 1`.
  - [ ] 3.3 Add `test_etagcache.py` covering hit / miss / evict-on-non-GET /
        LRU overflow.
  - References: Requirement 6.

- [ ] 4. Implement `auth.py`.
  - [ ] 4.1 Author `_RedfishAuth` ABC.
  - [ ] 4.2 Author `BasicAuth` — stateless `Authorization` header.
  - [ ] 4.3 Author `SessionAuth` — POSTs `SessionService/Sessions`,
        captures `X-Auth-Token`, renews on 401, DELETEs on teardown.
  - [ ] 4.4 Add `test_auth.py` covering token capture, 401 renewal, double-
        401 surfacing, and teardown-on-failed-DELETE suppression.
  - References: Requirement 2.

- [ ] 5. Implement `taskmonitor.py`.
  - [ ] 5.1 Author `poll_task_monitor` per the design.
  - [ ] 5.2 Define `TERMINAL_STATES` and `NON_TERMINAL_STATES` constants.
  - [ ] 5.3 Raise `UefiTransportError` with `Messages` in `details` on
        `Exception` / `Killed` / `Cancelled`.
  - [ ] 5.4 Raise `TimeoutError` with `details` on
        `completion_timeout` elapse.
  - [ ] 5.5 Add `test_taskmonitor.py` covering happy / timeout / failure-
        state paths.
  - References: Requirement 5.

- [ ] 6. Implement `serviceroot.py`.
  - [ ] 6.1 Author `discover_service_root(session, base_url) -> dict` that
        returns the resource map.
  - [ ] 6.2 Validate `@odata.type` starts with `#ServiceRoot.`.
  - [ ] 6.3 Add `test_serviceroot.py` covering both failure paths.
  - References: Requirement 1.

- [ ] 7. Implement `UefiRedfishTransport` in `redfishtransport.py`.
  - [ ] 7.1 Author the constructor — initialize every instance variable per
        the coding standards.
  - [ ] 7.2 Implement `connect()` honoring every `ActionPattern` value and
        raising `SemanticError` on `DO_WHILE_SUCCESS`.
  - [ ] 7.3 Implement `disconnect()` — DELETE session, close Session, wipe
        token; must be safe to call twice.
  - [ ] 7.4 Implement `get` / `patch` / `post` / `delete` /
        `multipart_upload` verbs; honor 401 auto-renew and TaskMonitor
        polling.
  - [ ] 7.5 Raise `SemanticError` on `send`, `recv`, `read_until`.
  - [ ] 7.6 Implement `is_connected` and `transport_kind` properties.
  - [ ] 7.7 Add `__enter__` / `__exit__`.
  - [ ] 7.8 Add Sphinx-style `:param:` / `:returns:` / `:raises:` docstrings
        on every public method per the repository coding standards.
  - References: Requirements 1, 2, 3, 4, 7, 8, 9, 11, 12.

- [ ] 8. Implement `capsule.py` and `virtualmedia.py`.
  - [ ] 8.1 Author `upload_capsule(transport, path, aspects)`; select
        multipart vs. `SimpleUpdate` based on service-root advertisement.
  - [ ] 8.2 Stream the capsule file from disk in chunks (do not load whole
        file into memory).
  - [ ] 8.3 Author `mount_virtual_media(transport, image_uri, ...)` and
        `unmount_virtual_media(transport, ...)` per DSP0266.
  - [ ] 8.4 Raise `UefiTransportError` when required resources absent.
  - [ ] 8.5 Add `test_capsule.py` and `test_virtualmedia.py`.
  - References: Requirement 10.

- [ ] 9. Structured logging wiring.
  - [ ] 9.1 Use `get_uefi_logger("uefi.transport.redfish")`.
  - [ ] 9.2 Always redact `Authorization` / `X-Auth-Token` before logging.
  - [ ] 9.3 Emit a `WARNING` log on every `connect()` when `verify=False`.
  - [ ] 9.4 Assert in tests that no credential-like value is ever logged.
  - References: Requirement 3.2, Logging section.

- [ ] 10. Author targeted action-pattern tests.
  - [ ] 10.1 `SINGLE_CALL` — one attempt, raise on failure.
  - [ ] 10.2 `SINGLE_CONNECTED_CALL` — no-op on already-connected.
  - [ ] 10.3 `DO_UNTIL_SUCCESS` — retry until deadline.
  - [ ] 10.4 `DO_UNTIL_CONNECTION_FAILURE` — liveness probe.
  - [ ] 10.5 `DO_WHILE_SUCCESS` — raises `SemanticError`.
  - References: Requirement 7.

- [ ] 11. Integration tests with a fake Redfish server.
  - [ ] 11.1 Build a `responses`-library-backed fake service exposing
        `/redfish/v1/`, `/SessionService/Sessions`,
        `/Systems/1/Bios`, `/UpdateService`, `/Managers/1/VirtualMedia/*`,
        and a TaskMonitor.
  - [ ] 11.2 Round-trip happy-path test (connect → BIOS GET → BIOS PATCH
        → TaskMonitor → disconnect).
  - [ ] 11.3 Self-signed-cert fixture for `verify=False` / `verify=<path>`
        coverage.
  - [ ] 11.4 Gate slow / long-running cases under an env var.
  - References: Requirements 1, 2, 3, 4, 5.

- [ ] 12. Documentation.
  - [ ] 12.1 Author a short usage guide at
        `userguide/20-03-uefi-transport-redfish.rst` covering auth mode
        selection, TLS verify best practices, and TaskMonitor timeout
        tuning.
  - [ ] 12.2 Cross-link from `AGENTS.md` Transports section.
  - References: Introduction.
