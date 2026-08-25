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


def test_inbox_http_allow_during_ws_executes_once(tmp_path):
    gmail = FakeGmail()
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
    app = create_app(token=TOKEN, provider=fake, state=tmp_path, gmail=gmail)
    client = TestClient(app)
    with client.websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        ws.send_json({"type": "chat", "text": "draft Alyssa"})
        while True:
            ev = ws.receive_json()
            if ev["type"] == "permission_required":
                client.post(
                    "/v1/inbox/call_draft",
                    headers={TOKEN_HEADER: TOKEN},
                    json={"decision": "allow"},
                )
            if ev["type"] in ("turn_end", "error"):
                break
    assert len(gmail.drafts) == 1
