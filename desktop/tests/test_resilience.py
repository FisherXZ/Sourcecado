"""Failure-path resilience: every turn ends, no transport error corrupts state."""

import asyncio
import threading
import time

from fastapi.testclient import TestClient

from coworker.events import new_turn_identity
from coworker.inbox import Inbox
from coworker.provider import FakeProvider, ToolCall
from coworker.server import TOKEN_HEADER, create_app
from coworker.store import ConversationStore
from coworker.tools import OPENAI_TOOLS
from coworker.turn import RunControl, run_turn

TOKEN = "test-token-resilience"


def _wait_until(check, timeout=3.0, interval=0.01):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(interval)
    return check()


def _run_turn(store, sid, *, provider, control, emit, system_prompt_fn=None):
    return asyncio.run(
        run_turn(
            text="hi",
            sid=sid,
            store=store,
            provider=provider,
            persona=None,
            skills=None,
            inbox=Inbox(store),
            openai_tools=OPENAI_TOOLS,
            execute_kwargs={},
            emit=emit,
            system_prompt_fn=system_prompt_fn,
            identity=control.identity,
            control=control,
        )
    )


def test_failing_system_prompt_still_reaches_exactly_one_terminal_state(tmp_path):
    store = ConversationStore(tmp_path)
    store.create_session("thread-prompt-boom")
    control = RunControl(new_turn_identity("thread-prompt-boom"))
    emitted = []

    async def emit(event):
        emitted.append(event)

    def broken_prompt(*args, **kwargs):
        raise RuntimeError("prompt assembly failed")

    result = _run_turn(
        store,
        "thread-prompt-boom",
        provider=FakeProvider(),
        control=control,
        emit=emit,
        system_prompt_fn=broken_prompt,
    )

    assert result["status"] == "error"
    assert control.terminal is True
    live_terminals = [e for e in emitted if e["type"] in ("error", "turn_end")]
    assert [e["type"] for e in live_terminals] == ["error"]
    assert live_terminals[0]["state"] == "failed"
    persisted = [
        e
        for e in store.load_events("thread-prompt-boom")
        if e["type"] in ("error", "turn_end")
    ]
    assert [e["type"] for e in persisted] == ["error"]


def test_unreadable_transcript_still_reaches_exactly_one_terminal_state(
    tmp_path, monkeypatch
):
    store = ConversationStore(tmp_path)
    store.create_session("thread-load-boom")
    control = RunControl(new_turn_identity("thread-load-boom"))
    emitted = []

    async def emit(event):
        emitted.append(event)

    def broken_load(sid):
        raise OSError("disk read failed")

    monkeypatch.setattr(store, "load", broken_load)
    result = _run_turn(
        store,
        "thread-load-boom",
        provider=FakeProvider(),
        control=control,
        emit=emit,
    )

    assert result["status"] == "error"
    assert control.terminal is True
    terminals = [e for e in emitted if e["type"] in ("error", "turn_end")]
    assert [e["type"] for e in terminals] == ["error"]
    assert "disk read failed" in terminals[0]["message"]


def test_torn_transcript_line_still_reaches_exactly_one_terminal_state(tmp_path):
    store = ConversationStore(tmp_path)
    store.create_session("thread-torn")
    store.append("thread-torn", {"role": "user", "content": "earlier"})
    with open(store.conv_dir / "thread-torn.jsonl", "a", encoding="utf-8") as fh:
        fh.write('{"role": "assistant", "content": "torn mid-wr')
    control = RunControl(new_turn_identity("thread-torn"))
    emitted = []

    async def emit(event):
        emitted.append(event)

    result = _run_turn(
        store,
        "thread-torn",
        provider=FakeProvider(),
        control=control,
        emit=emit,
    )

    assert result["status"] in ("ok", "error")
    assert control.terminal is True
    terminals = [e for e in emitted if e["type"] in ("error", "turn_end")]
    assert len(terminals) == 1


def _blocked_now_app(tmp_path, monkeypatch):
    release = threading.Event()
    started = threading.Event()

    def blocked_execute(name, arguments, **kwargs):
        started.set()
        assert release.wait(timeout=5)
        return True, {"time": "now-ish"}

    monkeypatch.setattr("coworker.turn.execute", blocked_execute)
    fake = FakeProvider(
        steps=[
            {"tool_calls": [ToolCall(id="call-now", name="now", arguments={})]},
            {"deltas": ("all done",)},
        ]
    )
    built = create_app(token=TOKEN, provider=fake, state=tmp_path)
    return built, fake, release, started


def _disconnect_mid_run(client, built, release, started):
    """Start a run, queue one item, drop the socket mid-tool, let the run end."""
    sid = built.state.store.open_session_id()
    with client.websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        ws.send_json({"type": "chat", "text": "what time", "session_id": sid})
        while ws.receive_json()["type"] != "tool_started":
            pass
        assert started.wait(timeout=2)
        ws.send_json(
            {
                "type": "queue_add",
                "session_id": sid,
                "command_id": "offline-add",
                "item_id": "offline-item",
                "text": "queued while the window is gone",
            }
        )
        assert ws.receive_json()["type"] == "queue_snapshot"
    release.set()
    assert _wait_until(
        lambda: any(
            event["type"] == "turn_end"
            for event in built.state.store.load_events(sid)
        )
    )
    return sid


def test_dead_socket_mid_run_completes_headless_and_parks_queue_offline(
    tmp_path, monkeypatch
):
    built, fake, release, started = _blocked_now_app(tmp_path, monkeypatch)
    with TestClient(built) as client:
        sid = _disconnect_mid_run(client, built, release, started)
        events = built.state.store.load_events(sid)
        assert [e["type"] for e in events if e["type"] == "error"] == []
        end = next(e for e in events if e["type"] == "turn_end")
        assert end["state"] == "complete"
        assert _wait_until(
            lambda: [
                item["state"] for item in built.state.store.list_queue(sid)
            ]
            == ["offline"]
        )
        # the queued item must not run headless with nobody watching
        assert len(fake.calls) == 2


def test_reconnect_resume_drains_offline_queue_items(tmp_path, monkeypatch):
    built, fake, release, started = _blocked_now_app(tmp_path, monkeypatch)
    with TestClient(built) as client:
        sid = _disconnect_mid_run(client, built, release, started)
        assert _wait_until(
            lambda: [
                item["state"] for item in built.state.store.list_queue(sid)
            ]
            == ["offline"]
        )
        with client.websocket_connect(
            "/ws/chat", subprotocols=["club", TOKEN]
        ) as ws:
            # The reconnect contract pushes an authoritative snapshot first.
            connection = ws.receive_json()
            assert connection["type"] == "queue_snapshot"
            assert connection["status"] == "connection"
            assert [item["state"] for item in connection["items"]] == ["offline"]
            ws.send_json(
                {
                    "type": "queue_resume",
                    "session_id": sid,
                    "command_id": "resume-after-reconnect",
                }
            )
            ack = ws.receive_json()
            assert ack["type"] == "queue_snapshot"
            assert ack["command_id"] == "resume-after-reconnect"
            assert [item["state"] for item in ack["items"]] == ["reconnecting"]
            while True:
                event = ws.receive_json()
                if event["type"] in ("turn_end", "error"):
                    break
        assert event["type"] == "turn_end"
        assert built.state.store.list_queue(sid) == []
        assert (
            fake.calls[-1][-1]["content"] == "queued while the window is gone"
        )


def test_malformed_frames_and_bad_queue_commands_do_not_kill_the_socket(tmp_path):
    built = create_app(
        token=TOKEN, provider=FakeProvider(deltas=("still alive",)), state=tmp_path
    )
    client = TestClient(built)
    sid = built.state.store.open_session_id()

    with client.websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        ws.send_text("this is not json")
        assert ws.receive_json()["type"] == "error"
        ws.send_json(["not", "an", "object"])
        assert ws.receive_json()["type"] == "error"
        ws.send_json(
            {"type": "queue_add", "session_id": sid, "command_id": "bad-add"}
        )
        assert ws.receive_json()["type"] == "error"
        ws.send_json({"type": "chat", "text": "hi", "session_id": sid})
        events = []
        while True:
            event = ws.receive_json()
            events.append(event)
            if event["type"] in ("turn_end", "error"):
                break

    assert events[0]["type"] == "turn_start"
    assert events[-1]["type"] == "turn_end"


def test_ws_allow_for_a_dead_turn_executes_server_side_instead_of_wedging(
    tmp_path, monkeypatch
):
    """B1: a permission answer must never claim on behalf of a turn that is gone."""
    executed = []

    def recording_execute(name, arguments, **kwargs):
        executed.append(name)
        return True, {"draft_id": "d1", "status": "not_sent"}

    monkeypatch.setattr("coworker.turn.execute", recording_execute)
    monkeypatch.setattr("coworker.server.execute", recording_execute)
    fake = FakeProvider(
        steps=[
            {
                "tool_calls": [
                    ToolCall(
                        id="call-b1",
                        name="gmail_draft",
                        arguments={"to": "a@b.c", "subject": "s", "body": "b"},
                    )
                ]
            },
            {"deltas": ("drafted",)},
        ]
    )
    built = create_app(token=TOKEN, provider=fake, state=tmp_path)
    # Bare TestClient: each ws context has its own portal, so leaving the first
    # context kills the turn's task outright — the dead-before-claim variant.
    client = TestClient(built)
    inbox = built.state.inbox

    with client.websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws1:
        ws1.send_json({"type": "chat", "text": "draft it"})
        while True:
            event = ws1.receive_json()
            if event["type"] == "permission_required":
                session_id = event["session_id"]
                break
    # ws1's portal is gone; the turn that parked call-b1 is dead.
    item = inbox.get("call-b1")
    assert item is not None and item["state"] == "pending"

    with client.websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws2:
        ws2.send_json(
            {
                "type": "permission",
                "id": "call-b1",
                "decision": "allow",
                "actor": "operator",
                "scope": "once",
            }
        )
        receipt_event = ws2.receive_json()

    item = inbox.get("call-b1")
    assert executed == ["gmail_draft"]  # the authorized action actually ran
    assert item["execution_status"] == "succeeded"
    assert str(item["execution_claimant"]).startswith("ws:")
    assert item["execution_result"] == {"draft_id": "d1", "status": "not_sent"}
    assert receipt_event["type"] == "approval_resolved"
    assert receipt_event["id"] == "call-b1"
    assert receipt_event["resolution"] == "allowed"
    assert receipt_event["execution_status"] == "succeeded"
    receipts = [
        event
        for event in built.state.store.load_events(session_id)
        if event["type"] == "approval_resolved" and event["id"] == "call-b1"
    ]
    assert len(receipts) == 1
    # An HTTP replay returns the terminal result instead of hanging.
    replay = client.post(
        "/v1/inbox/call-b1",
        headers={TOKEN_HEADER: TOKEN},
        json={"decision": "allow", "actor": "operator", "scope": "once"},
    )
    assert replay.status_code == 200
    assert replay.json()["idempotent"] is True
    assert replay.json()["result"] == {"draft_id": "d1", "status": "not_sent"}
    assert executed == ["gmail_draft"]  # replay executed nothing new


def test_new_socket_replays_live_turn_start_and_authoritative_queue_snapshot(
    tmp_path, monkeypatch
):
    built, fake, release, started = _blocked_now_app(tmp_path, monkeypatch)
    with TestClient(built) as client:
        sid = built.state.store.open_session_id()
        with client.websocket_connect(
            "/ws/chat", subprotocols=["club", TOKEN]
        ) as ws1:
            ws1.send_json({"type": "chat", "text": "what time", "session_id": sid})
            while ws1.receive_json()["type"] != "tool_started":
                pass
            assert started.wait(timeout=2)
            ws1.send_json(
                {
                    "type": "queue_add",
                    "session_id": sid,
                    "command_id": "replay-add",
                    "item_id": "replay-item",
                    "text": "queued during the run",
                }
            )
            assert ws1.receive_json()["type"] == "queue_snapshot"

            # A second window connects while the run is still in flight.
            with client.websocket_connect(
                "/ws/chat", subprotocols=["club", TOKEN]
            ) as ws2:
                first = ws2.receive_json()
                second = ws2.receive_json()

            release.set()
            while True:
                event = ws1.receive_json()
                if event["type"] in ("turn_end", "error"):
                    break

    replayed = next(e for e in (first, second) if e["type"] == "turn_start")
    snapshot = next(e for e in (first, second) if e["type"] == "queue_snapshot")
    original = next(
        e
        for e in built.state.store.load_events(sid)
        if e["type"] == "turn_start"
    )
    # The ORIGINAL envelope, byte for byte — same event_id, same identity.
    assert replayed == original
    assert snapshot["session_id"] == sid
    assert snapshot["status"] == "connection"
    assert str(snapshot["command_id"]).startswith("connection-")
    assert [item["id"] for item in snapshot["items"]] == ["replay-item"]
    assert [item["state"] for item in snapshot["items"]] == ["waiting"]
