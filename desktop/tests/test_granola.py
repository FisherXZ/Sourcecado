import json
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from coworker.apollo import FakeHttp
from coworker.mcp import FakeMcp, LiveMcp
from coworker.mcp_oauth import McpOAuth
from coworker.permissions import decide
from coworker.provider import FakeProvider, ToolCall
from coworker.secrets import SecretStore
from coworker.server import TOKEN_HEADER, create_app
from coworker.tools import execute

TOKEN = "test-token-granola"
ISSUER = "https://mcp-auth.granola.ai"
PRM_URL = "https://mcp.granola.ai/.well-known/oauth-protected-resource"
AS_META_URL = f"{ISSUER}/.well-known/oauth-authorization-server"
REGISTER_URL = f"{ISSUER}/oauth2/register"
AUTHORIZE_URL = f"{ISSUER}/oauth2/authorize"
TOKEN_URL = f"{ISSUER}/oauth2/token"


def granola_http(**extra):
    routes = {
        PRM_URL: {
            "resource": "https://mcp.granola.ai/mcp",
            "authorization_servers": [ISSUER],
            "scopes_supported": ["mcp"],
        },
        AS_META_URL: {
            "issuer": ISSUER,
            "authorization_endpoint": AUTHORIZE_URL,
            "token_endpoint": TOKEN_URL,
            "registration_endpoint": REGISTER_URL,
        },
        REGISTER_URL: {"client_id": "client_test"},
        TOKEN_URL: {"access_token": "at", "refresh_token": "rt"},
    }
    routes.update(extra)
    return FakeHttp(routes)


def test_default_mcp_json_names_granola(tmp_path):
    create_app(token=TOKEN, state=tmp_path)
    path = tmp_path / "mcp.json"
    assert path.is_file()
    data = json.loads(path.read_text())
    assert data["mcpServers"]["granola"]["url"] == "https://mcp.granola.ai/mcp"
    assert data["mcpServers"]["granola"]["auth"] == "oauth"


def test_granola_status_needs_auth(tmp_path):
    body = TestClient(create_app(token=TOKEN, state=tmp_path)).get(
        "/v1/connectors", headers={TOKEN_HEADER: TOKEN}
    ).json()
    gran = next(c for c in body["connectors"] if c["id"] == "granola")
    assert gran["status"] == "missing"


def test_connect_does_not_persist_forged_token(tmp_path, monkeypatch):
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: None)
    http = granola_http()
    app = create_app(token=TOKEN, state=tmp_path, http=http)
    res = TestClient(app).post("/v1/connectors/granola/connect", headers={TOKEN_HEADER: TOKEN})
    body = res.json()
    assert body["started"] is True
    parsed = urlparse(body["url"])
    assert parsed.netloc == "mcp-auth.granola.ai"
    assert parsed.path.rstrip("/") == "/oauth2/authorize"
    assert "mcp.granola.ai/authorize" not in body["url"]
    query = parse_qs(parsed.query)
    assert query["client_id"] == ["client_test"]
    assert query["resource"] == ["https://mcp.granola.ai/mcp"]
    assert query["scope"] == ["mcp"]
    assert any(c["url"] == REGISTER_URL and c["method"] == "POST" for c in http.calls)
    assert app.state.secrets.get("mcp-oauth:granola") in (None, {})
    bad = TestClient(app).get("/v1/mcp/oauth/callback?state=st")
    assert bad.status_code == 400
    assert app.state.secrets.get("mcp-oauth:granola") in (None, {})
    assert app.state.mcp.schemas() == []


def test_live_mcp_sdk_missing_is_error(tmp_path, monkeypatch):
    def boom():
        raise ImportError("mcp")

    monkeypatch.setattr("coworker.mcp._import_mcp_sdk", boom)
    secrets = SecretStore(tmp_path / "secrets.json")
    secrets.put("mcp-oauth:granola", {"access_token": "at"})
    mcp = LiveMcp(secrets=secrets, config_path=tmp_path / "mcp.json")
    ok, result = execute("mcp__granola__list_meetings", {}, mcp=mcp)
    assert ok is False
    assert "sdk" in str(result.get("error") or "").lower()


def test_connect_starts_flow(tmp_path, monkeypatch):
    seen = {}

    def fake_start(server):
        seen["name"] = server
        return {"url": "https://example.test/auth", "started": True}

    app = create_app(token=TOKEN, state=tmp_path)
    monkeypatch.setattr(app.state.mcp_oauth, "start", fake_start)
    res = TestClient(app).post("/v1/connectors/granola/connect", headers={TOKEN_HEADER: TOKEN})
    assert res.json()["started"] is True
    assert seen["name"] == "granola"


def test_mcp_write_tools_are_denied():
    d = decide("mcp__granola__create_note")
    assert d.allowed is False
    assert d.needs_user is False
    assert decide("mcp__granola__list_meetings").needs_user is False
    assert decide("mcp__granola__list_meetings").allowed is True


def test_unknown_granola_tools_do_not_execute(tmp_path):
    secrets = SecretStore(tmp_path / "secrets.json")
    secrets.put("mcp-oauth:granola", {"access_token": "at"})
    mcp = LiveMcp(secrets=secrets, config_path=tmp_path / "mcp.json")
    assert mcp.has("mcp__granola__list_meetings") is True
    assert mcp.has("mcp__granola__send_email") is False
    ok, result = execute("mcp__granola__send_email", {}, mcp=mcp)
    assert ok is False
    assert "unknown" in str(result.get("error") or "").lower()


def test_live_mcp_omits_schemas_until_oauth(tmp_path):
    mcp = LiveMcp(secrets=SecretStore(tmp_path / "secrets.json"), config_path=tmp_path / "mcp.json")
    assert mcp.schemas() == []


def test_live_mcp_refuses_writes(tmp_path):
    mcp = LiveMcp(secrets=SecretStore(tmp_path / "secrets.json"), config_path=tmp_path / "mcp.json")
    result = mcp.call("mcp__granola__create_note", {})
    assert result.get("error")


def test_tool_name_prefix_read_ok():
    mcp = FakeMcp([{"name": "mcp__granola__list_meetings", "handler": lambda a: {"ok": True}}])
    ok, result = execute("mcp__granola__list_meetings", {}, mcp=mcp)
    assert ok is True


def test_ws_denied_mcp_write_does_not_call(tmp_path):
    called = []
    mcp = FakeMcp(
        [{"name": "mcp__granola__create_note", "handler": lambda a: called.append(a) or {"ok": True}}]
    )
    fake = FakeProvider(
        steps=[
            {"tool_calls": [ToolCall(id="c1", name="mcp__granola__create_note", arguments={})]},
            {"deltas": ("nope",)},
        ]
    )
    app = create_app(token=TOKEN, provider=fake, state=tmp_path, mcp=mcp)
    with TestClient(app).websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        ws.send_json({"type": "chat", "text": "create a granola note"})
        events = []
        while True:
            ev = ws.receive_json()
            events.append(ev)
            if ev["type"] in ("turn_end", "error"):
                break
    assert called == []
    finished = next(e for e in events if e["type"] == "tool_finished")
    assert finished["ok"] is False


def test_oauth_finish_without_waiter_rejected(tmp_path):
    oauth = McpOAuth(SecretStore(tmp_path / "secrets.json"), "http://127.0.0.1:8765", http=granola_http())
    try:
        oauth.finish(code="abc", state="nope")
        raised = False
    except Exception:
        raised = True
    assert raised is True
    assert SecretStore(tmp_path / "secrets.json").get("mcp-oauth:granola") in (None, {})


def test_oauth_mismatched_state_does_not_consume(tmp_path):
    oauth = McpOAuth(SecretStore(tmp_path / "secrets.json"), "http://127.0.0.1:8765", http=granola_http())
    started = oauth.start("granola")
    assert started["started"] is True
    try:
        oauth.finish(code="abc", state="wrong")
    except Exception:
        pass
    assert getattr(oauth, "_pending", None) is not None


def test_oauth_finish_exchanges_code_at_auth_server(tmp_path, monkeypatch):
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: None)
    http = granola_http()
    oauth = McpOAuth(SecretStore(tmp_path / "secrets.json"), "http://127.0.0.1:8765", http=http)
    started = oauth.start("granola")
    state = parse_qs(urlparse(started["url"]).query)["state"][0]
    oauth.finish(code="real-code", state=state)
    token_posts = [c for c in http.calls if c["url"] == TOKEN_URL and c["method"] == "POST"]
    assert token_posts
    assert token_posts[0]["data"]["code"] == "real-code"
    assert token_posts[0]["data"]["client_id"] == "client_test"
    assert token_posts[0]["data"]["resource"] == "https://mcp.granola.ai/mcp"
    stored = SecretStore(tmp_path / "secrets.json").get("mcp-oauth:granola")
    assert stored["access_token"] == "at"
    assert stored["client_id"] == "client_test"
