import asyncio
import hashlib
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from coworker.agent_run_execution import AgentRunExecution
from coworker.events import TurnIdentity, build_event
from coworker.inbox import Inbox
from coworker.provider import FakeProvider, StreamChunk, ToolCall
from coworker.store import ConversationStore
from coworker.turn import resume_turn


def _identity(run_id: str = "run-resume-model") -> TurnIdentity:
    return TurnIdentity(
        session_id="thread-resume",
        run_id=run_id,
        message_id=f"message-{run_id}",
        part_id=f"part-{run_id}",
    )


def _dependencies(store: ConversationStore) -> dict:
    return {
        "persona": None,
        "skills": None,
        "inbox": Inbox(store),
        "openai_tools": [],
        "execute_kwargs": {},
    }


def _seed_model_retry(store: ConversationStore, identity: TurnIdentity) -> None:
    execution = AgentRunExecution.start(
        store,
        identity,
        "Find candidates",
        "chat",
        "fake",
        8,
        owner_id="crashed-owner",
        lease_seconds=30,
    )
    store.append(identity.session_id, {"role": "user", "content": "Find candidates"})
    store.append_event(
        identity.session_id,
        build_event(
            identity,
            "turn_start",
            event_id="event-original-start",
            state="running",
        ),
    )
    execution.user_input(
        store.load(identity.session_id), store.load_events(identity.session_id), 15
    )
    execution.model_pending(
        store.load(identity.session_id), store.load_events(identity.session_id), 0
    )
    with sqlite3.connect(store.db_path) as db:
        db.execute(
            "UPDATE agent_runs SET lease_expires_at = ? WHERE run_id = ?",
            ("2000-01-01T00:00:00+00:00", identity.run_id),
        )
    store.agent_runs.reconcile_expired_leases()


def _digest(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _seed_safe_tool_retry(
    store: ConversationStore,
    identity: TurnIdentity,
    calls: list[ToolCall],
    *,
    pending_index: int,
    retry_safe: bool = True,
) -> None:
    execution = AgentRunExecution.start(
        store,
        identity,
        "Find candidates",
        "chat",
        "fake",
        8,
        owner_id="crashed-tool-owner",
        lease_seconds=30,
    )
    store.append(identity.session_id, {"role": "user", "content": "Find candidates"})
    store.append_event(
        identity.session_id,
        build_event(
            identity,
            "turn_start",
            event_id=f"event-{identity.run_id}-start",
            state="running",
        ),
    )
    execution.user_input(
        store.load(identity.session_id), store.load_events(identity.session_id), 15
    )
    execution.model_pending(
        store.load(identity.session_id), store.load_events(identity.session_id), 0
    )
    assistant = {
        "role": "assistant",
        "content": "Searching",
        "message_id": identity.message_id,
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments),
                },
            }
            for call in calls
        ],
    }
    store.append(identity.session_id, assistant)
    execution.model_completed(
        store.load(identity.session_id),
        store.load_events(identity.session_id),
        0,
        len(calls),
        len("Searching"),
    )
    for index, call in enumerate(calls[:pending_index]):
        execution.tool_pending(
            store.load(identity.session_id),
            store.load_events(identity.session_id),
            0,
            index,
            call.id,
            call.name,
            True,
        )
        result = {"seeded": call.id}
        store.append(
            identity.session_id,
            {
                "role": "tool",
                "name": call.name,
                "tool_call_id": call.id,
                "content": json.dumps(result),
                "message_id": identity.message_id,
            },
        )
        execution.tool_completed(
            store.load(identity.session_id),
            store.load_events(identity.session_id),
            0,
            index,
            call.id,
            call.name,
            True,
            _digest(result),
        )
    pending = calls[pending_index]
    store.append_event(
        identity.session_id,
        build_event(
            identity,
            "tool_started",
            event_id=f"event-{pending.id}-started",
            id=pending.id,
            name=pending.name,
            arguments=pending.arguments,
            started_at="2026-08-26T00:00:00+00:00",
        ),
    )
    execution.tool_pending(
        store.load(identity.session_id),
        store.load_events(identity.session_id),
        0,
        pending_index,
        pending.id,
        pending.name,
        retry_safe,
    )
    with sqlite3.connect(store.db_path) as db:
        db.execute(
            "UPDATE agent_runs SET lease_expires_at = ? WHERE run_id = ?",
            ("2000-01-01T00:00:00+00:00", identity.run_id),
        )
    store.agent_runs.reconcile_expired_leases()


def _seed_unstarted_tool_boundary(
    store: ConversationStore,
    identity: TurnIdentity,
    calls: list[ToolCall],
    *,
    next_index: int,
) -> None:
    execution = AgentRunExecution.start(
        store,
        identity,
        "Find candidates",
        "chat",
        "fake",
        8,
        owner_id="crashed-unstarted-owner",
        lease_seconds=30,
    )
    store.append(identity.session_id, {"role": "user", "content": "Find candidates"})
    store.append_event(
        identity.session_id,
        build_event(
            identity,
            "turn_start",
            event_id=f"event-{identity.run_id}-start",
            state="running",
        ),
    )
    execution.user_input(
        store.load(identity.session_id), store.load_events(identity.session_id), 15
    )
    execution.model_pending(
        store.load(identity.session_id), store.load_events(identity.session_id), 0
    )
    store.append(
        identity.session_id,
        {
            "role": "assistant",
            "content": "Searching",
            "message_id": identity.message_id,
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments),
                    },
                }
                for call in calls
            ],
        },
    )
    execution.model_completed(
        store.load(identity.session_id),
        store.load_events(identity.session_id),
        0,
        len(calls),
        len("Searching"),
    )
    for index, call in enumerate(calls[:next_index]):
        execution.tool_pending(
            store.load(identity.session_id),
            store.load_events(identity.session_id),
            0,
            index,
            call.id,
            call.name,
            True,
        )
        payload = {"seeded": call.id}
        store.append(
            identity.session_id,
            {
                "role": "tool",
                "name": call.name,
                "tool_call_id": call.id,
                "content": json.dumps(payload),
                "message_id": identity.message_id,
            },
        )
        execution.tool_completed(
            store.load(identity.session_id),
            store.load_events(identity.session_id),
            0,
            index,
            call.id,
            call.name,
            True,
            _digest(payload),
        )
    with sqlite3.connect(store.db_path) as db:
        db.execute(
            "UPDATE agent_runs SET lease_expires_at = ? WHERE run_id = ?",
            ("2000-01-01T00:00:00+00:00", identity.run_id),
        )
    store.agent_runs.reconcile_expired_leases()


def test_resume_model_retry_preserves_identity_and_budget_without_duplicate_user(
    tmp_path,
):
    store = ConversationStore(tmp_path)
    identity = _identity()
    _seed_model_retry(store, identity)
    provider = FakeProvider(deltas=("Done",))

    result = asyncio.run(
        resume_turn(
            run_id=identity.run_id,
            store=store,
            provider=provider,
            dependencies=_dependencies(store),
        )
    )

    assert result == {"status": "ok", "text": "Done", "run_id": identity.run_id}
    assert len(provider.calls) == 1
    transcript = store.load(identity.session_id)
    assert [message["role"] for message in transcript] == ["user", "assistant"]
    run = store.get_agent_run(identity.run_id)
    assert run["current_state"] == "complete"
    assert run["continuation"]["identity"] == {
        "message_id": identity.message_id,
        "part_id": identity.part_id,
    }
    assert run["continuation"]["remaining_budgets"]["work_steps"] == 7
    assert run["usage"] == {"model_calls": 1}
    resume_events = store.load_events(identity.session_id)
    assert [event["type"] for event in resume_events].count("turn_start") == 2
    assert {
        (event["run_id"], event["message_id"], event["part_id"])
        for event in resume_events
    } == {(identity.run_id, identity.message_id, identity.part_id)}


def test_exact_projection_resume_does_not_create_repair_marker(tmp_path, monkeypatch):
    store = ConversationStore(tmp_path)
    identity = _identity("run-exact-no-repair")
    _seed_model_retry(store, identity)

    def reject_marker(self, marker):
        raise AssertionError("exact projections must not create a repair marker")

    monkeypatch.setattr(
        AgentRunExecution, "begin_projection_repair", reject_marker
    )
    result = asyncio.run(
        resume_turn(
            run_id=identity.run_id,
            store=store,
            provider=FakeProvider(deltas=("Done",)),
            dependencies=_dependencies(store),
        )
    )

    assert result["status"] == "ok"


def test_resume_truncates_uncommitted_transcript_and_event_tails_before_provider(
    tmp_path,
):
    store = ConversationStore(tmp_path)
    identity = _identity("run-resume-extra-tail")
    _seed_model_retry(store, identity)
    store.append(identity.session_id, {"role": "user", "content": "UNCOMMITTED"})
    store.append_event(
        identity.session_id,
        build_event(
            identity,
            "assistant_delta",
            event_id="event-uncommitted-tail",
            delta="UNCOMMITTED",
        ),
    )
    provider = FakeProvider(deltas=("Recovered",))

    result = asyncio.run(
        resume_turn(
            run_id=identity.run_id,
            store=store,
            provider=provider,
            dependencies=_dependencies(store),
        )
    )

    assert result["status"] == "ok"
    assert "UNCOMMITTED" not in json.dumps(provider.calls[0])
    assert [message["content"] for message in store.load(identity.session_id)] == [
        "Find candidates",
        "Recovered",
    ]
    assert "event-uncommitted-tail" not in {
        event["event_id"] for event in store.load_events(identity.session_id)
    }
    assert "projection_repair" not in store.get_agent_run(identity.run_id)[
        "continuation"
    ]


def test_active_resume_attempt_leaves_projection_tails_untouched(tmp_path):
    store = ConversationStore(tmp_path)
    identity = _identity("run-active-projection-owner")
    _seed_model_retry(store, identity)
    AgentRunExecution.resume(store, identity, 8, owner_id="active-owner")
    store.append(identity.session_id, {"role": "user", "content": "TAIL"})
    store.append_event(
        identity.session_id,
        build_event(
            identity,
            "assistant_delta",
            event_id="event-active-tail",
            delta="TAIL",
        ),
    )
    before_messages = store.load(identity.session_id)
    before_events = store.load_events(identity.session_id)

    result = asyncio.run(
        resume_turn(
            run_id=identity.run_id,
            store=store,
            provider=FakeProvider(deltas=("must not run",)),
            dependencies=_dependencies(store),
        )
    )

    assert result["status"] == "conflict"
    assert store.load(identity.session_id) == before_messages
    assert store.load_events(identity.session_id) == before_events


def test_projection_repair_crash_between_files_is_durable_and_retryable(
    tmp_path, monkeypatch
):
    store = ConversationStore(tmp_path)
    identity = _identity("run-projection-repair-crash")
    _seed_model_retry(store, identity)
    store.append(identity.session_id, {"role": "user", "content": "TAIL"})
    store.append_event(
        identity.session_id,
        build_event(
            identity,
            "assistant_delta",
            event_id="event-repair-tail",
            delta="TAIL",
        ),
    )
    original_replace_events = store.replace_events
    replacements = []

    def fail_first_event_rewrite(sid, events):
        replacements.append("event")
        if len(replacements) == 1:
            raise OSError("injected event rewrite crash")
        return original_replace_events(sid, events)

    monkeypatch.setattr(store, "replace_events", fail_first_event_rewrite)
    provider = FakeProvider(deltas=("Recovered",))
    first = asyncio.run(
        resume_turn(
            run_id=identity.run_id,
            store=store,
            provider=provider,
            dependencies=_dependencies(store),
        )
    )

    assert first["status"] == "error"
    assert provider.calls == []
    interrupted_repair = store.get_agent_run(identity.run_id)
    marker = interrupted_repair["continuation"]["projection_repair"]
    assert marker["transcript_prefix_count"] == 1
    assert marker["event_prefix_count"] == 1
    assert [message["content"] for message in store.load(identity.session_id)] == [
        "Find candidates"
    ]
    assert "event-repair-tail" in {
        event["event_id"] for event in store.load_events(identity.session_id)
    }
    with sqlite3.connect(store.db_path) as db:
        db.execute(
            "UPDATE agent_runs SET lease_expires_at = ? WHERE run_id = ?",
            ("2000-01-01T00:00:00+00:00", identity.run_id),
        )
    store.agent_runs.reconcile_expired_leases()

    second = asyncio.run(
        resume_turn(
            run_id=identity.run_id,
            store=store,
            provider=provider,
            dependencies=_dependencies(store),
        )
    )

    assert second["status"] == "ok"
    assert len(provider.calls) == 1
    final = store.get_agent_run(identity.run_id)
    assert "projection_repair" not in final["continuation"]
    assert "event-repair-tail" not in {
        event["event_id"] for event in store.load_events(identity.session_id)
    }


def test_projection_repair_failure_before_marker_leaves_files_untouched(
    tmp_path, monkeypatch
):
    store = ConversationStore(tmp_path)
    identity = _identity("run-projection-repair-marker-failure")
    _seed_model_retry(store, identity)
    store.append(identity.session_id, {"role": "user", "content": "TAIL"})
    store.append_event(
        identity.session_id,
        build_event(
            identity,
            "assistant_delta",
            event_id="event-marker-tail",
            delta="TAIL",
        ),
    )
    before_messages = store.load(identity.session_id)
    before_events = store.load_events(identity.session_id)

    def fail_marker(self, marker):
        raise OSError("marker write failed")

    monkeypatch.setattr(
        AgentRunExecution, "begin_projection_repair", fail_marker, raising=False
    )
    provider = FakeProvider(deltas=("must not run",))
    result = asyncio.run(
        resume_turn(
            run_id=identity.run_id,
            store=store,
            provider=provider,
            dependencies=_dependencies(store),
        )
    )

    assert result["status"] == "error"
    assert provider.calls == []
    assert store.load(identity.session_id) == before_messages
    assert store.load_events(identity.session_id) == before_events
    assert "projection_repair" not in store.get_agent_run(identity.run_id)[
        "continuation"
    ]


@pytest.mark.parametrize(
    ("projection", "damage"),
    [
        ("transcript", "missing"),
        ("event", "missing"),
        ("transcript", "hash"),
        ("event", "hash"),
    ],
)
def test_resume_projection_mismatch_marks_review_required_without_external_work(
    tmp_path, projection, damage
):
    store = ConversationStore(tmp_path)
    identity = _identity(f"run-resume-{damage}-{projection}")
    _seed_model_retry(store, identity)
    if projection == "transcript" and damage == "missing":
        store.replace_all(identity.session_id, [])
    elif projection == "event" and damage == "missing":
        store.replace_events(identity.session_id, [])
    elif projection == "transcript":
        store.replace_all(
            identity.session_id, [{"role": "user", "content": "DIFFERENT"}]
        )
    else:
        store.replace_events(
            identity.session_id,
            [
                build_event(
                    identity,
                    "turn_start",
                    event_id="event-different",
                    state="running",
                )
            ],
        )
    provider = FakeProvider(deltas=("must not run",))

    result = asyncio.run(
        resume_turn(
            run_id=identity.run_id,
            store=store,
            provider=provider,
            dependencies=_dependencies(store),
        )
    )

    assert result["status"] == "review_required"
    assert provider.calls == []
    run = store.get_agent_run(identity.run_id)
    assert run["current_state"] == "interrupted"
    assert run["continuation"]["cursor"]["phase"] == "review_required"
    checkpoints = store.list_agent_run_checkpoints(identity.run_id)
    assert checkpoints[-1]["kind"] == "projection_mismatch"
    assert checkpoints[-1]["payload"]["reason"] == "projection_mismatch"


def test_resume_safe_pending_tool_executes_before_provider_without_second_budget_debit(
    tmp_path, monkeypatch
):
    store = ConversationStore(tmp_path)
    identity = _identity("run-resume-safe-tool")
    call = ToolCall(
        id="call-safe-pending",
        name="gmail_search",
        arguments={"query": "site:example.com"},
    )
    _seed_safe_tool_retry(store, identity, [call], pending_index=0)
    attempt_id = store.get_agent_run(identity.run_id)["continuation"]["pending_tool"][
        "attempt_id"
    ]
    order = []

    def execute_tool(name, arguments, **kwargs):
        order.append(("tool", name, arguments))
        return True, {"messages": []}

    class OrderedProvider:
        model_id = "ordered"

        async def astream(self, *, messages, tools):
            order.append(("provider", [message["role"] for message in messages]))
            yield StreamChunk(text_delta="Finished")

    monkeypatch.setattr("coworker.turn.execute", execute_tool)
    result = asyncio.run(
        resume_turn(
            run_id=identity.run_id,
            store=store,
            provider=OrderedProvider(),
            dependencies=_dependencies(store),
        )
    )

    assert result["status"] == "ok"
    assert [item[0] for item in order] == ["tool", "provider"]
    run = store.get_agent_run(identity.run_id)
    assert run["continuation"]["remaining_budgets"] == {
        "work_steps": 6,
        "tool_calls": 7,
        "delivery_passes": 1,
    }
    assert run["usage"] == {"model_calls": 2, "tool_calls": 1}
    receipts = run["continuation"]["completed_tool_receipts"]
    assert [(receipt["call_id"], receipt["attempt_id"]) for receipt in receipts] == [
        (call.id, attempt_id)
    ]


def test_resume_multiple_tools_starts_at_pending_index_and_never_reexecutes_prior(
    tmp_path, monkeypatch
):
    store = ConversationStore(tmp_path)
    identity = _identity("run-resume-multiple-tools")
    calls = [
        ToolCall(id=f"call-{index}", name="gmail_search", arguments={"q": index})
        for index in range(3)
    ]
    _seed_safe_tool_retry(store, identity, calls, pending_index=1)
    executed = []

    def execute_tool(name, arguments, **kwargs):
        executed.append(arguments["q"])
        return True, {"q": arguments["q"]}

    monkeypatch.setattr("coworker.turn.execute", execute_tool)
    provider = FakeProvider(deltas=("All done",))
    result = asyncio.run(
        resume_turn(
            run_id=identity.run_id,
            store=store,
            provider=provider,
            dependencies=_dependencies(store),
        )
    )

    assert result["status"] == "ok"
    assert executed == [1, 2]
    assert len(provider.calls) == 1
    run = store.get_agent_run(identity.run_id)
    receipts = run["continuation"]["completed_tool_receipts"]
    assert [receipt["call_id"] for receipt in receipts] == [
        "call-0",
        "call-1",
        "call-2",
    ]
    assert run["usage"] == {"model_calls": 2, "tool_calls": 3}


@pytest.mark.parametrize("next_index", [0, 1])
def test_resume_tools_ready_without_pending_starts_at_unstarted_index(
    tmp_path, monkeypatch, next_index
):
    store = ConversationStore(tmp_path)
    identity = _identity(f"run-unstarted-tool-{next_index}")
    calls = [
        ToolCall(
            id=f"call-unstarted-{index}",
            name="gmail_search",
            arguments={"q": index},
        )
        for index in range(3)
    ]
    _seed_unstarted_tool_boundary(store, identity, calls, next_index=next_index)
    executed = []

    def execute_tool(name, arguments, **kwargs):
        executed.append(arguments["q"])
        return True, {"q": arguments["q"]}

    monkeypatch.setattr("coworker.turn.execute", execute_tool)
    provider = FakeProvider(deltas=("Done",))
    result = asyncio.run(
        resume_turn(
            run_id=identity.run_id,
            store=store,
            provider=provider,
            dependencies=_dependencies(store),
        )
    )

    assert result["status"] == "ok"
    assert executed == list(range(next_index, 3))
    run = store.get_agent_run(identity.run_id)
    assert [
        receipt["call_id"]
        for receipt in run["continuation"]["completed_tool_receipts"]
    ] == [call.id for call in calls]


def test_resume_unstarted_consequential_tool_enters_approval_wait(tmp_path):
    store = ConversationStore(tmp_path)
    identity = _identity("run-unstarted-consequential")
    call = ToolCall(id="call-unstarted-draft", name="gmail_draft", arguments={})
    _seed_unstarted_tool_boundary(store, identity, [call], next_index=0)
    provider = FakeProvider(deltas=("must not run",))

    result = asyncio.run(
        resume_turn(
            run_id=identity.run_id,
            store=store,
            provider=provider,
            dependencies=_dependencies(store),
        )
    )

    assert result["status"] == "waiting"
    assert provider.calls == []
    run = store.get_agent_run(identity.run_id)
    assert run["current_state"] == "waiting_approval"


@pytest.mark.parametrize("case", ["consequential", "policy_drift"])
def test_resume_rejects_unknown_or_no_longer_safe_tool_without_external_work(
    tmp_path, monkeypatch, case
):
    store = ConversationStore(tmp_path)
    identity = _identity(f"run-resume-{case}")
    if case == "consequential":
        call = ToolCall(id="call-consequential", name="gmail_draft", arguments={})
        _seed_safe_tool_retry(
            store, identity, [call], pending_index=0, retry_safe=False
        )
    else:
        call = ToolCall(id="call-policy-drift", name="gmail_search", arguments={})
        _seed_safe_tool_retry(store, identity, [call], pending_index=0)
        monkeypatch.setattr("coworker.turn._SAFE_RETRY_TOOLS", frozenset())
    external = []

    def execute_tool(*args, **kwargs):
        external.append("tool")
        return True, {}

    class CountingProvider:
        model_id = "counting"

        async def astream(self, *, messages, tools):
            external.append("provider")
            yield StreamChunk(text_delta="must not run")

    monkeypatch.setattr("coworker.turn.execute", execute_tool)
    result = asyncio.run(
        resume_turn(
            run_id=identity.run_id,
            store=store,
            provider=CountingProvider(),
            dependencies=_dependencies(store),
        )
    )

    assert result["status"] == "review_required"
    assert external == []
    run = store.get_agent_run(identity.run_id)
    assert run["current_state"] == "interrupted"
    assert run["continuation"]["cursor"]["phase"] == "review_required"


def test_resume_after_completed_tool_receipt_advances_to_provider_without_reexecution(
    tmp_path, monkeypatch
):
    store = ConversationStore(tmp_path)
    identity = _identity("run-resume-completed-receipt")
    call = ToolCall(id="call-already-complete", name="gmail_search", arguments={})
    _seed_safe_tool_retry(store, identity, [call], pending_index=0)
    run = store.get_agent_run(identity.run_id)
    resumed = AgentRunExecution.resume(store, identity, 8, owner_id="repair-owner")
    resumed.tool_pending(
        store.load(identity.session_id),
        store.load_events(identity.session_id),
        0,
        0,
        call.id,
        call.name,
        True,
    )
    result_payload = {"already": "done"}
    store.append(
        identity.session_id,
        {
            "role": "tool",
            "name": call.name,
            "tool_call_id": call.id,
            "content": json.dumps(result_payload),
            "message_id": identity.message_id,
        },
    )
    resumed.tool_completed(
        store.load(identity.session_id),
        store.load_events(identity.session_id),
        0,
        0,
        call.id,
        call.name,
        True,
        _digest(result_payload),
    )
    with sqlite3.connect(store.db_path) as db:
        db.execute(
            "UPDATE agent_runs SET lease_expires_at = ? WHERE run_id = ?",
            ("2000-01-01T00:00:00+00:00", identity.run_id),
        )
    store.agent_runs.reconcile_expired_leases()
    executed = []
    monkeypatch.setattr(
        "coworker.turn.execute",
        lambda *args, **kwargs: executed.append("tool") or (True, {}),
    )
    provider = FakeProvider(deltas=("Continued",))

    result = asyncio.run(
        resume_turn(
            run_id=identity.run_id,
            store=store,
            provider=provider,
            dependencies=_dependencies(store),
        )
    )

    assert result["status"] == "ok"
    assert executed == []
    assert len(provider.calls) == 1
    final = store.get_agent_run(identity.run_id)
    assert len(final["continuation"]["completed_tool_receipts"]) == 1
    assert final["usage"] == {"model_calls": 2, "tool_calls": 1}


def test_two_explicit_resume_racers_have_one_tool_and_provider_owner(
    tmp_path, monkeypatch
):
    seed_store = ConversationStore(tmp_path)
    identity = _identity("run-resume-race")
    call = ToolCall(id="call-race", name="gmail_search", arguments={})
    _seed_safe_tool_retry(seed_store, identity, [call], pending_index=0)
    barrier = threading.Barrier(2)
    lock = threading.Lock()
    tool_calls = []
    provider_calls = []

    def execute_tool(name, arguments, **kwargs):
        with lock:
            tool_calls.append(name)
        return True, {"messages": []}

    class CountingProvider:
        model_id = "race-provider"

        async def astream(self, *, messages, tools):
            with lock:
                provider_calls.append(messages)
            await asyncio.sleep(0.02)
            yield StreamChunk(text_delta="Won")

    monkeypatch.setattr("coworker.turn.execute", execute_tool)
    provider = CountingProvider()

    def race():
        store = ConversationStore(tmp_path)
        barrier.wait()
        return asyncio.run(
            resume_turn(
                run_id=identity.run_id,
                store=store,
                provider=provider,
                dependencies=_dependencies(store),
            )
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: race(), range(2)))

    assert sorted(result["status"] for result in results) == ["conflict", "ok"]
    assert tool_calls == [call.name]
    assert len(provider_calls) == 1
    final = seed_store.get_agent_run(identity.run_id)
    assert final["usage"] == {"model_calls": 2, "tool_calls": 1}
    assert len(final["continuation"]["completed_tool_receipts"]) == 1


def test_resumed_model_can_continue_through_safe_tool_and_followup_model(
    tmp_path, monkeypatch
):
    store = ConversationStore(tmp_path)
    identity = _identity("run-resume-model-then-tool")
    _seed_model_retry(store, identity)
    call = ToolCall(id="call-after-model-resume", name="gmail_search", arguments={})
    provider = FakeProvider(
        steps=[
            {"deltas": ("Searching",), "tool_calls": [call]},
            {"deltas": ("Complete",)},
        ]
    )
    executed = []
    monkeypatch.setattr(
        "coworker.turn.execute",
        lambda name, arguments, **kwargs: executed.append(name)
        or (True, {"messages": []}),
    )

    result = asyncio.run(
        resume_turn(
            run_id=identity.run_id,
            store=store,
            provider=provider,
            dependencies=_dependencies(store),
        )
    )

    assert result == {"status": "ok", "text": "Complete", "run_id": identity.run_id}
    assert len(provider.calls) == 2
    assert executed == [call.name]
    run = store.get_agent_run(identity.run_id)
    assert run["usage"] == {"model_calls": 2, "tool_calls": 1}
    assert [message["role"] for message in store.load(identity.session_id)] == [
        "user",
        "assistant",
        "tool",
        "assistant",
    ]


def test_tool_completion_lost_ack_resumes_without_duplicate_usage_or_receipt(
    tmp_path, monkeypatch
):
    store = ConversationStore(tmp_path)
    identity = _identity("run-resume-tool-lost-ack")
    call = ToolCall(id="call-lost-ack", name="gmail_search", arguments={})
    _seed_safe_tool_retry(store, identity, [call], pending_index=0)
    execution = AgentRunExecution.resume(store, identity, 8, owner_id="lost-ack-owner")
    execution.tool_pending(
        store.load(identity.session_id),
        store.load_events(identity.session_id),
        0,
        0,
        call.id,
        call.name,
        True,
    )
    result_payload = {"messages": []}
    store.append(
        identity.session_id,
        {
            "role": "tool",
            "name": call.name,
            "tool_call_id": call.id,
            "content": json.dumps(result_payload),
            "message_id": identity.message_id,
        },
    )
    original_checkpoint = store.agent_runs.checkpoint_leased

    def commit_then_lose_ack(lease, kind, continuation, **kwargs):
        committed = original_checkpoint(lease, kind, continuation, **kwargs)
        if kind == "tool_completed":
            raise ConnectionError("lost acknowledgement")
        return committed

    monkeypatch.setattr(store.agent_runs, "checkpoint_leased", commit_then_lose_ack)
    with pytest.raises(ConnectionError, match="lost acknowledgement"):
        execution.tool_completed(
            store.load(identity.session_id),
            store.load_events(identity.session_id),
            0,
            0,
            call.id,
            call.name,
            True,
            _digest(result_payload),
        )
    committed = store.get_agent_run(identity.run_id)
    assert committed["usage"] == {"model_calls": 1, "tool_calls": 1}
    assert len(committed["continuation"]["completed_tool_receipts"]) == 1
    with sqlite3.connect(store.db_path) as db:
        db.execute(
            "UPDATE agent_runs SET lease_expires_at = ? WHERE run_id = ?",
            ("2000-01-01T00:00:00+00:00", identity.run_id),
        )
    store.agent_runs.reconcile_expired_leases()
    executed = []
    monkeypatch.setattr(
        "coworker.turn.execute",
        lambda *args, **kwargs: executed.append("tool") or (True, {}),
    )
    provider = FakeProvider(deltas=("Recovered",))

    result = asyncio.run(
        resume_turn(
            run_id=identity.run_id,
            store=store,
            provider=provider,
            dependencies=_dependencies(store),
        )
    )

    assert result["status"] == "ok"
    assert executed == []
    final = store.get_agent_run(identity.run_id)
    assert final["usage"] == {"model_calls": 2, "tool_calls": 1}
    assert len(final["continuation"]["completed_tool_receipts"]) == 1


def test_matching_completed_receipt_repairs_stale_pending_cursor_without_reexecution(
    tmp_path, monkeypatch
):
    store = ConversationStore(tmp_path)
    identity = _identity("run-resume-stale-pending-receipt")
    call = ToolCall(id="call-stale-pending", name="gmail_search", arguments={})
    _seed_safe_tool_retry(store, identity, [call], pending_index=0)
    original_pending = dict(
        store.get_agent_run(identity.run_id)["continuation"]["pending_tool"]
    )
    execution = AgentRunExecution.resume(store, identity, 8, owner_id="stale-owner")
    execution.tool_pending(
        store.load(identity.session_id),
        store.load_events(identity.session_id),
        0,
        0,
        call.id,
        call.name,
        True,
    )
    result_payload = {"messages": []}
    store.append(
        identity.session_id,
        {
            "role": "tool",
            "name": call.name,
            "tool_call_id": call.id,
            "content": json.dumps(result_payload),
            "message_id": identity.message_id,
        },
    )
    execution.tool_completed(
        store.load(identity.session_id),
        store.load_events(identity.session_id),
        0,
        0,
        call.id,
        call.name,
        True,
        _digest(result_payload),
    )
    stale = store.get_agent_run(identity.run_id)["continuation"]
    stale["cursor"]["next_tool_index"] = 0
    stale["pending_tool"] = original_pending
    with sqlite3.connect(store.db_path) as db:
        db.execute(
            """
            UPDATE agent_runs
            SET continuation = ?, lease_expires_at = ?
            WHERE run_id = ?
            """,
            (
                json.dumps(stale),
                "2000-01-01T00:00:00+00:00",
                identity.run_id,
            ),
        )
    store.agent_runs.reconcile_expired_leases()
    executed = []
    monkeypatch.setattr(
        "coworker.turn.execute",
        lambda *args, **kwargs: executed.append("tool") or (True, {}),
    )
    provider = FakeProvider(deltas=("Continued",))

    result = asyncio.run(
        resume_turn(
            run_id=identity.run_id,
            store=store,
            provider=provider,
            dependencies=_dependencies(store),
        )
    )

    assert result["status"] == "ok"
    assert executed == []
    final = store.get_agent_run(identity.run_id)
    assert final["usage"] == {"model_calls": 2, "tool_calls": 1}
    assert len(final["continuation"]["completed_tool_receipts"]) == 1
    assert any(
        checkpoint["payload"].get("reason") == "completed_receipt_recovered"
        for checkpoint in store.list_agent_run_checkpoints(identity.run_id)
    )
