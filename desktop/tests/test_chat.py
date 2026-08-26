import asyncio
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from coworker.gmail import FakeGmail
from coworker.events import build_event, TurnIdentity
from coworker.provider import FakeProvider, StreamChunk, ToolCall
from coworker.server import TOKEN_HEADER, _close_open_tool_calls, create_app
from coworker.turn import _tool_failure

TOKEN = "test-token-slice-6"


@pytest.mark.parametrize(
    ("detail", "expected_class"),
    [
        ("Gmail is not connected.", "connector_auth"),
        ("request timed out", "timeout_network"),
        ("denied by user", "permission"),
        ("query is required", "validation"),
        ("opaque provider explosion", "unknown"),
    ],
)
def test_tool_failure_classifies_actionable_failure_classes(
    detail, expected_class
):
    failure = _tool_failure(
        ToolCall(id="call-classify", name="gmail_search", arguments={}),
        {"error": detail},
        TurnIdentity(
            session_id="thread-alpha",
            run_id="run-classify",
            message_id="message-classify",
            part_id="part-classify",
        ),
    )

    assert failure["class"] == expected_class
    assert failure["retry_safe"] is True
    assert failure["idempotent"] is True


def app(tmp_path, provider: FakeProvider | None = None):
    return create_app(token=TOKEN, provider=provider or FakeProvider(), state=tmp_path)


def test_health_reports_slice_and_model(tmp_path):
    fake = FakeProvider()
    res = TestClient(app(tmp_path, fake)).get("/v1/health", headers={TOKEN_HEADER: TOKEN})
    assert res.status_code == 200
    body = res.json()
    assert body["slice"] == 29
    assert body["model"] == "fake"
    assert body["piece"] == "sidecar"


def test_ws_rejects_missing_token(tmp_path):
    client = TestClient(app(tmp_path))
    try:
        with client.websocket_connect("/ws/chat"):
            assert False, "expected reject"
    except Exception:
        pass


def test_ws_streams_deltas_then_turn_end(tmp_path):
    fake = FakeProvider(deltas=("Hello ", "world"))
    client = TestClient(app(tmp_path, fake))
    with client.websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        ws.send_json({"type": "chat", "text": "hi"})
        events = []
        while True:
            ev = ws.receive_json()
            events.append(ev)
            if ev["type"] in ("turn_end", "error"):
                break
    types = [e["type"] for e in events]
    assert types[0] == "turn_start"
    assert types[-1] == "turn_end"
    text = "".join(e.get("delta", "") for e in events if e["type"] == "assistant_delta")
    assert text == "Hello world"
    assert fake.calls
    roles = [m["role"] for m in fake.calls[0]]
    assert roles[0] == "system"
    assert roles[-1] == "user"
    assert fake.calls[0][-1]["content"] == "hi"
    assert "now" in fake.calls[0][0]["content"]
    assert "Sourcecado's sourcing agent" in fake.calls[0][0]["content"]
    presentation_fields = {
        "version",
        "session_id",
        "run_id",
        "event_id",
        "message_id",
        "part_id",
        "state",
    }
    assert all(presentation_fields.isdisjoint(message) for message in fake.calls[0])


def test_ws_text_turn_uses_one_stable_v2_identity_through_completion(tmp_path):
    client = TestClient(app(tmp_path, FakeProvider(deltas=("Hello ", "world"))))
    sid = client.app.state.store.open_session_id()

    with client.websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        ws.send_json({"type": "chat", "text": "hi", "session_id": sid})
        events = _drain(ws)

    assert [event["type"] for event in events] == [
        "turn_start",
        "assistant_delta",
        "assistant_delta",
        "turn_end",
    ]
    assert {event["version"] for event in events} == {2}
    assert {event["session_id"] for event in events} == {sid}
    assert len({event["run_id"] for event in events}) == 1
    assert len({event["event_id"] for event in events}) == len(events)
    assert len({event["message_id"] for event in events}) == 1
    assert len({event["part_id"] for event in events}) == 1
    assert events[0]["state"] == "running"
    assert events[-1]["state"] == "complete"


def test_ws_cancel_is_received_during_stream_and_persists_one_terminal_ack(tmp_path):
    class SlowProvider:
        model_id = "slow"

        async def astream(self, *, messages, tools):
            yield StreamChunk(text_delta="first")
            await asyncio.sleep(0.05)
            yield StreamChunk(text_delta="late")

    built = create_app(token=TOKEN, provider=SlowProvider(), state=tmp_path)
    client = TestClient(built)
    sid = built.state.store.open_session_id()

    with client.websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        ws.send_json({"type": "chat", "text": "start", "session_id": sid})
        started = ws.receive_json()
        first = ws.receive_json()
        ws.send_json(
            {
                "type": "cancel",
                "session_id": sid,
                "run_id": started["run_id"],
            }
        )
        events = [started, first]
        while events[-1]["type"] not in {"turn_stopped", "turn_end", "error"}:
            events.append(ws.receive_json())

    assert [event["type"] for event in events] == [
        "turn_start",
        "assistant_delta",
        "turn_stopping",
        "turn_stopped",
    ]
    assert events[-2]["state"] == "stopping"
    assert events[-1]["state"] == "stopped"
    assert "late" not in "".join(event.get("delta", "") for event in events)
    assert len([event for event in events if event["type"] == "turn_stopped"]) == 1
    assert built.state.store.load_events(sid) == events


def test_ws_cancel_waits_for_a_synchronous_tool_before_terminal_ack(
    tmp_path, monkeypatch
):
    fake = FakeProvider(
        steps=[
            {"tool_calls": [ToolCall(id="call_slow", name="now", arguments={})]},
            {"deltas": ("must not start",)},
        ]
    )

    def slow_execute(name, arguments, **kwargs):
        time.sleep(0.05)
        return True, {"finished": True}

    monkeypatch.setattr("coworker.turn.execute", slow_execute)
    built = create_app(token=TOKEN, provider=fake, state=tmp_path)
    client = TestClient(built)
    sid = built.state.store.open_session_id()

    with client.websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        ws.send_json({"type": "chat", "text": "run it", "session_id": sid})
        events = _until(ws, "tool_started")
        ws.send_json(
            {
                "type": "cancel",
                "session_id": sid,
                "run_id": events[0]["run_id"],
            }
        )
        while events[-1]["type"] not in {"turn_stopped", "turn_end", "error"}:
            events.append(ws.receive_json())

    types = [event["type"] for event in events]
    assert types.index("turn_stopping") < types.index("tool_finished")
    assert types.index("tool_finished") < types.index("turn_stopped")
    assert "after the current action" in next(
        event["message"] for event in events if event["type"] == "turn_stopping"
    ).lower()
    assert "assistant_delta" not in types


def test_ws_cancel_closes_a_waiting_approval_without_denial(tmp_path):
    gmail = FakeGmail()
    fake = FakeProvider(
        steps=[
            {
                "tool_calls": [
                    ToolCall(
                        id="call_cancelled_draft",
                        name="gmail_draft",
                        arguments={
                            "to": "a@b.com",
                            "subject": "hi",
                            "body": "hello",
                        },
                    )
                ]
            },
            {"deltas": ("must not continue",)},
        ]
    )
    built = create_app(
        token=TOKEN, provider=fake, state=tmp_path, gmail=gmail
    )
    client = TestClient(built)
    sid = built.state.store.open_session_id()

    with client.websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        ws.send_json({"type": "chat", "text": "draft it", "session_id": sid})
        events = _until(ws, "permission_required")
        ws.send_json(
            {
                "type": "cancel",
                "session_id": sid,
                "run_id": events[0]["run_id"],
            }
        )
        while events[-1]["type"] not in {"turn_stopped", "turn_end", "error"}:
            events.append(ws.receive_json())

    item = built.state.inbox.get("call_cancelled_draft")
    assert item is not None
    assert item["state"] == "cancelled"
    assert item["decision"] is None
    assert gmail.drafts == []
    assert "tool_started" not in [event["type"] for event in events]
    assert events[-1]["type"] == "turn_stopped"


def test_ws_duplicate_cancel_is_idempotent(tmp_path):
    class SlowProvider:
        model_id = "slow"

        async def astream(self, *, messages, tools):
            yield StreamChunk(text_delta="first")
            await asyncio.sleep(0.05)
            yield StreamChunk(text_delta="late")

    built = create_app(token=TOKEN, provider=SlowProvider(), state=tmp_path)
    client = TestClient(built)
    sid = built.state.store.open_session_id()

    with client.websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        ws.send_json({"type": "chat", "text": "start", "session_id": sid})
        events = [ws.receive_json(), ws.receive_json()]
        command = {
            "type": "cancel",
            "session_id": sid,
            "run_id": events[0]["run_id"],
        }
        ws.send_json(command)
        ws.send_json(command)
        while events[-1]["type"] not in {"turn_stopped", "turn_end", "error"}:
            events.append(ws.receive_json())

    persisted = built.state.store.load_events(sid)
    assert [event["type"] for event in persisted].count("turn_stopping") == 1
    assert [event["type"] for event in persisted].count("turn_stopped") == 1


def test_ws_cancel_for_another_thread_cannot_stop_the_active_run(tmp_path):
    class SlowProvider:
        model_id = "slow"

        async def astream(self, *, messages, tools):
            yield StreamChunk(text_delta="first")
            await asyncio.sleep(0.03)
            yield StreamChunk(text_delta="still running")

    built = create_app(token=TOKEN, provider=SlowProvider(), state=tmp_path)
    client = TestClient(built)
    sid = built.state.store.open_session_id()
    built.state.store.create_session("thread-beta")

    with client.websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        ws.send_json({"type": "chat", "text": "start", "session_id": sid})
        events = [ws.receive_json(), ws.receive_json()]
        ws.send_json(
            {
                "type": "cancel",
                "session_id": "thread-beta",
                "run_id": events[0]["run_id"],
            }
        )
        while events[-1]["type"] not in {"turn_stopped", "turn_end", "error"}:
            events.append(ws.receive_json())

    assert events[-1]["type"] == "turn_end"
    assert events[-1]["state"] == "complete"
    assert "still running" in "".join(
        event.get("delta", "") for event in events
    )
    assert "turn_stopping" not in [event["type"] for event in events]


def test_ws_queue_add_during_run_persists_and_acknowledges_without_steering(tmp_path):
    class SlowProvider:
        model_id = "slow"

        def __init__(self):
            self.calls = []

        async def astream(self, *, messages, tools):
            self.calls.append(list(messages))
            yield StreamChunk(text_delta="active")
            await asyncio.sleep(0.05)
            yield StreamChunk(text_delta=" turn")

    provider = SlowProvider()
    built = create_app(token=TOKEN, provider=provider, state=tmp_path)
    client = TestClient(built)
    sid = built.state.store.open_session_id()

    with client.websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        ws.send_json({"type": "chat", "text": "current", "session_id": sid})
        events = [ws.receive_json(), ws.receive_json()]
        ws.send_json(
            {
                "type": "queue_add",
                "session_id": sid,
                "command_id": "queue-command-1",
                "item_id": "queue-item-1",
                "text": "queued follow-up",
            }
        )
        while not any(event["type"] == "turn_end" for event in events):
            events.append(ws.receive_json())

    snapshot = next(event for event in events if event["type"] == "queue_snapshot")
    assert snapshot["status"] == "accepted"
    assert snapshot["items"][0]["id"] == "queue-item-1"
    assert snapshot["items"][0]["state"] == "waiting"
    assert all(
        message.get("content") != "queued follow-up"
        for message in provider.calls[0]
    )


def test_ws_normal_terminal_drains_exactly_one_next_queue_item(tmp_path):
    class TwoTurnProvider:
        model_id = "two-turn"

        def __init__(self):
            self.calls = []

        async def astream(self, *, messages, tools):
            self.calls.append(list(messages))
            if len(self.calls) == 1:
                yield StreamChunk(text_delta="current")
                await asyncio.sleep(0.04)
                yield StreamChunk(text_delta=" done")
            else:
                yield StreamChunk(text_delta="queued done")

    provider = TwoTurnProvider()
    built = create_app(token=TOKEN, provider=provider, state=tmp_path)
    client = TestClient(built)
    sid = built.state.store.open_session_id()

    with client.websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        ws.send_json({"type": "chat", "text": "current", "session_id": sid})
        events = [ws.receive_json(), ws.receive_json()]
        ws.send_json(
            {
                "type": "queue_add",
                "session_id": sid,
                "command_id": "drain-add",
                "item_id": "drain-item",
                "text": "queued follow-up",
            }
        )
        while not any(
            event["type"] in {"turn_end", "turn_stopped", "error"}
            for event in events
        ):
            event = ws.receive_json()
            events.append(event)
        time.sleep(0.08)
        assert len(provider.calls) == 2
        while sum(
            event["type"] in {"turn_end", "turn_stopped", "error"}
            for event in events
        ) < 2:
            events.append(ws.receive_json())

    assert provider.calls[1][-1] == {"role": "user", "content": "queued follow-up"}
    assert built.state.store.list_queue(sid) == []
    assert [event["type"] for event in events].count("turn_start") == 2
    assert [event["type"] for event in events].count("turn_end") == 2


def test_ws_cancel_pauses_queue_until_explicit_resume(tmp_path):
    class ResumableProvider:
        model_id = "resumable"

        def __init__(self):
            self.calls = []

        async def astream(self, *, messages, tools):
            self.calls.append(list(messages))
            if len(self.calls) == 1:
                yield StreamChunk(text_delta="active")
                await asyncio.sleep(0.08)
                yield StreamChunk(text_delta="late")
            else:
                yield StreamChunk(text_delta="resumed queued item")

    provider = ResumableProvider()
    built = create_app(token=TOKEN, provider=provider, state=tmp_path)
    client = TestClient(built)
    sid = built.state.store.open_session_id()

    with client.websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        ws.send_json({"type": "chat", "text": "current", "session_id": sid})
        events = [ws.receive_json(), ws.receive_json()]
        ws.send_json(
            {
                "type": "queue_add",
                "session_id": sid,
                "command_id": "paused-add",
                "item_id": "paused-item",
                "text": "run only after resume",
            }
        )
        ws.send_json(
            {
                "type": "cancel",
                "session_id": sid,
                "run_id": events[0]["run_id"],
            }
        )
        while not any(event["type"] == "turn_stopped" for event in events):
            events.append(ws.receive_json())

        time.sleep(0.06)
        assert len(provider.calls) == 1
        assert built.state.store.queue_paused(sid) is True
        assert built.state.store.list_queue(sid)[0]["state"] == "waiting"

        ws.send_json(
            {
                "type": "queue_resume",
                "session_id": sid,
                "command_id": "resume-paused-queue",
            }
        )
        while len(provider.calls) < 2:
            events.append(ws.receive_json())
        while sum(event["type"] == "turn_end" for event in events) < 1:
            events.append(ws.receive_json())

    assert provider.calls[1][-1] == {
        "role": "user",
        "content": "run only after resume",
    }
    assert built.state.store.queue_paused(sid) is False
    assert built.state.store.list_queue(sid) == []


def test_ws_error_when_no_provider(tmp_path):
    client = TestClient(create_app(token=TOKEN, provider=None, state=tmp_path))
    with client.websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        ws.send_json({"type": "chat", "text": "hi"})
        events = _drain(ws)
    assert [event["type"] for event in events] == ["turn_start", "error"]
    assert events[-1]["state"] == "failed"
    assert events[-1]["message_id"] == events[0]["message_id"]
    assert "key" in events[-1]["message"].lower() or "model" in events[-1]["message"].lower()


def test_ws_persists_a_failed_terminal_event_with_the_turn_identity(tmp_path):
    class FailingProvider:
        model_id = "failing"

        async def astream(self, *, messages, tools):
            if False:
                yield
            raise RuntimeError("provider stream failed")

    built = create_app(token=TOKEN, provider=FailingProvider(), state=tmp_path)
    client = TestClient(built)
    sid = built.state.store.open_session_id()

    with client.websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        ws.send_json({"type": "chat", "text": "hi", "session_id": sid})
        events = _drain(ws)

    failed = events[-1]
    assert failed["type"] == "error"
    assert failed["state"] == "failed"
    assert failed["message"] == "provider stream failed"
    assert failed["run_id"] == events[0]["run_id"]
    assert failed["message_id"] == events[0]["message_id"]
    assert failed["part_id"] == events[0]["part_id"]
    assert built.state.store.load_events(sid) == events


def test_ws_persists_a_stopped_terminal_event_at_the_step_limit(tmp_path):
    fake = FakeProvider(
        steps=[
            {
                "tool_calls": [
                    ToolCall(id=f"call_{index}", name="now", arguments={})
                ]
            }
            for index in range(8)
        ]
    )
    built = create_app(token=TOKEN, provider=fake, state=tmp_path)
    client = TestClient(built)
    sid = built.state.store.open_session_id()

    with client.websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        ws.send_json({"type": "chat", "text": "keep checking", "session_id": sid})
        events = _drain(ws)

    stopped = events[-1]
    assert stopped["type"] == "turn_end"
    assert stopped["state"] == "stopped"
    assert "8 tool steps" in stopped["message"]
    assert stopped["run_id"] == events[0]["run_id"]
    assert built.state.store.load_events(sid) == events


def _drain(ws):
    events = []
    while True:
        ev = ws.receive_json()
        events.append(ev)
        if ev["type"] in ("turn_end", "error"):
            return events


def test_ws_runs_now_tool_then_answers(tmp_path):
    fake = FakeProvider(
        steps=[
            {"tool_calls": [ToolCall(id="call_1", name="now", arguments={})]},
            {"deltas": ("It is Tuesday in Los Angeles.",)},
        ]
    )
    client = TestClient(app(tmp_path, fake))
    with client.websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        ws.send_json({"type": "chat", "text": "what time is it?"})
        events = _drain(ws)
    types = [e["type"] for e in events]
    assert types[0] == "turn_start"
    assert "tool_started" in types
    assert "tool_finished" in types
    assert types[-1] == "turn_end"
    started = next(e for e in events if e["type"] == "tool_started")
    finished = next(e for e in events if e["type"] == "tool_finished")
    assert started["name"] == "now"
    assert finished["ok"] is True
    assert finished["result"]["tz"] == "America/Los_Angeles"
    text = "".join(e.get("delta", "") for e in events if e["type"] == "assistant_delta")
    assert "Tuesday" in text or "Los Angeles" in text
    assert len(fake.calls) == 2
    assert fake.calls[1][-1]["role"] == "tool"
    assert "permission_required" not in types


def test_failed_tool_emits_structured_recovery_metadata_and_partial_terminal(
    tmp_path, monkeypatch
):
    fake = FakeProvider(
        steps=[
            {
                "tool_calls": [
                    ToolCall(id="call-success", name="now", arguments={}),
                    ToolCall(
                        id="call-drive-failed",
                        name="drive_search",
                        arguments={"query": "Codeology"},
                    ),
                ]
            },
            {"deltas": ("I kept the successful result.",)},
        ]
    )

    def fake_execute(name, arguments, **kwargs):
        if name == "now":
            return True, {"iso": "2026-08-25T12:00:00-07:00"}
        return False, {"error": "Drive is not connected. raw-provider-detail"}

    monkeypatch.setattr("coworker.turn.execute", fake_execute)
    built = create_app(token=TOKEN, provider=fake, state=tmp_path)
    sid = built.state.store.open_session_id()
    with TestClient(built).websocket_connect(
        "/ws/chat", subprotocols=["club", TOKEN]
    ) as ws:
        ws.send_json({"type": "chat", "text": "check sources", "session_id": sid})
        events = _drain(ws)

    success = next(
        event
        for event in events
        if event["type"] == "tool_finished" and event["id"] == "call-success"
    )
    failed = next(
        event
        for event in events
        if event["type"] == "tool_finished" and event["id"] == "call-drive-failed"
    )
    assert success["ok"] is True
    assert failed["failure"] == {
        "class": "connector_auth",
        "connector_id": "drive",
        "source": "Google Drive",
        "retry_safe": True,
        "idempotent": True,
        "summary": "Google Drive needs to be repaired before this source can be checked.",
        "repair_route": "#/connections/drive",
        "detail": "Drive is not connected. raw-provider-detail",
        "call_id": "call-drive-failed",
        "run_id": failed["run_id"],
        "session_id": sid,
        "state": "failed",
    }
    assert events[-1]["type"] == "turn_end"
    assert events[-1]["state"] == "partial"


def test_safe_failed_step_retry_is_addressed_idempotent_and_reruns_only_failure(
    tmp_path, monkeypatch
):
    fake = FakeProvider(
        steps=[
            {
                "tool_calls": [
                    ToolCall(id="call-success", name="now", arguments={}),
                    ToolCall(
                        id="call-retry-drive",
                        name="drive_search",
                        arguments={"query": "Codeology"},
                    ),
                ]
            },
            {"deltas": ("Partial answer.",)},
        ]
    )
    calls: list[str] = []

    def flaky_execute(name, arguments, **kwargs):
        calls.append(name)
        if name == "now":
            return True, {"iso": "2026-08-25T12:00:00-07:00"}
        if calls.count("drive_search") == 1:
            return False, {"error": "Drive timed out"}
        return True, {"files": [{"id": "file-1"}]}

    monkeypatch.setattr("coworker.turn.execute", flaky_execute)
    built = create_app(token=TOKEN, provider=fake, state=tmp_path)
    sid = built.state.store.open_session_id()
    with TestClient(built).websocket_connect(
        "/ws/chat", subprotocols=["club", TOKEN]
    ) as ws:
        ws.send_json({"type": "chat", "text": "check sources", "session_id": sid})
        events = _drain(ws)
        run_id = events[0]["run_id"]
        command = {
            "type": "retry_failed_step",
            "session_id": sid,
            "run_id": run_id,
            "call_id": "call-retry-drive",
            "command_id": "retry-command-1",
        }
        ws.send_json(command)
        ws.send_json(command)
        time.sleep(0.08)

    assert calls == ["now", "drive_search", "drive_search"]
    recovery = [
        event
        for event in built.state.store.load_events(sid)
        if event["type"] == "tool_recovery"
    ]
    assert len(recovery) == 1
    assert recovery[0]["action"] == "retry"
    assert recovery[0]["status"] == "succeeded"
    assert recovery[0]["command_id"] == "retry-command-1"
    assert recovery[0]["call_id"] == "call-retry-drive"


def test_unsafe_failed_step_retry_creates_fresh_approval_before_execution(
    tmp_path, monkeypatch
):
    fake = FakeProvider(
        steps=[
            {
                "tool_calls": [
                    ToolCall(
                        id="call-write-failed",
                        name="gmail_draft",
                        arguments={
                            "to": "alyssa@example.com",
                            "subject": "Hello",
                            "body": "Draft body",
                        },
                    )
                ]
            },
            {"deltas": ("The draft step failed.",)},
        ]
    )
    executions: list[str] = []

    def failing_execute(name, arguments, **kwargs):
        executions.append(name)
        return False, {"error": "Gmail network failure"}

    monkeypatch.setattr("coworker.turn.execute", failing_execute)
    built = create_app(token=TOKEN, provider=fake, state=tmp_path)
    sid = built.state.store.open_session_id()
    with TestClient(built).websocket_connect(
        "/ws/chat", subprotocols=["club", TOKEN]
    ) as ws:
        ws.send_json({"type": "chat", "text": "draft", "session_id": sid})
        events = _until(ws, "permission_required")
        ws.send_json(
            {"type": "permission", "id": "call-write-failed", "decision": "allow"}
        )
        events.extend(_drain(ws))
        ws.send_json(
            {
                "type": "retry_failed_step",
                "session_id": sid,
                "run_id": events[0]["run_id"],
                "call_id": "call-write-failed",
                "command_id": "unsafe-retry-1",
            }
        )
        time.sleep(0.06)
        assert executions == ["gmail_draft"]
        persisted = built.state.store.load_events(sid)
        recovery = next(
            event
            for event in persisted
            if event["type"] == "tool_recovery"
            and event["command_id"] == "unsafe-retry-1"
        )
        assert recovery["status"] == "approval_required"
        fresh = next(
            event
            for event in persisted
            if event["type"] == "permission_required"
            and event.get("recovery_command_id") == "unsafe-retry-1"
        )
        assert fresh["id"] != "call-write-failed"
        pending = built.state.inbox.get(fresh["id"])
        assert pending is not None
        assert pending["state"] == "pending"
        ws.send_json(
            {"type": "permission", "id": fresh["id"], "decision": "allow"}
        )
        time.sleep(0.06)

    assert executions == ["gmail_draft", "gmail_draft"]
    outcomes = [
        event
        for event in built.state.store.load_events(sid)
        if event["type"] == "tool_recovery"
        and event["command_id"] == "unsafe-retry-1"
    ]
    assert [event["status"] for event in outcomes] == [
        "approval_required",
        "failed",
    ]


def test_unsafe_retry_http_allow_closes_recovery_and_replays_terminal_result(
    tmp_path, monkeypatch
):
    fake = FakeProvider(
        steps=[
            {
                "tool_calls": [
                    ToolCall(
                        id="call-http-recovery",
                        name="gmail_draft",
                        arguments={
                            "to": "alyssa@example.com",
                            "subject": "Hello",
                            "body": "Draft body",
                        },
                    )
                ]
            },
            {"deltas": ("The draft step failed.",)},
        ]
    )
    executions: list[str] = []
    recovered_result = {
        "draft_id": "draft-recovered",
        "status": "not_sent",
    }

    def retry_execute(name, arguments, **kwargs):
        executions.append(name)
        if len(executions) == 1:
            return False, {"error": "Gmail network failure"}
        return True, recovered_result

    monkeypatch.setattr("coworker.turn.execute", retry_execute)
    monkeypatch.setattr("coworker.server.execute", retry_execute)
    built = create_app(token=TOKEN, provider=fake, state=tmp_path)
    client = TestClient(built)
    sid = built.state.store.open_session_id()

    with client.websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        ws.send_json({"type": "chat", "text": "draft", "session_id": sid})
        events = _until(ws, "permission_required")
        ws.send_json(
            {
                "type": "permission",
                "id": "call-http-recovery",
                "decision": "allow",
            }
        )
        events.extend(_drain(ws))
        ws.send_json(
            {
                "type": "retry_failed_step",
                "session_id": sid,
                "run_id": events[0]["run_id"],
                "call_id": "call-http-recovery",
                "command_id": "http-unsafe-retry-1",
            }
        )
        setup_events = _until(ws, "permission_required")
        while not any(
            event["type"] == "tool_recovery"
            and event.get("command_id") == "http-unsafe-retry-1"
            for event in setup_events
        ):
            setup_events.append(ws.receive_json())
        fresh = next(
            event
            for event in setup_events
            if event["type"] == "permission_required"
            and event.get("recovery_command_id") == "http-unsafe-retry-1"
        )

        live_events: list[dict] = []
        live_terminal = threading.Event()

        def read_live_terminal():
            try:
                while True:
                    event = ws.receive_json()
                    live_events.append(event)
                    if (
                        event["type"] == "tool_recovery"
                        and event.get("command_id") == "http-unsafe-retry-1"
                        and event.get("status") == "succeeded"
                    ):
                        live_terminal.set()
                        return
            except Exception:
                return

        reader = threading.Thread(target=read_live_terminal, daemon=True)
        reader.start()

        first = client.post(
            f"/v1/inbox/{fresh['id']}",
            headers={TOKEN_HEADER: TOKEN},
            json={"decision": "allow", "actor": "Fisher", "scope": "once"},
        )
        live_published = live_terminal.wait(timeout=0.5)
        if not live_published:
            ws.close()
        reader.join(timeout=1)
        replay = client.post(
            f"/v1/inbox/{fresh['id']}",
            headers={TOKEN_HEADER: TOKEN},
            json={"decision": "allow", "actor": "Fisher", "scope": "once"},
        )

    assert first.status_code == 200
    assert first.json()["result"] == recovered_result
    assert replay.status_code == 200
    assert replay.json()["idempotent"] is True
    assert replay.json()["result"] == recovered_result
    assert executions == ["gmail_draft", "gmail_draft"]
    assert live_published is True
    assert [event["type"] for event in live_events] == [
        "tool_finished",
        "approval_resolved",
        "tool_recovery",
    ]
    assert live_events[-1]["status"] == "succeeded"

    reloaded = client.get(
        f"/v1/sessions/{sid}", headers={TOKEN_HEADER: TOKEN}
    ).json()
    recovery_events = [
        event
        for event in reloaded["events"]
        if event["type"] == "tool_recovery"
        and event.get("command_id") == "http-unsafe-retry-1"
    ]
    replacement_results = [
        event
        for event in reloaded["events"]
        if event["type"] == "tool_finished"
        and event.get("id") == "call-http-recovery"
        and event.get("recovery_command_id") == "http-unsafe-retry-1"
    ]
    approval_receipts = [
        event
        for event in reloaded["events"]
        if event["type"] == "approval_resolved" and event.get("id") == fresh["id"]
    ]
    model_results = [
        message
        for message in reloaded["messages"]
        if message.get("role") == "tool"
        and message.get("tool_call_id") == "call-http-recovery"
    ]

    assert [event["status"] for event in recovery_events] == [
        "approval_required",
        "succeeded",
    ]
    assert len(replacement_results) == 1
    assert replacement_results[0]["result"] == recovered_result
    assert len(approval_receipts) == 1
    assert approval_receipts[0]["execution_status"] == "succeeded"
    assert len(model_results) == 1
    assert json.loads(model_results[0]["content"]) == recovered_result


def test_unsafe_retry_http_deny_closes_recovery_without_executing(
    tmp_path, monkeypatch
):
    fake = FakeProvider(
        steps=[
            {
                "tool_calls": [
                    ToolCall(
                        id="call-http-denied-recovery",
                        name="gmail_draft",
                        arguments={
                            "to": "alyssa@example.com",
                            "subject": "Hello",
                            "body": "Draft body",
                        },
                    )
                ]
            },
            {"deltas": ("The draft step failed.",)},
        ]
    )
    executions: list[str] = []
    failed_result = {"error": "Gmail network failure"}

    def failing_execute(name, arguments, **kwargs):
        executions.append(name)
        return False, failed_result

    monkeypatch.setattr("coworker.turn.execute", failing_execute)
    monkeypatch.setattr("coworker.server.execute", failing_execute)
    built = create_app(token=TOKEN, provider=fake, state=tmp_path)
    client = TestClient(built)
    sid = built.state.store.open_session_id()

    with client.websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        ws.send_json({"type": "chat", "text": "draft", "session_id": sid})
        events = _until(ws, "permission_required")
        ws.send_json(
            {
                "type": "permission",
                "id": "call-http-denied-recovery",
                "decision": "allow",
            }
        )
        events.extend(_drain(ws))
        ws.send_json(
            {
                "type": "retry_failed_step",
                "session_id": sid,
                "run_id": events[0]["run_id"],
                "call_id": "call-http-denied-recovery",
                "command_id": "http-unsafe-deny-1",
            }
        )
        setup_events = _until(ws, "permission_required")
        while not any(
            event["type"] == "tool_recovery"
            and event.get("command_id") == "http-unsafe-deny-1"
            for event in setup_events
        ):
            setup_events.append(ws.receive_json())
        fresh = next(
            event
            for event in setup_events
            if event["type"] == "permission_required"
            and event.get("recovery_command_id") == "http-unsafe-deny-1"
        )

        first = client.post(
            f"/v1/inbox/{fresh['id']}",
            headers={TOKEN_HEADER: TOKEN},
            json={"decision": "deny", "actor": "Fisher", "scope": "once"},
        )
        replay = client.post(
            f"/v1/inbox/{fresh['id']}",
            headers={TOKEN_HEADER: TOKEN},
            json={"decision": "deny", "actor": "Fisher", "scope": "once"},
        )

    assert first.status_code == 200
    assert first.json()["ok"] is False
    assert replay.status_code == 200
    assert replay.json()["idempotent"] is True
    assert executions == ["gmail_draft"]

    reloaded = client.get(
        f"/v1/sessions/{sid}", headers={TOKEN_HEADER: TOKEN}
    ).json()
    recovery_events = [
        event
        for event in reloaded["events"]
        if event["type"] == "tool_recovery"
        and event.get("command_id") == "http-unsafe-deny-1"
    ]
    replacement_results = [
        event
        for event in reloaded["events"]
        if event["type"] == "tool_finished"
        and event.get("recovery_command_id") == "http-unsafe-deny-1"
    ]
    approval_receipts = [
        event
        for event in reloaded["events"]
        if event["type"] == "approval_resolved" and event.get("id") == fresh["id"]
    ]
    model_results = [
        message
        for message in reloaded["messages"]
        if message.get("role") == "tool"
        and message.get("tool_call_id") == "call-http-denied-recovery"
    ]

    assert [event["status"] for event in recovery_events] == [
        "approval_required",
        "denied",
    ]
    assert replacement_results == []
    assert len(approval_receipts) == 1
    assert approval_receipts[0]["resolution"] == "denied"
    assert approval_receipts[0]["execution_status"] == "not_run"
    assert len(model_results) == 1
    assert json.loads(model_results[0]["content"]) == failed_result


def test_orphaned_recovery_approval_replay_projects_interrupted_without_execution(
    tmp_path, monkeypatch
):
    first_app = create_app(token=TOKEN, state=tmp_path)
    store = first_app.state.store
    sid = store.open_session_id()
    identity = TurnIdentity(
        session_id=sid,
        run_id="run-orphaned-recovery",
        message_id="message-orphaned-recovery",
        part_id="part-orphaned-recovery",
    )
    failure = {
        "class": "unknown",
        "connector_id": "gmail",
        "source": "Gmail",
        "retry_safe": False,
        "idempotent": False,
        "summary": "The Gmail draft outcome needs review.",
        "repair_route": None,
        "detail": "Gmail connection dropped.",
        "call_id": "call-orphaned-recovery",
        "run_id": identity.run_id,
        "session_id": sid,
        "state": "failed",
    }
    approval_id = "recovery-orphaned-approval"
    command_id = "recovery-orphaned-command"
    store.append_event(
        sid,
        build_event(
            identity,
            "permission_required",
            event_id="event-orphaned-permission",
            id=approval_id,
            name="gmail_draft",
            arguments={"to": "a@b.c", "subject": "s", "body": "b"},
            reason="Retrying can create another external change.",
            requested_at="2026-08-26T12:00:00Z",
            scope="once",
            recovery_command_id=command_id,
            original_call_id="call-orphaned-recovery",
            failure=failure,
        ),
    )
    store.append_event(
        sid,
        build_event(
            identity,
            "tool_recovery",
            event_id="event-orphaned-recovery-required",
            command_id=command_id,
            call_id="call-orphaned-recovery",
            name="gmail_draft",
            action="retry",
            status="approval_required",
            outcome="Unsafe retry requires a fresh approval.",
            failure=failure,
            approval_id=approval_id,
        ),
    )
    parked = first_app.state.inbox.park(
        "gmail_draft",
        {"to": "a@b.c", "subject": "s", "body": "b"},
        item_id=approval_id,
        session_id=sid,
        run_id=identity.run_id,
        message_id=identity.message_id,
        part_id=identity.part_id,
        kind="recovery_approval",
        recovery_command_id=command_id,
        original_call_id="call-orphaned-recovery",
    )
    first_app.state.inbox.decide_and_claim(
        parked["id"],
        "allow",
        actor="Fisher",
        scope="once",
        claimant="recovery-http:dead-process",
    )
    executions = []

    def must_not_execute(name, arguments, **kwargs):
        executions.append(name)
        return True, {"draft_id": "unsafe-replay"}

    monkeypatch.setattr("coworker.turn.execute", must_not_execute)
    monkeypatch.setattr("coworker.server.execute", must_not_execute)
    reopened = create_app(token=TOKEN, state=tmp_path)
    interrupted = reopened.state.inbox.get(approval_id)
    assert interrupted is not None
    assert interrupted["execution_status"] == "interrupted"

    response = TestClient(reopened).post(
        f"/v1/inbox/{approval_id}",
        headers={TOKEN_HEADER: TOKEN},
        json={"decision": "allow", "actor": "Fisher", "scope": "once"},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert response.json()["result"] == interrupted["execution_result"]
    assert executions == []
    events = reopened.state.store.load_events(sid)
    outcomes = [
        event
        for event in events
        if event["type"] == "tool_recovery"
        and event.get("command_id") == command_id
    ]
    replacement_results = [
        event
        for event in events
        if event["type"] == "tool_finished"
        and event.get("recovery_command_id") == command_id
    ]
    assert [event["status"] for event in outcomes] == [
        "approval_required",
        "interrupted",
    ]
    assert "outcome is unknown" in outcomes[-1]["outcome"].lower()
    assert replacement_results == []


def test_repair_and_continue_without_source_are_durable_idempotent_choices(
    tmp_path, monkeypatch
):
    fake = FakeProvider(
        steps=[
            {
                "tool_calls": [
                    ToolCall(
                        id="call-repair-drive",
                        name="drive_search",
                        arguments={"query": "Codeology"},
                    )
                ]
            },
            {"deltas": ("Available work remains partial.",)},
        ]
    )
    monkeypatch.setattr(
        "coworker.turn.execute",
        lambda name, arguments, **kwargs: (
            False,
            {"error": "Drive is not connected."},
        ),
    )
    built = create_app(token=TOKEN, provider=fake, state=tmp_path)
    sid = built.state.store.open_session_id()
    with TestClient(built).websocket_connect(
        "/ws/chat", subprotocols=["club", TOKEN]
    ) as ws:
        ws.send_json({"type": "chat", "text": "check drive", "session_id": sid})
        events = _drain(ws)
        base = {
            "session_id": sid,
            "run_id": events[0]["run_id"],
            "call_id": "call-repair-drive",
        }
        ws.send_json(
            {
                **base,
                "type": "repair_connection",
                "command_id": "repair-command-1",
            }
        )
        continue_command = {
            **base,
            "type": "continue_without_source",
            "command_id": "continue-command-1",
        }
        ws.send_json(continue_command)
        ws.send_json(continue_command)
        time.sleep(0.08)

    recoveries = [
        event
        for event in built.state.store.load_events(sid)
        if event["type"] == "tool_recovery"
    ]
    assert [(event["action"], event["status"]) for event in recoveries] == [
        ("repair", "awaiting_repair"),
        ("continue", "skipped"),
    ]
    assert recoveries[0]["repair_route"] == "#/connections/drive"
    assert recoveries[1]["outcome"] == (
        "Continued without Google Drive; available work remains partial."
    )
    assert built.state.store.load_events(sid)[-1]["type"] == "tool_recovery"


def test_tool_result_normalizes_stable_source_and_artifact_provenance(
    tmp_path, monkeypatch
):
    fake = FakeProvider(
        steps=[
            {
                "tool_calls": [
                    ToolCall(
                        id="call-provenance",
                        name="drive_search",
                        arguments={"query": "Codeology"},
                    )
                ]
            },
            {"deltas": ("Found it.",)},
        ]
    )
    monkeypatch.setattr(
        "coworker.turn.execute",
        lambda name, arguments, **kwargs: (
            True,
            {
                "sources": [
                    {
                        "id": "source-brief",
                        "title": "Codeology brief",
                        "url": "https://drive.google.com/file/brief",
                    }
                ],
                "artifacts": [
                    {
                        "id": "artifact-summary",
                        "type": "text",
                        "title": "Research summary",
                        "preview": "Two matching contacts",
                        "external_url": None,
                    }
                ],
            },
        ),
    )
    built = create_app(token=TOKEN, provider=fake, state=tmp_path)
    sid = built.state.store.open_session_id()
    with TestClient(built).websocket_connect(
        "/ws/chat", subprotocols=["club", TOKEN]
    ) as ws:
        ws.send_json({"type": "chat", "text": "find brief", "session_id": sid})
        events = _drain(ws)

    finished = next(
        event for event in events if event["type"] == "tool_finished"
    )
    assert finished["sources"] == [
        {
            "id": "source-brief",
            "title": "Codeology brief",
            "url": "https://drive.google.com/file/brief",
            "provider": "Google Drive",
            "stale": False,
            "truncated": False,
        }
    ]
    assert finished["artifacts"] == [
        {
            "id": "artifact-summary",
            "artifact_type": "text",
            "title": "Research summary",
            "preview": "Two matching contacts",
            "external_url": None,
            "stale": False,
            "truncated": False,
        }
    ]


def _until(ws, typ: str, extra=None):
    events = []
    while True:
        ev = ws.receive_json()
        events.append(ev)
        if extra:
            extra(ev)
        if ev["type"] == typ:
            return events
        if ev["type"] in ("turn_end", "error"):
            return events


def test_ws_gmail_draft_waits_then_runs_on_allow(tmp_path):
    gmail = FakeGmail()
    fake = FakeProvider(
        steps=[
            {
                "tool_calls": [
                    ToolCall(
                        id="call_draft",
                        name="gmail_draft",
                        arguments={"to": "a@b.com", "subject": "hi", "body": "hello"},
                    )
                ]
            },
            {"deltas": ("Drafted, not sent.",)},
        ]
    )
    built = create_app(token=TOKEN, provider=fake, state=tmp_path, gmail=gmail)
    client = TestClient(built)
    with client.websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        ws.send_json({"type": "chat", "text": "draft alyssa"})
        events = _until(ws, "permission_required")
        types = [e["type"] for e in events]
        assert "permission_required" in types
        assert "tool_started" not in types
        ask = next(e for e in events if e["type"] == "permission_required")
        assert ask["name"] == "gmail_draft"
        ws.send_json({"type": "permission", "id": "call_draft", "decision": "allow"})
        rest = _drain(ws)
    events.extend(rest)
    types = [e["type"] for e in events]
    assert "tool_started" in types
    assert types[-1] == "turn_end"
    assert len(gmail.drafts) == 1
    assert gmail.sends == []
    text = "".join(e.get("delta", "") for e in events if e["type"] == "assistant_delta")
    assert "Drafted" in text
    receipt = built.state.inbox.get("call_draft")
    assert receipt is not None
    assert receipt["actor"] == "operator"
    assert receipt["scope"] == "once"
    assert receipt["execution_status"] == "succeeded"
    resolved_event = next(
        event for event in events if event["type"] == "approval_resolved"
    )
    assert resolved_event["id"] == "call_draft"
    assert resolved_event["resolution"] == "allowed"
    assert resolved_event["decision"] == "allow"
    assert resolved_event["actor"] == "operator"
    assert resolved_event["scope"] == "once"
    assert resolved_event["execution_status"] == "succeeded"


def test_ws_gmail_draft_deny_does_not_draft(tmp_path):
    gmail = FakeGmail()
    fake = FakeProvider(
        steps=[
            {
                "tool_calls": [
                    ToolCall(
                        id="call_draft",
                        name="gmail_draft",
                        arguments={"to": "a@b.com", "subject": "hi", "body": "hello"},
                    )
                ]
            },
            {"deltas": ("Okay, I did not draft it.",)},
        ]
    )
    built = create_app(token=TOKEN, provider=fake, state=tmp_path, gmail=gmail)
    client = TestClient(built)
    with client.websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        ws.send_json({"type": "chat", "text": "draft alyssa"})
        events = _until(ws, "permission_required")
        ws.send_json({"type": "permission", "id": "call_draft", "decision": "deny"})
        rest = _drain(ws)
    events.extend(rest)
    types = [e["type"] for e in events]
    assert "tool_started" not in types
    finished = next(e for e in events if e["type"] == "tool_finished")
    assert finished["ok"] is False
    assert "denied" in finished["result"]["error"]
    assert gmail.drafts == []
    assert gmail.sends == []
    assert types[-1] == "turn_end"
    text = "".join(e.get("delta", "") for e in events if e["type"] == "assistant_delta")
    assert "not draft" in text


def test_conversation_empty_before_chat(tmp_path):
    built = app(tmp_path)
    res = TestClient(built).get("/v1/conversation", headers={TOKEN_HEADER: TOKEN})
    assert res.status_code == 200
    body = res.json()
    assert body["id"] == built.state.store.open_session_id()
    assert body["messages"] == []
    assert body["title"] is None


def test_ws_persists_messages_to_disk(tmp_path):
    fake = FakeProvider(deltas=("Hello ", "world"))
    client = TestClient(app(tmp_path, fake))
    with client.websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        ws.send_json({"type": "chat", "text": "hi"})
        _drain(ws)
    res = client.get("/v1/conversation", headers={TOKEN_HEADER: TOKEN})
    body = res.json()
    assert body["title"] == "hi"
    assert body["messages"][0] == {"role": "user", "content": "hi"}
    last = body["messages"][-1]
    assert last["role"] == "assistant"
    assert last["content"] == "Hello world"
    assert last["message_id"]  # identity stamp for restore merges
    open_id = client.app.state.store.open_session_id()
    jsonl = tmp_path / "conversations" / f"{open_id}.jsonl"
    assert jsonl.is_file()
    assert (tmp_path / "club.db").is_file()


def test_new_sidecar_reloads_history(tmp_path):
    first = FakeProvider(deltas=("Hello ", "world"))
    with TestClient(app(tmp_path, first)).websocket_connect(
        "/ws/chat", subprotocols=["club", TOKEN]
    ) as ws:
        ws.send_json({"type": "chat", "text": "hi"})
        _drain(ws)
    second = FakeProvider(deltas=("I remember hi.",))
    with TestClient(app(tmp_path, second)).websocket_connect(
        "/ws/chat", subprotocols=["club", TOKEN]
    ) as ws:
        ws.send_json({"type": "chat", "text": "remember?"})
        _drain(ws)
    contents = [m.get("content") for m in second.calls[0]]
    assert "hi" in contents
    assert "Hello world" in contents
    assert contents[-1] == "remember?"


def test_ws_remember_survives_new_sidecar(tmp_path):
    first = FakeProvider(
        steps=[
            {
                "tool_calls": [
                    ToolCall(
                        id="call_mem",
                        name="remember",
                        arguments={"content": "Fisher drinks oat lattes"},
                    )
                ]
            },
            {"deltas": ("Got it, I'll remember that.",)},
        ]
    )
    with TestClient(app(tmp_path, first)).websocket_connect(
        "/ws/chat", subprotocols=["club", TOKEN]
    ) as ws:
        ws.send_json({"type": "chat", "text": "remember I drink oat lattes"})
        events = _drain(ws)
    finished = next(e for e in events if e["type"] == "tool_finished")
    assert finished["ok"] is True
    assert finished["result"]["saved"] is True
    assert "permission_required" not in [e["type"] for e in events]

    second = FakeProvider(deltas=("Oat lattes, right.",))
    with TestClient(app(tmp_path, second)).websocket_connect(
        "/ws/chat", subprotocols=["club", TOKEN]
    ) as ws:
        ws.send_json({"type": "chat", "text": "what do I drink?"})
        _drain(ws)
    system = second.calls[0][0]["content"]
    assert "oat lattes" in system
    assert "[#1]" in system


def test_close_open_tool_calls_inserts_missing_results():
    orphan_id = "call_00_yN6pdzE5m7PH2VQI3OBD3339"
    messages = [
        {"role": "user", "content": "draft it"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": orphan_id,
                    "type": "function",
                    "function": {"name": "gmail_draft", "arguments": "{}"},
                }
            ],
        },
        {"role": "user", "content": "hi"},
    ]
    closed = _close_open_tool_calls(messages)
    assert closed[0] == messages[0]
    assert closed[1] == messages[1]
    assert closed[2]["role"] == "tool"
    assert closed[2]["tool_call_id"] == orphan_id
    assert closed[2]["name"] == "gmail_draft"
    assert "interrupted" in closed[2]["content"]
    assert closed[3] == messages[2]


def test_close_open_tool_calls_leaves_complete_pairs():
    messages = [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "now", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "name": "now", "content": "{}"},
        {"role": "assistant", "content": "noon"},
    ]
    assert _close_open_tool_calls(messages) == messages


def test_ws_heals_orphaned_tool_call_before_model(tmp_path):
    fake = FakeProvider(deltas=("Hey.",))
    built = create_app(token=TOKEN, provider=fake, state=tmp_path)
    store = built.state.store
    sid = built.state.store.open_session_id()
    store.append(sid, {"role": "user", "content": "draft to alyssa"})
    store.append(
        sid,
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_orphan",
                    "type": "function",
                    "function": {
                        "name": "gmail_draft",
                        "arguments": '{"to":"alyssa@berkeley.edu"}',
                    },
                }
            ],
        },
    )
    client = TestClient(built)
    with client.websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        ws.send_json({"type": "chat", "text": "hi"})
        events = _drain(ws)
    assert events[-1]["type"] == "turn_end"
    sent = fake.calls[0]
    roles = [m["role"] for m in sent]
    assert "tool" in roles
    idx = next(i for i, m in enumerate(sent) if m.get("tool_calls"))
    assert sent[idx + 1]["role"] == "tool"
    assert sent[idx + 1]["tool_call_id"] == "call_orphan"
    disk = store.load(sid)
    tool_ids = [m.get("tool_call_id") for m in disk if m.get("role") == "tool"]
    assert "call_orphan" in tool_ids


def test_ws_sourcing_persona_on_duty(tmp_path):
    fake = FakeProvider(deltas=("On duty.",))
    client = TestClient(
        create_app(
            token=TOKEN,
            provider=fake,
            state=tmp_path,
            persona_id="sourcing",
        )
    )
    with client.websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        ws.send_json({"type": "chat", "text": "who are you?"})
        _drain(ws)
    system = fake.calls[0][0]["content"]
    assert "Sourcecado's sourcing agent" in system
    assert "personal coworker" not in system
    res = client.get("/v1/persona", headers={TOKEN_HEADER: TOKEN})
    assert res.json()["id"] == "sourcing"


def test_run_records_carry_the_turn_message_id_but_the_model_never_sees_it(
    tmp_path,
):
    fake = FakeProvider(
        steps=[
            {"tool_calls": [ToolCall(id="call-now", name="now", arguments={})]},
            {"deltas": ("done",)},
        ]
    )
    built = create_app(token=TOKEN, provider=fake, state=tmp_path)
    client = TestClient(built)
    sid = built.state.store.open_session_id()

    with client.websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        ws.send_json({"type": "chat", "text": "time?", "session_id": sid})
        events = _drain(ws)

    message_id = events[0]["message_id"]
    records = built.state.store.load(sid)
    assistants = [r for r in records if r["role"] == "assistant"]
    tools = [r for r in records if r["role"] == "tool"]
    users = [r for r in records if r["role"] == "user"]
    assert assistants and tools and users
    # Restore merges by identity: assistant + tool records carry the turn's
    # message_id; the user record does not (it is not in the event projection).
    assert all(r.get("message_id") == message_id for r in assistants + tools)
    assert all("message_id" not in r for r in users)

    # A later turn replays history to the model without presentation keys.
    with client.websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        ws.send_json({"type": "chat", "text": "again", "session_id": sid})
        _drain(ws)
    assert all(
        "message_id" not in message
        for call in fake.calls
        for message in call
    )
