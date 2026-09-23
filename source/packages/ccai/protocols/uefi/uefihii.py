"""
    .. module:: uefihii
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Module providing :class:`HiiFormNavigator`, a screen-scraping
               driver for firmware Setup UIs exposed over a serial or
               IPMI-SOL console.  The navigator recognizes the text screen
               rendered by common vendor Setup implementations (AMI Aptio,
               Insyde H2O, Phoenix SecureCore, EDK2/tianocore reference)
               and translates high-level requests such as
               ``set_boolean(label, value)`` into arrow-key / enter / F10
               escape sequences written to the underlying transport.

               This module is deliberately decoupled from the HII binary
               package format defined by UEFI Specification sections
               33 through 36; it does NOT parse HII IFR nor request
               HII packages from the target.  It screen-scrapes a text
               terminal, which is the same approach a human operator
               takes when driving Setup over a terminal emulator.  When
               a binary-format interpreter is required it will live in a
               separate feature module and must not be imported from
               here (see Requirement 15 in
               ``specs/5-hii-forms/requirements.md``).

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Code Craft AI LLC"
__credits__ = []


import os
import re

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from ccai.protocols.uefi.uefiaspects import (
    DEFAULT_CMD_ASPECTS,
    UefiCmdAspects,
    resolve_aspects,
)
from ccai.protocols.uefi.uefiexceptions import UefiError


# ---------------------------------------------------------------------------
# Keystroke byte-sequence constants (ECMA-48 / VT100)
# ---------------------------------------------------------------------------

KEY_UP: bytes = b"\x1b[A"
KEY_DOWN: bytes = b"\x1b[B"
KEY_RIGHT: bytes = b"\x1b[C"
KEY_LEFT: bytes = b"\x1b[D"
KEY_ENTER: bytes = b"\r"
KEY_ESC: bytes = b"\x1b"
KEY_TAB: bytes = b"\t"
KEY_BACKSPACE: bytes = b"\x7f"
KEY_SPACE: bytes = b" "
KEY_PLUS: bytes = b"+"
KEY_MINUS: bytes = b"-"
KEY_F1: bytes = b"\x1bOP"
KEY_F2: bytes = b"\x1bOQ"
KEY_F10: bytes = b"\x1b[21~"


# ---------------------------------------------------------------------------
# Navigation hard limits (guard against runaway arrow-key loops)
# ---------------------------------------------------------------------------

MAX_NAVIGATION_STEPS: int = 128
MAX_ENUM_CYCLES: int = 32
MAX_BOOLEAN_TOGGLES: int = 4
MAX_NUMBER_DIGITS: int = 32


# ---------------------------------------------------------------------------
# ANSI escape stripping
# ---------------------------------------------------------------------------

_ANSI_CSI_RE: re.Pattern = re.compile(rb"\x1b\[[0-?]*[ -/]*[@-~]")
_ANSI_OSC_RE: re.Pattern = re.compile(rb"\x1b\][^\x07]*\x07")
_ANSI_ESC_RE: re.Pattern = re.compile(rb"\x1b[@-Z\\-_]")
_BOX_DRAW_RE: re.Pattern = re.compile(r"[\u2500-\u257F]")


def _strip_ansi(text: str) -> str:
    """
        Strip ANSI / box-drawing artifacts from ``text`` for comparison
        purposes.  The original text is preserved elsewhere; this helper
        only produces the "human readable" projection used by the
        label-matching logic.

        :param text: A screen line.

        :returns: The same line with ANSI escape sequences and
                  box-drawing glyphs stripped.
    """
    as_bytes = text.encode("utf-8", errors="replace")
    as_bytes = _ANSI_OSC_RE.sub(b"", as_bytes)
    as_bytes = _ANSI_CSI_RE.sub(b"", as_bytes)
    as_bytes = _ANSI_ESC_RE.sub(b"", as_bytes)
    decoded = as_bytes.decode("utf-8", errors="replace")
    cleaned = _BOX_DRAW_RE.sub(" ", decoded)
    rtnval = cleaned
    return rtnval


# ---------------------------------------------------------------------------
# ScreenShape descriptor + shipped vendor registry
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ScreenShape:
    """
        Describes the ANSI / box-drawing layout that a specific firmware
        vendor uses for its Setup UI.  Instances are pure data; the
        navigator consults a :class:`ScreenShape` when parsing screens
        captured from the target.

        :param vendor_id: Short identifier for the vendor
                          (``"ami"``, ``"insyde"``, ``"phoenix"``,
                          ``"tianocore"``).
        :param menu_item_prefix: Prefix characters that mark a line as a
                                 navigable menu item (e.g. ``">"`` or
                                 ``""``).
        :param selected_item_marker: Marker that tags the currently
                                     highlighted item; for most vendors
                                     this is the ANSI reverse-video SGR
                                     (``"\x1b[7m"``), but some vendors
                                     use an ASCII token such as ``"*"``.
        :param field_value_separator: Separator between an item's label
                                      and its displayed value.
    """
    vendor_id: str
    menu_item_prefix: str
    selected_item_marker: str
    field_value_separator: str


SCREEN_SHAPES: Dict[str, ScreenShape] = {
    "ami": ScreenShape(
        vendor_id="ami",
        menu_item_prefix="",
        selected_item_marker="\x1b[7m",
        field_value_separator="  ",
    ),
    "insyde": ScreenShape(
        vendor_id="insyde",
        menu_item_prefix="",
        selected_item_marker="\x1b[7m",
        field_value_separator=": ",
    ),
    "phoenix": ScreenShape(
        vendor_id="phoenix",
        menu_item_prefix=">",
        selected_item_marker="*",
        field_value_separator="  ",
    ),
    "tianocore": ScreenShape(
        vendor_id="tianocore",
        menu_item_prefix="",
        selected_item_marker="\x1b[7m",
        field_value_separator="  ",
    ),
}


# ---------------------------------------------------------------------------
# FormItem
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FormItem:
    """
        A single menu item extracted from a rendered Setup screen.

        :param label: The item's visible label text, with leading and
                      trailing whitespace stripped.
        :param value: The item's displayed value, or ``None`` when the
                      item is a submenu link.
        :param is_menu: ``True`` when pressing Enter navigates into a
                        submenu (the item has no settable value on this
                        screen).
    """
    label: str
    value: Optional[str] = None
    is_menu: bool = False


# ---------------------------------------------------------------------------
# HII-specific error
# ---------------------------------------------------------------------------

class HiiNavigationError(UefiError):
    """
        Raised when Setup-menu navigation cannot locate the requested
        item, the post-navigation highlight does not match the expected
        label, or a set operation cannot converge on the requested
        value.  The captured screen snapshot is always provided in
        ``details`` when the navigator has one available.
    """


# ---------------------------------------------------------------------------
# Screen parsing helpers
# ---------------------------------------------------------------------------

def _screen_to_lines(screen_text: str) -> List[str]:
    """
        Split a raw captured screen into a list of lines.  CRLF and CR
        line endings are normalized to LF before splitting so that
        captures from telnet-style transports compare equal to captures
        from locally-attached serial.

        :param screen_text: The captured screen as a single string.

        :returns: The screen split into a list of line strings (the
                  trailing empty line after a terminating newline is
                  discarded).
    """
    if screen_text is None:
        rtnval: List[str] = []
        return rtnval

    normalized = screen_text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")

    # Drop a single trailing empty element introduced by a trailing LF.
    if len(lines) > 0 and lines[-1] == "":
        lines = lines[:-1]

    rtnval = lines
    return rtnval


def _is_candidate_menu_line(raw_line: str, cleaned_line: str,
                            shape: ScreenShape) -> bool:
    """
        Decide whether a single screen line looks like a navigable menu
        item for the given vendor shape.  The rule is intentionally
        conservative: the line must contain at least one non-whitespace
        glyph after stripping ANSI / box-drawing noise, and (when the
        shape declares a ``menu_item_prefix``) must either begin with
        the prefix or be the currently-highlighted line.

        :param raw_line: The original line including escape sequences
                         and box-drawing glyphs.
        :param cleaned_line: The line after :func:`_strip_ansi`.
        :param shape: The vendor :class:`ScreenShape` in effect.

        :returns: ``True`` when the line appears to be a menu item.
    """
    stripped = cleaned_line.strip()
    if len(stripped) == 0:
        rtnval = False
        return rtnval

    # Lines that are pure ASCII borders (dashes / plus signs / pipes) are not items.
    only_border_glyphs = True
    for character in stripped:
        if character not in ("-", "=", "+", "|", "*", ":"):
            only_border_glyphs = False
            break

    if only_border_glyphs:
        rtnval = False
        return rtnval

    if len(shape.menu_item_prefix) > 0:
        has_prefix = shape.menu_item_prefix in stripped[: len(shape.menu_item_prefix) + 2]
        is_highlighted = shape.selected_item_marker in raw_line
        if not has_prefix and not is_highlighted:
            rtnval = False
            return rtnval

    rtnval = True
    return rtnval


def _parse_line_to_item(raw_line: str, cleaned_line: str,
                        shape: ScreenShape) -> FormItem:
    """
        Translate a single screen line into a :class:`FormItem`.  The
        label / value split uses the shape's ``field_value_separator``.
        When the separator is not present the line is treated as a
        submenu link and ``is_menu`` is set to ``True``.

        :param raw_line: The original line including escape sequences.
        :param cleaned_line: The line after :func:`_strip_ansi`.
        :param shape: The vendor :class:`ScreenShape`.

        :returns: The parsed :class:`FormItem`.
    """
    # Strip an optional leading vendor prefix from the cleaned line.
    working = cleaned_line.strip()
    prefix = shape.menu_item_prefix.strip()
    if len(prefix) > 0 and working.startswith(prefix):
        working = working[len(prefix):].lstrip()

    # An ASCII selection marker (e.g. "*" for Phoenix) may also appear at the
    # very start of the line; strip it so it is not absorbed into the label.
    marker = shape.selected_item_marker
    if len(marker) > 0 and not marker.startswith("\x1b"):
        if working.startswith(marker):
            working = working[len(marker):].lstrip()

    separator = shape.field_value_separator
    if len(separator) > 0 and separator in working:
        head, _, tail = working.partition(separator)
        label_text = head.strip()
        value_text = tail.strip()

        if len(value_text) == 0:
            item = FormItem(label=label_text, value=None, is_menu=True)
            return item

        item = FormItem(label=label_text, value=value_text, is_menu=False)
        return item

    # No separator — the line is a label-only submenu link.
    item = FormItem(label=working.strip(), value=None, is_menu=True)
    return item


def _selected_index(raw_lines: List[str], item_rows: List[int],
                    shape: ScreenShape) -> Optional[int]:
    """
        Compute the index (into the filtered menu-item list) of the
        currently-highlighted row, or ``None`` when no row carries the
        shape's ``selected_item_marker``.

        :param raw_lines: The original screen lines including escape
                          sequences.
        :param item_rows: The row indices in ``raw_lines`` that were
                          classified as menu items.
        :param shape: The vendor :class:`ScreenShape`.

        :returns: The menu-item index or ``None``.
    """
    marker = shape.selected_item_marker
    if len(marker) == 0:
        rtnval: Optional[int] = None
        return rtnval

    for index, row in enumerate(item_rows):
        line = raw_lines[row]
        if marker in line:
            rtnval = index
            return rtnval

    rtnval = None
    return rtnval


# ---------------------------------------------------------------------------
# HiiFormNavigator
# ---------------------------------------------------------------------------

class HiiFormNavigator:
    """
        Drive the firmware Setup UI over a serial transport by
        screen-scraping.  This is NOT an HII binary-format interpreter —
        it reads the ANSI / box-drawing text rendered to the console
        and emits arrow-key / enter / F10 escape sequences.

        Vendor detection is pluggable via :class:`ScreenShape`
        instances.  The navigator takes a ``send`` callable that writes
        raw bytes to the console (typically
        :meth:`UefiSerialTransport.send`) and a ``read_screen``
        callable that returns the current screen text (typically wired
        to :meth:`UefiSerialTransport.recv_until` with a trailing
        idle-timeout).  Decoupling from a concrete transport keeps the
        navigator unit-testable without standing up a transport.

        All public methods accept an optional ``aspects`` argument that
        overrides the instance default; :func:`resolve_aspects` is
        consulted at each entry point.  Failure to locate an item, a
        mismatch between the expected and observed highlight, or the
        inability to converge a set operation raises
        :class:`HiiNavigationError` with the last captured screen in
        ``details``.

        :param send: Callable that transmits one keystroke byte sequence.
        :param read_screen: Callable that returns the current screen
                            text given a timeout in seconds.
        :param shape: The vendor :class:`ScreenShape` to apply.
        :param aspects: Default :class:`UefiCmdAspects` for this
                        navigator.
    """

    def __init__(self, send: Callable[[bytes], int],
                       read_screen: Callable[[float], str],
                       shape: ScreenShape,
                       aspects: UefiCmdAspects = DEFAULT_CMD_ASPECTS) -> None:
        """
            Initialize the navigator.

            :param send: Keystroke transmit callable.
            :param read_screen: Screen-capture callable.
            :param shape: Vendor :class:`ScreenShape`.
            :param aspects: Default :class:`UefiCmdAspects`.

            :raises: TypeError
        """
        if not callable(send):
            err_msg_lines = [
                "HiiFormNavigator requires a callable 'send'.",
                "GOT: value={!r} type={}".format(send, type(send).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise TypeError(err_msg) from None

        if not callable(read_screen):
            err_msg_lines = [
                "HiiFormNavigator requires a callable 'read_screen'.",
                "GOT: value={!r} type={}".format(read_screen, type(read_screen).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise TypeError(err_msg) from None

        if not isinstance(shape, ScreenShape):
            err_msg_lines = [
                "HiiFormNavigator requires a ScreenShape instance.",
                "GOT: value={!r} type={}".format(shape, type(shape).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise TypeError(err_msg) from None

        if not isinstance(aspects, UefiCmdAspects):
            err_msg_lines = [
                "HiiFormNavigator requires aspects to be a UefiCmdAspects.",
                "GOT: value={!r} type={}".format(aspects, type(aspects).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise TypeError(err_msg) from None

        self._send: Callable[[bytes], int] = send
        self._read_screen: Callable[[float], str] = read_screen
        self._shape: ScreenShape = shape
        self._aspects: UefiCmdAspects = aspects
        self._last_screen: Optional[str] = None
        return

    # -----------------------------------------------------------------------
    # Properties
    # -----------------------------------------------------------------------

    @property
    def shape(self) -> ScreenShape:
        """
            :returns: The vendor :class:`ScreenShape` in effect for this
                      navigator.
        """
        rtnval = self._shape
        return rtnval

    @property
    def last_screen(self) -> Optional[str]:
        """
            :returns: The most recently captured screen text, or
                      ``None`` when the navigator has not yet captured.
        """
        rtnval = self._last_screen
        return rtnval

    # -----------------------------------------------------------------------
    # Screen capture and parsing
    # -----------------------------------------------------------------------

    def _capture(self, effective: UefiCmdAspects) -> str:
        """
            Capture the current screen text from the target, honoring
            ``effective.completion_timeout`` as the read-ceiling.  The
            captured text is remembered in ``self._last_screen`` so
            exception paths can embed it in ``details``.

            :param effective: The resolved :class:`UefiCmdAspects`.

            :returns: The screen text as a single string.
        """
        timeout = float(effective.completion_timeout)
        captured = self._read_screen(timeout)
        if captured is None:
            captured = ""
        self._last_screen = captured
        rtnval = captured
        return rtnval

    def _parse(self, screen_text: str) -> Tuple[List[FormItem], List[int], List[str]]:
        """
            Parse a screen string into a list of :class:`FormItem`,
            the per-item row indices, and the raw line list.

            :param screen_text: Captured screen.

            :returns: ``(items, item_rows, raw_lines)``.
        """
        raw_lines = _screen_to_lines(screen_text=screen_text)
        cleaned_lines = [_strip_ansi(text=line) for line in raw_lines]

        items: List[FormItem] = []
        item_rows: List[int] = []

        for row_index, raw_line in enumerate(raw_lines):
            cleaned_line = cleaned_lines[row_index]
            is_item = _is_candidate_menu_line(
                raw_line=raw_line,
                cleaned_line=cleaned_line,
                shape=self._shape,
            )
            if not is_item:
                continue

            item = _parse_line_to_item(
                raw_line=raw_line,
                cleaned_line=cleaned_line,
                shape=self._shape,
            )
            items.append(item)
            item_rows.append(row_index)

        rtnval = (items, item_rows, raw_lines)
        return rtnval

    def current_items(self,
                      aspects: Optional[UefiCmdAspects] = None) -> List[FormItem]:
        """
            Capture the current screen and parse it into a list of
            :class:`FormItem` values.

            :param aspects: Optional per-call :class:`UefiCmdAspects`.

            :returns: The parsed items in screen order.
        """
        effective = resolve_aspects(passed=aspects, instance_default=self._aspects)
        screen_text = self._capture(effective=effective)
        items, _item_rows, _raw_lines = self._parse(screen_text=screen_text)
        rtnval = items
        return rtnval

    def _current_state(self, effective: UefiCmdAspects) -> Tuple[List[FormItem], Optional[int], str]:
        """
            Capture and parse the current screen, returning the item
            list, the currently-highlighted item's index (or ``None``),
            and the raw screen text.

            :param effective: The resolved :class:`UefiCmdAspects`.

            :returns: ``(items, selected_index, screen_text)``.
        """
        screen_text = self._capture(effective=effective)
        items, item_rows, raw_lines = self._parse(screen_text=screen_text)
        selected = _selected_index(
            raw_lines=raw_lines,
            item_rows=item_rows,
            shape=self._shape,
        )
        rtnval = (items, selected, screen_text)
        return rtnval

    # -----------------------------------------------------------------------
    # Lookups
    # -----------------------------------------------------------------------

    def find_item(self, label: str,
                        aspects: Optional[UefiCmdAspects] = None) -> int:
        """
            Return the index (into :meth:`current_items`) of the first
            item whose label matches ``label`` exactly.

            :param label: The visible label to search for.
            :param aspects: Optional per-call :class:`UefiCmdAspects`.

            :returns: The item's zero-based index.
            :raises: HiiNavigationError
        """
        if not isinstance(label, str):
            err_msg_lines = [
                "HiiFormNavigator.find_item requires a str 'label'.",
                "GOT: value={!r} type={}".format(label, type(label).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise HiiNavigationError(err_msg) from None

        effective = resolve_aspects(passed=aspects, instance_default=self._aspects)
        items, _selected, screen_text = self._current_state(effective=effective)

        for index, item in enumerate(items):
            if item.label == label:
                return index

        observed_labels = [item.label for item in items]
        err_msg_lines = [
            "HiiFormNavigator.find_item: label not found on current screen.",
            "VENDOR: {}".format(self._shape.vendor_id),
            "LABEL: {!r}".format(label),
            "OBSERVED LABELS: {}".format(observed_labels),
            "SCREEN:",
            screen_text,
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise HiiNavigationError(err_msg, details=list(err_msg_lines[1:])) from None

    # -----------------------------------------------------------------------
    # Navigation
    # -----------------------------------------------------------------------

    def navigate_to(self, label: str,
                          aspects: Optional[UefiCmdAspects] = None) -> None:
        """
            Navigate (via arrow keys) until the currently-highlighted
            item matches ``label``.  On entry the navigator captures
            the screen, locates the requested item, and sends UP or
            DOWN arrow keystrokes until the highlight converges.  When
            no highlight can be detected the navigator falls back to
            sending DOWN arrows until the label appears as the first
            screen item — suitable for vendors where the highlight is
            encoded in a way the shape does not recognize.

            :param label: The visible label to navigate to.
            :param aspects: Optional per-call :class:`UefiCmdAspects`.

            :raises: HiiNavigationError
        """
        effective = resolve_aspects(passed=aspects, instance_default=self._aspects)

        items, selected, screen_text = self._current_state(effective=effective)

        target_index: Optional[int] = None
        for index, item in enumerate(items):
            if item.label == label:
                target_index = index
                break

        if target_index is None:
            observed_labels = [item.label for item in items]
            err_msg_lines = [
                "HiiFormNavigator.navigate_to: label not visible on current screen.",
                "VENDOR: {}".format(self._shape.vendor_id),
                "LABEL: {!r}".format(label),
                "OBSERVED LABELS: {}".format(observed_labels),
                "SCREEN:",
                screen_text,
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise HiiNavigationError(err_msg, details=list(err_msg_lines[1:])) from None

        steps = 0
        while steps < MAX_NAVIGATION_STEPS:
            if selected is not None and selected == target_index:
                return

            if selected is None:
                # No highlight detectable: press DOWN and re-read.
                self._send(KEY_DOWN)
            elif selected < target_index:
                self._send(KEY_DOWN)
            else:
                self._send(KEY_UP)

            steps += 1

            items, selected, screen_text = self._current_state(effective=effective)

            # The target label may have shifted on paginated screens; re-resolve.
            target_index = None
            for index, item in enumerate(items):
                if item.label == label:
                    target_index = index
                    break

            if target_index is None:
                err_msg_lines = [
                    "HiiFormNavigator.navigate_to: target label vanished during navigation.",
                    "VENDOR: {}".format(self._shape.vendor_id),
                    "LABEL: {!r}".format(label),
                    "STEPS: {}".format(steps),
                    "SCREEN:",
                    screen_text,
                ]
                err_msg = os.linesep.join(err_msg_lines)
                raise HiiNavigationError(err_msg, details=list(err_msg_lines[1:])) from None

            if selected is not None and selected == target_index:
                return

        err_msg_lines = [
            "HiiFormNavigator.navigate_to: exceeded MAX_NAVIGATION_STEPS without landing on target.",
            "VENDOR: {}".format(self._shape.vendor_id),
            "LABEL: {!r}".format(label),
            "MAX STEPS: {}".format(MAX_NAVIGATION_STEPS),
            "SCREEN:",
            screen_text,
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise HiiNavigationError(err_msg, details=list(err_msg_lines[1:])) from None

    def enter(self, aspects: Optional[UefiCmdAspects] = None) -> None:
        """
            Press Enter on the currently-highlighted item.  Public
            callers typically invoke this after :meth:`navigate_to`.

            :param aspects: Optional per-call :class:`UefiCmdAspects`.
        """
        effective = resolve_aspects(passed=aspects, instance_default=self._aspects)
        # Touch the effective aspects so static-analysis sees the binding;
        # future enhancements may log under effective.logger.
        _ = effective
        self._send(KEY_ENTER)
        return

    # -----------------------------------------------------------------------
    # Value setters
    # -----------------------------------------------------------------------

    def set_boolean(self, label: str, value: bool,
                          aspects: Optional[UefiCmdAspects] = None) -> None:
        """
            Navigate to ``label`` and toggle its displayed value until
            it matches ``value``.  The navigator presses SPACE to
            toggle (the convention shared by every shipped shape) and
            captures the screen between presses to observe whether the
            value converged.

            :param label: The label of the boolean item.
            :param value: Desired boolean state.
            :param aspects: Optional per-call :class:`UefiCmdAspects`.

            :raises: HiiNavigationError
        """
        if not isinstance(value, bool):
            err_msg_lines = [
                "HiiFormNavigator.set_boolean requires a bool 'value'.",
                "GOT: value={!r} type={}".format(value, type(value).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise HiiNavigationError(err_msg) from None

        effective = resolve_aspects(passed=aspects, instance_default=self._aspects)
        self.navigate_to(label=label, aspects=effective)

        desired_true = ("enabled", "on", "yes", "true", "1")
        desired_false = ("disabled", "off", "no", "false", "0")

        toggles = 0
        while toggles <= MAX_BOOLEAN_TOGGLES:
            items, _selected, screen_text = self._current_state(effective=effective)
            current_value: Optional[str] = None
            for item in items:
                if item.label == label:
                    current_value = item.value
                    break

            if current_value is not None:
                normalized = current_value.strip().lower()
                if value and normalized in desired_true:
                    return
                if not value and normalized in desired_false:
                    return

            self._send(KEY_SPACE)
            toggles += 1

        err_msg_lines = [
            "HiiFormNavigator.set_boolean: value did not converge.",
            "VENDOR: {}".format(self._shape.vendor_id),
            "LABEL: {!r}".format(label),
            "DESIRED: {}".format(value),
            "TOGGLES: {}".format(toggles),
            "SCREEN:",
            screen_text,
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise HiiNavigationError(err_msg, details=list(err_msg_lines[1:])) from None

    def set_enum(self, label: str, value: str,
                       aspects: Optional[UefiCmdAspects] = None) -> None:
        """
            Navigate to ``label`` and press RIGHT-ARROW until the
            displayed value matches ``value``.  When the value does not
            converge within :data:`MAX_ENUM_CYCLES` presses the
            navigator raises :class:`HiiNavigationError`.

            :param label: The label of the enumerated item.
            :param value: Desired enumerated value as it appears on
                          screen.
            :param aspects: Optional per-call :class:`UefiCmdAspects`.

            :raises: HiiNavigationError
        """
        if not isinstance(value, str):
            err_msg_lines = [
                "HiiFormNavigator.set_enum requires a str 'value'.",
                "GOT: value={!r} type={}".format(value, type(value).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise HiiNavigationError(err_msg) from None

        effective = resolve_aspects(passed=aspects, instance_default=self._aspects)
        self.navigate_to(label=label, aspects=effective)

        observed_values: List[str] = []
        presses = 0
        while presses <= MAX_ENUM_CYCLES:
            items, _selected, screen_text = self._current_state(effective=effective)
            current_value: Optional[str] = None
            for item in items:
                if item.label == label:
                    current_value = item.value
                    break

            if current_value is not None:
                observed_values.append(current_value)
                if current_value.strip() == value.strip():
                    return

            self._send(KEY_RIGHT)
            presses += 1

        err_msg_lines = [
            "HiiFormNavigator.set_enum: desired value not observed.",
            "VENDOR: {}".format(self._shape.vendor_id),
            "LABEL: {!r}".format(label),
            "DESIRED: {!r}".format(value),
            "OBSERVED VALUES: {}".format(observed_values),
            "PRESSES: {}".format(presses),
            "SCREEN:",
            screen_text,
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise HiiNavigationError(err_msg, details=list(err_msg_lines[1:])) from None

    def set_number(self, label: str, value: int,
                         aspects: Optional[UefiCmdAspects] = None) -> None:
        """
            Navigate to ``label``, press Enter to open the numeric
            editor, type the ASCII digits of ``value`` (prefixing a
            ``-`` keystroke for negatives), and press Enter to commit.

            :param label: The label of the numeric item.
            :param value: Desired integer value.
            :param aspects: Optional per-call :class:`UefiCmdAspects`.

            :raises: HiiNavigationError
        """
        if isinstance(value, bool) or not isinstance(value, int):
            err_msg_lines = [
                "HiiFormNavigator.set_number requires an int 'value'.",
                "GOT: value={!r} type={}".format(value, type(value).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise HiiNavigationError(err_msg) from None

        digits_text = str(value)
        if len(digits_text) > MAX_NUMBER_DIGITS:
            err_msg_lines = [
                "HiiFormNavigator.set_number: value exceeds MAX_NUMBER_DIGITS.",
                "VALUE: {}".format(value),
                "DIGIT COUNT: {}".format(len(digits_text)),
                "MAX DIGITS: {}".format(MAX_NUMBER_DIGITS),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise HiiNavigationError(err_msg) from None

        effective = resolve_aspects(passed=aspects, instance_default=self._aspects)
        self.navigate_to(label=label, aspects=effective)

        self._send(KEY_ENTER)
        for character in digits_text:
            self._send(character.encode("ascii"))
        self._send(KEY_ENTER)
        return

    def save_and_exit(self, aspects: Optional[UefiCmdAspects] = None) -> None:
        """
            Press F10 to save (the convention shared by every shipped
            shape).

            :param aspects: Optional per-call :class:`UefiCmdAspects`.
        """
        effective = resolve_aspects(passed=aspects, instance_default=self._aspects)
        _ = effective
        self._send(KEY_F10)
        return


__all__ = [
    "FormItem",
    "HiiFormNavigator",
    "HiiNavigationError",
    "KEY_BACKSPACE",
    "KEY_DOWN",
    "KEY_ENTER",
    "KEY_ESC",
    "KEY_F1",
    "KEY_F2",
    "KEY_F10",
    "KEY_LEFT",
    "KEY_MINUS",
    "KEY_PLUS",
    "KEY_RIGHT",
    "KEY_SPACE",
    "KEY_TAB",
    "KEY_UP",
    "MAX_BOOLEAN_TOGGLES",
    "MAX_ENUM_CYCLES",
    "MAX_NAVIGATION_STEPS",
    "MAX_NUMBER_DIGITS",
    "SCREEN_SHAPES",
    "ScreenShape",
]
