"""Run budgets in the real turn loop.

Every test here first proves the run actually got far enough to produce the
state under test, and only then asserts the property. A budget test that
passed because the run never started would keep passing after the budget it
covers was deleted, which is the failure mode these are written against.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from coworker import turn as turn_module
from coworker.automation.scheduler import RECEIPT_STATUSES
from coworker.compaction import (
    OPEN_TAG,
    CompactionPolicy,
    SessionCompactor,
    state_key,
    transcript_defects,
)
from coworker.inbox import Inbox
from coworker.people import PersonStore
from coworker.permissions import ASK, AUTO, RETRY_SAFE, decide
from coworker.provider import (
    FakeProvider,
    ModelUsage,
    ProviderErrorKind,
    ProviderStreamError,
    ProviderTerminal,
    StreamChunk,
    ToolCall,
)
from coworker.provider_retry import RetryPolicy
from coworker.run_budget import (
    DEFAULT_ELAPSED_SECONDS,
    DEFAULT_ESTIMATED_COST_USD,
    DEFAULT_INPUT_TOKENS,
    DEFAULT_LOOP_REPEAT_LIMIT,
    DEFAULT_MODEL_TURNS,
    DEFAULT_OUTPUT_TOKENS,
    DEFAULT_TOOL_CALLS,
    MEASUREMENT_SOURCES,
    BudgetName,
    RunBudgetMeter,
    RunBudgetPolicy,
)
from coworker.store import ConversationStore
from coworker.tools import OPENAI_TOOLS
from coworker.turn import RunControl, new_turn_identity, run_turn

SID = "sess-budget"


def _usage(input_tokens: int, output_tokens: int) -> ModelUsage:
    return ModelUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        cached_input_tokens=0,
        uncached_input_tokens=input_tokens,
        reasoning_tokens=0,
    )


class SourcingProvider:
    """A model that keeps asking distinct Board questions and never stops.

    Distinct on purpose: this is the shape of ordinary sourcing work, so a run
    driven by it can only be stopped by an absolute budget, never by the loop
    detector. Tests that want the loop detector use `RepeatingProvider`.
    """

    provider_id = "fake"
    model_id = "fake"

    def __init__(
        self,
        *,
        usage: ModelUsage | None = None,
        cost_usd: float | None = None,
        calls_per_turn: int = 1,
        tool_name: str = "board_query",
        arguments: Any = None,
        sleep_seconds: float = 0.0,
    ) -> None:
        self.usage = usage
        self.cost_usd = cost_usd
        self.calls_per_turn = calls_per_turn
        self.tool_name = tool_name
        self.arguments = arguments
        self.sleep_seconds = sleep_seconds
        self.calls: list[list[dict[str, Any]]] = []
        self.requests = 0

    def _call(self, turn_index: int, slot: int) -> ToolCall:
        arguments = (
            dict(self.arguments)
            if self.arguments is not None
            else {"company": f"Company {turn_index}-{slot}"}
        )
        return ToolCall(
            id=f"call_{turn_index}_{slot}",
            name=self.tool_name,
            arguments=arguments,
        )

    async def astream(self, *, messages, tools=None, context_id=None):
        self.calls.append(list(messages))
        turn_index = self.requests
        self.requests += 1
        if self.sleep_seconds:
            await asyncio.sleep(self.sleep_seconds)
        yield StreamChunk.started(provider=self.provider_id, model=self.model_id)
        yield StreamChunk(text_delta=f"Working step {turn_index}. ")
        if self.usage is not None:
            yield StreamChunk(usage=self.usage)
        yield StreamChunk(
            finish_reason="tool_calls",
            terminal=ProviderTerminal(
                stop_reason="tool_calls",
                usage=self.usage,
                latency_ms=1.0,
                estimated_cost_usd=self.cost_usd,
            ),
        )
        yield StreamChunk(
            tool_calls=[
                self._call(turn_index, slot) for slot in range(self.calls_per_turn)
            ]
        )


class RepeatingProvider(SourcingProvider):
    """A model stuck asking the same question with the same answer."""

    def _call(self, turn_index: int, slot: int) -> ToolCall:
        return ToolCall(
            id=f"call_{turn_index}_{slot}",
            name=self.tool_name,
            arguments={"company": "Nimbus"},
        )


class ScriptedProvider(SourcingProvider):
    """Distinct tool steps for a while, then a closing answer."""

    def __init__(self, *, tool_turns: int, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.tool_turns = tool_turns

    async def astream(self, *, messages, tools=None, context_id=None):
        self.calls.append(list(messages))
        turn_index = self.requests
        self.requests += 1
        yield StreamChunk.started(provider=self.provider_id, model=self.model_id)
        if turn_index >= self.tool_turns:
            yield StreamChunk(text_delta="Here is the shortlist.")
            yield StreamChunk(finish_reason="stop")
            return
        yield StreamChunk(text_delta=f"Checking source {turn_index}. ")
        if self.usage is not None:
            yield StreamChunk(usage=self.usage)
        yield StreamChunk(finish_reason="tool_calls")
        yield StreamChunk(tool_calls=[self._call(turn_index, 0)])


def _events(store: ConversationStore, sid: str = SID) -> list[dict]:
    return list(store.load_events(sid))


def _terminal(store: ConversationStore, sid: str = SID) -> dict:
    ends = [
        event
        for event in _events(store, sid)
        if event["type"] in {"turn_end", "turn_stopped"}
    ]
    assert ends, "the run produced no terminal event"
    return ends[-1]


def _tool_started(store: ConversationStore, sid: str = SID) -> list[dict]:
    return [event for event in _events(store, sid) if event["type"] == "tool_started"]


def _turn(
    tmp_path,
    *,
    provider,
    text="Build the shortlist.",
    sid=SID,
    store=None,
    people=None,
    inbox=None,
    wait=None,
    gmail=None,
    **kwargs,
):
    conv = store if store is not None else ConversationStore(tmp_path)
    person_store = people if people is not None else PersonStore(tmp_path)

    async def _wait(_call_id: str) -> str:
        return wait or "allow"

    result = asyncio.run(
        run_turn(
            text=text,
            sid=sid,
            store=conv,
            provider=provider,
            persona=None,
            skills=None,
            inbox=inbox if inbox is not None else Inbox(conv),
            openai_tools=OPENAI_TOOLS,
            execute_kwargs={"people": person_store, "gmail": gmail},
            wait_permission=_wait if wait is not None else None,
            **kwargs,
        )
    )
    return result, conv


# --- criterion 9, first case: ordinary work is not cut short ---------------


def test_ordinary_sourcing_work_past_eight_steps_simply_finishes(tmp_path):
    """The point of the whole issue. Twelve honest steps, no interruption."""
    provider = ScriptedProvider(tool_turns=12, usage=_usage(900, 60))
    result, conv = _turn(tmp_path, provider=provider)

    assert len(_tool_started(conv)) == 12, "the run did not take twelve tool steps"
    assert result["status"] == "ok"
    assert result["text"] == "Here is the shortlist."
    terminal = _terminal(conv)
    assert terminal["state"] == "complete"
    assert terminal["run_budget"]["state"] == "finished"
    assert terminal["run_budget"]["stopped_by"] is None
    assert terminal["run_budget"]["remaining"]["final_answer"] is True
    assert terminal["run_budget"]["continue_available"] is False


# --- criterion 1: documented defaults --------------------------------------


def test_documented_defaults_cover_every_budget_and_name_their_measurement():
    limits = RunBudgetPolicy().limits()

    assert limits == {
        "model_turns": DEFAULT_MODEL_TURNS,
        "tool_calls": DEFAULT_TOOL_CALLS,
        "elapsed_seconds": DEFAULT_ELAPSED_SECONDS,
        "input_tokens": DEFAULT_INPUT_TOKENS,
        "output_tokens": DEFAULT_OUTPUT_TOKENS,
        "estimated_cost_usd": DEFAULT_ESTIMATED_COST_USD,
    }
    assert set(MEASUREMENT_SOURCES) == set(limits)
    assert set(limits) == {name.value for name in BudgetName}
    # The two the operator must not read as measurements.
    assert MEASUREMENT_SOURCES["estimated_cost_usd"] == "provider_cost_estimate"
    assert MEASUREMENT_SOURCES["elapsed_seconds"] == "monotonic_clock"


def test_a_policy_that_cannot_detect_a_loop_before_its_ceiling_is_rejected():
    """Criterion 7's ordering, held in the type rather than left to luck."""
    with pytest.raises(ValueError, match="before the absolute budget"):
        RunBudgetPolicy(tool_calls=DEFAULT_LOOP_REPEAT_LIMIT)


# --- criterion 2 and 9: each individual budget ------------------------------


def test_the_model_turn_budget_stops_a_run_that_will_not_end(tmp_path):
    provider = SourcingProvider()
    result, conv = _turn(
        tmp_path,
        provider=provider,
        run_budget_policy=RunBudgetPolicy(model_turns=5),
    )

    assert provider.requests == 5, "the run did not reach the model-turn budget"
    assert len(_tool_started(conv)) == 5
    terminal = _terminal(conv)
    assert result["status"] == "stopped"
    assert terminal["state"] == "stopped"
    assert terminal["run_budget"]["stopped_by"] == "model_turns"
    assert terminal["run_budget"]["consumed"]["model_turns"] == 5


def test_the_tool_call_budget_stops_a_run_mid_batch_and_names_what_was_queued(
    tmp_path,
):
    provider = SourcingProvider(calls_per_turn=3)
    result, conv = _turn(
        tmp_path,
        provider=provider,
        run_budget_policy=RunBudgetPolicy(model_turns=40, tool_calls=7),
    )

    started = _tool_started(conv)
    assert len(started) == 7, "the run did not reach the tool-call budget"
    terminal = _terminal(conv)
    assert result["status"] == "stopped"
    assert terminal["run_budget"]["stopped_by"] == "tool_calls"
    assert terminal["run_budget"]["consumed"]["tool_calls"] == 7
    # The two calls in the batch that were requested and never run.
    remaining = terminal["run_budget"]["remaining"]["requested_tools"]
    assert [item["name"] for item in remaining] == ["board_query", "board_query"]
    assert {item["id"] for item in remaining}.isdisjoint(
        {event["id"] for event in started}
    )
    # Stopping inside a batch leaves tool calls with no results. They are
    # closed on the way out, so the continuation reads a well-formed
    # transcript instead of an orphaned call.
    assert transcript_defects(conv.load(SID)) == []


def test_the_elapsed_time_budget_stops_a_slow_run(tmp_path):
    provider = SourcingProvider(sleep_seconds=0.05)
    result, conv = _turn(
        tmp_path,
        provider=provider,
        run_budget_policy=RunBudgetPolicy(elapsed_seconds=0.01),
    )

    assert provider.requests >= 1, "the run never issued a model request"
    terminal = _terminal(conv)
    assert result["status"] == "stopped"
    assert terminal["run_budget"]["stopped_by"] == "elapsed_seconds"
    assert terminal["run_budget"]["consumed"]["elapsed_seconds"] >= 0.01


def test_the_input_token_budget_counts_only_what_the_provider_reported(tmp_path):
    provider = SourcingProvider(usage=_usage(1_000, 10))
    result, conv = _turn(
        tmp_path,
        provider=provider,
        run_budget_policy=RunBudgetPolicy(input_tokens=2_500),
    )

    assert provider.requests == 3, "the run did not reach the input-token budget"
    terminal = _terminal(conv)
    assert result["status"] == "stopped"
    assert terminal["run_budget"]["stopped_by"] == "input_tokens"
    assert terminal["run_budget"]["consumed"]["input_tokens"] == 3_000
    assert terminal["run_budget"]["unmeasured_requests"] == 0


def test_the_output_token_budget_stops_a_verbose_run(tmp_path):
    provider = SourcingProvider(usage=_usage(10, 1_000))
    result, conv = _turn(
        tmp_path,
        provider=provider,
        run_budget_policy=RunBudgetPolicy(output_tokens=2_500),
    )

    assert provider.requests == 3, "the run did not reach the output-token budget"
    terminal = _terminal(conv)
    assert result["status"] == "stopped"
    assert terminal["run_budget"]["stopped_by"] == "output_tokens"
    assert terminal["run_budget"]["consumed"]["output_tokens"] == 3_000


def test_the_cost_budget_stops_a_run_and_says_the_figure_is_an_estimate(tmp_path):
    provider = SourcingProvider(usage=_usage(100, 10), cost_usd=0.05)
    result, conv = _turn(
        tmp_path,
        provider=provider,
        run_budget_policy=RunBudgetPolicy(estimated_cost_usd=0.12),
    )

    assert provider.requests == 3, "the run did not reach the cost budget"
    terminal = _terminal(conv)
    assert result["status"] == "stopped"
    assert terminal["run_budget"]["stopped_by"] == "estimated_cost_usd"
    assert terminal["run_budget"]["consumed"]["estimated_cost_usd"] == pytest.approx(
        0.15
    )
    assert terminal["run_budget"]["unpriced_requests"] == 0
    assert (
        terminal["run_budget"]["measurement"]["estimated_cost_usd"]
        == "provider_cost_estimate"
    )


def test_a_run_on_an_unpriced_model_says_the_cost_meter_did_not_cover_it(tmp_path):
    """The honest failure mode: no pricing means the cost budget cannot bind,
    and the operator has to be told rather than shown a reassuring $0.00."""
    provider = SourcingProvider(usage=_usage(1_000, 10), cost_usd=None)
    _result, conv = _turn(
        tmp_path,
        provider=provider,
        run_budget_policy=RunBudgetPolicy(input_tokens=2_500),
    )

    terminal = _terminal(conv)
    assert terminal["run_budget"]["stopped_by"] == "input_tokens"
    assert terminal["run_budget"]["consumed"]["estimated_cost_usd"] == 0.0
    assert terminal["run_budget"]["unpriced_requests"] == 3


def test_two_budgets_reached_together_are_both_reported(tmp_path):
    provider = SourcingProvider()
    _result, conv = _turn(
        tmp_path,
        provider=provider,
        run_budget_policy=RunBudgetPolicy(model_turns=4, tool_calls=4),
    )

    assert provider.requests == 4, "the run did not reach either budget"
    terminal = _terminal(conv)
    assert terminal["run_budget"]["exhausted"] == ["model_turns", "tool_calls"]
    assert terminal["run_budget"]["stopped_by"] == "model_turns"
    assert "model turns and tool calls" in terminal["message"]


# --- criterion 7: the loop detector fires first -----------------------------


def test_a_repeating_run_is_reported_as_stuck_before_any_budget_runs_out(tmp_path):
    provider = RepeatingProvider()
    result, conv = _turn(tmp_path, provider=provider)

    started = _tool_started(conv)
    assert len(started) > DEFAULT_LOOP_REPEAT_LIMIT - 1, "no repeats ever happened"
    terminal = _terminal(conv)
    assert result["status"] == "stopped"
    assert terminal["run_budget"]["stopped_by"] == "loop"
    assert terminal["run_budget"]["exhausted"] == []
    assert terminal["run_budget"]["repeats"] == DEFAULT_LOOP_REPEAT_LIMIT
    assert "returned nothing new" in terminal["message"]
    # The ordering claim, checked rather than assumed: every absolute budget
    # still had room when the run stopped.
    consumed = terminal["run_budget"]["consumed"]
    limits = terminal["run_budget"]["limits"]
    for name, limit in limits.items():
        assert consumed[name] < limit, f"{name} was already exhausted"


def test_a_run_that_keeps_asking_for_a_refused_tool_is_a_loop(tmp_path):
    """A refusal is an outcome. Asking for the same denied tool forever is not
    progress, so it has to reach the detector the same way a stale read does."""
    provider = RepeatingProvider(tool_name="board_get", arguments={})
    _result, conv = _turn(tmp_path, provider=provider)

    terminal = _terminal(conv)
    assert terminal["run_budget"]["stopped_by"] == "loop"
    assert all(record["ok"] is False for record in terminal["run_budget"]["completed"])


def test_distinct_work_never_trips_the_loop_detector(tmp_path):
    """The false positive that would replace one bad ceiling with another."""
    provider = ScriptedProvider(tool_turns=30)
    result, conv = _turn(tmp_path, provider=provider)

    assert len(_tool_started(conv)) == 30
    assert result["status"] == "ok"
    assert _terminal(conv)["run_budget"]["stopped_by"] is None


# --- criterion 3: the warning arrives before the stop -----------------------


def test_the_thread_is_warned_before_a_budget_is_exhausted(tmp_path):
    provider = SourcingProvider()
    _result, conv = _turn(
        tmp_path,
        provider=provider,
        run_budget_policy=RunBudgetPolicy(model_turns=5, warn_at=0.6),
    )

    events = _events(conv)
    started = [event for event in events if event["type"] == "tool_started"]
    assert len(started) == 5, "the run did not reach the budget"
    states = [event["run_budget"]["state"] for event in started]
    assert states == ["running", "running", "warning", "running", "running"]
    warning = started[2]["run_budget"]["warning"]
    assert warning["budget"] == "model_turns"
    assert "will stop and ask" in warning["message"]
    # Before, not with, the stop.
    assert events.index(started[2]) < events.index(_terminal(conv))


def test_every_activity_beat_carries_the_run_s_current_spend(tmp_path):
    provider = SourcingProvider(usage=_usage(100, 10))
    _result, conv = _turn(
        tmp_path,
        provider=provider,
        run_budget_policy=RunBudgetPolicy(model_turns=3),
    )

    started = _tool_started(conv)
    assert len(started) == 3
    turns = [event["run_budget"]["consumed"]["model_turns"] for event in started]
    assert turns == [1, 2, 3]
    assert started[0]["run_budget"]["limits"]["model_turns"] == 3
    assert started[0]["name"] == "board_query"


# --- criterion 4: a partial result is not a conclusion ----------------------


def test_an_exhausted_run_reports_receipts_and_unfinished_work_not_completion(
    tmp_path,
):
    provider = SourcingProvider()
    result, conv = _turn(
        tmp_path,
        provider=provider,
        run_budget_policy=RunBudgetPolicy(model_turns=3),
    )

    events = _events(conv)
    finished = [event for event in events if event["type"] == "tool_finished"]
    assert len(finished) == 3, "the run did not complete three tool calls"

    terminal = _terminal(conv)
    budget = terminal["run_budget"]
    assert result["status"] == "stopped"
    assert terminal["state"] == "stopped"
    assert budget["state"] == "exhausted"
    assert budget["remaining"]["final_answer"] is False
    assert budget["continue_available"] is True
    # The receipts are the calls that actually ran, in order, with outcomes.
    assert budget["completed"] == [
        {"id": event["id"], "name": event["name"], "ok": event["ok"]}
        for event in finished
    ]
    # And the run status the rest of Sourcecado derives from this is partial,
    # never success.
    assert RECEIPT_STATUSES[result["status"]] == "partial"


def test_a_stopped_run_never_claims_a_final_answer(tmp_path):
    provider = SourcingProvider()
    _result, conv = _turn(
        tmp_path,
        provider=provider,
        run_budget_policy=RunBudgetPolicy(model_turns=2),
    )

    terminal = _terminal(conv)
    assert terminal["state"] != "complete"
    assert terminal["run_budget"]["remaining"]["final_answer"] is False
    assert terminal["text"], "the partial text was thrown away"


# --- criterion 6: declining continuation loses nothing ----------------------


def test_declining_to_continue_leaves_every_completed_result_in_place(tmp_path):
    provider = SourcingProvider()
    _result, conv = _turn(
        tmp_path,
        provider=provider,
        run_budget_policy=RunBudgetPolicy(model_turns=3),
    )

    terminal = _terminal(conv)
    assert terminal["run_budget"]["stopped_by"] == "model_turns"

    # The director does nothing. The transcript still holds the work.
    transcript = conv.load(SID)
    tool_results = [message for message in transcript if message["role"] == "tool"]
    assert len(tool_results) == 3
    assert all(
        json.loads(message["content"]) for message in tool_results
    ), "a completed result was emptied"
    receipts = terminal["run_budget"]["completed"]
    assert {record["id"] for record in receipts} == {
        message["tool_call_id"] for message in tool_results
    }


# --- criterion 5: continuation preserves, and never replays -----------------


def test_continuing_reuses_completed_results_instead_of_running_them_again(
    tmp_path, monkeypatch
):
    executed: list[tuple[str, dict]] = []
    real_execute = turn_module.execute

    def _counting_execute(name, arguments=None, **kwargs):
        executed.append((name, dict(arguments or {})))
        return real_execute(name, arguments, **kwargs)

    monkeypatch.setattr(turn_module, "execute", _counting_execute)

    conv = ConversationStore(tmp_path)
    people = PersonStore(tmp_path)
    first = SourcingProvider()
    _result, _conv = _turn(
        tmp_path,
        provider=first,
        store=conv,
        people=people,
        run_budget_policy=RunBudgetPolicy(model_turns=2),
    )
    assert _terminal(conv)["run_budget"]["stopped_by"] == "model_turns"
    assert len(executed) == 2, "the first run did not complete two tool calls"
    first_calls = list(executed)

    # The director clicks Continue: an ordinary new turn on the same session.
    second = ScriptedProvider(tool_turns=0)
    result, _conv = _turn(
        tmp_path,
        provider=second,
        store=conv,
        people=people,
        text="Continue this run from where it stopped.",
    )

    assert result["status"] == "ok"
    assert executed == first_calls, "a completed tool result was replayed"
    view = second.calls[-1]
    carried = [
        message for message in view if message.get("role") == "tool"
    ]
    assert {message["tool_call_id"] for message in carried} == {
        f"call_{index}_0" for index in range(2)
    }


def test_continuing_after_a_ui_restart_still_carries_the_completed_work(tmp_path):
    """A restart means a fresh store handle and a fresh inbox over the same
    state directory. The continuation has to survive that, because that is
    what a director who reopens the app actually does."""
    conv = ConversationStore(tmp_path)
    people = PersonStore(tmp_path)
    first = SourcingProvider()
    _turn(
        tmp_path,
        provider=first,
        store=conv,
        people=people,
        run_budget_policy=RunBudgetPolicy(model_turns=2),
    )
    stop = _terminal(conv)
    assert stop["run_budget"]["continue_available"] is True

    restarted = ConversationStore(tmp_path)
    # What a reopened UI restores from still offers the Continue action.
    restored = [
        event for event in restarted.load_events(SID) if event["type"] == "turn_end"
    ][-1]
    assert restored["run_budget"]["continue_available"] is True
    assert len(restored["run_budget"]["completed"]) == 2

    second = ScriptedProvider(tool_turns=0)
    result, _conv = _turn(
        tmp_path,
        provider=second,
        store=restarted,
        people=PersonStore(tmp_path),
        inbox=Inbox(restarted),
        text="Continue this run from where it stopped.",
    )
    assert result["status"] == "ok"
    carried = [
        message for message in second.calls[-1] if message.get("role") == "tool"
    ]
    assert len(carried) == 2


def test_continuing_preserves_the_bound_person_and_the_compaction_state(tmp_path):
    conv = ConversationStore(tmp_path)
    people = PersonStore(tmp_path)
    person = people.keep_from_apollo(
        apollo_id="ada",
        first_name="Ada",
        last_name_obfuscated="L",
        title="Founder",
        company="Nimbus Robotics",
        target="Nimbus",
    )
    people.bind_session(SID, person["person_id"])

    async def _summary(_request):
        return "Earlier: the director asked about Nimbus and three names came back."

    compactor = SessionCompactor(
        store=conv,
        session_id=SID,
        policy=CompactionPolicy(threshold_pct=0.0001, keep_fraction=0.02),
        summarize=_summary,
    )
    first = SourcingProvider(usage=_usage(900, 20))
    _turn(
        tmp_path,
        provider=first,
        store=conv,
        people=people,
        compactor=compactor,
        run_budget_policy=RunBudgetPolicy(model_turns=4),
    )
    stop = _terminal(conv)
    assert stop["run_budget"]["stopped_by"] == "model_turns"
    assert stop.get("compaction"), "the run never compacted, so nothing was preserved"
    generation = stop["compaction"]["generation"]
    assert generation >= 1

    saved = json.loads(conv.get_setting(state_key(SID)))
    assert saved["generation"] == generation

    second = ScriptedProvider(tool_turns=0)
    result, _conv = _turn(
        tmp_path,
        provider=second,
        store=conv,
        people=people,
        text="Continue this run from where it stopped.",
    )
    assert result["status"] == "ok"
    assert people.person_for_session(SID) == person["person_id"]
    assert json.loads(conv.get_setting(state_key(SID)))["generation"] >= generation
    view = "".join(
        str(message.get("content") or "") for message in second.calls[-1]
    )
    assert OPEN_TAG in view, "the continuation lost the compacted context"


# --- criterion 8: continuation is not standing permission -------------------


class ApprovalProvider(SourcingProvider):
    """Asks for one approval-gated tool, once."""

    def __init__(self, *, tool_name: str) -> None:
        super().__init__(tool_name=tool_name, arguments={})

    async def astream(self, *, messages, tools=None, context_id=None):
        self.calls.append(list(messages))
        index = self.requests
        self.requests += 1
        yield StreamChunk.started(provider=self.provider_id, model=self.model_id)
        if index > 0:
            yield StreamChunk(text_delta="Stopping there.")
            yield StreamChunk(finish_reason="stop")
            return
        yield StreamChunk(text_delta="Sending it now. ")
        yield StreamChunk(finish_reason="tool_calls")
        yield StreamChunk(
            tool_calls=[
                ToolCall(id="call_gated", name=self.tool_name, arguments={})
            ]
        )


@pytest.mark.parametrize("gated", sorted(ASK))
def test_continuation_never_becomes_standing_permission(tmp_path, gated):
    """Every gate that applied before a budget stop applies after Continue.

    Driven against the real ASK set, not a stand-in, so a tool moving between
    the sets cannot quietly slip past this.
    """
    assert decide(gated).needs_user is True
    assert gated not in AUTO and gated not in RETRY_SAFE

    conv = ConversationStore(tmp_path)
    people = PersonStore(tmp_path)

    # Run one reaches the budget with real work behind it.
    first = SourcingProvider()
    _turn(
        tmp_path,
        provider=first,
        store=conv,
        people=people,
        run_budget_policy=RunBudgetPolicy(model_turns=2),
    )
    stop = _terminal(conv)
    assert stop["run_budget"]["stopped_by"] == "model_turns"
    assert len(stop["run_budget"]["completed"]) == 2

    # The director continues. The gated tool still has to be approved, and a
    # denial still stops it.
    second = ApprovalProvider(tool_name=gated)
    result, _conv = _turn(
        tmp_path,
        provider=second,
        store=conv,
        people=people,
        text="Continue this run from where it stopped.",
        wait="deny",
    )

    events = _events(conv)
    asked = [
        event
        for event in events
        if event["type"] == "permission_required" and event["name"] == gated
    ]
    assert len(asked) == 1, f"{gated} ran without asking after continuation"
    assert not [
        event
        for event in events
        if event["type"] == "tool_started" and event["name"] == gated
    ], f"{gated} executed despite the denial"
    resolved = [
        event
        for event in events
        if event["type"] == "approval_resolved" and event["name"] == gated
    ]
    assert resolved and resolved[-1]["resolution"] == "denied"
    assert result["status"] in {"partial", "ok"}


def test_a_continuation_run_asks_again_for_a_tool_approved_in_the_run_before(
    tmp_path,
):
    """An approval granted before the budget stop does not carry forward."""
    conv = ConversationStore(tmp_path)
    people = PersonStore(tmp_path)

    granted = ApprovalProvider(tool_name="calendar_create")
    _turn(tmp_path, provider=granted, store=conv, people=people, wait="allow")
    first_asks = [
        event
        for event in _events(conv)
        if event["type"] == "permission_required"
        and event["name"] == "calendar_create"
    ]
    assert len(first_asks) == 1, "the first run never asked"

    again = ApprovalProvider(tool_name="calendar_create")
    _turn(
        tmp_path,
        provider=again,
        store=conv,
        people=people,
        text="Continue this run from where it stopped.",
        wait="deny",
    )
    asks = [
        event
        for event in _events(conv)
        if event["type"] == "permission_required"
        and event["name"] == "calendar_create"
    ]
    assert len(asks) == 2, "the continuation reused the earlier approval"


# --- criterion 9: cancellation, retry -------------------------------------


def test_cancelling_a_budgeted_run_still_reports_a_cancellation(tmp_path):
    """A budget must not take credit for a stop the director asked for."""
    conv = ConversationStore(tmp_path)
    identity = new_turn_identity(SID)
    control = RunControl(identity)
    provider = SourcingProvider()

    async def _drive():
        async def _emit(event):
            if event.get("type") == "tool_finished":
                await control.request_cancel()

        return await run_turn(
            text="Build the shortlist.",
            sid=SID,
            store=conv,
            provider=provider,
            persona=None,
            skills=None,
            inbox=Inbox(conv),
            openai_tools=OPENAI_TOOLS,
            execute_kwargs={"people": PersonStore(tmp_path)},
            emit=_emit,
            identity=identity,
            control=control,
            run_budget_policy=RunBudgetPolicy(model_turns=40),
        )

    result = asyncio.run(_drive())

    assert result["status"] == "stopped"
    assert provider.requests == 1, "the run never got going"
    terminal = _terminal(conv)
    assert terminal["type"] == "turn_stopped"
    assert terminal["message"] == "Run cancelled."
    assert "run_budget" not in terminal


def test_a_provider_retry_costs_tokens_but_not_a_model_turn(tmp_path):
    """Retries repeat a request the loop already counted. Charging them to the
    turn budget would let a flaky provider eat a run's allowance."""
    rate_limited = ProviderStreamError(
        provider="fake",
        model="fake",
        kind=ProviderErrorKind.RATE_LIMIT,
        message="rate limited",
        retryable=True,
    )
    provider = FakeProvider(
        steps=[
            {"error": rate_limited},
            {
                "tool_calls": [
                    ToolCall(id="call_1", name="board_query", arguments={"company": "Nimbus"})
                ],
                "finish_reason": "tool_calls",
                "usage": _usage(500, 20),
            },
            {"deltas": ("Done.",), "finish_reason": "stop", "usage": _usage(600, 30)},
        ]
    )

    async def _no_sleep(_seconds: float) -> None:
        return None

    result, conv = _turn(
        tmp_path,
        provider=provider,
        retry_policy=RetryPolicy(),
        retry_sleep=_no_sleep,
        retry_random=lambda: 0.0,
    )

    assert provider.i == 3, "the retry never happened"
    assert result["status"] == "ok"
    budget = _terminal(conv)["run_budget"]
    assert budget["consumed"]["model_turns"] == 2
    assert budget["consumed"]["input_tokens"] == 1_100


# --- the meter on its own --------------------------------------------------


def test_the_elapsed_budget_reads_a_monotonic_clock(tmp_path):
    ticks = iter([0.0, 5.0, 11.0])
    readings = [0.0]

    def _clock() -> float:
        try:
            readings[0] = next(ticks)
        except StopIteration:
            pass
        return readings[0]

    meter = RunBudgetMeter(RunBudgetPolicy(elapsed_seconds=10.0), clock=_clock)
    assert meter.check() is None
    stop = meter.check()
    assert stop is not None and stop.exhausted == ("elapsed_seconds",)


def test_the_meter_reports_requests_the_provider_did_not_measure():
    meter = RunBudgetMeter()
    meter.record_request(None, None)
    payload = meter.terminal_payload(stop=None, final_answer=True)
    assert payload["unmeasured_requests"] == 1
    assert payload["unpriced_requests"] == 1
    assert payload["consumed"]["input_tokens"] == 0
