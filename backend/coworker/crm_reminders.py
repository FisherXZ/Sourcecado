"""Optional tasks after confirmed reviewed sends. No connector is reachable here."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from coworker.crm_tasks import TaskConflict, TaskError, TaskNotFound, TaskService
from coworker.people import PersonStore
from coworker.store import ConversationStore


class ReminderService:
    def __init__(self, people: PersonStore, tasks: TaskService, store: ConversationStore,
                 timezone: Callable[[], str]):
        self.people, self.tasks, self.store, self.timezone = people, tasks, store, timezone

    def _confirmed(self, person_id: str, message_id: str) -> dict[str, Any]:
        self.tasks._live_person(person_id)
        event = self.people._conn.execute(
            "SELECT payload FROM events WHERE person_id=? AND external_key=? AND source='gmail' AND kind='send'",
            (person_id, f"gmail:message:{message_id}"),
        ).fetchone()
        payload = json.loads(event["payload"]) if event else {}
        approval = self.store.get_inbox(str(payload.get("approval_id") or "")) or {}
        result, resource = approval.get("execution_result") or {}, approval.get("resource") or {}
        if not (payload.get("sent") is True and payload.get("message_id") == message_id
                and approval.get("name") == "gmail_send" and approval.get("decision") == "allow"
                and approval.get("execution_status") == "succeeded"
                and isinstance(resource, dict) and resource.get("kind") == "gmail_send_authority"
                and resource.get("person_id") == person_id
                and isinstance(result, dict) and result.get("sent") is True
                and result.get("message_id") == message_id):
            raise TaskConflict(
                "There is no confirmed saved send for this person and message. Do not resend to create a reminder.",
                code="send_not_confirmed",
            )
        return payload

    @staticmethod
    def _operation(person_id: str, message_id: str) -> str:
        # One ordinary task operation per person/message, across windows and restarts.
        digest = hashlib.sha256(json.dumps([person_id, message_id]).encode()).hexdigest()
        return f"sent-reminder:{digest}"

    def context(self, person_id: str, message_id: str) -> dict[str, Any]:
        with self.people._lock:
            self._confirmed(person_id, message_id)
            person = self.people.get(person_id)
            operation = self._operation(person_id, message_id)
            saved = self.people._conn.execute(
                "SELECT entity_id FROM crm_operations WHERE operation_id=? AND entity_type='task'", (operation,),
            ).fetchone()
            task = self.tasks.get(person_id, saved["entity_id"]) if saved else None
        name = " ".join(str(person.get(key) or "") for key in ("first_name", "last_name")).strip()
        return {"operation_id": operation, "timezone": self.timezone(),
                "title": f"Follow up with {name or 'this person'}"[:200], "task": task}

    def create(self, person_id: str, message_id: str, payload: Any) -> dict[str, Any]:
        with self.people.crm_transaction() as conn:
            sent = self._confirmed(person_id, message_id)
            required = {"title", "due_date", "operation_id", "timezone"}
            if not isinstance(payload, dict) or set(payload) != required:
                raise TaskError("title, due_date, operation_id and timezone are required")
            operation = self._operation(person_id, message_id)
            if payload["operation_id"] != operation:
                raise TaskConflict("Reopen the follow-up before saving.", code="operation_conflict")
            timezone = self.timezone()
            if payload["timezone"] != timezone:
                raise TaskConflict("Your saved timezone changed. Reopen the follow-up to review the date.", code="timezone_changed")
            if not isinstance(payload["due_date"], str) or not payload["due_date"].strip():
                raise TaskError("Choose a follow-up date before saving.")
            source_id = f"source_ref_{operation.split(':', 1)[1]}"
            fields = {"provider": "Gmail", "message_id": message_id,
                      "thread_id": sent.get("thread_id"), "approval_id": sent["approval_id"]}
            now = self.people._now()
            conn.execute("""INSERT INTO person_attachments
                (id,person_id,record_type,idempotency_key,fields_json,restricted,created_at,updated_at)
                VALUES (?,?,'source_ref',?,?,0,?,?) ON CONFLICT DO NOTHING""",
                (source_id, person_id, operation, json.dumps(fields, sort_keys=True), now, now))
            # Same transactional writer as every other ordinary task; source and
            # task/history/operation either all commit or all roll back.
            response = self.tasks._create_uncommitted(
                person_id, title=payload["title"], due_date=payload["due_date"],
                timezone=timezone, operation_id=operation, actor="director",
                reason="Director set a follow-up after a confirmed send.", source_refs=[source_id],
            )
        return self.tasks._project_response(response, None)


def reminder_router(*, people: PersonStore, tasks: TaskService, store: ConversationStore,
                    timezone: Callable[[], str]) -> APIRouter:
    router = APIRouter()
    service = ReminderService(people, tasks, store, timezone)
    path = "/v1/people/{person_id}/sent-messages/{message_id}/reminder"

    def failure(exc: TaskError) -> JSONResponse:
        status = 404 if isinstance(exc, TaskNotFound) else 409 if isinstance(exc, TaskConflict) else 400
        return JSONResponse({"error": str(exc), "code": getattr(exc, "code", "invalid_reminder")}, status_code=status)

    @router.get(path)
    def context(person_id: str, message_id: str):
        try:
            return service.context(person_id, message_id)
        except TaskError as exc:
            return failure(exc)

    @router.post(path)
    async def save(person_id: str, message_id: str, request: Request):
        try:
            payload = await request.json()
            result = service.create(person_id, message_id, payload)
            return JSONResponse(result, status_code=200 if result["replayed"] else 201)
        except TaskError as exc:
            return failure(exc)
        except (sqlite3.Error, RuntimeError):
            return JSONResponse({"error": "Couldn’t save the follow-up. Your email was already sent; retry only this task."}, status_code=503)

    return router
