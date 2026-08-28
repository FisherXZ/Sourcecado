from fastapi.testclient import TestClient

from coworker.mcp import FakeMcp
from coworker.provider import FakeProvider
from coworker.server import TOKEN_HEADER, create_app


TOKEN = "meeting-evidence-token"


class ReadOnlyCalendar:
    def __init__(self, events=None, error=None):
        self.events = list(events or [])
        self.error = error
        self.calls = []

    def list_events(self, **kwargs):
        self.calls.append({"operation": "list", **kwargs})
        if self.error:
            raise self.error
        return {"events": self.events}


def _auth():
    return {TOKEN_HEADER: TOKEN}


def _keep(app, *, apollo_id, first, last, email=None):
    person = app.state.people.keep_from_apollo(
        apollo_id=apollo_id,
        first_name=first,
        last_name_obfuscated=last,
        title="Founder",
        company="Analytic",
    )
    if email:
        app.state.people.apply_enrichment(person["person_id"], email=email)
    return app.state.people.get(person["person_id"])


def test_person_meeting_refresh_attach_reject_and_brief_contract(tmp_path):
    calendar = ReadOnlyCalendar(
        [
            {
                "id": "cal-api",
                "summary": "Calendar review",
                "start": {"dateTime": "2026-09-01T10:00:00Z"},
                "end": {"dateTime": "2026-09-01T10:30:00Z"},
                "attendees": [
                    {"email": "ada@example.test", "displayName": "Ada Lovelace"}
                ],
                "htmlLink": "https://calendar.test/cal-api",
            }
        ]
    )
    mcp = FakeMcp(
        [
            {
                "name": "mcp__granola__list_meetings",
                "handler": lambda _args: {
                    "meetings": [
                        {
                            "id": "granola-api",
                            "title": "Granola review",
                            "startTime": "2026-09-02T10:00:00Z",
                            "participants": [{"name": "Ada Lovelace"}],
                            "notes": "PRIVATE UNTRUSTED NOTES",
                            "url": "https://granola.test/granola-api",
                        }
                    ]
                },
            }
        ]
    )
    app = create_app(token=TOKEN, provider=FakeProvider(), state=tmp_path, mcp=mcp)
    app.state.calendar = calendar
    ada = _keep(
        app,
        apollo_id="ada",
        first="Ada",
        last="Lovelace",
        email="ada@example.test",
    )
    client = TestClient(app)

    refresh = client.post(
        f"/v1/people/{ada['person_id']}/meetings/refresh", headers=_auth()
    )
    person = client.get(f"/v1/people/{ada['person_id']}", headers=_auth()).json()

    assert refresh.status_code == 200
    assert refresh.json()["sources"] == {
        "calendar": {"status": "ok", "records": 1},
        "granola": {"status": "ok", "records": 1},
    }
    assert len(person["meeting_evidence"]["attached"]) == 1
    assert len(person["meeting_evidence"]["proposed"]) == 1
    assert "meeting notes" in person["brief"]["missing"]
    proposal = person["meeting_evidence"]["proposed"][0]

    attached = client.post(
        f"/v1/people/{ada['person_id']}/meetings/{proposal['evidence_id']}/attach",
        headers=_auth(),
    )

    assert attached.status_code == 200
    assert attached.json()["meeting"]["status"] == "attached"
    assert len(attached.json()["meeting_evidence"]["attached"]) == 2
    assert calendar.calls == [{"operation": "list", "max_results": 100}]
    assert mcp.calls == [
        {"name": "mcp__granola__list_meetings", "arguments": {}}
    ]

    grace = _keep(app, apollo_id="grace", first="Grace", last="Hopper")
    app.state.mcp = FakeMcp(
        [
            {
                "name": "mcp__granola__list_meetings",
                "handler": lambda _args: {
                    "meetings": [
                        {
                            "id": "granola-grace",
                            "title": "Grace review",
                            "participants": [{"name": "Grace Hopper"}],
                        }
                    ]
                },
            }
        ]
    )
    client.post(f"/v1/people/{grace['person_id']}/meetings/refresh", headers=_auth())
    grace_view = client.get(
        f"/v1/people/{grace['person_id']}", headers=_auth()
    ).json()
    grace_proposal = grace_view["meeting_evidence"]["proposed"][0]
    rejected = client.post(
        f"/v1/people/{grace['person_id']}/meetings/{grace_proposal['evidence_id']}/reject",
        headers=_auth(),
    )
    assert rejected.status_code == 200
    assert len(rejected.json()["meeting_evidence"]["rejected"]) == 1
    assert app.state.people.timeline(grace["person_id"]) == []


def test_refresh_reports_missing_calendar_and_granola_independently(tmp_path):
    mcp = FakeMcp(
        [
            {
                "name": "mcp__granola__list_meetings",
                "handler": lambda _args: {
                    "meetings": [
                        {
                            "id": "granola-ok",
                            "title": "Useful meeting",
                            "participants": [
                                {
                                    "email": "ada@example.test",
                                    "name": "Ada Lovelace",
                                }
                            ],
                            "notes": "notes",
                        }
                    ]
                },
            }
        ]
    )
    app = create_app(token=TOKEN, provider=FakeProvider(), state=tmp_path, mcp=mcp)
    app.state.calendar = None
    ada = _keep(
        app,
        apollo_id="ada",
        first="Ada",
        last="Lovelace",
        email="ada@example.test",
    )

    response = TestClient(app).post(
        f"/v1/people/{ada['person_id']}/meetings/refresh", headers=_auth()
    )

    assert response.status_code == 200
    assert response.json()["sources"]["calendar"] == {
        "status": "failed",
        "error": "unavailable",
    }
    assert response.json()["sources"]["granola"] == {
        "status": "ok",
        "records": 1,
    }


def test_meeting_api_survives_restart_and_never_crosses_people(tmp_path):
    app = create_app(token=TOKEN, provider=FakeProvider(), state=tmp_path, mcp=FakeMcp())
    app.state.calendar = ReadOnlyCalendar(
        [
            {
                "id": "cal-restart",
                "summary": "Ada only",
                "attendees": [{"email": "ada@example.test", "displayName": "Ada Lovelace"}],
            }
        ]
    )
    ada = _keep(
        app,
        apollo_id="ada",
        first="Ada",
        last="Lovelace",
        email="ada@example.test",
    )
    grace = _keep(
        app,
        apollo_id="grace",
        first="Grace",
        last="Hopper",
        email="grace@example.test",
    )
    TestClient(app).post(
        f"/v1/people/{ada['person_id']}/meetings/refresh", headers=_auth()
    )

    restarted = create_app(
        token=TOKEN, provider=FakeProvider(), state=tmp_path, mcp=FakeMcp()
    )
    client = TestClient(restarted)
    ada_file = client.get(f"/v1/people/{ada['person_id']}", headers=_auth()).json()
    grace_file = client.get(
        f"/v1/people/{grace['person_id']}", headers=_auth()
    ).json()

    assert len(ada_file["meeting_evidence"]["attached"]) == 1
    assert grace_file["meeting_evidence"]["attached"] == []


class _ClosedResourceError(Exception):
    pass


class _OpenMcpSession:
    def __init__(self, *_args, **_kwargs):
        self._open = False

    async def __aenter__(self):
        self._open = True
        return self

    async def __aexit__(self, *_exc):
        self._open = False

    async def initialize(self):
        if not self._open:
            raise _ClosedResourceError(
                "Attempted to send a request from a closed resource."
            )

    async def call_tool(self, _name, _arguments):
        if not self._open:
            raise _ClosedResourceError(
                "Attempted to send a request from a closed resource."
            )

        class Result:
            structuredContent = {
                "meetings": [
                    {
                        "id": "granola-live-1",
                        "title": "Partner sync",
                        "startTime": "2026-09-02T10:00:00Z",
                        "participants": [
                            {
                                "email": "ada@example.test",
                                "name": "Ada Lovelace",
                            }
                        ],
                        "notes": "Discussed hiring",
                        "url": "https://granola.test/granola-live-1",
                    }
                ]
            }
            content = []

        return Result()


class _AsyncCM:
    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *_exc):
        return None


def test_live_granola_refresh_attaches_notes_from_an_open_mcp_session(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("mcp.ClientSession", _OpenMcpSession)
    monkeypatch.setattr(
        "mcp.client.streamable_http.streamable_http_client",
        lambda *_args, **_kwargs: _AsyncCM((object(), object(), None)),
    )
    monkeypatch.setattr(
        "mcp.shared._httpx_utils.create_mcp_http_client",
        lambda *_args, **_kwargs: _AsyncCM(object()),
    )
    calendar = ReadOnlyCalendar(
        [
            {
                "id": "cal-live",
                "summary": "Calendar review",
                "start": {"dateTime": "2026-09-01T10:00:00Z"},
                "end": {"dateTime": "2026-09-01T10:30:00Z"},
                "attendees": [
                    {
                        "email": "ada@example.test",
                        "displayName": "Ada Lovelace",
                    }
                ],
                "htmlLink": "https://calendar.test/cal-live",
            }
        ]
    )
    app = create_app(token=TOKEN, provider=FakeProvider(), state=tmp_path)
    app.state.calendar = calendar
    app.state.secrets.put("mcp-oauth:granola", {"access_token": "at"})
    ada = _keep(
        app,
        apollo_id="ada-live",
        first="Ada",
        last="Lovelace",
        email="ada@example.test",
    )

    refresh = TestClient(app).post(
        f"/v1/people/{ada['person_id']}/meetings/refresh", headers=_auth()
    )

    assert refresh.status_code == 200
    body = refresh.json()
    assert body["sources"]["calendar"] == {"status": "ok", "records": 1}
    assert body["sources"]["granola"] == {"status": "ok", "records": 1}
    granola = next(
        meeting
        for meeting in body["meeting_evidence"]["attached"]
        if meeting["provider"] == "granola"
    )
    assert granola["title"] == "Partner sync"
    assert granola["notes"] == "Discussed hiring"
    assert any("Discussed hiring" in line for line in body["brief"]["learned"])
