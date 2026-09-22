"""Where an Agent Run meets the work that produces it.

`coworker/agent_run_repository.py` can fence an external effect. Nothing called
it. This module is the seam that does, and it exists so the ordering contract
is written once instead of at every call site:

    dispatch commits BEFORE the call, the outcome commits AFTER it.

`guarded_call` is the only way this package makes a consequential call, and it
encodes that order in control flow. A caller cannot get it backwards without
deleting the function, which is what `tests/test_agent_run_fence_mutations.py`
does on purpose to prove the guard is load-bearing.

Which tools this applies to is not decided here and is not a list anyone typed.
`agent_run_approval.replay_class` reads `permissions.RETRY_SAFE`, and everything
outside that set is consequential -- including a tool the permission module has
never heard of. `needs_fence` is that question and nothing else.

## Failing closed, and what that costs

Two kinds of write happen against a run, and they fail differently on purpose.

A **checkpoint** is a record of progress. Losing the lease under one means this
process no longer owns the run, so it stops writing -- but a chat turn that is
already mid-flight is not killed for it. `note` closes the context instead.

A **fence write** is the thing the effect record exists for. A closed context
refuses to dispatch, and `guarded_call` refuses to make the call: an external
effect Sourcecado cannot record durably is an external effect it does not make.
That is the whole trade. A run store that goes unwritable stops sends; it never
lets one through unrecorded.

## What a raised call means

A tool that returns says what happened. A tool that raises out of the call --
a cancelled turn most of all, where an operator pressed stop while a send was
in flight -- says nothing at all, and `record_effect_outcome` has no value for
"nothing at all". So the guard quarantines and re-raises. The effect becomes a
person's to settle, which is the same place a crash in that window lands it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Awaitable, Callable

from coworker.agent_run_approval import replay_class, ReplayClass
from coworker.agent_run_owner import RunOwner
from coworker.agent_run_repository import (
    AgentRunLease,
    AgentRunRepository,
)

# A turn holds its run for as long as one model call plus one tool call can
# plausibly take. Shorter, and an ordinary slow provider stream loses the lease
# mid-turn; longer buys nothing, because a dead owner is reclaimed from its
# released `flock` immediately and never has to wait for expiry.
TURN_LEASE_SECONDS = 900
# Renew when this much or less is left, so the gap a write has to survive is
# one step rather than the whole run.
RENEW_MARGIN_SECONDS = 120

_MAX_ERROR_SUMMARY = 400


class AgentRunUnavailable(RuntimeError):
    """The run store cannot fence this call, so the call is not made."""


class AgentRunEffectQuarantined(RuntimeError):
    """A consequential call raised. Whether it happened is now a person's call.

    Never swallow this into a tool failure. "It failed" and "we do not know"
    are different facts, and turning the second into the first is the mistake
    the whole run store exists to prevent.
    """

    def __init__(self, effect_id: str, tool_name: str) -> None:
        super().__init__(
            f"{tool_name} was dispatched and never reported an outcome; "
            f"effect {effect_id} is quarantined for a person to settle"
        )
        self.effect_id = effect_id
        self.tool_name = tool_name


def needs_fence(tool_name: str) -> bool:
    """Whether attempting this tool could produce a second external effect.

    Derived, never listed. `replay_class` reads `permissions.RETRY_SAFE`, so a
    tool added to the product is fenced until someone deliberately declares it
    safe to re-run.
    """
    return replay_class(tool_name) is ReplayClass.CONSEQUENTIAL


def _summary(value: Any) -> str:
    return str(value)[:_MAX_ERROR_SUMMARY]


@dataclass
class AgentRunContext:
    """One durable Agent Run, held for the life of one unit of assistant work."""

    repository: AgentRunRepository
    owner: RunOwner
    run_id: str
    lease: AgentRunLease | None
    closed_reason: str | None = None

    @classmethod
    def start(
        cls,
        repository: AgentRunRepository | None,
        owner: RunOwner | None,
        *,
        session_id: str,
        trigger: str,
        goal: str,
        run_id: str | None = None,
        person_id: str | None = None,
        provider_model_id: str | None = None,
        parent_run_id: str | None = None,
        lease_seconds: int | float = TURN_LEASE_SECONDS,
        now: datetime | None = None,
    ) -> AgentRunContext | None:
        """Open a run, or return None because this caller keeps no run store.

        Returning None is not a quiet downgrade: a context that does not exist
        refuses consequential tools exactly as a closed one does, because
        `guarded_call` is the only path to them.
        """
        if repository is None or owner is None:
            return None
        started = repository.create_run(
            session_id=session_id,
            trigger=trigger,
            goal=goal,
            owner=owner,
            run_id=run_id,
            person_id=person_id,
            provider_model_id=provider_model_id,
            parent_run_id=parent_run_id,
            lease_seconds=lease_seconds,
            now=now,
        )
        return cls(
            repository=repository,
            owner=owner,
            run_id=started.run["run_id"],
            lease=started.lease,
        )

    @classmethod
    def disarmed(
        cls,
        repository: AgentRunRepository | None,
        owner: RunOwner | None,
        *,
        reason: str,
    ) -> AgentRunContext | None:
        """A run that could not be opened, for a caller that has a run store.

        The distinction this exists for: `None` means nobody armed the fence,
        and a closed context means somebody did and it broke. `guarded_call`
        treats them differently on purpose, because a caller who supplied a run
        store and got nothing must not send, while a caller who never supplied
        one was never fenced in the first place.
        """
        if repository is None or owner is None:
            return None
        return cls(
            repository=repository,
            owner=owner,
            run_id="",
            lease=None,
            closed_reason=reason,
        )

    # --- state -----------------------------------------------------------

    @property
    def closed(self) -> bool:
        return self.closed_reason is not None

    def close(self, reason: str) -> None:
        if self.closed_reason is None:
            self.closed_reason = reason
        self.lease = None

    def _renew_if_stale(self, now: datetime | None = None) -> None:
        if self.lease is None:
            return
        moment = now or datetime.now(UTC)
        try:
            expires = datetime.fromisoformat(str(self.lease.expires_at))
        except ValueError:
            return
        if expires - moment > timedelta(seconds=RENEW_MARGIN_SECONDS):
            return
        self.lease = self.repository.renew_lease(
            self.lease, TURN_LEASE_SECONDS, now=moment
        )

    # --- checkpoints -----------------------------------------------------

    def note(
        self,
        kind: str,
        *,
        state: str | None = None,
        payload: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> bool:
        """Record progress. A failure here closes the run, never the turn.

        Losing the lease means another process may own this run, so this one
        stops writing to it. It does not follow that the operator's turn should
        die -- but it does follow that no consequential tool may run after it,
        and a closed context is what enforces that.
        """
        if self.closed or self.lease is None:
            return False
        try:
            self._renew_if_stale()
            commit = self.repository.checkpoint(
                self.lease, kind=kind, state=state, payload=payload, **kwargs
            )
        except Exception as exc:  # the run is no longer ours to write to
            self.close(f"{type(exc).__name__}: {_summary(exc)}")
            return False
        self.lease = commit.lease
        return True

    def park(self, approval_id: str, **payload: Any) -> bool:
        """Park the run on a person. The lease is released with the write."""
        return self.note(
            "waiting_approval",
            state="waiting_approval",
            payload={"approval_id": approval_id, **payload},
            approval_ids=[approval_id],
        )

    def resume(self, approval_id: str, decision: str) -> bool:
        """Come back through the approval door and pick the lease up again."""
        if self.closed:
            return False
        try:
            commit = self.repository.resume_from_approval(
                self.run_id,
                self.owner,
                approval_id=approval_id,
                decision=decision,
                lease_seconds=TURN_LEASE_SECONDS,
            )
        except Exception as exc:
            self.close(f"{type(exc).__name__}: {_summary(exc)}")
            return False
        if commit is None or commit.lease is None:
            self.close("the approval door did not return a lease")
            return False
        self.lease = commit.lease
        return True

    def finish(self, state: str, **result: Any) -> bool:
        """End the run. `terminal_result` is shape only, never the answer."""
        return self.note("terminal", state=state, terminal_result=result)

    # --- the fence -------------------------------------------------------

    def dispatch(
        self,
        *,
        tool_name: str,
        arguments: Any,
        tool_call_id: str | None = None,
        approval_id: str | None = None,
        step: int | None = None,
    ) -> str:
        """Record that an external effect is about to be attempted.

        Raises rather than degrading. A caller that cannot open an effect
        record must not make the call, so there is no return value meaning
        "carry on unfenced".
        """
        if self.closed or self.lease is None:
            raise AgentRunUnavailable(
                self.closed_reason or "this run holds no lease"
            )
        self._renew_if_stale()
        commit = self.repository.dispatch_effect(
            self.lease,
            tool_name=tool_name,
            arguments=arguments,
            tool_call_id=tool_call_id,
            approval_id=approval_id,
            step=step,
        )
        self.lease = commit.lease
        return str(commit.effect["effect_id"])

    def record(
        self,
        effect_id: str,
        *,
        ok: bool,
        outcome_ref: str | None = None,
        error_class: str | None = None,
        error_summary: str | None = None,
        duration_ms: int | None = None,
    ) -> bool:
        """Close the window on an effect this process dispatched."""
        if self.lease is None:
            return False
        try:
            commit = self.repository.record_effect_outcome(
                self.lease,
                effect_id,
                ok=ok,
                outcome_ref=outcome_ref,
                error_class=error_class,
                error_summary=(
                    _summary(error_summary) if error_summary is not None else None
                ),
                duration_ms=duration_ms,
            )
        except Exception as exc:
            self.close(f"{type(exc).__name__}: {_summary(exc)}")
            return False
        self.lease = commit.lease
        return True

    def quarantine(self, effect_id: str, *, reason: str) -> bool:
        """Hand an effect nobody knows the outcome of to a person."""
        if self.lease is None:
            return False
        try:
            self.repository.quarantine_effect(
                self.lease, effect_id, reason=_summary(reason)
            )
        except Exception as exc:
            self.close(f"{type(exc).__name__}: {_summary(exc)}")
            return False
        # The quarantine parked the run and released the lease with it.
        self.close(f"effect {effect_id} is quarantined")
        return True


async def guarded_call(
    context: AgentRunContext | None,
    *,
    tool_name: str,
    arguments: Any,
    call: Callable[[], Awaitable[tuple[bool, dict[str, Any]]]],
    tool_call_id: str | None = None,
    approval_id: str | None = None,
    step: int | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Make one tool call with the ordering contract around it.

    The order below is the entire recovery argument and it is expressed as
    control flow, not as a comment: `dispatch` returns only after its
    transaction committed, and `call` cannot run before it returns.

    A retry-safe tool skips the fence because re-running it produces no second
    external effect. Everything else is fenced, whether or not anyone thought
    about it when the tool was added.
    """
    if not needs_fence(tool_name):
        return await call()
    if context is None:
        # No run store was supplied for this work at all. Nothing to fence
        # against and nothing to fail closed about: the caller never armed it.
        # Every production caller does, and
        # `test_agent_run_integration.py` is what holds them to it.
        return await call()
    if context.closed:
        raise AgentRunUnavailable(
            context.closed_reason or "this run holds no lease"
        )
    effect_id = context.dispatch(
        tool_name=tool_name,
        arguments=arguments,
        tool_call_id=tool_call_id,
        approval_id=approval_id,
        step=step,
    )
    try:
        ok, result = await call()
    except BaseException as exc:
        # Nothing observed the outcome. Do not guess it in either direction.
        context.quarantine(
            effect_id, reason=f"{type(exc).__name__} during {tool_name}"
        )
        # A cancellation keeps its own type on the way out. Turning it into a
        # RuntimeError would leave the asyncio task uncancelled, so the run
        # store's record would be right and the process still wrong.
        if not isinstance(exc, Exception):
            raise
        raise AgentRunEffectQuarantined(effect_id, tool_name) from exc
    context.record(
        effect_id,
        ok=ok,
        error_class=None if ok else str(result.get("code") or "tool_error"),
        error_summary=None if ok else str(result.get("error") or ""),
    )
    return ok, result
