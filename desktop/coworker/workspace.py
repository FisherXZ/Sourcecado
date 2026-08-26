"""Durable operator grants for local workspace access."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any


class GrantAccess(StrEnum):
    READ_ONLY = "read_only"
    READ_WRITE = "read_write"


class GrantUnavailable(ValueError):
    """The requested root does not currently confer usable authority."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _access(value: GrantAccess | str) -> GrantAccess:
    try:
        return value if isinstance(value, GrantAccess) else GrantAccess(str(value))
    except ValueError as exc:
        raise GrantUnavailable("access must be read_only or read_write") from exc


def _identity(path: Path) -> dict[str, int]:
    stat_result = path.stat()
    return {"device": stat_result.st_dev, "inode": stat_result.st_ino}


class WorkspaceGrantStore:
    """Owns the persisted mapping from opaque grant IDs to canonical roots."""

    VERSION = 1

    def __init__(self, state_root: str | Path) -> None:
        self.state_root = Path(state_root).expanduser()
        self.state_root.mkdir(parents=True, exist_ok=True)
        self.path = self.state_root / "workspace_grants.json"
        self._lock = threading.RLock()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": self.VERSION, "grants": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise GrantUnavailable("workspace grant store is unreadable") from exc
        if (
            not isinstance(data, dict)
            or data.get("version") != self.VERSION
            or not isinstance(data.get("grants"), list)
        ):
            raise GrantUnavailable("workspace grant store has an unsupported format")
        return data

    def _save(self, data: dict[str, Any]) -> None:
        payload = json.dumps(data, indent=2, sort_keys=True) + "\n"
        fd, raw_path = tempfile.mkstemp(
            prefix=".workspace-grants-", suffix=".tmp", dir=self.state_root
        )
        temp_path = Path(raw_path)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            temp_path.replace(self.path)
            os.chmod(self.path, 0o600)
            directory_fd = os.open(self.state_root, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temp_path.unlink(missing_ok=True)

    @staticmethod
    def _canonical_directory(path: str | Path) -> Path:
        if not str(path).strip():
            raise GrantUnavailable("workspace root is required")
        candidate = Path(path).expanduser()
        if not candidate.exists():
            raise GrantUnavailable("workspace root does not exist")
        canonical = candidate.resolve(strict=True)
        if not canonical.is_dir():
            raise GrantUnavailable("workspace root must be a directory")
        return canonical

    def _validate_shell(
        self, access: GrantAccess, allow_shell: bool, root: Path
    ) -> None:
        if allow_shell and access != GrantAccess.READ_WRITE:
            raise GrantUnavailable("shell access requires a read_write grant")
        if allow_shell:
            canonical_state = self.state_root.resolve(strict=True)
            try:
                canonical_state.relative_to(root)
            except ValueError:
                pass
            else:
                raise GrantUnavailable(
                    "shell workspace cannot contain Sourcecado state"
                )

    def add(
        self,
        path: str | Path,
        *,
        label: str,
        access: GrantAccess | str,
        allow_shell: bool = False,
    ) -> dict[str, Any]:
        canonical = self._canonical_directory(path)
        normalized_access = _access(access)
        self._validate_shell(normalized_access, bool(allow_shell), canonical)
        stamp = _now()
        grant = {
            "id": f"grant_{uuid.uuid4().hex}",
            "path": str(canonical),
            "label": str(label).strip() or canonical.name or str(canonical),
            "access": normalized_access.value,
            "allow_shell": bool(allow_shell),
            "filesystem_identity": _identity(canonical),
            "created_at": stamp,
            "updated_at": stamp,
            "revoked_at": None,
        }
        with self._lock:
            data = self._load()
            data["grants"].append(grant)
            self._save(data)
        return dict(grant)

    def list_all(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self._load()["grants"]]

    def list_active(self) -> list[dict[str, Any]]:
        return [item for item in self.list_all() if item.get("revoked_at") is None]

    def get(self, grant_id: str) -> dict[str, Any] | None:
        return next(
            (item for item in self.list_all() if item.get("id") == grant_id),
            None,
        )

    def require(self, grant_id: str) -> dict[str, Any]:
        grant = self.get(grant_id)
        if grant is None:
            raise GrantUnavailable("workspace grant was not found")
        if grant.get("revoked_at") is not None:
            raise GrantUnavailable("workspace grant is revoked")
        root = Path(str(grant["path"]))
        if not root.is_dir():
            raise GrantUnavailable("workspace root is unavailable")
        if _identity(root.resolve(strict=True)) != grant.get("filesystem_identity"):
            raise GrantUnavailable("workspace root filesystem identity changed")
        return grant

    def update(
        self,
        grant_id: str,
        *,
        label: str | None = None,
        access: GrantAccess | str | None = None,
        allow_shell: bool | None = None,
        path: str | Path | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            data = self._load()
            index = next(
                (
                    offset
                    for offset, item in enumerate(data["grants"])
                    if item.get("id") == grant_id
                ),
                None,
            )
            if index is None:
                raise GrantUnavailable("workspace grant was not found")
            current = dict(data["grants"][index])
            if current.get("revoked_at") is not None:
                raise GrantUnavailable("workspace grant is revoked")
            next_access = _access(access if access is not None else current["access"])
            next_shell = bool(
                current.get("allow_shell") if allow_shell is None else allow_shell
            )
            if path is not None:
                canonical = self._canonical_directory(path)
                current["path"] = str(canonical)
                current["filesystem_identity"] = _identity(canonical)
            else:
                canonical = Path(str(current["path"])).resolve(strict=True)
            self._validate_shell(next_access, next_shell, canonical)
            if label is not None:
                current["label"] = str(label).strip() or Path(current["path"]).name
            current["access"] = next_access.value
            current["allow_shell"] = next_shell
            current["updated_at"] = _now()
            data["grants"][index] = current
            self._save(data)
            return dict(current)

    def revoke(self, grant_id: str) -> dict[str, Any]:
        with self._lock:
            data = self._load()
            for index, raw in enumerate(data["grants"]):
                if raw.get("id") != grant_id:
                    continue
                current = dict(raw)
                if current.get("revoked_at") is None:
                    stamp = _now()
                    current["revoked_at"] = stamp
                    current["updated_at"] = stamp
                    data["grants"][index] = current
                    self._save(data)
                return dict(current)
        raise GrantUnavailable("workspace grant was not found")
