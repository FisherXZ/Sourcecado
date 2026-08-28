"""Immutable, bounded receipts for local workspace authority and execution."""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Declared data version for workspace_receipts.jsonl, read by
# coworker.migrations. An append-only log carries no header, so the version
# is recorded out of band.
SCHEMA_VERSION = 1


_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(?P<key>(?:api[_-]?key|access[_-]?token|auth[_-]?token|token|secret|password))"
    r"(?P<separator>\s*[:=]\s*)(?P<value>[^\s]+)"
)
_PRIVATE_KEY = re.compile(
    r"-----BEGIN (?:(?:RSA|EC|DSA|OPENSSH|ENCRYPTED) )?PRIVATE KEY-----.*?"
    r"-----END (?:(?:RSA|EC|DSA|OPENSSH|ENCRYPTED) )?PRIVATE KEY-----",
    re.DOTALL,
)


def _safe_summary(value: str) -> str:
    text = _PRIVATE_KEY.sub("[REDACTED PRIVATE KEY]", str(value or ""))
    text = _SECRET_ASSIGNMENT.sub(
        lambda match: f"{match.group('key')}{match.group('separator')}[REDACTED]",
        text,
    )
    return text[:300]


class WorkspaceAuditStore:
    """Append-only receipt log with a deliberately narrow public schema."""

    def __init__(self, state_root: str | Path) -> None:
        self.state_root = Path(state_root).expanduser()
        self.state_root.mkdir(parents=True, exist_ok=True)
        self.path = self.state_root / "workspace_receipts.jsonl"
        self._lock = threading.RLock()

    def record(
        self,
        *,
        receipt_type: str,
        tool: str,
        risk_class: str,
        decision: str,
        execution_target: str,
        status: str,
        summary: str,
        grant_id: str | None = None,
        path: str | None = None,
        command_fingerprint: str | None = None,
        before_hash: str | None = None,
        after_hash: str | None = None,
        actor: str | None = None,
        session_id: str | None = None,
        run_id: str | None = None,
        task_id: str | None = None,
        started_at: str | None = None,
        finished_at: str | None = None,
        duration_ms: int | None = None,
        exit_code: int | None = None,
        truncated: bool | None = None,
    ) -> dict[str, Any]:
        receipt: dict[str, Any] = {
            "id": f"receipt_{uuid.uuid4().hex}",
            "receipt_type": str(receipt_type),
            "tool": str(tool),
            "risk_class": str(risk_class),
            "decision": str(decision),
            "execution_target": str(execution_target),
            "status": str(status),
            "summary": _safe_summary(summary),
            "created_at": datetime.now(UTC).isoformat(),
        }
        optional = {
            "grant_id": grant_id,
            "path": path,
            "command_fingerprint": command_fingerprint,
            "before_hash": before_hash,
            "after_hash": after_hash,
            "actor": actor,
            "session_id": session_id,
            "run_id": run_id,
            "task_id": task_id,
            "started_at": started_at,
            "finished_at": finished_at,
            "duration_ms": duration_ms,
            "exit_code": exit_code,
            "truncated": truncated,
        }
        receipt.update(
            {key: value for key, value in optional.items() if value is not None}
        )
        payload = json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n"
        with self._lock:
            descriptor = os.open(
                self.path,
                os.O_WRONLY | os.O_APPEND | os.O_CREAT,
                0o600,
            )
            try:
                os.write(descriptor, payload.encode("utf-8"))
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.chmod(self.path, 0o600)
        return receipt

    def list(self, *, limit: int = 100) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows: list[dict[str, Any]] = []
        with self._lock:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict):
                    rows.append(item)
        return list(reversed(rows[-max(1, min(int(limit), 1000)) :]))
