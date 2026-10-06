"""Tasks share PersonStore's transaction authority; no second CRM database."""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from coworker.people import PersonStore

# The registry executes these individually inside its migration transaction.
TASK_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS person_tasks (
        task_id TEXT PRIMARY KEY,
        person_id TEXT NOT NULL REFERENCES people(person_id),
        title TEXT NOT NULL CHECK(length(trim(title)) BETWEEN 1 AND 200),
        details TEXT NOT NULL DEFAULT '' CHECK(length(details) <= 8000),
        due_date TEXT CHECK(due_date IS NULL OR
            (length(due_date) = 10 AND due_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
             AND date(due_date, '+0 days') IS due_date)),
        due_timezone TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'open' CHECK(state IN ('open','completed','cancelled')),
        origin TEXT NOT NULL CHECK(origin IN ('human','assistant')),
        protected_fields TEXT NOT NULL DEFAULT '[]',
        source_refs TEXT NOT NULL DEFAULT '[]',
        version INTEGER NOT NULL CHECK(version > 0),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        completed_at TEXT,
        UNIQUE(task_id, person_id)
    )""",
    "CREATE INDEX IF NOT EXISTS tasks_person_due ON person_tasks(person_id, state, due_date)",
    "CREATE INDEX IF NOT EXISTS tasks_state_due ON person_tasks(state, due_date, task_id)",
    """CREATE TABLE IF NOT EXISTS crm_changes (
        change_id TEXT PRIMARY KEY,
        person_id TEXT NOT NULL REFERENCES people(person_id),
        task_id TEXT NOT NULL,
        version INTEGER NOT NULL CHECK(version > 0),
        actor TEXT NOT NULL CHECK(actor IN ('director','assistant')),
        reason TEXT NOT NULL CHECK(length(reason) BETWEEN 1 AND 500),
        before_json TEXT,
        after_json TEXT NOT NULL,
        source_refs TEXT NOT NULL DEFAULT '[]',
        operation_id TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL,
        FOREIGN KEY(task_id, person_id) REFERENCES person_tasks(task_id, person_id)
    )""",
    """CREATE TABLE IF NOT EXISTS crm_operations (
        operation_id TEXT PRIMARY KEY,
        fingerprint TEXT NOT NULL,
        change_id TEXT NOT NULL REFERENCES crm_changes(change_id),
        response_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    """CREATE TRIGGER IF NOT EXISTS tasks_live_person_insert BEFORE INSERT ON person_tasks
        WHEN NOT EXISTS (SELECT 1 FROM people WHERE person_id = NEW.person_id AND deleted_at IS NULL)
        BEGIN SELECT RAISE(ABORT, 'task requires a live person'); END""",
    """CREATE TRIGGER IF NOT EXISTS tasks_live_person_update BEFORE UPDATE ON person_tasks
        WHEN NOT EXISTS (SELECT 1 FROM people WHERE person_id = NEW.person_id AND deleted_at IS NULL)
        BEGIN SELECT RAISE(ABORT, 'task requires a live person'); END""",
    """CREATE TRIGGER IF NOT EXISTS tasks_person_immutable BEFORE UPDATE OF person_id ON person_tasks
        WHEN NEW.person_id != OLD.person_id
        BEGIN SELECT RAISE(ABORT, 'task person cannot change'); END""",
    """CREATE TRIGGER IF NOT EXISTS crm_changes_no_update BEFORE UPDATE ON crm_changes
        BEGIN SELECT RAISE(ABORT, 'CRM history is append only'); END""",
    """CREATE TRIGGER IF NOT EXISTS crm_changes_no_delete BEFORE DELETE ON crm_changes
        BEGIN SELECT RAISE(ABORT, 'CRM history is append only'); END""",
)


class CrmNotFound(Exception):
    pass


class CrmConflict(Exception):
    def __init__(self, message: str, *, current: dict | None = None):
        super().__init__(message)
        self.current = current


def _task(row) -> dict[str, Any]:
    result = dict(row)
    for field in ("protected_fields", "source_refs"):
        result[field] = json.loads(result[field])
    return result


_TASK_READ = """SELECT t.*, trim(coalesce(p.first_name,'') || ' ' || coalesce(p.last_name,'')) AS person_name,
    p.company AS person_company FROM person_tasks t JOIN people p ON p.person_id = t.person_id
    WHERE p.deleted_at IS NULL"""


class CrmRepository:
    def __init__(self, people: PersonStore):
        self.people = people

    @staticmethod
    def _live_person(conn, person_id):
        if conn.execute("SELECT 1 FROM people WHERE person_id=? AND deleted_at IS NULL", (person_id,)).fetchone() is None:
            raise CrmNotFound("Person not found")

    def list_tasks(self, *, person_id=None, limit=50, offset=0):
        with self.people.crm_transaction() as conn:
            where, args = "", []
            if person_id is not None:
                self._live_person(conn, person_id)
                where, args = " AND t.person_id=?", [person_id]
            rows = conn.execute(
                _TASK_READ + where + " ORDER BY t.due_date IS NULL, t.due_date, t.created_at, t.task_id LIMIT ? OFFSET ?",
                (*args, limit + 1, offset),
            ).fetchall()
            return {"tasks": [_task(row) for row in rows[:limit]],
                    "next_offset": offset + limit if len(rows) > limit else None}

    def list_people(self, *, query="", limit=50, offset=0):
        # Minimal identity projection. No attachments, emails or private evidence.
        with self.people.crm_transaction() as conn:
            rows = conn.execute(
                """SELECT person_id, first_name, last_name, company FROM people
                WHERE deleted_at IS NULL AND instr(lower(coalesce(first_name,'') || ' ' ||
                    coalesce(last_name,'') || ' ' || coalesce(company,'')), lower(?)) > 0
                ORDER BY first_name, last_name, person_id LIMIT ? OFFSET ?""",
                (query, limit + 1, offset),
            ).fetchall()
            return {"people": [dict(row) for row in rows[:limit]],
                    "next_offset": offset + limit if len(rows) > limit else None}

    def save(self, *, person_id, task_id, operation_id, fingerprint, changes, expected_version, timezone):
        with self.people.crm_transaction() as conn:
            self._live_person(conn, person_id)
            operation = conn.execute("SELECT * FROM crm_operations WHERE operation_id=?", (operation_id,)).fetchone()
            if operation is not None:
                if operation["fingerprint"] != fingerprint:
                    raise CrmConflict("This save ID was already used for different changes.")
                return json.loads(operation["response_json"])

            before = None
            now = self.people._now()
            if task_id is not None:
                row = conn.execute(_TASK_READ + " AND t.person_id=? AND t.task_id=?", (person_id, task_id)).fetchone()
                if row is None:
                    raise CrmNotFound("Task not found")
                before = _task(row)
                if before["version"] != expected_version:
                    raise CrmConflict("This task changed while you were editing.", current=before)
                values = {**before, **changes, "version": expected_version + 1, "updated_at": now}
                if "due_date" in changes:
                    values["due_timezone"] = timezone
                values["protected_fields"] = sorted(set(before["protected_fields"]) | set(changes))
                conn.execute("""UPDATE person_tasks SET title=?, details=?, due_date=?, due_timezone=?,
                    protected_fields=?, version=?, updated_at=? WHERE task_id=? AND version=?""",
                    (values["title"], values["details"], values["due_date"], values["due_timezone"],
                     json.dumps(values["protected_fields"]), values["version"], now, task_id, expected_version))
            else:
                task_id = "tsk_" + uuid.uuid4().hex
                conn.execute("""INSERT INTO person_tasks (task_id, person_id, title, details, due_date,
                    due_timezone, origin, protected_fields, version, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, 'human', ?, 1, ?, ?)""",
                    (task_id, person_id, changes["title"], changes.get("details", ""), changes.get("due_date"),
                     timezone, json.dumps(["title", "details", "due_date"]), now, now))
            task = _task(conn.execute(_TASK_READ + " AND t.task_id=?", (task_id,)).fetchone())
            change_id = "chg_" + uuid.uuid4().hex
            reason = "Edited task" if before else "Added task"
            conn.execute("""INSERT INTO crm_changes (change_id, person_id, task_id, version, actor,
                reason, before_json, after_json, operation_id, created_at)
                VALUES (?, ?, ?, ?, 'director', ?, ?, ?, ?, ?)""",
                (change_id, person_id, task_id, task["version"], reason,
                 json.dumps(before) if before else None, json.dumps(task), operation_id, now))
            receipt = {"change_id": change_id, "operation_id": operation_id, "task_id": task_id,
                       "version": task["version"], "actor": "director", "reason": reason, "created_at": now}
            response = {"task": task, "receipt": receipt}
            self.people._receipt(person_id, kind="crm_task", summary=reason,
                payload={"change_id": change_id, "task_id": task_id, "version": task["version"]},
                actor="director", session_id=None, run_id=None)
            conn.execute("INSERT INTO crm_operations VALUES (?, ?, ?, ?, ?)",
                (operation_id, fingerprint, change_id, json.dumps(response), now))
            return response
