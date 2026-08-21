"""
    .. module:: test_uefishell
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Unit tests for the :mod:`ccai.protocols.uefi.uefishell`
               module.  Tests use a hand-rolled :class:`_FakeUefiAgent`
               whose :meth:`run_cmd` returns canned ``(status, stdout,
               stderr)`` tuples looked up by the exact command text so no
               real transports, ports, or subprocesses are touched.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Code Craft AI LLC"
__credits__ = []


from ccai import testbase

import os

from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from ccai.protocols.uefi.uefiaspects import (
    DEFAULT_CMD_ASPECTS,
    UefiCmdAspects,
)
from ccai.protocols.uefi.uefiexceptions import UefiShellError

from ccai.protocols.uefi.uefishell import (
    BcfgEntry,
    BcfgResult,
    DevtreeNode,
    DevtreeResult,
    DhEntry,
    DhResult,
    DmpstoreEntry,
    DmpstoreResult,
    DriverEntry,
    DriversResult,
    LsEntry,
    LsResult,
    MapEntry,
    MapResult,
    MemmapEntry,
    MemmapResult,
    RESET_MODE_COLD,
    RESET_MODE_SHUTDOWN,
    RESET_MODE_WARM,
    SmbiosviewResult,
    shell_bcfg_boot_dump,
    shell_cd,
    shell_cp,
    shell_devtree,
    shell_dh,
    shell_dmpstore,
    shell_drivers,
    shell_ls,
    shell_map,
    shell_memmap,
    shell_reset,
    shell_setvar,
    shell_smbiosview,
)


# ---------------------------------------------------------------------------
# Fake agent
# ---------------------------------------------------------------------------

class _FakeUefiAgent:
    """
        Hand-rolled stand-in for :class:`UefiAgent`.  Records each
        :meth:`run_cmd` invocation and returns canned ``(status, stdout,
        stderr)`` tuples looked up by the exact command text.  Supports
        priming a side-effect callable (for example to raise
        :class:`ConnectionError`).

        :param aspects: Default aspects surfaced via ``self._aspects`` to
                        parallel the real agent's underscore attribute.
    """

    def __init__(self, aspects: Optional[UefiCmdAspects] = None) -> None:
        """
            Initialize an empty canned-response map and invocation log.

            :param aspects: Default aspects.
        """
        if aspects is None:
            self._aspects = DEFAULT_CMD_ASPECTS
        else:
            self._aspects = aspects

        self.responses: Dict[str, Tuple[int, str, str]] = {}
        self.side_effects: Dict[str, Callable[[], None]] = {}
        self.calls: List[Dict[str, Any]] = []
        return

    def set_response(self, command: str, status: int, stdout: str, stderr: str) -> None:
        """
            Prime a canned response for a specific command text.

            :param command: The command text.
            :param status: Status to return.
            :param stdout: Stdout text to return.
            :param stderr: Stderr text to return.
        """
        self.responses[command] = (status, stdout, stderr)
        return

    def set_side_effect(self, command: str, effect: Callable[[], None]) -> None:
        """
            Prime a callable to invoke before returning the response.  The
            callable may raise to simulate connection loss.

            :param command: The command text to match.
            :param effect: Callable invoked for side effects; may raise.
        """
        self.side_effects[command] = effect
        return

    def run_cmd(self, command: str,
                      exp_status: Union[int, Tuple[int, ...]] = 0,
                      user: Optional[str] = None,
                      aspects: Optional[UefiCmdAspects] = None) -> Tuple[int, str, str]:
        """
            Record the invocation, run the primed side effect, and return
            the canned response.

            :param command: Command text.
            :param exp_status: Expected status.
            :param user: Optional role name.
            :param aspects: Per-call aspects.

            :returns: ``(status, stdout, stderr)`` triple.
        """
        self.calls.append({
            "command": command,
            "exp_status": exp_status,
            "user": user,
            "aspects": aspects,
        })

        if command in self.side_effects:
            effect = self.side_effects[command]
            effect()

        if command in self.responses:
            rtnval = self.responses[command]
            return rtnval

        rtnval = (0, "", "")
        return rtnval


# ---------------------------------------------------------------------------
# shell_map
# ---------------------------------------------------------------------------

_MAP_FIXTURE = (
    "Mapping table" + os.linesep
    + "  FS0: Alias(s):HD1b:;BLK1:" + os.linesep
    + "      PciRoot(0x0)/Pci(0x1F,0x2)/Sata(0x0,0x0,0x0)/HD(1,GPT,ABC)" + os.linesep
    + "  BLK0: Alias(s):" + os.linesep
    + "      PciRoot(0x0)/Pci(0x1F,0x2)/Sata(0x0,0x0,0x0)" + os.linesep
)


# ----------------------------------------------------------------------
# MapWrapperTests
# ----------------------------------------------------------------------

"""
    Tests for :func:`shell_map`.
"""

def test_map_wrapper_tests_happy_path_parses_entries() -> None:
    """
        Canned ``map`` stdout must resolve into two entries with
        device paths and aliases populated.
    """
    agent = _FakeUefiAgent()
    agent.set_response(command="map", status=0, stdout=_MAP_FIXTURE, stderr="")

    result = shell_map(agent=agent)

    testbase.assert_true(isinstance(result, MapResult))
    testbase.assert_equal(len(result.entries), 2)

    fs0 = result.entries[0]
    testbase.assert_equal(fs0.alias, "FS0")
    testbase.assert_true("PciRoot" in fs0.device_path)
    testbase.assert_true("HD1b" in fs0.aliases[0])

    blk0 = result.entries[1]
    testbase.assert_equal(blk0.alias, "BLK0")
    testbase.assert_true("PciRoot" in blk0.device_path)
    return

def test_map_wrapper_tests_non_zero_status_raises_shell_error() -> None:
    """
        Non-zero exit status must raise :class:`UefiShellError` with
        command/status/stdout/stderr carried on the exception.
    """
    agent = _FakeUefiAgent()
    agent.set_response(command="map", status=7, stdout="boom\n", stderr="fatal\n")

    try:
        shell_map(agent=agent)
        raise AssertionError("expected UefiShellError")
    except UefiShellError as uerr:
        testbase.assert_equal(uerr.command, "map")
        testbase.assert_equal(uerr.status, 7)
        testbase.assert_equal(uerr.stdout, "boom\n")
        testbase.assert_equal(uerr.stderr, "fatal\n")
    return

def test_map_wrapper_tests_unparsed_lines_collected() -> None:
    """
        Injected garbage should appear verbatim in ``unparsed_lines``.
    """
    garbage = (
        "Mapping table" + os.linesep
        + "unrecognized firmware banner" + os.linesep
        + "  FS0: Alias(s):HD1b:" + os.linesep
        + "      PciRoot(0x0)/Pci(0x1F,0x2)" + os.linesep
        + "  ???????  garbage trailer" + os.linesep
    )
    agent = _FakeUefiAgent()
    agent.set_response(command="map", status=0, stdout=garbage, stderr="")

    result = shell_map(agent=agent)

    testbase.assert_true("unrecognized firmware banner" in result.unparsed_lines)
    testbase.assert_true(
        any("garbage trailer" in line for line in result.unparsed_lines)
    )
    return

def test_map_wrapper_tests_aspects_passthrough() -> None:
    """
        The caller-supplied aspects must reach the agent verbatim.
    """
    custom = UefiCmdAspects()
    agent = _FakeUefiAgent()
    agent.set_response(command="map", status=0, stdout=_MAP_FIXTURE, stderr="")

    shell_map(agent=agent, aspects=custom)

    testbase.assert_equal(len(agent.calls), 1)
    testbase.assert_true(agent.calls[0]["aspects"] is custom)
    return


# ---------------------------------------------------------------------------
# shell_ls
# ---------------------------------------------------------------------------

_LS_FIXTURE = (
    "<DIR>     05/11/2024  14:32                   EFI" + os.linesep
    + "A         05/11/2024  14:32         1,234,567 bootx64.efi" + os.linesep
)


# ----------------------------------------------------------------------
# LsWrapperTests
# ----------------------------------------------------------------------

"""
    Tests for :func:`shell_ls`.
"""

def test_ls_wrapper_tests_happy_path() -> None:
    """
        Canned ``ls`` stdout must resolve into two entries.
    """
    agent = _FakeUefiAgent()
    agent.set_response(command="ls", status=0, stdout=_LS_FIXTURE, stderr="")

    result = shell_ls(agent=agent)

    testbase.assert_true(isinstance(result, LsResult))
    testbase.assert_equal(len(result.entries), 2)

    dir_entry = result.entries[0]
    testbase.assert_equal(dir_entry.size, 0)
    testbase.assert_equal(dir_entry.name, "EFI")

    file_entry = result.entries[1]
    testbase.assert_equal(file_entry.size, 1234567)
    testbase.assert_equal(file_entry.name, "bootx64.efi")
    return

def test_ls_wrapper_tests_path_is_quoted() -> None:
    """
        When a path is supplied it must be quoted in the command.
    """
    agent = _FakeUefiAgent()
    quoted_cmd = 'ls "FS0:\\EFI\\Boot"'
    agent.set_response(command=quoted_cmd, status=0, stdout="", stderr="")

    shell_ls(agent=agent, path="FS0:\\EFI\\Boot")

    testbase.assert_equal(agent.calls[0]["command"], quoted_cmd)
    return

def test_ls_wrapper_tests_non_zero_raises() -> None:
    """
        Non-zero exit raises :class:`UefiShellError`.
    """
    agent = _FakeUefiAgent()
    agent.set_response(command="ls", status=1, stdout="x", stderr="y")

    try:
        shell_ls(agent=agent)
        raise AssertionError("expected UefiShellError")
    except UefiShellError as uerr:
        testbase.assert_equal(uerr.status, 1)
    return

def test_ls_wrapper_tests_unparsed_lines() -> None:
    """
        Garbage lines must appear in ``unparsed_lines``.
    """
    garbage = "this does not match the ls format" + os.linesep
    agent = _FakeUefiAgent()
    agent.set_response(command="ls", status=0, stdout=garbage, stderr="")

    result = shell_ls(agent=agent)

    testbase.assert_true("this does not match the ls format" in result.unparsed_lines)
    return


# ---------------------------------------------------------------------------
# shell_cp
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# CpWrapperTests
# ----------------------------------------------------------------------

"""
    Tests for :func:`shell_cp`.
"""

def test_cp_wrapper_tests_command_string_is_quoted() -> None:
    """
        The ``cp`` wrapper must quote both operands.
    """
    agent = _FakeUefiAgent()
    expected_cmd = 'cp "FS0:\\a.efi" "FS0:\\b.efi"'
    agent.set_response(command=expected_cmd, status=0, stdout="", stderr="")

    shell_cp(agent=agent, src="FS0:\\a.efi", dst="FS0:\\b.efi")

    testbase.assert_equal(agent.calls[0]["command"], expected_cmd)
    return

def test_cp_wrapper_tests_failure_raises_shell_error() -> None:
    """
        Non-zero exit raises :class:`UefiShellError` with context.
    """
    agent = _FakeUefiAgent()
    expected_cmd = 'cp "src" "dst"'
    agent.set_response(command=expected_cmd, status=5, stdout="nope", stderr="err")

    try:
        shell_cp(agent=agent, src="src", dst="dst")
        raise AssertionError("expected UefiShellError")
    except UefiShellError as uerr:
        testbase.assert_equal(uerr.command, expected_cmd)
        testbase.assert_equal(uerr.status, 5)
        testbase.assert_equal(uerr.stderr, "err")
    return

def test_cp_wrapper_tests_aspects_passthrough() -> None:
    """
        Aspects supplied by the caller reach the agent.
    """
    custom = UefiCmdAspects()
    agent = _FakeUefiAgent()
    expected_cmd = 'cp "s" "d"'
    agent.set_response(command=expected_cmd, status=0, stdout="", stderr="")

    shell_cp(agent=agent, src="s", dst="d", aspects=custom)

    testbase.assert_true(agent.calls[0]["aspects"] is custom)
    return


# ---------------------------------------------------------------------------
# shell_cd
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# CdWrapperTests
# ----------------------------------------------------------------------

"""
    Tests for :func:`shell_cd`.
"""

def test_cd_wrapper_tests_command_string() -> None:
    """
        The ``cd`` wrapper must quote its path operand.
    """
    agent = _FakeUefiAgent()
    expected_cmd = 'cd "FS0:\\"'
    agent.set_response(command=expected_cmd, status=0, stdout="", stderr="")

    shell_cd(agent=agent, path="FS0:\\")

    testbase.assert_equal(agent.calls[0]["command"], expected_cmd)
    return

def test_cd_wrapper_tests_failure_raises() -> None:
    """
        Non-zero exit raises :class:`UefiShellError`.
    """
    agent = _FakeUefiAgent()
    expected_cmd = 'cd "FS0:\\missing"'
    agent.set_response(command=expected_cmd, status=2, stdout="no", stderr="missing")

    try:
        shell_cd(agent=agent, path="FS0:\\missing")
        raise AssertionError("expected UefiShellError")
    except UefiShellError as uerr:
        testbase.assert_equal(uerr.status, 2)
    return


# ---------------------------------------------------------------------------
# shell_bcfg_boot_dump
# ---------------------------------------------------------------------------

_BCFG_FIXTURE = (
    "Option: 00. Variable: Boot0000" + os.linesep
    + "  Desc    - UEFI OS" + os.linesep
    + "  DevPath - PciRoot(0x0)/Pci(0x1F,0x2)/Sata(0x0,0x0,0x0)/HD(1,GPT,X)/\\EFI\\BOOT\\BOOTX64.EFI" + os.linesep
    + "Option: 01. Variable: Boot0001 Inactive" + os.linesep
    + "  Desc    - Network" + os.linesep
    + "  DevPath - PciRoot(0x0)/Pci(0x1F,0x6)/MAC(0011223344,0)" + os.linesep
)


# ----------------------------------------------------------------------
# BcfgWrapperTests
# ----------------------------------------------------------------------

"""
    Tests for :func:`shell_bcfg_boot_dump`.
"""

def test_bcfg_wrapper_tests_happy_path() -> None:
    """
        Canned ``bcfg boot dump`` stdout must resolve to two entries.
    """
    agent = _FakeUefiAgent()
    agent.set_response(command="bcfg boot dump", status=0,
                       stdout=_BCFG_FIXTURE, stderr="")

    result = shell_bcfg_boot_dump(agent=agent)

    testbase.assert_true(isinstance(result, BcfgResult))
    testbase.assert_equal(len(result.entries), 2)

    testbase.assert_equal(result.entries[0].index, 0)
    testbase.assert_true(result.entries[0].active)
    testbase.assert_equal(result.entries[0].description, "UEFI OS")
    testbase.assert_true(result.entries[0].file_path is not None)

    testbase.assert_equal(result.entries[1].index, 1)
    testbase.assert_false(result.entries[1].active)
    testbase.assert_equal(result.entries[1].description, "Network")
    return

def test_bcfg_wrapper_tests_non_zero_raises() -> None:
    """
        Non-zero exit raises :class:`UefiShellError`.
    """
    agent = _FakeUefiAgent()
    agent.set_response(command="bcfg boot dump", status=1,
                       stdout="s", stderr="e")

    try:
        shell_bcfg_boot_dump(agent=agent)
        raise AssertionError("expected UefiShellError")
    except UefiShellError as uerr:
        testbase.assert_equal(uerr.status, 1)
    return

def test_bcfg_wrapper_tests_unparsed_lines() -> None:
    """
        Unrecognized banner lines must appear in ``unparsed_lines``.
    """
    garbage = "strange firmware banner" + os.linesep + _BCFG_FIXTURE
    agent = _FakeUefiAgent()
    agent.set_response(command="bcfg boot dump", status=0,
                       stdout=garbage, stderr="")

    result = shell_bcfg_boot_dump(agent=agent)

    testbase.assert_true("strange firmware banner" in result.unparsed_lines)
    return


# ---------------------------------------------------------------------------
# shell_setvar
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# SetvarWrapperTests
# ----------------------------------------------------------------------

"""
    Tests for :func:`shell_setvar`.
"""

def test_setvar_wrapper_tests_command_assembly() -> None:
    """
        ``setvar`` must render its hex payload and optional flags.
    """
    agent = _FakeUefiAgent()
    expected_cmd = "setvar MyVar -guid 8BE4DF61-93CA-11D2-AA0D-00E098032B8C -attr 0x7 =DEADBEEF"
    agent.set_response(command=expected_cmd, status=0, stdout="", stderr="")

    shell_setvar(
        agent=agent,
        name="MyVar",
        value=bytes.fromhex("DEADBEEF"),
        guid="8BE4DF61-93CA-11D2-AA0D-00E098032B8C",
        attributes=0x7,
    )

    testbase.assert_equal(agent.calls[0]["command"], expected_cmd)
    return

def test_setvar_wrapper_tests_failure_raises() -> None:
    """
        Non-zero exit raises :class:`UefiShellError`.
    """
    agent = _FakeUefiAgent()
    expected_cmd = "setvar MyVar =01"
    agent.set_response(command=expected_cmd, status=9, stdout="o", stderr="e")

    try:
        shell_setvar(agent=agent, name="MyVar", value=b"\x01")
        raise AssertionError("expected UefiShellError")
    except UefiShellError as uerr:
        testbase.assert_equal(uerr.status, 9)
    return

def test_setvar_wrapper_tests_non_bytes_rejected() -> None:
    """
        ``value`` must be ``bytes``; anything else raises.
    """
    agent = _FakeUefiAgent()

    try:
        shell_setvar(agent=agent, name="MyVar", value="not bytes")  # type: ignore[arg-type]
        raise AssertionError("expected TypeError")
    except TypeError:
        pass
    return


# ---------------------------------------------------------------------------
# shell_dmpstore
# ---------------------------------------------------------------------------

_DMPSTORE_FIXTURE = (
    "Variable NV+BS 'GlobalDontKnow':{8BE4DF61-93CA-11D2-AA0D-00E098032B8C}" + os.linesep
    + "  DataSize = 0x08" + os.linesep
    + "  Data:" + os.linesep
    + "  00000000: 01 02 03 04 05 06 07 08                         *........*" + os.linesep
)


# ----------------------------------------------------------------------
# DmpstoreWrapperTests
# ----------------------------------------------------------------------

"""
    Tests for :func:`shell_dmpstore`.
"""

def test_dmpstore_wrapper_tests_happy_path() -> None:
    """
        Canned ``dmpstore`` stdout must resolve to one entry with
        decoded data.
    """
    agent = _FakeUefiAgent()
    agent.set_response(command="dmpstore", status=0,
                       stdout=_DMPSTORE_FIXTURE, stderr="")

    result = shell_dmpstore(agent=agent)

    testbase.assert_true(isinstance(result, DmpstoreResult))
    testbase.assert_equal(len(result.entries), 1)

    entry = result.entries[0]
    testbase.assert_equal(entry.name, "GlobalDontKnow")
    testbase.assert_equal(entry.guid, "8BE4DF61-93CA-11D2-AA0D-00E098032B8C")
    testbase.assert_equal(entry.data, b"\x01\x02\x03\x04\x05\x06\x07\x08")
    # NV=1, BS=2
    testbase.assert_equal(entry.attributes & 0x3, 0x3)
    return

def test_dmpstore_wrapper_tests_command_includes_name_and_guid() -> None:
    """
        Name and guid operands are appended to the command line.
    """
    agent = _FakeUefiAgent()
    expected_cmd = "dmpstore MyVar -guid 8BE4DF61-93CA-11D2-AA0D-00E098032B8C"
    agent.set_response(command=expected_cmd, status=0, stdout="", stderr="")

    shell_dmpstore(
        agent=agent,
        name="MyVar",
        guid="8BE4DF61-93CA-11D2-AA0D-00E098032B8C",
    )

    testbase.assert_equal(agent.calls[0]["command"], expected_cmd)
    return

def test_dmpstore_wrapper_tests_non_zero_raises() -> None:
    """
        Non-zero exit raises :class:`UefiShellError`.
    """
    agent = _FakeUefiAgent()
    agent.set_response(command="dmpstore", status=3, stdout="s", stderr="e")

    try:
        shell_dmpstore(agent=agent)
        raise AssertionError("expected UefiShellError")
    except UefiShellError as uerr:
        testbase.assert_equal(uerr.status, 3)
    return


# ---------------------------------------------------------------------------
# shell_reset
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# ResetWrapperTests
# ----------------------------------------------------------------------

"""
    Tests for :func:`shell_reset`.
"""

def test_warm_mode_issues_dash_w() -> None:
    """
        ``mode='warm'`` produces ``reset -w``.
    """
    agent = _FakeUefiAgent()
    agent.set_response(command="reset -w", status=0, stdout="", stderr="")

    shell_reset(agent=agent, mode=RESET_MODE_WARM)

    testbase.assert_equal(agent.calls[0]["command"], "reset -w")
    return

def test_cold_mode_issues_dash_c() -> None:
    """
        ``mode='cold'`` produces ``reset -c``.
    """
    agent = _FakeUefiAgent()
    agent.set_response(command="reset -c", status=0, stdout="", stderr="")

    shell_reset(agent=agent, mode=RESET_MODE_COLD)

    testbase.assert_equal(agent.calls[0]["command"], "reset -c")
    return

def test_shutdown_mode_issues_dash_s() -> None:
    """
        ``mode='shutdown'`` produces ``reset -s``.
    """
    agent = _FakeUefiAgent()
    agent.set_response(command="reset -s", status=0, stdout="", stderr="")

    shell_reset(agent=agent, mode=RESET_MODE_SHUTDOWN)

    testbase.assert_equal(agent.calls[0]["command"], "reset -s")
    return

def test_invalid_mode_raises() -> None:
    """
        Unknown modes raise :class:`ValueError`.
    """
    agent = _FakeUefiAgent()

    try:
        shell_reset(agent=agent, mode="sideways")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
    return

def test_swallow_connection_error() -> None:
    """
        A :class:`ConnectionError` from ``run_cmd`` must be swallowed.
    """
    agent = _FakeUefiAgent()

    def _raise() -> None:
        raise ConnectionError("transport gone")

    agent.set_side_effect(command="reset -w", effect=_raise)

    # Must NOT raise.
    shell_reset(agent=agent, mode=RESET_MODE_WARM)
    return

def test_non_zero_status_raises() -> None:
    """
        A non-zero exit that actually returns (i.e. the command came
        back before the box went down) raises :class:`UefiShellError`.
    """
    agent = _FakeUefiAgent()
    agent.set_response(command="reset -w", status=4, stdout="s", stderr="e")

    try:
        shell_reset(agent=agent, mode=RESET_MODE_WARM)
        raise AssertionError("expected UefiShellError")
    except UefiShellError as uerr:
        testbase.assert_equal(uerr.status, 4)
    return


# ---------------------------------------------------------------------------
# shell_dh
# ---------------------------------------------------------------------------

_DH_FIXTURE = (
    " 25: VariableArch Variable MonotonicCounter" + os.linesep
    + " 2A: SimpleFileSystem DiskIo BlockIo" + os.linesep
)


# ----------------------------------------------------------------------
# DhWrapperTests
# ----------------------------------------------------------------------

"""
    Tests for :func:`shell_dh`.
"""

def test_dh_wrapper_tests_happy_path() -> None:
    """
        Canned ``dh`` stdout must resolve to two handle entries.
    """
    agent = _FakeUefiAgent()
    agent.set_response(command="dh", status=0, stdout=_DH_FIXTURE, stderr="")

    result = shell_dh(agent=agent)

    testbase.assert_true(isinstance(result, DhResult))
    testbase.assert_equal(len(result.entries), 2)
    testbase.assert_equal(result.entries[0].handle_id, 0x25)
    testbase.assert_true("VariableArch" in result.entries[0].protocols)

    testbase.assert_equal(result.entries[1].handle_id, 0x2A)
    testbase.assert_true("SimpleFileSystem" in result.entries[1].protocols)
    return

def test_dh_wrapper_tests_non_zero_raises() -> None:
    """
        Non-zero exit raises :class:`UefiShellError`.
    """
    agent = _FakeUefiAgent()
    agent.set_response(command="dh", status=11, stdout="s", stderr="e")

    try:
        shell_dh(agent=agent)
        raise AssertionError("expected UefiShellError")
    except UefiShellError as uerr:
        testbase.assert_equal(uerr.status, 11)
    return


# ---------------------------------------------------------------------------
# shell_memmap
# ---------------------------------------------------------------------------

_MEMMAP_FIXTURE = (
    "Available  0000000000100000-0000000000FFFFFF 0000000000000F00 0000000000000000" + os.linesep
    + "Reserved   0000000001000000-00000000010FFFFF 0000000000000100 0000000000000001" + os.linesep
)


# ----------------------------------------------------------------------
# MemmapWrapperTests
# ----------------------------------------------------------------------

"""
    Tests for :func:`shell_memmap`.
"""

def test_memmap_wrapper_tests_happy_path() -> None:
    """
        Canned ``memmap`` stdout must resolve to two entries.
    """
    agent = _FakeUefiAgent()
    agent.set_response(command="memmap", status=0, stdout=_MEMMAP_FIXTURE, stderr="")

    result = shell_memmap(agent=agent)

    testbase.assert_true(isinstance(result, MemmapResult))
    testbase.assert_equal(len(result.entries), 2)
    testbase.assert_equal(result.entries[0].memory_type, "Available")
    testbase.assert_equal(result.entries[0].start, 0x100000)
    testbase.assert_equal(result.entries[0].end, 0xFFFFFF)
    testbase.assert_equal(result.entries[0].pages, 0xF00)

    testbase.assert_equal(result.entries[1].memory_type, "Reserved")
    testbase.assert_equal(result.entries[1].attributes, 0x1)
    return

def test_memmap_wrapper_tests_non_zero_raises() -> None:
    """
        Non-zero exit raises :class:`UefiShellError`.
    """
    agent = _FakeUefiAgent()
    agent.set_response(command="memmap", status=8, stdout="s", stderr="e")

    try:
        shell_memmap(agent=agent)
        raise AssertionError("expected UefiShellError")
    except UefiShellError as uerr:
        testbase.assert_equal(uerr.status, 8)
    return


# ---------------------------------------------------------------------------
# shell_smbiosview
# ---------------------------------------------------------------------------

_SMBIOS_FIXTURE = (
    "Type=1, Handle=0x0001, Length=27" + os.linesep
    + "  Manufacturer: ACME Corp" + os.linesep
    + "  Product Name: SuperServer" + os.linesep
    + "Type=2, Handle=0x0002, Length=15" + os.linesep
    + "  Manufacturer: ACME Corp" + os.linesep
)


# ----------------------------------------------------------------------
# SmbiosviewWrapperTests
# ----------------------------------------------------------------------

"""
    Tests for :func:`shell_smbiosview`.
"""

def test_smbiosview_wrapper_tests_happy_path() -> None:
    """
        Canned ``smbiosview`` stdout must resolve to two records.
    """
    agent = _FakeUefiAgent()
    agent.set_response(command="smbiosview", status=0,
                       stdout=_SMBIOS_FIXTURE, stderr="")

    result = shell_smbiosview(agent=agent)

    testbase.assert_true(isinstance(result, SmbiosviewResult))
    testbase.assert_equal(len(result.structures), 2)

    first = result.structures[0]
    testbase.assert_equal(first["type"], 1)
    testbase.assert_equal(first["handle"], 0x0001)
    testbase.assert_equal(first["length"], 27)
    testbase.assert_equal(first["fields"]["Manufacturer"], "ACME Corp")
    testbase.assert_equal(first["fields"]["Product Name"], "SuperServer")
    return

def test_smbiosview_wrapper_tests_non_zero_raises() -> None:
    """
        Non-zero exit raises :class:`UefiShellError`.
    """
    agent = _FakeUefiAgent()
    agent.set_response(command="smbiosview", status=13, stdout="s", stderr="e")

    try:
        shell_smbiosview(agent=agent)
        raise AssertionError("expected UefiShellError")
    except UefiShellError as uerr:
        testbase.assert_equal(uerr.status, 13)
    return


# ---------------------------------------------------------------------------
# shell_drivers
# ---------------------------------------------------------------------------

_DRIVERS_FIXTURE = (
    " 1F  0000000A  D  -  -   1   1  PciBus Driver                  PciBusDxe" + os.linesep
    + " 2A  00000001  D  ?  -   2   3  USB Bus Driver                 UsbBusDxe" + os.linesep
)


# ----------------------------------------------------------------------
# DriversWrapperTests
# ----------------------------------------------------------------------

"""
    Tests for :func:`shell_drivers`.
"""

def test_drivers_wrapper_tests_happy_path() -> None:
    """
        Canned ``drivers`` stdout must resolve to two entries.
    """
    agent = _FakeUefiAgent()
    agent.set_response(command="drivers", status=0,
                       stdout=_DRIVERS_FIXTURE, stderr="")

    result = shell_drivers(agent=agent)

    testbase.assert_true(isinstance(result, DriversResult))
    testbase.assert_equal(len(result.entries), 2)
    testbase.assert_equal(result.entries[0].handle_id, 0x1F)
    testbase.assert_equal(result.entries[0].version, "0000000A")
    testbase.assert_equal(result.entries[0].name, "PciBus Driver")

    testbase.assert_equal(result.entries[1].handle_id, 0x2A)
    testbase.assert_equal(result.entries[1].name, "USB Bus Driver")
    return

def test_drivers_wrapper_tests_non_zero_raises() -> None:
    """
        Non-zero exit raises :class:`UefiShellError`.
    """
    agent = _FakeUefiAgent()
    agent.set_response(command="drivers", status=2, stdout="s", stderr="e")

    try:
        shell_drivers(agent=agent)
        raise AssertionError("expected UefiShellError")
    except UefiShellError as uerr:
        testbase.assert_equal(uerr.status, 2)
    return


# ---------------------------------------------------------------------------
# shell_devtree
# ---------------------------------------------------------------------------

_DEVTREE_FIXTURE = (
    "Ctrl[1F] PciRoot(0x0)" + os.linesep
    + "  Ctrl[2A] Pci(0x1F,0x2)" + os.linesep
    + "    Ctrl[3B] Sata(0x0,0x0,0x0)" + os.linesep
)


# ----------------------------------------------------------------------
# DevtreeWrapperTests
# ----------------------------------------------------------------------

"""
    Tests for :func:`shell_devtree`.
"""

def test_devtree_wrapper_tests_happy_path() -> None:
    """
        Canned ``devtree`` stdout must resolve to three nodes with
        increasing depth.
    """
    agent = _FakeUefiAgent()
    agent.set_response(command="devtree", status=0,
                       stdout=_DEVTREE_FIXTURE, stderr="")

    result = shell_devtree(agent=agent)

    testbase.assert_true(isinstance(result, DevtreeResult))
    testbase.assert_equal(len(result.nodes), 3)
    testbase.assert_equal(result.nodes[0].depth, 0)
    testbase.assert_equal(result.nodes[0].handle_id, 0x1F)
    testbase.assert_true("PciRoot" in result.nodes[0].name)

    testbase.assert_equal(result.nodes[1].depth, 1)
    testbase.assert_equal(result.nodes[2].depth, 2)
    return

def test_devtree_wrapper_tests_non_zero_raises() -> None:
    """
        Non-zero exit raises :class:`UefiShellError`.
    """
    agent = _FakeUefiAgent()
    agent.set_response(command="devtree", status=6, stdout="s", stderr="e")

    try:
        shell_devtree(agent=agent)
        raise AssertionError("expected UefiShellError")
    except UefiShellError as uerr:
        testbase.assert_equal(uerr.status, 6)
    return

def test_devtree_wrapper_tests_unparsed_lines() -> None:
    """
        Garbage lines are captured as unparsed.
    """
    garbage = "this is not a devtree line" + os.linesep + _DEVTREE_FIXTURE
    agent = _FakeUefiAgent()
    agent.set_response(command="devtree", status=0, stdout=garbage, stderr="")

    result = shell_devtree(agent=agent)

    testbase.assert_true("this is not a devtree line" in result.unparsed_lines)
    return


# ---------------------------------------------------------------------------
# Aspects passthrough sanity across a sample of wrappers
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------------
# AspectsPassthroughTests
# ----------------------------------------------------------------------

"""
    Tests that every wrapper forwards aspects unchanged to the agent.
"""

def test_bcfg_passes_aspects() -> None:
    """
        ``shell_bcfg_boot_dump`` forwards the aspects argument.
    """
    custom = UefiCmdAspects()
    agent = _FakeUefiAgent()
    agent.set_response(command="bcfg boot dump", status=0,
                       stdout=_BCFG_FIXTURE, stderr="")

    shell_bcfg_boot_dump(agent=agent, aspects=custom)

    testbase.assert_true(agent.calls[0]["aspects"] is custom)
    return

def test_dmpstore_passes_aspects() -> None:
    """
        ``shell_dmpstore`` forwards the aspects argument.
    """
    custom = UefiCmdAspects()
    agent = _FakeUefiAgent()
    agent.set_response(command="dmpstore", status=0,
                       stdout=_DMPSTORE_FIXTURE, stderr="")

    shell_dmpstore(agent=agent, aspects=custom)

    testbase.assert_true(agent.calls[0]["aspects"] is custom)
    return

def test_reset_passes_aspects() -> None:
    """
        ``shell_reset`` forwards the aspects argument.
    """
    custom = UefiCmdAspects()
    agent = _FakeUefiAgent()
    agent.set_response(command="reset -w", status=0, stdout="", stderr="")

    shell_reset(agent=agent, mode=RESET_MODE_WARM, aspects=custom)

    testbase.assert_true(agent.calls[0]["aspects"] is custom)
    return
