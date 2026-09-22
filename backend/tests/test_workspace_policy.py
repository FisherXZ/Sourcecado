import json

import pytest

from coworker.workspace_policy import (
    HostApprovalStore,
    RiskClass,
    classify_shell,
    command_fingerprint,
)


@pytest.mark.parametrize(
    "command",
    [
        "pwd",
        "ls -la src",
    ],
)
def test_shell_classifier_auto_allows_only_vetted_read_only_commands(command):
    assert classify_shell(command) == RiskClass.VETTED_READ_ONLY_COMMAND


@pytest.mark.parametrize(
    "command",
    [
        "cat README.md > copy.md",
        "git status && rm -rf build",
        "echo $(cat .env)",
        "python -c 'print(1)'",
        "bash script.sh",
        "npm install",
        "curl https://example.com",
        "find . -delete",
        "rg --replace changed candidate .",
        "unknown-binary --version",
        "git diff --ext-diff",
        "git show --textconv HEAD:file",
        "file --compile magic",
        "./ls -la",
        "git status --short",
        "git diff --stat",
        "rg -n candidate .",
        "find . -maxdepth 2 -type f",
        "head -n 20 README.md",
    ],
)
def test_shell_classifier_requires_approval_for_opaque_write_script_or_network_commands(
    command,
):
    assert classify_shell(command) == RiskClass.CONSEQUENTIAL_COMMAND


def test_host_command_fingerprint_binds_command_cwd_env_target_and_script_hash(
    tmp_path,
):
    cwd = tmp_path / "workspace"
    cwd.mkdir()
    script = cwd / "inspect.sh"
    script.write_text("#!/bin/sh\npwd\n")

    original = command_fingerprint(
        "bash inspect.sh",
        cwd=cwd,
        environment={"MODE": "one"},
        execution_target="host",
    )
    same = command_fingerprint(
        "bash inspect.sh",
        cwd=cwd,
        environment={"MODE": "one"},
        execution_target="host",
    )
    assert same["digest"] == original["digest"]
    assert original["command_summary"] == "bash · 1 argument"
    assert original["scripts"][0]["path"] == str(script)

    changed_env = command_fingerprint(
        "bash inspect.sh",
        cwd=cwd,
        environment={"MODE": "two"},
        execution_target="host",
    )
    assert changed_env["digest"] != original["digest"]
    script.write_text("#!/bin/sh\nls\n")
    changed_script = command_fingerprint(
        "bash inspect.sh",
        cwd=cwd,
        environment={"MODE": "one"},
        execution_target="host",
    )
    assert changed_script["digest"] != original["digest"]


def test_host_command_fingerprint_hashes_extensionless_interpreter_scripts(tmp_path):
    cwd = tmp_path / "workspace"
    cwd.mkdir()
    script = cwd / "inspect"
    script.write_text("printf first")
    original = command_fingerprint(
        "bash inspect", cwd=cwd, environment={}, execution_target="host"
    )

    script.write_text("printf second")
    changed = command_fingerprint(
        "bash inspect", cwd=cwd, environment={}, execution_target="host"
    )

    assert original["scripts"][0]["path"] == str(script)
    assert changed["digest"] != original["digest"]


def test_permanent_host_approval_persists_exact_fingerprint_without_raw_command_or_env(
    tmp_path,
):
    cwd = tmp_path / "workspace"
    cwd.mkdir()
    (cwd / "inspect.sh").write_text("printf super-secret-value")
    fingerprint = command_fingerprint(
        "bash inspect.sh",
        cwd=cwd,
        environment={"TOKEN": "never-store-this"},
        execution_target="host",
    )
    store = HostApprovalStore(tmp_path / "state")

    approval = store.allow_always(fingerprint, actor="operator")

    assert HostApprovalStore(tmp_path / "state").allows(fingerprint) is True
    serialized = (tmp_path / "state" / "host_command_approvals.json").read_text()
    assert "super-secret-value" not in serialized
    assert "never-store-this" not in serialized
    assert approval["fingerprint"] == fingerprint["digest"]
    revoked = store.revoke(approval["id"])
    assert revoked["revoked_at"] is not None
    assert store.allows(fingerprint) is False
    assert json.loads(serialized)["version"] == 1
