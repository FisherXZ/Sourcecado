"""Stateful, transport-free boundaries for one leased Agent Run execution."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, TYPE_CHECKING

from coworker.agent_run_continuation import (
    MAX_SAFE_ID,
    project_continuation,
)
from coworker.agent_run_repository import (
    MAX_LEASE_SECONDS,
    AgentRunLease,
    AgentRunLeaseLost,
    AgentRunVersionConflict,
)
from coworker.agent_runs import (
    redact_sensitive_assignments,
    safe_error_summary,
)
from coworker.agent_run_state import (
    approval_resolved_transition,
    initial_continuation,
    model_attempt_id,
    model_completed_transition,
    model_pending_transition,
    prefixes,
    terminal_transition,
    tool_attempt_id,
    tool_completed_transition,
    tool_pending_transition,
    waiting_approval_transition,
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
        self._resolved_decision: str | None = None

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
        initial = initial_continuation(
            {
                "message_id": identity.message_id,
                "part_id": identity.part_id,
            },
            max_steps,
        )
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

    @classmethod
    def resume(
        cls,
        store: ConversationStore,
        identity: TurnIdentity,
        max_steps: int,
        owner_id: str | None = None,
        now: datetime | None = None,
    ) -> AgentRunExecution:
        if isinstance(max_steps, bool) or not isinstance(max_steps, int) or max_steps < 1:
            raise ValueError("max_steps must be a positive integer")
        run = store.get_agent_run(identity.run_id)
        if run is None:
            raise KeyError(identity.run_id)
        snapshot = project_continuation(run.get("continuation"))
        if run["current_state"] != "interrupted" or snapshot.get("identity") != {
            "message_id": identity.message_id,
            "part_id": identity.part_id,
        }:
            raise AgentRunExecutionOwnershipError(
                f"Agent Run {identity.run_id} is not an exact resumable continuation"
            )
        owner = owner_id or f"execution_{uuid.uuid4().hex}"
        lease = store.agent_runs.acquire_resumable_lease(
            identity.run_id,
            owner,
            run["version"],
            EXECUTION_LEASE_SECONDS,
            now=now,
        )
        if lease is None:
            raise AgentRunExecutionOwnershipError(
                f"Agent Run {identity.run_id} resumable lease is unavailable"
            )
        execution = cls(
            store, identity, owner, lease, snapshot, max_steps, now
        )
        execution._refresh_snapshot()
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
            "expected_tool_count": int(cursor.get("expected_tool_count", 0)),
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
        supplied_prefixes = prefixes(history, events)
        persisted = project_continuation(self._current_persisted_snapshot())
        self._snapshot = persisted
        if (
            self._last_checkpoint_kind() == "model_pending"
            and persisted.get("cursor", {}).get("phase") == "model_in_flight"
            and persisted.get("cursor", {}).get("step_index") == step
            and persisted.get("cursor", {}).get("next_tool_index") == 0
            and _cursor_has_prefixes(
                persisted.get("cursor", {}), supplied_prefixes
            )
            and persisted.get("pending_model")
            == {
                "attempt_id": model_attempt_id(self.run_id, step),
                "status": "in_flight",
                "budget_reserved": True,
            }
        ):
            self._recover_ambiguous_lease()
            return self.metadata
        next_snapshot = model_pending_transition(
            persisted, self.run_id, history, events, step
        )
        return self._checkpoint(
            "model_pending",
            next_snapshot,
            payload={"step_index": step},
        )

    def model_completed(
        self,
        history: list[dict[str, Any]],
        events: list[dict[str, Any]],
        step_index: int,
        tool_count: int,
        text_length: int,
    ) -> dict[str, Any]:
        self._require_lease()
        step = _nonnegative_index(step_index, "step_index")
        count = _nonnegative_index(tool_count, "tool_count")
        length = _nonnegative_index(text_length, "text_length")
        next_snapshot = model_completed_transition(
            self._snapshot,
            self.run_id,
            history,
            events,
            step,
            count,
            length,
            self._identity.message_id,
        )
        return self._checkpoint(
            "model_completed",
            {**next_snapshot, "pending_model": None},
            payload={
                "step_index": step,
                "tool_count": count,
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
        supplied_prefixes = prefixes(history, events)
        projected = project_continuation(self._current_persisted_snapshot())
        pending = projected.get("pending_tool")
        cursor = projected.get("cursor", {})
        attempt_id = tool_attempt_id(self.run_id, step, tool, call_id)
        if (
            self._last_checkpoint_kind() == "tool_pending"
            and cursor.get("phase") == "tool_in_flight"
            and cursor.get("step_index") == step
            and cursor.get("next_tool_index") == tool
            and _cursor_has_prefixes(cursor, supplied_prefixes)
            and pending
            == {
                "attempt_id": attempt_id,
                "call_id": call_id,
                "name": name,
                "retry_class": "safe" if retry_safe else "consequential",
                "status": "in_flight",
                "budget_reserved": True,
            }
        ):
            self._snapshot = projected
            self._recover_ambiguous_lease()
            return self.metadata
        self._snapshot = projected
        next_snapshot = tool_pending_transition(
            projected,
            self.run_id,
            history,
            events,
            step,
            tool,
            call_id,
            name,
            retry_safe,
        )
        return self._checkpoint(
            "tool_pending",
            next_snapshot,
            payload={
                "step_index": step,
                "tool_index": tool,
                "call_id": call_id,
                "name": name,
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
        next_snapshot = tool_completed_transition(
            self._snapshot,
            self.run_id,
            history,
            events,
            step,
            tool,
            call_id,
            name,
            ok,
            result_digest,
        )
        return self._checkpoint(
            "tool_completed",
            {**next_snapshot, "pending_tool": None},
            payload={
                "step_index": step,
                "tool_index": tool,
                "call_id": call_id,
                "name": name,
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
        next_snapshot = waiting_approval_transition(
            self._snapshot, history, events, interaction
        )
        return self._checkpoint(
            "waiting_approval",
            next_snapshot,
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
            acquired = self._store.agent_runs.acquire_resolved_waiting_lease(
                self.run_id,
                self.owner_id,
                self._version,
                interaction,
                EXECUTION_LEASE_SECONDS,
                now=self._now,
            )
        except AgentRunVersionConflict as exc:
            raise self._ownership_error("resolved approval lease was fenced") from exc
        if acquired is None:
            raise AgentRunExecutionOwnershipError(
                f"Agent Run {self.run_id} resolved approval is not available for reacquisition"
            )
        self._lease = acquired.lease
        self._version = acquired.lease.version
        self._resolved_decision = acquired.decision
        self._refresh_snapshot()
        return self.metadata

    def approval_resolved(
        self,
        history: list[dict[str, Any]],
        events: list[dict[str, Any]],
        interaction_id: str,
        resolution: str | None = None,
    ) -> dict[str, Any]:
        self._require_lease()
        interaction = _safe_boundary_text(
            interaction_id, MAX_SAFE_ID, "interaction_id"
        )
        decision = self._resolved_decision
        if decision not in {"allow", "deny"}:
            raise ValueError("approval decision is not bound to this execution")
        if resolution is not None and resolution != decision:
            raise ValueError("approval resolution does not match exact decision")
        next_snapshot = approval_resolved_transition(
            self._snapshot, history, events, interaction
        )
        metadata = self._checkpoint(
            "approval_resolved",
            {**next_snapshot, "pending_interaction": None},
            payload={"id": interaction, "resolution": decision},
            state="running",
        )
        self._resolved_decision = None
        return metadata

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
        next_snapshot = terminal_transition(
            self._snapshot,
            history,
            events,
            state,
            safe_message_id,
            length,
        )
        return self._checkpoint(
            "terminal",
            {
                **next_snapshot,
                "pending_interaction": None,
                "pending_model": None,
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
