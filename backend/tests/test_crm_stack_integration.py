"""Upgrades and boundaries shared by the independently developed CRM branches."""

import json
import sqlite3
import pytest
from coworker import migrations
from coworker.people import PersonStore, SCHEMA_VERSION
from coworker.crm_tasks import TaskService


@pytest.mark.parametrize(
    "layout,version",
    [("v5", 5), ("tracked", 6), ("analysis", 6), ("tasks", 7), ("attention", 7)],
)
def test_all_crm_branch_stores_upgrade_to_one_schema_without_losing_work(
    tmp_path, layout, version
):
    p = PersonStore(tmp_path)
    pid = p.keep_from_apollo(
        apollo_id="maya",
        first_name="Maya",
        last_name_obfuscated=None,
        title="Lead",
        company="Lumen",
    )["person_id"]
    task = TaskService(p).create(
        pid,
        title="Preserve human promise",
        timezone="UTC",
        actor="director",
        operation_id="legacy-promise",
    )["task"]
    p._conn.execute(
        "INSERT OR REPLACE INTO person_descriptions(person_id,slot,text,authority,source_refs,version) VALUES(?,'general','','human','[]',1)",
        (pid,),
    )
    p._conn.commit()
    p._conn.close()
    c = sqlite3.connect(tmp_path / "people.db")
    if layout in {"v5", "tracked", "attention"}:
        c.execute("DROP TABLE crm_analysis_state")
        c.execute(
            "CREATE TABLE crm_analysis_state(person_id TEXT PRIMARY KEY REFERENCES people(person_id),latest_evidence_revision INTEGER NOT NULL DEFAULT 0,analyzed_revision INTEGER NOT NULL DEFAULT 0)"
        )
        c.execute("INSERT INTO crm_analysis_state VALUES(?,2,0)", (pid,))
        for table in ("crm_task_evidence", "crm_proposals", "crm_analysis_attempts"):
            c.execute("DROP TABLE IF EXISTS " + table)
    if layout == "attention":
        from coworker.crm_schema import CRM_SCHEMA, extend_columns

        for statement in CRM_SCHEMA:
            c.execute(statement)
        extend_columns(c)
        c.execute(
            "INSERT INTO crm_proposals VALUES('legacy-proposal',?,'task_create',?,0,'Review this','[]','legacy-fingerprint','pending','2026-10-22','2026-10-22')",
            (pid, json.dumps({"title": "Keep suggestion"})),
        )
    c.execute("PRAGMA user_version=" + str(version))
    c.commit()
    c.close()
    outcome = migrations.apply_migrations(tmp_path)
    assert outcome.error is None, outcome
    p = PersonStore(tmp_path)
    assert p._conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    p.require_crm_schema()
    assert TaskService(p).get(pid, task["task_id"])["title"] == "Preserve human promise"
    assert (
        p.descriptions(pid)["general"]["authority"] == "human"
        and p.descriptions(pid)["general"]["text"] == ""
    )
    assert p._conn.execute("PRAGMA foreign_key_check").fetchall() == []
    if layout == "attention":
        row = p._conn.execute(
            "SELECT * FROM crm_proposals WHERE proposal_id='legacy-proposal'"
        ).fetchone()
        assert (
            row
            and row["state"] == "pending"
            and "Keep suggestion" in row["proposed_value"]
        )


def test_newly_shared_address_during_collection_never_commits_body_or_activity(
    tmp_path,
):
    from test_crm_mail import (
        ADDRESS,
        MailRepository,
        TrackedMailReader,
        deliver,
        mailbox,
        refresh_mail,
        tracked_person,
    )

    p = PersonStore(tmp_path)
    pid = tracked_person(p)
    g = mailbox()
    deliver(g, body="Private text that must not be assigned")
    original = g.read

    def change_owner(**kwargs):
        other = p.keep_from_apollo(
            apollo_id="other",
            first_name="Other",
            last_name_obfuscated=None,
            title="Lead",
            company="Other",
        )["person_id"]
        p.apply_enrichment(other, email=ADDRESS)
        return original(**kwargs)

    g.read = change_owner
    r = refresh_mail(p, TrackedMailReader(g))
    assert r["unassigned"] == 1
    assert MailRepository(p).message("director@sourcecado.test", "incoming") is None
    assert not any(
        e["payload"].get("message_id") == "incoming"
        for e in p.timeline(pid)
        if e["kind"] == "mail"
    )


def test_legacy_sources_keep_restriction_in_task_history_and_retry_receipt(tmp_path):
    p = PersonStore(tmp_path)
    pid = p.keep_from_apollo(
        apollo_id="private",
        first_name="Maya",
        last_name_obfuscated=None,
        title=None,
        company=None,
    )["person_id"]
    saved = TaskService(p).create(
        pid,
        title="Protected source promise",
        timezone="UTC",
        actor="director",
        operation_id="source-history-retry",
    )
    ref = {
        "message_id": "private-message",
        "account": "director@example.test",
        "thread_id": "private-thread",
    }
    fields = {**ref, "provider": "Gmail", "id": "gmail:private-message"}
    for sid, restricted in [("source_public_alias", 0), ("source_restricted_alias", 1)]:
        p._conn.execute(
            "INSERT INTO person_attachments(id,person_id,record_type,idempotency_key,fields_json,restricted,created_at,updated_at) VALUES(?,?,'source_ref',?,?,?,'2026-10-22','2026-10-22')",
            (sid, pid, sid, json.dumps(fields), restricted),
        )
    p._conn.execute(
        "UPDATE person_tasks SET source_refs=? WHERE task_id=?",
        (json.dumps([ref]), saved["task"]["task_id"]),
    )
    p._conn.execute(
        "UPDATE crm_changes SET source_refs=? WHERE change_id=?",
        (json.dumps([ref]), saved["change"]["change_id"]),
    )
    saved["task"]["source_refs"] = [ref]
    p._conn.execute(
        "UPDATE crm_operations SET response_json=? WHERE operation_id=?",
        (json.dumps(saved), "source-history-retry"),
    )
    p._conn.execute("PRAGMA user_version=7")
    p._conn.commit()
    p._conn.close()
    outcome = migrations.apply_migrations(tmp_path)
    assert outcome.error is None
    reopened = PersonStore(tmp_path)
    task_refs = json.loads(
        reopened._conn.execute(
            "SELECT source_refs FROM person_tasks WHERE task_id=?",
            (saved["task"]["task_id"],),
        ).fetchone()[0]
    )
    change_refs = json.loads(
        reopened._conn.execute(
            "SELECT source_refs FROM crm_changes WHERE change_id=?",
            (saved["change"]["change_id"],),
        ).fetchone()[0]
    )
    receipt = json.loads(
        reopened._conn.execute(
            "SELECT response_json FROM crm_operations WHERE operation_id=?",
            ("source-history-retry",),
        ).fetchone()[0]
    )
    assert task_refs == change_refs == ["source_restricted_alias"]
    assert receipt["task"]["source_refs"] == ["source_restricted_alias"]
    assert reopened._conn.execute("PRAGMA foreign_key_check").fetchall() == []
