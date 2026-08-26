from coworker.workspace_policy import RiskClass
from coworker.workspace_runtime import (
    WORKSPACE_TOOL_NAMES,
    WORKSPACE_TOOL_SCHEMAS,
    WorkspaceRuntime,
)
from coworker.workspace_shell import DockerSandbox
from coworker.workspace_shell import ShellTaskStore


def runtime_with_grant(tmp_path):
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
    return state, root, grant, runtime


def test_workspace_tool_catalog_is_sourcecado_owned_and_complete():
    names = {schema["function"]["name"] for schema in WORKSPACE_TOOL_SCHEMAS}
    assert names == WORKSPACE_TOOL_NAMES
    assert names == {
        "fs_stat",
        "fs_list",
        "fs_find",
        "fs_search",
        "fs_read",
        "fs_mkdir",
        "fs_write",
        "fs_patch",
        "fs_copy",
        "fs_move",
        "fs_trash",
        "request_directory",
        "shell_exec",
        "shell_poll",
        "shell_write_stdin",
        "shell_kill",
    }


def test_workspace_runtime_auto_allows_reads_and_version_checked_reversible_writes(
    tmp_path,
):
    _state, root, grant, runtime = runtime_with_grant(tmp_path)
    (root / "read.txt").write_text("read me")

    read = runtime.decide_tool("fs_read", {"grant_id": grant["id"], "path": "read.txt"})
    assert read.allowed is True
    assert read.risk_class == RiskClass.READ

    create_args = {
        "grant_id": grant["id"],
        "path": "created.txt",
        "content": "v1",
    }
    assert runtime.decide_tool("fs_write", create_args).allowed is True
    ok, created = runtime.execute_tool("fs_write", create_args)
    assert ok is True
    assert created["receipt_type"] == "created"

    unversioned = runtime.decide_tool(
        "fs_write",
        {**create_args, "content": "v2"},
    )
    assert unversioned.needs_approval is True
    versioned_args = {
        **create_args,
        "content": "v2",
        "expected_before_hash": created["after_hash"],
    }
    assert runtime.decide_tool("fs_write", versioned_args).allowed is True
    ok, updated = runtime.execute_tool("fs_write", versioned_args)
    assert ok is True
    assert updated["receipt_type"] == "updated"


def test_workspace_runtime_requires_approval_for_protected_conflicting_and_destructive_writes(
    tmp_path,
):
    _state, root, grant, runtime = runtime_with_grant(tmp_path)
    (root / ".env").write_text("MODE=old")
    protected = runtime.decide_tool(
        "fs_write",
        {"grant_id": grant["id"], "path": ".env", "content": "MODE=new"},
    )
    assert protected.needs_approval is True
    assert "protected" in protected.reason
    protected_read = runtime.decide_tool(
        "fs_read", {"grant_id": grant["id"], "path": ".env"}
    )
    assert protected_read.needs_approval is True
    ok, search = runtime.execute_tool(
        "fs_search",
        {"grant_id": grant["id"], "path": ".", "query": "MODE=old"},
    )
    assert ok is True
    assert search["matches"] == []
    trash = runtime.decide_tool("fs_trash", {"grant_id": grant["id"], "path": ".env"})
    assert trash.risk_class == RiskClass.DESTRUCTIVE_WRITE
    assert trash.needs_approval is True

    source = runtime.execute_tool(
        "fs_write",
        {"grant_id": grant["id"], "path": "source.txt", "content": "source"},
    )[1]
    runtime.execute_tool(
        "fs_write",
        {"grant_id": grant["id"], "path": "destination.txt", "content": "destination"},
    )
    move = runtime.decide_tool(
        "fs_move",
        {
            "grant_id": grant["id"],
            "path": "source.txt",
            "destination_path": "destination.txt",
            "expected_source_hash": source["after_hash"],
        },
    )
    assert move.needs_approval is True
    assert "overwrite" in move.reason


def test_workspace_runtime_directory_request_creates_no_authority_until_operator_selects(
    tmp_path,
):
    state = tmp_path / "state"
    runtime = WorkspaceRuntime(state, docker=DockerSandbox(docker_binary=None))

    decision = runtime.decide_tool(
        "request_directory",
        {"label": "Candidate packets", "access": "read_write", "allow_shell": True},
    )
    assert decision.allowed is True
    assert decision.risk_class == RiskClass.BOUNDARY_EXPANSION
    ok, result = runtime.execute_tool(
        "request_directory",
        {"label": "Candidate packets", "access": "read_write", "allow_shell": True},
        session_id="session-1",
        run_id="run-1",
    )

    assert ok is True
    assert result["receipt_type"] == "directory_requested"
    assert runtime.grants.list_active() == []
    assert runtime.directory_requests.pending()[0]["id"] == result["request_id"]


def test_workspace_runtime_records_sanitized_execution_receipts(tmp_path):
    _state, _root, grant, runtime = runtime_with_grant(tmp_path)
    args = {
        "grant_id": grant["id"],
        "path": "note.txt",
        "content": "TOKEN=body-secret",
    }
    runtime.decide_tool(
        "fs_write", args, actor="assistant", session_id="s1", run_id="r1"
    )
    ok, result = runtime.execute_tool(
        "fs_write", args, actor="assistant", session_id="s1", run_id="r1"
    )

    assert ok is True
    receipts = runtime.audit.list(limit=10)
    assert {receipt["receipt_type"] for receipt in receipts} >= {"proposal", "created"}
    assert "body-secret" not in str(receipts)
    assert any(
        receipt.get("after_hash") == result["after_hash"] for receipt in receipts
    )


def test_workspace_runtime_records_restart_reconciliation_as_interrupted(tmp_path):
    state = tmp_path / "state"
    ShellTaskStore(state).put(
        {
            "task_id": "task-restart",
            "grant_id": "grant-restart",
            "status": "running",
            "execution_target": "docker",
            "command_summary": "rg · 2 arguments",
            "command_fingerprint": "fingerprint",
            "started_at": "2026-08-26T12:00:00+00:00",
        }
    )

    runtime = WorkspaceRuntime(state, docker=DockerSandbox(docker_binary=None))

    receipt = runtime.audit.list(limit=10)[0]
    assert receipt["receipt_type"] == "interrupted"
    assert receipt["task_id"] == "task-restart"
    assert receipt["status"] == "interrupted"


def test_stale_shell_approval_receipt_preserves_the_host_execution_target(tmp_path):
    _state, root, grant, runtime = runtime_with_grant(tmp_path)
    script = root / "inspect.sh"
    script.write_text("printf first")
    arguments = {
        "grant_id": grant["id"],
        "command": "bash inspect.sh",
        "cwd": ".",
    }
    decision = runtime.decide_tool("shell_exec", arguments)
    script.write_text("printf changed")

    ok, result = runtime.execute_tool(
        "shell_exec",
        arguments,
        approval_granted=True,
        approval_fingerprint=decision.command_fingerprint,
    )

    assert ok is False
    assert result["receipt_type"] == "stale"
    receipt = runtime.audit.list(limit=1)[0]
    assert receipt["execution_target"] == "host"


def test_workspace_runtime_sanitizes_low_level_filesystem_failures(
    tmp_path, monkeypatch
):
    _state, root, grant, runtime = runtime_with_grant(tmp_path)
    (root / "note.txt").write_text("note")

    def fail_read(*_args, **_kwargs):
        raise OSError("disk failure at /private/operator/secret.txt")

    monkeypatch.setattr(runtime.files, "read", fail_read)
    ok, result = runtime.execute_tool(
        "fs_read", {"grant_id": grant["id"], "path": "note.txt"}
    )

    assert ok is False
    assert result["error"] == "workspace operation failed"
    assert "/private/operator" not in str(runtime.audit.list(limit=10))
