"""Contained, grant-relative filesystem operations."""

from __future__ import annotations

import fnmatch
import hashlib
import codecs
import mimetypes
import os
import stat as stat_module
import threading
import uuid
import errno
from pathlib import Path
from typing import Any, Callable

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
        state_info = self.state_root.stat()
        self._state_identity = (state_info.st_dev, state_info.st_ino)
        self.trash_root = self.state_root / "workspace_trash"
        self._locks_guard = threading.Lock()
        self._locks: dict[str, threading.RLock] = {}

    def _lock_for(self, path: Path) -> threading.RLock:
        key = str(path)
        with self._locks_guard:
            return self._locks.setdefault(key, threading.RLock())

    @staticmethod
    def _directory_flags() -> int:
        return os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)

    def _open_parent(
        self,
        grant_id: str,
        relative_path: str,
        *,
        write: bool,
        create_parents: bool = False,
    ) -> tuple[dict[str, Any], Path, int, int, tuple[str, ...], str]:
        try:
            grant = self.grants.require(grant_id)
        except GrantUnavailable as exc:
            raise WorkspacePathError(str(exc)) from exc
        if write and grant["access"] != GrantAccess.READ_WRITE.value:
            raise WorkspacePathError("workspace grant is read-only")
        relative = self._relative(relative_path)
        parts = tuple(part for part in relative.parts if part not in {"", "."})
        if not parts:
            raise WorkspacePathError("workspace path must name an item")
        root = Path(str(grant["path"]))
        try:
            root_fd = os.open(root, self._directory_flags())
        except OSError as exc:
            raise WorkspacePathError("workspace root is unavailable") from exc
        root_info = os.fstat(root_fd)
        identity = grant.get("filesystem_identity") or {}
        if root_info.st_dev != identity.get(
            "device"
        ) or root_info.st_ino != identity.get("inode"):
            os.close(root_fd)
            raise WorkspacePathError("workspace root filesystem identity changed")
        parent_fd = os.dup(root_fd)
        try:
            for part in parts[:-1]:
                try:
                    next_fd = os.open(part, self._directory_flags(), dir_fd=parent_fd)
                except FileNotFoundError:
                    if not create_parents:
                        raise WorkspacePathError(
                            "workspace parent directory does not exist"
                        ) from None
                    os.mkdir(part, mode=0o755, dir_fd=parent_fd)
                    next_fd = os.open(part, self._directory_flags(), dir_fd=parent_fd)
                except OSError as exc:
                    raise WorkspacePathError(
                        "workspace parent contains a symlink or unsafe component"
                    ) from exc
                info = os.fstat(next_fd)
                if (info.st_dev, info.st_ino) == self._state_identity:
                    os.close(next_fd)
                    raise WorkspacePathError("Sourcecado state is protected")
                os.close(parent_fd)
                parent_fd = next_fd
            return grant, root, root_fd, parent_fd, parts[:-1], parts[-1]
        except Exception:
            os.close(parent_fd)
            os.close(root_fd)
            raise

    def _assert_parent_binding(
        self,
        grant: dict[str, Any],
        parent_parts: tuple[str, ...],
        parent_fd: int,
    ) -> None:
        try:
            check_fd = os.open(str(grant["path"]), self._directory_flags())
        except OSError as exc:
            raise StaleWorkspaceWrite("workspace root binding changed") from exc
        try:
            identity = grant.get("filesystem_identity") or {}
            root_info = os.fstat(check_fd)
            if root_info.st_dev != identity.get(
                "device"
            ) or root_info.st_ino != identity.get("inode"):
                raise StaleWorkspaceWrite("workspace root binding changed")
            for part in parent_parts:
                try:
                    next_fd = os.open(part, self._directory_flags(), dir_fd=check_fd)
                except OSError as exc:
                    raise StaleWorkspaceWrite(
                        "workspace parent binding changed"
                    ) from exc
                os.close(check_fd)
                check_fd = next_fd
            expected = os.fstat(parent_fd)
            actual = os.fstat(check_fd)
            if (expected.st_dev, expected.st_ino) != (actual.st_dev, actual.st_ino):
                raise StaleWorkspaceWrite("workspace parent binding changed")
        finally:
            os.close(check_fd)

    @staticmethod
    def _hash_fd(descriptor: int) -> str:
        digest = hashlib.sha256()
        os.lseek(descriptor, 0, os.SEEK_SET)
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        os.lseek(descriptor, 0, os.SEEK_SET)
        return digest.hexdigest()

    def _file_info_at(
        self, parent_fd: int, name: str
    ) -> tuple[int, os.stat_result, str]:
        try:
            descriptor = os.open(
                name,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent_fd,
            )
        except FileNotFoundError:
            raise
        except OSError as exc:
            raise WorkspacePathError(
                "workspace target is a symlink or unsafe file"
            ) from exc
        info = os.fstat(descriptor)
        if not stat_module.S_ISREG(info.st_mode):
            os.close(descriptor)
            raise WorkspacePathError("workspace target is not a regular file")
        return descriptor, info, self._hash_fd(descriptor)

    def _before_hash_at(
        self,
        parent_fd: int,
        name: str,
        expected_before_hash: str | None,
        *,
        approved: bool,
        action: str = "overwrite",
    ) -> tuple[str | None, int | None]:
        try:
            descriptor, info, before_hash = self._file_info_at(parent_fd, name)
        except FileNotFoundError:
            if expected_before_hash:
                raise StaleWorkspaceWrite(
                    "workspace target changed or disappeared"
                ) from None
            return None, None
        finally:
            pass
        os.close(descriptor)
        if expected_before_hash is None:
            if not approved:
                raise WorkspaceApprovalRequired(f"{action} requires explicit approval")
        elif before_hash != expected_before_hash:
            raise StaleWorkspaceWrite("workspace target changed since it was read")
        return before_hash, stat_module.S_IMODE(info.st_mode)

    def _atomic_write_at(
        self,
        parent_fd: int,
        name: str,
        payload: bytes,
        *,
        mode: int | None,
        before_replace: Callable[[], None],
    ) -> None:
        temp_name = f".sourcecado-write-{uuid.uuid4().hex}.tmp"
        descriptor = os.open(
            temp_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=parent_fd,
        )
        try:
            if mode is not None:
                os.fchmod(descriptor, mode)
            with os.fdopen(descriptor, "wb", closefd=False) as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.close(descriptor)
            descriptor = -1
            before_replace()
            os.replace(
                temp_name,
                name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
            )
            os.fsync(parent_fd)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                os.unlink(temp_name, dir_fd=parent_fd)
            except FileNotFoundError:
                pass

    def _atomic_copy_fd_at(
        self,
        source_fd: int,
        destination_parent_fd: int,
        destination_name: str,
        *,
        mode: int,
        before_replace: Callable[[], None],
    ) -> None:
        temp_name = f".sourcecado-write-{uuid.uuid4().hex}.tmp"
        descriptor = os.open(
            temp_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=destination_parent_fd,
        )
        try:
            os.fchmod(descriptor, mode)
            os.lseek(source_fd, 0, os.SEEK_SET)
            while True:
                chunk = os.read(source_fd, 1024 * 1024)
                if not chunk:
                    break
                view = memoryview(chunk)
                while view:
                    written = os.write(descriptor, view)
                    view = view[written:]
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            before_replace()
            os.replace(
                temp_name,
                destination_name,
                src_dir_fd=destination_parent_fd,
                dst_dir_fd=destination_parent_fd,
            )
            os.fsync(destination_parent_fd)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                os.unlink(temp_name, dir_fd=destination_parent_fd)
            except FileNotFoundError:
                pass

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
        grant = self.grants.require(grant_id)
        relative = self._relative(path)
        with self._lock_for(Path(str(grant["path"])) / relative):
            grant, _root, root_fd, parent_fd, parent_parts, name = self._open_parent(
                grant_id,
                path,
                write=True,
                create_parents=bool(parents),
            )
            try:
                self._assert_parent_binding(grant, parent_parts, parent_fd)
                try:
                    os.mkdir(name, mode=0o755, dir_fd=parent_fd)
                except FileExistsError:
                    try:
                        descriptor = os.open(
                            name, self._directory_flags(), dir_fd=parent_fd
                        )
                    except OSError as exc:
                        raise WorkspacePathError(
                            "workspace target already exists"
                        ) from exc
                    os.close(descriptor)
                    return {
                        "receipt_type": "updated",
                        "grant_id": grant_id,
                        "path": relative.as_posix(),
                        "status": "already_exists",
                    }
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
                os.close(root_fd)
        return {
            "receipt_type": "created",
            "grant_id": grant_id,
            "path": relative.as_posix(),
            "status": "created",
        }

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
        grant = self.grants.require(grant_id)
        relative = self._relative(path)
        lock = self._lock_for(Path(str(grant["path"])) / relative)
        with lock:
            grant, root, root_fd, parent_fd, parent_parts, name = self._open_parent(
                grant_id,
                path,
                write=True,
                create_parents=create_parents,
            )
            try:
                before_hash, existing_mode = self._before_hash_at(
                    parent_fd,
                    name,
                    expected_before_hash,
                    approved=approved,
                )
                self._assert_parent_binding(grant, parent_parts, parent_fd)
                self._atomic_write_at(
                    parent_fd,
                    name,
                    str(content).encode("utf-8"),
                    mode=existing_mode,
                    before_replace=lambda: self._assert_parent_binding(
                        grant, parent_parts, parent_fd
                    ),
                )
                descriptor, info, after_hash = self._file_info_at(parent_fd, name)
                os.close(descriptor)
            finally:
                os.close(parent_fd)
                os.close(root_fd)
        return {
            "receipt_type": "created" if before_hash is None else "updated",
            "grant_id": grant_id,
            "path": relative.as_posix(),
            "before_hash": before_hash,
            "after_hash": after_hash,
            "size": info.st_size,
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
        destination_grant = self.grants.require(destination_grant_id)
        destination_relative = self._relative(destination_path)
        with self._lock_for(
            Path(str(destination_grant["path"])) / destination_relative
        ):
            (
                source_grant,
                _source_root,
                source_root_fd,
                source_parent_fd,
                source_parent_parts,
                source_name,
            ) = self._open_parent(source_grant_id, source_path, write=False)
            (
                destination_grant,
                _destination_root,
                destination_root_fd,
                destination_parent_fd,
                destination_parent_parts,
                destination_name,
            ) = self._open_parent(
                destination_grant_id,
                destination_path,
                write=True,
                create_parents=create_parents,
            )
            source_fd: int | None = None
            try:
                source_fd, source_info, _source_hash = self._file_info_at(
                    source_parent_fd, source_name
                )
                before_hash, _destination_mode = self._before_hash_at(
                    destination_parent_fd,
                    destination_name,
                    expected_destination_hash,
                    approved=approved,
                    action="copy overwrite",
                )

                def before_replace() -> None:
                    self._assert_parent_binding(
                        source_grant, source_parent_parts, source_parent_fd
                    )
                    self._assert_parent_binding(
                        destination_grant,
                        destination_parent_parts,
                        destination_parent_fd,
                    )

                self._atomic_copy_fd_at(
                    source_fd,
                    destination_parent_fd,
                    destination_name,
                    mode=stat_module.S_IMODE(source_info.st_mode),
                    before_replace=before_replace,
                )
                os.close(source_fd)
                source_fd = None
                destination_fd, destination_info, after_hash = self._file_info_at(
                    destination_parent_fd, destination_name
                )
                os.close(destination_fd)
            finally:
                if source_fd is not None:
                    os.close(source_fd)
                os.close(source_parent_fd)
                os.close(source_root_fd)
                os.close(destination_parent_fd)
                os.close(destination_root_fd)
        return {
            "receipt_type": "created" if before_hash is None else "updated",
            "grant_id": destination_grant_id,
            "path": destination_relative.as_posix(),
            "source_grant_id": source_grant_id,
            "source_path": source_path,
            "before_hash": before_hash,
            "after_hash": after_hash,
            "size": destination_info.st_size,
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
        if source_grant_id != destination_grant_id and not approved:
            raise WorkspaceApprovalRequired(
                "cross-root move requires explicit approval"
            )
        source_grant = self.grants.require(source_grant_id)
        destination_grant = self.grants.require(destination_grant_id)
        source_relative = self._relative(source_path)
        destination_relative = self._relative(destination_path)
        locks = sorted(
            {
                self._lock_for(Path(str(source_grant["path"])) / source_relative),
                self._lock_for(
                    Path(str(destination_grant["path"])) / destination_relative
                ),
            },
            key=id,
        )
        with locks[0]:
            with locks[-1]:
                (
                    source_grant,
                    _source_root,
                    source_root_fd,
                    source_parent_fd,
                    source_parent_parts,
                    source_name,
                ) = self._open_parent(source_grant_id, source_path, write=True)
                (
                    destination_grant,
                    _destination_root,
                    destination_root_fd,
                    destination_parent_fd,
                    destination_parent_parts,
                    destination_name,
                ) = self._open_parent(
                    destination_grant_id,
                    destination_path,
                    write=True,
                    create_parents=create_parents,
                )
                source_fd: int | None = None
                try:
                    source_fd, source_info, source_hash = self._file_info_at(
                        source_parent_fd, source_name
                    )
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
                    destination_before, _destination_mode = self._before_hash_at(
                        destination_parent_fd,
                        destination_name,
                        expected_destination_hash,
                        approved=approved,
                        action="move overwrite",
                    )
                    self._assert_parent_binding(
                        source_grant, source_parent_parts, source_parent_fd
                    )
                    self._assert_parent_binding(
                        destination_grant,
                        destination_parent_parts,
                        destination_parent_fd,
                    )
                    try:
                        os.replace(
                            source_name,
                            destination_name,
                            src_dir_fd=source_parent_fd,
                            dst_dir_fd=destination_parent_fd,
                        )
                    except OSError as exc:
                        if exc.errno != errno.EXDEV or destination_before is not None:
                            raise

                        def before_replace() -> None:
                            self._assert_parent_binding(
                                source_grant,
                                source_parent_parts,
                                source_parent_fd,
                            )
                            self._assert_parent_binding(
                                destination_grant,
                                destination_parent_parts,
                                destination_parent_fd,
                            )

                        self._atomic_copy_fd_at(
                            source_fd,
                            destination_parent_fd,
                            destination_name,
                            mode=stat_module.S_IMODE(source_info.st_mode),
                            before_replace=before_replace,
                        )
                        try:
                            self._assert_parent_binding(
                                source_grant,
                                source_parent_parts,
                                source_parent_fd,
                            )
                            os.unlink(source_name, dir_fd=source_parent_fd)
                        except Exception:
                            os.unlink(destination_name, dir_fd=destination_parent_fd)
                            raise
                    os.fsync(source_parent_fd)
                    os.fsync(destination_parent_fd)
                    destination_fd, _destination_info, after_hash = self._file_info_at(
                        destination_parent_fd, destination_name
                    )
                    os.close(destination_fd)
                finally:
                    if source_fd is not None:
                        os.close(source_fd)
                    os.close(source_parent_fd)
                    os.close(source_root_fd)
                    os.close(destination_parent_fd)
                    os.close(destination_root_fd)
        return {
            "receipt_type": "moved",
            "grant_id": destination_grant_id,
            "path": destination_relative.as_posix(),
            "source_grant_id": source_grant_id,
            "source_path": source_relative.as_posix(),
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
        grant = self.grants.require(grant_id)
        relative = self._relative(path)
        with self._lock_for(Path(str(grant["path"])) / relative):
            grant, _root, root_fd, parent_fd, parent_parts, name = self._open_parent(
                grant_id, path, write=True
            )
            recovery_id = f"trash_{uuid.uuid4().hex}"
            destination_dir = self.trash_root / grant_id
            destination_dir.mkdir(parents=True, exist_ok=True)
            os.chmod(self.trash_root, 0o700)
            os.chmod(destination_dir, 0o700)
            destination_name = f"{recovery_id}-{name}"
            destination_fd = os.open(destination_dir, self._directory_flags())
            try:
                source_info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                if stat_module.S_ISLNK(source_info.st_mode) or not (
                    stat_module.S_ISREG(source_info.st_mode)
                    or stat_module.S_ISDIR(source_info.st_mode)
                ):
                    raise WorkspacePathError(
                        "only files and directories can be trashed"
                    )
                source_fd = None
                before_hash = None
                if stat_module.S_ISREG(source_info.st_mode):
                    source_fd, _file_info, before_hash = self._file_info_at(
                        parent_fd, name
                    )
                self._assert_parent_binding(grant, parent_parts, parent_fd)
                try:
                    os.replace(
                        name,
                        destination_name,
                        src_dir_fd=parent_fd,
                        dst_dir_fd=destination_fd,
                    )
                except OSError as exc:
                    if exc.errno != errno.EXDEV or source_fd is None:
                        raise WorkspacePathError(
                            "trash could not move this item safely"
                        ) from exc

                    def before_replace() -> None:
                        self._assert_parent_binding(grant, parent_parts, parent_fd)

                    self._atomic_copy_fd_at(
                        source_fd,
                        destination_fd,
                        destination_name,
                        mode=stat_module.S_IMODE(source_info.st_mode),
                        before_replace=before_replace,
                    )
                    try:
                        self._assert_parent_binding(grant, parent_parts, parent_fd)
                        os.unlink(name, dir_fd=parent_fd)
                    except Exception:
                        os.unlink(destination_name, dir_fd=destination_fd)
                        raise
                finally:
                    if source_fd is not None:
                        os.close(source_fd)
                os.fsync(parent_fd)
                os.fsync(destination_fd)
            finally:
                os.close(destination_fd)
                os.close(parent_fd)
                os.close(root_fd)
        return {
            "receipt_type": "trashed",
            "grant_id": grant_id,
            "path": relative.as_posix(),
            "before_hash": before_hash,
            "recovery_id": recovery_id,
            "recoverable": True,
        }
