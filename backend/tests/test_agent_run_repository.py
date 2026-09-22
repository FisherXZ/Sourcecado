"""Slice 1: the durable Agent Run store — identity, correlation, redaction."""

import os
import sqlite3
import stat

import pytest

from coworker.agent_run_repository import (
    DB_NAME,
    SCHEMA_VERSION,
    AgentRunLeaseLost,
    AgentRunRepository,
)
from coworker.agent_run_state import AgentRunTransitionError

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


def _stored_bytes(tmp_path) -> bytes:
    blob = b""
    for suffix in ("", "-wal", "-shm"):
        path = tmp_path / f"{DB_NAME}{suffix}"
        if path.exists():
            blob += path.read_bytes()
    return blob


def test_schema_is_versioned_private_and_idempotent(tmp_path):
    repo = AgentRunRepository(tmp_path)
    path = tmp_path / DB_NAME
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        tables = {
            row[0]
            for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert {"agent_runs", "agent_run_checkpoints"} <= tables
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    repo.close()

    reopened = AgentRunRepository(tmp_path)
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    reopened.close()


def test_a_newer_schema_is_refused_rather_than_written_to(tmp_path):
    AgentRunRepository(tmp_path).close()
    with sqlite3.connect(tmp_path / DB_NAME) as db:
        db.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    with pytest.raises(RuntimeError, match="newer"):
        AgentRunRepository(tmp_path)


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
    assert GOAL.encode() not in _stored_bytes(tmp_path)


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

    blob = _stored_bytes(tmp_path)
    assert b"gmail_send" in blob, "the checkpoint must really be on disk"
    for secret in (
        planted["api_key"],
        "Bearer ZZZZZZZZZZZZZZZZZZZZZZ",
        planted["message"],
        planted["reasoning"],
        "private tool output row",
        "access_token",
    ):
        assert secret.encode() not in blob, secret
