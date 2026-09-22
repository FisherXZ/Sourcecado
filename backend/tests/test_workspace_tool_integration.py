from coworker.permissions import decide
from coworker.tools import OPENAI_TOOLS, execute
from coworker.workspace_runtime import WORKSPACE_TOOL_NAMES, WorkspaceRuntime
from coworker.workspace_shell import DockerSandbox


def configured_runtime(tmp_path):
    state = tmp_path / "state"
    root = tmp_path / "workspace"
    root.mkdir()
    runtime = WorkspaceRuntime(state, docker=DockerSandbox(docker_binary=None))
    grant = runtime.add_grant(
        root,
        label="Workspace",
        access="read_write",
        allow_shell=True,
    )
    return root, grant, runtime


def test_workspace_schemas_are_exposed_to_the_model():
    names = {schema["function"]["name"] for schema in OPENAI_TOOLS}
    assert WORKSPACE_TOOL_NAMES <= names


def test_permissions_delegate_workspace_risk_to_the_runtime(tmp_path):
    root, grant, runtime = configured_runtime(tmp_path)
    (root / "existing.txt").write_text("existing")

    read = decide(
        "fs_read",
        {"grant_id": grant["id"], "path": "existing.txt"},
        workspace_runtime=runtime,
    )
    overwrite = decide(
        "fs_write",
        {"grant_id": grant["id"], "path": "existing.txt", "content": "new"},
        workspace_runtime=runtime,
    )
    host_shell = decide(
        "shell_exec",
        {"grant_id": grant["id"], "command": "pwd", "cwd": "."},
        workspace_runtime=runtime,
    )

    assert read.allowed is True
    assert overwrite.needs_user is True
    assert host_shell.needs_user is True
    assert "not sandboxed" in host_shell.reason


def test_tool_dispatch_executes_workspace_calls_and_respects_approval_context(tmp_path):
    root, grant, runtime = configured_runtime(tmp_path)
    args = {"grant_id": grant["id"], "path": "note.txt", "content": "first"}

    ok, created = execute("fs_write", args, workspace_runtime=runtime)
    assert ok is True
    assert created["receipt_type"] == "created"

    ok, denied = execute(
        "fs_write",
        {**args, "content": "second"},
        workspace_runtime=runtime,
    )
    assert ok is False
    assert denied["status"] == "approval_required"

    ok, updated = execute(
        "fs_write",
        {**args, "content": "second"},
        workspace_runtime=runtime,
        approval_granted=True,
        approval_scope="once",
        actor="operator",
    )
    assert ok is True
    assert updated["receipt_type"] == "updated"
    assert (root / "note.txt").read_text() == "second"
