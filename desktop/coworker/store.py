"""Sourcecado conversation state: SQLite index plus append-only JSONL."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from coworker.context_projection import (
    ContextAuthority,
    ContextCategory,
    ContextSensitivity,
    ContextSourceRef,
    ContextState,
    ProjectionItem,
)

_SID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
# Declared schema version for club.db and the transcript log, read by the
# migration registry in coworker.migrations. Reporting only: this module
# does not stamp or check it.
SCHEMA_VERSION = 2
TRANSCRIPT_VERSION = 1

# context-projection-v1 (issue #58). A memory row is model context only after
# the director classifies it as a person-independent operator preference.
# Everything else -- a row migrated from before the contract, and every new
# ambiguous `remember` write -- waits for review and is withheld.
MEMORY_CATEGORY_LEGACY = "legacy_unclassified"
MEMORY_CATEGORY_UNCLASSIFIED = "unclassified"
MEMORY_CATEGORY_PREFERENCE = "operator_preference"
MEMORY_NEEDS_REVIEW = "needs_review"
MEMORY_CLASSIFIED = "classified"
MEMORY_SENSITIVITY_STANDARD = "standard"

# The columns that carry that contract. They are added to an existing database
# without a default on purpose: the registry step in coworker/migrations.py is
# what writes every existing row to legacy_unclassified/needs_review, with a
# backup taken first. A default here would do that work silently on the first
# open and leave the registered migration with nothing left to migrate.
_MEMORY_CONTEXT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("category", "TEXT"),
    ("classification_status", "TEXT"),
    ("person_id", "TEXT"),
    ("session_id", "TEXT"),
    ("source_ref", "TEXT"),
    ("updated_at", "TEXT"),
    ("fresh_until", "TEXT"),
    ("sensitivity", "TEXT"),
    ("claim_key", "TEXT"),
)

_MEMORY_COLUMNS = "id, content, created_at, " + ", ".join(
    column for column, _definition in _MEMORY_CONTEXT_COLUMNS
)

# Per-item cap for the operator-preference category in DEFAULT_PROJECTION_POLICY.
# The adapter clips to it; the projector omits anything still over it.
_MEMORY_ITEM_TOKENS = 64


def projection_tokens(text: str) -> int:
    """The versioned budget unit for context-projection-v1.

    `ceil(len(utf-8 bytes) / 3)`: provider-independent and conservative. It is
    an explicit budget unit, not a claim about any provider's billed tokens.
    """
    return -(-len(text.encode("utf-8")) // 3)


def _memory_source_ref(memory_id: int) -> str:
    return f"sourcecado:memory/{memory_id}"


def _memory_source_ref_record(row: sqlite3.Row) -> ContextSourceRef:
    return ContextSourceRef(
        id=_memory_source_ref(row["id"]),
        provider="sourcecado",
        locator=f"memory/{row['id']}",
        observed_at=row["created_at"],
        modified_at=row["updated_at"],
        fresh_until=row["fresh_until"],
    )


def _is_past(value: str | None, moment: datetime) -> bool:
    if not value:
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed <= moment


def _memory_line(memory_id: int, excerpt: str, state: str) -> str:
    return f"[#{memory_id}] {excerpt} ({_memory_source_ref(memory_id)}, {state})"


def _clip_memory_line(row: sqlite3.Row, state: ContextState) -> tuple[str, bool]:
    """Render one preference, clipped at a word boundary to the per-item cap.

    The Source Reference and the evidence state are outside the clipped span,
    so shortening an excerpt never cuts either in half.
    """
    line = _memory_line(row["id"], row["content"], state.value)
    if projection_tokens(line) <= _MEMORY_ITEM_TOKENS:
        return line, False
    words = row["content"].split()
    while words:
        words.pop()
        candidate = _memory_line(row["id"], " ".join(words) + "…", state.value)
        if projection_tokens(candidate) <= _MEMORY_ITEM_TOKENS:
            return candidate, True
    return _memory_line(row["id"], "…", state.value), True
_INTERRUPTED_APPROVAL_ERROR = (
    "Outcome is unknown after Sourcecado restarted. "
    "Verify the external resource before retrying."
)
_EXPIRED_PENDING_ERROR = (
    "This approval expired before a decision was made. "
    "Ask again if the action is still wanted."
)
_STALE_EXECUTION_ERROR = (
    "Outcome is unknown: this approval's execution never reported a result. "
    "Verify the external resource before retrying."
)
_INTERRUPTED_RUN_SUMMARY = (
    "Sourcecado restarted before this routine finished. "
    "Review the thread before retrying."
)
_DEFAULT_APPROVAL_TTL_SECONDS = 24 * 60 * 60.0


def _approval_ttl_seconds() -> float:
    raw = os.environ.get("CLUB_APPROVAL_TTL_SECONDS", "")
    try:
        value = float(raw)
    except ValueError:
        return _DEFAULT_APPROVAL_TTL_SECONDS
    return value if value > 0 else _DEFAULT_APPROVAL_TTL_SECONDS


def valid_session_id(sid: str) -> bool:
    return bool(sid) and bool(_SID_RE.fullmatch(sid)) and ".." not in sid


def _read_jsonl(blob: str) -> list[dict[str, Any]]:
    """Skip torn or foreign lines (crash mid-append) instead of failing the read."""
    out: list[dict[str, Any]] = []
    for line in blob.splitlines():
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            out.append(parsed)
    return out


def title_from(messages: list[dict[str, Any]]) -> str:
    for msg in messages:
        if msg.get("role") == "user":
            text = str(msg.get("content") or "").strip()
            if text:
                return text.splitlines()[0][:60]
    return "New session"


class ConversationStore:
    def __init__(
        self,
        base_dir: str | Path,
        *,
        approval_ttl_seconds: float | None = None,
    ) -> None:
        self.approval_ttl_seconds = (
            approval_ttl_seconds
            if approval_ttl_seconds is not None
            else _approval_ttl_seconds()
        )
        self.base = Path(base_dir).expanduser()
        self.base.mkdir(parents=True, exist_ok=True)
        os.chmod(self.base, 0o700)
        self.conv_dir = self.base / "conversations"
        self.conv_dir.mkdir(exist_ok=True)
        os.chmod(self.conv_dir, 0o700)
        self.event_dir = self.base / "events"
        self.event_dir.mkdir(exist_ok=True)
        os.chmod(self.event_dir, 0o700)
        self.memory_dir = self.base / "memory"
        self.memory_dir.mkdir(exist_ok=True)
        os.chmod(self.memory_dir, 0o700)
        self.db_path = self.base / "club.db"
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        os.chmod(self.db_path, 0o600)
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
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                category TEXT NOT NULL DEFAULT 'unclassified',
                classification_status TEXT NOT NULL DEFAULT 'needs_review',
                person_id TEXT,
                session_id TEXT,
                source_ref TEXT,
                updated_at TEXT,
                fresh_until TEXT,
                sensitivity TEXT NOT NULL DEFAULT 'standard',
                claim_key TEXT
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
                resource TEXT,
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
            CREATE TABLE IF NOT EXISTS recovery_commands (
                session_id TEXT NOT NULL,
                command_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (session_id, command_id)
            );
            CREATE INDEX IF NOT EXISTS chat_queue_session_position
                ON chat_queue(session_id, position);
            """
        )
        for column, definition in _MEMORY_CONTEXT_COLUMNS:
            try:
                self._conn.execute(
                    f"ALTER TABLE memories ADD COLUMN {column} {definition}"
                )
            except sqlite3.OperationalError:
                pass
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
            # Count of events already in the reused sched-{job_id} file
            # when this run started. The restart reconciler must ignore
            # those; they belong to earlier attempts.
            "event_offset": "INTEGER NOT NULL DEFAULT 0",
        }
        for column, definition in run_migrations.items():
            try:
                self._conn.execute(f"ALTER TABLE runs ADD COLUMN {column} {definition}")
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
        # Pre-S4 scheduler writes stored run_turn's raw "ok" status directly;
        # RECEIPT_STATUSES has mapped it to "success" ever since, but rows
        # written before that mapping existed are still on disk with the old
        # text. Normalize them once so every consumer -- API, UI, future
        # exports -- agrees on the shared status contract.
        self._conn.execute("UPDATE runs SET status = 'success' WHERE status = 'ok'")
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
            "resource": "TEXT",
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
        self._reconcile_orphaned_runs()
        self._reconcile_orphaned_recovery_commands()

    def _reconcile_orphaned_recovery_commands(self) -> None:
        """Drop in-flight recovery claims from the prior process.

        A claim only serializes concurrent sockets while the work runs; the
        durable duplicate guard is the tool_recovery event checked before
        claiming. A claim whose process died mid-work must not silence the
        command forever.
        """
        with self._lock:
            self._conn.execute("DELETE FROM recovery_commands")
            self._conn.commit()

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

    def _reconcile_orphaned_runs(self) -> None:
        """Close runs the prior process left mid-flight without inventing an outcome.

        A crash can land between this attempt's terminal event and the
        matching runs-table update. Trust only events written after this
        run started: sched-{job_id} files are reused, so last week's
        terminal event is still in the file.
        """
        now = datetime.now(UTC).isoformat()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                running = self._conn.execute(
                    """
                    SELECT id, session_id, event_offset
                    FROM runs WHERE status = 'running'
                    """
                ).fetchall()
                for row in running:
                    status, result, summary = self._terminal_transcript_outcome(
                        str(row["session_id"]),
                        int(row["event_offset"] or 0),
                    )
                    self._conn.execute(
                        """
                        UPDATE runs SET
                            status = ?,
                            result = ?,
                            summary = ?,
                            finished_at = ?,
                            duration_ms = COALESCE(duration_ms, 0)
                        WHERE id = ?
                        """,
                        (status, result, summary, now, row["id"]),
                    )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    # Mirrors scheduler.RECEIPT_STATUSES' run_turn -> receipt mapping, keyed
    # by the transcript's turn_end "state" instead of run_turn's return
    # value. Duplicated rather than imported: store.py cannot import
    # coworker.automation.scheduler without a circular import (scheduler.py
    # imports ConversationStore).
    _TRANSCRIPT_TERMINAL_STATES = {
        "complete": "success",
        "partial": "partial",
        "stopped": "partial",
    }

    def _terminal_transcript_outcome(
        self, session_id: str, event_offset: int = 0
    ) -> tuple[str, str, str]:
        """Derive (status, result, summary) from this attempt's last event, if terminal."""
        events = self.load_events(session_id)[max(0, event_offset) :]
        last = events[-1] if events else None
        if last is not None and last.get("type") == "error":
            message = str(last.get("message") or _INTERRUPTED_RUN_SUMMARY)
            return "failed", message, message
        if last is not None and last.get("type") == "turn_end":
            status = self._TRANSCRIPT_TERMINAL_STATES.get(str(last.get("state")))
            if status is not None:
                text = str(last.get("text") or "")
                summary = text or _INTERRUPTED_RUN_SUMMARY
                return status, text, summary
        return "interrupted", _INTERRUPTED_RUN_SUMMARY, _INTERRUPTED_RUN_SUMMARY

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
        return _read_jsonl(path.read_text(encoding="utf-8"))

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
        return _read_jsonl(path.read_text(encoding="utf-8"))

    def append_event(self, sid: str, event: dict[str, Any]) -> None:
        with self._lock:
            path = self._event_file(sid)
            descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            os.chmod(path, 0o600)
            with os.fdopen(descriptor, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, ensure_ascii=False) + "\n")
                fh.flush()

    def append_event_once(
        self,
        sid: str,
        event: dict[str, Any],
        *,
        matching_fields: tuple[str, ...],
    ) -> tuple[dict[str, Any], bool]:
        """Atomically append an event unless its durable identity already exists."""
        if not matching_fields or any(field not in event for field in matching_fields):
            raise ValueError("matching event fields must be present")
        with self._lock:
            existing = next(
                (
                    candidate
                    for candidate in self.load_events(sid)
                    if all(
                        candidate.get(field) == event[field]
                        for field in matching_fields
                    )
                ),
                None,
            )
            if existing is not None:
                return dict(existing), False
            self.append_event(sid, event)
            return dict(event), True

    def append(self, sid: str, message: dict[str, Any]) -> None:
        with self._lock:
            path = self._file(sid)
            descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            os.chmod(path, 0o600)
            with os.fdopen(descriptor, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(message, ensure_ascii=False) + "\n")
                fh.flush()
            self._reindex(sid, self.load(sid))

    def replace_all(self, sid: str, messages: list[dict[str, Any]]) -> None:
        """Rewrite jsonl. Used to insert missing tool results in the middle."""
        with self._lock:
            path = self._file(sid)
            tmp = path.with_suffix(".jsonl.tmp")
            descriptor = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as fh:
                for message in messages:
                    fh.write(json.dumps(message, ensure_ascii=False) + "\n")
                fh.flush()
            tmp.replace(path)
            os.chmod(path, 0o600)
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
            self._file(sid).touch(mode=0o600)
            os.chmod(self._file(sid), 0o600)
        row = self.index(sid)
        return (
            row
            if row is not None
            else {"session_id": sid, "title": None, "n_msgs": 0, "updated_at": None}
        )

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

    def remember(
        self,
        content: str,
        *,
        person_id: str | None = None,
        session_id: str | None = None,
        sensitivity: str = MEMORY_SENSITIVITY_STANDARD,
        fresh_until: str | None = None,
    ) -> dict[str, Any]:
        """Save a memory row. It is never model context until classified.

        A write becomes a global operator preference only when the director
        explicitly asks for one, which is `memory_classify`. Nothing here reads
        the content to guess a category: a row saying "prefer short drafts" and
        a row saying "Ada moved to Analytic" both land in the same waiting
        state, because the words alone cannot tell them apart.
        """
        now = datetime.now(UTC).isoformat()
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO memories
                    (content, category, classification_status, person_id,
                     session_id, updated_at, fresh_until, sensitivity)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    content,
                    MEMORY_CATEGORY_UNCLASSIFIED,
                    MEMORY_NEEDS_REVIEW,
                    person_id,
                    session_id,
                    now,
                    fresh_until,
                    sensitivity,
                ),
            )
            memory_id = cursor.lastrowid
            self._conn.execute(
                "UPDATE memories SET source_ref = ? WHERE id = ?",
                (_memory_source_ref(memory_id), memory_id),
            )
            self._conn.commit()
            row = self._conn.execute(
                f"SELECT {_MEMORY_COLUMNS} FROM memories WHERE id = ?",
                (memory_id,),
            ).fetchone()
        item = dict(row)
        self._write_memory_md(item)
        self._write_memory_index()
        return item

    def list_memories(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                f"SELECT {_MEMORY_COLUMNS} FROM memories ORDER BY id"
            ).fetchall()
        return [dict(row) for row in rows]

    def memory_update(self, memory_id: int, content: str) -> dict[str, Any] | None:
        """Rewrite a memory's content, which sends it back for review.

        Rewritten text is text the director has not classified. Keeping the old
        classification would let a rewrite change model context without anyone
        approving what it now says.
        """
        with self._lock:
            cursor = self._conn.execute(
                """
                UPDATE memories SET
                    content = ?,
                    category = ?,
                    classification_status = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    content,
                    MEMORY_CATEGORY_UNCLASSIFIED,
                    MEMORY_NEEDS_REVIEW,
                    datetime.now(UTC).isoformat(),
                    memory_id,
                ),
            )
            self._conn.commit()
            if cursor.rowcount == 0:
                return None
            row = self._conn.execute(
                f"SELECT {_MEMORY_COLUMNS} FROM memories WHERE id = ?",
                (memory_id,),
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        self._write_memory_md(item)
        self._write_memory_index()
        return item

    def memory_classify(
        self, memory_id: int, *, claim_key: str | None = None
    ) -> dict[str, Any] | None:
        """Record the director's explicit decision that a row is a preference.

        Refuses a row that carries a Person or session scope. A fact about one
        Person belongs on that Person File through the existing Board contract,
        and promoting it here is exactly the silent widening the migration
        default exists to prevent.
        """
        with self._lock:
            row = self._conn.execute(
                f"SELECT {_MEMORY_COLUMNS} FROM memories WHERE id = ?",
                (memory_id,),
            ).fetchone()
            if row is None:
                return None
            if row["person_id"] or row["session_id"]:
                raise ValueError(
                    "a Person- or session-scoped memory cannot become a global "
                    "operator preference; file it on the Person File instead"
                )
            self._conn.execute(
                """
                UPDATE memories SET
                    category = ?,
                    classification_status = ?,
                    claim_key = ?,
                    source_ref = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    MEMORY_CATEGORY_PREFERENCE,
                    MEMORY_CLASSIFIED,
                    claim_key,
                    _memory_source_ref(memory_id),
                    datetime.now(UTC).isoformat(),
                    memory_id,
                ),
            )
            self._conn.commit()
            updated = self._conn.execute(
                f"SELECT {_MEMORY_COLUMNS} FROM memories WHERE id = ?",
                (memory_id,),
            ).fetchone()
        return dict(updated)

    def memory_backlog(self) -> dict[str, Any]:
        """What the director still has to classify, and what is already live."""
        with self._lock:
            rows = self._conn.execute(
                f"SELECT {_MEMORY_COLUMNS} FROM memories ORDER BY id"
            ).fetchall()
        waiting = [
            dict(row)
            for row in rows
            if (row["classification_status"] or MEMORY_NEEDS_REVIEW)
            != MEMORY_CLASSIFIED
        ]
        return {
            "needs_review": len(waiting),
            "classified": len(rows) - len(waiting),
            "items": waiting,
        }

    def memory_projection_items(
        self, *, now: datetime | None = None
    ) -> tuple[ProjectionItem, ...]:
        """The classified operator preferences, as already-scoped projection items.

        Eligibility is decided in SQL before any ranking: unclassified rows,
        Person- or session-scoped rows, and restricted rows never leave this
        method, so they cannot reach a prompt.
        """
        moment = now or datetime.now(UTC)
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT {_MEMORY_COLUMNS} FROM memories
                WHERE classification_status = ?
                  AND category = ?
                  AND person_id IS NULL
                  AND session_id IS NULL
                  AND COALESCE(sensitivity, ?) = ?
                ORDER BY id
                """,
                (
                    MEMORY_CLASSIFIED,
                    MEMORY_CATEGORY_PREFERENCE,
                    MEMORY_SENSITIVITY_STANDARD,
                    MEMORY_SENSITIVITY_STANDARD,
                ),
            ).fetchall()
        contested: dict[str, list[sqlite3.Row]] = {}
        for row in rows:
            if row["claim_key"]:
                contested.setdefault(row["claim_key"], []).append(row)
        items: list[ProjectionItem] = []
        for row in rows:
            group = contested.get(row["claim_key"] or "", [])
            conflicting = len(group) > 1
            if conflicting:
                state = ContextState.CONFLICTING
            elif _is_past(row["fresh_until"], moment):
                state = ContextState.STALE
            else:
                state = ContextState.CURRENT
            refs = tuple(
                _memory_source_ref_record(member)
                for member in (group if conflicting else [row])
            )
            text, truncated = _clip_memory_line(row, state)
            items.append(
                ProjectionItem(
                    id=f"memory:{row['id']}",
                    category=ContextCategory.OPERATOR_PREFERENCE,
                    text=text,
                    tokens=projection_tokens(text),
                    state=state,
                    authority=ContextAuthority.DIRECTOR,
                    updated_at=row["updated_at"] or row["created_at"],
                    source_refs=refs,
                    claim_key=row["claim_key"],
                    truncated=truncated,
                    sensitivity=ContextSensitivity.STANDARD,
                )
            )
        return tuple(items)

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
                (
                    cron,
                    prompt,
                    next_run_at,
                    name or prompt,
                    template_id,
                    cadence or cron,
                ),
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
            event_offset = len(self.load_events(session_id))
            cursor = self._conn.execute(
                """
                INSERT INTO runs
                    (job_id, status, result, started_at, finished_at,
                     duration_ms, summary, artifacts, session_id,
                     waiting_approval_count, event_offset)
                VALUES (?, 'running', NULL, ?, NULL, NULL, '', '[]', ?, 0, ?)
                """,
                (job_id, started_at, session_id, event_offset),
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
            runs = self._conn.execute("SELECT * FROM runs ORDER BY id").fetchall()
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
        resource: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        existing = self.get_inbox(item_id)
        if existing is not None:
            return existing
        payload = json.dumps(arguments)
        requested = datetime.now(UTC)
        requested_at = requested.isoformat()
        expires_at = (
            requested + timedelta(seconds=self.approval_ttl_seconds)
        ).isoformat()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO inbox
                    (id, kind, name, arguments, state, requested_at, scope,
                     execution_status, expires_at, reason, session_id, run_id,
                     message_id, part_id, recovery_command_id, original_call_id,
                     resource)
                VALUES (?, ?, ?, ?, 'pending', ?, 'once', 'pending',
                        ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item_id,
                    kind,
                    name,
                    payload,
                    requested_at,
                    expires_at,
                    reason,
                    session_id,
                    run_id,
                    message_id,
                    part_id,
                    recovery_command_id,
                    original_call_id,
                    json.dumps(resource) if resource is not None else None,
                ),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM inbox WHERE id = ?", (item_id,)
            ).fetchone()
        return _inbox_row(row)

    def reap_overdue_inbox(self, now: str | None = None) -> list[dict[str, Any]]:
        """TTL reaper, two distinct sweeps.

        A PENDING approval past its TTL becomes 'expired' (nobody decided in
        time) — never a denial: decision stays NULL and execution_status is
        'expired', not 'not_run'. A stale EXECUTING claim past its TTL becomes
        'interrupted' (authorized, outcome unknown) — the same semantics the
        restart reconciler uses, never 'expired'.
        """
        stamp = now or datetime.now(UTC).isoformat()
        with self._lock:
            overdue = self._conn.execute(
                """
                SELECT id FROM inbox
                WHERE expires_at IS NOT NULL AND expires_at <= ?
                  AND (state = 'pending' OR execution_status = 'executing')
                """,
                (stamp,),
            ).fetchall()
            if not overdue:
                return []
            self._conn.execute(
                """
                UPDATE inbox SET
                    execution_status = 'interrupted',
                    execution_claimant = NULL,
                    execution_error = ?,
                    execution_result = ?
                WHERE expires_at IS NOT NULL AND expires_at <= ?
                  AND execution_status = 'executing'
                """,
                (
                    _STALE_EXECUTION_ERROR,
                    json.dumps(
                        {"status": "interrupted", "error": _STALE_EXECUTION_ERROR}
                    ),
                    stamp,
                ),
            )
            self._conn.execute(
                """
                UPDATE inbox SET
                    state = 'expired',
                    resolved_at = ?,
                    execution_status = 'expired',
                    execution_claimant = NULL,
                    execution_error = ?,
                    execution_result = ?
                WHERE expires_at IS NOT NULL AND expires_at <= ?
                  AND state = 'pending'
                """,
                (
                    stamp,
                    _EXPIRED_PENDING_ERROR,
                    json.dumps({"status": "expired", "error": _EXPIRED_PENDING_ERROR}),
                    stamp,
                ),
            )
            self._conn.commit()
            rows = [
                self._conn.execute(
                    "SELECT * FROM inbox WHERE id = ?", (str(row["id"]),)
                ).fetchone()
                for row in overdue
            ]
        return [_inbox_row(row) for row in rows if row is not None]

    def list_inbox(self, *, pending_only: bool = True) -> list[dict[str, Any]]:
        self.reap_overdue_inbox()
        query = "SELECT * FROM inbox"
        if pending_only:
            query += " WHERE state = 'pending'"
        query += " ORDER BY created_at"
        with self._lock:
            rows = self._conn.execute(query).fetchall()
        return [_inbox_row(row) for row in rows]

    def get_inbox(self, item_id: str) -> dict[str, Any] | None:
        self.reap_overdue_inbox()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM inbox WHERE id = ?", (item_id,)
            ).fetchone()
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
            row = self._conn.execute(
                "SELECT * FROM inbox WHERE id = ?", (item_id,)
            ).fetchone()
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
            row = self._conn.execute(
                "SELECT * FROM inbox WHERE id = ?", (item_id,)
            ).fetchone()
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

    def sessions_with_queue(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT session_id FROM chat_queue ORDER BY session_id"
            ).fetchall()
        return [str(row["session_id"]) for row in rows]

    def mark_queue_offline(self, session_id: str) -> int:
        """Park undelivered items when the socket driving their drain is gone."""
        now = datetime.now(UTC).isoformat()
        with self._lock:
            cursor = self._conn.execute(
                """
                UPDATE chat_queue SET state = 'offline', error = NULL, updated_at = ?
                WHERE session_id = ? AND state IN ('waiting', 'retrying')
                """,
                (now, session_id),
            )
            self._conn.commit()
        return cursor.rowcount

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
                WHERE session_id = ? AND state IN ('waiting', 'retrying', 'reconnecting')
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

    def claim_recovery_command(self, session_id: str, command_id: str) -> bool:
        """Atomically claim one recovery command; False when already claimed.

        Same shape as queue_commands: the claim closes the window between two
        sockets sending the same command. Callers check the event log for a
        recorded tool_recovery outcome before claiming.
        """
        if not valid_session_id(session_id):
            raise ValueError("invalid session id")
        if not command_id:
            raise ValueError("recovery command requires command_id")
        now = datetime.now(UTC).isoformat()
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT OR IGNORE INTO recovery_commands
                    (session_id, command_id, created_at)
                VALUES (?, ?, ?)
                """,
                (session_id, command_id, now),
            )
            self._conn.commit()
        return cursor.rowcount == 1

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
                # Items parked by a socket loss come back as reconnecting,
                # which the drain claims like waiting.
                self._conn.execute(
                    """
                    UPDATE chat_queue
                    SET state = 'reconnecting', error = NULL, updated_at = ?
                    WHERE session_id = ? AND state = 'offline'
                    """,
                    (now, session_id),
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
        os.chmod(path, 0o600)

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
        path = self.memory_dir / "MEMORY.md"
        path.write_text(text, encoding="utf-8")
        os.chmod(path, 0o600)


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
    raw_resource = item.get("resource")
    if isinstance(raw_resource, str):
        try:
            item["resource"] = json.loads(raw_resource)
        except json.JSONDecodeError:
            item["resource"] = None
    else:
        item["resource"] = None
    return item


def _run_row(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item.pop("event_offset", None)
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
