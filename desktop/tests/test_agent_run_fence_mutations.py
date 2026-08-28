"""Each guard is broken on purpose, and the property it protects must collapse.

A green fence test proves nothing if it would also pass with the fence removed.
Every test here runs the honest path first, asserts the property, then breaks
exactly one guard and asserts the same property fails. If one of these starts
failing, the guard it names has stopped doing work and the test that covers it
has gone vacuous.

The four failure modes this slice is most exposed to, one test each:

- the dispatch committed *after* the call instead of before it;
- a consequential tool wired without the fence, because someone wrote a list;
- a resume that treats a quarantined effect as work to continue;
- the inbox overriding the run store when the two disagree.

The first is the one that matters most. A system with the ordering reversed
still writes effect records, still shows a review queue, and still passes any
test that only asks "was an effect recorded?". It is protected against nothing,
and only an observation taken from inside the call can tell the difference.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from coworker import agent_run_dispatch as dispatch_module
from coworker import agent_run_reconcile as reconcile_module
from coworker import agent_run_resume as resume_module
from coworker import server as server_module
from coworker import turn as turn_module
from coworker.agent_run_approval import EffectStatus
from coworker.agent_run_repository import AgentRunRepository
from coworker.agent_run_resume import ResumeAction, classify_resume, restart
from coworker.agent_run_reconcile import review_queue, supersedes_inbox
from coworker.store import ConversationStore

from tests.test_agent_run_integration import (
    ACCOUNT,
    HEADERS,
    OneToolProvider,
    SID,
    WatchingGmail,
    _app,
    _bound_person,
    _effects,
    _park_send,
    _runs,
    _turn,
)


# --- mutation: the dispatch commits after the call -------------------------


def _watched_send(tmp_path) -> tuple[Any, WatchingGmail]:
    """One approved send, with a Gmail that reads the run store mid-call."""
    app = _app(tmp_path)
    gmail = WatchingGmail(app.state.agent_runs.path)
    gmail.account_email = ACCOUNT
    app.state.gmail = gmail
    client = TestClient(app)
    person_id, session_id = _bound_person(app)
    item_id = _park_send(client, person_id, session_id)
    res = client.post(
        f"/v1/inbox/{item_id}",
        headers=HEADERS,
        json={"decision": "allow", "actor": "Fisher", "scope": "once"},
    )
    assert res.status_code == 200, res.text
    assert len(gmail.sends) == 1, "the send never happened"
    return app, gmail


async def _dispatch_after_the_call(
    context,
    *,
    tool_name,
    arguments,
    call,
    tool_call_id=None,
    approval_id=None,
    step=None,
):
    """The mutation: same writes, same records, wrong order."""
    if not dispatch_module.needs_fence(tool_name) or context is None:
        return await call()
    ok, result = await call()
    effect_id = context.dispatch(
        tool_name=tool_name,
        arguments=arguments,
        tool_call_id=tool_call_id,
        approval_id=approval_id,
        step=step,
    )
    context.record(effect_id, ok=ok)
    return ok, result


def test_a_dispatch_committed_after_the_call_leaves_the_window_unrecorded(
    tmp_path, monkeypatch
):
    """The ordering, and only the ordering.

    Both runs below end with one `succeeded` effect row. The difference is what
    was on disk while Gmail was mid-send, which is exactly what a process that
    died there would have left for a restart to find.
    """
    _honest_app, honest = _watched_send(tmp_path / "honest")
    assert honest.seen == [("gmail_send", str(EffectStatus.DISPATCHED))]

    monkeypatch.setattr(server_module, "guarded_call", _dispatch_after_the_call)

    broken_app, broken = _watched_send(tmp_path / "broken")

    # Nothing was on disk during the call: a crash there is unrecoverable.
    assert broken.seen == []
    # And the giveaway, which is why this test reads from inside the call:
    # the finished record is identical, so every after-the-fact assertion
    # about it still passes.
    assert [effect["status"] for effect in _effects(broken_app.state.agent_runs)] == [
        str(EffectStatus.SUCCEEDED)
    ]


def test_the_same_reversal_in_the_turn_loop_is_caught_the_same_way(
    tmp_path, monkeypatch
):
    """Both dispatch sites, one guard, one mutation each.

    `turn.py` and `server.py` reach a tool through the same function, so this
    is not a second guard -- it is the second call site, held to the contract
    by the same observation.
    """
    import sqlite3

    repository, owner = _runs(tmp_path / "honest")

    def _watcher(store):
        def _execute(name, arguments, **kwargs):
            connection = sqlite3.connect(store.path)
            try:
                store.seen = connection.execute(
                    "SELECT tool_name, status FROM agent_run_effects"
                ).fetchall()
            finally:
                connection.close()
            return True, {"ok": True}

        return _execute

    monkeypatch.setattr(turn_module, "execute", _watcher(repository))
    _turn(
        tmp_path / "honest",
        provider=OneToolProvider("gmail_send"),
        repository=repository,
        owner=owner,
        wait="allow",
    )
    assert [tuple(map(str, row)) for row in repository.seen] == [
        ("gmail_send", str(EffectStatus.DISPATCHED))
    ]

    monkeypatch.setattr(turn_module, "guarded_call", _dispatch_after_the_call)

    broken, broken_owner = _runs(tmp_path / "broken")
    monkeypatch.setattr(turn_module, "execute", _watcher(broken))
    _turn(
        tmp_path / "broken",
        provider=OneToolProvider("gmail_send"),
        repository=broken,
        owner=broken_owner,
        wait="allow",
    )

    assert broken.seen == []
    assert [effect["status"] for effect in _effects(broken)] == [
        str(EffectStatus.SUCCEEDED)
    ]


# --- mutation: a consequential tool wired without the fence ----------------


def _fenced_tool_run(tmp_path, tool_name, monkeypatch):
    executed: list[str] = []

    def _execute(name, arguments, **kwargs):
        executed.append(name)
        return True, {"ok": True}

    monkeypatch.setattr(turn_module, "execute", _execute)
    repository, owner = _runs(tmp_path)
    _turn(
        tmp_path,
        provider=OneToolProvider(tool_name),
        repository=repository,
        owner=owner,
        wait="allow",
    )
    assert executed == [tool_name], f"{tool_name} never ran"
    return repository


def test_a_hand_written_fence_list_lets_every_other_consequential_tool_through(
    tmp_path, monkeypatch
):
    """`RETRY_SAFE` decides which tools are fenced. A list does not.

    The mutation is the mistake a person would actually make: fence the one
    tool everybody thinks of, and let the rest of the consequential set past.
    """
    honest = _fenced_tool_run(tmp_path / "honest", "apollo_enrich_contact", monkeypatch)
    assert [effect["tool_name"] for effect in _effects(honest)] == [
        "apollo_enrich_contact"
    ]

    monkeypatch.setattr(
        dispatch_module, "needs_fence", lambda name: name == "gmail_send"
    )

    broken = _fenced_tool_run(tmp_path / "broken", "apollo_enrich_contact", monkeypatch)

    # An Apollo enrichment spends real credits and left no record that it was
    # attempted. A crash mid-call is a charge nobody can account for.
    assert _effects(broken) == []
    # `gmail_send` still works, which is why the gap would go unnoticed.
    still_fenced = _fenced_tool_run(tmp_path / "still", "gmail_send", monkeypatch)
    assert [effect["tool_name"] for effect in _effects(still_fenced)] == ["gmail_send"]


# --- mutation: a resume that retries an ambiguous effect -------------------


def _quarantined_run(tmp_path):
    repository = AgentRunRepository(tmp_path)
    owner = repository.registry.register()
    started = repository.create_run(
        session_id=SID, trigger="chat", goal="send it", owner=owner
    )
    commit = repository.dispatch_effect(
        started.lease, tool_name="gmail_send", arguments={"draft_id": "d1"}
    )
    repository.quarantine_effect(
        commit.lease, commit.effect["effect_id"], reason="the owner died"
    )
    run = repository.get_run(started.run["run_id"])
    return repository, run


def test_a_resume_that_ignores_effects_would_send_the_same_mail_twice(
    tmp_path, monkeypatch
):
    """Effects outrank the run's own state, and that precedence is the guard.

    Without it the run looks like ordinary interrupted work with a tool step
    left to finish, and finishing it means calling `gmail_send` again.
    """
    repository, run = _quarantined_run(tmp_path / "honest")
    checkpoints = repository.list_checkpoints(run["run_id"])
    effects = repository.list_effects(run["run_id"])
    # Non-vacuity: the run really is quarantined and really does look resumable.
    assert effects[0]["status"] == str(EffectStatus.AMBIGUOUS)
    assert run["current_state"] == "interrupted"

    assert classify_resume(run, checkpoints, effects).action is ResumeAction.REVIEW
    # A restart offers it to nobody: `resumable` is what a caller would pick up.
    owner = repository.registry.register("owner-honest-restart")
    assert restart(repository, owner).resumable == ()

    # The mutation: stop letting a quarantined effect outrank the run state.
    monkeypatch.setattr(resume_module, "quarantined", lambda items: [])

    broken = classify_resume(run, checkpoints, effects)

    assert broken.action is ResumeAction.RESUME
    # The operational consequence: the run whose send may already have reached
    # a real person is now on the list a caller resumes.
    offered = restart(repository, owner).resumable
    assert [verdict.run_id for verdict in offered] == [run["run_id"]]


# --- mutation: the inbox overriding the run store --------------------------


def _disagreeing_stores(tmp_path):
    """One approved send: the inbox says interrupted, the run store ambiguous."""
    conversations = ConversationStore(tmp_path / "conv")
    repository = AgentRunRepository(tmp_path / "runs")
    owner = repository.registry.register()
    parked = conversations.park_inbox(
        "send_1", "gmail_send", {"draft_id": "d1"}, session_id=SID
    )
    conversations.decide_and_claim_inbox_execution(
        parked["id"], "allow", actor="Fisher", scope="once", claimant="http:1"
    )
    started = repository.create_run(
        session_id=SID, trigger="chat", goal="send", owner=owner
    )
    commit = repository.dispatch_effect(
        started.lease,
        tool_name="gmail_send",
        arguments={"draft_id": "d1"},
        approval_id=parked["id"],
    )
    repository.quarantine_effect(
        commit.lease, commit.effect["effect_id"], reason="owner died"
    )
    reopened = ConversationStore(tmp_path / "conv")
    item = reopened.get_inbox(parked["id"])
    assert item["execution_status"] == "interrupted", "the stores never disagreed"
    return repository, reopened


def test_letting_the_inbox_win_turns_an_unknown_outcome_into_a_failure(
    tmp_path, monkeypatch
):
    """The run store is the fence of record when the two stores disagree.

    Break that and the queue tells an operator the send was interrupted, which
    reads as "it did not go out" and invites exactly one more send.
    """
    repository, approvals = _disagreeing_stores(tmp_path / "honest")
    honest = review_queue(repository, approvals)
    assert honest[0]["status"] == str(EffectStatus.AMBIGUOUS)
    assert honest[0]["needs_a_person"] is True
    assert honest[0]["supersedes_inbox"] is True

    # The mutation: prefer whatever the inbox concluded about its claimant.
    monkeypatch.setattr(
        reconcile_module,
        "reconciled_status",
        lambda effect, approval: (
            str(approval.get("execution_status"))
            if approval is not None
            else str(effect.get("status"))
        ),
    )

    broken = review_queue(repository, approvals)

    assert broken[0]["status"] == "interrupted"
    # The row no longer says the two stores disagree, so no surface can know.
    assert broken[0]["supersedes_inbox"] is False
    assert supersedes_inbox(
        {"status": str(EffectStatus.AMBIGUOUS)},
        {"execution_status": "interrupted"},
    ) is False


# --- mutation: the fence disarmed at the call site -------------------------


def test_a_turn_that_forgets_to_pass_its_run_store_is_fenced_by_nothing(
    tmp_path, monkeypatch
):
    """Why `test_agent_run_integration` checks all three triggers behaviourally.

    `run_turn` without a run store is the legacy path, and it is unfenced by
    design. That is safe only for as long as every production caller passes
    one, which is a property of the call sites and not of this module.
    """
    executed: list[str] = []

    def _execute(name, arguments, **kwargs):
        executed.append(name)
        return True, {"ok": True}

    monkeypatch.setattr(turn_module, "execute", _execute)

    repository, owner = _runs(tmp_path / "wired")
    _turn(
        tmp_path / "wired",
        provider=OneToolProvider("gmail_send"),
        repository=repository,
        owner=owner,
        wait="allow",
    )
    assert len(_effects(repository)) == 1

    # The mutation: the caller drops the run store, as `create_app` did before
    # this slice. Nothing raises and nothing is recorded.
    unwired, _owner = _runs(tmp_path / "unwired")
    _turn(
        tmp_path / "unwired",
        provider=OneToolProvider("gmail_send"),
        repository=None,
        owner=None,
        wait="allow",
    )

    assert executed == ["gmail_send", "gmail_send"], "one of the sends never ran"
    assert _effects(unwired) == []
