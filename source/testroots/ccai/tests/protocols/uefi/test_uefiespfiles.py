"""
    .. module:: test_uefiespfiles
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Unit tests for :class:`EspFileProvider` and its private
               transport-specific backends.  Uses an in-process ``_FakeAgent``
               that records ``run_cmd`` calls and returns canned
               ``(status, stdout, stderr)`` tuples so the backends never
               touch a real serial port, BMC, or SSH session.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Code Craft AI LLC"
__credits__ = []


import base64
import os
import tempfile
import unittest

from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from ccai.errors.exceptions import ConfigurationError

from ccai.protocols.uefi.uefiaspects import (
    DEFAULT_CMD_ASPECTS,
    UefiCmdAspects,
)
from ccai.protocols.uefi.uefiexceptions import UefiError

from ccai.protocols.uefi.uefiespfiles import (
    DEFAULT_CHUNK_SIZE,
    DEFAULT_ESP_MOUNT,
    DEFAULT_VOLUME_ALIAS,
    EspDirEntry,
    EspFileProvider,
    MAX_CHUNK_SIZE,
    MIN_CHUNK_SIZE,
    _from_shell_path,
    _HostOsEspBackend,
    _RedfishEspBackend,
    _ShellEspBackend,
    _to_shell_path,
)


# ---------------------------------------------------------------------------
# Fake UefiAgent
# ---------------------------------------------------------------------------


class _FakeAgent:
    """
        Minimal stand-in for :class:`UefiAgent`.  Records every ``run_cmd``
        invocation and returns canned ``(status, stdout, stderr)`` tuples
        supplied by the test.
    """

    def __init__(self, transport_kind: str,
                       responder: Optional[Callable[[str], Tuple[int, str, str]]] = None) -> None:
        """
            Capture the configured transport kind and optional responder.

            :param transport_kind: Transport kind selector string.
            :param responder: Callable mapping a command string to a
                              canned ``(status, stdout, stderr)`` tuple.
        """
        self.transport_kind: str = transport_kind
        self._responder: Optional[Callable[[str], Tuple[int, str, str]]] = responder
        self.calls: List[Dict[str, Any]] = []
        self._default_return: Tuple[int, str, str] = (0, "", "")
        return

    def set_default(self, value: Tuple[int, str, str]) -> None:
        """
            Replace the default ``run_cmd`` return tuple used when the
            responder returns ``None`` or is not supplied.

            :param value: The new default ``(status, stdout, stderr)``.
        """
        self._default_return = value
        return

    def run_cmd(self, command: str,
                      exp_status: Union[int, Tuple[int, ...]] = 0,
                      user: Optional[str] = None,
                      aspects: Optional[UefiCmdAspects] = None) -> Tuple[int, str, str]:
        """
            Record the call and return the canned response.

            :param command: The command string.
            :param exp_status: Acceptable status code (accepted for API compat).
            :param user: Optional user role (accepted for API compat).
            :param aspects: Optional aspects (accepted for API compat).

            :returns: A ``(status, stdout, stderr)`` three-tuple.
        """
        record = {
            "command": command,
            "exp_status": exp_status,
            "user": user,
            "aspects": aspects,
        }
        self.calls.append(record)

        if self._responder is not None:
            result = self._responder(command)
            if result is not None:
                rtnval = result
                return rtnval

        rtnval = self._default_return
        return rtnval


# ---------------------------------------------------------------------------
# Path helper tests
# ---------------------------------------------------------------------------


class TestPathHelpers(unittest.TestCase):
    """
        Exercises :func:`_to_shell_path` and :func:`_from_shell_path`.
    """

    def test_to_shell_path_posix_input(self) -> None:
        """POSIX path with no alias gains the default alias and flipped separators."""
        result = _to_shell_path(posix_path="/EFI/BOOT/foo")
        self.assertEqual(result, "FS0:\\EFI\\BOOT\\foo")
        return

    def test_to_shell_path_preserves_existing_alias(self) -> None:
        """FS#: / BLK#: prefixes are preserved verbatim."""
        result = _to_shell_path(posix_path="FS1:/EFI/BOOT")
        self.assertEqual(result, "FS1:\\EFI\\BOOT")

        result2 = _to_shell_path(posix_path="BLK2:\\bin")
        self.assertEqual(result2, "BLK2:\\bin")
        return

    def test_to_shell_path_custom_alias(self) -> None:
        """Custom volume alias is applied when no alias is present in the input."""
        result = _to_shell_path(posix_path="/foo", volume_alias="FS3:")
        self.assertEqual(result, "FS3:\\foo")
        return

    def test_to_shell_path_rejects_parent_traversal(self) -> None:
        """``..`` segments raise :class:`UefiError`."""
        with self.assertRaises(UefiError):
            _to_shell_path(posix_path="/EFI/../secret")
        return

    def test_to_shell_path_rejects_none(self) -> None:
        """``None`` input raises :class:`UefiError`."""
        with self.assertRaises(UefiError):
            _to_shell_path(posix_path=None)  # type: ignore[arg-type]
        return

    def test_from_shell_path_roundtrip(self) -> None:
        """Inverse of _to_shell_path drops the alias and flips separators."""
        result = _from_shell_path(shell_path="FS0:\\EFI\\BOOT\\foo")
        self.assertEqual(result, "/EFI/BOOT/foo")
        return

    def test_from_shell_path_no_alias(self) -> None:
        """A path without an alias still flips separators and gains a leading /."""
        result = _from_shell_path(shell_path="\\EFI\\BOOT")
        self.assertEqual(result, "/EFI/BOOT")
        return

    def test_from_shell_path_blk_alias(self) -> None:
        """BLK#: alias is stripped as well."""
        result = _from_shell_path(shell_path="BLK3:\\bin\\foo")
        self.assertEqual(result, "/bin/foo")
        return


# ---------------------------------------------------------------------------
# EspFileProvider construction / dispatch
# ---------------------------------------------------------------------------


class TestProviderDispatch(unittest.TestCase):
    """
        Assert the facade selects the correct backend per transport kind.
    """

    def test_serial_dispatches_to_shell_backend(self) -> None:
        agent = _FakeAgent(transport_kind="serial")
        provider = EspFileProvider(agent=agent)
        self.assertIsInstance(provider._backend, _ShellEspBackend)
        return

    def test_ipmi_sol_dispatches_to_shell_backend(self) -> None:
        agent = _FakeAgent(transport_kind="ipmi-sol")
        provider = EspFileProvider(agent=agent)
        self.assertIsInstance(provider._backend, _ShellEspBackend)
        return

    def test_ssh_host_dispatches_to_host_os_backend(self) -> None:
        agent = _FakeAgent(transport_kind="ssh-host")
        provider = EspFileProvider(agent=agent)
        self.assertIsInstance(provider._backend, _HostOsEspBackend)
        return

    def test_redfish_dispatches_to_redfish_backend(self) -> None:
        agent = _FakeAgent(transport_kind="redfish")
        provider = EspFileProvider(agent=agent)
        self.assertIsInstance(provider._backend, _RedfishEspBackend)
        return

    def test_unknown_transport_kind_raises(self) -> None:
        agent = _FakeAgent(transport_kind="bogus")
        with self.assertRaises(ConfigurationError):
            EspFileProvider(agent=agent)
        return

    def test_none_agent_raises(self) -> None:
        with self.assertRaises(ConfigurationError):
            EspFileProvider(agent=None)
        return

    def test_missing_transport_kind_raises(self) -> None:
        """An agent without ``transport_kind`` or ``_transport_kind`` is rejected."""
        class _BrokenAgent:
            def run_cmd(self, command: str,
                              aspects: Optional[UefiCmdAspects] = None) -> Tuple[int, str, str]:
                rtnval: Tuple[int, str, str] = (0, "", "")
                return rtnval

        with self.assertRaises(ConfigurationError):
            EspFileProvider(agent=_BrokenAgent())
        return

    def test_invalid_chunk_size_too_small(self) -> None:
        agent = _FakeAgent(transport_kind="serial")
        with self.assertRaises(ConfigurationError):
            EspFileProvider(agent=agent, chunk_size=MIN_CHUNK_SIZE - 1)
        return

    def test_invalid_chunk_size_too_large(self) -> None:
        agent = _FakeAgent(transport_kind="serial")
        with self.assertRaises(ConfigurationError):
            EspFileProvider(agent=agent, chunk_size=MAX_CHUNK_SIZE + 1)
        return

    def test_default_chunk_size_accepted(self) -> None:
        agent = _FakeAgent(transport_kind="serial")
        provider = EspFileProvider(agent=agent, chunk_size=DEFAULT_CHUNK_SIZE)
        self.assertEqual(provider._chunk_size, DEFAULT_CHUNK_SIZE)
        return

    def test_invalid_esp_mount_raises(self) -> None:
        agent = _FakeAgent(transport_kind="ssh-host")
        with self.assertRaises(ConfigurationError):
            EspFileProvider(agent=agent, esp_mount="")
        return

    def test_invalid_volume_alias_raises(self) -> None:
        agent = _FakeAgent(transport_kind="serial")
        with self.assertRaises(ConfigurationError):
            EspFileProvider(agent=agent, volume_alias="")
        return

    def test_invalid_depth_raises(self) -> None:
        agent = _FakeAgent(transport_kind="serial")
        provider = EspFileProvider(agent=agent)
        with self.assertRaises(ConfigurationError):
            provider.directory_tree(path="/EFI", depth=-1)
        return


# ---------------------------------------------------------------------------
# Shell backend tests
# ---------------------------------------------------------------------------


_SHELL_LS_OUTPUT_ROOT = os.linesep.join([
    "Directory of: FS0:\\EFI",
    "",
    "10/01/2025  12:00     <DIR>            BOOT",
    "10/01/2025  12:00     <DIR>            fedora",
    "10/01/2025  12:00              512     grubx64.efi",
    "    1 File(s)          512 bytes",
    "    2 Dir(s)",
])

_SHELL_LS_OUTPUT_BOOT = os.linesep.join([
    "Directory of: FS0:\\EFI\\BOOT",
    "",
    "10/01/2025  12:00          1048576     BOOTX64.EFI",
    "    1 File(s)      1048576 bytes",
])


class TestShellBackend(unittest.TestCase):
    """
        Tests the UEFI Shell backend using ``_FakeAgent`` captured outputs.
    """

    def _make_provider(self, responder: Callable[[str], Tuple[int, str, str]],
                             chunk_size: int = DEFAULT_CHUNK_SIZE) -> Tuple[EspFileProvider, _FakeAgent]:
        """
            Build a provider whose ``_ShellEspBackend`` is driven by
            ``responder``.

            :param responder: Callable from command string to
                              ``(status, stdout, stderr)`` tuple.
            :param chunk_size: Streaming chunk size.

            :returns: ``(provider, fake_agent)`` tuple.
        """
        agent = _FakeAgent(transport_kind="serial", responder=responder)
        provider = EspFileProvider(agent=agent, chunk_size=chunk_size)
        rtnval = (provider, agent)
        return rtnval

    def test_directory_parses_ls_output(self) -> None:
        """``directory`` returns a mapping of names -> EspDirEntry."""

        def responder(command: str) -> Tuple[int, str, str]:
            rtnval: Tuple[int, str, str] = (0, _SHELL_LS_OUTPUT_ROOT, "")
            return rtnval

        provider, _agent = self._make_provider(responder=responder)
        entries = provider.directory(path="/EFI")

        self.assertIn("BOOT", entries)
        self.assertIn("fedora", entries)
        self.assertIn("grubx64.efi", entries)

        self.assertTrue(entries["BOOT"].is_directory)
        self.assertFalse(entries["grubx64.efi"].is_directory)
        self.assertEqual(entries["grubx64.efi"].size, 512)
        return

    def test_directory_nonzero_status_raises(self) -> None:

        def responder(command: str) -> Tuple[int, str, str]:
            rtnval: Tuple[int, str, str] = (1, "", "path not found")
            return rtnval

        provider, _agent = self._make_provider(responder=responder)
        with self.assertRaises(UefiError):
            provider.directory(path="/nope")
        return

    def test_directory_access_denied_raises(self) -> None:

        def responder(command: str) -> Tuple[int, str, str]:
            rtnval: Tuple[int, str, str] = (0, "Access Denied", "")
            return rtnval

        provider, _agent = self._make_provider(responder=responder)
        with self.assertRaises(UefiError):
            provider.directory(path="/EFI")
        return

    def test_file_exists_true(self) -> None:

        def responder(command: str) -> Tuple[int, str, str]:
            rtnval: Tuple[int, str, str] = (0, _SHELL_LS_OUTPUT_ROOT, "")
            return rtnval

        provider, _agent = self._make_provider(responder=responder)
        self.assertTrue(provider.file_exists(path="/EFI/grubx64.efi"))
        return

    def test_file_exists_false_for_directory(self) -> None:

        def responder(command: str) -> Tuple[int, str, str]:
            rtnval: Tuple[int, str, str] = (0, _SHELL_LS_OUTPUT_ROOT, "")
            return rtnval

        provider, _agent = self._make_provider(responder=responder)
        self.assertFalse(provider.file_exists(path="/EFI/BOOT"))
        return

    def test_file_exists_missing(self) -> None:

        def responder(command: str) -> Tuple[int, str, str]:
            rtnval: Tuple[int, str, str] = (0, _SHELL_LS_OUTPUT_ROOT, "")
            return rtnval

        provider, _agent = self._make_provider(responder=responder)
        self.assertFalse(provider.file_exists(path="/EFI/nothing.efi"))
        return

    def test_directory_exists_true(self) -> None:

        def responder(command: str) -> Tuple[int, str, str]:
            rtnval: Tuple[int, str, str] = (0, _SHELL_LS_OUTPUT_ROOT, "")
            return rtnval

        provider, _agent = self._make_provider(responder=responder)
        self.assertTrue(provider.directory_exists(path="/EFI/BOOT"))
        self.assertFalse(provider.directory_exists(path="/EFI/grubx64.efi"))
        return

    def test_directory_exists_failed_listing_returns_false(self) -> None:

        def responder(command: str) -> Tuple[int, str, str]:
            rtnval: Tuple[int, str, str] = (1, "", "not found")
            return rtnval

        provider, _agent = self._make_provider(responder=responder)
        self.assertFalse(provider.directory_exists(path="/nope/missing"))
        return

    def test_file_push_small_file_roundtrip(self) -> None:
        """Small file push issues the expected shell commands with chunk bytes."""
        issued: List[str] = []

        def responder(command: str) -> Tuple[int, str, str]:
            issued.append(command)
            rtnval: Tuple[int, str, str] = (0, "", "")
            return rtnval

        provider, _agent = self._make_provider(responder=responder)

        payload = b"Hello ESP!"
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(payload)
            tmp_path = tmp.name
        try:
            provider.file_push(local=tmp_path, remote="/EFI/BOOT/hello.txt")
        finally:
            os.unlink(tmp_path)

        # Expect: truncation + at least one append.
        self.assertTrue(any(cmd.startswith("rm ") for cmd in issued))
        append_cmds = [cmd for cmd in issued if cmd.startswith("espwrite ")]
        self.assertEqual(len(append_cmds), 1)

        # Verify the payload matches when the base64 token is extracted.
        append_command = append_cmds[0]
        tokens = append_command.split()
        encoded = tokens[-1]
        decoded = base64.b64decode(encoded)
        self.assertEqual(decoded, payload)
        return

    def test_file_push_large_file_chunks(self) -> None:
        """A > 16 MiB file is split into multiple chunks."""
        issued: List[str] = []

        def responder(command: str) -> Tuple[int, str, str]:
            issued.append(command)
            rtnval: Tuple[int, str, str] = (0, "", "")
            return rtnval

        chunk_size = 1024 * 1024  # 1 MiB chunks
        provider, _agent = self._make_provider(
            responder=responder,
            chunk_size=chunk_size,
        )

        total_size = 17 * 1024 * 1024  # 17 MiB
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            # Write non-trivial content rather than sparse zeros.
            chunk_payload = (b"A" * 4096)
            written = 0
            while written < total_size:
                to_write = min(len(chunk_payload), total_size - written)
                tmp.write(chunk_payload[:to_write])
                written = written + to_write
            tmp_path = tmp.name

        try:
            provider.file_push(local=tmp_path, remote="/EFI/big.bin")
        finally:
            os.unlink(tmp_path)

        append_cmds = [cmd for cmd in issued if cmd.startswith("espwrite ")]
        # 17 MiB / 1 MiB = 17 chunks
        self.assertEqual(len(append_cmds), 17)
        return

    def test_file_push_missing_local_raises(self) -> None:
        provider, _agent = self._make_provider(responder=lambda c: (0, "", ""))
        with self.assertRaises(UefiError):
            provider.file_push(local="/tmp/does-not-exist-xyz", remote="/EFI/x")
        return

    def test_file_pull_writes_payload(self) -> None:
        """file_pull writes the UEFI Shell `type` output to the local path."""
        payload = "The UEFI shell payload.\n"

        def responder(command: str) -> Tuple[int, str, str]:
            rtnval: Tuple[int, str, str] = (0, payload, "")
            return rtnval

        provider, _agent = self._make_provider(responder=responder)

        local_handle = tempfile.NamedTemporaryFile(delete=False)
        local_handle.close()
        try:
            provider.file_pull(remote="/EFI/BOOT/hello.txt", local=local_handle.name)
            with open(local_handle.name, "rb") as stream:
                observed = stream.read()
            self.assertEqual(observed, payload.encode("utf-8"))
        finally:
            os.unlink(local_handle.name)
        return

    def test_directory_tree_recurses(self) -> None:
        """directory_tree descends into subdirectories up to ``depth``."""

        def responder(command: str) -> Tuple[int, str, str]:
            tokens = command.split()
            if len(tokens) < 2:
                rtnval: Tuple[int, str, str] = (0, "", "")
                return rtnval
            target = tokens[-1]
            if target.endswith("EFI"):
                rtnval = (0, _SHELL_LS_OUTPUT_ROOT, "")
                return rtnval
            if target.endswith("BOOT"):
                rtnval = (0, _SHELL_LS_OUTPUT_BOOT, "")
                return rtnval
            rtnval = (0, "", "")
            return rtnval

        provider, _agent = self._make_provider(responder=responder)
        tree = provider.directory_tree(path="/EFI", depth=1)

        self.assertEqual(tree["path"], "/EFI")
        self.assertIn("BOOT", tree["entries"])
        self.assertIn("BOOT", tree["children"])
        self.assertIn("BOOTX64.EFI", tree["children"]["BOOT"]["entries"])
        return


# ---------------------------------------------------------------------------
# Host OS (ssh-host) backend tests
# ---------------------------------------------------------------------------


_POSIX_LS_OUTPUT = os.linesep.join([
    "total 8",
    "drwxr-xr-x 2 root root     4096 2025-10-01 12:00 BOOT",
    "-rw-r--r-- 1 root root     1024 2025-10-01 12:00 grubx64.efi",
])


class TestHostOsBackend(unittest.TestCase):
    """
        Tests the ssh-host backend using ``_FakeAgent`` canned responses.
    """

    def _make_provider(self, responder: Callable[[str], Tuple[int, str, str]],
                             chunk_size: int = DEFAULT_CHUNK_SIZE) -> Tuple[EspFileProvider, _FakeAgent]:
        agent = _FakeAgent(transport_kind="ssh-host", responder=responder)
        provider = EspFileProvider(agent=agent, chunk_size=chunk_size)
        rtnval = (provider, agent)
        return rtnval

    def test_directory_parses_posix_ls(self) -> None:

        def responder(command: str) -> Tuple[int, str, str]:
            rtnval: Tuple[int, str, str] = (0, _POSIX_LS_OUTPUT, "")
            return rtnval

        provider, _agent = self._make_provider(responder=responder)
        entries = provider.directory(path="/EFI")

        self.assertIn("BOOT", entries)
        self.assertTrue(entries["BOOT"].is_directory)
        self.assertIn("grubx64.efi", entries)
        self.assertFalse(entries["grubx64.efi"].is_directory)
        self.assertEqual(entries["grubx64.efi"].size, 1024)
        return

    def test_file_exists_probes_test_f(self) -> None:
        issued: List[str] = []

        def responder(command: str) -> Tuple[int, str, str]:
            issued.append(command)
            if command.startswith("test -f "):
                rtnval: Tuple[int, str, str] = (0, "", "")
                return rtnval
            rtnval = (1, "", "")
            return rtnval

        provider, _agent = self._make_provider(responder=responder)
        self.assertTrue(provider.file_exists(path="/EFI/grubx64.efi"))
        self.assertTrue(any("/boot/efi/EFI/grubx64.efi" in c for c in issued))
        return

    def test_directory_exists_probes_test_d(self) -> None:

        def responder(command: str) -> Tuple[int, str, str]:
            if command.startswith("test -d "):
                rtnval: Tuple[int, str, str] = (0, "", "")
                return rtnval
            rtnval = (1, "", "")
            return rtnval

        provider, _agent = self._make_provider(responder=responder)
        self.assertTrue(provider.directory_exists(path="/EFI"))
        return

    def test_file_exists_nonexistent_returns_false(self) -> None:

        def responder(command: str) -> Tuple[int, str, str]:
            rtnval: Tuple[int, str, str] = (1, "", "")
            return rtnval

        provider, _agent = self._make_provider(responder=responder)
        self.assertFalse(provider.file_exists(path="/EFI/nope"))
        return

    def test_file_push_chunks_and_seeks(self) -> None:
        """file_push splits payload into ``chunk_size`` blocks with seek offsets."""
        issued: List[str] = []

        def responder(command: str) -> Tuple[int, str, str]:
            issued.append(command)
            rtnval: Tuple[int, str, str] = (0, "", "")
            return rtnval

        chunk_size = 4096
        provider, _agent = self._make_provider(
            responder=responder,
            chunk_size=chunk_size,
        )

        payload = b"X" * (chunk_size * 3 + 10)
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(payload)
            tmp_path = tmp.name
        try:
            provider.file_push(local=tmp_path, remote="/EFI/big.bin")
        finally:
            os.unlink(tmp_path)

        dd_cmds = [c for c in issued if "dd of=" in c]
        self.assertEqual(len(dd_cmds), 4)

        # Each dd command carries an explicit seek offset; offsets grow.
        offsets: List[int] = []
        for cmd in dd_cmds:
            marker = "seek="
            idx = cmd.find(marker)
            remainder = cmd[idx + len(marker):]
            seek_token = remainder.split(" ", 1)[0]
            offsets.append(int(seek_token))
        self.assertEqual(offsets, [0, chunk_size, chunk_size * 2, chunk_size * 3])
        return

    def test_file_pull_decodes_base64(self) -> None:
        """file_pull decodes the remote base64 output back to bytes."""
        payload = b"binary-esp-content\x00\x01\x02"
        encoded = base64.b64encode(payload).decode("ascii")

        def responder(command: str) -> Tuple[int, str, str]:
            rtnval: Tuple[int, str, str] = (0, encoded, "")
            return rtnval

        provider, _agent = self._make_provider(responder=responder)

        local_handle = tempfile.NamedTemporaryFile(delete=False)
        local_handle.close()
        try:
            provider.file_pull(remote="/EFI/bin", local=local_handle.name)
            with open(local_handle.name, "rb") as stream:
                observed = stream.read()
            self.assertEqual(observed, payload)
        finally:
            os.unlink(local_handle.name)
        return

    def test_file_pull_status_nonzero_raises(self) -> None:

        def responder(command: str) -> Tuple[int, str, str]:
            rtnval: Tuple[int, str, str] = (1, "", "cat: No such file")
            return rtnval

        provider, _agent = self._make_provider(responder=responder)
        local_handle = tempfile.NamedTemporaryFile(delete=False)
        local_handle.close()
        try:
            with self.assertRaises(UefiError):
                provider.file_pull(remote="/EFI/missing", local=local_handle.name)
        finally:
            os.unlink(local_handle.name)
        return

    def test_eacces_translation(self) -> None:

        def responder(command: str) -> Tuple[int, str, str]:
            rtnval: Tuple[int, str, str] = (1, "", "Permission denied")
            return rtnval

        provider, _agent = self._make_provider(responder=responder)
        with self.assertRaises(UefiError):
            provider.directory(path="/EFI")
        return


# ---------------------------------------------------------------------------
# Redfish backend tests
# ---------------------------------------------------------------------------


class TestRedfishBackend(unittest.TestCase):
    """
        Verifies every Redfish backend operation raises a clear
        unsupported-error message.
    """

    def _make_provider(self) -> EspFileProvider:
        agent = _FakeAgent(transport_kind="redfish")
        rtnval = EspFileProvider(agent=agent)
        return rtnval

    def test_file_push_raises(self) -> None:
        provider = self._make_provider()
        with self.assertRaises(UefiError) as cm:
            # provider.file_push validates local existence first, so feed a
            # real file to reach the backend dispatch.
            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                tmp.write(b"x")
                tmp_path = tmp.name
            try:
                provider.file_push(local=tmp_path, remote="/EFI/x")
            finally:
                os.unlink(tmp_path)

        self.assertIn("Redfish ESP file operations not supported", str(cm.exception))
        return

    def test_file_pull_raises(self) -> None:
        provider = self._make_provider()
        with self.assertRaises(UefiError) as cm:
            provider.file_pull(remote="/EFI/x", local="/tmp/out")
        self.assertIn("Redfish ESP file operations not supported", str(cm.exception))
        return

    def test_file_exists_raises(self) -> None:
        provider = self._make_provider()
        with self.assertRaises(UefiError):
            provider.file_exists(path="/EFI/x")
        return

    def test_directory_exists_raises(self) -> None:
        provider = self._make_provider()
        with self.assertRaises(UefiError):
            provider.directory_exists(path="/EFI")
        return

    def test_directory_raises(self) -> None:
        provider = self._make_provider()
        with self.assertRaises(UefiError):
            provider.directory(path="/EFI")
        return

    def test_directory_tree_raises(self) -> None:
        provider = self._make_provider()
        with self.assertRaises(UefiError):
            provider.directory_tree(path="/EFI", depth=1)
        return


# ---------------------------------------------------------------------------
# Aspects passthrough tests
# ---------------------------------------------------------------------------


class TestAspectsPassthrough(unittest.TestCase):
    """
        Assert ``aspects=None`` substitutes the instance default.
    """

    def test_aspects_none_uses_instance_default(self) -> None:
        captured: List[Optional[UefiCmdAspects]] = []

        def responder(command: str) -> Tuple[int, str, str]:
            rtnval: Tuple[int, str, str] = (0, "", "")
            return rtnval

        agent = _FakeAgent(transport_kind="ssh-host", responder=responder)

        # Wrap run_cmd to capture the effective aspects.
        original_run_cmd = agent.run_cmd

        def _wrapper(command: str,
                     exp_status: Union[int, Tuple[int, ...]] = 0,
                     user: Optional[str] = None,
                     aspects: Optional[UefiCmdAspects] = None) -> Tuple[int, str, str]:
            captured.append(aspects)
            rtnval = original_run_cmd(
                command=command,
                exp_status=exp_status,
                user=user,
                aspects=aspects,
            )
            return rtnval

        agent.run_cmd = _wrapper  # type: ignore[assignment]

        custom_aspects = UefiCmdAspects()
        provider = EspFileProvider(agent=agent, aspects=custom_aspects)

        # file_exists uses a single run_cmd.
        provider.file_exists(path="/EFI/x")

        self.assertEqual(len(captured), 1)
        self.assertIs(captured[0], custom_aspects)
        return


# ---------------------------------------------------------------------------
# Defaults / constants sanity
# ---------------------------------------------------------------------------


class TestDefaults(unittest.TestCase):
    """
        Sanity-check the module-level defaults.
    """

    def test_defaults(self) -> None:
        self.assertEqual(DEFAULT_ESP_MOUNT, "/boot/efi")
        self.assertEqual(DEFAULT_VOLUME_ALIAS, "FS0:")
        self.assertEqual(DEFAULT_CHUNK_SIZE, 65536)
        self.assertEqual(MIN_CHUNK_SIZE, 512)
        self.assertEqual(MAX_CHUNK_SIZE, 16 * 1024 * 1024)
        return

    def test_esp_dir_entry_is_frozen(self) -> None:
        """`EspDirEntry` is a frozen dataclass."""
        entry = EspDirEntry(name="a", size=1, is_directory=False, modified=None)
        with self.assertRaises(Exception):
            entry.name = "b"  # type: ignore[misc]
        return


if __name__ == "__main__":
    unittest.main()
