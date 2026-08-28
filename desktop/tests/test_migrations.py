"""S5: one versioned migration registry across every durable Sourcecado store."""

import hashlib
import json
import os
import sqlite3
from pathlib import Path

import pytest

from coworker import migrations
from coworker.agent_run_approval import EFFECT_STATEMENTS
from coworker.migrations import (
    BackupFailed,
    Migration,
    StoreKind,
    StoreStatus,
    VersionChannel,
)
from tests.state_fixtures import (
    LEGACY_PERSON_ID,
    PLANTED_CANARIES,
    build_current_state,
    build_legacy_state,
)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree_digest(root: Path) -> dict[str, str]:
    """Hash the state a store holds, not SQLite's bookkeeping about it.

    A database in WAL mode recreates its `-shm` and `-wal` sidecars whenever it
    is opened, including read-only: the journal mode lives in the file header,
    so merely reading one brings them back. Hashing those turns "this changed
    nothing" into "nobody opened this", which is a different and weaker claim.

    `-shm` is shared memory and never holds committed data, so it is always
    skipped. A `-wal` is skipped only while it is empty. A write parked in a
    WAL that has not been checkpointed still shows up, so a change that hid
    there rather than in the main file is still caught.
    """
    digests: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.name.endswith("-shm"):
            continue
        if path.name.endswith("-wal") and path.stat().st_size == 0:
            continue
        digests[str(path.relative_to(root))] = _digest(path)
    return digests


def _user_version(path: Path) -> int:
    conn = sqlite3.connect(path)
    try:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])
    finally:
        conn.close()


def _dump_database(path: Path) -> dict[str, object]:
    """Everything a rollback has to put back, in comparable form.

    Restoring a live SQLite file goes through the online backup API, which
    rewrites page layout, so the bytes legitimately differ for a logically
    identical database. This captures what must not differ: the schema version,
    the exact DDL of every table and index, and every row of every table --
    `SELECT *` so an added column shows up as a wider tuple.
    """
    conn = sqlite3.connect(path)
    try:
        integrity = [str(row[0]) for row in conn.execute("PRAGMA integrity_check")]
        schema = sorted(
            (str(row[0]), str(row[1]), str(row[2] or ""))
            for row in conn.execute(
                "SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
            )
        )
        tables = [name for kind, name, _sql in schema if kind == "table"]
        rows = {
            name: conn.execute(f"SELECT * FROM {name} ORDER BY rowid").fetchall()
            for name in tables
        }
        return {
            "user_version": int(conn.execute("PRAGMA user_version").fetchone()[0]),
            "integrity": integrity,
            "schema": schema,
            "rows": rows,
        }
    finally:
        conn.close()


def _plan_for(plan, store_id):
    return next(item for item in plan.stores if item.store_id == store_id)


# --- the registry itself -------------------------------------------------


def test_registry_names_every_active_durable_store():
    registered = {spec.store_id for spec in migrations.REGISTRY}
    assert registered == {
        "agent_runs_db",
        "conversation_db",
        "people_db",
        "drive_ingestion",
        "meeting_evidence",
        "conversation_transcripts",
        "presentation_events",
        "memory_notes",
        "workspace_receipts",
        "workspace_grants",
        "shell_tasks",
        "host_command_approvals",
        "directory_requests",
        "workspace_trash",
        "secrets",
        "mcp_config",
        "dotenv",
    }


def test_registry_reuses_the_version_constant_owned_by_each_store_module():
    from coworker.people import SCHEMA_VERSION as people_version
    from coworker.secrets import SCHEMA_VERSION as secrets_version
    from coworker.store import SCHEMA_VERSION as conversation_version
    from coworker.workspace import WorkspaceGrantStore
    from coworker.workspace_audit import SCHEMA_VERSION as receipts_version
    from coworker.workspace_policy import HostApprovalStore
    from coworker.workspace_runtime import DirectoryRequestStore
    from coworker.workspace_shell import ShellTaskStore

    expected = {
        "conversation_db": conversation_version,
        "people_db": people_version,
        "secrets": secrets_version,
        "workspace_receipts": receipts_version,
        "workspace_grants": WorkspaceGrantStore.VERSION,
        "shell_tasks": ShellTaskStore.VERSION,
        "host_command_approvals": HostApprovalStore.VERSION,
        "directory_requests": DirectoryRequestStore.VERSION,
    }
    for store_id, version in expected.items():
        assert migrations.spec_for(store_id).current_version == version


def test_every_migration_chain_is_contiguous_and_ends_at_the_current_version():
    for spec in migrations.REGISTRY:
        if spec.version_channel is VersionChannel.NONE:
            assert spec.migrations == ()
            continue
        expected = 0
        for step in spec.migrations:
            assert step.from_version == expected
            assert step.to_version == expected + 1
            expected = step.to_version
        assert expected == spec.current_version


# --- version reporting ---------------------------------------------------


def test_every_versioned_store_reports_a_version_after_adoption(tmp_path):
    root = build_current_state(tmp_path / "state")
    migrations.apply_migrations(root)

    for spec in migrations.REGISTRY:
        if spec.version_channel is VersionChannel.NONE:
            continue
        assert migrations.read_version(root, spec) == spec.current_version, spec.store_id


def test_an_absent_store_reports_no_version_and_needs_no_migration(tmp_path):
    root = tmp_path / "empty"
    root.mkdir()
    plan = migrations.plan_migrations(root)

    for item in plan.stores:
        assert item.status is StoreStatus.ABSENT
        assert item.from_version is None
        assert item.steps == ()
    assert plan.pending == ()


# --- upgrades from the supported prior version ---------------------------


def test_legacy_conversation_db_upgrades_from_the_pre_registry_shape(tmp_path):
    root = build_legacy_state(tmp_path / "state")
    db = root / "club.db"
    assert _user_version(db) == 0

    plan = migrations.plan_migrations(root)
    store_plan = _plan_for(plan, "conversation_db")
    assert store_plan.status is StoreStatus.PENDING
    assert store_plan.from_version == 0
    assert store_plan.to_version == 1
    assert store_plan.record_count > 0

    outcome = migrations.apply_migrations(root)
    assert outcome.error is None
    assert _user_version(db) == 1

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        job = conn.execute("SELECT * FROM jobs WHERE id = 1").fetchone()
        assert job["name"] == "weekly sourcing check-in"
        assert job["template_id"] == "legacy"
        assert job["cadence"] == "0 9 * * 1"
        runs = conn.execute("SELECT * FROM runs ORDER BY id").fetchall()
        assert [row["status"] for row in runs] == ["success", "failed"]
        assert all(row["artifacts"] == "[]" for row in runs)
        assert all(row["session_id"] == "sched-1" for row in runs)
        session = conn.execute(
            "SELECT * FROM sessions WHERE session_id = 'main'"
        ).fetchone()
        assert session["pinned"] == 0
        assert session["opened_at"] == session["updated_at"]
        inbox = conn.execute(
            "SELECT * FROM inbox WHERE id = 'call_apollo_enrich_1'"
        ).fetchone()
        assert inbox["scope"] == "once"
        assert inbox["execution_status"] == "pending"
        assert inbox["requested_at"] == inbox["created_at"]
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert {
            "chat_queue",
            "queue_commands",
            "queue_sessions",
            "recovery_commands",
        } <= tables
    finally:
        conn.close()


def test_legacy_people_db_upgrades_from_the_pre_registry_shape(tmp_path):
    root = build_legacy_state(tmp_path / "state")
    db = root / "people.db"
    assert _user_version(db) == 0

    store_plan = _plan_for(migrations.plan_migrations(root), "people_db")
    assert store_plan.status is StoreStatus.PENDING
    assert store_plan.record_count > 0

    assert migrations.apply_migrations(root).error is None
    assert _user_version(db) == 1

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        person = conn.execute(
            "SELECT * FROM people WHERE person_id = ?", (LEGACY_PERSON_ID,)
        ).fetchone()
        assert person["first_name"] == "Dana"
        assert person["version"] == 1
        assert person["outcome"] is None
        assert person["deleted_at"] is None
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert {"person_attachments", "person_versions"} <= tables
    finally:
        conn.close()


def test_legacy_meeting_evidence_db_upgrades_from_the_pre_registry_shape(tmp_path):
    root = build_legacy_state(tmp_path / "state")
    db = root / "meeting_evidence.db"
    assert _user_version(db) == 0

    store_plan = _plan_for(migrations.plan_migrations(root), "meeting_evidence")
    assert store_plan.status is StoreStatus.PENDING
    assert store_plan.from_version == 0
    assert store_plan.to_version == 1
    assert store_plan.record_count == 1

    assert migrations.apply_migrations(root).error is None
    assert _user_version(db) == 1

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        meeting = conn.execute(
            "SELECT * FROM meeting_evidence WHERE evidence_id = 'meeting_abc123abc123abc123abcd'"
        ).fetchone()
        assert meeting["provider"] == "calendar"
        assert meeting["provider_id"] == "evt_1"
        assert meeting["title"] == "Rippling intro"
        assert meeting["status"] == "unmatched"
    finally:
        conn.close()


def test_legacy_drive_ingestion_db_upgrades_from_the_pre_registry_shape(tmp_path):
    root = build_legacy_state(tmp_path / "state")
    db = root / "drive_ingestion.db"
    assert _user_version(db) == 0

    store_plan = _plan_for(migrations.plan_migrations(root), "drive_ingestion")
    assert store_plan.status is StoreStatus.PENDING
    assert store_plan.from_version == 0
    assert store_plan.to_version == 1
    assert store_plan.record_count > 0

    assert migrations.apply_migrations(root).error is None
    assert _user_version(db) == 1

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        job = conn.execute(
            "SELECT * FROM drive_ingestion_jobs WHERE id = 'drive_ingest_1'"
        ).fetchone()
        assert job["folder_id"] == "folder_1"
        assert job["resolved_path"] == "Codeology/Sourcing"
        assert job["status"] == "paused"
        assert job["work_revision"] == 1
    finally:
        conn.close()


def test_legacy_json_documents_are_stamped_without_losing_records(tmp_path):
    root = build_legacy_state(tmp_path / "state")
    grants_path = root / "workspace_grants.json"
    assert "version" not in json.loads(grants_path.read_text(encoding="utf-8"))

    store_plan = _plan_for(migrations.plan_migrations(root), "workspace_grants")
    assert store_plan.status is StoreStatus.PENDING
    assert store_plan.record_count == 1

    assert migrations.apply_migrations(root).error is None
    data = json.loads(grants_path.read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert len(data["grants"]) == 1
    assert data["grants"][0]["id"] == "grant_legacy_1"


def test_legacy_jsonl_and_opaque_stores_adopt_a_version_in_the_manifest(tmp_path):
    root = build_legacy_state(tmp_path / "state")
    assert migrations.apply_migrations(root).error is None

    manifest = json.loads((root / migrations.MANIFEST_NAME).read_text(encoding="utf-8"))
    assert manifest["version"] == 1
    for store_id in (
        "conversation_transcripts",
        "presentation_events",
        "workspace_receipts",
        "secrets",
        "mcp_config",
    ):
        assert manifest["stores"][store_id] == 1


def test_state_is_readable_by_the_real_stores_after_a_restart(tmp_path):
    from coworker.people import PersonStore
    from coworker.store import ConversationStore
    from coworker.workspace import WorkspaceGrantStore

    root = build_legacy_state(tmp_path / "state")
    assert migrations.apply_migrations(root).error is None

    store = ConversationStore(root)
    assert store.load("main")[0]["content"] == "find leads at Rippling"
    assert len(store.list_schedule()["jobs"]) == 1
    assert store.get_setting("persona") == "sourcing"
    people = PersonStore(root)
    assert people.get(LEGACY_PERSON_ID)["first_name"] == "Dana"
    assert people.person_for_session("main") == LEGACY_PERSON_ID
    assert len(WorkspaceGrantStore(root).list_all()) == 1

    # Constructing the stores must not knock the recorded version off current.
    assert _user_version(root / "club.db") == 1
    assert _user_version(root / "people.db") == 1


# --- failing closed ------------------------------------------------------


def test_unknown_future_sqlite_version_fails_closed_without_modifying_state(tmp_path):
    root = build_current_state(tmp_path / "state")
    conn = sqlite3.connect(root / "club.db")
    conn.execute("PRAGMA user_version = 99")
    conn.commit()
    conn.close()
    before = _tree_digest(root)

    plan = migrations.plan_migrations(root)
    assert plan.blocked is True
    assert _plan_for(plan, "conversation_db").status is StoreStatus.UNSUPPORTED_FUTURE

    outcome = migrations.apply_migrations(root)
    assert outcome.blocked is True
    assert outcome.applied == ()
    assert outcome.backup_id is None
    assert _tree_digest(root) == before


def test_unknown_future_json_version_fails_closed_without_modifying_state(tmp_path):
    root = build_current_state(tmp_path / "state")
    path = root / "shell_tasks.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["version"] = 7
    path.write_text(json.dumps(data), encoding="utf-8")
    before = _tree_digest(root)

    plan = migrations.plan_migrations(root)
    assert plan.blocked is True
    assert _plan_for(plan, "shell_tasks").status is StoreStatus.UNSUPPORTED_FUTURE
    assert migrations.apply_migrations(root).applied == ()
    assert _tree_digest(root) == before


def test_a_missing_migration_step_is_reported_rather_than_silently_skipped(
    tmp_path, monkeypatch
):
    root = build_current_state(tmp_path / "state")
    original = migrations.spec_for("shell_tasks")
    gapped = migrations.dataclasses.replace(original, current_version=3)
    monkeypatch.setattr(
        migrations,
        "REGISTRY",
        tuple(gapped if item.store_id == "shell_tasks" else item for item in migrations.REGISTRY),
    )
    before = _tree_digest(root)

    plan = migrations.plan_migrations(root)
    assert _plan_for(plan, "shell_tasks").status is StoreStatus.MIGRATION_PATH_MISSING
    assert plan.blocked is True
    assert migrations.apply_migrations(root).applied == ()
    assert _tree_digest(root) == before


def test_an_unreadable_json_store_is_reported_and_never_migrated(tmp_path):
    root = build_current_state(tmp_path / "state")
    (root / "directory_requests.json").write_text("{ not json", encoding="utf-8")
    before = _tree_digest(root)

    plan = migrations.plan_migrations(root)
    assert _plan_for(plan, "directory_requests").status is StoreStatus.UNREADABLE
    assert plan.blocked is True
    assert _tree_digest(root) == before


# --- backups -------------------------------------------------------------


def test_migration_takes_a_timestamped_backup_before_changing_state(tmp_path):
    root = build_legacy_state(tmp_path / "state")
    outcome = migrations.apply_migrations(root)

    assert outcome.backup_id is not None
    assert outcome.backup_id.startswith("doctor-")
    backup_dir = root / migrations.BACKUPS_DIR_NAME / outcome.backup_id
    assert backup_dir.is_dir()
    assert oct(backup_dir.stat().st_mode & 0o777) == "0o700"
    manifest = json.loads((backup_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["reason"] == "migration"
    backed_up = {entry["store_id"] for entry in manifest["entries"]}
    assert {"conversation_db", "people_db", "workspace_grants"} <= backed_up
    assert _user_version(backup_dir / "club.db") == 0


def test_backups_can_be_listed_and_restored(tmp_path):
    root = build_current_state(tmp_path / "state")
    migrations.apply_migrations(root)
    grants_path = root / "workspace_grants.json"
    original = grants_path.read_bytes()

    backup = migrations.create_backup(root, ["workspace_grants"], reason="manual")
    grants_path.write_text(json.dumps({"version": 1, "grants": []}), encoding="utf-8")
    assert grants_path.read_bytes() != original

    listed = migrations.list_backups(root)
    assert backup.backup_id in {item["backup_id"] for item in listed}
    assert all("store_id" in entry for item in listed for entry in item["entries"])

    result = migrations.restore_backup(root, backup.backup_id)
    assert result["restored"] == ["workspace_grants"]
    assert grants_path.read_bytes() == original


def test_restore_backs_up_the_current_state_before_overwriting_it(tmp_path):
    root = build_current_state(tmp_path / "state")
    migrations.apply_migrations(root)
    backup = migrations.create_backup(root, ["workspace_grants"], reason="manual")
    replaced = json.dumps({"version": 1, "grants": []})
    (root / "workspace_grants.json").write_text(replaced, encoding="utf-8")

    result = migrations.restore_backup(root, backup.backup_id)
    safety = result["safety_backup_id"]
    assert safety != backup.backup_id
    safety_copy = root / migrations.BACKUPS_DIR_NAME / safety / "workspace_grants.json"
    assert safety_copy.read_text(encoding="utf-8") == replaced


def test_secret_bearing_stores_are_never_copied_into_a_backup(tmp_path):
    root = build_current_state(tmp_path / "state", plant_secrets=True)
    backup = migrations.create_backup(
        root, [spec.store_id for spec in migrations.REGISTRY], reason="manual"
    )

    assert not (backup.path / "secrets.json").exists()
    assert not (backup.path / ".env").exists()
    entries = {entry["store_id"]: entry for entry in backup.manifest["entries"]}
    assert entries["secrets"]["content_backed_up"] is False
    assert entries["dotenv"]["content_backed_up"] is False
    assert entries["conversation_db"]["content_backed_up"] is True


def test_backup_manifest_carries_no_secret_and_no_absolute_path(tmp_path):
    root = build_current_state(tmp_path / "state", plant_secrets=True)
    backup = migrations.create_backup(
        root, [spec.store_id for spec in migrations.REGISTRY], reason="manual"
    )
    rendered = (backup.path / "manifest.json").read_text(encoding="utf-8")

    for canary in PLANTED_CANARIES:
        assert canary not in rendered
    assert str(root) not in rendered
    assert str(Path.home()) not in rendered


def test_a_failed_backup_aborts_the_migration_and_leaves_state_untouched(tmp_path):
    root = build_legacy_state(tmp_path / "state")
    before = _tree_digest(root)
    blocker = root / migrations.BACKUPS_DIR_NAME
    blocker.write_text("not a directory", encoding="utf-8")

    with pytest.raises(BackupFailed):
        migrations.create_backup(root, ["conversation_db"], reason="manual")

    outcome = migrations.apply_migrations(root)
    assert outcome.error is not None
    assert outcome.applied == ()
    assert _user_version(root / "club.db") == 0
    blocker.unlink()
    assert _tree_digest(root) == before


# --- failure, rollback, rerun -------------------------------------------


def _break_step(store_id, monkeypatch, apply):
    """Swap one store's migration for a step that fails partway through."""
    original = migrations.spec_for(store_id)
    broken = migrations.dataclasses.replace(
        original,
        migrations=(
            Migration(
                from_version=0,
                to_version=1,
                description="deliberately failing step",
                count=lambda _context: 1,
                apply=apply,
            ),
        ),
    )
    monkeypatch.setattr(
        migrations,
        "REGISTRY",
        tuple(
            broken if item.store_id == store_id else item
            for item in migrations.REGISTRY
        ),
    )


def test_a_sqlite_migration_that_fails_halfway_leaves_the_database_as_it_was(
    tmp_path, monkeypatch
):
    """Two mechanisms cover a SQLite store: the step runs inside BEGIN IMMEDIATE
    and is rolled back, and the store is then restored from the backup. They are
    deliberately redundant, so this asserts the outcome rather than crediting one
    of them. Disabling either alone still passes; disabling both fails here. The
    JSON test below is the one that isolates the backup restore, because a JSON
    document has no transaction to fall back on.
    """
    root = build_legacy_state(tmp_path / "state")
    before = _dump_database(root / "club.db")
    assert before["integrity"] == ["ok"]
    assert len(before["rows"]["runs"]) == 2

    def mutate_then_explode(context):
        # Real work lands first, so the test can tell an undo from a no-op.
        context.connection.execute("ALTER TABLE jobs ADD COLUMN next_run_at TEXT")
        context.connection.execute("DELETE FROM runs")
        raise RuntimeError("migration step failed halfway")

    _break_step("conversation_db", monkeypatch, mutate_then_explode)

    outcome = migrations.apply_migrations(root)
    assert outcome.error is not None
    assert outcome.rolled_back == ("conversation_db",)

    # Sound, at the old version, at the old schema, every original row present.
    after = _dump_database(root / "club.db")
    assert after["integrity"] == ["ok"]
    assert after["user_version"] == 0
    assert after["schema"] == before["schema"]
    assert after["rows"] == before["rows"]
    jobs_ddl = next(sql for kind, name, sql in after["schema"] if name == "jobs")
    assert "next_run_at" not in jobs_ddl


def test_a_json_migration_that_fails_halfway_is_restored_from_the_backup(
    tmp_path, monkeypatch
):
    """A JSON document has no transaction, so the backup is the only way back."""
    root = build_legacy_state(tmp_path / "state")
    path = root / "workspace_grants.json"
    before = path.read_bytes()

    def corrupt_then_explode(context):
        context.path.write_text("{ half written", encoding="utf-8")
        raise RuntimeError("migration step failed halfway")

    _break_step("workspace_grants", monkeypatch, corrupt_then_explode)

    outcome = migrations.apply_migrations(root)
    assert outcome.error is not None
    assert outcome.rolled_back == ("workspace_grants",)

    assert path.read_bytes() == before
    assert json.loads(path.read_text(encoding="utf-8"))["grants"][0]["id"] == "grant_legacy_1"
    assert oct(path.stat().st_mode & 0o777) == "0o600"


def test_a_partially_applied_migration_does_not_leave_other_stores_half_done(
    tmp_path, monkeypatch
):
    root = build_legacy_state(tmp_path / "state")

    def explode(_context):
        raise RuntimeError("migration step failed")

    original = migrations.spec_for("people_db")
    broken = migrations.dataclasses.replace(
        original,
        migrations=(
            Migration(
                from_version=0,
                to_version=1,
                description="deliberately failing step",
                count=lambda _context: 1,
                apply=explode,
            ),
        ),
    )
    monkeypatch.setattr(
        migrations,
        "REGISTRY",
        tuple(
            broken if item.store_id == "people_db" else item
            for item in migrations.REGISTRY
        ),
    )

    outcome = migrations.apply_migrations(root)
    assert outcome.error is not None
    assert _user_version(root / "people.db") == 0
    # Stores that already migrated stay migrated; only the failing one rolls back.
    assert "people_db" in outcome.rolled_back
    assert migrations.plan_migrations(root).blocked is False


def test_migrations_are_idempotent_when_rerun(tmp_path):
    root = build_legacy_state(tmp_path / "state")
    first = migrations.apply_migrations(root)
    assert first.applied

    after_first = _tree_digest(root)
    second = migrations.apply_migrations(root)

    assert second.applied == ()
    assert second.backup_id is None
    assert second.error is None
    assert migrations.plan_migrations(root).pending == ()
    unchanged = {
        name: digest
        for name, digest in _tree_digest(root).items()
        if not name.startswith(migrations.BACKUPS_DIR_NAME)
    }
    assert unchanged == {
        name: digest
        for name, digest in after_first.items()
        if not name.startswith(migrations.BACKUPS_DIR_NAME)
    }


def test_migration_survives_a_process_restart_between_runs(tmp_path):
    root = build_legacy_state(tmp_path / "state")
    migrations.apply_migrations(root)

    # A second process reading the same directory sees the recorded versions and
    # has nothing left to do.
    assert migrations.plan_migrations(root).pending == ()
    for spec in migrations.REGISTRY:
        if spec.version_channel is VersionChannel.NONE:
            continue
        assert migrations.read_version(root, spec) == spec.current_version


def test_the_manifest_is_written_with_owner_only_permissions(tmp_path):
    root = build_legacy_state(tmp_path / "state")
    migrations.apply_migrations(root)
    manifest = root / migrations.MANIFEST_NAME

    assert oct(manifest.stat().st_mode & 0o777) == "0o600"


def test_sqlite_backups_are_taken_through_the_online_backup_api(tmp_path):
    root = build_current_state(tmp_path / "state")
    backup = migrations.create_backup(root, ["conversation_db"], reason="manual")
    copied = backup.path / "club.db"

    conn = sqlite3.connect(copied)
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] >= 1
    finally:
        conn.close()
    entry = next(
        item for item in backup.manifest["entries"] if item["store_id"] == "conversation_db"
    )
    assert entry["kind"] == StoreKind.SQLITE.value
    assert entry["relative_path"] == "club.db"
    assert entry["sha256"] == _digest(copied)


def test_backup_files_keep_owner_only_permissions(tmp_path):
    root = build_current_state(tmp_path / "state")
    backup = migrations.create_backup(root, ["conversation_db"], reason="manual")

    assert oct((backup.path / "club.db").stat().st_mode & 0o777) == "0o600"
    assert oct((backup.path / "manifest.json").stat().st_mode & 0o777) == "0o600"
    assert oct(os.stat(root / migrations.BACKUPS_DIR_NAME).st_mode & 0o777) == "0o700"


# --- the Agent Run store: version 1 to 2, the external-effect fence ------

_EFFECT_OBJECTS = {
    "agent_run_effects",
    "agent_run_effects_by_run",
    "agent_run_effects_open",
    "agent_run_effects_open_as_dispatched",
    "agent_run_effects_quarantine_is_operator_only",
    "agent_run_effects_settled_is_final",
    "agent_run_effects_are_never_deleted",
}


def _break_agent_run_fence(monkeypatch, apply):
    """Swap the 1 -> 2 step for one that fails partway through.

    `_break_step` builds a 0 -> 1 step, and the Agent Run store on disk is at
    version 1, so it needs its own.
    """
    original = migrations.spec_for("agent_runs_db")
    broken = migrations.dataclasses.replace(
        original,
        migrations=(
            Migration(
                from_version=1,
                to_version=2,
                description="deliberately failing fence step",
                count=lambda _context: 0,
                apply=apply,
            ),
        ),
    )
    monkeypatch.setattr(
        migrations,
        "REGISTRY",
        tuple(
            broken if item.store_id == "agent_runs_db" else item
            for item in migrations.REGISTRY
        ),
    )


def test_the_agent_run_store_is_registered_with_a_path_from_version_one(tmp_path):
    root = build_legacy_state(tmp_path / "state")

    plan = migrations.plan_migrations(root)
    store = _plan_for(plan, "agent_runs_db")

    assert store.status is StoreStatus.PENDING
    assert store.from_version == 1
    assert store.to_version == 2
    assert [(step.from_version, step.to_version) for step in store.steps] == [(1, 2)]
    assert "external-effect fence" in store.steps[0].description


def test_the_agent_run_fence_is_added_without_disturbing_a_single_run(tmp_path):
    root = build_legacy_state(tmp_path / "state")
    path = root / "agent_runs.db"
    before = _dump_database(path)
    assert before["user_version"] == 1
    assert len(before["rows"]["agent_runs"]) == 2
    assert len(before["rows"]["agent_run_checkpoints"]) == 4

    outcome = migrations.apply_migrations(root)

    assert outcome.error is None
    applied = [item for item in outcome.applied if item.store_id == "agent_runs_db"]
    assert [(item.from_version, item.to_version) for item in applied] == [(1, 2)]
    after = _dump_database(path)
    assert after["integrity"] == ["ok"]
    assert after["user_version"] == 2
    # Every run and checkpoint is exactly as it was.
    assert after["rows"]["agent_runs"] == before["rows"]["agent_runs"]
    assert (
        after["rows"]["agent_run_checkpoints"]
        == before["rows"]["agent_run_checkpoints"]
    )
    # Exactly the fence appeared, and nothing that was there was replaced.
    names_before = {name for _kind, name, _sql in before["schema"]}
    names_after = {name for _kind, name, _sql in after["schema"]}
    assert names_after - names_before == _EFFECT_OBJECTS
    assert names_before - names_after == set()


def test_the_agent_run_store_is_backed_up_before_its_fence_is_added(tmp_path):
    root = build_legacy_state(tmp_path / "state")

    outcome = migrations.apply_migrations(root)

    backup_dir = root / migrations.BACKUPS_DIR_NAME / outcome.backup_id
    manifest = json.loads((backup_dir / "manifest.json").read_text(encoding="utf-8"))
    entry = next(
        item for item in manifest["entries"] if item["store_id"] == "agent_runs_db"
    )
    assert entry["content_backed_up"] is True
    assert entry["store_version"] == 1
    # The copy is the store as it was: version 1, and no fence in it.
    copied = _dump_database(backup_dir / "agent_runs.db")
    assert copied["user_version"] == 1
    assert {name for _kind, name, _sql in copied["schema"]} & _EFFECT_OBJECTS == set()
    assert len(copied["rows"]["agent_runs"]) == 2


def test_a_failed_fence_step_leaves_the_run_store_at_version_one(tmp_path, monkeypatch):
    """The rollback has to undo real work, not a no-op."""
    root = build_legacy_state(tmp_path / "state")
    path = root / "agent_runs.db"
    before = _dump_database(path)

    def mutate_then_explode(context):
        # Land the fence and destroy a run, so an undo is distinguishable.
        for statement in EFFECT_STATEMENTS:
            context.connection.execute(statement)
        context.connection.execute("DELETE FROM agent_runs WHERE run_id = ?",
                                   ("run-legacy-2",))
        raise RuntimeError("fence step failed halfway")

    _break_agent_run_fence(monkeypatch, mutate_then_explode)

    outcome = migrations.apply_migrations(root)

    assert outcome.error is not None
    assert "agent_runs_db" in outcome.rolled_back
    after = _dump_database(path)
    assert after["integrity"] == ["ok"]
    assert after["user_version"] == 1
    assert after["schema"] == before["schema"]
    assert after["rows"] == before["rows"]
    assert {name for _kind, name, _sql in after["schema"]} & _EFFECT_OBJECTS == set()


def test_the_fence_step_never_commits_the_transaction_it_runs_inside(tmp_path):
    """The reason `EFFECT_STATEMENTS` is a tuple and not one script.

    `executescript` issues a COMMIT before it runs. A step that used it would
    end the transaction `_apply_store` opened, and the rollback above would
    then have nothing to undo -- silently, with the store left half migrated.
    """
    root = build_legacy_state(tmp_path / "state")
    conn = sqlite3.connect(root / "agent_runs.db")
    try:
        conn.execute("BEGIN IMMEDIATE")
        context = migrations.MigrationContext(
            root=root,
            spec=migrations.spec_for("agent_runs_db"),
            path=root / "agent_runs.db",
            connection=conn,
        )
        migrations._add_agent_run_effects(context)
        assert conn.in_transaction, "the fence step ended its own transaction"
        conn.rollback()
        surviving = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE name LIKE 'agent_run_effects%'"
            )
        }
    finally:
        conn.close()
    assert surviving == set(), "a rolled back fence step left objects behind"

    # Not vacuous: executescript really does end the transaction.
    other = sqlite3.connect(root / "agent_runs.db")
    try:
        other.execute("BEGIN IMMEDIATE")
        other.executescript("CREATE TABLE probe_that_should_roll_back (x)")
        assert not other.in_transaction
        other.rollback()
        assert other.execute(
            "SELECT count(*) FROM sqlite_master WHERE name = 'probe_that_should_roll_back'"
        ).fetchone()[0] == 1, "executescript committed, as documented"
        other.execute("DROP TABLE probe_that_should_roll_back")
        other.commit()
    finally:
        other.close()


def test_rerunning_the_fence_migration_changes_nothing(tmp_path):
    root = build_legacy_state(tmp_path / "state")
    migrations.apply_migrations(root)
    settled = _dump_database(root / "agent_runs.db")

    second = migrations.apply_migrations(root)

    assert second.applied == ()
    assert _plan_for(migrations.plan_migrations(root), "agent_runs_db").status is (
        StoreStatus.CURRENT
    )
    assert _dump_database(root / "agent_runs.db") == settled


def test_the_repository_opens_a_migrated_store_and_uses_its_fence(tmp_path):
    """The point of the migration: the fence works on a store that predates it."""
    from coworker.agent_run_approval import EffectStatus
    from coworker.agent_run_repository import AgentRunRepository

    root = build_legacy_state(tmp_path / "state")
    migrations.apply_migrations(root)

    repo = AgentRunRepository(root)
    try:
        owner = repo.registry.register()
        started = repo.create_run(
            session_id="main", trigger="chat", goal="reach out", owner=owner
        )
        dispatch = repo.dispatch_effect(
            started.lease, tool_name="gmail_send", arguments={"to": "d@t.test"}
        )
        quarantined = repo.quarantine_effect(
            dispatch.lease, dispatch.effect["effect_id"], reason="cut"
        )
        assert quarantined.effect["status"] == EffectStatus.AMBIGUOUS
        # The runs that predate the fence are still there and still readable.
        assert {run["run_id"] for run in repo.list_runs()} >= {
            "run-legacy-1",
            "run-legacy-2",
        }
    finally:
        repo.close()


def test_starting_the_application_never_upgrades_an_existing_run_store(tmp_path):
    """Opening a store is not migrating it. Only the registry moves a version."""
    from coworker.agent_run_repository import AgentRunRepository

    root = build_legacy_state(tmp_path / "state")
    assert _user_version(root / "agent_runs.db") == 1

    AgentRunRepository(root).close()
    AgentRunRepository(root).close()

    assert _user_version(root / "agent_runs.db") == 1
    assert _plan_for(migrations.plan_migrations(root), "agent_runs_db").status is (
        StoreStatus.PENDING
    )
    # And a brand new database is born current, so it needs no migration at all.
    fresh = tmp_path / "fresh"
    AgentRunRepository(fresh).close()
    assert _user_version(fresh / "agent_runs.db") == 2
