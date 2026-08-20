# Tasks — UefiChannel

- [ ] 1. Create package skeleton at
      `source/packages/ccai/protocols/uefi/channel/`.
  - [ ] 1.1 Add `__init__.py` that re-exports `UefiChannel`,
        `UefiShellChannel`, `UefiRedfishChannel`, `UefiHostOsChannel`.
  - [ ] 1.2 Add a module-level docstring summarizing the send/recv/
        recv_until/close unified surface.
  - References: Requirement 1.

- [ ] 2. Implement `uefichannel.py` (abstract base).
  - [ ] 2.1 Author the `UefiChannel` class with `__init__` initializing
        every instance variable (`_aspects`, `_logger`, `_channel_id`,
        `_closed`, `_buffer`) per coding standards.
  - [ ] 2.2 Implement `aspects`, `channel_id`, `closed` properties.
  - [ ] 2.3 Implement `send(data: bytes) -> int` including empty-data
        fast return and closed-channel guard.
  - [ ] 2.4 Implement `recv(size, timeout)` including buffer-first
        draining and zero-size fast return.
  - [ ] 2.5 Implement `recv_until(pattern, timeout)` with literal-
        bytes and compiled-regex matchers via `_compile_matcher`,
        deadline enforcement, and partial-bytes exception carry.
  - [ ] 2.6 Implement `close()` idempotent with suppressed-and-logged
        `_close_impl` failures.
  - [ ] 2.7 Mark `_send_impl`, `_recv_impl`, `_close_impl` as abstract
        by raising `AbstractMethodError` with the subclass name.
  - [ ] 2.8 Add Sphinx-style docstrings per repository coding
        standards.
  - References: Requirements 1, 2, 3, 4, 8, 9, 10, 11.

- [ ] 3. Implement `uefishellchannel.py`.
  - [ ] 3.1 Author `UefiShellChannel(UefiChannel)` holding a
        `UefiSerialTransport` or `UefiIpmiSolTransport` reference.
  - [ ] 3.2 Implement `_send_impl` to forward to `transport.write`
        without CR/LF normalization.
  - [ ] 3.3 Implement `_recv_impl` to forward to `transport.read` with
        the specified `size` and `timeout`.
  - [ ] 3.4 Implement `_close_impl` that clears the buffer and does NOT
        close the transport (client responsibility).
  - References: Requirement 5.

- [ ] 4. Implement `uefiredfishchannel.py`.
  - [ ] 4.1 Author `UefiRedfishChannel(UefiChannel)` with `shared_session`
        flag; build a dedicated `requests.Session` by default or scope
        over the transport's shared session.
  - [ ] 4.2 Implement `send_http(verb, path, json_body, aspects)` that
        dispatches through the scoped session with
        `aspects.completion_timeout`.
  - [ ] 4.3 Implement `poll_task(task_uri, aspects)` bounded by
        `aspects.completion_timeout` / `aspects.completion_interval`.
  - [ ] 4.4 Implement `_send_impl` to raise `SemanticError` directing
        callers to `send_http`.
  - [ ] 4.5 Implement `_recv_impl` to drain the last response body.
  - [ ] 4.6 Implement `_close_impl` that closes the dedicated session
        or detaches the header scope.
  - References: Requirement 6.

- [ ] 5. Implement `uefihostoschannel.py`.
  - [ ] 5.1 Author `UefiHostOsChannel(UefiChannel)` holding a
        `UefiSshHostTransport` and a lazily-opened `SshChannel`.
  - [ ] 5.2 Implement `ensure_open` that opens the sub-channel on first
        use via `self._transport.ssh_agent._create_client().connection.
        open_channel("session")`.
  - [ ] 5.3 Implement `_send_impl`, `_recv_impl` as thin forwarders to
        the wrapped `SshChannel`.
  - [ ] 5.4 Implement `_close_impl` that closes the wrapped
        `SshChannel`.
  - References: Requirement 7.

- [ ] 6. Author abstract-base unit tests
      (`test_uefichannel_base.py`).
  - [ ] 6.1 Empty-data `send` fast return.
  - [ ] 6.2 Zero-size `recv` fast return.
  - [ ] 6.3 Buffer-first draining on `recv`.
  - [ ] 6.4 `recv_until` with literal bytes.
  - [ ] 6.5 `recv_until` with compiled regex (bytes-typed).
  - [ ] 6.6 `recv_until` raises `ConfigurationError` for `timeout<=0`.
  - [ ] 6.7 `recv_until` raises `ConfigurationError` for string or
        int pattern.
  - [ ] 6.8 `recv_until` raises `TimeoutError` with `.partial`
        attribute on deadline expiry.
  - [ ] 6.9 `close()` idempotency.
  - [ ] 6.10 `close()` suppresses `_close_impl` exceptions with a
        WARNING log.
  - [ ] 6.11 `AbstractMethodError` from each `_impl` when called on
        the base.
  - References: Requirements 1, 2, 3, 4, 8, 11.

- [ ] 7. Author `UefiShellChannel` unit tests.
  - [ ] 7.1 `send` passes bytes through without normalization.
  - [ ] 7.2 `recv` returns exactly what the fake transport returned
        (CR-only, LF-only, CRLF).
  - [ ] 7.3 `close` clears buffer without closing transport.
  - References: Requirement 5.

- [ ] 8. Author `UefiRedfishChannel` unit tests.
  - [ ] 8.1 Dedicated-session mode closes session on `close`.
  - [ ] 8.2 Shared-session mode detaches headers without closing
        session.
  - [ ] 8.3 Header isolation between two channels on the same shared
        session.
  - [ ] 8.4 `send_http` uses `aspects.completion_timeout` as the
        request timeout.
  - [ ] 8.5 `_send_impl(b"...")` raises `SemanticError`.
  - [ ] 8.6 `poll_task` bounded by
        `aspects.completion_timeout`/`completion_interval`.
  - References: Requirement 6.

- [ ] 9. Author `UefiHostOsChannel` unit tests.
  - [ ] 9.1 `send`/`recv` forward to wrapped `SshChannel`.
  - [ ] 9.2 `close` closes the wrapped `SshChannel` and suppresses any
        exception with a WARNING log.
  - [ ] 9.3 `ensure_open` is idempotent — the sub-channel is opened
        exactly once.
  - References: Requirement 7.

- [ ] 10. Author inactivity-timeout integration test.
  - [ ] 10.1 Fake transport stalls forever; call `recv_until` with
        `aspects.inactivity_timeout=1.0`; expect `TimeoutError` with
        non-zero `.partial`.
  - [ ] 10.2 Verify `aspects.inactivity_interval` sleep observed
        between polls (via mocked `time.sleep`).
  - References: Requirement 3, Requirement 9.

- [ ] 11. Author large-response integration test.
  - [ ] 11.1 Stream 1 MiB through `UefiHostOsChannel` using a fake
        `SshChannel` that yields small chunks — verify every byte is
        delivered in-order.
  - References: Requirement 7.

- [ ] 12. Coverage gate.
  - [ ] 12.1 Configure pytest-cov for 95% line coverage across
        `ccai.protocols.uefi.channel`.
  - [ ] 12.2 Add CI step that fails on drop.
  - References: Testing Strategy.

- [ ] 13. Documentation.
  - [ ] 13.1 Author `userguide/20-03-uefi-channel.rst` with
        send/recv/recv_until examples per subclass and a diagram of
        the polling loop.
  - [ ] 13.2 Cross-link from `AGENTS.md` Features section.
  - References: Introduction.
