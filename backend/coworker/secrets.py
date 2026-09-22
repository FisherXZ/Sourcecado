"""Sourcecado secret storage: mode-0600 JSON excluded from prompts and APIs."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

# Declared data version for secrets.json, read by coworker.migrations. The
# version is recorded out of band so this file keeps holding only secrets.
SCHEMA_VERSION = 1


class SecretStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()
        self._lock = threading.RLock()

    def _load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        payload = json.dumps(data, indent=2) + "\n"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        tmp.replace(self.path)
        os.chmod(self.path, 0o600)

    def get(self, key: str) -> dict[str, Any] | None:
        value = self._load().get(key)
        return value if isinstance(value, dict) else None

    def put(self, key: str, value: dict[str, Any]) -> None:
        with self._lock:
            data = self._load()
            data[key] = value
            self._save(data)

    def delete(self, key: str) -> None:
        with self._lock:
            data = self._load()
            if key in data:
                del data[key]
                self._save(data)
