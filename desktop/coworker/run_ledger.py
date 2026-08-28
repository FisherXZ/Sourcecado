"""The read side of the durable Agent Run store, plus its retention bound.

`RunLedger` is the only thing that turns run rows into receipts an operator can
open, and the only place the retention bound is applied. It holds no lease and
cannot acquire one. Its single write is `prune_checkpoints`, which deletes
checkpoint rows and touches `agent_runs` never.

Authority never comes from here. The run store records which approval ids a run
touched; it never records whether the owner said yes. Every decision in a
receipt is resolved against the owner-native approval receipt, so an approval id
this module cannot verify renders as `missing` and never as an allowance.

The receipt shape itself lives in `coworker/run_receipt.py`. Person-file events
live in `coworker/ledger.py`, which is a different thing entirely.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Iterable, Protocol

from coworker.agent_run_repository import AgentRunRepository
from coworker.agent_runs import AGENT_RUN_STATES, RUN_TRIGGERS
from coworker.run_receipt import (
    approval_ids,
    build_receipt,
    parse_moment,
    run_summary,
)

# Step detail for a long-finished run is dropped after this; identity, sources,
# artifacts, approvals, usage, and outcome live on the run row and stay.
CHECKPOINT_RETENTION_DAYS = 30
DEFAULT_QUERY_LIMIT = 50
MAX_QUERY_LIMIT = 200


class ApprovalReceipts(Protocol):
    """The owner-native approval record. The ledger reads it and never writes it."""

    def get_inbox(self, item_id: str) -> dict[str, Any] | None: ...


class RunLedger:
    """Read-only projection over the durable run store, plus its retention bound."""

    def __init__(
        self,
        repository: AgentRunRepository,
        *,
        approvals: ApprovalReceipts | None = None,
    ) -> None:
        self.repository = repository
        self.approvals = approvals

    def receipt(self, run_id: str) -> dict[str, Any] | None:
        run = self.repository.get_run(run_id)
        if run is None:
            return None
        checkpoints = self.repository.list_checkpoints(run_id)
        return build_receipt(
            run, checkpoints, approvals=self._resolve_approvals(run, checkpoints)
        )

    def query(
        self,
        *,
        run_id: str | None = None,
        session_id: str | None = None,
        person_id: str | None = None,
        trigger: str | None = None,
        statuses: Iterable[str] | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = DEFAULT_QUERY_LIMIT,
    ) -> dict[str, Any]:
        """Pointers, not evidence: one bounded page of runs to open."""
        wanted = _validated_statuses(statuses)
        if trigger is not None and trigger not in RUN_TRIGGERS:
            raise ValueError(f"unknown run trigger {trigger!r}")
        bounded = _bounded_limit(limit)
        truncated = False
        if run_id:
            found = self.repository.get_run(run_id)
            rows = [found] if found is not None else []
        else:
            rows = self.repository.list_runs(
                session_id=session_id,
                person_id=person_id,
                trigger=trigger,
                states=wanted,
                created_after=since,
                created_before=until,
                limit=bounded + 1,
            )
            truncated = len(rows) > bounded
            rows = rows[:bounded]
        rows = [
            row
            for row in rows
            if _matches(row, session_id, person_id, trigger, wanted, since, until)
        ]
        return {
            "runs": [run_summary(row) for row in rows],
            "limit": bounded,
            "truncated": truncated,
        }

    def enforce_retention(
        self,
        *,
        max_age_days: int | float = CHECKPOINT_RETENTION_DAYS,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Drop step detail for long-finished runs. Identity is never removed.

        A run whose record marks a hole, or carries a checkpoint this build
        cannot read, keeps its detail: retention must never destroy the
        evidence that evidence is missing.
        """
        moment = now or datetime.now(UTC)
        cutoff = moment - timedelta(days=float(max_age_days))
        pruned = self.repository.prune_checkpoints(finished_before=cutoff)
        return {
            "cutoff": cutoff.isoformat(),
            "pruned_runs": len(pruned),
            "run_ids": pruned,
        }

    def _resolve_approvals(
        self, run: dict[str, Any], checkpoints: list[dict[str, Any]]
    ) -> dict[str, dict[str, Any] | None] | None:
        if self.approvals is None:
            return None
        return {
            approval_id: self.approvals.get_inbox(approval_id)
            for approval_id in approval_ids(run, checkpoints)
        }


def _matches(
    run: dict[str, Any],
    session_id: str | None,
    person_id: str | None,
    trigger: str | None,
    statuses: tuple[str, ...],
    since: datetime | None,
    until: datetime | None,
) -> bool:
    """Re-check every filter on the way out, so an exact id cannot bypass one."""
    if session_id is not None and str(run.get("session_id")) != session_id:
        return False
    if person_id is not None and run.get("person_id") != person_id:
        return False
    if trigger is not None and str(run.get("trigger")) != trigger:
        return False
    if statuses and str(run.get("current_state")) not in statuses:
        return False
    created = parse_moment(run.get("created_at"))
    if created is None:
        return since is None and until is None
    if since is not None and created < since:
        return False
    if until is not None and created > until:
        return False
    return True


def _validated_statuses(statuses: Iterable[str] | None) -> tuple[str, ...]:
    wanted = tuple(str(item) for item in (statuses or ()))
    unknown = [item for item in wanted if item not in AGENT_RUN_STATES]
    if unknown:
        raise ValueError(f"unknown run status {unknown[0]!r}")
    return wanted


def _bounded_limit(limit: int) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        return DEFAULT_QUERY_LIMIT
    return max(1, min(value, MAX_QUERY_LIMIT))


