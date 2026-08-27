"""Stateful, transport-free boundaries for one leased Agent Run execution."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from typing import Any, TYPE_CHECKING

from coworker.agent_run_continuation import (
    MAX_SAFE_ID,
    MAX_TOOL_NAME,
    project_continuation,
    transcript_prefix_sha256,
    valid_sha256,
)
from coworker.agent_run_repository import (
    MAX_LEASE_SECONDS,
    AgentRunLease,
    AgentRunLeaseLost,
    AgentRunVersionConflict,
)
from coworker.agent_runs import (
    TERMINAL_AGENT_RUN_STATES,
    redact_sensitive_assignments,
    safe_error_summary,
)
from coworker.events import TurnIdentity

if TYPE_CHECKING:
    from coworker.store import ConversationStore


# Provider streams currently use a 60-second transport timeout. Keep enough
# cushion for boundary persistence without permitting an unbounded claim.
EXECUTION_LEASE_SECONDS = min(5 * 60, MAX_LEASE_SECONDS)


class AgentRunExecutionOwnershipError(RuntimeError):
    """The execution no longer owns an active lease for its Agent Run."""


class AgentRunExecution:
    """Persist semantic execution boundaries without running external work."""

    def __init__(
        self,
        store: ConversationStore,
        identity: TurnIdentity,
        owner_id: str,
        lease: AgentRunLease,
        snapshot: dict[str, Any],
        max_steps: int,
        now: datetime | None,
    ) -> None:
        self._store = store
        self._identity = identity
        self._owner_id = owner_id
        self._lease: AgentRunLease | None = lease
        self._version = lease.version
        self._snapshot = project_continuation(snapshot)
        self._max_steps = max_steps
        self._now = now

    @classmethod
    def start(
        cls,
        store: ConversationStore,
        identity: TurnIdentity,
        goal: str,
        trigger: str,
        provider_model_id: str | None,
        max_steps: int,
        owner_id: str | None = None,
        now: datetime | None = None,
    ) -> AgentRunExecution:
        if isinstance(max_steps, bool) or not isinstance(max_steps, int) or max_steps < 1:
            raise ValueError("max_steps must be a positive integer")
        owner = owner_id or f"execution_{uuid.uuid4().hex}"
        started = store.start_agent_run(
            run_id=identity.run_id,
            session_id=identity.session_id,
            trigger=trigger,
            original_goal=goal,
            provider_model_id=provider_model_id,
        )
        existing = project_continuation(started.get("continuation"))
        cls._reject_unstartable(identity.run_id, started["current_state"], existing)
        lease = store.agent_runs.acquire_lease(
            identity.run_id,
            owner,
            started["version"],
            EXECUTION_LEASE_SECONDS,
            now=now,
        )
        if lease is None:
            raise AgentRunExecutionOwnershipError(
                f"Agent Run {identity.run_id} is owned by another execution"
            )
        execution = cls(
            store,
            identity,
            owner,
            lease,
            existing,
            max_steps,
            now,
        )
        current = store.get_agent_run(identity.run_id)
        if current is None:
            execution._release_after_start_failure()
            raise KeyError(identity.run_id)
        existing = project_continuation(current.get("continuation"))
        execution._snapshot = existing
        try:
            cls._reject_unstartable(
                identity.run_id, current["current_state"], existing
            )
        except AgentRunExecutionOwnershipError:
            execution._release_after_start_failure()
            raise
        if existing.get("identity"):
            if existing["identity"] != {
                "message_id": identity.message_id,
                "part_id": identity.part_id,
            }:
                execution._release_after_start_failure()
                raise ValueError("Agent Run continuation identity cannot change")
            execution._refresh_snapshot()
            return execution
        initial = {
            "identity": {
                "message_id": identity.message_id,
                "part_id": identity.part_id,
            },
            "cursor": {
                "phase": "model_ready",
                "step_index": 0,
                "next_tool_index": 0,
                **_prefixes([], []),
            },
            "visible_partial": {
                "message_id": identity.message_id,
                "text_length": 0,
                "truncated": False,
            },
            "completed_tool_receipts": [],
            "remaining_budgets": {
                "work_steps": max_steps,
                "tool_calls": max_steps,
                "delivery_passes": 1,
            },
        }
        try:
            execution._lease = store.agent_runs.update_continuation(
                lease, initial, now=now
            )
        except (AgentRunLeaseLost, AgentRunVersionConflict) as exc:
            execution._lease = None
            raise AgentRunExecutionOwnershipError(
                f"Agent Run {identity.run_id} lease was lost during initialization"
            ) from exc
        except Exception:
            execution._release_after_start_failure()
            raise
        execution._version = execution._lease.version
        execution._snapshot = project_continuation(initial)
        return execution

    @staticmethod
    def _reject_unstartable(
        run_id: str, state: str, snapshot: dict[str, Any]
    ) -> None:
        phase = snapshot.get("cursor", {}).get("phase")
        if state == "running" and phase != "review_required":
            return
        raise AgentRunExecutionOwnershipError(
            f"Agent Run {run_id} requires explicit review or resume"
            + (f" from {phase}" if phase else "")
        )

    @property
    def run_id(self) -> str:
        return self._identity.run_id

    @property
    def identity(self) -> TurnIdentity:
        return self._identity

    @property
    def owner_id(self) -> str:
        return self._owner_id

    @property
    def owner(self) -> str:
        return self._owner_id

    @property
    def lease(self) -> AgentRunLease | None:
        return self._lease

    @property
    def current_lease(self) -> AgentRunLease | None:
        return self._lease

    @property
    def metadata(self) -> dict[str, Any]:
        projected = project_continuation(self._snapshot)
        cursor = projected.get("cursor", {})
        return {
            "run_id": self.run_id,
            "owner_id": self.owner_id,
            "phase": cursor.get("phase"),
            "step_index": int(cursor.get("step_index", 0)),
            "next_tool_index": int(cursor.get("next_tool_index", 0)),
            "visible_partial": dict(projected.get("visible_partial", {})),
            "pending_interaction": (
                dict(projected["pending_interaction"])
                if "pending_interaction" in projected
                else None
            ),
            "remaining_budgets": dict(
                projected.get("remaining_budgets", {})
            ),
        }

    def renew(self) -> AgentRunLease:
        lease = self._require_lease()
        try:
            renewed = self._store.agent_runs.renew_lease(
                lease, EXECUTION_LEASE_SECONDS, now=self._now
            )
        except (AgentRunLeaseLost, AgentRunVersionConflict) as exc:
            self._lease = None
            raise self._ownership_error("lease renewal failed") from exc
        self._lease = renewed
        self._version = renewed.version
        return renewed

    def model_pending(
        self,
        history: list[dict[str, Any]],
        events: list[dict[str, Any]],
        step_index: int,
    ) -> dict[str, Any]:
        self._require_lease()
        step = _nonnegative_index(step_index, "step_index")
        prefixes = _prefixes(history, events)
        if self._pending_retry_matches(
            "model_pending",
            phase="model_in_flight",
            step_index=step,
            next_tool_index=0,
            prefixes=prefixes,
        ):
            self._recover_ambiguous_lease()
            return self.metadata
        cursor = self._cursor()
        phase = cursor.get("phase")
        current_step = int(cursor.get("step_index", 0))
        if phase not in {"model_ready", "tools_ready"} or step < current_step:
            raise ValueError("model_pending cannot move execution backwards")
        budgets = self._budgets()
        if budgets.get("work_steps", 0) < 1:
            raise ValueError("Agent Run work-step budget exhausted")
        if (
            phase == "model_ready"
            and step == current_step
            and budgets.get("work_steps") != self._max_steps
        ):
            raise ValueError("model_pending cannot repeat completed progress")
        budgets["work_steps"] -= 1
        return self._checkpoint(
            "model_pending",
            {
                "cursor": {
                    "phase": "model_in_flight",
                    "step_index": step,
                    "next_tool_index": 0,
                    **prefixes,
                },
                "remaining_budgets": budgets,
            },
            payload={"step_index": step},
        )

    def model_completed(
        self,
        history: list[dict[str, Any]],
        events: list[dict[str, Any]],
        step_index: int,
        has_tools: bool,
        text_length: int,
    ) -> dict[str, Any]:
        self._require_lease()
        step = _nonnegative_index(step_index, "step_index")
        length = _nonnegative_index(text_length, "text_length")
        self._require_phase("model_in_flight", step)
        return self._checkpoint(
            "model_completed",
            {
                "cursor": {
                    "phase": "tools_ready" if has_tools else "model_ready",
                    "step_index": step,
                    "next_tool_index": 0,
                    **_prefixes(history, events),
                },
                "visible_partial": {
                    "message_id": self._identity.message_id,
                    "text_length": length,
                    "truncated": False,
                },
            },
            payload={
                "step_index": step,
                "has_tools": bool(has_tools),
                "text_length": length,
            },
            usage_delta={"model_calls": 1},
        )

    def tool_pending(
        self,
        history: list[dict[str, Any]],
        events: list[dict[str, Any]],
        step_index: int,
        tool_index: int,
        call_id: str,
        name: str,
        retry_safe: bool,
    ) -> dict[str, Any]:
        self._require_lease()
        step = _nonnegative_index(step_index, "step_index")
        tool = _nonnegative_index(tool_index, "tool_index")
        safe_call_id = _safe_boundary_text(call_id, MAX_SAFE_ID, "call_id")
        safe_name = _safe_boundary_text(name, MAX_TOOL_NAME, "name")
        attempt_id = _attempt_id(self.run_id, step, tool, safe_call_id)
        prefixes = _prefixes(history, events)
        projected = project_continuation(self._current_persisted_snapshot())
        pending = projected.get("pending_tool")
        cursor = projected.get("cursor", {})
        if (
            self._last_checkpoint_kind() == "tool_pending"
            and cursor.get("phase") == "tool_in_flight"
            and cursor.get("step_index") == step
            and cursor.get("next_tool_index") == tool
            and _cursor_has_prefixes(cursor, prefixes)
            and pending
            == {
                "attempt_id": attempt_id,
                "call_id": safe_call_id,
                "name": safe_name,
                "retry_class": "safe" if retry_safe else "consequential",
                "status": "in_flight",
            }
        ):
            self._snapshot = projected
            self._recover_ambiguous_lease()
            return self.metadata
        self._snapshot = projected
        cursor = self._cursor()
        if (
            cursor.get("phase") != "tools_ready"
            or int(cursor.get("step_index", 0)) != step
            or int(cursor.get("next_tool_index", 0)) != tool
        ):
            raise ValueError("tool_pending does not match the next tool cursor")
        budgets = self._budgets()
        if budgets.get("tool_calls", 0) < 1:
            raise ValueError("Agent Run tool-call budget exhausted")
        budgets["tool_calls"] -= 1
        return self._checkpoint(
            "tool_pending",
            {
                "cursor": {
                    "phase": "tool_in_flight",
                    "step_index": step,
                    "next_tool_index": tool,
                    **prefixes,
                },
                "pending_tool": {
                    "attempt_id": attempt_id,
                    "call_id": safe_call_id,
                    "name": safe_name,
                    "retry_class": "safe" if retry_safe else "consequential",
                    "status": "in_flight",
                },
                "remaining_budgets": budgets,
            },
            payload={
                "step_index": step,
                "tool_index": tool,
                "call_id": safe_call_id,
                "name": safe_name,
                "retry_class": "safe" if retry_safe else "consequential",
            },
        )

    def tool_completed(
        self,
        history: list[dict[str, Any]],
        events: list[dict[str, Any]],
        step_index: int,
        tool_index: int,
        call_id: str,
        name: str,
        ok: bool,
        result_digest: str,
    ) -> dict[str, Any]:
        self._require_lease()
        step = _nonnegative_index(step_index, "step_index")
        tool = _nonnegative_index(tool_index, "tool_index")
        safe_call_id = _safe_boundary_text(call_id, MAX_SAFE_ID, "call_id")
        safe_name = _safe_boundary_text(name, MAX_TOOL_NAME, "name")
        digest = valid_sha256(result_digest)
        if digest is None:
            raise ValueError("result_digest must be a SHA-256 digest")
        attempt_id = _attempt_id(self.run_id, step, tool, safe_call_id)
        cursor = self._cursor()
        pending = self._snapshot.get("pending_tool")
        if (
            cursor.get("phase") != "tool_in_flight"
            or int(cursor.get("step_index", 0)) != step
            or int(cursor.get("next_tool_index", 0)) != tool
            or not isinstance(pending, dict)
            or pending.get("attempt_id") != attempt_id
            or pending.get("call_id") != safe_call_id
            or pending.get("name") != safe_name
        ):
            raise ValueError("tool_completed does not match the pending tool")
        receipt = {
            "attempt_id": attempt_id,
            "call_id": safe_call_id,
            "name": safe_name,
            "ok": bool(ok),
            "transcript_index": max(0, len(history) - 1),
            "result_sha256": digest,
        }
        return self._checkpoint(
            "tool_completed",
            {
                "cursor": {
                    "phase": "tools_ready",
                    "step_index": step,
                    "next_tool_index": tool + 1,
                    **_prefixes(history, events),
                },
                "pending_tool": None,
                "completed_tool_receipts": [receipt],
            },
            payload={
                "step_index": step,
                "tool_index": tool,
                "call_id": safe_call_id,
                "name": safe_name,
                "ok": bool(ok),
            },
            usage_delta={"tool_calls": 1},
        )

    def waiting_approval(
        self,
        history: list[dict[str, Any]],
        events: list[dict[str, Any]],
        interaction_id: str,
    ) -> dict[str, Any]:
        self._require_lease()
        interaction = _safe_boundary_text(
            interaction_id, MAX_SAFE_ID, "interaction_id"
        )
        cursor = self._cursor()
        return self._checkpoint(
            "waiting_approval",
            {
                "cursor": {
                    "phase": "waiting_approval",
                    "step_index": int(cursor.get("step_index", 0)),
                    "next_tool_index": int(cursor.get("next_tool_index", 0)),
                    **_prefixes(history, events),
                },
                "pending_interaction": {
                    "kind": "approval",
                    "id": interaction,
                },
            },
            payload={"id": interaction},
            state="waiting_approval",
        )

    def resume_resolved_approval(self, interaction_id: str) -> dict[str, Any]:
        if self._lease is not None:
            raise AgentRunExecutionOwnershipError(
                f"Agent Run {self.run_id} already has an active execution lease"
            )
        interaction = _safe_boundary_text(
            interaction_id, MAX_SAFE_ID, "interaction_id"
        )
        pending = self._snapshot.get("pending_interaction")
        if pending != {"kind": "approval", "id": interaction}:
            raise AgentRunExecutionOwnershipError(
                f"Agent Run {self.run_id} has no matching resolved approval"
            )
        try:
            lease = self._store.agent_runs.acquire_resolved_waiting_lease(
                self.run_id,
                self.owner_id,
                self._version,
                interaction,
                EXECUTION_LEASE_SECONDS,
                now=self._now,
            )
        except AgentRunVersionConflict as exc:
            raise self._ownership_error("resolved approval lease was fenced") from exc
        if lease is None:
            raise AgentRunExecutionOwnershipError(
                f"Agent Run {self.run_id} resolved approval is not available for reacquisition"
            )
        self._lease = lease
        self._version = lease.version
        self._refresh_snapshot()
        return self.metadata

    def approval_resolved(
        self,
        history: list[dict[str, Any]],
        events: list[dict[str, Any]],
        interaction_id: str,
        resolution: str,
    ) -> dict[str, Any]:
        self._require_lease()
        interaction = _safe_boundary_text(
            interaction_id, MAX_SAFE_ID, "interaction_id"
        )
        safe_resolution = _safe_boundary_text(resolution, 64, "resolution")
        if self._snapshot.get("pending_interaction") != {
            "kind": "approval",
            "id": interaction,
        }:
            raise ValueError("approval_resolved does not match pending interaction")
        cursor = self._cursor()
        if cursor.get("phase") != "waiting_approval":
            raise ValueError("approval_resolved requires waiting_approval phase")
        return self._checkpoint(
            "approval_resolved",
            {
                "cursor": {
                    "phase": "tools_ready",
                    "step_index": int(cursor.get("step_index", 0)),
                    "next_tool_index": int(cursor.get("next_tool_index", 0)),
                    **_prefixes(history, events),
                },
                "pending_interaction": None,
            },
            payload={"id": interaction, "resolution": safe_resolution},
            state="running",
        )

    def terminal(
        self,
        history: list[dict[str, Any]],
        events: list[dict[str, Any]],
        state: str,
        status: str,
        message_id: str,
        text_length: int,
        error: str | None = None,
    ) -> dict[str, Any]:
        self._require_lease()
        if state not in TERMINAL_AGENT_RUN_STATES:
            raise ValueError("terminal state is invalid")
        safe_status = _safe_boundary_text(status, 64, "status")
        safe_message_id = _safe_boundary_text(
            message_id, MAX_SAFE_ID, "message_id"
        )
        length = _nonnegative_index(text_length, "text_length")
        terminal_result: dict[str, Any] = {
            "status": safe_status,
            "message_id": safe_message_id,
            "text_length": length,
        }
        if error is not None:
            terminal_result["error"] = safe_error_summary(error)
        cursor = self._cursor()
        return self._checkpoint(
            "terminal",
            {
                "cursor": {
                    "phase": state,
                    "step_index": int(cursor.get("step_index", 0)),
                    "next_tool_index": int(cursor.get("next_tool_index", 0)),
                    **_prefixes(history, events),
                },
                "visible_partial": {
                    "message_id": safe_message_id,
                    "text_length": length,
                    "truncated": False,
                },
                "pending_interaction": None,
                "pending_tool": None,
            },
            payload={
                "status": safe_status,
                "state": state,
                "text_length": length,
            },
            state=state,
            terminal_result=terminal_result,
        )

    def _checkpoint(
        self,
        kind: str,
        continuation: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        lease = self._require_lease()
        try:
            next_lease, _checkpoint = self._store.agent_runs.checkpoint_leased(
                lease,
                kind,
                continuation,
                now=self._now,
                **kwargs,
            )
        except (AgentRunLeaseLost, AgentRunVersionConflict) as exc:
            self._lease = None
            raise self._ownership_error(f"{kind} boundary lost its lease") from exc
        self._lease = next_lease
        self._version = (
            next_lease.version if next_lease is not None else lease.version + 1
        )
        self._refresh_snapshot()
        return self.metadata

    def _require_lease(self) -> AgentRunLease:
        if self._lease is None:
            raise AgentRunExecutionOwnershipError(
                f"Agent Run {self.run_id} has no active execution lease"
            )
        return self._lease

    def _require_phase(self, phase: str, step_index: int) -> None:
        cursor = self._cursor()
        if (
            cursor.get("phase") != phase
            or int(cursor.get("step_index", 0)) != step_index
        ):
            raise ValueError(
                f"execution boundary requires {phase} at step {step_index}"
            )

    def _cursor(self) -> dict[str, Any]:
        return dict(self._snapshot.get("cursor", {}))

    def _budgets(self) -> dict[str, int]:
        return {
            key: int(value)
            for key, value in self._snapshot.get("remaining_budgets", {}).items()
        }

    def _pending_retry_matches(
        self,
        checkpoint_kind: str,
        *,
        phase: str,
        step_index: int,
        next_tool_index: int,
        prefixes: dict[str, Any],
    ) -> bool:
        persisted = project_continuation(self._current_persisted_snapshot())
        self._snapshot = persisted
        cursor = persisted.get("cursor", {})
        return (
            self._last_checkpoint_kind() == checkpoint_kind
            and cursor.get("phase") == phase
            and cursor.get("step_index") == step_index
            and cursor.get("next_tool_index") == next_tool_index
            and _cursor_has_prefixes(cursor, prefixes)
        )

    def _recover_ambiguous_lease(self) -> None:
        lease = self._require_lease()
        run = self._store.get_agent_run(self.run_id)
        if run is None:
            self._lease = None
            raise self._ownership_error("Agent Run disappeared")
        persisted_version = int(run["version"])
        if persisted_version == lease.version:
            return
        try:
            recovered = self._store.agent_runs.acquire_lease(
                self.run_id,
                self.owner_id,
                persisted_version,
                EXECUTION_LEASE_SECONDS,
                now=self._now,
            )
        except AgentRunVersionConflict as exc:
            self._lease = None
            raise self._ownership_error("ambiguous boundary retry was fenced") from exc
        if recovered is None:
            self._lease = None
            raise self._ownership_error("ambiguous boundary retry lost ownership")
        self._lease = recovered
        self._version = recovered.version

    def _current_persisted_snapshot(self) -> dict[str, Any]:
        run = self._store.get_agent_run(self.run_id)
        if run is None:
            raise KeyError(self.run_id)
        return run.get("continuation", {})

    def _last_checkpoint_kind(self) -> str | None:
        checkpoints = self._store.list_agent_run_checkpoints(self.run_id)
        return str(checkpoints[-1]["kind"]) if checkpoints else None

    def _refresh_snapshot(self) -> None:
        run = self._store.get_agent_run(self.run_id)
        if run is None:
            raise KeyError(self.run_id)
        self._version = int(run["version"])
        self._snapshot = project_continuation(run.get("continuation"))

    def _release_after_start_failure(self) -> None:
        if self._lease is None:
            return
        try:
            self._store.agent_runs.release_lease(self._lease, now=self._now)
        finally:
            self._lease = None

    def _ownership_error(self, detail: str) -> AgentRunExecutionOwnershipError:
        return AgentRunExecutionOwnershipError(
            f"Agent Run {self.run_id} execution ownership error: {detail}"
        )


def _prefixes(
    history: list[dict[str, Any]], events: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "transcript_prefix_count": len(history),
        "transcript_prefix_sha256": transcript_prefix_sha256(history),
        "event_prefix_count": len(events),
        "event_prefix_sha256": transcript_prefix_sha256(events),
    }


def _cursor_has_prefixes(
    cursor: dict[str, Any], prefixes: dict[str, Any]
) -> bool:
    return all(cursor.get(key) == value for key, value in prefixes.items())


def _nonnegative_index(value: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a nonnegative integer")
    return value


def _safe_boundary_text(value: str, limit: int, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > limit
        or any(ord(char) < 32 for char in value)
        or redact_sensitive_assignments(value) != value
    ):
        raise ValueError(f"invalid {field}")
    return value


def _attempt_id(run_id: str, step: int, tool: int, call_id: str) -> str:
    readable = f"{run_id}:{step}:{call_id}:{tool}"
    if len(readable) <= MAX_SAFE_ID:
        return readable
    canonical = json.dumps(
        [run_id, step, tool, call_id],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return "attempt_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
