"""ConversationStore — sqlite index + append-only jsonl.

Copied shape from OpenWorker `coworker/conversations.py`:
  club.db                     sessions(id → title, n_msgs)
  conversations/<id>.jsonl    one message per line, append only
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_SID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_INTERRUPTED_APPROVAL_ERROR = (
    "Outcome is unknown after Sourcecado restarted. "
    "Verify the external resource before retrying."
)


def valid_session_id(sid: str) -> bool:
    return bool(sid) and bool(_SID_RE.fullmatch(sid)) and ".." not in sid


def title_from(messages: list[dict[str, Any]]) -> str:
    for msg in messages:
        if msg.get("role") == "user":
            text = str(msg.get("content") or "").strip()
            if text:
                return text.splitlines()[0][:60]
    return "New session"


class ConversationStore:
    def __init__(self, base_dir: str | Path) -> None:
        self.base = Path(base_dir).expanduser()
        self.base.mkdir(parents=True, exist_ok=True)
        self.conv_dir = self.base / "conversations"
        self.conv_dir.mkdir(exist_ok=True)
        self.event_dir = self.base / "events"
        self.event_dir.mkdir(exist_ok=True)
        self.memory_dir = self.base / "memory"
        self.memory_dir.mkdir(exist_ok=True)
        self.db_path = self.base / "club.db"
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                title TEXT,
                n_msgs INTEGER DEFAULT 0,
                pinned INTEGER NOT NULL DEFAULT 0,
                opened_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cron TEXT NOT NULL,
                prompt TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                result TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS inbox (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                name TEXT NOT NULL,
                arguments TEXT NOT NULL,
                state TEXT NOT NULL,
                decision TEXT,
                actor TEXT,
                requested_at TEXT,
                resolved_at TEXT,
                scope TEXT DEFAULT 'once',
                execution_status TEXT DEFAULT 'pending',
                execution_error TEXT,
                execution_claimant TEXT,
                execution_result TEXT,
                expires_at TEXT,
                reason TEXT,
                session_id TEXT,
                run_id TEXT,
                message_id TEXT,
                part_id TEXT,
                recovery_command_id TEXT,
                original_call_id TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS chat_queue (
                session_id TEXT NOT NULL,
                id TEXT NOT NULL,
                text TEXT NOT NULL,
                position INTEGER NOT NULL,
                state TEXT NOT NULL,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (session_id, id)
            );
            CREATE TABLE IF NOT EXISTS queue_commands (
                session_id TEXT NOT NULL,
                command_id TEXT NOT NULL,
                command_type TEXT NOT NULL,
                item_id TEXT,
                acknowledgement TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (session_id, command_id)
            );
            CREATE TABLE IF NOT EXISTS queue_sessions (
                session_id TEXT PRIMARY KEY,
                paused INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS chat_queue_session_position
                ON chat_queue(session_id, position);
            """
        )
        try:
            self._conn.execute("ALTER TABLE jobs ADD COLUMN next_run_at TEXT")
        except sqlite3.OperationalError:
            pass
        for column in ("name", "template_id", "cadence"):
            try:
                self._conn.execute(f"ALTER TABLE jobs ADD COLUMN {column} TEXT")
            except sqlite3.OperationalError:
                pass
        self._conn.execute(
            """
            UPDATE jobs SET
                name = COALESCE(name, prompt),
                template_id = COALESCE(template_id, 'legacy'),
                cadence = COALESCE(cadence, cron)
            """
        )
        run_migrations = {
            "started_at": "TEXT",
            "finished_at": "TEXT",
            "duration_ms": "INTEGER",
            "summary": "TEXT",
            "artifacts": "TEXT",
            "session_id": "TEXT",
            "waiting_approval_count": "INTEGER DEFAULT 0",
        }
        for column, definition in run_migrations.items():
            try:
                self._conn.execute(
                    f"ALTER TABLE runs ADD COLUMN {column} {definition}"
                )
            except sqlite3.OperationalError:
                pass
        self._conn.execute(
            """
            UPDATE runs SET
                started_at = COALESCE(started_at, created_at),
                finished_at = CASE
                    WHEN status = 'running' THEN finished_at
                    ELSE COALESCE(finished_at, created_at)
                END,
                duration_ms = COALESCE(duration_ms, 0),
                summary = COALESCE(summary, result, ''),
                artifacts = COALESCE(artifacts, '[]'),
                session_id = COALESCE(session_id, 'sched-' || job_id),
                waiting_approval_count = COALESCE(waiting_approval_count, 0)
            """
        )
        try:
            self._conn.execute(
                "ALTER TABLE sessions ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0"
            )
        except sqlite3.OperationalError:
            pass
        try:
            self._conn.execute("ALTER TABLE sessions ADD COLUMN opened_at TEXT")
            self._conn.execute(
                "UPDATE sessions SET opened_at = updated_at WHERE opened_at IS NULL"
            )
        except sqlite3.OperationalError:
            pass
        inbox_migrations = {
            "actor": "TEXT",
            "requested_at": "TEXT",
            "resolved_at": "TEXT",
            "scope": "TEXT DEFAULT 'once'",
            "execution_status": "TEXT DEFAULT 'pending'",
            "execution_error": "TEXT",
            "execution_claimant": "TEXT",
            "execution_result": "TEXT",
            "expires_at": "TEXT",
            "reason": "TEXT",
            "session_id": "TEXT",
            "run_id": "TEXT",
            "message_id": "TEXT",
            "part_id": "TEXT",
            "recovery_command_id": "TEXT",
            "original_call_id": "TEXT",
        }
        for column, definition in inbox_migrations.items():
            try:
                self._conn.execute(
                    f"ALTER TABLE inbox ADD COLUMN {column} {definition}"
                )
            except sqlite3.OperationalError:
                pass
        self._conn.execute(
            """
            UPDATE inbox SET
                requested_at = COALESCE(requested_at, created_at),
                scope = COALESCE(scope, 'once'),
                execution_status = COALESCE(execution_status, 'pending')
            """
        )
        self._conn.commit()
        self._reconcile_orphaned_inbox_executions()
        self._reconcile_orphaned_queue_items()

    def _reconcile_orphaned_inbox_executions(self) -> None:
        """Close claims from the prior process without replaying external writes."""
        result = json.dumps(
            {"status": "interrupted", "error": _INTERRUPTED_APPROVAL_ERROR}
        )
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute(
                    """
                    UPDATE inbox SET
                        execution_status = 'interrupted',
                        execution_error = ?,
                        execution_claimant = NULL,
                        execution_result = ?
                    WHERE execution_status = 'executing'
                    """,
                    (_INTERRUPTED_APPROVAL_ERROR, result),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def _reconcile_orphaned_queue_items(self) -> None:
        """Make pre-start queue claims recoverable without replaying them."""
        now = datetime.now(UTC).isoformat()
        reason = (
            "Sourcecado restarted before this queued run reported completion. "
            "Review and retry explicitly."
        )
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                affected = self._conn.execute(
                    """
                    SELECT DISTINCT session_id
                    FROM chat_queue
                    WHERE state = 'sending'
                    """
                ).fetchall()
                if affected:
                    self._conn.execute(
                        """
                        UPDATE chat_queue
                        SET state = 'interrupted', error = ?, updated_at = ?
                        WHERE state = 'sending'
                        """,
                        (reason, now),
                    )
                    for row in affected:
                        self._conn.execute(
                            """
                            INSERT INTO queue_sessions (session_id, paused, updated_at)
                            VALUES (?, 1, ?)
                            ON CONFLICT(session_id) DO UPDATE SET
                                paused = 1,
                                updated_at = excluded.updated_at
                            """,
                            (str(row["session_id"]), now),
                        )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def _file(self, sid: str) -> Path:
        if not valid_session_id(sid):
            raise ValueError("invalid session id")
        path = self.conv_dir / f"{sid}.jsonl"
        if path.resolve().parent != self.conv_dir.resolve():
            raise ValueError("invalid session id")
        return path

    def load(self, sid: str) -> list[dict[str, Any]]:
        path = self._file(sid)
        if not path.exists():
            return []
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def _event_file(self, sid: str) -> Path:
        if not valid_session_id(sid):
            raise ValueError("invalid session id")
        path = self.event_dir / f"{sid}.jsonl"
        if path.resolve().parent != self.event_dir.resolve():
            raise ValueError("invalid session id")
        return path

    def load_events(self, sid: str) -> list[dict[str, Any]]:
        path = self._event_file(sid)
        if not path.exists():
            return []
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def append_event(self, sid: str, event: dict[str, Any]) -> None:
        with self._lock:
            with open(self._event_file(sid), "a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, ensure_ascii=False) + "\n")
                fh.flush()

    def append(self, sid: str, message: dict[str, Any]) -> None:
        with self._lock:
            with open(self._file(sid), "a", encoding="utf-8") as fh:
                fh.write(json.dumps(message, ensure_ascii=False) + "\n")
                fh.flush()
            self._reindex(sid, self.load(sid))

    def replace_all(self, sid: str, messages: list[dict[str, Any]]) -> None:
        """Rewrite jsonl. Used to insert missing tool results in the middle."""
        with self._lock:
            path = self._file(sid)
            tmp = path.with_suffix(".jsonl.tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                for message in messages:
                    fh.write(json.dumps(message, ensure_ascii=False) + "\n")
                fh.flush()
            tmp.replace(path)
            self._reindex(sid, messages)

    def _reindex(self, sid: str, messages: list[dict[str, Any]]) -> None:
        title = title_from(messages)
        self._conn.execute(
            """
            INSERT INTO sessions (session_id, title, n_msgs, opened_at, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(session_id) DO UPDATE SET
                title = COALESCE(sessions.title, excluded.title),
                n_msgs = excluded.n_msgs,
                updated_at = CURRENT_TIMESTAMP
            """,
            (sid, title, len(messages)),
        )
        self._conn.commit()

    def index(self, sid: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT session_id, title, n_msgs, pinned, opened_at, updated_at FROM sessions WHERE session_id = ?",
                (sid,),
            ).fetchone()
        if row is None:
            return None
        return _session_row(row)

    def list_sessions(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT session_id, title, n_msgs, pinned, opened_at, updated_at FROM sessions
                WHERE session_id NOT LIKE 'sched-%'
                ORDER BY COALESCE(opened_at, updated_at) DESC, updated_at DESC, rowid DESC
                """
            ).fetchall()
        return [_session_row(row) for row in rows]

    def create_session(self, sid: str | None = None) -> dict[str, Any]:
        sid = sid or uuid.uuid4().hex
        if not valid_session_id(sid):
            raise ValueError("invalid session id")
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO sessions (session_id, title, n_msgs, opened_at, updated_at) VALUES (?, NULL, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                (sid,),
            )
            self._conn.commit()
            self._file(sid).touch()
        row = self.index(sid)
        return row if row is not None else {"session_id": sid, "title": None, "n_msgs": 0, "updated_at": None}

    def rename_session(self, sid: str, title: str) -> dict[str, Any] | None:
        cleaned = title.strip()
        if not cleaned:
            return None
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE sessions SET title = ? WHERE session_id = ?",
                (cleaned, sid),
            )
            self._conn.commit()
            if cursor.rowcount == 0:
                return None
        return self.index(sid)

    def set_session_pinned(self, sid: str, pinned: bool) -> dict[str, Any] | None:
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE sessions SET pinned = ? WHERE session_id = ?",
                (int(pinned), sid),
            )
            self._conn.commit()
            if cursor.rowcount == 0:
                return None
        return self.index(sid)

    def open_session_id(self) -> str | None:
        return self.get_setting("open_session_id")

    def set_open_session(self, sid: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE sessions SET opened_at = ? WHERE session_id = ?",
                (datetime.now(UTC).isoformat(), sid),
            )
            self._conn.commit()
        self.set_setting("open_session_id", sid)

    def remember(self, content: str) -> dict[str, Any]:
        with self._lock:
            cursor = self._conn.execute(
                "INSERT INTO memories (content) VALUES (?)", (content,)
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT id, content, created_at FROM memories WHERE id = ?",
                (cursor.lastrowid,),
            ).fetchone()
        item = dict(row)
        self._write_memory_md(item)
        self._write_memory_index()
        return item

    def list_memories(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, content, created_at FROM memories ORDER BY id"
            ).fetchall()
        return [dict(row) for row in rows]

    def memory_update(self, memory_id: int, content: str) -> dict[str, Any] | None:
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE memories SET content = ? WHERE id = ?", (content, memory_id)
            )
            self._conn.commit()
            if cursor.rowcount == 0:
                return None
            row = self._conn.execute(
                "SELECT id, content, created_at FROM memories WHERE id = ?",
                (memory_id,),
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        self._write_memory_md(item)
        self._write_memory_index()
        return item

    def add_job(
        self,
        cron: str,
        prompt: str,
        next_run_at: str | None = None,
        *,
        name: str | None = None,
        template_id: str = "legacy",
        cadence: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO jobs
                    (cron, prompt, next_run_at, name, template_id, cadence)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (cron, prompt, next_run_at, name or prompt, template_id, cadence or cron),
            )
            self._conn.commit()
            row = self._conn.execute(
                """
                SELECT id, cron, prompt, created_at, next_run_at, name,
                       template_id, cadence
                FROM jobs WHERE id = ?
                """,
                (cursor.lastrowid,),
            ).fetchone()
        return dict(row)

    def record_run(self, job_id: int, status: str, result: str = "") -> dict[str, Any]:
        stamp = datetime.now(UTC).isoformat()
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO runs
                    (job_id, status, result, started_at, finished_at,
                     duration_ms, summary, artifacts, session_id,
                     waiting_approval_count)
                VALUES (?, ?, ?, ?, ?, 0, ?, '[]', ?, 0)
                """,
                (job_id, status, result, stamp, stamp, result, f"sched-{job_id}"),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM runs WHERE id = ?",
                (cursor.lastrowid,),
            ).fetchone()
        return _run_row(row)

    def start_run(
        self, job_id: int, *, session_id: str, started_at: str
    ) -> dict[str, Any]:
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO runs
                    (job_id, status, result, started_at, finished_at,
                     duration_ms, summary, artifacts, session_id,
                     waiting_approval_count)
                VALUES (?, 'running', NULL, ?, NULL, NULL, '', '[]', ?, 0)
                """,
                (job_id, started_at, session_id),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM runs WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
        return _run_row(row)

    def finish_run(
        self,
        run_id: int,
        *,
        status: str,
        result: str,
        summary: str,
        artifacts: list[dict[str, Any]],
        duration_ms: int,
        finished_at: str,
        waiting_approval_count: int = 0,
    ) -> dict[str, Any]:
        with self._lock:
            self._conn.execute(
                """
                UPDATE runs SET
                    status = ?, result = ?, summary = ?, artifacts = ?,
                    duration_ms = ?, finished_at = ?,
                    waiting_approval_count = ?
                WHERE id = ?
                """,
                (
                    status,
                    result,
                    summary,
                    json.dumps(artifacts),
                    max(0, duration_ms),
                    finished_at,
                    max(0, waiting_approval_count),
                    run_id,
                ),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
        return _run_row(row)

    def list_schedule(self) -> dict[str, list[dict[str, Any]]]:
        with self._lock:
            jobs = self._conn.execute(
                """
                SELECT id, cron, prompt, created_at, next_run_at, name,
                       template_id, cadence
                FROM jobs ORDER BY id
                """
            ).fetchall()
            runs = self._conn.execute(
                "SELECT * FROM runs ORDER BY id"
            ).fetchall()
        return {
            "jobs": [dict(row) for row in jobs],
            "runs": [_run_row(row) for row in runs],
        }

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            return default
        return str(row["value"])

    def set_setting(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            self._conn.commit()

    def park_inbox(
        self,
        item_id: str,
        name: str,
        arguments: dict[str, Any],
        *,
        reason: str | None = None,
        session_id: str | None = None,
        run_id: str | None = None,
        message_id: str | None = None,
        part_id: str | None = None,
        kind: str = "approval",
        recovery_command_id: str | None = None,
        original_call_id: str | None = None,
    ) -> dict[str, Any]:
        existing = self.get_inbox(item_id)
        if existing is not None:
            return existing
        payload = json.dumps(arguments)
        requested_at = datetime.now(UTC).isoformat()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO inbox
                    (id, kind, name, arguments, state, requested_at, scope,
                     execution_status, reason, session_id, run_id, message_id,
                     part_id, recovery_command_id, original_call_id)
                VALUES (?, ?, ?, ?, 'pending', ?, 'once', 'pending',
                        ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item_id,
                    kind,
                    name,
                    payload,
                    requested_at,
                    reason,
                    session_id,
                    run_id,
                    message_id,
                    part_id,
                    recovery_command_id,
                    original_call_id,
                ),
            )
            self._conn.commit()
            row = self._conn.execute("SELECT * FROM inbox WHERE id = ?", (item_id,)).fetchone()
        return _inbox_row(row)

    def list_inbox(self, *, pending_only: bool = True) -> list[dict[str, Any]]:
        query = "SELECT * FROM inbox"
        if pending_only:
            query += " WHERE state = 'pending'"
        query += " ORDER BY created_at"
        with self._lock:
            rows = self._conn.execute(query).fetchall()
        return [_inbox_row(row) for row in rows]

    def get_inbox(self, item_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM inbox WHERE id = ?", (item_id,)).fetchone()
        return None if row is None else _inbox_row(row)

    def resolve_inbox(
        self,
        item_id: str,
        decision: str,
        *,
        actor: str | None = None,
        scope: str = "once",
    ) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM inbox WHERE id = ?", (item_id,)).fetchone()
            if row is None or row["state"] != "pending":
                return None
            self._conn.execute(
                """
                UPDATE inbox SET
                    state = 'resolved',
                    decision = ?,
                    actor = ?,
                    resolved_at = ?,
                    scope = ?
                WHERE id = ?
                """,
                (
                    decision,
                    actor,
                    datetime.now(UTC).isoformat(),
                    scope or "once",
                    item_id,
                ),
            )
            self._conn.commit()
            row = self._conn.execute("SELECT * FROM inbox WHERE id = ?", (item_id,)).fetchone()
        return _inbox_row(row)

    def decide_and_claim_inbox_execution(
        self,
        item_id: str,
        decision: str,
        *,
        actor: str | None,
        scope: str,
        claimant: str,
    ) -> dict[str, Any] | None:
        """Atomically record a decision and claim consequential execution."""
        if decision not in {"allow", "deny"} or not claimant:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM inbox WHERE id = ?", (item_id,)
            ).fetchone()
            if row is None:
                return None
            decision_recorded = False
            if row["state"] == "pending":
                self._conn.execute(
                    """
                    UPDATE inbox SET
                        state = 'resolved',
                        decision = ?,
                        actor = ?,
                        resolved_at = ?,
                        scope = ?
                    WHERE id = ? AND state = 'pending'
                    """,
                    (
                        decision,
                        actor,
                        datetime.now(UTC).isoformat(),
                        scope or "once",
                        item_id,
                    ),
                )
                decision_recorded = True
            elif row["state"] != "resolved" or row["decision"] != decision:
                return None

            row = self._conn.execute(
                "SELECT * FROM inbox WHERE id = ?", (item_id,)
            ).fetchone()
            claimed = False
            execution_status = str(row["execution_status"] or "pending")
            if decision == "allow" and execution_status == "pending":
                cursor = self._conn.execute(
                    """
                    UPDATE inbox SET
                        execution_status = 'executing',
                        execution_claimant = ?
                    WHERE id = ?
                      AND COALESCE(execution_status, 'pending') = 'pending'
                    """,
                    (claimant, item_id),
                )
                claimed = cursor.rowcount == 1
            elif decision == "deny" and execution_status == "pending":
                self._conn.execute(
                    """
                    UPDATE inbox SET
                        execution_status = 'not_run',
                        execution_error = NULL,
                        execution_claimant = NULL,
                        execution_result = ?
                    WHERE id = ?
                      AND COALESCE(execution_status, 'pending') = 'pending'
                    """,
                    (json.dumps({"error": "denied by user"}), item_id),
                )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM inbox WHERE id = ?", (item_id,)
            ).fetchone()
        item = _inbox_row(row)
        return {
            "item": item,
            "claimed": claimed,
            "owned": (
                item["execution_status"] == "executing"
                and item.get("execution_claimant") == claimant
            ),
            "decision_recorded": decision_recorded,
        }

    def complete_inbox_execution(
        self,
        item_id: str,
        *,
        claimant: str,
        ok: bool,
        result: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Persist one claimant's terminal result without allowing overwrite."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM inbox WHERE id = ?", (item_id,)
            ).fetchone()
            if row is None:
                return None
            status = str(row["execution_status"] or "pending")
            if status in {
                "succeeded",
                "failed",
                "not_run",
                "cancelled",
                "expired",
                "interrupted",
            }:
                return _inbox_row(row)
            if status != "executing" or row["execution_claimant"] != claimant:
                return None
            error = (
                str(result.get("error"))
                if not ok and result.get("error") is not None
                else None
            )
            self._conn.execute(
                """
                UPDATE inbox SET
                    execution_status = ?,
                    execution_error = ?,
                    execution_result = ?
                WHERE id = ?
                  AND execution_status = 'executing'
                  AND execution_claimant = ?
                """,
                (
                    "succeeded" if ok else "failed",
                    error,
                    json.dumps(result, ensure_ascii=False),
                    item_id,
                    claimant,
                ),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM inbox WHERE id = ?", (item_id,)
            ).fetchone()
        return None if row is None else _inbox_row(row)

    def cancel_inbox(self, item_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM inbox WHERE id = ?", (item_id,)
            ).fetchone()
            if row is None or row["state"] != "pending":
                return None
            self._conn.execute(
                """
                UPDATE inbox SET
                    state = 'cancelled',
                    decision = NULL,
                    resolved_at = ?,
                    execution_status = 'cancelled'
                WHERE id = ?
                """,
                (datetime.now(UTC).isoformat(), item_id),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM inbox WHERE id = ?", (item_id,)
            ).fetchone()
        return _inbox_row(row)

    def list_queue(self, session_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, session_id, text, position, state, error,
                       created_at, updated_at
                FROM chat_queue
                WHERE session_id = ?
                ORDER BY position, created_at, id
                """,
                (session_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def queue_paused(self, session_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT paused FROM queue_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return bool(row["paused"]) if row is not None else False

    def set_queue_paused(self, session_id: str, paused: bool) -> None:
        if not valid_session_id(session_id):
            raise ValueError("invalid session id")
        now = datetime.now(UTC).isoformat()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO queue_sessions (session_id, paused, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    paused = excluded.paused,
                    updated_at = excluded.updated_at
                """,
                (session_id, int(paused), now),
            )
            self._conn.commit()

    def claim_next_queue(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            if self.queue_paused(session_id):
                return None
            sending = self._conn.execute(
                "SELECT 1 FROM chat_queue WHERE session_id = ? AND state = 'sending' LIMIT 1",
                (session_id,),
            ).fetchone()
            if sending is not None:
                return None
            row = self._conn.execute(
                """
                SELECT id FROM chat_queue
                WHERE session_id = ? AND state IN ('waiting', 'retrying')
                ORDER BY position, created_at, id
                LIMIT 1
                """,
                (session_id,),
            ).fetchone()
            if row is None:
                return None
            now = datetime.now(UTC).isoformat()
            self._conn.execute(
                "UPDATE chat_queue SET state = 'sending', error = NULL, updated_at = ? WHERE session_id = ? AND id = ?",
                (now, session_id, str(row["id"])),
            )
            self._conn.commit()
            claimed = self._conn.execute(
                """
                SELECT id, session_id, text, position, state, error,
                       created_at, updated_at
                FROM chat_queue WHERE session_id = ? AND id = ?
                """,
                (session_id, str(row["id"])),
            ).fetchone()
        return None if claimed is None else dict(claimed)

    def finish_queue_item(
        self,
        session_id: str,
        item_id: str,
        *,
        state: str,
        error: str | None = None,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        with self._lock:
            if state == "complete":
                self._conn.execute(
                    "DELETE FROM chat_queue WHERE session_id = ? AND id = ?",
                    (session_id, item_id),
                )
            else:
                self._conn.execute(
                    """
                    UPDATE chat_queue SET state = ?, error = ?, updated_at = ?
                    WHERE session_id = ? AND id = ?
                    """,
                    (state, error, now, session_id, item_id),
                )
            rows = self._conn.execute(
                "SELECT id FROM chat_queue WHERE session_id = ? ORDER BY position, created_at, id",
                (session_id,),
            ).fetchall()
            for position, row in enumerate(rows):
                self._conn.execute(
                    "UPDATE chat_queue SET position = ? WHERE session_id = ? AND id = ?",
                    (position, session_id, str(row["id"])),
                )
            self._conn.commit()

    def apply_queue_command(
        self, session_id: str, command: dict[str, Any]
    ) -> dict[str, Any]:
        if not valid_session_id(session_id):
            raise ValueError("invalid session id")
        command_id = str(command.get("command_id") or "").strip()
        command_type = str(command.get("type") or "").strip()
        item_id = str(command.get("item_id") or "").strip()
        if not command_id or not command_type:
            raise ValueError("queue command requires type and command_id")
        with self._lock:
            existing = self._conn.execute(
                "SELECT acknowledgement FROM queue_commands WHERE session_id = ? AND command_id = ?",
                (session_id, command_id),
            ).fetchone()
            if existing is not None:
                return json.loads(str(existing["acknowledgement"]))

            status = "rejected"
            if command_type == "queue_add":
                text = str(command.get("text") or "").strip()
                if not item_id or not text:
                    raise ValueError("queue_add requires item_id and text")
                position_row = self._conn.execute(
                    "SELECT COALESCE(MAX(position), -1) + 1 AS position FROM chat_queue WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
                position = int(position_row["position"])
                now = datetime.now(UTC).isoformat()
                self._conn.execute(
                    """
                    INSERT OR IGNORE INTO chat_queue
                        (session_id, id, text, position, state, error, created_at, updated_at)
                    VALUES (?, ?, ?, ?, 'waiting', NULL, ?, ?)
                    """,
                    (session_id, item_id, text, position, now, now),
                )
                status = "accepted"
            elif command_type == "queue_edit":
                text = str(command.get("text") or "").strip()
                if not item_id or not text:
                    raise ValueError("queue_edit requires item_id and text")
                now = datetime.now(UTC).isoformat()
                cursor = self._conn.execute(
                    """
                    UPDATE chat_queue
                    SET text = ?, updated_at = ?
                    WHERE session_id = ? AND id = ? AND state != 'sending'
                    """,
                    (text, now, session_id, item_id),
                )
                status = "accepted" if cursor.rowcount else "rejected"
            elif command_type == "queue_move":
                if not item_id:
                    raise ValueError("queue_move requires item_id")
                ordered = [
                    str(row["id"])
                    for row in self._conn.execute(
                        "SELECT id FROM chat_queue WHERE session_id = ? ORDER BY position, created_at, id",
                        (session_id,),
                    ).fetchall()
                ]
                if item_id in ordered:
                    ordered.remove(item_id)
                    before_id = str(command.get("before_id") or "")
                    after_id = str(command.get("after_id") or "")
                    if before_id in ordered:
                        ordered.insert(ordered.index(before_id), item_id)
                    elif after_id in ordered:
                        ordered.insert(ordered.index(after_id) + 1, item_id)
                    else:
                        ordered.append(item_id)
                    now = datetime.now(UTC).isoformat()
                    for position, ordered_id in enumerate(ordered):
                        self._conn.execute(
                            "UPDATE chat_queue SET position = ?, updated_at = ? WHERE session_id = ? AND id = ?",
                            (position, now, session_id, ordered_id),
                        )
                    status = "accepted"
            elif command_type == "queue_remove":
                if not item_id:
                    raise ValueError("queue_remove requires item_id")
                cursor = self._conn.execute(
                    "DELETE FROM chat_queue WHERE session_id = ? AND id = ? AND state != 'sending'",
                    (session_id, item_id),
                )
                if cursor.rowcount:
                    rows = self._conn.execute(
                        "SELECT id FROM chat_queue WHERE session_id = ? ORDER BY position, created_at, id",
                        (session_id,),
                    ).fetchall()
                    now = datetime.now(UTC).isoformat()
                    for position, row in enumerate(rows):
                        self._conn.execute(
                            "UPDATE chat_queue SET position = ?, updated_at = ? WHERE session_id = ? AND id = ?",
                            (position, now, session_id, str(row["id"])),
                        )
                    status = "accepted"
            elif command_type == "queue_resume":
                now = datetime.now(UTC).isoformat()
                self._conn.execute(
                    """
                    INSERT INTO queue_sessions (session_id, paused, updated_at)
                    VALUES (?, 0, ?)
                    ON CONFLICT(session_id) DO UPDATE SET
                        paused = 0,
                        updated_at = excluded.updated_at
                    """,
                    (session_id, now),
                )
                status = "accepted"
            elif command_type == "queue_retry":
                if not item_id:
                    raise ValueError("queue_retry requires item_id")
                now = datetime.now(UTC).isoformat()
                cursor = self._conn.execute(
                    """
                    UPDATE chat_queue
                    SET state = 'waiting', error = NULL, updated_at = ?
                    WHERE session_id = ? AND id = ?
                      AND state IN ('failed', 'interrupted', 'offline', 'reconnecting')
                    """,
                    (now, session_id, item_id),
                )
                status = "accepted" if cursor.rowcount else "rejected"

            items = [
                dict(row)
                for row in self._conn.execute(
                    """
                    SELECT id, session_id, text, position, state, error,
                           created_at, updated_at
                    FROM chat_queue
                    WHERE session_id = ?
                    ORDER BY position, created_at, id
                    """,
                    (session_id,),
                ).fetchall()
            ]
            acknowledgement = {
                "version": 2,
                "type": "queue_snapshot",
                "session_id": session_id,
                "command_id": command_id,
                "status": status,
                "paused": self.queue_paused(session_id),
                "items": items,
            }
            now = datetime.now(UTC).isoformat()
            self._conn.execute(
                """
                INSERT INTO queue_commands
                    (session_id, command_id, command_type, item_id,
                     acknowledgement, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    command_id,
                    command_type,
                    item_id or None,
                    json.dumps(acknowledgement),
                    now,
                ),
            )
            self._conn.commit()
        return acknowledgement

    def get_job(self, job_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT id, cron, prompt, created_at, next_run_at, name,
                       template_id, cadence
                FROM jobs WHERE id = ?
                """,
                (job_id,),
            ).fetchone()
        return None if row is None else dict(row)

    def due_jobs(self, now_iso: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, cron, prompt, created_at, next_run_at, name,
                       template_id, cadence
                FROM jobs
                WHERE next_run_at IS NOT NULL AND next_run_at <= ?
                ORDER BY id
                """,
                (now_iso,),
            ).fetchall()
        return [dict(row) for row in rows]

    def set_job_next_run(self, job_id: int, next_run_at: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE jobs SET next_run_at = ? WHERE id = ?", (next_run_at, job_id)
            )
            self._conn.commit()

    def memory_forget(self, memory_id: int) -> bool:
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM memories WHERE id = ?", (memory_id,)
            )
            self._conn.commit()
        gone = cursor.rowcount > 0
        if gone:
            path = self.memory_dir / f"{memory_id}.md"
            path.unlink(missing_ok=True)
            self._write_memory_index()
        return gone

    def _write_memory_md(self, item: dict[str, Any]) -> None:
        self.memory_dir.mkdir(exist_ok=True)
        path = self.memory_dir / f"{item['id']}.md"
        path.write_text(f"# {item['id']}\n\n{item['content']}\n", encoding="utf-8")

    def _write_memory_index(self) -> None:
        self.memory_dir.mkdir(exist_ok=True)
        lines = ["# Memory index", ""]
        for item in self.list_memories():
            lines.append(f"- [#{item['id']}] {item['content']}")
        text = "\n".join(lines) + "\n"
        if len(text) > 4000:
            kept: list[str] = []
            size = 0
            for line in text.splitlines():
                extra = len(line) + 1
                if size + extra > 4000 and kept:
                    break
                kept.append(line)
                size += extra
            text = "\n".join(kept) + "\n"
        (self.memory_dir / "MEMORY.md").write_text(text, encoding="utf-8")


def _inbox_row(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    raw = item.get("arguments") or "{}"
    try:
        item["arguments"] = json.loads(raw)
    except json.JSONDecodeError:
        item["arguments"] = {}
    item.setdefault("actor", None)
    item["requested_at"] = item.get("requested_at") or item.get("created_at")
    item.setdefault("resolved_at", None)
    item["scope"] = item.get("scope") or "once"
    item["execution_status"] = item.get("execution_status") or "pending"
    item.setdefault("execution_error", None)
    item.setdefault("execution_claimant", None)
    raw_result = item.get("execution_result")
    if isinstance(raw_result, str):
        try:
            item["execution_result"] = json.loads(raw_result)
        except json.JSONDecodeError:
            item["execution_result"] = None
    else:
        item["execution_result"] = None
    item.setdefault("expires_at", None)
    item.setdefault("reason", None)
    item.setdefault("session_id", None)
    item.setdefault("run_id", None)
    item.setdefault("message_id", None)
    item.setdefault("part_id", None)
    item.setdefault("recovery_command_id", None)
    item.setdefault("original_call_id", None)
    return item


def _run_row(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    raw = item.get("artifacts") or "[]"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = []
    item["artifacts"] = parsed if isinstance(parsed, list) else []
    item["waiting_approval_count"] = int(item.get("waiting_approval_count") or 0)
    return item


def _session_row(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["pinned"] = bool(item.get("pinned"))
    return item
