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
from pathlib import Path
from typing import Any

_SID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


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
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        try:
            self._conn.execute("ALTER TABLE jobs ADD COLUMN next_run_at TEXT")
        except sqlite3.OperationalError:
            pass
        self._conn.commit()

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
            INSERT INTO sessions (session_id, title, n_msgs, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
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
                "SELECT session_id, title, n_msgs, updated_at FROM sessions WHERE session_id = ?",
                (sid,),
            ).fetchone()
        if row is None:
            return None
        return dict(row)

    def list_sessions(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT session_id, title, n_msgs, updated_at FROM sessions
                WHERE session_id NOT LIKE 'sched-%'
                ORDER BY updated_at DESC, rowid DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def create_session(self, sid: str | None = None) -> dict[str, Any]:
        sid = sid or uuid.uuid4().hex
        if not valid_session_id(sid):
            raise ValueError("invalid session id")
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO sessions (session_id, title, n_msgs, updated_at) VALUES (?, NULL, 0, CURRENT_TIMESTAMP)",
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

    def open_session_id(self) -> str | None:
        return self.get_setting("open_session_id")

    def set_open_session(self, sid: str) -> None:
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

    def add_job(self, cron: str, prompt: str, next_run_at: str | None = None) -> dict[str, Any]:
        with self._lock:
            cursor = self._conn.execute(
                "INSERT INTO jobs (cron, prompt, next_run_at) VALUES (?, ?, ?)",
                (cron, prompt, next_run_at),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT id, cron, prompt, created_at, next_run_at FROM jobs WHERE id = ?",
                (cursor.lastrowid,),
            ).fetchone()
        return dict(row)

    def record_run(self, job_id: int, status: str, result: str = "") -> dict[str, Any]:
        with self._lock:
            cursor = self._conn.execute(
                "INSERT INTO runs (job_id, status, result) VALUES (?, ?, ?)",
                (job_id, status, result),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT id, job_id, status, result, created_at FROM runs WHERE id = ?",
                (cursor.lastrowid,),
            ).fetchone()
        return dict(row)

    def list_schedule(self) -> dict[str, list[dict[str, Any]]]:
        with self._lock:
            jobs = self._conn.execute(
                "SELECT id, cron, prompt, created_at, next_run_at FROM jobs ORDER BY id"
            ).fetchall()
            runs = self._conn.execute(
                "SELECT id, job_id, status, result, created_at FROM runs ORDER BY id"
            ).fetchall()
        return {"jobs": [dict(row) for row in jobs], "runs": [dict(row) for row in runs]}

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

    def park_inbox(self, item_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        existing = self.get_inbox(item_id)
        if existing is not None:
            return existing
        payload = json.dumps(arguments)
        with self._lock:
            self._conn.execute(
                "INSERT INTO inbox (id, kind, name, arguments, state) VALUES (?, 'approval', ?, ?, 'pending')",
                (item_id, name, payload),
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

    def resolve_inbox(self, item_id: str, decision: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM inbox WHERE id = ?", (item_id,)).fetchone()
            if row is None or row["state"] != "pending":
                return None
            self._conn.execute(
                "UPDATE inbox SET state = 'resolved', decision = ? WHERE id = ?",
                (decision, item_id),
            )
            self._conn.commit()
            row = self._conn.execute("SELECT * FROM inbox WHERE id = ?", (item_id,)).fetchone()
        return _inbox_row(row)

    def get_job(self, job_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, cron, prompt, created_at, next_run_at FROM jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        return None if row is None else dict(row)

    def due_jobs(self, now_iso: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, cron, prompt, created_at, next_run_at FROM jobs WHERE next_run_at IS NOT NULL AND next_run_at <= ? ORDER BY id",
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
    return item
