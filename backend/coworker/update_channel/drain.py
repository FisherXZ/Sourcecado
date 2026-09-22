"""Whether an update may proceed right now. Reads only; decides nothing else.

The natural updater stops the app, swaps the bundle, and starts it again. On a
machine that sends real mail to real people, that sequence is the bug. An
external effect that has been dispatched and has not reported back is a window
where nobody knows whether the send happened. Killing the process closes that
window on "unknown" forever, and a restart that resumes the run can put a
second copy of the same message in a real inbox.

So this module answers one question -- may an update proceed -- with three
answers, and "proceed anyway" is not among them:

- `READY`: every run is finished, parked on a person, or free of any external
  effect that has not reported. Whatever is left is restart-safe: it lives in
  the run store, and `agent_run_resume.restart()` picks it up after the new
  build launches.
- `ACTIVE_WORK` or `UNSETTLED_EFFECT`: wait. Both resolve on their own when the
  process that owns the work finishes. If the wait runs out, the update refuses;
  it never forces.
- `QUARANTINED_EFFECT`: refuse now, and do not wait. An ambiguous effect is
  settled by a person and by nothing else, so waiting cannot help.

The second half of the contract is what this module does *not* do. It performs
no writes at all. It does not quarantine an unreported effect -- that is
`agent_run_resume.restart()`'s decision after a crash has actually happened --
and it does not resolve a quarantine, which is a person's decision and carries
their name. An updater that "tidies up" the run store on the way past is how a
machine ends up recording an outcome nobody observed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Callable

from coworker.agent_run_approval import unreported
from coworker.agent_run_resume import OPEN_AGENT_RUN_STATES
from coworker.agent_run_state import is_waiting

DEFAULT_TIMEOUT = 120.0
DEFAULT_POLL = 1.0
DEFAULT_LIMIT = 500


class DrainStatus(StrEnum):
    READY = "ready"
    ACTIVE_WORK = "active_work"
    UNSETTLED_EFFECT = "unsettled_effect"
    QUARANTINED_EFFECT = "quarantined_effect"


# Worst last. The reported status is the worst blocker found.
_SEVERITY: dict[DrainStatus, int] = {
    DrainStatus.READY: 0,
    DrainStatus.ACTIVE_WORK: 1,
    DrainStatus.UNSETTLED_EFFECT: 2,
    DrainStatus.QUARANTINED_EFFECT: 3,
}

# The two states that clear on their own when the owning process finishes.
# A quarantine is not among them: only a person moves it.
WAITABLE = frozenset({DrainStatus.ACTIVE_WORK, DrainStatus.UNSETTLED_EFFECT})


@dataclass(frozen=True)
class DrainBlocker:
    run_id: str
    status: DrainStatus
    reason: str
    effect_id: str | None = None
    tool_name: str | None = None


@dataclass(frozen=True)
class DrainAssessment:
    status: DrainStatus
    blockers: tuple[DrainBlocker, ...] = ()
    continuable: tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        return self.status is DrainStatus.READY

    @property
    def may_wait(self) -> bool:
        """Whether waiting could change this answer. A quarantine never does."""
        return self.status in WAITABLE


def _lease_is_live(run: dict[str, Any], now: datetime) -> bool:
    """A lease that has not expired belongs to a process that may still be working.

    An expired lease is the run store's own definition of reclaimable, and the
    run behind it is durable, so it is restart-safe rather than active. Proving
    an owner dead is `reclaim_dead_owner_leases`' job and it writes; this module
    does not, so it uses the expiry the store already records.
    """
    if run.get("lease_owner") is None:
        return False
    expires = run.get("lease_expires_at")
    if not expires:
        return False
    try:
        return datetime.fromisoformat(str(expires)) > now
    except ValueError:
        # An unreadable expiry is not proof the lease is free.
        return True


def assess_drain(
    repo: Any, *, now: datetime | None = None, limit: int = DEFAULT_LIMIT
) -> DrainAssessment:
    """Report what would stop an update, without touching anything.

    Quarantined effects are swept across every run, not just open ones: an
    effect record outlives the run it belongs to, and a run can reach `partial`
    with a person still owing a decision on what left the machine.
    """
    moment = now or datetime.now(UTC)
    blockers: list[DrainBlocker] = []
    continuable: list[str] = []

    held: dict[str, DrainBlocker] = {}
    for effect in repo.list_quarantined_effects(limit=limit):
        run_id = str(effect["run_id"])
        held.setdefault(
            run_id,
            DrainBlocker(
                run_id=run_id,
                status=DrainStatus.QUARANTINED_EFFECT,
                reason=(
                    "an external effect is quarantined and only a person can "
                    "settle it"
                ),
                effect_id=str(effect["effect_id"]),
                tool_name=str(effect["tool_name"]),
            ),
        )
    blockers.extend(held.values())

    for run in repo.list_runs(states=sorted(OPEN_AGENT_RUN_STATES), limit=limit):
        run_id = str(run["run_id"])
        if run_id in held:
            continue
        open_effects = unreported(repo.list_effects(run_id))
        if open_effects:
            effect = open_effects[0]
            blockers.append(
                DrainBlocker(
                    run_id=run_id,
                    status=DrainStatus.UNSETTLED_EFFECT,
                    reason=(
                        "an external effect was dispatched and has not reported "
                        "back, so whether it happened is not yet known"
                    ),
                    effect_id=str(effect["effect_id"]),
                    tool_name=str(effect["tool_name"]),
                )
            )
            continue
        if is_waiting(str(run["current_state"])):
            continuable.append(run_id)
            continue
        if _lease_is_live(run, moment):
            blockers.append(
                DrainBlocker(
                    run_id=run_id,
                    status=DrainStatus.ACTIVE_WORK,
                    reason="a process is working this run",
                )
            )
            continue
        continuable.append(run_id)

    if not blockers:
        return DrainAssessment(
            status=DrainStatus.READY, continuable=tuple(continuable)
        )
    ordered = tuple(sorted(blockers, key=lambda item: -_SEVERITY[item.status]))
    return DrainAssessment(
        status=ordered[0].status,
        blockers=ordered,
        continuable=tuple(continuable),
    )


def wait_for_drain(
    repo: Any,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    poll: float = DEFAULT_POLL,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    now: datetime | None = None,
    limit: int = DEFAULT_LIMIT,
) -> DrainAssessment:
    """Poll until the work drains, the deadline passes, or waiting is pointless.

    Returns the last assessment either way. The caller refuses on anything that
    is not `READY`; there is no argument this function can be given that makes
    it force an update through.
    """
    deadline = clock() + max(0.0, float(timeout))
    while True:
        assessment = assess_drain(repo, now=now, limit=limit)
        if assessment.ready or not assessment.may_wait:
            return assessment
        if clock() >= deadline:
            return assessment
        sleep(max(0.0, float(poll)))
