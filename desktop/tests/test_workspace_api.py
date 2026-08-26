from fastapi.testclient import TestClient

from coworker.provider import FakeProvider
from coworker.provider import ToolCall
from coworker.server import TOKEN_HEADER, create_app
from coworker.workspace_runtime import WorkspaceRuntime
from coworker.workspace_shell import DockerSandbox


TOKEN = "workspace-api-token"


def app_and_client(tmp_path):
    runtime = WorkspaceRuntime(
        tmp_path / "state", docker=DockerSandbox(docker_binary=None)
    )
    app = create_app(
        token=TOKEN,
        state=tmp_path / "state",
        provider=FakeProvider(),
        workspace_runtime=runtime,
    )
    return app, TestClient(app)


def auth():
    return {TOKEN_HEADER: TOKEN}


def test_workspace_grant_api_creates_updates_lists_and_revokes_authority(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    app, client = app_and_client(tmp_path)

    created = client.post(
        "/v1/workspaces",
        headers=auth(),
        json={
            "path": str(root),
            "label": "Candidate packets",
            "access": "read_write",
            "allow_shell": True,
        },
    )
    assert created.status_code == 201
    grant = created.json()["grant"]
    assert grant["path"] == str(root.resolve())

    listed = client.get("/v1/workspaces", headers=auth()).json()
    assert listed["grants"] == [grant]
    assert listed["docker"]["available"] is False
    assert listed["execution_target"] == "host_fallback"

    changed = client.patch(
        f"/v1/workspaces/{grant['id']}",
        headers=auth(),
        json={"label": "Packets", "access": "read_write", "allow_shell": False},
    )
    assert changed.status_code == 200
    assert changed.json()["grant"]["label"] == "Packets"
    assert changed.json()["grant"]["allow_shell"] is False

    revoked = client.delete(f"/v1/workspaces/{grant['id']}", headers=auth())
    assert revoked.status_code == 200
    assert revoked.json()["grant"]["revoked_at"] is not None
    assert app.state.workspace_runtime.grants.list_active() == []


def test_settings_exposes_safe_runtime_diagnostics_and_receipts(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    app, client = app_and_client(tmp_path)
    grant = app.state.workspace_runtime.add_grant(
        root, label="Workspace", access="read_write"
    )
    app.state.workspace_runtime.execute_tool(
        "fs_write",
        {"grant_id": grant["id"], "path": "note.txt", "content": "secret body"},
    )

    settings = client.get("/v1/settings", headers=auth()).json()
    assert settings["workspace"]["grants"][0]["id"] == grant["id"]
    assert settings["workspace"]["host_fallback_enabled"] is True
    receipts = client.get("/v1/workspaces/receipts", headers=auth()).json()["receipts"]
    assert receipts[0]["receipt_type"] == "created"
    assert "secret body" not in str(receipts)


def test_host_approval_api_lists_and_revokes_permanent_exact_grants(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    app, client = app_and_client(tmp_path)
    grant = app.state.workspace_runtime.add_grant(
        root,
        label="Workspace",
        access="read_write",
        allow_shell=True,
    )
    decision = app.state.workspace_runtime.decide_tool(
        "shell_exec",
        {"grant_id": grant["id"], "command": "pwd", "cwd": "."},
    )
    app.state.workspace_runtime.execute_tool(
        "shell_exec",
        {"grant_id": grant["id"], "command": "pwd", "cwd": "."},
        approval_granted=True,
        approval_scope="always",
        approval_fingerprint=decision.command_fingerprint,
        actor="operator",
    )
    approval = app.state.workspace_runtime.shell.approvals.list_all()[0]

    listed = client.get("/v1/workspaces/host-approvals", headers=auth()).json()
    assert listed["approvals"][0]["id"] == approval["id"]
    revoked = client.delete(
        f"/v1/workspaces/host-approvals/{approval['id']}", headers=auth()
    )
    assert revoked.status_code == 200
    assert revoked.json()["approval"]["revoked_at"] is not None


def test_websocket_allow_always_persists_exact_host_shell_authority(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    runtime = WorkspaceRuntime(
        tmp_path / "state", docker=DockerSandbox(docker_binary=None)
    )
    grant = runtime.add_grant(
        root,
        label="Workspace",
        access="read_write",
        allow_shell=True,
    )
    provider = FakeProvider(
        steps=[
            {
                "tool_calls": [
                    ToolCall(
                        id="shell-call",
                        name="shell_exec",
                        arguments={
                            "grant_id": grant["id"],
                            "command": "printf approved-host",
                            "cwd": ".",
                        },
                    )
                ]
            },
            {"deltas": ("Done.",)},
        ]
    )
    app = create_app(
        token=TOKEN,
        state=tmp_path / "state",
        provider=provider,
        workspace_runtime=runtime,
    )
    client = TestClient(app)

    with client.websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        ws.send_json({"type": "chat", "text": "run it", "session_id": "main"})
        events = []
        while True:
            event = ws.receive_json()
            events.append(event)
            if event["type"] == "permission_required":
                assert event["resource"]["unsandboxed"] is True
                assert "approved-host" not in str(event["resource"])
                ws.send_json(
                    {
                        "type": "permission",
                        "id": event["id"],
                        "decision": "allow",
                        "scope": "always",
                    }
                )
            if event["type"] in {"turn_end", "error"}:
                break

    assert events[-1]["type"] == "turn_end"
    finished = next(event for event in events if event["type"] == "tool_finished")
    assert finished["result"]["output"] == "approved-host"
    assert runtime.shell.approvals.list_all()[0]["revoked_at"] is None
    assert (
        runtime.decide_tool(
            "shell_exec",
            {"grant_id": grant["id"], "command": "printf approved-host", "cwd": "."},
        ).allowed
        is True
    )
