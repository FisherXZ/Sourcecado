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


def test_empty_board_is_three_empty_lists(tmp_path):
    body = TestClient(_app(tmp_path)).get("/v1/board", headers={TOKEN_HEADER: TOKEN}).json()
    assert body == {"open": [], "in_conversation": [], "done": []}
    blob = str(body)
    assert "amount" not in blob
    assert "pipeline" not in blob
    assert "deal" not in blob


def test_board_lists_open_person_and_move(tmp_path):
    app = _app(tmp_path)
    person = app.state.people.keep_from_apollo(
        apollo_id="ada",
        first_name="Ada",
        last_name_obfuscated="L",
        title="Founder",
        company="Analytic",
    )
    app.state.people.set_sequence(person["person_id"], "open", actor="assistant")
    client = TestClient(app)
    board = client.get("/v1/board", headers={TOKEN_HEADER: TOKEN}).json()
    assert [row["person_id"] for row in board["open"]] == [person["person_id"]]
    moved = client.post(
        f"/v1/people/{person['person_id']}/sequence",
        headers={TOKEN_HEADER: TOKEN},
        json={"state": "in_conversation", "actor": "director"},
    )
    assert moved.status_code == 200
    assert moved.json()["person"]["sequence_state"] == "in_conversation"
    board = client.get("/v1/board", headers={TOKEN_HEADER: TOKEN}).json()
    assert board["open"] == []
    assert [row["person_id"] for row in board["in_conversation"]] == [person["person_id"]]
    kinds = [row["kind"] for row in app.state.people.timeline(person["person_id"])]
    assert "state" in kinds


def test_board_move_rejects_invalid_state_and_unknown_person(tmp_path):
    app = _app(tmp_path)
    person = app.state.people.keep_from_apollo(
        apollo_id="ada",
        first_name="Ada",
        last_name_obfuscated="L",
        title="Founder",
        company="Analytic",
    )
    client = TestClient(app)
    bad = client.post(
        f"/v1/people/{person['person_id']}/sequence",
        headers={TOKEN_HEADER: TOKEN},
        json={"state": "won", "actor": "director"},
    )
    assert bad.status_code == 400
    missing = client.post(
        f"/v1/people/per_{'0' * 32}/sequence",
        headers={TOKEN_HEADER: TOKEN},
        json={"state": "open", "actor": "director"},
    )
    assert missing.status_code == 404
