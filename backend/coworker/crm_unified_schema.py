"""Schema 8 joins the independently shipped CRM store layouts without losing records.

Only the registered backed-up migration calls normalize(). Constructor probes
must never rewrite a live legacy table or pretend an old store is current.
"""

from __future__ import annotations
import hashlib
import json

from coworker.crm_analysis_schema import CRM_ANALYSIS_STATEMENTS
from coworker.crm_email_task_schema import add_task_analysis_schema

ATTENTION_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS crm_interpretations(person_id TEXT NOT NULL REFERENCES people(person_id), fingerprint TEXT NOT NULL, change_id TEXT REFERENCES crm_changes(change_id), suppressed INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(person_id,fingerprint))",
    "CREATE TABLE IF NOT EXISTS crm_review_dismissals(person_id TEXT NOT NULL REFERENCES people(person_id),message_id TEXT NOT NULL,PRIMARY KEY(person_id,message_id))",
)


def columns(conn, table):
    return {row[1] for row in conn.execute("PRAGMA table_info(" + table + ")")}


def additions(conn):
    for table, fields in {
        "people": {"attention_reason": "TEXT", "attention_until": "TEXT"},
        "person_descriptions": {"analyzed_revision": "INTEGER NOT NULL DEFAULT 0"},
        "crm_analysis_state": {
            "latest_evidence_revision": "INTEGER NOT NULL DEFAULT 0",
            "observed_collection_revision": "INTEGER NOT NULL DEFAULT 0",
        },
    }.items():
        present = columns(conn, table)
        for name, ddl in fields.items():
            if name not in present:
                conn.execute("ALTER TABLE " + table + " ADD COLUMN " + name + " " + ddl)
    for statement in ATTENTION_SCHEMA:
        conn.execute(statement)


def _rows(conn, table):
    names = [row[1] for row in conn.execute("PRAGMA table_info(" + table + ")")]
    return (
        [dict(zip(names, row)) for row in conn.execute("SELECT * FROM " + table)]
        if names
        else []
    )


def _insert(conn, table, row):
    names = list(row)
    conn.execute(
        "INSERT INTO "
        + table
        + "("
        + ",".join(names)
        + ") VALUES("
        + ",".join("?" for _ in names)
        + ")",
        [row[k] for k in names],
    )


def _refs(conn, pid, values):
    if not isinstance(values, list):
        return values
    result = []
    for ref in values:
        if isinstance(ref, str):
            result.append(ref)
            continue
        if not isinstance(ref, dict):
            raise ValueError("invalid legacy CRM source reference")
        mid = ref.get("message_id")
        matches = []
        for row in conn.execute(
            "SELECT id,fields_json,restricted FROM person_attachments WHERE person_id=? AND record_type='source_ref'",
            (pid,),
        ):
            fields = json.loads(row[1])
            if (
                mid
                and (
                    not ref.get("account")
                    or not fields.get("account")
                    or fields.get("account") == ref.get("account")
                )
                and (
                    fields.get("message_id") == mid
                    or fields.get("id") in {f"gmail:{mid}", f"gmail:message:{mid}"}
                )
            ):
                matches.append(row)
        if matches:
            # Preserve the most restrictive alias; a public duplicate cannot grant access.
            result.append(sorted(matches, key=lambda r: (-r[2], r[0]))[0][0])
            continue
        identity = json.dumps(ref, sort_keys=True)
        sid = "source_" + hashlib.sha256((pid + identity).encode()).hexdigest()[:32]
        fields = {
            **ref,
            "provider": ref.get("provider", "Gmail"),
            "id": ref.get("id", f"gmail:{mid}"),
        }
        conn.execute(
            "INSERT OR IGNORE INTO person_attachments(id,person_id,record_type,idempotency_key,fields_json,restricted,created_at,updated_at) VALUES(?,?,'source_ref',?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
            (
                sid,
                pid,
                "crm-legacy:" + sid,
                json.dumps(fields),
                int(str(ref.get("sensitivity", "")).lower() == "restricted"),
            ),
        )
        result.append(sid)
    return sorted(set(result))


def _json_sources(conn, pid, value):
    if isinstance(value, list):
        return [_json_sources(conn, pid, v) for v in value]
    if not isinstance(value, dict):
        return value
    return {
        k: _refs(conn, pid, v) if k == "source_refs" else _json_sources(conn, pid, v)
        for k, v in value.items()
    }


def normalize(conn):
    """Recognize shape as well as version: v6/v7 had two incompatible branches."""
    state = _rows(conn, "crm_analysis_state")
    attempts = _rows(conn, "crm_analysis_attempts")
    proposals = _rows(conn, "crm_proposals")
    canonical_state = "latest_revision" in columns(conn, "crm_analysis_state")
    canonical_attempts = "input_revision" in columns(conn, "crm_analysis_attempts")
    canonical_proposals = "target_kind" in columns(conn, "crm_proposals")
    for table, rows, canonical in [
        ("crm_analysis_state", state, canonical_state),
        ("crm_analysis_attempts", attempts, canonical_attempts),
        ("crm_proposals", proposals, canonical_proposals),
    ]:
        if not canonical and rows:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS crm_legacy_records(table_name TEXT NOT NULL,record_id TEXT NOT NULL,record_json TEXT NOT NULL,PRIMARY KEY(table_name,record_id))"
            )
            for row in rows:
                identity = str(
                    row.get("attempt_id", row.get("proposal_id", row.get("person_id")))
                )
                conn.execute(
                    "INSERT OR IGNORE INTO crm_legacy_records VALUES(?,?,?)",
                    (table, identity, json.dumps(row)),
                )
    # Drop rather than rename: SQLite otherwise rewrites foreign keys in dependent tables.
    for table in ("crm_analysis_state", "crm_analysis_attempts", "crm_proposals"):
        conn.execute("DROP TABLE IF EXISTS " + table)
    for statement in CRM_ANALYSIS_STATEMENTS:
        conn.execute(
            statement.replace(
                "'description', 'task_create', 'task_update'",
                "'description', 'task_create', 'task_update', 'status'",
            )
        )
    add_task_analysis_schema(conn)
    additions(conn)
    state_fields = columns(conn, "crm_analysis_state")
    for old in state:
        if canonical_state:
            value = {k: v for k, v in old.items() if k in state_fields}
        else:
            latest = int(old.get("latest_evidence_revision", 0))
            value = {
                "person_id": old["person_id"],
                "latest_revision": latest,
                "analyzed_revision": min(latest, int(old.get("analyzed_revision", 0))),
                "latest_evidence_revision": latest,
                "observed_collection_revision": 0,
                "updated_at": old.get("last_analyzed_at")
                or "1970-01-01T00:00:00+00:00",
                "outcome": "pending",
                "failure_code": old.get("failure"),
            }
        value.setdefault("updated_at", "1970-01-01T00:00:00+00:00")
        _insert(conn, "crm_analysis_state", value)
    attempt_fields = columns(conn, "crm_analysis_attempts")
    for old in attempts:
        if canonical_attempts:
            value = {k: v for k, v in old.items() if k in attempt_fields}
        else:
            value = {
                "attempt_id": old["attempt_id"],
                "person_id": old["person_id"],
                "input_revision": old["evidence_revision"],
                "evidence_fingerprint": "legacy",
                "manifest_json": "[]",
                "prompt_version": str(old["model_version"]),
                "model": str(old["model_version"]),
                "usage_json": old.get("usage_json"),
                "outcome": {"succeeded": "analyzed", "running": "interrupted"}.get(
                    old["status"], old["status"]
                ),
                "created_at": old["started_at"],
                "updated_at": old.get("finished_at") or old["started_at"],
            }
        _insert(conn, "crm_analysis_attempts", value)
    proposal_fields = columns(conn, "crm_proposals")
    for old in proposals:
        pid = old["person_id"]
        if canonical_proposals:
            value = {k: v for k, v in old.items() if k in proposal_fields}
        else:
            content = json.loads(old["value_json"])
            kind = old["kind"]
            value = {
                "proposal_id": old["proposal_id"],
                "person_id": pid,
                "target_kind": kind,
                "target_id": content.get("task_id"),
                "target_slot": content.get("slot") if kind == "description" else None,
                "proposed_value": (
                    content.get("text", "")
                    if kind == "description"
                    else json.dumps(content)
                ),
                "expected_target_version": old["expected_version"],
                "reason": old["reason"],
                "source_refs": old["source_refs"],
                "intent_key": "legacy:" + kind + ":" + old["proposal_id"],
                "support_source_fingerprint": old["fingerprint"],
                "input_revision": 0,
                "state": old["state"],
                "created_at": old["created_at"],
                "updated_at": old["updated_at"],
            }
        value["source_refs"] = json.dumps(
            _refs(conn, pid, json.loads(value["source_refs"]))
        )
        _insert(conn, "crm_proposals", value)
    # Reconnect legacy review receipts to the exact mutation they accepted.
    from coworker.crm_tasks import _fingerprint

    for old in proposals:
        if canonical_proposals or old.get("state") != "accepted":
            continue
        receipt = conn.execute(
            "SELECT change_id,operation_id,after_json FROM crm_changes WHERE person_id=? AND entity_type='proposal' AND entity_id=? AND action='accept' ORDER BY rowid DESC LIMIT 1",
            (old["person_id"], old["proposal_id"]),
        ).fetchone()
        if receipt:
            target = conn.execute(
                "SELECT change_id FROM crm_changes WHERE person_id=? AND operation_id=?",
                (
                    old["person_id"],
                    "target_" + _fingerprint({"proposal_operation": receipt[1]}),
                ),
            ).fetchone()
            if target:
                conn.execute(
                    "UPDATE crm_proposals SET change_id=?,resolution='director' WHERE proposal_id=?",
                    (target[0], old["proposal_id"]),
                )
                after = json.loads(receipt[2])
                after["change_id"] = target[0]
                conn.execute(
                    "UPDATE crm_changes SET after_json=? WHERE change_id=?",
                    (json.dumps(after), receipt[0]),
                )
    # The old worker filed automatic creation receipts without proposal rows.
    # Materialize the same durable authority binding the canonical Undo checks.
    if columns(conn, "crm_interpretations"):
        for record in conn.execute(
            "SELECT i.person_id,i.fingerprint,i.suppressed,c.* FROM crm_interpretations i JOIN crm_changes c ON c.change_id=i.change_id WHERE c.entity_type='task' AND c.action='create'"
        ).fetchall():
            names = [
                d[0]
                for d in conn.execute(
                    "SELECT i.person_id,i.fingerprint,i.suppressed,c.* FROM crm_interpretations i JOIN crm_changes c ON c.change_id=i.change_id LIMIT 0"
                ).description
            ]
            item = dict(zip(names, record))
            cid = item["change_id"]
            if conn.execute(
                "SELECT 1 FROM crm_proposals WHERE change_id=?", (cid,)
            ).fetchone():
                continue
            task = json.loads(item["after_json"])
            _insert(
                conn,
                "crm_proposals",
                {
                    "proposal_id": "proposal_legacy_"
                    + hashlib.sha256(cid.encode()).hexdigest()[:24],
                    "person_id": item["person_id"],
                    "target_kind": "task_create",
                    "proposed_value": json.dumps(
                        {k: task.get(k) for k in ("title", "details", "due_date")}
                    ),
                    "expected_target_version": 0,
                    "reason": item["reason"],
                    "source_refs": item["source_refs"],
                    "intent_key": "legacy-auto:" + cid,
                    "support_source_fingerprint": item["fingerprint"],
                    "input_revision": 0,
                    "state": "dismissed" if item["suppressed"] else "accepted",
                    "resolution": "automatic",
                    "change_id": cid,
                    "created_at": item["created_at"],
                    "updated_at": item["created_at"],
                },
            )

    # Convert historical references too, so Undo/retry cannot resurrect dictionary grants.
    for table, key, fields in [
        ("person_tasks", "task_id", ("source_refs",)),
        ("person_descriptions", "rowid", ("source_refs",)),
        ("crm_changes", "change_id", ("source_refs", "before_json", "after_json")),
        ("events", "event_id", ("payload",)),
        ("person_versions", "version", ("person_json", "attachments_json")),
    ]:
        present = columns(conn, table)
        if not set(fields) <= present:
            continue
        for row in conn.execute("SELECT rowid,* FROM " + table).fetchall():
            names = [
                "__rowid",
                *[
                    d[0]
                    for d in conn.execute(
                        "SELECT * FROM " + table + " LIMIT 0"
                    ).description
                ],
            ]
            item = dict(zip(names, row))
            pid = item.get("person_id")
            if not pid:
                continue
            for field in fields:
                raw = item[field]
                if raw is None:
                    continue
                parsed = json.loads(raw)
                converted = (
                    _refs(conn, pid, parsed)
                    if field == "source_refs"
                    else _json_sources(conn, pid, parsed)
                )
                conn.execute(
                    "UPDATE " + table + " SET " + field + "=? WHERE rowid=?",
                    (json.dumps(converted), item["__rowid"]),
                )
    for operation in conn.execute(
        "SELECT o.operation_id,o.response_json,c.person_id FROM crm_operations o JOIN crm_changes c ON c.change_id=o.change_id"
    ).fetchall():
        conn.execute(
            "UPDATE crm_operations SET response_json=? WHERE operation_id=?",
            (
                json.dumps(_json_sources(conn, operation[2], json.loads(operation[1]))),
                operation[0],
            ),
        )
    return len(state) + len(attempts) + len(proposals)
