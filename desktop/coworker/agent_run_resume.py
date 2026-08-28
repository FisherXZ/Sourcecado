"""What a restarting process may pick up, and what it must leave alone.

Restart is two questions asked in order, and the order is the whole safety
argument.

First, which leases are free? `AgentRunRepository.reclaim_dead_owner_leases`
already answers that, and it answers it from kernel facts: a lease is taken
back when it has expired, or when the `flock` its owner held for the life of
its process has been released. A live owner is never stolen from, and an owner
whose liveness cannot be proven is left alone rather than guessed at.

Second, of the work that is now free, which is safe to continue? That is this
module. It reads the run's own record and reaches one of five verdicts, and it
never runs anything. Deciding is separated from doing so that the decision can
be tested, logged, and shown to an operator on its own.

The verdict that matters most is the one that refuses. An external effect that
was dispatched and never reported back is not resumed and not retried: it is
quarantined, because the send may already have reached a real person. See
`coworker/agent_run_approval.py` for the fence that holds it there.

Nothing here is wired into the running application. The process that starts the
sidecar is where `restart()` belongs, and that wiring is later work.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Iterable

from coworker.agent_run_approval import (
    is_replay_safe,
    quarantined,
    unreported,
)
from coworker.agent_run_owner import RunOwner
from coworker.agent_run_state import is_terminal, is_waiting
from coworker.agent_runs import AGENT_RUN_STATES, TERMINAL_AGENT_RUN_STATES

# Every state a restart might have to think about. Terminal runs never move
# again, so they are not among them.
OPEN_AGENT_RUN_STATES = AGENT_RUN_STATES - TERMINAL_AGENT_RUN_STATES


class ResumeAction(StrEnum):
    """What a restarting process may do with one run."""

    # Safe incomplete work. Continue under the same run identity.
    RESUME = "resume"
    # The final answer was generated and recorded but never delivered. Deliver
    # what is on record; do not ask the model again.
    DELIVER = "deliver"
    # An external effect was dispatched and never reported. Move it into review.
    QUARANTINE = "quarantine"
    # Already quarantined, or too unclear to touch. A person owns this.
    REVIEW = "review"
    # Finished, parked on a person, or owned by a process that is still working.
    NOTHING = "nothing"


@dataclass(frozen=True)
class ResumeVerdict:
    run_id: str
    action: ResumeAction
    reason: str
    effect_id: str | None = None
    tool_name: str | None = None
    step: int | None = None
    cut_point: str | None = None


@dataclass(frozen=True)
class RestartOutcome:
    """What one restart decided, and what it actually did about it.

    `verdicts` is the plan as it stood before anything was quarantined, so a
    run listed as `QUARANTINE` here appears in `quarantined` if the quarantine
    committed. Reclassifying it would report `REVIEW` and lose the fact that
    this restart is what noticed.
    """

    verdicts: tuple[ResumeVerdict, ...]
    quarantined: tuple[dict[str, Any], ...]

    @property
    def resumable(self) -> tuple[ResumeVerdict, ...]:
        return tuple(
            verdict
            for verdict in self.verdicts
            if verdict.action in {ResumeAction.RESUME, ResumeAction.DELIVER}
        )


_RESUME_REASONS = {
    "run_started": "no_work_was_recorded",
    "model_pending": "model_never_completed",
    "model_completed": "model_completed_before_the_next_step",
    "tool_pending": "tool_never_completed",
    "tool_completed": "tool_completed_before_the_next_step",
    "approval_resolved": "approval_resolved_before_work_resumed",
}


def classify_resume(
    run: dict[str, Any],
    checkpoints: list[dict[str, Any]],
    effects: Iterable[dict[str, Any]],
) -> ResumeVerdict:
    """Decide what may be done with one run. Runs nothing, writes nothing.

    Call this after reclaim. Before reclaim a dead owner's lease is still on the
    row and reads as held, so the verdict is `NOTHING` -- which errs toward
    leaving work alone, the direction a mistake here has to err in.
    """
    run_id = str(run.get("run_id"))
    state = str(run.get("current_state") or "")
    items = list(effects)
    cut = _cut_point(checkpoints)
    kind = str(cut.get("kind")) if cut else None
    payload = dict(cut.get("payload") or {}) if cut else {}
    step = payload.get("step")

    def verdict(action: ResumeAction, reason: str, **extra: Any) -> ResumeVerdict:
        return ResumeVerdict(
            run_id=run_id,
            action=action,
            reason=reason,
            step=step if isinstance(step, int) else None,
            cut_point=kind,
            **extra,
        )

    if is_terminal(state):
        return verdict(ResumeAction.NOTHING, "the run already finished")
    # After reclaim, a lease still on the row belongs to a process that is
    # either alive or not provably dead. Neither is ours to take.
    if run.get("lease_owner") is not None:
        return verdict(ResumeAction.NOTHING, "another process holds the lease")

    # Effects outrank everything below, including the run's own state. What the
    # run was doing matters less than whether something already left the machine.
    held = quarantined(items)
    if held:
        return verdict(
            ResumeAction.REVIEW,
            "an external effect is quarantined and only a person settles it",
            effect_id=str(held[0]["effect_id"]),
            tool_name=str(held[0]["tool_name"]),
        )
    open_effects = unreported(items)
    if open_effects:
        return verdict(
            ResumeAction.QUARANTINE,
            "an external effect was dispatched and never reported back",
            effect_id=str(open_effects[0]["effect_id"]),
            tool_name=str(open_effects[0]["tool_name"]),
        )

    if is_waiting(state):
        return verdict(ResumeAction.NOTHING, "the run is parked on a person")

    # A consequential tool that never opened an effect record left no way to
    # tell whether it ran. Fail closed: this is the same unknown, one level up.
    if kind == "tool_pending":
        tool_name = payload.get("tool_name")
        if tool_name is not None and not is_replay_safe(str(tool_name)):
            known = {str(effect["effect_id"]) for effect in items}
            if str(payload.get("attempt_id") or "") not in known:
                return verdict(
                    ResumeAction.REVIEW,
                    "a consequential tool was pending with no effect record, so "
                    "whether it ran cannot be established",
                    tool_name=str(tool_name),
                )
        if tool_name is not None:
            return verdict(
                ResumeAction.RESUME,
                _RESUME_REASONS["tool_pending"],
                tool_name=str(tool_name),
            )

    # The answer exists and was recorded; only its delivery is unaccounted for.
    # Asking the model again would spend tokens to produce a different answer.
    if run.get("terminal_result") is not None:
        return verdict(
            ResumeAction.DELIVER,
            "the final result is on record and its delivery is not",
        )

    return verdict(
        ResumeAction.RESUME,
        _RESUME_REASONS.get(kind or "", f"{kind} left the run incomplete"),
    )


def plan_restart(
    repo: Any, *, now: datetime | None = None, limit: int = 500
) -> list[ResumeVerdict]:
    """Reclaim what is provably free, then say what may be picked up.

    Reclaim runs first because a dead owner's lease is still on its row until it
    does. `reclaim_dead_owner_leases` never takes a lease from a live owner, so
    whatever is still held afterwards is genuinely held.
    """
    repo.reclaim_dead_owner_leases(now)
    return [
        classify_resume(
            run,
            repo.list_checkpoints(run["run_id"]),
            repo.list_effects(run["run_id"]),
        )
        for run in repo.list_runs(states=sorted(OPEN_AGENT_RUN_STATES), limit=limit)
    ]


def quarantine_unreported_effect(
    repo: Any,
    owner: RunOwner,
    verdict: ResumeVerdict,
    *,
    now: datetime | None = None,
) -> Any:
    """Take the run just long enough to move its unreported effect into review.

    Returns None when the lease could not be taken. Someone else is working that
    run, and a restart never forces its way past a live owner.
    """
    if verdict.action is not ResumeAction.QUARANTINE or verdict.effect_id is None:
        raise ValueError(f"{verdict.action} is not a quarantine verdict")
    lease = repo.acquire_lease(verdict.run_id, owner, now=now)
    if lease is None:
        return None
    return repo.quarantine_effect(
        lease, verdict.effect_id, reason=verdict.reason, now=now
    )


def restart(
    repo: Any,
    owner: RunOwner,
    *,
    now: datetime | None = None,
    limit: int = 500,
) -> RestartOutcome:
    """One startup pass: reclaim, classify, and quarantine what must not replay.

    Quarantining is the only write this makes, because it is the only decision
    that cannot wait. An effect nobody knows the outcome of has to stop being
    mistakable for work in progress before anything else looks at the run.
    Resuming and delivering are left to the caller.
    """
    verdicts = plan_restart(repo, now=now, limit=limit)
    held: list[dict[str, Any]] = []
    for verdict in verdicts:
        if verdict.action is not ResumeAction.QUARANTINE:
            continue
        commit = quarantine_unreported_effect(repo, owner, verdict, now=now)
        if commit is not None:
            held.append(commit.effect)
    return RestartOutcome(verdicts=tuple(verdicts), quarantined=tuple(held))


def _cut_point(checkpoints: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The last thing the run actually did.

    Reclaim appends `process_interrupted` to say the owner is gone. That is a
    record of the crash, not of the work, so the step to reason about is the
    last one before it.
    """
    for item in reversed(checkpoints):
        if str(item.get("kind")) != "process_interrupted":
            return item
    return None
