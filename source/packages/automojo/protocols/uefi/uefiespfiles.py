"""
    .. module:: uefiespfiles
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Module containing the :class:`EspFileProvider` facade and its
               private transport-specific backends that deliver the
               ``SshAgent``-shaped file API (``file_push``, ``file_pull``,
               ``file_exists``, ``directory_exists``, ``directory``,
               ``directory_tree``) across the three supported UEFI transports
               — UEFI Shell (serial / ipmi-sol), SSH-to-host, and Redfish
               VirtualMedia.

               The facade owns path normalization (UEFI ``\\``-separated
               convention vs. POSIX), chunked base64 streaming for the SSH
               and shell transports, and cross-transport error taxonomy.
               Callers do not branch on transport kind.  Raises
               :class:`UefiError` on file-system faults.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Automation Mojo LLC"
__credits__ = []


import base64
import os

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from automojo.errors.exceptions import ConfigurationError

from automojo.protocols.uefi.uefiaspects import (
    DEFAULT_CMD_ASPECTS,
    UefiCmdAspects,
    resolve_aspects,
)
from automojo.protocols.uefi.uefiexceptions import UefiError


# ---------------------------------------------------------------------------
# Module-level defaults
# ---------------------------------------------------------------------------

DEFAULT_ESP_MOUNT: str = "/boot/efi"
DEFAULT_VOLUME_ALIAS: str = "FS0:"
DEFAULT_CHUNK_SIZE: int = 65536

MIN_CHUNK_SIZE: int = 512
MAX_CHUNK_SIZE: int = 16 * 1024 * 1024

LARGE_FILE_THRESHOLD: int = 16 * 1024 * 1024


# ---------------------------------------------------------------------------
# EspDirEntry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EspDirEntry:
    """
        Typed directory-entry record returned by
        :meth:`EspFileProvider.directory` and
        :meth:`EspFileProvider.directory_tree`.

        :param name: File or directory basename (no parent segments).
        :param size: Size in bytes as reported by the backend.  For
                     directories, transport-dependent (often ``0``).
        :param is_directory: ``True`` when the entry is a directory.
        :param modified: Display-form modification timestamp.  The exact
                         encoding varies across transports; callers should
                         treat it as opaque unless the transport is known.
    """

    name: str
    size: int
    is_directory: bool
    modified: Optional[str] = None


# ---------------------------------------------------------------------------
# Path normalization helpers (pure functions)
# ---------------------------------------------------------------------------


def _to_shell_path(posix_path: str, volume_alias: str = DEFAULT_VOLUME_ALIAS) -> str:
    """
        Normalize ``posix_path`` into the UEFI Shell convention.  Paths that
        already begin with a ``FS#:`` or ``BLK#:`` alias are returned with
        their separators flipped to ``\\`` but otherwise preserved.

        :param posix_path: A POSIX-style path rooted at ``/`` or a UEFI
                           path that already contains a volume alias.
        :param volume_alias: Default alias applied to ``posix_path`` values
                             that do not already contain one.

        :returns: A string of the form ``FS0:\\EFI\\BOOT\\foo``.
        :raises: UefiError when ``..`` segments remain after normalization.
    """
    if posix_path is None:
        err_msg_lines = [
            "_to_shell_path: 'posix_path' must not be None.",
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiError(err_msg)

    working = posix_path

    # Detect an existing alias prefix (e.g. 'FS0:' or 'BLK1:').
    alias_prefix: Optional[str] = None
    if len(working) >= 3 and ":" in working[:6]:
        colon_index = working.find(":")
        candidate_alias = working[: colon_index + 1]
        # Must be either FS# or BLK#.
        alias_core = candidate_alias[:-1]
        if alias_core.startswith("FS") or alias_core.startswith("BLK"):
            alias_prefix = candidate_alias
            working = working[colon_index + 1:]

    if alias_prefix is None:
        alias_prefix = volume_alias

    # Flip separators uniformly.
    flipped = working.replace("/", "\\")

    # Ensure a leading backslash after the alias.
    if not flipped.startswith("\\"):
        flipped = "\\" + flipped

    # Reject `..` traversal AFTER normalization.
    segments = [seg for seg in flipped.split("\\") if seg != ""]
    for seg in segments:
        if seg == "..":
            err_msg_lines = [
                "_to_shell_path: parent-directory traversal is rejected.",
                "INPUT: {}".format(posix_path),
                "NORMALIZED: {}{}".format(alias_prefix, flipped),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg)

    rtnval = alias_prefix + flipped
    return rtnval


def _from_shell_path(shell_path: str) -> str:
    """
        Invert :func:`_to_shell_path`.  Strips any ``FS#:`` / ``BLK#:`` alias
        prefix and flips ``\\`` separators to ``/``.

        :param shell_path: A UEFI Shell convention path.

        :returns: A POSIX-style path beginning with ``/``.
        :raises: UefiError when ``shell_path`` is ``None``.
    """
    if shell_path is None:
        err_msg_lines = [
            "_from_shell_path: 'shell_path' must not be None.",
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiError(err_msg)

    working = shell_path

    # Remove an alias prefix if present.
    if ":" in working[:6]:
        colon_index = working.find(":")
        candidate_alias = working[: colon_index + 1]
        alias_core = candidate_alias[:-1]
        if alias_core.startswith("FS") or alias_core.startswith("BLK"):
            working = working[colon_index + 1:]

    flipped = working.replace("\\", "/")

    if not flipped.startswith("/"):
        flipped = "/" + flipped

    rtnval = flipped
    return rtnval


def _to_host_posix_path(path: str, esp_mount: str) -> str:
    """
        Translate a UEFI-convention or POSIX path to a host-OS path rooted
        under ``esp_mount``.  Paths already rooted at ``esp_mount`` are
        returned unchanged.

        :param path: UEFI or POSIX path.
        :param esp_mount: The ESP mount point on the host OS.

        :returns: Absolute POSIX path under ``esp_mount``.
        :raises: UefiError when the translated path escapes ``esp_mount``.
    """
    if path is None:
        err_msg_lines = [
            "_to_host_posix_path: 'path' must not be None.",
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiError(err_msg)

    as_posix = _from_shell_path(path)

    normalized_mount = esp_mount.rstrip("/")
    if len(normalized_mount) == 0:
        normalized_mount = "/"

    # If the path is already rooted at esp_mount, respect that.
    if as_posix == normalized_mount or as_posix.startswith(normalized_mount + "/"):
        candidate = as_posix
    else:
        if as_posix.startswith("/"):
            candidate = normalized_mount + as_posix
        else:
            candidate = normalized_mount + "/" + as_posix

    # Reject `..` segments after expansion.
    segments = [seg for seg in candidate.split("/") if seg != ""]
    for seg in segments:
        if seg == "..":
            err_msg_lines = [
                "_to_host_posix_path: parent-directory traversal is rejected.",
                "INPUT: {}".format(path),
                "RESOLVED: {}".format(candidate),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg)

    rtnval = candidate
    return rtnval


def _quote_shell_token(token: str) -> str:
    """
        Quote a UEFI Shell token when it contains whitespace.  The UEFI
        Shell uses ``"..."`` style quoting.

        :param token: The token to quote.

        :returns: A quoted (or unmodified) token string.
    """
    has_space = False
    for ch in token:
        if ch == " " or ch == "\t":
            has_space = True
            break

    if has_space:
        rtnval = '"' + token + '"'
    else:
        rtnval = token

    return rtnval


def _validate_chunk_size(chunk_size: int) -> int:
    """
        Validate the ``chunk_size`` argument is an int within the supported
        range ``[MIN_CHUNK_SIZE, MAX_CHUNK_SIZE]``.

        :param chunk_size: Candidate chunk size.

        :returns: The validated integer.
        :raises: ConfigurationError
    """
    if not isinstance(chunk_size, int) or isinstance(chunk_size, bool):
        err_msg_lines = [
            "EspFileProvider: 'chunk_size' must be an int.",
            "GOT: value={!r} type={}".format(chunk_size, type(chunk_size).__name__),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise ConfigurationError(err_msg) from None

    if chunk_size < MIN_CHUNK_SIZE or chunk_size > MAX_CHUNK_SIZE:
        err_msg_lines = [
            "EspFileProvider: 'chunk_size' is outside the supported range.",
            "GOT: value={}".format(chunk_size),
            "ALLOWED_MIN: {}".format(MIN_CHUNK_SIZE),
            "ALLOWED_MAX: {}".format(MAX_CHUNK_SIZE),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise ConfigurationError(err_msg) from None

    rtnval = chunk_size
    return rtnval


# ---------------------------------------------------------------------------
# Abstract backend contract
# ---------------------------------------------------------------------------


class _AbstractEspBackend:
    """
        Abstract contract implemented by each transport-specific backend.
        Backends perform raw I/O; the facade owns path normalization
        decisions and aspects resolution.
    """

    def __init__(self, agent: Any,
                       esp_mount: str,
                       volume_alias: str,
                       chunk_size: int) -> None:
        """
            Capture shared backend configuration.

            :param agent: The :class:`UefiAgent` bound to this backend.
            :param esp_mount: The POSIX mount point for the SSH-host backend.
            :param volume_alias: The UEFI volume alias for the shell backend.
            :param chunk_size: Bytes per streamed chunk.
        """
        self._agent = agent
        self._esp_mount = esp_mount
        self._volume_alias = volume_alias
        self._chunk_size = chunk_size
        return

    def file_push(self, local: str, remote: str, aspects: UefiCmdAspects) -> None:
        """
            Upload a local file to the remote ESP.

            :param local: Absolute source path on the test harness.
            :param remote: Destination path on the ESP.
            :param aspects: Resolved :class:`UefiCmdAspects` for this call.
        """
        err_msg_lines = [
            "_AbstractEspBackend.file_push: subclass must override.",
            "BACKEND: {}".format(type(self).__name__),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiError(err_msg)

    def file_pull(self, remote: str, local: str, aspects: UefiCmdAspects) -> None:
        """
            Download a remote ESP file to ``local``.

            :param remote: Source path on the ESP.
            :param local: Destination path on the test harness.
            :param aspects: Resolved :class:`UefiCmdAspects` for this call.
        """
        err_msg_lines = [
            "_AbstractEspBackend.file_pull: subclass must override.",
            "BACKEND: {}".format(type(self).__name__),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiError(err_msg)

    def file_exists(self, path: str, aspects: UefiCmdAspects) -> bool:
        """
            Return ``True`` if ``path`` exists and is a regular file.

            :param path: Path to test.
            :param aspects: Resolved :class:`UefiCmdAspects` for this call.
        """
        err_msg_lines = [
            "_AbstractEspBackend.file_exists: subclass must override.",
            "BACKEND: {}".format(type(self).__name__),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiError(err_msg)

    def directory_exists(self, path: str, aspects: UefiCmdAspects) -> bool:
        """
            Return ``True`` if ``path`` exists and is a directory.

            :param path: Path to test.
            :param aspects: Resolved :class:`UefiCmdAspects` for this call.
        """
        err_msg_lines = [
            "_AbstractEspBackend.directory_exists: subclass must override.",
            "BACKEND: {}".format(type(self).__name__),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiError(err_msg)

    def directory(self, path: str, aspects: UefiCmdAspects) -> Dict[str, EspDirEntry]:
        """
            Return the immediate children of ``path``.

            :param path: Directory path to list.
            :param aspects: Resolved :class:`UefiCmdAspects` for this call.
        """
        err_msg_lines = [
            "_AbstractEspBackend.directory: subclass must override.",
            "BACKEND: {}".format(type(self).__name__),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiError(err_msg)

    def directory_tree(self, path: str, depth: int, aspects: UefiCmdAspects) -> Dict[str, Any]:
        """
            Return a nested dict representation of ``path`` down to ``depth``
            levels.

            :param path: Root directory to tree-walk.
            :param depth: Maximum recursion depth.
            :param aspects: Resolved :class:`UefiCmdAspects` for this call.
        """
        err_msg_lines = [
            "_AbstractEspBackend.directory_tree: subclass must override.",
            "BACKEND: {}".format(type(self).__name__),
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiError(err_msg)


# ---------------------------------------------------------------------------
# Shell backend (serial / ipmi-sol)
# ---------------------------------------------------------------------------


class _ShellEspBackend(_AbstractEspBackend):
    """
        UEFI Shell backend.  Drives ``ls``, ``cp``, ``type`` through the
        agent's :meth:`UefiAgent.run_cmd`.  Paths are normalized to the
        UEFI convention (``FS0:\\EFI\\BOOT\\foo``).

        Large-file streaming note:
            ``file_push`` encodes the source bytes as base64 and appends
            chunks through repeated shell commands.  Extremely large files
            over a slow serial link will be rate-limited by the underlying
            transport; callers should prefer Redfish / ssh-host for large
            payloads.
    """

    def file_push(self, local: str, remote: str, aspects: UefiCmdAspects) -> None:
        """
            Upload ``local`` to ``remote`` on the ESP via the UEFI Shell.

            :param local: Local source file path.
            :param remote: Destination UEFI-convention path.
            :param aspects: Resolved :class:`UefiCmdAspects`.

            :raises: UefiError
        """
        shell_remote = _to_shell_path(posix_path=remote, volume_alias=self._volume_alias)
        source_size = os.path.getsize(local)

        self._stream_local_to_shell(
            local=local,
            shell_remote=shell_remote,
            source_size=source_size,
            aspects=aspects,
        )
        return

    def file_pull(self, remote: str, local: str, aspects: UefiCmdAspects) -> None:
        """
            Download ``remote`` from the ESP via the UEFI Shell ``type``
            command.

            :param remote: Source UEFI-convention path.
            :param local: Local destination path.
            :param aspects: Resolved :class:`UefiCmdAspects`.

            :raises: UefiError
        """
        shell_remote = _to_shell_path(posix_path=remote, volume_alias=self._volume_alias)
        quoted_remote = _quote_shell_token(token=shell_remote)
        command = "type {}".format(quoted_remote)

        status, stdout, stderr = self._agent.run_cmd(command=command, aspects=aspects)

        self._raise_on_access_denied(
            command=command,
            status=status,
            stdout=stdout,
            stderr=stderr,
            path=shell_remote,
        )

        # The shell `type` command returns the raw file contents.  Production
        # firmware may interleave prompt noise; a defensive implementation
        # strips any trailing shell prompt residue.
        payload_text = stdout if stdout is not None else ""
        payload = payload_text.encode("utf-8")

        with open(local, "wb") as stream:
            stream.write(payload)

        return

    def file_exists(self, path: str, aspects: UefiCmdAspects) -> bool:
        """
            Return ``True`` when ``path`` exists and is a regular file.

            :param path: UEFI-convention or POSIX path.
            :param aspects: Resolved :class:`UefiCmdAspects`.

            :returns: bool
        """
        entries_by_name = self._list_parent(path=path, aspects=aspects)
        if entries_by_name is None:
            rtnval = False
            return rtnval

        basename = self._basename_shell(path=path)
        entry = entries_by_name.get(basename)
        if entry is None:
            rtnval = False
            return rtnval

        rtnval = not entry.is_directory
        return rtnval

    def directory_exists(self, path: str, aspects: UefiCmdAspects) -> bool:
        """
            Return ``True`` when ``path`` exists and is a directory.

            :param path: UEFI-convention or POSIX path.
            :param aspects: Resolved :class:`UefiCmdAspects`.

            :returns: bool
        """
        entries_by_name = self._list_parent(path=path, aspects=aspects)
        if entries_by_name is None:
            rtnval = False
            return rtnval

        basename = self._basename_shell(path=path)
        entry = entries_by_name.get(basename)
        if entry is None:
            rtnval = False
            return rtnval

        rtnval = entry.is_directory
        return rtnval

    def directory(self, path: str, aspects: UefiCmdAspects) -> Dict[str, EspDirEntry]:
        """
            List the immediate children of ``path``.

            :param path: UEFI-convention directory path.
            :param aspects: Resolved :class:`UefiCmdAspects`.

            :returns: Mapping of basename to :class:`EspDirEntry`.
            :raises: UefiError
        """
        shell_path = _to_shell_path(posix_path=path, volume_alias=self._volume_alias)
        quoted_path = _quote_shell_token(token=shell_path)
        command = "ls {}".format(quoted_path)

        status, stdout, stderr = self._agent.run_cmd(command=command, aspects=aspects)
        self._raise_on_access_denied(
            command=command,
            status=status,
            stdout=stdout,
            stderr=stderr,
            path=shell_path,
        )

        if status != 0:
            err_msg_lines = [
                "ShellEspBackend.directory: 'ls' returned a non-zero status.",
                "COMMAND: {}".format(command),
                "STATUS: {}".format(status),
                "STDOUT:",
                stdout if stdout is not None else "",
                "STDERR:",
                stderr if stderr is not None else "",
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg)

        entries = self._parse_ls_output(output=stdout if stdout is not None else "")
        rtnval: Dict[str, EspDirEntry] = {}
        for entry in entries:
            rtnval[entry.name] = entry
        return rtnval

    def directory_tree(self, path: str, depth: int, aspects: UefiCmdAspects) -> Dict[str, Any]:
        """
            Recursively enumerate ``path`` down to ``depth`` levels.

            :param path: UEFI-convention directory path.
            :param depth: Maximum recursion depth.  ``0`` returns only the
                          root listing.
            :param aspects: Resolved :class:`UefiCmdAspects`.

            :returns: Nested dict with ``entry``, ``children`` keys.
            :raises: UefiError
        """
        entries = self.directory(path=path, aspects=aspects)
        rtnval: Dict[str, Any] = {
            "path": path,
            "entries": entries,
            "children": {},
        }

        if depth > 0:
            for name, entry in entries.items():
                if entry.is_directory:
                    child_path = path.rstrip("/\\") + "\\" + name
                    subtree = self.directory_tree(
                        path=child_path,
                        depth=depth - 1,
                        aspects=aspects,
                    )
                    rtnval["children"][name] = subtree

        return rtnval

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _stream_local_to_shell(self, local: str,
                                     shell_remote: str,
                                     source_size: int,
                                     aspects: UefiCmdAspects) -> None:
        """
            Stream ``local`` into ``shell_remote`` in ``self._chunk_size``
            chunks using base64-encoded append commands.  The UEFI Shell
            does not expose stdin redirection uniformly, so we rely on the
            ``cp``/``echo``/``type`` triad surfaced by most Tianocore
            implementations.

            This helper is intentionally transport-agnostic: it issues the
            same command shapes regardless of serial / ipmi-sol.  Concrete
            UEFI Shell implementations that lack base64 are out of scope
            for v1 and should prefer the SSH-to-host or Redfish backends.

            :param local: Local source path.
            :param shell_remote: Normalized UEFI remote path.
            :param source_size: Size of the source file in bytes.
            :param aspects: Resolved :class:`UefiCmdAspects`.

            :raises: UefiError
        """
        quoted_remote = _quote_shell_token(token=shell_remote)

        # Issue an initial truncation command so that the target starts empty.
        truncate_cmd = "rm {}".format(quoted_remote)
        self._agent.run_cmd(command=truncate_cmd, aspects=aspects)

        bytes_sent = 0
        with open(local, "rb") as stream:
            while True:
                chunk = stream.read(self._chunk_size)
                if len(chunk) == 0:
                    break

                encoded = base64.b64encode(chunk).decode("ascii")
                append_cmd = "espwrite {} {}".format(quoted_remote, encoded)

                status, stdout, stderr = self._agent.run_cmd(
                    command=append_cmd,
                    aspects=aspects,
                )
                self._raise_on_access_denied(
                    command=append_cmd,
                    status=status,
                    stdout=stdout,
                    stderr=stderr,
                    path=shell_remote,
                )

                bytes_sent = bytes_sent + len(chunk)

        if bytes_sent != source_size:
            err_msg_lines = [
                "ShellEspBackend.file_push: byte-count mismatch after streaming.",
                "LOCAL: {}".format(local),
                "REMOTE: {}".format(shell_remote),
                "EXPECTED: {}".format(source_size),
                "OBSERVED: {}".format(bytes_sent),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg)

        return

    def _list_parent(self, path: str,
                           aspects: UefiCmdAspects) -> Optional[Dict[str, EspDirEntry]]:
        """
            List the parent directory of ``path`` so that the caller can
            probe for the child entry.  Returns ``None`` when the parent
            listing itself fails (treated as non-existence for the
            non-raising existence probes).

            :param path: UEFI-convention or POSIX path.
            :param aspects: Resolved :class:`UefiCmdAspects`.

            :returns: Mapping of basename to :class:`EspDirEntry`, or ``None``.
        """
        shell_path = _to_shell_path(posix_path=path, volume_alias=self._volume_alias)
        parent = self._parent_shell(path=shell_path)

        quoted_parent = _quote_shell_token(token=parent)
        command = "ls {}".format(quoted_parent)

        status, stdout, stderr = self._agent.run_cmd(command=command, aspects=aspects)

        self._raise_on_access_denied(
            command=command,
            status=status,
            stdout=stdout,
            stderr=stderr,
            path=parent,
        )

        if status != 0:
            rtnval: Optional[Dict[str, EspDirEntry]] = None
            return rtnval

        entries = self._parse_ls_output(output=stdout if stdout is not None else "")
        rtnval = {}
        for entry in entries:
            rtnval[entry.name] = entry
        return rtnval

    def _parent_shell(self, path: str) -> str:
        """
            Return the parent path of a UEFI-convention ``path`` string.

            :param path: A UEFI-convention path.

            :returns: Parent path string.
        """
        # Strip alias prefix before slicing; reapply afterwards.
        alias_prefix = ""
        remainder = path
        if ":" in remainder[:6]:
            colon_index = remainder.find(":")
            alias_prefix = remainder[: colon_index + 1]
            remainder = remainder[colon_index + 1:]

        if remainder.endswith("\\") and len(remainder) > 1:
            remainder = remainder[:-1]

        last_sep = remainder.rfind("\\")
        if last_sep <= 0:
            parent_remainder = "\\"
        else:
            parent_remainder = remainder[:last_sep]

        rtnval = alias_prefix + parent_remainder
        return rtnval

    def _basename_shell(self, path: str) -> str:
        """
            Return the basename of a UEFI-convention path.

            :param path: UEFI or POSIX path.

            :returns: The final path segment.
        """
        shell_path = _to_shell_path(posix_path=path, volume_alias=self._volume_alias)
        # Strip alias.
        remainder = shell_path
        if ":" in remainder[:6]:
            colon_index = remainder.find(":")
            remainder = remainder[colon_index + 1:]

        if remainder.endswith("\\") and len(remainder) > 1:
            remainder = remainder[:-1]

        last_sep = remainder.rfind("\\")
        if last_sep < 0:
            rtnval = remainder
        else:
            rtnval = remainder[last_sep + 1:]
        return rtnval

    def _parse_ls_output(self, output: str) -> List[EspDirEntry]:
        """
            Parse a UEFI Shell ``ls`` output block into a list of
            :class:`EspDirEntry`.  The expected line format is::

                <date> <time> <attr> <size> <name>

            where ``<attr>`` is a column containing ``<DIR>`` for
            directories.  Blank lines, header lines, and lines containing
            ``Directory of:`` / ``file(s)`` / ``bytes`` markers are
            skipped.  The loose parser is designed to tolerate minor
            formatting variations across firmware implementations.

            :param output: Raw ``ls`` stdout.

            :returns: List of :class:`EspDirEntry`.
        """
        rtnval: List[EspDirEntry] = []

        for raw_line in output.splitlines():
            line = raw_line.strip()
            if len(line) == 0:
                continue

            lower = line.lower()
            if lower.startswith("directory of"):
                continue
            if "file(s)" in lower:
                continue
            if "dir(s)" in lower:
                continue
            if lower.startswith("volume"):
                continue

            tokens = line.split()
            if len(tokens) < 3:
                continue

            # The trailing token is the name.  The size / attribute column
            # appears before it.  We recognize <DIR> anywhere in the line
            # as the directory marker.
            is_directory = "<DIR>" in tokens

            size = 0
            if not is_directory:
                # Walk tokens backwards looking for an integer-like token.
                size_token: Optional[str] = None
                for candidate in tokens[:-1][::-1]:
                    # Strip commas used as thousand separators.
                    stripped = candidate.replace(",", "")
                    if stripped.isdigit():
                        size_token = stripped
                        break
                if size_token is not None:
                    size = int(size_token)

            name = tokens[-1]
            if name == "." or name == "..":
                continue

            # Attempt to extract a simple "<date> <time>" pair as the
            # modified display string — best-effort only.
            modified: Optional[str] = None
            if len(tokens) >= 2:
                modified = tokens[0] + " " + tokens[1]

            entry = EspDirEntry(
                name=name,
                size=size,
                is_directory=is_directory,
                modified=modified,
            )
            rtnval.append(entry)

        return rtnval

    def _raise_on_access_denied(self, command: str,
                                      status: Optional[int],
                                      stdout: Optional[str],
                                      stderr: Optional[str],
                                      path: str) -> None:
        """
            Inspect ``stdout``/``stderr`` for the canonical UEFI Shell
            "Access Denied" response and translate it to :class:`UefiError`.

            :param command: Issued shell command.
            :param status: Reported shell status.
            :param stdout: Captured stdout.
            :param stderr: Captured stderr.
            :param path: Attempted path (for diagnostics).

            :raises: UefiError
        """
        haystack_lines: List[str] = []
        if stdout is not None:
            haystack_lines.append(stdout)
        if stderr is not None:
            haystack_lines.append(stderr)

        haystack = " ".join(haystack_lines).lower()
        if "access denied" in haystack:
            err_msg_lines = [
                "ShellEspBackend: firmware reported Access Denied.",
                "COMMAND: {}".format(command),
                "PATH: {}".format(path),
                "STATUS: {}".format(status),
                "STDOUT: {}".format(stdout),
                "STDERR: {}".format(stderr),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg)

        return


# ---------------------------------------------------------------------------
# SSH-to-host backend
# ---------------------------------------------------------------------------


class _HostOsEspBackend(_AbstractEspBackend):
    """
        SSH-to-host backend.  Drives standard POSIX utilities through the
        agent's ``run_cmd`` (which is an ssh-host transport).  Paths are
        translated into POSIX under ``self._esp_mount``.

        Large-file ``file_push`` notes:
            The ssh-host transport exposed via :meth:`UefiAgent.run_cmd`
            does not provide a stdin channel, so file content is transferred
            as base64 payloads on the command line.  The backend splits
            large payloads into ``self._chunk_size`` blocks and appends
            each block with a ``dd`` command using an explicit ``seek``
            offset.  Single commands are therefore bounded by shell
            command-line length limits (~128 KiB typical); chunks above
            that should use a smaller ``chunk_size``.
    """

    def file_push(self, local: str, remote: str, aspects: UefiCmdAspects) -> None:
        """
            Upload ``local`` to the ESP-mounted location derived from
            ``remote``.  Large files are streamed in ``chunk_size`` blocks.

            :param local: Local source path.
            :param remote: UEFI or POSIX destination path.
            :param aspects: Resolved :class:`UefiCmdAspects`.

            :raises: UefiError
        """
        remote_posix = _to_host_posix_path(path=remote, esp_mount=self._esp_mount)
        source_size = os.path.getsize(local)

        # Truncate the destination.
        truncate_cmd = ": > '{}'".format(remote_posix)
        status, stdout, stderr = self._agent.run_cmd(
            command=truncate_cmd,
            aspects=aspects,
        )
        self._raise_on_eacces(
            command=truncate_cmd,
            status=status,
            stdout=stdout,
            stderr=stderr,
            path=remote_posix,
        )

        bytes_sent = 0
        with open(local, "rb") as stream:
            while True:
                chunk = stream.read(self._chunk_size)
                if len(chunk) == 0:
                    break

                encoded = base64.b64encode(chunk).decode("ascii")
                seek_blocks = bytes_sent
                append_cmd = (
                    "printf %s '{}' | base64 -d | "
                    "dd of='{}' bs=1 seek={} conv=notrunc status=none"
                ).format(encoded, remote_posix, seek_blocks)

                status, stdout, stderr = self._agent.run_cmd(
                    command=append_cmd,
                    aspects=aspects,
                )
                self._raise_on_eacces(
                    command=append_cmd,
                    status=status,
                    stdout=stdout,
                    stderr=stderr,
                    path=remote_posix,
                )

                bytes_sent = bytes_sent + len(chunk)

        if bytes_sent != source_size:
            err_msg_lines = [
                "HostOsEspBackend.file_push: byte-count mismatch after streaming.",
                "LOCAL: {}".format(local),
                "REMOTE: {}".format(remote_posix),
                "EXPECTED: {}".format(source_size),
                "OBSERVED: {}".format(bytes_sent),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg)

        return

    def file_pull(self, remote: str, local: str, aspects: UefiCmdAspects) -> None:
        """
            Download ``remote`` to ``local`` by base64-encoding the remote
            file through ``cat | base64``.

            :param remote: UEFI or POSIX source path.
            :param local: Local destination path.
            :param aspects: Resolved :class:`UefiCmdAspects`.

            :raises: UefiError
        """
        remote_posix = _to_host_posix_path(path=remote, esp_mount=self._esp_mount)

        command = "base64 '{}'".format(remote_posix)
        status, stdout, stderr = self._agent.run_cmd(command=command, aspects=aspects)
        self._raise_on_eacces(
            command=command,
            status=status,
            stdout=stdout,
            stderr=stderr,
            path=remote_posix,
        )

        if status != 0:
            err_msg_lines = [
                "HostOsEspBackend.file_pull: remote base64 read failed.",
                "COMMAND: {}".format(command),
                "STATUS: {}".format(status),
                "REMOTE: {}".format(remote_posix),
                "STDERR: {}".format(stderr),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg)

        payload_text = stdout if stdout is not None else ""
        # Strip whitespace and line breaks introduced by the base64 utility.
        compact = "".join(payload_text.split())
        try:
            decoded = base64.b64decode(compact, validate=False)
        except (ValueError, TypeError) as berr:
            err_msg_lines = [
                "HostOsEspBackend.file_pull: base64 decode failed.",
                "REMOTE: {}".format(remote_posix),
                "ERROR: {!r}".format(berr),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg) from berr

        with open(local, "wb") as stream:
            stream.write(decoded)
        return

    def file_exists(self, path: str, aspects: UefiCmdAspects) -> bool:
        """
            Return ``True`` when ``path`` exists and is a regular file.

            :param path: UEFI or POSIX path.
            :param aspects: Resolved :class:`UefiCmdAspects`.

            :returns: bool
        """
        remote_posix = _to_host_posix_path(path=path, esp_mount=self._esp_mount)
        command = "test -f '{}'".format(remote_posix)
        status, _, _ = self._agent.run_cmd(command=command, aspects=aspects)
        rtnval = status == 0
        return rtnval

    def directory_exists(self, path: str, aspects: UefiCmdAspects) -> bool:
        """
            Return ``True`` when ``path`` exists and is a directory.

            :param path: UEFI or POSIX path.
            :param aspects: Resolved :class:`UefiCmdAspects`.

            :returns: bool
        """
        remote_posix = _to_host_posix_path(path=path, esp_mount=self._esp_mount)
        command = "test -d '{}'".format(remote_posix)
        status, _, _ = self._agent.run_cmd(command=command, aspects=aspects)
        rtnval = status == 0
        return rtnval

    def directory(self, path: str, aspects: UefiCmdAspects) -> Dict[str, EspDirEntry]:
        """
            List immediate children of ``path``.

            :param path: UEFI or POSIX path.
            :param aspects: Resolved :class:`UefiCmdAspects`.

            :returns: Mapping of basename to :class:`EspDirEntry`.
            :raises: UefiError
        """
        remote_posix = _to_host_posix_path(path=path, esp_mount=self._esp_mount)
        command = "ls -lA --time-style=long-iso '{}'".format(remote_posix)
        status, stdout, stderr = self._agent.run_cmd(command=command, aspects=aspects)
        self._raise_on_eacces(
            command=command,
            status=status,
            stdout=stdout,
            stderr=stderr,
            path=remote_posix,
        )

        if status != 0:
            err_msg_lines = [
                "HostOsEspBackend.directory: 'ls' failed.",
                "COMMAND: {}".format(command),
                "STATUS: {}".format(status),
                "PATH: {}".format(remote_posix),
                "STDERR: {}".format(stderr),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg)

        entries = self._parse_posix_ls(output=stdout if stdout is not None else "")
        rtnval: Dict[str, EspDirEntry] = {}
        for entry in entries:
            rtnval[entry.name] = entry
        return rtnval

    def directory_tree(self, path: str, depth: int, aspects: UefiCmdAspects) -> Dict[str, Any]:
        """
            Recursively enumerate ``path`` to ``depth`` levels.

            :param path: UEFI or POSIX path.
            :param depth: Maximum recursion depth.
            :param aspects: Resolved :class:`UefiCmdAspects`.

            :returns: Nested dict.
            :raises: UefiError
        """
        entries = self.directory(path=path, aspects=aspects)
        rtnval: Dict[str, Any] = {
            "path": path,
            "entries": entries,
            "children": {},
        }

        if depth > 0:
            for name, entry in entries.items():
                if entry.is_directory:
                    child_path = path.rstrip("/\\") + "/" + name
                    subtree = self.directory_tree(
                        path=child_path,
                        depth=depth - 1,
                        aspects=aspects,
                    )
                    rtnval["children"][name] = subtree

        return rtnval

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _parse_posix_ls(self, output: str) -> List[EspDirEntry]:
        """
            Parse POSIX ``ls -lA --time-style=long-iso`` output into a list
            of :class:`EspDirEntry`.

            :param output: Raw stdout from ``ls``.

            :returns: List of :class:`EspDirEntry`.
        """
        rtnval: List[EspDirEntry] = []

        for raw_line in output.splitlines():
            line = raw_line.strip()
            if len(line) == 0:
                continue
            if line.lower().startswith("total "):
                continue

            tokens = line.split(None, 7)
            if len(tokens) < 8:
                continue

            mode = tokens[0]
            is_directory = mode.startswith("d")

            try:
                size = int(tokens[4])
            except ValueError:
                size = 0

            date_token = tokens[5]
            time_token = tokens[6]
            name = tokens[7]

            if name == "." or name == "..":
                continue

            entry = EspDirEntry(
                name=name,
                size=size,
                is_directory=is_directory,
                modified="{} {}".format(date_token, time_token),
            )
            rtnval.append(entry)

        return rtnval

    def _raise_on_eacces(self, command: str,
                               status: Optional[int],
                               stdout: Optional[str],
                               stderr: Optional[str],
                               path: str) -> None:
        """
            Translate an EACCES / permission-denied indication into a
            :class:`UefiError`.

            :param command: Issued shell command.
            :param status: Reported shell status.
            :param stdout: Captured stdout.
            :param stderr: Captured stderr.
            :param path: Attempted path (for diagnostics).

            :raises: UefiError
        """
        haystack_lines: List[str] = []
        if stdout is not None:
            haystack_lines.append(stdout)
        if stderr is not None:
            haystack_lines.append(stderr)

        haystack = " ".join(haystack_lines).lower()
        has_denied = "permission denied" in haystack or "eacces" in haystack

        if has_denied:
            err_msg_lines = [
                "HostOsEspBackend: host reported permission denied (EACCES).",
                "COMMAND: {}".format(command),
                "PATH: {}".format(path),
                "STATUS: {}".format(status),
                "STDOUT: {}".format(stdout),
                "STDERR: {}".format(stderr),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg)

        return


# ---------------------------------------------------------------------------
# Redfish backend (stub — VirtualMedia upload is out of scope for v1)
# ---------------------------------------------------------------------------


class _RedfishEspBackend(_AbstractEspBackend):
    """
        Redfish backend stub.  The VirtualMedia upload flow is complex and
        is scoped to a future release; every method here raises
        :class:`UefiError` with a clear message so that callers who
        accidentally route a file op through Redfish receive an
        actionable diagnostic.
    """

    def file_push(self, local: str, remote: str, aspects: UefiCmdAspects) -> None:
        """Not implemented; raises :class:`UefiError`."""
        self._raise_unsupported(operation="file_push")
        return

    def file_pull(self, remote: str, local: str, aspects: UefiCmdAspects) -> None:
        """Not implemented; raises :class:`UefiError`."""
        self._raise_unsupported(operation="file_pull")
        return

    def file_exists(self, path: str, aspects: UefiCmdAspects) -> bool:
        """Not implemented; raises :class:`UefiError`."""
        self._raise_unsupported(operation="file_exists")
        rtnval = False
        return rtnval

    def directory_exists(self, path: str, aspects: UefiCmdAspects) -> bool:
        """Not implemented; raises :class:`UefiError`."""
        self._raise_unsupported(operation="directory_exists")
        rtnval = False
        return rtnval

    def directory(self, path: str, aspects: UefiCmdAspects) -> Dict[str, EspDirEntry]:
        """Not implemented; raises :class:`UefiError`."""
        self._raise_unsupported(operation="directory")
        rtnval: Dict[str, EspDirEntry] = {}
        return rtnval

    def directory_tree(self, path: str, depth: int, aspects: UefiCmdAspects) -> Dict[str, Any]:
        """Not implemented; raises :class:`UefiError`."""
        self._raise_unsupported(operation="directory_tree")
        rtnval: Dict[str, Any] = {}
        return rtnval

    def _raise_unsupported(self, operation: str) -> None:
        """
            Raise a uniform "Redfish ESP file operations not supported"
            error.

            :param operation: Name of the operation that was requested.

            :raises: UefiError
        """
        err_msg_lines = [
            "Redfish ESP file operations not supported.",
            "OPERATION: {}".format(operation),
            "HINT: route ESP file ops through the 'serial', 'ipmi-sol',",
            "      or 'ssh-host' transport; Redfish VirtualMedia support",
            "      is scoped to a future release.",
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiError(err_msg)


# ---------------------------------------------------------------------------
# Public facade
# ---------------------------------------------------------------------------


class EspFileProvider:
    """
        Transport-agnostic EFI System Partition file operations facade.

        Dispatches to a concrete backend based on ``agent.transport_kind``:

        - ``serial`` / ``ipmi-sol``: UEFI Shell ``cp``/``ls``/``type``.
        - ``ssh-host``: OS-mounted ESP path ``/boot/efi`` (override via
          ``esp_mount`` arg).
        - ``redfish``: VirtualMedia upload + SFTP-like subresource
          (some BMCs support ESP mount via VirtualMedia).

        Path translation:
            - Shell: UEFI-style paths (``FS0:`` volume alias, ``\\``
              separators).
            - SSH-host: POSIX paths rooted at ``esp_mount``.

        :param agent: The constructed :class:`UefiAgent` that owns the
                      active transport.
        :param esp_mount: Host-OS mount point for the SSH-host backend.
                          Ignored for the other backends.
        :param volume_alias: Default UEFI volume alias applied by the
                             shell backend when a caller-supplied path
                             does not already include one.
        :param chunk_size: Bytes per streamed chunk; must be in
                           ``[MIN_CHUNK_SIZE, MAX_CHUNK_SIZE]``.
        :param aspects: Instance default :class:`UefiCmdAspects` used when
                        a per-call ``aspects`` argument is ``None``.

        :raises: ConfigurationError
    """

    def __init__(self, agent: Any,
                       esp_mount: str = DEFAULT_ESP_MOUNT,
                       volume_alias: str = DEFAULT_VOLUME_ALIAS,
                       chunk_size: int = DEFAULT_CHUNK_SIZE,
                       aspects: UefiCmdAspects = DEFAULT_CMD_ASPECTS) -> None:
        """
            Initialize the facade and dispatch to the correct backend.

            :param agent: The :class:`UefiAgent` hosting the transport.
            :param esp_mount: Host OS ESP mount point.
            :param volume_alias: UEFI volume alias.
            :param chunk_size: Streaming chunk size.
            :param aspects: Instance default aspects.

            :raises: ConfigurationError
        """
        if agent is None:
            err_msg_lines = [
                "EspFileProvider: 'agent' must not be None.",
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg) from None

        if not hasattr(agent, "run_cmd"):
            err_msg_lines = [
                "EspFileProvider: 'agent' does not expose a 'run_cmd' method.",
                "GOT: type={}".format(type(agent).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg) from None

        transport_kind = getattr(agent, "transport_kind", None)
        if transport_kind is None:
            # Fall back to the underscore-prefixed private attribute used on
            # the base class implementation.
            transport_kind = getattr(agent, "_transport_kind", None)

        if transport_kind is None:
            err_msg_lines = [
                "EspFileProvider: 'agent' does not expose a 'transport_kind' attribute.",
                "GOT: type={}".format(type(agent).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg) from None

        if not isinstance(esp_mount, str) or len(esp_mount) == 0:
            err_msg_lines = [
                "EspFileProvider: 'esp_mount' must be a non-empty str.",
                "GOT: value={!r} type={}".format(esp_mount, type(esp_mount).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg) from None

        if not isinstance(volume_alias, str) or len(volume_alias) == 0:
            err_msg_lines = [
                "EspFileProvider: 'volume_alias' must be a non-empty str.",
                "GOT: value={!r} type={}".format(volume_alias, type(volume_alias).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg) from None

        validated_chunk_size = _validate_chunk_size(chunk_size=chunk_size)

        self._agent: Any = agent
        self._transport_kind: str = transport_kind
        self._esp_mount: str = esp_mount
        self._volume_alias: str = volume_alias
        self._chunk_size: int = validated_chunk_size
        self._aspects: UefiCmdAspects = aspects
        self._backend: _AbstractEspBackend = self._resolve_backend(
            transport_kind=transport_kind,
        )
        return

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def file_push(self, local: str,
                        remote: str,
                        aspects: Optional[UefiCmdAspects] = None) -> None:
        """
            Upload a local file to the ESP at ``remote``.  Large files
            stream in ``self._chunk_size`` chunks.

            :param local: Absolute local source path.
            :param remote: UEFI or POSIX destination path.
            :param aspects: Optional per-call :class:`UefiCmdAspects`;
                            falls back to the instance default.

            :raises: UefiError, ConfigurationError
        """
        self._require_local_exists(local=local)
        effective = resolve_aspects(passed=aspects, instance_default=self._aspects)

        self._backend.file_push(local=local, remote=remote, aspects=effective)
        return

    def file_pull(self, remote: str,
                        local: str,
                        aspects: Optional[UefiCmdAspects] = None) -> None:
        """
            Download ``remote`` from the ESP to ``local``.

            :param remote: UEFI or POSIX source path.
            :param local: Absolute local destination path.
            :param aspects: Optional per-call aspects override.

            :raises: UefiError, ConfigurationError
        """
        effective = resolve_aspects(passed=aspects, instance_default=self._aspects)

        self._backend.file_pull(remote=remote, local=local, aspects=effective)
        return

    def file_exists(self, path: str,
                          aspects: Optional[UefiCmdAspects] = None) -> bool:
        """
            Return ``True`` when ``path`` exists and is a regular file.

            :param path: UEFI or POSIX path.
            :param aspects: Optional per-call aspects override.

            :returns: bool
            :raises: UefiError, ConfigurationError
        """
        effective = resolve_aspects(passed=aspects, instance_default=self._aspects)

        rtnval = self._backend.file_exists(path=path, aspects=effective)
        return rtnval

    def directory_exists(self, path: str,
                               aspects: Optional[UefiCmdAspects] = None) -> bool:
        """
            Return ``True`` when ``path`` exists and is a directory.

            :param path: UEFI or POSIX path.
            :param aspects: Optional per-call aspects override.

            :returns: bool
            :raises: UefiError, ConfigurationError
        """
        effective = resolve_aspects(passed=aspects, instance_default=self._aspects)

        rtnval = self._backend.directory_exists(path=path, aspects=effective)
        return rtnval

    def directory(self, path: str,
                        aspects: Optional[UefiCmdAspects] = None) -> Dict[str, EspDirEntry]:
        """
            Non-recursive listing of ``path``.

            :param path: UEFI or POSIX directory path.
            :param aspects: Optional per-call aspects override.

            :returns: Mapping of basename to :class:`EspDirEntry`.
            :raises: UefiError, ConfigurationError
        """
        effective = resolve_aspects(passed=aspects, instance_default=self._aspects)

        rtnval = self._backend.directory(path=path, aspects=effective)
        return rtnval

    def directory_tree(self, path: str,
                             depth: int = 1,
                             aspects: Optional[UefiCmdAspects] = None) -> Dict[str, Any]:
        """
            Nested dict rendering of the tree down to ``depth`` levels.

            :param path: UEFI or POSIX directory path.
            :param depth: Maximum recursion depth.
            :param aspects: Optional per-call aspects override.

            :returns: Nested dict with ``path``, ``entries`` and
                      ``children`` keys.
            :raises: UefiError, ConfigurationError
        """
        if not isinstance(depth, int) or isinstance(depth, bool):
            err_msg_lines = [
                "EspFileProvider.directory_tree: 'depth' must be an int.",
                "GOT: value={!r} type={}".format(depth, type(depth).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg) from None

        if depth < 0:
            err_msg_lines = [
                "EspFileProvider.directory_tree: 'depth' must be >= 0.",
                "GOT: value={}".format(depth),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg) from None

        effective = resolve_aspects(passed=aspects, instance_default=self._aspects)

        rtnval = self._backend.directory_tree(
            path=path,
            depth=depth,
            aspects=effective,
        )
        return rtnval

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_backend(self, transport_kind: str) -> _AbstractEspBackend:
        """
            Select the concrete backend implementation matching
            ``transport_kind``.

            :param transport_kind: One of ``'serial'``, ``'ipmi-sol'``,
                                   ``'ssh-host'``, ``'redfish'``.

            :returns: A backend instance derived from :class:`_AbstractEspBackend`.
            :raises: ConfigurationError
        """
        if transport_kind == "serial" or transport_kind == "ipmi-sol":
            rtnval: _AbstractEspBackend = _ShellEspBackend(
                agent=self._agent,
                esp_mount=self._esp_mount,
                volume_alias=self._volume_alias,
                chunk_size=self._chunk_size,
            )
            return rtnval

        if transport_kind == "ssh-host":
            rtnval = _HostOsEspBackend(
                agent=self._agent,
                esp_mount=self._esp_mount,
                volume_alias=self._volume_alias,
                chunk_size=self._chunk_size,
            )
            return rtnval

        if transport_kind == "redfish":
            rtnval = _RedfishEspBackend(
                agent=self._agent,
                esp_mount=self._esp_mount,
                volume_alias=self._volume_alias,
                chunk_size=self._chunk_size,
            )
            return rtnval

        err_msg_lines = [
            "EspFileProvider: unknown 'transport_kind'.",
            "GOT: value={!r}".format(transport_kind),
            "ALLOWED: serial, ipmi-sol, ssh-host, redfish",
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise ConfigurationError(err_msg) from None

    def _require_local_exists(self, local: str) -> None:
        """
            Validate that ``local`` exists on the harness before push.

            :param local: Local file path.

            :raises: UefiError, ConfigurationError
        """
        if not isinstance(local, str) or len(local) == 0:
            err_msg_lines = [
                "EspFileProvider.file_push: 'local' must be a non-empty str.",
                "GOT: value={!r} type={}".format(local, type(local).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise ConfigurationError(err_msg) from None

        if not os.path.exists(local):
            err_msg_lines = [
                "EspFileProvider.file_push: local source file does not exist.",
                "LOCAL: {}".format(local),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg)

        if not os.path.isfile(local):
            err_msg_lines = [
                "EspFileProvider.file_push: local source path is not a file.",
                "LOCAL: {}".format(local),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg)

        return


__all__ = [
    "DEFAULT_CHUNK_SIZE",
    "DEFAULT_ESP_MOUNT",
    "DEFAULT_VOLUME_ALIAS",
    "EspDirEntry",
    "EspFileProvider",
    "LARGE_FILE_THRESHOLD",
    "MAX_CHUNK_SIZE",
    "MIN_CHUNK_SIZE",
]
