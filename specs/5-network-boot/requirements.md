# Requirements — Network Boot

## Introduction

The Network Boot feature builds, installs, and triggers `Boot####` entries
for HTTP and PXE network boot.  It injects IPv4 and IPv6 URIs into the
`EFI_LOAD_OPTION` structure as URI device-path nodes, installs the resulting
variable via the 5-boot-manager feature, and triggers a one-shot boot via
the `BootNext` global variable.

The feature targets two UEFI-specified flows: PXE boot per UEFI §24.3 and
HTTP Boot per UEFI §24.7 (HTTP(S) boot over IPv4 and IPv6).  It delegates
URI device-path node construction to 5-device-path-codec and load-option
installation / BootOrder manipulation to 5-boot-manager.  Callers of this
feature are typically test authors exercising firmware network stacks,
provisioning engineers pinning a next-boot URL, or workflow features that
chain "boot to network → reboot → verify".

This feature depends on the measured-interop aspects contract via the
5-boot-manager calls it issues; it does not perform transport-level I/O
directly.

## Requirements

### Requirement 1 — HTTP Boot Option Construction

**User Story:** As a test author, I want to build an HTTP Boot load option
from a URI and a description so that I can exercise firmware HTTP Boot
without hand-assembling binary structures.

#### Acceptance Criteria

1. WHEN `build_http_boot_option(uri, description, ...)` is called with an
   HTTP or HTTPS URI THEN the system SHALL produce a
   `BootOption` whose `device_path` contains a URI device-path node per
   UEFI §10.3.5.23 and an end-of-path node terminator.
2. WHEN the URI scheme is `http://` THEN the system SHALL emit a URI
   device-path node without transport-security flags.
3. WHEN the URI scheme is `https://` THEN the system SHALL emit a URI
   device-path node and the installer SHALL ensure the firmware presents
   the resulting option with the correct TLS chain; verification of TLS
   trust is the firmware's responsibility, not this feature's.
4. IF the `uri` argument is not a `str` THEN the system SHALL raise
   `ConfigurationError` with `details` naming the observed type.
5. IF the `uri` scheme is not one of `http`, `https` THEN the system SHALL
   raise `ConfigurationError` with `details` listing supported schemes.
6. WHEN the caller provides an optional `mac_address` parameter THEN the
   system SHALL prepend a MAC-address device-path node per UEFI §10.3.4.10
   so that the firmware binds the boot attempt to a specific NIC.
7. WHEN the caller provides no `mac_address` THEN the system SHALL emit a
   URI-only path and rely on the firmware to choose the default interface.

### Requirement 2 — PXE Boot Option Construction (IPv4)

**User Story:** As a test author, I want to build a PXE-over-IPv4 load
option so that I can trigger legacy PXE boot flows on targets that
implement UEFI §24.3.

#### Acceptance Criteria

1. WHEN `build_pxe_boot_option_ipv4(description, mac_address, ...)` is
   called THEN the system SHALL produce a `BootOption` whose `device_path`
   contains a MAC-address node followed by an IPv4 device-path node per
   UEFI §10.3.4.11.
2. WHEN the caller omits the optional `local_ip` and `remote_ip` arguments
   THEN the system SHALL emit a zero-filled IPv4 node so the firmware
   selects addresses via DHCP.
3. WHEN the caller provides explicit `local_ip` or `remote_ip` THEN the
   system SHALL pack them into the IPv4 node's `LocalIpAddress` and
   `RemoteIpAddress` fields in big-endian byte order per UEFI §10.3.4.11.
4. IF `mac_address` is not a 6-byte value THEN the system SHALL raise
   `ConfigurationError` with `details` naming the observed length.

### Requirement 3 — PXE Boot Option Construction (IPv6)

**User Story:** As a test author, I want to build a PXE-over-IPv6 load
option so that I can exercise dual-stack firmware network boot per UEFI
§24.3.

#### Acceptance Criteria

1. WHEN `build_pxe_boot_option_ipv6(description, mac_address, ...)` is
   called THEN the system SHALL produce a `BootOption` whose `device_path`
   contains a MAC-address node followed by an IPv6 device-path node per
   UEFI §10.3.4.12.
2. WHEN the caller omits `local_ip` and `remote_ip` THEN the system SHALL
   emit a zero-filled IPv6 node so the firmware selects addresses via
   DHCPv6 / RA.
3. IF a provided IPv6 address is not representable in 16 bytes THEN the
   system SHALL raise `ConfigurationError` with `details` naming the
   offending field.
4. WHEN both IPv6 and HTTP variants exist for the same description THEN
   the system SHALL NOT silently alias them; each call produces its own
   `BootOption`.

### Requirement 4 — Load Option Installation

**User Story:** As a test author, I want the built load option installed
into firmware and registered in `BootOrder` so that the firmware
considers it at next boot.

#### Acceptance Criteria

1. WHEN `install(boot_option, slot=None, aspects)` is called THEN the
   system SHALL allocate the lowest free `Boot####` slot (or honor an
   explicit `slot`), invoke 5-boot-manager to write the
   `Boot{slot:04X}` variable, and return the assigned slot.
2. WHEN the caller passes `slot` that is already occupied THEN the system
   SHALL raise `UefiVariableError` with `details` naming the conflicting
   slot and its existing description.
3. WHEN `append_to_boot_order: bool = True` is passed (default) THEN the
   system SHALL append the new slot to `BootOrder` if it is not already
   present.
4. WHEN `append_to_boot_order=False` is passed THEN the system SHALL
   leave `BootOrder` unchanged.
5. IF 5-boot-manager rejects the write (e.g., read-only runtime, SetupMode
   mismatch, variable store full) THEN the system SHALL propagate the
   raised `UefiVariableError` without wrapping.

### Requirement 5 — One-Shot Boot Trigger

**User Story:** As a test author, I want to trigger a one-shot network
boot via `BootNext` so that the target boots the network option exactly
once without altering `BootOrder`.

#### Acceptance Criteria

1. WHEN `trigger_one_shot(slot, aspects)` is called THEN the system SHALL
   write the `BootNext` global variable to `slot` via 5-boot-manager per
   UEFI §3.3.
2. IF `slot` does not correspond to an existing `Boot####` variable THEN
   the system SHALL raise `UefiVariableError` with `details` naming the
   missing slot.
3. WHEN `trigger_one_shot` returns THEN the system SHALL NOT reboot the
   target; reboot is the caller's responsibility (typically via
   5-system-reset or 6-reboot-and-verify).
4. WHERE the caller passes `verify: bool = False` THEN the system SHALL
   NOT issue an immediate readback; `verify=True` triggers an immediate
   readback of `BootNext` to confirm the write took effect.

### Requirement 6 — Round-Trip Fidelity

**User Story:** As a maintainer, I want the built load option to
round-trip through encode → decode → render without loss so that
regressions in binary layout are caught in unit tests.

#### Acceptance Criteria

1. WHEN any `build_*_boot_option` output is encoded via
   `BootOption.encode()` and then decoded via `BootOption.decode(bytes)`
   THEN the decoded instance SHALL compare equal to the original.
2. WHEN the decoded option is rendered via
   `5-device-path-codec.to_text(device_path)` THEN the result SHALL be a
   UEFI §10.6 text form recognizable by `ConvertDevicePathFromText()`.
3. WHEN the rendered text is re-parsed via
   `5-device-path-codec.from_text(text)` THEN the result SHALL round-trip
   back to the original binary form.

### Requirement 7 — SINGLE_CALL Action Pattern

**User Story:** As a test author, I want one-shot install behavior so
that tests fail fast when the variable store is misbehaving.

#### Acceptance Criteria

1. WHEN any installation method is called with
   `aspects.action_pattern == ActionPattern.SINGLE_CALL` THEN the system
   SHALL perform the variable write exactly once.
2. IF the single write fails THEN the system SHALL raise immediately.

### Requirement 8 — DO_UNTIL_SUCCESS Action Pattern

**User Story:** As a test author, I want install-then-verify to retry
when racing against firmware variable-store housekeeping so that my
tests do not flake.

#### Acceptance Criteria

1. WHEN any installation method is called with
   `aspects.action_pattern == ActionPattern.DO_UNTIL_SUCCESS` THEN the
   system SHALL poll the write-then-read-back loop every
   `aspects.completion_interval` until success or
   `aspects.completion_timeout` elapses.
2. IF `aspects.completion_timeout` elapses THEN the system SHALL raise
   `TimeoutError` with `details` summarizing the last observed state.

### Requirement 9 — SINGLE_CONNECTED_CALL Action Pattern

**User Story:** As a test author operating over Redfish through a flaky
BMC, I want retryable connection failures to be handled automatically so
that I do not repeat the same boilerplate per call.

#### Acceptance Criteria

1. WHEN any installation method is called with
   `aspects.action_pattern == ActionPattern.SINGLE_CONNECTED_CALL` THEN
   the system SHALL retry only exceptions listed in
   `aspects.allowed_connection_exceptions`.
2. IF an application error is raised THEN the system SHALL NOT retry.

### Requirement 10 — Aspects=None Passthrough

**User Story:** As a caller, I want `aspects=None` to delegate to the
owning agent's instance default per the measured-interop contract.

#### Acceptance Criteria

1. WHEN any public method is called with `aspects=None` THEN the system
   SHALL call `resolve_aspects(None, self._aspects)` and use the returned
   instance.
2. IF both arguments are `None` THEN the system SHALL raise
   `ConfigurationError`.

### Requirement 11 — Load Option Attributes

**User Story:** As a test author, I want the built load option to carry
the correct UEFI attribute flags so that the firmware honors it exactly
as intended.

#### Acceptance Criteria

1. WHEN any `build_*_boot_option` is called THEN the system SHALL set
   `LOAD_OPTION_ACTIVE` (0x00000001) on the `Attributes` field per UEFI
   §3.1.3.
2. WHEN the caller passes `hidden=True` THEN the system SHALL set
   `LOAD_OPTION_HIDDEN` (0x00000008) in addition.
3. WHEN the caller passes `category_app=True` THEN the system SHALL set
   `LOAD_OPTION_CATEGORY_APP` (0x00000100); otherwise the category is
   `LOAD_OPTION_CATEGORY_BOOT` (0x00000000).
4. WHERE the caller provides `optional_data: bytes` THEN the system SHALL
   append those bytes to the load option's `OptionalData` region
   verbatim.

### Requirement 12 — Logging Channel

**User Story:** As an operator, I want network-boot operations to log on a
dedicated channel so I can filter them independently.

#### Acceptance Criteria

1. WHEN any network-boot API emits a log line THEN the system SHALL emit
   it on the `uefi.netboot` channel.
2. WHEN `aspects.logging_pattern == LoggingPattern.FAILURE_ONLY` THEN
   successful installs SHALL NOT emit INFO-level log lines.
3. WHEN `aspects.logging_pattern == LoggingPattern.SUCCESS_ONLY` THEN
   retryable failures SHALL NOT emit WARNING-level log lines.
