import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from coworker.inbox import Inbox
from coworker.persona import ManifestError, Persona, load_persona
from coworker.provider import FakeProvider, ToolCall
from coworker.server import TOKEN_HEADER, create_app
from coworker.store import ConversationStore
from coworker.turn import run_turn


TOKEN = "effective-tools-token"


class RecordingToolsProvider(FakeProvider):
    def __init__(self):
        super().__init__(deltas=("Ready.",))
        self.tool_catalogs = []

    async def astream(self, *, messages, tools=None, context_id=None):
        self.tool_catalogs.append(list(tools or []))
        async for chunk in super().astream(
            messages=messages, tools=tools, context_id=context_id
        ):
            yield chunk


def _names(catalog):
    return tuple(item.name for item in catalog.capabilities)


def test_composition_root_uses_fresh_connector_and_workspace_availability(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("APOLLO_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    app = create_app(token=TOKEN, provider=FakeProvider(), state=tmp_path)

    initial = _names(app.state.effective_tool_catalog())
    assert "gmail_search" not in initial
    assert "drive_read" not in initial
    assert "calendar_list" not in initial
    assert "apollo_search_people" not in initial
    assert "web_search" not in initial
    assert "fs_read" not in initial
    assert "shell_exec" not in initial
    assert "request_directory" in initial

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app.state.workspace_runtime.add_grant(
        workspace,
        label="Workspace",
        access="read_write",
        allow_shell=False,
    )
    with_workspace = _names(app.state.effective_tool_catalog())
    assert "fs_read" in with_workspace
    assert "fs_write" in with_workspace
    assert "shell_exec" not in with_workspace


def test_prompt_diagnostics_report_only_effective_names_and_classes(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-live-PLANTED-SECRET")
    monkeypatch.delenv("APOLLO_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    app = create_app(token=TOKEN, provider=FakeProvider(), state=tmp_path)
    sid = app.state.store.open_session_id()

    response = TestClient(app).get(
        f"/v1/sessions/{sid}/prompt/current",
        headers={TOKEN_HEADER: TOKEN},
    )

    assert response.status_code == 200
    tools = response.json()["effective_tools"]
    assert tools
    assert set(tools[0]) == {"name", "approval_class"}
    encoded = json.dumps(tools)
    assert "parameters" not in encoded
    assert "description" not in encoded
    assert "PLANTED-SECRET" not in encoded


def test_each_turn_receives_the_same_effective_schemas_reported_by_diagnostics(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("APOLLO_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    provider = RecordingToolsProvider()
    app = create_app(token=TOKEN, provider=provider, state=tmp_path)
    sid = app.state.store.open_session_id()
    client = TestClient(app)

    with client.websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        ws.send_json({"type": "chat", "text": "hello", "session_id": sid})
        while ws.receive_json()["type"] not in {"turn_end", "error"}:
            pass

    effective = app.state.effective_tool_catalog()
    sent_names = tuple(
        schema["function"]["name"] for schema in provider.tool_catalogs[0]
    )
    assert sent_names == effective.names
    assert all(
        "Runtime approval:" in schema["function"]["description"]
        for schema in provider.tool_catalogs[0]
    )


def test_switching_to_buddy_narrows_effective_tools_without_registry_broadening(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("APOLLO_API_KEY", "fake-apollo")
    app = create_app(token=TOKEN, provider=FakeProvider(), state=tmp_path)
    client = TestClient(app)
    sourcing = _names(app.state.effective_tool_catalog())
    assert "apollo_search_people" in sourcing
    assert "board_query" in sourcing

    response = client.post(
        "/v1/settings/persona",
        headers={TOKEN_HEADER: TOKEN},
        json={"id": "buddy"},
    )

    assert response.status_code == 200
    buddy = _names(app.state.effective_tool_catalog())
    assert "apollo_search_people" not in buddy
    assert "board_query" not in buddy
    assert set(buddy) < set(sourcing)


def test_provider_call_outside_supplied_catalog_never_executes(tmp_path):
    store = ConversationStore(tmp_path)
    store.create_session("catalog-guard")
    provider = FakeProvider(
        steps=[
            {
                "tool_calls": [
                    ToolCall(
                        id="not-available",
                        name="remember",
                        arguments={"content": "must not persist"},
                    )
                ]
            },
            {"deltas": ("Done.",)},
        ]
    )

    result = asyncio.run(
        run_turn(
            text="remember this",
            sid="catalog-guard",
            store=store,
            provider=provider,
            persona=load_persona("sourcing"),
            skills=None,
            inbox=Inbox(store),
            openai_tools=[],
            execute_kwargs={"store": store},
        )
    )

    assert result["status"] == "partial"
    assert store.list_memories() == []
    tool_result = next(
        message for message in store.load("catalog-guard") if message["role"] == "tool"
    )
    assert "not available in this run" in tool_result["content"]


def test_invalid_builtin_persona_declaration_fails_startup(tmp_path, monkeypatch):
    import coworker.server as server

    monkeypatch.setattr(
        server,
        "load_persona",
        lambda _persona_id: Persona(
            id="sourcing",
            name="Sourcing",
            body="",
            tools=["gmail_sned"],
        ),
    )

    with pytest.raises(ManifestError, match="unknown persona tool: gmail_sned"):
        create_app(token=TOKEN, provider=FakeProvider(), state=tmp_path)


def test_static_system_prompt_contains_no_manual_tool_inventory(tmp_path):
    from coworker.server import system_prompt

    prompt = system_prompt(ConversationStore(tmp_path))

    assert "gmail_send" not in prompt
    assert "fs_write" not in prompt
    assert "apollo_search_people" not in prompt
    assert "Use only the tools actually available in this run" in prompt
