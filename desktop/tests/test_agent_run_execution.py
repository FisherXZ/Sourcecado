import asyncio
import hashlib
import json
import sqlite3
import time

import pytest

from coworker.agent_run_continuation import transcript_prefix_sha256
from coworker.agent_run_execution import (
    AgentRunExecution,
    AgentRunExecutionOwnershipError,
)
from coworker.events import TurnIdentity
from coworker.inbox import Inbox
from coworker.provider import FakeProvider, StreamChunk, ToolCall
from coworker.store import ConversationStore
from coworker.turn import RunControl, run_turn


def _identity(run_id: str = "run-live") -> TurnIdentity:
    return TurnIdentity(
        session_id="thread-live",
        run_id=run_id,
        message_id=f"message-{run_id}",
        part_id=f"part-{run_id}",
    )


def _run(tmp_path, provider, *, identity=None):
    store = ConversationStore(tmp_path)
    turn_identity = identity or _identity()
    result = asyncio.run(
        run_turn(
            text="Find candidates",
            sid=turn_identity.session_id,
            store=store,
            provider=provider,
            persona=None,
            skills=None,
            inbox=Inbox(store),
            openai_tools=[],
            execute_kwargs={},
            identity=turn_identity,
        )
    )
    return store, result


def _force_waiting_tool_budget(monkeypatch, store, amount):
    original = AgentRunExecution.waiting_approval_atomic

    def force_budget(self, *args, **kwargs):
        lease = store.agent_runs.update_continuation(
            self.lease,
            {"remaining_budgets": {"tool_calls": amount}},
        )
        self._lease = lease
        self._refresh_snapshot()
        return original(self, *args, **kwargs)

    monkeypatch.setattr(
        AgentRunExecution, "waiting_approval_atomic", force_budget
    )


def test_provider_observes_committed_model_pending_boundary(tmp_path):
    class InspectingProvider:
        model_id = "inspecting"

        def __init__(self):
            self.store = None
            self.observed = None

        async def astream(self, *, messages, tools):
            run = self.store.get_agent_run("run-live")
            checkpoints = self.store.list_agent_run_checkpoints("run-live")
            self.observed = (run, checkpoints)
            yield StreamChunk(text_delta="Done")

    provider = InspectingProvider()
    store = ConversationStore(tmp_path)
    provider.store = store
    identity = _identity()
    result = asyncio.run(
        run_turn(
            text="Find candidates",
            sid=identity.session_id,
            store=store,
            provider=provider,
            persona=None,
            skills=None,
            inbox=Inbox(store),
            openai_tools=[],
            execute_kwargs={},
            identity=identity,
        )
    )

    assert result["status"] == "ok"
    run, checkpoints = provider.observed
    assert checkpoints[-1]["kind"] == "model_pending"
    assert run["continuation"]["cursor"]["phase"] == "model_in_flight"
    assert run["continuation"]["remaining_budgets"]["work_steps"] == 7
    transcript = store.load(identity.session_id)
    events = store.load_events(identity.session_id)
    cursor = run["continuation"]["cursor"]
    assert cursor["transcript_prefix_count"] == 1
    assert cursor["transcript_prefix_sha256"] == transcript_prefix_sha256(
        transcript[:1]
    )
    assert cursor["event_prefix_count"] == 1
    assert cursor["event_prefix_sha256"] == transcript_prefix_sha256(events[:1])


def test_model_pending_failure_fences_provider_transport(
    tmp_path, monkeypatch
):
    class CountingProvider:
        model_id = "counting"

        def __init__(self):
            self.calls = 0

        async def astream(self, *, messages, tools):
            self.calls += 1
            yield StreamChunk(text_delta="must not run")

    provider = CountingProvider()

    def fail_pending(self, history, events, step_index):
        raise RuntimeError("forced model_pending fence")

    monkeypatch.setattr(AgentRunExecution, "model_pending", fail_pending)
    store, result = _run(tmp_path, provider, identity=_identity("run-fenced"))

    assert result["status"] == "error"
    assert provider.calls == 0
    run = store.get_agent_run("run-fenced")
    assert run["current_state"] == "failed"
    assert run["terminal_result"]["status"] == "error"


def test_multi_tool_turn_fences_each_execute_and_aggregates_safe_receipts(
    tmp_path, monkeypatch
):
    identity = _identity("run-multi-tool")
    store = ConversationStore(tmp_path)
    provider = FakeProvider(
        steps=[
            {
                "tool_calls": [
                    ToolCall(id="call-now", name="now", arguments={}),
                    ToolCall(
                        id="call-skill",
                        name="load_skill",
                        arguments={"name": "research"},
                    ),
                ]
            },
            {"deltas": ("All done",)},
        ]
    )
    observed = []
    results = {
        "now": {"time": "noon"},
        "load_skill": {
            "name": "research",
            "sources": [
                {
                    "id": "source-1",
                    "title": "Primary source",
                    "url": "https://example.test/source",
                }
            ],
            "artifacts": [
                {
                    "id": "artifact-1",
                    "type": "note",
                    "title": "Research note",
                }
            ],
        },
    }

    def inspecting_execute(name, arguments, **kwargs):
        run = store.get_agent_run(identity.run_id)
        checkpoints = store.list_agent_run_checkpoints(identity.run_id)
        observed.append((name, run, checkpoints[-1]))
        return True, results[name]

    monkeypatch.setattr("coworker.turn.execute", inspecting_execute)
    result = asyncio.run(
        run_turn(
            text="Use two tools",
            sid=identity.session_id,
            store=store,
            provider=provider,
            persona=None,
            skills=None,
            inbox=Inbox(store),
            openai_tools=[],
            execute_kwargs={},
            identity=identity,
        )
    )

    assert result == {
        "status": "ok",
        "text": "All done",
        "run_id": identity.run_id,
    }
    assert [item[0] for item in observed] == ["now", "load_skill"]
    for tool_index, (_name, run, checkpoint) in enumerate(observed):
        assert checkpoint["kind"] == "tool_pending"
        assert run["continuation"]["cursor"]["phase"] == "tool_in_flight"
        assert run["continuation"]["cursor"]["next_tool_index"] == tool_index
        assert run["continuation"]["remaining_budgets"]["tool_calls"] == (
            7 - tool_index
        )
    run = store.get_agent_run(identity.run_id)
    assert run["current_state"] == "complete"
    assert run["usage"] == {"model_calls": 2, "tool_calls": 2}
    assert run["skills_loaded"] == ["research"]
    assert run["source_refs"][0]["id"] == "source-1"
    assert run["artifact_refs"][0]["id"] == "artifact-1"
    receipts = run["continuation"]["completed_tool_receipts"]
    assert [receipt["call_id"] for receipt in receipts] == [
        "call-now",
        "call-skill",
    ]
    assert [receipt["outcome"] for receipt in receipts] == [
        "executed",
        "executed",
    ]
    assert [receipt["result_sha256"] for receipt in receipts] == [
        hashlib.sha256(
            json.dumps(
                results[name],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        for name in ("now", "load_skill")
    ]
    checkpoints = store.list_agent_run_checkpoints(identity.run_id)
    assert [checkpoint["kind"] for checkpoint in checkpoints] == [
        "run_started",
        "user_input",
        "model_pending",
        "model_completed",
        "tool_pending",
        "tool_completed",
        "tool_pending",
        "tool_completed",
        "model_pending",
        "model_completed",
        "terminal",
    ]
    durable_authority = json.dumps(
        {"continuation": run["continuation"], "checkpoints": checkpoints},
        sort_keys=True,
    )
    assert "Use two tools" not in durable_authority
    assert "noon" not in durable_authority
    assert "All done" not in durable_authority


def test_tool_pending_failure_fences_execute(tmp_path, monkeypatch):
    identity = _identity("run-tool-fenced")
    provider = FakeProvider(
        steps=[
            {"tool_calls": [ToolCall(id="call-now", name="now", arguments={})]}
        ]
    )
    execute_calls = []

    def fail_pending(self, *args, **kwargs):
        raise RuntimeError("forced tool_pending fence")

    def forbidden_execute(*args, **kwargs):
        execute_calls.append((args, kwargs))
        return True, {"must": "not run"}

    monkeypatch.setattr(AgentRunExecution, "tool_pending", fail_pending)
    monkeypatch.setattr("coworker.turn.execute", forbidden_execute)
    store, result = _run(tmp_path, provider, identity=identity)

    assert result["status"] == "error"
    assert execute_calls == []
    assert store.get_agent_run(identity.run_id)["current_state"] == "failed"


def test_safe_tool_event_persistence_failure_suspends_for_retry(
    tmp_path, monkeypatch
):
    identity = _identity("run-safe-interrupted")
    store = ConversationStore(tmp_path)
    provider = FakeProvider(
        steps=[
            {"tool_calls": [ToolCall(id="call-safe", name="now", arguments={})]}
        ]
    )
    executions = []
    original_append_event = store.append_event

    def execute_once(name, arguments, **kwargs):
        executions.append(name)
        return True, {"time": "noon"}

    def fail_tool_finished(session_id, event):
        if event.get("type") == "tool_finished":
            raise RuntimeError("tool_finished persistence failed")
        return original_append_event(session_id, event)

    monkeypatch.setattr("coworker.turn.execute", execute_once)
    monkeypatch.setattr(store, "append_event", fail_tool_finished)
    result = asyncio.run(
        run_turn(
            text="Check the time",
            sid=identity.session_id,
            store=store,
            provider=provider,
            persona=None,
            skills=None,
            inbox=Inbox(store),
            openai_tools=[],
            execute_kwargs={},
            identity=identity,
        )
    )

    assert result == {
        "status": "interrupted",
        "text": "",
        "run_id": identity.run_id,
    }
    assert executions == ["now"]
    run = store.get_agent_run(identity.run_id)
    assert run["current_state"] == "interrupted"
    assert run["continuation"]["cursor"]["phase"] == "tools_ready"
    assert run["continuation"]["remaining_budgets"]["tool_calls"] == 7
    assert run["continuation"]["pending_tool"] == {
        "attempt_id": f"{identity.run_id}:0:call-safe:0",
        "call_id": "call-safe",
        "name": "now",
        "retry_class": "safe",
        "status": "retry_ready",
        "budget_reserved": True,
    }
    assert store.list_agent_run_checkpoints(identity.run_id)[-1]["kind"] == (
        "process_interrupted"
    )
    durable_history = store.load(identity.session_id)
    durable_events = store.load_events(identity.session_id)
    cursor = run["continuation"]["cursor"]
    assert cursor["transcript_prefix_count"] == 2
    assert cursor["transcript_prefix_sha256"] == transcript_prefix_sha256(
        durable_history[:2]
    )
    assert cursor["event_prefix_count"] == len(durable_events) - 1
    assert cursor["event_prefix_sha256"] == transcript_prefix_sha256(
        durable_events[:-1]
    )
    assert durable_events[-1]["state"] == "interrupted"
    with sqlite3.connect(store.db_path) as db:
        assert db.execute(
            "SELECT lease_owner, lease_expires_at FROM agent_runs WHERE run_id = ?",
            (identity.run_id,),
        ).fetchone() == (None, None)


def test_interrupted_projection_failure_keeps_suspension_and_no_lease(
    tmp_path, monkeypatch
):
    identity = _identity("run-interrupted-projection-failure")
    store = ConversationStore(tmp_path)
    provider = FakeProvider(
        steps=[
            {"tool_calls": [ToolCall(id="call-safe", name="now", arguments={})]}
        ]
    )
    original_append_event = store.append_event

    def fail_after_execution(session_id, event):
        if event.get("type") == "tool_finished" or event.get("state") == "interrupted":
            raise RuntimeError("interrupted projection failed")
        return original_append_event(session_id, event)

    monkeypatch.setattr(
        "coworker.turn.execute", lambda *args, **kwargs: (True, {"time": "noon"})
    )
    monkeypatch.setattr(store, "append_event", fail_after_execution)
    result = asyncio.run(
        run_turn(
            text="Check the time",
            sid=identity.session_id,
            store=store,
            provider=provider,
            persona=None,
            skills=None,
            inbox=Inbox(store),
            openai_tools=[],
            execute_kwargs={},
            identity=identity,
        )
    )

    assert result["status"] == "error"
    run = store.get_agent_run(identity.run_id)
    assert run["current_state"] == "interrupted"
    assert run["continuation"]["pending_tool"]["status"] == "retry_ready"
    assert store.list_agent_run_checkpoints(identity.run_id)[-1]["kind"] == (
        "process_interrupted"
    )
    with sqlite3.connect(store.db_path) as db:
        assert db.execute(
            "SELECT lease_owner, lease_expires_at FROM agent_runs WHERE run_id = ?",
            (identity.run_id,),
        ).fetchone() == (None, None)


def test_consequential_tool_transcript_failure_requires_review(
    tmp_path, monkeypatch
):
    identity = _identity("run-consequential-interrupted")
    store = ConversationStore(tmp_path)
    provider = FakeProvider(
        steps=[
            {
                "tool_calls": [
                    ToolCall(
                        id="call-draft",
                        name="gmail_draft",
                        arguments={"body": "private body"},
                    )
                ]
            }
        ]
    )
    executions = []
    original_append = store.append

    async def allow(_call_id):
        return "allow"

    def execute_once(name, arguments, **kwargs):
        executions.append(name)
        return True, {"draft_id": "draft-1"}

    def fail_tool_transcript(session_id, message):
        if message.get("role") == "tool":
            raise RuntimeError("tool transcript persistence failed")
        return original_append(session_id, message)

    monkeypatch.setattr("coworker.turn.execute", execute_once)
    monkeypatch.setattr(store, "append", fail_tool_transcript)
    result = asyncio.run(
        run_turn(
            text="Draft outreach",
            sid=identity.session_id,
            store=store,
            provider=provider,
            persona=None,
            skills=None,
            inbox=Inbox(store),
            openai_tools=[],
            execute_kwargs={},
            wait_permission=allow,
            identity=identity,
        )
    )

    assert result["status"] == "interrupted"
    assert executions == ["gmail_draft"]
    run = store.get_agent_run(identity.run_id)
    assert run["current_state"] == "interrupted"
    assert run["continuation"]["cursor"]["phase"] == "review_required"
    assert run["continuation"]["pending_tool"]["status"] == "outcome_unknown"
    assert run["continuation"]["pending_tool"]["retry_class"] == "consequential"
    assert store.list_agent_run_checkpoints(identity.run_id)[-1]["kind"] == (
        "tool_outcome_unknown"
    )
    assert store.load_events(identity.session_id)[-1]["state"] == "interrupted"
    assert [message["role"] for message in store.load(identity.session_id)] == [
        "user",
        "assistant",
        "tool",
    ]
    with sqlite3.connect(store.db_path) as db:
        assert db.execute(
            "SELECT lease_owner, lease_expires_at FROM agent_runs WHERE run_id = ?",
            (identity.run_id,),
        ).fetchone() == (None, None)


def test_tool_completed_checkpoint_failure_suspends_without_reexecution(
    tmp_path, monkeypatch
):
    identity = _identity("run-completion-interrupted")
    provider = FakeProvider(
        steps=[
            {"tool_calls": [ToolCall(id="call-safe", name="now", arguments={})]}
        ]
    )
    executions = []

    def execute_once(name, arguments, **kwargs):
        executions.append(name)
        return True, {"time": "noon"}

    def fail_completed(self, *args, **kwargs):
        raise RuntimeError("tool_completed checkpoint failed")

    monkeypatch.setattr("coworker.turn.execute", execute_once)
    monkeypatch.setattr(AgentRunExecution, "tool_completed", fail_completed)
    store, result = _run(tmp_path, provider, identity=identity)

    assert result["status"] == "interrupted"
    assert executions == ["now"]
    run = store.get_agent_run(identity.run_id)
    assert run["current_state"] == "interrupted"
    assert run["continuation"]["pending_tool"]["status"] == "retry_ready"
    assert store.list_agent_run_checkpoints(identity.run_id)[-1]["kind"] == (
        "process_interrupted"
    )


def test_policy_denial_advances_without_execution_or_tool_budget(
    tmp_path, monkeypatch
):
    identity = _identity("run-policy-deny")
    provider = FakeProvider(
        steps=[
            {
                "tool_calls": [
                    ToolCall(
                        id="call-forbidden",
                        name="unknown_write",
                        arguments={"secret": "do not persist"},
                    ),
                    ToolCall(id="call-now", name="now", arguments={}),
                ]
            },
            {"deltas": ("Continued",)},
        ]
    )
    executed = []

    def inspecting_execute(name, arguments, **kwargs):
        executed.append(name)
        return True, {"time": "now"}

    monkeypatch.setattr("coworker.turn.execute", inspecting_execute)
    store, result = _run(tmp_path, provider, identity=identity)

    assert result["status"] == "partial"
    assert result["text"] == "Continued"
    assert executed == ["now"]
    run = store.get_agent_run(identity.run_id)
    assert run["usage"] == {"model_calls": 2, "tool_calls": 1}
    assert run["continuation"]["remaining_budgets"]["tool_calls"] == 7
    receipts = run["continuation"]["completed_tool_receipts"]
    assert [(item["call_id"], item["outcome"]) for item in receipts] == [
        ("call-forbidden", "denied"),
        ("call-now", "executed"),
    ]
    checkpoints = store.list_agent_run_checkpoints(identity.run_id)
    forbidden = [
        item
        for item in checkpoints
        if item["payload"].get("call_id") == "call-forbidden"
    ]
    assert [item["kind"] for item in forbidden] == ["tool_completed"]
    assert forbidden[0]["payload"]["outcome"] == "denied"
    assert "do not persist" not in json.dumps(checkpoints, sort_keys=True)


def test_approval_deny_reacquires_from_inbox_and_advances_without_execute(
    tmp_path, monkeypatch
):
    identity = _identity("run-approval-deny")
    store = ConversationStore(tmp_path)
    provider = FakeProvider(
        steps=[
            {
                "tool_calls": [
                    ToolCall(
                        id="call-draft-denied",
                        name="gmail_draft",
                        arguments={
                            "to": "person@example.test",
                            "subject": "Hello",
                            "body": "private draft body",
                        },
                    )
                ]
            },
            {"deltas": ("Not drafted",)},
        ]
    )
    executed = []
    waiting_observation = {}

    async def deny(call_id):
        run = store.get_agent_run(identity.run_id)
        with sqlite3.connect(store.db_path) as db:
            lease = db.execute(
                "SELECT lease_owner, lease_expires_at FROM agent_runs WHERE run_id = ?",
                (identity.run_id,),
            ).fetchone()
        waiting_observation.update(
            state=run["current_state"], phase=run["continuation"]["cursor"]["phase"], lease=lease
        )
        return "deny"

    def forbidden_execute(*args, **kwargs):
        executed.append((args, kwargs))
        return True, {"must": "not run"}

    monkeypatch.setattr("coworker.turn.execute", forbidden_execute)
    result = asyncio.run(
        run_turn(
            text="Draft outreach",
            sid=identity.session_id,
            store=store,
            provider=provider,
            persona=None,
            skills=None,
            inbox=Inbox(store),
            openai_tools=[],
            execute_kwargs={},
            wait_permission=deny,
            identity=identity,
        )
    )

    assert waiting_observation == {
        "state": "waiting_approval",
        "phase": "waiting_approval",
        "lease": (None, None),
    }
    assert result["status"] == "partial"
    assert executed == []
    run = store.get_agent_run(identity.run_id)
    assert run["current_state"] == "partial"
    assert run["usage"] == {"model_calls": 2}
    assert run["continuation"]["remaining_budgets"]["tool_calls"] == 8
    assert run["continuation"]["completed_tool_receipts"][0]["outcome"] == "denied"
    kinds = [
        checkpoint["kind"]
        for checkpoint in store.list_agent_run_checkpoints(identity.run_id)
    ]
    assert "waiting_approval" in kinds
    assert "approval_resolved" in kinds
    assert "tool_pending" not in kinds
    with sqlite3.connect(store.db_path) as db:
        assert db.execute(
            "SELECT lease_owner, lease_expires_at FROM agent_runs WHERE run_id = ?",
            (identity.run_id,),
        ).fetchone() == (None, None)


def test_atomic_approval_boundary_failure_leaves_no_inbox_or_permission(
    tmp_path, monkeypatch
):
    identity = _identity("run-approval-atomic-failure")
    store = ConversationStore(tmp_path)
    provider = FakeProvider(
        steps=[
            {
                "tool_calls": [
                    ToolCall(
                        id="call-atomic-failure",
                        name="gmail_draft",
                        arguments={"body": "private body"},
                    )
                ]
            }
        ]
    )

    def fail_atomic(*args, **kwargs):
        raise RuntimeError("atomic approval boundary failed")

    monkeypatch.setattr(
        AgentRunExecution,
        "waiting_approval_atomic",
        fail_atomic,
        raising=False,
    )
    result = asyncio.run(
        run_turn(
            text="Draft outreach",
            sid=identity.session_id,
            store=store,
            provider=provider,
            persona=None,
            skills=None,
            inbox=Inbox(store),
            openai_tools=[],
            execute_kwargs={},
            identity=identity,
        )
    )

    assert result["status"] == "error"
    assert Inbox(store).get("call-atomic-failure") is None
    assert "permission_required" not in {
        event["type"] for event in store.load_events(identity.session_id)
    }
    assert store.get_agent_run(identity.run_id)["current_state"] == "failed"


def test_permission_projection_observes_atomic_waiting_authority(
    tmp_path
):
    identity = _identity("run-approval-atomic-order")
    store = ConversationStore(tmp_path)
    provider = FakeProvider(
        steps=[
            {
                "tool_calls": [
                    ToolCall(
                        id="call-atomic-order",
                        name="gmail_draft",
                        arguments={"body": "private body"},
                    )
                ]
            }
        ]
    )
    observed = {}

    async def inspect_emit(event):
        if event["type"] == "permission_required":
            run = store.get_agent_run(identity.run_id)
            with sqlite3.connect(store.db_path) as db:
                lease = db.execute(
                    "SELECT lease_owner, lease_expires_at FROM agent_runs WHERE run_id = ?",
                    (identity.run_id,),
                ).fetchone()
            observed.update(
                run_state=run["current_state"],
                phase=run["continuation"]["cursor"]["phase"],
                lease=lease,
                inbox=Inbox(store).get("call-atomic-order"),
            )

    result = asyncio.run(
        run_turn(
            text="Draft outreach",
            sid=identity.session_id,
            store=store,
            provider=provider,
            persona=None,
            skills=None,
            inbox=Inbox(store),
            openai_tools=[],
            execute_kwargs={},
            emit=inspect_emit,
            identity=identity,
        )
    )

    assert result["status"] == "waiting"
    assert observed["run_state"] == "waiting_approval"
    assert observed["phase"] == "waiting_approval"
    assert observed["lease"] == (None, None)
    assert observed["inbox"]["state"] == "pending"
    assert observed["inbox"]["arguments"] == {"body": "private body"}


def test_permission_projection_failure_preserves_atomic_waiting_authority(
    tmp_path, monkeypatch
):
    identity = _identity("run-permission-projection-failure")
    store = ConversationStore(tmp_path)
    provider = FakeProvider(
        steps=[
            {
                "tool_calls": [
                    ToolCall(
                        id="call-projection-failure",
                        name="gmail_draft",
                        arguments={"body": "private body"},
                    )
                ]
            }
        ]
    )
    original_append_event = store.append_event

    def fail_permission_event(session_id, event):
        if event.get("type") == "permission_required":
            raise RuntimeError("permission projection failed")
        return original_append_event(session_id, event)

    monkeypatch.setattr(store, "append_event", fail_permission_event)
    result = asyncio.run(
        run_turn(
            text="Draft outreach",
            sid=identity.session_id,
            store=store,
            provider=provider,
            persona=None,
            skills=None,
            inbox=Inbox(store),
            openai_tools=[],
            execute_kwargs={},
            identity=identity,
        )
    )

    assert result["status"] == "error"
    run = store.get_agent_run(identity.run_id)
    assert run["current_state"] == "waiting_approval"
    assert run["continuation"]["cursor"]["phase"] == "waiting_approval"
    assert Inbox(store).get("call-projection-failure")["state"] == "pending"
    assert "permission_required" not in {
        event["type"] for event in store.load_events(identity.session_id)
    }
    with sqlite3.connect(store.db_path) as db:
        assert db.execute(
            "SELECT lease_owner, lease_expires_at FROM agent_runs WHERE run_id = ?",
            (identity.run_id,),
        ).fetchone() == (None, None)


def test_persisted_approval_decision_wins_over_wait_callback(tmp_path, monkeypatch):
    identity = _identity("run-approval-race")
    store = ConversationStore(tmp_path)
    inbox = Inbox(store)
    provider = FakeProvider(
        steps=[
            {
                "tool_calls": [
                    ToolCall(
                        id="call-draft-raced",
                        name="gmail_draft",
                        arguments={"body": "private draft body"},
                    )
                ]
            },
            {"deltas": ("Decision respected",)},
        ]
    )
    executed = []

    async def externally_deny(call_id):
        assert inbox.resolve(call_id, "deny") is not None
        return "allow"

    monkeypatch.setattr(
        "coworker.turn.execute",
        lambda *args, **kwargs: executed.append((args, kwargs)),
    )
    result = asyncio.run(
        run_turn(
            text="Respect the stored decision",
            sid=identity.session_id,
            store=store,
            provider=provider,
            persona=None,
            skills=None,
            inbox=inbox,
            openai_tools=[],
            execute_kwargs={},
            wait_permission=externally_deny,
            identity=identity,
        )
    )

    assert result["status"] == "partial"
    assert result["text"] == "Decision respected"
    assert executed == []
    assert inbox.get("call-draft-raced")["decision"] == "deny"
    run = store.get_agent_run(identity.run_id)
    assert run["current_state"] == "partial"
    assert run["continuation"]["completed_tool_receipts"][0]["outcome"] == "denied"


def test_claim_none_stale_waiter_returns_conflict_without_touching_authority(
    tmp_path, monkeypatch
):
    identity = _identity("run-claim-none-race")
    store = ConversationStore(tmp_path)
    provider = FakeProvider(
        steps=[
            {
                "tool_calls": [
                    ToolCall(
                        id="call-claim-none",
                        name="gmail_draft",
                        arguments={"body": "private body"},
                    )
                ]
            }
        ]
    )
    winner = []
    before = {}
    executions = []

    async def competing_cancel(call_id):
        competitor_store = ConversationStore(tmp_path)
        assert Inbox(competitor_store).cancel(call_id) is not None
        winner_execution = AgentRunExecution.resume_closed_approval(
            competitor_store,
            identity.run_id,
            call_id,
            "cancelled",
            8,
            owner_id="winner-owner",
        )
        winner.append(winner_execution)
        before.update(
            transcript=store.load(identity.session_id),
            events=store.load_events(identity.session_id),
            checkpoints=store.list_agent_run_checkpoints(identity.run_id),
            usage=store.get_agent_run(identity.run_id)["usage"],
            continuation=store.get_agent_run(identity.run_id)["continuation"],
        )
        return "allow"

    monkeypatch.setattr(
        "coworker.turn.execute",
        lambda *args, **kwargs: executions.append((args, kwargs)),
    )
    result = asyncio.run(
        run_turn(
            text="Compete for approval",
            sid=identity.session_id,
            store=store,
            provider=provider,
            persona=None,
            skills=None,
            inbox=Inbox(store),
            openai_tools=[],
            execute_kwargs={},
            wait_permission=competing_cancel,
            identity=identity,
        )
    )

    assert result["status"] == "conflict"
    assert executions == []
    assert len(winner) == 1 and winner[0].owner_id == "winner-owner"
    assert store.load(identity.session_id) == before["transcript"]
    assert store.load_events(identity.session_id) == before["events"]
    assert store.list_agent_run_checkpoints(identity.run_id) == before["checkpoints"]
    run = store.get_agent_run(identity.run_id)
    assert run["usage"] == before["usage"]
    assert run["continuation"] == before["continuation"]
    assert "tool_finished" not in {event["type"] for event in before["events"]}
    assert "approval_resolved" not in {
        event["type"] for event in before["events"]
    }


def test_externally_completed_approval_is_adopted_once(
    tmp_path, monkeypatch
):
    identity = _identity("run-adopt-external")
    store = ConversationStore(tmp_path)
    provider = FakeProvider(
        steps=[
            {
                "tool_calls": [
                    ToolCall(
                        id="call-adopt-external",
                        name="gmail_draft",
                        arguments={"body": "private body"},
                    )
                ]
            },
            {"deltas": ("Adopted",)},
        ]
    )
    external_executions = []

    async def external_executor(call_id):
        external_store = ConversationStore(tmp_path)
        external_inbox = Inbox(external_store)
        claim = external_inbox.decide_and_claim(
            call_id,
            "allow",
            actor="external",
            scope="once",
            claimant="external-owner",
        )
        assert claim is not None and claim.owned
        external_executions.append(call_id)
        assert external_inbox.complete_execution(
            call_id,
            claimant="external-owner",
            ok=True,
            result={"draft_id": "draft-external"},
        ) is not None
        return "allow"

    monkeypatch.setattr(
        "coworker.turn.execute",
        lambda *args, **kwargs: pytest.fail("local execute must not run"),
    )
    result = asyncio.run(
        run_turn(
            text="Adopt external execution",
            sid=identity.session_id,
            store=store,
            provider=provider,
            persona=None,
            skills=None,
            inbox=Inbox(store),
            openai_tools=[],
            execute_kwargs={},
            wait_permission=external_executor,
            identity=identity,
        )
    )

    assert result["status"] == "ok"
    assert external_executions == ["call-adopt-external"]
    run = store.get_agent_run(identity.run_id)
    assert run["usage"] == {"model_calls": 2, "tool_calls": 1}
    assert run["continuation"]["remaining_budgets"]["tool_calls"] == 7
    receipts = run["continuation"]["completed_tool_receipts"]
    assert len(receipts) == 1
    assert receipts[0]["call_id"] == "call-adopt-external"
    assert receipts[0]["outcome"] == "executed_external"
    kinds = [
        item["kind"] for item in store.list_agent_run_checkpoints(identity.run_id)
    ]
    assert kinds.count("tool_completed") == 1
    event_types = [event["type"] for event in store.load_events(identity.session_id)]
    assert "tool_started" not in event_types
    assert event_types.count("tool_finished") == 1


def test_external_execution_timeout_waits_lease_free_then_resumes_once(
    tmp_path, monkeypatch
):
    identity = _identity("run-waiting-external")
    store = ConversationStore(tmp_path)
    provider = FakeProvider(
        steps=[
            {
                "tool_calls": [
                    ToolCall(
                        id="call-waiting-external",
                        name="gmail_draft",
                        arguments={"body": "private body"},
                    )
                ]
            }
        ]
    )
    external_store = ConversationStore(tmp_path)
    external_inbox = Inbox(external_store)

    async def external_claimant(call_id):
        claim = external_inbox.decide_and_claim(
            call_id,
            "allow",
            actor="external",
            scope="once",
            claimant="external-owner",
        )
        assert claim is not None and claim.owned
        return "allow"

    monkeypatch.setenv("CLUB_APPROVAL_WAIT_TIMEOUT", "0.02")
    result = asyncio.run(
        run_turn(
            text="Wait for external execution",
            sid=identity.session_id,
            store=store,
            provider=provider,
            persona=None,
            skills=None,
            inbox=Inbox(store),
            openai_tools=[],
            execute_kwargs={},
            wait_permission=external_claimant,
            identity=identity,
        )
    )

    assert result["status"] == "waiting"
    run = store.get_agent_run(identity.run_id)
    assert run["current_state"] == "waiting_external"
    assert run["continuation"]["cursor"]["phase"] == "waiting_external"
    assert run["usage"] == {"model_calls": 1}
    assert run["continuation"]["remaining_budgets"]["tool_calls"] == 7
    assert external_inbox.get("call-waiting-external")["execution_status"] == (
        "executing"
    )
    assert [message["role"] for message in store.load(identity.session_id)] == [
        "user",
        "assistant",
    ]
    event_types = [event["type"] for event in store.load_events(identity.session_id)]
    assert "tool_finished" not in event_types
    assert "approval_resolved" not in event_types
    assert "turn_end" not in event_types
    assert store.list_agent_run_checkpoints(identity.run_id)[-1]["kind"] == (
        "waiting_external"
    )
    with sqlite3.connect(store.db_path) as db:
        assert db.execute(
            "SELECT lease_owner, lease_expires_at FROM agent_runs WHERE run_id = ?",
            (identity.run_id,),
        ).fetchone() == (None, None)

    with pytest.raises(AgentRunExecutionOwnershipError):
        AgentRunExecution.resume_external_completion(
            store, identity.run_id, "call-waiting-external", 8
        )
    assert external_inbox.complete_execution(
        "call-waiting-external",
        claimant="external-owner",
        ok=True,
        result={"draft_id": "draft-later"},
    ) is not None
    with pytest.raises(AgentRunExecutionOwnershipError):
        AgentRunExecution.resume_external_completion(
            store, identity.run_id, "wrong-call", 8
        )
    resumed = AgentRunExecution.resume_external_completion(
        store,
        identity.run_id,
        "call-waiting-external",
        8,
        owner_id="resume-external-owner",
    )
    adopted_receipt = resumed.adopted_external_receipt
    assert adopted_receipt["execution_result"] == {"draft_id": "draft-later"}
    run = store.get_agent_run(identity.run_id)
    assert run["current_state"] == "running"
    assert run["usage"] == {"model_calls": 1, "tool_calls": 1}
    assert run["continuation"]["remaining_budgets"]["tool_calls"] == 7
    assert run["continuation"]["completed_tool_receipts"][-1]["outcome"] == (
        "executed_external"
    )
    assert "pending_tool" not in run["continuation"]
    with sqlite3.connect(store.db_path) as db:
        db.execute(
            "UPDATE agent_runs SET lease_expires_at = ? WHERE run_id = ?",
            ("2000-01-01T00:00:00+00:00", identity.run_id),
        )
    store.agent_runs.reconcile_expired_leases()
    interrupted = store.get_agent_run(identity.run_id)
    assert interrupted["current_state"] == "interrupted"
    assert "pending_tool" not in interrupted["continuation"]
    recovered = AgentRunExecution.resume(
        store,
        identity,
        8,
        owner_id="after-crash-owner",
    )
    recovered.model_pending(
        store.load(identity.session_id),
        store.load_events(identity.session_id),
        1,
    )
    assert store.get_agent_run(identity.run_id)["continuation"]["cursor"][
        "phase"
    ] == "model_in_flight"


def test_zero_tool_budget_allow_is_not_run_and_live_turn_advances_partial(
    tmp_path, monkeypatch
):
    identity = _identity("run-zero-budget-allow")
    store = ConversationStore(tmp_path)
    external_store = ConversationStore(tmp_path)
    external_inbox = Inbox(external_store)
    provider = FakeProvider(
        steps=[
            {
                "tool_calls": [
                    ToolCall(
                        id="call-zero-budget",
                        name="gmail_draft",
                        arguments={"body": "private body"},
                    )
                ]
            },
            {"deltas": ("Could not draft",)},
        ]
    )
    external_executions = []
    _force_waiting_tool_budget(monkeypatch, store, 0)
    monkeypatch.setenv("CLUB_APPROVAL_WAIT_TIMEOUT", "0.02")

    async def external_allow(call_id):
        claim = external_inbox.decide_and_claim(
            call_id,
            "allow",
            actor="external",
            scope="once",
            claimant="external-owner",
        )
        assert claim is not None
        if claim.owned:
            external_executions.append(call_id)
        return "allow"

    result = asyncio.run(
        run_turn(
            text="Draft with no budget",
            sid=identity.session_id,
            store=store,
            provider=provider,
            persona=None,
            skills=None,
            inbox=Inbox(store),
            openai_tools=[],
            execute_kwargs={},
            wait_permission=external_allow,
            identity=identity,
        )
    )

    assert result["status"] == "partial"
    assert external_executions == []
    item = external_inbox.get("call-zero-budget")
    assert item["state"] == "resolved"
    assert item["decision"] == "allow"
    assert item["execution_status"] == "not_run"
    assert item["execution_claimant"] is None
    assert item["execution_error"] == "Agent Run tool budget exhausted"
    run = store.get_agent_run(identity.run_id)
    assert run["continuation"]["remaining_budgets"]["tool_calls"] == 0
    assert run["usage"] == {"model_calls": 2}
    assert "tool_budget_reserved" not in {
        checkpoint["kind"]
        for checkpoint in store.list_agent_run_checkpoints(identity.run_id)
    }


def test_external_claim_observes_reserved_last_tool_budget(tmp_path, monkeypatch):
    identity = _identity("run-one-budget-external")
    store = ConversationStore(tmp_path)
    external_store = ConversationStore(tmp_path)
    external_inbox = Inbox(external_store)
    provider = FakeProvider(
        steps=[
            {
                "tool_calls": [
                    ToolCall(
                        id="call-one-budget-external",
                        name="gmail_draft",
                        arguments={"body": "private body"},
                    )
                ]
            },
            {"deltas": ("Drafted externally",)},
        ]
    )
    observed = {}
    external_executions = []
    _force_waiting_tool_budget(monkeypatch, store, 1)

    async def external_allow(call_id):
        claim = external_inbox.decide_and_claim(
            call_id,
            "allow",
            actor="external",
            scope="once",
            claimant="external-owner",
        )
        assert claim is not None and claim.owned
        run = store.get_agent_run(identity.run_id)
        observed.update(
            budget=run["continuation"]["remaining_budgets"]["tool_calls"],
            pending=run["continuation"]["pending_tool"],
            checkpoint=store.list_agent_run_checkpoints(identity.run_id)[-1],
        )
        external_executions.append(call_id)
        external_inbox.complete_execution(
            call_id,
            claimant="external-owner",
            ok=True,
            result={"draft_id": "draft-one-budget"},
        )
        return "allow"

    result = asyncio.run(
        run_turn(
            text="Use last budget externally",
            sid=identity.session_id,
            store=store,
            provider=provider,
            persona=None,
            skills=None,
            inbox=Inbox(store),
            openai_tools=[],
            execute_kwargs={},
            wait_permission=external_allow,
            identity=identity,
        )
    )

    assert result["status"] == "ok"
    assert external_executions == ["call-one-budget-external"]
    assert observed["budget"] == 0
    assert observed["pending"]["budget_reserved"] is True
    assert observed["checkpoint"]["kind"] == "tool_budget_reserved"
    run = store.get_agent_run(identity.run_id)
    assert run["usage"] == {"model_calls": 2, "tool_calls": 1}
    assert run["continuation"]["remaining_budgets"]["tool_calls"] == 0


def test_local_allow_reserves_budget_before_execute_without_second_debit(
    tmp_path, monkeypatch
):
    identity = _identity("run-one-budget-local")
    store = ConversationStore(tmp_path)
    provider = FakeProvider(
        steps=[
            {
                "tool_calls": [
                    ToolCall(
                        id="call-one-budget-local",
                        name="gmail_draft",
                        arguments={"body": "private body"},
                    )
                ]
            },
            {"deltas": ("Drafted locally",)},
        ]
    )
    observed = {}
    _force_waiting_tool_budget(monkeypatch, store, 1)

    async def allow(_call_id):
        return "allow"

    def inspect_execute(name, arguments, **kwargs):
        run = store.get_agent_run(identity.run_id)
        observed.update(
            budget=run["continuation"]["remaining_budgets"]["tool_calls"],
            pending=run["continuation"]["pending_tool"],
            kinds=[
                item["kind"]
                for item in store.list_agent_run_checkpoints(identity.run_id)
            ],
        )
        return True, {"draft_id": "draft-local"}

    monkeypatch.setattr("coworker.turn.execute", inspect_execute)
    result = asyncio.run(
        run_turn(
            text="Use last budget locally",
            sid=identity.session_id,
            store=store,
            provider=provider,
            persona=None,
            skills=None,
            inbox=Inbox(store),
            openai_tools=[],
            execute_kwargs={},
            wait_permission=allow,
            identity=identity,
        )
    )

    assert result["status"] == "ok"
    assert observed["budget"] == 0
    assert observed["pending"]["budget_reserved"] is True
    assert observed["kinds"].count("tool_budget_reserved") == 1
    run = store.get_agent_run(identity.run_id)
    assert run["continuation"]["remaining_budgets"]["tool_calls"] == 0
    assert run["usage"] == {"model_calls": 2, "tool_calls": 1}


def test_deny_never_reserves_tool_budget(tmp_path, monkeypatch):
    identity = _identity("run-budget-deny")
    store = ConversationStore(tmp_path)
    provider = FakeProvider(
        steps=[
            {
                "tool_calls": [
                    ToolCall(
                        id="call-budget-deny",
                        name="gmail_draft",
                        arguments={"body": "private body"},
                    )
                ]
            },
            {"deltas": ("Denied",)},
        ]
    )
    _force_waiting_tool_budget(monkeypatch, store, 1)

    async def deny(_call_id):
        return "deny"

    result = asyncio.run(
        run_turn(
            text="Deny with budget",
            sid=identity.session_id,
            store=store,
            provider=provider,
            persona=None,
            skills=None,
            inbox=Inbox(store),
            openai_tools=[],
            execute_kwargs={},
            wait_permission=deny,
            identity=identity,
        )
    )

    assert result["status"] == "partial"
    run = store.get_agent_run(identity.run_id)
    assert run["continuation"]["remaining_budgets"]["tool_calls"] == 1
    assert "tool_budget_reserved" not in {
        checkpoint["kind"]
        for checkpoint in store.list_agent_run_checkpoints(identity.run_id)
    }


def test_approval_allow_reacquires_same_run_and_executes_once(
    tmp_path, monkeypatch
):
    identity = _identity("run-approval-allow")
    store = ConversationStore(tmp_path)
    provider = FakeProvider(
        steps=[
            {
                "tool_calls": [
                    ToolCall(
                        id="call-draft-allowed",
                        name="gmail_draft",
                        arguments={
                            "to": "person@example.test",
                            "subject": "Hello",
                            "body": "private draft body",
                        },
                    )
                ]
            },
            {"deltas": ("Drafted",)},
        ]
    )
    executed = []
    waiting_lease = []

    async def allow(call_id):
        run = store.get_agent_run(identity.run_id)
        waiting_lease.append((run["current_state"], run["continuation"]["identity"]))
        with sqlite3.connect(store.db_path) as db:
            waiting_lease.append(
                db.execute(
                    "SELECT lease_owner, lease_expires_at FROM agent_runs WHERE run_id = ?",
                    (identity.run_id,),
                ).fetchone()
            )
        return "allow"

    def execute_once(name, arguments, **kwargs):
        run = store.get_agent_run(identity.run_id)
        executed.append((name, run["continuation"]["pending_tool"]))
        return True, {"draft_id": "draft-1"}

    monkeypatch.setattr("coworker.turn.execute", execute_once)
    result = asyncio.run(
        run_turn(
            text="Draft outreach",
            sid=identity.session_id,
            store=store,
            provider=provider,
            persona=None,
            skills=None,
            inbox=Inbox(store),
            openai_tools=[],
            execute_kwargs={},
            wait_permission=allow,
            identity=identity,
        )
    )

    assert waiting_lease == [
        (
            "waiting_approval",
            {"message_id": identity.message_id, "part_id": identity.part_id},
        ),
        (None, None),
    ]
    assert result["status"] == "ok"
    assert len(executed) == 1
    assert executed[0][0] == "gmail_draft"
    assert executed[0][1]["status"] == "in_flight"
    run = store.get_agent_run(identity.run_id)
    assert run["current_state"] == "complete"
    assert run["usage"] == {"model_calls": 2, "tool_calls": 1}
    kinds = [
        checkpoint["kind"]
        for checkpoint in store.list_agent_run_checkpoints(identity.run_id)
    ]
    assert kinds.index("waiting_approval") < kinds.index("approval_resolved")
    assert kinds.index("approval_resolved") < kinds.index("tool_pending")
    with sqlite3.connect(store.db_path) as db:
        assert db.execute(
            "SELECT lease_owner, lease_expires_at FROM agent_runs WHERE run_id = ?",
            (identity.run_id,),
        ).fetchone() == (None, None)


def test_approved_completion_uses_one_joint_run_and_inbox_commit(
    tmp_path, monkeypatch
):
    identity = _identity("run-approved-atomic-success")
    store = ConversationStore(tmp_path)
    inbox = Inbox(store)
    provider = FakeProvider(
        steps=[
            {
                "tool_calls": [
                    ToolCall(
                        id="call-approved-atomic",
                        name="gmail_draft",
                        arguments={"body": "private body"},
                    )
                ]
            },
            {"deltas": ("Drafted",)},
        ]
    )
    executions = []

    async def allow(_call_id):
        return "allow"

    def execute_once(name, arguments, **kwargs):
        executions.append(name)
        return True, {"draft_id": "draft-atomic"}

    def forbidden_legacy_completion(*args, **kwargs):
        raise AssertionError("legacy inbox completion must not be called")

    monkeypatch.setattr("coworker.turn.execute", execute_once)
    monkeypatch.setattr(inbox, "complete_execution", forbidden_legacy_completion)
    result = asyncio.run(
        run_turn(
            text="Draft outreach",
            sid=identity.session_id,
            store=store,
            provider=provider,
            persona=None,
            skills=None,
            inbox=inbox,
            openai_tools=[],
            execute_kwargs={},
            wait_permission=allow,
            identity=identity,
        )
    )

    assert result["status"] == "ok"
    assert executions == ["gmail_draft"]
    run = store.get_agent_run(identity.run_id)
    receipt = inbox.get("call-approved-atomic")
    assert run["current_state"] == "complete"
    assert run["continuation"]["completed_tool_receipts"][0]["call_id"] == (
        "call-approved-atomic"
    )
    assert receipt["execution_status"] == "succeeded"
    assert receipt["execution_result"] == {"draft_id": "draft-atomic"}


def test_approved_atomic_completion_failure_classifies_both_outcomes_unknown(
    tmp_path, monkeypatch
):
    identity = _identity("run-approved-atomic-failure")
    store = ConversationStore(tmp_path)
    inbox = Inbox(store)
    provider = FakeProvider(
        steps=[
            {
                "tool_calls": [
                    ToolCall(
                        id="call-approved-failure",
                        name="gmail_draft",
                        arguments={"body": "private body"},
                    )
                ]
            }
        ]
    )
    executions = []

    async def allow(_call_id):
        return "allow"

    def execute_once(name, arguments, **kwargs):
        executions.append(name)
        return True, {"draft_id": "draft-unknown"}

    def fail_joint_completion(*args, **kwargs):
        raise RuntimeError("forced joint completion failure")

    monkeypatch.setattr("coworker.turn.execute", execute_once)
    monkeypatch.setattr(
        AgentRunExecution,
        "complete_approved_tool",
        fail_joint_completion,
        raising=False,
    )
    result = asyncio.run(
        run_turn(
            text="Draft outreach",
            sid=identity.session_id,
            store=store,
            provider=provider,
            persona=None,
            skills=None,
            inbox=inbox,
            openai_tools=[],
            execute_kwargs={},
            wait_permission=allow,
            identity=identity,
        )
    )

    assert result["status"] == "interrupted"
    assert executions == ["gmail_draft"]
    run = store.get_agent_run(identity.run_id)
    receipt = inbox.get("call-approved-failure")
    assert run["current_state"] == "interrupted"
    assert run["continuation"]["cursor"]["phase"] == "review_required"
    assert run["continuation"]["pending_tool"]["status"] == "outcome_unknown"
    assert receipt["execution_status"] == "interrupted"
    assert receipt["execution_claimant"] is None
    assert store.list_agent_run_checkpoints(identity.run_id)[-1]["kind"] == (
        "tool_outcome_unknown"
    )
    with sqlite3.connect(store.db_path) as db:
        assert db.execute(
            "SELECT lease_owner, lease_expires_at FROM agent_runs WHERE run_id = ?",
            (identity.run_id,),
        ).fetchone() == (None, None)


def test_live_approval_cancel_reacquires_only_to_stop_without_denial(
    tmp_path, monkeypatch
):
    identity = _identity("run-approval-cancel")
    store = ConversationStore(tmp_path)
    provider = FakeProvider(
        steps=[
            {
                "tool_calls": [
                    ToolCall(
                        id="call-draft-cancelled",
                        name="gmail_draft",
                        arguments={"body": "private draft body"},
                    )
                ]
            }
        ]
    )
    executed = []

    async def cancel(_call_id):
        return "cancel"

    monkeypatch.setattr(
        "coworker.turn.execute",
        lambda *args, **kwargs: executed.append((args, kwargs)),
    )
    result = asyncio.run(
        run_turn(
            text="Cancel draft",
            sid=identity.session_id,
            store=store,
            provider=provider,
            persona=None,
            skills=None,
            inbox=Inbox(store),
            openai_tools=[],
            execute_kwargs={},
            wait_permission=cancel,
            identity=identity,
        )
    )

    assert result["status"] == "stopped"
    assert executed == []
    item = Inbox(store).get("call-draft-cancelled")
    assert item["state"] == "cancelled"
    assert item["decision"] is None
    run = store.get_agent_run(identity.run_id)
    assert run["current_state"] == "stopped"
    assert [event["type"] for event in store.load_events(identity.session_id)] == [
        "turn_start",
        "permission_required",
        "approval_resolved",
        "turn_end",
    ]
    with sqlite3.connect(store.db_path) as db:
        assert db.execute(
            "SELECT lease_owner, lease_expires_at FROM agent_runs WHERE run_id = ?",
            (identity.run_id,),
        ).fetchone() == (None, None)


def test_terminal_authority_precedes_projection_and_prefixes_are_exact(
    tmp_path
):
    identity = _identity("run-terminal-order")
    store = ConversationStore(tmp_path)
    terminal_observation = {}

    async def inspect_emit(event):
        if event["type"] == "turn_end":
            run = store.get_agent_run(identity.run_id)
            with sqlite3.connect(store.db_path) as db:
                lease = db.execute(
                    "SELECT lease_owner, lease_expires_at FROM agent_runs WHERE run_id = ?",
                    (identity.run_id,),
                ).fetchone()
            terminal_observation.update(run=run, lease=lease)

    result = asyncio.run(
        run_turn(
            text="Finish normally",
            sid=identity.session_id,
            store=store,
            provider=FakeProvider(deltas=("Visible answer",)),
            persona=None,
            skills=None,
            inbox=Inbox(store),
            openai_tools=[],
            execute_kwargs={},
            emit=inspect_emit,
            identity=identity,
        )
    )

    assert result["status"] == "ok"
    assert terminal_observation["run"]["current_state"] == "complete"
    assert terminal_observation["lease"] == (None, None)
    run = store.get_agent_run(identity.run_id)
    transcript = store.load(identity.session_id)
    events = store.load_events(identity.session_id)
    cursor = run["continuation"]["cursor"]
    assert cursor["transcript_prefix_count"] == len(transcript)
    assert cursor["transcript_prefix_sha256"] == transcript_prefix_sha256(
        transcript
    )
    assert cursor["event_prefix_count"] == len(events) - 1
    assert cursor["event_prefix_sha256"] == transcript_prefix_sha256(events[:-1])
    continuation_json = json.dumps(run["continuation"], sort_keys=True)
    assert "Visible answer" not in continuation_json
    assert "assistant_delta" not in continuation_json


def test_terminal_projection_failure_returns_error_but_keeps_terminal_authority(
    tmp_path, monkeypatch
):
    identity = _identity("run-terminal-projection-failure")
    store = ConversationStore(tmp_path)
    original_append_event = store.append_event

    def fail_terminal_event(session_id, event):
        if event.get("type") in {"turn_end", "turn_stopped", "error"}:
            raise RuntimeError("terminal event append failed")
        return original_append_event(session_id, event)

    monkeypatch.setattr(store, "append_event", fail_terminal_event)
    result = asyncio.run(
        run_turn(
            text="Finish despite projection failure",
            sid=identity.session_id,
            store=store,
            provider=FakeProvider(deltas=("Durable answer",)),
            persona=None,
            skills=None,
            inbox=Inbox(store),
            openai_tools=[],
            execute_kwargs={},
            identity=identity,
        )
    )

    assert result == {
        "status": "error",
        "text": "Durable answer",
        "run_id": identity.run_id,
    }
    run = store.get_agent_run(identity.run_id)
    assert run["current_state"] == "complete"
    assert run["terminal_result"]["status"] == "ok"
    assert store.list_agent_run_checkpoints(identity.run_id)[-1]["kind"] == "terminal"
    with sqlite3.connect(store.db_path) as db:
        assert db.execute(
            "SELECT lease_owner, lease_expires_at FROM agent_runs WHERE run_id = ?",
            (identity.run_id,),
        ).fetchone() == (None, None)


def test_cancellation_provider_error_and_step_limit_terminalize_honestly(
    tmp_path, monkeypatch
):
    async def run_case(identity, provider, *, control=None):
        store = ConversationStore(tmp_path / identity.run_id)
        result = await run_turn(
            text="Run case",
            sid=identity.session_id,
            store=store,
            provider=provider,
            persona=None,
            skills=None,
            inbox=Inbox(store),
            openai_tools=[],
            execute_kwargs={},
            identity=identity,
            control=control,
        )
        return store, result

    cancel_identity = _identity("run-cancelled")
    cancel_control = RunControl(cancel_identity)
    cancel_control.cancel_requested.set()
    cancel_store, cancelled = asyncio.run(
        run_case(
            cancel_identity,
            FakeProvider(deltas=("must not be delivered",)),
            control=cancel_control,
        )
    )
    assert cancelled["status"] == "stopped"
    assert cancel_store.get_agent_run(cancel_identity.run_id)["current_state"] == "stopped"

    class FailingProvider:
        model_id = "failing"

        async def astream(self, *, messages, tools):
            raise RuntimeError("provider transport failed")
            yield

    error_identity = _identity("run-provider-error")
    error_store, errored = asyncio.run(run_case(error_identity, FailingProvider()))
    assert errored["status"] == "error"
    assert error_store.get_agent_run(error_identity.run_id)["current_state"] == "failed"

    class EndlessToolProvider:
        model_id = "endless"

        def __init__(self):
            self.calls = 0

        async def astream(self, *, messages, tools):
            call_id = f"call-{self.calls}"
            self.calls += 1
            yield StreamChunk(
                tool_calls=[ToolCall(id=call_id, name="now", arguments={})]
            )

    monkeypatch.setattr(
        "coworker.turn.execute", lambda *args, **kwargs: (True, {"time": "now"})
    )
    limit_identity = _identity("run-step-limit")
    limit_store, limited = asyncio.run(
        run_case(limit_identity, EndlessToolProvider())
    )
    assert limited["status"] == "stopped"
    limit_run = limit_store.get_agent_run(limit_identity.run_id)
    assert limit_run["current_state"] == "stopped"
    assert limit_run["usage"] == {"model_calls": 8, "tool_calls": 8}
    for store, identity in (
        (cancel_store, cancel_identity),
        (error_store, error_identity),
        (limit_store, limit_identity),
    ):
        with sqlite3.connect(store.db_path) as db:
            assert db.execute(
                "SELECT lease_owner, lease_expires_at FROM agent_runs WHERE run_id = ?",
                (identity.run_id,),
            ).fetchone() == (None, None)


def test_same_identity_ownership_race_calls_one_provider_and_emits_one_turn(
    tmp_path
):
    identity = _identity("run-race")
    store = ConversationStore(tmp_path)

    class BlockingProvider:
        model_id = "blocking"

        def __init__(self):
            self.calls = 0
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def astream(self, *, messages, tools):
            self.calls += 1
            self.started.set()
            await self.release.wait()
            yield StreamChunk(text_delta="Winner")

    provider = BlockingProvider()

    async def invoke():
        return await run_turn(
            text="Race",
            sid=identity.session_id,
            store=store,
            provider=provider,
            persona=None,
            skills=None,
            inbox=Inbox(store),
            openai_tools=[],
            execute_kwargs={},
            identity=identity,
        )

    async def race():
        winner = asyncio.create_task(invoke())
        await provider.started.wait()
        loser = asyncio.create_task(invoke())
        loser_result = await loser
        provider.release.set()
        winner_result = await winner
        return winner_result, loser_result

    winner, loser = asyncio.run(race())

    assert winner["status"] == "ok"
    assert loser == {"status": "conflict", "text": "", "run_id": identity.run_id}
    assert provider.calls == 1
    assert [event["type"] for event in store.load_events(identity.session_id)] == [
        "turn_start",
        "assistant_delta",
        "turn_end",
    ]
    assert [message["role"] for message in store.load(identity.session_id)] == [
        "user",
        "assistant",
    ]


def test_heartbeat_keeps_tiny_lease_alive_during_blocked_provider(tmp_path):
    identity = _identity("run-heartbeat-provider")
    store = ConversationStore(tmp_path)

    class BlockedProvider:
        model_id = "blocked-provider"

        async def astream(self, *, messages, tools):
            await asyncio.sleep(2.25)
            yield StreamChunk(text_delta="Provider survived")

    result = asyncio.run(
        run_turn(
            text="Wait for provider",
            sid=identity.session_id,
            store=store,
            provider=BlockedProvider(),
            persona=None,
            skills=None,
            inbox=Inbox(store),
            openai_tools=[],
            execute_kwargs={},
            identity=identity,
            lease_seconds=1.5,
        )
    )

    assert result["status"] == "ok"
    assert store.get_agent_run(identity.run_id)["current_state"] == "complete"


def test_heartbeat_keeps_tiny_lease_alive_during_blocked_tool(
    tmp_path, monkeypatch
):
    identity = _identity("run-heartbeat-tool")
    store = ConversationStore(tmp_path)
    provider = FakeProvider(
        steps=[
            {"tool_calls": [ToolCall(id="call-slow", name="now", arguments={})]},
            {"deltas": ("Tool survived",)},
        ]
    )
    executions = []

    def blocked_execute(name, arguments, **kwargs):
        executions.append(name)
        time.sleep(2.25)
        return True, {"time": "noon"}

    monkeypatch.setattr("coworker.turn.execute", blocked_execute)
    result = asyncio.run(
        run_turn(
            text="Wait for tool",
            sid=identity.session_id,
            store=store,
            provider=provider,
            persona=None,
            skills=None,
            inbox=Inbox(store),
            openai_tools=[],
            execute_kwargs={},
            identity=identity,
            lease_seconds=1.5,
        )
    )

    assert result["status"] == "ok"
    assert executions == ["now"]
    assert store.get_agent_run(identity.run_id)["current_state"] == "complete"


def test_model_heartbeat_renewal_loss_reconciles_retryable_boundary(
    tmp_path, monkeypatch
):
    identity = _identity("run-heartbeat-model-loss")
    store = ConversationStore(tmp_path)
    renewals = []
    original_renew = AgentRunExecution.renew

    class BlockedProvider:
        model_id = "blocked-provider"

        async def astream(self, *, messages, tools):
            await asyncio.sleep(0.12)
            yield StreamChunk(text_delta="must not checkpoint")

    def expire_on_heartbeat(self):
        renewals.append(self.metadata["phase"])
        if self.metadata["phase"] == "model_in_flight":
            with sqlite3.connect(store.db_path) as db:
                db.execute(
                    "UPDATE agent_runs SET lease_expires_at = ? WHERE run_id = ?",
                    ("2000-01-01T00:00:00+00:00", identity.run_id),
                )
        return original_renew(self)

    monkeypatch.setattr(AgentRunExecution, "renew", expire_on_heartbeat)
    result = asyncio.run(
        run_turn(
            text="Lose model lease",
            sid=identity.session_id,
            store=store,
            provider=BlockedProvider(),
            persona=None,
            skills=None,
            inbox=Inbox(store),
            openai_tools=[],
            execute_kwargs={},
            identity=identity,
            lease_seconds=0.09,
        )
    )

    assert result["status"] == "interrupted"
    run = store.get_agent_run(identity.run_id)
    assert run["current_state"] == "interrupted"
    assert run["continuation"]["cursor"]["phase"] == "model_ready"
    assert run["continuation"]["pending_model"]["status"] == "retry_ready"
    with sqlite3.connect(store.db_path) as db:
        assert db.execute(
            "SELECT lease_owner, lease_expires_at FROM agent_runs WHERE run_id = ?",
            (identity.run_id,),
        ).fetchone() == (None, None)


def test_heartbeat_owner_takeover_fences_stale_without_checkpointing(
    tmp_path, monkeypatch
):
    identity = _identity("run-heartbeat-takeover")
    store = ConversationStore(tmp_path)
    original_renew = AgentRunExecution.renew
    takeover = []

    class BlockedProvider:
        model_id = "blocked-provider"

        async def astream(self, *, messages, tools):
            await asyncio.sleep(0.12)
            yield StreamChunk(text_delta="must not persist")

    def take_over_on_heartbeat(self):
        if self.metadata["phase"] == "model_in_flight" and not takeover:
            with sqlite3.connect(store.db_path) as db:
                db.execute(
                    "UPDATE agent_runs SET lease_expires_at = ? WHERE run_id = ?",
                    ("2000-01-01T00:00:00+00:00", identity.run_id),
                )
            run = store.get_agent_run(identity.run_id)
            takeover.append(
                store.agent_runs.acquire_lease(
                    identity.run_id,
                    "owner-takeover",
                    run["version"],
                    1,
                )
            )
        return original_renew(self)

    monkeypatch.setattr(AgentRunExecution, "renew", take_over_on_heartbeat)
    result = asyncio.run(
        run_turn(
            text="Lose ownership",
            sid=identity.session_id,
            store=store,
            provider=BlockedProvider(),
            persona=None,
            skills=None,
            inbox=Inbox(store),
            openai_tools=[],
            execute_kwargs={},
            identity=identity,
            lease_seconds=0.09,
        )
    )

    assert result["status"] == "conflict"
    assert len(takeover) == 1 and takeover[0].owner_id == "owner-takeover"
    run = store.get_agent_run(identity.run_id)
    assert run["current_state"] == "running"
    assert run["continuation"]["cursor"]["phase"] == "model_in_flight"
    assert [
        item["kind"] for item in store.list_agent_run_checkpoints(identity.run_id)
    ][-1] == "model_pending"
    assert "assistant_delta" not in {
        event["type"] for event in store.load_events(identity.session_id)
    }


def test_approved_tool_heartbeat_loss_executes_once_and_classifies_unknown(
    tmp_path, monkeypatch
):
    identity = _identity("run-heartbeat-approved-loss")
    store = ConversationStore(tmp_path)
    inbox = Inbox(store)
    provider = FakeProvider(
        steps=[
            {
                "tool_calls": [
                    ToolCall(
                        id="call-heartbeat-approved",
                        name="gmail_draft",
                        arguments={"body": "private body"},
                    )
                ]
            }
        ]
    )
    executions = []
    original_renew = AgentRunExecution.renew

    async def allow(_call_id):
        return "allow"

    def blocked_execute(name, arguments, **kwargs):
        executions.append(name)
        time.sleep(0.12)
        return True, {"draft_id": "draft-heartbeat-unknown"}

    def expire_approved_tool(self):
        if self.metadata["phase"] == "tool_in_flight":
            with sqlite3.connect(store.db_path) as db:
                db.execute(
                    "UPDATE agent_runs SET lease_expires_at = ? WHERE run_id = ?",
                    ("2000-01-01T00:00:00+00:00", identity.run_id),
                )
        return original_renew(self)

    monkeypatch.setattr("coworker.turn.execute", blocked_execute)
    monkeypatch.setattr(AgentRunExecution, "renew", expire_approved_tool)
    result = asyncio.run(
        run_turn(
            text="Lose approved tool lease",
            sid=identity.session_id,
            store=store,
            provider=provider,
            persona=None,
            skills=None,
            inbox=inbox,
            openai_tools=[],
            execute_kwargs={},
            wait_permission=allow,
            identity=identity,
            lease_seconds=0.09,
        )
    )

    assert result["status"] == "interrupted"
    assert executions == ["gmail_draft"]
    run = store.get_agent_run(identity.run_id)
    receipt = inbox.get("call-heartbeat-approved")
    assert run["current_state"] == "interrupted"
    assert run["continuation"]["cursor"]["phase"] == "review_required"
    assert run["continuation"]["pending_tool"]["status"] == "outcome_unknown"
    assert receipt["execution_status"] == "interrupted"
    assert receipt["execution_claimant"] is None
