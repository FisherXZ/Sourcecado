"""People-only upgrade checks avoid unrelated connector/workspace fixtures."""

import json
import sqlite3

import pytest

from coworker import migrations
from coworker.people import PersonStore
from tests.state_fixtures import LEGACY_PERSON_ID, build_legacy_state


def snapshot(path):
    with sqlite3.connect(path) as conn:
        return list(conn.iterdump())


def test_fresh_database_is_current_and_enforces_foreign_keys(tmp_path):
    people = PersonStore(tmp_path)
    assert people._conn.execute("PRAGMA user_version").fetchone()[0] == 3
    assert people._conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert people._conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert migrations.plan_migrations(tmp_path, store_ids=("people_db",)).pending == ()


def test_constructor_upgrades_legacy_people_only_and_preserves_handoffs(tmp_path):
    root = build_legacy_state(tmp_path / "legacy")
    conversations_before = (root / "club.db").read_bytes()
    people = PersonStore(root)
    person = people.get(LEGACY_PERSON_ID)
    assert person["first_name"] == "Dana"
    assert person["sequence_state"] == "open"
    assert people._conn.execute("PRAGMA user_version").fetchone()[0] == 3
    assert (root / "club.db").read_bytes() == conversations_before
    assert migrations.list_backups(root)
    assert people._conn.execute("SELECT count(*) FROM person_tasks").fetchone()[0] == 0


def prior_v2(root):
    people = PersonStore(root)
    person = people.keep_from_apollo(apollo_id="maya", first_name="Maya", last_name_obfuscated=None,
        title="Founder", company="Example")
    people.patch(person["person_id"], expected_version=1, actor="director",
        rationale_summary="Reviewed handoff", fields={"handoff_who": "Private handoff"})
    people._conn.execute("INSERT INTO person_attachments VALUES ('private', ?, 'source_ref', 'private', ?, 1, 'now', 'now')",
        (person["person_id"], json.dumps({"provider": "drive", "title": "Private source"})))
    # Remove only this fixture's newly added tables to represent real v2 state.
    for table in ("crm_operations", "crm_changes", "person_tasks"):
        if table == "crm_changes":
            people._conn.execute("DROP TRIGGER crm_changes_no_delete")
        people._conn.execute(f"DROP TABLE {table}")
    people._conn.execute("PRAGMA user_version = 2")
    people._conn.commit()
    people._conn.close()
    return person["person_id"]


def test_v2_upgrade_preserves_people_handoffs_private_sources_and_timeline(tmp_path):
    pid = prior_v2(tmp_path)
    with sqlite3.connect(tmp_path / "people.db") as conn:
        before = {table: conn.execute(f"SELECT * FROM {table}").fetchall()
                  for table in ("people", "person_attachments", "person_versions", "events")}
    people = PersonStore(tmp_path)
    for table, rows in before.items():
        assert [tuple(r) for r in people._conn.execute(f"SELECT * FROM {table}").fetchall()] == rows
    assert people.get(pid)["handoff_who"] == "Private handoff"
    assert people.get(pid, expand_sources=True)["sources"] == []
    assert people._conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert len(migrations.list_backups(tmp_path)) == 1


def test_failed_task_migration_restores_exact_database_and_backup_can_be_restored(tmp_path, monkeypatch):
    prior_v2(tmp_path)
    before = snapshot(tmp_path / "people.db")
    statements = migrations.TASK_SCHEMA
    monkeypatch.setattr(migrations, "TASK_SCHEMA", (*statements, "INVALID SQL"))
    outcome = migrations.apply_migrations(tmp_path, plan=migrations.plan_migrations(tmp_path, store_ids=("people_db",)))
    assert outcome.error
    assert outcome.rolled_back == ("people_db",)
    assert outcome.backup_id
    assert snapshot(tmp_path / "people.db") == before
    monkeypatch.setattr(migrations, "TASK_SCHEMA", statements)
    people = PersonStore(tmp_path)
    people._conn.close()
    restored = migrations.restore_backup(tmp_path, outcome.backup_id)
    assert restored["restored"] == ["people_db"]
    assert restored["safety_backup_id"]
    assert snapshot(tmp_path / "people.db") == before


def test_future_schema_is_rejected_before_constructor_changes_data(tmp_path):
    prior_v2(tmp_path)
    with sqlite3.connect(tmp_path / "people.db") as conn:
        conn.execute("PRAGMA user_version = 999")
    before = snapshot(tmp_path / "people.db")
    with pytest.raises(RuntimeError, match="upgrade failed"):
        PersonStore(tmp_path)
    assert snapshot(tmp_path / "people.db") == before
