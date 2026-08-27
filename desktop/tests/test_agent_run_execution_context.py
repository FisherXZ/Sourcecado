import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event

import pytest

import coworker.agent_run_repository as agent_run_repository
from coworker.agent_run_continuation import transcript_prefix_sha256
from coworker.agent_run_execution import (
    AgentRunExecution,
    AgentRunExecutionOwnershipError,
)
from coworker.agent_run_repository import (
    AgentRunLeaseLost,
    AgentRunVersionConflict,
)
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


def _seed_running_phase(store, identity, phase):
    started = store.start_agent_run(
        run_id=identity.run_id,
        session_id=identity.session_id,
        trigger="chat",
        original_goal="Find candidates",
        provider_model_id="fake-model",
    )
    lease = store.agent_runs.acquire_lease(
        identity.run_id, "seed-owner", started["version"], 30, now=NOW
    )
    lease = store.agent_runs.update_continuation(
        lease,
        {
            "identity": {
                "message_id": identity.message_id,
                "part_id": identity.part_id,
            },
            "cursor": {
                "phase": phase,
                "step_index": 0,
                "next_tool_index": 0,
                "transcript_prefix_count": 0,
                "transcript_prefix_sha256": EMPTY_SHA256,
                "event_prefix_count": 0,
                "event_prefix_sha256": EMPTY_SHA256,
            },
            "visible_partial": {
                "message_id": identity.message_id,
                "text_length": 0,
                "truncated": False,
            },
            "completed_tool_receipts": [],
            "remaining_budgets": {
                "work_steps": 4,
                "tool_calls": 4,
                "delivery_passes": 1,
            },
        },
        now=NOW,
    )
    store.agent_runs.release_lease(lease, now=NOW)


def _prepare_one_tool(execution):
    execution.model_pending([], [], 0)
    execution.model_completed([], [], 0, 1, 0)


def _prepare_approved_inflight(store, *, run_id):
    identity, execution = _start(store, run_id=run_id)
    _prepare_one_tool(execution)
    execution.waiting_approval_atomic(
        [],
        [],
        "call-approved",
        0,
        0,
        "call-approved",
        "gmail_draft",
        False,
        arguments={"body": "PRIVATE_APPROVAL_ARGUMENT"},
        reason="review",
        resource=None,
        approval_ttl_seconds=7 * 24 * 60 * 60,
    )
    claimant = "turn:approved-test"
    claim = Inbox(store).decide_and_claim(
        "call-approved",
        "allow",
        actor=None,
        scope="once",
        claimant=claimant,
    )
    assert claim is not None and claim.owned
    resumed = AgentRunExecution.resume_resolved_approval(
        store,
        identity.run_id,
        "call-approved",
        4,
        owner_id="owner-approved",
        now=NOW,
    )
    resumed.approval_resolved([], [], "call-approved")
    resumed.tool_pending(
        [], [], 0, 0, "call-approved", "gmail_draft", False
    )
    return identity, resumed, claimant


def test_atomic_approval_checkpoint_failure_rolls_back_inbox_insert(
    tmp_path, monkeypatch
):
    store = ConversationStore(tmp_path)
    identity, execution = _start(store, run_id="run-atomic-approval-rollback")
    _prepare_one_tool(execution)
    original_merge = agent_run_repository.merge_continuation

    def fail_waiting_merge(existing, incoming):
        if incoming.get("cursor", {}).get("phase") == "waiting_approval":
            raise RuntimeError("forced waiting checkpoint failure")
        return original_merge(existing, incoming)

    monkeypatch.setattr(
        agent_run_repository, "merge_continuation", fail_waiting_merge
    )
    with pytest.raises(RuntimeError, match="waiting checkpoint failure"):
        execution.waiting_approval_atomic(
            [],
            [],
            "approval-atomic",
            0,
            0,
            "call-atomic",
            "gmail_draft",
            False,
            arguments={"body": "PRIVATE_APPROVAL_ARGUMENT"},
            reason="review",
            resource=None,
            approval_ttl_seconds=60,
        )

    assert Inbox(store).get("approval-atomic") is None
    run = store.get_agent_run(identity.run_id)
    assert run["current_state"] == "running"
    assert run["continuation"]["cursor"]["phase"] == "tools_ready"
    assert "waiting_approval" not in {
        item["kind"] for item in store.list_agent_run_checkpoints(identity.run_id)
    }
    assert _lease_columns(store, identity.run_id)[0] == execution.owner_id


def test_atomic_approval_conflict_changes_neither_inbox_nor_run(tmp_path):
    store = ConversationStore(tmp_path)
    identity, execution = _start(store, run_id="run-atomic-approval-conflict")
    _prepare_one_tool(execution)
    Inbox(store).park(
        "gmail_draft",
        {"body": "different body"},
        item_id="approval-conflict",
        session_id=identity.session_id,
        run_id=identity.run_id,
        message_id=identity.message_id,
        part_id=identity.part_id,
    )

    with pytest.raises(ValueError, match="conflicts"):
        execution.waiting_approval_atomic(
            [],
            [],
            "approval-conflict",
            0,
            0,
            "call-conflict",
            "gmail_draft",
            False,
            arguments={"body": "expected body"},
            reason="review",
            resource=None,
            approval_ttl_seconds=60,
        )

    assert Inbox(store).get("approval-conflict")["arguments"] == {
        "body": "different body"
    }
    run = store.get_agent_run(identity.run_id)
    assert run["current_state"] == "running"
    assert run["continuation"]["cursor"]["phase"] == "tools_ready"
    assert _lease_columns(store, identity.run_id)[0] == execution.owner_id


def test_approved_completion_merge_failure_rolls_back_then_classifies_unknown(
    tmp_path, monkeypatch
):
    store = ConversationStore(tmp_path)
    identity, execution, claimant = _prepare_approved_inflight(
        store, run_id="run-approved-merge-rollback"
    )
    original_merge = agent_run_repository.merge_continuation

    def fail_completed_merge(existing, incoming):
        if incoming.get("pending_tool") is None:
            raise RuntimeError("forced completion merge failure")
        return original_merge(existing, incoming)

    monkeypatch.setattr(
        agent_run_repository, "merge_continuation", fail_completed_merge
    )
    with pytest.raises(RuntimeError, match="completion merge failure"):
        execution.complete_approved_tool(
            [],
            [],
            0,
            0,
            "call-approved",
            "gmail_draft",
            True,
            "a" * 64,
            claimant=claimant,
            result={"draft_id": "PRIVATE_RESULT"},
        )

    run = store.get_agent_run(identity.run_id)
    receipt = Inbox(store).get("call-approved")
    assert run["continuation"]["cursor"]["phase"] == "tool_in_flight"
    assert run["usage"] == {"model_calls": 1}
    assert receipt["execution_status"] == "executing"
    assert receipt["execution_result"] is None
    monkeypatch.setattr(
        agent_run_repository, "merge_continuation", original_merge
    )

    execution.interrupt_approved_inflight_tool(
        [], [], claimant=claimant
    )

    run = store.get_agent_run(identity.run_id)
    receipt = Inbox(store).get("call-approved")
    assert run["current_state"] == "interrupted"
    assert run["continuation"]["cursor"]["phase"] == "review_required"
    assert receipt["execution_status"] == "interrupted"
    assert receipt["execution_claimant"] is None


def test_approved_checkpoint_insert_failure_rolls_back_both_authorities(
    tmp_path
):
    store = ConversationStore(tmp_path)
    identity, execution, claimant = _prepare_approved_inflight(
        store, run_id="run-approved-checkpoint-rollback"
    )
    with sqlite3.connect(store.db_path) as db:
        db.executescript(
            """
            CREATE TRIGGER fail_approved_tool_checkpoint
            BEFORE INSERT ON agent_run_checkpoints
            WHEN NEW.kind = 'tool_completed'
            BEGIN
                SELECT RAISE(ABORT, 'forced approved checkpoint failure');
            END;
            """
        )
    with pytest.raises(sqlite3.IntegrityError, match="approved checkpoint failure"):
        execution.complete_approved_tool(
            [],
            [],
            0,
            0,
            "call-approved",
            "gmail_draft",
            True,
            "b" * 64,
            claimant=claimant,
            result={"draft_id": "PRIVATE_RESULT"},
        )
    with sqlite3.connect(store.db_path) as db:
        db.execute("DROP TRIGGER fail_approved_tool_checkpoint")

    run = store.get_agent_run(identity.run_id)
    receipt = Inbox(store).get("call-approved")
    assert run["continuation"]["cursor"]["phase"] == "tool_in_flight"
    assert "tool_completed" not in {
        item["kind"] for item in store.list_agent_run_checkpoints(identity.run_id)
    }
    assert receipt["execution_status"] == "executing"
    assert receipt["execution_result"] is None

    execution.interrupt_approved_inflight_tool(
        [], [], claimant=claimant
    )
    assert store.get_agent_run(identity.run_id)["current_state"] == "interrupted"
    assert Inbox(store).get("call-approved")["execution_status"] == "interrupted"


def test_external_approval_adoption_rejects_nonterminal_then_is_idempotent(
    tmp_path
):
    store = ConversationStore(tmp_path)
    identity, execution = _start(store, run_id="run-adopt-idempotent")
    _prepare_one_tool(execution)
    execution.waiting_approval_atomic(
        [],
        [],
        "call-external",
        0,
        0,
        "call-external",
        "gmail_draft",
        False,
        arguments={"body": "PRIVATE_APPROVAL_ARGUMENT"},
        reason="review",
        resource=None,
        approval_ttl_seconds=7 * 24 * 60 * 60,
    )
    external = Inbox(store)
    claim = external.decide_and_claim(
        "call-external",
        "allow",
        actor="external",
        scope="once",
        claimant="external-owner",
    )
    assert claim is not None and claim.owned
    resumed = AgentRunExecution.resume_resolved_approval(
        store,
        identity.run_id,
        "call-external",
        4,
        owner_id="adopting-owner",
        now=NOW,
    )
    resumed.approval_resolved([], [], "call-external")

    with pytest.raises(ValueError, match="not terminal"):
        resumed.adopt_completed_approval(
            [], [], 0, 0, "call-external", "gmail_draft"
        )
    before = store.get_agent_run(identity.run_id)
    assert before["continuation"]["cursor"]["next_tool_index"] == 0
    assert before["usage"] == {"model_calls": 1}

    assert external.complete_execution(
        "call-external",
        claimant="external-owner",
        ok=True,
        result={"draft_id": "draft-external"},
    ) is not None
    first = resumed.adopt_completed_approval(
        [], [], 0, 0, "call-external", "gmail_draft"
    )
    second = resumed.adopt_completed_approval(
        [], [], 0, 0, "call-external", "gmail_draft"
    )

    assert first == second
    run = store.get_agent_run(identity.run_id)
    assert run["usage"] == {"model_calls": 1, "tool_calls": 1}
    assert run["continuation"]["remaining_budgets"]["tool_calls"] == 3
    assert [
        item["kind"] for item in store.list_agent_run_checkpoints(identity.run_id)
    ].count("tool_completed") == 1


def _wait_for_tool(
    execution,
    interaction_id,
    *,
    call_id="call-approved",
    name="gmail_draft",
    retry_safe=False,
):
    execution.waiting_approval(
        [],
        [],
        interaction_id,
        0,
        0,
        call_id,
        name,
        retry_safe,
    )


def test_resolved_waiting_lease_requires_exact_resolved_inbox_and_has_one_owner(
    tmp_path,
):
    first = ConversationStore(tmp_path)
    second = ConversationStore(tmp_path)
    identity, execution = _start(first, run_id="run-waiting")
    _park(first, identity, "approval-1")
    _prepare_one_tool(execution)
    _wait_for_tool(execution, "approval-1")
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
    _prepare_one_tool(wrong_execution)
    _wait_for_tool(wrong_execution, "approval-wrong-run")
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
    _prepare_one_tool(execution)
    _wait_for_tool(execution, interaction_id)
    waiting = store.get_agent_run(identity.run_id)
    Inbox(store).resolve(interaction_id, decision)

    acquired = store.acquire_resolved_waiting_lease(
        identity.run_id,
        "owner-resume",
        waiting["version"],
        interaction_id,
        30,
        now=NOW,
    )

    assert acquired is not None
    assert acquired.lease.owner_id == "owner-resume"
    assert acquired.decision == decision


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
        "expected_tool_count": 0,
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
        "expected_tool_count": 0,
        "transcript_prefix_count": 0,
        "transcript_prefix_sha256": EMPTY_SHA256,
        "event_prefix_count": 0,
        "event_prefix_sha256": EMPTY_SHA256,
    }


def test_start_never_inserts_an_unleased_running_row_while_store_contends(
    tmp_path,
):
    store = ConversationStore(tmp_path)
    unleased_insert = Event()

    def mark_unleased_insert():
        unleased_insert.set()
        return 0

    with store._lock:
        store._conn.create_function("mark_unleased_insert", 0, mark_unleased_insert)
        store._conn.execute(
            """
            CREATE TRIGGER reject_unleased_agent_run_insert
            BEFORE INSERT ON agent_runs
            WHEN NEW.current_state = 'running' AND NEW.lease_owner IS NULL
            BEGIN
                SELECT mark_unleased_insert();
                SELECT RAISE(FAIL, 'unleased running insert');
            END
            """
        )
        store._conn.commit()
    identity = _identity("run-atomic-start")
    live_now = datetime.now(UTC)

    with ThreadPoolExecutor(max_workers=2) as pool:
        start_future = pool.submit(
            AgentRunExecution.start,
            store,
            identity,
            "Find candidates",
            "chat",
            "fake-model",
            4,
            "owner-atomic",
            live_now,
        )
        contender_future = pool.submit(ConversationStore, tmp_path)
        execution = start_future.result(timeout=5)
        contender = contender_future.result(timeout=5)

    assert unleased_insert.is_set() is False
    assert execution.lease is not None
    run = contender.get_agent_run(identity.run_id)
    assert run["current_state"] == "running"
    assert run["continuation"]["cursor"]["phase"] == "model_ready"
    assert _lease_columns(store, identity.run_id)[0] == "owner-atomic"


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
    released, _checkpoint = store.agent_runs.checkpoint_leased(
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
    assert released is None
    assert _lease_columns(store, "run-review-required") == (None, None)

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


def test_start_rejects_running_review_required_without_leaving_a_lease(tmp_path):
    store = ConversationStore(tmp_path)
    identity = _identity("run-running-review-required")
    _seed_running_phase(store, identity, "review_required")

    assert store.get_agent_run(identity.run_id)["current_state"] == "running"
    assert _lease_columns(store, identity.run_id) == (None, None)
    with pytest.raises(AgentRunExecutionOwnershipError, match="review"):
        AgentRunExecution.start(
            store,
            identity,
            "Find candidates",
            "chat",
            "fake-model",
            4,
            owner_id="new-owner",
            now=NOW,
        )

    assert _lease_columns(store, identity.run_id) == (None, None)


def test_start_releases_lease_if_phase_becomes_review_required_during_acquire(
    tmp_path, monkeypatch
):
    store = ConversationStore(tmp_path)
    identity = _identity("run-review-race")
    _seed_running_phase(store, identity, "model_ready")
    original = store.agent_runs.start_and_acquire_lease

    def acquire_then_require_review(**kwargs):
        started = original(**kwargs)
        assert started is not None
        lease = store.agent_runs.update_continuation(
            started.lease,
            {"cursor": {"phase": "review_required"}},
            now=NOW,
        )
        return type(started)(run=store.get_agent_run(identity.run_id), lease=lease)

    monkeypatch.setattr(
        store.agent_runs,
        "start_and_acquire_lease",
        acquire_then_require_review,
    )

    with pytest.raises(AgentRunExecutionOwnershipError, match="review"):
        AgentRunExecution.start(
            store,
            identity,
            "Find candidates",
            "chat",
            "fake-model",
            4,
            owner_id="new-owner",
            now=NOW,
        )

    assert _lease_columns(store, identity.run_id) == (None, None)


@pytest.mark.parametrize("phase", ("model_ready", "tools_ready"))
def test_start_accepts_valid_running_resumable_phases(tmp_path, phase):
    store = ConversationStore(tmp_path)
    identity = _identity(f"run-{phase}")
    _seed_running_phase(store, identity, phase)

    execution = AgentRunExecution.start(
        store,
        identity,
        "Find candidates",
        "chat",
        "fake-model",
        4,
        owner_id="new-owner",
        now=NOW,
    )

    assert execution.lease is not None
    assert execution.metadata["phase"] == phase


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
    execution.model_completed(history, events, 0, 2, 0)
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
    execution.model_completed([], [], 0, 1, 0)
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


def test_ambiguous_recovery_uses_configured_tiny_lease(
    tmp_path, monkeypatch
):
    store = ConversationStore(tmp_path)
    identity = _identity("run-tiny-ambiguous")
    execution = AgentRunExecution.start(
        store,
        identity,
        "Find candidates",
        "chat",
        "fake-model",
        4,
        owner_id="tiny-owner",
        now=NOW,
        lease_seconds=0.09,
    )
    original_checkpoint = store.agent_runs.checkpoint_leased
    original_acquire = store.agent_runs.acquire_lease
    lost = True
    recovery_durations = []

    def commit_then_lose(lease, kind, continuation, **kwargs):
        nonlocal lost
        result = original_checkpoint(lease, kind, continuation, **kwargs)
        if lost and kind == "model_pending":
            lost = False
            raise RuntimeError("lost tiny acknowledgement")
        return result

    def record_recovery(run_id, owner_id, expected_version, lease_seconds, **kwargs):
        recovery_durations.append(lease_seconds)
        return original_acquire(
            run_id,
            owner_id,
            expected_version,
            lease_seconds,
            **kwargs,
        )

    monkeypatch.setattr(store.agent_runs, "checkpoint_leased", commit_then_lose)
    monkeypatch.setattr(store.agent_runs, "acquire_lease", record_recovery)
    with pytest.raises(RuntimeError, match="tiny acknowledgement"):
        execution.model_pending([], [], 0)
    execution.model_pending([], [], 0)

    assert recovery_durations == [0.09]
    assert execution.lease_seconds == 0.09


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
    execution.model_completed(history, [], 0, 1, 0)
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
                "outcome": "executed",
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
    _prepare_one_tool(execution)

    _wait_for_tool(execution, "approval-resume")

    assert execution.lease is None
    assert _lease_columns(store, identity.run_id) == (None, None)
    with pytest.raises(AgentRunExecutionOwnershipError, match="lease"):
        execution.model_pending([], [], 0)
    with pytest.raises(AgentRunExecutionOwnershipError, match="resolved approval"):
        AgentRunExecution.resume_resolved_approval(
            store,
            identity.run_id,
            "approval-resume",
            4,
            owner_id="owner-resume",
            now=NOW,
        )

    Inbox(store).resolve("approval-resume", "deny")
    execution = AgentRunExecution.resume_resolved_approval(
        store,
        identity.run_id,
        "approval-resume",
        4,
        owner_id="owner-resume",
        now=NOW,
    )
    assert execution.lease is not None
    assert execution.run_id == identity.run_id
    execution.approval_resolved([], [], "approval-resume")

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


@pytest.mark.parametrize("decision", ("allow", "deny"))
def test_approval_resolution_uses_exact_atomically_acquired_decision(
    tmp_path, decision
):
    store = ConversationStore(tmp_path)
    identity, execution = _start(store, run_id=f"run-bound-{decision}")
    interaction_id = f"approval-bound-{decision}"
    _park(store, identity, interaction_id)
    _prepare_one_tool(execution)
    _wait_for_tool(execution, interaction_id)
    Inbox(store).resolve(interaction_id, decision)
    execution = AgentRunExecution.resume_resolved_approval(
        store,
        identity.run_id,
        interaction_id,
        4,
        owner_id="owner-resume",
        now=NOW,
    )

    execution.approval_resolved([], [], interaction_id)

    assert store.list_agent_run_checkpoints(identity.run_id)[-1]["payload"] == {
        "id": interaction_id,
        "resolution": decision,
    }
    continuation = store.get_agent_run(identity.run_id)["continuation"]
    assert continuation["cursor"]["phase"] == "tools_ready"
    assert continuation["remaining_budgets"]["tool_calls"] == 4
    assert "pending_interaction" not in continuation
    assert "resolved_approval" not in continuation
    if decision == "allow":
        assert continuation["cursor"]["next_tool_index"] == 0
        assert continuation["pending_tool"] == {
            "attempt_id": f"{identity.run_id}:0:call-approved:0",
            "call_id": "call-approved",
            "name": "gmail_draft",
            "retry_class": "consequential",
            "status": "not_started",
            "budget_reserved": False,
        }
    else:
        assert continuation["cursor"]["next_tool_index"] == 1
        assert "pending_tool" not in continuation
        assert continuation["completed_tool_receipts"][-1] == {
            "attempt_id": f"{identity.run_id}:0:call-approved:0",
            "call_id": "call-approved",
            "name": "gmail_draft",
            "ok": False,
            "outcome": "denied",
            "transcript_index": 0,
            "result_sha256": None,
        }
        assert "tool_pending" not in {
            item["kind"]
            for item in store.list_agent_run_checkpoints(identity.run_id)
        }


@pytest.mark.parametrize("resolution", ("deny", "allowed", "arbitrary"))
def test_approval_resolution_cannot_be_overridden(tmp_path, resolution):
    store = ConversationStore(tmp_path)
    identity, execution = _start(store, run_id=f"run-mismatch-{resolution}")
    interaction_id = f"approval-mismatch-{resolution}"
    _park(store, identity, interaction_id)
    _prepare_one_tool(execution)
    _wait_for_tool(execution, interaction_id)
    Inbox(store).resolve(interaction_id, "allow")
    execution = AgentRunExecution.resume_resolved_approval(
        store,
        identity.run_id,
        interaction_id,
        4,
        owner_id="owner-resume",
        now=NOW,
    )
    before = store.get_agent_run(identity.run_id)
    checkpoint_count = len(store.list_agent_run_checkpoints(identity.run_id))

    with pytest.raises(TypeError):
        execution.approval_resolved([], [], interaction_id, resolution)

    after = store.get_agent_run(identity.run_id)
    assert after["version"] == before["version"]
    assert after["continuation"] == before["continuation"]
    assert len(store.list_agent_run_checkpoints(identity.run_id)) == checkpoint_count


@pytest.mark.parametrize("decision", ("allow", "deny"))
def test_waiting_approval_reopens_and_reconstructs_exact_context(
    tmp_path, decision
):
    store = ConversationStore(tmp_path)
    identity, execution = _start(store, run_id=f"run-reopen-{decision}")
    interaction_id = f"approval-reopen-{decision}"
    _park(store, identity, interaction_id)
    _prepare_one_tool(execution)
    _wait_for_tool(execution, interaction_id)

    reopened = ConversationStore(tmp_path)
    Inbox(reopened).resolve(interaction_id, decision)
    resumed = AgentRunExecution.resume_resolved_approval(
        reopened,
        identity.run_id,
        interaction_id,
        4,
        owner_id="owner-reopened",
        now=NOW,
    )

    ready = reopened.get_agent_run(identity.run_id)["continuation"]
    assert ready["cursor"]["phase"] == "approval_ready"
    assert ready["resolved_approval"] == {
        "id": interaction_id,
        "decision": decision,
    }
    resumed.approval_resolved([], [], interaction_id)
    assert reopened.get_agent_run(identity.run_id)["continuation"]["cursor"][
        "phase"
    ] == "tools_ready"


@pytest.mark.parametrize("decision", ("allow", "deny"))
def test_resolved_approval_acquisition_survives_expiry_before_consumption(
    tmp_path, decision
):
    store = ConversationStore(tmp_path)
    identity, execution = _start(store, run_id=f"run-ready-crash-{decision}")
    interaction_id = f"approval-ready-crash-{decision}"
    _park(store, identity, interaction_id)
    _prepare_one_tool(execution)
    _wait_for_tool(execution, interaction_id)
    Inbox(store).resolve(interaction_id, decision)
    first = AgentRunExecution.resume_resolved_approval(
        store,
        identity.run_id,
        interaction_id,
        4,
        owner_id="owner-before-crash",
        now=NOW,
    )
    old_lease = first.lease

    store.agent_runs.reconcile_expired_leases(
        now=NOW + timedelta(seconds=301)
    )
    interrupted = store.get_agent_run(identity.run_id)
    assert interrupted["current_state"] == "interrupted"
    assert interrupted["continuation"]["cursor"]["phase"] == "approval_ready"
    assert interrupted["continuation"]["resolved_approval"]["decision"] == decision

    reopened = ConversationStore(tmp_path)
    resumed = AgentRunExecution.resume_resolved_approval(
        reopened,
        identity.run_id,
        interaction_id,
        4,
        owner_id="owner-after-crash",
        now=NOW + timedelta(seconds=301),
    )
    resumed.approval_resolved([], [], interaction_id)

    continuation = reopened.get_agent_run(identity.run_id)["continuation"]
    assert continuation["cursor"]["phase"] == "tools_ready"
    assert "resolved_approval" not in continuation
    with pytest.raises((AgentRunLeaseLost, AgentRunVersionConflict)):
        store.agent_runs.renew_lease(
            old_lease, 30, now=NOW + timedelta(seconds=301)
        )


def test_expired_model_attempt_resumes_without_spending_budget_twice(tmp_path):
    store = ConversationStore(tmp_path)
    identity, execution = _start(store, run_id="run-crash-model")
    history = [{"role": "user", "content": "Find candidates"}]
    events = [{"type": "turn_start"}]
    execution.model_pending(history, events, 0)
    old_lease = execution.lease

    store.agent_runs.reconcile_expired_leases(
        now=NOW + timedelta(seconds=301)
    )
    recovered = store.get_agent_run(identity.run_id)["continuation"]
    assert recovered["cursor"]["phase"] == "model_ready"
    assert recovered["pending_model"] == {
        "attempt_id": f"{identity.run_id}:0:model",
        "status": "retry_ready",
        "budget_reserved": True,
    }
    assert recovered["remaining_budgets"]["work_steps"] == 3

    reopened = ConversationStore(tmp_path)
    resumed = AgentRunExecution.resume(
        reopened,
        identity,
        4,
        owner_id="owner-resumed",
        now=NOW + timedelta(seconds=301),
    )
    resumed.model_pending(history, events, 0)
    resumed.model_completed(history, events, 0, 0, 0)

    continuation = reopened.get_agent_run(identity.run_id)["continuation"]
    assert continuation["cursor"]["phase"] == "terminal_ready"
    assert "pending_model" not in continuation
    assert continuation["remaining_budgets"]["work_steps"] == 3
    assert [
        item["kind"] for item in reopened.list_agent_run_checkpoints(identity.run_id)
    ].count("model_pending") == 2
    with pytest.raises((AgentRunLeaseLost, AgentRunVersionConflict)):
        store.agent_runs.renew_lease(
            old_lease, 30, now=NOW + timedelta(seconds=301)
        )


def test_expired_safe_tool_attempt_resumes_without_spending_budget_twice(tmp_path):
    store = ConversationStore(tmp_path)
    identity, execution = _start(store, run_id="run-crash-tool")
    history = [{"role": "user", "content": "Find candidates"}]
    events = [{"type": "turn_start"}]
    execution.model_pending(history, events, 0)
    execution.model_completed(history, events, 0, 1, 0)
    execution.tool_pending(
        history, events, 0, 0, "call-crash", "search", True
    )
    old_lease = execution.lease

    store.agent_runs.reconcile_expired_leases(
        now=NOW + timedelta(seconds=301)
    )
    recovered = store.get_agent_run(identity.run_id)["continuation"]
    assert recovered["cursor"]["phase"] == "tools_ready"
    assert recovered["pending_tool"] == {
        "attempt_id": f"{identity.run_id}:0:call-crash:0",
        "call_id": "call-crash",
        "name": "search",
        "retry_class": "safe",
        "status": "retry_ready",
        "budget_reserved": True,
    }
    assert recovered["remaining_budgets"]["tool_calls"] == 3

    reopened = ConversationStore(tmp_path)
    resumed = AgentRunExecution.resume(
        reopened,
        identity,
        4,
        owner_id="owner-resumed",
        now=NOW + timedelta(seconds=301),
    )
    resumed.tool_pending(
        history, events, 0, 0, "call-crash", "search", True
    )
    resumed.tool_completed(
        history,
        events,
        0,
        0,
        "call-crash",
        "search",
        True,
        "b" * 64,
    )

    continuation = reopened.get_agent_run(identity.run_id)["continuation"]
    assert continuation["cursor"]["next_tool_index"] == 1
    assert continuation["remaining_budgets"]["tool_calls"] == 3
    assert [
        item["kind"] for item in reopened.list_agent_run_checkpoints(identity.run_id)
    ].count("tool_pending") == 2
    with pytest.raises((AgentRunLeaseLost, AgentRunVersionConflict)):
        store.agent_runs.renew_lease(
            old_lease, 30, now=NOW + timedelta(seconds=301)
        )


def test_terminal_releases_projects_metadata_and_fences_old_owner(tmp_path):
    store = ConversationStore(tmp_path)
    identity, execution = _start(store, run_id="run-terminal")
    execution.model_pending([], [], 0)
    execution.model_completed([], [], 0, 0, len("PRIVATE_FINAL_TEXT"))
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
    history = [{"role": "user"}]
    execution.model_pending(history, [], 0)
    execution.model_completed(history, [], 0, 1, 0)
    execution.tool_pending(history, [], 0, 0, "call-1", "search", True)
    execution.tool_completed(
        history, [], 0, 0, "call-1", "search", True, "a" * 64
    )
    execution.model_pending(history, [], 1)
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
    execution.model_completed(second_history, second_events, 0, 0, 4)
    second = store.get_agent_run(identity.run_id)["continuation"]["cursor"]
    assert second["transcript_prefix_count"] == len(second_history)
    assert second["transcript_prefix_sha256"] == transcript_prefix_sha256(
        second_history
    )
    assert second["event_prefix_count"] == len(second_events)
    assert second["event_prefix_sha256"] == transcript_prefix_sha256(second_events)
