# Requirements — HII Forms Navigation

## Introduction

The HII Forms feature provides optional navigation of the firmware Setup
UI for scripted BIOS configuration.  It drives the firmware Setup menu
over a serial (or IPMI-SOL) console by recognizing the form structure
rendered to the text screen and simulating arrow-key / enter / escape
keystrokes.  It is deliberately decoupled from the HII binary package
format defined by UEFI §33–§36 — it does not parse HII IFR or HII strings,
it screen-scrapes the console.

The feature targets the four most commonly encountered Setup UI vendors:
AMI Aptio, Insyde H2O, Phoenix SecureCore, and EDK2 / tianocore reference
firmware (as found in OVMF builds).  Each vendor's screen shape differs
in characters used for borders, selection highlights, menu-item
enumeration, and status-line formatting.  A registry of `ScreenShape`
detectors matches captured screens to the correct adapter.

This feature depends on a serial-capable transport (typically
2-transport-serial or 2-transport-ipmi-sol), 4-agent's channel primitives
for key-send and screen-read, and the measured-interop aspects contract
for every public entry point.

## Requirements

### Requirement 1 — Screen Shape Detection

**User Story:** As a test author, I want the library to auto-detect which
firmware vendor's Setup UI is currently rendered so that I do not have to
select an adapter manually.

#### Acceptance Criteria

1. WHEN `HiiFormNavigator.detect_shape()` is called THEN the system SHALL
   capture the current screen and dispatch it through a registry of
   `ScreenShape` detectors per UEFI §33 Setup-UI conventions.
2. WHEN exactly one detector matches THEN the system SHALL return the
   matching `ScreenShape` instance.
3. IF zero detectors match THEN the system SHALL raise `UefiError` with
   the captured screen snapshot in `details`.
4. IF two or more detectors match THEN the system SHALL raise
   `UefiError` with a list of matching detector names in `details` so
   the caller can narrow the registry.
5. WHEN the caller constructs a navigator with an explicit `shape`
   argument THEN the system SHALL bypass detection and use the supplied
   shape.

### Requirement 2 — Menu Item Lookup by Label

**User Story:** As a test author, I want to locate a menu item by its
visible label so that I can write tests that read like the on-screen
prompts.

#### Acceptance Criteria

1. WHEN `navigator.find_item(label)` is called THEN the system SHALL
   search every item on the current screen (and its paginated
   continuations) for an exact-match label and return an `HiiFormItem`.
2. WHEN no exact match is found and `label` contains a Python regex
   wildcard (`?`, `*`, `[`, etc.) THEN the system SHALL compile
   `label` as a regex and return the first regex-matching item.
3. IF the screen contains no item matching `label` THEN the system SHALL
   raise `UefiError` with `details` including the captured screen
   snapshot and a list of all labels observed on the current screen.
4. WHERE the caller passes `case_sensitive=False` (default `True`) THEN
   the system SHALL match labels case-insensitively.

### Requirement 3 — Arrow-Key Navigation

**User Story:** As a test author, I want to navigate to a specific item
and actuate it without hand-assembling ANSI escape sequences.

#### Acceptance Criteria

1. WHEN `navigator.navigate_to(item)` is called THEN the system SHALL
   emit the sequence of arrow-key bytes computed from the current
   highlight position to the target item.
2. WHEN `navigator.select(item)` is called THEN the system SHALL
   navigate to the item and emit an ENTER keystroke.
3. IF the highlight does not land on the expected item after navigation
   THEN the system SHALL raise `UefiError` with `details` naming the
   expected and observed labels and the captured post-navigation
   screen.
4. WHERE the `ScreenShape` declares non-standard navigation keys (e.g.,
   TAB instead of DOWN-ARROW) THEN the system SHALL use the
   shape-specified key bytes.

### Requirement 4 — Boolean Toggle

**User Story:** As a test author, I want to flip a boolean Setup option
(Enabled/Disabled, On/Off, Yes/No) by label.

#### Acceptance Criteria

1. WHEN `navigator.set_boolean(label, value)` is called THEN the system
   SHALL locate the item by label, navigate to it, press SPACE or ENTER
   per shape convention to enter the value selector, and choose
   `Enabled`/`Disabled` (or the shape's synonyms) per `value`.
2. IF the target item is not a boolean (its value list is not a
   two-element Enabled/Disabled-like set) THEN the system SHALL raise
   `UefiError` with `details` naming the observed value list.
3. WHEN the post-selection screen displays the chosen value THEN the
   system SHALL return; otherwise it SHALL raise `UefiError` with
   `details` containing the captured screen.

### Requirement 5 — Enumerated Option Selection

**User Story:** As a test author, I want to set an enumerated Setup
option to a specific value from its list of choices.

#### Acceptance Criteria

1. WHEN `navigator.set_enum(label, choice)` is called THEN the system
   SHALL locate the item, open its value selector, navigate to the
   `choice` entry, and press ENTER.
2. IF `choice` is not one of the item's enumerated values THEN the
   system SHALL raise `UefiError` with `details` listing the valid
   choices observed.
3. WHEN the post-selection screen displays `choice` as the new value
   THEN the system SHALL return; otherwise it SHALL raise `UefiError`
   with the captured screen in `details`.

### Requirement 6 — Numeric Option Entry

**User Story:** As a test author, I want to set a numeric Setup option
to a specific integer so that I can validate numeric ranges and
persistence.

#### Acceptance Criteria

1. WHEN `navigator.set_numeric(label, value)` is called THEN the system
   SHALL locate the item, open its value editor per shape convention,
   clear the existing value, type `value` as ASCII digits, and press
   ENTER.
2. IF `value` is outside the item's `[min, max]` range (when the shape
   surfaces those via adjacent text) THEN the system SHALL raise
   `UefiError` with `details` naming the range and the offending
   value.
3. WHEN the post-entry screen displays `value` as the new value THEN
   the system SHALL return; otherwise it SHALL raise `UefiError` with
   `details` containing the captured screen.

### Requirement 7 — Form Entry and Exit

**User Story:** As a test author, I want to enter and exit Setup cleanly
so that my tests do not leave the firmware in an unsaved state.

#### Acceptance Criteria

1. WHEN `navigator.enter_setup()` is called THEN the system SHALL wait
   for the firmware to reach the Setup entry point (typically by
   pressing DEL or F2 per shape convention) and return once the main
   Setup form is detected.
2. IF `aspects.completion_timeout` elapses before the Setup form is
   observed THEN the system SHALL raise `TimeoutError` with `details`
   naming the last observed screen.
3. WHEN `navigator.exit_setup(save: bool)` is called with `save=True`
   THEN the system SHALL navigate to the Save & Exit form per shape
   convention, confirm the save prompt, and return.
4. WHEN `navigator.exit_setup(save=False)` is called THEN the system
   SHALL discard changes and exit.
5. IF the save/discard confirmation screen is not detected within
   `aspects.completion_timeout` after the exit keystroke THEN the
   system SHALL raise `TimeoutError` with the captured screen.

### Requirement 8 — Screen Capture Utility

**User Story:** As a test author debugging a navigation failure, I want
access to the raw captured screen so that I can see exactly what the
library saw when it failed.

#### Acceptance Criteria

1. WHEN `navigator.capture_screen()` is called THEN the system SHALL
   return the current screen as a `Screen` dataclass (text lines,
   cursor position, highlighted-item index if detectable).
2. WHEN any `UefiError` is raised by this feature THEN its `details`
   SHALL include a rendered screen snapshot suitable for inclusion in a
   failure report.
3. WHERE the caller passes `capture_on_exception=False` to the
   navigator constructor THEN the system SHALL omit screen captures
   from `details` (for extremely latency-sensitive callers).

### Requirement 9 — Vendor Shape Registry

**User Story:** As an integrator, I want to register a custom
`ScreenShape` for an OEM firmware that the library does not ship with.

#### Acceptance Criteria

1. WHEN `HiiFormNavigator.register_shape(shape)` is called THEN the
   system SHALL prepend the shape to the detection registry so that it
   is considered before shipped shapes.
2. WHEN an already-registered shape is registered again THEN the system
   SHALL log a WARNING on channel `uefi.hii` and not duplicate the
   entry.
3. WHEN the library is imported THEN the system SHALL have shipped
   detectors for at least AMI Aptio, Insyde H2O, Phoenix SecureCore,
   and tianocore/EDK2 reference firmware.

### Requirement 10 — SINGLE_CALL Action Pattern

**User Story:** As a test author, I want fast-fail behavior for a single
Setup interaction.

#### Acceptance Criteria

1. WHEN a public method is called with
   `aspects.action_pattern == ActionPattern.SINGLE_CALL` THEN the system
   SHALL perform the interaction exactly once and not retry on failure.

### Requirement 11 — DO_UNTIL_SUCCESS Action Pattern

**User Story:** As a test author scripting Setup on slow-rendering
firmware, I want screen-read + key-send retries so that my tests do not
flake on a single missed redraw.

#### Acceptance Criteria

1. WHEN a public method is called with
   `aspects.action_pattern == ActionPattern.DO_UNTIL_SUCCESS` THEN the
   system SHALL retry the interaction every
   `aspects.completion_interval` until success or
   `aspects.completion_timeout` elapses.
2. IF the timeout elapses THEN the system SHALL raise `TimeoutError`
   with the last captured screen in `details`.

### Requirement 12 — SINGLE_CONNECTED_CALL Action Pattern

**User Story:** As a test author on an IPMI-SOL link, I want connection
errors retried but semantic errors surfaced immediately.

#### Acceptance Criteria

1. WHEN a public method is called with
   `aspects.action_pattern == ActionPattern.SINGLE_CONNECTED_CALL` THEN
   the system SHALL retry only exceptions in
   `aspects.allowed_connection_exceptions`.

### Requirement 13 — Aspects=None Passthrough

**User Story:** As a caller, I want `aspects=None` to delegate to the
owning agent's instance default.

#### Acceptance Criteria

1. WHEN any public method is called with `aspects=None` THEN the system
   SHALL call `resolve_aspects(None, self._aspects)` and use the
   returned instance.
2. IF both the passed `aspects` and the instance default are `None`
   THEN the system SHALL raise `ConfigurationError`.

### Requirement 14 — Logging Channel

**User Story:** As an operator, I want HII navigation activity to log on
a dedicated channel so I can filter it independently.

#### Acceptance Criteria

1. WHEN any HII API emits a log line THEN the system SHALL emit it on
   the `uefi.hii` channel.
2. WHEN `aspects.logging_pattern` is `FAILURE_ONLY` THEN successful
   navigations SHALL NOT emit INFO-level lines.
3. WHEN `aspects.logging_pattern` is `SUCCESS_ONLY` THEN retryable
   failures SHALL NOT emit WARNING-level lines.

### Requirement 15 — Screen-Scrape Only

**User Story:** As a maintainer, I want the feature to be strictly
screen-scraping so that it does not implicitly depend on HII binary
parsing.

#### Acceptance Criteria

1. WHILE any HII navigator method is executing the system SHALL NOT
   import or invoke any HII binary package parser.
2. WHILE any HII navigator method is executing the system SHALL NOT
   request HII packages from the target via the HII Database Protocol
   (UEFI §33.2.9).
3. IF a caller attempts to access an attribute named `hii_packages` on
   the navigator THEN the system SHALL raise `SemanticError` because
   that facility is out of scope for this feature.
