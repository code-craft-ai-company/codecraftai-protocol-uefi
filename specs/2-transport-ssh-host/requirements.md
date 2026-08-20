# Requirements — SSH-to-Host Transport

## Introduction

The SSH-to-Host Transport feature delivers a layer-2 transport that reaches
a UEFI target by SSHing into a *booted* operating system on that target and
running OS-resident UEFI tooling (`efibootmgr`, `efivar`, `fwupdmgr`, reads
of `/sys/firmware/efi/*`).  It is the lowest-friction path for post-boot
inspection and for day-to-day integration testing where a full OOB
management loop would be overkill.

The transport is strictly post-boot — it cannot operate when the target is in
UEFI pre-boot or in a firmware setup screen, because there is no OS shell to
reach.  This limitation is explicit in the transport matrix below.

The transport reuses `codecraftai-protocol-ssh`'s `SshAgent` via *composition*,
not inheritance: the caller supplies a fully-constructed `SshAgent`, and this
transport holds a reference to it.  All SSH lifecycle concerns (key
negotiation, jump hosts, agent forwarding, connection retry) are delegated
to the existing library.  This transport adds only the UEFI-specific
capability probing, root-escalation policy, and `IUefiTransport` conformance
on top of `SshAgent.run_cmd`.

This feature has dependencies on layer 1 (`1-aspects`, `1-interfaces`,
`1-exceptions`) and on the external `codecraftai-protocol-ssh` package.

## Requirements

### Requirement 1 — Composition Over an Existing SshAgent

**User Story:** As a test author, I want to hand the transport an
`SshAgent` I have already configured, so that my existing SSH credential,
jump host, and key-agent wiring is preserved.

#### Acceptance Criteria

1. WHEN the caller constructs `UefiSshHostTransport(ssh_agent=agent, ...)`
   THEN the system SHALL store `agent` as the sole SSH mechanism and SHALL
   NOT open any paramiko session of its own.
2. IF the caller passes a non-`SshAgent` value THEN the system SHALL raise
   `ConfigurationError` with `details` naming the offending type.
3. IF `ssh_agent.verify_connectivity()` fails at transport `connect()` time
   THEN the system SHALL raise `UefiTransportError` with the underlying
   exception chained via `from`.
4. WHEN the transport is closed THEN the system SHALL NOT close the
   supplied `SshAgent` — lifecycle of the agent is owned by the caller.

### Requirement 2 — Capability Probing at Connect

**User Story:** As a domain-feature author, I want to know up front which
OS-side tools are available so that I can select the right code path per
target without trying every tool and catching failures.

#### Acceptance Criteria

1. WHEN `connect()` runs THEN the system SHALL probe for each of:
   `efibootmgr`, `efivar`, `efivarfs` mount readability/writability,
   `fwupdmgr`, and the presence of `/sys/firmware/efi/efivars/`.
2. WHEN probing completes THEN the system SHALL populate a public dict
   `.capabilities` with boolean keys `has_efibootmgr`, `has_efivar`,
   `has_fwupd`, `has_efivarfs_rw`, `has_efivarfs_ro`, `has_sysfs_efi`.
3. WHEN a probe command fails because the binary is absent THEN the system
   SHALL set the corresponding flag to `False` and SHALL NOT raise.
4. WHEN a probe command fails with a non-absence error (e.g., permission
   denied) THEN the system SHALL log at `WARNING` and SHALL set the flag
   to `False`.
5. WHEN probing completes THEN the system SHALL emit an `INFO` log
   enumerating the final capability dict for triage.

### Requirement 3 — Required-Capability Enforcement

**User Story:** As a caller of a feature that needs a specific tool, I want
the transport to refuse to run that feature cleanly with a helpful error
when the tool is missing, so that the failure is immediately actionable.

#### Acceptance Criteria

1. WHEN a caller invokes `require(capability_name)` with a name present in
   `.capabilities` and `True` THEN the system SHALL return normally.
2. WHEN a caller invokes `require(capability_name)` with a name present and
   `False` THEN the system SHALL raise `UefiTransportError` whose `details`
   include: the capability name, the expected package/binary, the target
   distribution hint (read from `/etc/os-release` at `connect()`), and the
   suggested install command.
3. IF the caller invokes `require(capability_name)` with a name not present
   in `.capabilities` THEN the system SHALL raise `SemanticError`.

### Requirement 4 — Sudo Escalation Policy

**User Story:** As a test author, I want commands that need root to
escalate non-interactively, so that my tests never hang on a password
prompt.

#### Acceptance Criteria

1. WHEN `run_cmd(command, as_root=False, ...)` is invoked THEN the system
   SHALL pass the command through to `SshAgent.run_cmd` unchanged.
2. WHEN `run_cmd(command, as_root=True, ...)` is invoked THEN the system
   SHALL prefix the command with `sudo -n --` so that sudo is
   non-interactive.
3. IF a `as_root=True` command's stderr contains the sudo password-prompt
   sentinel (`"a password is required"` or `"a terminal is required"` or
   `"sudo: a password is required"`) THEN the system SHALL raise
   `ConfigurationError` with `details` listing the target host, the
   command, and the sudoers remediation hint.
4. WHEN `as_root=True` and the effective user is already root THEN the
   system SHALL skip the `sudo -n --` prefix.

### Requirement 5 — Connection Retry Bounded by Aspects

**User Story:** As a test author, I want transport-level connect retries to
obey the aspects contract, so that flaky OS bring-up does not immediately
fail my test.

#### Acceptance Criteria

1. WHEN `connect()` is invoked with `aspects.action_pattern ==
   ActionPattern.DO_UNTIL_SUCCESS` THEN the system SHALL retry
   `ssh_agent.verify_connectivity()` every `aspects.connection_interval`
   until success or `aspects.connection_timeout` elapses.
2. IF a soft failure occurs whose exception type is in
   `aspects.allowed_connection_exceptions` THEN the system SHALL retry per
   the action pattern.
3. IF a soft failure occurs whose exception type is not in
   `aspects.allowed_connection_exceptions` THEN the system SHALL re-raise
   immediately.
4. WHEN `aspects.must_connect == True` THEN a connectivity failure SHALL
   raise `ConnectionError` immediately regardless of action pattern.
5. IF `aspects.action_pattern == ActionPattern.DO_WHILE_SUCCESS` THEN
   `connect()` SHALL raise `SemanticError`.
6. WHEN `aspects.action_pattern == ActionPattern.SINGLE_CONNECTED_CALL` and
   `self._connected` is `True` THEN `connect()` SHALL return without
   re-probing capabilities.

### Requirement 6 — Implements IUefiTransport

**User Story:** As a layer-3 integrator, I want the SSH-to-host transport
to be structurally interchangeable with the other layer-2 transports.

#### Acceptance Criteria

1. WHEN a `UefiSshHostTransport` instance is checked against
   `IUefiTransport` via `isinstance` THEN the system SHALL return `True`.
2. WHEN `transport_kind` is read THEN the system SHALL return
   `"ssh-host"`.
3. WHEN `IUefiTransport.send` / `recv` / `read_until` are invoked THEN the
   system SHALL raise `SemanticError` because byte-pipe primitives are not
   meaningful for a command-per-call transport.

### Requirement 7 — Command Execution Surface

**User Story:** As a layer-5 feature author, I want a compact command
surface on the transport that maps cleanly to `SshAgent.run_cmd`, so that I
do not reach around the transport to call `ssh_agent` directly.

#### Acceptance Criteria

1. WHEN `run_cmd(command, aspects=None, as_root=False)` is invoked THEN the
   system SHALL return a `HostCmdResult` with `status`, `stdout`, `stderr`,
   `duration_s`.
2. WHEN `file_pull(remote_path, local_path, aspects=None)` is invoked THEN
   the system SHALL delegate to `ssh_agent.file_pull`.
3. WHEN `file_push(local_path, remote_path, aspects=None)` is invoked THEN
   the system SHALL delegate to `ssh_agent.file_push`.
4. WHEN `read_efivar(name, vendor_guid, aspects=None)` is invoked THEN the
   system SHALL read the efivarfs file
   `/sys/firmware/efi/efivars/{name}-{vendor_guid}` and SHALL return the
   raw bytes with the 4-byte attribute header preserved.
5. WHEN `write_efivar(name, vendor_guid, payload, aspects=None)` is invoked
   THEN the system SHALL require `has_efivarfs_rw` and SHALL write via
   `sudo -n tee` with immutable-bit clearing when necessary.

### Requirement 8 — Distribution Detection

**User Story:** As an error-message consumer, I want suggested install
commands to reflect the target distribution, so that remediation is a
single copy-paste.

#### Acceptance Criteria

1. WHEN `connect()` runs THEN the system SHALL read `/etc/os-release` from
   the target.
2. WHEN `/etc/os-release` declares `ID=ubuntu` or `ID=debian` THEN
   `UefiTransportError` `details` for missing `efibootmgr` SHALL suggest
   `sudo apt-get install efibootmgr`.
3. WHEN `ID=fedora`, `ID=rhel`, `ID=centos` THEN the suggested command
   SHALL be `sudo dnf install efibootmgr`.
4. WHEN `ID=arch` THEN the suggested command SHALL be
   `sudo pacman -S efibootmgr`.
5. WHEN `/etc/os-release` cannot be read or `ID` is absent THEN the error
   SHALL fall back to the generic message
   `"install 'efibootmgr' using your distribution's package manager"`.

### Requirement 9 — Aspects=None Passthrough

**User Story:** As a caller of transport methods, I want to omit `aspects`
and have the transport fall back to the instance default.

#### Acceptance Criteria

1. WHEN any public method is invoked with `aspects=None` THEN the system
   SHALL use the aspects supplied at construction.
2. IF no aspects object was supplied at construction and `aspects=None` is
   passed THEN the system SHALL raise `ConfigurationError`.

### Requirement 10 — Action-Pattern Coverage

**User Story:** As a test author, I want every action pattern I use
elsewhere in the stack to also work for the SSH-to-host transport.

#### Acceptance Criteria

1. WHEN `connect(aspects=ActionPattern.SINGLE_CALL)` is invoked THEN the
   system SHALL perform exactly one connectivity probe.
2. WHEN `connect(aspects=ActionPattern.SINGLE_CONNECTED_CALL)` is invoked
   THEN the system SHALL skip connectivity probing when already connected.
3. WHEN `connect(aspects=ActionPattern.DO_UNTIL_SUCCESS)` is invoked THEN
   the system SHALL retry until the connectivity-timeout deadline.
4. WHEN `connect(aspects=ActionPattern.DO_UNTIL_CONNECTION_FAILURE)` is
   invoked on an already-connected transport THEN the system SHALL poll
   connectivity every `aspects.connection_interval` until a failure is
   observed and SHALL raise the first failure.
5. IF `aspects.action_pattern == ActionPattern.DO_WHILE_SUCCESS` THEN
   `connect()` SHALL raise `SemanticError`.

### Requirement 11 — Diagnostics and Logging

**User Story:** As an operator triaging failures, I want every connect,
capability-probe result, sudo-prompt detection, and `require()` refusal to
emit a structured log entry, so that I can grep the run log without
scraping stack traces.

#### Acceptance Criteria

1. WHEN `connect()` begins THEN the system SHALL emit `INFO` with the
   target host and the `SshAgent` instance id.
2. WHEN capability probing completes THEN the system SHALL emit `INFO`
   with the capability dict.
3. WHEN `require()` refuses THEN the system SHALL emit `WARNING` with the
   capability name and the suggested install command.
4. WHEN a sudo password prompt is detected THEN the system SHALL emit
   `ERROR` with the target host and the command (redacted of any
   credential-shaped tokens).
5. WHEN `disconnect()` is invoked THEN the system SHALL emit `DEBUG` with
   the total number of `run_cmd` calls made over the transport's lifetime.
