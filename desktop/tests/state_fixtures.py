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
    _write_meeting_evidence_db(root)

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

    # The Agent Run store, created by the real repository. A fresh database is
    # born at the current version, so the registry has nothing to migrate.
    # Deliberately empty: a healthy state directory must contribute nothing to
    # any ownership or history finding, so a test that plants a stale owner, a
    # dead one, or an unreadable checkpoint measures exactly what it planted.
    from coworker.agent_run_repository import AgentRunRepository

    AgentRunRepository(root).close()

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

# MeetingEvidenceStore as it shipped on main. user_version stays 0 until adopted.
MEETING_EVIDENCE_DDL = """
    CREATE TABLE meeting_evidence (
        evidence_id TEXT PRIMARY KEY,
        provider TEXT NOT NULL,
        provider_id TEXT NOT NULL,
        title TEXT NOT NULL,
        starts_at TEXT,
        ends_at TEXT,
        participants_json TEXT NOT NULL,
        source_ref_json TEXT NOT NULL,
        notes TEXT,
        status TEXT NOT NULL,
        person_id TEXT,
        match_reason TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(provider, provider_id)
    );
    CREATE TABLE meeting_candidates (
        evidence_id TEXT NOT NULL,
        person_id TEXT NOT NULL,
        status TEXT NOT NULL,
        match_reason TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY(evidence_id, person_id)
    );
"""


# The Agent Run store as version 1 shipped it: identity, leases, and
# checkpoints, and no external-effect fence. Written out in full rather than
# imported so that a change to today's schema shows up as a migration to test,
# not as a fixture that quietly moved with it.
LEGACY_AGENT_RUNS_DDL = """
    CREATE TABLE agent_runs (
        run_id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        person_id TEXT,
        parent_run_id TEXT,
        trigger TEXT NOT NULL,
        goal_fingerprint TEXT NOT NULL,
        provider_model_id TEXT,
        current_state TEXT NOT NULL,
        version INTEGER NOT NULL DEFAULT 0,
        checkpoint_sequence INTEGER NOT NULL DEFAULT 0,
        approval_ids TEXT NOT NULL DEFAULT '[]',
        source_refs TEXT NOT NULL DEFAULT '[]',
        artifact_refs TEXT NOT NULL DEFAULT '[]',
        usage TEXT NOT NULL DEFAULT '{}',
        terminal_result TEXT,
        lease_owner TEXT,
        lease_owner_host TEXT,
        lease_owner_pid INTEGER,
        lease_expires_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        finished_at TEXT
    );
    CREATE TABLE agent_run_checkpoints (
        run_id TEXT NOT NULL,
        sequence INTEGER NOT NULL,
        kind TEXT NOT NULL,
        state TEXT NOT NULL,
        payload TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (run_id, sequence),
        FOREIGN KEY (run_id) REFERENCES agent_runs(run_id)
    );
    CREATE INDEX agent_runs_session_created
        ON agent_runs(session_id, created_at);
    CREATE INDEX agent_runs_live_lease
        ON agent_runs(lease_expires_at) WHERE lease_owner IS NOT NULL;
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
    _write_meeting_evidence_db(root)

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
    # The Agent Run store as the build before the external-effect fence left it:
    # version 1 tables, real rows, no `agent_run_effects`. This is the
    # production-shaped fixture the 1 -> 2 migration is exercised against, so it
    # goes through backup, apply, rollback, rerun, and restart with every other
    # store in this directory.
    runs_conn = sqlite3.connect(root / "agent_runs.db")
    runs_conn.executescript(LEGACY_AGENT_RUNS_DDL)
    # Both runs hold no lease: one parked on an operator, one finished. A
    # migration fixture is about rows surviving an upgrade, and ownership states
    # are planted by the tests that are about ownership.
    runs_conn.execute(
        "INSERT INTO agent_runs (run_id, session_id, trigger, goal_fingerprint, "
        "current_state, version, checkpoint_sequence, approval_ids, created_at, "
        "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "run-legacy-1",
            "main",
            "chat",
            "b" * 64,
            "waiting_approval",
            2,
            2,
            json.dumps(["call_gmail_send_1"]),
            "2026-08-01T09:12:00.000000+00:00",
            "2026-08-01T09:12:30.000000+00:00",
        ),
    )
    runs_conn.execute(
        "INSERT INTO agent_runs (run_id, session_id, trigger, goal_fingerprint, "
        "current_state, version, checkpoint_sequence, terminal_result, "
        "created_at, updated_at, finished_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "run-legacy-2",
            "sched-1",
            "scheduled",
            "c" * 64,
            "complete",
            3,
            2,
            json.dumps({"status": "ok", "text_length": 214}),
            "2026-08-03T16:00:00.000000+00:00",
            "2026-08-03T16:04:00.000000+00:00",
            "2026-08-03T16:04:00.000000+00:00",
        ),
    )
    for legacy_run, sequence, kind, state in (
        ("run-legacy-1", 1, "run_started", "running"),
        ("run-legacy-1", 2, "waiting_approval", "waiting_approval"),
        ("run-legacy-2", 1, "run_started", "running"),
        ("run-legacy-2", 2, "terminal", "complete"),
    ):
        runs_conn.execute(
            "INSERT INTO agent_run_checkpoints (run_id, sequence, kind, state, "
            "payload, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                legacy_run,
                sequence,
                kind,
                state,
                "{}",
                "2026-08-01T09:12:00.000000+00:00",
            ),
        )
    runs_conn.execute("PRAGMA user_version = 1")
    runs_conn.commit()
    runs_conn.close()
    os.chmod(root / "agent_runs.db", 0o600)

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


def _write_meeting_evidence_db(root: Path) -> None:
    path = root / "meeting_evidence.db"
    conn = sqlite3.connect(path)
    try:
        conn.executescript(MEETING_EVIDENCE_DDL)
        conn.execute(
            """
            INSERT INTO meeting_evidence (
                evidence_id, provider, provider_id, title, starts_at, ends_at,
                participants_json, source_ref_json, notes, status, person_id,
                match_reason, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'unmatched', NULL, NULL, ?, ?)
            """,
            (
                "meeting_abc123abc123abc123abcd",
                "calendar",
                "evt_1",
                "Rippling intro",
                "2026-08-20T16:00:00+00:00",
                "2026-08-20T16:30:00+00:00",
                json.dumps([{"name": "Dana Ruiz", "email": "dana@example.com"}]),
                json.dumps(
                    {
                        "id": "calendar:evt_1",
                        "title": "Rippling intro",
                        "url": None,
                        "provider": "Google Calendar",
                    }
                ),
                None,
                "2026-08-20T16:00:00+00:00",
                "2026-08-20T16:00:00+00:00",
            ),
        )
        conn.commit()
    finally:
        conn.close()
    os.chmod(path, 0o600)
