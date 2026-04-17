# Design — HII Forms Navigation

## Overview

HII Forms provides console-driven navigation of firmware Setup UIs.
Rather than interpreting HII binary packages (UEFI §33–§36), this feature
screen-scrapes the serial console and simulates keystrokes — the same
approach a human operator takes when driving Setup over a terminal
emulator.  The screen-scrape strategy trades off: it cannot observe HII
form IDs or question GUIDs, but it works on any firmware that renders
Setup to a text console regardless of vendor HII implementation
differences.

The feature ships four reference `ScreenShape` detectors — AMI Aptio,
Insyde H2O, Phoenix SecureCore, and EDK2 / tianocore (OVMF).  Each
detector encapsulates border characters, highlight detection, menu-item
parsing, value-selector opening key, save-and-exit navigation path, and
the Setup-entry keystroke.  Integrators can register custom detectors
for OEM firmware that does not match any shipped shape.

## Architecture

```
automojo.protocols.uefi.hii/
    __init__.py            # re-exports HiiFormNavigator, ScreenShape, HiiFormItem, Screen
    screen.py              # Screen dataclass + screen capture helpers
    shapes/
        __init__.py
        ami.py             # AMI Aptio shape
        insyde.py          # Insyde H2O shape
        phoenix.py         # Phoenix SecureCore shape
        tianocore.py       # EDK2 / OVMF reference shape
        base.py            # ScreenShape abstract base
        registry.py        # Shape registry
    navigator.py           # HiiFormNavigator facade
    keystrokes.py          # Named key-byte constants (ANSI escape sequences)
    errors.py              # HII-specific UefiError subclasses (thin)
```

Dependency direction:

```
keystrokes.py ── screen.py ── shapes/base.py ─┬── shapes/ami.py
                                              ├── shapes/insyde.py
                                              ├── shapes/phoenix.py
                                              └── shapes/tianocore.py
shapes/registry.py ── navigator.py
```

## Components and Interfaces

### `HiiFormNavigator` (navigator.py)

```python
class HiiFormNavigator:
    """
        Screen-scraping navigator for UEFI Setup forms.

        Drives firmware Setup UI over a serial or IPMI-SOL console by
        recognizing form structure rendered to the text screen.
    """

    def __init__(
        self,
        agent: "UefiAgent",
        shape: Optional["ScreenShape"] = None,
        capture_on_exception: bool = True,
        aspects: Optional[UefiCmdAspects] = None,
    ) -> None:
        self._agent = agent
        self._shape = shape
        self._capture_on_exception = capture_on_exception
        self._aspects = aspects
        return

    def detect_shape(
        self,
        aspects: Optional[UefiCmdAspects] = None,
    ) -> "ScreenShape": ...

    def enter_setup(
        self,
        aspects: Optional[UefiCmdAspects] = None,
    ) -> None: ...

    def exit_setup(
        self,
        save: bool,
        aspects: Optional[UefiCmdAspects] = None,
    ) -> None: ...

    def capture_screen(
        self,
        aspects: Optional[UefiCmdAspects] = None,
    ) -> "Screen": ...

    def find_item(
        self,
        label: str,
        case_sensitive: bool = True,
        aspects: Optional[UefiCmdAspects] = None,
    ) -> "HiiFormItem": ...

    def navigate_to(
        self,
        item: "HiiFormItem",
        aspects: Optional[UefiCmdAspects] = None,
    ) -> None: ...

    def select(
        self,
        item: "HiiFormItem",
        aspects: Optional[UefiCmdAspects] = None,
    ) -> None: ...

    def set_boolean(
        self,
        label: str,
        value: bool,
        aspects: Optional[UefiCmdAspects] = None,
    ) -> None: ...

    def set_enum(
        self,
        label: str,
        choice: str,
        aspects: Optional[UefiCmdAspects] = None,
    ) -> None: ...

    def set_numeric(
        self,
        label: str,
        value: int,
        aspects: Optional[UefiCmdAspects] = None,
    ) -> None: ...

    @classmethod
    def register_shape(
        cls,
        shape: "ScreenShape",
    ) -> None: ...
```

Every public method validates its shape (using either the constructor
shape or auto-detecting on first use), routes through `resolve_aspects`,
and dispatches to shape-specific logic.

### `ScreenShape` base (shapes/base.py)

```python
class ScreenShape(ABC):
    """
        Abstract description of a vendor Setup-UI screen format and
        keystroke conventions.
    """

    name: str

    @abstractmethod
    def detect(self, screen: "Screen") -> bool:
        """
            Return True if this shape recognizes the given screen.

            :param screen: Captured Screen dataclass.

            :returns: True on confident match.
        """
        ...

    @abstractmethod
    def parse_items(self, screen: "Screen") -> tuple["HiiFormItem", ...]:
        """
            Extract the menu items visible on the given screen.
        """
        ...

    @abstractmethod
    def highlighted_item(self, screen: "Screen") -> Optional["HiiFormItem"]:
        ...

    @abstractmethod
    def nav_down_key(self) -> bytes: ...

    @abstractmethod
    def nav_up_key(self) -> bytes: ...

    @abstractmethod
    def select_key(self) -> bytes: ...

    @abstractmethod
    def escape_key(self) -> bytes: ...

    @abstractmethod
    def open_value_selector_key(self) -> bytes: ...

    @abstractmethod
    def save_and_exit_path(self) -> tuple[bytes, ...]:
        """
            Return the keystroke sequence to reach "Save & Exit" from
            the top-level Setup form.
        """
        ...

    @abstractmethod
    def setup_entry_key(self) -> bytes: ...

    @abstractmethod
    def is_setup_form(self, screen: "Screen") -> bool:
        """
            Detect whether the current screen is the Setup main form.
        """
        ...
```

### `Screen` (screen.py)

```python
@dataclass(frozen=True)
class Screen:
    """
        A captured text screen.

        :param lines: Text lines as they appeared on screen, after ANSI
            sequence stripping for display rendering.  Raw bytes are
            preserved in `raw_bytes` for debugging.
        :param cursor_row: Cursor row position if extractable.
        :param cursor_col: Cursor column position if extractable.
        :param raw_bytes: Raw bytes including escape sequences.
    """
    lines: tuple[str, ...]
    cursor_row: Optional[int]
    cursor_col: Optional[int]
    raw_bytes: bytes

    def render_snapshot(self) -> str:
        rtnval = os.linesep.join(self.lines)
        return rtnval
```

### `HiiFormItem` (screen.py)

```python
@dataclass(frozen=True)
class HiiFormItem:
    """
        A single menu item extracted from a Screen.

        :param label: Visible label text.
        :param value: Current displayed value (empty if this is a
            submenu link).
        :param row: Screen row index of the item.
        :param is_submenu: True if selecting this item navigates to a
            child form.
        :param enum_values: Detected enumerated value list for boolean
            or enum items; empty tuple if not applicable.
        :param numeric_min: Detected min for numeric items, or None.
        :param numeric_max: Detected max for numeric items, or None.
    """
    label: str
    value: str
    row: int
    is_submenu: bool
    enum_values: tuple[str, ...]
    numeric_min: Optional[int]
    numeric_max: Optional[int]
```

### AMI Aptio shape (shapes/ami.py)

Recognizes the Aptio top-banner signature (`"Aptio Setup Utility"`,
`"American Megatrends"`), `│`/`─` box-drawing borders, reverse-video
highlight, and the F10-to-save / ESC-to-exit convention.  `nav_down_key`
is `b"\x1b[B"` (CSI B), `select_key` is `b"\r"`, `escape_key` is `b"\x1b"`.

### Insyde H2O shape (shapes/insyde.py)

Recognizes the H2O top banner, `═`/`║` double-line borders, F10 save,
ESC exit.

### Phoenix SecureCore shape (shapes/phoenix.py)

Recognizes the SecureCore banner, `+`/`-`/`|` ASCII borders, F10 save.

### Tianocore / EDK2 shape (shapes/tianocore.py)

Recognizes the EDK2 reference Setup UI (OVMF), which uses a simpler
ANSI-art layout and the `F10` save convention.

### Registry (shapes/registry.py)

```python
_REGISTRY: list[ScreenShape] = []


def register_shape(shape: ScreenShape) -> None:
    logger = get_uefi_logger("uefi.hii")
    for existing in _REGISTRY:
        if existing.name == shape.name:
            logger.warning("shape already registered name=%s", shape.name)
            return
    _REGISTRY.insert(0, shape)
    return


def dispatch(screen: Screen) -> tuple[ScreenShape, ...]:
    matches = []
    for shape in _REGISTRY:
        if shape.detect(screen=screen) is True:
            matches.append(shape)
    rtnval = tuple(matches)
    return rtnval
```

The shipped shapes register themselves on module import via a
`shapes/__init__.py` that imports each concrete shape module and calls
`register_shape`.

### Screen capture flow (screen.py)

```python
def _capture(agent: "UefiAgent", aspects: UefiCmdAspects) -> Screen:
    chunk_timeout = aspects.inactivity_interval
    raw = agent.read_until_idle(
        idle_seconds=chunk_timeout,
        max_bytes=64 * 1024,
        aspects=aspects,
    )
    lines, cursor = _ansi_render(raw=raw)
    rtnval = Screen(lines=lines, cursor_row=cursor[0], cursor_col=cursor[1], raw_bytes=raw)
    return rtnval
```

`_ansi_render` is a small ANSI-state-machine implementation that
resolves CSI cursor moves, SGR attributes (for highlight detection), and
screen clears.  No `curses` dependency; pure Python.

## Data Models

### Keystrokes (keystrokes.py)

```python
KEY_ENTER = b"\r"
KEY_ESC = b"\x1b"
KEY_UP = b"\x1b[A"
KEY_DOWN = b"\x1b[B"
KEY_RIGHT = b"\x1b[C"
KEY_LEFT = b"\x1b[D"
KEY_TAB = b"\t"
KEY_BACKSPACE = b"\x7f"
KEY_DELETE = b"\x1b[3~"
KEY_F1 = b"\x1bOP"
KEY_F2 = b"\x1bOQ"
KEY_F10 = b"\x1b[21~"
KEY_SPACE = b" "
KEY_PLUS = b"+"
KEY_MINUS = b"-"
```

### Screen (see Components section above)

### `HiiFormItem` (see Components section above)

## Measured Interop Contract

Consumed `aspects` fields:

* `action_pattern` — governs retry behavior at the screen-read /
  keystroke level.
* `completion_timeout`, `completion_interval` — used by
  `DO_UNTIL_SUCCESS` and by `enter_setup` / `exit_setup` confirmation
  waits.
* `connection_timeout`, `connection_interval`,
  `allowed_connection_exceptions` — consumed by the underlying 4-agent
  channel.
* `inactivity_interval`, `inactivity_timeout` — used by the screen
  capture idle-detection logic; `inactivity_interval` determines when a
  partial screen is considered "stable" for parsing.
* `monitor_delay` — used post-keystroke to allow firmware to repaint.
* `logging_pattern` — routes log emission on channel `uefi.hii`.
* `logger` — injected logger; if `None`, `get_uefi_logger("uefi.hii")`.

Supported `ActionPattern` values:

* `ActionPattern.SINGLE_CALL`
* `ActionPattern.SINGLE_CONNECTED_CALL`
* `ActionPattern.DO_UNTIL_SUCCESS`

`DO_WHILE_SUCCESS` and `DO_UNTIL_CONNECTION_FAILURE` SHALL raise
`SemanticError`.

## Error Handling

| Condition                                            | Exception            | Included in `details`                                              |
|------------------------------------------------------|----------------------|--------------------------------------------------------------------|
| Zero shape detectors match                           | `UefiError`          | captured screen snapshot                                           |
| Multiple shape detectors match                       | `UefiError`          | list of matching detector names, captured screen                   |
| Label not found on screen                            | `UefiError`          | captured screen snapshot, list of observed labels                  |
| Post-navigation highlight mismatch                   | `UefiError`          | expected label, observed label, captured screen                    |
| Boolean target is not boolean                         | `UefiError`          | observed value list, captured screen                               |
| Enum `choice` not in value list                      | `UefiError`          | valid choices                                                      |
| Numeric value outside [min, max]                      | `UefiError`          | min, max, offending value                                          |
| Post-set value does not reflect input                 | `UefiError`          | captured screen                                                    |
| Setup entry timed out                                 | `TimeoutError`       | captured last screen                                               |
| Save/Discard confirmation screen timeout              | `TimeoutError`       | captured last screen                                               |
| Access to `hii_packages` attribute                    | `SemanticError`      | "screen-scrape only"                                               |
| Unsupported `ActionPattern` requested                 | `SemanticError`      | requested pattern, supported patterns                              |

## Logging

* Channel: `uefi.hii`.
* INFO: shape detected, Setup entered / exited, item found,
  boolean/enum/numeric value set.
* DEBUG: keystroke byte sequences emitted, per-line screen-parse
  results, highlight position transitions.
* WARNING: retry attempts during `DO_UNTIL_SUCCESS`; duplicate-shape
  registration attempts.
* ERROR: terminal navigation failures before re-raise.

## Security Considerations

* **Opaque input.**  Screen capture consumes whatever the firmware
  emits; a malicious firmware could emit crafted ANSI sequences.  The
  `_ansi_render` state machine is implemented defensively — unknown
  escape sequences are dropped, oversized sequences are bounded.
* **Password fields.**  When the shape detects a password-entry item,
  the navigator's public API emits a WARNING and refuses to type
  passwords; password automation is out of scope because the value
  cannot be read back to verify.  Callers who must automate passwords
  must drop to the agent's raw channel.
* **No HII binary parsing.**  By Requirement 15, this feature never
  imports or invokes an HII package parser; that is a separate feature
  (reserved for a future `5-hii-binary` spec).
* **BMC session exposure.**  Over IPMI-SOL the feature shares the
  transport's session; it does not spawn a parallel SOL connection.

## Testing Strategy

### Unit tests — shape detectors

One test module per shape (`test_shape_ami.py`, `test_shape_insyde.py`,
etc.) exercising:

* Positive detection on a captured fixture screen.
* Negative detection on a screen from a different shape.
* Item parsing yields the expected labels and values.
* Highlight extraction matches the fixture's known highlight position.
* Value-selector / save / escape keystroke round-trips on a recorded
  interaction transcript.

### Unit tests — navigator (`test_navigator.py`)

* `find_item(exact)` returns the expected item.
* `find_item(regex)` returns the expected item.
* `find_item(case_sensitive=False)` matches case-insensitively.
* `navigate_to(item)` issues the correct arrow-key sequence computed
  from the current highlight position.
* Post-navigation highlight mismatch raises `UefiError`.
* `set_boolean`, `set_enum`, `set_numeric` — happy paths via a
  scripted fake agent that advances a deterministic screen sequence.
* `enter_setup` / `exit_setup(save=True)` / `exit_setup(save=False)`
  sequences match the shape's expected keystrokes.

### Unit tests — ANSI renderer (`test_ansi_render.py`)

* CSI cursor moves resolve to the expected final cursor position.
* SGR reverse-video detection isolates highlight lines.
* Screen clear drops all prior content.
* Oversized escape sequences are bounded and do not hang the renderer.

### Action-pattern tests (`test_actionpatterns.py`)

* `SINGLE_CALL` — single attempt.
* `SINGLE_CONNECTED_CALL` — retries connection class.
* `DO_UNTIL_SUCCESS` — retries until success or timeout.
* `DO_WHILE_SUCCESS` / `DO_UNTIL_CONNECTION_FAILURE` raise
  `SemanticError`.
* `aspects=None` falls back to instance default.

### Fixtures corpus (`tests/fixtures/hii/`)

Captured console transcripts and rendered screens from:

* AMI Aptio — current-generation server BIOS (e.g., Supermicro,
  Gigabyte).
* Insyde H2O — client / server firmware.
* Phoenix SecureCore — enterprise laptop/workstation firmware.
* EDK2 / tianocore — OVMF VM.

Each fixture includes:

* `capture.bin` — raw serial bytes including ANSI.
* `expected_screen.txt` — rendered text lines.
* `expected_items.json` — parsed `HiiFormItem` list.
* `interaction_transcript.jsonl` — scripted navigation sequence for
  replay tests.

### Integration tests

* OVMF VM: enter Setup, toggle a known boolean (e.g.,
  "System Profile"), save, reboot, verify persistence.
* Gate on `AUTOMOJO_UEFI_OVMF_VM` env var.

### Coverage target

≥ 90% line coverage for `navigator.py`, each shape module, and the
ANSI renderer.

## Dependencies on Other Features

* 1-aspects — consumes `UefiCmdAspects`, `resolve_aspects`,
  `ActionPattern`, `LoggingPattern`.
* 1-exceptions — raises `UefiError`, `SemanticError`,
  `ConfigurationError`, `TimeoutError`.
* 1-logging — emits on `uefi.hii` channel.
* 2-transport-serial / 2-transport-ipmi-sol — console byte stream.
* 4-agent — provides `read_until_idle`, `write`, and channel lifecycle.

No dependency on HII binary parsing (explicitly excluded per
Requirement 15).  No dependency on 5-variable-services — Setup value
persistence is validated by reading the Setup form back, not by reading
the underlying `Setup` variable.
