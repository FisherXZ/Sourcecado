from fastapi.testclient import TestClient

from coworker.mcp import FakeMcp
from coworker.provider import FakeProvider, ToolCall
from coworker.server import TOKEN_HEADER, create_app
from coworker.tools import execute

TOKEN = "test-token-mcp"


def _drain(ws):
    events = []
    while True:
        ev = ws.receive_json()
        events.append(ev)
        if ev["type"] in ("turn_end", "error"):
            return events


def test_mcp_echo_is_not_gmail():
    mcp = FakeMcp(
        [
            {
                "name": "mcp__echo__ping",
                "description": "Echo ping",
                "handler": lambda args: {"pong": args.get("msg"), "gmail": False},
            }
        ]
    )
    ok, result = execute("mcp__echo__ping", {"msg": "hi"}, mcp=mcp)
    assert ok is True
    assert result["pong"] == "hi"
    assert result["gmail"] is False
    assert mcp.calls[0]["name"] == "mcp__echo__ping"


def test_ws_mcp_tool_runs(tmp_path):
    mcp = FakeMcp(
        [
            {
                "name": "mcp__echo__ping",
                "description": "Echo ping",
                "parameters": {
                    "type": "object",
                    "properties": {"msg": {"type": "string"}},
                },
                "handler": lambda args: {"pong": args.get("msg")},
            }
        ]
    )
    fake = FakeProvider(
        steps=[
            {
                "tool_calls": [
                    ToolCall(id="c1", name="mcp__echo__ping", arguments={"msg": "hi"})
                ]
            },
            {"deltas": ("pong hi",)},
        ]
    )
    client = TestClient(
        create_app(token=TOKEN, provider=fake, state=tmp_path, mcp=mcp)
    )
    with client.websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        ws.send_json({"type": "chat", "text": "ping mcp"})
        events = _drain(ws)
    finished = next(e for e in events if e["type"] == "tool_finished")
    assert finished["ok"] is True
    assert finished["result"]["pong"] == "hi"
    assert "permission_required" not in [e["type"] for e in events]
    assert mcp.calls[0]["name"] == "mcp__echo__ping"
