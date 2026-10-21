"""Person tasks — the commitments the director keeps for each person.

One task record backs both the global Tasks view in Contacts and the task list
in a person file, so an edit in either place is the same saved row. Tasks live
in `people.db` beside the person they belong to and share the PersonStore
connection and lock, so a task, its change history and its operation receipt
commit in one transaction.

Every write carries an operation id. Replaying the same operation returns the
original receipt instead of writing again; reusing the id for different
arguments is a conflict. Edits are checked against the task's own version, so
new evidence on the person never invalidates an open task edit.

A state change (complete, reopen, cancel) is an edit of the `state` field and
goes through the same path. Undo writes a new compensating change that
restores the values the undone change replaced; it never deletes history,
and it refuses when the task has moved past the version that change produced.

Email analysis uses caller-owned no-commit primitives so task mutations can
commit atomically with descriptions, proposals and the analysis checkpoint.
Automatic creation Undo cancels the inferred task and suppresses its evidence.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from coworker.crm_sources import source_ids as _source_ids
from coworker.crm_sources import source_links, sources_visible

if TYPE_CHECKING:
    from coworker.people import PersonStore

TASK_STATES = ("open", "completed", "cancelled")
TASK_ORIGINS = ("human", "assistant")
TASK_EDIT_FIELDS = ("title", "details", "due_date")
TASK_CHANGE_FIELDS = (*TASK_EDIT_FIELDS, "state")
# Which state may follow which, and the history action that records it.
TASK_TRANSITIONS = {
    ("open", "completed"): "complete",
    ("open", "cancelled"): "cancel",
    ("completed", "open"): "reopen",
    ("cancelled", "open"): "reopen",
}
# The task fields an undo restores from the undone change's "before" record.
UNDO_FIELDS = ("title", "details", "due_date", "due_timezone", "state", "completed_at")
TITLE_MAX = 200
DETAILS_MAX = 4000
REASON_MAX = 500
DEFAULT_TIMEZONE = "America/Los_Angeles"
TIMEZONE_SETTING = "operator_timezone"
LIST_LIMIT_DEFAULT = 200
LIST_LIMIT_MAX = 500

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_OPERATION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
_TASK_ID_RE = re.compile(r"^task_[0-9a-f]{32}$")
_CHANGE_ID_RE = re.compile(r"^chg_[0-9a-f]{32}$")

# The column list of crm_changes, shared by the constructor DDL and the 3->4
# rebuild in migrations.py so a fresh install and a migrated one match.
CRM_CHANGES_V5_COLUMNS = """
        change_id TEXT PRIMARY KEY,
        person_id TEXT NOT NULL REFERENCES people(person_id),
        entity_type TEXT NOT NULL CHECK (entity_type IN ('task')),
        entity_id TEXT NOT NULL,
        entity_version INTEGER NOT NULL CHECK (entity_version >= 1),
        action TEXT NOT NULL CHECK (
            action IN ('create', 'update', 'complete', 'reopen', 'cancel', 'undo')
        ),
        before_json TEXT,
        after_json TEXT NOT NULL,
        actor TEXT NOT NULL CHECK (actor IN ('director', 'assistant')),
        reason TEXT NOT NULL,
        source_refs TEXT NOT NULL DEFAULT '[]',
        operation_id TEXT NOT NULL,
        session_id TEXT,
        run_id TEXT,
        compensates_change_id TEXT REFERENCES crm_changes(change_id),
        created_at TEXT NOT NULL
"""
CRM_CHANGES_COLUMNS = CRM_CHANGES_V5_COLUMNS.replace("IN ('task')", "IN ('task', 'description')")
CRM_CHANGES_INDEX_STATEMENTS: tuple[str, ...] = (
    """
    CREATE INDEX IF NOT EXISTS crm_changes_entity
    ON crm_changes(entity_type, entity_id, created_at)
    """,
)

# Executed one statement at a time, never through `executescript`, which
# commits first: the migration step runs these inside its own transaction and
# must be able to roll them back.
CRM_TASK_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS person_tasks (
        task_id TEXT PRIMARY KEY,
        person_id TEXT NOT NULL REFERENCES people(person_id),
        title TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
        details TEXT NOT NULL DEFAULT '' CHECK (length(details) <= 4000),
        due_date TEXT CHECK (
            due_date IS NULL
            OR due_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
        ),
        due_timezone TEXT,
        state TEXT NOT NULL DEFAULT 'open'
            CHECK (state IN ('open', 'completed', 'cancelled')),
        origin TEXT NOT NULL CHECK (origin IN ('human', 'assistant')),
        protected_fields TEXT NOT NULL DEFAULT '[]',
        source_refs TEXT NOT NULL DEFAULT '[]',
        version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        completed_at TEXT,
        CHECK ((due_date IS NULL) = (due_timezone IS NULL))
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS person_tasks_person_state_due
    ON person_tasks(person_id, state, due_date)
    """,
    """
    CREATE INDEX IF NOT EXISTS person_tasks_state_due
    ON person_tasks(state, due_date, task_id)
    """,
    f"CREATE TABLE IF NOT EXISTS crm_changes ({CRM_CHANGES_COLUMNS})",
    *CRM_CHANGES_INDEX_STATEMENTS,
    """
    CREATE TABLE IF NOT EXISTS crm_operations (
        operation_id TEXT PRIMARY KEY,
        fingerprint TEXT NOT NULL,
        change_id TEXT NOT NULL REFERENCES crm_changes(change_id),
        entity_type TEXT NOT NULL,
        entity_id TEXT NOT NULL,
        response_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
)


class TaskError(ValueError):
    """Invalid input. Maps to HTTP 400."""


class TaskNotFound(TaskError):
    """Unknown, deleted or out-of-scope person or task. Maps to HTTP 404."""


def source_ids(*args: Any) -> list[str]:
    try:
        return _source_ids(*args)
    except ValueError as exc:
        raise TaskError(str(exc)) from exc


class TaskConflict(TaskError):
    """A stale version or a reused operation id. Maps to HTTP 409.

    `current` carries the newer saved task when the conflict is a stale edit,
    so a client can show it without discarding the director's unsaved text.
    """

    def __init__(
        self, message: str, *, code: str, current: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.current = current


def validate_timezone(name: object) -> str:
    if not isinstance(name, str) or not name.strip():
        raise TaskError("timezone must be an IANA name such as America/Los_Angeles")
    name = name.strip()
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise TaskError(f"unknown timezone {name!r}") from exc
    return name


def today_in(timezone: str, *, now: datetime | None = None) -> date:
    moment = now if now is not None else datetime.now(UTC)
    return moment.astimezone(ZoneInfo(timezone)).date()


def _clean_title(value: object) -> str:
    if not isinstance(value, str):
        raise TaskError("title is required")
    title = " ".join(value.split())
    if not title:
        raise TaskError("title is required")
    if len(title) > TITLE_MAX:
        raise TaskError(f"title is limited to {TITLE_MAX} characters")
    return title


def _clean_details(value: object) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise TaskError("details must be text")
    details = value.strip()
    if len(details) > DETAILS_MAX:
        raise TaskError(f"details are limited to {DETAILS_MAX} characters")
    return details


def _clean_due_date(value: object) -> str | None:
    """A calendar date, or nothing. Never a time and never a guessed date."""
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not _DATE_RE.match(value):
        raise TaskError("due_date must be a calendar date in YYYY-MM-DD form")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise TaskError(f"{value} is not a real calendar date") from exc
    return parsed.isoformat()


def _clean_reason(value: object, fallback: str) -> str:
    reason = " ".join(str(value or "").split()) or fallback
    return reason[:REASON_MAX]


def _clean_operation_id(value: object) -> str:
    if not isinstance(value, str) or not _OPERATION_RE.match(value):
        raise TaskError("operation_id is required (8-128 letters, digits, . _ : -)")
    return value


def _fingerprint(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def due_status(due: str | None, today: date) -> str:
    if due is None:
        return "none"
    when = date.fromisoformat(due)
    if when < today:
        return "overdue"
    if when == today:
        return "today"
    return "upcoming"


_DUE_RANK = {"overdue": 0, "today": 1, "upcoming": 2, "none": 3}
_DEFAULT_REASON = {
    "update": "Edited a task",
    "complete": "Marked done",
    "reopen": "Reopened",
    "cancel": "Cancelled",
}


class TaskService:
    """The one write path for tasks, shared by HTTP, chat and email analysis."""

    def __init__(self, people: PersonStore) -> None:
        self._people = people
        # Shared with PersonStore on purpose: a task commits in the same
        # transaction authority as the person it belongs to.
        self._conn = people._conn
        self._lock = people._lock

    # --- reads --------------------------------------------------------------

    def _task_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "task_id": str(row["task_id"]),
            "person_id": str(row["person_id"]),
            "title": str(row["title"]),
            "details": str(row["details"] or ""),
            "due_date": row["due_date"],
            "due_timezone": row["due_timezone"],
            "state": str(row["state"]),
            "origin": str(row["origin"]),
            "protected_fields": json.loads(str(row["protected_fields"] or "[]")),
            "source_refs": json.loads(str(row["source_refs"] or "[]")),
            "version": int(row["version"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "completed_at": row["completed_at"],
        }

    def _live_person(self, person_id: str) -> sqlite3.Row:
        row = self._conn.execute(
            "SELECT * FROM people WHERE person_id = ? AND deleted_at IS NULL",
            (person_id,),
        ).fetchone()
        if row is None:
            raise TaskNotFound("unknown person")
        return row

    def _load_task(self, person_id: str, task_id: str) -> sqlite3.Row:
        row = self._conn.execute(
            """
            SELECT person_tasks.* FROM person_tasks
            JOIN people ON people.person_id = person_tasks.person_id
            WHERE person_tasks.task_id = ? AND person_tasks.person_id = ?
              AND people.deleted_at IS NULL
            """,
            (task_id, person_id),
        ).fetchone()
        if row is None:
            raise TaskNotFound("unknown task")
        return row

    def get(self, person_id: str, task_id: str, *, allowed_source_ids: set[str] | None = None) -> dict[str, Any]:
        with self._lock:
            return self._project_task(self._task_dict(self._load_task(person_id, task_id)), allowed_source_ids)

    def _project_task(self, task: dict, allowed: set[str] | None = None) -> dict:
        return {**task, "source_links": source_links(self._people, task["person_id"], task["source_refs"], allowed)}

    def _can_undo(self, row: Any) -> bool:
        task = self._load_task(row["person_id"], row["entity_id"])
        if task["version"] != row["entity_version"]:
            return False
        if row["action"] != "create":
            return row["before_json"] is not None
        return self._automatic_creation(row["change_id"])

    def _automatic_creation(self, change_id: str) -> bool:
        # Only the durable analysis binding authorizes creation Undo. Merely
        # executing as assistant (e.g. chat) is not automatic authority.
        return self._conn.execute("""SELECT 1 FROM crm_proposals WHERE change_id=?
            AND target_kind='task_create' AND resolution='automatic' AND state='accepted'""", (change_id,)).fetchone() is not None

    def _project_response(self, response: dict, allowed: set[str] | None = None) -> dict:
        response = {**response, "task": self._project_task(response["task"], allowed)}
        if response.get("change"):
            row = self._conn.execute("SELECT * FROM crm_changes WHERE change_id=?", (response["change"]["change_id"],)).fetchone()
            response["change"] = {**response["change"], "source_links": source_links(self._people, row["person_id"], json.loads(row["source_refs"]), allowed), "can_undo": self._can_undo(row)}
        return response

    def list(
        self,
        *,
        timezone: str,
        person_id: str | None = None,
        states: tuple[str, ...] = ("open",),
        active_sequences_only: bool = False,
        limit: int = LIST_LIMIT_DEFAULT,
        now: datetime | None = None,
        allowed_source_ids: set[str] | None = None,
        permitted_only: bool = False,
    ) -> dict[str, Any]:
        """Tasks in due order: overdue, today, upcoming, then undated.

        Dated work sorts by date, then creation time and id; undated work by
        creation time and id. The global view passes
        `active_sequences_only` so a kept person who has not started a
        sequence does not appear in Contacts through a task.
        """
        unknown = set(states) - set(TASK_STATES)
        if not states or unknown:
            raise TaskError("state must be open, completed or cancelled")
        limit = max(1, min(int(limit), LIST_LIMIT_MAX))
        clauses = ["people.deleted_at IS NULL"]
        params: list[Any] = []
        clauses.append(f"person_tasks.state IN ({', '.join('?' for _ in states)})")
        params.extend(states)
        if person_id is not None:
            clauses.append("person_tasks.person_id = ?")
            params.append(person_id)
        if active_sequences_only:
            clauses.append("people.sequence_state IN ('open', 'in_conversation', 'done')")
        with self._lock:
            if person_id is not None:
                self._live_person(person_id)
            rows = self._conn.execute(
                f"""
                SELECT person_tasks.*,
                       people.first_name AS person_first_name,
                       people.last_name AS person_last_name,
                       people.company AS person_company,
                       people.sequence_state AS person_sequence_state
                FROM person_tasks
                JOIN people ON people.person_id = person_tasks.person_id
                WHERE {" AND ".join(clauses)}
                ORDER BY person_tasks.due_date IS NULL,
                         person_tasks.due_date,
                         person_tasks.created_at,
                         person_tasks.task_id
                LIMIT ?
                """,
                (*params, limit + 1),
            ).fetchall()
        today = today_in(timezone, now=now)
        tasks = []
        for row in rows[:limit]:
            task = self._task_dict(row)
            if permitted_only and not sources_visible(self._people, task["person_id"], task["source_refs"], allowed_source_ids):
                continue
            task = self._project_task(task, allowed_source_ids)
            task["due_status"] = due_status(task["due_date"], today)
            task["person"] = {
                "person_id": task["person_id"],
                "first_name": row["person_first_name"],
                "last_name": row["person_last_name"],
                "company": row["person_company"],
                "sequence_state": row["person_sequence_state"],
            }
            tasks.append(task)
        # SQL already orders by date; this keeps the four groups contiguous
        # even if a stored date compares oddly as text.
        tasks.sort(
            key=lambda item: (
                _DUE_RANK[item["due_status"]],
                item["due_date"] or "",
                item["created_at"],
                item["task_id"],
            )
        )
        return {
            "tasks": tasks,
            "truncated": len(rows) > limit,
            "timezone": timezone,
            "today": today.isoformat(),
        }

    # --- operation receipts ------------------------------------------------

    def _replay(self, operation_id: str, fingerprint: str, *, legacy_fingerprint: str | None = None) -> dict[str, Any] | None:
        if self._conn.execute("SELECT 1 FROM events WHERE external_key = ?",
                              (f"crm-operation:{operation_id}",)).fetchone():
            raise TaskConflict("operation id already belongs to a description change", code="operation_conflict")
        row = self._conn.execute(
            "SELECT fingerprint, response_json FROM crm_operations WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
        if row is None:
            return None
        if str(row["fingerprint"]) not in {fingerprint, legacy_fingerprint}:
            raise TaskConflict(
                "this operation id was already used for a different change",
                code="operation_conflict",
            )
        response = json.loads(str(row["response_json"]))
        response["replayed"] = True
        return response

    def _record(
        self,
        *,
        operation_id: str,
        fingerprint: str,
        person_id: str,
        task: dict[str, Any],
        action: str,
        before: dict[str, Any] | None,
        actor: str,
        reason: str,
        session_id: str | None,
        run_id: str | None,
        now: str,
        compensates: str | None = None,
    ) -> dict[str, Any]:
        change_id = "chg_" + uuid.uuid4().hex
        change = {
            "change_id": change_id,
            "entity_type": "task",
            "entity_id": task["task_id"],
            "entity_version": task["version"],
            "action": action,
            "actor": actor,
            "reason": reason,
            "operation_id": operation_id,
            "compensates_change_id": compensates,
            "created_at": now,
        }
        self._conn.execute(
            """
            INSERT INTO crm_changes (
                change_id, person_id, entity_type, entity_id, entity_version,
                action, before_json, after_json, actor, reason, source_refs,
                operation_id, session_id, run_id, compensates_change_id,
                created_at
            ) VALUES (?, ?, 'task', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                change_id,
                person_id,
                task["task_id"],
                task["version"],
                action,
                json.dumps(before, sort_keys=True) if before is not None else None,
                json.dumps(task, sort_keys=True),
                actor,
                reason,
                json.dumps(task["source_refs"]),
                operation_id,
                session_id,
                run_id,
                compensates,
                now,
            ),
        )
        response = {"task": task, "change": change, "replayed": False}
        self._conn.execute(
            """
            INSERT INTO crm_operations (
                operation_id, fingerprint, change_id, entity_type, entity_id,
                response_json, created_at
            ) VALUES (?, ?, ?, 'task', ?, ?, ?)
            """,
            (
                operation_id,
                fingerprint,
                change_id,
                task["task_id"],
                json.dumps(response, sort_keys=True),
                now,
            ),
        )
        return response

    def changes(self, task_id: str, *, allowed_source_ids: set[str] | None = None) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT *
                FROM crm_changes
                WHERE entity_type = 'task' AND entity_id = ?
                ORDER BY created_at, entity_version
                """,
                (task_id,),
            ).fetchall()
            result = []
            for row in rows:
                refs = json.loads(row["source_refs"])
                if not sources_visible(self._people, row["person_id"], refs, allowed_source_ids):
                    continue
                item = {key: row[key] for key in ("change_id", "entity_version", "action", "actor", "reason", "operation_id", "compensates_change_id", "created_at")}
                item.update(source_links=source_links(self._people, row["person_id"], refs, allowed_source_ids), can_undo=self._can_undo(row))
                result.append(item)
            return result

    # --- writes -------------------------------------------------------------

    def create(self, person_id: str, **kwargs: Any) -> dict[str, Any]:
        with self._lock, self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            response = self._create_uncommitted(person_id, **kwargs)
        return self._project_response(response, kwargs.get("allowed_source_ids"))

    def update(self, person_id: str, task_id: str, **kwargs: Any) -> dict[str, Any]:
        with self._lock, self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            response = self._update_uncommitted(person_id, task_id, **kwargs)
        return self._project_response(response, kwargs.get("allowed_source_ids"))

    def _create_uncommitted(
        self,
        person_id: str,
        *,
        title: object,
        details: object = None,
        due_date: object = None,
        timezone: str,
        operation_id: object,
        actor: str,
        reason: object = None,
        session_id: str | None = None,
        run_id: str | None = None,
        human_requested: bool = False,
        source_refs: object = None,
        allowed_source_ids: set[str] | None = None,
    ) -> dict[str, Any]:
        if actor not in ("director", "assistant"):
            raise TaskError(f"invalid actor {actor}")
        operation = _clean_operation_id(operation_id)
        clean = {
            "title": _clean_title(title),
            "details": _clean_details(details),
            "due_date": _clean_due_date(due_date),
        }
        timezone = validate_timezone(timezone)
        origin = "human" if actor == "director" or human_requested else "assistant"
        # A director's own words are protected from later automatic rewrites.
        protected = (
            sorted(field for field in TASK_EDIT_FIELDS if clean[field])
            if origin == "human"
            else []
        )
        with self._lock:
            if not self._conn.in_transaction:
                raise RuntimeError("task writer requires a caller-owned transaction")
            self._live_person(person_id)
            refs = source_ids(self._people, person_id, source_refs, allowed_source_ids)
            fingerprint = _fingerprint(
                {"op": "task.create", "person_id": person_id, "actor": actor,
                 "human_requested": human_requested, "source_refs": refs,
                 "timezone": timezone, "reason": reason, **clean}
            )
            legacy = _fingerprint({"op": "task.create", "person_id": person_id, "actor": actor, **clean}) if not human_requested and not refs else None
            replay = self._replay(operation, fingerprint, legacy_fingerprint=legacy)
            if replay is not None:
                source_ids(self._people, person_id, replay["task"]["source_refs"], allowed_source_ids)
                return replay
            self._live_person(person_id)
            now = datetime.now(UTC).isoformat()
            task_id = "task_" + uuid.uuid4().hex
            self._conn.execute(
                """
                INSERT INTO person_tasks (
                    task_id, person_id, title, details, due_date,
                    due_timezone, state, origin, protected_fields,
                    source_refs, version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, 1, ?, ?)
                """,
                (
                    task_id,
                    person_id,
                    clean["title"],
                    clean["details"],
                    clean["due_date"],
                    timezone if clean["due_date"] else None,
                    origin,
                    json.dumps(protected),
                    json.dumps(refs),
                    now,
                    now,
                ),
            )
            task = self._task_dict(self._load_task(person_id, task_id))
            response = self._record(
                operation_id=operation,
                fingerprint=fingerprint,
                person_id=person_id,
                task=task,
                action="create",
                before=None,
                actor=actor,
                reason=_clean_reason(reason, "Added a task"),
                session_id=session_id,
                run_id=run_id,
                now=now,
            )
        return response

    def _update_uncommitted(
        self,
        person_id: str,
        task_id: str,
        *,
        changes: dict[str, Any],
        expected_version: object,
        timezone: str,
        operation_id: object,
        actor: str,
        reason: object = None,
        session_id: str | None = None,
        run_id: str | None = None,
        human_requested: bool = False,
        allowed_source_ids: set[str] | None = None,
        source_refs: object = None,
    ) -> dict[str, Any]:
        if actor not in ("director", "assistant"):
            raise TaskError(f"invalid actor {actor}")
        if not _TASK_ID_RE.match(task_id):
            raise TaskNotFound("unknown task")
        operation = _clean_operation_id(operation_id)
        if not isinstance(changes, dict) or not changes:
            raise TaskError("nothing to change: send title, details, due_date or state")
        unknown = set(changes) - set(TASK_CHANGE_FIELDS)
        if unknown:
            raise TaskError(f"unsupported task fields {sorted(unknown)}")
        if (
            isinstance(expected_version, bool)
            or not isinstance(expected_version, int)
            or expected_version < 1
        ):
            raise TaskError("expected_version must be a positive integer")
        clean: dict[str, Any] = {}
        if "title" in changes:
            clean["title"] = _clean_title(changes["title"])
        if "details" in changes:
            clean["details"] = _clean_details(changes["details"])
        if "due_date" in changes:
            clean["due_date"] = _clean_due_date(changes["due_date"])
        if "state" in changes:
            if changes["state"] not in TASK_STATES:
                raise TaskError("state must be open, completed or cancelled")
            clean["state"] = changes["state"]
        timezone = validate_timezone(timezone)
        refs = source_ids(self._people, person_id, source_refs, allowed_source_ids) if source_refs is not None else None
        fingerprint = _fingerprint(
            {
                "op": "task.update",
                "person_id": person_id,
                "task_id": task_id,
                "actor": actor,
                "human_requested": human_requested,
                "timezone": timezone,
                "reason": reason,
                "expected_version": expected_version,
                "changes": clean,
                **({"source_refs": refs} if refs is not None else {}),
            }
        )
        legacy = _fingerprint({"op": "task.update", "person_id": person_id, "task_id": task_id,
                               "actor": actor, "expected_version": expected_version, "changes": clean}) if not human_requested else None
        with self._lock:
            if not self._conn.in_transaction:
                raise RuntimeError("task writer requires a caller-owned transaction")
            self._live_person(person_id)
            replay = self._replay(operation, fingerprint, legacy_fingerprint=legacy)
            if replay is not None:
                source_ids(self._people, person_id, replay["task"]["source_refs"], allowed_source_ids)
                return replay
            self._live_person(person_id)
            before = self._task_dict(self._load_task(person_id, task_id))
            source_ids(self._people, person_id, before["source_refs"], allowed_source_ids)
            if before["version"] != expected_version:
                raise TaskConflict(
                    "this task changed since you opened it",
                    code="task_conflict",
                    current=self._project_task(before, allowed_source_ids),
                )
            changed = {
                field: value
                for field, value in clean.items()
                if value != before[field]
            }
            if not changed:
                # Nothing differs: no new version, no history row.
                return {"task": before, "change": None, "replayed": False}
            if actor == "assistant" and not human_requested and "state" in changed:
                raise TaskConflict("assistant lifecycle changes require verified task fulfillment or a director request",
                                   code="fulfillment_required", current=before)
            if actor == "assistant" and not human_requested and set(changed) & set(before["protected_fields"]):
                raise TaskConflict("human-edited task fields require a director request",
                                   code="human_protected", current=before)
            action = "update"
            if "state" in changed:
                action = TASK_TRANSITIONS.get((before["state"], changed["state"]))
                if action is None:
                    raise TaskError(
                        f"a {before['state']} task cannot become {changed['state']}"
                    )
            # The director's own words and completion decisions are
            # protected from later automatic rewrites.
            protected = set(before["protected_fields"])
            if actor == "director" or human_requested:
                protected |= set(changed)
            now = datetime.now(UTC).isoformat()
            if "due_date" in changed:
                changed["due_timezone"] = timezone if changed["due_date"] else None
            if refs is not None:
                changed["source_refs"] = json.dumps(sorted(set(before["source_refs"]) | set(refs)))
            if action in ("complete", "cancel"):
                changed["completed_at"] = now
            elif action == "reopen":
                changed["completed_at"] = None
            task = self._write(
                person_id,
                task_id,
                changed,
                protected=protected,
                expected_version=expected_version,
                now=now,
            )
            response = self._record(
                operation_id=operation,
                fingerprint=fingerprint,
                person_id=person_id,
                task=task,
                action=action,
                before=before,
                actor=actor,
                reason=_clean_reason(reason, _DEFAULT_REASON[action]),
                session_id=session_id,
                run_id=run_id,
                now=now,
            )
        return response

    def _write(
        self,
        person_id: str,
        task_id: str,
        changed: dict[str, Any],
        *,
        protected: set[str],
        expected_version: int,
        now: str,
    ) -> dict[str, Any]:
        """Apply column changes as one new version; conflict if the row moved."""
        assignments = [f"{field} = ?" for field in changed]
        cursor = self._conn.execute(
            f"""
            UPDATE person_tasks SET
                {", ".join(assignments)},
                protected_fields = ?, version = version + 1, updated_at = ?
            WHERE task_id = ? AND person_id = ? AND version = ?
            """,
            (
                *changed.values(),
                json.dumps(sorted(protected)),
                now,
                task_id,
                person_id,
                expected_version,
            ),
        )
        if cursor.rowcount != 1:
            raise TaskConflict(
                "this task changed since you opened it",
                code="task_conflict",
                current=self._task_dict(self._load_task(person_id, task_id)),
            )
        return self._task_dict(self._load_task(person_id, task_id))

    def undo(
        self,
        person_id: str,
        change_id: str,
        *,
        operation_id: object,
        actor: str,
        reason: object = None,
        session_id: str | None = None,
        run_id: str | None = None,
        human_requested: bool = False,
        expected_version: int | None = None,
        allowed_source_ids: set[str] | None = None,
    ) -> dict[str, Any]:
        """Restore what one recorded change replaced, as a new change.

        Only the task that change touched moves; nothing else on the person
        is read or written. If the task's version is newer than the one the
        change produced, a later edit exists and the undo is refused with
        the current task, so that edit is never erased.
        """
        if actor not in ("director", "assistant"):
            raise TaskError(f"invalid actor {actor}")
        if not _CHANGE_ID_RE.match(change_id):
            raise TaskNotFound("unknown change")
        operation = _clean_operation_id(operation_id)
        fingerprint = _fingerprint(
            {
                "op": "task.undo",
                "person_id": person_id,
                "change_id": change_id,
                "actor": actor,
                "human_requested": human_requested,
                "expected_version": expected_version,
                "reason": reason,
            }
        )
        legacy = _fingerprint({"op": "task.undo", "person_id": person_id, "change_id": change_id,
                               "actor": actor}) if not human_requested and expected_version is None else None
        with self._lock, self._conn:
            try:
                if not self._conn.in_transaction:
                    self._conn.execute("BEGIN IMMEDIATE")
                self._live_person(person_id)
                replay = self._replay(operation, fingerprint, legacy_fingerprint=legacy)
                if replay is not None:
                    source_ids(self._people, person_id, replay["task"]["source_refs"], allowed_source_ids)
                    return self._project_response(replay, allowed_source_ids)
                self._live_person(person_id)
                row = self._conn.execute(
                    """
                    SELECT entity_id, entity_version, action, before_json
                    FROM crm_changes
                    WHERE change_id = ? AND person_id = ? AND entity_type = 'task'
                    """,
                    (change_id, person_id),
                ).fetchone()
                if row is None:
                    raise TaskNotFound("unknown change")
                automatic = row["before_json"] is None and self._automatic_creation(change_id)
                if row["before_json"] is None and not automatic:
                    raise TaskError("creating a task cannot be undone; cancel it instead")
                task_id = str(row["entity_id"])
                current = self._task_dict(self._load_task(person_id, task_id))
                source_ids(self._people, person_id, current["source_refs"], allowed_source_ids)
                if expected_version is not None and current["version"] != expected_version:
                    raise TaskConflict("this task changed since you opened it", code="task_conflict", current=self._project_task(current, allowed_source_ids))
                if current["version"] != int(row["entity_version"]):
                    raise TaskConflict(
                        "this task changed again after that change",
                        code="task_conflict",
                        current=self._project_task(current, allowed_source_ids),
                    )
                restored = json.loads(str(row["before_json"])) if not automatic else None
                changed = {"state": "cancelled", "completed_at": datetime.now(UTC).isoformat()} if automatic else {
                    field: restored[field]
                    for field in UNDO_FIELDS
                    if restored[field] != current[field]
                }
                if not changed:
                    return {"task": current, "change": None, "replayed": False}
                if actor == "assistant" and not human_requested and "state" in changed:
                    raise TaskConflict("assistant lifecycle changes require verified task fulfillment or a director request",
                                       code="fulfillment_required", current=current)
                if actor == "assistant" and not human_requested and set(changed) & set(current["protected_fields"]):
                    raise TaskConflict("human-edited task fields require a director request",
                                       code="human_protected", current=current)
                protected = set(current["protected_fields"])
                if actor == "director" or human_requested:
                    protected |= set(changed) & set(TASK_CHANGE_FIELDS)
                now = datetime.now(UTC).isoformat()
                task = self._write(
                    person_id,
                    task_id,
                    changed,
                    protected=protected,
                    expected_version=current["version"],
                    now=now,
                )
                response = self._record(
                    operation_id=operation,
                    fingerprint=fingerprint,
                    person_id=person_id,
                    task=task,
                    action="undo",
                    before=current,
                    actor=actor,
                    reason=_clean_reason(reason, f"Undid: {row['action']}"),
                    session_id=session_id,
                    run_id=run_id,
                    now=now,
                    compensates=change_id,
                )
                self._conn.execute("""UPDATE crm_proposals SET state='dismissed', version=version+1,
                    updated_at=? WHERE change_id=? AND state='accepted'""", (now, change_id))
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return self._project_response(response, allowed_source_ids)
