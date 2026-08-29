"""Versioned presentation events emitted by the sidecar."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

EVENT_VERSION = 2
EVENT_TYPES = frozenset(
    {
        "turn_start",
        "turn_stopping",
        "turn_stopped",
        "assistant_delta",
        "permission_required",
        "approval_resolved",
        "tool_started",
        "tool_finished",
        "tool_recovery",
        "provider_recovery",
        "turn_end",
        "error",
    }
)


@dataclass(frozen=True)
class TurnIdentity:
    session_id: str
    run_id: str
    message_id: str
    part_id: str


def new_turn_identity(session_id: str) -> TurnIdentity:
    return TurnIdentity(
        session_id=session_id,
        run_id=f"run_{uuid.uuid4().hex}",
        message_id=f"message_{uuid.uuid4().hex}",
        part_id=f"part_{uuid.uuid4().hex}",
    )


def validate_event(event: object) -> str | None:
    if not isinstance(event, dict):
        return "event must be an object"
    version = event.get("version")
    if version != EVENT_VERSION:
        return f"unsupported version {version}"
    for field in (
        "type",
        "session_id",
        "run_id",
        "event_id",
        "message_id",
        "part_id",
    ):
        if not isinstance(event.get(field), str) or not event[field]:
            return f"missing {field}"
    if event["type"] not in EVENT_TYPES:
        return f"unsupported event type {event['type']}"
    event_type = event["type"]
    if event_type == "turn_start" and event.get("state") != "running":
        return "turn_start.state must be running"
    if event_type == "turn_stopping":
        if event.get("state") != "stopping":
            return "turn_stopping.state must be stopping"
        if not isinstance(event.get("message"), str):
            return "turn_stopping.message must be a string"
    if event_type == "turn_stopped":
        if event.get("state") != "stopped":
            return "turn_stopped.state must be stopped"
        if not isinstance(event.get("text"), str):
            return "turn_stopped.text must be a string"
        if not isinstance(event.get("message"), str):
            return "turn_stopped.message must be a string"
    if event_type == "assistant_delta" and not isinstance(event.get("delta"), str):
        return "assistant_delta.delta must be a string"
    if event_type == "turn_end":
        if event.get("state") not in {"complete", "partial", "stopped", "interrupted"}:
            return "turn_end.state must be complete, partial, stopped, or interrupted"
        if not isinstance(event.get("text"), str):
            return "turn_end.text must be a string"
    if event_type == "error":
        # `held` is not a softer `failed`. It means a consequential call was
        # dispatched and never reported back, so the outcome is a person's to
        # settle. It carries the effect id because the operator's next move is
        # to open that row, not to try again.
        if event.get("state") not in {"failed", "held"}:
            return "error.state must be failed or held"
        if not isinstance(event.get("message"), str):
            return "error.message must be a string"
        if event.get("state") == "held":
            if event.get("code") != "outcome_unknown":
                return "error.code must be outcome_unknown when state is held"
            if not isinstance(event.get("effect_id"), str) or not event["effect_id"]:
                return "error.effect_id must be a non-empty string when state is held"
        failure = event.get("failure")
        if failure is not None:
            if not isinstance(failure, dict):
                return "error.failure must be an object"
            for field in ("code", "provider", "model"):
                if not isinstance(failure.get(field), str) or not failure[field]:
                    return f"error.failure.{field} must be a non-empty string"
            if not isinstance(failure.get("attempts"), int) or failure["attempts"] < 1:
                return "error.failure.attempts must be a positive integer"
            if (
                not isinstance(failure.get("recovery_count"), int)
                or failure["recovery_count"] < 0
            ):
                return "error.failure.recovery_count must be a non-negative integer"
            if not isinstance(failure.get("exhausted"), bool):
                return "error.failure.exhausted must be a boolean"
    if event_type in {"permission_required", "tool_started", "tool_finished"}:
        for field in ("id", "name"):
            if not isinstance(event.get(field), str) or not event[field]:
                return f"{event_type}.{field} must be a non-empty string"
    if event_type == "approval_resolved":
        for field in (
            "id",
            "name",
            "resolution",
            "requested_at",
            "resolved_at",
            "scope",
            "execution_status",
        ):
            if not isinstance(event.get(field), str) or not event[field]:
                return f"approval_resolved.{field} must be a non-empty string"
        if event.get("resolution") not in {
            "allowed",
            "denied",
            "cancelled",
            "expired",
        }:
            return "approval_resolved.resolution is invalid"
        if event.get("decision") not in {"allow", "deny", None}:
            return "approval_resolved.decision is invalid"
        if event.get("actor") is not None and not isinstance(event.get("actor"), str):
            return "approval_resolved.actor must be a string or null"
        if event.get("execution_error") is not None and not isinstance(
            event.get("execution_error"), str
        ):
            return "approval_resolved.execution_error must be a string or null"
    if event_type in {"permission_required", "tool_started"} and not isinstance(
        event.get("arguments"), dict
    ):
        return f"{event_type}.arguments must be an object"
    if event_type == "permission_required" and not isinstance(event.get("reason"), str):
        return "permission_required.reason must be a string"
    if (
        event_type == "permission_required"
        and event.get("resource") is not None
        and not isinstance(event.get("resource"), dict)
    ):
        return "permission_required.resource must be an object"
    if event_type == "tool_finished":
        if not isinstance(event.get("ok"), bool):
            return "tool_finished.ok must be a boolean"
        if not isinstance(event.get("result"), dict):
            return "tool_finished.result must be an object"
    if event_type == "tool_recovery":
        for field in ("command_id", "call_id", "name", "action", "status"):
            if not isinstance(event.get(field), str) or not event[field]:
                return f"tool_recovery.{field} must be a non-empty string"
        if event.get("action") not in {"retry", "repair", "continue"}:
            return "tool_recovery.action is invalid"
    if event_type == "provider_recovery":
        for field in ("action", "provider", "model", "reason", "message"):
            if not isinstance(event.get(field), str) or not event[field]:
                return f"provider_recovery.{field} must be a non-empty string"
        if event.get("action") not in {"retry", "failover"}:
            return "provider_recovery.action is invalid"
        if not isinstance(event.get("attempt"), int) or event["attempt"] < 1:
            return "provider_recovery.attempt must be a positive integer"
        if not isinstance(event.get("delay_ms"), int) or event["delay_ms"] < 0:
            return "provider_recovery.delay_ms must be a non-negative integer"
    return None


def build_event(
    identity: TurnIdentity,
    event_type: str,
    *,
    event_id: str,
    **payload: Any,
) -> dict[str, Any]:
    event = {
        "version": EVENT_VERSION,
        "type": event_type,
        "session_id": identity.session_id,
        "run_id": identity.run_id,
        "event_id": event_id,
        "message_id": identity.message_id,
        "part_id": identity.part_id,
        **payload,
    }
    problem = validate_event(event)
    if problem is not None:
        raise ValueError(problem)
    return event


class TurnEventStream:
    """Persist and forward the same canonical event object for one turn."""

    def __init__(
        self,
        *,
        identity: TurnIdentity,
        store: Any,
        emit: Callable[[dict[str, Any]], Awaitable[None]] | None,
        persist_transform: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        self.identity = identity
        self._store = store
        self._emit = emit
        self._persist_transform = persist_transform

    async def send(self, event: dict[str, Any]) -> dict[str, Any]:
        payload = dict(event)
        event_type = str(payload.pop("type"))
        envelope = build_event(
            self.identity,
            event_type,
            event_id=f"event_{uuid.uuid4().hex}",
            **payload,
        )
        persisted = (
            self._persist_transform(envelope)
            if self._persist_transform is not None
            else envelope
        )
        self._store.append_event(self.identity.session_id, persisted)
        if self._emit is not None:
            await self._emit(envelope)
        return envelope
