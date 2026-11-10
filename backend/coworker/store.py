"""Sourcecado conversation state: Postgres index plus append-only JSONL."""

from __future__ import annotations

from coworker.consequential_policy import requires_once

import json
import os
import re
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from coworker import db
from coworker.context_projection import (
    DEFAULT_PROJECTION_POLICY,
    ContextAuthority,
    ContextCategory,
    ContextSensitivity,
    ContextSourceRef,
    ContextState,
    ProjectionItem,
)

_SID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
# Declared version of the transcript log, read by the migration registry in
# coworker.migrations. Reporting only: this module does not stamp or check it.
# The tables are defined by supabase/migrations/20261110000600_club.sql.
TRANSCRIPT_VERSION = 1

# context-projection-v1 (issue #58). A memory row is model context only after
# the director classifies it as a person-independent operator preference.
# Everything else waits for review and is withheld: a new ambiguous `remember`
# write as "unclassified", and a row migrated from before the contract as
# "legacy_unclassified", which coworker/migrations.py writes.
MEMORY_CATEGORY_UNCLASSIFIED = "unclassified"
MEMORY_CATEGORY_PREFERENCE = "operator_preference"
MEMORY_NEEDS_REVIEW = "needs_review"
MEMORY_CLASSIFIED = "classified"
MEMORY_SENSITIVITY_STANDARD = "standard"

_MEMORY_COLUMNS = (
    "id, content, created_at, category, classification_status, person_id, "
    "session_id, source_ref, updated_at, fresh_until, sensitivity, claim_key"
)
_JOB_COLUMNS = (
    "id, cron, prompt, created_at, next_run_at, name, template_id, cadence"
)
# `event_offset` is read here and dropped by `_run_row`.
_RUN_COLUMNS = (
    "id, job_id, status, result, created_at, started_at, finished_at, "
    "duration_ms, summary, artifacts, session_id, waiting_approval_count, "
    "event_offset"
)
_INBOX_COLUMNS = (
    "id, kind, name, arguments, state, decision, actor, requested_at, "
    "resolved_at, scope, execution_status, execution_error, "
    "execution_claimant, execution_result, expires_at, reason, session_id, "
    "run_id, message_id, part_id, recovery_command_id, original_call_id, "
    "resource, created_at"
)
_QUEUE_COLUMNS = (
    "id, session_id, text, position, state, error, created_at, updated_at"
)
# The text SQLite's CURRENT_TIMESTAMP gave: `YYYY-MM-DD HH:MM:SS` in UTC.
_SQL_NOW = "to_char(now() at time zone 'utc', 'YYYY-MM-DD HH24:MI:SS')"


def _query(conn: psycopg.Connection, sql: str, params: tuple[Any, ...]) -> psycopg.Cursor:
    """Run one statement and return a cursor whose rows are dicts."""
    return conn.cursor(row_factory=dict_row).execute(sql, params)

# Read from the approved policy rather than restated, so the adapter clips to
# exactly the cap the projector enforces and the two cannot drift apart.
_MEMORY_ITEM_TOKENS = next(
    budget.max_item_tokens
    for budget in DEFAULT_PROJECTION_POLICY.category_budgets
    if budget.category is ContextCategory.OPERATOR_PREFERENCE
)


def projection_tokens(text: str) -> int:
    """The versioned budget unit for context-projection-v1.

    `ceil(len(utf-8 bytes) / 3)`: provider-independent and conservative. It is
    an explicit budget unit, not a claim about any provider's billed tokens.
    """
    return -(-len(text.encode("utf-8")) // 3)


def _memory_source_ref(memory_id: int) -> str:
    return f"sourcecado:memory/{memory_id}"


def _memory_source_ref_record(row: dict[str, Any]) -> ContextSourceRef:
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


# The longest memory the director can save from the Memory page. At 140
# characters the prompt line still fits the per-item cap whole, even with a
# six-digit id and the longest evidence state, so the model never reads a
# clipped copy of what the director wrote.
MEMORY_MAX_CHARS = 140


def memory_fits_prompt(content: str) -> bool:
    line = _memory_line(999_999, content, ContextState.CONFLICTING.value)
    return projection_tokens(line) <= _MEMORY_ITEM_TOKENS


def _memory_in_use(row: dict[str, Any]) -> bool:
    """The same predicate `memory_projection_items` applies in SQL."""
    return (
        row.get("classification_status") == MEMORY_CLASSIFIED
        and row.get("category") == MEMORY_CATEGORY_PREFERENCE
        and not row.get("person_id")
        and not row.get("session_id")
        and (row.get("sensitivity") or MEMORY_SENSITIVITY_STANDARD)
        == MEMORY_SENSITIVITY_STANDARD
    )


def _clip_memory_line(row: dict[str, Any], state: ContextState) -> tuple[str, bool]:
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
        self._lock = threading.RLock()
        self._db, self._user_id = db.resolve(self.base)
        self._reconcile_orphaned_inbox_executions()
        self._reconcile_orphaned_queue_items()
        self._reconcile_orphaned_runs()
        self._reconcile_orphaned_recovery_commands()
        self._normalize_legacy_run_status()

    def _tx(self):
        """One transaction as this store's user. See docs/engineering/postgres-port.md."""
        return self._db.tx(self._user_id)

    def _normalize_legacy_run_status(self) -> None:
        """Store run_turn's raw "ok" as the shared status "success".

        Scheduler writes from before RECEIPT_STATUSES stored "ok", and
        `record_run` still accepts it. Every consumer (API, UI, exports)
        reads the shared status contract, so the store heals it on open.
        """
        with self._lock, self._tx() as conn:
            conn.execute(
                "UPDATE runs SET status = 'success' WHERE user_id = %s AND status = 'ok'",
                (self._user_id,),
            )

    def _reconcile_orphaned_recovery_commands(self) -> None:
        """Drop in-flight recovery claims from the prior process.

        A claim only serializes concurrent sockets while the work runs; the
        durable duplicate guard is the tool_recovery event checked before
        claiming. A claim whose process died mid-work must not silence the
        command forever.
        """
        with self._lock, self._tx() as conn:
            conn.execute(
                "DELETE FROM recovery_commands WHERE user_id = %s", (self._user_id,)
            )

    def _reconcile_orphaned_inbox_executions(self) -> None:
        """Close claims from the prior process without replaying external writes."""
        result = json.dumps(
            {"status": "interrupted", "error": _INTERRUPTED_APPROVAL_ERROR}
        )
        # One UPDATE statement. It locks and changes each matching row in
        # one step, so no read can fall between the check and the write.
        with self._lock, self._tx() as conn:
            conn.execute(
                """
                UPDATE inbox SET
                    execution_status = 'interrupted',
                    execution_error = %s,
                    execution_claimant = NULL,
                    execution_result = %s
                WHERE user_id = %s AND execution_status = 'executing'
                """,
                (_INTERRUPTED_APPROVAL_ERROR, result, self._user_id),
            )

    def _reconcile_orphaned_queue_items(self) -> None:
        """Make pre-start queue claims recoverable without replaying them."""
        now = datetime.now(UTC).isoformat()
        reason = (
            "Sourcecado restarted before this queued run reported completion. "
            "Review and retry explicitly."
        )
        with self._lock, self._tx() as conn:
            # RETURNING names exactly the rows this UPDATE changed, so the
            # sessions paused below are the sessions whose items moved.
            affected = _query(
                conn,
                """
                UPDATE chat_queue
                SET state = 'interrupted', error = %s, updated_at = %s
                WHERE user_id = %s AND state = 'sending'
                RETURNING session_id
                """,
                (reason, now, self._user_id),
            ).fetchall()
            for session_id in sorted({str(row["session_id"]) for row in affected}):
                conn.execute(
                    """
                    INSERT INTO queue_sessions (user_id, session_id, paused, updated_at)
                    VALUES (%s, %s, 1, %s)
                    ON CONFLICT (user_id, session_id) DO UPDATE SET
                        paused = 1,
                        updated_at = excluded.updated_at
                    """,
                    (self._user_id, session_id, now),
                )

    def _reconcile_orphaned_runs(self) -> None:
        """Close runs the prior process left mid-flight without inventing an outcome.

        A crash can land between this attempt's terminal event and the
        matching runs-table update. Trust only events written after this
        run started: sched-{job_id} files are reused, so last week's
        terminal event is still in the file.
        """
        now = datetime.now(UTC).isoformat()
        with self._lock, self._tx() as conn:
            # FOR UPDATE holds the running rows until commit, so no other
            # writer can finish one between this read and the UPDATE below.
            running = _query(
                conn,
                """
                SELECT id, session_id, event_offset
                FROM runs WHERE user_id = %s AND status = 'running'
                ORDER BY id
                FOR UPDATE
                """,
                (self._user_id,),
            ).fetchall()
            for row in running:
                status, result, summary = self._terminal_transcript_outcome(
                    str(row["session_id"]),
                    int(row["event_offset"] or 0),
                )
                conn.execute(
                    """
                    UPDATE runs SET
                        status = %s,
                        result = %s,
                        summary = %s,
                        finished_at = %s,
                        duration_ms = COALESCE(duration_ms, 0)
                    WHERE user_id = %s AND id = %s
                    """,
                    (status, result, summary, now, self._user_id, row["id"]),
                )

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
        with self._tx() as conn:
            conn.execute(
                f"""
                INSERT INTO sessions
                    (user_id, session_id, title, n_msgs, opened_at, updated_at)
                VALUES (%s, %s, %s, %s, {_SQL_NOW}, {_SQL_NOW})
                ON CONFLICT (user_id, session_id) DO UPDATE SET
                    title = COALESCE(sessions.title, excluded.title),
                    n_msgs = excluded.n_msgs,
                    updated_at = {_SQL_NOW}
                """,
                (self._user_id, sid, title, len(messages)),
            )

    def index(self, sid: str) -> dict[str, Any] | None:
        with self._lock, self._tx() as conn:
            row = _query(
                conn,
                """
                SELECT session_id, title, n_msgs, pinned, opened_at, updated_at
                FROM sessions WHERE user_id = %s AND session_id = %s
                """,
                (self._user_id, sid),
            ).fetchone()
        if row is None:
            return None
        return _session_row(row)

    def list_sessions(self) -> list[dict[str, Any]]:
        with self._lock, self._tx() as conn:
            # `seq` stands in for the SQLite rowid as the last tie-break.
            rows = _query(
                conn,
                """
                SELECT session_id, title, n_msgs, pinned, opened_at, updated_at FROM sessions
                WHERE user_id = %s AND session_id NOT LIKE 'sched-%%'
                ORDER BY COALESCE(opened_at, updated_at) DESC, updated_at DESC, seq DESC
                """,
                (self._user_id,),
            ).fetchall()
        return [_session_row(row) for row in rows]

    def create_session(self, sid: str | None = None) -> dict[str, Any]:
        sid = sid or uuid.uuid4().hex
        if not valid_session_id(sid):
            raise ValueError("invalid session id")
        with self._lock:
            with self._tx() as conn:
                conn.execute(
                    f"""
                    INSERT INTO sessions
                        (user_id, session_id, title, n_msgs, opened_at, updated_at)
                    VALUES (%s, %s, NULL, 0, {_SQL_NOW}, {_SQL_NOW})
                    ON CONFLICT DO NOTHING
                    """,
                    (self._user_id, sid),
                )
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
        with self._lock, self._tx() as conn:
            cursor = conn.execute(
                "UPDATE sessions SET title = %s WHERE user_id = %s AND session_id = %s",
                (cleaned, self._user_id, sid),
            )
            if cursor.rowcount == 0:
                return None
        return self.index(sid)

    def set_session_pinned(self, sid: str, pinned: bool) -> dict[str, Any] | None:
        with self._lock, self._tx() as conn:
            cursor = conn.execute(
                "UPDATE sessions SET pinned = %s WHERE user_id = %s AND session_id = %s",
                (int(pinned), self._user_id, sid),
            )
            if cursor.rowcount == 0:
                return None
        return self.index(sid)

    def open_session_id(self) -> str | None:
        return self.get_setting("open_session_id")

    def set_open_session(self, sid: str) -> None:
        with self._lock, self._tx() as conn:
            conn.execute(
                "UPDATE sessions SET opened_at = %s WHERE user_id = %s AND session_id = %s",
                (datetime.now(UTC).isoformat(), self._user_id, sid),
            )
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
        with self._lock, self._tx() as conn:
            memory_id = _query(
                conn,
                """
                INSERT INTO memories
                    (user_id, content, category, classification_status, person_id,
                     session_id, updated_at, fresh_until, sensitivity)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    self._user_id,
                    content,
                    MEMORY_CATEGORY_UNCLASSIFIED,
                    MEMORY_NEEDS_REVIEW,
                    person_id,
                    session_id,
                    now,
                    fresh_until,
                    sensitivity,
                ),
            ).fetchone()["id"]
            row = _query(
                conn,
                f"""
                UPDATE memories SET source_ref = %s
                WHERE user_id = %s AND id = %s
                RETURNING {_MEMORY_COLUMNS}
                """,
                (_memory_source_ref(memory_id), self._user_id, memory_id),
            ).fetchone()
        item = dict(row)
        self._write_memory_md(item)
        self._write_memory_index()
        return item

    def list_memories(self) -> list[dict[str, Any]]:
        with self._lock, self._tx() as conn:
            rows = _query(
                conn,
                f"SELECT {_MEMORY_COLUMNS} FROM memories WHERE user_id = %s ORDER BY id",
                (self._user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def memory_update(self, memory_id: int, content: str) -> dict[str, Any] | None:
        """Rewrite a memory's content, which sends it back for review.

        Rewritten text is text the director has not classified. Keeping the old
        classification would let a rewrite change model context without anyone
        approving what it now says.
        """
        with self._lock, self._tx() as conn:
            row = _query(
                conn,
                f"""
                UPDATE memories SET
                    content = %s,
                    category = %s,
                    classification_status = %s,
                    updated_at = %s
                WHERE user_id = %s AND id = %s
                RETURNING {_MEMORY_COLUMNS}
                """,
                (
                    content,
                    MEMORY_CATEGORY_UNCLASSIFIED,
                    MEMORY_NEEDS_REVIEW,
                    datetime.now(UTC).isoformat(),
                    self._user_id,
                    memory_id,
                ),
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
        with self._lock, self._tx() as conn:
            row = _query(
                conn,
                f"SELECT {_MEMORY_COLUMNS} FROM memories WHERE user_id = %s AND id = %s",
                (self._user_id, memory_id),
            ).fetchone()
            if row is None:
                return None
            if row["person_id"] or row["session_id"]:
                raise ValueError(
                    "a Person- or session-scoped memory cannot become a global "
                    "operator preference; file it on the Person File instead"
                )
            updated = _query(
                conn,
                f"""
                UPDATE memories SET
                    category = %s,
                    classification_status = %s,
                    claim_key = %s,
                    source_ref = %s,
                    updated_at = %s
                WHERE user_id = %s AND id = %s
                RETURNING {_MEMORY_COLUMNS}
                """,
                (
                    MEMORY_CATEGORY_PREFERENCE,
                    MEMORY_CLASSIFIED,
                    claim_key,
                    _memory_source_ref(memory_id),
                    datetime.now(UTC).isoformat(),
                    self._user_id,
                    memory_id,
                ),
            ).fetchone()
        return dict(updated)

    def memory_backlog(self) -> dict[str, Any]:
        """What the director still has to classify, and what is already live."""
        rows = self.list_memories()
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

    def memory_page(self) -> dict[str, Any]:
        """Every memory row for the Memory page, newest first.

        `in_use` says whether the row reaches the prompt; a row waiting for
        review is shown with it, so nothing Sourcecado holds is hidden.
        """
        items = [
            {**row, "in_use": _memory_in_use(row)}
            for row in reversed(self.list_memories())
        ]
        return {"items": items, "max_chars": MEMORY_MAX_CHARS}

    def memory_projection_items(
        self, *, now: datetime | None = None
    ) -> tuple[ProjectionItem, ...]:
        """The classified operator preferences, as already-scoped projection items.

        Eligibility is decided in SQL before any ranking: unclassified rows,
        Person- or session-scoped rows, and restricted rows never leave this
        method, so they cannot reach a prompt.
        """
        moment = now or datetime.now(UTC)
        with self._lock, self._tx() as conn:
            rows = _query(
                conn,
                f"""
                SELECT {_MEMORY_COLUMNS} FROM memories
                WHERE user_id = %s
                  AND classification_status = %s
                  AND category = %s
                  AND person_id IS NULL
                  AND session_id IS NULL
                  AND COALESCE(sensitivity, %s) = %s
                ORDER BY id
                """,
                (
                    self._user_id,
                    MEMORY_CLASSIFIED,
                    MEMORY_CATEGORY_PREFERENCE,
                    MEMORY_SENSITIVITY_STANDARD,
                    MEMORY_SENSITIVITY_STANDARD,
                ),
            ).fetchall()
        contested: dict[str, list[dict[str, Any]]] = {}
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
        with self._lock, self._tx() as conn:
            row = _query(
                conn,
                f"""
                INSERT INTO jobs
                    (user_id, cron, prompt, next_run_at, name, template_id, cadence)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING {_JOB_COLUMNS}
                """,
                (
                    self._user_id,
                    cron,
                    prompt,
                    next_run_at,
                    name or prompt,
                    template_id,
                    cadence or cron,
                ),
            ).fetchone()
        return dict(row)

    def record_run(self, job_id: int, status: str, result: str = "") -> dict[str, Any]:
        stamp = datetime.now(UTC).isoformat()
        with self._lock, self._tx() as conn:
            row = _query(
                conn,
                f"""
                INSERT INTO runs
                    (user_id, job_id, status, result, started_at, finished_at,
                     duration_ms, summary, artifacts, session_id,
                     waiting_approval_count)
                VALUES (%s, %s, %s, %s, %s, %s, 0, %s, '[]', %s, 0)
                RETURNING {_RUN_COLUMNS}
                """,
                (
                    self._user_id,
                    job_id,
                    status,
                    result,
                    stamp,
                    stamp,
                    result,
                    f"sched-{job_id}",
                ),
            ).fetchone()
        return _run_row(row)

    def start_run(
        self, job_id: int, *, session_id: str, started_at: str
    ) -> dict[str, Any]:
        with self._lock:
            event_offset = len(self.load_events(session_id))
            with self._tx() as conn:
                row = _query(
                    conn,
                    f"""
                    INSERT INTO runs
                        (user_id, job_id, status, result, started_at, finished_at,
                         duration_ms, summary, artifacts, session_id,
                         waiting_approval_count, event_offset)
                    VALUES (%s, %s, 'running', NULL, %s, NULL, NULL, '', '[]', %s, 0, %s)
                    RETURNING {_RUN_COLUMNS}
                    """,
                    (self._user_id, job_id, started_at, session_id, event_offset),
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
        with self._lock, self._tx() as conn:
            conn.execute(
                """
                UPDATE runs SET
                    status = %s, result = %s, summary = %s, artifacts = %s,
                    duration_ms = %s, finished_at = %s,
                    waiting_approval_count = %s
                WHERE user_id = %s AND id = %s
                """,
                (
                    status,
                    result,
                    summary,
                    json.dumps(artifacts),
                    max(0, duration_ms),
                    finished_at,
                    max(0, waiting_approval_count),
                    self._user_id,
                    run_id,
                ),
            )
            row = _query(
                conn,
                f"SELECT {_RUN_COLUMNS} FROM runs WHERE user_id = %s AND id = %s",
                (self._user_id, run_id),
            ).fetchone()
        return _run_row(row)

    def list_schedule(self) -> dict[str, list[dict[str, Any]]]:
        with self._lock, self._tx() as conn:
            jobs = _query(
                conn,
                f"SELECT {_JOB_COLUMNS} FROM jobs WHERE user_id = %s ORDER BY id",
                (self._user_id,),
            ).fetchall()
            runs = _query(
                conn,
                f"SELECT {_RUN_COLUMNS} FROM runs WHERE user_id = %s ORDER BY id",
                (self._user_id,),
            ).fetchall()
        return {
            "jobs": [dict(row) for row in jobs],
            "runs": [_run_row(row) for row in runs],
        }

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self._lock, self._tx() as conn:
            row = _query(
                conn,
                "SELECT value FROM settings WHERE user_id = %s AND key = %s",
                (self._user_id, key),
            ).fetchone()
        if row is None:
            return default
        return str(row["value"])

    def set_setting(self, key: str, value: str) -> None:
        with self._lock, self._tx() as conn:
            conn.execute(
                """
                INSERT INTO settings (user_id, key, value) VALUES (%s, %s, %s)
                ON CONFLICT (user_id, key) DO UPDATE SET value = excluded.value
                """,
                (self._user_id, key, value),
            )

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
        with self._lock, self._tx() as conn:
            row = _query(
                conn,
                f"""
                INSERT INTO inbox
                    (user_id, id, kind, name, arguments, state, requested_at, scope,
                     execution_status, expires_at, reason, session_id, run_id,
                     message_id, part_id, recovery_command_id, original_call_id,
                     resource)
                VALUES (%s, %s, %s, %s, %s, 'pending', %s, 'once', 'pending',
                        %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING {_INBOX_COLUMNS}
                """,
                (
                    self._user_id,
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
            ).fetchone()
        return _inbox_row(row)

    def _inbox_item(
        self, conn: psycopg.Connection, item_id: str, *, lock: bool = False
    ) -> dict[str, Any] | None:
        """One inbox row of this user. `lock` holds the row until commit."""
        return _query(
            conn,
            f"SELECT {_INBOX_COLUMNS} FROM inbox WHERE user_id = %s AND id = %s"
            + (" FOR UPDATE" if lock else ""),
            (self._user_id, item_id),
        ).fetchone()

    def reap_overdue_inbox(self, now: str | None = None) -> list[dict[str, Any]]:
        """TTL reaper, two distinct sweeps.

        A PENDING approval past its TTL becomes 'expired' (nobody decided in
        time) — never a denial: decision stays NULL and execution_status is
        'expired', not 'not_run'. A stale EXECUTING claim past its TTL becomes
        'interrupted' (authorized, outcome unknown) — the same semantics the
        restart reconciler uses, never 'expired'.
        """
        stamp = now or datetime.now(UTC).isoformat()
        with self._lock, self._tx() as conn:
            overdue = _query(
                conn,
                """
                SELECT id FROM inbox
                WHERE user_id = %s
                  AND expires_at IS NOT NULL AND expires_at <= %s
                  AND (state = 'pending' OR execution_status = 'executing')
                """,
                (self._user_id, stamp),
            ).fetchall()
            if not overdue:
                return []
            # Each UPDATE checks the state again on the row it locks, so a
            # decision that commits first is never overwritten here.
            conn.execute(
                """
                UPDATE inbox SET
                    execution_status = 'interrupted',
                    execution_claimant = NULL,
                    execution_error = %s,
                    execution_result = %s
                WHERE user_id = %s
                  AND expires_at IS NOT NULL AND expires_at <= %s
                  AND execution_status = 'executing'
                """,
                (
                    _STALE_EXECUTION_ERROR,
                    json.dumps(
                        {"status": "interrupted", "error": _STALE_EXECUTION_ERROR}
                    ),
                    self._user_id,
                    stamp,
                ),
            )
            conn.execute(
                """
                UPDATE inbox SET
                    state = 'expired',
                    resolved_at = %s,
                    execution_status = 'expired',
                    execution_claimant = NULL,
                    execution_error = %s,
                    execution_result = %s
                WHERE user_id = %s
                  AND expires_at IS NOT NULL AND expires_at <= %s
                  AND state = 'pending'
                """,
                (
                    stamp,
                    _EXPIRED_PENDING_ERROR,
                    json.dumps({"status": "expired", "error": _EXPIRED_PENDING_ERROR}),
                    self._user_id,
                    stamp,
                ),
            )
            rows = [self._inbox_item(conn, str(row["id"])) for row in overdue]
        return [_inbox_row(row) for row in rows if row is not None]

    def list_inbox(self, *, pending_only: bool = True) -> list[dict[str, Any]]:
        self.reap_overdue_inbox()
        query = f"SELECT {_INBOX_COLUMNS} FROM inbox WHERE user_id = %s"
        if pending_only:
            query += " AND state = 'pending'"
        # `seq` stands in for the SQLite rowid as the tie-break.
        query += " ORDER BY created_at, seq"
        with self._lock, self._tx() as conn:
            rows = _query(conn, query, (self._user_id,)).fetchall()
        return [_inbox_row(row) for row in rows]

    def get_inbox(self, item_id: str) -> dict[str, Any] | None:
        self.reap_overdue_inbox()
        with self._lock, self._tx() as conn:
            row = self._inbox_item(conn, item_id)
        return None if row is None else _inbox_row(row)

    # The four methods below decide an inbox item. Each reads the row with
    # FOR UPDATE inside one transaction. A second caller on another
    # connection waits on that row lock until the first commits, then reads
    # the decided row. So the first resolution wins, and the second sees it.

    def resolve_inbox(
        self,
        item_id: str,
        decision: str,
        *,
        actor: str | None = None,
        scope: str = "once",
    ) -> dict[str, Any] | None:
        with self._lock, self._tx() as conn:
            row = self._inbox_item(conn, item_id, lock=True)
            if row is None or row["state"] != "pending":
                return None
            if requires_once(row["name"]) and scope != "once":
                raise ValueError("Sending and paid enrichment require a once-only action decision.")
            row = _query(
                conn,
                f"""
                UPDATE inbox SET
                    state = 'resolved',
                    decision = %s,
                    actor = %s,
                    resolved_at = %s,
                    scope = %s
                WHERE user_id = %s AND id = %s
                RETURNING {_INBOX_COLUMNS}
                """,
                (
                    decision,
                    actor,
                    datetime.now(UTC).isoformat(),
                    scope or "once",
                    self._user_id,
                    item_id,
                ),
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
        with self._lock, self._tx() as conn:
            row = self._inbox_item(conn, item_id, lock=True)
            if row is None:
                return None
            if requires_once(row["name"]) and scope != "once":
                raise ValueError("Sending and paid enrichment require a once-only action decision.")
            decision_recorded = False
            if row["state"] == "pending":
                conn.execute(
                    """
                    UPDATE inbox SET
                        state = 'resolved',
                        decision = %s,
                        actor = %s,
                        resolved_at = %s,
                        scope = %s
                    WHERE user_id = %s AND id = %s AND state = 'pending'
                    """,
                    (
                        decision,
                        actor,
                        datetime.now(UTC).isoformat(),
                        scope or "once",
                        self._user_id,
                        item_id,
                    ),
                )
                decision_recorded = True
            elif row["state"] != "resolved" or row["decision"] != decision:
                return None

            row = self._inbox_item(conn, item_id)
            claimed = False
            execution_status = str(row["execution_status"] or "pending")
            if decision == "allow" and execution_status == "pending":
                cursor = conn.execute(
                    """
                    UPDATE inbox SET
                        execution_status = 'executing',
                        execution_claimant = %s
                    WHERE user_id = %s AND id = %s
                      AND COALESCE(execution_status, 'pending') = 'pending'
                    """,
                    (claimant, self._user_id, item_id),
                )
                claimed = cursor.rowcount == 1
            elif decision == "deny" and execution_status == "pending":
                conn.execute(
                    """
                    UPDATE inbox SET
                        execution_status = 'not_run',
                        execution_error = NULL,
                        execution_claimant = NULL,
                        execution_result = %s
                    WHERE user_id = %s AND id = %s
                      AND COALESCE(execution_status, 'pending') = 'pending'
                    """,
                    (json.dumps({"error": "denied by user"}), self._user_id, item_id),
                )
            row = self._inbox_item(conn, item_id)
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
        with self._lock, self._tx() as conn:
            row = self._inbox_item(conn, item_id, lock=True)
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
            conn.execute(
                """
                UPDATE inbox SET
                    execution_status = %s,
                    execution_error = %s,
                    execution_result = %s
                WHERE user_id = %s AND id = %s
                  AND execution_status = 'executing'
                  AND execution_claimant = %s
                """,
                (
                    "succeeded" if ok else "failed",
                    error,
                    json.dumps(result, ensure_ascii=False),
                    self._user_id,
                    item_id,
                    claimant,
                ),
            )
            row = self._inbox_item(conn, item_id)
        return None if row is None else _inbox_row(row)

    def cancel_inbox(self, item_id: str) -> dict[str, Any] | None:
        with self._lock, self._tx() as conn:
            row = self._inbox_item(conn, item_id, lock=True)
            if row is None or row["state"] != "pending":
                return None
            row = _query(
                conn,
                f"""
                UPDATE inbox SET
                    state = 'cancelled',
                    decision = NULL,
                    resolved_at = %s,
                    execution_status = 'cancelled'
                WHERE user_id = %s AND id = %s
                RETURNING {_INBOX_COLUMNS}
                """,
                (datetime.now(UTC).isoformat(), self._user_id, item_id),
            ).fetchone()
        return _inbox_row(row)

    def _lock_queue(self, conn: psycopg.Connection, session_id: str) -> None:
        """Serialize writers of one session's queue until this transaction ends.

        SQLite ran these methods one at a time under `self._lock`. Postgres
        gives each caller its own connection, so a transaction-scoped
        advisory lock on (user, session) keeps the same order across
        connections: one claim, one renumbering, one command at a time.
        """
        conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"chat_queue:{self._user_id}:{session_id}",),
        )

    def _queue_items(
        self, conn: psycopg.Connection, session_id: str
    ) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in _query(
                conn,
                f"""
                SELECT {_QUEUE_COLUMNS}
                FROM chat_queue
                WHERE user_id = %s AND session_id = %s
                ORDER BY position, created_at, id
                """,
                (self._user_id, session_id),
            ).fetchall()
        ]

    def _renumber_queue(
        self, conn: psycopg.Connection, session_id: str, *, now: str | None
    ) -> None:
        """Give the session's items positions 0..n-1 in their current order."""
        for position, item in enumerate(self._queue_items(conn, session_id)):
            if now is None:
                conn.execute(
                    "UPDATE chat_queue SET position = %s "
                    "WHERE user_id = %s AND session_id = %s AND id = %s",
                    (position, self._user_id, session_id, str(item["id"])),
                )
            else:
                conn.execute(
                    "UPDATE chat_queue SET position = %s, updated_at = %s "
                    "WHERE user_id = %s AND session_id = %s AND id = %s",
                    (position, now, self._user_id, session_id, str(item["id"])),
                )

    def _queue_paused(self, conn: psycopg.Connection, session_id: str) -> bool:
        row = _query(
            conn,
            "SELECT paused FROM queue_sessions WHERE user_id = %s AND session_id = %s",
            (self._user_id, session_id),
        ).fetchone()
        return bool(row["paused"]) if row is not None else False

    def list_queue(self, session_id: str) -> list[dict[str, Any]]:
        with self._lock, self._tx() as conn:
            return self._queue_items(conn, session_id)

    def queue_paused(self, session_id: str) -> bool:
        with self._lock, self._tx() as conn:
            return self._queue_paused(conn, session_id)

    def set_queue_paused(self, session_id: str, paused: bool) -> None:
        if not valid_session_id(session_id):
            raise ValueError("invalid session id")
        now = datetime.now(UTC).isoformat()
        with self._lock, self._tx() as conn:
            conn.execute(
                """
                INSERT INTO queue_sessions (user_id, session_id, paused, updated_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (user_id, session_id) DO UPDATE SET
                    paused = excluded.paused,
                    updated_at = excluded.updated_at
                """,
                (self._user_id, session_id, int(paused), now),
            )

    def sessions_with_queue(self) -> list[str]:
        with self._lock, self._tx() as conn:
            rows = _query(
                conn,
                "SELECT DISTINCT session_id FROM chat_queue WHERE user_id = %s "
                "ORDER BY session_id",
                (self._user_id,),
            ).fetchall()
        return [str(row["session_id"]) for row in rows]

    def mark_queue_offline(self, session_id: str) -> int:
        """Park undelivered items when the socket driving their drain is gone."""
        now = datetime.now(UTC).isoformat()
        with self._lock, self._tx() as conn:
            cursor = conn.execute(
                """
                UPDATE chat_queue SET state = 'offline', error = NULL, updated_at = %s
                WHERE user_id = %s AND session_id = %s
                  AND state IN ('waiting', 'retrying')
                """,
                (now, self._user_id, session_id),
            )
        return cursor.rowcount

    def claim_next_queue(self, session_id: str) -> dict[str, Any] | None:
        with self._lock, self._tx() as conn:
            self._lock_queue(conn, session_id)
            if self._queue_paused(conn, session_id):
                return None
            sending = _query(
                conn,
                "SELECT 1 FROM chat_queue WHERE user_id = %s AND session_id = %s "
                "AND state = 'sending' LIMIT 1",
                (self._user_id, session_id),
            ).fetchone()
            if sending is not None:
                return None
            row = _query(
                conn,
                """
                SELECT id FROM chat_queue
                WHERE user_id = %s AND session_id = %s
                  AND state IN ('waiting', 'retrying', 'reconnecting')
                ORDER BY position, created_at, id
                LIMIT 1
                """,
                (self._user_id, session_id),
            ).fetchone()
            if row is None:
                return None
            now = datetime.now(UTC).isoformat()
            claimed = _query(
                conn,
                f"""
                UPDATE chat_queue SET state = 'sending', error = NULL, updated_at = %s
                WHERE user_id = %s AND session_id = %s AND id = %s
                RETURNING {_QUEUE_COLUMNS}
                """,
                (now, self._user_id, session_id, str(row["id"])),
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
        with self._lock, self._tx() as conn:
            self._lock_queue(conn, session_id)
            if state == "complete":
                conn.execute(
                    "DELETE FROM chat_queue WHERE user_id = %s AND session_id = %s AND id = %s",
                    (self._user_id, session_id, item_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE chat_queue SET state = %s, error = %s, updated_at = %s
                    WHERE user_id = %s AND session_id = %s AND id = %s
                    """,
                    (state, error, now, self._user_id, session_id, item_id),
                )
            self._renumber_queue(conn, session_id, now=None)

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
        with self._lock, self._tx() as conn:
            cursor = conn.execute(
                """
                INSERT INTO recovery_commands
                    (user_id, session_id, command_id, created_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (self._user_id, session_id, command_id, now),
            )
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
        with self._lock, self._tx() as conn:
            self._lock_queue(conn, session_id)
            existing = _query(
                conn,
                "SELECT acknowledgement FROM queue_commands "
                "WHERE user_id = %s AND session_id = %s AND command_id = %s",
                (self._user_id, session_id, command_id),
            ).fetchone()
            if existing is not None:
                return json.loads(str(existing["acknowledgement"]))

            status = "rejected"
            if command_type == "queue_add":
                text = str(command.get("text") or "").strip()
                if not item_id or not text:
                    raise ValueError("queue_add requires item_id and text")
                position_row = _query(
                    conn,
                    "SELECT COALESCE(MAX(position), -1) + 1 AS position FROM chat_queue "
                    "WHERE user_id = %s AND session_id = %s",
                    (self._user_id, session_id),
                ).fetchone()
                position = int(position_row["position"])
                now = datetime.now(UTC).isoformat()
                conn.execute(
                    """
                    INSERT INTO chat_queue
                        (user_id, session_id, id, text, position, state, error,
                         created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, 'waiting', NULL, %s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    (self._user_id, session_id, item_id, text, position, now, now),
                )
                status = "accepted"
            elif command_type == "queue_edit":
                text = str(command.get("text") or "").strip()
                if not item_id or not text:
                    raise ValueError("queue_edit requires item_id and text")
                now = datetime.now(UTC).isoformat()
                cursor = conn.execute(
                    """
                    UPDATE chat_queue
                    SET text = %s, updated_at = %s
                    WHERE user_id = %s AND session_id = %s AND id = %s
                      AND state != 'sending'
                    """,
                    (text, now, self._user_id, session_id, item_id),
                )
                status = "accepted" if cursor.rowcount else "rejected"
            elif command_type == "queue_move":
                if not item_id:
                    raise ValueError("queue_move requires item_id")
                ordered = [
                    str(row["id"]) for row in self._queue_items(conn, session_id)
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
                        conn.execute(
                            "UPDATE chat_queue SET position = %s, updated_at = %s "
                            "WHERE user_id = %s AND session_id = %s AND id = %s",
                            (position, now, self._user_id, session_id, ordered_id),
                        )
                    status = "accepted"
            elif command_type == "queue_remove":
                if not item_id:
                    raise ValueError("queue_remove requires item_id")
                cursor = conn.execute(
                    "DELETE FROM chat_queue WHERE user_id = %s AND session_id = %s "
                    "AND id = %s AND state != 'sending'",
                    (self._user_id, session_id, item_id),
                )
                if cursor.rowcount:
                    self._renumber_queue(
                        conn, session_id, now=datetime.now(UTC).isoformat()
                    )
                    status = "accepted"
            elif command_type == "queue_resume":
                now = datetime.now(UTC).isoformat()
                conn.execute(
                    """
                    INSERT INTO queue_sessions (user_id, session_id, paused, updated_at)
                    VALUES (%s, %s, 0, %s)
                    ON CONFLICT (user_id, session_id) DO UPDATE SET
                        paused = 0,
                        updated_at = excluded.updated_at
                    """,
                    (self._user_id, session_id, now),
                )
                # Items parked by a socket loss come back as reconnecting,
                # which the drain claims like waiting.
                conn.execute(
                    """
                    UPDATE chat_queue
                    SET state = 'reconnecting', error = NULL, updated_at = %s
                    WHERE user_id = %s AND session_id = %s AND state = 'offline'
                    """,
                    (now, self._user_id, session_id),
                )
                status = "accepted"
            elif command_type == "queue_retry":
                if not item_id:
                    raise ValueError("queue_retry requires item_id")
                now = datetime.now(UTC).isoformat()
                cursor = conn.execute(
                    """
                    UPDATE chat_queue
                    SET state = 'waiting', error = NULL, updated_at = %s
                    WHERE user_id = %s AND session_id = %s AND id = %s
                      AND state IN ('failed', 'interrupted', 'offline', 'reconnecting')
                    """,
                    (now, self._user_id, session_id, item_id),
                )
                status = "accepted" if cursor.rowcount else "rejected"

            acknowledgement = {
                "version": 2,
                "type": "queue_snapshot",
                "session_id": session_id,
                "command_id": command_id,
                "status": status,
                "paused": self._queue_paused(conn, session_id),
                "items": self._queue_items(conn, session_id),
            }
            now = datetime.now(UTC).isoformat()
            conn.execute(
                """
                INSERT INTO queue_commands
                    (user_id, session_id, command_id, command_type, item_id,
                     acknowledgement, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    self._user_id,
                    session_id,
                    command_id,
                    command_type,
                    item_id or None,
                    json.dumps(acknowledgement),
                    now,
                ),
            )
        return acknowledgement

    def get_job(self, job_id: int) -> dict[str, Any] | None:
        with self._lock, self._tx() as conn:
            row = _query(
                conn,
                f"SELECT {_JOB_COLUMNS} FROM jobs WHERE user_id = %s AND id = %s",
                (self._user_id, job_id),
            ).fetchone()
        return None if row is None else dict(row)

    def due_jobs(self, now_iso: str) -> list[dict[str, Any]]:
        with self._lock, self._tx() as conn:
            rows = _query(
                conn,
                f"""
                SELECT {_JOB_COLUMNS}
                FROM jobs
                WHERE user_id = %s AND next_run_at IS NOT NULL AND next_run_at <= %s
                ORDER BY id
                """,
                (self._user_id, now_iso),
            ).fetchall()
        return [dict(row) for row in rows]

    def set_job_next_run(self, job_id: int, next_run_at: str) -> None:
        with self._lock, self._tx() as conn:
            conn.execute(
                "UPDATE jobs SET next_run_at = %s WHERE user_id = %s AND id = %s",
                (next_run_at, self._user_id, job_id),
            )

    def update_job(
        self,
        job_id: int,
        *,
        name: str,
        prompt: str,
        cadence: str,
        cron: str,
        next_run_at: str | None,
    ) -> dict[str, Any] | None:
        """Replace a job's editable fields. A None next run pauses the job."""
        with self._lock, self._tx() as conn:
            conn.execute(
                """
                UPDATE jobs SET name = %s, prompt = %s, cadence = %s, cron = %s,
                                next_run_at = %s
                WHERE user_id = %s AND id = %s
                """,
                (name, prompt, cadence, cron, next_run_at, self._user_id, job_id),
            )
        return self.get_job(job_id)

    def memory_forget(self, memory_id: int) -> bool:
        with self._lock, self._tx() as conn:
            cursor = conn.execute(
                "DELETE FROM memories WHERE user_id = %s AND id = %s",
                (self._user_id, memory_id),
            )
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


def _inbox_row(row: dict[str, Any]) -> dict[str, Any]:
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


def _run_row(row: dict[str, Any]) -> dict[str, Any]:
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


def _session_row(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    item["pinned"] = bool(item.get("pinned"))
    return item
