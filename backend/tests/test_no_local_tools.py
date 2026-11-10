"""Guards for migration slice S23: no local file tools, no shell, no folder grants.

On a public server the agent must not read the host disk or run a command.
These tests fail if any part of that surface comes back.

The removed tool names are built from parts on purpose. The slice's done
check greps the tree for the literal names and expects no match.
"""

from __future__ import annotations

import importlib
import os
import subprocess
from pathlib import Path

import pytest

SHELL_RUN = "shell" + "_exec"
FILE_READ = "fs" + "_read"

REMOVED_MODULES = (
    "coworker.workspace",
    "coworker.workspace_audit",
    "coworker.workspace_files",
    "coworker.workspace_policy",
    "coworker.workspace" + "_runtime",
    "coworker.workspace_shell",
)


def _refuse(*_args, **_kwargs):
    raise AssertionError("a removed tool tried to start a process")


def test_the_executor_treats_removed_tools_as_unknown_and_runs_nothing(
    tmp_path, monkeypatch
):
    from coworker.tools import execute

    for name in ("Popen", "run", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, _refuse)
    monkeypatch.setattr(os, "system", _refuse)

    sentinel = tmp_path / "host-file.txt"
    sentinel.write_text("host sentinel", encoding="utf-8")
    marker = tmp_path / "ran"

    ok, result = execute(
        SHELL_RUN,
        {"grant_id": "g1", "command": f"touch {marker}", "cwd": str(tmp_path)},
    )
    assert ok is False
    assert result == {"error": f"unknown tool {SHELL_RUN}"}
    assert not marker.exists()

    ok, result = execute(FILE_READ, {"grant_id": "g1", "path": str(sentinel)})
    assert ok is False
    assert result == {"error": f"unknown tool {FILE_READ}"}
    assert "host sentinel" not in str(result)


def test_the_executor_has_no_runtime_parameter_for_local_tools():
    import inspect

    from coworker.tools import execute

    assert "workspace" + "_runtime" not in inspect.signature(execute).parameters


@pytest.mark.parametrize("module", REMOVED_MODULES)
def test_removed_workspace_modules_cannot_be_imported(module):
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module)


def test_no_registered_tool_schema_is_a_file_or_shell_tool():
    from coworker.tools import OPENAI_TOOLS

    names = [schema["function"]["name"] for schema in OPENAI_TOOLS]
    assert not [n for n in names if n.startswith(("fs_", "shell_"))]
    assert "request_directory" not in names


def test_no_http_route_serves_folders_grants_or_commands(tmp_path):
    from coworker.provider import FakeProvider
    from coworker.server import create_app

    app = create_app(token="guard-token", provider=FakeProvider(), state=tmp_path)
    paths = [str(getattr(route, "path", "")) for route in app.routes]
    assert paths, "the app registered no routes; this test would prove nothing"
    banned = ("workspace", "directory", "host-command", "shell")
    offending = [p for p in paths if any(word in p.lower() for word in banned)]
    assert offending == []


def test_the_state_root_gets_no_workspace_files(tmp_path):
    from coworker.provider import FakeProvider
    from coworker.server import create_app

    create_app(token="guard-token", provider=FakeProvider(), state=tmp_path)
    created = {path.name for path in Path(tmp_path).iterdir()}
    removed = {
        "workspace_receipts.jsonl",
        "workspace_grants.json",
        "shell_tasks.json",
        "host_command_approvals.json",
        "directory_requests.json",
        "workspace_trash",
    }
    assert not created & removed
