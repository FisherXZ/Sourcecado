"""Contained, grant-relative filesystem operations."""

from __future__ import annotations

import fnmatch
import hashlib
import codecs
import mimetypes
import os
import shutil
import stat as stat_module
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any

from coworker.workspace import GrantAccess, GrantUnavailable, WorkspaceGrantStore


IGNORED_DIRECTORIES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "__pycache__",
        "build",
        "coverage",
        "dist",
        "node_modules",
        "target",
    }
)
PROTECTED_PATH_PARTS = frozenset({".git", ".ssh", ".gnupg"})
PROTECTED_FILE_NAMES = frozenset(
    {".env", ".npmrc", ".pypirc", "credentials", "credentials.json"}
)
PROTECTED_FILE_SUFFIXES = frozenset({".key", ".pem", ".p12", ".pfx"})


class WorkspacePathError(ValueError):
    """A path was invalid, escaped authority, or named an unsafe file type."""


class WorkspaceApprovalRequired(WorkspacePathError):
    """The requested mutation is valid but cannot run without approval."""


class StaleWorkspaceWrite(WorkspacePathError):
    """The target changed after the agent observed it."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def is_protected_workspace_path(path: str | Path) -> bool:
    candidate = Path(str(path or "."))
    lowered = {part.casefold() for part in candidate.parts}
    name = candidate.name.casefold()
    return bool(
        lowered & PROTECTED_PATH_PARTS
        or name in PROTECTED_FILE_NAMES
        or candidate.suffix.casefold() in PROTECTED_FILE_SUFFIXES
        or name.startswith(".env.")
    )


class WorkspaceFilesystem:
    MAX_READ_CHARS = 64_000
    MAX_LIST_ENTRIES = 500
    MAX_SEARCH_RESULTS = 200
    MAX_SEARCH_FILE_BYTES = 2 * 1024 * 1024

    def __init__(self, grants: WorkspaceGrantStore, *, state_root: str | Path) -> None:
        self.grants = grants
        self.state_root = Path(state_root).expanduser().resolve(strict=True)
        self.trash_root = self.state_root / "workspace_trash"
        self._locks_guard = threading.Lock()
        self._locks: dict[str, threading.RLock] = {}

    def _lock_for(self, path: Path) -> threading.RLock:
        key = str(path)
        with self._locks_guard:
            return self._locks.setdefault(key, threading.RLock())

    @staticmethod
    def _relative(raw: str | Path) -> Path:
        text = str(raw or ".")
        if "\x00" in text:
            raise WorkspacePathError("path contains an invalid null byte")
        relative = Path(text)
        if relative.is_absolute():
            raise WorkspacePathError("workspace paths must be relative")
        if any(part == ".." for part in relative.parts):
            raise WorkspacePathError("path is outside the authorized root")
        return relative

    def _protected(self, path: Path) -> bool:
        return _inside(path, self.state_root)

    def resolve(
        self,
        grant_id: str,
        relative_path: str | Path,
        *,
        must_exist: bool = True,
        write: bool = False,
    ) -> tuple[dict[str, Any], Path, Path]:
        try:
            grant = self.grants.require(grant_id)
        except GrantUnavailable as exc:
            raise WorkspacePathError(str(exc)) from exc
        if write and grant["access"] != GrantAccess.READ_WRITE.value:
            raise WorkspacePathError("workspace grant is read-only")
        root = Path(str(grant["path"])).resolve(strict=True)
        relative = self._relative(relative_path)
        lexical = root / relative
        try:
            resolved = lexical.resolve(strict=must_exist)
        except FileNotFoundError as exc:
            raise WorkspacePathError("workspace path does not exist") from exc
        if not _inside(resolved, root):
            raise WorkspacePathError("path resolves outside the authorized root")
        if self._protected(resolved):
            raise WorkspacePathError("Sourcecado state is protected")
        current = root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise WorkspacePathError("symbolic links are not allowed")
            if not current.exists():
                break
        return grant, root, resolved

    @staticmethod
    def _kind(mode: int) -> str:
        if stat_module.S_ISREG(mode):
            return "file"
        if stat_module.S_ISDIR(mode):
            return "directory"
        if stat_module.S_ISLNK(mode):
            return "symlink"
        return "special"

    def stat(self, grant_id: str, path: str) -> dict[str, Any]:
        _grant, root, resolved = self.resolve(grant_id, path)
        info = resolved.stat()
        kind = self._kind(info.st_mode)
        if kind == "special":
            raise WorkspacePathError("device and special files are not allowed")
        result: dict[str, Any] = {
            "path": resolved.relative_to(root).as_posix() or ".",
            "type": kind,
            "size": info.st_size,
            "modified_ns": info.st_mtime_ns,
        }
        if kind == "file":
            result["sha256"] = _sha256(resolved)
            result["mime_type"] = mimetypes.guess_type(resolved.name)[0]
        return result

    def list(
        self,
        grant_id: str,
        path: str = ".",
        *,
        offset: int = 0,
        max_entries: int = 100,
    ) -> dict[str, Any]:
        _grant, root, resolved = self.resolve(grant_id, path)
        if not resolved.is_dir():
            raise WorkspacePathError("workspace path is not a directory")
        limit = max(1, min(int(max_entries), self.MAX_LIST_ENTRIES))
        start = max(0, int(offset))
        entries: list[dict[str, Any]] = []
        for child in sorted(resolved.iterdir(), key=lambda item: item.name.casefold()):
            try:
                canonical = child.resolve(strict=True)
            except OSError:
                continue
            if (
                self._protected(canonical)
                or child.is_symlink()
                or is_protected_workspace_path(child.relative_to(root))
            ):
                continue
            try:
                info = child.stat()
            except OSError:
                continue
            kind = self._kind(info.st_mode)
            if kind == "special":
                continue
            entries.append(
                {
                    "name": child.name,
                    "path": child.relative_to(root).as_posix(),
                    "type": kind,
                    "size": info.st_size,
                    "modified_ns": info.st_mtime_ns,
                }
            )
        page = entries[start : start + limit]
        next_offset = start + len(page)
        return {
            "path": resolved.relative_to(root).as_posix() or ".",
            "entries": page,
            "truncated": next_offset < len(entries),
            "next_offset": next_offset if next_offset < len(entries) else None,
        }

    def read(
        self,
        grant_id: str,
        path: str,
        *,
        offset: int = 0,
        max_chars: int = 20_000,
    ) -> dict[str, Any]:
        _grant, root, resolved = self.resolve(grant_id, path)
        if not resolved.is_file():
            raise WorkspacePathError("workspace path is not a regular file")
        start = max(0, int(offset))
        limit = max(1, min(int(max_chars), self.MAX_READ_CHARS))
        info = resolved.stat()
        with resolved.open("rb") as handle:
            handle.seek(start)
            raw = handle.read((limit * 4) + 4)
        base = {
            "path": resolved.relative_to(root).as_posix(),
            "size": info.st_size,
            "sha256": _sha256(resolved),
            "mime_type": mimetypes.guess_type(resolved.name)[0],
        }
        if b"\x00" in raw:
            return {**base, "kind": "binary"}
        try:
            decoder = codecs.getincrementaldecoder("utf-8")("strict")
            text = decoder.decode(raw, final=start + len(raw) >= info.st_size)
        except UnicodeDecodeError:
            return {**base, "kind": "binary"}
        content = text[:limit]
        consumed = len(content.encode("utf-8"))
        next_offset = start + consumed
        truncated = next_offset < info.st_size
        return {
            **base,
            "kind": "text",
            "content": content,
            "offset": start,
            "truncated": truncated,
            "next_offset": next_offset if truncated else None,
        }

    def _walk(self, root: Path, start: Path):
        for current, dirnames, filenames in os.walk(start, followlinks=False):
            current_path = Path(current)
            kept: list[str] = []
            for dirname in sorted(dirnames):
                child = current_path / dirname
                try:
                    canonical = child.resolve(strict=True)
                except OSError:
                    continue
                if (
                    dirname in IGNORED_DIRECTORIES
                    or child.is_symlink()
                    or self._protected(canonical)
                    or not _inside(canonical, root)
                ):
                    continue
                kept.append(dirname)
            dirnames[:] = kept
            yield current_path, sorted(filenames)

    def search(
        self,
        grant_id: str,
        path: str,
        *,
        query: str,
        max_results: int = 50,
    ) -> dict[str, Any]:
        if not query:
            raise WorkspacePathError("search query is required")
        _grant, root, resolved = self.resolve(grant_id, path)
        if not resolved.is_dir():
            raise WorkspacePathError("search path is not a directory")
        limit = max(1, min(int(max_results), self.MAX_SEARCH_RESULTS))
        matches: list[dict[str, Any]] = []
        for current, filenames in self._walk(root, resolved):
            for filename in filenames:
                candidate = current / filename
                if candidate.is_symlink() or is_protected_workspace_path(
                    candidate.relative_to(root)
                ):
                    continue
                try:
                    info = candidate.stat()
                    if (
                        not candidate.is_file()
                        or info.st_size > self.MAX_SEARCH_FILE_BYTES
                        or self._protected(candidate.resolve(strict=True))
                    ):
                        continue
                    text = candidate.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                for line_number, line in enumerate(text.splitlines(), start=1):
                    if query.casefold() not in line.casefold():
                        continue
                    matches.append(
                        {
                            "path": candidate.relative_to(root).as_posix(),
                            "line": line_number,
                            "excerpt": line[:300],
                        }
                    )
                    if len(matches) > limit:
                        return {
                            "query": query,
                            "matches": matches[:limit],
                            "truncated": True,
                        }
        return {"query": query, "matches": matches, "truncated": False}

    def find(
        self,
        grant_id: str,
        path: str,
        *,
        pattern: str,
        max_results: int = 100,
    ) -> dict[str, Any]:
        if not pattern:
            raise WorkspacePathError("find pattern is required")
        _grant, root, resolved = self.resolve(grant_id, path)
        if not resolved.is_dir():
            raise WorkspacePathError("find path is not a directory")
        limit = max(1, min(int(max_results), self.MAX_SEARCH_RESULTS))
        matches: list[dict[str, Any]] = []
        for current, filenames in self._walk(root, resolved):
            for filename in filenames:
                candidate = current / filename
                if (
                    candidate.is_symlink()
                    or is_protected_workspace_path(candidate.relative_to(root))
                    or not fnmatch.fnmatch(filename, pattern)
                ):
                    continue
                try:
                    canonical = candidate.resolve(strict=True)
                except OSError:
                    continue
                if self._protected(canonical) or not _inside(canonical, root):
                    continue
                matches.append(
                    {
                        "path": candidate.relative_to(root).as_posix(),
                        "type": "file",
                        "size": candidate.stat().st_size,
                    }
                )
                if len(matches) > limit:
                    return {
                        "pattern": pattern,
                        "matches": matches[:limit],
                        "truncated": True,
                    }
        return {"pattern": pattern, "matches": matches, "truncated": False}

    def mkdir(
        self,
        grant_id: str,
        path: str,
        *,
        parents: bool = False,
    ) -> dict[str, Any]:
        _grant, root, target = self.resolve(
            grant_id, path, must_exist=False, write=True
        )
        with self._lock_for(target):
            if target.exists():
                if not target.is_dir():
                    raise WorkspacePathError("workspace target already exists")
                return {
                    "receipt_type": "updated",
                    "grant_id": grant_id,
                    "path": target.relative_to(root).as_posix(),
                    "status": "already_exists",
                }
            target.mkdir(parents=bool(parents), exist_ok=False)
            self._sync_directory(target.parent)
        return {
            "receipt_type": "created",
            "grant_id": grant_id,
            "path": target.relative_to(root).as_posix(),
            "status": "created",
        }

    @staticmethod
    def _verify_before_hash(
        path: Path,
        expected_before_hash: str | None,
        *,
        approved: bool,
        action: str = "overwrite",
    ) -> str | None:
        if not path.exists():
            if expected_before_hash:
                raise StaleWorkspaceWrite("workspace target changed or disappeared")
            return None
        if not path.is_file():
            raise WorkspacePathError("workspace target is not a regular file")
        before_hash = _sha256(path)
        if expected_before_hash is None:
            if not approved:
                raise WorkspaceApprovalRequired(f"{action} requires explicit approval")
        elif before_hash != expected_before_hash:
            raise StaleWorkspaceWrite("workspace target changed since it was read")
        return before_hash

    @staticmethod
    def _sync_directory(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _atomic_write(
        self, target: Path, payload: bytes, *, mode: int | None = None
    ) -> None:
        fd, raw_path = tempfile.mkstemp(
            prefix=".sourcecado-write-", suffix=".tmp", dir=target.parent
        )
        temp_path = Path(raw_path)
        try:
            if mode is not None:
                os.fchmod(fd, mode)
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            temp_path.replace(target)
            self._sync_directory(target.parent)
        finally:
            temp_path.unlink(missing_ok=True)

    def write(
        self,
        grant_id: str,
        path: str,
        content: str,
        *,
        expected_before_hash: str | None = None,
        create_parents: bool = False,
        approved: bool = False,
    ) -> dict[str, Any]:
        _grant, root, target = self.resolve(
            grant_id, path, must_exist=False, write=True
        )
        lock = self._lock_for(target)
        with lock:
            _grant, root, target = self.resolve(
                grant_id, path, must_exist=False, write=True
            )
            if create_parents:
                target.parent.mkdir(parents=True, exist_ok=True)
            if not target.parent.is_dir():
                raise WorkspacePathError("workspace parent directory does not exist")
            before_hash = self._verify_before_hash(
                target, expected_before_hash, approved=approved
            )
            existing_mode = (
                stat_module.S_IMODE(target.stat().st_mode)
                if before_hash is not None
                else None
            )
            self._atomic_write(
                target,
                str(content).encode("utf-8"),
                mode=existing_mode,
            )
            after_hash = _sha256(target)
        return {
            "receipt_type": "created" if before_hash is None else "updated",
            "grant_id": grant_id,
            "path": target.relative_to(root).as_posix(),
            "before_hash": before_hash,
            "after_hash": after_hash,
            "size": target.stat().st_size,
        }

    def patch(
        self,
        grant_id: str,
        path: str,
        *,
        replacements: list[dict[str, Any]],
        expected_before_hash: str | None = None,
        approved: bool = False,
    ) -> dict[str, Any]:
        _grant, _root, target = self.resolve(grant_id, path, write=True)
        lock = self._lock_for(target)
        with lock:
            current = self.read(grant_id, path)
            if current.get("kind") != "text" or current.get("truncated"):
                raise WorkspacePathError("fs_patch requires a complete UTF-8 text file")
            text = str(current.get("content") or "")
            for replacement in replacements:
                old = replacement.get("old")
                new = replacement.get("new")
                if not isinstance(old, str) or not old:
                    raise WorkspacePathError("patch old text must be non-empty")
                if not isinstance(new, str):
                    raise WorkspacePathError("patch new text must be a string")
                occurrences = text.count(old)
                if occurrences != 1:
                    raise WorkspacePathError("patch old text must match exactly once")
                text = text.replace(old, new, 1)
            return self.write(
                grant_id,
                path,
                text,
                expected_before_hash=expected_before_hash,
                approved=approved,
            )

    def copy(
        self,
        source_grant_id: str,
        source_path: str,
        destination_grant_id: str,
        destination_path: str,
        *,
        expected_destination_hash: str | None = None,
        create_parents: bool = False,
        approved: bool = False,
    ) -> dict[str, Any]:
        _source_grant, _source_root, source = self.resolve(source_grant_id, source_path)
        _destination_grant, destination_root, destination = self.resolve(
            destination_grant_id,
            destination_path,
            must_exist=False,
            write=True,
        )
        if not source.is_file():
            raise WorkspacePathError("fs_copy currently requires a regular file")
        with self._lock_for(destination):
            if create_parents:
                destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.parent.is_dir():
                raise WorkspacePathError("workspace parent directory does not exist")
            before_hash = self._verify_before_hash(
                destination,
                expected_destination_hash,
                approved=approved,
                action="copy overwrite",
            )
            fd, raw_path = tempfile.mkstemp(
                prefix=".sourcecado-write-",
                suffix=".tmp",
                dir=destination.parent,
            )
            temp_path = Path(raw_path)
            try:
                os.fchmod(fd, stat_module.S_IMODE(source.stat().st_mode))
                with (
                    source.open("rb") as source_handle,
                    os.fdopen(fd, "wb") as destination_handle,
                ):
                    shutil.copyfileobj(source_handle, destination_handle)
                    destination_handle.flush()
                    os.fsync(destination_handle.fileno())
                temp_path.replace(destination)
                self._sync_directory(destination.parent)
            finally:
                temp_path.unlink(missing_ok=True)
            after_hash = _sha256(destination)
        return {
            "receipt_type": "created" if before_hash is None else "updated",
            "grant_id": destination_grant_id,
            "path": destination.relative_to(destination_root).as_posix(),
            "source_grant_id": source_grant_id,
            "source_path": source_path,
            "before_hash": before_hash,
            "after_hash": after_hash,
            "size": destination.stat().st_size,
        }

    def move(
        self,
        source_grant_id: str,
        source_path: str,
        destination_grant_id: str,
        destination_path: str,
        *,
        expected_source_hash: str | None = None,
        expected_destination_hash: str | None = None,
        create_parents: bool = False,
        approved: bool = False,
    ) -> dict[str, Any]:
        _source_grant, source_root, source = self.resolve(
            source_grant_id, source_path, write=True
        )
        _destination_grant, destination_root, destination = self.resolve(
            destination_grant_id,
            destination_path,
            must_exist=False,
            write=True,
        )
        if source_grant_id != destination_grant_id and not approved:
            raise WorkspaceApprovalRequired(
                "cross-root move requires explicit approval"
            )
        locks = sorted({self._lock_for(source), self._lock_for(destination)}, key=id)
        with locks[0]:
            with locks[-1]:
                if not source.is_file():
                    raise WorkspacePathError(
                        "fs_move currently requires a regular file"
                    )
                source_hash = _sha256(source)
                if (
                    expected_source_hash is not None
                    and source_hash != expected_source_hash
                ):
                    raise StaleWorkspaceWrite(
                        "workspace source changed since it was read"
                    )
                if expected_source_hash is None and not approved:
                    raise WorkspaceApprovalRequired(
                        "move without a source before-hash requires explicit approval"
                    )
                if create_parents:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                if not destination.parent.is_dir():
                    raise WorkspacePathError(
                        "workspace parent directory does not exist"
                    )
                destination_before = self._verify_before_hash(
                    destination,
                    expected_destination_hash,
                    approved=approved,
                    action="move overwrite",
                )
                try:
                    source.replace(destination)
                except OSError:
                    self.copy(
                        source_grant_id,
                        source_path,
                        destination_grant_id,
                        destination_path,
                        expected_destination_hash=expected_destination_hash,
                        approved=approved,
                    )
                    source.unlink()
                self._sync_directory(source.parent)
                if destination.parent != source.parent:
                    self._sync_directory(destination.parent)
                after_hash = _sha256(destination)
        return {
            "receipt_type": "moved",
            "grant_id": destination_grant_id,
            "path": destination.relative_to(destination_root).as_posix(),
            "source_grant_id": source_grant_id,
            "source_path": source.relative_to(source_root).as_posix(),
            "before_hash": destination_before,
            "source_hash": source_hash,
            "after_hash": after_hash,
        }

    def trash(
        self,
        grant_id: str,
        path: str,
        *,
        approved: bool = False,
    ) -> dict[str, Any]:
        if not approved:
            raise WorkspaceApprovalRequired("trash requires explicit approval")
        _grant, root, source = self.resolve(grant_id, path, write=True)
        if not source.is_file() and not source.is_dir():
            raise WorkspacePathError("only files and directories can be trashed")
        with self._lock_for(source):
            recovery_id = f"trash_{uuid.uuid4().hex}"
            destination_dir = self.trash_root / grant_id
            destination_dir.mkdir(parents=True, exist_ok=True)
            destination = destination_dir / f"{recovery_id}-{source.name}"
            before_hash = _sha256(source) if source.is_file() else None
            source.replace(destination)
            self._sync_directory(source.parent)
            self._sync_directory(destination_dir)
        return {
            "receipt_type": "trashed",
            "grant_id": grant_id,
            "path": source.relative_to(root).as_posix(),
            "before_hash": before_hash,
            "recovery_id": recovery_id,
            "recoverable": True,
        }
