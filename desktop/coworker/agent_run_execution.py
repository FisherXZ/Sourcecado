"""Stateful, transport-free boundaries for one leased Agent Run execution."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, TYPE_CHECKING

from coworker.agent_run_approval import build_approval_park_request
from coworker.agent_run_continuation import (
    MAX_SAFE_ID,
    merge_continuation,
    project_continuation,
)
from coworker.agent_run_repository import (
    MAX_LEASE_SECONDS,
    AgentRunLease,
    AgentRunLeaseLost,
    AgentRunStartConflict,
    AgentRunVersionConflict,
)
from coworker.agent_runs import (
    redact_sensitive_assignments,
    safe_error_summary,
)
from coworker.agent_run_state import (
    approval_resolved_transition,
    initial_continuation,
    interrupt_inflight_tool_transition,
    model_attempt_id,
    model_completed_transition,
    model_pending_transition,
    prefixes,
    terminal_transition,
    tool_attempt_id,
    tool_completed_transition,
    tool_pending_transition,
    tool_skipped_transition,
    waiting_approval_transition,
    waiting_external_execution_transition,
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
        lease_seconds: float,
    ) -> None:
        self._store = store
        self._identity = identity
        self._owner_id = owner_id
        self._lease: AgentRunLease | None = lease
        self._version = lease.version
        self._snapshot = project_continuation(snapshot)
        self._max_steps = max_steps
        self._now = now
        self._lease_seconds = _execution_lease_seconds(lease_seconds)
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
        parent_run_id: str | None = None,
        lease_seconds: float = EXECUTION_LEASE_SECONDS,
    ) -> AgentRunExecution:
        if isinstance(max_steps, bool) or not isinstance(max_steps, int) or max_steps < 1:
            raise ValueError("max_steps must be a positive integer")
        duration = _execution_lease_seconds(lease_seconds)
        owner = owner_id or f"execution_{uuid.uuid4().hex}"
        initial = initial_continuation(
            {
                "message_id": identity.message_id,
                "part_id": identity.part_id,
            },
            max_steps,
        )
        try:
            started = store.agent_runs.start_and_acquire_lease(
                run_id=identity.run_id,
                session_id=identity.session_id,
                trigger=trigger,
                original_goal=goal,
                provider_model_id=provider_model_id,
                owner_id=owner,
                lease_seconds=duration,
                continuation=initial,
                parent_run_id=parent_run_id,
                now=now,
            )
        except AgentRunStartConflict as exc:
            raise AgentRunExecutionOwnershipError(str(exc)) from exc
        if started is None:
            raise AgentRunExecutionOwnershipError(
                f"Agent Run {identity.run_id} is owned by another execution"
            )
        execution = cls(
            store,
            identity,
            owner,
            started.lease,
            started.run["continuation"],
            max_steps,
            now,
            duration,
        )
        phase = execution._snapshot.get("cursor", {}).get("phase")
        if phase == "review_required":
            execution._release_after_start_failure()
            raise AgentRunExecutionOwnershipError(
                f"Agent Run {identity.run_id} requires explicit review"
            )
        return execution

    @classmethod
    def resume(
        cls,
        store: ConversationStore,
        identity: TurnIdentity,
        max_steps: int,
        owner_id: str | None = None,
        now: datetime | None = None,
        lease_seconds: float = EXECUTION_LEASE_SECONDS,
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
        duration = _execution_lease_seconds(lease_seconds)
        owner = owner_id or f"execution_{uuid.uuid4().hex}"
        lease = store.agent_runs.acquire_resumable_lease(
            identity.run_id,
            owner,
            run["version"],
            duration,
            now=now,
        )
        if lease is None:
            raise AgentRunExecutionOwnershipError(
                f"Agent Run {identity.run_id} resumable lease is unavailable"
            )
        execution = cls(
            store, identity, owner, lease, snapshot, max_steps, now, duration
        )
        execution._refresh_snapshot()
        return execution

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
    def lease_seconds(self) -> float:
        return self._lease_seconds

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
                lease, self._lease_seconds, now=self._now
            )
        except (AgentRunLeaseLost, AgentRunVersionConflict) as exc:
            self._lease = None
            raise self._ownership_error("lease renewal failed") from exc
        self._lease = renewed
        self._version = renewed.version
        return renewed

    def reconcile_expired_boundary(self) -> dict[str, Any]:
        """Classify an expired lease without granting this execution authority."""
        self._lease = None
        self._store.agent_runs.reconcile_expired_leases(now=self._now)
        self._refresh_snapshot()
        run = self._store.get_agent_run(self.run_id)
        if run is None:
            raise KeyError(self.run_id)
        return run

    def user_input(
        self,
        history: list[dict[str, Any]],
        events: list[dict[str, Any]],
        text_length: int,
    ) -> dict[str, Any]:
        """Commit the exact durable input/event prefix before external work."""
        self._require_lease()
        length = _nonnegative_index(text_length, "text_length")
        persisted = project_continuation(self._current_persisted_snapshot())
        cursor = dict(persisted.get("cursor", {}))
        if cursor.get("phase") != "model_ready" or cursor.get("step_index") != 0:
            raise ValueError("user_input requires the initial model-ready phase")
        self._snapshot = persisted
        next_snapshot = merge_continuation(
            persisted,
            {"cursor": {**cursor, **prefixes(history, events)}},
        )
        return self._checkpoint(
            "user_input",
            next_snapshot,
            payload={"text_length": length},
        )

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
        *,
        skills_loaded: list[str] | None = None,
        source_refs: list[dict[str, Any]] | None = None,
        artifact_refs: list[dict[str, Any]] | None = None,
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
            skills_loaded=skills_loaded,
            source_refs=source_refs,
            artifact_refs=artifact_refs,
            usage_delta={"tool_calls": 1},
        )

    def complete_approved_tool(
        self,
        history: list[dict[str, Any]],
        events: list[dict[str, Any]],
        step_index: int,
        tool_index: int,
        call_id: str,
        name: str,
        ok: bool,
        result_digest: str,
        *,
        claimant: str,
        result: dict[str, Any],
        skills_loaded: list[str] | None = None,
        source_refs: list[dict[str, Any]] | None = None,
        artifact_refs: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Commit an approved tool to its inbox claim and run atomically."""
        lease = self._require_lease()
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
        try:
            next_lease, _checkpoint, receipt = (
                self._store.agent_runs.complete_approved_tool(
                    lease,
                    {**next_snapshot, "pending_tool": None},
                    approval_id=call_id,
                    claimant=claimant,
                    ok=ok,
                    result=result,
                    payload={
                        "step_index": step,
                        "tool_index": tool,
                        "call_id": call_id,
                        "name": name,
                        "ok": bool(ok),
                    },
                    skills_loaded=skills_loaded,
                    source_refs=source_refs,
                    artifact_refs=artifact_refs,
                    now=self._now,
                )
            )
        except (AgentRunLeaseLost, AgentRunVersionConflict) as exc:
            self._lease = None
            raise self._ownership_error(
                "approved tool completion lost its lease or claim"
            ) from exc
        self._lease = next_lease
        self._version = next_lease.version
        self._refresh_snapshot()
        return receipt

    def waiting_approval(
        self,
        history: list[dict[str, Any]],
        events: list[dict[str, Any]],
        interaction_id: str,
        step_index: int,
        tool_index: int,
        call_id: str,
        name: str,
        retry_safe: bool,
    ) -> dict[str, Any]:
        self._require_lease()
        interaction = _safe_boundary_text(
            interaction_id, MAX_SAFE_ID, "interaction_id"
        )
        next_snapshot = waiting_approval_transition(
            self._snapshot,
            self.run_id,
            history,
            events,
            interaction,
            step_index,
            tool_index,
            call_id,
            name,
            retry_safe,
        )
        return self._checkpoint(
            "waiting_approval",
            next_snapshot,
            payload={"id": interaction},
            state="waiting_approval",
        )

    def waiting_approval_atomic(
        self,
        history: list[dict[str, Any]],
        events: list[dict[str, Any]],
        interaction_id: str,
        step_index: int,
        tool_index: int,
        call_id: str,
        name: str,
        retry_safe: bool,
        *,
        arguments: dict[str, Any],
        reason: str | None,
        resource: dict[str, Any] | None,
        approval_ttl_seconds: float,
    ) -> dict[str, Any]:
        """Atomically park an approval and relinquish execution authority."""
        lease = self._require_lease()
        interaction = _safe_boundary_text(
            interaction_id, MAX_SAFE_ID, "interaction_id"
        )
        next_snapshot = waiting_approval_transition(
            self._snapshot,
            self.run_id,
            history,
            events,
            interaction,
            step_index,
            tool_index,
            call_id,
            name,
            retry_safe,
        )
        approval = build_approval_park_request(
            item_id=interaction,
            call_id=call_id,
            name=name,
            arguments=arguments,
            reason=reason,
            session_id=self._identity.session_id,
            run_id=self.run_id,
            message_id=self._identity.message_id,
            part_id=self._identity.part_id,
            resource=resource,
            ttl_seconds=approval_ttl_seconds,
            now=self._now,
        )
        try:
            next_lease, _checkpoint, parked = (
                self._store.agent_runs.park_approval_and_wait(
                    lease,
                    next_snapshot,
                    approval,
                    payload={"id": interaction, "name": name},
                    now=self._now,
                )
            )
        except (AgentRunLeaseLost, AgentRunVersionConflict) as exc:
            self._lease = None
            raise self._ownership_error(
                "waiting_approval boundary lost its lease"
            ) from exc
        self._lease = next_lease
        self._version = lease.version + 1
        self._refresh_snapshot()
        return parked

    def waiting_external_execution(
        self,
        history: list[dict[str, Any]],
        events: list[dict[str, Any]],
        interaction_id: str,
        step_index: int,
        tool_index: int,
        call_id: str,
        name: str,
    ) -> dict[str, Any]:
        lease = self._require_lease()
        next_snapshot = waiting_external_execution_transition(
            self._snapshot,
            history,
            events,
            interaction_id,
            step_index,
            tool_index,
            call_id,
            name,
        )
        try:
            next_lease, _checkpoint = (
                self._store.agent_runs.wait_for_external_execution(
                    lease,
                    next_snapshot,
                    interaction_id=interaction_id,
                    payload={
                        "id": interaction_id,
                        "call_id": call_id,
                        "name": name,
                    },
                    now=self._now,
                )
            )
        except (AgentRunLeaseLost, AgentRunVersionConflict) as exc:
            self._lease = None
            raise self._ownership_error(
                "waiting external boundary lost its lease"
            ) from exc
        self._lease = next_lease
        self._version = lease.version + 1
        self._refresh_snapshot()
        return self.metadata

    def tool_skipped(
        self,
        history: list[dict[str, Any]],
        events: list[dict[str, Any]],
        step_index: int,
        tool_index: int,
        call_id: str,
        name: str,
        result_digest: str,
        *,
        outcome: str = "denied",
    ) -> dict[str, Any]:
        self._require_lease()
        step = _nonnegative_index(step_index, "step_index")
        tool = _nonnegative_index(tool_index, "tool_index")
        next_snapshot = tool_skipped_transition(
            self._snapshot,
            self.run_id,
            history,
            events,
            step,
            tool,
            call_id,
            name,
            result_digest,
            outcome,
        )
        return self._checkpoint(
            "tool_completed",
            {**next_snapshot, "pending_tool": None},
            payload={
                "step_index": step,
                "tool_index": tool,
                "call_id": call_id,
                "name": name,
                "ok": False,
                "outcome": outcome,
            },
        )

    def adopt_completed_approval(
        self,
        history: list[dict[str, Any]],
        events: list[dict[str, Any]],
        step_index: int,
        tool_index: int,
        call_id: str,
        name: str,
    ) -> dict[str, Any]:
        """Adopt a terminal external approval exactly once."""
        lease = self._require_lease()
        step = _nonnegative_index(step_index, "step_index")
        tool = _nonnegative_index(tool_index, "tool_index")
        attempt = tool_attempt_id(self.run_id, step, tool, call_id)
        persisted = project_continuation(self._current_persisted_snapshot())
        for receipt in persisted.get("completed_tool_receipts", []):
            if (
                receipt.get("attempt_id") == attempt
                and receipt.get("call_id") == call_id
                and receipt.get("name") == name
                and receipt.get("outcome")
                in {"executed_external", "failed_external"}
            ):
                self._snapshot = persisted
                self._recover_ambiguous_lease()
                item = self._store.get_inbox(call_id)
                if item is None:
                    raise ValueError("adopted approval receipt disappeared")
                return item
        try:
            next_lease, _checkpoint, item = (
                self._store.agent_runs.adopt_completed_approval(
                    lease,
                    history=history,
                    events=events,
                    step_index=step,
                    tool_index=tool,
                    call_id=call_id,
                    name=name,
                    now=self._now,
                )
            )
        except (AgentRunLeaseLost, AgentRunVersionConflict) as exc:
            self._lease = None
            raise self._ownership_error(
                "external approval adoption lost its lease"
            ) from exc
        self._lease = next_lease
        self._version = next_lease.version
        self._refresh_snapshot()
        return item

    def interrupt_inflight_tool(
        self,
        history: list[dict[str, Any]],
        events: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Suspend external work whose completion was not durably checkpointed."""
        self._require_lease()
        pending = self._snapshot.get("pending_tool")
        cursor = self._snapshot.get("cursor", {})
        next_snapshot = interrupt_inflight_tool_transition(
            self._snapshot, history, events
        )
        retry_class = (
            str(pending.get("retry_class"))
            if isinstance(pending, dict)
            else ""
        )
        retry_safe = retry_class == "safe"
        return self._checkpoint(
            "process_interrupted" if retry_safe else "tool_outcome_unknown",
            next_snapshot,
            payload={
                "status": "interrupted",
                "phase": next_snapshot.get("cursor", {}).get("phase"),
                "step_index": int(cursor.get("step_index", 0)),
                "tool_index": int(cursor.get("next_tool_index", 0)),
                "call_id": (
                    pending.get("call_id")
                    if isinstance(pending, dict)
                    else None
                ),
                "name": pending.get("name") if isinstance(pending, dict) else None,
                "retry_class": retry_class,
            },
            state="interrupted",
        )

    def interrupt_approved_inflight_tool(
        self,
        history: list[dict[str, Any]],
        events: list[dict[str, Any]],
        *,
        claimant: str,
    ) -> dict[str, Any]:
        """Atomically mark an approved external effect unknown on both records."""
        lease = self._require_lease()
        pending = self._snapshot.get("pending_tool")
        cursor = self._snapshot.get("cursor", {})
        if (
            not isinstance(pending, dict)
            or pending.get("retry_class") != "consequential"
        ):
            raise ValueError("approved interruption requires a consequential tool")
        next_snapshot = interrupt_inflight_tool_transition(
            self._snapshot, history, events
        )
        try:
            next_lease, _checkpoint, receipt = (
                self._store.agent_runs.interrupt_approved_tool(
                    lease,
                    next_snapshot,
                    approval_id=str(pending["call_id"]),
                    claimant=claimant,
                    payload={
                        "status": "interrupted",
                        "phase": "review_required",
                        "step_index": int(cursor.get("step_index", 0)),
                        "tool_index": int(cursor.get("next_tool_index", 0)),
                        "call_id": pending["call_id"],
                        "name": pending["name"],
                        "retry_class": "consequential",
                    },
                    now=self._now,
                )
            )
        except (AgentRunLeaseLost, AgentRunVersionConflict) as exc:
            self._lease = None
            raise self._ownership_error(
                "approved tool interruption lost its lease or claim"
            ) from exc
        self._lease = next_lease
        self._version = lease.version + 1
        self._refresh_snapshot()
        return receipt

    @classmethod
    def resume_resolved_approval(
        cls,
        store: ConversationStore,
        run_id: str,
        interaction_id: str,
        max_steps: int,
        owner_id: str | None = None,
        now: datetime | None = None,
        lease_seconds: float = EXECUTION_LEASE_SECONDS,
    ) -> AgentRunExecution:
        interaction = _safe_boundary_text(
            interaction_id, MAX_SAFE_ID, "interaction_id"
        )
        run = store.get_agent_run(run_id)
        if run is None:
            raise KeyError(run_id)
        snapshot = project_continuation(run.get("continuation"))
        pending = snapshot.get("pending_interaction")
        if pending != {"kind": "approval", "id": interaction}:
            raise AgentRunExecutionOwnershipError(
                f"Agent Run {run_id} has no matching resolved approval"
            )
        stored_identity = snapshot.get("identity")
        if not isinstance(stored_identity, dict):
            raise AgentRunExecutionOwnershipError(
                f"Agent Run {run_id} has no reconstructable identity"
            )
        identity = TurnIdentity(
            session_id=str(run["session_id"]),
            run_id=run_id,
            message_id=str(stored_identity["message_id"]),
            part_id=str(stored_identity["part_id"]),
        )
        duration = _execution_lease_seconds(lease_seconds)
        owner = owner_id or f"execution_{uuid.uuid4().hex}"
        try:
            acquired = store.agent_runs.acquire_resolved_waiting_lease(
                run_id,
                owner,
                int(run["version"]),
                interaction,
                duration,
                now=now,
            )
        except AgentRunVersionConflict as exc:
            raise AgentRunExecutionOwnershipError(
                f"Agent Run {run_id} resolved approval lease was fenced"
            ) from exc
        if acquired is None:
            raise AgentRunExecutionOwnershipError(
                f"Agent Run {run_id} resolved approval is not available for reacquisition"
            )
        execution = cls(
            store,
            identity,
            owner,
            acquired.lease,
            snapshot,
            max_steps,
            now,
            duration,
        )
        execution._refresh_snapshot()
        persisted_decision = execution._snapshot.get("resolved_approval", {}).get(
            "decision"
        )
        if persisted_decision != acquired.decision:
            execution._release_after_start_failure()
            raise AgentRunExecutionOwnershipError(
                f"Agent Run {run_id} approval decision binding changed"
            )
        execution._resolved_decision = acquired.decision
        return execution

    @classmethod
    def resume_closed_approval(
        cls,
        store: ConversationStore,
        run_id: str,
        interaction_id: str,
        closed_state: str,
        max_steps: int,
        owner_id: str | None = None,
        now: datetime | None = None,
        lease_seconds: float = EXECUTION_LEASE_SECONDS,
    ) -> AgentRunExecution:
        interaction = _safe_boundary_text(
            interaction_id, MAX_SAFE_ID, "interaction_id"
        )
        run = store.get_agent_run(run_id)
        if run is None:
            raise KeyError(run_id)
        snapshot = project_continuation(run.get("continuation"))
        stored_identity = snapshot.get("identity")
        if (
            snapshot.get("pending_interaction")
            != {"kind": "approval", "id": interaction}
            or not isinstance(stored_identity, dict)
        ):
            raise AgentRunExecutionOwnershipError(
                f"Agent Run {run_id} has no matching closed approval"
            )
        identity = TurnIdentity(
            session_id=str(run["session_id"]),
            run_id=run_id,
            message_id=str(stored_identity["message_id"]),
            part_id=str(stored_identity["part_id"]),
        )
        duration = _execution_lease_seconds(lease_seconds)
        owner = owner_id or f"execution_{uuid.uuid4().hex}"
        lease = store.agent_runs.acquire_closed_waiting_lease(
            run_id,
            owner,
            int(run["version"]),
            interaction,
            closed_state,
            duration,
            now=now,
        )
        if lease is None:
            raise AgentRunExecutionOwnershipError(
                f"Agent Run {run_id} closed approval is unavailable"
            )
        execution = cls(
            store, identity, owner, lease, snapshot, max_steps, now, duration
        )
        execution._refresh_snapshot()
        return execution

    @classmethod
    def resume_external_completion(
        cls,
        store: ConversationStore,
        run_id: str,
        interaction_id: str,
        max_steps: int,
        owner_id: str | None = None,
        now: datetime | None = None,
        lease_seconds: float = EXECUTION_LEASE_SECONDS,
    ) -> AgentRunExecution:
        interaction = _safe_boundary_text(
            interaction_id, MAX_SAFE_ID, "interaction_id"
        )
        run = store.get_agent_run(run_id)
        if run is None:
            raise KeyError(run_id)
        snapshot = project_continuation(run.get("continuation"))
        stored_identity = snapshot.get("identity")
        if not isinstance(stored_identity, dict):
            raise AgentRunExecutionOwnershipError(
                f"Agent Run {run_id} has no external completion identity"
            )
        identity = TurnIdentity(
            session_id=str(run["session_id"]),
            run_id=run_id,
            message_id=str(stored_identity["message_id"]),
            part_id=str(stored_identity["part_id"]),
        )
        duration = _execution_lease_seconds(lease_seconds)
        owner = owner_id or f"execution_{uuid.uuid4().hex}"
        try:
            lease = store.agent_runs.acquire_external_completion_lease(
                run_id,
                owner,
                int(run["version"]),
                interaction,
                duration,
                now=now,
            )
        except AgentRunVersionConflict as exc:
            raise AgentRunExecutionOwnershipError(
                f"Agent Run {run_id} external completion was fenced"
            ) from exc
        if lease is None:
            raise AgentRunExecutionOwnershipError(
                f"Agent Run {run_id} external completion is unavailable"
            )
        execution = cls(
            store, identity, owner, lease, snapshot, max_steps, now, duration
        )
        execution._refresh_snapshot()
        return execution

    def approval_resolved(
        self,
        history: list[dict[str, Any]],
        events: list[dict[str, Any]],
        interaction_id: str,
    ) -> dict[str, Any]:
        self._require_lease()
        interaction = _safe_boundary_text(
            interaction_id, MAX_SAFE_ID, "interaction_id"
        )
        decision = self._snapshot.get("resolved_approval", {}).get("decision")
        if decision not in {"allow", "deny"}:
            raise ValueError("approval decision is not bound to this execution")
        next_snapshot = approval_resolved_transition(
            self._snapshot, history, events, interaction
        )
        continuation = {
            **next_snapshot,
            "pending_interaction": None,
            "resolved_approval": None,
        }
        if "pending_tool" not in next_snapshot:
            continuation["pending_tool"] = None
        metadata = self._checkpoint(
            "approval_resolved",
            continuation,
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
        error_class: str | None = None,
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
        if error_class is not None:
            terminal_result["class"] = _safe_boundary_text(
                error_class, 128, "error_class"
            )
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
                self._lease_seconds,
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


def _execution_lease_seconds(value: int | float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("lease_seconds must be a positive number")
    seconds = float(value)
    if seconds <= 0 or seconds > MAX_LEASE_SECONDS:
        raise ValueError(
            f"lease_seconds must be between 0 and {MAX_LEASE_SECONDS}"
        )
    return seconds
