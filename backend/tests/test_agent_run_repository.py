"""Slice 1: the durable Agent Run store — identity, correlation, redaction."""

import pytest

from coworker.agent_run_repository import (
    AgentRunLeaseLost,
    AgentRunRepository,
)
from coworker.agent_run_state import AgentRunTransitionError
from tests.agent_run_db import stored_text

GOAL = "Find three Codeology leads at Ramp"


def _repo(tmp_path):
    repo = AgentRunRepository(tmp_path)
    return repo, repo.registry.register()


def _start(repo, owner, **kwargs):
    return repo.create_run(
        session_id=kwargs.pop("session_id", "sess-1"),
        trigger=kwargs.pop("trigger", "chat"),
        goal=kwargs.pop("goal", GOAL),
        owner=owner,
        **kwargs,
    )


def test_a_run_is_one_identity_shared_by_every_trigger(tmp_path):
    repo, owner = _repo(tmp_path)
    triggers = ("chat", "queued_chat", "scheduled")
    runs = [
        _start(repo, owner, session_id=f"sess-{trigger}", trigger=trigger).run
        for trigger in triggers
    ]
    assert [run["trigger"] for run in runs] == list(triggers)
    assert len({run["run_id"] for run in runs}) == 3
    for run in runs:
        assert run["current_state"] == "running"
        assert run["version"] == 1
        assert run["checkpoint_sequence"] == 1
        assert run["goal_fingerprint"] == runs[0]["goal_fingerprint"]
        assert repo.get_run(run["run_id"]) == run
    with pytest.raises(ValueError):
        _start(repo, owner, trigger="telepathy")
    with pytest.raises(ValueError):
        _start(repo, owner, session_id="../escape")


def test_a_created_run_carries_its_correlation_and_never_the_goal_text(tmp_path):
    repo, owner = _repo(tmp_path)
    run = _start(
        repo,
        owner,
        session_id="sess-1",
        person_id="person-7",
        parent_run_id=None,
        provider_model_id="fake-model",
    ).run

    assert repo.get_run(run["run_id"])["person_id"] == "person-7"
    assert run["approval_ids"] == []
    assert run["source_refs"] == []
    assert run["artifact_refs"] == []
    assert run["usage"] == {}
    assert run["terminal_result"] is None
    assert run["finished_at"] is None
    assert "goal" not in run
    checkpoints = repo.list_checkpoints(run["run_id"])
    assert [item["kind"] for item in checkpoints] == ["run_started"]
    assert GOAL.encode() not in stored_text(repo)


def test_checkpoints_accumulate_correlation_and_bump_the_sequence(tmp_path):
    repo, owner = _repo(tmp_path)
    started = _start(repo, owner)
    commit = repo.checkpoint(
        started.lease,
        kind="model_completed",
        payload={"step": 1, "model_id": "fake-model"},
        usage={"input_tokens": 10, "output_tokens": 4},
        source_refs=[{"id": "src-1", "title": "Ramp", "url": "https://a.test/x"}],
        approval_ids=["ap-1"],
    )
    second = repo.checkpoint(
        commit.lease,
        kind="tool_completed",
        payload={"step": 2, "tool_name": "apollo_search", "item_count": 3},
        usage={"input_tokens": 5},
        source_refs=[{"id": "src-1", "title": "Ramp again"}],
        artifact_refs=[{"id": "art-1", "artifact_type": "brief", "title": "Brief"}],
        approval_ids=["ap-1", "ap-2"],
    )

    run = second.run
    assert run["checkpoint_sequence"] == 3
    assert run["version"] == started.run["version"] + 2
    assert run["usage"] == {"input_tokens": 15, "output_tokens": 4}
    assert run["approval_ids"] == ["ap-1", "ap-2"]
    assert [ref["id"] for ref in run["source_refs"]] == ["src-1"]
    assert run["source_refs"][0]["title"] == "Ramp"
    assert [ref["id"] for ref in run["artifact_refs"]] == ["art-1"]
    assert [item["sequence"] for item in repo.list_checkpoints(run["run_id"])] == [
        1,
        2,
        3,
    ]
    assert repo.list_checkpoints(run["run_id"])[2]["payload"] == {
        "step": 2,
        "tool_name": "apollo_search",
        "item_count": 3,
    }
    assert second.checkpoint["state"] == "running"


def test_a_terminal_checkpoint_closes_the_run_and_drops_the_lease(tmp_path):
    repo, owner = _repo(tmp_path)
    started = _start(repo, owner)
    commit = repo.checkpoint(
        started.lease,
        kind="terminal",
        state="complete",
        terminal_result={"status": "complete", "text": "the answer the operator reads"},
    )

    assert commit.lease is None
    run = commit.run
    assert run["current_state"] == "complete"
    assert run["finished_at"] is not None
    assert run["lease_owner"] is None
    assert run["terminal_result"] == {
        "status": "complete",
        "text_length": len("the answer the operator reads"),
    }
    with pytest.raises(AgentRunLeaseLost):
        repo.checkpoint(started.lease, kind="model_completed")
    assert repo.acquire_lease(run["run_id"], owner, 60) is None


def test_an_impossible_transition_is_refused_and_writes_nothing(tmp_path):
    repo, owner = _repo(tmp_path)
    started = _start(repo, owner)
    with pytest.raises(AgentRunTransitionError):
        repo.checkpoint(started.lease, kind="model_completed", state="waiting_approval")
    run = repo.get_run(started.run["run_id"])
    assert run["current_state"] == "running"
    assert run["version"] == started.run["version"]
    assert len(repo.list_checkpoints(run["run_id"])) == 1


def test_runs_are_listable_by_session_and_state(tmp_path):
    repo, owner = _repo(tmp_path)
    first = _start(repo, owner, session_id="sess-a")
    second = _start(repo, owner, session_id="sess-a")
    other = _start(repo, owner, session_id="sess-b")
    repo.checkpoint(second.lease, kind="terminal", state="failed")

    assert [run["run_id"] for run in repo.list_runs(session_id="sess-a")] == [
        second.run["run_id"],
        first.run["run_id"],
    ]
    assert [
        run["run_id"] for run in repo.list_runs(session_id="sess-a", states=("running",))
    ] == [first.run["run_id"]]
    assert {run["run_id"] for run in repo.list_runs()} == {
        first.run["run_id"],
        second.run["run_id"],
        other.run["run_id"],
    }
    assert repo.get_run("missing") is None
    assert repo.list_checkpoints("missing") == []


def test_planted_secrets_never_reach_the_persisted_checkpoint(tmp_path):
    """Non-vacuous: the checkpoint must exist and be non-empty first."""
    repo, owner = _repo(tmp_path)
    started = _start(repo, owner)
    planted = {
        "api_key": "sk-proj-ZZZZZZZZZZZZZZZZZZZZZZZZZZZZ",
        "authorization": "Bearer ZZZZZZZZZZZZZZZZZZZZZZ",
        "message": "the operator wrote something private here",
        "reasoning": "raw model reasoning that must never persist",
        "result": {"rows": ["private tool output row"]},
    }
    commit = repo.checkpoint(
        started.lease,
        kind="tool_completed",
        payload={
            "step": 4,
            "tool_name": "gmail_send",
            "error_summary": f"denied with {planted['api_key']}",
            **planted,
        },
        source_refs=[
            {
                "id": "src-9",
                "title": "Ramp",
                "url": "https://a.test/p?access_token=ZZZZZZZZZZZZZZZZZZZZZZ",
            }
        ],
    )

    stored = repo.list_checkpoints(started.run["run_id"])[-1]
    assert stored["sequence"] == 2
    assert stored["kind"] == "tool_completed"
    assert stored["payload"], "the checkpoint payload must not be empty"
    assert stored["payload"]["step"] == 4
    assert stored["payload"]["tool_name"] == "gmail_send"
    assert stored["payload"]["error_summary"].startswith("denied with ")
    assert commit.run["source_refs"][0]["url"] == "https://a.test/p"

    blob = stored_text(repo)
    assert b"gmail_send" in blob, "the checkpoint must really be stored"
    for secret in (
        planted["api_key"],
        "Bearer ZZZZZZZZZZZZZZZZZZZZZZ",
        planted["message"],
        planted["reasoning"],
        "private tool output row",
        "access_token",
    ):
        assert secret.encode() not in blob, secret


def test_two_users_have_separate_runs_and_cannot_touch_each_other(tmp_path):
    """Two state directories are two users. Neither reaches the other by id."""
    from datetime import UTC, datetime, timedelta

    from coworker.agent_run_approval import AgentRunEffectFenced
    from coworker.agent_run_repository import AgentRunLease

    now = datetime(2026, 8, 27, 10, 0, 0, tzinfo=UTC)
    alice, alice_owner = _repo(tmp_path / "alice")
    bob, bob_owner = _repo(tmp_path / "bob")
    assert alice._user_id != bob._user_id

    started = _start(alice, alice_owner, now=now, lease_seconds=60)
    run_id = started.run["run_id"]
    dispatch = alice.dispatch_effect(
        started.lease,
        tool_name="gmail_send",
        arguments={"to": "dana@ramp.test"},
        approval_id="appr-1",
        now=now,
    )
    effect_id = dispatch.effect["effect_id"]
    alice.quarantine_effect(dispatch.lease, effect_id, reason="cut", now=now)
    before = alice.get_run(run_id)
    checkpoints = alice.list_checkpoints(run_id)
    effects = alice.list_effects(run_id)

    # Bob reads nothing of Alice's, by id or by listing.
    assert bob.get_run(run_id) is None
    assert bob.list_runs() == []
    assert bob.list_checkpoints(run_id) == []
    assert bob.list_effects(run_id) == []
    assert bob.effects_for_approval("appr-1") == []
    assert bob.list_quarantined_effects() == []

    # Bob changes nothing of Alice's, with or without a forged lease.
    forged = AgentRunLease(run_id, bob_owner.owner_id, before["version"], "9999")
    with pytest.raises(KeyError):
        bob.acquire_lease(run_id, bob_owner, 600, now=now)
    with pytest.raises(KeyError):
        bob.checkpoint(forged, kind="model_completed", now=now)
    with pytest.raises(KeyError):
        bob.renew_lease(forged, now=now)
    with pytest.raises(KeyError):
        bob.release_lease(forged, now=now)
    with pytest.raises(KeyError):
        bob.resume_from_approval(
            run_id, bob_owner, approval_id="appr-1", decision="allow", now=now
        )
    with pytest.raises(KeyError):
        bob.dispatch_effect(forged, tool_name="gmail_send", now=now)
    with pytest.raises((KeyError, AgentRunEffectFenced)):
        bob.resolve_quarantined_effect(
            effect_id, decision="abandoned", operator="bob"
        )
    later = now + timedelta(days=30)
    assert bob.reconcile_expired_leases(now=later) == []
    assert bob.reclaim_dead_owner_leases(now=later) == []
    assert bob.prune_checkpoints(finished_before=later) == []

    # The same run id is free for Bob, and stays two runs.
    twin = _start(bob, bob_owner, run_id=run_id, now=now)
    assert twin.run["run_id"] == run_id
    assert [run["session_id"] for run in bob.list_runs()] == ["sess-1"]
    assert len(alice.list_runs()) == 1

    assert alice.get_run(run_id) == before
    assert alice.list_checkpoints(run_id) == checkpoints
    assert alice.list_effects(run_id) == effects
