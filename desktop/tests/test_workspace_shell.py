import os
import time
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

from coworker.workspace import WorkspaceGrantStore
from coworker.workspace_files import (
    StaleWorkspaceWrite,
    WorkspaceApprovalRequired,
    WorkspaceFilesystem,
)
from coworker.workspace_shell import DockerSandbox, ShellRuntime, ShellTaskStore


def shell_runtime(tmp_path, *, allow_shell=True):
    state = tmp_path / "state"
    root = tmp_path / "workspace"
    root.mkdir()
    (root / ".env").write_text("TOKEN=secret")
    (root / "public.txt").symlink_to(root / ".env")
    grants = WorkspaceGrantStore(state)
    grant = grants.add(
        root,
        label="Workspace",
        access="read_write",
        allow_shell=allow_shell,
    )
    files = WorkspaceFilesystem(grants, state_root=state)
    runtime = ShellRuntime(
        state_root=state,
        grants=grants,
        files=files,
        docker=DockerSandbox(docker_binary=None),
    )
    return state, root, grant, runtime


def approved_shell(runtime, grant_id, command, *, environment=None, **options):
    decision = runtime.decide(
        grant_id=grant_id,
        command=command,
        cwd=".",
        environment=environment or {},
    )
    return runtime.exec(
        grant_id=grant_id,
        command=command,
        cwd=".",
        environment=environment or {},
        approved=True,
        approval_fingerprint=decision.fingerprint["digest"],
        **options,
    )


def test_docker_create_command_mounts_only_grants_with_hardening_and_full_network(
    tmp_path,
):
    state = tmp_path / "state"
    primary_root = tmp_path / "primary"
    readonly_root = tmp_path / "readonly"
    primary_root.mkdir()
    readonly_root.mkdir()
    grants = WorkspaceGrantStore(state)
    primary = grants.add(
        primary_root,
        label="Primary",
        access="read_write",
        allow_shell=True,
    )
    readonly = grants.add(readonly_root, label="Reference", access="read_only")
    broad = grants.add(tmp_path, label="Broad reference", access="read_only")
    sandbox = DockerSandbox(docker_binary="docker", image="sourcecado-sandbox:test")

    command = sandbox.build_create_command(
        container_name="sourcecado-test",
        primary=primary,
        grants=[primary, readonly, broad],
        uid=501,
        gid=20,
        protected_roots=[state],
    )
    rendered = " ".join(command)

    assert f"{primary_root}:/workspace:rw" in command
    assert f"{readonly_root}:/grants/{readonly['id']}:ro" in command
    assert f"{tmp_path}:/grants/{broad['id']}:ro" not in command
    assert "--cap-drop ALL" in rendered
    assert "--security-opt no-new-privileges" in rendered
    assert "--pids-limit 128" in rendered
    assert "--memory 1g" in rendered
    assert "--cpus 2" in rendered
    assert "--user 501:20" in rendered
    assert "--read-only" in command
    assert "--network" not in command
    assert str(state) not in rendered
    assert "/var/run/docker.sock" not in rendered
    assert "sourcecado-sandbox:test" in command


def test_docker_rejects_an_identity_changed_secondary_mount(tmp_path):
    state = tmp_path / "state"
    primary_root = tmp_path / "primary"
    secondary_root = tmp_path / "secondary"
    outside_root = tmp_path / "outside"
    primary_root.mkdir()
    secondary_root.mkdir()
    outside_root.mkdir()
    grants = WorkspaceGrantStore(state)
    primary = grants.add(
        primary_root, label="Primary", access="read_write", allow_shell=True
    )
    secondary = grants.add(secondary_root, label="Secondary", access="read_only")
    secondary_root.rmdir()
    secondary_root.symlink_to(outside_root, target_is_directory=True)

    with pytest.raises(RuntimeError, match="identity"):
        DockerSandbox(docker_binary="docker").build_create_command(
            container_name="sourcecado-test",
            primary=primary,
            grants=[primary, secondary],
            uid=501,
            gid=20,
            protected_roots=[state],
        )


def test_docker_exec_passes_the_exact_command_without_outer_shell_expansion():
    sandbox = DockerSandbox(docker_binary="docker")
    command = "printf '$HOME' $(pwd)"

    invocation = sandbox.exec_command(
        container="sourcecado-test",
        task_id="task-safe",
        command=command,
        cwd="/workspace",
        environment={},
    )

    assert shlex.quote(command) in invocation[-1]


def test_docker_diagnostics_rejects_empty_success_with_connection_error(monkeypatch):
    def disconnected(*_args, **_kwargs):
        return subprocess.CompletedProcess(
            args=["docker"],
            returncode=0,
            stdout="\n",
            stderr="Cannot connect to the Docker daemon",
        )

    monkeypatch.setattr(subprocess, "run", disconnected)

    diagnostics = DockerSandbox(docker_binary="docker").diagnostics()

    assert diagnostics["daemon_available"] is False
    assert diagnostics["available"] is False


def test_live_docker_sandbox_mounts_workspace_without_host_state_when_available(
    tmp_path,
):
    sandbox = DockerSandbox()
    if not sandbox.diagnostics()["available"]:
        pytest.skip("Docker daemon and sandbox image are not available")
    state = tmp_path / "state"
    root = tmp_path / "workspace"
    root.mkdir()
    grants = WorkspaceGrantStore(state)
    grant = grants.add(root, label="Workspace", access="read_write", allow_shell=True)
    container = sandbox.ensure_container(
        grant,
        grants.list_active(),
        protected_roots=[state],
    )
    invocation = sandbox.exec_command(
        container=container,
        task_id="live-docker-test",
        command=(
            'test "$HOME" = /tmp/sourcecado-home && '
            "test ! -S /var/run/docker.sock && "
            "test -r /proc/net/route && "
            "printf mounted > /workspace/docker.txt"
        ),
        cwd="/workspace",
        environment={},
    )
    try:
        completed = subprocess.run(
            invocation, capture_output=True, timeout=15, check=False
        )
        assert completed.returncode == 0
        assert (root / "docker.txt").read_text() == "mounted"
    finally:
        sandbox.close()


def test_host_fallback_requires_approval_even_for_read_only_and_warns_when_executed(
    tmp_path,
):
    _state, root, grant, runtime = shell_runtime(tmp_path)

    decision = runtime.decide(
        grant_id=grant["id"], command="pwd", cwd=".", environment={}
    )
    assert decision.execution_target == "host"
    assert decision.needs_approval is True
    assert "not sandboxed" in decision.reason

    result = approved_shell(runtime, grant["id"], "pwd")
    assert result["status"] == "succeeded"
    assert result["execution_target"] == "host"
    assert result["unsandboxed"] is True
    assert result["output"].strip() == str(root)
    assert result["exit_code"] == 0


def test_docker_read_only_auto_classification_rejects_environment_injection(tmp_path):
    class AvailableDocker(DockerSandbox):
        def diagnostics(self):
            return {
                "cli_available": True,
                "daemon_available": True,
                "image_available": True,
                "available": True,
                "image": "test",
                "network": "unrestricted",
            }

    state = tmp_path / "state"
    root = tmp_path / "workspace"
    root.mkdir()
    grants = WorkspaceGrantStore(state)
    grant = grants.add(root, label="Workspace", access="read_write", allow_shell=True)
    runtime = ShellRuntime(
        state_root=state,
        grants=grants,
        files=WorkspaceFilesystem(grants, state_root=state),
        docker=AvailableDocker(docker_binary="docker"),
    )

    assert (
        runtime.decide(
            grant_id=grant["id"], command="pwd", cwd=".", environment={}
        ).allowed
        is True
    )
    injected = runtime.decide(
        grant_id=grant["id"],
        command="git diff",
        cwd=".",
        environment={"GIT_EXTERNAL_DIFF": "writer"},
    )
    assert injected.needs_approval is True
    protected = runtime.decide(
        grant_id=grant["id"],
        command="cat .env",
        cwd=".",
        environment={},
    )
    assert protected.needs_approval is True
    aliased = runtime.decide(
        grant_id=grant["id"],
        command="cat public.txt",
        cwd=".",
        environment={},
    )
    assert aliased.needs_approval is True


def test_shell_output_is_bounded_and_persistent_metadata_contains_no_output(tmp_path):
    state, _root, grant, runtime = shell_runtime(tmp_path)

    result = approved_shell(
        runtime,
        grant["id"],
        "python3 -c 'print(\"x\" * 300000)'",
    )

    assert result["truncated"] is True
    assert len(result["output"]) <= 256 * 1024
    stored = ShellTaskStore(state).get(result["task_id"])
    assert "output" not in stored


def test_host_allow_always_replays_only_while_complete_fingerprint_matches(tmp_path):
    _state, root, grant, runtime = shell_runtime(tmp_path)
    script = root / "inspect.sh"
    script.write_text("#!/bin/sh\nprintf original\n")

    allowed = approved_shell(
        runtime,
        grant["id"],
        "bash inspect.sh",
        approval_scope="always",
        actor="operator",
    )
    assert allowed["output"] == "original"
    assert (
        runtime.decide(
            grant_id=grant["id"],
            command="bash inspect.sh",
            cwd=".",
            environment={},
        ).allowed
        is True
    )
    assert (
        runtime.decide(
            grant_id=grant["id"],
            command="bash inspect.sh",
            cwd=".",
            environment={"MODE": "two"},
        ).needs_approval
        is True
    )
    script.write_text("#!/bin/sh\nprintf changed\n")
    assert (
        runtime.decide(
            grant_id=grant["id"],
            command="bash inspect.sh",
            cwd=".",
            environment={"MODE": "one"},
        ).needs_approval
        is True
    )


def test_host_execution_requires_and_rechecks_the_fingerprint_that_was_approved(
    tmp_path,
):
    _state, root, grant, runtime = shell_runtime(tmp_path)
    script = root / "approved.sh"
    script.write_text("printf original")
    decision = runtime.decide(
        grant_id=grant["id"],
        command="bash approved.sh",
        cwd=".",
        environment={},
    )

    with pytest.raises(WorkspaceApprovalRequired, match="fingerprint"):
        runtime.exec(
            grant_id=grant["id"],
            command="bash approved.sh",
            cwd=".",
            environment={},
            approved=True,
        )

    script.write_text("printf changed")
    with pytest.raises(StaleWorkspaceWrite, match="changed after approval"):
        runtime.exec(
            grant_id=grant["id"],
            command="bash approved.sh",
            cwd=".",
            environment={},
            approved=True,
            approval_fingerprint=decision.fingerprint["digest"],
        )


def test_host_fingerprint_resolves_the_executable_from_the_environment_used_by_popen(
    tmp_path,
):
    _state, _root, grant, runtime = shell_runtime(tmp_path)

    decision = runtime.decide(
        grant_id=grant["id"], command="python3 --version", cwd=".", environment={}
    )

    expected = shutil.which("python3", path=runtime._host_environment({})["PATH"])
    assert decision.fingerprint["executable"]["path"] == str(Path(expected).resolve())


def test_allow_always_rejects_opaque_wrapper_commands(tmp_path):
    _state, root, grant, runtime = shell_runtime(tmp_path)
    (root / "runner").write_text("printf first")
    decision = runtime.decide(
        grant_id=grant["id"],
        command="env MODE=x bash runner",
        cwd=".",
        environment={},
    )

    assert decision.fingerprint["permanent_eligible"] is False
    (root / "runner").write_text("printf changed")
    changed = runtime.decide(
        grant_id=grant["id"],
        command="env MODE=x bash runner",
        cwd=".",
        environment={},
    )
    assert changed.fingerprint["digest"] != decision.fingerprint["digest"]
    with pytest.raises(WorkspaceApprovalRequired, match="permanent"):
        runtime.exec(
            grant_id=grant["id"],
            command="env MODE=x bash runner",
            cwd=".",
            environment={},
            approved=True,
            approval_scope="always",
            approval_fingerprint=changed.fingerprint["digest"],
        )


def test_shell_approval_resource_shows_safe_exact_command_and_environment_keys(
    tmp_path,
):
    _state, _root, grant, runtime = shell_runtime(tmp_path)

    resource = runtime.approval_resource(
        grant_id=grant["id"],
        command="python3 --version",
        cwd=".",
        environment={"MODE": "private-value"},
    )

    assert resource["command_display"] == "python3 --version"
    assert resource["environment_keys"] == ["MODE"]
    assert "private-value" not in str(resource)
    assert resource["permanent_eligible"] is False


def test_background_shell_has_incremental_bounded_output_stdin_and_cancellation(
    tmp_path,
):
    _state, _root, grant, runtime = shell_runtime(tmp_path)
    task = approved_shell(
        runtime,
        grant["id"],
        "read line; printf received-$line; sleep 10",
        background=True,
    )
    assert task["status"] == "running"

    runtime.write_stdin(task["task_id"], "hello\n")
    deadline = time.time() + 3
    poll = runtime.poll(task["task_id"], offset=0)
    while "received-hello" not in poll["output"] and time.time() < deadline:
        time.sleep(0.02)
        poll = runtime.poll(task["task_id"], offset=0)
    assert "received-hello" in poll["output"]
    assert poll["next_offset"] >= len("received-hello")

    killed = runtime.kill(task["task_id"])
    assert killed["status"] == "cancelled"
    assert (
        runtime.poll(task["task_id"], offset=poll["next_offset"])["status"]
        == "cancelled"
    )


def test_shell_task_store_reconciles_unknown_running_tasks_to_interrupted(tmp_path):
    store = ShellTaskStore(tmp_path / "state")
    store.put(
        {
            "task_id": "task-old",
            "grant_id": "grant-old",
            "status": "running",
            "execution_target": "docker",
            "command_summary": "rg · 2 arguments",
            "started_at": "2026-08-26T12:00:00+00:00",
        }
    )

    reconciled = ShellTaskStore(tmp_path / "state", reconcile=True).get("task-old")

    assert reconciled["status"] == "interrupted"
    assert reconciled["exit_code"] is None
    assert "restart" in reconciled["error"]


def test_restart_reconciliation_terminates_a_surviving_host_process(tmp_path):
    state, _root, grant, runtime = shell_runtime(tmp_path)
    task = approved_shell(runtime, grant["id"], "sleep 10", background=True)
    process_id = ShellTaskStore(state).get(task["task_id"])["process_id"]

    ShellRuntime(
        state_root=state,
        grants=runtime.grants,
        files=runtime.files,
        docker=DockerSandbox(docker_binary=None),
    )

    deadline = time.time() + 2
    while time.time() < deadline:
        try:
            os.kill(process_id, 0)
        except ProcessLookupError:
            break
        time.sleep(0.02)
    with pytest.raises(ProcessLookupError):
        os.kill(process_id, 0)


def test_revoking_workspace_stops_its_background_shell_tasks(tmp_path):
    _state, _root, grant, runtime = shell_runtime(tmp_path)
    task = approved_shell(
        runtime,
        grant["id"],
        "sleep 10",
        background=True,
    )

    stopped = runtime.stop_for_grant(grant["id"])

    assert stopped == [task["task_id"]]
    assert runtime.poll(task["task_id"], offset=0)["status"] == "cancelled"
