from fastapi.testclient import TestClient

from coworker.server import TOKEN_HEADER, create_app

TOKEN = "test-token-board-index-api"


def test_board_index_api_lists_inspects_and_reverts_versioned_records(tmp_path):
    app = create_app(token=TOKEN, state=tmp_path)
    created = app.state.sourcing_index.upsert(
        record_type="contact",
        fields={"name": "Ada", "title": "Founder"},
        idempotency_key="contact:api-ada",
        actor="assistant",
        session_id="session-api",
        run_id="run-api",
        rationale_summary="Create Ada.",
    )
    updated = app.state.sourcing_index.patch(
        created["id"],
        fields={"title": "Incorrect"},
        expected_version=1,
        actor="assistant",
        rationale_summary="Apply an incorrect value for the revert fixture.",
    )
    client = TestClient(app)

    listing = client.get(
        "/v1/board/records?record_type=contact&name=Ada",
        headers={TOKEN_HEADER: TOKEN},
    )
    assert listing.status_code == 200
    assert [record["id"] for record in listing.json()["records"]] == [created["id"]]

    detail = client.get(
        f"/v1/board/records/{created['id']}", headers={TOKEN_HEADER: TOKEN}
    )
    assert detail.status_code == 200
    assert detail.json()["record"]["version"] == 2
    assert [receipt["operation"] for receipt in detail.json()["receipts"]] == [
        "create",
        "patch",
    ]

    reverted = client.post(
        f"/v1/board/records/{created['id']}/revert",
        headers={TOKEN_HEADER: TOKEN},
        json={
            "to_version": 1,
            "expected_version": updated["version"],
            "rationale_summary": "Restore the verified value.",
        },
    )
    assert reverted.status_code == 200
    assert reverted.json()["record"]["fields"]["title"] == "Founder"
    assert reverted.json()["record"]["version"] == 3


def test_board_index_api_does_not_expose_restricted_sources_without_a_grant(tmp_path):
    app = create_app(token=TOKEN, state=tmp_path)
    restricted = app.state.sourcing_index.upsert(
        record_type="source_ref",
        fields={"title": "Resume", "sensitivity": "restricted"},
        idempotency_key="source:api-resume",
        actor="director",
        rationale_summary="Register the restricted source.",
    )
    client = TestClient(app)

    listing = client.get(
        "/v1/board/records?record_type=source_ref", headers={TOKEN_HEADER: TOKEN}
    )
    detail = client.get(
        f"/v1/board/records/{restricted['id']}", headers={TOKEN_HEADER: TOKEN}
    )

    assert listing.json()["records"] == []
    assert detail.status_code == 404
