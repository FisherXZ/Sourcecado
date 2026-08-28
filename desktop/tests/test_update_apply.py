"""The update state machine: what it refuses, what it changes, and what it puts back.

Two properties carry most of the weight.

Criterion 2 -- state compatibility and the required backup -- is not decided
here. `coworker/migrations.py` is the one registry of store versions, backups,
and rollback, and these tests assert that the updater *calls* it rather than
forming a second opinion.

Criterion 4 -- a failure keeps or restores the last usable version -- is tested
by breaking each stage in turn and reading the bundle and the state back
afterwards. The negative tests assert the update reached the stage that stopped
it before they assert it stopped, because a test that passes because nothing
started is not a test.
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import update_fixtures as fx  # noqa: E402
from coworker import migrations  # noqa: E402
from coworker.agent_run_repository import AgentRunRepository  # noqa: E402
from coworker.update_channel import (  # noqa: E402
    UpdateStage,
    UpdateStatus,
    apply_update,
    drain,
    manifest as m,
    rollback,
)
from state_fixtures import build_legacy_state  # noqa: E402

NOW = datetime(2026, 8, 27, 10, 0, 0, tzinfo=UTC)
SEND_ARGS = {"to": "dana@ramp.test", "subject": "Intro", "body": "Hello Dana."}


def _healthy(_installation) -> bool:
    return True


def _unhealthy(_installation) -> bool:
    return False


def _apply(document, install, artifact, public, **kwargs):
    kwargs.setdefault("health_check", _healthy)
    kwargs.setdefault("trust", fx.trust(public))
    kwargs.setdefault("drain_timeout", 0.0)
    kwargs.setdefault("sleep", lambda _: None)
    # An installation with existing state has a run store, and a real caller
    # opens it. Only a test that is about the missing-store refusal passes None.
    opened = None
    if "runs" not in kwargs and (install.state_root / "agent_runs.db").exists():
        opened = AgentRunRepository(install.state_root)
        kwargs["runs"] = opened
    try:
        return apply_update(
            document, installation=install, artifact_path=artifact, **kwargs
        )
    finally:
        # apply_update closes the run store once the drain gate passes; closing
        # a second time is harmless and covers the paths that stop before it.
        if opened is not None:
            try:
                opened.close()
            except Exception:
                pass


def _prepare(tmp_path, *, installed_version="0.0.1", state=None):
    seed, public = fx.keypair()
    artifact = fx.artifact(tmp_path, version="0.0.2")
    install = fx.installation(tmp_path, version=installed_version)
    if state is not None:
        state(install.state_root)
    fx.app_tree(install.bundle_path.parent, version=installed_version)
    return seed, public, artifact, install


def _runs(install):
    """The run store an installation with existing state already has."""
    return AgentRunRepository(install.state_root)


# --- the update actually works -------------------------------------------


def test_a_verified_update_installs_the_new_bundle(tmp_path):
    seed, public, artifact, install = _prepare(tmp_path)
    assert fx.bundle_version(install.bundle_path) == "0.0.1"

    outcome = _apply(fx.document(artifact, seed), install, artifact, public)

    assert outcome.status is UpdateStatus.APPLIED, outcome.reason
    assert outcome.stage is UpdateStage.DONE
    assert outcome.to_version == "0.0.2"
    assert fx.bundle_version(install.bundle_path) == "0.0.2"


def test_a_clean_install_needs_no_previous_version_and_takes_no_backup(tmp_path):
    seed, public = fx.keypair()
    artifact = fx.artifact(tmp_path, version="0.0.2")
    install = fx.installation(tmp_path, version="0.0.0")
    assert not install.bundle_path.exists()

    outcome = _apply(
        fx.document(artifact, seed, minimum_upgradable_version="0.0.0"),
        install,
        artifact,
        public,
    )

    assert outcome.status is UpdateStatus.APPLIED, outcome.reason
    assert fx.bundle_version(install.bundle_path) == "0.0.2"
    assert outcome.backup_id is None
    assert outcome.migrated == ()


# --- criterion 3: the drain gate -----------------------------------------


def test_an_update_requested_during_an_unsettled_send_does_not_proceed(tmp_path):
    seed, public, artifact, install = _prepare(tmp_path)
    install.state_root.mkdir(parents=True, exist_ok=True)
    repo = AgentRunRepository(install.state_root)
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
    before = repo.list_effects(started.run["run_id"])
    before_checkpoints = len(repo.list_checkpoints(started.run["run_id"]))

    outcome = _apply(
        fx.document(artifact, seed), install, artifact, public, runs=repo, now=NOW
    )

    # It started: verification passed and the gate is what stopped it.
    assert outcome.stage is UpdateStage.DRAIN
    assert outcome.status is UpdateStatus.BLOCKED
    assert outcome.blockers[0].status is drain.DrainStatus.UNSETTLED_EFFECT
    assert outcome.blockers[0].effect_id == commit.effect["effect_id"]
    assert outcome.blockers[0].tool_name == "gmail_send"

    # It did not kill anything: the running bundle is untouched.
    assert fx.bundle_version(install.bundle_path) == "0.0.1"
    assert not install.previous_path.exists()

    # It did not mark the effect anything. Not succeeded, not failed, not
    # quarantined, not abandoned. The row is exactly as the dispatcher left it.
    assert repo.list_effects(started.run["run_id"]) == before
    assert len(repo.list_checkpoints(started.run["run_id"])) == before_checkpoints
    assert repo.list_quarantined_effects() == []
    assert outcome.backup_id is None
    repo.close()


def test_an_update_never_settles_a_quarantined_effect_to_get_past_it(tmp_path):
    seed, public, artifact, install = _prepare(tmp_path)
    install.state_root.mkdir(parents=True, exist_ok=True)
    repo = AgentRunRepository(install.state_root)
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
    repo.quarantine_effect(
        commit.lease, commit.effect["effect_id"], reason="owner died", now=NOW
    )
    before = repo.list_effects(started.run["run_id"])

    outcome = _apply(
        fx.document(artifact, seed), install, artifact, public, runs=repo, now=NOW
    )

    assert outcome.stage is UpdateStage.DRAIN
    assert outcome.status is UpdateStatus.BLOCKED
    assert outcome.blockers[0].status is drain.DrainStatus.QUARANTINED_EFFECT
    assert repo.list_effects(started.run["run_id"]) == before
    assert repo.list_quarantined_effects()[0]["resolved_by"] is None
    assert fx.bundle_version(install.bundle_path) == "0.0.1"
    repo.close()


def test_an_update_with_a_run_store_it_was_not_given_refuses(tmp_path):
    """Fail closed. An uninspected run store is not an empty one."""
    seed, public, artifact, install = _prepare(tmp_path)
    install.state_root.mkdir(parents=True, exist_ok=True)
    AgentRunRepository(install.state_root).close()

    outcome = _apply(fx.document(artifact, seed), install, artifact, public, runs=None)

    assert outcome.stage is UpdateStage.DRAIN
    assert outcome.status is UpdateStatus.REFUSED
    assert outcome.reason == "run_store_not_inspected"
    assert fx.bundle_version(install.bundle_path) == "0.0.1"


def test_an_update_records_the_work_that_will_continue_after_the_restart(tmp_path):
    """The other half of criterion 3: drain, *or* record restart-safe continuation."""
    seed, public, artifact, install = _prepare(tmp_path)
    install.state_root.mkdir(parents=True, exist_ok=True)
    repo = AgentRunRepository(install.state_root)
    owner = repo.registry.register()
    started = repo.create_run(
        session_id="sess-1",
        trigger="chat",
        goal="reach out to Dana",
        owner=owner,
        lease_seconds=600,
        now=NOW,
    )
    # Parked on a person: durable in the run store, so a restart picks it up.
    repo.checkpoint(
        started.lease,
        kind="waiting_approval",
        state="waiting_approval",
        payload={"approval_id": "appr-1"},
        now=NOW,
    )

    outcome = _apply(
        fx.document(artifact, seed), install, artifact, public, runs=repo, now=NOW
    )

    assert outcome.status is UpdateStatus.APPLIED, outcome.reason
    assert outcome.continuable == (started.run["run_id"],)
    assert outcome.to_dict()["continuable"] == [started.run["run_id"]]
    # The run itself is untouched: continuation is recorded, not performed.
    reopened = AgentRunRepository(install.state_root)
    try:
        run = reopened.get_run(started.run["run_id"])
        assert run["current_state"] == "waiting_approval"
        assert reopened.list_effects(started.run["run_id"]) == []
    finally:
        reopened.close()


def test_an_installation_with_no_run_store_at_all_proceeds(tmp_path):
    seed, public, artifact, install = _prepare(tmp_path)
    outcome = _apply(fx.document(artifact, seed), install, artifact, public, runs=None)
    assert outcome.status is UpdateStatus.APPLIED, outcome.reason


# --- criterion 2: compatibility and backup, delegated --------------------


def test_a_pending_migration_is_backed_up_and_applied_by_the_registry(tmp_path):
    seed, public, artifact, install = _prepare(tmp_path, state=build_legacy_state)
    pending_before = {
        item.store_id for item in migrations.plan_migrations(install.state_root).pending
    }
    assert "people_db" in pending_before

    outcome = _apply(fx.document(artifact, seed), install, artifact, public)

    assert outcome.status is UpdateStatus.APPLIED, outcome.reason
    assert outcome.backup_id is not None
    assert pending_before <= set(outcome.migrated)
    assert migrations.plan_migrations(install.state_root).pending == ()
    # The backup the registry wrote is on disk and describes itself.
    backups = migrations.list_backups(install.state_root)
    assert any(item["backup_id"] == outcome.backup_id for item in backups)


def test_the_backup_is_created_before_anything_changes(tmp_path):
    seed, public, artifact, install = _prepare(tmp_path, state=build_legacy_state)
    order: list[str] = []
    real_backup = migrations.create_backup
    real_apply = migrations.apply_migrations

    def watched_backup(*args, **kwargs):
        order.append("backup")
        return real_backup(*args, **kwargs)

    def watched_apply(*args, **kwargs):
        order.append("migrate")
        return real_apply(*args, **kwargs)

    migrations.create_backup = watched_backup
    migrations.apply_migrations = watched_apply
    try:
        outcome = _apply(
            fx.document(artifact, seed),
            install,
            artifact,
            public,
            health_check=lambda _i: (order.append("health"), True)[1],
        )
    finally:
        migrations.create_backup = real_backup
        migrations.apply_migrations = real_apply

    assert outcome.status is UpdateStatus.APPLIED, outcome.reason
    assert order[0] == "backup"
    assert order.index("backup") < order.index("migrate") < order.index("health")


def test_a_store_from_a_future_version_refuses_the_whole_update(tmp_path):
    """The registry's own fail-closed rule, not a second opinion about it."""
    seed, public, artifact, install = _prepare(tmp_path, state=build_legacy_state)
    conn = sqlite3.connect(install.state_root / "people.db")
    conn.execute("PRAGMA user_version = 99")
    conn.commit()
    conn.close()
    assert migrations.plan_migrations(install.state_root).blocked

    outcome = _apply(fx.document(artifact, seed), install, artifact, public)

    assert outcome.stage is UpdateStage.COMPATIBILITY
    assert outcome.status is UpdateStatus.REFUSED
    assert "people_db" in outcome.reason or "people_db" in outcome.guidance
    assert fx.bundle_version(install.bundle_path) == "0.0.1"


# --- criterion 4: keep or restore the last usable version ----------------


def test_a_failed_health_check_restores_the_previous_bundle(tmp_path):
    seed, public, artifact, install = _prepare(tmp_path)

    outcome = _apply(
        fx.document(artifact, seed),
        install,
        artifact,
        public,
        health_check=_unhealthy,
    )

    assert outcome.stage is UpdateStage.HEALTH
    assert outcome.status is UpdateStatus.ROLLED_BACK
    assert fx.bundle_version(install.bundle_path) == "0.0.1"
    assert not install.previous_path.exists()
    assert "0.0.1" in outcome.guidance


def test_a_health_check_that_raises_is_a_failed_health_check(tmp_path):
    seed, public, artifact, install = _prepare(tmp_path)

    def explode(_installation):
        raise RuntimeError("the sidecar never opened its port")

    outcome = _apply(
        fx.document(artifact, seed), install, artifact, public, health_check=explode
    )

    assert outcome.status is UpdateStatus.ROLLED_BACK
    assert fx.bundle_version(install.bundle_path) == "0.0.1"


def test_a_failed_health_check_restores_the_state_it_migrated(tmp_path):
    seed, public, artifact, install = _prepare(tmp_path, state=build_legacy_state)
    before = _people_rows(install.state_root)
    versions_before = _versions(install.state_root)

    outcome = _apply(
        fx.document(artifact, seed),
        install,
        artifact,
        public,
        health_check=_unhealthy,
    )

    assert outcome.status is UpdateStatus.ROLLED_BACK
    assert outcome.backup_id is not None
    assert _versions(install.state_root) == versions_before
    assert _people_rows(install.state_root) == before
    assert fx.bundle_version(install.bundle_path) == "0.0.1"


def test_a_failed_migration_rolls_the_state_back_and_leaves_the_bundle_alone(
    tmp_path, monkeypatch
):
    seed, public, artifact, install = _prepare(tmp_path, state=build_legacy_state)
    versions_before = _versions(install.state_root)
    people_before = _people_rows(install.state_root)

    def broken(root, *, plan=None, backup=None):
        return migrations.MigrationOutcome(
            backup_id=backup.backup_id if backup else None,
            error="people_db: the step raised",
        )

    monkeypatch.setattr(migrations, "apply_migrations", broken)
    outcome = _apply(fx.document(artifact, seed), install, artifact, public)

    assert outcome.stage is UpdateStage.MIGRATE
    assert outcome.status is UpdateStatus.ROLLED_BACK
    assert _versions(install.state_root) == versions_before
    assert _people_rows(install.state_root) == people_before
    assert fx.bundle_version(install.bundle_path) == "0.0.1"


def test_a_corrupt_artifact_is_refused_before_anything_is_touched(tmp_path):
    seed, public, artifact, install = _prepare(tmp_path, state=build_legacy_state)
    document = fx.document(artifact, seed)
    artifact.write_bytes(b"not a zip")
    document["signed"]["artifact_size"] = artifact.stat().st_size
    document["signed"]["artifact_sha256"] = m.sha256_file(artifact)
    document = m.sign_manifest(document["signed"], seed=seed, key_id=fx.KEY_ID)

    outcome = _apply(document, install, artifact, public)

    assert outcome.stage is UpdateStage.STAGE
    assert outcome.status is UpdateStatus.REFUSED
    assert migrations.list_backups(install.state_root) == []
    assert fx.bundle_version(install.bundle_path) == "0.0.1"


def test_an_unverified_manifest_never_reaches_the_run_store(tmp_path):
    seed, _public, artifact, install = _prepare(tmp_path)
    _, other_public = fx.keypair()

    outcome = _apply(fx.document(artifact, seed), install, artifact, other_public)

    assert outcome.stage is UpdateStage.VERIFY
    assert outcome.status is UpdateStatus.REFUSED
    assert outcome.reason == str(m.Refusal.BAD_SIGNATURE)
    assert fx.bundle_version(install.bundle_path) == "0.0.1"


def test_a_backup_that_cannot_be_written_stops_the_update(tmp_path, monkeypatch):
    seed, public, artifact, install = _prepare(tmp_path, state=build_legacy_state)

    def refuse(*args, **kwargs):
        raise migrations.BackupFailed("the disk is full")

    monkeypatch.setattr(migrations, "create_backup", refuse)
    outcome = _apply(fx.document(artifact, seed), install, artifact, public)

    assert outcome.stage is UpdateStage.BACKUP
    assert outcome.status is UpdateStatus.REFUSED
    assert fx.bundle_version(install.bundle_path) == "0.0.1"
    assert migrations.plan_migrations(install.state_root).pending != ()


# --- explicit rollback ---------------------------------------------------


def test_rollback_puts_the_previous_bundle_and_state_back(tmp_path):
    seed, public, artifact, install = _prepare(tmp_path, state=build_legacy_state)
    people_before = _people_rows(install.state_root)
    versions_before = _versions(install.state_root)

    applied = _apply(fx.document(artifact, seed), install, artifact, public)
    assert applied.status is UpdateStatus.APPLIED, applied.reason
    assert fx.bundle_version(install.bundle_path) == "0.0.2"
    assert _versions(install.state_root) != versions_before

    reverted = rollback(install, backup_id=applied.backup_id)

    assert reverted.status is UpdateStatus.ROLLED_BACK, reverted.reason
    assert fx.bundle_version(install.bundle_path) == "0.0.1"
    assert _versions(install.state_root) == versions_before
    assert _people_rows(install.state_root) == people_before


def test_rollback_with_nothing_to_roll_back_to_says_so(tmp_path):
    install = fx.installation(tmp_path)
    fx.app_tree(install.bundle_path.parent, version="0.0.1")
    outcome = rollback(install)
    assert outcome.status is UpdateStatus.REFUSED
    assert outcome.reason == "no_previous_version"
    assert fx.bundle_version(install.bundle_path) == "0.0.1"


# --- helpers -------------------------------------------------------------


def _versions(root: Path) -> dict[str, int | None]:
    return {
        spec.store_id: migrations.read_version(root, spec)
        for spec in migrations.REGISTRY
        if migrations.store_present(root, spec)
    }


def _people_rows(root: Path) -> list[tuple]:
    """The person file as content, over the columns the old schema had."""
    path = root / "people.db"
    if not path.is_file():
        return []
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT * FROM people ORDER BY person_id").fetchall()
        columns = (
            "person_id",
            "apollo_id",
            "first_name",
            "last_name",
            "title",
            "company",
            "email",
            "linkedin_url",
            "sequence_state",
            "target",
        )
        return [tuple(row[name] for name in columns) for row in rows]
    finally:
        conn.close()
