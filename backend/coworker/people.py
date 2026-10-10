"""Person files — one sqlite db, separate from conversation store."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from coworker.person_identity import (
    apollo_surname_is_masked,
    without_apollo_name_masks,
)

_SID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

# Declared schema version for people.db, read by coworker.migrations.
# v3 adds the person_descriptions table (ticket #202); the constructor creates
# it for a fresh database, and the registered 2->3 migration adopts an existing
# one with backup and rollback.
SCHEMA_VERSION = 3

SEQUENCE_STATES = ("open", "in_conversation", "done")
BOARD_LANES = ("backlog", *SEQUENCE_STATES)
ACTORS = ("director", "assistant")
DESCRIPTION_SLOTS = ("general", "detailed")
# Character ceilings from the Contacts spec (§6). The general slot is the
# concise Contacts overview; the detailed slot is the readable person-file prose.
DESCRIPTION_LIMITS = {"general": 500, "detailed": 8000}
ATTACHMENT_TYPES = frozenset({"artifact", "knowledge_gap", "source_ref"})
PERSON_PATCH_FIELDS = frozenset(
    {
        "first_name",
        "last_name",
        "title",
        "company",
        "target",
        "handoff_who",
        "handoff_wanted",
        "handoff_happened",
        "handoff_they_want",
    }
)
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
        os.chmod(self.base, 0o700)
        self.db_path = self.base / "people.db"
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        os.chmod(self.db_path, 0o600)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS people (
                person_id TEXT PRIMARY KEY,
                apollo_id TEXT UNIQUE,
                first_name TEXT,
                last_name TEXT,
                apollo_last_name_obfuscated TEXT,
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
                version INTEGER NOT NULL DEFAULT 1,
                outcome TEXT,
                deleted_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY,
                person_id TEXT NOT NULL,
                external_key TEXT,
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
            CREATE TABLE IF NOT EXISTS person_attachments (
                id TEXT PRIMARY KEY,
                person_id TEXT NOT NULL,
                record_type TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                fields_json TEXT NOT NULL,
                restricted INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(person_id, record_type, idempotency_key)
            );
            CREATE TABLE IF NOT EXISTS person_versions (
                person_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                person_json TEXT NOT NULL,
                attachments_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (person_id, version)
            );
            CREATE TABLE IF NOT EXISTS reply_sync (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                history_id TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS person_descriptions (
                person_id TEXT NOT NULL,
                slot TEXT NOT NULL,
                text TEXT,
                authority TEXT NOT NULL DEFAULT 'assistant',
                source_refs TEXT NOT NULL DEFAULT '[]',
                version INTEGER NOT NULL DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (person_id, slot)
            );
            """
        )
        self._ensure_schema()
        self._conn.commit()

    def _ensure_schema(self) -> None:
        columns = {
            str(row["name"])
            for row in self._conn.execute("PRAGMA table_info(people)").fetchall()
        }
        if "version" not in columns:
            self._conn.execute(
                "ALTER TABLE people ADD COLUMN version INTEGER NOT NULL DEFAULT 1"
            )
        if "outcome" not in columns:
            self._conn.execute("ALTER TABLE people ADD COLUMN outcome TEXT")
        if "deleted_at" not in columns:
            self._conn.execute("ALTER TABLE people ADD COLUMN deleted_at TEXT")
        if "apollo_last_name_obfuscated" not in columns:
            self._conn.execute(
                "ALTER TABLE people ADD COLUMN apollo_last_name_obfuscated TEXT"
            )
        self._conn.execute(
            """
            UPDATE people SET
                apollo_last_name_obfuscated = last_name,
                last_name = NULL
            WHERE last_name LIKE '%*%'
            """
        )
        event_columns = {
            str(row["name"])
            for row in self._conn.execute("PRAGMA table_info(events)").fetchall()
        }
        if "external_key" not in event_columns:
            self._conn.execute("ALTER TABLE events ADD COLUMN external_key TEXT")
        self._conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS events_person_external_key
            ON events(person_id, external_key)
            """
        )

    def _now(self) -> str:
        return datetime.now(UTC).isoformat()

    def _new_attachment_id(self, record_type: str) -> str:
        return f"{record_type}_{uuid.uuid4().hex}"

    def _person_dict(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        person = dict(row)
        apollo_hidden = person.pop("apollo_last_name_obfuscated", None)
        for field in (
            "handoff_who",
            "handoff_wanted",
            "handoff_happened",
            "handoff_they_want",
        ):
            if person.get(field):
                person[field] = without_apollo_name_masks(person[field])
        person["last_name_status"] = (
            "known"
            if _clean(person.get("last_name"))
            else "hidden_by_apollo"
            if _clean(apollo_hidden)
            else "missing"
        )
        person["version"] = int(person.get("version") or 1)
        person["restricted"] = False
        return person

    def _attachment_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": str(row["id"]),
            "person_id": str(row["person_id"]),
            "type": str(row["record_type"]),
            "fields": json.loads(str(row["fields_json"])),
            "restricted": bool(row["restricted"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }

    def _default_description(self, slot: str) -> dict[str, Any]:
        # A person with no row for this slot reads as assistant-owned and
        # unsummarized: version 0, no text. The first human save lazily
        # creates the row and flips authority to "human".
        return {
            "slot": slot,
            "text": None,
            "authority": "assistant",
            "source_refs": [],
            "version": 0,
        }

    def _description_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "slot": str(row["slot"]),
            "text": row["text"],
            "authority": str(row["authority"]),
            "source_refs": json.loads(str(row["source_refs"] or "[]")),
            "version": int(row["version"] or 0),
        }

    def _descriptions(self, person_id: str) -> dict[str, dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM person_descriptions WHERE person_id = ?",
            (person_id,),
        ).fetchall()
        found = {str(row["slot"]): self._description_dict(row) for row in rows}
        return {
            slot: found.get(slot, self._default_description(slot))
            for slot in DESCRIPTION_SLOTS
        }

    def descriptions(self, person_id: str) -> dict[str, dict[str, Any]]:
        """Both description slots for a person, defaulting missing slots.

        Always returns every slot in `DESCRIPTION_SLOTS` so callers never have
        to distinguish "no row" from "slot does not exist".
        """
        with self._lock:
            return self._descriptions(person_id)

    def _require_audit(self, actor: str, rationale_summary: str) -> None:
        if actor not in ACTORS:
            raise ValueError(f"invalid actor {actor}")
        if not rationale_summary.strip():
            raise ValueError("actor and rationale_summary are required")

    def _load_person_row(
        self, person_id: str, *, include_deleted: bool = False
    ) -> sqlite3.Row:
        row = self._conn.execute(
            "SELECT * FROM people WHERE person_id = ?",
            (person_id,),
        ).fetchone()
        if row is None:
            raise ValueError("unknown person")
        if not include_deleted and row["deleted_at"]:
            raise ValueError("unknown person")
        return row

    def _attachments_for(
        self,
        person_id: str,
        *,
        allowed_source_ids: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        allowed = allowed_source_ids or set()
        rows = self._conn.execute(
            """
            SELECT * FROM person_attachments
            WHERE person_id = ?
            ORDER BY created_at, id
            """,
            (person_id,),
        ).fetchall()
        records: list[dict[str, Any]] = []
        for row in rows:
            record = self._attachment_dict(row)
            if record["restricted"] and record["id"] not in allowed:
                continue
            records.append(record)
        return records

    def _snapshot(self, person_id: str, version: int, created_at: str) -> None:
        person = self._person_dict(
            self._conn.execute(
                "SELECT * FROM people WHERE person_id = ?", (person_id,)
            ).fetchone()
        )
        if person is not None:
            # Carry the description rows (text + per-slot authority) into the
            # snapshot so a person revert has them available. Restoring them is
            # a deferred slice (ticket #202 §8.2 follow-up); capturing them now
            # keeps the snapshot from silently losing description state.
            person["descriptions"] = self._descriptions(person_id)
        attachments = [
            self._attachment_dict(row)
            for row in self._conn.execute(
                "SELECT * FROM person_attachments WHERE person_id = ? ORDER BY id",
                (person_id,),
            ).fetchall()
        ]
        self._conn.execute(
            """
            INSERT OR REPLACE INTO person_versions (
                person_id, version, person_json, attachments_json, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                person_id,
                version,
                json.dumps(person, sort_keys=True),
                json.dumps(attachments, sort_keys=True),
                created_at,
            ),
        )

    def _restore_descriptions(
        self, person_id: str, captured: dict[str, Any], now: str
    ) -> None:
        """Put a snapshot's description rows back during a person revert.

        Restores each slot's text together with its per-slot authority and
        source references (spec §8.2), so a revert can never silently
        un-protect a human edit or drop a description's sources. A slot the
        snapshot held in its untouched default (assistant-owned, no text,
        version 0) is left with no row, which reads back as that same default.
        """
        self._conn.execute(
            "DELETE FROM person_descriptions WHERE person_id = ?", (person_id,)
        )
        for slot in DESCRIPTION_SLOTS:
            entry = captured.get(slot)
            if not isinstance(entry, dict):
                continue
            text = entry.get("text")
            authority = str(entry.get("authority") or "assistant")
            slot_version = int(entry.get("version") or 0)
            if text is None and authority == "assistant" and slot_version == 0:
                continue  # the untouched default carries no row
            source_refs = entry.get("source_refs") or []
            self._conn.execute(
                """
                INSERT INTO person_descriptions (
                    person_id, slot, text, authority, source_refs, version,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    person_id,
                    slot,
                    text,
                    authority if authority in ("human", "assistant") else "assistant",
                    json.dumps(source_refs),
                    slot_version,
                    now,
                    now,
                ),
            )

    def _receipt(
        self,
        person_id: str,
        *,
        kind: str,
        summary: str,
        payload: dict[str, Any],
        actor: str,
        session_id: str | None,
        run_id: str | None,
        tool: str | None = None,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO events (
                event_id, person_id, source, kind, summary, payload,
                actor, session_id, run_id, tool
            ) VALUES (?, ?, 'sourcecado', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _new_event_id(),
                person_id,
                kind,
                summary,
                json.dumps(payload),
                actor,
                session_id,
                run_id,
                tool,
            ),
        )

    def _has_conversation_evidence(self, person_id: str) -> bool:
        for event in self.timeline(person_id):
            if event.get("kind") == "error":
                continue
            payload = (
                event.get("payload") if isinstance(event.get("payload"), dict) else {}
            )
            if event.get("kind") == "send":
                return True
            if payload.get("sent") is True:
                return True
            if payload.get("direction") == "inbound":
                return True
            if event.get("kind") == "mail" and event.get("tool") == "gmail_read":
                return True
        return False

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
        incoming_last_name = (
            None
            if apollo_surname_is_masked(last_name_obfuscated)
            else last_name_obfuscated
        )
        incoming_hidden_last_name = (
            last_name_obfuscated
            if apollo_surname_is_masked(last_name_obfuscated)
            else None
        )
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
                restoring = bool(existing["deleted_at"])
                now = self._now()
                stored_last_name = _clean(existing["last_name"])
                stored_hidden_last_name = _clean(
                    existing["apollo_last_name_obfuscated"]
                )
                next_last_name = incoming_last_name or stored_last_name
                next_hidden_last_name = (
                    None
                    if incoming_last_name
                    else incoming_hidden_last_name or stored_hidden_last_name
                )
                changed = restoring or any(
                    incoming is not None and incoming != existing[field]
                    for incoming, field in (
                        (first_name, "first_name"),
                        (title, "title"),
                        (company, "company"),
                        (cleaned_target, "target"),
                    )
                ) or next_last_name != stored_last_name or (
                    next_hidden_last_name != stored_hidden_last_name
                )
                next_version = int(existing["version"] or 1) + int(changed)
                self._conn.execute(
                    """
                    UPDATE people SET
                        first_name = COALESCE(?, first_name),
                        last_name = ?,
                        apollo_last_name_obfuscated = ?,
                        title = COALESCE(?, title),
                        company = COALESCE(?, company),
                        target = COALESCE(?, target),
                        deleted_at = NULL,
                        version = ?,
                        updated_at = ?
                    WHERE person_id = ?
                    """,
                    (
                        first_name,
                        next_last_name,
                        next_hidden_last_name,
                        title,
                        company,
                        cleaned_target,
                        next_version,
                        now,
                        existing["person_id"],
                    ),
                )
                if restoring:
                    self._receipt(
                        existing["person_id"],
                        kind="keep",
                        summary="Restored person file from Apollo keep",
                        payload={"version": next_version, "apollo_id": apollo},
                        actor="assistant",
                        session_id=None,
                        run_id=None,
                        tool="people_keep",
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
                        person_id, apollo_id, first_name, last_name,
                        apollo_last_name_obfuscated, title, company, target
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        pid,
                        apollo,
                        first_name,
                        incoming_last_name,
                        incoming_hidden_last_name,
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
            self._snapshot(person["person_id"], int(person["version"]), self._now())
            self._conn.commit()
        person = self._person_dict(row)
        assert person is not None
        return person

    def get_by_apollo_id(self, apollo_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM people WHERE apollo_id = ? AND deleted_at IS NULL",
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
            if apollo_surname_is_masked(last_name):
                last_name = None
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

    def record_apollo_enrichment(
        self,
        person_id: str,
        *,
        result: dict[str, Any],
        approval_id: str,
        credits: int,
        matched_on: str,
        actor: str = "director",
        session_id: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Apply one approved enrichment and file the Apollo source receipt.

        Only ``person_id`` is written. The receipt records which Apollo record
        the facts came from and what the spend bought, so a later reader can
        tell an enriched field from a typed one.
        """
        person = self.apply_enrichment(
            person_id,
            name=result.get("name"),
            title=result.get("title"),
            company=result.get("organizationName"),
            email=result.get("email"),
            linkedin_url=result.get("linkedinUrl"),
            phone=result.get("phone"),
        )
        if person is None:
            return None
        applied = sorted(
            field
            for field, value in (
                ("name", result.get("name")),
                ("title", result.get("title")),
                ("company", result.get("organizationName")),
                ("email", result.get("email")),
                ("linkedin_url", result.get("linkedinUrl")),
                ("phone", result.get("phone")),
            )
            if value
        )
        source = self.upsert_attachment(
            person_id,
            record_type="source_ref",
            fields={
                "source": "apollo",
                "apollo_id": str(result.get("apolloId") or "") or None,
                "matched_on": matched_on,
                "approval_id": approval_id,
                "credits": int(credits),
                "fields_applied": applied,
                "fetched_at": self._now(),
            },
            idempotency_key=f"apollo:enrich:{approval_id}",
            actor=actor,
            rationale_summary="Approved Apollo enrichment",
            session_id=session_id,
            run_id=run_id,
        )
        with self._lock:
            self._receipt(
                person_id,
                kind="enrich",
                summary=f"Enriched from Apollo ({credits} credit)",
                payload={
                    "approval_id": approval_id,
                    "apollo_id": str(result.get("apolloId") or "") or None,
                    "credits": int(credits),
                    "matched_on": matched_on,
                    "fields_applied": applied,
                    "source_ref_id": source["id"],
                },
                actor=actor,
                session_id=session_id,
                run_id=run_id,
                tool="apollo_enrich_contact",
            )
            self._conn.commit()
        return {
            "person": self.get(person_id),
            "source_ref": source,
            "fields_applied": applied,
        }

    def record_approved_send(
        self,
        person_id: str,
        *,
        message_id: str,
        thread_id: str | None,
        draft_id: str,
        to: str,
        subject: str,
        body_digest: str,
        account: str | None,
        approval_id: str,
        actor: str = "director",
        session_id: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        """File the durable receipt for one approved send, then open the person.

        Keyed on the Gmail message id, so re-filing the same message can never
        produce a second receipt. Advancing to Open happens when the person is
        not already in one of the three valid sequence states; an open,
        in-conversation, or done person is left where the director put them.
        """
        if not message_id.strip():
            raise ValueError("message_id is required")
        external_key = f"gmail:message:{message_id}"
        payload = {
            "sent": True,
            "message_id": message_id,
            "thread_id": thread_id,
            "draft_id": draft_id,
            "to": to,
            "subject": subject,
            "body_digest": body_digest,
            "account": account,
            "approval_id": approval_id,
        }
        with self._lock:
            row = self._load_person_row(person_id)
            already = self._conn.execute(
                "SELECT event_id FROM events WHERE person_id = ? AND external_key = ?",
                (person_id, external_key),
            ).fetchone()
            if already is None:
                self._conn.execute(
                    """
                    INSERT INTO events (
                        event_id, person_id, external_key, source, kind, summary,
                        payload, actor, session_id, run_id, tool
                    ) VALUES (?, ?, ?, 'gmail', 'send', ?, ?, ?, ?, ?, 'gmail_send')
                    """,
                    (
                        _new_event_id(),
                        person_id,
                        external_key,
                        f"Sent approved outreach to {to}".strip(),
                        json.dumps(payload),
                        actor,
                        session_id,
                        run_id,
                    ),
                )
                self._conn.execute(
                    "UPDATE people SET updated_at = ? WHERE person_id = ?",
                    (self._now(), person_id),
                )
                self._conn.commit()
            event = self._event_dict(
                self._conn.execute(
                    "SELECT * FROM events WHERE person_id = ? AND external_key = ?",
                    (person_id, external_key),
                ).fetchone()
            )
            needs_open = row["sequence_state"] not in SEQUENCE_STATES
        person = (
            self.set_sequence(
                person_id,
                "open",
                actor=actor if actor in ACTORS else "director",
                session_id=session_id,
                run_id=run_id,
                rationale_summary="Approved outreach sent",
            )
            if needs_open
            else self.get(person_id)
        )
        return {"event": event, "person": person, "advanced_to_open": needs_open}

    # --- inbound replies -------------------------------------------------

    def reply_cursor(self) -> str | None:
        """The Gmail history id the last completed reply sync reached."""
        with self._lock:
            row = self._conn.execute(
                "SELECT history_id FROM reply_sync WHERE id = 1"
            ).fetchone()
        if row is None:
            return None
        return str(row["history_id"]) if row["history_id"] else None

    def set_reply_cursor(self, history_id: str | None) -> None:
        """Move the incremental boundary, or clear it to force a re-baseline."""
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO reply_sync (id, history_id, updated_at)
                VALUES (1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    history_id = excluded.history_id,
                    updated_at = excluded.updated_at
                """,
                (history_id, self._now()),
            )
            self._conn.commit()

    def _has_external_event(self, person_id: str, external_key: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT 1 FROM events
                WHERE person_id = ? AND external_key = ?
                """,
                (person_id, external_key),
            ).fetchone()
        return row is not None

    def _has_attachment(
        self, person_id: str, record_type: str, idempotency_key: str
    ) -> bool:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT 1 FROM person_attachments
                WHERE person_id = ? AND record_type = ? AND idempotency_key = ?
                """,
                (person_id, record_type, idempotency_key),
            ).fetchone()
        return row is not None

    def _already_transitioned_for(self, person_id: str, message_id: str) -> bool:
        """Whether this exact reply already moved this person once.

        The state receipt written for the move carries the Gmail message id
        that caused it, so the receipt is also the record that stops a second
        move. Nothing here depends on the sync cursor: a cursor that is lost
        and rebuilt replays the same message and still finds this.
        """
        for event in self.timeline(person_id):
            payload = event.get("payload")
            if event.get("kind") != "state" or not isinstance(payload, dict):
                continue
            source_ref = payload.get("source_ref")
            if (
                isinstance(source_ref, dict)
                and str(source_ref.get("message_id") or "") == message_id
            ):
                return True
        return False

    def file_inbound_reply(
        self,
        person_id: str,
        *,
        message_id: str,
        thread_id: str | None,
        sender: str,
        subject: str | None,
        snippet: str,
        received_at: str | None,
        actor: str = "assistant",
    ) -> dict[str, Any]:
        """File one verified inbound reply, then open the conversation.

        Keyed on the Gmail message id twice over: the timeline event carries it
        as its external key, so a repeated sync updates one event instead of
        adding a second, and the state receipt carries it too, so the Open to
        In conversation move happens once even after the sync cursor is lost
        and the message is read again.

        Only Open advances. A person the director already moved to In
        conversation or Done stays where they put them.
        """
        if not message_id.strip():
            raise ValueError("message_id is required")
        source_ref = {
            "provider": "Gmail",
            "message_id": message_id,
            "thread_id": thread_id,
            "url": (
                f"https://mail.google.com/mail/u/0/#all/{thread_id}"
                if thread_id
                else None
            ),
        }
        headline = (subject or "").strip() or "no subject"
        external_key = f"gmail:message:{message_id}"
        already_filed = self._has_external_event(person_id, external_key)
        event = self.upsert_external_event(
            person_id,
            external_key=external_key,
            source="gmail",
            kind="mail",
            summary=f"Reply from {sender}: {headline}",
            payload={
                "direction": "inbound",
                "message_id": message_id,
                "thread_id": thread_id,
                "from": sender,
                "subject": subject,
                "snippet": snippet,
                "received_at": received_at,
                "source_ref": source_ref,
                "untrusted": True,
                "read_only": True,
            },
            actor=actor,
            tool="gmail_read",
        )
        with self._lock:
            row = self._load_person_row(person_id)
            advance = str(row["sequence_state"] or "") == "open"
        if advance and self._already_transitioned_for(person_id, message_id):
            advance = False
        person = (
            self.set_sequence(
                person_id,
                "in_conversation",
                actor="assistant",
                rationale_summary=f"Verified inbound reply from {sender}.",
                source_ref=source_ref,
            )
            if advance
            else self.get(person_id)
        )
        return {
            "event": event,
            "person": person,
            "advanced_to_in_conversation": advance,
            "already_filed": already_filed,
        }

    def record_reply_gap(
        self,
        person_id: str,
        *,
        message_id: str,
        thread_id: str | None,
        reason: str,
        question: str,
        candidate_count: int,
        received_at: str | None,
        actor: str = "assistant",
    ) -> dict[str, Any]:
        """Record that a reply on this person's thread could not be attributed.

        The gap carries thread identity, the refusal reason, and the question a
        human has to answer. It never carries the reply text: the message may
        belong to someone else, and copying its words onto a person file is the
        contamination the refusal exists to prevent.
        """
        if not message_id.strip():
            raise ValueError("message_id is required")
        idempotency_key = f"gmail:reply:{message_id}"
        already_recorded = self._has_attachment(
            person_id, "knowledge_gap", idempotency_key
        )
        gap = self.upsert_attachment(
            person_id,
            record_type="knowledge_gap",
            fields={
                "kind": "unassigned_reply",
                "provider": "Gmail",
                "evidence": "ambiguous",
                "message_id": message_id,
                "thread_id": thread_id,
                "reason": reason,
                "question": question,
                "candidate_count": int(candidate_count),
                "received_at": received_at,
            },
            idempotency_key=idempotency_key,
            actor=actor,
            rationale_summary="Inbound reply could not be tied to one person.",
        )
        return {"gap": gap, "already_recorded": already_recorded}

    def mail_state(self, person_id: str) -> dict[str, Any]:
        """Last contact, replied state, and whether the person needs attention.

        Ordering comes from the ledger, not from comparing timestamps: the
        events are read in write order, so the last mail event seen is the
        latest one whatever format its stamp is in.
        """
        direction: str | None = None
        last_at: str | None = None
        replied = False
        replied_at: str | None = None
        for event in self.timeline(person_id):
            payload = (
                event.get("payload") if isinstance(event.get("payload"), dict) else {}
            )
            created = str(event.get("created_at") or "") or None
            if event.get("kind") == "send" and payload.get("sent"):
                direction, last_at = "outbound", created
            elif payload.get("direction") == "inbound":
                replied = True
                replied_at = payload.get("received_at") or created
                direction, last_at = "inbound", replied_at
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT fields_json FROM person_attachments
                WHERE person_id = ? AND record_type = 'knowledge_gap'
                """,
                (person_id,),
            ).fetchall()
        needs_review = any(
            json.loads(str(row["fields_json"])).get("kind") == "unassigned_reply"
            for row in rows
        )
        if direction == "inbound":
            follow_up = {"needed": True, "reason": "reply_unanswered"}
        elif needs_review:
            follow_up = {"needed": True, "reason": "reply_needs_review"}
        else:
            follow_up = {"needed": False, "reason": None}
        return {
            "last_contact_at": last_at,
            "last_contact_direction": direction,
            "replied": replied,
            "replied_at": replied_at,
            "follow_up": follow_up,
        }

    def get(
        self,
        person_id: str,
        *,
        expand_sources: bool = False,
        allowed_source_ids: set[str] | None = None,
    ) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM people WHERE person_id = ? AND deleted_at IS NULL",
                (person_id,),
            ).fetchone()
            person = self._person_dict(row)
            if person is None or not expand_sources:
                return person
            attachments = self._attachments_for(
                person_id, allowed_source_ids=allowed_source_ids
            )
            restricted_hidden = self._conn.execute(
                """
                SELECT COUNT(*) FROM person_attachments
                WHERE person_id = ? AND restricted = 1
                """,
                (person_id,),
            ).fetchone()[0]
        person["attachments"] = attachments
        person["sources"] = [
            item for item in attachments if item["type"] == "source_ref"
        ]
        person["artifacts"] = [
            item for item in attachments if item["type"] == "artifact"
        ]
        person["knowledge_gaps"] = [
            item for item in attachments if item["type"] == "knowledge_gap"
        ]
        visible_restricted = sum(1 for item in attachments if item["restricted"])
        person["restricted_source_count"] = int(restricted_hidden) - visible_restricted
        person.update(self.mail_state(person_id))
        return person

    def set_sequence(
        self,
        person_id: str,
        state: str,
        *,
        actor: str,
        expected_version: int | None = None,
        session_id: str | None = None,
        run_id: str | None = None,
        rationale_summary: str = "",
        source_ref: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if state not in SEQUENCE_STATES:
            raise ValueError(f"invalid sequence state {state}")
        if actor not in ACTORS:
            raise ValueError(f"invalid actor {actor}")
        with self._lock:
            row = self._load_person_row(person_id)
            if expected_version is not None and int(row["version"]) != expected_version:
                raise ValueError(
                    f"stale record version: expected {expected_version}, current {row['version']}"
                )
            if state == "in_conversation" and actor == "assistant":
                if not self._has_conversation_evidence(person_id):
                    raise ValueError(
                        "in_conversation requires sent outreach, an inbound reply, or director Allow"
                    )
            next_outcome = row["outcome"]
            if state == "done" and not str(row["outcome"] or "").strip():
                if actor != "director":
                    raise ValueError("done requires a recorded outcome")
                next_outcome = "closed"
            now = self._now()
            next_version = int(row["version"] or 1) + 1
            self._conn.execute(
                """
                UPDATE people
                SET sequence_state = ?, outcome = ?, version = ?, updated_at = ?
                WHERE person_id = ?
                """,
                (state, next_outcome, next_version, now, person_id),
            )
            self._receipt(
                person_id,
                kind="state",
                summary=f"Moved to {state.replace('_', ' ')}",
                payload={
                    "state": state,
                    "rationale_summary": rationale_summary,
                    "version": next_version,
                    # The external record that justified the move, when one
                    # did. This is what a reader points at to check the move,
                    # and what stops the same source moving the person twice.
                    **({"source_ref": source_ref} if source_ref else {}),
                },
                actor=actor,
                session_id=session_id,
                run_id=run_id,
            )
            self._snapshot(person_id, next_version, now)
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM people WHERE person_id = ?",
                (person_id,),
            ).fetchone()
        person = self._person_dict(row)
        assert person is not None
        return person

    def list_board(self) -> dict[str, list[dict[str, Any]]]:
        board: dict[str, list[dict[str, Any]]] = {lane: [] for lane in BOARD_LANES}
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM people
                WHERE deleted_at IS NULL
                ORDER BY updated_at DESC, person_id
                """
            ).fetchall()
            description_rows = self._conn.execute(
                "SELECT * FROM person_descriptions"
            ).fetchall()
        descriptions_by_person: dict[str, dict[str, dict[str, Any]]] = {}
        for description_row in description_rows:
            person_descriptions = descriptions_by_person.setdefault(
                str(description_row["person_id"]), {}
            )
            person_descriptions[str(description_row["slot"])] = self._description_dict(
                description_row
            )
        for row in rows:
            person = self._person_dict(row)
            assert person is not None
            found = descriptions_by_person.get(person["person_id"], {})
            person["descriptions"] = {
                slot: found.get(slot, self._default_description(slot))
                for slot in DESCRIPTION_SLOTS
            }
            person.update(self.mail_state(person["person_id"]))
            lane = (
                person["sequence_state"]
                if person["sequence_state"] in SEQUENCE_STATES
                else "backlog"
            )
            person["board_lane"] = lane
            board[lane].append(person)
        return board

    def list_people(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM people
                WHERE deleted_at IS NULL
                ORDER BY person_id
                """
            ).fetchall()
        return [person for row in rows if (person := self._person_dict(row))]

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
                "SELECT 1 FROM people WHERE person_id = ? AND deleted_at IS NULL",
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

    def upsert_external_event(
        self,
        person_id: str,
        *,
        external_key: str,
        source: str,
        kind: str,
        summary: str,
        payload: dict[str, Any] | None = None,
        actor: str = "assistant",
        tool: str | None = None,
    ) -> dict[str, Any]:
        key = external_key.strip()
        if not key:
            raise ValueError("external_key is required")
        if source not in SOURCES:
            raise ValueError(f"invalid source {source}")
        if actor not in ACTORS:
            raise ValueError(f"invalid actor {actor}")
        if not summary.strip():
            raise ValueError("summary is required")
        with self._lock:
            self._load_person_row(person_id)
            existing = self._conn.execute(
                """
                SELECT event_id FROM events
                WHERE person_id = ? AND external_key = ?
                """,
                (person_id, key),
            ).fetchone()
            if existing is None:
                event_id = _new_event_id()
                self._conn.execute(
                    """
                    INSERT INTO events (
                        event_id, person_id, external_key, source, kind, summary,
                        payload, actor, session_id, run_id, tool
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?)
                    """,
                    (
                        event_id,
                        person_id,
                        key,
                        source,
                        kind,
                        summary.strip(),
                        json.dumps(payload or {}),
                        actor,
                        tool,
                    ),
                )
            else:
                event_id = str(existing["event_id"])
                self._conn.execute(
                    """
                    UPDATE events SET
                        source = ?, kind = ?, summary = ?, payload = ?,
                        actor = ?, tool = ?
                    WHERE event_id = ?
                    """,
                    (
                        source,
                        kind,
                        summary.strip(),
                        json.dumps(payload or {}),
                        actor,
                        tool,
                        event_id,
                    ),
                )
            self._conn.execute(
                "UPDATE people SET updated_at = CURRENT_TIMESTAMP WHERE person_id = ?",
                (person_id,),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM events WHERE event_id = ?", (event_id,)
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

    def bind_session(
        self,
        session_id: str,
        person_id: str,
        *,
        expected_person_version: int | None = None,
    ) -> None:
        sid = (session_id or "").strip()
        if not sid or not _SID_RE.fullmatch(sid) or ".." in sid:
            raise ValueError("invalid session id")
        with self._lock:
            exists = self._conn.execute(
                "SELECT version FROM people WHERE person_id = ? AND deleted_at IS NULL",
                (person_id,),
            ).fetchone()
            if exists is None:
                raise ValueError("unknown person")
            current_version = int(exists["version"])
            if (
                expected_person_version is not None
                and current_version != expected_person_version
            ):
                raise ValueError(
                    "stale record version: "
                    f"expected {expected_person_version}, current {current_version}"
                )
            bound = self._conn.execute(
                "SELECT person_id FROM session_people WHERE session_id = ?",
                (sid,),
            ).fetchone()
            if bound is not None:
                if str(bound["person_id"]) != person_id:
                    raise ValueError("session is already bound to another person")
                return
            person_session = self._conn.execute(
                "SELECT session_id FROM session_people WHERE person_id = ?",
                (person_id,),
            ).fetchone()
            if person_session is not None:
                raise ValueError("person already has a bound sourcing session")
            self._conn.execute(
                "INSERT INTO session_people (session_id, person_id) VALUES (?, ?)",
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

    def session_for_person(self, person_id: str) -> str | None:
        with self._lock:
            rows = self._conn.execute(
                "SELECT session_id FROM session_people WHERE person_id = ?",
                (person_id,),
            ).fetchall()
        if not rows:
            return None
        if len(rows) > 1:
            raise ValueError("person has multiple bound sourcing sessions")
        return str(rows[0]["session_id"])

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

    def query(
        self,
        *,
        sequence: str | None = None,
        company: str | None = None,
        target: str | None = None,
    ) -> list[dict[str, Any]]:
        if sequence is not None and sequence not in SEQUENCE_STATES:
            raise ValueError(f"invalid sequence state {sequence}")
        board = self.list_board()
        people = (
            board[sequence]
            if sequence is not None
            else [person for lane in BOARD_LANES for person in board[lane]]
        )
        matched = []
        for person in people:
            if company is not None and person.get("company") != company:
                continue
            if target is not None and person.get("target") != target:
                continue
            matched.append(person)
        return matched

    def patch(
        self,
        person_id: str,
        *,
        fields: dict[str, Any],
        expected_version: int,
        actor: str,
        rationale_summary: str,
        session_id: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(fields, dict) or not fields:
            raise ValueError("fields must be an object")
        unknown = set(fields) - PERSON_PATCH_FIELDS
        if unknown:
            raise ValueError(f"unsupported person fields {sorted(unknown)}")
        if apollo_surname_is_masked(fields.get("last_name")):
            raise ValueError("an obfuscated Apollo surname cannot be canonical")
        fields = dict(fields)
        for field in (
            "handoff_who",
            "handoff_wanted",
            "handoff_happened",
            "handoff_they_want",
        ):
            if fields.get(field):
                fields[field] = without_apollo_name_masks(fields[field])
        self._require_audit(actor, rationale_summary)
        with self._lock:
            row = self._load_person_row(person_id)
            if int(row["version"]) != expected_version:
                raise ValueError(
                    f"stale record version: expected {expected_version}, current {row['version']}"
                )
            now = self._now()
            next_version = expected_version + 1
            assignments = [f"{key} = ?" for key in fields]
            values: list[Any] = [fields[key] for key in fields]
            values.extend([next_version, now, person_id, expected_version])
            cursor = self._conn.execute(
                f"""
                UPDATE people
                SET {", ".join([*assignments, "version = ?", "updated_at = ?"])}
                WHERE person_id = ? AND version = ?
                """,
                values,
            )
            if cursor.rowcount != 1:
                self._conn.rollback()
                raise ValueError("stale record version")
            self._receipt(
                person_id,
                kind="patch",
                summary=rationale_summary.strip(),
                payload={"fields": fields, "version": next_version},
                actor=actor,
                session_id=session_id,
                run_id=run_id,
            )
            self._snapshot(person_id, next_version, now)
            self._conn.commit()
        person = self.get(person_id)
        assert person is not None
        return person

    def patch_description(
        self,
        person_id: str,
        slot: str,
        *,
        expected_version: int,
        text: str | None = None,
        release: bool = False,
        actor: str,
        rationale_summary: str,
        session_id: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        """Save a description slot's text, or release it back to the assistant.

        Concurrency is slot-scoped: `expected_version` is checked against the
        slot's own version, so an edit to `general` never false-conflicts with
        an edit to `detailed` or the handoff. A text save flips authority to
        "human" (an empty string is an intentional, protected blank); `release`
        flips it back to "assistant" and leaves the text in place for a later
        assistant writer. Either way the write takes a person snapshot and
        leaves a receipt, so audit and revert stay person-scoped.
        """
        if slot not in DESCRIPTION_SLOTS:
            raise ValueError(f"unknown description slot {slot}")
        self._require_audit(actor, rationale_summary)
        if not release:
            if text is None:
                raise ValueError("a description save needs text or release")
            limit = DESCRIPTION_LIMITS[slot]
            if len(text) > limit:
                raise ValueError(
                    f"{slot} description exceeds {limit} characters"
                )
        with self._lock:
            self._load_person_row(person_id)
            current = self._conn.execute(
                "SELECT * FROM person_descriptions WHERE person_id = ? AND slot = ?",
                (person_id, slot),
            ).fetchone()
            current_version = int(current["version"]) if current is not None else 0
            if current_version != expected_version:
                raise ValueError(
                    "stale record version: "
                    f"expected {expected_version}, current {current_version}"
                )
            now = self._now()
            next_slot_version = expected_version + 1
            if release:
                new_authority = "assistant"
                new_text = current["text"] if current is not None else None
                new_source_refs = (
                    str(current["source_refs"]) if current is not None else "[]"
                )
            else:
                new_authority = "human"
                new_text = text
                # A human edit carries no source references.
                new_source_refs = "[]"
            created_at = (
                str(current["created_at"]) if current is not None else now
            )
            self._conn.execute(
                """
                INSERT INTO person_descriptions (
                    person_id, slot, text, authority, source_refs, version,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(person_id, slot) DO UPDATE SET
                    text = excluded.text,
                    authority = excluded.authority,
                    source_refs = excluded.source_refs,
                    version = excluded.version,
                    updated_at = excluded.updated_at
                """,
                (
                    person_id,
                    slot,
                    new_text,
                    new_authority,
                    new_source_refs,
                    next_slot_version,
                    created_at,
                    now,
                ),
            )
            person_row = self._load_person_row(person_id)
            next_person_version = int(person_row["version"] or 1) + 1
            self._conn.execute(
                "UPDATE people SET version = ?, updated_at = ? WHERE person_id = ?",
                (next_person_version, now, person_id),
            )
            self._receipt(
                person_id,
                kind="description",
                summary=rationale_summary.strip(),
                payload={
                    "slot": slot,
                    "authority": new_authority,
                    "released": bool(release),
                    "version": next_slot_version,
                },
                actor=actor,
                session_id=session_id,
                run_id=run_id,
            )
            self._snapshot(person_id, next_person_version, now)
            self._conn.commit()
        return self.descriptions(person_id)[slot]

    def upsert_attachment(
        self,
        person_id: str,
        *,
        record_type: str,
        fields: dict[str, Any],
        idempotency_key: str,
        actor: str,
        rationale_summary: str,
        session_id: str | None = None,
        run_id: str | None = None,
        allowed_source_ids: set[str] | None = None,
    ) -> dict[str, Any]:
        if record_type not in ATTACHMENT_TYPES:
            raise ValueError(f"unknown record type {record_type}")
        if not isinstance(fields, dict):
            raise ValueError("fields must be an object")
        if not idempotency_key.strip():
            raise ValueError("idempotency_key is required")
        self._require_audit(actor, rationale_summary)
        restricted = int(
            record_type == "source_ref"
            and str(fields.get("sensitivity") or "").lower() == "restricted"
        )
        allowed = allowed_source_ids or set()

        def _public(record: dict[str, Any]) -> dict[str, Any]:
            if record["restricted"] and record["id"] not in allowed:
                return {
                    "id": record["id"],
                    "person_id": record["person_id"],
                    "type": record["type"],
                    "restricted": True,
                }
            return record

        with self._lock:
            row = self._load_person_row(person_id)
            existing = self._conn.execute(
                """
                SELECT * FROM person_attachments
                WHERE person_id = ? AND record_type = ? AND idempotency_key = ?
                """,
                (person_id, record_type, idempotency_key),
            ).fetchone()
            if existing is not None:
                current = self._attachment_dict(existing)
                if current["fields"] != fields or current["restricted"] != bool(
                    restricted
                ):
                    raise ValueError(
                        "idempotency conflict: key already belongs to different record facts"
                    )
                return _public(current)
            now = self._now()
            record_id = self._new_attachment_id(record_type)
            self._conn.execute(
                """
                INSERT INTO person_attachments (
                    id, person_id, record_type, idempotency_key, fields_json,
                    restricted, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    person_id,
                    record_type,
                    idempotency_key,
                    json.dumps(fields, sort_keys=True),
                    restricted,
                    now,
                    now,
                ),
            )
            next_version = int(row["version"] or 1) + 1
            self._conn.execute(
                "UPDATE people SET version = ?, updated_at = ? WHERE person_id = ?",
                (next_version, now, person_id),
            )
            self._receipt(
                person_id,
                kind=record_type,
                summary=rationale_summary.strip(),
                payload={"id": record_id, "type": record_type, "version": next_version},
                actor=actor,
                session_id=session_id,
                run_id=run_id,
            )
            self._snapshot(person_id, next_version, now)
            self._conn.commit()
            stored = self._conn.execute(
                "SELECT * FROM person_attachments WHERE id = ?", (record_id,)
            ).fetchone()
        return _public(self._attachment_dict(stored))

    def capture_outcome(
        self,
        person_id: str,
        *,
        outcome: str,
        expected_version: int,
        actor: str,
        rationale_summary: str,
        session_id: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        if not outcome.strip():
            raise ValueError("outcome is required")
        self._require_audit(actor, rationale_summary)
        with self._lock:
            row = self._load_person_row(person_id)
            if int(row["version"]) != expected_version:
                raise ValueError(
                    f"stale record version: expected {expected_version}, current {row['version']}"
                )
            now = self._now()
            next_version = expected_version + 1
            cursor = self._conn.execute(
                """
                UPDATE people SET outcome = ?, version = ?, updated_at = ?
                WHERE person_id = ? AND version = ?
                """,
                (outcome.strip(), next_version, now, person_id, expected_version),
            )
            if cursor.rowcount != 1:
                self._conn.rollback()
                raise ValueError("stale record version")
            self._receipt(
                person_id,
                kind="outcome",
                summary=rationale_summary.strip(),
                payload={"outcome": outcome.strip(), "version": next_version},
                actor=actor,
                session_id=session_id,
                run_id=run_id,
            )
            self._snapshot(person_id, next_version, now)
            self._conn.commit()
        person = self.get(person_id)
        assert person is not None
        return person

    def revert(
        self,
        person_id: str,
        *,
        to_version: int,
        expected_version: int,
        actor: str,
        rationale_summary: str,
        session_id: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        self._require_audit(actor, rationale_summary)
        with self._lock:
            row = self._load_person_row(person_id)
            if int(row["version"]) != expected_version:
                raise ValueError(
                    f"stale record version: expected {expected_version}, current {row['version']}"
                )
            snapshot = self._conn.execute(
                """
                SELECT * FROM person_versions
                WHERE person_id = ? AND version = ?
                """,
                (person_id, to_version),
            ).fetchone()
            if snapshot is None:
                raise ValueError(f"unknown record version {to_version}")
            target = json.loads(str(snapshot["person_json"]))
            target_last_name = target.get("last_name")
            target_hidden_last_name = None
            if apollo_surname_is_masked(target_last_name):
                target_hidden_last_name = target_last_name
                target_last_name = None
            attachments = json.loads(str(snapshot["attachments_json"]))
            # Snapshots carry description rows under person_json["descriptions"]
            # (text + per-slot authority + sources). Restore them below so a
            # revert never silently un-protects a human edit or drops a
            # description's sources (spec §8.2). A snapshot taken before #202
            # has no such key; those predate descriptions, so there is nothing
            # to restore and the live rows are left untouched.
            restored_descriptions = (
                target.get("descriptions")
                if isinstance(target.get("descriptions"), dict)
                else None
            )
            now = self._now()
            next_version = expected_version + 1
            cursor = self._conn.execute(
                """
                UPDATE people SET
                    first_name = ?, last_name = ?,
                    apollo_last_name_obfuscated = COALESCE(?, apollo_last_name_obfuscated),
                    title = ?, company = ?, target = ?,
                    sequence_state = ?, outcome = ?,
                    handoff_who = ?, handoff_wanted = ?, handoff_happened = ?,
                    handoff_they_want = ?, version = ?, updated_at = ?
                WHERE person_id = ? AND version = ?
                """,
                (
                    target.get("first_name"),
                    target_last_name,
                    target_hidden_last_name,
                    target.get("title"),
                    target.get("company"),
                    target.get("target"),
                    target.get("sequence_state"),
                    target.get("outcome"),
                    without_apollo_name_masks(target.get("handoff_who")) or None,
                    without_apollo_name_masks(target.get("handoff_wanted")) or None,
                    without_apollo_name_masks(target.get("handoff_happened")) or None,
                    without_apollo_name_masks(target.get("handoff_they_want")) or None,
                    next_version,
                    now,
                    person_id,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                self._conn.rollback()
                raise ValueError("stale record version")
            self._conn.execute(
                "DELETE FROM person_attachments WHERE person_id = ?", (person_id,)
            )
            for item in attachments:
                self._conn.execute(
                    """
                    INSERT INTO person_attachments (
                        id, person_id, record_type, idempotency_key, fields_json,
                        restricted, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item["id"],
                        person_id,
                        item["type"],
                        f"restored:{item['id']}",
                        json.dumps(item.get("fields") or {}, sort_keys=True),
                        int(bool(item.get("restricted"))),
                        item.get("created_at") or now,
                        now,
                    ),
                )
            if restored_descriptions is not None:
                self._restore_descriptions(person_id, restored_descriptions, now)
            self._receipt(
                person_id,
                kind="revert",
                summary=rationale_summary.strip(),
                payload={"to_version": to_version, "version": next_version},
                actor=actor,
                session_id=session_id,
                run_id=run_id,
            )
            self._snapshot(person_id, next_version, now)
            self._conn.commit()
        person = self.get(person_id)
        assert person is not None
        return person

    def delete(
        self,
        person_id: str,
        *,
        expected_version: int,
        actor: str,
        rationale_summary: str,
        session_id: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        self._require_audit(actor, rationale_summary)
        with self._lock:
            row = self._load_person_row(person_id)
            if int(row["version"]) != expected_version:
                raise ValueError(
                    f"stale record version: expected {expected_version}, current {row['version']}"
                )
            now = self._now()
            next_version = expected_version + 1
            self._conn.execute(
                """
                UPDATE people SET deleted_at = ?, version = ?, updated_at = ?
                WHERE person_id = ? AND version = ?
                """,
                (now, next_version, now, person_id, expected_version),
            )
            self._receipt(
                person_id,
                kind="delete",
                summary=rationale_summary.strip(),
                payload={"deleted": True, "version": next_version},
                actor=actor,
                session_id=session_id,
                run_id=run_id,
            )
            self._conn.commit()
        return {"deleted": True, "id": person_id}

    def versions(self, person_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT version, created_at FROM person_versions
                WHERE person_id = ? ORDER BY version
                """,
                (person_id,),
            ).fetchall()
        return [
            {"version": int(row["version"]), "created_at": str(row["created_at"])}
            for row in rows
        ]
