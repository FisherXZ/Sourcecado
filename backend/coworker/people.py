"""Person files — one sqlite db, separate from conversation store."""

from __future__ import annotations
from contextlib import contextmanager

import json
import os
import re
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from coworker.crm_tasks import CRM_TASK_STATEMENTS, TaskConflict, _clean_operation_id, _fingerprint
from coworker.crm_sources import source_ids
from coworker.crm_analysis_schema import CRM_ANALYSIS_STATEMENTS
from coworker.crm_email_task_schema import add_task_analysis_schema
from coworker.person_identity import (
    apollo_surname_is_masked,
    without_apollo_name_masks,
)

_SID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

# Declared schema version for people.db, read by coworker.migrations.
# v3 adds the person_descriptions table (ticket #202); v4 adds the person_tasks,
# crm_changes and crm_operations tables (ticket #200); v5 adds task lifecycle
# actions (ticket #201); v6 adds durable analysis, proposals and description
# history (#209); v7 adds canonical task evidence and analysis stage tracking
# (#210); v9 adds approval identity revisions and v10 protected person corrections
# (#243). The constructor creates fresh tables. Existing databases use
# the registered migrations with backup and rollback before the new writer runs.
SCHEMA_VERSION = 10
APPROVAL_IDENTITY_FIELDS = ("first_name", "last_name", "company", "email", "apollo_id")

SEQUENCE_STATES = ("open", "in_conversation", "done")
BOARD_LANES = ("backlog", *SEQUENCE_STATES)
ACTORS = ("director", "assistant")
DESCRIPTION_SLOTS = ("general", "detailed")
# Character ceilings from the Contacts spec (§6). The general slot is the
# concise Contacts overview; the detailed slot is the readable person-file prose.
DESCRIPTION_LIMITS = {"general": 500, "detailed": 8000}
ATTACHMENT_TYPES = frozenset({"artifact", "knowledge_gap", "source_ref"})
# Saved-person finder (#242). Results are bounded so the finder never returns an
# unbounded list; the hard cap protects the UI and the assistant tool alike.
SEARCH_LIMIT_DEFAULT = 20
SEARCH_LIMIT_MAX = 50
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


class CrmMigrationRequired(ValueError):
    """The declared people.db version cannot safely record current CRM writes."""

    code = "crm_migration_required"

    def __init__(self) -> None:
        super().__init__("CRM migration required: stop Sourcecado and run make doctor-repair before analysis")


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
        # Only an empty, unversioned database is ours to initialize at the
        # current version. Existing stores still need the backed-up registry
        # path, even when constructor probes have added missing tables.
        initial_version = int(self._conn.execute("PRAGMA user_version").fetchone()[0])
        fresh_database = initial_version == 0 and self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' LIMIT 1"
        ).fetchone() is None
        # SQLite enforces foreign keys per connection. Task and CRM-change rows
        # reference people(person_id), so a write for a missing person fails
        # here even if a caller skips the service's own check.
        self._conn.execute("PRAGMA foreign_keys = ON")
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
                identity_revision INTEGER NOT NULL DEFAULT 0 CHECK(identity_revision >= 0),
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
        from coworker.crm_mail_repository import MAIL_SCHEMA
        from coworker.crm_unified_schema import additions
        statements = (*CRM_TASK_STATEMENTS, *MAIL_SCHEMA, *CRM_ANALYSIS_STATEMENTS)
        if fresh_database:
            # Canonical analysis owns the table; the historical mail DDL is frozen.
            statements = (*CRM_TASK_STATEMENTS, *CRM_ANALYSIS_STATEMENTS, *MAIL_SCHEMA)
        for statement in statements:
            if not fresh_database and statement.lstrip().startswith("CREATE INDEX"):
                continue
            self._conn.execute(statement.replace("'description', 'task_create', 'task_update'", "'description', 'task_create', 'task_update', 'status'"))
        if fresh_database:
            add_task_analysis_schema(self._conn)
            additions(self._conn)
            from coworker.person_corrections import add_schema
            add_schema(self._conn)
            self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self._conn.commit()

    @contextmanager
    def crm_transaction(self):
        with self._lock:
            nested = self._conn.in_transaction
            if nested:
                self._conn.execute("SAVEPOINT crm_nested")
            else:
                self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
                self._conn.execute("RELEASE crm_nested") if nested else self._conn.commit()
            except BaseException:
                if nested:
                    self._conn.execute("ROLLBACK TO crm_nested")
                    self._conn.execute("RELEASE crm_nested")
                else:
                    self._conn.rollback()
                raise

    def _crm_description_history_ready(self) -> bool:
        version = int(self._conn.execute("PRAGMA user_version").fetchone()[0])
        schema = self._conn.execute("SELECT sql FROM sqlite_master WHERE name='crm_changes'").fetchone()
        stages = {row["name"] for row in self._conn.execute("PRAGMA table_info(crm_analysis_state)")}
        evidence = self._conn.execute("SELECT 1 FROM sqlite_master WHERE name='crm_task_evidence'").fetchone()
        return (version == SCHEMA_VERSION and schema is not None and "'description'" in schema[0]
            and "analysis_version" in stages and evidence is not None)

    def require_crm_schema(self) -> None:
        with self._lock:
            if not self._crm_description_history_ready():
                raise CrmMigrationRequired()

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
        person["identity_revision"] = int(person.get("identity_revision") or 0)
        person["restricted"] = False
        from coworker.person_corrections import projection
        person['identity_field_controls'],person['identity_conflicts']=projection(self,person['person_id'])
        return person

    def _bump_identity_revision(self, person_id: str, before: Any) -> int:
        """After a caller-owned identity UPDATE, bump once from its fresh prior row.

        Call under the people lock, before committing and snapshotting. The
        current revision prevents a repeated call for the same update bumping
        twice. Corrections/Undo and provider merges share this narrow helper.
        Historical stores remain on their registered migration path.
        """
        old = dict(before)
        current = dict(self._load_person_row(person_id))
        if "identity_revision" not in current:
            return 0
        def facts(row: dict) -> tuple:
            return tuple((_clean(str(row.get(field) or "")) or "").casefold()
                         if field == "email" else _clean(str(row.get(field) or ""))
                         for field in APPROVAL_IDENTITY_FIELDS)
        revision = int(current["identity_revision"])
        if facts(old) != facts(current) and revision == int(old.get("identity_revision") or 0):
            self._conn.execute("UPDATE people SET identity_revision=identity_revision+1 WHERE person_id=?", (person_id,))
            revision += 1
        return revision

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
        previous = self._descriptions(person_id)
        self._conn.execute(
            "DELETE FROM person_descriptions WHERE person_id = ?", (person_id,)
        )
        for slot in DESCRIPTION_SLOTS:
            entry = captured.get(slot)
            if not isinstance(entry, dict):
                continue
            text = entry.get("text")
            authority = str(entry.get("authority") or "assistant")
            source_refs = entry.get("source_refs") or []
            current = previous[slot]
            same = (
                current["text"] == text
                and current["authority"] == authority
                and current["source_refs"] == source_refs
            )
            slot_version = current["version"] if same else current["version"] + 1
            if text is None and authority == "assistant" and slot_version == 0:
                continue

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
                incoming,_conflicts=self._filter_provider_fields(existing['person_id'],{'first_name':first_name,'last_name':incoming_last_name,'title':title,'company':company})
                first_name,incoming_last_name,title,company=(incoming.get(field) for field in ('first_name','last_name','title','company'))
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
                from coworker.person_corrections import note_field_changes
                note_field_changes(self,existing["person_id"],existing)
                self._bump_identity_revision(existing["person_id"], existing)
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

    def _filter_provider_fields(self,person_id,incoming,*,provider='apollo',source_ref_id=None):
        from coworker.person_corrections import filter_provider_fields
        return filter_provider_fields(self,person_id,incoming,provider=provider,source_ref_id=source_ref_id)

    def apply_enrichment(self,person_id,*,name=None,title=None,company=None,email=None,linkedin_url=None,phone=None):
        with self.crm_transaction():
            return self._apply_enrichment_uncommitted(person_id,name=name,title=title,company=company,email=email,linkedin_url=linkedin_url,phone=phone)

    def _apply_enrichment_uncommitted(
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
        if not self._conn.in_transaction:
            raise RuntimeError("Enrichment filing requires a caller-owned transaction")
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
                "SELECT * FROM people WHERE person_id = ?",
                (person_id,),
            ).fetchone()
            if exists is None:
                return None
            incoming,conflicts=self._filter_provider_fields(person_id,{'first_name':first_name,'last_name':last_name,'title':title,'company':company,'email':email,'linkedin_url':linkedin_url,'phone':phone})
            first_name,last_name,title,company,email,linkedin_url,phone=(incoming.get(field) for field in ('first_name','last_name','title','company','email','linkedin_url','phone'))
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
            from coworker.person_corrections import note_field_changes
            note_field_changes(self,person_id,exists)
            self._bump_identity_revision(person_id, exists)
        return self.get(person_id)


    def record_apollo_enrichment(self,person_id,*,result,approval_id,credits: int | float | None,matched_on,actor="director",session_id=None,run_id=None):
        """Commit applied facts, protection conflicts, source and receipt together."""
        with self.crm_transaction():
            return self._record_apollo_enrichment_uncommitted(person_id,result=result,approval_id=approval_id,credits=credits,matched_on=matched_on,actor=actor,session_id=session_id,run_id=run_id)

    def _record_apollo_enrichment_uncommitted(
        self,
        person_id: str,
        *,
        result: dict[str, Any],
        approval_id: str,
        credits: int | float | None,
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
        digest=_fingerprint({key:result.get(key) for key in ("apolloId","name","title","organizationName","email","linkedinUrl","phone")})
        filed=self.get_apollo_enrichment_receipt(person_id,approval_id)
        if filed is not None:
            # A retried completion replays its receipt; it never reapplies facts over later corrections.
            if filed["result_digest"] not in (None,digest):
                raise TaskConflict("approval id already filed different provider facts",code="operation_conflict")
            return {**filed,"person":self.get(person_id),"replayed":True}
        before=self.get(person_id)
        person = self._apply_enrichment_uncommitted(
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
        supplied={field:value for field,value in (("name",result.get("name")),("title",result.get("title")),("company",result.get("organizationName")),("email",result.get("email")),("linkedin_url",result.get("linkedinUrl")),("phone",result.get("phone"))) if value}
        found=sorted(supplied)
        controls=(before or {}).get('identity_field_controls',{})
        applied=[]
        for field,value in supplied.items():
            if field=='name':
                parts=str(value).split(None,1)
                first=parts[0];last=parts[1] if len(parts)>1 else None
                if not any(controls.get(f,{}).get('protected') for f in ('first_name','last_name')) and person.get('first_name')==first and person.get('last_name')==last:applied.append('name')
                else:
                    for key,incoming in (('first_name',first),('last_name',last)):
                        if incoming and not controls.get(key,{}).get('protected') and person.get(key)==incoming:applied.append(key)
            elif not controls.get(field,{}).get('protected') and person.get(field)==value:applied.append(field)
        applied=sorted(applied)
        conflicts=[]
        for item in person.get('identity_conflicts',[]):
            field=item['field'];supplied_value=supplied.get(field)
            if field in ('first_name','last_name') and supplied.get('name'):
                parts=str(supplied['name']).split(None,1);supplied_value=parts[0] if field=='first_name' else parts[1] if len(parts)>1 else None
            if supplied_value==item['provider_value']:conflicts.append(item)
        source = self.upsert_attachment(
            person_id,
            record_type="source_ref",
            fields={
                "source": "apollo",
                "apollo_id": str(result.get("apolloId") or "") or None,
                "matched_on": matched_on,
                "approval_id": approval_id,
                "credits": credits,
                "fields_applied": applied,
                "fields_found": found,
                "fields_conflicting": sorted({item["field"] for item in conflicts}),
                "fetched_at": self._now(),
            },
            idempotency_key=f"apollo:enrich:{approval_id}",
            actor=actor,
            rationale_summary="Approved Apollo enrichment",
            session_id=session_id,
            run_id=run_id,
        )
        with self._lock:
            for item in conflicts:
                self._conn.execute('UPDATE person_correction_conflicts SET source_ref_id=? WHERE conflict_id=?',(source['id'],item['conflict_id']))
                item['source_ref_id']=source['id']
            self._receipt(
                person_id,
                kind="enrich",
                summary=f"Enriched from Apollo ({credits} credit{'' if credits == 1 else 's'})" if credits is not None else "Enriched from Apollo (actual credit cost not reported)",
                payload={
                    "approval_id": approval_id,
                    "apollo_id": str(result.get("apolloId") or "") or None,
                    "credits": credits,
                    "matched_on": matched_on,
                    "fields_applied": applied,
                    "source_ref_id": source["id"],
                    "result_digest": digest,
                },
                actor=actor,
                session_id=session_id,
                run_id=run_id,
                tool="apollo_enrich_contact",
            )
        return {
            "person": self.get(person_id),
            "source_ref": source,
            "fields_applied": applied,
            "fields_found": found,
            "conflicts": conflicts,
        }

    def get_apollo_enrichment_receipt(self,person_id,approval_id):
        """The filed receipt for one approved lookup, or None before it is filed."""
        with self._lock:
            rows=self._conn.execute("SELECT payload FROM events WHERE person_id=? AND kind='enrich' AND tool='apollo_enrich_contact'",(person_id,)).fetchall()
        payload=next((data for data in (json.loads(row[0] or "{}") for row in rows) if data.get("approval_id")==approval_id),None)
        if payload is None:
            return None
        with self._lock:
            source=next((item for item in self._attachments_for(person_id) if item["id"]==payload.get("source_ref_id")),None)
        if source is None:
            return None
        person=self.get(person_id) or {}
        return {
            "source_ref": source,
            "fields_applied": payload.get("fields_applied",[]),
            "fields_found": source["fields"].get("fields_found",[]),
            "conflicts": [item for item in person.get("identity_conflicts",[]) if item.get("source_ref_id")==source["id"]],
            "result_digest": payload.get("result_digest"),
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
        task_id: str | None = None,
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
        if task_id:
            # Recorded only. Sending does not complete the task.
            payload["task_id"] = task_id
        with self._lock:
            row = self._load_person_row(person_id)
            already = self._conn.execute(
                "SELECT event_id, kind, payload FROM events WHERE person_id = ? AND external_key = ?",
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
            elif already["kind"] == "mail":
                collected = json.loads(already["payload"])
                if collected.get("direction") != "outbound":
                    raise ValueError("Approved send conflicts with collected message direction")
                # Collection is read-only evidence, never a send approval. If
                # the sanctioned send path later files its receipt, preserve
                # that authority on the same event rather than losing it.
                self._conn.execute("""UPDATE events SET kind = 'send', summary = ?, payload = ?,
                    actor = ?, session_id = ?, run_id = ?, tool = 'gmail_send' WHERE event_id = ?""",
                    (f"Sent approved outreach to {to}", json.dumps({**collected, **payload}),
                     actor, session_id, run_id, already["event_id"]))
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
        self._require_audit(actor, "Gmail message could not be tied to one person.")
        idempotency_key = f"gmail:reply:{message_id}"
        fields = {
            "kind": "unassigned_reply",
            "provider": "Gmail",
            "evidence": "ambiguous",
            "message_id": message_id,
            "thread_id": thread_id,
            "reason": reason,
            "question": question,
            "candidate_count": int(candidate_count),
            "received_at": received_at,
        }
        # Mail classification can change as identity evidence changes. This
        # message-specific lifecycle must not relax generic attachment upserts.
        with self.crm_transaction() as conn:
            row = self._load_person_row(person_id)
            prior = conn.execute("""SELECT * FROM person_attachments
                WHERE person_id = ? AND record_type = 'knowledge_gap' AND idempotency_key = ?""",
                (person_id, idempotency_key)).fetchone()
            if prior and json.loads(prior["fields_json"]) == fields:
                return {"gap": self._attachment_dict(prior), "already_recorded": True}
            now = self._now()
            record_id = prior["id"] if prior else self._new_attachment_id("knowledge_gap")
            if prior:
                conn.execute("UPDATE person_attachments SET fields_json = ?, updated_at = ? WHERE id = ?",
                    (json.dumps(fields, sort_keys=True), now, record_id))
            else:
                conn.execute("""INSERT INTO person_attachments
                    (id, person_id, record_type, idempotency_key, fields_json, restricted, created_at, updated_at)
                    VALUES (?, ?, 'knowledge_gap', ?, ?, 0, ?, ?)""",
                    (record_id, person_id, idempotency_key, json.dumps(fields, sort_keys=True), now, now))
            version = int(row["version"]) + 1
            conn.execute("UPDATE people SET version = ?, updated_at = ? WHERE person_id = ?", (version, now, person_id))
            self._receipt(person_id, kind="reply_gap_updated" if prior else "knowledge_gap",
                summary="Gmail message could not be tied to one person.",
                payload={"id": record_id, "message_id": message_id, "reason": reason, "version": version},
                actor=actor, session_id=None, run_id=None)
            self._snapshot(person_id, version, now)
            stored = conn.execute("SELECT * FROM person_attachments WHERE id = ?", (record_id,)).fetchone()
            return {"gap": self._attachment_dict(stored), "already_recorded": False}


    def resolve_reply_gaps(self, *, message_id: str) -> None:
        """Resolve this message's warnings on all earlier candidates, retaining audit."""
        with self.crm_transaction() as conn:
            gaps = conn.execute("""SELECT a.* FROM person_attachments a JOIN people p ON p.person_id = a.person_id
                WHERE a.record_type = 'knowledge_gap' AND a.idempotency_key = ? AND p.deleted_at IS NULL""",
                (f"gmail:reply:{message_id}",)).fetchall()
            for gap in gaps:
                fields = json.loads(gap["fields_json"])
                if fields.get("resolved") or fields.get("kind") != "unassigned_reply":
                    continue
                person_id = gap["person_id"]
                now = self._now()
                fields.update({"resolved": True, "resolved_at": now})
                conn.execute("UPDATE person_attachments SET fields_json = ?, updated_at = ? WHERE id = ?",
                    (json.dumps(fields, sort_keys=True), now, gap["id"]))
                row = self._load_person_row(person_id)
                version = int(row["version"]) + 1
                conn.execute("UPDATE people SET version = ?, updated_at = ? WHERE person_id = ?", (version, now, person_id))
                self._receipt(person_id, kind="reply_gap_resolved", summary="Matched previously unassigned Gmail message.",
                    payload={"id": gap["id"], "message_id": message_id, "version": version}, actor="assistant",
                    session_id=None, run_id=None)
                self._snapshot(person_id, version, now)


    def mail_threads(self, person_id: str) -> list[dict[str, Any]]:
        """This person's Gmail threads, newest first.

        Only threads with an approved send to them or a reply verified as
        theirs. Replies we could not match to one person are not included.
        """
        threads: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        for event in self.timeline(person_id):
            payload = (
                event.get("payload") if isinstance(event.get("payload"), dict) else {}
            )
            thread_id = str(payload.get("thread_id") or "").strip()
            if not thread_id:
                continue
            if event.get("kind") == "send" and payload.get("sent"):
                direction = "outbound"
                at = str(event.get("created_at") or "") or None
            elif payload.get("direction") == "inbound":
                direction = "inbound"
                at = payload.get("received_at") or event.get("created_at")
            else:
                continue
            entry = threads.setdefault(
                thread_id,
                {"thread_id": thread_id, "subject": None, "message_count": 0},
            )
            subject = str(payload.get("subject") or "").strip()
            if subject:
                entry["subject"] = subject
            entry["message_count"] += 1
            entry["last_direction"] = direction
            entry["last_at"] = at
            entry["last_message_id"] = payload.get("message_id")
            entry["url"] = f"https://mail.google.com/mail/u/0/#all/{thread_id}"
            # Events are in write order, so the latest thread ends up last.
            if thread_id in order:
                order.remove(thread_id)
            order.append(thread_id)
        return [threads[thread_id] for thread_id in reversed(order)]

    def mail_state(self, person_id: str) -> dict[str, Any]:
        """Last contact, replied state, and whether the person needs attention.

        Compare message times, not filing order: a historical thread reread
        must not turn old incoming mail into a new unanswered reply. Each
        thread keeps its own reminder; replying on another thread cannot clear it.
        """
        mail = []
        for event in self.timeline(person_id):
            payload = (
                event.get("payload") if isinstance(event.get("payload"), dict) else {}
            )
            created = str(event.get("created_at") or "") or None
            direction = payload.get("direction")
            if event.get("kind") == "send" and payload.get("sent"):
                direction = "outbound"
            if direction not in {"inbound", "outbound"}:
                continue
            source_time = payload.get("received_at")
            at = source_time or (created if event.get("kind") == "send" or direction == "inbound" else None)
            known_time = bool(source_time or event.get("kind") == "send")
            try:
                stamp = datetime.fromisoformat(at)
            except (ValueError, TypeError):
                try:
                    stamp = parsedate_to_datetime(at)
                except (ValueError, TypeError):
                    stamp = datetime.min.replace(tzinfo=UTC)
                    known_time = False
            stamp = stamp.replace(tzinfo=stamp.tzinfo or UTC).astimezone(UTC)
            mail.append((stamp, len(mail), direction, at, payload, known_time))
        mail.sort(key=lambda item: (item[0], item[1]))
        incoming = [item for item in mail if item[2] == "inbound"]
        latest_incoming = {item[4].get("thread_id"): item for item in incoming}
        unanswered = []
        for thread, reply in latest_incoming.items():
            answered = any(item[2] == "outbound" and item[4].get("thread_id") == thread and item[5] and reply[5]
                and (item[0], item[1]) > (reply[0], reply[1])
                and (not item[4].get("in_reply_to") or item[4]["in_reply_to"] == reply[4].get("rfc_message_id"))
                for item in mail)
            if not answered:
                unanswered.append(reply[4].get("message_id"))
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT fields_json FROM person_attachments
                WHERE person_id = ? AND record_type = 'knowledge_gap'
                """,
                (person_id,),
            ).fetchall()
        gaps = [json.loads(str(row["fields_json"])) for row in rows]
        needs_review = any(gap.get("kind") == "unassigned_reply" and not gap.get("resolved") for gap in gaps)
        if unanswered:
            follow_up = {"needed": True, "reason": "reply_unanswered"}
        elif needs_review:
            follow_up = {"needed": True, "reason": "reply_needs_review"}
        else:
            follow_up = {"needed": False, "reason": None}
        return {
            "last_contact_at": mail[-1][3] if mail else None,
            "last_contact_direction": mail[-1][2] if mail else None,
            "replied": bool(incoming),
            "replied_at": incoming[-1][3] if incoming else None,
            "follow_up": follow_up,
            "unanswered_message_ids": unanswered,
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

    def _search_result(
        self, row: sqlite3.Row, *, match_kind: str
    ) -> dict[str, Any]:
        """Lean identity projection for the finder.

        Returns only identity columns plus sequence context. It never returns
        the stored email address (only ``has_email`` state), never exposes the
        masked Apollo surname as a name, and never touches attachments/sources,
        so restricted content can never leak through search.
        """
        person = self._person_dict(row)
        assert person is not None
        sequence_state = person.get("sequence_state")
        lane = sequence_state if sequence_state in SEQUENCE_STATES else "backlog"
        return {
            "person_id": person["person_id"],
            "first_name": person.get("first_name"),
            "last_name": person.get("last_name"),
            "last_name_status": person["last_name_status"],
            "title": person.get("title"),
            "company": person.get("company"),
            "sequence_state": sequence_state,
            "board_lane": lane,
            "has_email": bool(_clean(person.get("email"))),
            "apollo_id": person.get("apollo_id"),
            "match_kind": match_kind,
        }

    def search(self, text: str, *, limit: int = SEARCH_LIMIT_DEFAULT) -> list[dict[str, Any]]:
        """Find saved people by name, company, title, or known email.

        Searches every saved person (all board lanes, including ``backlog`` —
        people kept before outreach), excludes soft-deleted records, and is a
        pure read of the local ``people`` table: it imports nothing from Apollo
        or Gmail, so it can never fetch an email or spend an enrichment credit.

        Matching: a case-insensitive substring over first name, canonical last
        name, company, and title — every whitespace token must appear in at
        least one of those fields. The masked Apollo surname is never part of
        the predicate (a redacted surname is not a usable match key). Known
        email matches only when the whole query equals the stored address
        exactly; the address itself is never returned.
        """
        query = (text or "").strip()
        if not query:
            return []
        capped = min(max(int(limit or 0), 1), SEARCH_LIMIT_MAX)
        query_lower = query.lower()
        tokens = [token for token in query_lower.split() if token]
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM people
                WHERE deleted_at IS NULL
                ORDER BY updated_at DESC, person_id
                """
            ).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            first = str(row["first_name"] or "").lower()
            # Canonical surname only; the masked column is deliberately ignored.
            last = str(row["last_name"] or "").lower()
            company = str(row["company"] or "").lower()
            title = str(row["title"] or "").lower()
            email = str(row["email"] or "").lower()
            name_fields = f"{first} {last}"
            email_hit = bool(email) and email == query_lower
            token_hit = bool(tokens) and all(
                token in name_fields or token in company or token in title
                for token in tokens
            )
            if not (email_hit or token_hit):
                continue
            if email_hit:
                match_kind = "email"
            elif any(token in name_fields for token in tokens):
                match_kind = "name"
            elif any(token in company for token in tokens):
                match_kind = "company"
            else:
                match_kind = "title"
            results.append(self._search_result(row, match_kind=match_kind))

        def _exact_name_first(result: dict[str, Any]) -> int:
            full = " ".join(
                part
                for part in (result.get("first_name"), result.get("last_name"))
                if part
            ).strip().lower()
            return 0 if full and full == query_lower else 1

        # Stable sort keeps the SQL recency order within each group.
        results.sort(key=_exact_name_first)
        return results[:capped]

    def _identity_brief(self, person: dict[str, Any]) -> dict[str, Any]:
        """Lean identity fields shared by the finder and the shortlist badges."""
        sequence_state = person.get("sequence_state")
        lane = sequence_state if sequence_state in SEQUENCE_STATES else "backlog"
        return {
            "person_id": person["person_id"],
            "first_name": person.get("first_name"),
            "last_name": person.get("last_name"),
            "last_name_status": person.get("last_name_status"),
            "title": person.get("title"),
            "company": person.get("company"),
            "sequence_state": sequence_state,
            "board_lane": lane,
        }

    @staticmethod
    def _normalized_full_name(first: object, last: object) -> str:
        first_text = str(first or "").strip()
        last_text = str(last or "").strip()
        if not first_text or not last_text:
            return ""
        return " ".join(f"{first_text} {last_text}".lower().split())

    def resolve_shortlist(
        self, candidates: list[dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        """Classify Apollo shortlist candidates against saved identities.

        Keyed by each candidate's Apollo id:
        - ``resolved`` — an exact ``apollo_id`` match to a saved person. This is
          the authoritative "Already saved" signal, the same ``apollo_id``-keyed
          lookup the keep/curate path uses, so retry-Keep opens the same record.
        - ``possible`` — a candidate that is NOT saved by id but whose exact
          normalized full name matches one or more saved people. Shown as a
          "possible match" so a likely duplicate is never kept silently. It is
          deliberately conservative (exact first+last only, never a masked
          surname) to avoid false positives, and it never merges anything.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM people WHERE deleted_at IS NULL"
            ).fetchall()
        by_apollo: dict[str, dict[str, Any]] = {}
        by_name: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            person = self._person_dict(row)
            assert person is not None
            apollo_id = _clean(person.get("apollo_id"))
            if apollo_id:
                by_apollo[apollo_id] = person
            key = self._normalized_full_name(
                person.get("first_name"), person.get("last_name")
            )
            if key:
                by_name.setdefault(key, []).append(person)

        resolved: dict[str, dict[str, Any]] = {}
        possible: dict[str, list[dict[str, Any]]] = {}
        seen: set[str] = set()
        for candidate in candidates or []:
            if not isinstance(candidate, dict):
                continue
            apollo_id = _clean(candidate.get("apollo_id"))
            if not apollo_id or apollo_id in seen:
                continue
            seen.add(apollo_id)
            saved = by_apollo.get(apollo_id)
            if saved is not None:
                resolved[apollo_id] = self._identity_brief(saved)
                continue
            key = " ".join(str(candidate.get("name") or "").lower().split())
            matches = by_name.get(key) if key else None
            if matches:
                possible[apollo_id] = [
                    self._identity_brief(person) for person in matches
                ]
        return {"resolved": resolved, "possible": possible}

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
        from coworker.person_corrections import FIELDS,PersonCorrectionService
        identity_fields=set(fields)&set(FIELDS)
        if identity_fields:
            if set(fields)-set(FIELDS):raise ValueError('Correct person details separately from other person edits')
            if expected_version!=self._load_person_row(person_id)['version']:raise ValueError('stale record version')
            if actor!='director':raise ValueError('Use the verified person correction operation for identity changes')
            return PersonCorrectionService(self).correct(person_id,changes=fields,expected_version=expected_version,operation_id='person-patch:'+uuid.uuid4().hex,reason=rationale_summary,actor=actor,session_id=session_id,run_id=run_id)['person']
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
            self._bump_identity_revision(person_id, row)
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
        operation_id: str | None = None,
        human_requested: bool = False,
        source_refs: object = None,
        allowed_source_ids: set[str] | None = None,
    ) -> dict[str, Any]:
        """Save a slot through the same atomic writer used by CRM analysis."""
        with self._lock, self._conn:
            if not self._conn.in_transaction:
                self._conn.execute("BEGIN IMMEDIATE")
            return self._patch_description_uncommitted(
                person_id, slot, expected_version=expected_version, text=text,
                release=release, actor=actor, rationale_summary=rationale_summary,
                session_id=session_id, run_id=run_id, operation_id=operation_id,
                human_requested=human_requested, source_refs=source_refs,
                allowed_source_ids=allowed_source_ids,
            )

    def _patch_description_uncommitted(
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
        operation_id: str | None = None,
        human_requested: bool = False,
        source_refs: object = None,
        allowed_source_ids: set[str] | None = None,
        restore: dict[str, Any] | None = None,
        compensates_change_id: str | None = None,
    ) -> dict[str, Any]:
        """Save a description slot's text, or release it back to the assistant.

        Concurrency is slot-scoped: `expected_version` is checked against the
        slot's own version, so an edit to `general` never false-conflicts with
        an edit to `detailed` or the handoff. A director-requested text save
        flips authority to "human" (including an intentional blank); `release`
        flips it back to "assistant" and leaves the text in place for a later
        assistant writer. Either way the write takes a person snapshot and
        leaves a receipt, so audit and revert stay person-scoped.
        """
        if slot not in DESCRIPTION_SLOTS:
            raise ValueError(f"unknown description slot {slot}")
        self._require_audit(actor, rationale_summary)
        if isinstance(expected_version, bool) or not isinstance(expected_version, int) or expected_version < 0:
            raise ValueError("expected_version must be a nonnegative integer")
        human = actor == "director" or human_requested
        if release and not human:
            raise TaskConflict("only the director can release a human description", code="human_protected")
        if restore is not None:
            if restore.get("authority") not in {"human", "assistant"} or (restore.get("text") is not None and not isinstance(restore["text"], str)):
                raise ValueError("invalid description restore")
            text = restore.get("text")
        if not release:
            if not isinstance(text, str) and not (restore is not None and text is None):
                raise ValueError("a description save needs text or release")
            limit = DESCRIPTION_LIMITS[slot]
            if text is not None and len(text) > limit:
                raise ValueError(
                    f"{slot} description exceeds {limit} characters"
                )
        operation = _clean_operation_id(operation_id) if operation_id is not None else None
        if not self._conn.in_transaction:
            raise RuntimeError("description writer requires a transaction")
        self._load_person_row(person_id)
        refs = source_ids(self, person_id, source_refs, allowed_source_ids)
        fingerprint = _fingerprint({
            "op": "description.update", "person_id": person_id, "slot": slot,
            "expected_version": expected_version, "text": text, "release": release,
            "actor": actor, "human_requested": human_requested,
            "source_refs": refs, "reason": rationale_summary,
        })
        if restore is not None:
            fingerprint = _fingerprint({"base": fingerprint, "restore": restore, "compensates": compensates_change_id})
        if operation is not None:
            prior = self._conn.execute(
                "SELECT payload FROM events WHERE external_key = ?",
                (f"crm-operation:{operation}",),
            ).fetchone()
            task_operation = self._conn.execute(
                "SELECT 1 FROM crm_operations WHERE operation_id = ?", (operation,)
            ).fetchone()
            if task_operation:
                raise TaskConflict("operation id already belongs to a task change", code="operation_conflict")
            if prior:
                payload = json.loads(prior["payload"])
                if payload.get("fingerprint") != fingerprint:
                    raise TaskConflict("operation id already belongs to a different change", code="operation_conflict")
                response = payload["crm_response"]
                source_ids(self, person_id, response["description"]["source_refs"], allowed_source_ids)
                return {**response, "replayed": True}
        history_enabled = self._crm_description_history_ready()
        declared_version = int(self._conn.execute("PRAGMA user_version").fetchone()[0])
        if not history_enabled and not (human and restore is None and 0 <= declared_version < SCHEMA_VERSION):
            raise CrmMigrationRequired()
        # Existing releases saved human descriptions in event receipts only.
        # Keep that safe path available until the registered migration runs;
        # it cannot add v6 rows that historical migrations cannot express.
        current = self._conn.execute(
            "SELECT * FROM person_descriptions WHERE person_id = ? AND slot = ?",
            (person_id, slot),
        ).fetchone()
        current_version = int(current["version"]) if current is not None else 0
        if current_version != expected_version:
            raise TaskConflict(
                "stale record version: "
                f"expected {expected_version}, current {current_version}",
                code="description_conflict",
            )
        if current is not None:
            source_ids(self, person_id, json.loads(current["source_refs"]), allowed_source_ids)
            if not human and current["authority"] == "human":
                raise TaskConflict("human-edited description requires a director request", code="human_protected")
        now = self._now()
        next_slot_version = expected_version + 1
        if release:
            new_authority = "assistant"
            new_text = current["text"] if current is not None else None
            new_source_refs = (
                str(current["source_refs"]) if current is not None else "[]"
            )
        else:
            new_authority = restore["authority"] if restore is not None else "human" if human else "assistant"
            new_text = text
            new_source_refs = json.dumps(refs)
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
        description = self._descriptions(person_id)[slot]
        event_id = _new_event_id()
        change_id = "chg_" + uuid.uuid4().hex if history_enabled else event_id
        response = {"description": description, "change": {
            "change_id": change_id, "entity_type": "description",
            "entity_id": f"{person_id}:{slot}", "entity_version": next_slot_version,
            "actor": actor, "operation_id": operation,
        }, "replayed": False}
        payload = {
                "slot": slot,
                "authority": new_authority,
                "released": bool(release),
                "version": next_slot_version,
            }
        if operation is not None:
            payload.update(fingerprint=fingerprint, crm_response=response)
        self._conn.execute(
            """INSERT INTO events (event_id, person_id, source, kind, summary,
                   payload, actor, session_id, run_id, external_key)
               VALUES (?, ?, 'sourcecado', 'description', ?, ?, ?, ?, ?, ?)""",
            (event_id, person_id, rationale_summary.strip(), json.dumps(payload),
             actor, session_id, run_id, f"crm-operation:{operation}" if operation else None),
        )
        before = self._description_dict(current) if current is not None else self._default_description(slot)
        if history_enabled:
            self._conn.execute(
                """INSERT INTO crm_changes (change_id, person_id, entity_type, entity_id,
                       entity_version, action, before_json, after_json, actor, reason,
                       source_refs, operation_id, session_id, run_id, compensates_change_id, created_at)
                   VALUES (?, ?, 'description', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (change_id, person_id, f"{person_id}:{slot}", next_slot_version,
                 "undo" if compensates_change_id else "update", json.dumps(before), json.dumps(description), actor, rationale_summary.strip()[:500],
                 json.dumps(refs), operation or change_id, session_id, run_id, compensates_change_id, now),
            )
        self._snapshot(person_id, next_person_version, now)
        return response if operation is not None else description

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

        with self.crm_transaction():
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
        with self._lock, self._conn:
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
            if actor!='director' and any(row[field] != (target_last_name if field=='last_name' else target.get(field)) for field in ('first_name','last_name','title','company')):
                raise ValueError('Use the verified field-scoped person correction Undo for identity changes')
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
            from coworker.person_corrections import note_field_changes
            note_field_changes(self,person_id,row,human=actor=="director",reason=rationale_summary)
            self._bump_identity_revision(person_id, row)
            # Tasks and their history are independently versioned. Preserve the
            # current source rows they depend on, including current restrictions,
            # rather than replacing their evidence from a person snapshot.
            task_sources = set()
            for source_row in self._conn.execute("""SELECT source_refs FROM person_tasks WHERE person_id=?
                UNION ALL SELECT source_refs FROM crm_changes WHERE person_id=?
                UNION ALL SELECT source_refs FROM crm_proposals WHERE person_id=?""",
                (person_id, person_id, person_id)):
                task_sources.update(json.loads(source_row[0]))
            for desc in self._descriptions(person_id).values():
                task_sources.update(desc['source_refs'])
            task_sources.update(r[0] for r in self._conn.execute('SELECT id FROM person_attachments WHERE person_id=? AND restricted=1',(person_id,)))
            if self._conn.execute("SELECT 1 FROM sqlite_master WHERE name='crm_task_evidence'").fetchone():
                task_sources.update(r[0] for r in self._conn.execute("""SELECT e.source_id FROM crm_task_evidence e
                    JOIN crm_proposals p ON p.proposal_id=e.proposal_id WHERE p.person_id=?""", (person_id,)))
            preserved = {r[0] for r in self._conn.execute("SELECT id FROM person_attachments WHERE person_id=?", (person_id,))} & task_sources
            placeholders = ",".join("?" for _ in preserved)
            self._conn.execute("DELETE FROM person_attachments WHERE person_id=?" +
                (f" AND id NOT IN ({placeholders})" if preserved else ""), (person_id, *sorted(preserved)))
            for item in attachments:
                if item["id"] in preserved:
                    continue
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
