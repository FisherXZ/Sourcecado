from fastapi.testclient import TestClient

from coworker.gmail import FakeGmail
from coworker.provider import FakeProvider, ToolCall
from coworker.server import TOKEN_HEADER, _close_open_tool_calls, create_app

TOKEN = "test-token-slice-6"


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


def test_ws_error_when_no_provider(tmp_path):
    client = TestClient(create_app(token=TOKEN, provider=None, state=tmp_path))
    with client.websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        ws.send_json({"type": "chat", "text": "hi"})
        ev = ws.receive_json()
        if ev["type"] == "turn_start":
            ev = ws.receive_json()
        assert ev["type"] == "error"
        assert "key" in ev["message"].lower() or "model" in ev["message"].lower()


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
    assert body["messages"][-1] == {"role": "assistant", "content": "Hello world"}
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

