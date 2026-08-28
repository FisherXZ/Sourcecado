"""S5: Sourcecado Doctor — diagnostic by default, bounded, redacted, backed up."""

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from coworker import doctor, migrations
from coworker.doctor import Repair, Severity
from tests.state_fixtures import (
    PLANTED_API_KEY,
    PLANTED_CANARIES,
    build_current_state,
    build_legacy_state,
)

DESKTOP = Path(__file__).resolve().parents[1]


def _tree_digest(root: Path) -> dict[str, tuple[str, str]]:
    return {
        str(path.relative_to(root)): (
            hashlib.sha256(path.read_bytes()).hexdigest(),
            oct(path.stat().st_mode & 0o777),
        )
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _outside_backups(digest: dict) -> dict:
    return {
        name: value
        for name, value in digest.items()
        if not name.startswith(migrations.BACKUPS_DIR_NAME)
    }


def _checks(report) -> set[str]:
    return {finding.check for finding in report.findings}


def _finding(report, check):
    return next(item for item in report.findings if item.check == check)


def _connect(root: Path, name: str) -> sqlite3.Connection:
    conn = sqlite3.connect(root / name)
    conn.row_factory = sqlite3.Row
    return conn


# --- Doctor starts nothing ----------------------------------------------


def test_doctor_never_imports_the_model_or_the_connectors(tmp_path):
    root = build_current_state(tmp_path / "state")
    script = (
        "import sys\n"
        "from coworker import doctor\n"
        f"doctor.diagnose({str(root)!r})\n"
        "forbidden = [name for name in sys.modules if name in {\n"
        "    'coworker.provider', 'coworker.server', 'coworker.turn',\n"
        "    'coworker.gmail', 'coworker.apollo', 'coworker.calendar',\n"
        "    'coworker.drive', 'coworker.web', 'coworker.automation.scheduler',\n"
        "    'httpx', 'uvicorn', 'fastapi',\n"
        "}]\n"
        "print(','.join(sorted(forbidden)))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=DESKTOP,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == ""


def test_doctor_runs_with_networking_disabled(tmp_path, monkeypatch):
    import socket

    root = build_current_state(tmp_path / "state")

    def refuse(*args, **kwargs):
        raise AssertionError("Doctor must not open a socket")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)

    report = doctor.diagnose(root)
    assert report.stores


# --- healthy state -------------------------------------------------------


def test_a_healthy_current_state_reports_no_problems(tmp_path):
    root = build_current_state(tmp_path / "state")
    migrations.apply_migrations(root)

    report = doctor.diagnose(root)

    assert report.blocked is False
    assert report.healthy is True
    assert report.proposed_repairs == ()
    assert [item for item in report.findings if item.severity is not Severity.INFO] == []


def test_doctor_reports_a_version_for_every_durable_store(tmp_path):
    root = build_current_state(tmp_path / "state")
    migrations.apply_migrations(root)

    report = doctor.diagnose(root)
    versioned = {
        item.store_id: item
        for item in report.stores
        if item.version_channel != migrations.VersionChannel.NONE.value
    }
    assert versioned
    for store_id, item in versioned.items():
        if not item.present:
            continue
        assert item.version == item.target_version, store_id
        assert item.status == migrations.StoreStatus.CURRENT.value


# --- integrity checks ----------------------------------------------------


def test_an_old_schema_version_is_reported_with_its_proposed_upgrade(tmp_path):
    root = build_legacy_state(tmp_path / "state")

    report = doctor.diagnose(root)

    finding = _finding(report, "store.version_behind")
    assert finding.repair is Repair.AUTOMATIC
    upgrades = [item for item in report.proposed_repairs if item.action == "migration.apply"]
    assert {item.store_id for item in upgrades} >= {"conversation_db", "people_db"}
    assert any(item.record_count > 0 for item in upgrades)


def test_a_torn_trailing_jsonl_line_is_detected_and_safely_repairable(tmp_path):
    root = build_current_state(tmp_path / "state")
    migrations.apply_migrations(root)
    path = root / "conversations" / "main.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"role": "assistant", "content": "half a mes')

    report = doctor.diagnose(root)
    finding = _finding(report, "jsonl.torn_tail")
    assert finding.repair is Repair.AUTOMATIC
    assert finding.record_count == 1
    assert any(item.action == "jsonl.truncate_torn_tail" for item in report.proposed_repairs)

    after = doctor.repair(root)
    assert "jsonl.truncate_torn_tail" in after.applied_repairs
    assert path.read_text(encoding="utf-8").endswith('"Searching Apollo for Rippling recruiters."}\n')
    assert "jsonl.torn_tail" not in _checks(doctor.diagnose(root))


def test_a_torn_line_in_the_middle_of_a_jsonl_file_is_review_required(tmp_path):
    root = build_current_state(tmp_path / "state")
    migrations.apply_migrations(root)
    path = root / "conversations" / "main.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text(
        "\n".join([lines[0], '{"role": "assist', *lines[1:]]) + "\n", encoding="utf-8"
    )
    before = _tree_digest(root)

    report = doctor.diagnose(root)
    finding = _finding(report, "jsonl.torn_line")
    assert finding.repair is Repair.REVIEW_REQUIRED
    assert finding.severity is Severity.ERROR

    doctor.repair(root)
    assert _outside_backups(_tree_digest(root)) == _outside_backups(before)


def test_a_corrupt_json_column_is_detected_and_is_review_required(tmp_path):
    root = build_current_state(tmp_path / "state")
    migrations.apply_migrations(root)
    conn = _connect(root, "club.db")
    conn.execute("UPDATE inbox SET arguments = 'not json at all'")
    conn.commit()
    conn.close()

    finding = _finding(doctor.diagnose(root), "sqlite.corrupt_row")
    assert finding.store_id == "conversation_db"
    assert finding.repair is Repair.REVIEW_REQUIRED
    assert finding.record_count == 1
    assert any("inbox.arguments" in line for line in finding.detail)


def test_a_corrupt_person_event_payload_is_detected(tmp_path):
    root = build_current_state(tmp_path / "state")
    migrations.apply_migrations(root)
    conn = _connect(root, "people.db")
    conn.execute("UPDATE events SET payload = '{broken'")
    conn.commit()
    conn.close()

    finding = _finding(doctor.diagnose(root), "sqlite.corrupt_row")
    assert finding.store_id == "people_db"
    assert finding.repair is Repair.REVIEW_REQUIRED


def test_a_malformed_database_is_reported_and_never_repaired(tmp_path):
    root = build_current_state(tmp_path / "state")
    migrations.apply_migrations(root)
    path = root / "people.db"
    raw = bytearray(path.read_bytes())
    raw[4096 : 4096 + 512] = b"\x00" * 512
    path.write_bytes(bytes(raw))
    before = _tree_digest(root)

    report = doctor.diagnose(root)
    assert {"sqlite.integrity", "store.unreadable"} & _checks(report)
    assert all(
        item.repair is not Repair.AUTOMATIC
        for item in report.findings
        if item.store_id == "people_db"
    )
    assert report.blocked is True

    doctor.repair(root)
    assert _outside_backups(_tree_digest(root)) == _outside_backups(before)


def test_a_file_that_is_not_a_database_blocks_every_repair(tmp_path):
    root = build_current_state(tmp_path / "state")
    (root / "club.db").write_bytes(b"this is not a sqlite file")
    before = _tree_digest(root)

    report = doctor.diagnose(root)
    assert "store.unreadable" in _checks(report)
    assert report.blocked is True
    assert report.proposed_repairs == ()

    outcome = doctor.repair(root)
    assert outcome.applied_repairs == ()
    assert _outside_backups(_tree_digest(root)) == _outside_backups(before)


def test_an_unreadable_json_document_is_reported(tmp_path):
    root = build_current_state(tmp_path / "state")
    (root / "shell_tasks.json").write_text("{{{", encoding="utf-8")

    report = doctor.diagnose(root)
    assert "store.unreadable" in _checks(report)
    assert report.blocked is True


def test_an_event_record_at_an_unsupported_version_is_reported(tmp_path):
    root = build_current_state(tmp_path / "state")
    migrations.apply_migrations(root)
    path = root / "events" / "main.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"version": 1, "type": "turn_start"}) + "\n")

    finding = _finding(doctor.diagnose(root), "jsonl.record_version")
    assert finding.store_id == "presentation_events"
    assert finding.repair is Repair.REVIEW_REQUIRED
    assert finding.record_count == 1


# --- unknown future version ---------------------------------------------


def test_an_unknown_future_version_fails_closed_without_modifying_state(tmp_path):
    root = build_current_state(tmp_path / "state")
    migrations.apply_migrations(root)
    conn = sqlite3.connect(root / "club.db")
    conn.execute("PRAGMA user_version = 99")
    conn.commit()
    conn.close()
    before = _tree_digest(root)

    report = doctor.diagnose(root)
    finding = _finding(report, "store.version_ahead")
    assert finding.severity is Severity.ERROR
    assert finding.repair is Repair.NONE
    assert report.blocked is True
    assert report.proposed_repairs == ()

    outcome = doctor.repair(root)
    assert outcome.applied_repairs == ()
    assert outcome.backup_id is None
    assert _tree_digest(root) == before


# --- permissions, dependencies -------------------------------------------


def test_unsafe_state_permissions_are_detected_and_repaired(tmp_path):
    root = build_current_state(tmp_path / "state")
    migrations.apply_migrations(root)
    os.chmod(root / "club.db", 0o644)
    os.chmod(root / "secrets.json", 0o666)
    os.chmod(root / "conversations", 0o755)

    report = doctor.diagnose(root)
    finding = _finding(report, "permissions.drift")
    assert finding.repair is Repair.AUTOMATIC
    assert finding.record_count == 3
    assert any(item.action == "permissions.tighten" for item in report.proposed_repairs)

    after = doctor.repair(root)
    assert "permissions.tighten" in after.applied_repairs
    assert oct((root / "club.db").stat().st_mode & 0o777) == "0o600"
    assert oct((root / "secrets.json").stat().st_mode & 0o777) == "0o600"
    assert oct((root / "conversations").stat().st_mode & 0o777) == "0o700"
    assert "permissions.drift" not in _checks(doctor.diagnose(root))


def test_a_missing_runtime_dependency_is_detected(tmp_path, monkeypatch):
    root = build_current_state(tmp_path / "state")
    real = doctor.find_spec

    def missing(name, *args, **kwargs):
        return None if name == "fastapi" else real(name, *args, **kwargs)

    monkeypatch.setattr(doctor, "find_spec", missing)

    report = doctor.diagnose(root)
    finding = _finding(report, "dependencies.missing")
    assert finding.severity is Severity.ERROR
    assert finding.repair is Repair.REVIEW_REQUIRED
    assert any("fastapi" in line for line in finding.detail)


def test_present_dependencies_are_reported_without_a_finding(tmp_path):
    root = build_current_state(tmp_path / "state")
    report = doctor.diagnose(root)

    names = {item.name for item in report.dependencies}
    assert {"fastapi", "uvicorn", "httpx"} <= names
    assert all(item.present for item in report.dependencies if item.required)
    assert "dependencies.missing" not in _checks(report)


# --- schedule and orphaned links ----------------------------------------


def test_schedule_inconsistencies_are_detected(tmp_path):
    root = build_current_state(tmp_path / "state")
    migrations.apply_migrations(root)
    conn = _connect(root, "club.db")
    conn.execute(
        "INSERT INTO runs (job_id, status, result, session_id, artifacts) "
        "VALUES (404, 'success', 'orphan', 'sched-404', '[]')"
    )
    conn.execute("UPDATE jobs SET next_run_at = 'not-a-timestamp' WHERE id = 1")
    conn.execute(
        "INSERT INTO runs (job_id, status, result, session_id, artifacts) "
        "VALUES (1, 'banana', '', 'sched-1', '[]')"
    )
    conn.commit()
    conn.close()

    report = doctor.diagnose(root)
    assert _finding(report, "schedule.orphaned_run").record_count == 1
    assert _finding(report, "schedule.unparsable_next_run").record_count == 1
    assert _finding(report, "schedule.unknown_run_status").record_count == 1
    for check in (
        "schedule.orphaned_run",
        "schedule.unparsable_next_run",
        "schedule.unknown_run_status",
    ):
        assert _finding(report, check).repair is Repair.REVIEW_REQUIRED


def test_orphaned_queue_and_approval_links_are_detected(tmp_path):
    root = build_current_state(tmp_path / "state")
    migrations.apply_migrations(root)
    conn = _connect(root, "club.db")
    conn.execute(
        "INSERT INTO chat_queue "
        "(session_id, id, text, position, state, created_at, updated_at) "
        "VALUES ('ghost', 'q1', 'stranded', 0, 'waiting', '2026-08-01', '2026-08-01')"
    )
    conn.execute(
        "INSERT INTO inbox (id, kind, name, arguments, state, session_id) "
        "VALUES ('call_ghost', 'approval', 'gmail_send', '{}', 'pending', 'ghost')"
    )
    conn.execute(
        "INSERT INTO inbox (id, kind, name, arguments, state, original_call_id) "
        "VALUES ('call_chained', 'approval', 'gmail_send', '{}', 'pending', 'call_missing')"
    )
    conn.commit()
    conn.close()
    people = _connect(root, "people.db")
    people.execute(
        "INSERT INTO session_people (session_id, person_id) VALUES ('ghost', 'per_x')"
    )
    people.execute(
        "INSERT INTO events (event_id, person_id, source, kind, summary, actor) "
        "VALUES ('evt_orphan', 'per_missing', 'gmail', 'mail', 'stranded', 'assistant')"
    )
    people.commit()
    people.close()

    report = doctor.diagnose(root)
    assert _finding(report, "queue.orphaned_session").record_count == 1
    assert _finding(report, "approval.orphaned_session").record_count == 1
    assert _finding(report, "approval.orphaned_chain").record_count == 1
    assert _finding(report, "person.orphaned_session_binding").record_count == 1
    assert _finding(report, "person.orphaned_event").record_count == 1
    assert all(
        item.repair is Repair.REVIEW_REQUIRED
        for item in report.findings
        if item.check.startswith(("queue.", "approval.", "person."))
    )


def test_an_interrupted_approval_is_reported_and_never_auto_repaired(tmp_path):
    from coworker.store import ConversationStore

    root = build_current_state(tmp_path / "state")
    migrations.apply_migrations(root)
    conn = _connect(root, "club.db")
    conn.execute(
        "UPDATE inbox SET state = 'resolved', decision = 'allow', "
        "execution_status = 'executing' WHERE id = 'call_gmail_send_1'"
    )
    conn.commit()
    conn.close()
    # The restart reconciler is what turns a dead claim into an interrupted one.
    ConversationStore(root)

    report = doctor.diagnose(root)
    finding = _finding(report, "approval.interrupted")
    assert finding.repair is Repair.REVIEW_REQUIRED
    assert finding.record_count == 1
    assert all(
        item.action != "approval" for item in report.proposed_repairs
    )


# --- dry run, repair, backup --------------------------------------------


def test_dry_run_is_the_default_and_changes_nothing(tmp_path):
    root = build_legacy_state(tmp_path / "state")
    os.chmod(root / "club.db", 0o644)
    with (root / "conversations" / "main.jsonl").open("a", encoding="utf-8") as handle:
        handle.write('{"role": "user", "content": "tor')
    before = _tree_digest(root)

    report = doctor.diagnose(root)

    assert report.backup_id is None
    assert report.applied_repairs == ()
    assert _tree_digest(root) == before
    actions = {item.action for item in report.proposed_repairs}
    assert actions == {"migration.apply", "permissions.tighten", "jsonl.truncate_torn_tail"}
    for item in report.proposed_repairs:
        assert item.description
        assert item.record_count >= 0
    assert next(
        item for item in report.proposed_repairs if item.action == "permissions.tighten"
    ).record_count == 1


def test_repair_creates_a_timestamped_backup_before_changing_state(tmp_path):
    root = build_legacy_state(tmp_path / "state")

    report = doctor.repair(root)

    assert report.backup_id is not None
    assert report.backup_id.startswith("doctor-")
    backup = root / migrations.BACKUPS_DIR_NAME / report.backup_id
    assert (backup / "manifest.json").is_file()
    conn = sqlite3.connect(backup / "club.db")
    try:
        assert int(conn.execute("PRAGMA user_version").fetchone()[0]) == 0
    finally:
        conn.close()


def test_only_deterministic_repairs_run_automatically(tmp_path):
    root = build_current_state(tmp_path / "state")
    migrations.apply_migrations(root)
    conn = _connect(root, "club.db")
    conn.execute("UPDATE inbox SET arguments = 'not json'")
    conn.execute(
        "INSERT INTO chat_queue "
        "(session_id, id, text, position, state, created_at, updated_at) "
        "VALUES ('ghost', 'q1', 'stranded', 0, 'waiting', '2026-08-01', '2026-08-01')"
    )
    conn.commit()
    conn.close()
    os.chmod(root / "club.db", 0o644)

    report = doctor.repair(root)

    assert report.applied_repairs == ("permissions.tighten",)
    survivors = _checks(doctor.diagnose(root))
    assert {"sqlite.corrupt_row", "queue.orphaned_session"} <= survivors
    conn = _connect(root, "club.db")
    try:
        assert conn.execute("SELECT COUNT(*) FROM chat_queue").fetchone()[0] == 2
        assert conn.execute(
            "SELECT arguments FROM inbox WHERE id = 'call_gmail_send_1'"
        ).fetchone()[0] == "not json"
    finally:
        conn.close()


def test_repair_is_idempotent(tmp_path):
    root = build_legacy_state(tmp_path / "state")
    os.chmod(root / "club.db", 0o644)

    first = doctor.repair(root)
    assert first.applied_repairs
    after_first = _outside_backups(_tree_digest(root))

    second = doctor.repair(root)
    assert second.applied_repairs == ()
    assert second.backup_id is None
    assert _outside_backups(_tree_digest(root)) == after_first


def test_state_is_usable_after_repair_and_a_restart(tmp_path):
    from coworker.people import PersonStore
    from coworker.store import ConversationStore
    from coworker.workspace import WorkspaceGrantStore
    from tests.state_fixtures import LEGACY_PERSON_ID

    root = build_legacy_state(tmp_path / "state")
    os.chmod(root / "club.db", 0o644)
    with (root / "conversations" / "main.jsonl").open("a", encoding="utf-8") as handle:
        handle.write('{"role": "user", "content": "tor')

    doctor.repair(root)

    store = ConversationStore(root)
    assert len(store.load("main")) == 2
    assert store.list_schedule()["jobs"][0]["template_id"] == "legacy"
    assert PersonStore(root).get(LEGACY_PERSON_ID)["first_name"] == "Dana"
    assert len(WorkspaceGrantStore(root).list_all()) == 1
    assert doctor.diagnose(root).healthy is True


def test_a_failed_backup_blocks_the_repair_and_leaves_state_untouched(tmp_path):
    root = build_legacy_state(tmp_path / "state")
    before = _tree_digest(root)
    blocker = root / migrations.BACKUPS_DIR_NAME
    blocker.write_text("not a directory", encoding="utf-8")

    report = doctor.repair(root)

    assert report.applied_repairs == ()
    assert _finding(report, "repair.backup_failed").severity is Severity.ERROR
    blocker.unlink()
    assert _tree_digest(root) == before


def test_backups_are_listed_for_inspection_and_restore(tmp_path):
    root = build_legacy_state(tmp_path / "state")
    report = doctor.repair(root)

    listed = doctor.backups(root)
    assert report.backup_id in {item["backup_id"] for item in listed}

    result = migrations.restore_backup(root, report.backup_id)
    assert "conversation_db" in result["restored"]
    conn = sqlite3.connect(root / "club.db")
    try:
        assert int(conn.execute("PRAGMA user_version").fetchone()[0]) == 0
    finally:
        conn.close()


# --- bounded and redacted output ----------------------------------------


def test_no_planted_secret_survives_into_doctor_output(tmp_path):
    root = build_current_state(tmp_path / "state", plant_secrets=True)
    conn = _connect(root, "club.db")
    conn.execute("UPDATE inbox SET arguments = ?", (f"authorization: Bearer {PLANTED_API_KEY}",))
    conn.commit()
    conn.close()
    with (root / "conversations" / "main.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"role": "user", "content": PLANTED_API_KEY})[:40])
    with (root / "workspace_receipts.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("{\"summary\": \"api_key=" + PLANTED_API_KEY + "\", \"tru")
    os.chmod(root / "secrets.json", 0o644)

    report = doctor.diagnose(root)
    rendered = report.render()
    serialized = json.dumps(report.to_dict())

    assert report.findings
    for canary in PLANTED_CANARIES:
        assert canary not in rendered, canary
        assert canary not in serialized, canary


def test_no_secret_survives_a_repair_backup_manifest(tmp_path):
    root = build_current_state(tmp_path / "state", plant_secrets=True)
    os.chmod(root / "club.db", 0o644)

    report = doctor.repair(root)
    manifest = (
        root / migrations.BACKUPS_DIR_NAME / report.backup_id / "manifest.json"
    ).read_text(encoding="utf-8")

    for canary in PLANTED_CANARIES:
        assert canary not in manifest
    assert not (root / migrations.BACKUPS_DIR_NAME / report.backup_id / "secrets.json").exists()


def test_output_carries_no_absolute_path(tmp_path):
    root = build_current_state(tmp_path / "state", plant_secrets=True)
    os.chmod(root / "club.db", 0o644)
    with (root / "conversations" / "main.jsonl").open("a", encoding="utf-8") as handle:
        handle.write('{"role": "user", "content": "tor')

    report = doctor.diagnose(root)
    rendered = report.render()
    serialized = json.dumps(report.to_dict())

    for text in (rendered, serialized):
        assert str(root) not in text
        assert str(tmp_path) not in text
        assert str(Path.home()) not in text
        assert "/Users/" not in text
        assert "/private/var/" not in text
    assert "<state>/conversations/main.jsonl" in rendered


def test_output_is_bounded_even_with_many_defects(tmp_path):
    root = build_current_state(tmp_path / "state")
    migrations.apply_migrations(root)
    conv = root / "conversations"
    for index in range(200):
        path = conv / f"session-{index}.jsonl"
        path.write_text('{"role": "user", "content": "ok"}\n{"role": "user", "cont', encoding="utf-8")
        os.chmod(path, 0o644)

    report = doctor.diagnose(root)
    rendered = report.render()

    torn = _finding(report, "jsonl.torn_tail")
    assert torn.record_count == 200
    assert len(torn.detail) <= doctor.MAX_DETAIL_ROWS
    assert len(report.findings) <= doctor.MAX_FINDINGS
    for finding in report.findings:
        assert len(finding.detail) <= doctor.MAX_DETAIL_ROWS
        for line in finding.detail:
            assert len(line) <= doctor.MAX_DETAIL_CHARS
    assert len(rendered) <= doctor.MAX_REPORT_CHARS
    assert "200" in rendered


# --- command line --------------------------------------------------------


def test_cli_check_is_the_default_and_reports_health_by_exit_code(tmp_path, capsys):
    root = build_current_state(tmp_path / "state")
    migrations.apply_migrations(root)

    assert doctor.main(["--state", str(root)]) == 0
    capsys.readouterr()

    os.chmod(root / "club.db", 0o644)
    before = _tree_digest(root)
    assert doctor.main(["--state", str(root)]) == 1
    assert _tree_digest(root) == before


def test_cli_blocks_with_a_distinct_exit_code(tmp_path, capsys):
    root = build_current_state(tmp_path / "state")
    (root / "club.db").write_bytes(b"not a database")

    assert doctor.main(["--state", str(root)]) == 2
    assert doctor.main(["repair", "--state", str(root)]) == 2
    capsys.readouterr()


def test_cli_repair_applies_safe_repairs_and_prints_the_backup_id(tmp_path, capsys):
    root = build_legacy_state(tmp_path / "state")

    assert doctor.main(["repair", "--state", str(root)]) == 0
    output = capsys.readouterr().out
    assert "backup" in output.lower()
    assert doctor.main(["--state", str(root)]) == 0


def test_cli_json_output_is_machine_readable(tmp_path, capsys):
    root = build_current_state(tmp_path / "state")
    doctor.main(["--state", str(root), "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert payload["stores"]
    assert "findings" in payload
    assert "proposed_repairs" in payload


def test_cli_lists_and_restores_backups(tmp_path, capsys):
    root = build_legacy_state(tmp_path / "state")
    doctor.main(["repair", "--state", str(root)])
    capsys.readouterr()

    assert doctor.main(["backups", "--state", str(root)]) == 0
    listing = capsys.readouterr().out
    backup_id = doctor.backups(root)[0]["backup_id"]
    assert backup_id in listing

    assert doctor.main(["restore", backup_id, "--state", str(root)]) == 0
    conn = sqlite3.connect(root / "club.db")
    try:
        assert int(conn.execute("PRAGMA user_version").fetchone()[0]) == 0
    finally:
        conn.close()


def test_cli_uses_the_same_state_directory_as_the_sidecar(monkeypatch, tmp_path):
    from coworker.server import state_dir

    monkeypatch.setenv("CLUB_STATE_DIR", str(tmp_path / "state"))
    assert doctor.state_root() == state_dir()
    monkeypatch.delenv("CLUB_STATE_DIR")
    assert doctor.state_root() == state_dir()
