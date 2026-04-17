"""
    .. module:: test_uefishell
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Unit tests for the :mod:`automojo.protocols.uefi.uefishell`
               module.  Tests use a hand-rolled :class:`_FakeUefiAgent`
               whose :meth:`run_cmd` returns canned ``(status, stdout,
               stderr)`` tuples looked up by the exact command text so no
               real transports, ports, or subprocesses are touched.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Automation Mojo LLC"
__credits__ = []


import os
import unittest

from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from automojo.protocols.uefi.uefiaspects import (
    DEFAULT_CMD_ASPECTS,
    UefiCmdAspects,
)
from automojo.protocols.uefi.uefiexceptions import UefiShellError

from automojo.protocols.uefi.uefishell import (
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


class MapWrapperTests(unittest.TestCase):
    """
        Tests for :func:`shell_map`.
    """

    def test_happy_path_parses_entries(self) -> None:
        """
            Canned ``map`` stdout must resolve into two entries with
            device paths and aliases populated.
        """
        agent = _FakeUefiAgent()
        agent.set_response(command="map", status=0, stdout=_MAP_FIXTURE, stderr="")

        result = shell_map(agent=agent)

        self.assertIsInstance(result, MapResult)
        self.assertEqual(len(result.entries), 2)

        fs0 = result.entries[0]
        self.assertEqual(fs0.alias, "FS0")
        self.assertIn("PciRoot", fs0.device_path)
        self.assertIn("HD1b", fs0.aliases[0])

        blk0 = result.entries[1]
        self.assertEqual(blk0.alias, "BLK0")
        self.assertIn("PciRoot", blk0.device_path)
        return

    def test_non_zero_status_raises_shell_error(self) -> None:
        """
            Non-zero exit status must raise :class:`UefiShellError` with
            command/status/stdout/stderr carried on the exception.
        """
        agent = _FakeUefiAgent()
        agent.set_response(command="map", status=7, stdout="boom\n", stderr="fatal\n")

        try:
            shell_map(agent=agent)
            self.fail("expected UefiShellError")
        except UefiShellError as uerr:
            self.assertEqual(uerr.command, "map")
            self.assertEqual(uerr.status, 7)
            self.assertEqual(uerr.stdout, "boom\n")
            self.assertEqual(uerr.stderr, "fatal\n")
        return

    def test_unparsed_lines_collected(self) -> None:
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

        self.assertIn("unrecognized firmware banner", result.unparsed_lines)
        self.assertTrue(
            any("garbage trailer" in line for line in result.unparsed_lines)
        )
        return

    def test_aspects_passthrough(self) -> None:
        """
            The caller-supplied aspects must reach the agent verbatim.
        """
        custom = UefiCmdAspects()
        agent = _FakeUefiAgent()
        agent.set_response(command="map", status=0, stdout=_MAP_FIXTURE, stderr="")

        shell_map(agent=agent, aspects=custom)

        self.assertEqual(len(agent.calls), 1)
        self.assertIs(agent.calls[0]["aspects"], custom)
        return


# ---------------------------------------------------------------------------
# shell_ls
# ---------------------------------------------------------------------------

_LS_FIXTURE = (
    "<DIR>     05/11/2024  14:32                   EFI" + os.linesep
    + "A         05/11/2024  14:32         1,234,567 bootx64.efi" + os.linesep
)


class LsWrapperTests(unittest.TestCase):
    """
        Tests for :func:`shell_ls`.
    """

    def test_happy_path(self) -> None:
        """
            Canned ``ls`` stdout must resolve into two entries.
        """
        agent = _FakeUefiAgent()
        agent.set_response(command="ls", status=0, stdout=_LS_FIXTURE, stderr="")

        result = shell_ls(agent=agent)

        self.assertIsInstance(result, LsResult)
        self.assertEqual(len(result.entries), 2)

        dir_entry = result.entries[0]
        self.assertEqual(dir_entry.size, 0)
        self.assertEqual(dir_entry.name, "EFI")

        file_entry = result.entries[1]
        self.assertEqual(file_entry.size, 1234567)
        self.assertEqual(file_entry.name, "bootx64.efi")
        return

    def test_path_is_quoted(self) -> None:
        """
            When a path is supplied it must be quoted in the command.
        """
        agent = _FakeUefiAgent()
        quoted_cmd = 'ls "FS0:\\EFI\\Boot"'
        agent.set_response(command=quoted_cmd, status=0, stdout="", stderr="")

        shell_ls(agent=agent, path="FS0:\\EFI\\Boot")

        self.assertEqual(agent.calls[0]["command"], quoted_cmd)
        return

    def test_non_zero_raises(self) -> None:
        """
            Non-zero exit raises :class:`UefiShellError`.
        """
        agent = _FakeUefiAgent()
        agent.set_response(command="ls", status=1, stdout="x", stderr="y")

        try:
            shell_ls(agent=agent)
            self.fail("expected UefiShellError")
        except UefiShellError as uerr:
            self.assertEqual(uerr.status, 1)
        return

    def test_unparsed_lines(self) -> None:
        """
            Garbage lines must appear in ``unparsed_lines``.
        """
        garbage = "this does not match the ls format" + os.linesep
        agent = _FakeUefiAgent()
        agent.set_response(command="ls", status=0, stdout=garbage, stderr="")

        result = shell_ls(agent=agent)

        self.assertIn("this does not match the ls format", result.unparsed_lines)
        return


# ---------------------------------------------------------------------------
# shell_cp
# ---------------------------------------------------------------------------

class CpWrapperTests(unittest.TestCase):
    """
        Tests for :func:`shell_cp`.
    """

    def test_command_string_is_quoted(self) -> None:
        """
            The ``cp`` wrapper must quote both operands.
        """
        agent = _FakeUefiAgent()
        expected_cmd = 'cp "FS0:\\a.efi" "FS0:\\b.efi"'
        agent.set_response(command=expected_cmd, status=0, stdout="", stderr="")

        shell_cp(agent=agent, src="FS0:\\a.efi", dst="FS0:\\b.efi")

        self.assertEqual(agent.calls[0]["command"], expected_cmd)
        return

    def test_failure_raises_shell_error(self) -> None:
        """
            Non-zero exit raises :class:`UefiShellError` with context.
        """
        agent = _FakeUefiAgent()
        expected_cmd = 'cp "src" "dst"'
        agent.set_response(command=expected_cmd, status=5, stdout="nope", stderr="err")

        try:
            shell_cp(agent=agent, src="src", dst="dst")
            self.fail("expected UefiShellError")
        except UefiShellError as uerr:
            self.assertEqual(uerr.command, expected_cmd)
            self.assertEqual(uerr.status, 5)
            self.assertEqual(uerr.stderr, "err")
        return

    def test_aspects_passthrough(self) -> None:
        """
            Aspects supplied by the caller reach the agent.
        """
        custom = UefiCmdAspects()
        agent = _FakeUefiAgent()
        expected_cmd = 'cp "s" "d"'
        agent.set_response(command=expected_cmd, status=0, stdout="", stderr="")

        shell_cp(agent=agent, src="s", dst="d", aspects=custom)

        self.assertIs(agent.calls[0]["aspects"], custom)
        return


# ---------------------------------------------------------------------------
# shell_cd
# ---------------------------------------------------------------------------

class CdWrapperTests(unittest.TestCase):
    """
        Tests for :func:`shell_cd`.
    """

    def test_command_string(self) -> None:
        """
            The ``cd`` wrapper must quote its path operand.
        """
        agent = _FakeUefiAgent()
        expected_cmd = 'cd "FS0:\\"'
        agent.set_response(command=expected_cmd, status=0, stdout="", stderr="")

        shell_cd(agent=agent, path="FS0:\\")

        self.assertEqual(agent.calls[0]["command"], expected_cmd)
        return

    def test_failure_raises(self) -> None:
        """
            Non-zero exit raises :class:`UefiShellError`.
        """
        agent = _FakeUefiAgent()
        expected_cmd = 'cd "FS0:\\missing"'
        agent.set_response(command=expected_cmd, status=2, stdout="no", stderr="missing")

        try:
            shell_cd(agent=agent, path="FS0:\\missing")
            self.fail("expected UefiShellError")
        except UefiShellError as uerr:
            self.assertEqual(uerr.status, 2)
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


class BcfgWrapperTests(unittest.TestCase):
    """
        Tests for :func:`shell_bcfg_boot_dump`.
    """

    def test_happy_path(self) -> None:
        """
            Canned ``bcfg boot dump`` stdout must resolve to two entries.
        """
        agent = _FakeUefiAgent()
        agent.set_response(command="bcfg boot dump", status=0,
                           stdout=_BCFG_FIXTURE, stderr="")

        result = shell_bcfg_boot_dump(agent=agent)

        self.assertIsInstance(result, BcfgResult)
        self.assertEqual(len(result.entries), 2)

        self.assertEqual(result.entries[0].index, 0)
        self.assertTrue(result.entries[0].active)
        self.assertEqual(result.entries[0].description, "UEFI OS")
        self.assertIsNotNone(result.entries[0].file_path)

        self.assertEqual(result.entries[1].index, 1)
        self.assertFalse(result.entries[1].active)
        self.assertEqual(result.entries[1].description, "Network")
        return

    def test_non_zero_raises(self) -> None:
        """
            Non-zero exit raises :class:`UefiShellError`.
        """
        agent = _FakeUefiAgent()
        agent.set_response(command="bcfg boot dump", status=1,
                           stdout="s", stderr="e")

        try:
            shell_bcfg_boot_dump(agent=agent)
            self.fail("expected UefiShellError")
        except UefiShellError as uerr:
            self.assertEqual(uerr.status, 1)
        return

    def test_unparsed_lines(self) -> None:
        """
            Unrecognized banner lines must appear in ``unparsed_lines``.
        """
        garbage = "strange firmware banner" + os.linesep + _BCFG_FIXTURE
        agent = _FakeUefiAgent()
        agent.set_response(command="bcfg boot dump", status=0,
                           stdout=garbage, stderr="")

        result = shell_bcfg_boot_dump(agent=agent)

        self.assertIn("strange firmware banner", result.unparsed_lines)
        return


# ---------------------------------------------------------------------------
# shell_setvar
# ---------------------------------------------------------------------------

class SetvarWrapperTests(unittest.TestCase):
    """
        Tests for :func:`shell_setvar`.
    """

    def test_command_assembly(self) -> None:
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

        self.assertEqual(agent.calls[0]["command"], expected_cmd)
        return

    def test_failure_raises(self) -> None:
        """
            Non-zero exit raises :class:`UefiShellError`.
        """
        agent = _FakeUefiAgent()
        expected_cmd = "setvar MyVar =01"
        agent.set_response(command=expected_cmd, status=9, stdout="o", stderr="e")

        try:
            shell_setvar(agent=agent, name="MyVar", value=b"\x01")
            self.fail("expected UefiShellError")
        except UefiShellError as uerr:
            self.assertEqual(uerr.status, 9)
        return

    def test_non_bytes_rejected(self) -> None:
        """
            ``value`` must be ``bytes``; anything else raises.
        """
        agent = _FakeUefiAgent()

        try:
            shell_setvar(agent=agent, name="MyVar", value="not bytes")  # type: ignore[arg-type]
            self.fail("expected TypeError")
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


class DmpstoreWrapperTests(unittest.TestCase):
    """
        Tests for :func:`shell_dmpstore`.
    """

    def test_happy_path(self) -> None:
        """
            Canned ``dmpstore`` stdout must resolve to one entry with
            decoded data.
        """
        agent = _FakeUefiAgent()
        agent.set_response(command="dmpstore", status=0,
                           stdout=_DMPSTORE_FIXTURE, stderr="")

        result = shell_dmpstore(agent=agent)

        self.assertIsInstance(result, DmpstoreResult)
        self.assertEqual(len(result.entries), 1)

        entry = result.entries[0]
        self.assertEqual(entry.name, "GlobalDontKnow")
        self.assertEqual(entry.guid, "8BE4DF61-93CA-11D2-AA0D-00E098032B8C")
        self.assertEqual(entry.data, b"\x01\x02\x03\x04\x05\x06\x07\x08")
        # NV=1, BS=2
        self.assertEqual(entry.attributes & 0x3, 0x3)
        return

    def test_command_includes_name_and_guid(self) -> None:
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

        self.assertEqual(agent.calls[0]["command"], expected_cmd)
        return

    def test_non_zero_raises(self) -> None:
        """
            Non-zero exit raises :class:`UefiShellError`.
        """
        agent = _FakeUefiAgent()
        agent.set_response(command="dmpstore", status=3, stdout="s", stderr="e")

        try:
            shell_dmpstore(agent=agent)
            self.fail("expected UefiShellError")
        except UefiShellError as uerr:
            self.assertEqual(uerr.status, 3)
        return


# ---------------------------------------------------------------------------
# shell_reset
# ---------------------------------------------------------------------------

class ResetWrapperTests(unittest.TestCase):
    """
        Tests for :func:`shell_reset`.
    """

    def test_warm_mode_issues_dash_w(self) -> None:
        """
            ``mode='warm'`` produces ``reset -w``.
        """
        agent = _FakeUefiAgent()
        agent.set_response(command="reset -w", status=0, stdout="", stderr="")

        shell_reset(agent=agent, mode=RESET_MODE_WARM)

        self.assertEqual(agent.calls[0]["command"], "reset -w")
        return

    def test_cold_mode_issues_dash_c(self) -> None:
        """
            ``mode='cold'`` produces ``reset -c``.
        """
        agent = _FakeUefiAgent()
        agent.set_response(command="reset -c", status=0, stdout="", stderr="")

        shell_reset(agent=agent, mode=RESET_MODE_COLD)

        self.assertEqual(agent.calls[0]["command"], "reset -c")
        return

    def test_shutdown_mode_issues_dash_s(self) -> None:
        """
            ``mode='shutdown'`` produces ``reset -s``.
        """
        agent = _FakeUefiAgent()
        agent.set_response(command="reset -s", status=0, stdout="", stderr="")

        shell_reset(agent=agent, mode=RESET_MODE_SHUTDOWN)

        self.assertEqual(agent.calls[0]["command"], "reset -s")
        return

    def test_invalid_mode_raises(self) -> None:
        """
            Unknown modes raise :class:`ValueError`.
        """
        agent = _FakeUefiAgent()

        try:
            shell_reset(agent=agent, mode="sideways")
            self.fail("expected ValueError")
        except ValueError:
            pass
        return

    def test_swallow_connection_error(self) -> None:
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

    def test_non_zero_status_raises(self) -> None:
        """
            A non-zero exit that actually returns (i.e. the command came
            back before the box went down) raises :class:`UefiShellError`.
        """
        agent = _FakeUefiAgent()
        agent.set_response(command="reset -w", status=4, stdout="s", stderr="e")

        try:
            shell_reset(agent=agent, mode=RESET_MODE_WARM)
            self.fail("expected UefiShellError")
        except UefiShellError as uerr:
            self.assertEqual(uerr.status, 4)
        return


# ---------------------------------------------------------------------------
# shell_dh
# ---------------------------------------------------------------------------

_DH_FIXTURE = (
    " 25: VariableArch Variable MonotonicCounter" + os.linesep
    + " 2A: SimpleFileSystem DiskIo BlockIo" + os.linesep
)


class DhWrapperTests(unittest.TestCase):
    """
        Tests for :func:`shell_dh`.
    """

    def test_happy_path(self) -> None:
        """
            Canned ``dh`` stdout must resolve to two handle entries.
        """
        agent = _FakeUefiAgent()
        agent.set_response(command="dh", status=0, stdout=_DH_FIXTURE, stderr="")

        result = shell_dh(agent=agent)

        self.assertIsInstance(result, DhResult)
        self.assertEqual(len(result.entries), 2)
        self.assertEqual(result.entries[0].handle_id, 0x25)
        self.assertIn("VariableArch", result.entries[0].protocols)

        self.assertEqual(result.entries[1].handle_id, 0x2A)
        self.assertIn("SimpleFileSystem", result.entries[1].protocols)
        return

    def test_non_zero_raises(self) -> None:
        """
            Non-zero exit raises :class:`UefiShellError`.
        """
        agent = _FakeUefiAgent()
        agent.set_response(command="dh", status=11, stdout="s", stderr="e")

        try:
            shell_dh(agent=agent)
            self.fail("expected UefiShellError")
        except UefiShellError as uerr:
            self.assertEqual(uerr.status, 11)
        return


# ---------------------------------------------------------------------------
# shell_memmap
# ---------------------------------------------------------------------------

_MEMMAP_FIXTURE = (
    "Available  0000000000100000-0000000000FFFFFF 0000000000000F00 0000000000000000" + os.linesep
    + "Reserved   0000000001000000-00000000010FFFFF 0000000000000100 0000000000000001" + os.linesep
)


class MemmapWrapperTests(unittest.TestCase):
    """
        Tests for :func:`shell_memmap`.
    """

    def test_happy_path(self) -> None:
        """
            Canned ``memmap`` stdout must resolve to two entries.
        """
        agent = _FakeUefiAgent()
        agent.set_response(command="memmap", status=0, stdout=_MEMMAP_FIXTURE, stderr="")

        result = shell_memmap(agent=agent)

        self.assertIsInstance(result, MemmapResult)
        self.assertEqual(len(result.entries), 2)
        self.assertEqual(result.entries[0].memory_type, "Available")
        self.assertEqual(result.entries[0].start, 0x100000)
        self.assertEqual(result.entries[0].end, 0xFFFFFF)
        self.assertEqual(result.entries[0].pages, 0xF00)

        self.assertEqual(result.entries[1].memory_type, "Reserved")
        self.assertEqual(result.entries[1].attributes, 0x1)
        return

    def test_non_zero_raises(self) -> None:
        """
            Non-zero exit raises :class:`UefiShellError`.
        """
        agent = _FakeUefiAgent()
        agent.set_response(command="memmap", status=8, stdout="s", stderr="e")

        try:
            shell_memmap(agent=agent)
            self.fail("expected UefiShellError")
        except UefiShellError as uerr:
            self.assertEqual(uerr.status, 8)
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


class SmbiosviewWrapperTests(unittest.TestCase):
    """
        Tests for :func:`shell_smbiosview`.
    """

    def test_happy_path(self) -> None:
        """
            Canned ``smbiosview`` stdout must resolve to two records.
        """
        agent = _FakeUefiAgent()
        agent.set_response(command="smbiosview", status=0,
                           stdout=_SMBIOS_FIXTURE, stderr="")

        result = shell_smbiosview(agent=agent)

        self.assertIsInstance(result, SmbiosviewResult)
        self.assertEqual(len(result.structures), 2)

        first = result.structures[0]
        self.assertEqual(first["type"], 1)
        self.assertEqual(first["handle"], 0x0001)
        self.assertEqual(first["length"], 27)
        self.assertEqual(first["fields"]["Manufacturer"], "ACME Corp")
        self.assertEqual(first["fields"]["Product Name"], "SuperServer")
        return

    def test_non_zero_raises(self) -> None:
        """
            Non-zero exit raises :class:`UefiShellError`.
        """
        agent = _FakeUefiAgent()
        agent.set_response(command="smbiosview", status=13, stdout="s", stderr="e")

        try:
            shell_smbiosview(agent=agent)
            self.fail("expected UefiShellError")
        except UefiShellError as uerr:
            self.assertEqual(uerr.status, 13)
        return


# ---------------------------------------------------------------------------
# shell_drivers
# ---------------------------------------------------------------------------

_DRIVERS_FIXTURE = (
    " 1F  0000000A  D  -  -   1   1  PciBus Driver                  PciBusDxe" + os.linesep
    + " 2A  00000001  D  ?  -   2   3  USB Bus Driver                 UsbBusDxe" + os.linesep
)


class DriversWrapperTests(unittest.TestCase):
    """
        Tests for :func:`shell_drivers`.
    """

    def test_happy_path(self) -> None:
        """
            Canned ``drivers`` stdout must resolve to two entries.
        """
        agent = _FakeUefiAgent()
        agent.set_response(command="drivers", status=0,
                           stdout=_DRIVERS_FIXTURE, stderr="")

        result = shell_drivers(agent=agent)

        self.assertIsInstance(result, DriversResult)
        self.assertEqual(len(result.entries), 2)
        self.assertEqual(result.entries[0].handle_id, 0x1F)
        self.assertEqual(result.entries[0].version, "0000000A")
        self.assertEqual(result.entries[0].name, "PciBus Driver")

        self.assertEqual(result.entries[1].handle_id, 0x2A)
        self.assertEqual(result.entries[1].name, "USB Bus Driver")
        return

    def test_non_zero_raises(self) -> None:
        """
            Non-zero exit raises :class:`UefiShellError`.
        """
        agent = _FakeUefiAgent()
        agent.set_response(command="drivers", status=2, stdout="s", stderr="e")

        try:
            shell_drivers(agent=agent)
            self.fail("expected UefiShellError")
        except UefiShellError as uerr:
            self.assertEqual(uerr.status, 2)
        return


# ---------------------------------------------------------------------------
# shell_devtree
# ---------------------------------------------------------------------------

_DEVTREE_FIXTURE = (
    "Ctrl[1F] PciRoot(0x0)" + os.linesep
    + "  Ctrl[2A] Pci(0x1F,0x2)" + os.linesep
    + "    Ctrl[3B] Sata(0x0,0x0,0x0)" + os.linesep
)


class DevtreeWrapperTests(unittest.TestCase):
    """
        Tests for :func:`shell_devtree`.
    """

    def test_happy_path(self) -> None:
        """
            Canned ``devtree`` stdout must resolve to three nodes with
            increasing depth.
        """
        agent = _FakeUefiAgent()
        agent.set_response(command="devtree", status=0,
                           stdout=_DEVTREE_FIXTURE, stderr="")

        result = shell_devtree(agent=agent)

        self.assertIsInstance(result, DevtreeResult)
        self.assertEqual(len(result.nodes), 3)
        self.assertEqual(result.nodes[0].depth, 0)
        self.assertEqual(result.nodes[0].handle_id, 0x1F)
        self.assertIn("PciRoot", result.nodes[0].name)

        self.assertEqual(result.nodes[1].depth, 1)
        self.assertEqual(result.nodes[2].depth, 2)
        return

    def test_non_zero_raises(self) -> None:
        """
            Non-zero exit raises :class:`UefiShellError`.
        """
        agent = _FakeUefiAgent()
        agent.set_response(command="devtree", status=6, stdout="s", stderr="e")

        try:
            shell_devtree(agent=agent)
            self.fail("expected UefiShellError")
        except UefiShellError as uerr:
            self.assertEqual(uerr.status, 6)
        return

    def test_unparsed_lines(self) -> None:
        """
            Garbage lines are captured as unparsed.
        """
        garbage = "this is not a devtree line" + os.linesep + _DEVTREE_FIXTURE
        agent = _FakeUefiAgent()
        agent.set_response(command="devtree", status=0, stdout=garbage, stderr="")

        result = shell_devtree(agent=agent)

        self.assertIn("this is not a devtree line", result.unparsed_lines)
        return


# ---------------------------------------------------------------------------
# Aspects passthrough sanity across a sample of wrappers
# ---------------------------------------------------------------------------

class AspectsPassthroughTests(unittest.TestCase):
    """
        Tests that every wrapper forwards aspects unchanged to the agent.
    """

    def test_bcfg_passes_aspects(self) -> None:
        """
            ``shell_bcfg_boot_dump`` forwards the aspects argument.
        """
        custom = UefiCmdAspects()
        agent = _FakeUefiAgent()
        agent.set_response(command="bcfg boot dump", status=0,
                           stdout=_BCFG_FIXTURE, stderr="")

        shell_bcfg_boot_dump(agent=agent, aspects=custom)

        self.assertIs(agent.calls[0]["aspects"], custom)
        return

    def test_dmpstore_passes_aspects(self) -> None:
        """
            ``shell_dmpstore`` forwards the aspects argument.
        """
        custom = UefiCmdAspects()
        agent = _FakeUefiAgent()
        agent.set_response(command="dmpstore", status=0,
                           stdout=_DMPSTORE_FIXTURE, stderr="")

        shell_dmpstore(agent=agent, aspects=custom)

        self.assertIs(agent.calls[0]["aspects"], custom)
        return

    def test_reset_passes_aspects(self) -> None:
        """
            ``shell_reset`` forwards the aspects argument.
        """
        custom = UefiCmdAspects()
        agent = _FakeUefiAgent()
        agent.set_response(command="reset -w", status=0, stdout="", stderr="")

        shell_reset(agent=agent, mode=RESET_MODE_WARM, aspects=custom)

        self.assertIs(agent.calls[0]["aspects"], custom)
        return


if __name__ == "__main__":
    unittest.main()
