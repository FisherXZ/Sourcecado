"""Shared local CRM authority for UI, chat and evidence analysis.

No connector is available here. External effects belong to reviewed outreach.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from coworker.crm_tasks import (
    TaskConflict, TaskError, TaskNotFound, TaskService, _clean_due_date,
    _clean_operation_id, _clean_reason, _fingerprint, due_status, today_in,
)
from coworker.people import DESCRIPTION_LIMITS, PersonStore


class CrmService:
    def __init__(self, people: PersonStore):
        self.people = people
        self.tasks = TaskService(people)
        self.conn = people._conn

    def _person(self, pid):
        return self.tasks._live_person(pid)

    def _version(self, current, expected):
        if isinstance(expected, bool) or not isinstance(expected, int) or expected < 0:
            raise TaskError("expected_version must be a non-negative integer")
        if current != expected:
            raise TaskConflict("This record changed. Review the current value before saving.", code="crm_conflict")

    def _record(self, pid, kind, entity, version, before, after, *, operation, fingerprint,
                actor="director", reason="Updated record", sources=(), action="update", compensates=None,
                session_id=None, run_id=None):
        if actor not in ("director", "assistant"):
            raise TaskError("invalid actor")
        change = {"change_id": "chg_" + uuid.uuid4().hex, "entity_type": kind,
            "entity_id": entity, "entity_version": version, "actor": actor,
            "reason": _clean_reason(reason, "Updated record"), "source_refs": list(sources),
            "action": action, "created_at": self.people._now()}
        self.conn.execute("""INSERT INTO crm_changes(change_id,person_id,entity_type,entity_id,
            entity_version,action,before_json,after_json,actor,reason,source_refs,operation_id,
            session_id,run_id,compensates_change_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (change["change_id"], pid, kind, entity, version, action, json.dumps(before), json.dumps(after),
             actor, change["reason"], json.dumps(list(sources)), operation, session_id, run_id,
             compensates, change["created_at"]))
        response = {kind: after, "change": change, "replayed": False}
        self.conn.execute("INSERT INTO crm_operations VALUES(?,?,?,?,?,?,?)",
            (operation, fingerprint, change["change_id"], kind, entity, json.dumps(response), change["created_at"]))
        self.people._receipt(pid, kind="crm_change", summary=change["reason"],
            payload={"change_id": change["change_id"], "entity_type": kind, "action": action},
            actor=actor, session_id=session_id, run_id=run_id)
        return response

    def _bump_person(self, pid):
        self.conn.execute("UPDATE people SET version=version+1, updated_at=? WHERE person_id=?",
            (self.people._now(), pid))
        version = self._person(pid)["version"]
        self.people._snapshot(pid, version, self.people._now())
        return version

    def set_attention(self, pid, *, reason, until, expected_version, operation_id, actor="director", **audit):
        if reason is not None and (not isinstance(reason, str) or len(reason) > 500):
            raise TaskError("attention reason must be text up to 500 characters")
        reason = reason.strip() if reason else None
        until = _clean_due_date(until) if reason else None
        op = _clean_operation_id(operation_id)
        fp = _fingerprint({"attention": pid, "reason": reason, "until": until, "version": expected_version, "actor": actor})
        with self.people.crm_transaction():
            replay = self.tasks._replay(op, fp)
            if replay:
                return replay
            person = self._person(pid)
            self._version(person["version"], expected_version)
            before = {"reason": person["attention_reason"], "until": person["attention_until"]}
            self.conn.execute("UPDATE people SET attention_reason=?,attention_until=? WHERE person_id=?", (reason, until, pid))
            version = self._bump_person(pid)
            return self._record(pid, "attention", pid, version, before, {"reason": reason, "until": until},
                operation=op, fingerprint=fp, actor=actor, reason="Updated manual attention", **audit)

    def save_description(self, pid, slot, *, text=None, release=False, expected_version,
                         operation_id, actor="director", sources=(), evidence_revision=None,
                         human_directed=True, **audit):
        if slot not in DESCRIPTION_LIMITS:
            raise TaskError("unknown description slot")
        if not release and (not isinstance(text, str) or len(text) > DESCRIPTION_LIMITS[slot]):
            raise TaskError(f"{slot} description must be text up to {DESCRIPTION_LIMITS[slot]} characters")
        op = _clean_operation_id(operation_id)
        fp = _fingerprint({"description": pid, "slot": slot, "text": text, "release": release,
            "version": expected_version, "actor": actor, "sources": list(sources), "human": human_directed})
        with self.people.crm_transaction():
            replay = self.tasks._replay(op, fp)
            if replay:
                return replay
            self._person(pid)
            before = self.people._descriptions(pid)[slot]
            self._version(before["version"], expected_version)
            if not human_directed:
                if before["authority"] == "human":
                    raise TaskConflict("Human description is protected", code="human_protected")
                self.validate_sources(pid, sources)
                revision = self.conn.execute("SELECT latest_evidence_revision FROM crm_analysis_state WHERE person_id=?", (pid,)).fetchone()
                self._version(revision[0] if revision else 0, evidence_revision)
            after = {"slot": slot, "text": before["text"] if release else text,
                "authority": "assistant" if release or not human_directed else "human",
                "source_refs": before["source_refs"] if release else list(sources), "version": expected_version + 1,
                "analyzed_input_revision": evidence_revision if evidence_revision is not None else before.get("analyzed_input_revision", 0)}
            self._write_description(pid, after)
            self._bump_person(pid)
            return self._record(pid, "description", f"{pid}:{slot}", after["version"], before, after,
                operation=op, fingerprint=fp, actor=actor, sources=sources, reason="Updated description", **audit)

    def _write_description(self, pid, value):
        now = self.people._now()
        self.conn.execute("""INSERT INTO person_descriptions(person_id,slot,text,authority,source_refs,version,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(person_id,slot) DO UPDATE SET text=excluded.text,
            authority=excluded.authority,source_refs=excluded.source_refs,version=excluded.version,updated_at=excluded.updated_at""",
            (pid, value["slot"], value["text"], value["authority"], json.dumps(value["source_refs"]), value["version"], now, now))
        self.conn.execute("UPDATE person_descriptions SET analyzed_revision=? WHERE person_id=? AND slot=?",
            (value.get("analyzed_input_revision", 0), pid, value["slot"]))

    def validate_sources(self, pid, sources):
        if not sources or len(sources) > 20:
            raise TaskError("one to twenty owned Gmail sources are required")
        for ref in sources:
            if not isinstance(ref, dict) or not self.conn.execute(
                "SELECT 1 FROM gmail_messages WHERE person_id=? AND message_id=? AND account=?",
                (pid, ref.get("message_id"), ref.get("account"))).fetchone():
                raise TaskError("source does not belong to this person")

    def _proposal(self, row):
        result = dict(row)
        result["value"] = json.loads(result.pop("value_json"))
        result["source_refs"] = json.loads(result["source_refs"])
        return result

    def proposals(self, pid, *, limit=100):
        with self.people._lock:
            self._person(pid)
            rows = self.conn.execute("SELECT * FROM crm_proposals WHERE person_id=? AND state='pending' ORDER BY created_at,proposal_id LIMIT ?", (pid, limit)).fetchall()
            return [self._proposal(row) for row in rows]

    def all_proposals(self, *, limit=100, offset=0):
        with self.people._lock:
            rows = self.conn.execute("""SELECT c.* FROM crm_proposals c JOIN people p ON p.person_id=c.person_id
                WHERE c.state='pending' AND p.deleted_at IS NULL ORDER BY c.created_at,c.proposal_id LIMIT ? OFFSET ?""", (limit + 1, offset)).fetchall()
            result = []
            for row in rows[:limit]:
                proposal = self._proposal(row)
                person = self._person(proposal["person_id"])
                proposal["person"] = {key: person[key] for key in ("first_name", "last_name", "company", "sequence_state")}
                result.append(proposal)
            return {"proposals": result, "truncated": len(rows) > limit}

    def recent_changes(self, pid, *, limit=50):
        with self.people._lock:
            self._person(pid)
            return [dict(row) for row in self.conn.execute("""SELECT change_id,entity_type,entity_id,action,reason,source_refs,actor,created_at
                FROM crm_changes WHERE person_id=? ORDER BY created_at DESC LIMIT ?""", (pid, limit))]

    def propose(self, pid, *, kind, value, expected_version, sources, fingerprint, reason):
        if kind not in ("task_create", "task_update", "description", "status") or not isinstance(value, dict):
            raise TaskError("invalid proposal")
        if len(json.dumps(value)) > 16000:
            raise TaskError("proposal is too large")
        with self.people.crm_transaction():
            self._person(pid)
            if sources:
                self.validate_sources(pid, sources)
            existing = self.conn.execute("SELECT * FROM crm_proposals WHERE person_id=? AND fingerprint=?", (pid, fingerprint)).fetchone()
            if existing:
                return self._proposal(existing)
            proposal_id, now = "prop_" + uuid.uuid4().hex, self.people._now()
            self.conn.execute("INSERT INTO crm_proposals VALUES(?,?,?,?,?,?,?,?,'pending',?,?)",
                (proposal_id, pid, kind, json.dumps(value), expected_version, _clean_reason(reason, "Review suggested change"), json.dumps(sources), fingerprint, now, now))
            return self._proposal(self.conn.execute("SELECT * FROM crm_proposals WHERE proposal_id=?", (proposal_id,)).fetchone())

    def resolve(self, pid, proposal_id, *, decision, operation_id, timezone, value=None, actor="director", **audit):
        if decision not in ("accepted", "dismissed"):
            raise TaskError("decision must be accepted or dismissed")
        op = _clean_operation_id(operation_id)
        fp = _fingerprint({"resolve": proposal_id, "person": pid, "decision": decision, "value": value, "actor": actor})
        with self.people.crm_transaction():
            replay = self.tasks._replay(op, fp)
            if replay:
                return replay
            self._person(pid)
            row = self.conn.execute("SELECT * FROM crm_proposals WHERE person_id=? AND proposal_id=?", (pid, proposal_id)).fetchone()
            if not row:
                raise TaskNotFound("unknown proposal")
            proposal = self._proposal(row)
            if proposal["state"] != "pending":
                raise TaskConflict("Proposal was already resolved", code="crm_conflict")
            committed = {}
            if decision == "accepted":
                fields = value if value is not None else proposal["value"]
                if not isinstance(fields, dict):
                    raise TaskError("edited proposal must be an object")
                target_field = "slot" if proposal["kind"] == "description" else "task_id" if proposal["kind"] == "task_update" else None
                if target_field and fields.get(target_field) != proposal["value"].get(target_field):
                    raise TaskError("An edited suggestion cannot change its target")
                write = dict(operation_id="target_" + _fingerprint({"proposal_operation": op}), actor=actor, **audit)
                if proposal["source_refs"]:
                    self.validate_sources(pid, proposal["source_refs"])
                if proposal["kind"] == "task_create":
                    committed = self.tasks.create(pid, title=fields.get("title"), details=fields.get("details"),
                        due_date=fields.get("due_date"), timezone=timezone, human_directed=True, source_refs=tuple(proposal["source_refs"]), **write)
                elif proposal["kind"] == "task_update":
                    committed = self.tasks.update(pid, fields.get("task_id", ""), changes=fields.get("changes", {}),
                        expected_version=proposal["expected_version"], timezone=timezone, human_directed=True, source_refs=tuple(proposal["source_refs"]), **write)
                elif proposal["kind"] == "description":
                    committed = self.save_description(pid, fields.get("slot"), text=fields.get("text"),
                        expected_version=proposal["expected_version"], sources=proposal["source_refs"], **write)
                else:
                    committed = self.set_status(pid, state=fields.get("state"),
                        expected_version=proposal["expected_version"], **write)
            after = {**proposal, "state": decision}
            self.conn.execute("UPDATE crm_proposals SET state=?,updated_at=? WHERE proposal_id=?", (decision, self.people._now(), proposal_id))
            response = self._record(pid, "proposal", proposal_id, 1, proposal, after,
                operation=op, fingerprint=fp, actor=actor, reason="Reviewed suggestion", action="accept" if decision == "accepted" else "dismiss", **audit)
            response.update({key: val for key, val in committed.items() if key not in ("change", "replayed")})
            self.conn.execute("UPDATE crm_operations SET response_json=? WHERE operation_id=?", (json.dumps(response), op))
            return response

    def set_status(self, pid, *, state, expected_version, operation_id, actor="director", **audit):
        if state not in ("open", "in_conversation", "done"):
            raise TaskError("unknown sequence state")
        op = _clean_operation_id(operation_id)
        fp = _fingerprint({"status": pid, "state": state, "version": expected_version, "actor": actor})
        with self.people.crm_transaction():
            replay = self.tasks._replay(op, fp)
            if replay:
                return replay
            person = self._person(pid)
            self._version(person["version"], expected_version)
            before = {"state": person["sequence_state"]}
            self.conn.execute("UPDATE people SET sequence_state=? WHERE person_id=?", (state, pid))
            version = self._bump_person(pid)
            return self._record(pid, "status", pid, version, before, {"state": state},
                operation=op, fingerprint=fp, actor=actor, reason="Director changed relationship status", **audit)

    def undo(self, pid, change_id, *, operation_id, actor="director", reason=None, **audit):
        op = _clean_operation_id(operation_id)
        fp = _fingerprint({"undo": change_id, "person": pid, "actor": actor})
        with self.people.crm_transaction():
            self._person(pid)
            row = self.conn.execute("SELECT * FROM crm_changes WHERE change_id=? AND person_id=?", (change_id, pid)).fetchone()
            if not row:
                raise TaskNotFound("unknown change")
            if row["entity_type"] == "task":
                fp = _fingerprint({"op": "task.undo", "person_id": pid, "change_id": change_id, "actor": actor})
            replay = self.tasks._replay(op, fp)
            if replay:
                return replay
            if row["entity_type"] == "task":
                current = self.tasks.get(pid, row["entity_id"])
                if row["action"] == "create" and current["origin"] == "assistant":
                    self._version(current["version"], row["entity_version"])
                    now = self.people._now()
                    task = self.tasks._write(pid, current["task_id"], {"state": "cancelled", "completed_at": now},
                        protected=set(current["protected_fields"]) | {"state"}, expected_version=current["version"], now=now)
                    result = self.tasks._record(person_id=pid, task=task, before=current, operation_id=op,
                        fingerprint=fp, action="undo", actor=actor, reason="Undid automatic task creation", now=now, compensates=change_id,
                        session_id=audit.get("session_id"), run_id=audit.get("run_id"))
                else:
                    result = self.tasks.undo(pid, change_id, operation_id=op, actor=actor, reason=reason, **audit)
            else:
                kind, before = row["entity_type"], json.loads(row["before_json"])
                if kind == "description":
                    current = self.people._descriptions(pid)[before["slot"]]
                    self._version(current["version"], row["entity_version"])
                    restored = {**before, "version": current["version"] + 1, "authority": "human"}
                    self._write_description(pid, restored)
                    self._bump_person(pid)
                    version = restored["version"]
                elif kind == "attention":
                    person = self._person(pid)
                    self._version(person["version"], row["entity_version"])
                    current = {"reason": person["attention_reason"], "until": person["attention_until"]}
                    restored = before
                    self.conn.execute("UPDATE people SET attention_reason=?,attention_until=? WHERE person_id=?", (before["reason"], before["until"], pid))
                    version = self._bump_person(pid)
                else:
                    raise TaskError("This receipt cannot be undone")
                result = self._record(pid, kind, row["entity_id"], version, current, restored,
                    operation=op, fingerprint=fp, actor=actor, reason=reason or "Undid CRM change", action="undo", compensates=change_id, **audit)
            self.conn.execute("UPDATE crm_interpretations SET suppressed=1 WHERE person_id=? AND change_id=?", (pid, change_id))
            return result

    def dismiss_review(self, pid, message_id, *, operation_id, actor="director", **audit):
        op = _clean_operation_id(operation_id)
        fp = _fingerprint({"review": pid, "message": message_id, "actor": actor})
        with self.people.crm_transaction():
            replay = self.tasks._replay(op, fp)
            if replay:
                return replay
            self._person(pid)
            message = self.conn.execute("SELECT 1 FROM gmail_messages WHERE person_id=? AND message_id=? AND direction='inbound'", (pid, message_id)).fetchone()
            if not message:
                raise TaskNotFound("unknown inbound message")
            self.conn.execute("INSERT OR IGNORE INTO crm_review_dismissals VALUES(?,?)", (pid, message_id))
            return self._record(pid, "review", message_id, 1, None, {"message_id": message_id}, operation=op,
                fingerprint=fp, actor=actor, reason="Dismissed reply review", action="dismiss", **audit)

    def contacts(self, *, timezone, now: datetime | None = None, limit=200, offset=0, query="", state=None, needs_attention=False, include_saved=False):
        from coworker.crm_mail_repository import MailRepository

        today = today_in(timezone, now=now).isoformat()
        with self.people._lock:
            board = self.people.list_board()
            # Batch relationship-wide reads. Opening Contacts never runs a model
            # or performs a task/proposal query for every visible row.
            tasks_by_person: dict[str, list[dict[str, Any]]] = {}
            for task_row in self.conn.execute("""SELECT t.* FROM person_tasks t
                    JOIN people p ON p.person_id=t.person_id WHERE t.state='open' AND p.deleted_at IS NULL
                    ORDER BY t.due_date IS NULL,t.due_date,t.created_at,t.task_id"""):
                task = self.tasks._task_dict(task_row)
                task["due_status"] = due_status(task["due_date"], today_in(task["due_timezone"] or timezone, now=now))
                tasks_by_person.setdefault(task["person_id"], []).append(task)
            proposal_counts = {row[0]: row[1] for row in self.conn.execute("SELECT person_id,count(*) FROM crm_proposals WHERE state='pending' GROUP BY person_id")}
            ambiguous = set()
            for gap in self.conn.execute("SELECT person_id,fields_json FROM person_attachments WHERE record_type='knowledge_gap'"):
                fields = json.loads(gap[1])
                if fields.get("kind") == "unassigned_reply" and not fields.get("resolved"):
                    ambiguous.add(gap[0])
            dismissed = {(row[0],row[1]) for row in self.conn.execute("SELECT person_id,message_id FROM crm_review_dismissals")}
            analysis = {row["person_id"]: MailRepository.analysis_projection(dict(row)) for row in self.conn.execute("SELECT * FROM crm_analysis_state")}
            rows = []
            candidates = [*board["open"], *board["in_conversation"], *board["done"]]
            if include_saved:
                candidates.extend(board["backlog"])
            for person in candidates:
                if state and person["sequence_state"] != state:
                    continue
                if query.casefold() not in " ".join(str(person.get(k) or "") for k in ("first_name", "last_name", "title", "company")).casefold():
                    continue
                pid = person["person_id"]
                tasks = tasks_by_person.get(pid, [])
                proposal_count = proposal_counts.get(pid, 0)
                reasons = []
                follow = person.get("follow_up") or {}
                if pid in ambiguous:
                    reasons.append({"code": "attribution", "label": "Reply attribution needs review"})
                if person.get("attention_reason") and (not person.get("attention_until") or person["attention_until"] >= today):
                    reasons.append({"code": "manual", "label": person["attention_reason"]})
                for status, code, label in (("overdue", "overdue", "Overdue task"), ("today", "due_today", "Task due today")):
                    count = sum(task["due_status"] == status for task in tasks)
                    if count:
                        reasons.append({"code": code, "label": label, "count": count})
                if follow.get("needed") and follow.get("reason") == "reply_unanswered":
                    pending = [mid for mid in person.get("unanswered_message_ids", []) if (pid, mid) not in dismissed]
                    if pending:
                        reasons.append({"code": "reply", "label": "Reply to review", "message_ids": pending})
                if proposal_count:
                    reasons.append({"code": "suggestion", "label": "Suggestion to review", "count": proposal_count})
                if needs_attention and not reasons:
                    continue
                rows.append({**person, "attention_reasons": reasons, "next_action": tasks[0] if tasks else None,
                    "mail_analysis": analysis.get(pid, MailRepository.analysis_projection(None))})
            ranks = {code: i for i, code in enumerate(("attribution", "manual", "overdue", "due_today", "reply", "suggestion"))}
            rows.sort(key=lambda p: (ranks.get(p["attention_reasons"][0]["code"], 6) if p["attention_reasons"] else 6,
                (p["next_action"] or {}).get("due_date") or "9999", p["person_id"]))
            return {"contacts": rows[offset:offset+limit], "total": len(rows), "truncated": len(rows) > offset + limit,
                "timezone": timezone, "today": today, "mail_sync": MailRepository(self.people).public_sync_state()}
