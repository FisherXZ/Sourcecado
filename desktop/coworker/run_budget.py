"""What one Sourcecado run may spend before it stops and asks.

The fixed eight-step ceiling this replaces had two problems. It cut ordinary
sourcing work short, because a real run reads a board, checks several sources,
and drafts, which is more than eight steps. And it bounded the wrong thing: a
run that repeats one tool forever and a run that reads a hundred large pages
both cost the director something, but only the second is expensive and only
the first is stuck.

So there are two mechanisms here, in a deliberate order.

The loop detector fires first. It watches for tool calls that produce nothing
new and stops the run while the absolute budgets still have room, so the
operator is told the run is stuck rather than that it was expensive. Those are
different diagnoses and they lead to different actions.

The absolute budgets are the backstop: model turns, tool calls, elapsed time,
input tokens, output tokens, and estimated cost. Exhausting one stops the run
with the work it has actually completed, never with a claim that it finished.

Nothing in this module decides what a run is allowed to do. Permission lives
in `permissions.decide`, is asked per tool call, and is unaffected by how much
budget is left or by whether the director chose to continue.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from coworker.telemetry import CostEstimate, UsageEvent

# Defaults. See `desktop/docs/run-budgets.md` for how each was chosen.
#
# A model turn is one pass of the agent loop: one model request plus the tool
# calls it asked for. Provider retries and failovers are not model turns; they
# repeat a request the loop already counted and are bounded by RetryPolicy.
DEFAULT_MODEL_TURNS = 40
# A model turn may request several tools at once, so this is not a multiple of
# the turn budget by accident: roughly three calls per turn.
DEFAULT_TOOL_CALLS = 120
# Fifteen minutes. Long enough for forty turns of connector work, short enough
# that an unattended run cannot quietly spend an afternoon.
DEFAULT_ELAPSED_SECONDS = 900.0
# The token ceilings exist for models with no entry in the pricing table,
# where the cost meter reads zero and cannot bind. On a priced model the cost
# ceiling is reached first.
DEFAULT_INPUT_TOKENS = 2_000_000
DEFAULT_OUTPUT_TOKENS = 200_000
DEFAULT_ESTIMATED_COST_USD = 2.00
# Warn once a budget passes this fraction, so the director sees it coming.
DEFAULT_WARN_AT = 0.8
# Consecutive tool calls that repeat a (call, result) pair already seen in
# this run. Three is enough to distinguish a cycle from a coincidence.
DEFAULT_LOOP_REPEAT_LIMIT = 3

_FINGERPRINT_CHARS = 16


class BudgetName(StrEnum):
    MODEL_TURNS = "model_turns"
    TOOL_CALLS = "tool_calls"
    ELAPSED = "elapsed_seconds"
    INPUT_TOKENS = "input_tokens"
    OUTPUT_TOKENS = "output_tokens"
    COST = "estimated_cost_usd"


#: How each number reaching a budget was obtained. Two of the six are provider
#: estimates rather than measurements, and the operator is told which.
MEASUREMENT_SOURCES: dict[str, str] = {
    BudgetName.MODEL_TURNS.value: "counted",
    BudgetName.TOOL_CALLS.value: "counted",
    BudgetName.ELAPSED.value: "monotonic_clock",
    BudgetName.INPUT_TOKENS.value: "provider_usage",
    BudgetName.OUTPUT_TOKENS.value: "provider_usage",
    BudgetName.COST.value: "provider_cost_estimate",
}

_LABELS: dict[str, str] = {
    BudgetName.MODEL_TURNS.value: "model turns",
    BudgetName.TOOL_CALLS.value: "tool calls",
    BudgetName.ELAPSED.value: "elapsed time",
    BudgetName.INPUT_TOKENS.value: "input tokens",
    BudgetName.OUTPUT_TOKENS.value: "output tokens",
    BudgetName.COST.value: "estimated cost",
}


class StopKind(StrEnum):
    #: A no-progress tool pattern, caught before any absolute budget.
    LOOP = "loop"
    #: An absolute budget ran out.
    BUDGET = "budget"


@dataclass(frozen=True)
class RunBudgetPolicy:
    model_turns: int = DEFAULT_MODEL_TURNS
    tool_calls: int = DEFAULT_TOOL_CALLS
    elapsed_seconds: float = DEFAULT_ELAPSED_SECONDS
    input_tokens: int = DEFAULT_INPUT_TOKENS
    output_tokens: int = DEFAULT_OUTPUT_TOKENS
    estimated_cost_usd: float = DEFAULT_ESTIMATED_COST_USD
    warn_at: float = DEFAULT_WARN_AT
    loop_repeat_limit: int = DEFAULT_LOOP_REPEAT_LIMIT

    def __post_init__(self) -> None:
        for name in (
            "model_turns",
            "tool_calls",
            "input_tokens",
            "output_tokens",
            "loop_repeat_limit",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        for name in ("elapsed_seconds", "estimated_cost_usd"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} must be a positive number")
            if value <= 0:
                raise ValueError(f"{name} must be a positive number")
        if not 0 < self.warn_at < 1:
            raise ValueError("warn_at must be a fraction between 0 and 1")
        # Criterion 7's ordering, enforced in the type rather than left to the
        # numbers happening to line up: a stuck run must be able to reach the
        # loop detector before it can exhaust the tool-call budget.
        if self.loop_repeat_limit >= self.tool_calls:
            raise ValueError(
                "loop_repeat_limit must be below tool_calls so the loop "
                "detector can fire before the absolute budget"
            )

    def limits(self) -> dict[str, float | int]:
        return {
            BudgetName.MODEL_TURNS.value: self.model_turns,
            BudgetName.TOOL_CALLS.value: self.tool_calls,
            BudgetName.ELAPSED.value: self.elapsed_seconds,
            BudgetName.INPUT_TOKENS.value: self.input_tokens,
            BudgetName.OUTPUT_TOKENS.value: self.output_tokens,
            BudgetName.COST.value: self.estimated_cost_usd,
        }


@dataclass(frozen=True)
class BudgetWarning:
    budget: str
    used: float
    limit: float

    @property
    def used_ratio(self) -> float:
        return self.used / self.limit if self.limit else 0.0

    def message(self) -> str:
        return (
            f"This run has used {round(self.used_ratio * 100)}% of its "
            f"{_LABELS[self.budget]} budget. It will stop and ask before "
            "going further."
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "budget": self.budget,
            "used": self.used,
            "limit": self.limit,
            "used_ratio": self.used_ratio,
            "message": self.message(),
        }


@dataclass(frozen=True)
class BudgetStop:
    kind: StopKind
    #: For a budget stop, every budget that is out, first-reached first. For a
    #: loop stop, empty: no budget ran out.
    exhausted: tuple[str, ...]
    #: For a loop stop, how many consecutive no-progress calls tripped it.
    repeats: int = 0

    def message(self) -> str:
        if self.kind is StopKind.LOOP:
            return (
                f"Stopped after {self.repeats} tool calls in a row that "
                "returned nothing new. The run was repeating itself, not "
                "making progress."
            )
        names = " and ".join(_LABELS[name] for name in self.exhausted)
        return f"Stopped after this run reached its {names} budget."


@dataclass
class _ToolRecord:
    call_id: str
    name: str
    ok: bool


def _fingerprint(value: Any) -> str:
    """A stable, content-free handle for a tool result.

    The digest never leaves this module: it decides whether a call produced
    something new, and only the count of repeats is ever reported.
    """
    try:
        rendered = json.dumps(value, sort_keys=True, default=str)
    except (TypeError, ValueError):
        rendered = repr(value)
    return hashlib.sha256(rendered.encode("utf-8", "replace")).hexdigest()[
        :_FINGERPRINT_CHARS
    ]


class RunBudgetMeter:
    """Measures one run against one policy.

    Every input is a typed runtime telemetry value or an exact count taken at
    the point the loop does the work: `UsageEvent` for tokens, `CostEstimate`
    for spend, the monotonic clock for elapsed time. Nothing here re-derives a
    measurement from text.
    """

    def __init__(
        self,
        policy: RunBudgetPolicy | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.policy = policy or RunBudgetPolicy()
        self._clock = clock
        self._started_at = clock()
        self._model_turns = 0
        self._tool_calls = 0
        self._input_tokens = 0
        self._output_tokens = 0
        self._cost_usd = 0.0
        self._unpriced_requests = 0
        self._unmeasured_requests = 0
        self._completed: list[_ToolRecord] = []
        self._seen_outcomes: set[tuple[str, str]] = set()
        self._stale_streak = 0
        self._warned: set[str] = set()

    # --- measurement intake ------------------------------------------------

    def start_model_turn(self) -> None:
        """One pass of the agent loop. Retries inside it are not turns."""
        self._model_turns += 1

    def record_request(
        self,
        usage: UsageEvent | None,
        cost: CostEstimate | None,
    ) -> None:
        """Take one model request's telemetry as the provider reported it."""
        if usage is None:
            self._unmeasured_requests += 1
        else:
            self._input_tokens += usage.input_tokens or 0
            self._output_tokens += usage.output_tokens or 0
        if cost is None:
            self._unpriced_requests += 1
        else:
            self._cost_usd += cost.estimated_cost_usd

    def charge_tool_call(self) -> None:
        self._tool_calls += 1

    def record_tool_outcome(
        self,
        *,
        call_id: str,
        name: str,
        arguments: Any,
        result: Any,
        ok: bool,
    ) -> None:
        """A completed call: a receipt, and one reading for the loop detector.

        Progress means this exact call has not already produced this exact
        result in this run. A refusal counts: a model that asks for a denied
        tool over and over is looping just as much as one that re-reads the
        same page.
        """
        self._completed.append(_ToolRecord(call_id=call_id, name=name, ok=ok))
        outcome = (f"{name}:{_fingerprint(arguments)}", _fingerprint(result))
        if outcome in self._seen_outcomes:
            self._stale_streak += 1
        else:
            self._seen_outcomes.add(outcome)
            self._stale_streak = 0

    # --- readings ----------------------------------------------------------

    @property
    def elapsed_seconds(self) -> float:
        return max(0.0, self._clock() - self._started_at)

    def consumed(self) -> dict[str, float | int]:
        return {
            BudgetName.MODEL_TURNS.value: self._model_turns,
            BudgetName.TOOL_CALLS.value: self._tool_calls,
            BudgetName.ELAPSED.value: self.elapsed_seconds,
            BudgetName.INPUT_TOKENS.value: self._input_tokens,
            BudgetName.OUTPUT_TOKENS.value: self._output_tokens,
            BudgetName.COST.value: self._cost_usd,
        }

    def exhausted(self) -> tuple[str, ...]:
        limits = self.policy.limits()
        consumed = self.consumed()
        return tuple(
            name for name in limits if consumed[name] >= limits[name]
        )

    def looping(self) -> bool:
        return self._stale_streak >= self.policy.loop_repeat_limit

    def check(self) -> BudgetStop | None:
        """The stop decision, loop detector first.

        Order is the point. A run that is stuck must be reported as stuck even
        if it also happens to be out of budget on the same call.
        """
        if self.looping():
            return BudgetStop(
                kind=StopKind.LOOP,
                exhausted=(),
                repeats=self._stale_streak,
            )
        out = self.exhausted()
        if out:
            return BudgetStop(kind=StopKind.BUDGET, exhausted=out)
        return None

    def warning(self) -> BudgetWarning | None:
        """The first budget past the warning threshold, reported once."""
        limits = self.policy.limits()
        consumed = self.consumed()
        for name, limit in limits.items():
            used = consumed[name]
            if used >= limit or used < limit * self.policy.warn_at:
                continue
            if name in self._warned:
                continue
            self._warned.add(name)
            return BudgetWarning(budget=name, used=used, limit=limit)
        return None

    # --- projections -------------------------------------------------------

    def live_payload(self) -> dict[str, Any]:
        """What the thread shows while the run is working."""
        warning = self.warning()
        return {
            "state": "warning" if warning is not None else "running",
            "consumed": self.consumed(),
            "limits": self.policy.limits(),
            "warning": warning.as_dict() if warning is not None else None,
        }

    def terminal_payload(
        self,
        *,
        stop: BudgetStop | None,
        pending_calls: tuple[tuple[str, str], ...] = (),
        final_answer: bool,
    ) -> dict[str, Any]:
        """The record of what this run actually did.

        `final_answer` is the whole difference between a result and a stopping
        point. It is false whenever the run ended without the model closing
        it, and the operator surfaces that as unfinished work rather than as
        an answer.
        """
        return {
            "state": "exhausted" if stop is not None else "finished",
            "stopped_by": (
                None
                if stop is None
                else (
                    StopKind.LOOP.value
                    if stop.kind is StopKind.LOOP
                    else stop.exhausted[0]
                )
            ),
            "exhausted": list(stop.exhausted) if stop is not None else [],
            "repeats": stop.repeats if stop is not None else 0,
            "consumed": self.consumed(),
            "limits": self.policy.limits(),
            "measurement": dict(MEASUREMENT_SOURCES),
            "unpriced_requests": self._unpriced_requests,
            "unmeasured_requests": self._unmeasured_requests,
            "completed": [
                {"id": record.call_id, "name": record.name, "ok": record.ok}
                for record in self._completed
            ],
            "remaining": {
                "requested_tools": [
                    {"id": call_id, "name": name} for call_id, name in pending_calls
                ],
                "final_answer": final_answer,
            },
            "continue_available": stop is not None,
        }
