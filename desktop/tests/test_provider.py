from coworker.provider import (
    DEEPSEEK_MODEL,
    KIMI_MODEL,
    OpenAICompatProvider,
    default_model_id,
    provider_from_env,
)


def test_deepseek_wins_over_kimi(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-key")
    monkeypatch.setenv("MOONSHOT_API_KEY", "kimi-key")
    monkeypatch.delenv("CLUB_MODEL", raising=False)
    p = provider_from_env()
    assert isinstance(p, OpenAICompatProvider)
    assert p.model_id == DEEPSEEK_MODEL
    assert p.base_url == "https://api.deepseek.com"
    assert p.api_key == "ds-key"


def test_kimi_when_no_deepseek(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("KIMI_API_KEY", "kimi-key")
    monkeypatch.delenv("CLUB_MODEL", raising=False)
    p = provider_from_env()
    assert isinstance(p, OpenAICompatProvider)
    assert p.model_id == KIMI_MODEL
    assert p.base_url == "https://api.moonshot.ai/v1"
    assert p.api_key == "kimi-key"


def test_club_model_override(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-key")
    monkeypatch.setenv("CLUB_MODEL", "deepseek-v4-flash")
    p = provider_from_env()
    assert p is not None
    assert p.model_id == "deepseek-v4-flash"


def test_default_model_id_none(monkeypatch):
    for key in (
        "DEEPSEEK_API_KEY",
        "MOONSHOT_API_KEY",
        "KIMI_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "CLUB_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)
    assert default_model_id() is None
    assert provider_from_env() is None
