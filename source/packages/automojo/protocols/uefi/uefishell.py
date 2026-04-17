"""
    .. module:: uefishell
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Module containing high-level typed wrappers around the most
               commonly used UEFI Shell commands (``map``, ``ls``, ``cp``,
               ``cd``, ``bcfg``, ``setvar``, ``dmpstore``, ``reset``, ``dh``,
               ``memmap``, ``smbiosview``, ``drivers``, ``devtree``).  Each
               wrapper dispatches through :meth:`UefiAgent.run_cmd`, parses
               the textual response using module-level compiled regular
               expressions, and returns a ``frozen=True`` dataclass.  Any
               output line that the parser cannot classify is preserved in
               the result's ``.unparsed_lines`` tuple so that callers can
               diagnose new firmware-vendor variants without editing this
               module.

               Every public wrapper accepts an optional
               :class:`UefiCmdAspects` override; when ``None`` the wrapper
               resolves the effective aspects through
               :func:`resolve_aspects` using the agent's instance default.
               Non-zero shell exit status raises
               :class:`UefiShellError` carrying the command text, the
               status, and the captured stdout / stderr streams.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Automation Mojo LLC"
__credits__ = []


import os
import re

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from automojo.protocols.uefi.uefiaspects import (
    UefiCmdAspects,
    resolve_aspects,
)
from automojo.protocols.uefi.uefiexceptions import UefiShellError
from automojo.protocols.uefi.uefilogging import (
    UEFI_SHELL_CHANNEL,
    get_uefi_logger,
)


# ---------------------------------------------------------------------------
# Module logger
# ---------------------------------------------------------------------------

_LOGGER = get_uefi_logger(UEFI_SHELL_CHANNEL)


# ---------------------------------------------------------------------------
# Reset-mode vocabulary
# ---------------------------------------------------------------------------

RESET_MODE_WARM = "warm"
RESET_MODE_COLD = "cold"
RESET_MODE_SHUTDOWN = "shutdown"

_VALID_RESET_MODES: Tuple[str, ...] = (
    RESET_MODE_WARM,
    RESET_MODE_COLD,
    RESET_MODE_SHUTDOWN,
)

_RESET_FLAG_BY_MODE: Dict[str, str] = {
    RESET_MODE_WARM: "-w",
    RESET_MODE_COLD: "-c",
    RESET_MODE_SHUTDOWN: "-s",
}


# ---------------------------------------------------------------------------
# Compiled regular expressions (compiled once at import time per Requirement 14)
# ---------------------------------------------------------------------------

# ``map`` line example:
#   FS0: Alias(s):HD1b:;BLK1:
_MAP_ENTRY_REGEX = re.compile(
    r"^\s*(?P<alias>[A-Za-z][A-Za-z0-9_]*)\s*:\s*"
    r"Alias\(s\)\s*:\s*(?P<aliases>[^\r\n]*)$"
)

# ``map`` device path continuation line (everything that is not a mapping line
# and that starts with a known UEFI device path token):
_MAP_DEVICE_PATH_REGEX = re.compile(
    r"^\s*(?P<dp>(?:PciRoot|Acpi|HD|CD|MemoryMapped|VenHw|Pci|Sata|"
    r"NVMe|USB|Mac|IPv4|IPv6|Uri|MessagingVendor|Ctrl|eMMC|UFS|SD)"
    r"[^\r\n]*)$",
    re.IGNORECASE,
)

# ``ls`` long-format entry line.  Example:
#   <DIR>     05/11/2024  14:32                 EFI
#   A         05/11/2024  14:32       1,234,567 bootx64.efi
_LS_ENTRY_REGEX = re.compile(
    r"^\s*(?P<attrs>\S+)\s+"
    r"(?P<date>\d{1,2}/\d{1,2}/\d{2,4})\s+"
    r"(?P<time>\d{1,2}:\d{2}(?::\d{2})?(?:\s*[AaPp][Mm])?)\s+"
    r"(?:(?P<size>[\d,]+)\s+)?"
    r"(?P<name>\S.*?)\s*$"
)

# ``bcfg boot dump`` entry.  Example:
#   Option: 00. Variable: Boot0000
#   Desc    - UEFI OS
#   DevPath - PciRoot(0x0)/Pci(...)/HD(...)
#   Optional- ...
_BCFG_OPTION_REGEX = re.compile(
    r"^\s*Option\s*:\s*(?P<index>[0-9A-Fa-f]+)\.\s*"
    r"Variable\s*:\s*Boot(?P<varhex>[0-9A-Fa-f]+)\s*"
    r"(?P<active>.*)?$"
)
_BCFG_DESC_REGEX = re.compile(r"^\s*Desc\s*-\s*(?P<desc>.*)$")
_BCFG_DEVPATH_REGEX = re.compile(r"^\s*DevPath\s*-\s*(?P<dp>.*)$")

# ``dmpstore`` block header.  Example:
#   Variable NV+BS 'GlobalDontKnow':{8BE4DF61-93CA-11D2-AA0D-00E098032B8C}
_DMPSTORE_HEADER_REGEX = re.compile(
    r"^Variable\s+(?P<attrs_text>[A-Za-z0-9+_-]+)\s+"
    r"'(?P<name>[^']*)'\s*:\s*"
    r"\{(?P<guid>[0-9A-Fa-f-]+)\}\s*$"
)
_DMPSTORE_DATASIZE_REGEX = re.compile(
    r"^\s*DataSize\s*=\s*0x(?P<size>[0-9A-Fa-f]+)\s*$"
)
_DMPSTORE_HEXLINE_REGEX = re.compile(
    r"^\s*(?P<offset>[0-9A-Fa-f]+)\s*:\s*"
    r"(?P<hexbytes>(?:[0-9A-Fa-f]{2}\s+)+)"
    r"\*(?P<ascii>.*)\*\s*$"
)

# ``dh`` handle line.  Example:
#   25: VariableArch Variable MonotonicCounter
_DH_HANDLE_LINE_REGEX = re.compile(
    r"^\s*(?P<handle>[0-9A-Fa-f]+)\s*:\s*(?P<protocols>.+?)\s*$"
)

# ``memmap`` entry.  Example:
#   Available  0000000000100000-0000000000FFFFFF 0000000000000F00 0000000000000000
_MEMMAP_ENTRY_REGEX = re.compile(
    r"^\s*(?P<mtype>[A-Za-z][A-Za-z0-9_]*)\s+"
    r"(?P<start>[0-9A-Fa-f]+)\s*-\s*(?P<end>[0-9A-Fa-f]+)\s+"
    r"(?P<pages>[0-9A-Fa-f]+)\s+"
    r"(?P<attr>[0-9A-Fa-f]+)\s*$"
)

# ``smbiosview`` record header.  Example:
#   Type=1, Handle=0x0001, Length=27
_SMBIOSVIEW_HEADER_REGEX = re.compile(
    r"^\s*Type\s*=\s*(?P<type>\d+)\s*,\s*"
    r"Handle\s*=\s*0x(?P<handle>[0-9A-Fa-f]+)\s*,\s*"
    r"Length\s*=\s*(?P<length>\d+)\s*$"
)
_SMBIOSVIEW_FIELD_REGEX = re.compile(
    r"^\s*(?P<key>[A-Za-z][A-Za-z0-9 _\-/]*?)\s*:\s*(?P<value>.*?)\s*$"
)

# ``drivers`` line.  Example:
#   T   D
#   D       VERSION  E  G  G  #D  #C  DRIVER NAME                IMAGE NAME
# 1F  0000000A  D  -  -   1   1  PciBus Driver                  PciBusDxe
_DRIVERS_ENTRY_REGEX = re.compile(
    r"^\s*(?P<handle>[0-9A-Fa-f]+)\s+"
    r"(?P<version>[0-9A-Fa-f]+)\s+"
    r"[A-Z?-]\s+[A-Z?-]\s+[A-Z?-]\s+"
    r"(?P<dcount>[0-9?-]+)\s+"
    r"(?P<ccount>[0-9?-]+)\s+"
    r"(?P<name>.+?)\s{2,}"
    r"(?P<image>\S+)\s*$"
)

# ``devtree`` node line.  Example:
#    Ctrl[1F] PciRoot(0x0)
#       Ctrl[2A] Pci(0x1F,0x2)
_DEVTREE_NODE_REGEX = re.compile(
    r"^(?P<indent>\s*)Ctrl\s*\[\s*(?P<handle>[0-9A-Fa-f]+)\s*\]\s*"
    r"(?P<name>.*)$"
)


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _split_output_lines(raw_stdout: str) -> List[str]:
    """
        Split Shell stdout into right-stripped, non-empty lines while
        preserving order.

        :param raw_stdout: Raw stdout text.

        :returns: A list of non-empty right-stripped lines.
    """
    lines: List[str] = []
    if raw_stdout is None:
        rtnval = lines
        return rtnval

    for line in raw_stdout.splitlines():
        stripped = line.rstrip()
        if len(stripped) > 0:
            lines.append(stripped)

    rtnval = lines
    return rtnval


def _quote_shell_path(path: str) -> str:
    """
        Quote a UEFI Shell path for safe interpolation into a command line.

        :param path: Path to quote.

        :returns: The path surrounded by double quotes, with embedded
                  double quotes doubled per Shell Specification §3.4.
    """
    if path is None:
        err_msg_lines = [
            "_quote_shell_path requires a str; got None.",
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise ValueError(err_msg) from None

    escaped = path.replace('"', '""')
    rtnval = '"{}"'.format(escaped)
    return rtnval


def _resolve_cmd_aspects(agent: Any, aspects: Optional[UefiCmdAspects]) -> UefiCmdAspects:
    """
        Resolve the effective :class:`UefiCmdAspects` for a wrapper call.
        Delegates to :func:`resolve_aspects` against the agent's instance
        default.

        :param agent: The :class:`UefiAgent` dispatching the command.
        :param aspects: Optional per-call aspects override.

        :returns: The resolved :class:`UefiCmdAspects` instance.
        :raises: ConfigurationError
    """
    instance_default: Optional[UefiCmdAspects] = getattr(agent, "_aspects", None)
    effective = resolve_aspects(passed=aspects, instance_default=instance_default)
    return effective


def _raise_shell_error(command: str, status: int, stdout: str, stderr: str,
                       summary: str) -> None:
    """
        Raise a :class:`UefiShellError` with the full context fields populated.

        :param command: The command text that was dispatched.
        :param status: Exit status returned by the shell.
        :param stdout: Captured stdout stream.
        :param stderr: Captured stderr stream.
        :param summary: Short human-readable summary for the exception.

        :raises: UefiShellError
    """
    err_msg_lines = [summary]
    err_msg_lines.append("COMMAND: {}".format(command))
    err_msg_lines.append("STATUS: {}".format(status))

    err_msg = os.linesep.join(err_msg_lines)
    raise UefiShellError(
        err_msg,
        command=command,
        status=status,
        stdout=stdout,
        stderr=stderr,
    ) from None


def _run_and_check(agent: Any, command: str,
                   aspects: UefiCmdAspects) -> Tuple[int, str, str]:
    """
        Dispatch ``command`` through ``agent.run_cmd`` and return the raw
        status/stdout/stderr triple.  Does NOT raise on non-zero status —
        callers that treat non-zero as error must invoke
        :func:`_raise_shell_error` themselves with the appropriate summary.

        :param agent: The :class:`UefiAgent`.
        :param command: The command text.
        :param aspects: The resolved :class:`UefiCmdAspects`.

        :returns: A ``(status, stdout, stderr)`` tuple.
    """
    status, stdout, stderr = agent.run_cmd(command, aspects=aspects)

    if stdout is None:
        resolved_stdout = ""
    else:
        resolved_stdout = stdout

    if stderr is None:
        resolved_stderr = ""
    else:
        resolved_stderr = stderr

    resolved_status = int(status) if status is not None else 0

    rtnval = (resolved_status, resolved_stdout, resolved_stderr)
    return rtnval


def _log_unparsed(command: str, unparsed_lines: Tuple[str, ...]) -> None:
    """
        Emit a single WARNING log line when ``unparsed_lines`` is non-empty,
        per Requirement 12.3.

        :param command: The command that produced the output.
        :param unparsed_lines: The unparsed lines collected by the parser.
    """
    count = len(unparsed_lines)
    if count > 0:
        _LOGGER.warning(
            "uefi.shell: %d unparsed output lines from command=%r",
            count, command,
        )
    return


# ---------------------------------------------------------------------------
# map
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MapEntry:
    """
        Single UEFI Shell mapping-table entry produced by ``map``.

        :param alias: Primary alias (e.g. ``"FS0"``).
        :param device_path: Device path text associated with the mapping.
        :param aliases: Tuple of extra aliases pointing to the same device.
    """
    alias: str
    device_path: str
    aliases: Tuple[str, ...]


@dataclass(frozen=True)
class MapResult:
    """
        Result object returned by :func:`shell_map`.

        :param entries: Tuple of :class:`MapEntry` parsed from stdout.
        :param unparsed_lines: Output lines that did not match any parser pattern.
        :param raw_stdout: Raw stdout text as returned by the agent.
        :param raw_stderr: Raw stderr text as returned by the agent.
        :param exit_status: Exit status returned by the shell.
    """
    entries: Tuple[MapEntry, ...]
    unparsed_lines: Tuple[str, ...]
    raw_stdout: str
    raw_stderr: str
    exit_status: int


def _parse_map_aliases(aliases_text: str) -> Tuple[str, str]:
    """
        Split the ``Alias(s):`` trailing segment into a primary device path
        component and a tuple of semicolon-separated aliases.

        :param aliases_text: The text following ``Alias(s):`` on a ``map``
                             mapping line.

        :returns: A two-tuple ``(device_hint, aliases_joined)`` where
                  ``device_hint`` is left empty (the real device path is
                  parsed from the following indented line) and
                  ``aliases_joined`` is the raw alias segment for caller
                  convenience.
    """
    trimmed = aliases_text.strip()
    rtnval = ("", trimmed)
    return rtnval


def shell_map(agent: Any,
              aspects: Optional[UefiCmdAspects] = None) -> MapResult:
    """
        Run the UEFI Shell ``map`` command and return structured mapping
        entries.

        :param agent: The :class:`UefiAgent` dispatching the command.
        :param aspects: Optional per-call :class:`UefiCmdAspects`.

        :returns: A :class:`MapResult` containing one :class:`MapEntry`
                  per mapping plus any lines that failed classification.
        :raises: UefiShellError, ConfigurationError
    """
    effective = _resolve_cmd_aspects(agent=agent, aspects=aspects)

    command = "map"
    status, stdout, stderr = _run_and_check(agent=agent, command=command, aspects=effective)

    if status != 0:
        _raise_shell_error(
            command=command,
            status=status,
            stdout=stdout,
            stderr=stderr,
            summary="UEFI Shell 'map' returned a non-zero status.",
        )

    entries_list: List[MapEntry] = []
    unparsed: List[str] = []

    lines = _split_output_lines(raw_stdout=stdout)

    pending_alias: Optional[str] = None
    pending_aliases: Tuple[str, ...] = ()

    for line in lines:
        map_match = _MAP_ENTRY_REGEX.match(line)
        if map_match is not None:
            # Close out any prior entry for which we never saw a device-path line.
            if pending_alias is not None:
                entries_list.append(MapEntry(
                    alias=pending_alias,
                    device_path="",
                    aliases=pending_aliases,
                ))

            raw_alias = map_match.group("alias")
            aliases_raw = map_match.group("aliases")
            _unused_hint, alias_blob = _parse_map_aliases(aliases_text=aliases_raw)

            if len(alias_blob) > 0:
                alias_parts = [part.strip() for part in alias_blob.split(";") if part.strip() != ""]
                aliases_tuple = tuple(alias_parts)
            else:
                aliases_tuple = ()

            pending_alias = raw_alias
            pending_aliases = aliases_tuple
            continue

        dp_match = _MAP_DEVICE_PATH_REGEX.match(line)
        if dp_match is not None and pending_alias is not None:
            device_path_text = dp_match.group("dp").strip()
            entries_list.append(MapEntry(
                alias=pending_alias,
                device_path=device_path_text,
                aliases=pending_aliases,
            ))
            pending_alias = None
            pending_aliases = ()
            continue

        # Header / banner lines are considered unparsed only if they are not
        # the known banner text.
        if line.strip().lower().startswith("mapping table"):
            continue

        unparsed.append(line)

    # Flush any final entry whose device-path line never arrived.
    if pending_alias is not None:
        entries_list.append(MapEntry(
            alias=pending_alias,
            device_path="",
            aliases=pending_aliases,
        ))

    unparsed_tuple = tuple(unparsed)
    _log_unparsed(command=command, unparsed_lines=unparsed_tuple)

    rtnval = MapResult(
        entries=tuple(entries_list),
        unparsed_lines=unparsed_tuple,
        raw_stdout=stdout,
        raw_stderr=stderr,
        exit_status=status,
    )
    return rtnval


# ---------------------------------------------------------------------------
# ls
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LsEntry:
    """
        Single directory entry produced by ``ls``.

        :param attributes: Raw attribute flag string (e.g. ``"A"`` or ``"<DIR>"``).
        :param size: File size in bytes; zero for directories.
        :param date: Date text as emitted by the shell.
        :param time: Time text as emitted by the shell.
        :param name: File or directory name.
    """
    attributes: str
    size: int
    date: str
    time: str
    name: str


@dataclass(frozen=True)
class LsResult:
    """
        Result object returned by :func:`shell_ls`.

        :param entries: Tuple of :class:`LsEntry` parsed from stdout.
        :param unparsed_lines: Output lines not matching any parser pattern.
        :param raw_stdout: Raw stdout returned by the agent.
        :param raw_stderr: Raw stderr returned by the agent.
        :param exit_status: Exit status returned by the shell.
    """
    entries: Tuple[LsEntry, ...]
    unparsed_lines: Tuple[str, ...]
    raw_stdout: str
    raw_stderr: str
    exit_status: int


def shell_ls(agent: Any, path: str = "",
             aspects: Optional[UefiCmdAspects] = None) -> LsResult:
    """
        Run the UEFI Shell ``ls`` command and return structured entries.

        :param agent: The :class:`UefiAgent` dispatching the command.
        :param path: Optional path operand; the empty string lists the cwd.
        :param aspects: Optional per-call :class:`UefiCmdAspects`.

        :returns: An :class:`LsResult` with one :class:`LsEntry` per file
                  or directory.
        :raises: UefiShellError, ConfigurationError
    """
    effective = _resolve_cmd_aspects(agent=agent, aspects=aspects)

    if path is None:
        path_arg = ""
    else:
        path_arg = path

    if len(path_arg) == 0:
        command = "ls"
    else:
        command = "ls {}".format(_quote_shell_path(path=path_arg))

    status, stdout, stderr = _run_and_check(agent=agent, command=command, aspects=effective)

    if status != 0:
        _raise_shell_error(
            command=command,
            status=status,
            stdout=stdout,
            stderr=stderr,
            summary="UEFI Shell 'ls' returned a non-zero status.",
        )

    entries_list: List[LsEntry] = []
    unparsed: List[str] = []

    lines = _split_output_lines(raw_stdout=stdout)
    for line in lines:
        match = _LS_ENTRY_REGEX.match(line)
        if match is None:
            unparsed.append(line)
            continue

        attrs_text = match.group("attrs").strip()
        date_text = match.group("date").strip()
        time_text = match.group("time").strip()
        raw_size = match.group("size")
        if raw_size is None:
            size_text = ""
        else:
            size_text = raw_size.strip()
        name_text = match.group("name").strip()

        if size_text == "" or size_text.upper() == "<DIR>":
            size_value = 0
        else:
            cleaned = size_text.replace(",", "")
            try:
                size_value = int(cleaned)
            except ValueError:
                unparsed.append(line)
                continue

        entries_list.append(LsEntry(
            attributes=attrs_text,
            size=size_value,
            date=date_text,
            time=time_text,
            name=name_text,
        ))

    unparsed_tuple = tuple(unparsed)
    _log_unparsed(command=command, unparsed_lines=unparsed_tuple)

    rtnval = LsResult(
        entries=tuple(entries_list),
        unparsed_lines=unparsed_tuple,
        raw_stdout=stdout,
        raw_stderr=stderr,
        exit_status=status,
    )
    return rtnval


# ---------------------------------------------------------------------------
# cp
# ---------------------------------------------------------------------------

def shell_cp(agent: Any, src: str, dst: str,
             aspects: Optional[UefiCmdAspects] = None) -> None:
    """
        Run the UEFI Shell ``cp`` command to copy ``src`` to ``dst``.

        :param agent: The :class:`UefiAgent` dispatching the command.
        :param src: Source path.
        :param dst: Destination path.
        :param aspects: Optional per-call :class:`UefiCmdAspects`.

        :raises: UefiShellError, ConfigurationError
    """
    effective = _resolve_cmd_aspects(agent=agent, aspects=aspects)

    command = "cp {} {}".format(
        _quote_shell_path(path=src),
        _quote_shell_path(path=dst),
    )

    status, stdout, stderr = _run_and_check(agent=agent, command=command, aspects=effective)

    if status != 0:
        _raise_shell_error(
            command=command,
            status=status,
            stdout=stdout,
            stderr=stderr,
            summary="UEFI Shell 'cp' returned a non-zero status.",
        )

    return


# ---------------------------------------------------------------------------
# cd
# ---------------------------------------------------------------------------

def shell_cd(agent: Any, path: str,
             aspects: Optional[UefiCmdAspects] = None) -> None:
    """
        Run the UEFI Shell ``cd`` command to change the shell's working
        directory.

        :param agent: The :class:`UefiAgent` dispatching the command.
        :param path: Target path.
        :param aspects: Optional per-call :class:`UefiCmdAspects`.

        :raises: UefiShellError, ConfigurationError
    """
    effective = _resolve_cmd_aspects(agent=agent, aspects=aspects)

    command = "cd {}".format(_quote_shell_path(path=path))

    status, stdout, stderr = _run_and_check(agent=agent, command=command, aspects=effective)

    if status != 0:
        _raise_shell_error(
            command=command,
            status=status,
            stdout=stdout,
            stderr=stderr,
            summary="UEFI Shell 'cd' returned a non-zero status.",
        )

    return


# ---------------------------------------------------------------------------
# bcfg boot dump
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BcfgEntry:
    """
        Single ``bcfg boot dump`` entry.

        :param index: Option index (0-based).
        :param active: ``True`` when the entry is not marked inactive.
        :param description: Friendly description string (``Desc -`` line).
        :param file_path: Device-path text from the ``DevPath -`` line; may
                          be ``None`` when the entry had no DevPath line.
    """
    index: int
    active: bool
    description: str
    file_path: Optional[str]


@dataclass(frozen=True)
class BcfgResult:
    """
        Result object returned by :func:`shell_bcfg_boot_dump`.

        :param entries: Tuple of :class:`BcfgEntry`.
        :param unparsed_lines: Output lines not matched by the parser.
        :param raw_stdout: Raw stdout returned by the agent.
        :param raw_stderr: Raw stderr returned by the agent.
        :param exit_status: Exit status returned by the shell.
    """
    entries: Tuple[BcfgEntry, ...]
    unparsed_lines: Tuple[str, ...]
    raw_stdout: str
    raw_stderr: str
    exit_status: int


def _flush_bcfg_entry(idx: Optional[int], active: bool, desc: str,
                      devpath: Optional[str],
                      collector: List[BcfgEntry]) -> None:
    """
        Append a :class:`BcfgEntry` to ``collector`` unless ``idx`` is
        ``None``, in which case the call is a no-op.  Used to flush the
        per-line accumulator inside :func:`shell_bcfg_boot_dump`.

        :param idx: Index of the current entry, or ``None`` when no
                    option header has been seen yet.
        :param active: Whether the entry is marked active.
        :param desc: Description string collected so far.
        :param devpath: Device path string collected so far, or ``None``.
        :param collector: List that collects the flushed entries.
    """
    if idx is None:
        return

    collector.append(BcfgEntry(
        index=idx,
        active=active,
        description=desc,
        file_path=devpath,
    ))
    return


def shell_bcfg_boot_dump(agent: Any,
                         aspects: Optional[UefiCmdAspects] = None) -> BcfgResult:
    """
        Run ``bcfg boot dump`` and parse the returned entries.

        :param agent: The :class:`UefiAgent` dispatching the command.
        :param aspects: Optional per-call :class:`UefiCmdAspects`.

        :returns: A :class:`BcfgResult`.
        :raises: UefiShellError, ConfigurationError
    """
    effective = _resolve_cmd_aspects(agent=agent, aspects=aspects)

    command = "bcfg boot dump"
    status, stdout, stderr = _run_and_check(agent=agent, command=command, aspects=effective)

    if status != 0:
        _raise_shell_error(
            command=command,
            status=status,
            stdout=stdout,
            stderr=stderr,
            summary="UEFI Shell 'bcfg boot dump' returned a non-zero status.",
        )

    entries_list: List[BcfgEntry] = []
    unparsed: List[str] = []

    lines = _split_output_lines(raw_stdout=stdout)

    current_index: Optional[int] = None
    current_active: bool = True
    current_desc: str = ""
    current_devpath: Optional[str] = None

    for line in lines:
        opt_match = _BCFG_OPTION_REGEX.match(line)
        if opt_match is not None:
            _flush_bcfg_entry(
                idx=current_index,
                active=current_active,
                desc=current_desc,
                devpath=current_devpath,
                collector=entries_list,
            )

            raw_index = opt_match.group("index")
            try:
                current_index = int(raw_index, 16)
            except ValueError:
                unparsed.append(line)
                current_index = None
                current_active = True
                current_desc = ""
                current_devpath = None
                continue

            active_text = opt_match.group("active") or ""
            # "Inactive" or "INACTIVE" anywhere in the suffix flips the flag.
            if "inactive" in active_text.lower():
                current_active = False
            else:
                current_active = True
            current_desc = ""
            current_devpath = None
            continue

        desc_match = _BCFG_DESC_REGEX.match(line)
        if desc_match is not None:
            current_desc = desc_match.group("desc").strip()
            continue

        dp_match = _BCFG_DEVPATH_REGEX.match(line)
        if dp_match is not None:
            current_devpath = dp_match.group("dp").strip()
            continue

        unparsed.append(line)

    _flush_bcfg_entry(
        idx=current_index,
        active=current_active,
        desc=current_desc,
        devpath=current_devpath,
        collector=entries_list,
    )

    unparsed_tuple = tuple(unparsed)
    _log_unparsed(command=command, unparsed_lines=unparsed_tuple)

    rtnval = BcfgResult(
        entries=tuple(entries_list),
        unparsed_lines=unparsed_tuple,
        raw_stdout=stdout,
        raw_stderr=stderr,
        exit_status=status,
    )
    return rtnval


# ---------------------------------------------------------------------------
# setvar
# ---------------------------------------------------------------------------

def shell_setvar(agent: Any, name: str, value: bytes,
                 guid: Optional[str] = None,
                 attributes: Optional[int] = None,
                 aspects: Optional[UefiCmdAspects] = None) -> None:
    """
        Run the UEFI Shell ``setvar`` command to set a variable to the
        supplied byte contents.  The byte payload is rendered as a
        hex-literal per Shell Specification §5.6.19.

        :param agent: The :class:`UefiAgent` dispatching the command.
        :param name: UEFI variable name.
        :param value: Raw byte contents to write.
        :param guid: Optional vendor GUID (brace-less dashed form).
        :param attributes: Optional attribute bitfield; when provided it is
                           rendered as ``-attr 0x%X``.
        :param aspects: Optional per-call :class:`UefiCmdAspects`.

        :raises: UefiShellError, ConfigurationError
    """
    effective = _resolve_cmd_aspects(agent=agent, aspects=aspects)

    if not isinstance(value, (bytes, bytearray)):
        err_msg_lines = [
            "shell_setvar: 'value' must be bytes or bytearray.",
            "GOT: type={}".format(type(value).__name__),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise TypeError(err_msg) from None

    parts: List[str] = ["setvar", name]
    if guid is not None:
        parts.append("-guid")
        parts.append(guid)
    if attributes is not None:
        parts.append("-attr")
        parts.append("0x{:X}".format(int(attributes)))

    hex_payload = bytes(value).hex().upper()
    if len(hex_payload) == 0:
        parts.append("=")
    else:
        parts.append("={}".format(hex_payload))

    command = " ".join(parts)

    status, stdout, stderr = _run_and_check(agent=agent, command=command, aspects=effective)

    if status != 0:
        _raise_shell_error(
            command=command,
            status=status,
            stdout=stdout,
            stderr=stderr,
            summary="UEFI Shell 'setvar' returned a non-zero status.",
        )

    return


# ---------------------------------------------------------------------------
# dmpstore
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DmpstoreEntry:
    """
        Single ``dmpstore`` record.

        :param name: Variable name.
        :param guid: Vendor GUID text (no braces).
        :param attributes: Attribute bitfield derived from the header text
                           (best-effort: bits are set according to the
                           ``NV``, ``BS``, ``RT``, ``AT`` tokens found).
        :param data: Raw byte contents decoded from the hexdump.
    """
    name: str
    guid: str
    attributes: int
    data: bytes


@dataclass(frozen=True)
class DmpstoreResult:
    """
        Result object returned by :func:`shell_dmpstore`.

        :param entries: Tuple of :class:`DmpstoreEntry`.
        :param unparsed_lines: Output lines not matched by the parser.
        :param raw_stdout: Raw stdout returned by the agent.
        :param raw_stderr: Raw stderr returned by the agent.
        :param exit_status: Exit status returned by the shell.
    """
    entries: Tuple[DmpstoreEntry, ...]
    unparsed_lines: Tuple[str, ...]
    raw_stdout: str
    raw_stderr: str
    exit_status: int


# Attribute-token-to-bit mapping for dmpstore headers.  The numerical bits
# match the UEFI Specification attribute definitions.
_DMPSTORE_ATTR_BITS: Dict[str, int] = {
    "NV": 0x00000001,
    "BS": 0x00000002,
    "RT": 0x00000004,
    "HR": 0x00000008,
    "AW": 0x00000010,
    "AT": 0x00000020,
}


def _decode_dmpstore_attrs(attrs_text: str) -> int:
    """
        Decode a ``dmpstore`` attribute header token (e.g. ``NV+BS+RT``)
        into the corresponding attribute bitfield.

        :param attrs_text: Plus-joined attribute token string.

        :returns: Attribute bitfield value.
    """
    result = 0
    if len(attrs_text) > 0:
        for token in attrs_text.split("+"):
            key = token.strip().upper()
            if key in _DMPSTORE_ATTR_BITS:
                result |= _DMPSTORE_ATTR_BITS[key]
    rtnval = result
    return rtnval


def _decode_hex_row(hexbytes_text: str) -> bytes:
    """
        Convert the hex byte segment of a dmpstore hex-dump row into
        :class:`bytes`.

        :param hexbytes_text: Whitespace-separated pairs of hex digits.

        :returns: The decoded byte sequence.
    """
    tokens = hexbytes_text.split()
    byte_list: List[int] = []
    for token in tokens:
        byte_list.append(int(token, 16))
    rtnval = bytes(byte_list)
    return rtnval


def _flush_dmpstore_entry(vname: Optional[str], vguid: Optional[str],
                          vattrs: int, vdata: bytearray,
                          collector: List[DmpstoreEntry]) -> None:
    """
        Append a :class:`DmpstoreEntry` to ``collector`` unless ``vname``
        or ``vguid`` is ``None``, in which case the call is a no-op.  Used
        to flush the per-line accumulator inside :func:`shell_dmpstore`.

        :param vname: Variable name or ``None`` when no header has been seen.
        :param vguid: Vendor GUID text or ``None``.
        :param vattrs: Attribute bitfield accumulated for this entry.
        :param vdata: Bytearray of data accumulated for this entry.
        :param collector: List that collects the flushed entries.
    """
    if vname is None or vguid is None:
        return

    collector.append(DmpstoreEntry(
        name=vname,
        guid=vguid,
        attributes=vattrs,
        data=bytes(vdata),
    ))
    return


def shell_dmpstore(agent: Any,
                   name: Optional[str] = None,
                   guid: Optional[str] = None,
                   aspects: Optional[UefiCmdAspects] = None) -> DmpstoreResult:
    """
        Run the UEFI Shell ``dmpstore`` command and parse the variable-store
        dump into typed entries.

        :param agent: The :class:`UefiAgent` dispatching the command.
        :param name: Optional variable-name filter.
        :param guid: Optional vendor-GUID filter (brace-less dashed form).
        :param aspects: Optional per-call :class:`UefiCmdAspects`.

        :returns: A :class:`DmpstoreResult`.
        :raises: UefiShellError, ConfigurationError
    """
    effective = _resolve_cmd_aspects(agent=agent, aspects=aspects)

    parts: List[str] = ["dmpstore"]
    if name is not None:
        parts.append(name)
    if guid is not None:
        parts.append("-guid")
        parts.append(guid)

    command = " ".join(parts)
    status, stdout, stderr = _run_and_check(agent=agent, command=command, aspects=effective)

    if status != 0:
        _raise_shell_error(
            command=command,
            status=status,
            stdout=stdout,
            stderr=stderr,
            summary="UEFI Shell 'dmpstore' returned a non-zero status.",
        )

    entries_list: List[DmpstoreEntry] = []
    unparsed: List[str] = []

    lines = _split_output_lines(raw_stdout=stdout)

    current_name: Optional[str] = None
    current_guid: Optional[str] = None
    current_attrs: int = 0
    current_data: bytearray = bytearray()
    inside_data_block: bool = False

    for line in lines:
        header_match = _DMPSTORE_HEADER_REGEX.match(line)
        if header_match is not None:
            _flush_dmpstore_entry(
                vname=current_name,
                vguid=current_guid,
                vattrs=current_attrs,
                vdata=current_data,
                collector=entries_list,
            )
            current_name = header_match.group("name")
            current_guid = header_match.group("guid")
            attrs_text = header_match.group("attrs_text")
            current_attrs = _decode_dmpstore_attrs(attrs_text=attrs_text)
            current_data = bytearray()
            inside_data_block = False
            continue

        if line.strip().lower() == "data:":
            inside_data_block = True
            continue

        size_match = _DMPSTORE_DATASIZE_REGEX.match(line)
        if size_match is not None:
            inside_data_block = False
            continue

        hex_match = _DMPSTORE_HEXLINE_REGEX.match(line)
        if hex_match is not None and current_name is not None:
            row_bytes = _decode_hex_row(hexbytes_text=hex_match.group("hexbytes"))
            current_data.extend(row_bytes)
            continue

        if inside_data_block is True:
            # Inside a data block but not matching a hex row — likely a
            # trailer or a blank transition.  Record the line as unparsed
            # only if it has content we did not expect.
            if line.strip() != "":
                unparsed.append(line)
            continue

        unparsed.append(line)

    _flush_dmpstore_entry(
        vname=current_name,
        vguid=current_guid,
        vattrs=current_attrs,
        vdata=current_data,
        collector=entries_list,
    )

    unparsed_tuple = tuple(unparsed)
    _log_unparsed(command=command, unparsed_lines=unparsed_tuple)

    rtnval = DmpstoreResult(
        entries=tuple(entries_list),
        unparsed_lines=unparsed_tuple,
        raw_stdout=stdout,
        raw_stderr=stderr,
        exit_status=status,
    )
    return rtnval


# ---------------------------------------------------------------------------
# reset
# ---------------------------------------------------------------------------

def shell_reset(agent: Any, mode: str = RESET_MODE_WARM,
                aspects: Optional[UefiCmdAspects] = None) -> None:
    """
        Run the UEFI Shell ``reset`` command with the supplied mode.  The
        wrapper does NOT wait for the target to reconnect — that is the
        responsibility of :meth:`UefiAgent.reboot`.

        :param agent: The :class:`UefiAgent` dispatching the command.
        :param mode: Reset mode; one of ``"warm"``, ``"cold"``, ``"shutdown"``.
        :param aspects: Optional per-call :class:`UefiCmdAspects`.

        :raises: UefiShellError, ConfigurationError
    """
    effective = _resolve_cmd_aspects(agent=agent, aspects=aspects)

    if not isinstance(mode, str):
        err_msg_lines = [
            "shell_reset: 'mode' must be a str.",
            "GOT: value={!r} type={}".format(mode, type(mode).__name__),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise TypeError(err_msg) from None

    if mode not in _VALID_RESET_MODES:
        err_msg_lines = [
            "shell_reset: 'mode' is not a valid reset mode.",
            "GOT: value={!r}".format(mode),
            "ALLOWED: {}".format(", ".join(_VALID_RESET_MODES)),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise ValueError(err_msg) from None

    flag = _RESET_FLAG_BY_MODE[mode]
    command = "reset {}".format(flag)

    try:
        status, stdout, stderr = _run_and_check(agent=agent, command=command, aspects=effective)
    except ConnectionError as cerr:
        # Expected: the reset command typically drops the transport before a
        # clean prompt arrives.  Swallow per Requirement 9.5.
        _LOGGER.debug(
            "uefi.shell: shell_reset swallowing ConnectionError as expected: %r",
            cerr,
        )
        return

    if status != 0:
        _raise_shell_error(
            command=command,
            status=status,
            stdout=stdout,
            stderr=stderr,
            summary="UEFI Shell 'reset' returned a non-zero status.",
        )

    return


# ---------------------------------------------------------------------------
# dh
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DhEntry:
    """
        Single ``dh`` handle entry.

        :param handle_id: Integer handle id.
        :param protocols: Tuple of protocol names supported by the handle.
    """
    handle_id: int
    protocols: Tuple[str, ...]


@dataclass(frozen=True)
class DhResult:
    """
        Result object returned by :func:`shell_dh`.

        :param entries: Tuple of :class:`DhEntry`.
        :param unparsed_lines: Output lines not matched by the parser.
        :param raw_stdout: Raw stdout returned by the agent.
        :param raw_stderr: Raw stderr returned by the agent.
        :param exit_status: Exit status returned by the shell.
    """
    entries: Tuple[DhEntry, ...]
    unparsed_lines: Tuple[str, ...]
    raw_stdout: str
    raw_stderr: str
    exit_status: int


def shell_dh(agent: Any,
             aspects: Optional[UefiCmdAspects] = None) -> DhResult:
    """
        Run the UEFI Shell ``dh`` command (driver-handle dump) and parse
        the handle list.

        :param agent: The :class:`UefiAgent` dispatching the command.
        :param aspects: Optional per-call :class:`UefiCmdAspects`.

        :returns: A :class:`DhResult`.
        :raises: UefiShellError, ConfigurationError
    """
    effective = _resolve_cmd_aspects(agent=agent, aspects=aspects)

    command = "dh"
    status, stdout, stderr = _run_and_check(agent=agent, command=command, aspects=effective)

    if status != 0:
        _raise_shell_error(
            command=command,
            status=status,
            stdout=stdout,
            stderr=stderr,
            summary="UEFI Shell 'dh' returned a non-zero status.",
        )

    entries_list: List[DhEntry] = []
    unparsed: List[str] = []

    lines = _split_output_lines(raw_stdout=stdout)
    for line in lines:
        match = _DH_HANDLE_LINE_REGEX.match(line)
        if match is None:
            unparsed.append(line)
            continue

        raw_handle = match.group("handle")
        try:
            handle_value = int(raw_handle, 16)
        except ValueError:
            unparsed.append(line)
            continue

        protocols_text = match.group("protocols").strip()
        if len(protocols_text) == 0:
            protocols_tuple: Tuple[str, ...] = ()
        else:
            proto_parts = [part.strip() for part in protocols_text.split() if part.strip() != ""]
            protocols_tuple = tuple(proto_parts)

        entries_list.append(DhEntry(
            handle_id=handle_value,
            protocols=protocols_tuple,
        ))

    unparsed_tuple = tuple(unparsed)
    _log_unparsed(command=command, unparsed_lines=unparsed_tuple)

    rtnval = DhResult(
        entries=tuple(entries_list),
        unparsed_lines=unparsed_tuple,
        raw_stdout=stdout,
        raw_stderr=stderr,
        exit_status=status,
    )
    return rtnval


# ---------------------------------------------------------------------------
# memmap
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MemmapEntry:
    """
        Single ``memmap`` entry.

        :param memory_type: Memory-type name exactly as emitted by the shell.
        :param start: First physical byte (inclusive).
        :param end: Last physical byte (inclusive).
        :param pages: Page count.
        :param attributes: Raw attributes bitfield.
    """
    memory_type: str
    start: int
    end: int
    pages: int
    attributes: int


@dataclass(frozen=True)
class MemmapResult:
    """
        Result object returned by :func:`shell_memmap`.

        :param entries: Tuple of :class:`MemmapEntry`.
        :param unparsed_lines: Output lines not matched by the parser.
        :param raw_stdout: Raw stdout returned by the agent.
        :param raw_stderr: Raw stderr returned by the agent.
        :param exit_status: Exit status returned by the shell.
    """
    entries: Tuple[MemmapEntry, ...]
    unparsed_lines: Tuple[str, ...]
    raw_stdout: str
    raw_stderr: str
    exit_status: int


def shell_memmap(agent: Any,
                 aspects: Optional[UefiCmdAspects] = None) -> MemmapResult:
    """
        Run the UEFI Shell ``memmap`` command and parse the memory map.

        :param agent: The :class:`UefiAgent` dispatching the command.
        :param aspects: Optional per-call :class:`UefiCmdAspects`.

        :returns: A :class:`MemmapResult`.
        :raises: UefiShellError, ConfigurationError
    """
    effective = _resolve_cmd_aspects(agent=agent, aspects=aspects)

    command = "memmap"
    status, stdout, stderr = _run_and_check(agent=agent, command=command, aspects=effective)

    if status != 0:
        _raise_shell_error(
            command=command,
            status=status,
            stdout=stdout,
            stderr=stderr,
            summary="UEFI Shell 'memmap' returned a non-zero status.",
        )

    entries_list: List[MemmapEntry] = []
    unparsed: List[str] = []

    lines = _split_output_lines(raw_stdout=stdout)
    for line in lines:
        match = _MEMMAP_ENTRY_REGEX.match(line)
        if match is None:
            unparsed.append(line)
            continue

        mtype_text = match.group("mtype")
        try:
            start_value = int(match.group("start"), 16)
            end_value = int(match.group("end"), 16)
            pages_value = int(match.group("pages"), 16)
            attr_value = int(match.group("attr"), 16)
        except ValueError:
            unparsed.append(line)
            continue

        entries_list.append(MemmapEntry(
            memory_type=mtype_text,
            start=start_value,
            end=end_value,
            pages=pages_value,
            attributes=attr_value,
        ))

    unparsed_tuple = tuple(unparsed)
    _log_unparsed(command=command, unparsed_lines=unparsed_tuple)

    rtnval = MemmapResult(
        entries=tuple(entries_list),
        unparsed_lines=unparsed_tuple,
        raw_stdout=stdout,
        raw_stderr=stderr,
        exit_status=status,
    )
    return rtnval


# ---------------------------------------------------------------------------
# smbiosview
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SmbiosviewResult:
    """
        Result object returned by :func:`shell_smbiosview`.

        :param structures: Tuple of loose dictionaries; one per parsed record.
                           Keys are ``type``, ``handle``, ``length`` plus any
                           key/value fields that were successfully captured
                           from the record body.
        :param unparsed_lines: Output lines not matched by the parser.
        :param raw_stdout: Raw stdout returned by the agent.
        :param raw_stderr: Raw stderr returned by the agent.
        :param exit_status: Exit status returned by the shell.
    """
    structures: Tuple[Dict[str, Any], ...]
    unparsed_lines: Tuple[str, ...]
    raw_stdout: str
    raw_stderr: str
    exit_status: int


def shell_smbiosview(agent: Any,
                     aspects: Optional[UefiCmdAspects] = None) -> SmbiosviewResult:
    """
        Run the UEFI Shell ``smbiosview`` command and parse each SMBIOS
        structure into a loose dictionary.  Because SMBIOS output varies
        widely between firmware vendors, the parser is deliberately lenient
        and collects any ``Key: Value`` shaped line into the active record.

        :param agent: The :class:`UefiAgent` dispatching the command.
        :param aspects: Optional per-call :class:`UefiCmdAspects`.

        :returns: A :class:`SmbiosviewResult`.
        :raises: UefiShellError, ConfigurationError
    """
    effective = _resolve_cmd_aspects(agent=agent, aspects=aspects)

    command = "smbiosview"
    status, stdout, stderr = _run_and_check(agent=agent, command=command, aspects=effective)

    if status != 0:
        _raise_shell_error(
            command=command,
            status=status,
            stdout=stdout,
            stderr=stderr,
            summary="UEFI Shell 'smbiosview' returned a non-zero status.",
        )

    structures_list: List[Dict[str, Any]] = []
    unparsed: List[str] = []

    lines = _split_output_lines(raw_stdout=stdout)

    current_record: Optional[Dict[str, Any]] = None

    for line in lines:
        header_match = _SMBIOSVIEW_HEADER_REGEX.match(line)
        if header_match is not None:
            if current_record is not None:
                structures_list.append(current_record)
            try:
                rec_type = int(header_match.group("type"))
                rec_handle = int(header_match.group("handle"), 16)
                rec_length = int(header_match.group("length"))
            except ValueError:
                unparsed.append(line)
                current_record = None
                continue

            current_record = {
                "type": rec_type,
                "handle": rec_handle,
                "length": rec_length,
                "fields": {},
            }
            continue

        if current_record is None:
            unparsed.append(line)
            continue

        field_match = _SMBIOSVIEW_FIELD_REGEX.match(line)
        if field_match is None:
            unparsed.append(line)
            continue

        key_text = field_match.group("key").strip()
        value_text = field_match.group("value").strip()
        fields_dict = current_record["fields"]
        fields_dict[key_text] = value_text

    if current_record is not None:
        structures_list.append(current_record)

    unparsed_tuple = tuple(unparsed)
    _log_unparsed(command=command, unparsed_lines=unparsed_tuple)

    rtnval = SmbiosviewResult(
        structures=tuple(structures_list),
        unparsed_lines=unparsed_tuple,
        raw_stdout=stdout,
        raw_stderr=stderr,
        exit_status=status,
    )
    return rtnval


# ---------------------------------------------------------------------------
# drivers
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DriverEntry:
    """
        Single ``drivers`` entry.

        :param handle_id: Integer handle id.
        :param version: Driver version text (hex).
        :param name: Driver friendly name.
    """
    handle_id: int
    version: str
    name: str


@dataclass(frozen=True)
class DriversResult:
    """
        Result object returned by :func:`shell_drivers`.

        :param entries: Tuple of :class:`DriverEntry`.
        :param unparsed_lines: Output lines not matched by the parser.
        :param raw_stdout: Raw stdout returned by the agent.
        :param raw_stderr: Raw stderr returned by the agent.
        :param exit_status: Exit status returned by the shell.
    """
    entries: Tuple[DriverEntry, ...]
    unparsed_lines: Tuple[str, ...]
    raw_stdout: str
    raw_stderr: str
    exit_status: int


def shell_drivers(agent: Any,
                  aspects: Optional[UefiCmdAspects] = None) -> DriversResult:
    """
        Run the UEFI Shell ``drivers`` command and parse the driver list.

        :param agent: The :class:`UefiAgent` dispatching the command.
        :param aspects: Optional per-call :class:`UefiCmdAspects`.

        :returns: A :class:`DriversResult`.
        :raises: UefiShellError, ConfigurationError
    """
    effective = _resolve_cmd_aspects(agent=agent, aspects=aspects)

    command = "drivers"
    status, stdout, stderr = _run_and_check(agent=agent, command=command, aspects=effective)

    if status != 0:
        _raise_shell_error(
            command=command,
            status=status,
            stdout=stdout,
            stderr=stderr,
            summary="UEFI Shell 'drivers' returned a non-zero status.",
        )

    entries_list: List[DriverEntry] = []
    unparsed: List[str] = []

    lines = _split_output_lines(raw_stdout=stdout)
    for line in lines:
        match = _DRIVERS_ENTRY_REGEX.match(line)
        if match is None:
            unparsed.append(line)
            continue

        raw_handle = match.group("handle")
        version_text = match.group("version")
        name_text = match.group("name").strip()

        try:
            handle_value = int(raw_handle, 16)
        except ValueError:
            unparsed.append(line)
            continue

        entries_list.append(DriverEntry(
            handle_id=handle_value,
            version=version_text,
            name=name_text,
        ))

    unparsed_tuple = tuple(unparsed)
    _log_unparsed(command=command, unparsed_lines=unparsed_tuple)

    rtnval = DriversResult(
        entries=tuple(entries_list),
        unparsed_lines=unparsed_tuple,
        raw_stdout=stdout,
        raw_stderr=stderr,
        exit_status=status,
    )
    return rtnval


# ---------------------------------------------------------------------------
# devtree
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DevtreeNode:
    """
        Single node in the ``devtree`` hierarchy.

        :param depth: Zero-based indentation depth.
        :param handle_id: Integer handle id.
        :param name: Node text (commonly the device name or controller label).
    """
    depth: int
    handle_id: int
    name: str


@dataclass(frozen=True)
class DevtreeResult:
    """
        Result object returned by :func:`shell_devtree`.

        :param tree_text: Raw indentation-preserved text rendering of the
                          device tree.
        :param nodes: Tuple of :class:`DevtreeNode` parsed in traversal order.
        :param unparsed_lines: Output lines not matched by the parser.
        :param raw_stdout: Raw stdout returned by the agent.
        :param raw_stderr: Raw stderr returned by the agent.
        :param exit_status: Exit status returned by the shell.
    """
    tree_text: str
    nodes: Tuple[DevtreeNode, ...]
    unparsed_lines: Tuple[str, ...]
    raw_stdout: str
    raw_stderr: str
    exit_status: int


def shell_devtree(agent: Any,
                  aspects: Optional[UefiCmdAspects] = None) -> DevtreeResult:
    """
        Run the UEFI Shell ``devtree`` command and parse the device tree.
        The tree is rendered back as a string with indentation preserved
        and also returned as a flat list of :class:`DevtreeNode` with the
        captured ``depth`` so callers can rebuild a tree if needed.

        :param agent: The :class:`UefiAgent` dispatching the command.
        :param aspects: Optional per-call :class:`UefiCmdAspects`.

        :returns: A :class:`DevtreeResult`.
        :raises: UefiShellError, ConfigurationError
    """
    effective = _resolve_cmd_aspects(agent=agent, aspects=aspects)

    command = "devtree"
    status, stdout, stderr = _run_and_check(agent=agent, command=command, aspects=effective)

    if status != 0:
        _raise_shell_error(
            command=command,
            status=status,
            stdout=stdout,
            stderr=stderr,
            summary="UEFI Shell 'devtree' returned a non-zero status.",
        )

    nodes_list: List[DevtreeNode] = []
    unparsed: List[str] = []
    tree_lines: List[str] = []

    lines = _split_output_lines(raw_stdout=stdout)
    for line in lines:
        match = _DEVTREE_NODE_REGEX.match(line)
        if match is None:
            unparsed.append(line)
            continue

        indent_text = match.group("indent")
        raw_handle = match.group("handle")
        name_text = match.group("name").strip()

        try:
            handle_value = int(raw_handle, 16)
        except ValueError:
            unparsed.append(line)
            continue

        # Depth is measured in units of 2 spaces when possible; fall back to
        # raw character count when the indentation is not a multiple of 2.
        indent_chars = len(indent_text)
        if indent_chars % 2 == 0:
            depth_value = indent_chars // 2
        else:
            depth_value = indent_chars

        nodes_list.append(DevtreeNode(
            depth=depth_value,
            handle_id=handle_value,
            name=name_text,
        ))
        tree_lines.append(line)

    unparsed_tuple = tuple(unparsed)
    _log_unparsed(command=command, unparsed_lines=unparsed_tuple)

    tree_text = os.linesep.join(tree_lines)

    rtnval = DevtreeResult(
        tree_text=tree_text,
        nodes=tuple(nodes_list),
        unparsed_lines=unparsed_tuple,
        raw_stdout=stdout,
        raw_stderr=stderr,
        exit_status=status,
    )
    return rtnval


__all__ = [
    "BcfgEntry",
    "BcfgResult",
    "DevtreeNode",
    "DevtreeResult",
    "DhEntry",
    "DhResult",
    "DmpstoreEntry",
    "DmpstoreResult",
    "DriverEntry",
    "DriversResult",
    "LsEntry",
    "LsResult",
    "MapEntry",
    "MapResult",
    "MemmapEntry",
    "MemmapResult",
    "RESET_MODE_COLD",
    "RESET_MODE_SHUTDOWN",
    "RESET_MODE_WARM",
    "SmbiosviewResult",
    "shell_bcfg_boot_dump",
    "shell_cd",
    "shell_cp",
    "shell_devtree",
    "shell_dh",
    "shell_dmpstore",
    "shell_drivers",
    "shell_ls",
    "shell_map",
    "shell_memmap",
    "shell_reset",
    "shell_setvar",
    "shell_smbiosview",
]
