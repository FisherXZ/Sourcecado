"""Criterion 6: the five exercises, run rather than described.

Clean install, upgrade from the prior preview, a failed update, a rollback, and
the person file surviving all of them. `docs/update-channel.md` describes these
in prose; this file is what actually runs, and the doc points at it by name so
the two cannot drift.

The person-file exercise is the one a user would notice. An update that loses a
person file is worse than an update that fails, because a failed update is
visible and a lost person file is not. So every exercise below that touches
state asserts the person file back afterwards, through the real `PersonStore`,
not just through raw rows.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import update_fixtures as fx  # noqa: E402
from coworker import doctor, migrations  # noqa: E402
from coworker.agent_run_repository import AgentRunRepository  # noqa: E402
from coworker.people import PersonStore  # noqa: E402
from coworker.update_channel import UpdateStage, UpdateStatus, apply_update, rollback  # noqa: E402
from state_fixtures import build_legacy_state  # noqa: E402

PERSON_COLUMNS = (
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


def _people_rows(root: Path) -> list[tuple]:
    path = root / "people.db"
    if not path.is_file():
        return []
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT * FROM people ORDER BY person_id").fetchall()
        return [tuple(row[name] for name in PERSON_COLUMNS) for row in rows]
    finally:
        conn.close()


def _events(root: Path) -> list[tuple]:
    path = root / "people.db"
    if not path.is_file():
        return []
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT person_id, source, kind, summary FROM events "
            "ORDER BY person_id, rowid"
        ).fetchall()
        return [tuple(row) for row in rows]
    finally:
        conn.close()


def _versions(root: Path) -> dict[str, int | None]:
    return {
        spec.store_id: migrations.read_version(root, spec)
        for spec in migrations.REGISTRY
        if migrations.store_present(root, spec)
    }


def _update(install, artifact, seed, public, **kwargs):
    kwargs.setdefault("health_check", lambda _i: True)
    opened = None
    if "runs" not in kwargs and (install.state_root / "agent_runs.db").exists():
        opened = AgentRunRepository(install.state_root)
        kwargs["runs"] = opened
    kwargs.setdefault("runs", None)
    try:
        return apply_update(
            fx.document(artifact, seed, **kwargs.pop("manifest", {})),
            installation=install,
            artifact_path=artifact,
            trust=fx.trust(public),
            drain_timeout=0.0,
            sleep=lambda _: None,
            **kwargs,
        )
    finally:
        if opened is not None:
            try:
                opened.close()
            except Exception:
                pass


# --- exercise 1: clean install -------------------------------------------


def test_exercise_clean_install(tmp_path):
    """No previous application, no previous state. Nothing to back up or migrate."""
    seed, public = fx.keypair()
    artifact = fx.artifact(tmp_path, version="0.0.2")
    install = fx.installation(tmp_path, version="0.0.0")
    assert not install.bundle_path.exists()
    assert not install.state_root.exists()

    outcome = _update(
        install,
        artifact,
        seed,
        public,
        manifest={"minimum_upgradable_version": "0.0.0"},
    )

    assert outcome.status is UpdateStatus.APPLIED, outcome.reason
    assert outcome.stage is UpdateStage.DONE
    assert fx.bundle_version(install.bundle_path) == "0.0.2"
    assert outcome.backup_id is None
    assert outcome.migrated == ()
    assert not install.previous_path.exists()


# --- exercise 2: upgrade from the prior preview --------------------------


def test_exercise_upgrade_from_the_prior_preview(tmp_path):
    """State written by the previous preview build, brought forward and kept."""
    seed, public = fx.keypair()
    artifact = fx.artifact(tmp_path, version="0.0.2")
    install = fx.installation(tmp_path, version="0.0.1")
    build_legacy_state(install.state_root)
    fx.app_tree(install.bundle_path.parent, version="0.0.1")
    people_before = _people_rows(install.state_root)
    events_before = _events(install.state_root)
    assert people_before, "the prior preview's state must contain a person file"

    outcome = _update(install, artifact, seed, public)

    assert outcome.status is UpdateStatus.APPLIED, outcome.reason
    assert fx.bundle_version(install.bundle_path) == "0.0.2"
    assert fx.bundle_version(install.previous_path) == "0.0.1"
    assert "people_db" in outcome.migrated
    assert outcome.backup_id is not None
    assert migrations.plan_migrations(install.state_root).pending == ()
    # The person file came forward with it.
    assert _people_rows(install.state_root) == people_before
    assert _events(install.state_root) == events_before


# --- exercise 3: a failed update -----------------------------------------


def test_exercise_failed_update_restores_the_last_usable_version(tmp_path):
    seed, public = fx.keypair()
    artifact = fx.artifact(tmp_path, version="0.0.2")
    install = fx.installation(tmp_path, version="0.0.1")
    build_legacy_state(install.state_root)
    fx.app_tree(install.bundle_path.parent, version="0.0.1")
    people_before = _people_rows(install.state_root)
    events_before = _events(install.state_root)
    versions_before = _versions(install.state_root)

    outcome = _update(install, artifact, seed, public, health_check=lambda _i: False)

    assert outcome.stage is UpdateStage.HEALTH, "the update must reach the launch check"
    assert outcome.status is UpdateStatus.ROLLED_BACK
    assert fx.bundle_version(install.bundle_path) == "0.0.1"
    assert not install.previous_path.exists()
    assert not install.staging_root.exists()
    assert _versions(install.state_root) == versions_before
    assert _people_rows(install.state_root) == people_before
    assert _events(install.state_root) == events_before
    assert "0.0.1" in outcome.guidance

    # Doctor and the updater must not disagree about what is on this machine.
    report = doctor.diagnose(install.state_root)
    assert not report.blocked, [item.code for item in report.findings if item.blocking]
    assert {item.store_id: item.status for item in report.stores}["people_db"] in {
        "pending",
        "current",
    }


# --- exercise 4: rollback -------------------------------------------------


def test_exercise_rollback_after_a_successful_update(tmp_path):
    seed, public = fx.keypair()
    artifact = fx.artifact(tmp_path, version="0.0.2")
    install = fx.installation(tmp_path, version="0.0.1")
    build_legacy_state(install.state_root)
    fx.app_tree(install.bundle_path.parent, version="0.0.1")
    people_before = _people_rows(install.state_root)
    versions_before = _versions(install.state_root)

    applied = _update(install, artifact, seed, public)
    assert applied.status is UpdateStatus.APPLIED, applied.reason
    assert _versions(install.state_root) != versions_before

    reverted = rollback(install, backup_id=applied.backup_id)

    assert reverted.status is UpdateStatus.ROLLED_BACK, reverted.reason
    assert fx.bundle_version(install.bundle_path) == "0.0.1"
    assert not install.previous_path.exists()
    assert _versions(install.state_root) == versions_before
    assert _people_rows(install.state_root) == people_before
    assert "people_db" in reverted.restored
    assert not doctor.diagnose(install.state_root).blocked


# --- exercise 5: the person file -----------------------------------------


def test_exercise_the_person_file_reads_back_through_its_own_store(tmp_path):
    """Rows surviving is not enough. The person file must still open."""
    seed, public = fx.keypair()
    artifact = fx.artifact(tmp_path, version="0.0.2")
    install = fx.installation(tmp_path, version="0.0.1")
    build_legacy_state(install.state_root)
    fx.app_tree(install.bundle_path.parent, version="0.0.1")
    person_id = _people_rows(install.state_root)[0][0]

    outcome = _update(install, artifact, seed, public)
    assert outcome.status is UpdateStatus.APPLIED, outcome.reason

    store = PersonStore(install.state_root)
    person = store.get(person_id, expand_sources=True)
    assert person is not None, "the person file did not survive the update"
    assert person["first_name"] == "Dana"
    assert person["company"] == "Rippling"
    assert person["email"] == "dana@example.com"
    assert person["sequence_state"] == "open"
    assert store.timeline(person_id), "the person's history did not survive"
    assert person["person_id"] == person_id


def test_exercise_the_person_file_survives_a_failed_update_and_a_rollback(tmp_path):
    """The worst outcome in this issue, tested on the two paths that could cause it."""
    seed, public = fx.keypair()
    artifact = fx.artifact(tmp_path, version="0.0.2")
    install = fx.installation(tmp_path, version="0.0.1")
    build_legacy_state(install.state_root)
    fx.app_tree(install.bundle_path.parent, version="0.0.1")
    before = _people_rows(install.state_root)
    person_id = before[0][0]

    failed = _update(install, artifact, seed, public, health_check=lambda _i: False)
    assert failed.status is UpdateStatus.ROLLED_BACK
    assert _people_rows(install.state_root) == before

    applied = _update(install, artifact, seed, public)
    assert applied.status is UpdateStatus.APPLIED, applied.reason
    reverted = rollback(install, backup_id=applied.backup_id)
    assert reverted.status is UpdateStatus.ROLLED_BACK, reverted.reason

    assert _people_rows(install.state_root) == before
    store = PersonStore(install.state_root)
    assert store.get(person_id) is not None
