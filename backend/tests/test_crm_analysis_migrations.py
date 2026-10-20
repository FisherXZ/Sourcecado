"""Real v5 upgrades, rollback, FK constraints, and snapshot authority."""
import asyncio
import dataclasses
import sqlite3

import pytest

from coworker import migrations
from coworker.crm_tasks import CRM_CHANGES_V5_COLUMNS, TaskService
from coworker.people import PersonStore
from test_crm_analysis import DescriptionModel, tracked_person, worker


def v5_database(root):
    people = PersonStore(root)
    pid = tracked_person(people)
    task = TaskService(people).create(pid, title="Preserve this task", timezone="America/Los_Angeles", actor="director", operation_id="v5-task-operation")
    people._conn.close()
    conn = sqlite3.connect(root / "people.db")
    changes = conn.execute("SELECT * FROM crm_changes").fetchall()
    ops = conn.execute("SELECT * FROM crm_operations").fetchall()
    ops_ddl = conn.execute("SELECT sql FROM sqlite_master WHERE name='crm_operations'").fetchone()[0]
    for table in ("crm_proposals", "crm_analysis_attempts", "crm_analysis_state", "crm_operations", "crm_changes"):
        conn.execute(f"DROP TABLE {table}")
    conn.execute(f"CREATE TABLE crm_changes ({CRM_CHANGES_V5_COLUMNS})")
    conn.execute(ops_ddl)
    conn.executemany("INSERT INTO crm_changes VALUES (" + ','.join('?' * len(changes[0])) + ")", changes)
    conn.executemany("INSERT INTO crm_operations VALUES (" + ','.join('?' * len(ops[0])) + ")", ops)
    conn.execute("PRAGMA user_version=5")
    conn.commit()
    conn.close()
    return pid, task


def test_real_v5_requires_registered_migration_before_model_spend_and_preserves_rows(tmp_path):
    pid, task = v5_database(tmp_path)
    model = DescriptionModel()
    people = PersonStore(tmp_path)
    with pytest.raises(ValueError, match="make doctor-repair"):
        asyncio.run(worker(people, model).analyze(pid))
    assert model.calls == []
    people._conn.close()
    outcome = migrations.apply_migrations(tmp_path)
    assert outcome.error is None
    assert outcome.backup_id
    reopened = PersonStore(tmp_path)
    assert reopened._conn.execute("PRAGMA user_version").fetchone()[0] == 6
    assert reopened._conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert TaskService(reopened).get(pid, task["task"]["task_id"])["title"] == "Preserve this task"
    assert asyncio.run(worker(reopened, model).analyze(pid))["status"] == "analyzed"
    with pytest.raises(sqlite3.IntegrityError):
        reopened._conn.execute("INSERT INTO crm_analysis_state(person_id, updated_at) VALUES ('missing', 'now')")
    reopened._conn.rollback()


def test_failed_v6_migration_rolls_back_and_can_be_retried(tmp_path, monkeypatch):
    v5_database(tmp_path)
    conn = sqlite3.connect(tmp_path / "people.db")
    before = list(conn.iterdump())
    conn.close()
    original = migrations.spec_for("people_db")
    def fail(context):
        migrations._add_crm_analysis(context)
        raise RuntimeError("interrupted after real DDL")
    broken = dataclasses.replace(original, migrations=tuple(
        dataclasses.replace(step, apply=fail) if step.from_version == 5 else step for step in original.migrations))
    monkeypatch.setattr(migrations, "REGISTRY", tuple(broken if s.store_id == "people_db" else s for s in migrations.REGISTRY))
    result = migrations.apply_migrations(tmp_path)
    assert result.error is not None and "people_db" in result.rolled_back
    conn = sqlite3.connect(tmp_path / "people.db")
    assert list(conn.iterdump()) == before
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 5
    conn.close()
    monkeypatch.undo()
    assert migrations.apply_migrations(tmp_path).error is None


def test_snapshot_restore_retains_description_authority_and_sources(tmp_path):
    people = PersonStore(tmp_path)
    pid = tracked_person(people)
    asyncio.run(worker(people).analyze(pid))
    saved = people.descriptions(pid)
    version = people.get(pid)["version"]
    people.patch_description(pid, "general", expected_version=1, text="", actor="director", rationale_summary="Intentional blank")
    people.revert(pid, to_version=version, expected_version=people.get(pid)["version"], actor="director", rationale_summary="Restore sourced descriptions")
    restored = people.descriptions(pid)
    assert restored == saved
    # Generated prose and its authority/source metadata travel together.
    assert restored["general"]["source_refs"] and restored["general"]["authority"] == "assistant"
