"""Field-scoped history/undo for descriptions using PersonStore's common writer."""
from __future__ import annotations

import json

from coworker.crm_sources import source_ids, sources_visible
from coworker.crm_tasks import TaskConflict, _clean_operation_id
from coworker.people import PersonStore


class DescriptionService:
    def __init__(self, people: PersonStore, *, allowed_source_ids: set[str] | None = None):
        self.people = people
        self.allowed = set(allowed_source_ids or ())

    def changes(self, pid: str, slot: str) -> list[dict]:
        with self.people._lock:
            self.people._load_person_row(pid)
            result = []
            for row in self.people._conn.execute("""SELECT * FROM crm_changes
                WHERE person_id=? AND entity_type='description' AND entity_id=? ORDER BY rowid""", (pid, f"{pid}:{slot}")):
                item = dict(row)
                item["source_refs"] = json.loads(item["source_refs"])
                if not sources_visible(self.people, pid, item["source_refs"], self.allowed):
                    continue
                item["before"] = json.loads(item.pop("before_json"))
                item["after"] = json.loads(item.pop("after_json"))
                # A change restoring private text requires both source sets.
                if sources_visible(self.people, pid, item["before"]["source_refs"], self.allowed):
                    result.append(item)
            return result

    def undo(self, pid: str, change_id: str, *, operation_id: str, actor: str = "director",
             reason: str = "Director undid a description change.") -> dict:
        if actor != "director":
            raise TaskConflict("description undo requires the director", code="human_protected")
        operation_id = _clean_operation_id(operation_id)
        with self.people._lock, self.people._conn:
            self.people._conn.execute("BEGIN IMMEDIATE")
            self.people._load_person_row(pid)
            change = self.people._conn.execute("""SELECT * FROM crm_changes WHERE person_id=?
                AND change_id=? AND entity_type='description'""", (pid, change_id)).fetchone()
            if change is None:
                raise ValueError("unknown description change")
            before = json.loads(change["before_json"])
            source_ids(self.people, pid, before["source_refs"], self.allowed)
            source_ids(self.people, pid, json.loads(change["source_refs"]), self.allowed)
            slot = str(change["entity_id"]).rsplit(":", 1)[-1]
            replay = self.people._conn.execute("SELECT 1 FROM events WHERE external_key=?",
                (f"crm-operation:{operation_id}",)).fetchone()
            if not replay and self.people._descriptions(pid)[slot] != json.loads(change["after_json"]):
                raise TaskConflict("description changed since this change", code="description_conflict")
            result = self.people._patch_description_uncommitted(pid, slot,
                expected_version=change["entity_version"], text=before["text"],
                actor=actor, rationale_summary=reason, source_refs=before["source_refs"],
                allowed_source_ids=self.allowed, operation_id=operation_id,
                restore=before, compensates_change_id=change_id)
            self.people._conn.execute("""UPDATE crm_proposals SET state='dismissed', resolution='undo',
                version=version+1, updated_at=? WHERE change_id=? AND state='accepted'""", (self.people._now(), change_id))
            return result
