"""Production-shaped Sourcecado state fixtures for Doctor and migration tests.

Two shapes are built here. `build_current_state` drives the real store classes so
the result is exactly what a running Sourcecado leaves on disk. `build_legacy_state`
writes the pre-registry schema by hand, from the DDL those stores shipped before
the column-add blocks existed, so upgrade tests run against a real prior version
rather than a toy one.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

# Planted credentials and message bodies. Redaction tests assert that none of
# these strings reach Doctor output, Doctor JSON, or a backup manifest.
PLANTED_API_KEY = "sk-live-DOCTORLEAKCANARY0123456789"
PLANTED_BEARER = "ya29.DOCTORLEAKCANARYgoogleoauthaccesstoken"
PLANTED_REFRESH = "1//0gDOCTORLEAKCANARYrefreshtoken"
PLANTED_BODY = (
    "Hi Dana, following up on the Codeology intro. My cell is 555-0147 "
    "and the offer detail is attached."
)
PLANTED_REASONING = "private reasoning: Dana looked lukewarm on the last thread"
PLANTED_CANARIES = (
    PLANTED_API_KEY,
    PLANTED_BEARER,
    PLANTED_REFRESH,
    PLANTED_BODY,
    PLANTED_REASONING,
)


# --- current shape -------------------------------------------------------


def build_current_state(root: Path, *, plant_secrets: bool = False) -> Path:
    """Write a full state directory using the real store classes."""
    from coworker.people import PersonStore
    from coworker.store import ConversationStore
    from coworker.workspace import GrantAccess, WorkspaceGrantStore
    from coworker.workspace_audit import WorkspaceAuditStore
    from coworker.workspace_policy import HostApprovalStore
    from coworker.workspace_runtime import DirectoryRequestStore
    from coworker.workspace_shell import ShellTaskStore

    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)

    store = ConversationStore(root)
    store.create_session("main")
    store.set_open_session("main")
    store.append("main", {"role": "user", "content": "find leads at Rippling"})
    store.append(
        "main",
        {"role": "assistant", "content": "Searching Apollo for Rippling recruiters."},
    )
    store.append_event(
        "main",
        {
            "version": 2,
            "type": "turn_start",
            "session_id": "main",
            "run_id": "run_a1",
            "event_id": "event_a1",
            "message_id": "message_a1",
            "part_id": "part_a1",
            "state": "running",
        },
    )
    store.append_event(
        "main",
        {
            "version": 2,
            "type": "turn_end",
            "session_id": "main",
            "run_id": "run_a1",
            "event_id": "event_a2",
            "message_id": "message_a1",
            "part_id": "part_a1",
            "state": "idle",
            "text": "Found four recruiters.",
        },
    )
    store.remember("Codeology sources design-adjacent engineers first.")
    store.set_setting("persona", "sourcing")
    job = store.add_job(
        "0 9 * * 1",
        "weekly sourcing check-in",
        next_run_at="2026-09-07T09:00:00-07:00",
        name="Weekly sourcing review",
        template_id="weekly_sourcing_review",
        cadence="weekly_monday_0900",
    )
    store.record_run(int(job["id"]), "success", "Reviewed 4 open sequences.")
    store.park_inbox(
        "call_gmail_send_1",
        "gmail_send",
        {"draft_id": "draft_991", "body": PLANTED_BODY if plant_secrets else "hello"},
        reason="consequential",
        session_id="main",
        run_id="run_a1",
        message_id="message_a1",
        part_id="part_a1",
    )
    store.apply_queue_command(
        "main",
        {
            "type": "queue_add",
            "command_id": "cmd_1",
            "item_id": "queue_1",
            "text": "check the Rippling thread",
        },
    )

    people = PersonStore(root)
    person = people.keep_from_apollo(
        apollo_id="apollo_5501",
        first_name="Dana",
        last_name_obfuscated="R.",
        title="Technical Recruiter",
        company="Rippling",
        target="Rippling",
    )
    person_id = str(person["person_id"])
    people.apply_enrichment(
        person_id,
        email="dana@example.com",
        linkedin_url="https://www.linkedin.com/in/dana-ruiz",
    )
    people.bind_session("main", person_id)
    people.append_event(
        person_id,
        source="gmail",
        kind="draft",
        summary="Drafted intro mail",
        payload={
            "draft_id": "draft_991",
            "body": PLANTED_BODY if plant_secrets else "hello",
        },
        actor="assistant",
        session_id="main",
    )
    people.set_sequence(person_id, "open", actor="director")

    grants = WorkspaceGrantStore(root)
    grant_root = root.parent / "granted-workspace"
    grant_root.mkdir(parents=True, exist_ok=True)
    # A credential pasted into a label or a command summary is the realistic way
    # a secret reaches a store that is not itself secret-bearing.
    label = f"Sourcing notes {PLANTED_API_KEY}" if plant_secrets else "Sourcing notes"
    grants.add(grant_root, label=label, access=GrantAccess.READ_ONLY)

    ShellTaskStore(root).put(
        {
            "task_id": "task_1",
            "status": "succeeded",
            "execution_target": "docker",
            "command_summary": (
                f"curl -H 'authorization: Bearer {PLANTED_BEARER}' api.example.com"
                if plant_secrets
                else "ls -la"
            ),
            "exit_code": 0,
        }
    )
    DirectoryRequestStore(root).create(
        {"label": label, "access": "read_only"},
        session_id="main",
        run_id="run_a1",
    )
    HostApprovalStore(root)._save(
        {
            "version": HostApprovalStore.VERSION,
            "approvals": [
                {
                    "id": "host_approval_1",
                    "fingerprint": "b" * 64,
                    "command_summary": (
                        f"npm test --token={PLANTED_API_KEY}"
                        if plant_secrets
                        else "npm test"
                    ),
                    "cwd": "/tmp/granted-workspace",
                    "environment_fingerprint": "c" * 64,
                    "actor": "director",
                    "created_at": _now(),
                    "revoked_at": None,
                }
            ],
        }
    )
    WorkspaceAuditStore(root).record(
        receipt_type="shell",
        tool="shell_run",
        risk_class="reversible",
        decision="allow",
        execution_target="docker",
        status="succeeded",
        summary="ran npm test",
        grant_id="grant_1",
    )

    _write_json(
        root / "mcp.json",
        {"mcpServers": {"granola": {"headers": {"Authorization": PLANTED_BEARER}}}}
        if plant_secrets
        else {"mcpServers": {}},
    )
    secrets_payload: dict[str, Any] = {"google": {"scopes": ["gmail.readonly"]}}
    if plant_secrets:
        secrets_payload = {
            "google": {
                "access_token": PLANTED_BEARER,
                "refresh_token": PLANTED_REFRESH,
                "scopes": ["gmail.readonly"],
            },
            "apollo": {"api_key": PLANTED_API_KEY},
        }
    _write_json(root / "secrets.json", secrets_payload)
    _write_private(
        root / ".env",
        f"APOLLO_API_KEY={PLANTED_API_KEY}\n" if plant_secrets else "CLUB_PERSONA=sourcing\n",
    )
    _write_drive_ingestion_db(root, CURRENT_DRIVE_INGESTION_DDL)

    if plant_secrets:
        store.append(
            "main",
            {"role": "assistant", "content": PLANTED_REASONING},
        )
        store.set_setting("apollo_key_hint", PLANTED_API_KEY)
        store.append_event(
            "main",
            {
                "version": 2,
                "type": "error",
                "session_id": "main",
                "run_id": "run_a1",
                "event_id": "event_a3",
                "message_id": "message_a1",
                "part_id": "part_a1",
                "message": f"Apollo rejected api_key={PLANTED_API_KEY}",
            },
        )
        _write_private(
            root / "memory" / "99.md",
            f"# 99\n\nApollo key is {PLANTED_API_KEY}. {PLANTED_REASONING}\n",
        )

    return root


# --- legacy shape --------------------------------------------------------

# The conversation schema as it shipped before store.py grew its column-add
# block: no jobs.next_run_at/name/template_id/cadence, no runs receipt columns,
# no sessions.pinned/opened_at, no inbox approval columns, and none of the chat
# queue tables.
LEGACY_CONVERSATION_DDL = """
    CREATE TABLE sessions (
        session_id TEXT PRIMARY KEY,
        title TEXT,
        n_msgs INTEGER DEFAULT 0,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE memories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        content TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cron TEXT NOT NULL,
        prompt TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER NOT NULL,
        status TEXT NOT NULL,
        result TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    CREATE TABLE inbox (
        id TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        name TEXT NOT NULL,
        arguments TEXT NOT NULL,
        state TEXT NOT NULL,
        decision TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
"""

# The person schema before attachments, versions, and soft delete landed.
LEGACY_PEOPLE_DDL = """
    CREATE TABLE people (
        person_id TEXT PRIMARY KEY,
        apollo_id TEXT UNIQUE,
        first_name TEXT,
        last_name TEXT,
        title TEXT,
        company TEXT,
        email TEXT,
        linkedin_url TEXT,
        phone TEXT,
        sequence_state TEXT,
        target TEXT,
        handoff_who TEXT,
        handoff_wanted TEXT,
        handoff_happened TEXT,
        handoff_they_want TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE events (
        event_id TEXT PRIMARY KEY,
        person_id TEXT NOT NULL,
        source TEXT NOT NULL,
        kind TEXT NOT NULL,
        summary TEXT NOT NULL,
        payload TEXT NOT NULL DEFAULT '{}',
        actor TEXT NOT NULL,
        session_id TEXT,
        run_id TEXT,
        tool TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE session_people (
        session_id TEXT PRIMARY KEY,
        person_id TEXT NOT NULL
    );
"""

LEGACY_PERSON_ID = "per_0f3c9a1b4d2e4f6a8b0c1d2e3f405162"

# DriveIngestionStore as it shipped before work_revision. user_version stays 0.
LEGACY_DRIVE_INGESTION_DDL = """
    CREATE TABLE drive_ingestion_jobs (
        id TEXT PRIMARY KEY,
        folder_id TEXT NOT NULL,
        resolved_path TEXT NOT NULL,
        status TEXT NOT NULL,
        generation INTEGER NOT NULL,
        cancel_requested INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE drive_ingestion_folders (
        job_id TEXT NOT NULL,
        drive_id TEXT NOT NULL,
        parent_id TEXT,
        path TEXT NOT NULL,
        status TEXT NOT NULL,
        page_token TEXT,
        generation INTEGER NOT NULL,
        error_kind TEXT,
        PRIMARY KEY (job_id, drive_id)
    );
    CREATE TABLE drive_ingestion_sources (
        job_id TEXT NOT NULL,
        drive_id TEXT NOT NULL,
        scope TEXT NOT NULL DEFAULT 'tree',
        parent_id TEXT,
        path TEXT NOT NULL,
        name TEXT NOT NULL,
        mime_type TEXT NOT NULL,
        modified_time TEXT,
        web_view_link TEXT,
        sensitivity TEXT NOT NULL DEFAULT 'standard',
        extraction_status TEXT NOT NULL DEFAULT 'pending',
        content TEXT,
        citations_json TEXT NOT NULL DEFAULT '[]',
        redaction_count INTEGER NOT NULL DEFAULT 0,
        source_safety_json TEXT,
        generation INTEGER NOT NULL,
        last_action TEXT NOT NULL DEFAULT 'pending',
        error_kind TEXT,
        deleted INTEGER NOT NULL DEFAULT 0,
        needs_read INTEGER NOT NULL DEFAULT 1,
        PRIMARY KEY (job_id, drive_id)
    );
    CREATE TABLE drive_ingestion_proposals (
        id TEXT PRIMARY KEY,
        job_id TEXT NOT NULL,
        source_drive_id TEXT NOT NULL,
        person_id TEXT NOT NULL,
        record_type TEXT NOT NULL,
        fields_json TEXT NOT NULL,
        diff_json TEXT NOT NULL,
        source_refs_json TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        reviewed_at TEXT,
        reviewer TEXT
    );
"""

# The constructor's current CREATE TABLE, still at user_version 0 until adopted.
CURRENT_DRIVE_INGESTION_DDL = """
    CREATE TABLE drive_ingestion_jobs (
        id TEXT PRIMARY KEY,
        folder_id TEXT NOT NULL,
        resolved_path TEXT NOT NULL,
        status TEXT NOT NULL,
        generation INTEGER NOT NULL,
        work_revision INTEGER NOT NULL DEFAULT 1,
        cancel_requested INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE drive_ingestion_folders (
        job_id TEXT NOT NULL,
        drive_id TEXT NOT NULL,
        parent_id TEXT,
        path TEXT NOT NULL,
        status TEXT NOT NULL,
        page_token TEXT,
        generation INTEGER NOT NULL,
        error_kind TEXT,
        PRIMARY KEY (job_id, drive_id)
    );
    CREATE TABLE drive_ingestion_sources (
        job_id TEXT NOT NULL,
        drive_id TEXT NOT NULL,
        scope TEXT NOT NULL DEFAULT 'tree',
        parent_id TEXT,
        path TEXT NOT NULL,
        name TEXT NOT NULL,
        mime_type TEXT NOT NULL,
        modified_time TEXT,
        web_view_link TEXT,
        sensitivity TEXT NOT NULL DEFAULT 'standard',
        extraction_status TEXT NOT NULL DEFAULT 'pending',
        content TEXT,
        citations_json TEXT NOT NULL DEFAULT '[]',
        redaction_count INTEGER NOT NULL DEFAULT 0,
        source_safety_json TEXT,
        generation INTEGER NOT NULL,
        last_action TEXT NOT NULL DEFAULT 'pending',
        error_kind TEXT,
        deleted INTEGER NOT NULL DEFAULT 0,
        needs_read INTEGER NOT NULL DEFAULT 1,
        PRIMARY KEY (job_id, drive_id)
    );
    CREATE TABLE drive_ingestion_proposals (
        id TEXT PRIMARY KEY,
        job_id TEXT NOT NULL,
        source_drive_id TEXT NOT NULL,
        person_id TEXT NOT NULL,
        record_type TEXT NOT NULL,
        fields_json TEXT NOT NULL,
        diff_json TEXT NOT NULL,
        source_refs_json TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        reviewed_at TEXT,
        reviewer TEXT
    );
"""


def build_legacy_state(root: Path, *, plant_secrets: bool = False) -> Path:
    """Write the pre-registry state directory, populated the way an install is."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)

    conn = sqlite3.connect(root / "club.db")
    conn.executescript(LEGACY_CONVERSATION_DDL)
    conn.execute(
        "INSERT INTO sessions (session_id, title, n_msgs, updated_at) VALUES (?, ?, ?, ?)",
        ("main", "find leads at Rippling", 2, "2026-08-01T09:12:00+00:00"),
    )
    conn.execute(
        "INSERT INTO sessions (session_id, title, n_msgs, updated_at) VALUES (?, ?, ?, ?)",
        ("sched-1", "Weekly sourcing review", 4, "2026-08-03T16:00:00+00:00"),
    )
    conn.execute(
        "INSERT INTO memories (content) VALUES (?)",
        ("Codeology sources design-adjacent engineers first.",),
    )
    conn.execute("INSERT INTO jobs (cron, prompt) VALUES (?, ?)", ("0 9 * * 1", "weekly sourcing check-in"))
    conn.execute(
        "INSERT INTO runs (job_id, status, result) VALUES (?, ?, ?)",
        (1, "success", "Reviewed 4 open sequences."),
    )
    conn.execute(
        "INSERT INTO runs (job_id, status, result) VALUES (?, ?, ?)",
        (1, "failed", "Apollo credit check failed."),
    )
    conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("persona", "sourcing"))
    conn.execute(
        "INSERT INTO inbox (id, kind, name, arguments, state, decision) VALUES (?, ?, ?, ?, ?, ?)",
        (
            "call_gmail_send_1",
            "approval",
            "gmail_send",
            json.dumps(
                {
                    "draft_id": "draft_991",
                    "body": PLANTED_BODY if plant_secrets else "hello",
                }
            ),
            "resolved",
            "allow",
        ),
    )
    conn.execute(
        "INSERT INTO inbox (id, kind, name, arguments, state, decision) VALUES (?, ?, ?, ?, ?, ?)",
        (
            "call_apollo_enrich_1",
            "approval",
            "apollo_enrich_contact",
            json.dumps({"person_id": LEGACY_PERSON_ID}),
            "pending",
            None,
        ),
    )
    conn.commit()
    conn.close()
    os.chmod(root / "club.db", 0o600)

    conn = sqlite3.connect(root / "people.db")
    conn.executescript(LEGACY_PEOPLE_DDL)
    conn.execute(
        """
        INSERT INTO people
            (person_id, apollo_id, first_name, last_name, title, company, email,
             linkedin_url, sequence_state, target)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            LEGACY_PERSON_ID,
            "apollo_5501",
            "Dana",
            "Ruiz",
            "Technical Recruiter",
            "Rippling",
            "dana@example.com",
            "https://www.linkedin.com/in/dana-ruiz",
            "open",
            "Rippling",
        ),
    )
    conn.execute(
        """
        INSERT INTO events
            (event_id, person_id, source, kind, summary, payload, actor, session_id, tool)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "evt_1",
            LEGACY_PERSON_ID,
            "gmail",
            "draft",
            "Drafted intro mail",
            json.dumps(
                {
                    "draft_id": "draft_991",
                    "body": PLANTED_BODY if plant_secrets else "hello",
                }
            ),
            "assistant",
            "main",
            "gmail_draft",
        ),
    )
    conn.execute(
        "INSERT INTO session_people (session_id, person_id) VALUES (?, ?)",
        ("main", LEGACY_PERSON_ID),
    )
    conn.commit()
    conn.close()
    os.chmod(root / "people.db", 0o600)
    _write_drive_ingestion_db(root, LEGACY_DRIVE_INGESTION_DDL)

    conv_dir = root / "conversations"
    conv_dir.mkdir(exist_ok=True)
    os.chmod(conv_dir, 0o700)
    _write_jsonl(
        conv_dir / "main.jsonl",
        [
            {"role": "user", "content": "find leads at Rippling"},
            {"role": "assistant", "content": "Searching Apollo for Rippling recruiters."},
        ],
    )
    event_dir = root / "events"
    event_dir.mkdir(exist_ok=True)
    os.chmod(event_dir, 0o700)
    _write_jsonl(
        event_dir / "main.jsonl",
        [
            {
                "version": 2,
                "type": "turn_start",
                "session_id": "main",
                "run_id": "run_a1",
                "event_id": "event_a1",
                "message_id": "message_a1",
                "part_id": "part_a1",
                "state": "running",
            }
        ],
    )
    memory_dir = root / "memory"
    memory_dir.mkdir(exist_ok=True)
    os.chmod(memory_dir, 0o700)
    _write_private(
        memory_dir / "1.md",
        "# 1\n\nCodeology sources design-adjacent engineers first.\n",
    )

    # Pre-registry JSON documents: shaped correctly but carrying no version key.
    _write_json(
        root / "workspace_grants.json",
        {
            "grants": [
                {
                    "id": "grant_legacy_1",
                    "path": str(root.parent / "granted-workspace"),
                    "label": "Sourcing notes",
                    "access": "read_only",
                    "allow_shell": False,
                    "filesystem_identity": {"device": 1, "inode": 2},
                    "created_at": "2026-07-14T18:02:00+00:00",
                    "updated_at": "2026-07-14T18:02:00+00:00",
                    "revoked_at": None,
                }
            ]
        },
    )
    _write_json(root / "shell_tasks.json", {"tasks": []})
    _write_json(root / "host_command_approvals.json", {"approvals": []})
    _write_json(root / "directory_requests.json", {"requests": []})
    _write_json(root / "mcp.json", {"mcpServers": {}})
    _write_json(
        root / "secrets.json",
        {"apollo": {"api_key": PLANTED_API_KEY}} if plant_secrets else {"google": {}},
    )
    _write_jsonl(
        root / "workspace_receipts.jsonl",
        [
            {
                "id": "receipt_legacy_1",
                "receipt_type": "file",
                "tool": "fs_write",
                "risk_class": "reversible",
                "decision": "allow",
                "execution_target": "host",
                "status": "succeeded",
                "summary": "wrote notes.md",
                "created_at": "2026-07-14T18:04:00+00:00",
            }
        ],
    )
    return root


# --- helpers -------------------------------------------------------------


def _now() -> str:
    return datetime.now(UTC).isoformat()


def hours_ago(hours: int) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours)).isoformat()


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.chmod(path, 0o600)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    _write_private(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    body = "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)
    _write_private(path, body)


def _write_drive_ingestion_db(root: Path, ddl: str) -> None:
    path = root / "drive_ingestion.db"
    conn = sqlite3.connect(path)
    try:
        conn.executescript(ddl)
        conn.execute(
            """
            INSERT INTO drive_ingestion_jobs
                (id, folder_id, resolved_path, status, generation, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "drive_ingest_1",
                "folder_1",
                "Codeology/Sourcing",
                "paused",
                1,
                "2026-08-01T09:00:00+00:00",
                "2026-08-01T09:00:00+00:00",
            ),
        )
        conn.commit()
    finally:
        conn.close()
    os.chmod(path, 0o600)
