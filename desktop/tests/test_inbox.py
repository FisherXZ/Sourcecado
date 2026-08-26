import asyncio
import threading
import time

from fastapi.testclient import TestClient

from coworker.gmail import FakeGmail
from coworker.inbox import Inbox
from coworker.provider import FakeProvider, ToolCall
from coworker.server import TOKEN_HEADER, create_app
from coworker.store import ConversationStore

TOKEN = "test-token-inbox"


def test_inbox_first_resolve_wins(tmp_path):
    inbox = Inbox(ConversationStore(tmp_path))
    item = inbox.park("gmail_draft", {"to": "a@b.c", "subject": "s", "body": "b"}, item_id="call_1")
    assert item["state"] == "pending"
    first = inbox.resolve("call_1", "allow")
    assert first is not None
    assert first["decision"] == "allow"
    second = inbox.resolve("call_1", "deny")
    assert second is None


def test_inbox_pending_item_carries_backward_compatible_audit_metadata(tmp_path):
    inbox = Inbox(ConversationStore(tmp_path))

    item = inbox.park(
        "gmail_draft",
        {"to": "a@b.c", "subject": "s", "body": "b"},
        item_id="call-audit",
    )

    assert item["state"] == "pending"
    assert item["actor"] is None
    assert item["requested_at"]
    assert item["resolved_at"] is None
    assert item["scope"] == "once"
    assert item["execution_status"] == "pending"
    assert item["execution_error"] is None


def test_inbox_execution_claim_is_atomic_and_result_survives_reopen(tmp_path):
    store = ConversationStore(tmp_path)
    inbox = Inbox(store)
    inbox.park(
        "gmail_draft",
        {"to": "a@b.c", "subject": "s", "body": "b"},
        item_id="call-claim",
    )

    http = inbox.decide_and_claim(
        "call-claim",
        "allow",
        actor="Fisher",
        scope="once",
        claimant="http:request-1",
    )
    live = inbox.decide_and_claim(
        "call-claim",
        "allow",
        actor="operator",
        scope="once",
        claimant="turn:run-1",
    )

    assert http is not None and http.claimed is True and http.owned is True
    assert live is not None and live.claimed is False and live.owned is False
    assert live.item["actor"] == "Fisher"
    assert live.item["execution_status"] == "executing"
    terminal = inbox.complete_execution(
        "call-claim",
        claimant="http:request-1",
        ok=True,
        result={"draft_id": "draft-1", "status": "not_sent"},
    )

    assert terminal is not None
    assert terminal["execution_status"] == "succeeded"
    assert terminal["execution_result"] == {
        "draft_id": "draft-1",
        "status": "not_sent",
    }
    reopened = Inbox(ConversationStore(tmp_path)).get("call-claim")
    assert reopened is not None
    assert reopened["execution_status"] == "succeeded"
    assert reopened["execution_result"] == terminal["execution_result"]


def test_store_reopen_interrupts_orphaned_approval_execution_without_replaying(
    tmp_path,
):
    store = ConversationStore(tmp_path)
    inbox = Inbox(store)
    parked = inbox.park(
        "gmail_draft",
        {"to": "a@b.c", "subject": "s", "body": "b"},
        item_id="call-orphaned-execution",
        session_id="thread-alpha",
        run_id="run-alpha",
        message_id="message-alpha",
        part_id="part-alpha",
    )
    claimed = inbox.decide_and_claim(
        parked["id"],
        "allow",
        actor="Fisher",
        scope="once",
        claimant="http:dead-process",
    )
    assert claimed is not None and claimed.owned is True
    assert claimed.item["execution_status"] == "executing"

    reopened = Inbox(ConversationStore(tmp_path))
    interrupted = reopened.get(parked["id"])

    assert interrupted is not None
    assert interrupted["state"] == "resolved"
    assert interrupted["decision"] == "allow"
    assert interrupted["actor"] == "Fisher"
    assert interrupted["resolved_at"] == claimed.item["resolved_at"]
    assert interrupted["scope"] == "once"
    assert interrupted["execution_status"] == "interrupted"
    assert interrupted["execution_claimant"] is None
    assert "outcome is unknown" in interrupted["execution_error"].lower()
    assert "verify the external resource" in interrupted["execution_error"].lower()
    assert interrupted["execution_result"] == {
        "status": "interrupted",
        "error": interrupted["execution_error"],
    }
    waited = asyncio.run(
        asyncio.wait_for(reopened.wait_for_execution(parked["id"]), timeout=0.2)
    )
    assert waited == interrupted
    late_completion = inbox.complete_execution(
        parked["id"],
        claimant="http:dead-process",
        ok=True,
        result={"draft_id": "must-not-overwrite"},
    )
    assert late_completion == interrupted


def test_inbox_http_allow_creates_draft(tmp_path):
    gmail = FakeGmail()
    app = create_app(token=TOKEN, state=tmp_path, gmail=gmail)
    item = app.state.inbox.park(
        "gmail_draft",
        {"to": "alyssa@berkeley.edu", "subject": "hi", "body": "hello"},
        item_id="call_draft",
    )
    res = TestClient(app).post(
        f"/v1/inbox/{item['id']}",
        headers={TOKEN_HEADER: TOKEN},
        json={"decision": "allow"},
    )
    assert res.status_code == 200
    assert res.json()["ok"] is True
    assert gmail.drafts and gmail.drafts[0]["to"] == "alyssa@berkeley.edu"
    assert gmail.sends == []


def test_orphaned_approval_http_and_ws_replay_unknown_without_execution(
    tmp_path, monkeypatch
):
    store = ConversationStore(tmp_path)
    store.create_session("thread-orphaned")
    inbox = Inbox(store)
    parked = inbox.park(
        "gmail_draft",
        {"to": "a@b.c", "subject": "s", "body": "b"},
        item_id="call-orphaned-http-ws",
        session_id="thread-orphaned",
        run_id="run-orphaned",
        message_id="message-orphaned",
        part_id="part-orphaned",
    )
    inbox.decide_and_claim(
        parked["id"],
        "allow",
        actor="Fisher",
        scope="once",
        claimant="http:dead-process",
    )
    executions = []

    def must_not_execute(name, arguments, **kwargs):
        executions.append(name)
        return True, {"draft_id": "unsafe-replay"}

    monkeypatch.setattr("coworker.server.execute", must_not_execute)
    monkeypatch.setattr("coworker.turn.execute", must_not_execute)
    app = create_app(token=TOKEN, state=tmp_path)
    interrupted = app.state.inbox.get(parked["id"])
    assert interrupted is not None
    assert interrupted["execution_status"] == "interrupted"
    client = TestClient(app)
    payload = {"decision": "allow", "actor": "Fisher", "scope": "once"}

    first = client.post(
        f"/v1/inbox/{parked['id']}",
        headers={TOKEN_HEADER: TOKEN},
        json=payload,
    )
    replay = client.post(
        f"/v1/inbox/{parked['id']}",
        headers={TOKEN_HEADER: TOKEN},
        json=payload,
    )

    assert first.status_code == 200
    assert first.json()["ok"] is False
    assert first.json()["idempotent"] is True
    assert first.json()["result"] == interrupted["execution_result"]
    assert replay.status_code == 200
    assert replay.json()["result"] == first.json()["result"]
    assert executions == []
    receipts = [
        event
        for event in app.state.store.load_events("thread-orphaned")
        if event["type"] == "approval_resolved"
        and event["id"] == parked["id"]
    ]
    assert len(receipts) == 1
    assert receipts[0]["execution_status"] == "interrupted"
    assert "outcome is unknown" in receipts[0]["execution_error"].lower()

    with client.websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        ws.send_json(
            {"type": "permission", "id": parked["id"], "decision": "allow"}
        )
        live_receipt = ws.receive_json()
    assert live_receipt["type"] == "approval_resolved"
    assert live_receipt["id"] == parked["id"]
    assert live_receipt["execution_status"] == "interrupted"
    assert executions == []


def test_inbox_http_resolution_is_idempotent_and_persists_execution_receipt(tmp_path):
    gmail = FakeGmail()
    app = create_app(token=TOKEN, state=tmp_path, gmail=gmail)
    app.state.inbox.park(
        "gmail_draft",
        {"to": "alyssa@berkeley.edu", "subject": "hi", "body": "hello"},
        item_id="call-idempotent",
    )
    client = TestClient(app)
    payload = {"decision": "allow", "actor": "Fisher", "scope": "once"}

    first = client.post(
        "/v1/inbox/call-idempotent",
        headers={TOKEN_HEADER: TOKEN},
        json=payload,
    )
    duplicate = client.post(
        "/v1/inbox/call-idempotent",
        headers={TOKEN_HEADER: TOKEN},
        json=payload,
    )

    assert first.status_code == 200
    assert duplicate.status_code == 200
    assert duplicate.json()["idempotent"] is True
    assert len(gmail.drafts) == 1
    receipt = app.state.inbox.get("call-idempotent")
    assert receipt is not None
    assert receipt["decision"] == "allow"
    assert receipt["actor"] == "Fisher"
    assert receipt["resolved_at"]
    assert receipt["scope"] == "once"
    assert receipt["execution_status"] == "succeeded"
    assert receipt["execution_error"] is None


def test_inbox_http_resolution_persists_receipt_into_its_thread_event_log(tmp_path):
    app = create_app(token=TOKEN, state=tmp_path)
    sid = app.state.store.open_session_id()
    app.state.inbox.park(
        "gmail_draft",
        {"to": "alyssa@example.com", "subject": "Hello", "body": "Draft"},
        item_id="call-thread-receipt",
        reason="Draft creation needs approval.",
        session_id=sid,
        run_id="run-thread-receipt",
        message_id="message-thread-receipt",
        part_id="part-thread-receipt",
    )

    response = TestClient(app).post(
        "/v1/inbox/call-thread-receipt",
        headers={TOKEN_HEADER: TOKEN},
        json={"decision": "deny", "actor": "Fisher", "scope": "once"},
    )

    assert response.status_code == 200
    events = app.state.store.load_events(sid)
    receipt = next(event for event in events if event["type"] == "approval_resolved")
    assert receipt["id"] == "call-thread-receipt"
    assert receipt["resolution"] == "denied"
    assert receipt["actor"] == "Fisher"
    assert receipt["execution_status"] == "not_run"


def test_inbox_http_deny_creates_nothing(tmp_path):
    gmail = FakeGmail()
    app = create_app(token=TOKEN, state=tmp_path, gmail=gmail)
    item = app.state.inbox.park(
        "gmail_draft",
        {"to": "alyssa@berkeley.edu", "subject": "hi", "body": "hello"},
        item_id="call_draft",
    )
    res = TestClient(app).post(
        f"/v1/inbox/{item['id']}",
        headers={TOKEN_HEADER: TOKEN},
        json={"decision": "deny"},
    )
    assert res.status_code == 200
    assert res.json()["ok"] is False
    assert gmail.drafts == []
    assert gmail.sends == []


def test_inbox_http_allow_during_ws_executes_once(tmp_path, monkeypatch):
    calls = []
    http_started = threading.Event()
    release_http = threading.Event()

    def slow_http_execute(name, arguments, **kwargs):
        calls.append("http-start")
        http_started.set()
        assert release_http.wait(timeout=2)
        calls.append("http-end")
        return True, {"draft_id": "draft-from-http", "status": "not_sent"}

    def ws_execute(name, arguments, **kwargs):
        calls.append("ws-execute")
        return True, {"draft_id": "draft-from-ws", "status": "not_sent"}

    monkeypatch.setattr("coworker.server.execute", slow_http_execute)
    monkeypatch.setattr("coworker.turn.execute", ws_execute)
    fake = FakeProvider(
        steps=[
            {
                "tool_calls": [
                    ToolCall(
                        id="call_draft",
                        name="gmail_draft",
                        arguments={
                            "to": "alyssa@berkeley.edu",
                            "subject": "hi",
                            "body": "hello",
                        },
                    )
                ]
            },
            {"deltas": ("drafted",)},
        ]
    )
    app = create_app(token=TOKEN, provider=fake, state=tmp_path)
    client = TestClient(app)
    http_response = {}

    def allow_over_http():
        http_response["response"] = client.post(
            "/v1/inbox/call_draft",
            headers={TOKEN_HEADER: TOKEN},
            json={"decision": "allow", "actor": "Fisher", "scope": "once"},
        )

    with client.websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        ws.send_json({"type": "chat", "text": "draft Alyssa"})
        events = []
        while True:
            ev = ws.receive_json()
            events.append(ev)
            if ev["type"] == "permission_required":
                request = threading.Thread(target=allow_over_http)
                request.start()
                assert http_started.wait(timeout=1)
                try:
                    time.sleep(0.12)
                    calls_before_release = list(calls)
                finally:
                    release_http.set()
                    request.join(timeout=2)
            if ev["type"] in ("turn_end", "error"):
                break

    first = http_response["response"]
    replay = client.post(
        "/v1/inbox/call_draft",
        headers={TOKEN_HEADER: TOKEN},
        json={"decision": "allow", "actor": "Fisher", "scope": "once"},
    )
    receipt = app.state.inbox.get("call_draft")
    finished = next(event for event in events if event["type"] == "tool_finished")
    persisted_receipts = [
        event
        for event in app.state.store.load_events(str(receipt["session_id"]))
        if event["type"] == "approval_resolved"
        and event["id"] == "call_draft"
    ]

    assert calls_before_release == ["http-start"]
    assert calls == ["http-start", "http-end"]
    assert first.status_code == 200
    assert first.json()["result"] == {
        "draft_id": "draft-from-http",
        "status": "not_sent",
    }
    assert replay.status_code == 200
    assert replay.json()["idempotent"] is True
    assert replay.json()["result"] == first.json()["result"]
    assert receipt["execution_status"] == "succeeded"
    assert receipt["execution_result"] == first.json()["result"]
    assert finished["result"] == first.json()["result"]
    assert len(persisted_receipts) == 1


def test_ws_allow_claim_makes_overlapping_http_wait_for_terminal_result(
    tmp_path, monkeypatch
):
    calls = []
    ws_started = threading.Event()
    release_ws = threading.Event()

    def slow_ws_execute(name, arguments, **kwargs):
        calls.append("ws-start")
        ws_started.set()
        assert release_ws.wait(timeout=2)
        calls.append("ws-end")
        return True, {"draft_id": "draft-from-ws", "status": "not_sent"}

    def http_execute(name, arguments, **kwargs):
        calls.append("http-execute")
        return True, {"draft_id": "draft-from-http", "status": "not_sent"}

    monkeypatch.setattr("coworker.turn.execute", slow_ws_execute)
    monkeypatch.setattr("coworker.server.execute", http_execute)
    fake = FakeProvider(
        steps=[
            {
                "tool_calls": [
                    ToolCall(
                        id="call-ws-wins",
                        name="gmail_draft",
                        arguments={
                            "to": "alyssa@berkeley.edu",
                            "subject": "hi",
                            "body": "hello",
                        },
                    )
                ]
            },
            {"deltas": ("drafted",)},
        ]
    )
    app = create_app(token=TOKEN, provider=fake, state=tmp_path)
    client = TestClient(app)
    http_response = {}

    def allow_over_http():
        http_response["response"] = client.post(
            "/v1/inbox/call-ws-wins",
            headers={TOKEN_HEADER: TOKEN},
            json={"decision": "allow", "actor": "Fisher", "scope": "once"},
        )

    with client.websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        ws.send_json({"type": "chat", "text": "draft Alyssa"})
        events = []
        while True:
            event = ws.receive_json()
            events.append(event)
            if event["type"] == "permission_required":
                ws.send_json(
                    {
                        "type": "permission",
                        "id": "call-ws-wins",
                        "decision": "allow",
                        "actor": "operator",
                        "scope": "once",
                    }
                )
                assert ws_started.wait(timeout=1)
                request = threading.Thread(target=allow_over_http)
                request.start()
                try:
                    time.sleep(0.12)
                    http_waited_for_terminal = request.is_alive()
                    calls_before_release = list(calls)
                finally:
                    release_ws.set()
                    request.join(timeout=2)
            if event["type"] in ("turn_end", "error"):
                break

    response = http_response["response"]
    finished = next(event for event in events if event["type"] == "tool_finished")
    receipt = app.state.inbox.get("call-ws-wins")

    assert http_waited_for_terminal is True
    assert calls_before_release == ["ws-start"]
    assert calls == ["ws-start", "ws-end"]
    assert response.status_code == 200
    assert response.json()["idempotent"] is True
    assert response.json()["result"] == {
        "draft_id": "draft-from-ws",
        "status": "not_sent",
    }
    assert receipt is not None
    assert receipt["execution_status"] == "succeeded"
    assert receipt["execution_result"] == response.json()["result"]
    assert finished["result"] == response.json()["result"]
