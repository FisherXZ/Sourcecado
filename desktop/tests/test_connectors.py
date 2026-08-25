from fastapi.testclient import TestClient

from coworker.server import TOKEN_HEADER, create_app

TOKEN = "test-token-connectors"


def test_connectors_never_include_secrets(tmp_path):
    app = create_app(token=TOKEN, state=tmp_path, apollo_key="sk-secret")
    app.state.secrets.put(
        "google",
        {
            "refresh_token": "rt-secret",
            "email": "fisher@example.com",
            "scopes": [
                "https://www.googleapis.com/auth/gmail.compose",
                "https://www.googleapis.com/auth/gmail.readonly",
                "https://www.googleapis.com/auth/userinfo.email",
            ],
        },
    )
    body = TestClient(app).get("/v1/connectors", headers={TOKEN_HEADER: TOKEN}).json()
    blob = str(body)
    assert "rt-secret" not in blob
    assert "sk-secret" not in blob
    by_id = {c["id"]: c for c in body["connectors"]}
    assert by_id["gmail"]["status"] == "connected"
    assert by_id["gmail"]["email"] == "fisher@example.com"
    assert by_id["drive"]["status"] == "missing"
    assert by_id["calendar"]["status"] == "missing"
    assert by_id["apollo"]["status"] == "configured"
    assert by_id["granola"]["status"] == "missing"
