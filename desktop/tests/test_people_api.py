from fastapi.testclient import TestClient

from coworker.provider import FakeProvider
from coworker.server import TOKEN_HEADER, create_app

TOKEN = "test-token-people"


def _app(tmp_path):
    return create_app(token=TOKEN, state=tmp_path, provider=FakeProvider())


def test_get_person_returns_brief_and_timeline(tmp_path):
    app = _app(tmp_path)
    person = app.state.people.keep_from_apollo(
        apollo_id="abc123",
        first_name="Alyssa",
        last_name_obfuscated="W***n",
        title="Partner",
        company="Codeology",
        target="club research dinner",
    )
    app.state.people.append_event(
        person["person_id"],
        source="gmail",
        kind="mail",
        summary="Read mail from Alyssa",
        payload={"id": "m1"},
    )
    res = TestClient(app).get(
        f"/v1/people/{person['person_id']}",
        headers={TOKEN_HEADER: TOKEN},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["person"]["person_id"] == person["person_id"]
    assert body["person"]["first_name"] == "Alyssa"
    assert "Alyssa" in body["brief"]["who"]
    assert body["brief"]["why"] == "club research dinner"
    assert body["timeline"][0]["kind"] == "mail"
    assert body["timeline"][0]["summary"] == "Read mail from Alyssa"


def test_get_unknown_person_is_404(tmp_path):
    res = TestClient(_app(tmp_path)).get(
        f"/v1/people/per_{'0' * 32}",
        headers={TOKEN_HEADER: TOKEN},
    )
    assert res.status_code == 404


def test_get_person_does_not_leak_access_token(tmp_path):
    app = _app(tmp_path)
    person = app.state.people.keep_from_apollo(
        apollo_id="ada",
        first_name="Ada",
        last_name_obfuscated="L",
        title="Founder",
        company="Analytic",
    )
    app.state.people.append_event(
        person["person_id"],
        source="drive",
        kind="file",
        summary="Read deck",
        payload={"id": "d1", "access_token": "ya29.secret", "name": "Deck"},
    )
    res = TestClient(app).get(
        f"/v1/people/{person['person_id']}",
        headers={TOKEN_HEADER: TOKEN},
    )
    assert res.status_code == 200
    blob = res.text
    assert "ya29.secret" not in blob
    assert "access_token" not in blob
    assert res.json()["timeline"][0]["payload"]["name"] == "Deck"
    assert res.json()["timeline"][0]["payload"]["id"] == "d1"
