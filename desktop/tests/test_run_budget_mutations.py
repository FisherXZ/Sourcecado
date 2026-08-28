"""Each guard is broken on purpose, and the property it protects must fail.

A passing budget test proves nothing if it would also pass with the budget
deleted. Every test here removes one guard and asserts the corresponding
property collapses. If one of these starts failing, the guard it names has
stopped doing work and the test that covers it has gone vacuous.

The four failure modes this issue is most exposed to, one test each: a budget
that never trips, a loop detector that never fires, a continuation that grants
authority, and a partial result that claims completion.
"""

from __future__ import annotations

import itertools

from coworker import run_budget as run_budget_module
from coworker import turn as turn_module
from coworker.permissions import ASK, Decision
from coworker.run_budget import RunBudgetMeter, RunBudgetPolicy
from coworker.store import ConversationStore
from coworker.people import PersonStore

from tests.test_run_budget import (
    SID,
    ApprovalProvider,
    RepeatingProvider,
    ScriptedProvider,
    SourcingProvider,
    _events,
    _terminal,
    _tool_started,
    _turn,
)

GATED = "gmail_send"


# --- mutation: a budget that never trips -----------------------------------


def test_a_budget_that_never_trips_lets_a_run_past_its_ceiling(tmp_path, monkeypatch):
    honest_provider = ScriptedProvider(tool_turns=12)
    honest_store = ConversationStore(tmp_path / "honest")
    result, _conv = _turn(
        tmp_path,
        provider=honest_provider,
        store=honest_store,
        run_budget_policy=RunBudgetPolicy(model_turns=5),
    )
    assert result["status"] == "stopped"
    assert _terminal(honest_store)["run_budget"]["stopped_by"] == "model_turns"
    assert len(_tool_started(honest_store)) == 5

    monkeypatch.setattr(RunBudgetMeter, "exhausted", lambda self: ())

    broken_provider = ScriptedProvider(tool_turns=12)
    broken_store = ConversationStore(tmp_path / "broken")
    broken, _conv = _turn(
        tmp_path,
        provider=broken_provider,
        store=broken_store,
        run_budget_policy=RunBudgetPolicy(model_turns=5),
    )

    assert broken["status"] == "ok"
    assert len(_tool_started(broken_store)) == 12
    assert _terminal(broken_store)["run_budget"]["stopped_by"] is None


# --- mutation: a loop detector that never fires ----------------------------


def test_a_loop_detector_that_never_fires_reports_a_stuck_run_as_expensive(
    tmp_path, monkeypatch
):
    """The diagnosis, not just the stop, is what the detector buys."""
    policy = RunBudgetPolicy(tool_calls=8, loop_repeat_limit=3)

    honest_store = ConversationStore(tmp_path / "honest")
    _result, _conv = _turn(
        tmp_path,
        provider=RepeatingProvider(),
        store=honest_store,
        run_budget_policy=policy,
    )
    honest = _terminal(honest_store)["run_budget"]
    assert honest["stopped_by"] == "loop"
    assert honest["consumed"]["tool_calls"] < policy.tool_calls

    # Every call now looks new, which is what a detector keyed on the wrong
    # thing would see.
    counter = itertools.count()
    monkeypatch.setattr(
        run_budget_module,
        "_fingerprint",
        lambda value: f"unique-{next(counter)}",
    )

    broken_store = ConversationStore(tmp_path / "broken")
    _broken, _conv = _turn(
        tmp_path,
        provider=RepeatingProvider(),
        store=broken_store,
        run_budget_policy=policy,
    )
    broken = _terminal(broken_store)["run_budget"]

    assert broken["stopped_by"] == "tool_calls"
    assert broken["consumed"]["tool_calls"] == policy.tool_calls


def test_checking_budgets_before_the_loop_detector_misnames_a_stuck_run(
    tmp_path, monkeypatch
):
    """Both conditions are true on the same call. Order decides the diagnosis."""
    policy = RunBudgetPolicy(tool_calls=4, loop_repeat_limit=3)

    honest_store = ConversationStore(tmp_path / "honest")
    _turn(
        tmp_path,
        provider=RepeatingProvider(),
        store=honest_store,
        run_budget_policy=policy,
    )
    honest = _terminal(honest_store)["run_budget"]
    assert honest["stopped_by"] == "loop"
    assert honest["consumed"]["tool_calls"] == policy.tool_calls, (
        "the absolute budget was not also exhausted, so order proved nothing"
    )

    def _budget_first(self):
        out = self.exhausted()
        if out:
            return run_budget_module.BudgetStop(
                kind=run_budget_module.StopKind.BUDGET, exhausted=out
            )
        if self.looping():
            return run_budget_module.BudgetStop(
                kind=run_budget_module.StopKind.LOOP,
                exhausted=(),
                repeats=self._stale_streak,
            )
        return None

    monkeypatch.setattr(RunBudgetMeter, "check", _budget_first)

    broken_store = ConversationStore(tmp_path / "broken")
    _turn(
        tmp_path,
        provider=RepeatingProvider(),
        store=broken_store,
        run_budget_policy=policy,
    )
    assert _terminal(broken_store)["run_budget"]["stopped_by"] == "tool_calls"


def test_a_detector_that_ignores_refusals_misses_a_run_stuck_on_a_denied_tool(
    tmp_path, monkeypatch
):
    honest_store = ConversationStore(tmp_path / "honest")
    _turn(
        tmp_path,
        provider=RepeatingProvider(tool_name="board_get", arguments={}),
        store=honest_store,
        run_budget_policy=RunBudgetPolicy(tool_calls=8, loop_repeat_limit=3),
    )
    assert _terminal(honest_store)["run_budget"]["stopped_by"] == "loop"

    real = RunBudgetMeter.record_tool_outcome

    def _successes_only(self, *, call_id, name, arguments, result, ok):
        if not ok:
            return None
        return real(
            self,
            call_id=call_id,
            name=name,
            arguments=arguments,
            result=result,
            ok=ok,
        )

    monkeypatch.setattr(RunBudgetMeter, "record_tool_outcome", _successes_only)

    broken_store = ConversationStore(tmp_path / "broken")
    _turn(
        tmp_path,
        provider=RepeatingProvider(tool_name="board_get", arguments={}),
        store=broken_store,
        run_budget_policy=RunBudgetPolicy(tool_calls=8, loop_repeat_limit=3),
    )
    assert _terminal(broken_store)["run_budget"]["stopped_by"] == "tool_calls"


# --- mutation: a continuation that grants authority ------------------------


def test_a_continuation_that_grants_authority_skips_the_approval_entirely(
    tmp_path, monkeypatch
):
    """The mutation criterion 8 exists to forbid: the second run treats the
    director's Continue as permission to act."""
    assert GATED in ASK

    def _run(store, people, *, gated_provider):
        _turn(
            tmp_path,
            provider=SourcingProvider(),
            store=store,
            people=people,
            run_budget_policy=RunBudgetPolicy(model_turns=2),
        )
        stop = _terminal(store)["run_budget"]
        assert stop["stopped_by"] == "model_turns"
        _turn(
            tmp_path,
            provider=gated_provider,
            store=store,
            people=people,
            text="Continue this run from where it stopped.",
            wait="deny",
        )
        events = _events(store)
        return (
            [
                event
                for event in events
                if event["type"] == "permission_required"
                and event["name"] == GATED
            ],
            [
                event
                for event in events
                if event["type"] == "tool_started" and event["name"] == GATED
            ],
        )

    honest_store = ConversationStore(tmp_path / "honest")
    asked, executed = _run(
        honest_store,
        PersonStore(tmp_path / "honest-people"),
        gated_provider=ApprovalProvider(tool_name=GATED),
    )
    assert len(asked) == 1
    assert executed == []

    monkeypatch.setattr(
        turn_module,
        "decide",
        lambda name, arguments=None, **kwargs: Decision(True, False, "continued"),
    )

    broken_store = ConversationStore(tmp_path / "broken")
    asked, executed = _run(
        broken_store,
        PersonStore(tmp_path / "broken-people"),
        gated_provider=ApprovalProvider(tool_name=GATED),
    )
    assert asked == []
    assert len(executed) == 1


# --- mutation: a partial result that claims completion ---------------------


def test_a_partial_result_that_claims_completion_hides_the_stop(
    tmp_path, monkeypatch
):
    honest_store = ConversationStore(tmp_path / "honest")
    result, _conv = _turn(
        tmp_path,
        provider=SourcingProvider(),
        store=honest_store,
        run_budget_policy=RunBudgetPolicy(model_turns=3),
    )
    honest = _terminal(honest_store)["run_budget"]
    assert result["status"] == "stopped"
    assert honest["state"] == "exhausted"
    assert honest["remaining"]["final_answer"] is False

    real = RunBudgetMeter.terminal_payload

    def _always_finished(self, *, stop, pending_calls=(), final_answer):
        payload = real(
            self, stop=stop, pending_calls=pending_calls, final_answer=final_answer
        )
        payload["state"] = "finished"
        payload["stopped_by"] = None
        payload["remaining"] = {"requested_tools": [], "final_answer": True}
        return payload

    monkeypatch.setattr(RunBudgetMeter, "terminal_payload", _always_finished)

    broken_store = ConversationStore(tmp_path / "broken")
    _turn(
        tmp_path,
        provider=SourcingProvider(),
        store=broken_store,
        run_budget_policy=RunBudgetPolicy(model_turns=3),
    )
    broken = _terminal(broken_store)["run_budget"]
    assert broken["state"] == "finished"
    assert broken["remaining"]["final_answer"] is True


def test_receipts_that_omit_failed_calls_understate_what_the_run_did(
    tmp_path, monkeypatch
):
    """The defect the receipt check itself could miss: a completed call that
    failed is still work the run did, and a receipt list that drops it reads
    as a cleaner run than actually happened."""
    honest_store = ConversationStore(tmp_path / "honest")
    _turn(
        tmp_path,
        provider=SourcingProvider(tool_name="board_get", arguments={}),
        store=honest_store,
        run_budget_policy=RunBudgetPolicy(model_turns=3),
    )
    honest = _terminal(honest_store)["run_budget"]
    finished = [
        event for event in _events(honest_store) if event["type"] == "tool_finished"
    ]
    assert len(finished) == 3
    assert all(event["ok"] is False for event in finished)
    assert len(honest["completed"]) == 3

    real = RunBudgetMeter.record_tool_outcome

    def _successes_only(self, *, call_id, name, arguments, result, ok):
        if not ok:
            return None
        return real(
            self,
            call_id=call_id,
            name=name,
            arguments=arguments,
            result=result,
            ok=ok,
        )

    monkeypatch.setattr(RunBudgetMeter, "record_tool_outcome", _successes_only)

    broken_store = ConversationStore(tmp_path / "broken")
    _turn(
        tmp_path,
        provider=SourcingProvider(tool_name="board_get", arguments={}),
        store=broken_store,
        run_budget_policy=RunBudgetPolicy(model_turns=3),
    )
    broken = _terminal(broken_store)["run_budget"]
    assert [
        event for event in _events(broken_store) if event["type"] == "tool_finished"
    ]
    assert broken["completed"] == []
