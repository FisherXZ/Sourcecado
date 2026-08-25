from urllib.parse import unquote

from fastapi.testclient import TestClient

from coworker.apollo import FakeHttp
from coworker.connectors.google_oauth import (
    CALENDAR_SCOPE,
    COMPOSE_SCOPE,
    DRIVE_SCOPE,
    READ_SCOPE,
    SEND_SCOPE,
    save_google,
)
from coworker.drive import DriveApi
from coworker.permissions import decide
from coworker.provider import FakeProvider, ToolCall
from coworker.secrets import SecretStore
from coworker.server import TOKEN_HEADER, create_app

TOKEN = "test-token-drive"
FILES_URL = "https://www.googleapis.com/drive/v3/files"


def test_drive_tools_are_auto():
    assert decide("drive_search").needs_user is False
    assert decide("drive_read").needs_user is False


def test_drive_search_fake_http(tmp_path):
    secrets = SecretStore(tmp_path / "secrets.json")
    save_google(secrets, {"refresh_token": "rt", "access_token": "at", "scopes": [DRIVE_SCOPE]})
    http = FakeHttp(
        {
            FILES_URL: {
                "files": [
                    {
                        "id": "f1",
                        "name": "Q3 plan",
                        "mimeType": "text/plain",
                        "modifiedTime": "2026-08-01T00:00:00Z",
                    }
                ]
            }
        }
    )
    out = DriveApi(secrets, http=http, client_id="cid", client_secret="sec").search("Q3 plan")
    assert out["files"][0]["id"] == "f1"
    assert "/upload" not in str(http.calls)


def test_drive_read_exports_google_doc(tmp_path):
    secrets = SecretStore(tmp_path / "secrets.json")
    save_google(secrets, {"refresh_token": "rt", "access_token": "at", "scopes": [DRIVE_SCOPE]})
    http = FakeHttp(
        {
            f"{FILES_URL}/f1": {"id": "f1", "name": "doc", "mimeType": "application/vnd.google-apps.document"},
            f"{FILES_URL}/f1/export": "plain text body",
        }
    )
    out = DriveApi(secrets, http=http, client_id="cid", client_secret="sec").read("f1")
    assert out["content"] == "plain text body"
    assert out["truncated"] is False


def test_drive_connect_url_requests_readonly(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "sec")
    res = TestClient(create_app(token=TOKEN, state=tmp_path)).post(
        "/v1/connectors/drive/connect", headers={TOKEN_HEADER: TOKEN}
    )
    assert res.status_code == 200
    decoded = unquote(res.json()["url"])
    assert DRIVE_SCOPE in decoded
    assert COMPOSE_SCOPE in decoded
    assert READ_SCOPE in decoded
    assert SEND_SCOPE in decoded
    assert CALENDAR_SCOPE in decoded
    assert "drive.file" not in decoded


def test_ws_drive_search_does_not_ask(tmp_path):
    http = FakeHttp(
        {FILES_URL: {"files": [{"id": "f1", "name": "Q3 plan", "mimeType": "text/plain"}]}}
    )
    fake = FakeProvider(
        steps=[
            {"tool_calls": [ToolCall(id="c1", name="drive_search", arguments={"query": "Q3"})]},
            {"deltas": ("found it",)},
        ]
    )
    app = create_app(token=TOKEN, provider=fake, state=tmp_path, http=http)
    save_google(app.state.secrets, {"refresh_token": "rt", "access_token": "at", "scopes": [DRIVE_SCOPE]})
    app.state.drive = DriveApi(app.state.secrets, http=http, client_id="cid", client_secret="sec")
    with TestClient(app).websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        ws.send_json({"type": "chat", "text": "find Q3 plan"})
        events = []
        while True:
            ev = ws.receive_json()
            events.append(ev)
            if ev["type"] in ("turn_end", "error"):
                break
    assert "permission_required" not in [e["type"] for e in events]
    assert next(e for e in events if e["type"] == "tool_finished")["ok"] is True
