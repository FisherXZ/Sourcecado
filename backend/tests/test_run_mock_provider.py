"""`python -m coworker.run --mock-provider`: QA with no model key."""

from __future__ import annotations

import socket

import pytest
from fastapi.testclient import TestClient

from coworker.provider import FakeProvider
from coworker.run import MOCK_REPLY, provider_overrides
from coworker.server import create_app

TOKEN = "t" * 64


def test_without_the_flag_the_provider_comes_from_the_environment():
    # No override: create_app builds its provider chain from the model keys.
    assert provider_overrides(False) == {}


def test_two_chats_in_mock_mode_complete_with_no_network_call(tmp_path, monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("mock mode opened a network connection")

    overrides = provider_overrides(True)
    assert isinstance(overrides["provider"], FakeProvider)
    client = TestClient(create_app(token=TOKEN, state=tmp_path, **overrides))
    # Only outbound connections are refused; the test client itself is in-process.
    monkeypatch.setattr(socket.socket, "connect", refuse)

    with client.websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        # Two chats: the mock must answer every one, not only the first.
        for _ in range(2):
            ws.send_json({"type": "chat", "text": "hi"})
            events = []
            while True:
                event = ws.receive_json()
                events.append(event)
                if event["type"] in ("turn_end", "error"):
                    break
            assert events[-1]["type"] == "turn_end", events[-1]
            text = "".join(
                e.get("delta", "") for e in events if e["type"] == "assistant_delta"
            )
            assert text == "".join(MOCK_REPLY)


def test_the_launcher_accepts_the_flag(monkeypatch):
    # The parser is inside main(); stop before the app is built.
    from coworker import run

    seen = {}

    def stop(mock):
        seen["mock"] = mock
        raise SystemExit(0)

    monkeypatch.setattr(run, "provider_overrides", stop)
    monkeypatch.setattr(run, "_load_dotenv", lambda path: None)
    monkeypatch.setattr(run, "ensure_token", lambda port: (TOKEN, None))
    with pytest.raises(SystemExit):
        run.main(["--mock-provider"])
    assert seen == {"mock": True}
