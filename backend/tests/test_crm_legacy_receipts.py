"""Scoped Undo remains available for records created by the older attention worker."""

import json
from coworker.people import PersonStore
from coworker.crm_service import CrmService
from coworker.crm_tasks import TaskService, _fingerprint
from coworker.migrations import apply_migrations


def setup(tmp_path):
    p = PersonStore(tmp_path)
    pid = p.keep_from_apollo(
        apollo_id="maya",
        first_name="Maya",
        last_name_obfuscated=None,
        title="Lead",
        company="Lumen",
    )["person_id"]
    return p, pid, CrmService(p)


def test_legacy_automatic_creation_keeps_its_safe_undo_binding(tmp_path):
    p, pid, c = setup(tmp_path)
    result = TaskService(p).create(
        pid,
        title="Send examples",
        timezone="UTC",
        actor="assistant",
        operation_id="inference_legacy",
    )
    p._conn.execute(
        "INSERT INTO crm_interpretations VALUES(?,?,?,0)",
        (pid, "legacy-fingerprint", result["change"]["change_id"]),
    )
    p._conn.execute("PRAGMA user_version=7")
    p._conn.commit()
    p._conn.close()
    assert apply_migrations(tmp_path).error is None
    p = PersonStore(tmp_path)
    c = CrmService(p)
    assert next(
        row
        for row in c.recent_changes(pid)
        if row["change_id"] == result["change"]["change_id"]
    )["can_undo"]
    c.undo(pid, result["change"]["change_id"], operation_id="undo-legacy-auto")
    assert TaskService(p).get(pid, result["task"]["task_id"])["state"] == "cancelled"


def test_legacy_accepted_proposal_receipt_keeps_its_target_undo(tmp_path):
    p, pid, c = setup(tmp_path)
    prop = c.propose(
        pid,
        kind="task_create",
        value={"title": "Send examples"},
        expected_version=0,
        sources=[],
        fingerprint="request",
        reason="Review",
    )
    accepted = c.resolve(
        pid,
        prop["proposal_id"],
        decision="accepted",
        operation_id="accept-legacy-proposal",
        timezone="UTC",
    )
    change = p._conn.execute(
        "SELECT after_json FROM crm_changes WHERE change_id=?",
        (accepted["change"]["change_id"],),
    ).fetchone()
    after = json.loads(change[0])
    after.pop("change_id")
    p._conn.execute(
        "UPDATE crm_changes SET after_json=? WHERE change_id=?",
        (json.dumps(after), accepted["change"]["change_id"]),
    )
    p._conn.execute("DROP TABLE crm_proposals")
    from coworker.crm_schema import CRM_SCHEMA

    for sql in CRM_SCHEMA:
        p._conn.execute(sql)
    p._conn.execute(
        "INSERT INTO crm_proposals VALUES(?,?,'task_create',?,0,'Review','[]','legacy-request','accepted','2026-10-22','2026-10-22')",
        (prop["proposal_id"], pid, json.dumps({"title": "Send examples"})),
    )
    p._conn.execute("PRAGMA user_version=7")
    p._conn.commit()
    p._conn.close()
    assert apply_migrations(tmp_path).error is None
    p = PersonStore(tmp_path)
    c = CrmService(p)
    assert next(
        row
        for row in c.recent_changes(pid)
        if row["change_id"] == accepted["change"]["change_id"]
    )["can_undo"]
    c.undo(pid, accepted["change"]["change_id"], operation_id="undo-legacy-accepted")
    assert TaskService(p).get(pid, accepted["task"]["task_id"])["state"] == "cancelled"
