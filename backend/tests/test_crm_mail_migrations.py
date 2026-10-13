import sqlite3

import pytest

from coworker import migrations
from coworker.people import PersonStore, SCHEMA_VERSION


def test_mail_migration_is_registered_and_preserves_populated_v3(tmp_path):
    people = PersonStore(tmp_path)
    pid = people.keep_from_apollo(apollo_id="saved", first_name="Saved", last_name_obfuscated=None,
        title="Founder", company="Example")["person_id"]
    before = people.get(pid)
    for table in ("crm_analysis_state", "gmail_messages", "gmail_collection_state"):
        people._conn.execute(f"DROP TABLE IF EXISTS {table}")
    people._conn.execute("PRAGMA user_version = 3")
    people._conn.commit()
    people._conn.close()
    upgraded = PersonStore(tmp_path)
    assert SCHEMA_VERSION == 4
    assert upgraded.get(pid) == before
    assert upgraded._conn.execute("PRAGMA user_version").fetchone()[0] == 4
    assert upgraded._conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert migrations.list_backups(tmp_path)


def test_failed_mail_migration_rolls_back(tmp_path, monkeypatch):
    people = PersonStore(tmp_path)
    people._conn.execute("PRAGMA user_version = 3")
    people._conn.commit()
    people._conn.close()
    with sqlite3.connect(tmp_path / "people.db") as conn:
        before = list(conn.iterdump())
    monkeypatch.setattr(migrations, "MAIL_SCHEMA", (*migrations.MAIL_SCHEMA, "INVALID SQL"))
    outcome = migrations.apply_migrations(tmp_path, plan=migrations.plan_migrations(tmp_path, store_ids=("people_db",)))
    assert outcome.error
    assert outcome.rolled_back == ("people_db",)
    with sqlite3.connect(tmp_path / "people.db") as conn:
        assert list(conn.iterdump()) == before
