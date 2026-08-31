"""Regression coverage for coworker.run's loopback guard and per-launch token.

Packaging the backend into a bundled macOS artifact must not weaken either
guarantee: the backend only ever binds loopback, and each launch gets its own
in-memory token rather than a fixed or shared secret.
"""

import pytest

from coworker.run import TOKEN_ENV, ensure_token, main


def test_main_refuses_to_bind_off_loopback(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--host", "0.0.0.0"])
    assert exc.value.code == 2
    assert "loopback" in capsys.readouterr().err


def test_main_refuses_an_arbitrary_hostname(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--host", "example.com"])
    assert exc.value.code == 2
    assert "loopback" in capsys.readouterr().err


def test_ensure_token_generates_a_fresh_random_token_per_launch(tmp_path, monkeypatch):
    monkeypatch.setenv("CLUB_STATE_DIR", str(tmp_path))
    monkeypatch.delenv(TOKEN_ENV, raising=False)

    token_a, path_a = ensure_token(39991)
    assert path_a is not None
    assert path_a.read_text().strip() == token_a
    assert path_a.stat().st_mode & 0o777 == 0o600
    assert len(token_a) == 64  # secrets.token_hex(32)

    # A second launch (fresh environment) must not reuse the first token.
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    token_b, _ = ensure_token(39992)
    assert token_b != token_a


def test_ensure_token_reuses_an_already_injected_token(monkeypatch):
    monkeypatch.setenv(TOKEN_ENV, "preset-token")
    token, path = ensure_token(39993)
    assert token == "preset-token"
    assert path is None
