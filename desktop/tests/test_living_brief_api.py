"""The living brief and the successor handoff over HTTP."""

from __future__ import annotations

from fastapi.testclient import TestClient

from coworker.drive_evidence import attach as attach_drive_evidence
from coworker.provider import FakeProvider
from coworker.server import TOKEN_HEADER, create_app

TOKEN = "test-token-living-brief"
HEADERS = {TOKEN_HEADER: TOKEN}


def _app(tmp_path):
    return create_app(token=TOKEN, state=tmp_path, provider=FakeProvider())


def _person(app, **kwargs):
    fields = {
        "apollo_id": "ada",
        "first_name": "Ada",
        "last_name_obfuscated": "Lovelace",
        "title": "Founder",
        "company": "Analytic",
        "target": "research dinner",
    }
    fields.update(kwargs)
    return app.state.people.keep_from_apollo(**fields)


def _drive(app, person_id, *, file_id, name, **raw):
    attach_drive_evidence(
        app.state.people,
        person_id,
        kind="search_result",
        raw={
            "id": file_id,
            "name": name,
            "modifiedTime": "2026-08-01T10:00:00+00:00",
            "status": "read",
            **raw,
        },
        actor="director",
        rationale_summary="Director attached Drive evidence.",
    )


def test_person_route_exposes_the_whole_living_brief(tmp_path):
    app = _app(tmp_path)
    person = _person(app)
    pid = person["person_id"]
    app.state.people.append_event(
        pid,
        source="gmail",
        kind="send",
        summary="Sent draft draft-1",
        payload={"sent": True, "to": "ada@analytic.example"},
    )
    app.state.people.upsert_attachment(
        pid,
        record_type="artifact",
        fields={"title": "Dinner invitation draft"},
        idempotency_key="artifact:invite",
        actor="director",
        rationale_summary="Director filed the invitation draft.",
    )
    _drive(app, pid, file_id="drive-1", name="Fall sourcing masterdoc")

    body = TestClient(app).get(f"/v1/people/{pid}", headers=HEADERS).json()
    brief = body["brief"]

    assert brief["identity"]["text"].startswith("Ada Lovelace")
    assert brief["target"]["text"] == "research dinner"
    assert brief["last_contact"]["direction"] == "outbound"
    assert brief["outcome"]["text"]
    assert any("Dinner invitation draft" in item["text"] for item in brief["artifacts"])
    assert brief["gaps"]
    assert brief["source_refs"]
    assert brief["restricted_source_count"] == 0
    assert brief["partial"] is False
    assert brief["handoff"]["generated"] is True
    assert any(
        "Fall sourcing masterdoc" in claim["text"] for claim in brief["claims"]
    )
    # The pre-existing keys still answer, so nothing reading the old shape breaks.
    assert brief["who"].startswith("Ada Lovelace")
    assert brief["why"] == "research dinner"
    assert "gmail" in brief["sources"]


def test_a_restricted_source_is_counted_never_named_over_http(tmp_path):
    app = _app(tmp_path)
    person = _person(app)
    pid = person["person_id"]
    _drive(app, pid, file_id="drive-open", name="Fall sourcing masterdoc")
    _drive(
        app,
        pid,
        file_id="drive-secret",
        name="Board compensation memo",
        sensitive_content_redacted=True,
    )

    res = TestClient(app).get(f"/v1/people/{pid}", headers=HEADERS)
    raw = res.text

    # Non-vacuity: the response really carries this person's brief.
    assert res.json()["brief"]["claims"]
    assert "Fall sourcing masterdoc" in raw
    # And the restricted sibling is a count, not a title.
    assert res.json()["brief"]["restricted_source_count"] == 1
    assert "Board compensation memo" not in raw
    assert "drive-secret" not in raw


def test_a_failed_meeting_refresh_returns_a_partial_brief_not_an_empty_one(tmp_path):
    app = _app(tmp_path)
    person = _person(app)
    pid = person["person_id"]
    _drive(app, pid, file_id="drive-1", name="Fall sourcing masterdoc")
    app.state.calendar = None  # Calendar is unreachable; Granola is not wired.

    res = TestClient(app).post(f"/v1/people/{pid}/meetings/refresh", headers=HEADERS)
    body = res.json()

    assert res.status_code == 200
    assert body["sources"]["calendar"]["status"] == "failed"
    brief = body["brief"]
    assert brief["partial"] is True
    assert "calendar" in brief["partial_sources"]
    # Everything that was already known survives the failure.
    assert any(
        "Fall sourcing masterdoc" in claim["text"] for claim in brief["claims"]
    )
    assert brief["identity"]["text"].startswith("Ada Lovelace")


def test_the_director_reviews_versions_and_reverts_a_handoff(tmp_path):
    app = _app(tmp_path)
    client = TestClient(app)
    person = _person(app)
    pid = person["person_id"]
    baseline = int(person["version"])
    _drive(app, pid, file_id="drive-1", name="Fall sourcing masterdoc")

    generated = client.get(f"/v1/people/{pid}", headers=HEADERS).json()
    draft = generated["brief"]["handoff"]
    assert draft["generated"] is True

    saved = client.post(
        f"/v1/people/{pid}/handoff",
        headers=HEADERS,
        json={
            "who": draft["who"],
            "wanted": draft["wanted"],
            "happened": "Attached the masterdoc; no outreach yet",
            "they_want": "Unknown until they reply",
            "expected_version": generated["person"]["version"],
        },
    )
    assert saved.status_code == 200
    stored = saved.json()["brief"]["handoff"]
    assert stored["generated"] is False
    assert stored["happened"] == "Attached the masterdoc; no outreach yet"
    assert saved.json()["versions"][-1]["version"] == stored["version"]

    # A second writer holding the old version is refused, not silently merged.
    stale = client.post(
        f"/v1/people/{pid}/handoff",
        headers=HEADERS,
        json={
            "who": "someone else",
            "wanted": "",
            "happened": "",
            "they_want": "",
            "expected_version": generated["person"]["version"],
        },
    )
    assert stale.status_code == 409

    live = client.get(f"/v1/people/{pid}", headers=HEADERS).json()
    reverted = client.post(
        f"/v1/people/{pid}/revert",
        headers=HEADERS,
        json={
            "to_version": baseline,
            "expected_version": live["person"]["version"],
            "rationale_summary": "Restore the earlier person file.",
        },
    )
    assert reverted.status_code == 200

    after = client.get(f"/v1/people/{pid}", headers=HEADERS).json()["brief"]
    # The handoff and the sources that backed it roll back together.
    assert after["handoff"]["generated"] is True
    assert not [
        row for row in after["source_refs"] if row["provider"] == "Google Drive"
    ]
    assert after["claims"], "a reverted person file still has a brief"


def test_a_handoff_needs_a_version_and_at_least_one_field(tmp_path):
    app = _app(tmp_path)
    client = TestClient(app)
    pid = _person(app)["person_id"]

    assert (
        client.post(
            f"/v1/people/{pid}/handoff", headers=HEADERS, json={"who": "Ada"}
        ).status_code
        == 400
    )
    assert (
        client.post(
            f"/v1/people/{pid}/handoff",
            headers=HEADERS,
            json={"expected_version": 1},
        ).status_code
        == 400
    )
    assert (
        client.post(
            f"/v1/people/per_{'0' * 32}/handoff",
            headers=HEADERS,
            json={"who": "Ada", "expected_version": 1},
        ).status_code
        == 404
    )
