"""Raw model reasoning in the thread (#225).

The model's reasoning is shown to the operator, labeled as model reasoning. It
has one home: the presentation event log, which is what the thread reloads
from. Every other record keeps its own closed shape and must never see it.

The planted-string tests are the guard. A test that only checks one store
would keep passing after reasoning leaked into another, so the planted text is
searched for everywhere the state directory writes.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime

from fastapi.testclient import TestClient

from coworker.compaction import (
    OPEN_TAG,
    CompactionPolicy,
    SessionCompactor,
    provider_summarizer,
)
from coworker.inbox import Inbox
from coworker.provider import FakeProvider, ModelUsage, StreamChunk, ToolCall
from coworker.server import TOKEN_HEADER, create_app
from coworker.store import ConversationStore
from coworker.telemetry.schema import record_to_dict
from coworker.tools import OPENAI_TOOLS
from coworker.turn import _ReasoningFeed, run_turn

TOKEN = "test-token-reasoning-events"
HEADERS = {TOKEN_HEADER: TOKEN}
PLANTED = "reasoning-canary-7f3a91-never-store-elsewhere"


def _turn_events(tmp_path, provider, *, text="find engineers", sid="sess-reasoning"):
    store = ConversationStore(tmp_path)
    store.create_session(sid)
    emitted: list[dict] = []

    async def emit(event):
        emitted.append(event)

    result = asyncio.run(
        run_turn(
            text=text,
            sid=sid,
            store=store,
            provider=provider,
            persona=None,
            skills=None,
            inbox=Inbox(store),
            openai_tools=OPENAI_TOOLS,
            execute_kwargs={},
            emit=emit,
        )
    )
    return result, emitted, store


def _reasoning(events):
    return [event for event in events if event["type"] == "reasoning_delta"]


# --- the event ---------------------------------------------------------------


def test_reasoning_arrives_before_the_answer_in_a_few_batched_events(tmp_path):
    words = tuple(f"word{index} " for index in range(60))
    provider = FakeProvider(
        steps=[{"reasoning_deltas": words, "deltas": ("Answer.",), "finish_reason": "stop"}]
    )

    result, events, store = _turn_events(tmp_path, provider)

    assert result["status"] == "ok"
    kinds = [event["type"] for event in events]
    assert kinds[0] == "turn_start"
    assert kinds.index("reasoning_delta") < kinds.index("assistant_delta")
    assert "".join(event["delta"] for event in _reasoning(events)) == "".join(words)
    # The first delta goes out alone, so the row can show at once. The rest
    # wait for a batch. Sixty tokens must not become sixty log lines.
    assert _reasoning(events)[0]["delta"] == words[0]
    assert len(_reasoning(events)) <= 3
    assert len({event["event_id"] for event in events}) == len(events)
    for event in _reasoning(events):
        assert event["step"] == 1
        assert datetime.fromisoformat(event["at"]).tzinfo is not None
    # The same events are what the transcript restores from.
    assert _reasoning(store.load_events("sess-reasoning")) == _reasoning(events)


def test_a_provider_without_reasoning_emits_no_reasoning_event(tmp_path):
    provider = FakeProvider(steps=[{"deltas": ("Plain answer.",), "finish_reason": "stop"}])

    result, events, store = _turn_events(tmp_path, provider)

    assert result["status"] == "ok"
    assert _reasoning(events) == []
    assert _reasoning(store.load_events("sess-reasoning")) == []


def test_each_model_step_is_its_own_block_in_order(tmp_path):
    provider = FakeProvider(
        steps=[
            {
                "reasoning_deltas": ("plan the lookup",),
                "tool_calls": [ToolCall(id="call_1", name="now", arguments={})],
            },
            {"reasoning_deltas": ("read the result",), "deltas": ("Done.",)},
        ]
    )

    result, events, _store = _turn_events(tmp_path, provider)

    assert result["status"] == "ok"
    blocks = [(event["step"], event["delta"]) for event in _reasoning(events)]
    assert blocks == [(1, "plan the lookup"), (2, "read the result")]
    kinds = [event["type"] for event in events]
    assert kinds.index("reasoning_delta") < kinds.index("tool_started")
    assert kinds.index("tool_finished") < len(kinds) - 1 - kinds[::-1].index(
        "reasoning_delta"
    )


def test_reasoning_streamed_before_a_provider_failure_still_reaches_the_thread(tmp_path):
    class FailsMidThought:
        provider_id = "fake"
        model_id = "fake"

        async def astream(self, *, messages, tools=None, context_id=None):
            yield StreamChunk.started(provider="fake", model="fake")
            yield StreamChunk(transient_reasoning_delta="first thought ")
            yield StreamChunk(transient_reasoning_delta="and a second")
            raise RuntimeError("boom")

    _result, events, _store = _turn_events(tmp_path, FailsMidThought())

    assert "".join(event["delta"] for event in _reasoning(events)) == (
        "first thought and a second"
    )
    kinds = [event["type"] for event in events]
    assert kinds.index("reasoning_delta") < kinds.index("error")


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_the_feed_sends_the_first_delta_at_once_then_batches_by_age():
    sent: list[dict] = []
    clock = _Clock()

    async def send(event):
        sent.append(event)

    async def run():
        feed = _ReasoningFeed(send, clock=clock)
        await feed.add("a")
        clock.now = 0.05
        await feed.add("b")
        clock.now = 0.10
        await feed.add("c")
        assert [event["delta"] for event in sent] == ["a"]
        clock.now = 0.20
        await feed.add("d")
        assert [event["delta"] for event in sent] == ["a", "bcd"]
        await feed.add("e")
        await feed.end_block()
        # A later block starts a new step and sends its first delta at once.
        await feed.add("f")
        await feed.end_block()
        await feed.end_block()

    asyncio.run(run())

    assert [(event["step"], event["delta"]) for event in sent] == [
        (1, "a"),
        (1, "bcd"),
        (1, "e"),
        (2, "f"),
    ]


# --- raw reasoning lives in the transcript event log and nowhere else --------


def _holders(root):
    """Every file under `root` that contains the planted reasoning."""
    return {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and PLANTED.encode() in path.read_bytes()
    }


def test_planted_reasoning_is_in_the_restored_transcript_and_no_other_record(tmp_path):
    usage = ModelUsage(
        input_tokens=40,
        output_tokens=12,
        total_tokens=52,
        cached_input_tokens=0,
        uncached_input_tokens=40,
        reasoning_tokens=9,
    )
    provider = FakeProvider(
        steps=[
            {
                "reasoning_deltas": ("I should check the time. ", PLANTED),
                "tool_calls": [ToolCall(id="call_1", name="now", arguments={})],
                "finish_reason": "tool_calls",
                "usage": usage,
            },
            {
                "reasoning_deltas": ("The tool answered. ", PLANTED),
                "deltas": ("It is Tuesday.",),
                "finish_reason": "stop",
                "usage": usage,
            },
        ]
    )
    client = TestClient(create_app(token=TOKEN, provider=provider, state=tmp_path))
    sid = client.app.state.store.open_session_id()

    with client.websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        ws.send_json({"type": "chat", "text": "what time is it", "session_id": sid})
        live = []
        while True:
            event = ws.receive_json()
            live.append(event)
            if event["type"] in ("turn_end", "error"):
                break
    assert live[-1]["type"] == "turn_end"
    assert any(PLANTED in event["delta"] for event in _reasoning(live))
    run_id = live[0]["run_id"]

    # Present: the restored transcript carries the reasoning, in order.
    restored = client.get(f"/v1/sessions/{sid}", headers=HEADERS).json()
    restored_text = "".join(event["delta"] for event in _reasoning(restored["events"]))
    assert restored_text.count(PLANTED) == 2
    assert restored_text.index("check the time") < restored_text.index("tool answered")

    # Absent: the model's own history, which compaction reads.
    assert PLANTED not in json.dumps(restored["messages"])

    # Absent: the run ledger, list and receipt.
    listing = client.get("/v1/agent-runs", headers=HEADERS)
    receipt = client.get(f"/v1/agent-runs/{run_id}", headers=HEADERS)
    assert receipt.status_code == 200, "the run never reached the ledger"
    assert PLANTED not in listing.text
    assert PLANTED not in receipt.text

    # Absent: telemetry records and the public metrics the thread header reads.
    adapter = client.app.state.telemetry_adapter
    assert adapter.records, "telemetry recorded nothing"
    assert PLANTED not in json.dumps([record_to_dict(r) for r in adapter.records])
    metrics = client.get(f"/v1/sessions/{sid}/telemetry/current", headers=HEADERS)
    assert metrics.json()["current_run"]["reasoning_tokens"] == 18
    assert PLANTED not in metrics.text

    # Absent from every file the state directory holds, except the event log.
    assert _holders(tmp_path) == {f"events/{sid}.jsonl"}


def test_a_compaction_summary_never_carries_reasoning(tmp_path):
    sid = "sess-reasoning-compaction"
    store = ConversationStore(tmp_path)
    store.create_session(sid)
    first = FakeProvider(
        steps=[
            {
                "reasoning_deltas": (PLANTED,),
                "deltas": ("I found three engineers at Nimbus.",),
                "finish_reason": "stop",
            }
        ]
    )

    def turn(provider, text, **kwargs):
        return asyncio.run(
            run_turn(
                text=text,
                sid=sid,
                store=store,
                provider=provider,
                persona=None,
                skills=None,
                inbox=Inbox(store),
                openai_tools=OPENAI_TOOLS,
                execute_kwargs={},
                **kwargs,
            )
        )

    turn(first, "find Nimbus engineers")
    for index in range(12):
        store.append(sid, {"role": "user", "content": f"director turn {index} " + "x" * 200})
        store.append(sid, {"role": "assistant", "content": f"answer {index} " + "y" * 200})

    # The summarizer is itself a reasoning model, so it streams reasoning too.
    summarizer_requests: list[list[dict]] = []
    summarizing = provider_summarizer(
        FakeProvider(
            steps=[
                {
                    "reasoning_deltas": (PLANTED,),
                    "deltas": ("Earlier: three Nimbus engineers were found.",),
                    "finish_reason": "stop",
                }
            ]
        )
    )

    async def summarize(request):
        summarizer_requests.append(list(request))
        return await summarizing(request)

    compactor = SessionCompactor(
        store=store,
        session_id=sid,
        policy=CompactionPolicy(threshold_pct=0.0001, keep_fraction=0.02),
        summarize=summarize,
    )
    second = FakeProvider(steps=[{"deltas": ("ok",), "finish_reason": "stop"}])
    turn(second, "what next", compactor=compactor)

    view = json.dumps(second.calls[-1])
    assert OPEN_TAG in view, "compaction never ran"
    assert summarizer_requests, "the summarizer never ran"
    assert compactor.state is not None
    assert compactor.state.summary_text.startswith("Earlier:")
    assert PLANTED not in json.dumps(summarizer_requests)
    assert PLANTED not in compactor.state.summary_text
    assert PLANTED not in json.dumps(compactor.state.as_dict())
    assert PLANTED not in view
    assert _holders(tmp_path) == {f"events/{sid}.jsonl"}


def test_the_diagnostic_log_carries_reasoning_as_a_length_only():
    from coworker import diagnostic_bundle

    logs = diagnostic_bundle._logs(
        [
            {
                "version": 2,
                "type": "reasoning_delta",
                "session_id": "main",
                "run_id": "run_b1",
                "event_id": "event_b1",
                "message_id": "message_b1",
                "part_id": "part_b1",
                "delta": PLANTED,
                "step": 1,
                "at": "2026-10-26T12:00:00+00:00",
            }
        ]
    )

    assert PLANTED not in json.dumps(logs)
    assert logs["records"][0]["delta_length"] == len(PLANTED)
