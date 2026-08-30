import pytest
from fastapi.testclient import TestClient

from coworker.apollo import FakeHttp
from coworker.apollo_curation import curate_apollo_candidates
from coworker.people import PersonStore
from coworker.provider import FakeProvider
from coworker.server import TOKEN_HEADER, create_app

TOKEN = "test-token-apollo-curation"


def _alyssa() -> dict:
    return {
        "apolloId": "abc123",
        "firstName": "Alyssa",
        "lastNameObfuscated": "W***n",
        "title": "Partner",
        "organizationName": "Codeology",
        "hasEmail": True,
    }


def test_curate_one_candidate_creates_one_unenriched_person_with_target(tmp_path):
    people = PersonStore(tmp_path)

    result = curate_apollo_candidates(
        people,
        [_alyssa()],
        target="Invite senior operators to the director's dinner.",
    )

    assert result["status"] == "success"
    assert result["selected_identity_count"] == 1
    assert result["failed"] == []
    assert len(result["kept"]) == 1
    kept = result["kept"][0]
    assert kept["apollo_id"] == "abc123"
    assert kept["operation"] == "created"
    person = people.get(kept["person_id"])
    assert person is not None
    assert person["target"] == "Invite senior operators to the director's dinner."
    assert person["email"] is None
    assert person["sequence_state"] is None
    assert "email" not in kept


def test_duplicate_apollo_ids_are_kept_once_without_forking(tmp_path):
    people = PersonStore(tmp_path)

    result = curate_apollo_candidates(
        people,
        [
            _alyssa(),
            {**_alyssa(), "title": "Duplicate row must not run"},
        ],
        target="Director-authored target",
    )

    assert result["status"] == "success"
    assert result["selected_identity_count"] == 1
    assert len(result["kept"]) == 1
    assert result["duplicates"] == [{"row_index": 1, "apollo_id": "abc123"}]
    assert people.get_by_apollo_id("abc123")["title"] == "Partner"


def test_partial_fields_and_failed_rows_preserve_success_with_selection_count(tmp_path):
    people = PersonStore(tmp_path)

    result = curate_apollo_candidates(
        people,
        [
            {"apolloId": "partial-person", "firstName": "Maya"},
            {"firstName": "Missing identity"},
        ],
        target="Director-authored target",
    )

    assert result["status"] == "partial"
    assert result["selected_row_count"] == 2
    assert result["selected_identity_count"] == 1
    assert result["failed"] == [
        {"row_index": 1, "apollo_id": None, "code": "missing_apollo_id"}
    ]
    assert len(result["kept"]) == 1
    person = people.get(result["kept"][0]["person_id"])
    assert person is not None
    assert person["first_name"] == "Maya"
    assert person["title"] is None
    assert person["company"] is None
    assert person["target"] == "Director-authored target"


def test_repeated_identity_updates_same_person_once_and_survives_restart(tmp_path):
    people = PersonStore(tmp_path)
    first = curate_apollo_candidates(
        people,
        [_alyssa()],
        target="Initial director target",
    )["kept"][0]

    restarted = PersonStore(tmp_path)
    updated_row = {**_alyssa(), "title": "General Partner"}
    second = curate_apollo_candidates(
        restarted,
        [updated_row],
        target="Updated director target",
    )["kept"][0]
    third = curate_apollo_candidates(
        restarted,
        [updated_row],
        target="Updated director target",
    )["kept"][0]

    assert second["person_id"] == first["person_id"]
    assert second["operation"] == "updated"
    assert second["version"] == first["version"] + 1
    assert third["version"] == second["version"]
    loaded = restarted.get_by_apollo_id("abc123")
    assert loaded is not None
    assert loaded["title"] == "General Partner"
    assert loaded["target"] == "Updated director target"


def test_row_failure_does_not_stop_later_rows_or_erase_earlier_success(tmp_path):
    people = PersonStore(tmp_path)

    result = curate_apollo_candidates(
        people,
        [
            _alyssa(),
            {"apolloId": "bad-row", "firstName": 42},
            {"apolloId": "ada", "firstName": "Ada"},
        ],
        target="Director-authored target",
    )

    assert result["status"] == "partial"
    assert [row["apollo_id"] for row in result["kept"]] == ["abc123", "ada"]
    assert result["failed"] == [
        {"row_index": 1, "apollo_id": "bad-row", "code": "invalid_candidate"}
    ]
    assert people.get_by_apollo_id("abc123") is not None
    assert people.get_by_apollo_id("ada") is not None
    assert people.get_by_apollo_id("bad-row") is None


def test_curation_requires_the_director_authored_target(tmp_path):
    people = PersonStore(tmp_path)

    with pytest.raises(ValueError, match="target"):
        curate_apollo_candidates(people, [_alyssa()], target="   ")

    assert people.get_by_apollo_id("abc123") is None


def test_multi_candidate_api_keeps_separate_unbound_people_without_credit_use(tmp_path):
    http = FakeHttp()
    app = create_app(
        token=TOKEN,
        provider=FakeProvider(),
        state=tmp_path,
        http=http,
    )
    session_id = app.state.store.open_session_id()
    response = TestClient(app).post(
        "/v1/apollo/curate",
        headers={TOKEN_HEADER: TOKEN},
        json={
            "session_id": session_id,
            "target": "Invite senior operators to the director's dinner.",
            "people": [
                _alyssa(),
                {"apolloId": "partial-person", "firstName": "Maya"},
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["original_session"] == {
        "session_id": session_id,
        "bound_person_id": None,
        "reason": "multiple_selection",
    }
    assert len(body["kept"]) == 2
    assert all(row["sourcing_chat"] is None for row in body["kept"])
    assert app.state.people.person_for_session(session_id) is None
    for row in body["kept"]:
        person = app.state.people.get(row["person_id"])
        assert person is not None
        assert person["target"] == "Invite senior operators to the director's dinner."
        assert person["email"] is None
        assert person["sequence_state"] is None
    assert http.calls == []


def test_multi_candidate_api_preserves_and_reports_an_existing_chat_binding(tmp_path):
    app = create_app(token=TOKEN, provider=FakeProvider(), state=tmp_path)
    session_id = app.state.store.open_session_id()
    owner = app.state.people.keep_from_apollo(
        apollo_id="existing-owner",
        first_name="Existing",
        last_name_obfuscated=None,
        title=None,
        company=None,
        target="Existing target",
    )
    app.state.people.bind_session(session_id, owner["person_id"])

    response = TestClient(app).post(
        "/v1/apollo/curate",
        headers={TOKEN_HEADER: TOKEN},
        json={
            "session_id": session_id,
            "target": "Director-authored target",
            "people": [
                _alyssa(),
                {"apolloId": "partial-person", "firstName": "Maya"},
            ],
            "bind_original": False,
        },
    )

    assert response.status_code == 200
    assert response.json()["original_session"] == {
        "session_id": session_id,
        "bound_person_id": owner["person_id"],
        "reason": "already_bound",
    }
    assert app.state.people.person_for_session(session_id) == owner["person_id"]


def test_single_candidate_api_binds_original_chat_to_that_person(tmp_path):
    app = create_app(token=TOKEN, provider=FakeProvider(), state=tmp_path)
    session_id = app.state.store.open_session_id()

    response = TestClient(app).post(
        "/v1/apollo/curate",
        headers={TOKEN_HEADER: TOKEN},
        json={
            "session_id": session_id,
            "target": "Director-authored target",
            "people": [_alyssa()],
            "bind_original": True,
        },
    )

    assert response.status_code == 200
    body = response.json()
    kept = body["kept"][0]
    assert body["original_session"] == {
        "session_id": session_id,
        "bound_person_id": kept["person_id"],
        "reason": "single_selection",
    }
    assert kept["sourcing_chat"] == {"session_id": session_id}
    assert app.state.people.person_for_session(session_id) == kept["person_id"]
    board = TestClient(app).get(
        "/v1/board",
        headers={TOKEN_HEADER: TOKEN},
    ).json()
    assert [row["person_id"] for row in board["backlog"]] == [kept["person_id"]]
    assert board["backlog"][0]["sequence_state"] is None
    assert board["backlog"][0]["board_lane"] == "backlog"


def test_retrying_only_failed_multi_rows_never_rebinds_or_updates_successes(tmp_path):
    app = create_app(token=TOKEN, provider=FakeProvider(), state=tmp_path)
    session_id = app.state.store.open_session_id()
    client = TestClient(app)
    first = client.post(
        "/v1/apollo/curate",
        headers={TOKEN_HEADER: TOKEN},
        json={
            "session_id": session_id,
            "target": "Director-authored target",
            "people": [_alyssa(), {"firstName": "Missing identity"}],
            "bind_original": False,
        },
    ).json()
    successful = first["kept"][0]

    retried = client.post(
        "/v1/apollo/curate",
        headers={TOKEN_HEADER: TOKEN},
        json={
            "session_id": session_id,
            "target": "Director-authored target",
            "people": [{"apolloId": "fixed-row", "firstName": "Fixed"}],
            "bind_original": False,
        },
    ).json()

    assert retried["status"] == "success"
    assert [row["apollo_id"] for row in retried["kept"]] == ["fixed-row"]
    assert app.state.people.get(successful["person_id"])["version"] == successful[
        "version"
    ]
    assert app.state.people.person_for_session(session_id) is None
