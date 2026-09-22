import json

from fastapi.testclient import TestClient

from coworker.provider import FakeProvider
from coworker.server import TOKEN_HEADER, create_app


TOKEN = "prompt-diagnostics-token"


def test_prompt_diagnostics_endpoint_is_versioned_and_content_free(tmp_path):
    app = create_app(token=TOKEN, provider=FakeProvider(), state=tmp_path)
    sid = app.state.store.open_session_id()
    app.state.store.remember("PRIVATE MEMORY SENTINEL")
    app.state.store.memory_classify(1)
    client = TestClient(app)

    response = client.get(
        f"/v1/sessions/{sid}/prompt/current",
        headers={TOKEN_HEADER: TOKEN},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["prompt_version"] == "sourcing-director-v1"
    assert body["prompt_section_ids"][:2] == [
        "identity_authority",
        "domain_model",
    ]
    assert body["prompt_section_count"] == len(body["prompt_section_ids"])
    assert body["labels_budget_chars"] == 500
    assert body["dynamic_context_sections"][0] == {
        "section_id": "saved_memory",
        "chars": len("[#1] PRIVATE MEMORY SENTINEL (sourcecado:memory/1, current)"),
        "budget_chars": 4_000,
    }
    assert body["dynamic_context_sections"][1]["section_id"] == "skill_catalog"
    assert body["dynamic_context_sections"][1]["budget_chars"] == 3_000
    assert len(body["system_prompt_sha256"]) == 64
    encoded = json.dumps(body)
    assert "PRIVATE MEMORY SENTINEL" not in encoded
    assert "Identity and authority" not in encoded
    assert "system_prompt" not in body
