"""Shared local CRM authority for UI, chat and evidence analysis.

No connector is available here. External effects belong to reviewed outreach.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from coworker.crm_tasks import (
    TaskConflict,
    TaskError,
    TaskNotFound,
    TaskService,
    _clean_due_date,
    _clean_operation_id,
    _clean_reason,
    _fingerprint,
    due_status,
    today_in,
)
from coworker.people import DESCRIPTION_LIMITS, PersonStore
from coworker.crm_sources import source_ids, sources_visible, source_links
from coworker.crm_descriptions import DescriptionService
from coworker.crm_email_tasks import EmailTaskWriter


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
            raise TaskConflict(
                "This record changed. Review the current value before saving.",
                code="crm_conflict",
            )

    def _permitted_receipt(self, pid, response, allowed_source_ids):
        """A saved response is history, never a saved permission grant."""

        def inspect(value):
            if isinstance(value, list):
                for child in value:
                    inspect(child)
            elif isinstance(value, dict):
                refs = value.get("source_refs")
                if isinstance(refs, list):
                    ids = [
                        ref.get("id") if isinstance(ref, dict) else ref for ref in refs
                    ]
                    source_ids(self.people, pid, ids, allowed_source_ids)
                for child in value.values():
                    inspect(child)

        seen = set()

        def inspect_change(change_id):
            if not change_id or change_id in seen:
                return
            seen.add(change_id)
            row = self.conn.execute(
                "SELECT * FROM crm_changes WHERE person_id=? AND change_id=?",
                (pid, change_id),
            ).fetchone()
            if not row:
                raise TaskNotFound("unknown receipt change")
            source_ids(
                self.people, pid, json.loads(row["source_refs"]), allowed_source_ids
            )
            for field in ("before_json", "after_json"):
                if row[field]:
                    inspect(json.loads(row[field]))
            if row["entity_type"] == "proposal":
                inspect_change(json.loads(row["after_json"]).get("change_id"))
            elif row["entity_type"] == "task":
                task = self.tasks._task_dict(
                    self.tasks._load_task(pid, row["entity_id"])
                )
                source_ids(self.people, pid, task["source_refs"], allowed_source_ids)
            elif row["entity_type"] == "description":
                desc = self.people.descriptions(pid)[
                    row["entity_id"].rsplit(":", 1)[-1]
                ]
                source_ids(self.people, pid, desc["source_refs"], allowed_source_ids)

        inspect(response)
        inspect_change(response.get("change", {}).get("change_id"))
        if isinstance(response.get("task"), dict):
            response = {
                **response,
                "task": self.tasks._project_task(response["task"], allowed_source_ids),
            }
        return response

    def _record(
        self,
        pid,
        kind,
        entity,
        version,
        before,
        after,
        *,
        operation,
        fingerprint,
        actor="director",
        reason="Updated record",
        sources=(),
        action="update",
        compensates=None,
        session_id=None,
        run_id=None,
    ):
        if actor not in ("director", "assistant"):
            raise TaskError("invalid actor")
        change = {
            "change_id": "chg_" + uuid.uuid4().hex,
            "entity_type": kind,
            "entity_id": entity,
            "entity_version": version,
            "actor": actor,
            "reason": _clean_reason(reason, "Updated record"),
            "source_refs": list(sources),
            "action": action,
            "created_at": self.people._now(),
        }
        self.conn.execute(
            """INSERT INTO crm_changes(change_id,person_id,entity_type,entity_id,
            entity_version,action,before_json,after_json,actor,reason,source_refs,operation_id,
            session_id,run_id,compensates_change_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                change["change_id"],
                pid,
                kind,
                entity,
                version,
                action,
                json.dumps(before),
                json.dumps(after),
                actor,
                change["reason"],
                json.dumps(list(sources)),
                operation,
                session_id,
                run_id,
                compensates,
                change["created_at"],
            ),
        )
        response = {kind: after, "change": change, "replayed": False}
        self.conn.execute(
            "INSERT INTO crm_operations VALUES(?,?,?,?,?,?,?)",
            (
                operation,
                fingerprint,
                change["change_id"],
                kind,
                entity,
                json.dumps(response),
                change["created_at"],
            ),
        )
        self.people._receipt(
            pid,
            kind="crm_change",
            summary=change["reason"],
            payload={
                "change_id": change["change_id"],
                "entity_type": kind,
                "action": action,
            },
            actor=actor,
            session_id=session_id,
            run_id=run_id,
        )
        return response

    def _bump_person(self, pid):
        self.conn.execute(
            "UPDATE people SET version=version+1, updated_at=? WHERE person_id=?",
            (self.people._now(), pid),
        )
        version = self._person(pid)["version"]
        self.people._snapshot(pid, version, self.people._now())
        return version

    def set_attention(
        self,
        pid,
        *,
        reason,
        until,
        expected_version,
        operation_id,
        actor="director",
        **audit,
    ):
        if actor != "director" and not audit.pop("human_requested", False):
            raise TaskConflict(
                "attention requires the director", code="human_protected"
            )
        audit.pop("allowed_source_ids", None)
        rationale = audit.pop("rationale_summary", "Updated manual attention")
        if reason is not None and (not isinstance(reason, str) or len(reason) > 500):
            raise TaskError("attention reason must be text up to 500 characters")
        reason = reason.strip() if reason else None
        until = _clean_due_date(until) if reason else None
        op = _clean_operation_id(operation_id)
        fp = _fingerprint(
            {
                "attention": pid,
                "reason": reason,
                "until": until,
                "version": expected_version,
                "actor": actor,
            }
        )
        with self.people.crm_transaction():
            replay = self.tasks._replay(op, fp)
            if replay:
                return replay
            person = self._person(pid)
            self._version(person["version"], expected_version)
            before = {
                "reason": person["attention_reason"],
                "until": person["attention_until"],
            }
            self.conn.execute(
                "UPDATE people SET attention_reason=?,attention_until=? WHERE person_id=?",
                (reason, until, pid),
            )
            version = self._bump_person(pid)
            return self._record(
                pid,
                "attention",
                pid,
                version,
                before,
                {"reason": reason, "until": until},
                operation=op,
                fingerprint=fp,
                actor=actor,
                reason=rationale,
                **audit,
            )

    def save_description(
        self,
        pid,
        slot,
        *,
        text=None,
        release=False,
        expected_version,
        operation_id,
        actor="director",
        sources=(),
        evidence_revision=None,
        human_directed=True,
        human_requested=False,
        allowed_source_ids=None,
        **audit,
    ):
        refs = (
            self.validate_sources(pid, sources, allowed_source_ids) if sources else []
        )
        with self.people.crm_transaction():
            # The v7 attention writer kept description receipts in crm_operations;
            # canonical description writes use person events. Honor only an exact
            # historical request, with current source checks, before the writer
            # treats a cross-store operation ID as a collision.
            legacy = self.conn.execute(
                "SELECT 1 FROM crm_operations WHERE operation_id=? AND entity_type='description'",
                (operation_id,),
            ).fetchone()
            if legacy:
                fingerprint = _fingerprint(
                    {
                        "description": pid,
                        "slot": slot,
                        "text": text,
                        "release": release,
                        "version": expected_version,
                        "actor": actor,
                        "sources": list(sources),
                        "human": human_directed,
                    }
                )
                replay = self.tasks._replay(
                    _clean_operation_id(operation_id), fingerprint
                )
                return self._permitted_receipt(pid, replay, allowed_source_ids)
            result = self.people._patch_description_uncommitted(
                pid,
                slot,
                text=text,
                release=release,
                expected_version=expected_version,
                operation_id=operation_id,
                actor=actor,
                human_requested=human_requested,
                rationale_summary=audit.get("reason", "Updated description"),
                source_refs=refs,
                allowed_source_ids=allowed_source_ids,
                session_id=audit.get("session_id"),
                run_id=audit.get("run_id"),
            )
            if evidence_revision is not None:
                self.conn.execute(
                    "UPDATE person_descriptions SET analyzed_revision=? WHERE person_id=? AND slot=?",
                    (evidence_revision, pid, slot),
                )
            return result

    def _write_description(self, pid, value):
        now = self.people._now()
        self.conn.execute(
            """INSERT INTO person_descriptions(person_id,slot,text,authority,source_refs,version,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(person_id,slot) DO UPDATE SET text=excluded.text,
            authority=excluded.authority,source_refs=excluded.source_refs,version=excluded.version,updated_at=excluded.updated_at""",
            (
                pid,
                value["slot"],
                value["text"],
                value["authority"],
                json.dumps(value["source_refs"]),
                value["version"],
                now,
                now,
            ),
        )
        self.conn.execute(
            "UPDATE person_descriptions SET analyzed_revision=? WHERE person_id=? AND slot=?",
            (value.get("analyzed_input_revision", 0), pid, value["slot"]),
        )

    def validate_sources(self, pid, sources, allowed_source_ids=None):
        values = list(sources)
        if any(not isinstance(ref, str) for ref in values):
            raise TaskError("source_refs must be owned source reference ids")
        return source_ids(self.people, pid, values, allowed_source_ids)

    def _proposal(self, row, allowed_source_ids=None):
        result = dict(row)
        kind = result["target_kind"]
        pid = result["person_id"]
        refs = json.loads(result["source_refs"])
        source_ids(self.people, pid, refs, allowed_source_ids)
        value = (
            {"slot": result["target_slot"], "text": result["proposed_value"]}
            if kind == "description"
            else json.loads(result["proposed_value"])
        )
        if kind == "task_update":
            value = {**value, "task_id": result["target_id"]}
        result.update(
            kind=kind,
            value=value,
            expected_version=result["expected_target_version"],
            source_refs=[
                {"id": link["id"], "url": link.get("url")}
                for link in source_links(self.people, pid, refs, allowed_source_ids)
            ],
        )
        result.pop("resolution_receipt_json", None)
        current = None
        if kind == "description":
            record = self.people.descriptions(pid)[result["target_slot"]]
            if sources_visible(
                self.people, pid, record["source_refs"], allowed_source_ids
            ):
                current = {"text": record["text"]}
        elif kind == "status":
            current = {"state": self.people.get(pid)["sequence_state"]}
        elif kind == "task_update" and result["target_id"]:
            record = self.tasks.get(
                pid, result["target_id"], allowed_source_ids=allowed_source_ids
            )
            if sources_visible(
                self.people, pid, record["source_refs"], allowed_source_ids
            ):
                current = record
        result["current_value"] = current
        return result

    def proposals(self, pid, *, limit=100, allowed_source_ids=None):
        with self.people._lock:
            self._person(pid)
            rows = self.conn.execute(
                "SELECT * FROM crm_proposals WHERE person_id=? AND state='pending' ORDER BY created_at,proposal_id LIMIT ?",
                (pid, limit),
            ).fetchall()
            return [
                self._proposal(row, allowed_source_ids)
                for row in rows
                if sources_visible(
                    self.people, pid, json.loads(row["source_refs"]), allowed_source_ids
                )
            ]

    def all_proposals(self, *, limit=100, offset=0, allowed_source_ids=None):
        with self.people._lock:
            rows = self.conn.execute(
                "SELECT c.* FROM crm_proposals c JOIN people p ON p.person_id=c.person_id WHERE c.state='pending' AND p.deleted_at IS NULL ORDER BY c.created_at,c.proposal_id"
            ).fetchall()
            visible = [
                r
                for r in rows
                if sources_visible(
                    self.people,
                    r["person_id"],
                    json.loads(r["source_refs"]),
                    allowed_source_ids,
                )
            ]
            result = []
            for row in visible[offset : offset + limit]:
                proposal = self._proposal(row, allowed_source_ids)
                person = self._person(row["person_id"])
                proposal["person"] = {
                    k: person[k]
                    for k in ("first_name", "last_name", "company", "sequence_state")
                }
                result.append(proposal)
            return {"proposals": result, "truncated": len(visible) > offset + limit}

    def recent_changes(self, pid, *, limit=50, allowed_source_ids=None):
        with self.people._lock:
            self._person(pid)
            result = []
            for row in self.conn.execute(
                "SELECT * FROM crm_changes WHERE person_id=? ORDER BY rowid DESC LIMIT ?",
                (pid, limit),
            ):
                if not sources_visible(
                    self.people, pid, json.loads(row["source_refs"]), allowed_source_ids
                ):
                    continue
                item = {
                    k: row[k]
                    for k in (
                        "change_id",
                        "entity_type",
                        "entity_id",
                        "action",
                        "reason",
                        "source_refs",
                        "actor",
                        "created_at",
                    )
                }
                item["can_undo"] = self._can_undo(row, allowed_source_ids)
                result.append(item)
            return result

    def _can_undo(self, row, allowed_source_ids=None):
        try:
            kind = row["entity_type"]
            before = json.loads(row["before_json"]) if row["before_json"] else None
            after = json.loads(row["after_json"])
            if kind == "task":
                return self.tasks._can_undo(row)
            if kind == "proposal" and row["action"] == "accept":
                target = after.get("change_id")
                target_row = self.conn.execute(
                    "SELECT * FROM crm_changes WHERE person_id=? AND change_id=?",
                    (row["person_id"], target),
                ).fetchone()
                if not target_row:
                    return False
                if (
                    target_row["entity_type"] == "task"
                    and target_row["action"] == "create"
                ):
                    return (
                        self.tasks.get(row["person_id"], target_row["entity_id"])[
                            "version"
                        ]
                        == target_row["entity_version"]
                    )
                return self._can_undo(target_row, allowed_source_ids)
            if before is None:
                return False
            if kind == "description":
                slot = row["entity_id"].rsplit(":", 1)[-1]
                return self.people.descriptions(row["person_id"])[slot] == after
            if kind == "status":
                return (
                    self._person(row["person_id"])["sequence_state"] == after["state"]
                    and not self.conn.execute(
                        "SELECT 1 FROM crm_changes WHERE person_id=? AND entity_type='status' AND rowid>(SELECT rowid FROM crm_changes WHERE change_id=?)",
                        (row["person_id"], row["change_id"]),
                    ).fetchone()
                )
            if kind == "attention":
                person = self._person(row["person_id"])
                return {
                    "reason": person["attention_reason"],
                    "until": person["attention_until"],
                } == after and not self.conn.execute(
                    "SELECT 1 FROM crm_changes WHERE person_id=? AND entity_type='attention' AND rowid>(SELECT rowid FROM crm_changes WHERE change_id=?)",
                    (row["person_id"], row["change_id"]),
                ).fetchone()
        except (TaskError, ValueError, TypeError):
            return False
        return False

    def propose(
        self, pid, *, kind, value, expected_version, sources, fingerprint, reason
    ):
        if kind not in (
            "task_create",
            "task_update",
            "description",
            "status",
        ) or not isinstance(value, dict):
            raise TaskError("invalid proposal")
        if len(json.dumps(value)) > 16000:
            raise TaskError("proposal is too large")
        with self.people.crm_transaction():
            self._person(pid)
            refs = self.validate_sources(pid, sources) if sources else []
            prior = self.conn.execute(
                "SELECT * FROM crm_proposals WHERE person_id=? AND intent_key=? AND support_source_fingerprint=?",
                (pid, "ui:" + kind, fingerprint),
            ).fetchone()
            if prior:
                return self._proposal(prior)
            proposal_id = "proposal_" + uuid.uuid4().hex
            now = self.people._now()
            stored = (
                value.get("text", "") if kind == "description" else json.dumps(value)
            )
            self.conn.execute(
                """INSERT INTO crm_proposals(proposal_id,person_id,target_kind,target_id,target_slot,proposed_value,expected_target_version,reason,source_refs,intent_key,support_source_fingerprint,input_revision,state,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,0,'pending',?,?)""",
                (
                    proposal_id,
                    pid,
                    kind,
                    value.get("task_id"),
                    value.get("slot") if kind == "description" else None,
                    stored,
                    expected_version,
                    _clean_reason(reason, "Review suggested change"),
                    json.dumps(refs),
                    "ui:" + kind,
                    fingerprint,
                    now,
                    now,
                ),
            )
            return self._proposal(
                self.conn.execute(
                    "SELECT * FROM crm_proposals WHERE proposal_id=?", (proposal_id,)
                ).fetchone()
            )

    def resolve(
        self,
        pid,
        proposal_id,
        *,
        decision,
        operation_id,
        timezone,
        value=None,
        actor="director",
        human_requested=False,
        allowed_source_ids=None,
        **audit,
    ):
        if actor != "director" and not human_requested:
            raise TaskConflict(
                "proposal resolution requires the director", code="human_protected"
            )
        if decision not in ("accepted", "dismissed"):
            raise TaskError("decision must be accepted or dismissed")
        op = _clean_operation_id(operation_id)
        fp = _fingerprint(
            {
                "resolve": proposal_id,
                "person": pid,
                "decision": decision,
                "value": value,
                "actor": actor,
                "expected_version": audit.get("expected_version"),
                "expected_target_version": audit.get("expected_target_version"),
            }
        )
        with self.people.crm_transaction():
            legacy_fp = (
                _fingerprint(
                    {
                        "resolve": proposal_id,
                        "person": pid,
                        "decision": decision,
                        "value": value,
                        "actor": actor,
                    }
                )
                if audit.get("expected_version") is None
                and audit.get("expected_target_version") is None
                else None
            )
            replay = self.tasks._replay(op, fp, legacy_fingerprint=legacy_fp)
            if replay:
                row = self.conn.execute(
                    "SELECT source_refs FROM crm_proposals WHERE person_id=? AND proposal_id=?",
                    (pid, proposal_id),
                ).fetchone()
                if row:
                    source_ids(self.people, pid, json.loads(row[0]), allowed_source_ids)
                return self._permitted_receipt(pid, replay, allowed_source_ids)
            row = self.conn.execute(
                "SELECT * FROM crm_proposals WHERE person_id=? AND proposal_id=?",
                (pid, proposal_id),
            ).fetchone()
            if not row:
                raise TaskNotFound("unknown proposal")
            refs = source_ids(
                self.people, pid, json.loads(row["source_refs"]), allowed_source_ids
            )
            proposal = self._proposal(row, allowed_source_ids)
            if row["state"] != "pending":
                raise TaskConflict("Proposal was already resolved", code="crm_conflict")
            if (
                audit.get("expected_version", row["version"]) != row["version"]
                or audit.get("expected_target_version", row["expected_target_version"])
                != row["expected_target_version"]
            ):
                raise TaskConflict("Proposal changed", code="crm_conflict")
            committed = {}
            if decision == "accepted":
                fields = value if value is not None else proposal["value"]
                if not isinstance(fields, dict):
                    raise TaskError("edited proposal must be an object")
                kind = row["target_kind"]
                write = dict(
                    operation_id="target_" + _fingerprint({"proposal_operation": op}),
                    actor=actor,
                    session_id=audit.get("session_id"),
                    run_id=audit.get("run_id"),
                )
                if kind == "description":
                    if fields.get("slot") != row["target_slot"]:
                        raise TaskError("An edited suggestion cannot change its target")
                    committed = self.people._patch_description_uncommitted(
                        pid,
                        row["target_slot"],
                        text=fields.get("text"),
                        expected_version=row["expected_target_version"],
                        rationale_summary="Director accepted a sourced description revision.",
                        human_requested=human_requested,
                        source_refs=refs,
                        allowed_source_ids=allowed_source_ids,
                        **write,
                    )
                elif kind == "status":
                    committed = self.set_status(
                        pid,
                        state=fields.get("state"),
                        expected_version=row["expected_target_version"],
                        human_requested=human_requested,
                        **write,
                    )
                elif kind == "task_create":
                    writer = EmailTaskWriter(
                        self.people,
                        allowed=set(allowed_source_ids or ()),
                        timezone=timezone,
                    )
                    if writer.matching_tasks(
                        pid, fields.get("details") or fields.get("title", "")
                    ):
                        raise TaskConflict(
                            "matching work appeared; review saved task",
                            code="task_conflict",
                        )
                    committed = self.tasks._create_uncommitted(
                        pid,
                        title=fields.get("title"),
                        details=fields.get("details"),
                        due_date=fields.get("due_date"),
                        timezone=timezone,
                        human_requested=human_requested,
                        source_refs=refs,
                        allowed_source_ids=allowed_source_ids,
                        **write,
                    )
                else:
                    if (
                        fields.get("task_id") != row["target_id"]
                        or not row["target_id"]
                        or proposal["value"].get("requires_target_review")
                    ):
                        raise TaskConflict(
                            "choose the matching saved task before acceptance",
                            code="task_conflict",
                        )
                    if "due_date" in fields.get("changes", {}):
                        target = self.tasks._task_dict(
                            self.tasks._load_task(pid, row["target_id"])
                        )
                        request = proposal["value"].get("request")
                        if (
                            request is not None
                            and EmailTaskWriter(
                                self.people,
                                allowed=set(allowed_source_ids or ()),
                                timezone=timezone,
                            ).task_relation(target, request)
                            != "same"
                        ):
                            raise TaskConflict(
                                "request does not establish saved obligation",
                                code="task_conflict",
                            )
                    committed = self.tasks._update_uncommitted(
                        pid,
                        row["target_id"],
                        changes=fields.get("changes", {}),
                        expected_version=row["expected_target_version"],
                        timezone=timezone,
                        human_requested=human_requested,
                        source_refs=refs,
                        allowed_source_ids=allowed_source_ids,
                        **write,
                    )
            target = (
                committed.get("change", {}).get("change_id")
                if committed.get("change")
                else None
            )
            self.conn.execute(
                "UPDATE crm_proposals SET state=?,resolution=?,change_id=?,version=version+1,updated_at=? WHERE proposal_id=?",
                (decision, "director", target, self.people._now(), proposal_id),
            )
            after = {**proposal, "state": decision, "change_id": target}
            result = self._record(
                pid,
                "proposal",
                proposal_id,
                1,
                proposal,
                after,
                operation=op,
                fingerprint=fp,
                actor=actor,
                reason=audit.get("rationale_summary") or "Reviewed suggestion",
                sources=refs,
                action="accept" if decision == "accepted" else "dismiss",
                session_id=audit.get("session_id"),
                run_id=audit.get("run_id"),
            )
            result.update(
                {k: v for k, v in committed.items() if k not in ("change", "replayed")}
            )
            self.conn.execute(
                "UPDATE crm_operations SET response_json=? WHERE operation_id=?",
                (json.dumps(result), op),
            )
            return result

    def set_status(
        self, pid, *, state, expected_version, operation_id, actor="director", **audit
    ):
        if actor != "director" and not audit.pop("human_requested", False):
            raise TaskConflict("status requires the director", code="human_protected")
        audit.pop("allowed_source_ids", None)
        if state not in ("open", "in_conversation", "done"):
            raise TaskError("unknown sequence state")
        op = _clean_operation_id(operation_id)
        fp = _fingerprint(
            {"status": pid, "state": state, "version": expected_version, "actor": actor}
        )
        with self.people.crm_transaction():
            replay = self.tasks._replay(op, fp)
            if replay:
                return replay
            person = self._person(pid)
            self._version(person["version"], expected_version)
            before = {"state": person["sequence_state"]}
            self.conn.execute(
                "UPDATE people SET sequence_state=? WHERE person_id=?", (state, pid)
            )
            version = self._bump_person(pid)
            return self._record(
                pid,
                "status",
                pid,
                version,
                before,
                {"state": state},
                operation=op,
                fingerprint=fp,
                actor=actor,
                reason="Director changed relationship status",
                **audit,
            )

    def undo(
        self,
        pid,
        change_id,
        *,
        operation_id,
        actor="director",
        reason=None,
        human_requested=False,
        allowed_source_ids=None,
        **audit,
    ):
        if actor != "director" and not human_requested:
            raise TaskConflict("Undo requires the director", code="human_protected")
        op = _clean_operation_id(operation_id)
        fp = _fingerprint({"undo": change_id, "person": pid, "actor": actor})
        with self.people.crm_transaction():
            self._person(pid)
            original = self.conn.execute(
                "SELECT * FROM crm_changes WHERE person_id=? AND change_id=?",
                (pid, change_id),
            ).fetchone()
            if not original:
                raise TaskNotFound("unknown change")
            source_ids(
                self.people,
                pid,
                json.loads(original["source_refs"]),
                allowed_source_ids,
            )
            if original["entity_type"] == "task":
                return self.tasks.undo(
                    pid,
                    change_id,
                    operation_id=op,
                    actor=actor,
                    human_requested=human_requested,
                    reason=reason,
                    expected_version=audit.get("expected_version"),
                    allowed_source_ids=allowed_source_ids,
                    session_id=audit.get("session_id"),
                    run_id=audit.get("run_id"),
                )
            if original["entity_type"] == "description":
                legacy = self.conn.execute(
                    "SELECT 1 FROM crm_operations WHERE operation_id=? AND entity_type='description'",
                    (op,),
                ).fetchone()
                if legacy:
                    replay = self.tasks._replay(op, fp)
                    return self._permitted_receipt(pid, replay, allowed_source_ids)
                return DescriptionService(
                    self.people, allowed_source_ids=allowed_source_ids
                ).undo(
                    pid,
                    change_id,
                    operation_id=op,
                    actor=actor,
                    human_requested=human_requested,
                    reason=reason or "Director undid a description change.",
                )
            replay = self.tasks._replay(op, fp)
            if replay:
                return self._permitted_receipt(pid, replay, allowed_source_ids)
            if not self._can_undo(original, allowed_source_ids):
                raise TaskConflict(
                    "This field changed after the recorded change", code="crm_conflict"
                )
            row = original
            proposal_id = None
            if row["entity_type"] == "proposal":
                proposal_id = row["entity_id"]
                after = json.loads(row["after_json"])
                target = after.get("change_id")
                row = self.conn.execute(
                    "SELECT * FROM crm_changes WHERE person_id=? AND change_id=?",
                    (pid, target),
                ).fetchone()
                if not row:
                    raise TaskConflict(
                        "Proposal has no target change to undo", code="crm_conflict"
                    )
                source_ids(
                    self.people, pid, json.loads(row["source_refs"]), allowed_source_ids
                )
            kind = row["entity_type"]
            before = json.loads(row["before_json"]) if row["before_json"] else None
            child_op = "undo_target_" + _fingerprint(
                {"op": op, "change": row["change_id"]}
            )
            if kind == "task":
                if row["action"] == "create":
                    current = self.tasks.get(
                        pid, row["entity_id"], allowed_source_ids=allowed_source_ids
                    )
                    result = self.tasks._update_uncommitted(
                        pid,
                        row["entity_id"],
                        changes={"state": "cancelled"},
                        expected_version=row["entity_version"],
                        timezone=current["due_timezone"] or "America/Los_Angeles",
                        operation_id=child_op,
                        actor=actor,
                        human_requested=human_requested,
                        allowed_source_ids=allowed_source_ids,
                        reason="Undid accepted task creation",
                    )
                else:
                    result = self.tasks.undo(
                        pid,
                        row["change_id"],
                        operation_id=child_op,
                        actor=actor,
                        human_requested=human_requested,
                        reason=reason,
                        allowed_source_ids=allowed_source_ids,
                    )
            elif kind == "description":
                result = DescriptionService(
                    self.people, allowed_source_ids=allowed_source_ids
                ).undo(
                    pid,
                    row["change_id"],
                    operation_id=child_op,
                    actor=actor,
                    human_requested=human_requested,
                )
            else:
                person = self._person(pid)
                if kind == "status":
                    current = {"state": person["sequence_state"]}
                    restored = before
                    self.conn.execute(
                        "UPDATE people SET sequence_state=? WHERE person_id=?",
                        (before["state"], pid),
                    )
                elif kind == "attention":
                    current = {
                        "reason": person["attention_reason"],
                        "until": person["attention_until"],
                    }
                    restored = before
                    self.conn.execute(
                        "UPDATE people SET attention_reason=?,attention_until=? WHERE person_id=?",
                        (before["reason"], before["until"], pid),
                    )
                else:
                    raise TaskError("This receipt cannot be undone")
                version = self._bump_person(pid)
                result = {kind: restored}
            if proposal_id:
                self.conn.execute(
                    "UPDATE crm_proposals SET state='dismissed',resolution='undo',version=version+1,updated_at=? WHERE proposal_id=?",
                    (self.people._now(), proposal_id),
                )
                current = json.loads(original["after_json"])
                restored = {**current, "state": "dismissed"}
                kind = "proposal"
                version = 2
            recorded = self._record(
                pid,
                kind,
                original["entity_id"],
                (
                    version
                    if kind in ("status", "attention", "proposal")
                    else row["entity_version"] + 1
                ),
                current,
                restored,
                operation=op,
                fingerprint=fp,
                actor=actor,
                reason=reason or "Undid CRM change",
                action="undo",
                compensates=change_id,
                sources=json.loads(original["source_refs"]),
                session_id=audit.get("session_id"),
                run_id=audit.get("run_id"),
            )
            recorded.update(
                {k: v for k, v in result.items() if k not in ("change", "replayed")}
            )
            self.conn.execute(
                "UPDATE crm_operations SET response_json=? WHERE operation_id=?",
                (json.dumps(recorded), op),
            )
            self.conn.execute(
                "UPDATE crm_interpretations SET suppressed=1 WHERE person_id=? AND change_id=?",
                (pid, row["change_id"]),
            )
            return recorded

    def dismiss_review(
        self, pid, message_id, *, operation_id, actor="director", **audit
    ):
        op = _clean_operation_id(operation_id)
        fp = _fingerprint({"review": pid, "message": message_id, "actor": actor})
        with self.people.crm_transaction():
            replay = self.tasks._replay(op, fp)
            if replay:
                return replay
            self._person(pid)
            message = self.conn.execute(
                "SELECT 1 FROM gmail_messages WHERE person_id=? AND message_id=? AND direction='inbound'",
                (pid, message_id),
            ).fetchone()
            if not message:
                raise TaskNotFound("unknown inbound message")
            self.conn.execute(
                "INSERT OR IGNORE INTO crm_review_dismissals VALUES(?,?)",
                (pid, message_id),
            )
            return self._record(
                pid,
                "review",
                message_id,
                1,
                None,
                {"message_id": message_id},
                operation=op,
                fingerprint=fp,
                actor=actor,
                reason="Dismissed reply review",
                action="dismiss",
                **audit,
            )

    def contacts(
        self,
        *,
        timezone,
        person_id=None,
        allowed_source_ids=None,
        now: datetime | None = None,
        limit=200,
        offset=0,
        query="",
        state=None,
        needs_attention=False,
        include_saved=False,
    ):
        from coworker.crm_mail_repository import MailRepository

        today = today_in(timezone, now=now).isoformat()
        with self.people._lock:
            board = self.people.list_board()
            # Batch relationship-wide reads. Opening Contacts never runs a model
            # or performs a task/proposal query for every visible row.
            hidden = False
            tasks_by_person: dict[str, list[dict[str, Any]]] = {}
            for task_row in self.conn.execute("""SELECT t.* FROM person_tasks t
                    JOIN people p ON p.person_id=t.person_id WHERE t.state='open' AND p.deleted_at IS NULL
                    ORDER BY t.due_date IS NULL,t.due_date,t.created_at,t.task_id"""):
                task = self.tasks._task_dict(task_row)
                if not sources_visible(
                    self.people,
                    task["person_id"],
                    task["source_refs"],
                    allowed_source_ids,
                ):
                    if not person_id or person_id == task["person_id"]:
                        hidden = True
                    continue
                task = self.tasks._project_task(task, allowed_source_ids)
                task["due_status"] = due_status(
                    task["due_date"],
                    today_in(task["due_timezone"] or timezone, now=now),
                )
                tasks_by_person.setdefault(task["person_id"], []).append(task)
            proposal_counts = {
                row[0]: row[1]
                for row in self.conn.execute(
                    "SELECT person_id,count(*) FROM crm_proposals WHERE state='pending' GROUP BY person_id"
                )
            }
            ambiguous = set()
            for gap in self.conn.execute(
                "SELECT person_id,fields_json FROM person_attachments WHERE record_type='knowledge_gap'"
            ):
                fields = json.loads(gap[1])
                if fields.get("kind") == "unassigned_reply" and not fields.get(
                    "resolved"
                ):
                    ambiguous.add(gap[0])
            dismissed = {
                (row[0], row[1])
                for row in self.conn.execute(
                    "SELECT person_id,message_id FROM crm_review_dismissals"
                )
            }
            analysis = {
                row["person_id"]: MailRepository.analysis_projection(dict(row))
                for row in self.conn.execute("SELECT * FROM crm_analysis_state")
            }
            rows = []
            candidates = [*board["open"], *board["in_conversation"], *board["done"]]
            if include_saved:
                candidates.extend(board["backlog"])
            for person in candidates:
                if person_id and person["person_id"] != person_id:
                    continue
                if state and person["sequence_state"] != state:
                    continue
                if (
                    query.casefold()
                    not in " ".join(
                        str(person.get(k) or "")
                        for k in ("first_name", "last_name", "title", "company")
                    ).casefold()
                ):
                    continue
                pid = person["person_id"]
                descriptions = {}
                for slot, description in person.get("descriptions", {}).items():
                    if sources_visible(
                        self.people, pid, description["source_refs"], allowed_source_ids
                    ):
                        descriptions[slot] = description
                    else:
                        descriptions[slot] = {"slot": slot, "restricted": True}
                        hidden = True
                person = {**person, "descriptions": descriptions}
                tasks = tasks_by_person.get(pid, [])
                proposal_count = len(
                    self.proposals(pid, allowed_source_ids=allowed_source_ids)
                )
                if proposal_count != proposal_counts.get(pid, 0):
                    hidden = True
                reasons = []
                follow = person.get("follow_up") or {}
                if pid in ambiguous:
                    reasons.append(
                        {
                            "code": "attribution",
                            "label": "Reply attribution needs review",
                        }
                    )
                if person.get("attention_reason") and (
                    not person.get("attention_until")
                    or person["attention_until"] >= today
                ):
                    reasons.append(
                        {"code": "manual", "label": person["attention_reason"]}
                    )
                for status, code, label in (
                    ("overdue", "overdue", "Overdue task"),
                    ("today", "due_today", "Task due today"),
                ):
                    count = sum(task["due_status"] == status for task in tasks)
                    if count:
                        reasons.append({"code": code, "label": label, "count": count})
                if follow.get("needed") and follow.get("reason") == "reply_unanswered":
                    pending = [
                        mid
                        for mid in person.get("unanswered_message_ids", [])
                        if (pid, mid) not in dismissed
                    ]
                    if pending:
                        reasons.append(
                            {
                                "code": "reply",
                                "label": "Reply to review",
                                "message_ids": pending,
                            }
                        )
                if proposal_count:
                    reasons.append(
                        {
                            "code": "suggestion",
                            "label": "Suggestion to review",
                            "count": proposal_count,
                        }
                    )
                if needs_attention and not reasons:
                    continue
                rows.append(
                    {
                        **person,
                        "attention_reasons": reasons,
                        "next_action": tasks[0] if tasks else None,
                        "mail_analysis": analysis.get(
                            pid, MailRepository.analysis_projection(None)
                        ),
                    }
                )
            ranks = {
                code: i
                for i, code in enumerate(
                    (
                        "attribution",
                        "manual",
                        "overdue",
                        "due_today",
                        "reply",
                        "suggestion",
                    )
                )
            }
            rows.sort(
                key=lambda p: (
                    (
                        ranks.get(p["attention_reasons"][0]["code"], 6)
                        if p["attention_reasons"]
                        else 6
                    ),
                    (p["next_action"] or {}).get("due_date") or "9999",
                    p["person_id"],
                )
            )
            return {
                "contacts": rows[offset : offset + limit],
                "total": len(rows),
                "truncated": len(rows) > offset + limit,
                "timezone": timezone,
                "today": today,
                "partial": hidden,
                "mail_sync": MailRepository(self.people).public_sync_state(),
            }
