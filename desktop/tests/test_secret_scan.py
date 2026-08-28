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
import uuid
from pathlib import Path

import pytest

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


# --- the CLI -----------------------------------------------------------------


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


def test_cli_exits_with_a_usage_code_for_an_unregistered_key(tmp_path, capsys):
    root = tmp_path / "state"
    root.mkdir()
    _write_secrets(root, {"apollo": {"api_key": _probe_value()}})

    code = main(["--state", str(root), "--secret-key", "not-registered"])
    captured = capsys.readouterr()

    assert code == 2
    assert captured.out == ""
