import asyncio
import hashlib
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from coworker.agent_run_continuation import transcript_prefix_sha256
from coworker.agent_run_execution import AgentRunExecution
from coworker.events import TurnIdentity, build_event
from coworker.inbox import Inbox
from coworker.provider import FakeProvider, StreamChunk, ToolCall
from coworker.store import ConversationStore
from coworker.turn import resume_turn, run_turn


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
    original_replace_events = store._rewrite_event_projection
    replacements = []

    def fail_first_event_rewrite(sid, events):
        replacements.append("event")
        if len(replacements) == 1:
            raise OSError("injected event rewrite crash")
        return original_replace_events(sid, events)

    monkeypatch.setattr(store, "_rewrite_event_projection", fail_first_event_rewrite)
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
    assert interrupted_repair["current_state"] == "interrupted"

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


def test_stale_projection_owner_cannot_publish_after_new_owner_repairs(
    tmp_path,
):
    first = ConversationStore(tmp_path)
    identity = _identity("run-stale-projection-publisher")
    _seed_model_retry(first, identity)
    first.append(identity.session_id, {"role": "user", "content": "TAIL"})
    first.append_event(
        identity.session_id,
        build_event(
            identity,
            "assistant_delta",
            event_id="event-stale-tail",
            delta="TAIL",
        ),
    )
    stale = AgentRunExecution.resume(
        first, identity, 8, owner_id="stale-projection-owner"
    )
    cursor = first.get_agent_run(identity.run_id)["continuation"]["cursor"]
    marker = {
        key: cursor[key]
        for key in (
            "transcript_prefix_count",
            "transcript_prefix_sha256",
            "event_prefix_count",
            "event_prefix_sha256",
        )
    }
    stale.begin_projection_repair(marker)
    with sqlite3.connect(first.db_path) as db:
        db.execute(
            "UPDATE agent_runs SET lease_expires_at = ? WHERE run_id = ?",
            ("2000-01-01T00:00:00+00:00", identity.run_id),
        )
    second = ConversationStore(tmp_path)
    second.agent_runs.reconcile_expired_leases()
    ready = threading.Barrier(2)
    release_stale = threading.Event()
    stale_callback = []
    stale_errors = []

    def stale_publish():
        ready.wait()
        release_stale.wait()
        try:
            stale.publish_projection_repair(
                lambda: stale_callback.append("published")
            )
        except Exception as exc:
            stale_errors.append(type(exc).__name__)

    thread = threading.Thread(target=stale_publish)
    thread.start()
    ready.wait()
    provider = FakeProvider(deltas=("New owner result",))
    repaired = asyncio.run(
        resume_turn(
            run_id=identity.run_id,
            store=second,
            provider=provider,
            dependencies=_dependencies(second),
        )
    )
    release_stale.set()
    thread.join(timeout=2)

    assert repaired["status"] == "ok"
    assert stale_callback == []
    assert stale_errors
    assert [message.get("content") for message in second.load(identity.session_id)][
        -1
    ] == "New owner result"


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


def test_resume_rejects_wrong_prior_tool_result_name_even_with_matching_prefix(
    tmp_path, monkeypatch
):
    store = ConversationStore(tmp_path)
    identity = _identity("run-wrong-prior-result-name")
    calls = [
        ToolCall(id="call-prior", name="gmail_search", arguments={}),
        ToolCall(id="call-next", name="gmail_search", arguments={}),
    ]
    _seed_unstarted_tool_boundary(store, identity, calls, next_index=1)
    messages = store.load(identity.session_id)
    messages[-1]["name"] = "drive_search"
    store.replace_all(identity.session_id, messages)
    continuation = store.get_agent_run(identity.run_id)["continuation"]
    continuation["cursor"]["transcript_prefix_sha256"] = transcript_prefix_sha256(
        messages
    )
    with sqlite3.connect(store.db_path) as db:
        db.execute(
            "UPDATE agent_runs SET continuation = ? WHERE run_id = ?",
            (json.dumps(continuation), identity.run_id),
        )
    executed = []
    monkeypatch.setattr(
        "coworker.turn.execute",
        lambda *args, **kwargs: executed.append("tool") or (True, {}),
    )

    result = asyncio.run(
        resume_turn(
            run_id=identity.run_id,
            store=store,
            provider=FakeProvider(deltas=("must not run",)),
            dependencies=_dependencies(store),
        )
    )

    assert result["status"] == "review_required"
    assert executed == []


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


@pytest.mark.parametrize(
    ("outcome", "ok"),
    [
        ("executed", False),
        ("failed_unexecuted", False),
        ("failed_external", False),
        ("denied", False),
    ],
)
def test_resume_preserves_durable_prior_tool_failure_as_partial(
    tmp_path, outcome, ok
):
    store = ConversationStore(tmp_path)
    identity = _identity(f"run-prior-failure-{outcome}")
    call = ToolCall(id="call-prior-failure", name="gmail_search", arguments={})
    _seed_unstarted_tool_boundary(store, identity, [call], next_index=1)
    continuation = store.get_agent_run(identity.run_id)["continuation"]
    continuation["completed_tool_receipts"][0]["outcome"] = outcome
    continuation["completed_tool_receipts"][0]["ok"] = ok
    with sqlite3.connect(store.db_path) as db:
        db.execute(
            "UPDATE agent_runs SET continuation = ? WHERE run_id = ?",
            (json.dumps(continuation), identity.run_id),
        )

    result = asyncio.run(
        resume_turn(
            run_id=identity.run_id,
            store=store,
            provider=FakeProvider(deltas=("Done",)),
            dependencies=_dependencies(store),
        )
    )

    assert result["status"] == "partial"
    assert store.get_agent_run(identity.run_id)["current_state"] == "partial"


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


def test_safe_tool_checkpoint_failure_truncates_uncommitted_result_before_retry(
    tmp_path, monkeypatch
):
    store = ConversationStore(tmp_path)
    identity = _identity("run-safe-tool-result-retry")
    call = ToolCall(id="call-result-retry", name="gmail_search", arguments={})
    provider = FakeProvider(
        steps=[
            {"tool_calls": [call]},
            {"deltas": ("Recovered",)},
        ]
    )
    executions = []

    def execute_tool(name, arguments, **kwargs):
        executions.append(name)
        return True, {"attempt": len(executions)}

    original_completed = AgentRunExecution.tool_completed
    failures = []

    def fail_first_completion(self, *args, **kwargs):
        if not failures:
            failures.append("failed")
            raise OSError("checkpoint unavailable")
        return original_completed(self, *args, **kwargs)

    monkeypatch.setattr("coworker.turn.execute", execute_tool)
    monkeypatch.setattr(AgentRunExecution, "tool_completed", fail_first_completion)
    first = asyncio.run(
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

    assert first["status"] == "interrupted"
    interrupted = store.get_agent_run(identity.run_id)
    assert interrupted["continuation"]["pending_tool"]["status"] == "retry_ready"

    second = asyncio.run(
        resume_turn(
            run_id=identity.run_id,
            store=store,
            provider=provider,
            dependencies=_dependencies(store),
        )
    )

    assert second["status"] == "ok"
    assert executions == [call.name, call.name]
    transcript = store.load(identity.session_id)
    assert [
        message.get("tool_call_id")
        for message in transcript
        if message.get("role") == "tool"
    ] == [call.id]
    assert [
        event.get("id")
        for event in store.load_events(identity.session_id)
        if event.get("type") == "tool_finished"
    ] == [call.id]
    final = store.get_agent_run(identity.run_id)
    assert final["continuation"]["remaining_budgets"]["tool_calls"] == 7
    assert final["usage"] == {"model_calls": 2, "tool_calls": 1}
    assert len(final["continuation"]["completed_tool_receipts"]) == 1


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


def _seed_terminal_without_event(
    store: ConversationStore, identity: TurnIdentity, state: str
) -> None:
    asyncio.run(
        run_turn(
            text="Finish",
            sid=identity.session_id,
            store=store,
            provider=FakeProvider(deltas=("Visible answer",)),
            persona=None,
            skills=None,
            inbox=Inbox(store),
            openai_tools=[],
            execute_kwargs={},
            identity=identity,
        )
    )
    store.replace_events(
        identity.session_id,
        [
            event
            for event in store.load_events(identity.session_id)
            if event.get("type") not in {"turn_end", "turn_stopped", "error"}
        ],
    )
    run = store.get_agent_run(identity.run_id)
    continuation = run["continuation"]
    continuation["cursor"]["phase"] = state
    terminal_result = {
        "status": {
            "complete": "ok",
            "partial": "partial",
            "stopped": "stopped",
            "failed": "error",
        }[state],
        "message_id": identity.message_id,
        "text_length": len("Visible answer"),
    }
    if state == "failed":
        terminal_result["error"] = "Recovered failure"
        terminal_result["class"] = "run_error"
    with sqlite3.connect(store.db_path) as db:
        db.execute(
            """
            UPDATE agent_runs
            SET current_state = ?, continuation = ?, terminal_result = ?
            WHERE run_id = ?
            """,
            (
                state,
                json.dumps(continuation),
                json.dumps(terminal_result),
                identity.run_id,
            ),
        )


@pytest.mark.parametrize(
    ("state", "status", "event_type"),
    [
        ("complete", "ok", "turn_end"),
        ("partial", "partial", "turn_end"),
        ("stopped", "stopped", "turn_end"),
        ("failed", "error", "error"),
    ],
)
def test_explicit_terminal_projection_repair_is_idempotent(
    tmp_path, state, status, event_type
):
    store = ConversationStore(tmp_path)
    identity = _identity(f"run-terminal-repair-{state}")
    _seed_terminal_without_event(store, identity, state)
    emitted = []

    async def emit(event):
        emitted.append(event)

    first = asyncio.run(
        resume_turn(
            run_id=identity.run_id,
            store=store,
            provider=None,
            dependencies=_dependencies(store),
            emit=emit,
        )
    )
    second = asyncio.run(
        resume_turn(
            run_id=identity.run_id,
            store=store,
            provider=None,
            dependencies=_dependencies(store),
            emit=emit,
        )
    )

    assert first["status"] == second["status"] == status
    terminal_events = [
        event
        for event in store.load_events(identity.session_id)
        if event.get("type") in {"turn_end", "turn_stopped", "error"}
    ]
    assert len(terminal_events) == 1
    assert terminal_events[0]["type"] == event_type
    assert terminal_events[0]["event_id"].startswith("event_terminal_")
    assert emitted == terminal_events


def test_terminal_projection_repair_race_and_torn_line_append_once(tmp_path):
    seed = ConversationStore(tmp_path)
    identity = _identity("run-terminal-repair-race")
    _seed_terminal_without_event(seed, identity, "complete")
    with open(seed.event_dir / f"{identity.session_id}.jsonl", "ab") as fh:
        fh.write(b'{"event_id":"torn')
    barrier = threading.Barrier(2)

    def repair():
        store = ConversationStore(tmp_path)
        barrier.wait()
        return asyncio.run(
            resume_turn(
                run_id=identity.run_id,
                store=store,
                provider=None,
                dependencies=_dependencies(store),
            )
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: repair(), range(2)))

    assert [result["status"] for result in results] == ["ok", "ok"]
    terminal_events = [
        event
        for event in seed.load_events(identity.session_id)
        if event.get("type") == "turn_end"
    ]
    assert len(terminal_events) == 1


def test_live_terminal_event_failure_is_repaired_explicitly(tmp_path, monkeypatch):
    store = ConversationStore(tmp_path)
    identity = _identity("run-live-terminal-repair")
    original_append = store.append_event

    def fail_terminal(sid, event):
        if event.get("type") == "turn_end":
            raise OSError("terminal event unavailable")
        return original_append(sid, event)

    monkeypatch.setattr(store, "append_event", fail_terminal)
    live = asyncio.run(
        run_turn(
            text="Finish",
            sid=identity.session_id,
            store=store,
            provider=FakeProvider(deltas=("Done",)),
            persona=None,
            skills=None,
            inbox=Inbox(store),
            openai_tools=[],
            execute_kwargs={},
            identity=identity,
        )
    )
    assert live["status"] == "error"
    assert store.get_agent_run(identity.run_id)["current_state"] == "complete"

    repaired = asyncio.run(
        resume_turn(
            run_id=identity.run_id,
            store=store,
            provider=None,
            dependencies=_dependencies(store),
        )
    )

    assert repaired["status"] == "ok"
    assert len(
        [
            event
            for event in store.load_events(identity.session_id)
            if event.get("type") == "turn_end"
        ]
    ) == 1


def test_resumed_terminal_event_failure_is_repaired_explicitly(
    tmp_path, monkeypatch
):
    store = ConversationStore(tmp_path)
    identity = _identity("run-resumed-terminal-repair")
    _seed_model_retry(store, identity)
    original_append = store.append_event

    def fail_terminal(sid, event):
        if event.get("type") == "turn_end":
            raise OSError("resumed terminal event unavailable")
        return original_append(sid, event)

    monkeypatch.setattr(store, "append_event", fail_terminal)
    first = asyncio.run(
        resume_turn(
            run_id=identity.run_id,
            store=store,
            provider=FakeProvider(deltas=("Done",)),
            dependencies=_dependencies(store),
        )
    )
    assert first["status"] == "error"
    assert store.get_agent_run(identity.run_id)["current_state"] == "complete"

    repaired = asyncio.run(
        resume_turn(
            run_id=identity.run_id,
            store=store,
            provider=None,
            dependencies=_dependencies(store),
        )
    )

    assert repaired["status"] == "ok"
    assert len(
        [
            event
            for event in store.load_events(identity.session_id)
            if event.get("type") == "turn_end"
            and event.get("state") == "complete"
        ]
    ) == 1


def test_terminal_repair_write_failure_can_retry_immediately(tmp_path, monkeypatch):
    store = ConversationStore(tmp_path)
    identity = _identity("run-terminal-repair-write-failure")
    _seed_terminal_without_event(store, identity, "complete")
    original_append_once = store.append_event_once
    attempts = []

    def fail_once(sid, event):
        attempts.append(event["event_id"])
        if len(attempts) == 1:
            raise OSError("recovery append failed")
        return original_append_once(sid, event)

    monkeypatch.setattr(store, "append_event_once", fail_once)
    first = asyncio.run(
        resume_turn(
            run_id=identity.run_id,
            store=store,
            provider=None,
            dependencies=_dependencies(store),
        )
    )
    second = asyncio.run(
        resume_turn(
            run_id=identity.run_id,
            store=store,
            provider=None,
            dependencies=_dependencies(store),
        )
    )

    assert first["status"] == "error"
    assert second["status"] == "ok"
    assert attempts[0] == attempts[1]


def test_interrupted_event_does_not_mask_missing_final_terminal_event(tmp_path):
    store = ConversationStore(tmp_path)
    identity = _identity("run-interrupted-before-terminal-repair")
    _seed_terminal_without_event(store, identity, "complete")
    store.append_event(
        identity.session_id,
        build_event(
            identity,
            "turn_end",
            event_id="event-earlier-interruption",
            state="interrupted",
            text="",
            message="Earlier interruption",
        ),
    )

    result = asyncio.run(
        resume_turn(
            run_id=identity.run_id,
            store=store,
            provider=None,
            dependencies=_dependencies(store),
        )
    )

    assert result["status"] == "ok"
    assert [
        event["state"]
        for event in store.load_events(identity.session_id)
        if event.get("type") == "turn_end"
    ] == ["interrupted", "complete"]


def test_resume_memory_tool_refreshes_system_prompt_before_followup_model(
    tmp_path, monkeypatch
):
    store = ConversationStore(tmp_path)
    identity = _identity("run-resume-memory-refresh")
    call = ToolCall(id="call-remember", name="remember", arguments={"content": "x"})
    _seed_unstarted_tool_boundary(store, identity, [call], next_index=0)
    prompt_versions = []
    memory = {"version": 0}

    def system_prompt(*args, **kwargs):
        prompt_versions.append(memory["version"])
        return f"memory-version-{memory['version']}"

    def execute_tool(name, arguments, **kwargs):
        memory["version"] = 1
        return True, {"remembered": True}

    monkeypatch.setattr("coworker.turn.execute", execute_tool)
    provider = FakeProvider(deltas=("Done",))
    dependencies = _dependencies(store)
    dependencies["system_prompt_fn"] = system_prompt
    result = asyncio.run(
        resume_turn(
            run_id=identity.run_id,
            store=store,
            provider=provider,
            dependencies=dependencies,
        )
    )

    assert result["status"] == "ok"
    assert prompt_versions == [0, 1]
    assert provider.calls[0][0]["content"] == "memory-version-1"


def test_resume_policy_denial_projects_person_file_failure(tmp_path, monkeypatch):
    store = ConversationStore(tmp_path)
    identity = _identity("run-resume-denied-person")
    call = ToolCall(id="call-denied", name="unknown_tool", arguments={})
    _seed_unstarted_tool_boundary(store, identity, [call], next_index=0)
    projected = []

    def record(sid, tool_call, ok, result, execute_kwargs):
        projected.append((sid, tool_call.id, ok, result))

    monkeypatch.setattr("coworker.turn._record_person_file", record)
    result = asyncio.run(
        resume_turn(
            run_id=identity.run_id,
            store=store,
            provider=FakeProvider(deltas=("Done",)),
            dependencies=_dependencies(store),
        )
    )

    assert result["status"] == "partial"
    assert projected == [
        (
            identity.session_id,
            call.id,
            False,
            {"error": "unknown tool unknown_tool"},
        )
    ]


def test_resume_permission_event_includes_approval_resource(tmp_path):
    store = ConversationStore(tmp_path)
    identity = _identity("run-resume-permission-resource")
    call = ToolCall(
        id="call-draft-resource",
        name="gmail_send",
        arguments={"draft_id": "draft-1"},
    )
    _seed_unstarted_tool_boundary(store, identity, [call], next_index=0)

    class Gmail:
        def get_draft(self, *, draft_id):
            return {"to": "person@example.com", "subject": "Hello"}

        def account(self):
            return "fisher@example.com"

    emitted = []

    async def emit(event):
        emitted.append(event)

    dependencies = _dependencies(store)
    dependencies["execute_kwargs"] = {"gmail": Gmail()}
    result = asyncio.run(
        resume_turn(
            run_id=identity.run_id,
            store=store,
            provider=FakeProvider(deltas=("must not run",)),
            dependencies=dependencies,
            emit=emit,
        )
    )

    assert result["status"] == "waiting"
    permission = next(
        event for event in emitted if event["type"] == "permission_required"
    )
    assert permission["resource"] == {
        "kind": "gmail_draft",
        "draft_id": "draft-1",
        "to": "person@example.com",
        "subject": "Hello",
        "account": "fisher@example.com",
    }
