import dataclasses
import sqlite3

import pytest

from coworker import migrations
from coworker.people import PersonStore, SCHEMA_VERSION, SCHEMA_VERSION


def fail_after_unified_upgrade(monkeypatch):
    original = migrations.spec_for("people_db")
    upgrade = next(step for step in original.migrations if step.from_version == 7)

    def fail(context):
        upgrade.apply(context)
        raise RuntimeError("interrupted after real unified v8 migration")

    broken = dataclasses.replace(original, migrations=tuple(
        dataclasses.replace(step, apply=fail) if step is upgrade else step
        for step in original.migrations
    ))
    monkeypatch.setattr(migrations, "REGISTRY", tuple(
        broken if spec.store_id == "people_db" else spec for spec in migrations.REGISTRY
    ))


def test_mail_migration_is_registered_and_preserves_populated_v5(tmp_path):
    people = PersonStore(tmp_path)
    pid = people.keep_from_apollo(apollo_id="saved", first_name="Saved", last_name_obfuscated=None,
        title="Founder", company="Example")["person_id"]
    from coworker.crm_tasks import TaskService
    tasks = TaskService(people)
    tasks.create(pid, title="Keep this promise", timezone="America/Los_Angeles",
        operation_id="migration-task", actor="director")
    people.patch_description(pid, "general", text="Protected director context", expected_version=0,
        actor="director", rationale_summary="Director wrote context before upgrade.")
    preserved_tables = ("person_descriptions", "person_tasks", "crm_changes", "crm_operations")
    rows_before = {table: people._conn.execute(f"SELECT * FROM {table}").fetchall() for table in preserved_tables}
    before = people.get(pid)
    for table in ("crm_analysis_state", "gmail_messages", "gmail_collection_state"):
        people._conn.execute(f"DROP TABLE IF EXISTS {table}")
    people._conn.execute("PRAGMA user_version = 5")
    people._conn.commit()
    people._conn.close()
    outcome = migrations.apply_migrations(tmp_path)
    assert outcome.error is None and outcome.backup_id
    upgraded = PersonStore(tmp_path)
    assert SCHEMA_VERSION == SCHEMA_VERSION
    assert upgraded.get(pid) == before
    for table, rows in rows_before.items():
        assert upgraded._conn.execute(f"SELECT * FROM {table}").fetchall() == rows
    assert upgraded._conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert upgraded._conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert migrations.list_backups(tmp_path)


def test_failed_mail_migration_rolls_back(tmp_path, monkeypatch):
    people = PersonStore(tmp_path)
    people._conn.execute("PRAGMA user_version = 5")
    people._conn.commit()
    people._conn.close()
    with sqlite3.connect(tmp_path / "people.db") as conn:
        before = list(conn.iterdump())
    fail_after_unified_upgrade(monkeypatch)
    plan = migrations.MigrationPlan(stores=tuple(
        store for store in migrations.plan_migrations(tmp_path).stores if store.store_id == "people_db"
    ))
    outcome = migrations.apply_migrations(tmp_path, plan=plan)
    assert outcome.error and "interrupted after real unified v8 migration" in outcome.error
    assert outcome.backup_id
    assert outcome.rolled_back == ("people_db",)
    with sqlite3.connect(tmp_path / "people.db") as conn:
        assert list(conn.iterdump()) == before


def test_failed_attention_migration_rolls_back_receipts_and_schema(tmp_path, monkeypatch):
    people = PersonStore(tmp_path)
    people.keep_from_apollo(apollo_id="kept", first_name="Kept", last_name_obfuscated=None, title=None, company=None)
    people._conn.execute("PRAGMA user_version=6")
    people._conn.commit()
    people._conn.close()
    with sqlite3.connect(tmp_path / "people.db") as conn:
        before = list(conn.iterdump())
    fail_after_unified_upgrade(monkeypatch)
    plan = migrations.MigrationPlan(stores=tuple(
        store for store in migrations.plan_migrations(tmp_path).stores if store.store_id == "people_db"
    ))
    outcome = migrations.apply_migrations(tmp_path, plan=plan)
    assert outcome.error and outcome.rolled_back == ("people_db",)
    assert "interrupted after real unified v8 migration" in outcome.error
    assert outcome.backup_id
    with sqlite3.connect(tmp_path / "people.db") as conn:
        assert list(conn.iterdump()) == before
