import json

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


def test_settings_exposes_provider_verification_without_credentials(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-private-key")
    monkeypatch.setenv("MOONSHOT_API_KEY", "kimi-private-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-private-key")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-private-key")
    monkeypatch.delenv("CLUB_MODEL", raising=False)

    body = TestClient(create_app(token=TOKEN, state=tmp_path)).get(
        "/v1/settings", headers={TOKEN_HEADER: TOKEN}
    ).json()

    assert body["model"] == "deepseek-v4-pro"
    assert body["providers"] == [
        {
            "provider": "deepseek",
            "model": "deepseek-v4-pro",
            "selected": True,
            "eligible": True,
            "failures": [],
            "context_window_tokens": 1_000_000,
            "capabilities": {
                "text": True,
                "transient_reasoning": True,
                "tool_calling": True,
                "terminal_usage": True,
                "cache_usage": True,
                "reasoning_usage": True,
            },
        },
        {
            "provider": "kimi",
            "model": "kimi-k3",
            "selected": False,
            "eligible": True,
            "failures": [],
            "context_window_tokens": 1_000_000,
            "capabilities": {
                "text": True,
                "transient_reasoning": True,
                "tool_calling": True,
                "terminal_usage": True,
                "cache_usage": True,
                "reasoning_usage": False,
            },
        },
        {
            "provider": "anthropic",
            "model": "claude-sonnet-4-6",
            "selected": False,
            "eligible": True,
            "failures": [],
            "context_window_tokens": 1_000_000,
            "capabilities": {
                "text": True,
                "transient_reasoning": True,
                "tool_calling": True,
                "terminal_usage": True,
                "cache_usage": True,
                "reasoning_usage": False,
            },
        },
        {
            "provider": "openai",
            "model": "gpt-4o-mini",
            "selected": False,
            "eligible": True,
            "failures": [],
            "context_window_tokens": 128_000,
            "capabilities": {
                "text": True,
                "transient_reasoning": True,
                "tool_calling": True,
                "terminal_usage": True,
                "cache_usage": True,
                "reasoning_usage": True,
            },
        },
    ]
    encoded = json.dumps(body)
    assert "private-key" not in encoded
    assert "authorization" not in encoded.lower()


def test_settings_redacts_invalid_explicit_model_from_legacy_and_provider_fields(
    tmp_path, monkeypatch
):
    planted = "xoxb-PLANTED-SENTINEL"
    for key in (
        "DEEPSEEK_API_KEY",
        "MOONSHOT_API_KEY",
        "KIMI_API_KEY",
        "ANTHROPIC_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-private-key")
    monkeypatch.setenv("CLUB_MODEL", planted)

    body = TestClient(create_app(token=TOKEN, state=tmp_path)).get(
        "/v1/settings", headers={TOKEN_HEADER: TOKEN}
    ).json()

    openai = next(
        report for report in body["providers"] if report["provider"] == "openai"
    )
    assert body["model"] is None
    assert openai["model"] == "gpt-4o-mini"
    assert openai["eligible"] is False
    assert openai["failures"] == ["invalid_model_identifier"]
    assert planted not in json.dumps(body)
