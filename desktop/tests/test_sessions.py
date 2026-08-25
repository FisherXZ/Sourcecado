from fastapi.testclient import TestClient

from coworker.provider import FakeProvider
from coworker.server import TOKEN_HEADER, create_app

TOKEN = "test-token-sessions"


def client(tmp_path):
    return TestClient(create_app(token=TOKEN, provider=FakeProvider(), state=tmp_path))


def test_post_session_then_list(tmp_path):
    c = client(tmp_path)
    created = c.post("/v1/sessions", headers={TOKEN_HEADER: TOKEN}).json()
    assert created["n_msgs"] == 0
    listing = c.get("/v1/sessions", headers={TOKEN_HEADER: TOKEN}).json()
    assert listing["open_id"] == created["id"]
    assert listing["sessions"][0]["session_id"] == created["id"]


def test_patch_title(tmp_path):
    c = client(tmp_path)
    sid = c.post("/v1/sessions", headers={TOKEN_HEADER: TOKEN}).json()["id"]
    res = c.patch(f"/v1/sessions/{sid}", headers={TOKEN_HEADER: TOKEN}, json={"title": "Alyssa"})
    assert res.json()["title"] == "Alyssa"
    listed = c.get("/v1/sessions", headers={TOKEN_HEADER: TOKEN}).json()
    assert listed["sessions"][0]["title"] == "Alyssa"


def test_get_unknown_session_404(tmp_path):
    c = client(tmp_path)
    res = c.get("/v1/sessions/does-not-exist", headers={TOKEN_HEADER: TOKEN})
    assert res.status_code == 404


def test_ws_rejects_path_escape_session_id(tmp_path):
    app = create_app(token=TOKEN, provider=FakeProvider(deltas=("ok",)), state=tmp_path)
    c = TestClient(app)
    with c.websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        ws.send_json({"type": "chat", "text": "hello", "session_id": "../secrets"})
        ev = ws.receive_json()
        assert ev["type"] == "error"
        assert "invalid session" in ev["message"]
    assert not (tmp_path / "secrets.jsonl").exists()


def test_ws_chat_uses_named_session(tmp_path):
    app = create_app(token=TOKEN, provider=FakeProvider(deltas=("ok",)), state=tmp_path)
    c = TestClient(app)
    sid = c.post("/v1/sessions", headers={TOKEN_HEADER: TOKEN}).json()["id"]
    with c.websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        ws.send_json({"type": "chat", "text": "hello there", "session_id": sid})
        events = []
        while True:
            ev = ws.receive_json()
            events.append(ev)
            if ev["type"] in ("turn_end", "error"):
                break
    assert any(e["type"] == "turn_end" for e in events)
    body = c.get(f"/v1/sessions/{sid}", headers={TOKEN_HEADER: TOKEN}).json()
    assert body["messages"][0]["content"] == "hello there"
    assert body["title"] == "hello there"


def test_new_session_does_not_read_old_transcript(tmp_path):
    app = create_app(token=TOKEN, provider=FakeProvider(), state=tmp_path)
    app.state.store.append("old", {"role": "user", "content": "secret old chat"})
    c = TestClient(app)
    fresh = c.post("/v1/sessions", headers={TOKEN_HEADER: TOKEN}).json()
    body = c.get(f"/v1/sessions/{fresh['id']}", headers={TOKEN_HEADER: TOKEN}).json()
    assert body["messages"] == []


def test_ws_two_sessions_do_not_leak_history(tmp_path):
    app = create_app(token=TOKEN, provider=FakeProvider(deltas=("ok",)), state=tmp_path)
    c = TestClient(app)
    a = c.post("/v1/sessions", headers={TOKEN_HEADER: TOKEN}).json()["id"]
    b = c.post("/v1/sessions", headers={TOKEN_HEADER: TOKEN}).json()["id"]
    with c.websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        ws.send_json({"type": "chat", "text": "alpha secret", "session_id": a})
        while True:
            if ws.receive_json()["type"] in ("turn_end", "error"):
                break
        ws.send_json({"type": "chat", "text": "bravo only", "session_id": b})
        while True:
            if ws.receive_json()["type"] in ("turn_end", "error"):
                break
    msgs_a = c.get(f"/v1/sessions/{a}", headers={TOKEN_HEADER: TOKEN}).json()["messages"]
    msgs_b = c.get(f"/v1/sessions/{b}", headers={TOKEN_HEADER: TOKEN}).json()["messages"]
    assert any("alpha secret" in str(m.get("content")) for m in msgs_a)
    assert not any("alpha secret" in str(m.get("content")) for m in msgs_b)
    assert any("bravo only" in str(m.get("content")) for m in msgs_b)
