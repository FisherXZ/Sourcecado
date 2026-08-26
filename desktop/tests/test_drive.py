import json
from urllib.parse import unquote

import pytest
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


@pytest.mark.parametrize(
    "source",
    [
        "APOLLO_API_KEY=apollo_live_1234567890",
        "access token: ya29.example_access_token_123456",
        "secret = internal_secret_value_123456",
        "password: example_password_value_123456",
        "password: abc",
        '\"private_key\": \"private_key_material_1234567890\"',
        '\"password\": \"P@ssw0rd!with:punctuation#123\"',
    ],
)
def test_drive_read_redacts_credential_assignments(tmp_path, source):
    secrets = SecretStore(tmp_path / "secrets.json")
    save_google(secrets, {"refresh_token": "rt", "access_token": "at", "scopes": [DRIVE_SCOPE]})
    http = FakeHttp(
        {
            f"{FILES_URL}/f1": {
                "id": "f1",
                "name": "Sourcing SOP",
                "mimeType": "application/vnd.google-apps.document",
            },
            f"{FILES_URL}/f1/export": f"Setup\n{source}\nDone",
        }
    )

    out = DriveApi(secrets, http=http, client_id="cid", client_secret="sec").read("f1")

    assert source not in out["content"]
    assert "[REDACTED]" in out["content"]
    assert out["sensitive_content_redacted"] is True
    assert out["redaction_count"] == 1


def test_drive_read_redacts_private_key_blocks(tmp_path):
    secrets = SecretStore(tmp_path / "secrets.json")
    save_google(secrets, {"refresh_token": "rt", "access_token": "at", "scopes": [DRIVE_SCOPE]})
    private_key = (
        "-----BEGIN PRIVATE KEY-----\n"
        "private-key-material-that-must-never-leave-the-boundary\n"
        "-----END PRIVATE KEY-----"
    )
    http = FakeHttp(
        {
            f"{FILES_URL}/f1": {
                "id": "f1",
                "name": "Setup",
                "mimeType": "application/vnd.google-apps.document",
            },
            f"{FILES_URL}/f1/export": private_key,
        }
    )

    out = DriveApi(secrets, http=http, client_id="cid", client_secret="sec").read("f1")

    assert private_key not in out["content"]
    assert out["content"] == "[REDACTED PRIVATE KEY]"
    assert out["redaction_count"] == 1


def test_drive_read_does_not_redact_ordinary_security_prose(tmp_path):
    secrets = SecretStore(tmp_path / "secrets.json")
    save_google(secrets, {"refresh_token": "rt", "access_token": "at", "scopes": [DRIVE_SCOPE]})
    prose = "Keep the API key in the local secret store. Password rotation is required."
    http = FakeHttp(
        {
            f"{FILES_URL}/f1": {
                "id": "f1",
                "name": "Security guide",
                "mimeType": "application/vnd.google-apps.document",
            },
            f"{FILES_URL}/f1/export": prose,
        }
    )

    out = DriveApi(secrets, http=http, client_id="cid", client_secret="sec").read("f1")

    assert out["content"] == prose
    assert out["sensitive_content_redacted"] is False
    assert out["redaction_count"] == 0


def test_drive_read_flags_mismatched_legal_template_as_not_ready(tmp_path):
    secrets = SecretStore(tmp_path / "secrets.json")
    save_google(secrets, {"refresh_token": "rt", "access_token": "at", "scopes": [DRIVE_SCOPE]})
    http = FakeHttp(
        {
            f"{FILES_URL}/f1": {
                "id": "f1",
                "name": "Codeology NDA Template",
                "mimeType": "application/vnd.google-apps.document",
            },
            f"{FILES_URL}/f1/export": (
                "NONDISCLOSURE AGREEMENT between De Beers and Berkeley Consulting."
            ),
        }
    )

    out = DriveApi(secrets, http=http, client_id="cid", client_secret="sec").read("f1")

    assert out["source_safety"] == {
        "legal_document": True,
        "ready_to_use": False,
        "status": "party_mismatch",
        "reasons": ["unexpected_recipient_berkeley_consulting"],
    }


def test_drive_read_does_not_classify_ordinary_agreement_prose_as_legal(tmp_path):
    secrets = SecretStore(tmp_path / "secrets.json")
    save_google(secrets, {"refresh_token": "rt", "access_token": "at", "scopes": [DRIVE_SCOPE]})
    http = FakeHttp(
        {
            f"{FILES_URL}/f1": {
                "id": "f1",
                "name": "Meeting notes",
                "mimeType": "application/vnd.google-apps.document",
            },
            f"{FILES_URL}/f1/export": "We reached agreement on the project dates.",
        }
    )

    out = DriveApi(secrets, http=http, client_id="cid", client_secret="sec").read("f1")

    assert "source_safety" not in out


def test_ws_drive_read_redacts_before_provider_events_and_persistence(tmp_path):
    raw_secret = "apollo_live_secret_1234567890"
    http = FakeHttp(
        {
            f"{FILES_URL}/f1": {
                "id": "f1",
                "name": "Sourcing SOP",
                "mimeType": "application/vnd.google-apps.document",
            },
            f"{FILES_URL}/f1/export": f"APOLLO_API_KEY={raw_secret}",
        }
    )
    fake = FakeProvider(
        steps=[
            {"tool_calls": [ToolCall(id="c1", name="drive_read", arguments={"file_id": "f1"})]},
            {"deltas": ("The credential was redacted.",)},
        ]
    )
    app = create_app(token=TOKEN, provider=fake, state=tmp_path, http=http)
    save_google(app.state.secrets, {"refresh_token": "rt", "access_token": "at", "scopes": [DRIVE_SCOPE]})
    app.state.drive = DriveApi(app.state.secrets, http=http, client_id="cid", client_secret="sec")
    sid = app.state.store.open_session_id()

    with TestClient(app).websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        ws.send_json({"type": "chat", "text": "check the sourcing SOP", "session_id": sid})
        events = []
        while True:
            event = ws.receive_json()
            events.append(event)
            if event["type"] in ("turn_end", "error"):
                break

    assert raw_secret not in json.dumps(events)
    assert raw_secret not in json.dumps(fake.calls)
    assert raw_secret not in json.dumps(app.state.store.load_events(sid))
    finished = next(event for event in events if event["type"] == "tool_finished")
    assert finished["result"]["content"] == "APOLLO_API_KEY=[REDACTED]"
    assert finished["result"]["sensitive_content_redacted"] is True


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
