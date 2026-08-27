import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier

import pytest

from coworker.agent_run_continuation import transcript_prefix_sha256
from coworker.agent_run_execution import (
    AgentRunExecution,
    AgentRunExecutionOwnershipError,
)
from coworker.agent_run_repository import AgentRunLeaseLost
from coworker.events import TurnIdentity
from coworker.inbox import Inbox
from coworker.store import ConversationStore


NOW = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
EMPTY_SHA256 = transcript_prefix_sha256([])


def _identity(run_id="run-execution", session_id="thread-execution"):
    return TurnIdentity(
        session_id=session_id,
        run_id=run_id,
        message_id=f"message-{run_id}",
        part_id=f"part-{run_id}",
    )


def _start(store, *, run_id="run-execution", owner_id="owner-a", max_steps=4):
    identity = _identity(run_id)
    execution = AgentRunExecution.start(
        store,
        identity,
        "Find candidates",
        "chat",
        "fake-model",
        max_steps,
        owner_id=owner_id,
        now=NOW,
    )
    return identity, execution


def _lease_columns(store, run_id):
    with sqlite3.connect(store.db_path) as db:
        return db.execute(
            "SELECT lease_owner, lease_expires_at FROM agent_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()


def _park(store, identity, interaction_id, *, run_id=None):
    return Inbox(store).park(
        "gmail_draft",
        {"body": "PRIVATE_APPROVAL_ARGUMENT"},
        item_id=interaction_id,
        session_id=identity.session_id,
        run_id=run_id if run_id is not None else identity.run_id,
        message_id=identity.message_id,
        part_id=identity.part_id,
    )


def test_resolved_waiting_lease_requires_exact_resolved_inbox_and_has_one_owner(
    tmp_path,
):
    first = ConversationStore(tmp_path)
    second = ConversationStore(tmp_path)
    identity, execution = _start(first, run_id="run-waiting")
    _park(first, identity, "approval-1")
    execution.waiting_approval([], [], "approval-1")
    waiting = first.get_agent_run(identity.run_id)

    assert (
        first.agent_runs.acquire_resolved_waiting_lease(
            identity.run_id,
            "owner-b",
            waiting["version"],
            "approval-1",
            30,
            now=NOW,
        )
        is None
    )
    assert (
        first.agent_runs.acquire_lease(
            identity.run_id, "owner-b", waiting["version"], 30, now=NOW
        )
        is None
    )
    Inbox(first).resolve("approval-1", "allow")
    assert (
        first.agent_runs.acquire_resolved_waiting_lease(
            identity.run_id,
            "owner-b",
            waiting["version"],
            "wrong-id",
            30,
            now=NOW,
        )
        is None
    )

    barrier = Barrier(2)

    def acquire(store, owner):
        barrier.wait()
        return store.agent_runs.acquire_resolved_waiting_lease(
            identity.run_id,
            owner,
            waiting["version"],
            "approval-1",
            30,
            now=NOW,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(acquire, (first, second), ("owner-b", "owner-c"))
        )

    assert sum(result is not None for result in results) == 1
    assert first.get_agent_run(identity.run_id)["current_state"] == "running"

    wrong_identity, wrong_execution = _start(
        first, run_id="run-wrong-inbox", owner_id="owner-wrong"
    )
    _park(
        first,
        wrong_identity,
        "approval-wrong-run",
        run_id="some-other-run",
    )
    Inbox(first).resolve("approval-wrong-run", "deny")
    wrong_execution.waiting_approval([], [], "approval-wrong-run")
    wrong_waiting = first.get_agent_run(wrong_identity.run_id)
    assert (
        first.agent_runs.acquire_resolved_waiting_lease(
            wrong_identity.run_id,
            "owner-new",
            wrong_waiting["version"],
            "approval-wrong-run",
            30,
            now=NOW,
        )
        is None
    )


@pytest.mark.parametrize("decision", ("allow", "deny"))
def test_resolved_allow_and_deny_each_reacquire_waiting_run(tmp_path, decision):
    store = ConversationStore(tmp_path)
    identity, execution = _start(store, run_id=f"run-{decision}")
    interaction_id = f"approval-{decision}"
    _park(store, identity, interaction_id)
    execution.waiting_approval([], [], interaction_id)
    waiting = store.get_agent_run(identity.run_id)
    Inbox(store).resolve(interaction_id, decision)

    lease = store.acquire_resolved_waiting_lease(
        identity.run_id,
        "owner-resume",
        waiting["version"],
        interaction_id,
        30,
        now=NOW,
    )

    assert lease is not None
    assert lease.owner_id == "owner-resume"


def test_start_initializes_identity_budgets_prefixes_and_single_owner(tmp_path):
    first = ConversationStore(tmp_path)
    second = ConversationStore(tmp_path)
    identity = _identity("run-start")
    barrier = Barrier(2)

    def start(store, owner):
        barrier.wait()
        try:
            return AgentRunExecution.start(
                store,
                identity,
                "Find candidates",
                "chat",
                "provider-independent-model",
                5,
                owner_id=owner,
                now=NOW,
            )
        except AgentRunExecutionOwnershipError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        contexts = list(
            pool.map(start, (first, second), ("execution-a", "execution-b"))
        )

    assert sum(context is not None for context in contexts) == 1
    context = next(context for context in contexts if context is not None)
    assert context.run_id == identity.run_id
    assert context.owner_id in {"execution-a", "execution-b"}
    assert context.lease is not None
    assert not hasattr(context, "continuation")
    assert context.metadata == {
        "run_id": identity.run_id,
        "owner_id": context.owner_id,
        "phase": "model_ready",
        "step_index": 0,
        "next_tool_index": 0,
        "visible_partial": {
            "message_id": identity.message_id,
            "text_length": 0,
            "truncated": False,
        },
        "pending_interaction": None,
        "remaining_budgets": {
            "work_steps": 5,
            "tool_calls": 5,
            "delivery_passes": 1,
        },
    }
    continuation = first.get_agent_run(identity.run_id)["continuation"]
    assert continuation["identity"] == {
        "message_id": identity.message_id,
        "part_id": identity.part_id,
    }
    assert continuation["cursor"] == {
        "phase": "model_ready",
        "step_index": 0,
        "next_tool_index": 0,
        "transcript_prefix_count": 0,
        "transcript_prefix_sha256": EMPTY_SHA256,
        "event_prefix_count": 0,
        "event_prefix_sha256": EMPTY_SHA256,
    }


def test_start_rejects_interrupted_review_required_continuation(tmp_path):
    store = ConversationStore(tmp_path)
    started = store.start_agent_run(
        run_id="run-review-required",
        session_id="thread-execution",
        trigger="chat",
        original_goal="Find candidates",
        provider_model_id="fake-model",
    )
    lease = store.agent_runs.acquire_lease(
        "run-review-required", "old-owner", started["version"], 30, now=NOW
    )
    lease, _checkpoint = store.agent_runs.checkpoint_leased(
        lease,
        "process_interrupted",
        {
            "identity": {
                "message_id": "message-run-review-required",
                "part_id": "part-run-review-required",
            },
            "cursor": {
                "phase": "review_required",
                "step_index": 0,
                "next_tool_index": 0,
            },
            "remaining_budgets": {
                "work_steps": 4,
                "tool_calls": 4,
                "delivery_passes": 1,
            },
        },
        state="interrupted",
        now=NOW,
    )
    store.agent_runs.release_lease(lease, now=NOW)

    with pytest.raises(AgentRunExecutionOwnershipError, match="review"):
        AgentRunExecution.start(
            store,
            _identity("run-review-required"),
            "Find candidates",
            "chat",
            "fake-model",
            4,
            owner_id="new-owner",
            now=NOW,
        )


def test_pending_boundaries_decrement_once_and_exact_retries_are_idempotent(
    tmp_path,
):
    store = ConversationStore(tmp_path)
    identity, execution = _start(store)
    history = [{"role": "user", "content": "Find candidates"}]
    events = [{"type": "turn_start", "run_id": identity.run_id}]

    execution.model_pending(history, events, 0)
    first_model_lease = execution.lease
    execution.model_pending(history, events, 0)
    assert execution.lease == first_model_lease
    execution.model_completed(history, events, 0, True, 0)
    execution.tool_pending(history, events, 0, 0, "call-1", "search", True)
    first_tool_lease = execution.lease
    execution.tool_pending(history, events, 0, 0, "call-1", "search", True)
    assert execution.lease == first_tool_lease
    execution.tool_completed(
        history,
        events,
        0,
        0,
        "call-1",
        "search",
        True,
        "a" * 64,
    )
    execution.tool_pending(history, events, 0, 1, "call-2", "search", True)

    continuation = store.get_agent_run(identity.run_id)["continuation"]
    assert continuation["cursor"]["phase"] == "tool_in_flight"
    assert continuation["cursor"]["next_tool_index"] == 1
    assert continuation["remaining_budgets"] == {
        "work_steps": 3,
        "tool_calls": 2,
        "delivery_passes": 1,
    }
    assert [
        checkpoint["kind"]
        for checkpoint in store.list_agent_run_checkpoints(identity.run_id)
    ] == [
        "run_started",
        "model_pending",
        "model_completed",
        "tool_pending",
        "tool_completed",
        "tool_pending",
    ]


def test_pending_retries_recover_after_commit_acknowledgement_loss(
    tmp_path, monkeypatch
):
    store = ConversationStore(tmp_path)
    _identity_value, execution = _start(store)
    original = store.agent_runs.checkpoint_leased
    lose_ack_for = {"model_pending", "tool_pending"}

    def commit_then_lose_ack(lease, kind, continuation, **kwargs):
        result = original(lease, kind, continuation, **kwargs)
        if kind in lose_ack_for:
            lose_ack_for.remove(kind)
            raise RuntimeError(f"lost {kind} acknowledgement")
        return result

    monkeypatch.setattr(
        store.agent_runs, "checkpoint_leased", commit_then_lose_ack
    )

    with pytest.raises(RuntimeError, match="model_pending"):
        execution.model_pending([], [], 0)
    execution.model_pending([], [], 0)
    execution.model_completed([], [], 0, True, 0)
    with pytest.raises(RuntimeError, match="tool_pending"):
        execution.tool_pending([], [], 0, 0, "call-1", "search", True)
    execution.tool_pending([], [], 0, 0, "call-1", "search", True)

    continuation = store.get_agent_run(execution.run_id)["continuation"]
    assert continuation["remaining_budgets"] == {
        "work_steps": 3,
        "tool_calls": 3,
        "delivery_passes": 1,
    }
    assert [
        item["kind"] for item in store.list_agent_run_checkpoints(execution.run_id)
    ].count("model_pending") == 1
    assert [
        item["kind"] for item in store.list_agent_run_checkpoints(execution.run_id)
    ].count("tool_pending") == 1


def test_completed_boundaries_update_prefixes_and_append_digest_only_receipt(
    tmp_path,
):
    store = ConversationStore(tmp_path)
    identity, execution = _start(store)
    history = [
        {"role": "user", "content": "PRIVATE_TRANSCRIPT_VALUE"},
        {"role": "assistant", "tool_calls": [{"id": "call-1"}]},
    ]
    events = [{"type": "tool_finished", "private": "PRIVATE_EVENT_VALUE"}]
    result_digest = hashlib.sha256(b"PRIVATE_RESULT_VALUE").hexdigest()

    execution.model_pending(history[:1], [], 0)
    execution.model_completed(history, [], 0, True, 0)
    assert execution.metadata["phase"] == "tools_ready"
    execution.tool_pending(
        history, events, 0, 0, "call-1", "apollo_search", True
    )
    execution.tool_completed(
        history,
        events,
        0,
        0,
        "call-1",
        "apollo_search",
        True,
        result_digest,
    )

    continuation = store.get_agent_run(identity.run_id)["continuation"]
    cursor = continuation["cursor"]
    assert cursor["phase"] == "tools_ready"
    assert cursor["next_tool_index"] == 1
    assert cursor["transcript_prefix_count"] == len(history)
    assert cursor["transcript_prefix_sha256"] == transcript_prefix_sha256(history)
    assert cursor["event_prefix_count"] == len(events)
    assert cursor["event_prefix_sha256"] == transcript_prefix_sha256(events)
    assert "pending_tool" not in continuation
    assert continuation["completed_tool_receipts"] == [
        {
            "attempt_id": f"{identity.run_id}:0:call-1:0",
            "call_id": "call-1",
            "name": "apollo_search",
            "ok": True,
            "transcript_index": len(history) - 1,
            "result_sha256": result_digest,
        }
    ]
    persisted = json.dumps(
        {
            "run": store.get_agent_run(identity.run_id),
            "checkpoints": store.list_agent_run_checkpoints(identity.run_id),
        }
    )
    assert "PRIVATE_TRANSCRIPT_VALUE" not in persisted
    assert "PRIVATE_EVENT_VALUE" not in persisted
    assert "PRIVATE_RESULT_VALUE" not in persisted


def test_waiting_releases_and_resolved_resume_clears_pending_interaction(tmp_path):
    store = ConversationStore(tmp_path)
    identity, execution = _start(store, run_id="run-resume")
    _park(store, identity, "approval-resume")

    execution.waiting_approval([], [], "approval-resume")

    assert execution.lease is None
    assert _lease_columns(store, identity.run_id) == (None, None)
    with pytest.raises(AgentRunExecutionOwnershipError, match="lease"):
        execution.model_pending([], [], 0)
    with pytest.raises(AgentRunExecutionOwnershipError, match="resolved approval"):
        execution.resume_resolved_approval("approval-resume")

    Inbox(store).resolve("approval-resume", "deny")
    execution.resume_resolved_approval("approval-resume")
    assert execution.lease is not None
    assert execution.run_id == identity.run_id
    execution.approval_resolved([], [], "approval-resume", "denied")

    continuation = store.get_agent_run(identity.run_id)["continuation"]
    assert continuation["identity"] == {
        "message_id": identity.message_id,
        "part_id": identity.part_id,
    }
    assert continuation["cursor"]["phase"] == "tools_ready"
    assert "pending_interaction" not in continuation
    assert [
        item["kind"] for item in store.list_agent_run_checkpoints(identity.run_id)
    ][-2:] == ["waiting_approval", "approval_resolved"]


def test_terminal_releases_projects_metadata_and_fences_old_owner(tmp_path):
    store = ConversationStore(tmp_path)
    identity, execution = _start(store, run_id="run-terminal")
    old_lease = execution.lease

    execution.terminal(
        [{"role": "assistant", "content": "PRIVATE_FINAL_TEXT"}],
        [{"type": "turn_end", "text": "PRIVATE_FINAL_TEXT"}],
        "complete",
        "ok",
        identity.message_id,
        len("PRIVATE_FINAL_TEXT"),
    )

    assert execution.lease is None
    assert execution.metadata["phase"] == "complete"
    assert execution.metadata["visible_partial"] == {
        "message_id": identity.message_id,
        "text_length": len("PRIVATE_FINAL_TEXT"),
        "truncated": False,
    }
    with pytest.raises(AgentRunExecutionOwnershipError, match="lease"):
        execution.terminal([], [], "complete", "ok", identity.message_id, 0)
    with pytest.raises((AgentRunLeaseLost, ValueError)):
        store.agent_runs.renew_lease(old_lease, 30, now=NOW)
    persisted = json.dumps(store.get_agent_run(identity.run_id))
    assert "PRIVATE_FINAL_TEXT" not in persisted
    assert store.get_agent_run(identity.run_id)["terminal_result"] == {
        "status": "ok",
        "message_id": identity.message_id,
        "text_length": len("PRIVATE_FINAL_TEXT"),
    }


def test_repository_still_rejects_backwards_cursors_and_budget_increases(tmp_path):
    store = ConversationStore(tmp_path)
    _identity_value, execution = _start(store)
    execution.model_pending([{"role": "user"}], [], 1)
    lease = execution.lease

    with pytest.raises(ValueError, match="cannot decrease"):
        store.agent_runs.update_continuation(
            lease,
            {"cursor": {"step_index": 0}},
            now=NOW,
        )
    with pytest.raises(ValueError, match="cannot increase"):
        store.agent_runs.update_continuation(
            lease,
            {"remaining_budgets": {"work_steps": 4}},
            now=NOW,
        )


def test_prefix_hashes_are_canonical_and_match_every_exact_supplied_array(tmp_path):
    store = ConversationStore(tmp_path)
    identity, execution = _start(store)
    first_history = [
        {"content": {"b": 2, "a": "é"}, "role": "user"},
    ]
    first_events = [{"z": [2, 1], "a": {"nested": True}}]

    execution.model_pending(first_history, first_events, 0)
    first = store.get_agent_run(identity.run_id)["continuation"]["cursor"]
    assert first["transcript_prefix_count"] == len(first_history)
    assert first["transcript_prefix_sha256"] == transcript_prefix_sha256(
        first_history
    )
    assert first["event_prefix_count"] == len(first_events)
    assert first["event_prefix_sha256"] == transcript_prefix_sha256(first_events)

    second_history = [*first_history, {"role": "assistant", "content": "done"}]
    second_events = [*first_events, {"type": "assistant_delta", "delta": "done"}]
    execution.model_completed(second_history, second_events, 0, False, 4)
    second = store.get_agent_run(identity.run_id)["continuation"]["cursor"]
    assert second["transcript_prefix_count"] == len(second_history)
    assert second["transcript_prefix_sha256"] == transcript_prefix_sha256(
        second_history
    )
    assert second["event_prefix_count"] == len(second_events)
    assert second["event_prefix_sha256"] == transcript_prefix_sha256(second_events)
