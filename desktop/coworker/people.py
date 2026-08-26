"""Person files — one sqlite db, separate from conversation store."""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any

_SID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

SEQUENCE_STATES = ("open", "in_conversation", "done")
ACTORS = ("director", "assistant")
SOURCES = (
    "apollo",
    "gmail",
    "calendar",
    "drive",
    "web",
    "granola",
    "sourcecado",
)


def _clean(value: str | None) -> str | None:
    return (value or "").strip() or None


def _new_person_id() -> str:
    return "per_" + uuid.uuid4().hex


def _new_event_id() -> str:
    return "evt_" + uuid.uuid4().hex


class PersonStore:
    def __init__(self, base_dir: str | Path) -> None:
        self.base = Path(base_dir).expanduser()
        self.base.mkdir(parents=True, exist_ok=True)
        self.db_path = self.base / "people.db"
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS people (
                person_id TEXT PRIMARY KEY,
                apollo_id TEXT UNIQUE,
                first_name TEXT,
                last_name TEXT,
                title TEXT,
                company TEXT,
                email TEXT,
                linkedin_url TEXT,
                phone TEXT,
                sequence_state TEXT,
                target TEXT,
                handoff_who TEXT,
                handoff_wanted TEXT,
                handoff_happened TEXT,
                handoff_they_want TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY,
                person_id TEXT NOT NULL,
                source TEXT NOT NULL,
                kind TEXT NOT NULL,
                summary TEXT NOT NULL,
                payload TEXT NOT NULL DEFAULT '{}',
                actor TEXT NOT NULL,
                session_id TEXT,
                run_id TEXT,
                tool TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS session_people (
                session_id TEXT PRIMARY KEY,
                person_id TEXT NOT NULL
            );
            """
        )
        self._conn.commit()

    def _person_dict(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return dict(row)

    def keep_from_apollo(
        self,
        *,
        apollo_id: str | None,
        first_name: str | None,
        last_name_obfuscated: str | None,
        title: str | None,
        company: str | None,
        target: str | None = None,
    ) -> dict[str, Any]:
        apollo = _clean(apollo_id)
        first_name = _clean(first_name)
        last_name_obfuscated = _clean(last_name_obfuscated)
        title = _clean(title)
        company = _clean(company)
        cleaned_target = _clean(target)
        with self._lock:
            existing = None
            if apollo is not None:
                existing = self._conn.execute(
                    "SELECT * FROM people WHERE apollo_id = ?",
                    (apollo,),
                ).fetchone()
            if existing is not None:
                self._conn.execute(
                    """
                    UPDATE people SET
                        first_name = COALESCE(?, first_name),
                        last_name = COALESCE(?, last_name),
                        title = COALESCE(?, title),
                        company = COALESCE(?, company),
                        target = COALESCE(?, target),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE person_id = ?
                    """,
                    (
                        first_name,
                        last_name_obfuscated,
                        title,
                        company,
                        cleaned_target,
                        existing["person_id"],
                    ),
                )
                self._conn.commit()
                row = self._conn.execute(
                    "SELECT * FROM people WHERE person_id = ?",
                    (existing["person_id"],),
                ).fetchone()
            else:
                pid = _new_person_id()
                self._conn.execute(
                    """
                    INSERT INTO people (
                        person_id, apollo_id, first_name, last_name, title, company, target
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        pid,
                        apollo,
                        first_name,
                        last_name_obfuscated,
                        title,
                        company,
                        cleaned_target,
                    ),
                )
                self._conn.commit()
                row = self._conn.execute(
                    "SELECT * FROM people WHERE person_id = ?",
                    (pid,),
                ).fetchone()
        person = self._person_dict(row)
        assert person is not None
        return person

    def get_by_apollo_id(self, apollo_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM people WHERE apollo_id = ?",
                (apollo_id,),
            ).fetchone()
        return self._person_dict(row)

    def apply_enrichment(
        self,
        person_id: str,
        *,
        name: str | None = None,
        title: str | None = None,
        company: str | None = None,
        email: str | None = None,
        linkedin_url: str | None = None,
        phone: str | None = None,
    ) -> dict[str, Any] | None:
        first_name = None
        last_name = None
        if name:
            parts = str(name).split(None, 1)
            first_name = parts[0]
            last_name = parts[1] if len(parts) > 1 else None
        with self._lock:
            exists = self._conn.execute(
                "SELECT 1 FROM people WHERE person_id = ?",
                (person_id,),
            ).fetchone()
            if exists is None:
                return None
            self._conn.execute(
                """
                UPDATE people SET
                    first_name = COALESCE(?, first_name),
                    last_name = COALESCE(?, last_name),
                    title = COALESCE(?, title),
                    company = COALESCE(?, company),
                    email = COALESCE(?, email),
                    linkedin_url = COALESCE(?, linkedin_url),
                    phone = COALESCE(?, phone),
                    updated_at = CURRENT_TIMESTAMP
                WHERE person_id = ?
                """,
                (
                    first_name,
                    last_name,
                    title,
                    company,
                    email,
                    linkedin_url,
                    phone,
                    person_id,
                ),
            )
            self._conn.commit()
        return self.get(person_id)

    def get(self, person_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM people WHERE person_id = ?",
                (person_id,),
            ).fetchone()
        return self._person_dict(row)

    def set_sequence(self, person_id: str, state: str, *, actor: str) -> dict[str, Any]:
        if state not in SEQUENCE_STATES:
            raise ValueError(f"invalid sequence state {state}")
        if actor not in ACTORS:
            raise ValueError(f"invalid actor {actor}")
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM people WHERE person_id = ?",
                (person_id,),
            ).fetchone()
            if row is None:
                raise ValueError("unknown person")
            self._conn.execute(
                """
                UPDATE people SET sequence_state = ?, updated_at = CURRENT_TIMESTAMP
                WHERE person_id = ?
                """,
                (state, person_id),
            )
            self._conn.execute(
                """
                INSERT INTO events (
                    event_id, person_id, source, kind, summary, payload, actor
                ) VALUES (?, ?, 'sourcecado', 'state', ?, ?, ?)
                """,
                (
                    _new_event_id(),
                    person_id,
                    f"Moved to {state.replace('_', ' ')}",
                    json.dumps({"state": state}),
                    actor,
                ),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM people WHERE person_id = ?",
                (person_id,),
            ).fetchone()
        person = self._person_dict(row)
        assert person is not None
        return person

    def list_board(self) -> dict[str, list[dict[str, Any]]]:
        board: dict[str, list[dict[str, Any]]] = {state: [] for state in SEQUENCE_STATES}
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM people
                WHERE sequence_state IS NOT NULL
                ORDER BY updated_at DESC, person_id
                """
            ).fetchall()
        for row in rows:
            person = self._person_dict(row)
            assert person is not None
            board[person["sequence_state"]].append(person)
        return board

    def _event_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        raw = item.get("payload") or "{}"
        item["payload"] = json.loads(raw) if isinstance(raw, str) else raw
        return item

    def append_event(
        self,
        person_id: str,
        *,
        source: str,
        kind: str,
        summary: str,
        payload: dict[str, Any] | None = None,
        actor: str = "assistant",
        session_id: str | None = None,
        run_id: str | None = None,
        tool: str | None = None,
    ) -> dict[str, Any]:
        if source not in SOURCES:
            raise ValueError(f"invalid source {source}")
        if actor not in ACTORS:
            raise ValueError(f"invalid actor {actor}")
        if not summary.strip():
            raise ValueError("summary is required")
        with self._lock:
            exists = self._conn.execute(
                "SELECT 1 FROM people WHERE person_id = ?",
                (person_id,),
            ).fetchone()
            if exists is None:
                raise ValueError("unknown person")
            event_id = _new_event_id()
            self._conn.execute(
                """
                INSERT INTO events (
                    event_id, person_id, source, kind, summary, payload,
                    actor, session_id, run_id, tool
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    person_id,
                    source,
                    kind,
                    summary.strip(),
                    json.dumps(payload or {}),
                    actor,
                    session_id,
                    run_id,
                    tool,
                ),
            )
            self._conn.execute(
                "UPDATE people SET updated_at = CURRENT_TIMESTAMP WHERE person_id = ?",
                (person_id,),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM events WHERE event_id = ?",
                (event_id,),
            ).fetchone()
        return self._event_dict(row)

    def timeline(self, person_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM events
                WHERE person_id = ?
                ORDER BY rowid ASC
                """,
                (person_id,),
            ).fetchall()
        return [self._event_dict(row) for row in rows]

    def bind_session(self, session_id: str, person_id: str) -> None:
        sid = (session_id or "").strip()
        if not sid or not _SID_RE.fullmatch(sid) or ".." in sid:
            raise ValueError("invalid session id")
        with self._lock:
            exists = self._conn.execute(
                "SELECT 1 FROM people WHERE person_id = ?",
                (person_id,),
            ).fetchone()
            if exists is None:
                raise ValueError("unknown person")
            self._conn.execute(
                """
                INSERT INTO session_people (session_id, person_id) VALUES (?, ?)
                ON CONFLICT(session_id) DO UPDATE SET person_id = excluded.person_id
                """,
                (sid, person_id),
            )
            self._conn.commit()

    def person_for_session(self, session_id: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT person_id FROM session_people WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        return str(row["person_id"])

    def set_handoff(
        self,
        person_id: str,
        *,
        who: str,
        wanted: str,
        happened: str,
        they_want: str,
    ) -> dict[str, Any]:
        with self._lock:
            exists = self._conn.execute(
                "SELECT 1 FROM people WHERE person_id = ?",
                (person_id,),
            ).fetchone()
            if exists is None:
                raise ValueError("unknown person")
            self._conn.execute(
                """
                UPDATE people SET
                    handoff_who = ?,
                    handoff_wanted = ?,
                    handoff_happened = ?,
                    handoff_they_want = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE person_id = ?
                """,
                (who, wanted, happened, they_want, person_id),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM people WHERE person_id = ?",
                (person_id,),
            ).fetchone()
        person = self._person_dict(row)
        assert person is not None
        return person
