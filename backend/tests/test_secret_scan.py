"""S0: a non-printing scan for a revoked secret's absence.

Every leak test here plants a value, asserts the scan actually reported a
match for the store that carries it, and only then asserts the value is gone
from every output path. A test that passes because nothing was found proves
nothing and would keep passing after the redaction broke.

No credential-shaped literal is written in this file. The one real-world
fixture below (`build_current_state(plant_secrets=True)`) is imported, not
authored here; every value this file plants itself is assembled at runtime
from a random suffix, never a literal that looks like an issued credential.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import pytest

from coworker import migrations
from coworker.secret_scan import (
    MAX_DETAIL_ROWS,
    NoRegisteredSecret,
    main,
    scan_state,
)
from tests.state_fixtures import PLANTED_API_KEY, build_current_state

VAULT_STORE_IDS = {"secrets", "mcp_config", "dotenv"}


def _probe_value() -> str:
    """A distinctive value with no credential-shaped prefix, built at runtime."""
    return "probe-" + uuid.uuid4().hex


def _write_secrets(root: Path, payload: dict) -> None:
    (root / "secrets.json").write_text(json.dumps(payload), encoding="utf-8")


def _write_backup(root: Path, backup_id: str, entries: list[dict]) -> Path:
    """A hand-built backup directory, shaped like `migrations.create_backup` writes one.

    Built by hand rather than via `create_backup` so a test can plant an entry
    the real writer would never produce -- see the adversarial-manifest test
    below, which claims a secret-bearing store was copied when the real writer
    never does that.
    """
    backup_dir = root / migrations.BACKUPS_DIR_NAME / backup_id
    backup_dir.mkdir(parents=True)
    manifest = {
        "version": 1,
        "backup_id": backup_id,
        "created_at": "2026-01-01T00:00:00+00:00",
        "reason": "test",
        "entries": entries,
    }
    (backup_dir / migrations.BACKUP_MANIFEST_NAME).write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    return backup_dir


# --- finds a real leak, never prints it -------------------------------------


def test_finds_a_planted_registered_secret_in_several_stores_without_printing_it(tmp_path):
    root = build_current_state(tmp_path / "state", plant_secrets=True)

    report = scan_state(root, secret_key="apollo")

    # Non-vacuous: prove real stores actually matched before checking output.
    found_ids = {item.store_id for item in report.findings}
    expected = {"conversation_db", "memory_notes", "workspace_grants", "host_command_approvals"}
    assert expected <= found_ids, found_ids
    assert not report.clean

    rendered = report.render()
    payload = json.dumps(report.to_dict())
    assert PLANTED_API_KEY not in rendered
    assert PLANTED_API_KEY not in payload
    assert PLANTED_API_KEY not in repr(report)


def test_vault_stores_are_never_scanned_even_though_they_hold_the_value(tmp_path):
    root = build_current_state(tmp_path / "state", plant_secrets=True)

    report = scan_state(root, secret_key="apollo")

    assert report.findings  # the value is genuinely present elsewhere
    assert not (VAULT_STORE_IDS & {item.store_id for item in report.findings})
    assert not (VAULT_STORE_IDS & set(report.stores_scanned))


# --- a clean scan is a real scan, not an empty one --------------------------


def test_clean_state_reports_no_match_when_the_value_is_registered_but_not_leaked(tmp_path):
    root = build_current_state(tmp_path / "state", plant_secrets=False)
    probe = _probe_value()
    payload = json.loads((root / "secrets.json").read_text(encoding="utf-8"))
    payload["probe"] = {"api_key": probe}
    _write_secrets(root, payload)

    report = scan_state(root, secret_key="probe")

    assert report.needle_count == 1
    assert report.stores_scanned
    assert report.clean
    assert probe not in report.render()


def test_an_unknown_secret_key_refuses_rather_than_reporting_clean(tmp_path):
    root = build_current_state(tmp_path / "state", plant_secrets=False)

    with pytest.raises(NoRegisteredSecret):
        scan_state(root, secret_key="does-not-exist")


def test_an_empty_vault_refuses_rather_than_reporting_clean(tmp_path):
    root = tmp_path / "state"
    root.mkdir()

    with pytest.raises(NoRegisteredSecret):
        scan_state(root)


def test_after_rotation_the_revoked_value_is_still_found_in_conversations_and_events(tmp_path):
    """Issue #38 asks whether the old value is gone, not whether the replacement is.

    After Apollo rotation the live vault holds a different value. A scan that
    reads secrets.json would search the replacement and report clean even while
    conversations and events still carry the revoked value.
    """
    root = tmp_path / "state"
    root.mkdir()
    revoked = _probe_value()
    replacement = _probe_value()
    _write_secrets(root, {"apollo": {"api_key": replacement}})
    snapshot = tmp_path / "pre-rotation-secrets.json"
    snapshot.write_text(json.dumps({"apollo": {"api_key": revoked}}), encoding="utf-8")

    conversations = root / "conversations"
    conversations.mkdir()
    (conversations / "main.jsonl").write_text(
        json.dumps({"role": "user", "content": f"using {revoked}"}) + "\n",
        encoding="utf-8",
    )
    events = root / "events"
    events.mkdir()
    (events / "main.jsonl").write_text(
        json.dumps({"type": "error", "message": f"apollo rejected {revoked}"}) + "\n",
        encoding="utf-8",
    )

    report = scan_state(root, secret_key="apollo", revoked_from=snapshot)

    found_ids = {item.store_id for item in report.findings}
    assert found_ids >= {"conversation_transcripts", "presentation_events"}, found_ids
    assert not report.clean

    rendered = report.render()
    payload = json.dumps(report.to_dict())
    assert revoked not in rendered
    assert replacement not in rendered
    assert revoked not in payload
    assert replacement not in payload


# --- an incomplete scan is not a clean one ----------------------------------


def test_a_truncated_store_that_still_holds_the_value_is_not_reported_clean(
    tmp_path, capsys
):
    """A store that cannot be read is not a proof the value is absent.

    Public seam: scan_state / ScanReport.clean / ScanReport.render / main.
    A garbage club.db can still contain the planted bytes. The structured
    SQLite walk reports it unreadable. That must not print
    'clean -- no match found' or exit 0 -- those feed the remediation receipt.
    """
    root = tmp_path / "state"
    root.mkdir()
    probe = _probe_value()
    _write_secrets(root, {"probe": {"api_key": probe}})
    (root / "club.db").write_bytes(
        b"not-a-database " + probe.encode() + b" trailing-garbage"
    )

    report = scan_state(root, secret_key="probe")

    assert report.unreadable == ("conversation_db",)
    assert not report.clean
    rendered = report.render()
    assert "clean -- no match found" not in rendered
    assert probe not in rendered

    code = main(["--state", str(root), "--secret-key", "probe"])
    captured = capsys.readouterr()
    assert code == 2
    assert "clean -- no match found" not in captured.out
    assert probe not in captured.out


@pytest.mark.parametrize(
    "dirname,store_id",
    [
        ("conversations", "conversation_transcripts"),
        ("events", "presentation_events"),
    ],
)
def test_an_unlistable_jsonl_directory_is_not_reported_clean(
    tmp_path, capsys, dirname, store_id
):
    """Path.glob on an unlistable directory returns [] without raising.

    Public seam: scan_state / ScanReport.unreadable / ScanReport.clean / main.
    A conversations/ or events/ directory that cannot be listed still holds
    the planted file. That must land in unreadable, must not print
    'clean -- no match found', and must not exit 0.
    """
    root = tmp_path / "state"
    root.mkdir()
    probe = _probe_value()
    _write_secrets(root, {"probe": {"api_key": probe}})
    folder = root / dirname
    folder.mkdir()
    (folder / "main.jsonl").write_text(
        json.dumps({"role": "user", "content": f"using {probe}"}) + "\n",
        encoding="utf-8",
    )
    os.chmod(folder, 0o000)
    try:
        report = scan_state(root, secret_key="probe")
        code = main(["--state", str(root), "--secret-key", "probe"])
        captured = capsys.readouterr()
    finally:
        os.chmod(folder, 0o700)

    assert store_id in report.unreadable
    assert not report.clean
    rendered = report.render()
    assert "clean -- no match found" not in rendered
    assert probe not in rendered
    assert code == 2
    assert probe not in captured.out


# --- bounded output ----------------------------------------------------------


def test_a_store_with_many_matches_is_capped_not_printed_in_full(tmp_path):
    root = tmp_path / "state"
    root.mkdir()
    probe = _probe_value()
    _write_secrets(root, {"probe": {"api_key": probe}})
    conversations = root / "conversations"
    conversations.mkdir()
    lines = [
        json.dumps({"role": "user", "content": f"leaked again: {probe} (#{i})"})
        for i in range(25)
    ]
    (conversations / "big.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

    report = scan_state(root, secret_key="probe")

    finding = next(item for item in report.findings if item.store_id == "conversation_transcripts")
    assert finding.count == 25
    assert len(finding.detail) == MAX_DETAIL_ROWS
    assert finding.detail[-1].startswith("and ") and finding.detail[-1].endswith("more")

    rendered = report.render()
    payload = json.dumps(report.to_dict())
    assert probe not in rendered
    assert probe not in payload
    # The overflow line, not 25 individual detail lines, is what stands in.
    assert rendered.count(" line ") == MAX_DETAIL_ROWS - 1


# --- backups: a pre-remediation copy the live store no longer carries -------


def test_a_value_surviving_only_in_a_backup_is_found_and_never_printed(tmp_path):
    root = tmp_path / "state"
    root.mkdir()
    probe = _probe_value()
    _write_secrets(root, {"probe": {"api_key": probe}})
    # The live store is clean -- the value only survives in a backup taken
    # before remediation, which is exactly the scenario a live-store-only
    # scan would miss.
    backup_dir = _write_backup(
        root,
        "doctor-20260101T000000000000Z",
        [
            {
                "store_id": "workspace_grants",
                "kind": "json_document",
                "relative_path": "workspace_grants.json",
                "content_backed_up": True,
                "mode": "0o600",
            }
        ],
    )
    (backup_dir / "workspace_grants.json").write_text(
        json.dumps({"grants": [{"id": "g1", "label": f"pre-rotation note {probe}"}]}),
        encoding="utf-8",
    )

    report = scan_state(root, secret_key="probe")

    # Non-vacuous: the backup finding actually exists before checking output.
    label = "backup:doctor-20260101T000000000000Z:workspace_grants"
    finding = next(item for item in report.findings if item.store_id == label)
    assert finding.count == 1
    assert "doctor-20260101T000000000000Z" in report.backups_scanned
    assert not report.clean

    rendered = report.render()
    payload = json.dumps(report.to_dict())
    assert probe not in rendered
    assert probe not in payload


def test_a_secret_bearing_entry_inside_a_backup_stays_excluded_even_if_the_manifest_lies(tmp_path):
    root = tmp_path / "state"
    root.mkdir()
    probe = _probe_value()
    _write_secrets(root, {"probe": {"api_key": probe}})
    # The real backup writer never copies a secret-bearing store and always
    # records content_backed_up: false for it. This plants the file anyway and
    # claims otherwise, to prove the exclusion does not take the manifest's word.
    backup_dir = _write_backup(
        root,
        "doctor-adversarial",
        [
            {
                "store_id": "secrets",
                "kind": "opaque_file",
                "relative_path": "secrets.json",
                "content_backed_up": True,
                "mode": "0o600",
            }
        ],
    )
    (backup_dir / "secrets.json").write_text(
        json.dumps({"probe": {"api_key": probe}}), encoding="utf-8"
    )

    report = scan_state(root, secret_key="probe")

    assert "doctor-adversarial" in report.backups_scanned  # the backup was inspected
    assert report.clean
    assert not any(item.store_id.startswith("backup:") for item in report.findings)


# --- the CLI -----------------------------------------------------------------


def test_an_unreadable_store_is_not_reported_clean(tmp_path, capsys):
    """Doctor treats unreadable as no answer, not as absence.

    A clean receipt feeds a remediation receipt. If a live store cannot be
    read, the report may list it under unreadable, but it must not print
    'clean -- no match found' or exit 0.
    """
    root = tmp_path / "state"
    root.mkdir()
    probe = _probe_value()
    _write_secrets(root, {"probe": {"api_key": probe}})
    events = root / "events"
    events.mkdir()
    planted = events / "main.jsonl"
    planted.write_text(json.dumps({"type": "turn_end", "text": "idle"}) + "\n", encoding="utf-8")
    planted.chmod(0o000)
    try:
        report = scan_state(root, secret_key="probe")
        code = main(["--state", str(root), "--secret-key", "probe"])
    finally:
        planted.chmod(0o600)

    assert report.unreadable == ("presentation_events",)
    assert report.findings == ()
    assert report.clean is False
    rendered = report.render()
    assert "clean -- no match found" not in rendered
    assert "MATCH FOUND" not in rendered
    assert probe not in rendered

    captured = capsys.readouterr()
    assert code == 2
    assert "clean -- no match found" not in captured.out
    assert probe not in captured.out


def test_an_unreadable_backup_copy_is_not_reported_clean(tmp_path, capsys):
    root = tmp_path / "state"
    root.mkdir()
    probe = _probe_value()
    _write_secrets(root, {"probe": {"api_key": probe}})
    backup_id = "doctor-20260101T090000000000Z"
    backup_dir = _write_backup(
        root,
        backup_id,
        [
            {
                "store_id": "workspace_grants",
                "kind": "json_document",
                "relative_path": "workspace_grants.json",
                "content_backed_up": True,
                "mode": "0o600",
            }
        ],
    )
    planted = backup_dir / "workspace_grants.json"
    planted.write_text(json.dumps({"grants": []}), encoding="utf-8")
    planted.chmod(0o000)
    try:
        report = scan_state(root, secret_key="probe")
        code = main(["--state", str(root), "--secret-key", "probe"])
    finally:
        planted.chmod(0o600)

    label = f"backup:{backup_id}:workspace_grants"
    assert label in report.unreadable
    assert report.clean is False
    assert "clean -- no match found" not in report.render()
    captured = capsys.readouterr()
    assert code == 2
    assert probe not in captured.out


def test_cli_exits_nonzero_and_prints_nothing_sensitive_when_a_match_is_found(tmp_path, capsys):
    root = tmp_path / "state"
    root.mkdir()
    probe = _probe_value()
    _write_secrets(root, {"probe": {"api_key": probe}})
    memory = root / "memory"
    memory.mkdir()
    (memory / "1.md").write_text(f"the value is {probe}\n", encoding="utf-8")

    code = main(["--state", str(root), "--secret-key", "probe", "--json"])
    captured = capsys.readouterr()
    body = json.loads(captured.out)

    assert code == 1
    assert not body["clean"]
    assert any(item["store_id"] == "memory_notes" for item in body["findings"])
    assert probe not in captured.out
    assert probe not in captured.err


def test_cli_exits_zero_on_a_genuinely_clean_state(tmp_path, capsys):
    root = tmp_path / "state"
    root.mkdir()
    probe = _probe_value()
    _write_secrets(root, {"probe": {"api_key": probe}})

    code = main(["--state", str(root), "--secret-key", "probe"])
    captured = capsys.readouterr()

    assert code == 0
    assert "clean" in captured.out
    assert probe not in captured.out


def test_cli_searches_the_revoked_snapshot_not_the_live_replacement(tmp_path, capsys):
    root = tmp_path / "state"
    root.mkdir()
    revoked = _probe_value()
    replacement = _probe_value()
    _write_secrets(root, {"apollo": {"api_key": replacement}})
    snapshot = tmp_path / "pre-rotation-secrets.json"
    snapshot.write_text(json.dumps({"apollo": {"api_key": revoked}}), encoding="utf-8")
    conversations = root / "conversations"
    conversations.mkdir()
    (conversations / "main.jsonl").write_text(
        json.dumps({"role": "user", "content": f"using {revoked}"}) + "\n",
        encoding="utf-8",
    )

    code = main(
        [
            "--state",
            str(root),
            "--secret-key",
            "apollo",
            "--revoked-from",
            str(snapshot),
            "--json",
        ]
    )
    captured = capsys.readouterr()
    body = json.loads(captured.out)

    assert code == 1
    assert not body["clean"]
    assert any(item["store_id"] == "conversation_transcripts" for item in body["findings"])
    assert revoked not in captured.out
    assert replacement not in captured.out
    assert revoked not in captured.err
    assert replacement not in captured.err


def test_cli_exits_with_a_usage_code_for_an_unregistered_key(tmp_path, capsys):
    root = tmp_path / "state"
    root.mkdir()
    _write_secrets(root, {"apollo": {"api_key": _probe_value()}})

    code = main(["--state", str(root), "--secret-key", "not-registered"])
    captured = capsys.readouterr()

    assert code == 2
    assert captured.out == ""
