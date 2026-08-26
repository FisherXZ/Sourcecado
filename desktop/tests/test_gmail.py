from urllib.parse import unquote

import httpx
from fastapi.testclient import TestClient

from coworker.apollo import FakeHttp, HttpError
from coworker.connectors.google_oauth import (
    CALENDAR_SCOPE,
    COMPOSE_SCOPE,
    DRIVE_SCOPE,
    READ_SCOPE,
    SEND_SCOPE,
    authorization_url,
    load_google,
    save_google,
)
from coworker.gmail import DRAFTS_URL, FakeGmail, GmailApi, gmail_from_secrets
from coworker.provider import FakeProvider, ToolCall
from coworker.secrets import SecretStore
from coworker.server import TOKEN_HEADER, create_app
from coworker.tools import execute

GMAIL_API_DISABLED = (
    "Gmail API has not been used in project 1011298621436 before or it is disabled. "
    "Enable it by visiting https://console.developers.google.com/apis/api/gmail.googleapis.com/overview?project=1011298621436 then retry."
)


def _http_status_error(status: int, body: dict) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", DRAFTS_URL)
    response = httpx.Response(status, json=body, request=request)
    return httpx.HTTPStatusError(
        f"Client error '{status} Forbidden' for url '{DRAFTS_URL}'",
        request=request,
        response=response,
    )

TOKEN = "test-token-gmail"


def _drain(ws):
    events = []
    while True:
        ev = ws.receive_json()
        events.append(ev)
        if ev["type"] in ("turn_end", "error"):
            return events


def _until(ws, typ: str):
    events = []
    while True:
        ev = ws.receive_json()
        events.append(ev)
        if ev["type"] == typ:
            return events
        if ev["type"] in ("turn_end", "error"):
            return events


def test_gmail_draft_execute_does_not_send():
    gmail = FakeGmail()
    ok, result = execute(
        "gmail_draft",
        {"to": "fisher@example.com", "subject": "hi", "body": "hello"},
        gmail=gmail,
    )
    assert ok is True
    assert result["drafted"] is True
    assert result["sent"] is False
    assert result["id"] == "draft_1"
    assert len(gmail.drafts) == 1
    assert gmail.sends == []


def test_ws_gmail_draft_allow_creates_draft(tmp_path):
    gmail = FakeGmail()
    fake = FakeProvider(
        steps=[
            {
                "tool_calls": [
                    ToolCall(
                        id="call_draft",
                        name="gmail_draft",
                        arguments={
                            "to": "fisher@example.com",
                            "subject": "hi",
                            "body": "hello",
                        },
                    )
                ]
            },
            {"deltas": ("Draft is ready.",)},
        ]
    )
    client = TestClient(
        create_app(token=TOKEN, provider=fake, state=tmp_path, gmail=gmail)
    )
    with client.websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        ws.send_json({"type": "chat", "text": "draft an email to me"})
        events = _until(ws, "permission_required")
        ask = next(e for e in events if e["type"] == "permission_required")
        assert ask["name"] == "gmail_draft"
        ws.send_json({"type": "permission", "id": "call_draft", "decision": "allow"})
        rest = _drain(ws)
    events.extend(rest)
    finished = next(e for e in events if e["type"] == "tool_finished")
    assert finished["ok"] is True
    assert finished["result"]["id"] == "draft_1"
    assert finished["result"]["sent"] is False
    assert len(gmail.drafts) == 1
    assert gmail.sends == []


def test_ws_gmail_draft_deny_creates_nothing(tmp_path):
    gmail = FakeGmail()
    fake = FakeProvider(
        steps=[
            {
                "tool_calls": [
                    ToolCall(
                        id="call_draft",
                        name="gmail_draft",
                        arguments={
                            "to": "fisher@example.com",
                            "subject": "hi",
                            "body": "hello",
                        },
                    )
                ]
            },
            {"deltas": ("Okay, I did not draft it.",)},
        ]
    )
    client = TestClient(
        create_app(token=TOKEN, provider=fake, state=tmp_path, gmail=gmail)
    )
    with client.websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        ws.send_json({"type": "chat", "text": "draft an email to me"})
        events = _until(ws, "permission_required")
        ws.send_json({"type": "permission", "id": "call_draft", "decision": "deny"})
        rest = _drain(ws)
    events.extend(rest)
    finished = next(e for e in events if e["type"] == "tool_finished")
    assert finished["ok"] is False
    assert "denied" in finished["result"]["error"]
    assert gmail.drafts == []
    assert gmail.sends == []


def test_auth_url_is_compose_not_send():
    url = authorization_url(
        client_id="cid",
        redirect_uri="http://127.0.0.1:8765/v1/gmail/callback",
        state="st",
    )
    from urllib.parse import unquote

    decoded = unquote(url)
    assert COMPOSE_SCOPE in decoded
    assert SEND_SCOPE in decoded
    assert "response_type=code" in url


def test_gmail_api_posts_to_drafts_never_send(tmp_path):
    secrets = SecretStore(tmp_path / "secrets.json")
    secrets.put("gmail", {"refresh_token": "rt", "access_token": "at"})
    http = FakeHttp({DRAFTS_URL: {"id": "r-abc"}})
    api = GmailApi(secrets, http=http, client_id="cid", client_secret="sec")
    result = api.create_draft(to="alyssa@berkeley.edu", subject="hi", body="hello")
    assert result["id"] == "r-abc"
    assert result["sent"] is False
    assert result["drafted"] is True
    assert DRAFTS_URL in http.calls[0]["url"]
    assert "send" not in http.calls[0]["url"]
    assert "send" not in str(http.calls)
    assert api.sends == []


def test_gmail_api_http_403_raises_gmail_error(tmp_path):
    secrets = SecretStore(tmp_path / "secrets.json")
    secrets.put("gmail", {"refresh_token": "rt", "access_token": "at"})
    http = FakeHttp(
        {
            DRAFTS_URL: _http_status_error(
                403,
                {
                    "error": {
                        "code": 403,
                        "message": GMAIL_API_DISABLED,
                        "status": "PERMISSION_DENIED",
                        "details": [{"reason": "accessNotConfigured"}],
                    }
                },
            )
        }
    )
    api = GmailApi(secrets, http=http, client_id="cid", client_secret="sec")
    try:
        api.create_draft(to="alyssa@berkeley.edu", subject="hi", body="hello")
        assert False, "expected GmailError"
    except Exception as exc:
        from coworker.gmail import GmailError

        assert isinstance(exc, GmailError)
        assert "Gmail API has not been used" in str(exc)
        assert "Client error" not in str(exc)


def test_gmail_draft_execute_http_error_returns_ok_false(tmp_path):
    secrets = SecretStore(tmp_path / "secrets.json")
    secrets.put("gmail", {"refresh_token": "rt", "access_token": "at"})
    http = FakeHttp(
        {DRAFTS_URL: _http_status_error(403, {"error": {"message": GMAIL_API_DISABLED}})}
    )
    api = GmailApi(secrets, http=http, client_id="cid", client_secret="sec")
    ok, result = execute(
        "gmail_draft",
        {"to": "alyssa@berkeley.edu", "subject": "hi", "body": "hello"},
        gmail=api,
    )
    assert ok is False
    assert "Gmail API has not been used" in result["error"]


def test_ws_gmail_draft_http_error_emits_tool_finished(tmp_path):
    secrets = SecretStore(tmp_path / "secrets.json")
    secrets.put("gmail", {"refresh_token": "rt", "access_token": "at"})
    http = FakeHttp(
        {DRAFTS_URL: _http_status_error(403, {"error": {"message": GMAIL_API_DISABLED}})}
    )
    gmail = GmailApi(secrets, http=http, client_id="cid", client_secret="sec")
    fake = FakeProvider(
        steps=[
            {
                "tool_calls": [
                    ToolCall(
                        id="call_draft",
                        name="gmail_draft",
                        arguments={
                            "to": "alyssa@berkeley.edu",
                            "subject": "Catching up next week",
                            "body": "Hi Alyssa,\n\nHope you're doing well!",
                        },
                    )
                ]
            },
            {"deltas": ("Gmail API is not enabled yet.",)},
        ]
    )
    client = TestClient(
        create_app(token=TOKEN, provider=fake, state=tmp_path, gmail=gmail)
    )
    with client.websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        ws.send_json({"type": "chat", "text": "draft that email to gmail"})
        events = _until(ws, "permission_required")
        ws.send_json({"type": "permission", "id": "call_draft", "decision": "allow"})
        rest = _drain(ws)
    events.extend(rest)
    types = [e["type"] for e in events]
    assert "tool_started" in types
    assert "tool_finished" in types
    finished = next(e for e in events if e["type"] == "tool_finished")
    assert finished["ok"] is False
    assert "Gmail API has not been used" in finished["result"]["error"]
    assert "error" not in types
    assert types[-1] == "turn_end"


def test_gmail_from_secrets_missing(tmp_path):
    secrets = SecretStore(tmp_path / "secrets.json")
    client = gmail_from_secrets(secrets, http=FakeHttp())
    try:
        client.create_draft(to="a@b.c", subject="s", body="b")
        assert False, "expected error"
    except Exception as exc:
        assert "not connected" in str(exc).lower()


def test_gmail_status_disconnected(tmp_path):
    res = TestClient(create_app(token=TOKEN, state=tmp_path)).get(
        "/v1/gmail", headers={TOKEN_HEADER: TOKEN}
    )
    assert res.status_code == 200
    body = res.json()
    assert body["connected"] is False
    assert body["email"] is None


def test_gmail_status_connected(tmp_path):
    app = create_app(token=TOKEN, state=tmp_path)
    app.state.secrets.put("gmail", {"refresh_token": "rt", "email": "fisher@example.com"})
    res = TestClient(app).get("/v1/gmail", headers={TOKEN_HEADER: TOKEN})
    assert res.json() == {"connected": True, "email": "fisher@example.com"}


def test_gmail_callback_error_is_escaped(tmp_path):
    app = create_app(token=TOKEN, state=tmp_path)
    res = TestClient(app).get("/v1/gmail/callback?error=%3Cscript%3Exss%3C/script%3E")
    assert res.status_code == 400
    assert "<script>" not in res.text
    assert "&lt;script&gt;" in res.text


def test_gmail_connect_requires_oauth_client(tmp_path, monkeypatch):
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    res = TestClient(create_app(token=TOKEN, state=tmp_path)).post(
        "/v1/gmail/connect", headers={TOKEN_HEADER: TOKEN}
    )
    assert res.status_code == 400
    assert "GOOGLE_OAUTH_CLIENT_ID" in res.json()["error"]


def test_gmail_connect_uses_injected_browser_opener(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "sec")
    opened_urls = []
    app = create_app(
        token=TOKEN,
        state=tmp_path,
        browser_opener=lambda url: opened_urls.append(url) or True,
    )

    res = TestClient(app).post("/v1/gmail/connect", headers={TOKEN_HEADER: TOKEN})

    assert res.status_code == 200
    assert opened_urls == [res.json()["url"]]


def test_auth_url_includes_readonly_and_compose():
    url = authorization_url(
        client_id="cid",
        redirect_uri="http://127.0.0.1:8765/v1/gmail/callback",
        state="st",
    )
    decoded = unquote(url)
    assert COMPOSE_SCOPE in decoded
    assert READ_SCOPE in decoded
    assert SEND_SCOPE in decoded
    assert DRIVE_SCOPE in decoded
    assert CALENDAR_SCOPE in decoded
    assert "include_granted_scopes=true" in url


def test_callback_stores_email_and_scopes(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "sec")
    http = FakeHttp(
        {
            "https://oauth2.googleapis.com/token": {
                "access_token": "at",
                "refresh_token": "rt",
                "scope": f"{COMPOSE_SCOPE} {READ_SCOPE} https://www.googleapis.com/auth/userinfo.email",
            },
            "https://www.googleapis.com/oauth2/v2/userinfo": {
                "email": "fisher@example.com"
            },
        }
    )
    app = create_app(token=TOKEN, state=tmp_path, http=http)
    app.state.oauth_state = "st"
    res = TestClient(app).get("/v1/gmail/callback?code=abc&state=st")
    assert res.status_code == 200
    assert b"Gmail connected" in res.content
    profile = load_google(app.state.secrets)
    assert profile["email"] == "fisher@example.com"
    assert profile["refresh_token"] == "rt"
    assert READ_SCOPE in profile["scopes"]
    assert COMPOSE_SCOPE in profile["scopes"]
    status = TestClient(app).get("/v1/gmail", headers={TOKEN_HEADER: TOKEN}).json()
    assert status == {"connected": True, "email": "fisher@example.com"}
    assert "rt" not in str(status)
    assert "at" not in str(status)


def test_callback_merges_drive_scope_into_existing_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "sec")
    http = FakeHttp(
        {
            "https://oauth2.googleapis.com/token": {
                "access_token": "at2",
                "refresh_token": "rt",
                "scope": f"{COMPOSE_SCOPE} {READ_SCOPE} {DRIVE_SCOPE} https://www.googleapis.com/auth/userinfo.email",
            },
            "https://www.googleapis.com/oauth2/v2/userinfo": {
                "email": "fisher@example.com"
            },
        }
    )
    app = create_app(token=TOKEN, state=tmp_path, http=http)
    save_google(
        app.state.secrets,
        {
            "refresh_token": "rt",
            "access_token": "old",
            "email": "fisher@example.com",
            "scopes": [COMPOSE_SCOPE, READ_SCOPE],
        },
    )
    app.state.oauth_state = "st"
    res = TestClient(app).get("/v1/gmail/callback?code=abc&state=st")
    assert res.status_code == 200
    profile = load_google(app.state.secrets)
    assert COMPOSE_SCOPE in profile["scopes"]
    assert READ_SCOPE in profile["scopes"]
    assert DRIVE_SCOPE in profile["scopes"]


def test_disconnect_deletes_google_and_gmail_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "sec")
    app = create_app(token=TOKEN, state=tmp_path)
    save_google(
        app.state.secrets,
        {"refresh_token": "rt", "email": "fisher@example.com", "scopes": [COMPOSE_SCOPE]},
    )
    TestClient(app).post("/v1/gmail/disconnect", headers={TOKEN_HEADER: TOKEN})
    assert load_google(app.state.secrets) == {}


def test_fake_http_get_matches_path_without_query():
    http = FakeHttp({"https://example.test/v1/items": {"ok": True}})
    assert http.get("https://example.test/v1/items?q=hi&maxResults=5") == {"ok": True}


def test_fake_http_get_returns_string_body():
    http = FakeHttp({"https://example.test/export": "plain text body"})
    assert http.get("https://example.test/export?alt=media") == "plain text body"


def test_gmail_draft_refreshes_on_401(tmp_path):
    secrets = SecretStore(tmp_path / "secrets.json")
    save_google(
        secrets,
        {
            "refresh_token": "rt",
            "access_token": "stale",
            "email": "fisher@example.com",
            "scopes": [COMPOSE_SCOPE, READ_SCOPE],
        },
    )

    class Once401(FakeHttp):
        def post(self, url, **kwargs):
            self.calls.append({"url": url, **kwargs})
            if url == DRAFTS_URL and not any(
                c["url"] == "https://oauth2.googleapis.com/token" for c in self.calls[:-1]
            ):
                raise HttpError(401, url)
            if url == "https://oauth2.googleapis.com/token":
                return {"access_token": "fresh"}
            if url == DRAFTS_URL:
                return {"id": "r-new"}
            raise RuntimeError(url)

    api = GmailApi(secrets, http=Once401(), client_id="cid", client_secret="sec")
    result = api.create_draft(to="a@b.c", subject="s", body="b")
    assert result["id"] == "r-new"
    assert load_google(secrets)["access_token"] == "fresh"


MESSAGES_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages"


def test_gmail_search_is_auto_and_draft_still_asks():
    from coworker.permissions import decide

    assert decide("gmail_search").needs_user is False
    assert decide("gmail_read").needs_user is False
    assert decide("gmail_draft").needs_user is True
    assert decide("gmail_send").allowed is False
    assert decide("gmail_send").needs_user is True


def test_gmail_search_execute_fake_http(tmp_path):
    secrets = SecretStore(tmp_path / "secrets.json")
    save_google(secrets, {"refresh_token": "rt", "access_token": "at", "scopes": [READ_SCOPE]})
    http = FakeHttp(
        {
            MESSAGES_URL: {"messages": [{"id": "m1", "threadId": "t1"}]},
            f"{MESSAGES_URL}/m1": {
                "id": "m1",
                "threadId": "t1",
                "payload": {
                    "headers": [
                        {"name": "From", "value": "Alyssa <a@berkeley.edu>"},
                        {"name": "Subject", "value": "hi"},
                        {"name": "Date", "value": "Mon, 24 Aug 2026 09:00:00 -0700"},
                    ]
                },
            },
        }
    )
    api = GmailApi(secrets, http=http, client_id="cid", client_secret="sec")
    out = api.search(query="from:alyssa", max_results=5)
    assert out["messages"][0]["subject"] == "hi"
    assert out["messages"][0]["from"] == "Alyssa <a@berkeley.edu>"
    assert "send" not in str(http.calls)


def test_gmail_read_execute_fake_http(tmp_path):
    import base64

    secrets = SecretStore(tmp_path / "secrets.json")
    save_google(secrets, {"refresh_token": "rt", "access_token": "at", "scopes": [READ_SCOPE]})
    http = FakeHttp(
        {
            f"{MESSAGES_URL}/m1": {
                "id": "m1",
                "snippet": "hello",
                "payload": {
                    "mimeType": "text/plain",
                    "body": {"data": base64.urlsafe_b64encode(b"hello body").decode().rstrip("=")},
                    "headers": [{"name": "Subject", "value": "hi"}],
                },
            }
        }
    )
    api = GmailApi(secrets, http=http, client_id="cid", client_secret="sec")
    out = api.read(message_id="m1")
    assert "hello body" in out["body"]
    assert out["sent"] is not True


def test_ws_gmail_search_does_not_ask(tmp_path):
    http = FakeHttp(
        {
            MESSAGES_URL: {"messages": [{"id": "m1", "threadId": "t1"}]},
            f"{MESSAGES_URL}/m1": {
                "id": "m1",
                "payload": {"headers": [{"name": "Subject", "value": "hi"}]},
            },
        }
    )
    fake = FakeProvider(
        steps=[
            {"tool_calls": [ToolCall(id="c1", name="gmail_search", arguments={"query": "alyssa"})]},
            {"deltas": ("found one",)},
        ]
    )
    app = create_app(token=TOKEN, provider=fake, state=tmp_path, http=http)
    save_google(app.state.secrets, {"refresh_token": "rt", "access_token": "at", "scopes": [READ_SCOPE]})
    app.state.gmail = GmailApi(app.state.secrets, http=http, client_id="cid", client_secret="sec")
    client = TestClient(app)
    with client.websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        ws.send_json({"type": "chat", "text": "search mail for Alyssa"})
        events = _drain(ws)
    assert "permission_required" not in [e["type"] for e in events]
    finished = next(e for e in events if e["type"] == "tool_finished")
    assert finished["ok"] is True
    assert finished["result"]["messages"][0]["subject"] == "hi"
