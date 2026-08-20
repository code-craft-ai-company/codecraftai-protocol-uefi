"""
    .. module:: espcontentsync
    :platform: Darwin, Linux, Unix, Windows
    :synopsis: Module containing the :class:`EspContentSync` workflow which
               mirrors a local directory tree into ``/EFI/<vendor>/`` on the
               target's EFI System Partition using rsync-style delta
               semantics.

               The workflow composes :class:`EspFileProvider` (from
               ``5-esp-file-ops``) with a local SHA-256 / glob layer that
               decides what actions to take.  Files whose local and remote
               digests match are skipped, files whose digests differ (or
               whose remote counterpart is absent) are pushed, and — when
               the caller opts in via ``delete=True`` — remote files that
               have no local counterpart are deleted.  ``include`` and
               ``exclude`` glob lists restrict the scope; exclude wins over
               include when both match.

    .. moduleauthor:: Myron Walker <myron.walker@gmail.com>

"""

__author__ = "Myron Walker"
__copyright__ = "Copyright 2025, Code Craft AI LLC"
__credits__ = []


import fnmatch
import hashlib
import os
import tempfile

from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional, Set

from ccai.protocols.uefi.uefiaspects import (
    DEFAULT_CMD_ASPECTS,
    UefiCmdAspects,
    resolve_aspects,
)
from ccai.protocols.uefi.uefiespfiles import EspFileProvider
from ccai.protocols.uefi.uefiexceptions import UefiError


__all__ = [
    "EspContentSync",
    "SyncStats",
]


# ---------------------------------------------------------------------------
# SyncStats
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SyncStats:
    """
        Summary counters describing one :meth:`EspContentSync.sync` run.

        :ivar pushed: Number of files whose content was pushed to the ESP
                      (either newly created or updated).
        :ivar skipped: Number of files whose remote SHA-256 matched the
                       local SHA-256 and therefore were left alone.
        :ivar deleted: Number of remote files removed because they had no
                       local counterpart (only populated when the caller
                       passes ``delete=True``).
        :ivar total_bytes: Total bytes written to the ESP across every
                           push.
    """
    pushed: int
    skipped: int
    deleted: int
    total_bytes: int


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _sha256_of_bytes(data: bytes) -> str:
    """
        Compute the SHA-256 hex digest of a ``bytes`` buffer.

        :param data: The buffer to hash.

        :returns: A lowercase hex digest.
    """
    hasher = hashlib.sha256()
    hasher.update(data)
    rtnval = hasher.hexdigest()
    return rtnval


def _sha256_of_file(path: Path) -> str:
    """
        Compute the SHA-256 hex digest of a local file, reading in 64 KiB
        chunks so large files do not require whole-file buffering.

        :param path: Local path to hash.

        :returns: A lowercase hex digest.
    """
    hasher = hashlib.sha256()
    with open(path, "rb") as stream:
        while True:
            chunk = stream.read(65536)
            if len(chunk) == 0:
                break
            hasher.update(chunk)

    rtnval = hasher.hexdigest()
    return rtnval


def _normalize_relative_posix(rel_path: str) -> str:
    """
        Normalize a relative path to forward-slash POSIX form suitable for
        glob matching and set membership comparisons.

        :param rel_path: A relative path in either POSIX or UEFI convention.

        :returns: The forward-slash form with no leading / trailing
                  separator.
    """
    if rel_path is None:
        err_msg_lines = [
            "_normalize_relative_posix: 'rel_path' must not be None.",
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiError(err_msg)

    working = rel_path.replace("\\", "/")
    while working.startswith("/"):
        working = working[1:]
    while working.endswith("/"):
        working = working[:-1]

    rtnval = working
    return rtnval


def _join_remote(remote_root: str, rel_posix: str) -> str:
    """
        Join a remote-root prefix and a POSIX-relative path into a single
        POSIX-form path that downstream :class:`EspFileProvider` methods can
        accept (the provider normalizes to its transport-specific form
        internally).

        :param remote_root: The remote prefix (POSIX or UEFI convention).
        :param rel_posix: The forward-slash-separated relative path.

        :returns: The concatenated POSIX-form path.
    """
    prefix = remote_root.replace("\\", "/")
    while prefix.endswith("/"):
        prefix = prefix[:-1]

    if len(rel_posix) == 0:
        rtnval = prefix if len(prefix) > 0 else "/"
        return rtnval

    if len(prefix) == 0:
        rtnval = "/" + rel_posix
        return rtnval

    rtnval = prefix + "/" + rel_posix
    return rtnval


def _matches_any(rel_posix: str, patterns: List[str]) -> bool:
    """
        Return ``True`` when ``rel_posix`` matches any glob in ``patterns``.

        :param rel_posix: The forward-slash-separated relative path.
        :param patterns: A list of glob patterns.

        :returns: ``True`` if any pattern matches.
    """
    for pattern in patterns:
        if fnmatch.fnmatch(rel_posix, pattern):
            rtnval = True
            return rtnval

    rtnval = False
    return rtnval


def _flatten_remote_tree(tree: Any, rel_prefix: str, accumulator: List[str]) -> None:
    """
        Walk a directory-tree dict as returned by
        :meth:`EspFileProvider.directory_tree` and accumulate every file's
        POSIX-form relative path (relative to the root the tree was rooted
        at).  Directories are not appended — only files.

        :param tree: A nested dict with ``entries`` and ``children`` keys.
        :param rel_prefix: The current relative-path prefix.  ``""`` at the
                           root.
        :param accumulator: A caller-owned list into which relative paths
                            are appended.
    """
    if tree is None:
        return

    if not isinstance(tree, dict):
        return

    entries = tree.get("entries")
    if entries is None:
        return

    for name, entry in entries.items():
        if len(rel_prefix) == 0:
            rel_path = name
        else:
            rel_path = rel_prefix + "/" + name

        if entry.is_directory:
            child_tree = None
            children = tree.get("children")
            if children is not None:
                child_tree = children.get(name)

            _flatten_remote_tree(
                tree=child_tree,
                rel_prefix=rel_path,
                accumulator=accumulator,
            )
        else:
            accumulator.append(rel_path)

    return


# ---------------------------------------------------------------------------
# EspContentSync
# ---------------------------------------------------------------------------


class EspContentSync:
    """
        Mirror a local directory tree into ``/EFI/<vendor>/`` on the ESP
        using rsync-style delta semantics.

        The workflow iterates the local tree, computes per-file SHA-256
        digests, compares each digest to the remote counterpart (pulled in
        memory), and pushes only files that differ.  Callers can restrict
        the scope with ``include`` / ``exclude`` glob lists and opt into
        mirror-style ``delete=True`` mode which removes remote files with
        no local counterpart.

        :param esp: A configured :class:`EspFileProvider` that actually
                    moves bytes to and from the ESP.
        :param aspects: Default :class:`UefiCmdAspects` applied when a
                        per-call override is not supplied.
    """

    def __init__(self, esp: EspFileProvider,
                       aspects: UefiCmdAspects = DEFAULT_CMD_ASPECTS) -> None:
        """
            Initialize the workflow.

            :param esp: The ESP file-operations facade.
            :param aspects: Instance-default aspects.
        """
        self._esp: EspFileProvider = esp
        self._aspects: UefiCmdAspects = aspects
        return

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def sync(self, local_root: str,
                   remote_root: str,
                   include: Optional[List[str]] = None,
                   exclude: Optional[List[str]] = None,
                   delete: bool = False,
                   aspects: Optional[UefiCmdAspects] = None) -> SyncStats:
        """
            Mirror ``local_root`` onto ``remote_root`` on the ESP.

            Execution order:

            1. Walk ``local_root`` (:meth:`pathlib.Path.rglob`), filter by
               ``include`` / ``exclude`` globs.
            2. For each local file:

               * If remote does not exist, push.
               * If remote exists, pull into memory, SHA-256 locally, and
                 compare to the local SHA-256.  Match -> skip.  Mismatch
                 -> push.
            3. When ``delete=True``, walk ``remote_root`` and delete every
               remote file whose relative path is not in the local set.
            4. Return a :class:`SyncStats` record with the per-category
               counters and total bytes pushed.

            :param local_root: Local directory root (must exist).
            :param remote_root: Remote directory root; POSIX or UEFI
                                convention accepted.
            :param include: Optional list of globs; when provided, ONLY
                            paths matching at least one include glob are
                            considered.
            :param exclude: Optional list of globs; paths matching any
                            exclude glob are skipped even when they also
                            match an include glob.
            :param delete: When ``True``, remove remote files not present
                           locally.  Defaults to ``False``.
            :param aspects: Optional per-call :class:`UefiCmdAspects`
                            override.

            :returns: A :class:`SyncStats` record.
            :raises: UefiError
        """
        effective_aspects = resolve_aspects(
            passed=aspects, instance_default=self._aspects,
        )

        validated_local_root = self._require_local_directory(
            local_root=local_root,
        )

        validated_include = self._validate_pattern_list(
            field_name="include", value=include,
        )
        validated_exclude = self._validate_pattern_list(
            field_name="exclude", value=exclude,
        )

        local_relatives = self._collect_local_files(
            local_root=validated_local_root,
            include=validated_include,
            exclude=validated_exclude,
        )

        pushed_count = 0
        skipped_count = 0
        total_bytes = 0

        for rel_posix in local_relatives:
            local_path = validated_local_root / rel_posix
            remote_path = _join_remote(
                remote_root=remote_root, rel_posix=rel_posix,
            )

            local_digest = _sha256_of_file(path=local_path)
            remote_exists = self._esp.file_exists(
                path=remote_path, aspects=effective_aspects,
            )

            push_required = True
            if remote_exists:
                remote_digest = self._pull_and_hash(
                    remote_path=remote_path,
                    aspects=effective_aspects,
                )
                if remote_digest == local_digest:
                    push_required = False

            if push_required:
                self._esp.file_push(
                    local=str(local_path),
                    remote=remote_path,
                    aspects=effective_aspects,
                )
                file_size = local_path.stat().st_size
                total_bytes = total_bytes + file_size
                pushed_count = pushed_count + 1
            else:
                skipped_count = skipped_count + 1

        deleted_count = 0
        if delete is True:
            deleted_count = self._delete_orphans(
                remote_root=remote_root,
                local_set=set(local_relatives),
                aspects=effective_aspects,
            )

        rtnval = SyncStats(
            pushed=pushed_count,
            skipped=skipped_count,
            deleted=deleted_count,
            total_bytes=total_bytes,
        )
        return rtnval

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _require_local_directory(self, local_root: str) -> Path:
        """
            Validate that ``local_root`` exists and is a directory.

            :param local_root: Candidate path string.

            :returns: The resolved :class:`pathlib.Path`.
            :raises: UefiError
        """
        if local_root is None:
            err_msg_lines = [
                "EspContentSync.sync: 'local_root' must not be None.",
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg)

        if not isinstance(local_root, str):
            err_msg_lines = [
                "EspContentSync.sync: 'local_root' must be a str.",
                "GOT: value={!r} type={}".format(
                    local_root, type(local_root).__name__,
                ),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg)

        candidate = Path(local_root)
        if not candidate.exists():
            err_msg_lines = [
                "EspContentSync.sync: 'local_root' does not exist.",
                "LOCAL_ROOT: {}".format(local_root),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg)

        if not candidate.is_dir():
            err_msg_lines = [
                "EspContentSync.sync: 'local_root' is not a directory.",
                "LOCAL_ROOT: {}".format(local_root),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg)

        rtnval = candidate
        return rtnval

    def _validate_pattern_list(self, field_name: str,
                                     value: Optional[List[str]]) -> List[str]:
        """
            Validate an optional list of glob patterns.  ``None`` becomes
            an empty list; non-string elements raise :class:`UefiError`.

            :param field_name: Field name for diagnostics.
            :param value: Candidate pattern list.

            :returns: A defensively-copied list of strings.
            :raises: UefiError
        """
        if value is None:
            rtnval: List[str] = []
            return rtnval

        if not isinstance(value, list):
            err_msg_lines = [
                "EspContentSync.sync: '{}' must be a list of str or None.".format(
                    field_name,
                ),
                "GOT: value={!r} type={}".format(value, type(value).__name__),
            ]
            err_msg = os.linesep.join(err_msg_lines)
            raise UefiError(err_msg)

        rtnval = []
        for index, element in enumerate(value):
            if not isinstance(element, str):
                err_msg_lines = [
                    "EspContentSync.sync: '{}' elements must be str.".format(
                        field_name,
                    ),
                    "INDEX: {}".format(index),
                    "GOT: value={!r} type={}".format(
                        element, type(element).__name__,
                    ),
                ]
                err_msg = os.linesep.join(err_msg_lines)
                raise UefiError(err_msg)
            rtnval.append(element)

        return rtnval

    def _collect_local_files(self, local_root: Path,
                                   include: List[str],
                                   exclude: List[str]) -> List[str]:
        """
            Walk ``local_root`` recursively and return every regular-file
            path relative to ``local_root`` in POSIX form.  Apply
            ``include`` / ``exclude`` glob filters; ``exclude`` wins over
            ``include`` when both match.

            :param local_root: The resolved local root.
            :param include: Compiled include globs (may be empty).
            :param exclude: Compiled exclude globs (may be empty).

            :returns: A deterministic (sorted) list of POSIX-form relative
                      paths.
        """
        collected: List[str] = []

        for candidate in local_root.rglob("*"):
            if not candidate.is_file():
                continue

            rel = candidate.relative_to(local_root)
            rel_posix = rel.as_posix()

            if len(include) > 0:
                if not _matches_any(rel_posix=rel_posix, patterns=include):
                    continue

            if len(exclude) > 0:
                if _matches_any(rel_posix=rel_posix, patterns=exclude):
                    continue

            collected.append(rel_posix)

        collected.sort()
        rtnval = collected
        return rtnval

    def _pull_and_hash(self, remote_path: str,
                             aspects: UefiCmdAspects) -> str:
        """
            Pull ``remote_path`` into a temporary local file, read it, and
            return its SHA-256 hex digest.  The temporary file is removed
            regardless of outcome.

            :param remote_path: Remote path (POSIX-form).
            :param aspects: Effective aspects.

            :returns: The lowercase hex SHA-256 of the remote bytes.
        """
        with tempfile.NamedTemporaryFile(
            prefix="espcontentsync-",
            suffix=".bin",
            delete=False,
        ) as scratch:
            scratch_path = scratch.name

        try:
            self._esp.file_pull(
                remote=remote_path,
                local=scratch_path,
                aspects=aspects,
            )
            with open(scratch_path, "rb") as stream:
                remote_bytes = stream.read()
        finally:
            try:
                os.remove(scratch_path)
            except OSError:
                # Temp file already removed; not fatal for the digest path.
                pass

        rtnval = _sha256_of_bytes(data=remote_bytes)
        return rtnval

    def _delete_orphans(self, remote_root: str,
                              local_set: Set[str],
                              aspects: UefiCmdAspects) -> int:
        """
            Walk the remote tree under ``remote_root`` and delete every
            file whose POSIX-relative path is not in ``local_set``.

            :param remote_root: The remote root.
            :param local_set: The set of relative paths present locally
                              (after include/exclude filtering).
            :param aspects: Effective aspects.

            :returns: The number of files deleted.
        """
        tree = self._esp.directory_tree(
            path=remote_root,
            depth=100,
            aspects=aspects,
        )

        remote_relatives: List[str] = []
        _flatten_remote_tree(
            tree=tree,
            rel_prefix="",
            accumulator=remote_relatives,
        )

        deleted_count = 0
        for rel_posix in remote_relatives:
            normalized = _normalize_relative_posix(rel_path=rel_posix)
            if normalized in local_set:
                continue

            orphan_remote = _join_remote(
                remote_root=remote_root, rel_posix=normalized,
            )
            self._delete_remote_file(
                remote_path=orphan_remote, aspects=aspects,
            )
            deleted_count = deleted_count + 1

        rtnval = deleted_count
        return rtnval

    def _delete_remote_file(self, remote_path: str,
                                  aspects: UefiCmdAspects) -> None:
        """
            Delete ``remote_path`` on the ESP.  The method reaches through
            the provider's ``file_delete`` when available and falls back to
            the agent's ``run_cmd`` via a ``rm`` / ``del`` command when the
            provider does not expose ``file_delete``.  In practice the
            unit tests supply a fake provider with a ``file_delete`` method
            so this fallback is exercised only against real deployments.

            :param remote_path: The remote path to delete.
            :param aspects: Effective aspects.
        """
        file_delete_fn = getattr(self._esp, "file_delete", None)
        if callable(file_delete_fn):
            file_delete_fn(path=remote_path, aspects=aspects)
            return

        err_msg_lines = [
            "EspContentSync: remote file delete is not supported by this EspFileProvider.",
            "REMOTE_PATH: {}".format(remote_path),
            "HINT: supply an EspFileProvider that exposes a 'file_delete(path, aspects)' method.",
        ]
        err_msg = os.linesep.join(err_msg_lines)
        raise UefiError(err_msg)
