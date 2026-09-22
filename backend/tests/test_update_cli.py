"""The two commands an operator runs by hand, and what they refuse to do.

`status` is the question before quitting the app: is anything in flight? It has
to answer honestly and it has to answer without writing, because the state it is
reporting on is the state a careless read-and-fix would destroy.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import update_fixtures as fx  # noqa: E402
from coworker.agent_run_repository import AgentRunRepository  # noqa: E402
from coworker.update_channel import __main__ as cli  # noqa: E402
from state_fixtures import build_legacy_state  # noqa: E402

NOW = datetime(2026, 8, 27, 10, 0, 0, tzinfo=UTC)
SEND_ARGS = {"to": "dana@ramp.test", "subject": "Intro", "body": "Hello Dana."}


def _dispatched_send(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    repo = AgentRunRepository(root)
    owner = repo.registry.register()
    started = repo.create_run(
        session_id="sess-1",
        trigger="chat",
        goal="reach out to Dana",
        owner=owner,
        lease_seconds=600,
        now=NOW,
    )
    commit = repo.dispatch_effect(
        started.lease, tool_name="gmail_send", arguments=SEND_ARGS, now=NOW
    )
    return repo, commit


def test_status_says_it_is_safe_when_nothing_is_running(tmp_path, capsys):
    code = cli.main(["--state-root", str(tmp_path / "state"), "status"])
    assert code == 0
    assert "safe to update" in capsys.readouterr().out


def test_status_reports_an_unsettled_send_and_refuses(tmp_path, capsys):
    root = tmp_path / "state"
    repo, commit = _dispatched_send(root)
    before = repo.list_effects(commit.effect["run_id"])
    repo.close()

    code = cli.main(["--state-root", str(root), "status"])

    assert code == 1
    output = capsys.readouterr().out
    assert "gmail_send" in output
    assert "wait for the work above to finish" in output
    # Reporting must not have changed the thing being reported on.
    repo = AgentRunRepository(root)
    try:
        assert repo.list_effects(commit.effect["run_id"]) == before
        assert repo.list_quarantined_effects() == []
    finally:
        repo.close()


def test_status_sends_a_quarantine_to_a_person_and_never_settles_it(tmp_path, capsys):
    root = tmp_path / "state"
    repo, commit = _dispatched_send(root)
    repo.quarantine_effect(
        commit.lease, commit.effect["effect_id"], reason="owner died", now=NOW
    )
    repo.close()

    code = cli.main(["--state-root", str(root), "status"])

    assert code == 1
    assert "settle the quarantined effect" in capsys.readouterr().out
    repo = AgentRunRepository(root)
    try:
        assert repo.list_quarantined_effects()[0]["resolved_by"] is None
    finally:
        repo.close()


def test_rollback_lists_the_backups_it_could_restore(tmp_path, capsys):
    from coworker import migrations

    root = tmp_path / "state"
    build_legacy_state(root)
    backup = migrations.create_backup(
        root, [spec.store_id for spec in migrations.REGISTRY], reason="update 1 to 2"
    )

    code = cli.main(
        [
            "--state-root",
            str(root),
            "rollback",
            "--bundle",
            str(tmp_path / "Sourcecado.app"),
            "--list-backups",
        ]
    )
    assert code == 0
    assert backup.backup_id in capsys.readouterr().out


def test_rollback_restores_the_previous_application(tmp_path, capsys):
    install = fx.installation(tmp_path, version="0.0.2")
    fx.app_tree(install.bundle_path.parent, version="0.0.2")
    previous = fx.app_tree(tmp_path / "old", version="0.0.1")
    previous.rename(install.previous_path)

    code = cli.main(
        [
            "--state-root",
            str(install.state_root),
            "rollback",
            "--bundle",
            str(install.bundle_path),
        ]
    )

    assert code == 0
    assert "rolled_back" in capsys.readouterr().out
    assert fx.bundle_version(install.bundle_path) == "0.0.1"


def test_rollback_with_nothing_kept_refuses_rather_than_removing_anything(
    tmp_path, capsys
):
    install = fx.installation(tmp_path, version="0.0.2")
    fx.app_tree(install.bundle_path.parent, version="0.0.2")

    code = cli.main(
        [
            "--state-root",
            str(install.state_root),
            "rollback",
            "--bundle",
            str(install.bundle_path),
        ]
    )

    assert code == 1
    assert "nothing to roll back to" in capsys.readouterr().out
    assert fx.bundle_version(install.bundle_path) == "0.0.2"
