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


def test_create_person_sourcing_chat_persists_binding_before_any_turn(tmp_path):
    app = _app(tmp_path)
    person = app.state.people.keep_from_apollo(
        apollo_id="ada",
        first_name="Ada",
        last_name_obfuscated="L",
        title="Founder",
        company="Analytic",
    )

    response = TestClient(app).post(
        f"/v1/people/{person['person_id']}/sourcing-chat",
        headers={TOKEN_HEADER: TOKEN},
        json={"expected_person_version": person["version"]},
    )

    assert response.status_code == 201
    body = response.json()
    session_id = body["session"]["id"]
    assert body["created"] is True
    assert body["active_person"] == {
        "person_id": person["person_id"],
        "version": person["version"],
        "label": "Ada L, Founder at Analytic",
    }
    assert app.state.people.person_for_session(session_id) == person["person_id"]
    assert app.state.store.index(session_id) is not None
    assert app.state.store.load(session_id) == []
    assert app.state.store.load_events(session_id) == []


def test_reopen_person_sourcing_chat_returns_same_session_after_restart(tmp_path):
    app = _app(tmp_path)
    person = app.state.people.keep_from_apollo(
        apollo_id="ada",
        first_name="Ada",
        last_name_obfuscated="L",
        title="Founder",
        company="Analytic",
    )
    first = TestClient(app).post(
        f"/v1/people/{person['person_id']}/sourcing-chat",
        headers={TOKEN_HEADER: TOKEN},
        json={"expected_person_version": person["version"]},
    )
    session_id = first.json()["session"]["id"]

    restarted = _app(tmp_path)
    reopened = TestClient(restarted).post(
        f"/v1/people/{person['person_id']}/sourcing-chat",
        headers={TOKEN_HEADER: TOKEN},
        json={"expected_person_version": person["version"]},
    )

    assert reopened.status_code == 200
    assert reopened.json()["created"] is False
    assert reopened.json()["session"]["id"] == session_id
    assert restarted.state.people.person_for_session(session_id) == person["person_id"]


def test_person_file_reports_whether_bound_sourcing_chat_exists(tmp_path):
    app = _app(tmp_path)
    person = app.state.people.keep_from_apollo(
        apollo_id="ada",
        first_name="Ada",
        last_name_obfuscated="L",
        title="Founder",
        company="Analytic",
    )
    client = TestClient(app)

    before = client.get(
        f"/v1/people/{person['person_id']}", headers={TOKEN_HEADER: TOKEN}
    )
    assert before.json()["sourcing_chat"] is None

    created = client.post(
        f"/v1/people/{person['person_id']}/sourcing-chat",
        headers={TOKEN_HEADER: TOKEN},
        json={"expected_person_version": person["version"]},
    ).json()
    after = client.get(
        f"/v1/people/{person['person_id']}", headers={TOKEN_HEADER: TOKEN}
    )

    assert after.json()["sourcing_chat"] == {
        "session_id": created["session"]["id"],
        "person_id": person["person_id"],
    }


def test_create_sourcing_chat_for_missing_person_is_404(tmp_path):
    response = TestClient(_app(tmp_path), raise_server_exceptions=False).post(
        f"/v1/people/per_{'0' * 32}/sourcing-chat",
        headers={TOKEN_HEADER: TOKEN},
        json={"expected_person_version": 1},
    )

    assert response.status_code == 404
    assert response.json() == {"error": "not found"}


def test_stale_person_version_does_not_create_or_bind_a_chat(tmp_path):
    app = _app(tmp_path)
    person = app.state.people.keep_from_apollo(
        apollo_id="ada",
        first_name="Ada",
        last_name_obfuscated="L",
        title="Founder",
        company="Analytic",
    )
    app.state.people.set_sequence(person["person_id"], "open", actor="director")
    sessions_before = app.state.store.list_sessions()

    response = TestClient(app).post(
        f"/v1/people/{person['person_id']}/sourcing-chat",
        headers={TOKEN_HEADER: TOKEN},
        json={"expected_person_version": person["version"]},
    )

    assert response.status_code == 409
    assert "stale" in response.json()["error"]
    assert app.state.people.session_for_person(person["person_id"]) is None
    assert app.state.store.list_sessions() == sessions_before


def test_cross_person_session_reuse_fails_without_rebinding(tmp_path):
    app = _app(tmp_path)
    ada = app.state.people.keep_from_apollo(
        apollo_id="ada",
        first_name="Ada",
        last_name_obfuscated="L",
        title="Founder",
        company="Analytic",
    )
    alonzo = app.state.people.keep_from_apollo(
        apollo_id="alonzo",
        first_name="Alonzo",
        last_name_obfuscated="C",
        title="Professor",
        company="Princeton",
    )
    client = TestClient(app)
    ada_session = client.post(
        f"/v1/people/{ada['person_id']}/sourcing-chat",
        headers={TOKEN_HEADER: TOKEN},
        json={"expected_person_version": ada["version"]},
    ).json()["session"]["id"]

    response = client.post(
        f"/v1/people/{alonzo['person_id']}/sourcing-chat",
        headers={TOKEN_HEADER: TOKEN},
        json={
            "expected_person_version": alonzo["version"],
            "session_id": ada_session,
        },
    )

    assert response.status_code == 409
    assert "already bound" in response.json()["error"]
    assert app.state.people.person_for_session(ada_session) == ada["person_id"]
    assert app.state.people.session_for_person(alonzo["person_id"]) is None


def test_bound_session_restore_returns_current_active_person(tmp_path):
    app = _app(tmp_path)
    person = app.state.people.keep_from_apollo(
        apollo_id="ada",
        first_name="Ada",
        last_name_obfuscated="L",
        title="Founder",
        company="Analytic",
    )
    client = TestClient(app)
    session_id = client.post(
        f"/v1/people/{person['person_id']}/sourcing-chat",
        headers={TOKEN_HEADER: TOKEN},
        json={"expected_person_version": person["version"]},
    ).json()["session"]["id"]

    restored = client.get(
        f"/v1/sessions/{session_id}", headers={TOKEN_HEADER: TOKEN}
    )

    assert restored.status_code == 200
    assert restored.json()["active_person"] == {
        "person_id": person["person_id"],
        "version": person["version"],
        "label": "Ada L, Founder at Analytic",
    }


def test_deleted_bound_person_makes_session_restore_fail_visibly(tmp_path):
    app = _app(tmp_path)
    person = app.state.people.keep_from_apollo(
        apollo_id="ada",
        first_name="Ada",
        last_name_obfuscated="L",
        title="Founder",
        company="Analytic",
    )
    client = TestClient(app)
    session_id = client.post(
        f"/v1/people/{person['person_id']}/sourcing-chat",
        headers={TOKEN_HEADER: TOKEN},
        json={"expected_person_version": person["version"]},
    ).json()["session"]["id"]
    app.state.people.delete(
        person["person_id"],
        expected_version=person["version"],
        actor="director",
        rationale_summary="Remove stale person file.",
    )

    restored = client.get(
        f"/v1/sessions/{session_id}", headers={TOKEN_HEADER: TOKEN}
    )

    assert restored.status_code == 409
    assert restored.json() == {
        "error": "bound person file is unavailable",
        "code": "bound_person_unavailable",
    }


def test_deleted_bound_person_blocks_model_turn_before_provider_call(tmp_path):
    app = _app(tmp_path)
    person = app.state.people.keep_from_apollo(
        apollo_id="ada",
        first_name="Ada",
        last_name_obfuscated="L",
        title="Founder",
        company="Analytic",
    )
    client = TestClient(app)
    session_id = client.post(
        f"/v1/people/{person['person_id']}/sourcing-chat",
        headers={TOKEN_HEADER: TOKEN},
        json={"expected_person_version": person["version"]},
    ).json()["session"]["id"]
    app.state.people.delete(
        person["person_id"],
        expected_version=person["version"],
        actor="director",
        rationale_summary="Remove stale person file.",
    )

    with client.websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        ws.send_json(
            {"type": "chat", "text": "do not run", "session_id": session_id}
        )
        event = ws.receive_json()

    assert event == {
        "type": "error",
        "message": "This conversation's bound person file is unavailable.",
    }
    assert app.state.provider.calls == []
    assert app.state.store.load(session_id) == []


def test_session_route_rejects_a_different_expected_person(tmp_path):
    app = _app(tmp_path)
    ada = app.state.people.keep_from_apollo(
        apollo_id="ada",
        first_name="Ada",
        last_name_obfuscated="L",
        title="Founder",
        company="Analytic",
    )
    alonzo = app.state.people.keep_from_apollo(
        apollo_id="alonzo",
        first_name="Alonzo",
        last_name_obfuscated="C",
        title="Professor",
        company="Princeton",
    )
    client = TestClient(app)
    session_id = client.post(
        f"/v1/people/{ada['person_id']}/sourcing-chat",
        headers={TOKEN_HEADER: TOKEN},
        json={"expected_person_version": ada["version"]},
    ).json()["session"]["id"]

    response = client.get(
        f"/v1/sessions/{session_id}",
        headers={TOKEN_HEADER: TOKEN},
        params={"expected_person_id": alonzo["person_id"]},
    )

    assert response.status_code == 409
    assert response.json() == {
        "error": "conversation is bound to a different person",
        "code": "person_binding_mismatch",
    }
    assert app.state.people.person_for_session(session_id) == ada["person_id"]


def test_reverting_person_data_preserves_the_same_bound_session(tmp_path):
    app = _app(tmp_path)
    person = app.state.people.keep_from_apollo(
        apollo_id="ada",
        first_name="Ada",
        last_name_obfuscated="L",
        title="Founder",
        company="Analytic",
    )
    version_two = app.state.people.set_sequence(
        person["person_id"], "open", actor="director"
    )
    client = TestClient(app)
    session_id = client.post(
        f"/v1/people/{person['person_id']}/sourcing-chat",
        headers={TOKEN_HEADER: TOKEN},
        json={"expected_person_version": version_two["version"]},
    ).json()["session"]["id"]
    reverted = app.state.people.revert(
        person["person_id"],
        to_version=person["version"],
        expected_version=version_two["version"],
        actor="director",
        rationale_summary="Restore the earlier person file.",
    )

    restored = client.get(
        f"/v1/sessions/{session_id}",
        headers={TOKEN_HEADER: TOKEN},
        params={"expected_person_id": person["person_id"]},
    )
    reopened = client.post(
        f"/v1/people/{person['person_id']}/sourcing-chat",
        headers={TOKEN_HEADER: TOKEN},
        json={"expected_person_version": reverted["version"]},
    )

    assert restored.status_code == 200
    assert restored.json()["active_person"]["person_id"] == person["person_id"]
    assert restored.json()["active_person"]["version"] == reverted["version"]
    assert reopened.json()["session"]["id"] == session_id
    assert reopened.json()["created"] is False


def test_explicit_bound_chat_prompt_uses_only_its_person_evidence(tmp_path):
    app = _app(tmp_path)
    ada = app.state.people.keep_from_apollo(
        apollo_id="ada",
        first_name="Ada",
        last_name_obfuscated="L",
        title="Founder",
        company="Analytic",
        target="research dinner",
    )
    alonzo = app.state.people.keep_from_apollo(
        apollo_id="alonzo",
        first_name="Alonzo",
        last_name_obfuscated="C",
        title="Professor",
        company="Princeton",
        target="different project",
    )
    app.state.people.append_event(
        ada["person_id"],
        source="gmail",
        kind="mail",
        summary="Ada asked about the research dinner.",
    )
    app.state.people.append_event(
        alonzo["person_id"],
        source="granola",
        kind="meeting",
        summary="PRIVATE ALONZO NOTES",
    )
    client = TestClient(app)
    session_id = client.post(
        f"/v1/people/{ada['person_id']}/sourcing-chat",
        headers={TOKEN_HEADER: TOKEN},
        json={"expected_person_version": ada["version"]},
    ).json()["session"]["id"]

    with client.websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        ws.send_json(
            {"type": "chat", "text": "prepare me", "session_id": session_id}
        )
        while ws.receive_json()["type"] not in {"turn_end", "error"}:
            pass

    prompt = str(app.state.provider.calls[0][0]["content"])
    assert "Ada" in prompt
    assert "Ada asked about the research dinner." in prompt
    assert "Alonzo" not in prompt
    assert "PRIVATE ALONZO NOTES" not in prompt


def test_legacy_multiple_person_chats_fail_visibly_instead_of_choosing_one(tmp_path):
    app = _app(tmp_path)
    person = app.state.people.keep_from_apollo(
        apollo_id="ada",
        first_name="Ada",
        last_name_obfuscated="L",
        title="Founder",
        company="Analytic",
    )
    app.state.store.create_session("legacy-one")
    app.state.store.create_session("legacy-two")
    with app.state.people._lock:
        app.state.people._conn.executemany(
            "INSERT INTO session_people (session_id, person_id) VALUES (?, ?)",
            [
                ("legacy-one", person["person_id"]),
                ("legacy-two", person["person_id"]),
            ],
        )
        app.state.people._conn.commit()

    response = TestClient(app, raise_server_exceptions=False).get(
        f"/v1/people/{person['person_id']}", headers={TOKEN_HEADER: TOKEN}
    )

    assert response.status_code == 409
    assert response.json() == {
        "error": "person has multiple bound sourcing sessions",
        "code": "person_chat_conflict",
    }


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
