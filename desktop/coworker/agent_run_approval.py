"""Approval persistence values shared by the store and Agent Run authority."""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Mapping


APPROVED_TOOL_OUTCOME_UNKNOWN_ERROR = (
    "Outcome is unknown: the approved action did not durably record completion. "
    "Verify the external resource before retrying."
)
AGENT_RUN_TOOL_BUDGET_EXHAUSTED = "Agent Run tool budget exhausted"


@dataclass(frozen=True)
class ApprovalParkRequest:
    item_id: str
    call_id: str
    kind: str
    name: str
    arguments: dict[str, Any]
    reason: str | None
    session_id: str
    run_id: str
    message_id: str
    part_id: str
    resource: dict[str, Any] | None
    requested_at: str
    expires_at: str
    scope: str = "once"


def build_approval_park_request(
    *,
    item_id: str,
    call_id: str,
    name: str,
    arguments: dict[str, Any],
    reason: str | None,
    session_id: str,
    run_id: str,
    message_id: str,
    part_id: str,
    resource: dict[str, Any] | None,
    ttl_seconds: float,
    now: datetime | None = None,
) -> ApprovalParkRequest:
    ttl = float(ttl_seconds)
    if not math.isfinite(ttl) or ttl <= 0:
        raise ValueError("approval TTL must be a positive finite duration")
    requested = now or datetime.now(UTC)
    if requested.tzinfo is None:
        requested = requested.replace(tzinfo=UTC)
    requested = requested.astimezone(UTC)
    try:
        expires = requested + timedelta(seconds=ttl)
    except (OverflowError, ValueError) as exc:
        raise ValueError("approval TTL is out of range") from exc
    return ApprovalParkRequest(
        item_id=str(item_id),
        call_id=str(call_id),
        kind="approval",
        name=str(name),
        arguments=dict(arguments),
        reason=str(reason) if reason is not None else None,
        session_id=str(session_id),
        run_id=str(run_id),
        message_id=str(message_id),
        part_id=str(part_id),
        resource=dict(resource) if resource is not None else None,
        requested_at=requested.isoformat(),
        expires_at=expires.isoformat(),
    )


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def project_inbox_row(row: sqlite3.Row | Mapping[str, Any]) -> dict[str, Any]:
    item = dict(row)
    raw = item.get("arguments") or "{}"
    try:
        item["arguments"] = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        item["arguments"] = {}
    item.setdefault("actor", None)
    item["requested_at"] = item.get("requested_at") or item.get("created_at")
    item.setdefault("resolved_at", None)
    item["scope"] = item.get("scope") or "once"
    item["execution_status"] = item.get("execution_status") or "pending"
    item.setdefault("execution_error", None)
    item.setdefault("execution_claimant", None)
    raw_result = item.get("execution_result")
    if isinstance(raw_result, str):
        try:
            item["execution_result"] = json.loads(raw_result)
        except json.JSONDecodeError:
            item["execution_result"] = None
    else:
        item["execution_result"] = None
    item.setdefault("expires_at", None)
    item.setdefault("reason", None)
    item.setdefault("session_id", None)
    item.setdefault("run_id", None)
    item.setdefault("message_id", None)
    item.setdefault("part_id", None)
    item.setdefault("recovery_command_id", None)
    item.setdefault("original_call_id", None)
    raw_resource = item.get("resource")
    if isinstance(raw_resource, str):
        try:
            item["resource"] = json.loads(raw_resource)
        except json.JSONDecodeError:
            item["resource"] = None
    else:
        item["resource"] = None
    return item
