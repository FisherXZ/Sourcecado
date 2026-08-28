from urllib.parse import unquote

from fastapi.testclient import TestClient

from coworker.apollo import FakeHttp
from coworker.calendar import CalendarApi
from coworker.connectors.google_oauth import (
    CALENDAR_EVENTS_SCOPE,
    CALENDAR_SCOPE,
    COMPOSE_SCOPE,
    DRIVE_SCOPE,
    READ_SCOPE,
    SEND_SCOPE,
    save_google,
)
from coworker.permissions import decide
from coworker.provider import FakeProvider, ToolCall
from coworker.secrets import SecretStore
from coworker.server import TOKEN_HEADER, create_app

TOKEN = "test-token-cal"
EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"


def test_calendar_connect_url_requests_full_google_grant(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "sec")
    opened_urls = []
    res = TestClient(
        create_app(
            token=TOKEN,
            state=tmp_path,
            browser_opener=lambda url: opened_urls.append(url) or True,
        )
    ).post(
        "/v1/connectors/calendar/connect", headers={TOKEN_HEADER: TOKEN}
    )
    assert res.status_code == 200
    decoded = unquote(res.json()["url"])
    assert opened_urls == [res.json()["url"]]
    assert CALENDAR_SCOPE in decoded
    assert COMPOSE_SCOPE in decoded
    assert READ_SCOPE in decoded
    assert SEND_SCOPE in decoded
    assert DRIVE_SCOPE in decoded


def test_calendar_permissions():
    assert decide("calendar_list").needs_user is False
    assert decide("calendar_create").needs_user is True
    assert decide("calendar_update").needs_user is True
    assert decide("calendar_delete").allowed is False


def test_calendar_list_fake_http(tmp_path):
    secrets = SecretStore(tmp_path / "secrets.json")
    save_google(secrets, {"refresh_token": "rt", "access_token": "at", "scopes": [CALENDAR_SCOPE]})
    http = FakeHttp(
        {
            EVENTS_URL: {
                "items": [
                    {
                        "id": "e1",
                        "summary": "standup",
                        "start": {"dateTime": "2026-08-24T09:00:00-07:00"},
                        "end": {"dateTime": "2026-08-24T09:30:00-07:00"},
                        "attendees": [
                            {
                                "email": "ada@example.test",
                                "displayName": "Ada Lovelace",
                            }
                        ],
                        "htmlLink": "https://calendar.test/e1",
                    }
                ]
            }
        }
    )
    out = CalendarApi(secrets, http=http, client_id="cid", client_secret="sec").list_events()
    assert out["events"][0]["summary"] == "standup"
    assert out["events"][0]["attendees"] == [
        {"email": "ada@example.test", "displayName": "Ada Lovelace"}
    ]
    assert out["events"][0]["htmlLink"] == "https://calendar.test/e1"
    assert "delete" not in str(http.calls)


def test_calendar_events_scope_still_lists(tmp_path):
    secrets = SecretStore(tmp_path / "secrets.json")
    save_google(secrets, {"refresh_token": "rt", "access_token": "at", "scopes": [CALENDAR_EVENTS_SCOPE]})
    http = FakeHttp({EVENTS_URL: {"items": [{"id": "e1", "summary": "ok"}]}})
    out = CalendarApi(secrets, http=http, client_id="cid", client_secret="sec").list_events()
    assert out["events"][0]["summary"] == "ok"


def test_calendar_list_defaults_time_min_to_now(tmp_path):
    secrets = SecretStore(tmp_path / "secrets.json")
    save_google(secrets, {"refresh_token": "rt", "access_token": "at", "scopes": [CALENDAR_SCOPE]})
    http = FakeHttp({EVENTS_URL: {"items": []}})
    CalendarApi(secrets, http=http, client_id="cid", client_secret="sec").list_events()
    params = http.calls[0]["params"]
    assert params["orderBy"] == "startTime"
    assert "timeMin" in params
    assert params["timeMin"] >= "2026-01-01"


def test_calendar_create_posts_event(tmp_path):
    secrets = SecretStore(tmp_path / "secrets.json")
    save_google(secrets, {"refresh_token": "rt", "access_token": "at", "scopes": [CALENDAR_SCOPE]})
    http = FakeHttp({EVENTS_URL: {"id": "e2", "summary": "Alyssa", "htmlLink": "https://cal"}})
    out = CalendarApi(secrets, http=http, client_id="cid", client_secret="sec").create(
        summary="Alyssa",
        start="2026-08-25T10:00:00",
        end="2026-08-25T10:30:00",
    )
    assert out["id"] == "e2"
    posted = http.calls[0]["json"]
    assert posted["start"]["timeZone"] == "America/Los_Angeles"
    assert http.calls[0]["url"] == EVENTS_URL


def test_calendar_update_asks():
    assert decide("calendar_update").needs_user is True
    assert decide("calendar_update").allowed is False


def test_calendar_update_patches_only_provided_fields(tmp_path):
    secrets = SecretStore(tmp_path / "secrets.json")
    save_google(secrets, {"refresh_token": "rt", "access_token": "at", "scopes": [CALENDAR_SCOPE]})
    http = FakeHttp({f"{EVENTS_URL}/e2": {"id": "e2", "summary": "Alyssa v2"}})
    out = CalendarApi(secrets, http=http, client_id="cid", client_secret="sec").update(
        event_id="e2",
        summary="Alyssa v2",
    )
    assert out["id"] == "e2"
    assert http.calls[0]["json"] == {"summary": "Alyssa v2"}
    assert "start" not in http.calls[0]["json"]
    assert http.calls[0]["method"] == "PATCH"


def test_ws_calendar_create_asks_and_deny_writes_nothing(tmp_path):
    http = FakeHttp({EVENTS_URL: {"id": "e2", "summary": "Alyssa"}})
    fake = FakeProvider(
        steps=[
            {
                "tool_calls": [
                    ToolCall(
                        id="c1",
                        name="calendar_create",
                        arguments={
                            "summary": "Alyssa",
                            "start": "2026-08-25T10:00:00",
                            "end": "2026-08-25T10:30:00",
                        },
                    )
                ]
            },
            {"deltas": ("okay",)},
        ]
    )
    app = create_app(token=TOKEN, provider=fake, state=tmp_path, http=http)
    save_google(app.state.secrets, {"refresh_token": "rt", "access_token": "at", "scopes": [CALENDAR_SCOPE]})
    with TestClient(app).websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        ws.send_json({"type": "chat", "text": "put Alyssa on the calendar"})
        events = []
        while True:
            ev = ws.receive_json()
            events.append(ev)
            if ev["type"] == "permission_required":
                ws.send_json({"type": "permission", "id": "c1", "decision": "deny"})
            if ev["type"] in ("turn_end", "error"):
                break
    assert http.calls == []
