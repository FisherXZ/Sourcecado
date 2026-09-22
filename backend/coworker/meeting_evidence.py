"""Person-scoped, reviewable Calendar and Granola meeting evidence."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from datetime import UTC, datetime
from functools import wraps
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable, Iterable

from coworker.people import PersonStore

PROVIDERS = frozenset({"calendar", "granola"})


def _synchronized(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapped


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _clean(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _email(value: Any) -> str | None:
    text = _clean(value)
    return text.casefold() if text and "@" in text else None


def _name(value: Any) -> str | None:
    text = _clean(value)
    if not text:
        return None
    normalized = " ".join(re.findall(r"[a-z0-9]+", text.casefold()))
    return normalized or None


def _timestamp(value: Any) -> str | None:
    if isinstance(value, dict):
        return _clean(value.get("dateTime") or value.get("date"))
    return _clean(value)


def _participants(rows: Any) -> list[dict[str, str | None]]:
    if not isinstance(rows, list):
        return []
    participants: list[dict[str, str | None]] = []
    seen: set[tuple[str | None, str | None]] = set()
    for raw in rows:
        if isinstance(raw, str):
            email = _email(raw)
            name = None if email else _clean(raw)
        elif isinstance(raw, dict):
            email = _email(raw.get("email"))
            name = _clean(raw.get("displayName") or raw.get("name"))
        else:
            continue
        if not email and not name:
            continue
        key = (email, _name(name))
        if key in seen:
            continue
        seen.add(key)
        participants.append({"name": name, "email": email})
    participants.sort(key=lambda item: (item.get("email") or "", item.get("name") or ""))
    return participants


def _evidence_id(provider: str, provider_id: str) -> str:
    digest = sha256(f"{provider}\0{provider_id}".encode("utf-8")).hexdigest()[:24]
    return f"meeting_{digest}"


def _normalize(provider: str, raw: dict[str, Any]) -> dict[str, Any]:
    provider_id = _clean(raw.get("id") or raw.get("provider_id"))
    if not provider_id:
        raise ValueError("meeting provider id is required")
    if provider == "calendar":
        title = _clean(raw.get("summary") or raw.get("title")) or "Calendar meeting"
        starts_at = _timestamp(raw.get("start"))
        ends_at = _timestamp(raw.get("end"))
        participants = _participants(raw.get("attendees") or raw.get("participants"))
        url = _clean(raw.get("htmlLink") or raw.get("url"))
        notes = None
        provider_label = "Google Calendar"
    else:
        title = _clean(raw.get("title") or raw.get("summary")) or "Granola meeting"
        starts_at = _timestamp(raw.get("startTime") or raw.get("starts_at") or raw.get("start"))
        ends_at = _timestamp(raw.get("endTime") or raw.get("ends_at") or raw.get("end"))
        participants = _participants(raw.get("participants") or raw.get("attendees"))
        url = _clean(raw.get("url") or raw.get("external_url"))
        notes = _clean(raw.get("notes") or raw.get("summaryText") or raw.get("transcript"))
        provider_label = "Granola"
    return {
        "evidence_id": _evidence_id(provider, provider_id),
        "provider": provider,
        "provider_id": provider_id,
        "title": title,
        "starts_at": starts_at,
        "ends_at": ends_at,
        "participants": participants,
        "source_ref": {
            "id": f"{provider}:{provider_id}",
            "title": title,
            "url": url,
            "provider": provider_label,
        },
        "notes": notes,
    }


class MeetingEvidenceStore:
    def __init__(self, base_dir: str | Path, *, people: PersonStore) -> None:
        self.base = Path(base_dir).expanduser()
        self.base.mkdir(parents=True, exist_ok=True)
        os.chmod(self.base, 0o700)
        self.people = people
        self._lock = threading.RLock()
        self.db_path = self.base / "meeting_evidence.db"
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        os.chmod(self.db_path, 0o600)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS meeting_evidence (
                evidence_id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                provider_id TEXT NOT NULL,
                title TEXT NOT NULL,
                starts_at TEXT,
                ends_at TEXT,
                participants_json TEXT NOT NULL,
                source_ref_json TEXT NOT NULL,
                notes TEXT,
                status TEXT NOT NULL,
                person_id TEXT,
                match_reason TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(provider, provider_id)
            );
            CREATE TABLE IF NOT EXISTS meeting_candidates (
                evidence_id TEXT NOT NULL,
                person_id TEXT NOT NULL,
                status TEXT NOT NULL,
                match_reason TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(evidence_id, person_id)
            );
            """
        )
        self._conn.commit()

    def _row(self, row: sqlite3.Row, *, status: str | None = None, reason: str | None = None) -> dict[str, Any]:
        return {
            "evidence_id": str(row["evidence_id"]),
            "provider": str(row["provider"]),
            "provider_id": str(row["provider_id"]),
            "title": str(row["title"]),
            "starts_at": row["starts_at"],
            "ends_at": row["ends_at"],
            "participants": json.loads(str(row["participants_json"])),
            "source_ref": json.loads(str(row["source_ref_json"])),
            "notes": row["notes"],
            "status": status or str(row["status"]),
            "person_id": row["person_id"],
            "match_reason": reason if reason is not None else row["match_reason"],
            "updated_at": str(row["updated_at"]),
        }

    def _get_row(self, evidence_id: str) -> sqlite3.Row:
        row = self._conn.execute(
            "SELECT * FROM meeting_evidence WHERE evidence_id = ?", (evidence_id,)
        ).fetchone()
        if row is None:
            raise ValueError("unknown meeting evidence")
        return row

    def _upsert(self, meeting: dict[str, Any]) -> sqlite3.Row:
        stamp = _now()
        existing = self._conn.execute(
            """
            SELECT * FROM meeting_evidence
            WHERE provider = ? AND provider_id = ?
            """,
            (meeting["provider"], meeting["provider_id"]),
        ).fetchone()
        if existing is None:
            self._conn.execute(
                """
                INSERT INTO meeting_evidence (
                    evidence_id, provider, provider_id, title, starts_at, ends_at,
                    participants_json, source_ref_json, notes, status, person_id,
                    match_reason, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'unmatched', NULL, NULL, ?, ?)
                """,
                (
                    meeting["evidence_id"],
                    meeting["provider"],
                    meeting["provider_id"],
                    meeting["title"],
                    meeting["starts_at"],
                    meeting["ends_at"],
                    json.dumps(meeting["participants"]),
                    json.dumps(meeting["source_ref"]),
                    meeting["notes"],
                    stamp,
                    stamp,
                ),
            )
        else:
            self._conn.execute(
                """
                UPDATE meeting_evidence SET
                    title = ?, starts_at = ?, ends_at = ?, participants_json = ?,
                    source_ref_json = ?, notes = ?, updated_at = ?
                WHERE evidence_id = ?
                """,
                (
                    meeting["title"],
                    meeting["starts_at"],
                    meeting["ends_at"],
                    json.dumps(meeting["participants"]),
                    json.dumps(meeting["source_ref"]),
                    meeting["notes"],
                    stamp,
                    str(existing["evidence_id"]),
                ),
            )
        self._conn.commit()
        return self._get_row(meeting["evidence_id"])

    def _candidate_matches(self, row: sqlite3.Row) -> dict[str, str]:
        participants = json.loads(str(row["participants_json"]))
        participant_emails = {
            _email(item.get("email"))
            for item in participants
            if isinstance(item, dict) and _email(item.get("email"))
        }
        participant_names = {
            _name(item.get("name"))
            for item in participants
            if isinstance(item, dict) and _name(item.get("name"))
        }
        matches: dict[str, str] = {}
        for person in self.people.list_people():
            person_id = str(person["person_id"])
            person_email = _email(person.get("email"))
            person_name = _name(
                " ".join(
                    part
                    for part in (person.get("first_name"), person.get("last_name"))
                    if part
                )
            )
            if person_email and person_email in participant_emails:
                matches[person_id] = "exact_email"
            elif person_name and person_name in participant_names:
                matches[person_id] = "name_only"
        return matches

    def _sync_person_event(self, row: sqlite3.Row) -> None:
        if row["status"] != "attached" or not row["person_id"]:
            return
        meeting = self._row(row)
        provider_label = "Calendar" if meeting["provider"] == "calendar" else "Granola"
        self.people.upsert_external_event(
            str(row["person_id"]),
            external_key=f"meeting:{meeting['evidence_id']}",
            source=meeting["provider"],
            kind="meeting",
            summary=f"{provider_label} meeting: {meeting['title']}",
            payload={
                "evidence_id": meeting["evidence_id"],
                "provider_id": meeting["provider_id"],
                "starts_at": meeting["starts_at"],
                "ends_at": meeting["ends_at"],
                "participants": meeting["participants"],
                "source_ref": meeting["source_ref"],
                "notes_present": bool(meeting["notes"]),
                "notes_excerpt": (
                    str(meeting["notes"])[:1_000] if meeting["notes"] else None
                ),
                "untrusted": True,
                "read_only": True,
            },
            tool=(
                "calendar_list"
                if meeting["provider"] == "calendar"
                else "mcp__granola__list_meetings"
            ),
        )

    def _match(self, row: sqlite3.Row) -> None:
        if row["status"] == "attached":
            self._sync_person_event(row)
            return
        matches = self._candidate_matches(row)
        self._conn.execute(
            "DELETE FROM meeting_candidates WHERE evidence_id = ? AND status = 'proposed'",
            (row["evidence_id"],),
        )
        email_matches = {
            person_id for person_id, reason in matches.items() if reason == "exact_email"
        }
        if len(matches) == 1 and len(email_matches) == 1:
            person_id = next(iter(email_matches))
            self._conn.execute(
                """
                UPDATE meeting_evidence SET
                    status = 'attached', person_id = ?, match_reason = 'exact_email',
                    updated_at = ?
                WHERE evidence_id = ?
                """,
                (person_id, _now(), row["evidence_id"]),
            )
            self._conn.commit()
            self._sync_person_event(self._get_row(str(row["evidence_id"])))
            return
        for person_id, reason in matches.items():
            self._conn.execute(
                """
                INSERT INTO meeting_candidates (
                    evidence_id, person_id, status, match_reason, updated_at
                ) VALUES (?, ?, 'proposed', ?, ?)
                ON CONFLICT(evidence_id, person_id) DO UPDATE SET
                    match_reason = excluded.match_reason,
                    updated_at = excluded.updated_at
                WHERE meeting_candidates.status != 'rejected'
                """,
                (row["evidence_id"], person_id, reason, _now()),
            )
        self._conn.execute(
            """
            UPDATE meeting_evidence SET status = ?, person_id = NULL,
                match_reason = NULL, updated_at = ?
            WHERE evidence_id = ?
            """,
            ("proposed" if matches else "unmatched", _now(), row["evidence_id"]),
        )
        self._conn.commit()

    @_synchronized
    def ingest(self, provider: str, records: Iterable[dict[str, Any]]) -> int:
        if provider not in PROVIDERS:
            raise ValueError(f"unsupported meeting provider: {provider}")
        normalized = [_normalize(provider, record) for record in records]
        for meeting in normalized:
            row = self._upsert(meeting)
            self._match(row)
        return len(normalized)

    def refresh(
        self,
        *,
        calendar_fetch: Callable[[], dict[str, Any]] | None = None,
        granola_fetch: Callable[[], dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        sources: dict[str, dict[str, Any]] = {}
        for provider, fetch, key in (
            ("calendar", calendar_fetch, "events"),
            ("granola", granola_fetch, "meetings"),
        ):
            if fetch is None:
                continue
            try:
                payload = fetch()
                rows = payload.get(key) if isinstance(payload, dict) else None
                if not isinstance(rows, list):
                    raise ValueError("meeting source returned malformed records")
                count = self.ingest(provider, rows)
            except Exception:
                sources[provider] = {"status": "failed", "error": "unavailable"}
            else:
                sources[provider] = {"status": "ok", "records": count}
        return {"sources": sources}

    @_synchronized
    def for_person(self, person_id: str) -> dict[str, list[dict[str, Any]]]:
        if self.people.get(person_id) is None:
            raise ValueError("unknown person")
        attached_rows = self._conn.execute(
            """
            SELECT * FROM meeting_evidence
            WHERE status = 'attached' AND person_id = ?
            ORDER BY starts_at, evidence_id
            """,
            (person_id,),
        ).fetchall()
        candidate_rows = self._conn.execute(
            """
            SELECT e.*, c.status AS candidate_status,
                   c.match_reason AS candidate_reason
            FROM meeting_candidates c
            JOIN meeting_evidence e ON e.evidence_id = c.evidence_id
            WHERE c.person_id = ?
            ORDER BY e.starts_at, e.evidence_id
            """,
            (person_id,),
        ).fetchall()
        return {
            "attached": [self._row(row) for row in attached_rows],
            "proposed": [
                self._row(
                    row,
                    status="proposed",
                    reason=str(row["candidate_reason"]),
                )
                for row in candidate_rows
                if row["candidate_status"] == "proposed"
            ],
            "rejected": [
                self._row(
                    row,
                    status="rejected",
                    reason=str(row["candidate_reason"]),
                )
                for row in candidate_rows
                if row["candidate_status"] == "rejected"
            ],
        }

    @_synchronized
    def attach(self, evidence_id: str, person_id: str) -> dict[str, Any]:
        if self.people.get(person_id) is None:
            raise ValueError("unknown person")
        row = self._get_row(evidence_id)
        candidate = self._conn.execute(
            """
            SELECT 1 FROM meeting_candidates
            WHERE evidence_id = ? AND person_id = ?
            """,
            (evidence_id, person_id),
        ).fetchone()
        if candidate is None and not (
            row["status"] == "attached" and row["person_id"] == person_id
        ):
            raise ValueError("meeting was not proposed for this person")
        self._conn.execute(
            """
            UPDATE meeting_evidence SET status = 'attached', person_id = ?,
                match_reason = 'director_attached', updated_at = ?
            WHERE evidence_id = ?
            """,
            (person_id, _now(), evidence_id),
        )
        self._conn.execute(
            """
            UPDATE meeting_candidates SET
                status = CASE WHEN person_id = ? THEN 'attached' ELSE 'rejected' END,
                updated_at = ?
            WHERE evidence_id = ?
            """,
            (person_id, _now(), evidence_id),
        )
        self._conn.commit()
        attached = self._get_row(evidence_id)
        self._sync_person_event(attached)
        return self._row(attached)

    @_synchronized
    def reject(self, evidence_id: str, person_id: str) -> dict[str, Any]:
        candidate = self._conn.execute(
            """
            SELECT match_reason FROM meeting_candidates
            WHERE evidence_id = ? AND person_id = ?
            """,
            (evidence_id, person_id),
        ).fetchone()
        if candidate is None:
            raise ValueError("meeting was not proposed for this person")
        self._conn.execute(
            """
            UPDATE meeting_candidates SET status = 'rejected', updated_at = ?
            WHERE evidence_id = ? AND person_id = ?
            """,
            (_now(), evidence_id, person_id),
        )
        self._conn.commit()
        return self._row(
            self._get_row(evidence_id),
            status="rejected",
            reason=str(candidate["match_reason"]),
        )
