"""The director's route out of the classification backlog.

Migrating to context-projection-v1 withholds every existing memory row until it
is classified. These endpoints are how the count of waiting rows is visible and
how a row is either promoted to a global operator preference or deleted.
"""

from fastapi.testclient import TestClient

from coworker.provider import FakeProvider
from coworker.server import TOKEN_HEADER, create_app, system_prompt

TOKEN = "memory-classification-token"


def _client(tmp_path):
    app = create_app(token=TOKEN, provider=FakeProvider(), state=tmp_path)
    return app, TestClient(app)


def test_the_backlog_reports_what_is_waiting(tmp_path):
    app, client = _client(tmp_path)
    app.state.store.remember("Codeology sources design-adjacent engineers first.")
    app.state.store.remember("Keep outreach drafts under 140 words.")

    response = client.get("/v1/memory/classification", headers={TOKEN_HEADER: TOKEN})

    assert response.status_code == 200
    body = response.json()
    assert body["needs_review"] == 2
    assert body["classified"] == 0
    assert [item["id"] for item in body["items"]] == [1, 2]
    assert body["items"][0]["category"] == "unclassified"


def test_classifying_a_preference_puts_it_back_into_the_prompt(tmp_path):
    app, client = _client(tmp_path)
    app.state.store.remember("Keep outreach drafts under 140 words.")
    assert "Keep outreach drafts under 140 words." not in system_prompt(app.state.store)

    response = client.post(
        "/v1/memory/1/classification",
        json={"category": "operator_preference"},
        headers={TOKEN_HEADER: TOKEN},
    )

    assert response.status_code == 200
    assert response.json()["memory"]["classification_status"] == "classified"
    assert "Keep outreach drafts under 140 words." in system_prompt(app.state.store)
    backlog = client.get(
        "/v1/memory/classification", headers={TOKEN_HEADER: TOKEN}
    ).json()
    assert backlog["needs_review"] == 0
    assert backlog["classified"] == 1


def test_no_other_category_can_be_claimed_through_this_route(tmp_path):
    app, client = _client(tmp_path)
    app.state.store.remember("Ada moved to Analytic in June.")

    response = client.post(
        "/v1/memory/1/classification",
        json={"category": "person_evidence"},
        headers={TOKEN_HEADER: TOKEN},
    )

    assert response.status_code == 400
    assert app.state.store.memory_projection_items() == ()


def test_a_person_scoped_row_is_refused_with_a_reason(tmp_path):
    app, client = _client(tmp_path)
    app.state.store.remember("Ada moved to Analytic in June.", person_id="per_ada")

    response = client.post(
        "/v1/memory/1/classification",
        json={"category": "operator_preference"},
        headers={TOKEN_HEADER: TOKEN},
    )

    assert response.status_code == 400
    assert "Person File" in response.json()["error"]


def test_classifying_a_row_that_is_gone_reports_not_found(tmp_path):
    _app, client = _client(tmp_path)

    response = client.post(
        "/v1/memory/9/classification",
        json={"category": "operator_preference"},
        headers={TOKEN_HEADER: TOKEN},
    )

    assert response.status_code == 404


def test_deleting_a_row_drains_the_backlog(tmp_path):
    app, client = _client(tmp_path)
    app.state.store.remember("Obsolete note.")

    response = client.delete("/v1/memory/1", headers={TOKEN_HEADER: TOKEN})

    assert response.status_code == 200
    assert response.json() == {"forgotten": True, "id": 1}
    assert client.get(
        "/v1/memory/classification", headers={TOKEN_HEADER: TOKEN}
    ).json()["needs_review"] == 0
    assert client.delete("/v1/memory/1", headers={TOKEN_HEADER: TOKEN}).status_code == 404
