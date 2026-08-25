from fastapi.testclient import TestClient

from coworker.gmail import FakeGmail
from coworker.provider import FakeProvider
from coworker.server import TOKEN_HEADER, create_app

TOKEN = "test-token-settings"


def test_settings_default_persona_is_sourcing(tmp_path):
    res = TestClient(create_app(token=TOKEN, state=tmp_path, provider=FakeProvider())).get(
        "/v1/settings", headers={TOKEN_HEADER: TOKEN}
    )
    assert res.status_code == 200
    body = res.json()
    assert body["persona"]["id"] == "sourcing"
    assert body["gmail"] == {"connected": False, "email": None}
    assert body["apollo"] == {"configured": False}
    assert "api_key" not in str(body).lower()


def test_settings_switch_persona_persists(tmp_path):
    app = create_app(token=TOKEN, state=tmp_path, provider=FakeProvider())
    client = TestClient(app)
    res = client.post(
        "/v1/settings/persona",
        headers={TOKEN_HEADER: TOKEN},
        json={"id": "buddy"},
    )
    assert res.status_code == 200
    assert res.json()["persona"]["id"] == "buddy"
    again = TestClient(create_app(token=TOKEN, state=tmp_path, provider=FakeProvider()))
    body = again.get("/v1/settings", headers={TOKEN_HEADER: TOKEN}).json()
    assert body["persona"]["id"] == "buddy"


def test_settings_apollo_configured_flag_not_key(tmp_path):
    res = TestClient(
        create_app(token=TOKEN, state=tmp_path, provider=FakeProvider(), apollo_key="secret-key")
    ).get("/v1/settings", headers={TOKEN_HEADER: TOKEN})
    body = res.json()
    assert body["apollo"]["configured"] is True
    assert "secret-key" not in str(body)


def test_settings_gmail_connected_flag(tmp_path):
    app = create_app(token=TOKEN, state=tmp_path, provider=FakeProvider(), gmail=FakeGmail())
    app.state.secrets.put("gmail", {"refresh_token": "rt", "email": "fisher@example.com"})
    body = TestClient(app).get("/v1/settings", headers={TOKEN_HEADER: TOKEN}).json()
    assert body["gmail"]["connected"] is True
    assert body["gmail"]["email"] == "fisher@example.com"
