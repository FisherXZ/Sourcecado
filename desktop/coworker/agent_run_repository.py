"""SQLite authority for durable Agent Run state."""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from coworker.agent_run_approval import (
    APPROVED_TOOL_OUTCOME_UNKNOWN_ERROR,
    ApprovalParkRequest,
    canonical_json,
    project_inbox_row,
)
from coworker.agent_run_continuation import (
    merge_continuation,
    project_continuation,
    transcript_prefix_sha256,
    valid_sha256,
)
from coworker.agent_runs import (
    AGENT_RUN_CHECKPOINT_KINDS,
    AGENT_RUN_STATES,
    TERMINAL_AGENT_RUN_STATES,
    add_usage,
    bounded_checkpoint_payload,
    json_list,
    json_object,
    merge_unique_json,
    merge_unique_strings,
    nullable_json_object,
    original_goal_fingerprint,
    project_artifact_refs,
    project_source_refs,
    project_terminal_result,
    redact_sensitive_assignments,
    sanitize_agent_run_value,
)
from coworker.agent_run_state import approval_ready_transition


_RESUME_MIGRATION = "agent_run_resume_v1"
_PRIVACY_MIGRATION = "agent_run_privacy_v3"
# Keep crashed work recoverable promptly and prevent accidental day-long claims.
MAX_LEASE_SECONDS = 60 * 60
_MAX_SAFE_ID = 256
_SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


@dataclass(frozen=True)
class AgentRunLease:
    run_id: str
    owner_id: str
    version: int
    expires_at: str


@dataclass(frozen=True)
class ResolvedApprovalLease:
    lease: AgentRunLease
    decision: Literal["allow", "deny"]


@dataclass(frozen=True)
class StartedAgentRunLease:
    run: dict[str, Any]
    lease: AgentRunLease


class AgentRunVersionConflict(RuntimeError):
    """The caller's compare-and-swap version is no longer current."""


class AgentRunLeaseLost(RuntimeError):
    """The lease expired, was released, or belongs to another owner."""


class AgentRunStartConflict(RuntimeError):
    """An existing run cannot be started through the new-run entry point."""


class _AgentRunRepositoryBase:
    """Own Agent Run persistence on the store's existing connection and lock."""

    def __init__(self, connection: sqlite3.Connection, lock: threading.RLock) -> None:
        self._conn = connection
        self._lock = lock
        self._initialize_schema()
        self._ensure_fingerprint_columns()
        self._migrate_agent_run_privacy()
        self._migrate_resume_schema()
        self.reconcile_expired_leases()

    def acquire_lease(
        self,
        run_id: str,
        owner_id: str,
        expected_version: int,
        lease_seconds: int | float,
        now: datetime | None = None,
    ) -> AgentRunLease | None:
        owner = _lease_owner(owner_id)
        seconds = _lease_seconds(lease_seconds)
        stamp = _timestamp(now)
        expires_at = _timestamp(_as_datetime(stamp) + timedelta(seconds=seconds))
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    "SELECT * FROM agent_runs WHERE run_id = ?", (run_id,)
                ).fetchone()
                if row is None:
                    raise KeyError(run_id)
                current_state = str(row["current_state"])
                if current_state in TERMINAL_AGENT_RUN_STATES or current_state in {
                    "waiting_approval",
                    "waiting_question",
                }:
                    self._conn.commit()
                    return None
                active_owner = row["lease_owner"]
                active_expiry = row["lease_expires_at"]
                if (
                    active_owner is not None
                    and active_expiry is not None
                    and str(active_expiry) > stamp
                    and str(active_owner) != owner
                ):
                    self._conn.commit()
                    return None
                if int(row["version"]) != int(expected_version):
                    raise AgentRunVersionConflict(run_id)
                cursor = self._conn.execute(
                    """
                    UPDATE agent_runs SET
                        lease_owner = ?, lease_expires_at = ?,
                        version = version + 1, updated_at = ?
                    WHERE run_id = ? AND version = ?
                      AND current_state NOT IN (
                          'waiting_approval', 'waiting_question',
                          'complete', 'partial', 'stopped', 'failed'
                      )
                      AND (
                          lease_owner IS NULL OR lease_expires_at IS NULL
                          OR lease_expires_at <= ? OR lease_owner = ?
                      )
                    """,
                    (
                        owner,
                        expires_at,
                        stamp,
                        run_id,
                        int(expected_version),
                        stamp,
                        owner,
                    ),
                )
                if cursor.rowcount != 1:
                    current = self._conn.execute(
                        "SELECT * FROM agent_runs WHERE run_id = ?", (run_id,)
                    ).fetchone()
                    if current is None:
                        raise KeyError(run_id)
                    current_state = str(current["current_state"])
                    if current_state in TERMINAL_AGENT_RUN_STATES or current_state in {
                        "waiting_approval",
                        "waiting_question",
                    }:
                        self._conn.commit()
                        return None
                    if (
                        current["lease_owner"] is not None
                        and current["lease_expires_at"] is not None
                        and str(current["lease_expires_at"]) > stamp
                        and str(current["lease_owner"]) != owner
                    ):
                        self._conn.commit()
                        return None
                    raise AgentRunVersionConflict(run_id)
                row = self._conn.execute(
                    "SELECT * FROM agent_runs WHERE run_id = ?", (run_id,)
                ).fetchone()
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return _lease_from_row(row)

    def acquire_resolved_waiting_lease(
        self,
        run_id: str,
        owner_id: str,
        expected_version: int,
        interaction_id: str,
        lease_seconds: int | float,
        now: datetime | None = None,
    ) -> ResolvedApprovalLease | None:
        """Resume one exact, durably resolved approval under a new lease."""
        owner = _lease_owner(owner_id)
        interaction = _bounded_id(interaction_id, "interaction_id")
        seconds = _lease_seconds(lease_seconds)
        stamp = _timestamp(now)
        expires_at = _timestamp(_as_datetime(stamp) + timedelta(seconds=seconds))
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    "SELECT * FROM agent_runs WHERE run_id = ?", (run_id,)
                ).fetchone()
                if row is None:
                    raise KeyError(run_id)
                state = str(row["current_state"])
                if state not in {"waiting_approval", "interrupted"} or (
                    row["lease_owner"] is not None
                    or row["lease_expires_at"] is not None
                ):
                    self._conn.commit()
                    return None
                if int(row["version"]) != int(expected_version):
                    raise AgentRunVersionConflict(run_id)
                continuation = project_continuation(
                    json_object(row["continuation"])
                )
                if continuation.get("pending_interaction") != {
                    "kind": "approval",
                    "id": interaction,
                }:
                    self._conn.commit()
                    return None
                phase = continuation.get("cursor", {}).get("phase")
                if state == "waiting_approval" and phase != "waiting_approval":
                    self._conn.commit()
                    return None
                if state == "interrupted" and phase != "approval_ready":
                    self._conn.commit()
                    return None
                inbox = self._conn.execute(
                    "SELECT state, decision, run_id FROM inbox WHERE id = ?",
                    (interaction,),
                ).fetchone()
                if (
                    inbox is None
                    or str(inbox["state"]) != "resolved"
                    or inbox["decision"] not in {"allow", "deny"}
                    or inbox["run_id"] != run_id
                ):
                    self._conn.commit()
                    return None
                decision = str(inbox["decision"])
                if state == "waiting_approval":
                    continuation = approval_ready_transition(
                        continuation, interaction, decision
                    )
                elif continuation.get("resolved_approval") != {
                    "id": interaction,
                    "decision": decision,
                }:
                    self._conn.commit()
                    return None
                cursor = self._conn.execute(
                    """
                    UPDATE agent_runs SET
                        current_state = 'running', lease_owner = ?,
                        lease_expires_at = ?, continuation = ?,
                        version = version + 1,
                        updated_at = ?
                    WHERE run_id = ? AND version = ?
                      AND current_state = ?
                      AND lease_owner IS NULL AND lease_expires_at IS NULL
                    """,
                    (
                        owner,
                        expires_at,
                        _json(continuation),
                        stamp,
                        run_id,
                        int(expected_version),
                        state,
                    ),
                )
                if cursor.rowcount != 1:
                    current = self._conn.execute(
                        "SELECT * FROM agent_runs WHERE run_id = ?", (run_id,)
                    ).fetchone()
                    if current is None:
                        raise KeyError(run_id)
                    if int(current["version"]) != int(expected_version):
                        raise AgentRunVersionConflict(run_id)
                    self._conn.commit()
                    return None
                row = self._conn.execute(
                    "SELECT * FROM agent_runs WHERE run_id = ?", (run_id,)
                ).fetchone()
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return ResolvedApprovalLease(
            lease=_lease_from_row(row), decision=decision
        )

    def acquire_closed_waiting_lease(
        self,
        run_id: str,
        owner_id: str,
        expected_version: int,
        interaction_id: str,
        closed_state: str,
        lease_seconds: int | float,
        now: datetime | None = None,
    ) -> AgentRunLease | None:
        """Reacquire one exact live-closed approval only to terminalize."""
        owner = _lease_owner(owner_id)
        interaction = _bounded_id(interaction_id, "interaction_id")
        if closed_state not in {"cancelled", "expired"}:
            raise ValueError("closed approval state is invalid")
        seconds = _lease_seconds(lease_seconds)
        stamp = _timestamp(now)
        expires_at = _timestamp(_as_datetime(stamp) + timedelta(seconds=seconds))
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    "SELECT * FROM agent_runs WHERE run_id = ?", (run_id,)
                ).fetchone()
                if row is None:
                    raise KeyError(run_id)
                continuation = project_continuation(
                    json_object(row["continuation"])
                )
                inbox = self._conn.execute(
                    "SELECT state, decision, run_id FROM inbox WHERE id = ?",
                    (interaction,),
                ).fetchone()
                if (
                    str(row["current_state"]) != "waiting_approval"
                    or row["lease_owner"] is not None
                    or row["lease_expires_at"] is not None
                    or int(row["version"]) != int(expected_version)
                    or continuation.get("cursor", {}).get("phase")
                    != "waiting_approval"
                    or continuation.get("pending_interaction")
                    != {"kind": "approval", "id": interaction}
                    or inbox is None
                    or str(inbox["state"]) != closed_state
                    or inbox["decision"] is not None
                    or inbox["run_id"] != run_id
                ):
                    self._conn.commit()
                    return None
                cursor = self._conn.execute(
                    """
                    UPDATE agent_runs SET
                        current_state = 'running', lease_owner = ?,
                        lease_expires_at = ?, version = version + 1,
                        updated_at = ?
                    WHERE run_id = ? AND version = ?
                      AND current_state = 'waiting_approval'
                      AND lease_owner IS NULL AND lease_expires_at IS NULL
                    """,
                    (
                        owner,
                        expires_at,
                        stamp,
                        run_id,
                        int(expected_version),
                    ),
                )
                if cursor.rowcount != 1:
                    raise AgentRunVersionConflict(run_id)
                row = self._conn.execute(
                    "SELECT * FROM agent_runs WHERE run_id = ?", (run_id,)
                ).fetchone()
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return _lease_from_row(row)

    def acquire_resumable_lease(
        self,
        run_id: str,
        owner_id: str,
        expected_version: int,
        lease_seconds: int | float,
        now: datetime | None = None,
    ) -> AgentRunLease | None:
        """Explicitly claim one safely classified interrupted continuation."""
        owner = _lease_owner(owner_id)
        seconds = _lease_seconds(lease_seconds)
        stamp = _timestamp(now)
        expires_at = _timestamp(_as_datetime(stamp) + timedelta(seconds=seconds))
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    "SELECT * FROM agent_runs WHERE run_id = ?", (run_id,)
                ).fetchone()
                if row is None:
                    raise KeyError(run_id)
                if (
                    str(row["current_state"]) != "interrupted"
                    or row["lease_owner"] is not None
                    or row["lease_expires_at"] is not None
                ):
                    self._conn.commit()
                    return None
                if int(row["version"]) != int(expected_version):
                    raise AgentRunVersionConflict(run_id)
                phase = (
                    project_continuation(json_object(row["continuation"]))
                    .get("cursor", {})
                    .get("phase")
                )
                if phase not in {"model_ready", "tools_ready"}:
                    self._conn.commit()
                    return None
                cursor = self._conn.execute(
                    """
                    UPDATE agent_runs SET
                        current_state = 'running', lease_owner = ?,
                        lease_expires_at = ?, version = version + 1,
                        updated_at = ?
                    WHERE run_id = ? AND version = ?
                      AND current_state = 'interrupted'
                      AND lease_owner IS NULL AND lease_expires_at IS NULL
                    """,
                    (owner, expires_at, stamp, run_id, int(expected_version)),
                )
                if cursor.rowcount != 1:
                    current = self._conn.execute(
                        "SELECT * FROM agent_runs WHERE run_id = ?", (run_id,)
                    ).fetchone()
                    if current is None:
                        raise KeyError(run_id)
                    if int(current["version"]) != int(expected_version):
                        raise AgentRunVersionConflict(run_id)
                    self._conn.commit()
                    return None
                row = self._conn.execute(
                    "SELECT * FROM agent_runs WHERE run_id = ?", (run_id,)
                ).fetchone()
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return _lease_from_row(row)


def _lease_seconds(value: int | float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("lease_seconds must be a positive number")
    seconds = float(value)
    if seconds <= 0 or seconds > MAX_LEASE_SECONDS:
        raise ValueError(
            f"lease_seconds must be between 0 and {MAX_LEASE_SECONDS}"
        )
    return seconds


def _lease_owner(value: str) -> str:
    owner = str(value).strip()
    if not owner or len(owner) > _MAX_SAFE_ID or any(ord(char) < 32 for char in owner):
        raise ValueError("invalid lease owner")
    if redact_sensitive_assignments(owner) != owner:
        raise ValueError("lease owner must not contain credential-shaped data")
    return owner


def _bounded_id(value: str, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > _MAX_SAFE_ID
        or any(ord(char) < 32 for char in value)
        or redact_sensitive_assignments(value) != value
    ):
        raise ValueError(f"invalid {field}")
    return value


def _valid_session_id(value: str) -> bool:
    return bool(value) and _SESSION_ID.fullmatch(value) is not None and ".." not in value


def _timestamp(value: datetime | None) -> str:
    moment = value or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).isoformat()


def _as_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(UTC)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _lease_from_row(row: sqlite3.Row | None) -> AgentRunLease:
    if row is None or row["lease_owner"] is None or row["lease_expires_at"] is None:
        raise AgentRunLeaseLost("lease is not active")
    return AgentRunLease(
        run_id=str(row["run_id"]),
        owner_id=str(row["lease_owner"]),
        version=int(row["version"]),
        expires_at=str(row["lease_expires_at"]),
    )


def _run_row(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    for private in (
        "original_goal_fingerprint",
        "original_goal_fingerprint_source",
        "lease_owner",
        "lease_expires_at",
    ):
        item.pop(private, None)
    item["version"] = int(item.get("version") or 0)
    item["checkpoint_sequence"] = int(item.get("checkpoint_sequence") or 0)
    item["skills_loaded"] = json_list(item.get("skills_loaded"))
    item["source_refs"] = json_list(item.get("source_refs"))
    item["artifact_refs"] = json_list(item.get("artifact_refs"))
    item["usage"] = json_object(item.get("usage"))
    item["terminal_result"] = nullable_json_object(item.get("terminal_result"))
    raw_continuation = json_object(item.get("continuation"))
    item["continuation"] = (
        project_continuation(raw_continuation) if raw_continuation else {}
    )
    return item


def _checkpoint_row(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["sequence"] = int(item["sequence"])
    item["payload"] = json_object(item.get("payload"))
    return item


def _inbox_matches_approval(
    row: sqlite3.Row, approval: ApprovalParkRequest
) -> bool:
    try:
        arguments = json.loads(str(row["arguments"] or "{}"))
        resource = (
            json.loads(str(row["resource"]))
            if row["resource"] is not None
            else None
        )
    except (json.JSONDecodeError, TypeError):
        return False
    return (
        str(row["kind"]) == approval.kind
        and str(row["name"]) == approval.name
        and arguments == approval.arguments
        and str(row["state"]) == "pending"
        and row["decision"] is None
        and row["actor"] is None
        and row["resolved_at"] is None
        and str(row["requested_at"]) == approval.requested_at
        and str(row["scope"] or "once") == approval.scope
        and str(row["execution_status"] or "pending") == "pending"
        and row["execution_claimant"] is None
        and row["execution_error"] is None
        and row["execution_result"] is None
        and str(row["expires_at"]) == approval.expires_at
        and row["reason"] == approval.reason
        and row["session_id"] == approval.session_id
        and row["run_id"] == approval.run_id
        and row["message_id"] == approval.message_id
        and row["part_id"] == approval.part_id
        and row["recovery_command_id"] is None
        and row["original_call_id"] is None
        and resource == approval.resource
    )


def _owned_approved_inbox(
    row: sqlite3.Row | None, run_id: str, claimant: str
) -> bool:
    return bool(
        row is not None
        and row["run_id"] == run_id
        and str(row["state"]) == "resolved"
        and str(row["decision"]) == "allow"
        and str(row["execution_status"] or "pending") == "executing"
        and row["execution_claimant"] == claimant
    )


def _validate_approved_run_boundary(
    row: sqlite3.Row,
    approval_id: str,
    continuation: dict[str, Any],
    *,
    interrupted: bool,
) -> None:
    current = project_continuation(json_object(row["continuation"]))
    incoming = project_continuation(continuation)
    cursor = current.get("cursor", {})
    pending = current.get("pending_tool")
    next_cursor = incoming.get("cursor", {})
    exact_pending = (
        isinstance(pending, dict)
        and pending.get("call_id") == approval_id
        and pending.get("retry_class") == "consequential"
        and pending.get("status") == "in_flight"
        and pending.get("budget_reserved") is True
    )
    if cursor.get("phase") != "tool_in_flight" or not exact_pending:
        raise ValueError("approved completion does not match the in-flight tool")
    if interrupted:
        incoming_pending = incoming.get("pending_tool")
        legal = (
            next_cursor.get("phase") == "review_required"
            and isinstance(incoming_pending, dict)
            and incoming_pending.get("call_id") == approval_id
            and incoming_pending.get("status") == "outcome_unknown"
            and incoming_pending.get("budget_reserved") is True
        )
    else:
        legal = (
            next_cursor.get("phase") == "tools_ready"
            and "pending_tool" not in incoming
            and int(next_cursor.get("next_tool_index", -1))
            == int(cursor.get("next_tool_index", 0)) + 1
            and any(
                receipt.get("call_id") == approval_id
                and receipt.get("outcome") == "executed"
                for receipt in incoming.get("completed_tool_receipts", [])
            )
        )
    if not legal:
        raise ValueError("approved completion continuation is inconsistent")


def _validate_checkpoint(kind: str, state: str | None) -> None:
    if kind not in AGENT_RUN_CHECKPOINT_KINDS:
        raise ValueError(f"invalid Agent Run checkpoint kind: {kind}")
    if state is not None and state not in AGENT_RUN_STATES:
        raise ValueError(f"invalid Agent Run state: {state}")
    if kind == "terminal" and state not in TERMINAL_AGENT_RUN_STATES:
        raise ValueError("terminal checkpoint requires a terminal state")
    if state in TERMINAL_AGENT_RUN_STATES and kind != "terminal":
        raise ValueError("terminal Agent Run state requires a terminal checkpoint")


def _checkpoint_projection(
    row: sqlite3.Row,
    *,
    state: str | None,
    skills_loaded: list[str] | None,
    source_refs: list[dict[str, Any]] | None,
    artifact_refs: list[dict[str, Any]] | None,
    usage_delta: dict[str, int | float] | None,
    terminal_result: dict[str, Any] | None,
    kind: str,
    now: str,
) -> dict[str, Any]:
    current_state = str(row["current_state"])
    if current_state in TERMINAL_AGENT_RUN_STATES:
        raise ValueError("cannot append to a terminal Agent Run")
    next_state = state or current_state
    existing_skills = sanitize_agent_run_value(json_list(row["skills_loaded"]))
    incoming_skills = sanitize_agent_run_value(skills_loaded or [])
    merged_skills = merge_unique_strings(existing_skills, incoming_skills)
    merged_sources = merge_unique_json(
        project_source_refs(json_list(row["source_refs"])),
        project_source_refs(source_refs or []),
    )
    merged_artifacts = merge_unique_json(
        project_artifact_refs(json_list(row["artifact_refs"])),
        project_artifact_refs(artifact_refs or []),
    )
    usage = add_usage(json_object(row["usage"]), usage_delta or {})
    next_terminal = (
        _json(project_terminal_result(terminal_result))
        if kind == "terminal" and terminal_result is not None
        else row["terminal_result"]
    )
    return {
        "state": next_state,
        "skills": _json(merged_skills),
        "sources": _json(merged_sources),
        "artifacts": _json(merged_artifacts),
        "usage": _json(usage),
        "terminal_result": next_terminal,
        "finished_at": now
        if next_state in TERMINAL_AGENT_RUN_STATES
        else row["finished_at"],
    }


class AgentRunRepository(_AgentRunRepositoryBase):
    def start_and_acquire_lease(
        self,
        *,
        run_id: str,
        session_id: str,
        trigger: str,
        original_goal: str,
        provider_model_id: str | None,
        owner_id: str,
        lease_seconds: int | float,
        continuation: dict[str, Any],
        parent_run_id: str | None = None,
        now: datetime | None = None,
    ) -> StartedAgentRunLease | None:
        """Create and lease a run atomically, or idempotently claim an exact run."""
        if not run_id or not _valid_session_id(session_id):
            raise ValueError("invalid Agent Run identity")
        if not trigger.strip():
            raise ValueError("Agent Run trigger is required")
        owner = _lease_owner(owner_id)
        seconds = _lease_seconds(lease_seconds)
        stamp = _timestamp(now)
        expires_at = _timestamp(_as_datetime(stamp) + timedelta(seconds=seconds))
        safe_goal = str(sanitize_agent_run_value(original_goal))
        fingerprint = original_goal_fingerprint(original_goal)
        initial = project_continuation(continuation)
        if not initial.get("identity") or initial.get("cursor", {}).get("phase") != "model_ready":
            raise ValueError("initial Agent Run continuation is invalid")
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    "SELECT * FROM agent_runs WHERE run_id = ?", (run_id,)
                ).fetchone()
                if row is None:
                    self._conn.execute(
                        """
                        INSERT INTO agent_runs (
                            run_id, session_id, parent_run_id, trigger,
                            original_goal, original_goal_fingerprint,
                            original_goal_fingerprint_source, current_state,
                            provider_model_id, checkpoint_sequence, skills_loaded,
                            source_refs, artifact_refs, usage, terminal_result,
                            created_at, started_at, updated_at, finished_at,
                            version, lease_owner, lease_expires_at, continuation
                        ) VALUES (
                            ?, ?, ?, ?, ?, ?, 'raw', 'running', ?, 1, '[]', '[]',
                            '[]', '{}', NULL, ?, ?, ?, NULL, 1, ?, ?, ?
                        )
                        """,
                        (
                            run_id,
                            session_id,
                            parent_run_id,
                            trigger,
                            safe_goal,
                            fingerprint,
                            provider_model_id,
                            stamp,
                            stamp,
                            stamp,
                            owner,
                            expires_at,
                            _json(initial),
                        ),
                    )
                    self._conn.execute(
                        """
                        INSERT INTO agent_run_checkpoints
                            (run_id, sequence, kind, payload, created_at)
                        VALUES (?, 1, 'run_started', ?, ?)
                        """,
                        (
                            run_id,
                            _json(
                                bounded_checkpoint_payload(
                                    {
                                        "trigger": trigger,
                                        "provider_model_id": provider_model_id,
                                        "has_parent": parent_run_id is not None,
                                    }
                                )
                            ),
                            stamp,
                        ),
                    )
                else:
                    immutable = {
                        "session_id": session_id,
                        "parent_run_id": parent_run_id,
                        "trigger": trigger,
                        "provider_model_id": provider_model_id,
                    }
                    conflicts = [
                        field
                        for field, value in immutable.items()
                        if row[field] != value
                    ]
                    if row["original_goal_fingerprint"] != fingerprint:
                        conflicts.append("original_goal")
                    if conflicts:
                        raise ValueError(
                            "conflicting Agent Run metadata: " + ", ".join(conflicts)
                        )
                    existing = project_continuation(json_object(row["continuation"]))
                    phase = existing.get("cursor", {}).get("phase")
                    if str(row["current_state"]) != "running" or phase not in {
                        None,
                        "model_ready",
                        "tools_ready",
                    }:
                        raise AgentRunStartConflict(
                            f"Agent Run {run_id} requires explicit review or resume from {phase}"
                        )
                    if existing.get("identity") and existing.get("identity") != initial.get(
                        "identity"
                    ):
                        raise ValueError("Agent Run continuation identity cannot change")
                    active = (
                        row["lease_owner"] is not None
                        and row["lease_expires_at"] is not None
                        and str(row["lease_expires_at"]) > stamp
                    )
                    if active and str(row["lease_owner"]) != owner:
                        self._conn.commit()
                        return None
                    if not active:
                        cursor = self._conn.execute(
                            """
                            UPDATE agent_runs SET
                                continuation = ?, lease_owner = ?,
                                lease_expires_at = ?, version = version + 1,
                                updated_at = ?
                            WHERE run_id = ? AND version = ?
                              AND current_state = 'running'
                              AND (
                                  lease_owner IS NULL OR lease_expires_at IS NULL
                                  OR lease_expires_at <= ?
                              )
                            """,
                            (
                                _json(existing if existing.get("identity") else initial),
                                owner,
                                expires_at,
                                stamp,
                                run_id,
                                int(row["version"]),
                                stamp,
                            ),
                        )
                        if cursor.rowcount != 1:
                            raise AgentRunVersionConflict(run_id)
                row = self._conn.execute(
                    "SELECT * FROM agent_runs WHERE run_id = ?", (run_id,)
                ).fetchone()
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return StartedAgentRunLease(run=_run_row(row), lease=_lease_from_row(row))

    def start_agent_run(
        self,
        *,
        run_id: str,
        session_id: str,
        trigger: str,
        original_goal: str,
        provider_model_id: str | None = None,
        parent_run_id: str | None = None,
    ) -> dict[str, Any]:
        if not run_id or not _valid_session_id(session_id):
            raise ValueError("invalid Agent Run identity")
        if not trigger.strip():
            raise ValueError("Agent Run trigger is required")
        safe_goal = str(sanitize_agent_run_value(original_goal))
        fingerprint = original_goal_fingerprint(original_goal)
        now = _timestamp(None)
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                existing = self._conn.execute(
                    "SELECT * FROM agent_runs WHERE run_id = ?", (run_id,)
                ).fetchone()
                if existing is not None:
                    if str(existing["session_id"]) != session_id:
                        raise ValueError(
                            "Agent Run identity belongs to another session"
                        )
                    immutable = {
                        "parent_run_id": parent_run_id,
                        "trigger": trigger,
                        "provider_model_id": provider_model_id,
                    }
                    conflicts = [
                        field
                        for field, value in immutable.items()
                        if existing[field] != value
                    ]
                    if existing["original_goal_fingerprint"] != fingerprint:
                        if (
                            existing["original_goal_fingerprint_source"]
                            == "legacy_sanitized"
                        ):
                            raise ValueError(
                                "legacy Agent Run goal identity is irrecoverable; "
                                "start a new run"
                            )
                        conflicts.append("original_goal")
                    if conflicts:
                        raise ValueError(
                            "conflicting Agent Run metadata: " + ", ".join(conflicts)
                        )
                    self._conn.commit()
                    return _run_row(existing)
                self._conn.execute(
                    """
                    INSERT INTO agent_runs (
                        run_id, session_id, parent_run_id, trigger,
                        original_goal, original_goal_fingerprint,
                        original_goal_fingerprint_source, current_state,
                        provider_model_id, checkpoint_sequence, skills_loaded,
                        source_refs, artifact_refs, usage, terminal_result,
                        created_at, started_at, updated_at, finished_at
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, 'raw', 'running', ?, 1, '[]', '[]',
                        '[]', '{}', NULL, ?, ?, ?, NULL
                    )
                    """,
                    (
                        run_id,
                        session_id,
                        parent_run_id,
                        trigger,
                        safe_goal,
                        fingerprint,
                        provider_model_id,
                        now,
                        now,
                        now,
                    ),
                )
                self._conn.execute(
                    """
                    INSERT INTO agent_run_checkpoints
                        (run_id, sequence, kind, payload, created_at)
                    VALUES (?, 1, 'run_started', ?, ?)
                    """,
                    (
                        run_id,
                        _json(
                            bounded_checkpoint_payload(
                                {
                                    "trigger": trigger,
                                    "provider_model_id": provider_model_id,
                                    "has_parent": parent_run_id is not None,
                                }
                            )
                        ),
                        now,
                    ),
                )
                row = self._conn.execute(
                    "SELECT * FROM agent_runs WHERE run_id = ?", (run_id,)
                ).fetchone()
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        if row is None:
            raise RuntimeError("Agent Run insert did not persist")
        return _run_row(row)

    def checkpoint_agent_run(
        self,
        run_id: str,
        *,
        kind: str,
        payload: dict[str, Any] | None = None,
        state: str | None = None,
        skills_loaded: list[str] | None = None,
        source_refs: list[dict[str, Any]] | None = None,
        artifact_refs: list[dict[str, Any]] | None = None,
        usage_delta: dict[str, int | float] | None = None,
        terminal_result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Compatibility path for Slice A callers that do not yet use leases."""
        _validate_checkpoint(kind, state)
        now = _timestamp(None)
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    "SELECT * FROM agent_runs WHERE run_id = ?", (run_id,)
                ).fetchone()
                if row is None:
                    raise KeyError(run_id)
                current_state = str(row["current_state"])
                if kind == "terminal" and current_state in TERMINAL_AGENT_RUN_STATES:
                    checkpoint = self._conn.execute(
                        """
                        SELECT * FROM agent_run_checkpoints
                        WHERE run_id = ? AND kind = 'terminal'
                        ORDER BY sequence DESC LIMIT 1
                        """,
                        (run_id,),
                    ).fetchone()
                    self._conn.commit()
                    if checkpoint is None:
                        raise RuntimeError(
                            "terminal Agent Run is missing its checkpoint"
                        )
                    return _checkpoint_row(checkpoint)
                if current_state in TERMINAL_AGENT_RUN_STATES:
                    raise ValueError("cannot append to a terminal Agent Run")
                if row["lease_owner"] is not None:
                    raise AgentRunLeaseLost(
                        "leased Agent Run requires checkpoint_leased"
                    )
                values = _checkpoint_projection(
                    row,
                    state=state,
                    skills_loaded=skills_loaded,
                    source_refs=source_refs,
                    artifact_refs=artifact_refs,
                    usage_delta=usage_delta,
                    terminal_result=terminal_result,
                    kind=kind,
                    now=now,
                )
                sequence = int(row["checkpoint_sequence"]) + 1
                cursor = self._conn.execute(
                    """
                    UPDATE agent_runs SET
                        current_state = ?, checkpoint_sequence = ?,
                        skills_loaded = ?, source_refs = ?, artifact_refs = ?,
                        usage = ?, terminal_result = ?, version = version + 1,
                        updated_at = ?, finished_at = ?
                    WHERE run_id = ? AND version = ? AND lease_owner IS NULL
                      AND current_state NOT IN ('complete', 'partial', 'stopped', 'failed')
                    """,
                    (
                        values["state"],
                        sequence,
                        values["skills"],
                        values["sources"],
                        values["artifacts"],
                        values["usage"],
                        values["terminal_result"],
                        now,
                        values["finished_at"],
                        run_id,
                        int(row["version"]),
                    ),
                )
                if cursor.rowcount != 1:
                    raise AgentRunVersionConflict(run_id)
                self._conn.execute(
                    """
                    INSERT INTO agent_run_checkpoints
                        (run_id, sequence, kind, payload, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        sequence,
                        kind,
                        _json(bounded_checkpoint_payload(payload)),
                        now,
                    ),
                )
                checkpoint = self._conn.execute(
                    """
                    SELECT * FROM agent_run_checkpoints
                    WHERE run_id = ? AND sequence = ?
                    """,
                    (run_id, sequence),
                ).fetchone()
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        if checkpoint is None:
            raise RuntimeError("Agent Run checkpoint insert did not persist")
        return _checkpoint_row(checkpoint)

    def get_agent_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM agent_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        return None if row is None else _run_row(row)

    def list_agent_runs(
        self, *, session_id: str | None = None
    ) -> list[dict[str, Any]]:
        with self._lock:
            if session_id is None:
                rows = self._conn.execute(
                    "SELECT * FROM agent_runs ORDER BY created_at, rowid"
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """
                    SELECT * FROM agent_runs
                    WHERE session_id = ? ORDER BY created_at, rowid
                    """,
                    (session_id,),
                ).fetchall()
        return [_run_row(row) for row in rows]

    def list_agent_run_checkpoints(self, run_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM agent_run_checkpoints
                WHERE run_id = ? ORDER BY sequence
                """,
                (run_id,),
            ).fetchall()
        return [_checkpoint_row(row) for row in rows]

    def renew_lease(
        self,
        lease: AgentRunLease,
        lease_seconds: int | float,
        now: datetime | None = None,
    ) -> AgentRunLease:
        seconds = _lease_seconds(lease_seconds)
        stamp = _timestamp(now)
        expires_at = _timestamp(_as_datetime(stamp) + timedelta(seconds=seconds))
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = self._conn.execute(
                    """
                    UPDATE agent_runs SET
                        lease_expires_at = ?, version = version + 1,
                        updated_at = ?
                    WHERE run_id = ? AND version = ? AND lease_owner = ?
                      AND lease_expires_at > ?
                      AND current_state NOT IN ('complete', 'partial', 'stopped', 'failed')
                    """,
                    (
                        expires_at,
                        stamp,
                        lease.run_id,
                        lease.version,
                        lease.owner_id,
                        stamp,
                    ),
                )
                if cursor.rowcount != 1:
                    self._raise_fence_failure(lease, stamp)
                row = self._conn.execute(
                    "SELECT * FROM agent_runs WHERE run_id = ?", (lease.run_id,)
                ).fetchone()
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return _lease_from_row(row)

    def update_continuation(
        self,
        lease: AgentRunLease,
        continuation: dict[str, Any],
        now: datetime | None = None,
    ) -> AgentRunLease:
        stamp = _timestamp(now)
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._fenced_row(lease, stamp)
                next_continuation = merge_continuation(
                    json_object(row["continuation"]), continuation
                )
                cursor = self._conn.execute(
                    """
                    UPDATE agent_runs SET
                        continuation = ?, version = version + 1, updated_at = ?
                    WHERE run_id = ? AND version = ? AND lease_owner = ?
                      AND lease_expires_at > ?
                      AND current_state NOT IN ('complete', 'partial', 'stopped', 'failed')
                    """,
                    (
                        _json(next_continuation),
                        stamp,
                        lease.run_id,
                        lease.version,
                        lease.owner_id,
                        stamp,
                    ),
                )
                if cursor.rowcount != 1:
                    self._raise_fence_failure(lease, stamp)
                row = self._conn.execute(
                    "SELECT * FROM agent_runs WHERE run_id = ?", (lease.run_id,)
                ).fetchone()
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return _lease_from_row(row)

    def checkpoint_leased(
        self,
        lease: AgentRunLease,
        kind: str,
        continuation: dict[str, Any],
        payload: dict[str, Any] | None = None,
        state: str | None = None,
        skills_loaded: list[str] | None = None,
        source_refs: list[dict[str, Any]] | None = None,
        artifact_refs: list[dict[str, Any]] | None = None,
        usage_delta: dict[str, int | float] | None = None,
        terminal_result: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> tuple[AgentRunLease | None, dict[str, Any]]:
        _validate_checkpoint(kind, state)
        stamp = _timestamp(now)
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._fenced_row(lease, stamp)
                values = _checkpoint_projection(
                    row,
                    state=state,
                    skills_loaded=skills_loaded,
                    source_refs=source_refs,
                    artifact_refs=artifact_refs,
                    usage_delta=usage_delta,
                    terminal_result=terminal_result,
                    kind=kind,
                    now=stamp,
                )
                sequence = int(row["checkpoint_sequence"]) + 1
                next_continuation = merge_continuation(
                    json_object(row["continuation"]), continuation
                )
                relinquish = (
                    values["state"] in TERMINAL_AGENT_RUN_STATES
                    or values["state"] == "interrupted"
                    or values["state"]
                    in {"waiting_approval", "waiting_question"}
                )
                cursor = self._conn.execute(
                    """
                    UPDATE agent_runs SET
                        current_state = ?, checkpoint_sequence = ?,
                        skills_loaded = ?, source_refs = ?, artifact_refs = ?,
                        usage = ?, terminal_result = ?, continuation = ?,
                        lease_owner = ?, lease_expires_at = ?,
                        version = version + 1, updated_at = ?, finished_at = ?
                    WHERE run_id = ? AND version = ? AND lease_owner = ?
                      AND lease_expires_at > ?
                      AND current_state NOT IN ('complete', 'partial', 'stopped', 'failed')
                    """,
                    (
                        values["state"],
                        sequence,
                        values["skills"],
                        values["sources"],
                        values["artifacts"],
                        values["usage"],
                        values["terminal_result"],
                        _json(next_continuation),
                        None if relinquish else lease.owner_id,
                        None if relinquish else lease.expires_at,
                        stamp,
                        values["finished_at"],
                        lease.run_id,
                        lease.version,
                        lease.owner_id,
                        stamp,
                    ),
                )
                if cursor.rowcount != 1:
                    self._raise_fence_failure(lease, stamp)
                self._conn.execute(
                    """
                    INSERT INTO agent_run_checkpoints
                        (run_id, sequence, kind, payload, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        lease.run_id,
                        sequence,
                        kind,
                        _json(bounded_checkpoint_payload(payload)),
                        stamp,
                    ),
                )
                checkpoint_row = self._conn.execute(
                    """
                    SELECT * FROM agent_run_checkpoints
                    WHERE run_id = ? AND sequence = ?
                    """,
                    (lease.run_id, sequence),
                ).fetchone()
                run_row = self._conn.execute(
                    "SELECT * FROM agent_runs WHERE run_id = ?", (lease.run_id,)
                ).fetchone()
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        if checkpoint_row is None:
            raise RuntimeError("Agent Run checkpoint insert did not persist")
        next_lease = None if relinquish else _lease_from_row(run_row)
        return next_lease, _checkpoint_row(checkpoint_row)

    def park_approval_and_wait(
        self,
        lease: AgentRunLease,
        continuation: dict[str, Any],
        approval: ApprovalParkRequest,
        *,
        payload: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> tuple[None, dict[str, Any], dict[str, Any]]:
        """Atomically park one approval and relinquish its Agent Run lease."""
        _validate_checkpoint("waiting_approval", "waiting_approval")
        stamp = _timestamp(now)
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._fenced_row(lease, stamp)
                persisted = project_continuation(
                    json_object(row["continuation"])
                )
                identity = persisted.get("identity", {})
                waiting = project_continuation(continuation)
                waiting_tool = waiting.get("pending_tool")
                if (
                    approval.run_id != lease.run_id
                    or approval.session_id != str(row["session_id"])
                    or approval.message_id != identity.get("message_id")
                    or approval.part_id != identity.get("part_id")
                    or waiting.get("cursor", {}).get("phase")
                    != "waiting_approval"
                    or waiting.get("pending_interaction")
                    != {"kind": "approval", "id": approval.item_id}
                    or not isinstance(waiting_tool, dict)
                    or waiting_tool.get("call_id") != approval.call_id
                    or waiting_tool.get("name") != approval.name
                    or waiting_tool.get("status") != "not_started"
                    or waiting_tool.get("budget_reserved") is not False
                ):
                    raise ValueError("approval identity does not match Agent Run")
                inbox_row = self._conn.execute(
                    "SELECT * FROM inbox WHERE id = ?", (approval.item_id,)
                ).fetchone()
                if inbox_row is None:
                    self._conn.execute(
                        """
                        INSERT INTO inbox
                            (id, kind, name, arguments, state, requested_at,
                             scope, execution_status, expires_at, reason,
                             session_id, run_id, message_id, part_id,
                             recovery_command_id, original_call_id, resource)
                        VALUES (?, ?, ?, ?, 'pending', ?, ?, 'pending', ?, ?,
                                ?, ?, ?, ?, NULL, NULL, ?)
                        """,
                        (
                            approval.item_id,
                            approval.kind,
                            approval.name,
                            canonical_json(approval.arguments),
                            approval.requested_at,
                            approval.scope,
                            approval.expires_at,
                            approval.reason,
                            approval.session_id,
                            approval.run_id,
                            approval.message_id,
                            approval.part_id,
                            (
                                canonical_json(approval.resource)
                                if approval.resource is not None
                                else None
                            ),
                        ),
                    )
                elif not _inbox_matches_approval(inbox_row, approval):
                    raise ValueError("approval inbox identity conflicts with existing row")
                sequence = int(row["checkpoint_sequence"]) + 1
                next_continuation = merge_continuation(
                    json_object(row["continuation"]), continuation
                )
                cursor = self._conn.execute(
                    """
                    UPDATE agent_runs SET
                        current_state = 'waiting_approval',
                        checkpoint_sequence = ?, continuation = ?,
                        lease_owner = NULL, lease_expires_at = NULL,
                        version = version + 1, updated_at = ?
                    WHERE run_id = ? AND version = ? AND lease_owner = ?
                      AND lease_expires_at > ? AND current_state = 'running'
                    """,
                    (
                        sequence,
                        _json(next_continuation),
                        stamp,
                        lease.run_id,
                        lease.version,
                        lease.owner_id,
                        stamp,
                    ),
                )
                if cursor.rowcount != 1:
                    self._raise_fence_failure(lease, stamp)
                self._conn.execute(
                    """
                    INSERT INTO agent_run_checkpoints
                        (run_id, sequence, kind, payload, created_at)
                    VALUES (?, ?, 'waiting_approval', ?, ?)
                    """,
                    (
                        lease.run_id,
                        sequence,
                        _json(bounded_checkpoint_payload(payload)),
                        stamp,
                    ),
                )
                checkpoint_row = self._conn.execute(
                    """
                    SELECT * FROM agent_run_checkpoints
                    WHERE run_id = ? AND sequence = ?
                    """,
                    (lease.run_id, sequence),
                ).fetchone()
                inbox_row = self._conn.execute(
                    "SELECT * FROM inbox WHERE id = ?", (approval.item_id,)
                ).fetchone()
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        if checkpoint_row is None or inbox_row is None:
            raise RuntimeError("atomic approval boundary did not persist")
        return None, _checkpoint_row(checkpoint_row), project_inbox_row(inbox_row)

    def complete_approved_tool(
        self,
        lease: AgentRunLease,
        continuation: dict[str, Any],
        *,
        approval_id: str,
        claimant: str,
        ok: bool,
        result: dict[str, Any],
        payload: dict[str, Any] | None = None,
        skills_loaded: list[str] | None = None,
        source_refs: list[dict[str, Any]] | None = None,
        artifact_refs: list[dict[str, Any]] | None = None,
        now: datetime | None = None,
    ) -> tuple[AgentRunLease, dict[str, Any], dict[str, Any]]:
        """Commit one approved tool result to inbox and Agent Run together."""
        _validate_checkpoint("tool_completed", None)
        stamp = _timestamp(now)
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._fenced_row(lease, stamp)
                _validate_approved_run_boundary(
                    row, approval_id, continuation, interrupted=False
                )
                inbox_row = self._conn.execute(
                    "SELECT * FROM inbox WHERE id = ?", (approval_id,)
                ).fetchone()
                if not _owned_approved_inbox(
                    inbox_row, lease.run_id, claimant
                ):
                    raise AgentRunLeaseLost(
                        "approved inbox execution is not owned by this claimant"
                    )
                execution_error = (
                    str(result.get("error"))
                    if not ok and result.get("error") is not None
                    else None
                )
                inbox_cursor = self._conn.execute(
                    """
                    UPDATE inbox SET
                        execution_status = ?, execution_error = ?,
                        execution_result = ?
                    WHERE id = ? AND state = 'resolved'
                      AND decision = 'allow' AND execution_status = 'executing'
                      AND execution_claimant = ? AND run_id = ?
                    """,
                    (
                        "succeeded" if ok else "failed",
                        execution_error,
                        json.dumps(result, ensure_ascii=False),
                        approval_id,
                        claimant,
                        lease.run_id,
                    ),
                )
                if inbox_cursor.rowcount != 1:
                    raise AgentRunLeaseLost(
                        "approved inbox execution completion was fenced"
                    )
                values = _checkpoint_projection(
                    row,
                    state=None,
                    skills_loaded=skills_loaded,
                    source_refs=source_refs,
                    artifact_refs=artifact_refs,
                    usage_delta={"tool_calls": 1},
                    terminal_result=None,
                    kind="tool_completed",
                    now=stamp,
                )
                sequence = int(row["checkpoint_sequence"]) + 1
                next_continuation = merge_continuation(
                    json_object(row["continuation"]), continuation
                )
                run_cursor = self._conn.execute(
                    """
                    UPDATE agent_runs SET
                        current_state = ?, checkpoint_sequence = ?,
                        skills_loaded = ?, source_refs = ?, artifact_refs = ?,
                        usage = ?, continuation = ?, version = version + 1,
                        updated_at = ?
                    WHERE run_id = ? AND version = ? AND lease_owner = ?
                      AND lease_expires_at > ? AND current_state = 'running'
                    """,
                    (
                        values["state"],
                        sequence,
                        values["skills"],
                        values["sources"],
                        values["artifacts"],
                        values["usage"],
                        _json(next_continuation),
                        stamp,
                        lease.run_id,
                        lease.version,
                        lease.owner_id,
                        stamp,
                    ),
                )
                if run_cursor.rowcount != 1:
                    self._raise_fence_failure(lease, stamp)
                self._conn.execute(
                    """
                    INSERT INTO agent_run_checkpoints
                        (run_id, sequence, kind, payload, created_at)
                    VALUES (?, ?, 'tool_completed', ?, ?)
                    """,
                    (
                        lease.run_id,
                        sequence,
                        _json(bounded_checkpoint_payload(payload)),
                        stamp,
                    ),
                )
                checkpoint_row = self._conn.execute(
                    """
                    SELECT * FROM agent_run_checkpoints
                    WHERE run_id = ? AND sequence = ?
                    """,
                    (lease.run_id, sequence),
                ).fetchone()
                run_row = self._conn.execute(
                    "SELECT * FROM agent_runs WHERE run_id = ?", (lease.run_id,)
                ).fetchone()
                inbox_row = self._conn.execute(
                    "SELECT * FROM inbox WHERE id = ?", (approval_id,)
                ).fetchone()
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        if checkpoint_row is None or inbox_row is None:
            raise RuntimeError("atomic approved completion did not persist")
        return (
            _lease_from_row(run_row),
            _checkpoint_row(checkpoint_row),
            project_inbox_row(inbox_row),
        )

    def interrupt_approved_tool(
        self,
        lease: AgentRunLease,
        continuation: dict[str, Any],
        *,
        approval_id: str,
        claimant: str,
        payload: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> tuple[None, dict[str, Any], dict[str, Any]]:
        """Atomically classify a lost approved tool outcome on both authorities."""
        _validate_checkpoint("tool_outcome_unknown", "interrupted")
        stamp = _timestamp(now)
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._fenced_row(lease, stamp)
                _validate_approved_run_boundary(
                    row, approval_id, continuation, interrupted=True
                )
                inbox_row = self._conn.execute(
                    "SELECT * FROM inbox WHERE id = ?", (approval_id,)
                ).fetchone()
                if not _owned_approved_inbox(
                    inbox_row, lease.run_id, claimant
                ):
                    raise AgentRunLeaseLost(
                        "approved inbox execution interruption was fenced"
                    )
                inbox_cursor = self._conn.execute(
                    """
                    UPDATE inbox SET
                        execution_status = 'interrupted',
                        execution_claimant = NULL,
                        execution_error = ?, execution_result = ?
                    WHERE id = ? AND state = 'resolved'
                      AND decision = 'allow' AND execution_status = 'executing'
                      AND execution_claimant = ? AND run_id = ?
                    """,
                    (
                        APPROVED_TOOL_OUTCOME_UNKNOWN_ERROR,
                        json.dumps(
                            {
                                "status": "interrupted",
                                "error": APPROVED_TOOL_OUTCOME_UNKNOWN_ERROR,
                            }
                        ),
                        approval_id,
                        claimant,
                        lease.run_id,
                    ),
                )
                if inbox_cursor.rowcount != 1:
                    raise AgentRunLeaseLost(
                        "approved inbox execution interruption was fenced"
                    )
                sequence = int(row["checkpoint_sequence"]) + 1
                next_continuation = merge_continuation(
                    json_object(row["continuation"]), continuation
                )
                run_cursor = self._conn.execute(
                    """
                    UPDATE agent_runs SET
                        current_state = 'interrupted', checkpoint_sequence = ?,
                        continuation = ?, lease_owner = NULL,
                        lease_expires_at = NULL, version = version + 1,
                        updated_at = ?
                    WHERE run_id = ? AND version = ? AND lease_owner = ?
                      AND lease_expires_at > ? AND current_state = 'running'
                    """,
                    (
                        sequence,
                        _json(next_continuation),
                        stamp,
                        lease.run_id,
                        lease.version,
                        lease.owner_id,
                        stamp,
                    ),
                )
                if run_cursor.rowcount != 1:
                    self._raise_fence_failure(lease, stamp)
                self._conn.execute(
                    """
                    INSERT INTO agent_run_checkpoints
                        (run_id, sequence, kind, payload, created_at)
                    VALUES (?, ?, 'tool_outcome_unknown', ?, ?)
                    """,
                    (
                        lease.run_id,
                        sequence,
                        _json(bounded_checkpoint_payload(payload)),
                        stamp,
                    ),
                )
                checkpoint_row = self._conn.execute(
                    """
                    SELECT * FROM agent_run_checkpoints
                    WHERE run_id = ? AND sequence = ?
                    """,
                    (lease.run_id, sequence),
                ).fetchone()
                inbox_row = self._conn.execute(
                    "SELECT * FROM inbox WHERE id = ?", (approval_id,)
                ).fetchone()
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        if checkpoint_row is None or inbox_row is None:
            raise RuntimeError("approved interruption did not persist")
        return None, _checkpoint_row(checkpoint_row), project_inbox_row(inbox_row)

    def release_lease(
        self,
        lease: AgentRunLease,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        stamp = _timestamp(now)
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = self._conn.execute(
                    """
                    UPDATE agent_runs SET
                        lease_owner = NULL, lease_expires_at = NULL,
                        version = version + 1, updated_at = ?
                    WHERE run_id = ? AND version = ? AND lease_owner = ?
                      AND lease_expires_at > ?
                    """,
                    (
                        stamp,
                        lease.run_id,
                        lease.version,
                        lease.owner_id,
                        stamp,
                    ),
                )
                if cursor.rowcount != 1:
                    self._raise_fence_failure(
                        lease, stamp, terminal_is_error=False
                    )
                row = self._conn.execute(
                    "SELECT * FROM agent_runs WHERE run_id = ?", (lease.run_id,)
                ).fetchone()
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return _run_row(row)

    def reconcile_expired_leases(
        self, now: datetime | None = None
    ) -> list[dict[str, Any]]:
        stamp = _timestamp(now)
        recovered: list[dict[str, Any]] = []
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                rows = self._conn.execute(
                    """
                    SELECT * FROM agent_runs
                    WHERE (
                        lease_owner IS NOT NULL
                        AND (
                            current_state IN ('waiting_approval', 'waiting_question')
                            OR lease_expires_at IS NULL OR lease_expires_at <= ?
                        )
                    ) OR (
                        lease_owner IS NULL AND current_state = 'running'
                    )
                    ORDER BY created_at, rowid
                    """,
                    (stamp,),
                ).fetchall()
                for row in rows:
                    state = str(row["current_state"])
                    run_id = str(row["run_id"])
                    version = int(row["version"])
                    if state in {"waiting_approval", "waiting_question"}:
                        cursor = self._conn.execute(
                            """
                            UPDATE agent_runs SET
                                lease_owner = NULL, lease_expires_at = NULL,
                                version = version + 1, updated_at = ?
                            WHERE run_id = ? AND version = ?
                              AND current_state = ? AND lease_owner IS NOT NULL
                            """,
                            (stamp, run_id, version, state),
                        )
                        if cursor.rowcount == 1:
                            current = self._conn.execute(
                                "SELECT * FROM agent_runs WHERE run_id = ?",
                                (run_id,),
                            ).fetchone()
                            recovered.append(_run_row(current))
                        continue
                    if state in TERMINAL_AGENT_RUN_STATES or state in {
                        "interrupted",
                    }:
                        if row["lease_owner"] is None:
                            continue
                        cursor = self._conn.execute(
                            """
                            UPDATE agent_runs SET
                                lease_owner = NULL, lease_expires_at = NULL,
                                version = version + 1, updated_at = ?
                            WHERE run_id = ? AND version = ?
                              AND lease_owner IS NOT NULL
                              AND (lease_expires_at IS NULL OR lease_expires_at <= ?)
                            """,
                            (stamp, run_id, version, stamp),
                        )
                        if cursor.rowcount == 1:
                            current = self._conn.execute(
                                "SELECT * FROM agent_runs WHERE run_id = ?",
                                (run_id,),
                            ).fetchone()
                            recovered.append(_run_row(current))
                        continue

                    continuation = project_continuation(
                        json_object(row["continuation"])
                    )
                    cursor_data = continuation.setdefault("cursor", {})
                    phase = str(cursor_data.get("phase") or "")
                    pending_model = continuation.get("pending_model")
                    pending = continuation.get("pending_tool")
                    if phase == "approval_ready":
                        resolved = continuation.get("resolved_approval")
                        interaction = continuation.get("pending_interaction")
                        if not (
                            isinstance(resolved, dict)
                            and resolved.get("decision") in {"allow", "deny"}
                            and interaction
                            == {"kind": "approval", "id": resolved.get("id")}
                            and isinstance(pending, dict)
                            and pending.get("status") == "not_started"
                            and pending.get("budget_reserved") is False
                        ):
                            cursor_data["phase"] = "review_required"
                    elif phase == "model_in_flight":
                        if (
                            isinstance(pending_model, dict)
                            and pending_model.get("budget_reserved") is True
                        ):
                            cursor_data["phase"] = "model_ready"
                            pending_model["status"] = "retry_ready"
                        else:
                            cursor_data["phase"] = "review_required"
                    elif phase == "tool_in_flight" and isinstance(pending, dict):
                        if (
                            pending.get("retry_class") == "safe"
                            and pending.get("budget_reserved") is True
                        ):
                            cursor_data["phase"] = "tools_ready"
                            pending["status"] = "retry_ready"
                        else:
                            cursor_data["phase"] = "review_required"
                            pending["status"] = "outcome_unknown"
                            self._conn.execute(
                                """
                                UPDATE inbox SET
                                    execution_status = 'interrupted',
                                    execution_claimant = NULL,
                                    execution_error = ?, execution_result = ?
                                WHERE id = ? AND run_id = ?
                                  AND state = 'resolved' AND decision = 'allow'
                                  AND execution_status = 'executing'
                                """,
                                (
                                    APPROVED_TOOL_OUTCOME_UNKNOWN_ERROR,
                                    json.dumps(
                                        {
                                            "status": "interrupted",
                                            "error": APPROVED_TOOL_OUTCOME_UNKNOWN_ERROR,
                                        }
                                    ),
                                    pending.get("call_id"),
                                    run_id,
                                ),
                            )
                    elif phase not in {"model_ready", "tools_ready"}:
                        cursor_data["phase"] = "review_required"
                    sequence = int(row["checkpoint_sequence"]) + 1
                    cursor = self._conn.execute(
                        """
                        UPDATE agent_runs SET
                            current_state = 'interrupted', checkpoint_sequence = ?,
                            continuation = ?, lease_owner = NULL,
                            lease_expires_at = NULL, version = version + 1,
                            updated_at = ?
                        WHERE run_id = ? AND version = ? AND current_state = 'running'
                          AND (
                              lease_owner IS NULL OR lease_expires_at IS NULL
                              OR lease_expires_at <= ?
                          )
                        """,
                        (
                            sequence,
                            _json(continuation),
                            stamp,
                            run_id,
                            version,
                            stamp,
                        ),
                    )
                    if cursor.rowcount != 1:
                        continue
                    self._conn.execute(
                        """
                        INSERT INTO agent_run_checkpoints
                            (run_id, sequence, kind, payload, created_at)
                        VALUES (?, ?, 'process_interrupted', ?, ?)
                        """,
                        (
                            run_id,
                            sequence,
                            _json(
                                {
                                    "status": "interrupted",
                                    "reason": "lease_expired"
                                    if row["lease_owner"] is not None
                                    else "legacy_unleased_run",
                                    "phase": cursor_data["phase"],
                                }
                            ),
                            stamp,
                        ),
                    )
                    current = self._conn.execute(
                        "SELECT * FROM agent_runs WHERE run_id = ?", (run_id,)
                    ).fetchone()
                    recovered.append(_run_row(current))
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return recovered

    def list_resumable_runs(
        self, now: datetime | None = None
    ) -> list[dict[str, Any]]:
        self.reconcile_expired_leases(now=now)
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM agent_runs
                WHERE current_state = 'interrupted' AND lease_owner IS NULL
                ORDER BY updated_at, rowid
                """
            ).fetchall()
        return [
            _run_row(row)
            for row in rows
            if json_object(row["continuation"])
            .get("cursor", {})
            .get("phase")
            in {"model_ready", "tools_ready", "approval_ready"}
        ]

    def validate_transcript_prefix(
        self, run_id: str, messages: list[dict[str, Any]]
    ) -> Literal["exact", "extra_tail", "mismatch"]:
        with self._lock:
            row = self._conn.execute(
                "SELECT continuation FROM agent_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise KeyError(run_id)
        cursor = json_object(row["continuation"]).get("cursor")
        if not isinstance(cursor, dict):
            return "mismatch"
        count = cursor.get("transcript_prefix_count")
        digest = cursor.get("transcript_prefix_sha256")
        if (
            not isinstance(count, int)
            or isinstance(count, bool)
            or count < 0
            or not isinstance(digest, str)
            or valid_sha256(digest) is None
            or len(messages) < count
        ):
            return "mismatch"
        if transcript_prefix_sha256(messages[:count]) != digest:
            return "mismatch"
        return "exact" if len(messages) == count else "extra_tail"

    def _fenced_row(
        self, lease: AgentRunLease, stamp: str
    ) -> sqlite3.Row:
        row = self._conn.execute(
            "SELECT * FROM agent_runs WHERE run_id = ?", (lease.run_id,)
        ).fetchone()
        if row is None:
            raise KeyError(lease.run_id)
        if str(row["current_state"]) in TERMINAL_AGENT_RUN_STATES:
            raise ValueError("cannot mutate a terminal Agent Run")
        if row["lease_owner"] != lease.owner_id:
            raise AgentRunLeaseLost(lease.run_id)
        if row["lease_expires_at"] is None or str(row["lease_expires_at"]) <= stamp:
            raise AgentRunLeaseLost(lease.run_id)
        if int(row["version"]) != lease.version:
            raise AgentRunVersionConflict(lease.run_id)
        return row

    def _raise_fence_failure(
        self,
        lease: AgentRunLease,
        stamp: str,
        *,
        terminal_is_error: bool = True,
    ) -> None:
        row = self._conn.execute(
            "SELECT * FROM agent_runs WHERE run_id = ?", (lease.run_id,)
        ).fetchone()
        if row is None:
            raise KeyError(lease.run_id)
        if terminal_is_error and str(row["current_state"]) in TERMINAL_AGENT_RUN_STATES:
            raise ValueError("cannot mutate a terminal Agent Run")
        if row["lease_owner"] != lease.owner_id:
            raise AgentRunLeaseLost(lease.run_id)
        if row["lease_expires_at"] is None or str(row["lease_expires_at"]) <= stamp:
            raise AgentRunLeaseLost(lease.run_id)
        if int(row["version"]) != lease.version:
            raise AgentRunVersionConflict(lease.run_id)
        raise AgentRunLeaseLost(lease.run_id)

    def _initialize_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    name TEXT PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS agent_runs (
                    run_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    parent_run_id TEXT,
                    trigger TEXT NOT NULL,
                    original_goal TEXT NOT NULL,
                    original_goal_fingerprint TEXT,
                    original_goal_fingerprint_source TEXT,
                    current_state TEXT NOT NULL,
                    provider_model_id TEXT,
                    checkpoint_sequence INTEGER NOT NULL DEFAULT 0,
                    skills_loaded TEXT NOT NULL DEFAULT '[]',
                    source_refs TEXT NOT NULL DEFAULT '[]',
                    artifact_refs TEXT NOT NULL DEFAULT '[]',
                    usage TEXT NOT NULL DEFAULT '{}',
                    terminal_result TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    finished_at TEXT
                );
                CREATE TABLE IF NOT EXISTS agent_run_checkpoints (
                    run_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, sequence),
                    FOREIGN KEY (run_id) REFERENCES agent_runs(run_id)
                );
                CREATE INDEX IF NOT EXISTS agent_runs_session_created
                    ON agent_runs(session_id, created_at);
                """
            )
            self._conn.commit()

    def _ensure_fingerprint_columns(self) -> None:
        with self._lock:
            for name in (
                "original_goal_fingerprint",
                "original_goal_fingerprint_source",
            ):
                try:
                    self._conn.execute(
                        f"ALTER TABLE agent_runs ADD COLUMN {name} TEXT"
                    )
                except sqlite3.OperationalError:
                    pass
            self._conn.commit()

    def _migrate_agent_run_privacy(self) -> None:
        """Scrub pre-invariant Agent Run rows without changing their history."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                applied = self._conn.execute(
                    "SELECT 1 FROM schema_migrations WHERE name = ?",
                    (_PRIVACY_MIGRATION,),
                ).fetchone()
                if applied is not None:
                    self._conn.commit()
                    return
                runs = self._conn.execute(
                    """
                    SELECT run_id, original_goal, original_goal_fingerprint,
                           original_goal_fingerprint_source, skills_loaded,
                           source_refs, artifact_refs, terminal_result
                    FROM agent_runs
                    """
                ).fetchall()
                for row in runs:
                    stored_goal = str(row["original_goal"])
                    fingerprint = row["original_goal_fingerprint"]
                    if fingerprint is None:
                        fingerprint = original_goal_fingerprint(stored_goal)
                    source = row["original_goal_fingerprint_source"]
                    if source not in {"raw", "legacy_sanitized"}:
                        source = (
                            "legacy_sanitized"
                            if "[REDACTED" in stored_goal
                            and fingerprint == original_goal_fingerprint(stored_goal)
                            else "raw"
                        )
                    self._conn.execute(
                        """
                        UPDATE agent_runs SET
                            original_goal_fingerprint = ?,
                            original_goal_fingerprint_source = ?
                        WHERE run_id = ?
                        """,
                        (fingerprint, source, str(row["run_id"])),
                    )

                for row in runs:
                    terminal = nullable_json_object(row["terminal_result"])
                    safe_terminal = (
                        None
                        if terminal is None
                        else _json(project_terminal_result(terminal))
                    )
                    self._conn.execute(
                        """
                        UPDATE agent_runs SET
                            original_goal = ?, skills_loaded = ?, source_refs = ?,
                            artifact_refs = ?, terminal_result = ?
                        WHERE run_id = ?
                        """,
                        (
                            str(sanitize_agent_run_value(row["original_goal"])),
                            _json(
                                sanitize_agent_run_value(
                                    json_list(row["skills_loaded"])
                                )
                            ),
                            _json(project_source_refs(json_list(row["source_refs"]))),
                            _json(
                                project_artifact_refs(
                                    json_list(row["artifact_refs"])
                                )
                            ),
                            safe_terminal,
                            str(row["run_id"]),
                        ),
                    )

                checkpoints = self._conn.execute(
                    """
                    SELECT run_id, sequence, payload
                    FROM agent_run_checkpoints
                    """
                ).fetchall()
                for row in checkpoints:
                    self._conn.execute(
                        """
                        UPDATE agent_run_checkpoints SET payload = ?
                        WHERE run_id = ? AND sequence = ?
                        """,
                        (
                            _json(
                                bounded_checkpoint_payload(
                                    json_object(row["payload"])
                                )
                            ),
                            str(row["run_id"]),
                            int(row["sequence"]),
                        ),
                    )
                self._conn.execute(
                    "INSERT INTO schema_migrations (name, applied_at) VALUES (?, ?)",
                    (_PRIVACY_MIGRATION, _timestamp(None)),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def _migrate_resume_schema(self) -> None:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                applied = self._conn.execute(
                    "SELECT 1 FROM schema_migrations WHERE name = ?",
                    (_RESUME_MIGRATION,),
                ).fetchone()
                if applied is not None:
                    self._conn.commit()
                    return
                columns = {
                    str(row[1])
                    for row in self._conn.execute(
                        "PRAGMA table_info(agent_runs)"
                    ).fetchall()
                }
                additions = {
                    "version": "INTEGER NOT NULL DEFAULT 0",
                    "lease_owner": "TEXT",
                    "lease_expires_at": "TEXT",
                    "continuation": "TEXT NOT NULL DEFAULT '{}'",
                }
                for name, definition in additions.items():
                    if name not in columns:
                        self._conn.execute(
                            f"ALTER TABLE agent_runs ADD COLUMN {name} {definition}"
                        )
                self._conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS agent_runs_active_lease_expiry
                    ON agent_runs(lease_expires_at)
                    WHERE lease_owner IS NOT NULL
                    """
                )
                now = datetime.now(UTC).isoformat()
                rows = self._conn.execute(
                    """
                    SELECT run_id, checkpoint_sequence
                    FROM agent_runs
                    WHERE current_state = 'running'
                      AND COALESCE(continuation, '{}') = '{}'
                    """
                ).fetchall()
                for row in rows:
                    run_id = str(row["run_id"])
                    sequence = int(row["checkpoint_sequence"]) + 1
                    continuation = {
                        "schema_version": 1,
                        "cursor": {"phase": "review_required"},
                    }
                    self._conn.execute(
                        """
                        UPDATE agent_runs SET
                            current_state = 'interrupted',
                            checkpoint_sequence = ?, continuation = ?,
                            version = version + 1, updated_at = ?
                        WHERE run_id = ? AND current_state = 'running'
                        """,
                        (sequence, json.dumps(continuation), now, run_id),
                    )
                    self._conn.execute(
                        """
                        INSERT INTO agent_run_checkpoints
                            (run_id, sequence, kind, payload, created_at)
                        VALUES (?, ?, 'process_interrupted', ?, ?)
                        """,
                        (
                            run_id,
                            sequence,
                            json.dumps(
                                {
                                    "status": "interrupted",
                                    "reason": "legacy_unleased_run",
                                }
                            ),
                            now,
                        ),
                    )
                self._conn.execute(
                    "INSERT INTO schema_migrations (name, applied_at) VALUES (?, ?)",
                    (_RESUME_MIGRATION, now),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
