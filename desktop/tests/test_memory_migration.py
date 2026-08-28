"""The registered conversation_db step that adopts context-projection-v1.

Every memory row already on disk becomes `legacy_unclassified` with
`classification_status = needs_review`, and is withheld from model context until
the director classifies it. The step is a real registry migration: it is
versioned, backed up, rolled back on failure, safe to rerun, and it refuses a
database from a future build.
"""

import sqlite3

import pytest

from coworker import migrations
from coworker.migrations import Migration, StoreStatus
from coworker.store import (
    MEMORY_CLASSIFIED,
    MEMORY_NEEDS_REVIEW,
    ConversationStore,
)

# The value the registry step writes, asserted as the literal that lands on
# disk rather than through a symbol the store could later rename.
MEMORY_CATEGORY_LEGACY = "legacy_unclassified"
from tests.state_fixtures import build_current_state, build_legacy_state


def _dump_database(path):
    conn = sqlite3.connect(path)
    try:
        schema = sorted(
            (str(row[0]), str(row[1]), str(row[2] or ""))
            for row in conn.execute(
                "SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
            )
        )
        tables = [name for kind, name, _sql in schema if kind == "table"]
        return {
            "user_version": int(conn.execute("PRAGMA user_version").fetchone()[0]),
            "integrity": [str(row[0]) for row in conn.execute("PRAGMA integrity_check")],
            "schema": schema,
            "rows": {
                name: conn.execute(f"SELECT * FROM {name} ORDER BY rowid").fetchall()
                for name in tables
            },
        }
    finally:
        conn.close()


def _memories(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in conn.execute("SELECT * FROM memories ORDER BY id")]
    finally:
        conn.close()


def _plan_for(plan, store_id):
    return next(item for item in plan.stores if item.store_id == store_id)


def _break_memory_step(monkeypatch, apply):
    """Replace only the 1 -> 2 step, so the failure is this work failing."""
    original = migrations.spec_for("conversation_db")
    steps = tuple(
        migrations.dataclasses.replace(step, apply=apply)
        if step.to_version == 2
        else step
        for step in original.migrations
    )
    broken = migrations.dataclasses.replace(original, migrations=steps)
    monkeypatch.setattr(
        migrations,
        "REGISTRY",
        tuple(
            broken if item.store_id == "conversation_db" else item
            for item in migrations.REGISTRY
        ),
    )


def test_the_registry_carries_a_step_to_the_memory_classification_version():
    spec = migrations.spec_for("conversation_db")

    assert spec.current_version == 2
    assert [step.to_version for step in spec.migrations] == [1, 2]
    assert isinstance(spec.migrations[-1], Migration)


def test_every_existing_memory_row_migrates_to_legacy_unclassified(tmp_path):
    root = build_legacy_state(tmp_path / "state")

    plan = migrations.plan_migrations(root)
    store_plan = _plan_for(plan, "conversation_db")
    assert store_plan.status is StoreStatus.PENDING
    assert store_plan.to_version == 2

    outcome = migrations.apply_migrations(root)
    assert outcome.error is None

    rows = _memories(root / "club.db")
    assert len(rows) == 1
    row = rows[0]
    assert row["category"] == MEMORY_CATEGORY_LEGACY
    assert row["classification_status"] == MEMORY_NEEDS_REVIEW
    assert row["source_ref"] == "sourcecado:memory/1"
    assert row["sensitivity"] == "standard"
    assert row["person_id"] is None
    assert row["claim_key"] is None


def test_the_migration_preserves_ids_content_and_timestamps(tmp_path):
    root = build_legacy_state(tmp_path / "state")
    before = _memories(root / "club.db")[0]

    migrations.apply_migrations(root)

    after = _memories(root / "club.db")[0]
    assert after["id"] == before["id"]
    assert after["content"] == before["content"]
    assert after["created_at"] == before["created_at"]
    assert after["updated_at"] == before["created_at"]


def test_the_migration_never_infers_a_category_from_row_text(tmp_path):
    """A well-worded row must not earn a category by containing the right phrase."""
    root = build_legacy_state(tmp_path / "state")
    conn = sqlite3.connect(root / "club.db")
    conn.execute(
        "INSERT INTO memories (content) VALUES (?)",
        ("Fisher's global preference: always keep outreach drafts under 140 words.",),
    )
    conn.execute(
        "INSERT INTO memories (content) VALUES (?)",
        ("Operator preference, person-independent: use plain sign-offs.",),
    )
    conn.commit()
    conn.close()

    migrations.apply_migrations(root)

    rows = _memories(root / "club.db")
    assert len(rows) == 3
    assert {row["category"] for row in rows} == {MEMORY_CATEGORY_LEGACY}
    assert {row["classification_status"] for row in rows} == {MEMORY_NEEDS_REVIEW}


def test_a_migrated_row_is_withheld_from_model_context(tmp_path):
    root = build_legacy_state(tmp_path / "state")
    migrations.apply_migrations(root)

    store = ConversationStore(root)
    store.remember("Prefer outreach drafts under 140 words.")
    store.memory_classify(2)

    projected = store.memory_projection_items()

    # Non-vacuous: the classified preference is projected, the legacy row is not.
    assert [item.id for item in projected] == ["memory:2"]
    assert store.memory_backlog()["needs_review"] == 1


def test_a_legacy_row_is_withheld_even_before_the_migration_runs(tmp_path):
    """Opening the store first adds the columns but classifies nothing."""
    root = build_legacy_state(tmp_path / "state")

    store = ConversationStore(root)

    assert store.list_memories()[0]["classification_status"] is None
    assert store.memory_projection_items() == ()
    assert store.memory_backlog()["needs_review"] == 1


def test_the_memory_step_backs_the_store_up_before_it_writes(tmp_path):
    root = build_legacy_state(tmp_path / "state")

    outcome = migrations.apply_migrations(root)

    assert outcome.backup_id is not None
    backup = root / "backups" / outcome.backup_id / "club.db"
    assert backup.is_file()
    saved = _memories(backup)
    assert "classification_status" not in saved[0]
    assert saved[0]["content"] == _memories(root / "club.db")[0]["content"]


def test_a_failing_memory_step_undoes_its_own_classification_write(
    tmp_path, monkeypatch
):
    """The step must not use `executescript`, which commits before it runs.

    Real work lands first so the assertion tells an undo apart from a no-op. If
    the step ended the transaction `_apply_store` opened, the columns and the
    classification below would survive the rollback.
    """
    root = build_legacy_state(tmp_path / "state")
    before = _dump_database(root / "club.db")
    assert before["integrity"] == ["ok"]
    real = migrations.spec_for("conversation_db").migrations[-1].apply

    def apply_then_explode(context):
        real(context)
        raise RuntimeError("memory step failed after writing")

    _break_memory_step(monkeypatch, apply_then_explode)

    outcome = migrations.apply_migrations(root)
    assert outcome.error is not None
    assert outcome.rolled_back == ("conversation_db",)

    after = _dump_database(root / "club.db")
    assert after["integrity"] == ["ok"]
    assert after["user_version"] == 0
    assert after["schema"] == before["schema"]
    assert after["rows"] == before["rows"]
    memories_ddl = next(sql for kind, name, sql in after["schema"] if name == "memories")
    assert "classification_status" not in memories_ddl


def test_the_memory_step_keeps_the_transaction_it_was_given(tmp_path):
    """Isolates the transaction half of the rollback, which the backup hides.

    `apply_migrations` restores the store from the backup as well, so a step
    that ended its own transaction still leaves the right file on disk and the
    end-to-end test above stays green. This asserts the narrower thing with no
    backup involved: run the step inside a transaction, roll it back by hand,
    and nothing it wrote survives. `executescript` commits before it runs, so
    it fails here.
    """
    root = build_legacy_state(tmp_path / "state")
    db = root / "club.db"
    spec = migrations.spec_for("conversation_db")
    step = spec.migrations[-1]
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
        step.apply(
            migrations.MigrationContext(
                root=root, spec=spec, path=db, connection=conn
            )
        )
        assert "classification_status" in migrations.column_names(conn, "memories")

        conn.rollback()

        assert "classification_status" not in migrations.column_names(conn, "memories")
    finally:
        conn.close()


def test_rerunning_the_migration_reports_current_and_changes_nothing(tmp_path):
    root = build_legacy_state(tmp_path / "state")
    migrations.apply_migrations(root)
    settled = _dump_database(root / "club.db")

    plan = migrations.plan_migrations(root)
    assert _plan_for(plan, "conversation_db").status is StoreStatus.CURRENT
    outcome = migrations.apply_migrations(root)

    assert outcome.error is None
    assert outcome.applied == ()
    assert _dump_database(root / "club.db") == settled
    assert settled["user_version"] == 2


def test_a_classified_preference_survives_a_rerun(tmp_path):
    root = build_current_state(tmp_path / "state")
    migrations.apply_migrations(root)
    store = ConversationStore(root)
    store.remember("Prefer outreach drafts under 140 words.")
    store.memory_classify(1)

    migrations.apply_migrations(root)

    reopened = ConversationStore(root)
    assert reopened.list_memories()[0]["classification_status"] == MEMORY_CLASSIFIED
    assert len(reopened.memory_projection_items()) == 1


def test_a_conversation_db_from_a_future_build_is_refused(tmp_path):
    root = build_current_state(tmp_path / "state")
    migrations.apply_migrations(root)
    conn = sqlite3.connect(root / "club.db")
    conn.execute("PRAGMA user_version = 3")
    conn.commit()
    conn.close()
    before = _dump_database(root / "club.db")

    plan = migrations.plan_migrations(root)
    assert plan.blocked is True
    assert _plan_for(plan, "conversation_db").status is StoreStatus.UNSUPPORTED_FUTURE

    outcome = migrations.apply_migrations(root)
    assert outcome.blocked is True
    assert outcome.applied == ()
    assert _dump_database(root / "club.db") == before


def test_the_plan_counts_the_rows_the_memory_step_will_classify(tmp_path):
    root = build_legacy_state(tmp_path / "state")
    conn = sqlite3.connect(root / "club.db")
    conn.execute("INSERT INTO memories (content) VALUES ('second')")
    conn.commit()
    conn.close()

    plan = migrations.plan_migrations(root)

    assert _plan_for(plan, "conversation_db").record_count >= 2
    outcome = migrations.apply_migrations(root)
    memory_step = next(
        step
        for step in outcome.applied
        if step.store_id == "conversation_db" and step.to_version == 2
    )
    assert memory_step.record_count == 2


@pytest.mark.parametrize("store_id", ["conversation_db"])
def test_the_step_chain_stays_contiguous(store_id):
    spec = migrations.spec_for(store_id)
    expected = 0
    for step in spec.migrations:
        assert step.from_version == expected
        assert step.to_version == expected + 1
        expected = step.to_version
    assert expected == spec.current_version
