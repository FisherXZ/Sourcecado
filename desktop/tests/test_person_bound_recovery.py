from __future__ import annotations

import asyncio
import json

from coworker.agent_run_repository import AgentRunRepository
from coworker.inbox import Inbox
from coworker.people import PersonStore
from coworker.provider import FakeProvider, StreamChunk
from coworker.provider_retry import RetryPolicy
from coworker.store import ConversationStore
from coworker.turn import run_turn


SID = "retained-person-chat"


def _seed_retained_shape(root):
    store = ConversationStore(root)
    store.create_session(SID)
    store.append(SID, {"role": "user", "content": "Prepare the person file."})
    store.append(
        SID,
        {
            "role": "assistant",
            "content": None,
            "message_id": "legacy-tool-message",
            "tool_calls": [
                {
                    "id": "completed-send",
                    "type": "function",
                    "function": {
                        "name": "gmail_send",
                        "arguments": json.dumps({"draft_id": "draft-already-sent"}),
                    },
                }
            ],
        },
    )
    store.append(
        SID,
        {
            "role": "tool",
            "name": "gmail_send",
            "tool_call_id": "completed-send",
            "message_id": "legacy-tool-message",
            "content": json.dumps({"sent": True, "message_id": "sent-once"}),
        },
    )
    store.append(
        SID,
        {
            "role": "assistant",
            "message_id": "legacy-answer",
            "content": "The approved message was sent.",
        },
    )
    store.append(SID, {"role": "user", "content": "Keep the context."})
    store.append(SID, {"role": "assistant", "content": "Context retained."})
    store.append(SID, {"role": "user", "content": "First failed request."})
    store.append(SID, {"role": "user", "content": "Second failed request."})
    store.append(SID, {"role": "user", "content": "Third failed request."})
    store.append(SID, {"role": "assistant", "content": "Recovered once."})
    store.append(SID, {"role": "user", "content": "Latest retained request."})

    people = PersonStore(root)
    person = people.keep_from_apollo(
        apollo_id="retained-person",
        first_name="Fisher",
        last_name_obfuscated="Z.",
        title="Building GTM AI",
        company="The Hog",
    )
    people.bind_session(SID, person["person_id"])
    source = people.append_event(
        person["person_id"],
        source="web",
        kind="search",
        summary="Filed one durable source",
    )
    return store, people, person, source


def _run(*, store, people, provider, failovers=(), repository=None, owner=None):
    return asyncio.run(
        run_turn(
            text="Reply with READY and do not call tools.",
            sid=SID,
            store=store,
            provider=provider,
            persona=None,
            skills=None,
            inbox=Inbox(store),
            openai_tools=[
                {"type": "function", "function": {"name": "gmail_send"}}
            ],
            execute_kwargs={"people": people},
            failover_providers=failovers,
            retry_policy=RetryPolicy(max_attempts_per_provider=1),
            agent_runs=repository,
            run_owner=owner,
        )
    )


def test_retained_person_chat_completes_before_and_after_restart_without_replay(
    tmp_path, monkeypatch
):
    store, people, person, source = _seed_retained_shape(tmp_path)
    executions = []
    monkeypatch.setattr(
        "coworker.turn.execute",
        lambda *args, **kwargs: executions.append((args, kwargs)),
    )

    first = _run(
        store=store,
        people=people,
        provider=FakeProvider(deltas=("READY",)),
    )
    restarted_store = ConversationStore(tmp_path)
    restarted_people = PersonStore(tmp_path)
    second_provider = FakeProvider(deltas=("READY after restart",))
    second = _run(
        store=restarted_store,
        people=restarted_people,
        provider=second_provider,
    )

    assert first == {"status": "ok", "text": "READY"}
    assert second == {"status": "ok", "text": "READY after restart"}
    assert executions == []
    assert restarted_people.person_for_session(SID) == person["person_id"]
    assert restarted_people.timeline(person["person_id"])[0]["event_id"] == source["event_id"]
    assert sum(
        message.get("tool_call_id") == "completed-send"
        for message in restarted_store.load(SID)
    ) == 1
    request = second_provider.calls[0]
    assert any(message.get("tool_call_id") == "completed-send" for message in request)


class IncompatibleProvider:
    uses_transient_context = True

    def __init__(self, provider_id="deepseek", model_id="deepseek-v4-pro"):
        self.provider_id = provider_id
        self.model_id = model_id
        self.calls = 0

    async def astream(self, *, messages, tools, context_id=None):
        self.calls += 1
        raise RuntimeError("PRIVATE retained-history incompatibility")
        yield


class HealthyFallback:
    provider_id = "openai"
    model_id = "gpt-4o-mini"

    def __init__(self):
        self.calls = []

    async def astream(self, *, messages, tools):
        self.calls.append(messages)
        yield StreamChunk(text_delta="READY from fallback")
        yield StreamChunk(finish_reason="stop")


def test_incompatible_primary_fails_over_without_replaying_completed_effects(
    tmp_path, monkeypatch
):
    store, people, person, source = _seed_retained_shape(tmp_path)
    executions = []
    monkeypatch.setattr(
        "coworker.turn.execute",
        lambda *args, **kwargs: executions.append((args, kwargs)),
    )
    primary = IncompatibleProvider()
    fallback = HealthyFallback()
    transcript_before = list(store.load(SID))
    timeline_before = list(people.timeline(person["person_id"]))

    result = _run(
        store=store,
        people=people,
        provider=primary,
        failovers=(fallback,),
    )

    assert result == {"status": "ok", "text": "READY from fallback"}
    assert primary.calls == 1
    assert len(fallback.calls) == 1
    assert executions == []
    assert store.load(SID)[: len(transcript_before)] == transcript_before
    assert people.timeline(person["person_id"]) == timeline_before
    assert people.person_for_session(SID) == person["person_id"]
    assert timeline_before[0]["event_id"] == source["event_id"]
    recoveries = [
        event for event in store.load_events(SID) if event.get("type") == "provider_recovery"
    ]
    assert [(event["action"], event["provider"]) for event in recoveries] == [
        ("failover", "openai")
    ]


def test_exhausted_provider_recovery_records_one_classified_final_failure(tmp_path):
    store, people, _person, _source = _seed_retained_shape(tmp_path)
    repository = AgentRunRepository(tmp_path / "runs")
    owner = repository.registry.register()
    primary = IncompatibleProvider()
    fallback = IncompatibleProvider("openai", "gpt-4o-mini")
    fallback.uses_transient_context = False

    result = _run(
        store=store,
        people=people,
        provider=primary,
        failovers=(fallback,),
        repository=repository,
        owner=owner,
    )

    assert result["status"] == "error"
    error = [event for event in store.load_events(SID) if event.get("type") == "error"][-1]
    assert error["message"] == "The model provider failed after bounded recovery attempts."
    assert error["failure"] == {
        "code": "provider_runtime_error",
        "provider": "openai",
        "model": "gpt-4o-mini",
        "attempts": 1,
        "recovery_count": 1,
        "exhausted": True,
    }
    assert "PRIVATE" not in str(error)
    run = repository.list_runs(session_id=SID, limit=10)[0]
    checkpoint = next(
        item
        for item in repository.list_checkpoints(run["run_id"])
        if item["kind"] == "model_failed"
    )
    assert checkpoint["payload"]["error_class"] == "provider_runtime_error"
    assert checkpoint["payload"]["provider"] == "openai"
    assert checkpoint["payload"]["model_id"] == "gpt-4o-mini"
    assert "PRIVATE" not in str(checkpoint)
