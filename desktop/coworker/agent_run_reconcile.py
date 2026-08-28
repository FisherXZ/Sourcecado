"""When the inbox and the run store disagree about one external effect.

At-most-once has two halves and they answer different questions.

`ConversationStore.decide_and_claim_inbox_execution` answers *may this run at
all*. One claiming UPDATE, one winner, so an approved send is attempted once.

The run store answers *what happened when it did*. A dispatch commits before
the call and an outcome commits after it, so a process that dies in between
leaves an effect nobody holds the outcome of.

They compose and must not be duplicated. The claim is not re-implemented here
and nothing in this module hands out a second permission to run.

## The rule

A restart writes to both stores, independently, and they can disagree.

`ConversationStore._reconcile_orphaned_inbox_executions` sets every `executing`
approval to `interrupted`, and `reap_overdue_inbox` does the same to a stale
claim. That is the inbox saying "authorized, outcome unknown" -- but it says it
from the *claim's* point of view: it knows a claimant vanished, and nothing
more. It cannot know whether the call was made.

`agent_run_resume.restart` quarantines an effect that was dispatched and never
reported. That is the run store saying "the call may have gone out", and it
knows because the dispatch record committed before the call.

**When they disagree, the run store is the fence of record.** It is the only
one of the two whose write ordering is tied to the external call, so it is the
only one whose silence carries information. An operator is shown `ambiguous`,
not `interrupted`, and settling it is a person's write, not a retry.

The rule runs the other way too. An effect the run store recorded as
`succeeded` is a send that happened, whatever the inbox concluded about its
claimant. Reporting that as an interruption would invite a second send.
"""

from __future__ import annotations

from typing import Any, Iterable, Protocol

from coworker.agent_run_approval import (
    EffectStatus,
    OPERATOR_OUTCOMES,
    external_effect_evidence,
)

# What the inbox says when its claimant vanished: authorized, outcome unknown.
# These are the claims the run store may override, and the only ones.
UNSETTLED_INBOX_STATUSES = frozenset({"interrupted", "executing", "pending"})
# What a person may choose for a quarantined effect. The store refuses the rest.
OPERATOR_DECISIONS = tuple(sorted(str(value) for value in OPERATOR_OUTCOMES))
FENCE_OF_RECORD = "agent_run_store"


class ApprovalReader(Protocol):
    """The owner-native approval record, read only. Nothing here writes it."""

    def get_inbox(self, item_id: str) -> dict[str, Any] | None: ...


def reconciled_status(
    effect: dict[str, Any] | None, approval: dict[str, Any] | None
) -> str:
    """What one external effect actually is, when the two stores disagree.

    The run store wins whenever it has a record, because its writes bracket the
    external call and the inbox's do not. With no effect record there is nothing
    to override and the inbox's own account stands.
    """
    if effect is not None:
        return str(effect.get("status") or "")
    if approval is None:
        return "unknown"
    return str(approval.get("execution_status") or "pending")


def supersedes_inbox(
    effect: dict[str, Any] | None, approval: dict[str, Any] | None
) -> bool:
    """Whether the run store is overriding something the inbox already claimed."""
    if effect is None or approval is None:
        return False
    claimed = str(approval.get("execution_status") or "")
    return claimed != reconciled_status(effect, approval)


def needs_a_person(effect: dict[str, Any] | None) -> bool:
    return effect is not None and str(effect.get("status")) == EffectStatus.AMBIGUOUS


def _approval_view(approval: dict[str, Any] | None) -> dict[str, Any] | None:
    """The few approval fields an operator needs to settle an effect.

    The resource is the binding the director already read -- recipient, subject,
    account -- and never the body. `approval_resource` bounded it when the
    approval was parked and nothing widens it here.
    """
    if approval is None:
        return None
    return {
        "id": str(approval.get("id") or ""),
        "name": str(approval.get("name") or ""),
        "state": approval.get("state"),
        "decision": approval.get("decision"),
        "execution_status": approval.get("execution_status"),
        "session_id": approval.get("session_id"),
        "run_id": approval.get("run_id"),
        "requested_at": approval.get("requested_at"),
        "resource": approval.get("resource"),
    }


def review_row(
    effect: dict[str, Any],
    approval: dict[str, Any] | None,
    run: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One line of the operator's review queue, both stores already reconciled."""
    return {
        "effect_id": str(effect.get("effect_id") or ""),
        "run_id": str(effect.get("run_id") or ""),
        "tool_name": str(effect.get("tool_name") or ""),
        "approval_id": effect.get("approval_id"),
        "replay_class": effect.get("replay_class"),
        "dispatched_at": effect.get("dispatched_at"),
        "reason": effect.get("reason"),
        "status": reconciled_status(effect, approval),
        "fence_of_record": FENCE_OF_RECORD,
        "supersedes_inbox": supersedes_inbox(effect, approval),
        "inbox_claim": (
            None if approval is None else approval.get("execution_status")
        ),
        "needs_a_person": needs_a_person(effect),
        # The one Evidence value the run store can decide on its own. A
        # surface that shows a receipt and this queue reads the same word for
        # the same fact.
        "evidence": str(external_effect_evidence([effect]) or ""),
        "decisions": list(OPERATOR_DECISIONS),
        "approval": _approval_view(approval),
        "session_id": (
            None if run is None else run.get("session_id")
        ),
        "person_id": None if run is None else run.get("person_id"),
        "run_state": None if run is None else run.get("current_state"),
    }


def review_queue(
    repository: Any,
    approvals: ApprovalReader | None = None,
    *,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Every effect nobody knows the outcome of, joined to its approval.

    `list_quarantined_effects` is the queue. This adds the two things a person
    needs to act: what they authorized, and which run it belonged to.
    """
    rows: list[dict[str, Any]] = []
    for effect in repository.list_quarantined_effects(limit=limit):
        approval_id = effect.get("approval_id")
        approval = (
            approvals.get_inbox(str(approval_id))
            if approvals is not None and approval_id
            else None
        )
        rows.append(review_row(effect, approval, repository.get_run(effect["run_id"])))
    return rows


def contested_approvals(
    repository: Any, approvals: Iterable[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Approvals whose inbox account the run store overrides.

    An operator surface that renders `interrupted` from the inbox alone would
    offer a retry for a send that may already have gone out. This names every
    row where that would happen.
    """
    contested: list[dict[str, Any]] = []
    for approval in approvals:
        if str(approval.get("execution_status") or "") not in UNSETTLED_INBOX_STATUSES:
            continue
        effect = effect_for_approval(repository, str(approval.get("id") or ""))
        if effect is None or not supersedes_inbox(effect, approval):
            continue
        contested.append(
            review_row(effect, approval, repository.get_run(effect["run_id"]))
        )
    return contested


def effect_for_approval(repository: Any, approval_id: str) -> dict[str, Any] | None:
    """The run store's record of one approved effect, if it opened one.

    An approval's effect may sit on a run other than the one the approval was
    parked from: an approval decided from the operator surface is executed by
    the server under its own run. So this asks by approval, not by run.
    """
    if not approval_id:
        return None
    found = repository.effects_for_approval(approval_id)
    return found[-1] if found else None
