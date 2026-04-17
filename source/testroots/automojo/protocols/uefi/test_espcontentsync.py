"""
    .. module:: test_espcontentsync
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Unit tests for the :mod:`automojo.protocols.uefi.espcontentsync`
               module.  Exercises the :class:`EspContentSync` workflow
               through a :class:`_FakeEspFileProvider` stand-in whose
               ``file_push`` / ``file_pull`` / ``file_exists`` /
               ``directory_tree`` / ``file_delete`` surface is backed by an
               in-memory dict so tests never touch a real serial port, BMC,
               or SSH session.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Automation Mojo LLC"
__credits__ = []


import hashlib
import os
import unittest

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from automojo.protocols.uefi.uefiaspects import (
    DEFAULT_CMD_ASPECTS,
    UefiCmdAspects,
)
from automojo.protocols.uefi.uefiespfiles import EspDirEntry
from automojo.protocols.uefi.uefiexceptions import UefiError

from automojo.protocols.uefi.espcontentsync import (
    EspContentSync,
    SyncStats,
    _flatten_remote_tree,
    _matches_any,
    _normalize_relative_posix,
    _sha256_of_bytes,
)


# ---------------------------------------------------------------------------
# Fake EspFileProvider
# ---------------------------------------------------------------------------


class _FakeEspFileProvider:
    """
        In-memory stand-in for :class:`EspFileProvider`.  Tracks a dict of
        POSIX-relative path (relative to the configured remote root) to
        raw ``bytes`` payload and exposes the subset of the
        :class:`EspFileProvider` surface needed by :class:`EspContentSync`.

        Every ``file_push`` / ``file_pull`` / ``file_delete`` invocation is
        recorded so tests can assert call order and call count.
    """

    def __init__(self, remote_root: str,
                       initial_files: Optional[Dict[str, bytes]] = None) -> None:
        """
            Seed the fake with an optional initial state.

            :param remote_root: The POSIX remote root all stored paths are
                                rooted at (matches what the workflow will
                                pass).
            :param initial_files: Mapping of POSIX-relative path ->
                                  content bytes.
        """
        self._remote_root: str = remote_root
        if initial_files is None:
            self._files: Dict[str, bytes] = {}
        else:
            self._files = dict(initial_files)

        self.push_calls: List[Tuple[str, str]] = []
        self.pull_calls: List[str] = []
        self.delete_calls: List[str] = []
        self.exists_calls: List[str] = []
        self.directory_tree_calls: List[Tuple[str, int]] = []
        return

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _to_relative(self, path: str) -> str:
        """
            Strip ``self._remote_root`` off a caller-supplied path and
            return the POSIX-relative portion.
        """
        working = path.replace("\\", "/")
        prefix = self._remote_root.replace("\\", "/").rstrip("/")

        if len(prefix) > 0 and working.startswith(prefix):
            remainder = working[len(prefix):]
        else:
            remainder = working

        while remainder.startswith("/"):
            remainder = remainder[1:]
        rtnval = remainder
        return rtnval

    # ------------------------------------------------------------------
    # EspFileProvider-shaped API
    # ------------------------------------------------------------------

    def file_push(self, local: str,
                        remote: str,
                        aspects: Optional[UefiCmdAspects] = None) -> None:
        """
            Read ``local`` off the filesystem and store its bytes under the
            relative path derived from ``remote``.
        """
        self.push_calls.append((local, remote))
        with open(local, "rb") as stream:
            payload = stream.read()

        rel = self._to_relative(path=remote)
        self._files[rel] = payload
        return

    def file_pull(self, remote: str,
                        local: str,
                        aspects: Optional[UefiCmdAspects] = None) -> None:
        """
            Write the stored bytes for ``remote`` into ``local``.
        """
        self.pull_calls.append(remote)
        rel = self._to_relative(path=remote)
        if rel not in self._files:
            err_msg_lines = [
                "_FakeEspFileProvider.file_pull: file not in fake store.",
                "REMOTE: {}".format(remote),
                "REL: {}".format(rel),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg)

        with open(local, "wb") as stream:
            stream.write(self._files[rel])
        return

    def file_exists(self, path: str,
                          aspects: Optional[UefiCmdAspects] = None) -> bool:
        """
            Return True when ``path`` is registered in the fake store.
        """
        self.exists_calls.append(path)
        rel = self._to_relative(path=path)
        rtnval = rel in self._files
        return rtnval

    def file_delete(self, path: str,
                          aspects: Optional[UefiCmdAspects] = None) -> None:
        """
            Remove ``path`` from the store.  Unknown paths are tolerated.
        """
        self.delete_calls.append(path)
        rel = self._to_relative(path=path)
        if rel in self._files:
            del self._files[rel]
        return

    def directory_tree(self, path: str,
                             depth: int = 1,
                             aspects: Optional[UefiCmdAspects] = None) -> Dict[str, Any]:
        """
            Produce an :meth:`EspFileProvider.directory_tree`-shaped dict
            for the subtree under ``path``.
        """
        self.directory_tree_calls.append((path, depth))

        rel_root = self._to_relative(path=path)
        # Collect every file whose relative path begins at rel_root (or
        # the whole store when rel_root is empty / equals "").
        relevant: List[str] = []
        for stored_rel in self._files.keys():
            if len(rel_root) == 0:
                relevant.append(stored_rel)
                continue
            if stored_rel == rel_root:
                relevant.append(stored_rel)
                continue
            if stored_rel.startswith(rel_root + "/"):
                relevant.append(stored_rel[len(rel_root) + 1:])

        # Build a nested-dict tree where leaves are file entries and
        # branches are directory entries.
        rtnval = self._build_tree(path=path, relative_entries=relevant)
        return rtnval

    def _build_tree(self, path: str,
                          relative_entries: List[str]) -> Dict[str, Any]:
        """
            Build a nested directory-tree dict from a flat list of
            forward-slash relative paths.
        """
        entries: Dict[str, EspDirEntry] = {}
        children_map: Dict[str, List[str]] = {}

        for rel in relative_entries:
            if "/" in rel:
                head, tail = rel.split("/", 1)
                if head not in entries:
                    entries[head] = EspDirEntry(
                        name=head, size=0, is_directory=True,
                    )
                if head not in children_map:
                    children_map[head] = []
                children_map[head].append(tail)
            else:
                # File at this level.
                file_size = 0
                # Size may be computed if desired but it is not required
                # for the sync workflow's correctness.
                entries[rel] = EspDirEntry(
                    name=rel, size=file_size, is_directory=False,
                )

        children: Dict[str, Any] = {}
        for dir_name, sub_entries in children_map.items():
            child_path = path.rstrip("/") + "/" + dir_name
            subtree = self._build_tree(
                path=child_path, relative_entries=sub_entries,
            )
            children[dir_name] = subtree

        rtnval: Dict[str, Any] = {
            "path": path,
            "entries": entries,
            "children": children,
        }
        return rtnval

    # ------------------------------------------------------------------
    # Test introspection
    # ------------------------------------------------------------------

    @property
    def stored_files(self) -> Dict[str, bytes]:
        """
            Expose the backing dict for test assertions.
        """
        return self._files


# ---------------------------------------------------------------------------
# Helper-function tests
# ---------------------------------------------------------------------------


class TestHelperFunctions(unittest.TestCase):
    """
        Exercises the module-level helpers (``_sha256_of_bytes``,
        ``_matches_any``, ``_normalize_relative_posix``,
        ``_flatten_remote_tree``).
    """

    def test_sha256_of_bytes_stable(self) -> None:
        """Hash digests are stable across calls."""
        digest_a = _sha256_of_bytes(data=b"hello")
        digest_b = _sha256_of_bytes(data=b"hello")
        self.assertEqual(digest_a, digest_b)
        expected = hashlib.sha256(b"hello").hexdigest()
        self.assertEqual(digest_a, expected)
        return

    def test_matches_any_true(self) -> None:
        """A glob match returns True."""
        rtnval = _matches_any(
            rel_posix="foo/bar.efi",
            patterns=["*.efi", "*.txt"],
        )
        self.assertTrue(rtnval)
        return

    def test_matches_any_false(self) -> None:
        """No glob match returns False."""
        rtnval = _matches_any(
            rel_posix="foo/bar.bin",
            patterns=["*.efi", "*.txt"],
        )
        self.assertFalse(rtnval)
        return

    def test_matches_any_empty(self) -> None:
        """An empty pattern list returns False."""
        rtnval = _matches_any(rel_posix="foo", patterns=[])
        self.assertFalse(rtnval)
        return

    def test_normalize_relative_posix_flips_backslashes(self) -> None:
        """Back-slashes are flipped and leading/trailing separators removed."""
        rtnval = _normalize_relative_posix(rel_path="\\EFI\\BOOT\\foo.efi")
        self.assertEqual(rtnval, "EFI/BOOT/foo.efi")
        return

    def test_normalize_relative_posix_none_raises(self) -> None:
        """A ``None`` value raises :class:`UefiError`."""
        with self.assertRaises(UefiError):
            _normalize_relative_posix(rel_path=None)
        return

    def test_flatten_remote_tree_empty(self) -> None:
        """An empty tree yields an empty accumulator."""
        acc: List[str] = []
        _flatten_remote_tree(tree=None, rel_prefix="", accumulator=acc)
        self.assertEqual(acc, [])
        return

    def test_flatten_remote_tree_nested(self) -> None:
        """Nested children are reported as joined relative paths."""
        tree = {
            "path": "/remote",
            "entries": {
                "sub": EspDirEntry(name="sub", size=0, is_directory=True),
                "top.txt": EspDirEntry(name="top.txt", size=1, is_directory=False),
            },
            "children": {
                "sub": {
                    "path": "/remote/sub",
                    "entries": {
                        "leaf.bin": EspDirEntry(
                            name="leaf.bin", size=2, is_directory=False,
                        ),
                    },
                    "children": {},
                },
            },
        }
        acc: List[str] = []
        _flatten_remote_tree(tree=tree, rel_prefix="", accumulator=acc)
        self.assertIn("top.txt", acc)
        self.assertIn("sub/leaf.bin", acc)
        self.assertEqual(len(acc), 2)
        return


# ---------------------------------------------------------------------------
# EspContentSync behavior tests
# ---------------------------------------------------------------------------


class TestEspContentSync(unittest.TestCase):
    """
        End-to-end tests of the :meth:`EspContentSync.sync` workflow using
        the in-memory :class:`_FakeEspFileProvider`.
    """

    def _write_local_file(self, root: Path, rel: str, content: bytes) -> Path:
        """
            Write a local file and return its absolute path.
        """
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "wb") as stream:
            stream.write(content)

        rtnval = target
        return rtnval

    def _make_tmp_root(self) -> Path:
        """
            Build a temporary directory rooted at ``tempfile.mkdtemp`` and
            register it for teardown.
        """
        import tempfile
        root = Path(tempfile.mkdtemp(prefix="espsync-test-"))
        self.addCleanup(self._rmtree, root=root)

        rtnval = root
        return rtnval

    def _rmtree(self, root: Path) -> None:
        """
            Remove a directory tree safely during teardown.
        """
        import shutil
        try:
            shutil.rmtree(root)
        except OSError:
            pass
        return

    # ------------------------------------------------------------------
    # No-op on matching SHA-256
    # ------------------------------------------------------------------

    def test_noop_on_matching_sha256(self) -> None:
        """Files with equal local and remote SHA-256 are skipped, not pushed."""
        local_root = self._make_tmp_root()
        content = b"hello-efi-world"
        self._write_local_file(root=local_root, rel="EFI/vendor/loader.efi",
                               content=content)

        remote_root = "/EFI/vendor-remote"
        fake = _FakeEspFileProvider(
            remote_root=remote_root,
            initial_files={"EFI/vendor/loader.efi": content},
        )

        workflow = EspContentSync(esp=fake, aspects=DEFAULT_CMD_ASPECTS)
        stats = workflow.sync(
            local_root=str(local_root),
            remote_root=remote_root,
        )

        self.assertIsInstance(stats, SyncStats)
        self.assertEqual(stats.pushed, 0)
        self.assertEqual(stats.skipped, 1)
        self.assertEqual(stats.deleted, 0)
        self.assertEqual(stats.total_bytes, 0)
        self.assertEqual(len(fake.push_calls), 0)
        self.assertEqual(len(fake.pull_calls), 1)
        return

    # ------------------------------------------------------------------
    # Push on content mismatch
    # ------------------------------------------------------------------

    def test_push_on_content_mismatch(self) -> None:
        """Different local and remote bytes trigger a push."""
        local_root = self._make_tmp_root()
        self._write_local_file(
            root=local_root, rel="EFI/vendor/loader.efi",
            content=b"LOCAL-VERSION-2",
        )

        remote_root = "/EFI/vendor"
        fake = _FakeEspFileProvider(
            remote_root=remote_root,
            initial_files={"EFI/vendor/loader.efi": b"OLD-VERSION-1"},
        )

        workflow = EspContentSync(esp=fake, aspects=DEFAULT_CMD_ASPECTS)
        stats = workflow.sync(
            local_root=str(local_root),
            remote_root=remote_root,
        )

        self.assertEqual(stats.pushed, 1)
        self.assertEqual(stats.skipped, 0)
        self.assertEqual(stats.deleted, 0)
        self.assertEqual(stats.total_bytes, len(b"LOCAL-VERSION-2"))
        self.assertEqual(len(fake.push_calls), 1)

        stored = fake.stored_files["EFI/vendor/loader.efi"]
        self.assertEqual(stored, b"LOCAL-VERSION-2")
        return

    # ------------------------------------------------------------------
    # Missing remote => push
    # ------------------------------------------------------------------

    def test_push_when_remote_missing(self) -> None:
        """A local file with no remote counterpart is pushed."""
        local_root = self._make_tmp_root()
        self._write_local_file(
            root=local_root, rel="EFI/vendor/new.efi",
            content=b"NEW-BINARY",
        )

        remote_root = "/EFI/vendor"
        fake = _FakeEspFileProvider(
            remote_root=remote_root, initial_files={},
        )

        workflow = EspContentSync(esp=fake)
        stats = workflow.sync(
            local_root=str(local_root),
            remote_root=remote_root,
        )

        self.assertEqual(stats.pushed, 1)
        self.assertEqual(stats.skipped, 0)
        self.assertEqual(stats.total_bytes, len(b"NEW-BINARY"))
        self.assertEqual(len(fake.pull_calls), 0)
        return

    # ------------------------------------------------------------------
    # include filter
    # ------------------------------------------------------------------

    def test_include_filter_restricts_scope(self) -> None:
        """Only files matching an include glob are considered."""
        local_root = self._make_tmp_root()
        self._write_local_file(
            root=local_root, rel="a.efi", content=b"EFI",
        )
        self._write_local_file(
            root=local_root, rel="b.txt", content=b"TXT",
        )

        remote_root = "/EFI/vendor"
        fake = _FakeEspFileProvider(
            remote_root=remote_root, initial_files={},
        )

        workflow = EspContentSync(esp=fake)
        stats = workflow.sync(
            local_root=str(local_root),
            remote_root=remote_root,
            include=["*.efi"],
        )

        self.assertEqual(stats.pushed, 1)
        self.assertIn("a.efi", fake.stored_files)
        self.assertNotIn("b.txt", fake.stored_files)
        return

    # ------------------------------------------------------------------
    # exclude filter (wins over include)
    # ------------------------------------------------------------------

    def test_exclude_wins_over_include(self) -> None:
        """A file matching both include and exclude is excluded."""
        local_root = self._make_tmp_root()
        self._write_local_file(
            root=local_root, rel="keep.efi", content=b"KEEP",
        )
        self._write_local_file(
            root=local_root, rel="skip.efi", content=b"SKIP",
        )

        remote_root = "/EFI/vendor"
        fake = _FakeEspFileProvider(
            remote_root=remote_root, initial_files={},
        )

        workflow = EspContentSync(esp=fake)
        stats = workflow.sync(
            local_root=str(local_root),
            remote_root=remote_root,
            include=["*.efi"],
            exclude=["skip.*"],
        )

        self.assertEqual(stats.pushed, 1)
        self.assertIn("keep.efi", fake.stored_files)
        self.assertNotIn("skip.efi", fake.stored_files)
        return

    # ------------------------------------------------------------------
    # delete mode removes orphans
    # ------------------------------------------------------------------

    def test_delete_mode_removes_orphans(self) -> None:
        """Remote files missing locally are deleted when ``delete=True``."""
        local_root = self._make_tmp_root()
        self._write_local_file(
            root=local_root, rel="kept.efi", content=b"KEPT",
        )

        remote_root = "/EFI/vendor"
        fake = _FakeEspFileProvider(
            remote_root=remote_root,
            initial_files={
                "kept.efi": b"KEPT",
                "orphan.efi": b"ORPHAN",
                "nested/orphan2.bin": b"O2",
            },
        )

        workflow = EspContentSync(esp=fake)
        stats = workflow.sync(
            local_root=str(local_root),
            remote_root=remote_root,
            delete=True,
        )

        self.assertEqual(stats.pushed, 0)
        self.assertEqual(stats.skipped, 1)
        self.assertEqual(stats.deleted, 2)
        self.assertIn("kept.efi", fake.stored_files)
        self.assertNotIn("orphan.efi", fake.stored_files)
        self.assertNotIn("nested/orphan2.bin", fake.stored_files)
        return

    # ------------------------------------------------------------------
    # default (delete=False) does NOT delete
    # ------------------------------------------------------------------

    def test_default_delete_is_false(self) -> None:
        """Orphans remain intact when the caller omits ``delete``."""
        local_root = self._make_tmp_root()
        self._write_local_file(
            root=local_root, rel="a.efi", content=b"A",
        )

        remote_root = "/EFI/vendor"
        fake = _FakeEspFileProvider(
            remote_root=remote_root,
            initial_files={
                "a.efi": b"A",
                "leftover.efi": b"OLD",
            },
        )

        workflow = EspContentSync(esp=fake)
        stats = workflow.sync(
            local_root=str(local_root),
            remote_root=remote_root,
            include=["*.efi"],
        )

        self.assertEqual(stats.deleted, 0)
        self.assertIn("leftover.efi", fake.stored_files)
        self.assertEqual(len(fake.delete_calls), 0)
        return

    # ------------------------------------------------------------------
    # include without delete does NOT delete (different axis)
    # ------------------------------------------------------------------

    def test_include_without_delete_does_not_delete(self) -> None:
        """Include filtering alone must not trigger deletion."""
        local_root = self._make_tmp_root()
        self._write_local_file(
            root=local_root, rel="new.efi", content=b"NEW",
        )

        remote_root = "/EFI/vendor"
        fake = _FakeEspFileProvider(
            remote_root=remote_root,
            initial_files={
                "old.efi": b"OLD",
            },
        )

        workflow = EspContentSync(esp=fake)
        stats = workflow.sync(
            local_root=str(local_root),
            remote_root=remote_root,
            include=["*.efi"],
        )

        # delete must default to False even when include is supplied.
        self.assertEqual(stats.deleted, 0)
        self.assertIn("old.efi", fake.stored_files)
        self.assertIn("new.efi", fake.stored_files)
        return

    # ------------------------------------------------------------------
    # Missing local_root
    # ------------------------------------------------------------------

    def test_missing_local_root_raises(self) -> None:
        """A local_root that does not exist raises :class:`UefiError`."""
        remote_root = "/EFI/vendor"
        fake = _FakeEspFileProvider(remote_root=remote_root)
        workflow = EspContentSync(esp=fake)

        with self.assertRaises(UefiError):
            workflow.sync(
                local_root="/non/existent/path/nowhere",
                remote_root=remote_root,
            )
        return

    # ------------------------------------------------------------------
    # Malformed include list
    # ------------------------------------------------------------------

    def test_include_must_be_list_of_str(self) -> None:
        """Non-str include elements raise :class:`UefiError`."""
        local_root = self._make_tmp_root()

        remote_root = "/EFI/vendor"
        fake = _FakeEspFileProvider(remote_root=remote_root)
        workflow = EspContentSync(esp=fake)

        with self.assertRaises(UefiError):
            workflow.sync(
                local_root=str(local_root),
                remote_root=remote_root,
                include=[123],  # type: ignore[list-item]
            )
        return


if __name__ == "__main__":
    unittest.main()
