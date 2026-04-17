# Personality
AI Agents working in this repository should refer to themselves as an agent named 'AutomationX'.

# Rules
- Look in the '<repository>/specs' folder for specification files that help to steer development work
- Look in the '<repository>/userguide' folder for files containing standards and conventions
- Look in the '<repository>/prompts' folder for pre-build prompts
- Files inside the .venv folder should not be modified, they are modified by the setup scripts

# Project Overview
This repository provides `automojo-uefi`, a Python interop library for interacting with UEFI-based
devices from automation and test code.  It is modeled on the `automojo-protocol-ssh` library and
exposes the same `Agent` / `Session` / `Aspects` interop pattern so that UEFI targets can be driven
with the same behavioral controls (action patterns, retry, timeouts, logging) used across the
automojo ecosystem.

Because UEFI runs as firmware rather than as a network service, interop is performed over one of
several transports (serial console / UEFI Shell, Redfish, SSH-to-host for OS-resident UEFI tooling,
or IPMI SOL).  The library focuses on the subset of the UEFI specification that is useful for
automation: UEFI Shell command execution, variable services, boot manager control, device path
encoding, GPT parsing, EFI System Partition file operations, firmware capsule delivery, Secure
Boot key management, and system reset/power control.  Pure in-firmware concerns (EBC, driver
binding, in-firmware USB/PCI/network stacks) are consumed indirectly rather than reimplemented.

The canonical reference for feature scope is [specs/uefi-specification.md](specs/uefi-specification.md).
The canonical reference for the interop pattern is the sibling `automojo-protocol-ssh` repository.

# Features

## Architectural Scaffolding
Mirrors the `automojo-protocol-ssh` layout; every feature below rides on these primitives.

* `UefiBase` — shared state and generic run-pattern engine (`SINGLE_CALL`, `DO_UNTIL_SUCCESS`,
  `DO_WHILE_SUCCESS`) built on `TimeoutContext` and `MonitoredScope`.  Implements `ISystemContext`.
* `UefiAgent` — one-shot API that opens a transport per call and tears it down.
* `UefiSession` — persistent-transport API for chained commands.
* `UefiClient`, `UefiConnection`, `UefiChannel` — transport primitives.
* `UefiAspects`, `UefiCmdAspects` — `Freezable` behavior packages (action pattern, completion /
  connection / inactivity timeouts and intervals, logging pattern, allowed exceptions,
  `must_connect`).
* `UefiConst` — constants, `UefiJumpParams`, command templates, regular expressions.
* `UefiCredential` — per-transport credential types (serial, Redfish, SSH-to-BMC, IPMI).
* `UefiHelpers` — primitive parsing and low-level routines.

## Transports
* Serial-console transport with PTY-style prompt matching against the UEFI Shell.
* Redfish / REST-over-HTTPS transport for BMC-managed systems (UEFI spec §29.7).
* SSH-to-host transport (depends on `automojo-protocol-ssh`) for OS-resident UEFI tooling such as
  `efibootmgr`, `efivar`, `fwupd`, and `/sys/firmware/efi/*`.
* IPMI / Serial-Over-LAN transport for out-of-band console redirection.

## Core Interop Modules
* **UEFI Shell command execution** — `run_cmd` with prompt detection, exit-code parsing, and
  wrappers for `map`, `ls`, `cp`, `bcfg`, `setvar`, `dmpstore`, `reset`.
* **Variable services** (spec §8.2) — `GetVariable`, `SetVariable`, `GetNextVariableName`,
  `QueryVariableInfo` via OS passthrough or Shell.  Full attribute-flag support (NV/BS/RT/AW/TA).
* **Boot Manager control** (spec §3) — read / write `Boot####`, `BootOrder`, `BootNext`,
  `BootCurrent`, `Timeout`; parse `EFI_LOAD_OPTION`; boot-to-setup and boot-to-option-once.
* **Device Path codec** (spec §10) — binary ⇄ text round-trip for Hardware / ACPI / Messaging /
  Media / BBS node types (PCI, USB, SATA, NVMe, iSCSI, MAC, IPv4/6, URI, File Path).
* **GPT / MBR parsing** (spec §5) — header, partition entry array, protective MBR, CRC
  validation, partition-type GUIDs.
* **EFI System Partition file operations** (spec §13.3–13.5) — locate ESP, `file_push`,
  `file_pull`, `file_exists`, `directory`, `directory_tree` against `/EFI/**`.
* **System tables readout** (spec §4.6) — ESRT (§23.4), `EFI_MEMORY_ATTRIBUTES_TABLE`,
  `EFI_CONFORMANCE_PROFILE_TABLE`, ACPI RSDP, SMBIOS — parsed from OS exports or Shell commands.
* **Firmware update / capsules** (spec §23) — build `EFI_CAPSULE_HEADER` and FMP payloads, deliver
  via `UpdateCapsule` runtime call or mass-storage delivery (§8.5.5); read capsule report
  variables.
* **Secure Boot** (spec §32) — enroll and read PK / KEK / db / dbx with
  `EFI_VARIABLE_AUTHENTICATION_2` authenticated writes; inspect SetupMode, AuditMode,
  DeployedMode, and SecureBoot state.
* **Loaded Image metadata** (spec §9, §7.4) — parse PE/COFF UEFI images and their loader device
  paths.
* **Measured boot / TPM** (spec §38) — TPM event-log extraction and PCR-readback helpers.
* **HTTP / PXE boot orchestration** (spec §24.3, §24.7) — create `Boot####` entries for network
  boot and inject HTTP-boot URIs.
* **System reset / power control** (spec §8.5.1) — cold, warm, shutdown, and platform-specific
  resets with reconnect verification (pattern mirrors `SshAgent.reboot`).
* **HII / Setup forms** (spec §33–36) — scripted Setup-menu navigation over serial for BIOS
  configuration toggles.
* **Time services** (spec §8.3) — `GetTime` and `SetTime` wrappers.

## Explicitly Out of Scope
These are in-firmware concerns that cannot meaningfully be implemented by a Python interop
library and are consumed only indirectly:

* EFI Byte Code virtual machine (spec §22).
* UEFI compression algorithm (spec §19).
* Protocol-handler services and driver-binding APIs (spec §7.3, §11).
* In-firmware USB / PCI / SCSI / NVMe / ATA pass-through protocols (spec §13–17).
* In-firmware USB, Bluetooth, Wi-Fi, IPsec, and TLS stacks (spec §26–28).

# Testing Conventions
Use this section to describe the test framework that should be used for testing code in this repository.  You can instruct the Agent on how to write tests for this repository by using the
following helper paragraphs:

This repository utilizes the 'unittest' test framework.  Detailed information about how to
utilize the 'unittest' test framework with this code base are located in the [Testing Frameworks - UnitTest](userguide/10-04-01-testing-frameworks-unittest.rst)

This repository utilizes the 'pytest' test framework.  Detailed information about how to
utilize the 'pytest' test framework with this code base are located in the [Testing Frameworks - PyTest](userguide/10-04-01-testing-frameworks-pytest.rst)

This repository utilizes the 'testbase' test framework.  Detailed information about how to
utilize the 'testbase' test framework with this code base are located in the [Testing Frameworks - TestBase](userguide/10-04-01-testing-frameworks-testbase.rst)

# Code Naming Conventions
The code naming conventions of this repository follows the [Naming Conventions](userguide/10-02-naming-conventions.rst) document.


# Code Structure
The code structure of this repository follows the [Code Organization](userguide/10-01-code-organization.rst) document

Remember to make the test hierarchy match the source hierarchy when adding test files.  For example:

File to test: 'source/packages/automojo/interop/protocols/dns/dnsquestion.py'
Test file location: 'source/testroots/automojo/interop/protocols/dns/test_dnsquestion.py'

# Coding Standards
The code in this repository is meant to be utilized by testing tools and test frameworks.  Therefore, the code must prioritize:

*  debuggability over performance
*  readability over conciseness

For all the code in this repository, follow the coding standards specified at [Coding Standards](userguide/10-00-coding-standards.rst)


# Environment Setup

## Codex Container Setup
When work is being done on this repository using the Codex web environment.  Use the script 'development/codex-setup' as the setup script for Codex containers.

## Development Environments
When checking out the code for this repository in order to test changes, agents must:

* run the 'repository-setup/rehome-repository' script.  It creates a needed '.env' file.
* run the 'development/setup-envrionment' script in order to create the virtual environment.

# Project Specific Rules


# Pull Request Guidelines


# Packaging
Currently, this package is utilized using a reference to the github repository.  It is currently not published.
