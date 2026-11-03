"""Explicit, reviewed person corrections with per-field authority and scoped Undo."""

from __future__ import annotations

import json
import re
import uuid
from urllib.parse import urlsplit, urlunsplit

from coworker.crm_sources import source_ids
from coworker.crm_tasks import (
    TaskConflict,
    TaskError,
    TaskNotFound,
    _clean_operation_id,
    _clean_reason,
    _fingerprint,
)

FIELDS = {
    "first_name": 100,
    "last_name": 150,
    "title": 200,
    "company": 200,
    "email": 254,
    "phone": 80,
    "linkedin_url": 500,
}
LABELS = {
    "first_name": "First name",
    "last_name": "Last name",
    "title": "Role",
    "company": "Company",
    "email": "Email",
    "phone": "Phone",
    "linkedin_url": "LinkedIn URL",
}
SCHEMA = (
    """CREATE TABLE IF NOT EXISTS person_field_controls(
        person_id TEXT NOT NULL REFERENCES people(person_id),field TEXT NOT NULL,
        revision INTEGER NOT NULL DEFAULT 0 CHECK(revision>=0),protected INTEGER NOT NULL DEFAULT 0 CHECK(protected IN (0,1)),
        source_refs TEXT NOT NULL DEFAULT '[]',reason TEXT NOT NULL DEFAULT '',updated_at TEXT NOT NULL,
        PRIMARY KEY(person_id,field))""",
    """CREATE TABLE IF NOT EXISTS person_correction_conflicts(
        conflict_id TEXT PRIMARY KEY,person_id TEXT NOT NULL REFERENCES people(person_id),field TEXT NOT NULL,
        saved_value TEXT,incoming_value TEXT,provider TEXT NOT NULL,field_revision INTEGER NOT NULL,
        source_ref_id TEXT, fingerprint TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'pending',created_at TEXT NOT NULL,
        UNIQUE(person_id,fingerprint))""",
)


def add_schema(conn):
    for sql in SCHEMA:
        conn.execute(sql)


def ready(people):
    return (
        people._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='person_field_controls'"
        ).fetchone()
        is not None
    )


def field_state(people, pid, field):
    person = people._conn.execute(
        "SELECT * FROM people WHERE person_id=?", (pid,)
    ).fetchone()
    if person is None:
        raise TaskNotFound("Unknown person")
    row = people._conn.execute(
        "SELECT * FROM person_field_controls WHERE person_id=? AND field=?",
        (pid, field),
    ).fetchone()
    return {
        "value": person[field],
        "revision": row["revision"] if row else 0,
        "protected": bool(row["protected"]) if row else False,
        "source_refs": json.loads(row["source_refs"]) if row else [],
    }


def normalize(changes):
    if not isinstance(changes, dict) or not changes or set(changes) - set(FIELDS):
        raise TaskError("Choose supported person details to correct")
    result = {}
    for field, value in changes.items():
        if value is not None and not isinstance(value, str):
            raise TaskError(LABELS[field] + " must be text")
        value = " ".join(value.split()) if isinstance(value, str) else None
        value = value or None
        if value and len(value) > FIELDS[field]:
            raise TaskError(LABELS[field] + " is too long")
        if field in {"first_name", "last_name"} and value and "*" in value:
            raise TaskError("An obfuscated Apollo name cannot be saved as a correction")
        if field == "email" and value:
            if not re.fullmatch(
                r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?",
                value,
            ):
                raise TaskError("Enter a complete email address")
            value = value.casefold()
        if (
            field == "phone"
            and value
            and (
                not re.fullmatch(r"[+\d() .-]+", value)
                or not 7 <= len(re.sub(r"\D", "", value)) <= 20
            )
        ):
            raise TaskError("Enter a valid phone number or leave it blank")
        if field == "linkedin_url" and value:
            url = urlsplit(value)
            if (
                url.scheme != "https"
                or url.hostname not in {"linkedin.com", "www.linkedin.com"}
                or url.username
                or url.password
                or url.port
                or not re.fullmatch(r"/in/[^/?#\s]+/?", url.path)
            ):
                raise TaskError("Use an https LinkedIn person profile URL")
            value = urlunsplit(
                ("https", "www.linkedin.com", url.path.rstrip("/"), "", "")
            )
        result[field] = value
    return result


def projection(people, pid):
    if not ready(people):
        return {}, []
    controls = {
        r["field"]: {"protected": bool(r["protected"]), "revision": r["revision"]}
        for r in people._conn.execute(
            "SELECT * FROM person_field_controls WHERE person_id=?", (pid,)
        )
    }
    conflicts = []
    for row in people._conn.execute(
        "SELECT * FROM person_correction_conflicts WHERE person_id=? AND state='pending' ORDER BY created_at,conflict_id",
        (pid,),
    ):
        if row["source_ref_id"]:
            try:
                source_ids(people, pid, [row["source_ref_id"]], set())
            except ValueError:
                continue
        item = dict(row)
        item["saved_value"] = json.loads(item["saved_value"])
        item["provider_value"] = json.loads(item.pop("incoming_value"))
        conflicts.append(item)
    return controls, conflicts


def filter_provider_fields(
    people, pid, incoming, *, provider="apollo", source_ref_id=None
):
    """Called under the owning person write lock; provider facts cannot grant authority."""
    if not ready(people):
        return dict(incoming), []
    allowed = dict(incoming)
    conflicts = []
    for field, value in incoming.items():
        if field not in FIELDS or value is None:
            continue
        saved = field_state(people, pid, field)
        if not saved["protected"]:
            continue
        allowed.pop(field, None)
        if value == saved["value"]:
            continue
        fingerprint = _fingerprint(
            {
                "field": field,
                "value": value,
                "revision": saved["revision"],
                "provider": provider,
            }
        )
        cid = "conflict_" + uuid.uuid4().hex
        people._conn.execute(
            """INSERT OR IGNORE INTO person_correction_conflicts
            (conflict_id,person_id,field,saved_value,incoming_value,provider,field_revision,source_ref_id,fingerprint,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                cid,
                pid,
                field,
                json.dumps(saved["value"]),
                json.dumps(value),
                provider,
                saved["revision"],
                source_ref_id,
                fingerprint,
                people._now(),
            ),
        )
        conflicts.append(
            {
                "field": field,
                "saved_value": saved["value"],
                "provider_value": value,
                "provider": provider,
                "source_ref_id": source_ref_id,
            }
        )
    return allowed, conflicts


class CorrectionConflict(TaskConflict):
    def __init__(self, message, *, code, review=None):
        super().__init__(message, code=code)
        self.review = review


class PersonCorrectionService:
    def __init__(self, people):
        self.people = people
        self.conn = people._conn

    def _check(self):
        self.people.require_crm_schema()
        if not ready(self.people):
            raise TaskError("Person corrections require the backed-up CRM migration")

    def _duplicates(self, pid, changes):
        result = []
        for row in self.conn.execute(
            "SELECT * FROM people WHERE person_id!=? AND deleted_at IS NULL ORDER BY person_id",
            (pid,),
        ):
            matches = []
            for field in ("email", "phone", "linkedin_url"):
                value = changes.get(field)
                if not value or not row[field]:
                    continue

                def key(v):
                    return (
                        re.sub(r"\D", "", v)
                        if field == "phone"
                        else v.casefold().rstrip("/")
                    )

                if key(value) == key(row[field]):
                    matches.append(field)
            if matches:
                result.append(
                    {
                        "person_id": row["person_id"],
                        "name": " ".join(
                            str(row[f] or "") for f in ("first_name", "last_name")
                        ).strip()
                        or "Saved person",
                        "company": row["company"],
                        "matched_fields": matches,
                        "version": row["version"],
                        "href": "#/people/" + row["person_id"],
                    }
                )
        return result

    def review(self, pid, changes, expected_version, *, restoring=False):
        self._check()
        changes = normalize(changes)
        with self.people._lock:
            person = self.people._load_person_row(pid)
            if (
                type(expected_version) is not int
                or expected_version != person["version"]
            ):
                raise CorrectionConflict(
                    "The saved person changed. Review the current details before saving.",
                    code="person_conflict",
                )
            if (
                not restoring
                and set(changes) & {"first_name", "last_name"}
                and not (
                    changes.get("first_name", person["first_name"])
                    or changes.get("last_name", person["last_name"])
                )
            ):
                raise TaskError("Keep a first or last name")
            duplicates = self._duplicates(pid, changes)
            fields = [
                {"field": f, "label": LABELS[f], "before": person[f], "after": v}
                for f, v in sorted(changes.items())
            ]
            token = _fingerprint(
                {
                    "person": pid,
                    "version": expected_version,
                    "changes": changes,
                    "duplicates": duplicates,
                }
            )
            return {
                "person_id": pid,
                "expected_version": expected_version,
                "changes": fields,
                "duplicates": duplicates,
                "collision_token": token,
                "identity_changed": any(
                    f in ("first_name", "last_name", "company", "email")
                    and person[f] != v
                    for f, v in changes.items()
                ),
            }

    def _confirm(self, review, token, confirmed):
        ids = sorted(d["person_id"] for d in review["duplicates"])
        if ids and (
            token != review["collision_token"]
            or not isinstance(confirmed, list)
            or sorted(confirmed) != ids
        ):
            raise CorrectionConflict(
                "Review the possible duplicate before saving this correction.",
                code="duplicate_review_required",
                review=review,
            )

    def correct(
        self,
        pid,
        *,
        changes,
        expected_version,
        operation_id,
        reason,
        actor="director",
        human_requested=False,
        source_refs=(),
        allowed_source_ids=None,
        collision_token=None,
        confirmed_duplicate_ids=None,
        session_id=None,
        run_id=None,
    ):
        if actor != "director" and not human_requested:
            raise TaskConflict(
                "A person correction requires the director", code="human_protected"
            )
        self._check()
        changes = normalize(changes)
        operation = _clean_operation_id(operation_id)
        reason = _clean_reason(reason, "Director corrected person details")
        refs = source_ids(self.people, pid, source_refs, allowed_source_ids)
        fingerprint = _fingerprint(
            {
                "person_correction": pid,
                "changes": changes,
                "version": expected_version,
                "actor": actor,
                "reason": reason,
                "refs": refs,
                "collision_token": collision_token,
                "confirmed": confirmed_duplicate_ids or [],
            }
        )
        from coworker.crm_service import CrmService

        crm = CrmService(self.people)
        with self.people.crm_transaction():
            replay = crm.tasks._replay(operation, fingerprint)
            if replay:
                return crm._permitted_receipt(pid, replay, allowed_source_ids)
            review = self.review(pid, changes, expected_version)
            self._confirm(review, collision_token, confirmed_duplicate_ids)
            source_ids(self.people, pid, refs, allowed_source_ids)
            before = {f: field_state(self.people, pid, f) for f in changes}
            for state in before.values():
                source_ids(self.people, pid, state["source_refs"], allowed_source_ids)
            old = dict(self.people._load_person_row(pid))
            now = self.people._now()
            self.conn.execute(
                "UPDATE people SET "
                + ",".join(f + "=?" for f in changes)
                + ",version=version+1,updated_at=? WHERE person_id=?",
                (*changes.values(), now, pid),
            )
            after = {}
            for field, value in changes.items():
                revision = before[field]["revision"] + 1
                self.conn.execute(
                    """INSERT INTO person_field_controls(person_id,field,revision,protected,source_refs,reason,updated_at) VALUES(?,?,?,1,?,?,?)
                    ON CONFLICT(person_id,field) DO UPDATE SET revision=excluded.revision,protected=1,source_refs=excluded.source_refs,reason=excluded.reason,updated_at=excluded.updated_at""",
                    (pid, field, revision, json.dumps(refs), reason, now),
                )
                after[field] = field_state(self.people, pid, field)
                self.conn.execute(
                    "UPDATE person_correction_conflicts SET state='superseded' WHERE person_id=? AND field=? AND state='pending'",
                    (pid, field),
                )
            self.people._bump_identity_revision(pid, old)
            person = self.people.get(pid)
            self.people._snapshot(pid, person["version"], now)
            response = crm._record(
                pid,
                "person_details",
                pid,
                person["version"],
                {"fields": before},
                {"fields": after},
                operation=operation,
                fingerprint=fingerprint,
                actor=actor,
                reason=reason,
                sources=refs,
                session_id=session_id,
                run_id=run_id,
            )
            response["person"] = person
            self.conn.execute(
                "UPDATE crm_operations SET response_json=? WHERE operation_id=?",
                (json.dumps(response), operation),
            )
            return response

    def can_undo(self, row):
        if row["entity_type"] != "person_details":
            return False
        after = json.loads(row["after_json"])["fields"]
        return all(
            field_state(self.people, row["person_id"], f) == state
            for f, state in after.items()
        )

    def undo(
        self,
        pid,
        change_id,
        *,
        operation_id,
        actor="director",
        human_requested=False,
        reason=None,
        allowed_source_ids=None,
        **audit,
    ):
        if actor != "director" and not human_requested:
            raise TaskConflict("Undo requires the director", code="human_protected")
        self._check()
        operation = _clean_operation_id(operation_id)
        fingerprint = _fingerprint(
            {
                "person_correction_undo": pid,
                "change": change_id,
                "actor": actor,
                "collision_token": audit.get("collision_token"),
                "confirmed": audit.get("confirmed_duplicate_ids") or [],
            }
        )
        from coworker.crm_service import CrmService

        crm = CrmService(self.people)
        with self.people.crm_transaction():
            replay = crm.tasks._replay(operation, fingerprint)
            if replay:
                return crm._permitted_receipt(pid, replay, allowed_source_ids)
            row = self.conn.execute(
                "SELECT * FROM crm_changes WHERE person_id=? AND change_id=? AND entity_type='person_details'",
                (pid, change_id),
            ).fetchone()
            if not row:
                raise TaskNotFound("Unknown person correction")
            refs = source_ids(
                self.people, pid, json.loads(row["source_refs"]), allowed_source_ids
            )
            if not self.can_undo(row):
                raise TaskConflict(
                    "A corrected field changed again. The newer edit is kept.",
                    code="person_conflict",
                )
            before = json.loads(row["before_json"])["fields"]
            for state in before.values():
                source_ids(self.people, pid, state["source_refs"], allowed_source_ids)
            current = json.loads(row["after_json"])["fields"]
            old = dict(self.people._load_person_row(pid))
            now = self.people._now()
            review = self.review(
                pid,
                {f: v["value"] for f, v in before.items()},
                old["version"],
                restoring=True,
            )
            self._confirm(
                review,
                audit.get("collision_token"),
                audit.get("confirmed_duplicate_ids"),
            )
            values = {f: state["value"] for f, state in before.items()}
            self.conn.execute(
                "UPDATE people SET "
                + ",".join(f + "=?" for f in values)
                + ",version=version+1,updated_at=? WHERE person_id=?",
                (*values.values(), now, pid),
            )
            after = {}
            for field, state in before.items():
                self.conn.execute(
                    "UPDATE person_field_controls SET revision=revision+1,protected=?,source_refs=?,reason=?,updated_at=? WHERE person_id=? AND field=?",
                    (
                        int(state["protected"]),
                        json.dumps(state["source_refs"]),
                        reason or "Undid person correction",
                        now,
                        pid,
                        field,
                    ),
                )
                after[field] = field_state(self.people, pid, field)
            self.people._bump_identity_revision(pid, old)
            person = self.people.get(pid)
            self.people._snapshot(pid, person["version"], now)
            result = crm._record(
                pid,
                "person_details",
                pid,
                person["version"],
                {"fields": current},
                {"fields": after},
                operation=operation,
                fingerprint=fingerprint,
                actor=actor,
                reason=reason or "Undid person correction",
                sources=refs,
                action="undo",
                compensates=change_id,
                session_id=audit.get("session_id"),
                run_id=audit.get("run_id"),
            )
            result["person"] = person
            self.conn.execute(
                "UPDATE crm_operations SET response_json=? WHERE operation_id=?",
                (json.dumps(result), operation),
            )
            return result


def verify_director_correction(text, changes, person, confirmed_duplicate_ids=None):
    """Positive current-channel grammar; model arguments never mint authority."""
    if not isinstance(text, str) or not isinstance(changes, dict):
        return False
    command = re.sub(r"^please\s+", "", text.strip(), flags=re.I)
    confirmation = re.search(r"\s+(?:I\s+)?confirm\s+duplicates?\b", command, re.I)
    suffix = command[confirmation.start() :] if confirmation else ""
    command = command[: confirmation.start()].rstrip(" .") if confirmation else command
    confirmed = set(confirmed_duplicate_ids or ())
    if confirmed != set(re.findall(r"\bper_[0-9a-f]{32}\b", suffix)):
        return False
    start = re.match(r"^(change|set|update|correct|clear)\s+", command, re.I)
    if not start:
        return False
    rest = command[start.end() :]
    names = "first name|last name|linkedin(?: url)?|company|role|title|email|phone|name"
    match = re.search(r"\b(" + names + r")\b(?:\s+to\s*:?|\s*:)\s*", rest, re.I)
    if not match:
        if start[1].casefold() != "clear":
            return False
        match = re.search(r"\b(" + names + r")\b[.!]?$", rest, re.I)
        if not match:
            return False
    prefix = re.sub(r"['’]s$", "", rest[: match.start()].strip(), flags=re.I).strip()
    if prefix.casefold().startswith("the "):
        prefix = prefix[4:]
    allowed = {
        "",
        "this person",
        "this contact",
        "person",
        "contact",
        str(person["person_id"]).casefold(),
        str(person.get("first_name") or "").casefold(),
        " ".join(str(person.get(k) or "") for k in ("first_name", "last_name"))
        .strip()
        .casefold(),
    }
    if prefix.casefold() not in allowed:
        return False
    clauses = rest[match.start() :]
    chunks = re.split(
        r"\s+and\s+(?=(?:" + names + r")\s+(?:to\b|:))|;\s*", clauses, flags=re.I
    )
    parsed = {}
    mapping = {
        "first name": "first_name",
        "last name": "last_name",
        "role": "title",
        "linkedin": "linkedin_url",
        "linkedin url": "linkedin_url",
    }
    for chunk in chunks:
        if start[1].casefold() == "clear":
            item = re.fullmatch("(" + names + r")[.!]?", chunk, re.I)
            if not item:
                return False
            field = mapping.get(item[1].casefold(), item[1].casefold())
            parsed[field] = None
            continue
        item = re.fullmatch("(" + names + r")(?:\s+to\s*:?|\s*:)\s*(.+)", chunk, re.I)
        if not item:
            return False
        field = mapping.get(item[1].casefold(), item[1].casefold())
        value = item[2].strip()
        if field == "name":
            parts = value.split(None, 1)
            parsed["first_name"] = parts[0]
            parsed["last_name"] = parts[1] if len(parts) > 1 else None
        else:
            parsed[field] = value
    try:
        actual = normalize(changes)
        for field, value in parsed.items():
            if (
                isinstance(value, str)
                and value.endswith(".")
                and value[:-1] == changes.get(field)
            ):
                parsed[field] = value[:-1]
        return normalize(parsed) == actual
    except (ValueError, TypeError):
        return False


def note_field_changes(
    people, pid, before, *, human=False, reason="Provider facts changed"
):
    """Count real later field edits, including ABA through snapshot restoration."""
    if not ready(people):
        return
    current = dict(people._load_person_row(pid))
    before = dict(before)
    for field in FIELDS:
        if before.get(field) == current.get(field):
            continue
        row = people._conn.execute(
            "SELECT * FROM person_field_controls WHERE person_id=? AND field=?",
            (pid, field),
        ).fetchone()
        if row or human:
            people._conn.execute(
                """INSERT INTO person_field_controls(person_id,field,revision,protected,reason,updated_at) VALUES(?,?,1,?,?,?)
                ON CONFLICT(person_id,field) DO UPDATE SET revision=revision+1,protected=CASE WHEN ? THEN 1 ELSE protected END,reason=excluded.reason,updated_at=excluded.updated_at""",
                (pid, field, int(human), reason, people._now(), int(human)),
            )
