import asyncio

from coworker.inbox import Inbox
from coworker.provider import FakeProvider, ToolCall
from coworker.store import ConversationStore
from coworker.tools import OPENAI_TOOLS
from coworker.turn import approval_resource, run_turn
from coworker.workspace_runtime import WorkspaceRuntime
from coworker.workspace_shell import DockerSandbox


def runtime_with_file(tmp_path):
    state = tmp_path / "state"
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "existing.txt").write_text("old")
    runtime = WorkspaceRuntime(state, docker=DockerSandbox(docker_binary=None))
    grant = runtime.add_grant(
        root,
        label="Workspace",
        access="read_write",
        allow_shell=True,
    )
    return state, root, grant, runtime


def test_turn_uses_argument_aware_workspace_policy_and_parks_conflicting_write(
    tmp_path,
):
    state, _root, grant, runtime = runtime_with_file(tmp_path)
    store = ConversationStore(state)
    events = []
    provider = FakeProvider(
        steps=[
            {
                "tool_calls": [
                    ToolCall(
                        id="fs-write-1",
                        name="fs_write",
                        arguments={
                            "grant_id": grant["id"],
                            "path": "existing.txt",
                            "content": "new",
                        },
                    )
                ]
            }
        ]
    )

    result = asyncio.run(
        run_turn(
            text="update it",
            sid="session-1",
            store=store,
            provider=provider,
            persona=None,
            skills=None,
            inbox=Inbox(store),
            openai_tools=OPENAI_TOOLS,
            execute_kwargs={"workspace_runtime": runtime},
            emit=lambda event: _append(events, event),
            wait_permission=None,
        )
    )

    assert result["status"] == "waiting"
    permission = next(
        event for event in events if event["type"] == "permission_required"
    )
    assert permission["name"] == "fs_write"
    assert "overwrite" in permission["reason"]


def test_shell_host_fallback_approval_resource_is_safe_and_explicit(tmp_path):
    _state, root, grant, runtime = runtime_with_file(tmp_path)

    resource = approval_resource(
        "shell_exec",
        {
            "grant_id": grant["id"],
            "command": "python3 --version",
            "cwd": ".",
            "environment": {"TOKEN": "secret-environment-value"},
        },
        gmail=None,
        workspace_runtime=runtime,
    )

    assert resource["kind"] == "shell_command"
    assert resource["execution_target"] == "host"
    assert resource["unsandboxed"] is True
    assert resource["cwd"] == str(root)
    assert resource["command_display"] == "python3 --version"
    assert resource["environment_keys"] == ["TOKEN"]
    assert "secret-environment-value" not in str(resource)


def test_denied_workspace_approval_is_recorded_in_the_workspace_receipt_log(tmp_path):
    state, _root, grant, runtime = runtime_with_file(tmp_path)
    store = ConversationStore(state)
    provider = FakeProvider(
        steps=[
            {
                "tool_calls": [
                    ToolCall(
                        id="fs-denied",
                        name="fs_write",
                        arguments={
                            "grant_id": grant["id"],
                            "path": "existing.txt",
                            "content": "new",
                        },
                    )
                ]
            },
            {"deltas": ("Kept the existing file.",)},
        ]
    )

    async def deny(_call_id):
        return "deny"

    asyncio.run(
        run_turn(
            text="update it",
            sid="session-denied",
            store=store,
            provider=provider,
            persona=None,
            skills=None,
            inbox=Inbox(store),
            openai_tools=OPENAI_TOOLS,
            execute_kwargs={"workspace_runtime": runtime},
            wait_permission=deny,
        )
    )

    assert any(
        receipt["receipt_type"] == "denied"
        and receipt["decision"] == "deny"
        and receipt["tool"] == "fs_write"
        for receipt in runtime.audit.list(limit=20)
    )


def test_permanent_exact_host_approval_can_satisfy_an_afk_turn(tmp_path):
    state, _root, grant, runtime = runtime_with_file(tmp_path)
    decision = runtime.decide_tool(
        "shell_exec",
        {"grant_id": grant["id"], "command": "pwd", "cwd": "."},
    )
    runtime.execute_tool(
        "shell_exec",
        {"grant_id": grant["id"], "command": "pwd", "cwd": "."},
        approval_granted=True,
        approval_scope="always",
        approval_fingerprint=decision.command_fingerprint,
        actor="operator",
    )
    store = ConversationStore(state)
    inbox = Inbox(store)
    provider = FakeProvider(
        steps=[
            {
                "tool_calls": [
                    ToolCall(
                        id="standing-shell",
                        name="shell_exec",
                        arguments={
                            "grant_id": grant["id"],
                            "command": "pwd",
                            "cwd": ".",
                        },
                    )
                ]
            },
            {"deltas": ("Finished while you were away.",)},
        ]
    )

    result = asyncio.run(
        run_turn(
            text="run approved command",
            sid="session-afk",
            store=store,
            provider=provider,
            persona=None,
            skills=None,
            inbox=inbox,
            openai_tools=OPENAI_TOOLS,
            execute_kwargs={"workspace_runtime": runtime},
            wait_permission=None,
        )
    )

    assert result["status"] == "ok"
    assert inbox.pending() == []


WORKSPACE_BODY_SHOULD_NOT_HIT_DISK = "WORKSPACE_BODY_SHOULD_NOT_HIT_DISK_9f2a1c"


def test_cancelling_a_later_approval_does_not_write_a_workspace_file_body_to_disk(
    tmp_path,
):
    """Workspace two-tier redaction: model may see the body this turn; disk must not.

    Public seam: run_turn, then ConversationStore.load / load_events.
    """
    state, root, grant, runtime = runtime_with_file(tmp_path)
    (root / "secret.txt").write_text(WORKSPACE_BODY_SHOULD_NOT_HIT_DISK)
    store = ConversationStore(state)
    sid = "session-cancel-after-read"
    provider = FakeProvider(
        steps=[
            {
                "tool_calls": [
                    ToolCall(
                        id="fs-read-1",
                        name="fs_read",
                        arguments={
                            "grant_id": grant["id"],
                            "path": "secret.txt",
                        },
                    )
                ]
            },
            {
                "tool_calls": [
                    ToolCall(
                        id="fs-write-1",
                        name="fs_write",
                        arguments={
                            "grant_id": grant["id"],
                            "path": "existing.txt",
                            "content": "new",
                        },
                    )
                ]
            },
        ]
    )

    async def cancel(_call_id):
        return "cancel"

    result = asyncio.run(
        run_turn(
            text="read then write",
            sid=sid,
            store=store,
            provider=provider,
            persona=None,
            skills=None,
            inbox=Inbox(store),
            openai_tools=OPENAI_TOOLS,
            execute_kwargs={"workspace_runtime": runtime},
            wait_permission=cancel,
        )
    )

    assert result["status"] == "stopped"
    durable = str(store.load(sid)) + str(store.load_events(sid))
    assert WORKSPACE_BODY_SHOULD_NOT_HIT_DISK not in durable


async def _append(events, event):
    events.append(event)
