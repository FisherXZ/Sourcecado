"""Person-scoped, reviewable Calendar and Granola meeting evidence."""

from __future__ import annotations

import json
import re
from contextlib import contextmanager
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

import psycopg
from psycopg.rows import dict_row

from coworker import db
from coworker.people import PersonStore

PROVIDERS = frozenset({"calendar", "granola"})


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
        self.people = people
        self._db, self._user_id = db.resolve(self.base)

    @contextmanager
    def _tx(self) -> Iterator[psycopg.Cursor[dict[str, Any]]]:
        """One transaction for this store's user. Rows come back as dicts."""
        with self._db.tx(self._user_id) as conn:
            yield conn.cursor(row_factory=dict_row)

    def _row(self, row: dict[str, Any], *, status: str | None = None, reason: str | None = None) -> dict[str, Any]:
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

    def _get_row(
        self, cur: psycopg.Cursor[dict[str, Any]], evidence_id: str, *, lock: bool = False
    ) -> dict[str, Any]:
        row = cur.execute(
            "SELECT * FROM meeting_evidence WHERE user_id = %s AND evidence_id = %s"
            + (" FOR UPDATE" if lock else ""),
            (self._user_id, evidence_id),
        ).fetchone()
        if row is None:
            raise ValueError("unknown meeting evidence")
        return row

    def _upsert(self, meeting: dict[str, Any]) -> dict[str, Any]:
        stamp = _now()
        with self._tx() as cur:
            return cur.execute(
                """
                INSERT INTO meeting_evidence (
                    user_id, evidence_id, provider, provider_id, title, starts_at,
                    ends_at, participants_json, source_ref_json, notes, status,
                    person_id, match_reason, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'unmatched',
                          NULL, NULL, %s, %s)
                ON CONFLICT (user_id, provider, provider_id) DO UPDATE SET
                    title = excluded.title,
                    starts_at = excluded.starts_at,
                    ends_at = excluded.ends_at,
                    participants_json = excluded.participants_json,
                    source_ref_json = excluded.source_ref_json,
                    notes = excluded.notes,
                    updated_at = excluded.updated_at
                RETURNING *
                """,
                (
                    self._user_id,
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
            ).fetchone()
    def _candidate_matches(self, row: dict[str, Any]) -> dict[str, str]:
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

    def _sync_person_event(self, row: dict[str, Any]) -> None:
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

    def _match(self, row: dict[str, Any]) -> None:
        if row["status"] == "attached":
            self._sync_person_event(row)
            return
        # Read the people store before the transaction opens, so no row lock
        # is held while another store works.
        matches = self._candidate_matches(row)
        email_matches = {
            person_id for person_id, reason in matches.items() if reason == "exact_email"
        }
        evidence_id = str(row["evidence_id"])
        with self._tx() as cur:
            # The row lock stands in for the SQLite store's process lock: an
            # attach cannot land between this check and the writes below.
            current = self._get_row(cur, evidence_id, lock=True)
            if current["status"] == "attached":
                attached = current
            elif len(matches) == 1 and len(email_matches) == 1:
                cur.execute(
                    """
                    DELETE FROM meeting_candidates
                    WHERE user_id = %s AND evidence_id = %s AND status = 'proposed'
                    """,
                    (self._user_id, evidence_id),
                )
                cur.execute(
                    """
                    UPDATE meeting_evidence SET
                        status = 'attached', person_id = %s,
                        match_reason = 'exact_email', updated_at = %s
                    WHERE user_id = %s AND evidence_id = %s
                    """,
                    (next(iter(email_matches)), _now(), self._user_id, evidence_id),
                )
                attached = self._get_row(cur, evidence_id)
            else:
                attached = None
                cur.execute(
                    """
                    DELETE FROM meeting_candidates
                    WHERE user_id = %s AND evidence_id = %s AND status = 'proposed'
                    """,
                    (self._user_id, evidence_id),
                )
                for person_id, reason in matches.items():
                    cur.execute(
                        """
                        INSERT INTO meeting_candidates (
                            user_id, evidence_id, person_id, status, match_reason,
                            updated_at
                        ) VALUES (%s, %s, %s, 'proposed', %s, %s)
                        ON CONFLICT (user_id, evidence_id, person_id) DO UPDATE SET
                            match_reason = excluded.match_reason,
                            updated_at = excluded.updated_at
                        WHERE meeting_candidates.status != 'rejected'
                        """,
                        (self._user_id, evidence_id, person_id, reason, _now()),
                    )
                cur.execute(
                    """
                    UPDATE meeting_evidence SET status = %s, person_id = NULL,
                        match_reason = NULL, updated_at = %s
                    WHERE user_id = %s AND evidence_id = %s
                    """,
                    (
                        "proposed" if matches else "unmatched",
                        _now(),
                        self._user_id,
                        evidence_id,
                    ),
                )
        # The person event is written after this store's commit, as before.
        if attached is not None:
            self._sync_person_event(attached)

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

    def for_person(self, person_id: str) -> dict[str, list[dict[str, Any]]]:
        if self.people.get(person_id) is None:
            raise ValueError("unknown person")
        with self._tx() as cur:
            attached_rows = cur.execute(
                """
                SELECT * FROM meeting_evidence
                WHERE user_id = %s AND status = 'attached' AND person_id = %s
                ORDER BY starts_at NULLS FIRST, evidence_id
                """,
                (self._user_id, person_id),
            ).fetchall()
            candidate_rows = cur.execute(
                """
                SELECT e.*, c.status AS candidate_status,
                       c.match_reason AS candidate_reason
                FROM meeting_candidates c
                JOIN meeting_evidence e
                  ON e.user_id = c.user_id AND e.evidence_id = c.evidence_id
                WHERE c.user_id = %s AND c.person_id = %s
                ORDER BY e.starts_at NULLS FIRST,
                         e.evidence_id
                """,
                (self._user_id, person_id),
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

    def attach(self, evidence_id: str, person_id: str) -> dict[str, Any]:
        if self.people.get(person_id) is None:
            raise ValueError("unknown person")
        with self._tx() as cur:
            # The row lock makes the proposal check and the attach one step.
            row = self._get_row(cur, evidence_id, lock=True)
            candidate = cur.execute(
                """
                SELECT 1 FROM meeting_candidates
                WHERE user_id = %s AND evidence_id = %s AND person_id = %s
                """,
                (self._user_id, evidence_id, person_id),
            ).fetchone()
            if candidate is None and not (
                row["status"] == "attached" and row["person_id"] == person_id
            ):
                raise ValueError("meeting was not proposed for this person")
            cur.execute(
                """
                UPDATE meeting_evidence SET status = 'attached', person_id = %s,
                    match_reason = 'director_attached', updated_at = %s
                WHERE user_id = %s AND evidence_id = %s
                """,
                (person_id, _now(), self._user_id, evidence_id),
            )
            cur.execute(
                """
                UPDATE meeting_candidates SET
                    status = CASE WHEN person_id = %s THEN 'attached' ELSE 'rejected' END,
                    updated_at = %s
                WHERE user_id = %s AND evidence_id = %s
                """,
                (person_id, _now(), self._user_id, evidence_id),
            )
            attached = self._get_row(cur, evidence_id)
        # The person event is written after this store's commit, as before.
        self._sync_person_event(attached)
        return self._row(attached)

    def reject(self, evidence_id: str, person_id: str) -> dict[str, Any]:
        with self._tx() as cur:
            candidate = cur.execute(
                """
                SELECT match_reason FROM meeting_candidates
                WHERE user_id = %s AND evidence_id = %s AND person_id = %s
                FOR UPDATE
                """,
                (self._user_id, evidence_id, person_id),
            ).fetchone()
            if candidate is None:
                raise ValueError("meeting was not proposed for this person")
            cur.execute(
                """
                UPDATE meeting_candidates SET status = 'rejected', updated_at = %s
                WHERE user_id = %s AND evidence_id = %s AND person_id = %s
                """,
                (_now(), self._user_id, evidence_id, person_id),
            )
        # Read after the commit, as before: an unknown evidence id raises here
        # and leaves the rejection in place.
        with self._tx() as cur:
            row = self._get_row(cur, evidence_id)
        return self._row(
            row,
            status="rejected",
            reason=str(candidate["match_reason"]),
        )
