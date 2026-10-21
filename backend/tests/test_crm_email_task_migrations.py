"""v6→v7 task evidence upgrade is registered, backed up, and atomic."""
import asyncio
import dataclasses
import sqlite3

import pytest

from coworker import migrations
from coworker.people import PersonStore
from test_crm_analysis import tracked_person, worker
from test_crm_email_tasks import TaskMail, TaskModel, saved_tasks


def v6_database(root):
    people = PersonStore(root)
    pid = tracked_person(people)
    asyncio.run(worker(people).analyze(pid))
    people._conn.close()
    conn = sqlite3.connect(root / "people.db")
    conn.execute("DROP TABLE IF EXISTS crm_task_evidence")
    columns = {row[1] for row in conn.execute("PRAGMA table_info(crm_analysis_state)")}
    if "analysis_version" in columns:
        conn.execute("ALTER TABLE crm_analysis_state DROP COLUMN analysis_version")
    conn.execute("PRAGMA user_version=6")
    conn.commit()
    conn.close()
    return pid


def test_registered_v7_upgrade_preserves_v6_history_and_schedules_task_stage(tmp_path):
    pid = v6_database(tmp_path)
    before = sqlite3.connect(tmp_path / "people.db")
    history = before.execute("SELECT * FROM crm_changes").fetchall()
    before.close()
    result = migrations.apply_migrations(tmp_path)
    assert result.error is None and result.backup_id
    people = PersonStore(tmp_path)
    assert people._conn.execute("PRAGMA user_version").fetchone()[0] == 7
    assert [tuple(row) for row in people._conn.execute("SELECT * FROM crm_changes")] == history
    assert people._conn.execute("SELECT count(*) FROM crm_task_evidence").fetchone()[0] == 0
    service = worker(people, TaskModel(), TaskMail())
    assert pid in service.pending()
    assert asyncio.run(service.analyze(pid))["status"] == "analyzed"
    assert len(saved_tasks(people, pid)) == 1
    assert people._conn.execute("PRAGMA foreign_key_check").fetchall() == []
    with pytest.raises(sqlite3.IntegrityError):
        people._conn.execute("""INSERT INTO crm_task_evidence(proposal_id, source_id, message_id, body_fingerprint, request_start, request_end)
            VALUES ('missing', 'missing', 'm', 'hash', 0, 5)""")
    people._conn.rollback()


def test_unmigrated_v6_is_gated_before_model_spend_or_pending_sql(tmp_path):
    from coworker.people import CrmMigrationRequired
    pid = v6_database(tmp_path)
    people = PersonStore(tmp_path)
    model = TaskModel()
    service = worker(people, model, TaskMail())
    with pytest.raises(CrmMigrationRequired):
        asyncio.run(service.analyze(pid))
    with pytest.raises(CrmMigrationRequired):
        service.pending()
    assert model.calls == []


def test_missing_v7_shape_is_gated_even_when_store_version_claims_current(tmp_path):
    from coworker.people import CrmMigrationRequired
    pid = v6_database(tmp_path)
    conn = sqlite3.connect(tmp_path / "people.db")
    conn.execute("PRAGMA user_version=7")
    conn.close()
    model = TaskModel()
    with pytest.raises(CrmMigrationRequired):
        asyncio.run(worker(PersonStore(tmp_path), model, TaskMail()).analyze(pid))
    assert model.calls == []


def test_v7_failure_after_schema_write_restores_populated_v6_exactly(tmp_path, monkeypatch):
    v6_database(tmp_path)
    conn = sqlite3.connect(tmp_path / "people.db")
    before = list(conn.iterdump())
    conn.close()
    original = migrations.spec_for("people_db")
    def fail(context):
        migrations._add_crm_email_tasks(context)
        raise RuntimeError("after real v7 DDL")
    broken = dataclasses.replace(original, migrations=tuple(dataclasses.replace(step, apply=fail) if step.from_version == 6 else step for step in original.migrations))
    monkeypatch.setattr(migrations, "REGISTRY", tuple(broken if s.store_id == "people_db" else s for s in migrations.REGISTRY))
    result = migrations.apply_migrations(tmp_path)
    assert result.error is not None and "people_db" in result.rolled_back
    conn = sqlite3.connect(tmp_path / "people.db")
    assert list(conn.iterdump()) == before
    conn.close()
    monkeypatch.undo()
    assert migrations.apply_migrations(tmp_path).error is None


def test_person_snapshot_revert_preserves_independent_inferred_work_and_source_fk(tmp_path):
    from coworker.crm_tasks import TaskService
    people = PersonStore(tmp_path)
    pid = tracked_person(people)
    before_analysis = people.get(pid)["version"]
    service = worker(people, TaskModel(), TaskMail())
    assert asyncio.run(service.analyze(pid))["status"] == "analyzed"
    task = saved_tasks(people, pid)[0]
    evidence = [tuple(r) for r in people._conn.execute("SELECT * FROM crm_task_evidence")]
    changes = TaskService(people).changes(task["task_id"])
    people.revert(pid, to_version=before_analysis, expected_version=people.get(pid)["version"], actor="director", rationale_summary="Restore an earlier person file.")
    assert saved_tasks(people, pid) == [task]
    assert [tuple(r) for r in people._conn.execute("SELECT * FROM crm_task_evidence")] == evidence
    assert TaskService(people).changes(task["task_id"]) == changes
    assert people._conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert task["source_refs"][0] in {r[0] for r in people._conn.execute("SELECT id FROM person_attachments")}
