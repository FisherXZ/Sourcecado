import time

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
                            "command": "pwd",
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
                assert event["resource"]["command_display"] == "pwd"
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
    assert finished["result"]["output"].strip() == str(root)
    assert runtime.shell.approvals.list_all()[0]["revoked_at"] is None
    assert (
        runtime.decide_tool(
            "shell_exec",
            {"grant_id": grant["id"], "command": "pwd", "cwd": "."},
        ).allowed
        is True
    )


def test_shell_approval_persistence_redacts_raw_command_environment_and_locks_state_modes(
    tmp_path,
):
    state = tmp_path / "state"
    root = tmp_path / "workspace"
    root.mkdir()
    runtime = WorkspaceRuntime(state, docker=DockerSandbox(docker_binary=None))
    grant = runtime.add_grant(
        root, label="Workspace", access="read_write", allow_shell=True
    )
    command_secret = "command-secret-938475"
    environment_secret = "environment-secret-294857"
    provider = FakeProvider(
        steps=[
            {
                "tool_calls": [
                    ToolCall(
                        id="shell-secret-call",
                        name="shell_exec",
                        arguments={
                            "grant_id": grant["id"],
                            "command": f"printf {command_secret}",
                            "cwd": ".",
                            "environment": {"TOKEN": environment_secret},
                        },
                    )
                ]
            }
        ]
    )
    app = create_app(
        token=TOKEN,
        state=state,
        provider=provider,
        workspace_runtime=runtime,
    )
    client = TestClient(app)

    with client.websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        ws.send_json({"type": "chat", "text": "run", "session_id": "main"})
        while ws.receive_json()["type"] != "permission_required":
            pass

    persisted = b"\n".join(
        path.read_bytes()
        for path in state.rglob("*")
        if path.is_file() and path.name != "secrets.json"
    )
    assert command_secret.encode() not in persisted
    assert environment_secret.encode() not in persisted
    assert state.stat().st_mode & 0o777 == 0o700
    for path in state.rglob("*"):
        if path.is_dir():
            assert path.stat().st_mode & 0o077 == 0
        elif path.is_file():
            assert path.stat().st_mode & 0o077 == 0


def test_workspace_retry_allow_executes_the_original_write(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    runtime = WorkspaceRuntime(
        tmp_path / "state", docker=DockerSandbox(docker_binary=None)
    )
    grant = runtime.add_grant(
        root, label="Workspace", access="read_write", allow_shell=False
    )
    original = runtime.execute_tool
    calls: list[dict] = []

    def fail_first_write(name, arguments, **kwargs):
        payload = dict(arguments or {})
        calls.append(payload)
        if name == "fs_write" and payload.get("path") == "note.txt" and len(calls) == 1:
            return False, {
                "receipt_type": "denied",
                "status": "failed",
                "error": "disk full",
            }
        return original(name, arguments, **kwargs)

    runtime.execute_tool = fail_first_write
    provider = FakeProvider(
        steps=[
            {
                "tool_calls": [
                    ToolCall(
                        id="fs-write-1",
                        name="fs_write",
                        arguments={
                            "grant_id": grant["id"],
                            "path": "note.txt",
                            "content": "packet-body-secret",
                        },
                    )
                ]
            },
            {"deltas": ("Could not write.",)},
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
        ws.send_json({"type": "chat", "text": "write the note", "session_id": "main"})
        events = []
        while True:
            event = ws.receive_json()
            events.append(event)
            if event["type"] in {"turn_end", "error"}:
                break
        run_id = next(event["run_id"] for event in events if event.get("run_id"))
        ws.send_json(
            {
                "type": "retry_failed_step",
                "session_id": "main",
                "run_id": run_id,
                "call_id": "fs-write-1",
                "command_id": "retry-write-1",
            }
        )
        time.sleep(0.08)
        persisted = app.state.store.load_events("main")
        fresh = next(
            event
            for event in persisted
            if event["type"] == "permission_required"
            and event.get("recovery_command_id") == "retry-write-1"
        )
        ws.send_json({"type": "permission", "id": fresh["id"], "decision": "allow"})
        time.sleep(0.08)

    assert len(calls) == 2
    assert calls[-1]["content"] == "packet-body-secret"
    assert (root / "note.txt").read_text() == "packet-body-secret"
