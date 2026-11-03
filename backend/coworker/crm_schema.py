"""Versioned CRM additions. Statements run inside the owning transaction."""

CRM_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS crm_analysis_attempts (
        attempt_id TEXT PRIMARY KEY, person_id TEXT NOT NULL REFERENCES people(person_id),
        evidence_revision INTEGER NOT NULL, model_version TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('running','succeeded','failed','interrupted','stale')),
        started_at TEXT NOT NULL, finished_at TEXT, usage_json TEXT NOT NULL DEFAULT '{}'
    )""",
    """CREATE TABLE IF NOT EXISTS crm_proposals (
        proposal_id TEXT PRIMARY KEY, person_id TEXT NOT NULL REFERENCES people(person_id),
        kind TEXT NOT NULL CHECK(kind IN ('task_create','task_update','description','status')),
        value_json TEXT NOT NULL, expected_version INTEGER NOT NULL CHECK(expected_version >= 0),
        reason TEXT NOT NULL, source_refs TEXT NOT NULL, fingerprint TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','accepted','dismissed')),
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        UNIQUE(person_id, fingerprint)
    )""",
    "CREATE INDEX IF NOT EXISTS crm_proposals_person_state ON crm_proposals(person_id, state)",
    """CREATE TABLE IF NOT EXISTS crm_interpretations (
        person_id TEXT NOT NULL REFERENCES people(person_id), fingerprint TEXT NOT NULL,
        change_id TEXT REFERENCES crm_changes(change_id), suppressed INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(person_id, fingerprint)
    )""",
    """CREATE TABLE IF NOT EXISTS crm_review_dismissals (
        person_id TEXT NOT NULL REFERENCES people(person_id), message_id TEXT NOT NULL,
        PRIMARY KEY(person_id, message_id)
    )""",
)


def extend_columns(conn):
    additions = {
        "people": {"attention_reason": "TEXT", "attention_until": "TEXT"},
        "person_descriptions": {"analyzed_revision": "INTEGER NOT NULL DEFAULT 0"},
        "crm_analysis_state": {
            "attempts": "INTEGER NOT NULL DEFAULT 0", "attempted_revision": "INTEGER NOT NULL DEFAULT 0",
            "failure": "TEXT", "next_attempt_at": "TEXT", "model_version": "TEXT",
            "usage_json": "TEXT NOT NULL DEFAULT '{}'", "last_analyzed_at": "TEXT",
        },
    }
    for table, fields in additions.items():
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, declaration in fields.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
