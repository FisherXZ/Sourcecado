"""DDL shared by fresh person stores and the registered v5→v6 migration."""

CRM_ANALYSIS_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS crm_analysis_state (
        person_id TEXT PRIMARY KEY REFERENCES people(person_id),
        latest_revision INTEGER NOT NULL DEFAULT 0,
        analyzed_revision INTEGER NOT NULL DEFAULT 0,
        evidence_fingerprint TEXT NOT NULL DEFAULT '',
        collection_fingerprint TEXT NOT NULL DEFAULT '',
        attempts INTEGER NOT NULL DEFAULT 0,
        next_retry_at REAL,
        lease_token TEXT,
        lease_until REAL,
        failure_code TEXT,
        outcome TEXT NOT NULL DEFAULT 'pending',
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS crm_analysis_attempts (
        attempt_id TEXT PRIMARY KEY,
        person_id TEXT NOT NULL REFERENCES people(person_id),
        input_revision INTEGER NOT NULL,
        evidence_fingerprint TEXT NOT NULL,
        manifest_json TEXT NOT NULL,
        prompt_version TEXT NOT NULL,
        provider TEXT,
        model TEXT,
        usage_json TEXT,
        estimated_cost_usd REAL,
        budget_json TEXT,
        outcome TEXT NOT NULL,
        failure_code TEXT,
        change_ids TEXT NOT NULL DEFAULT '[]',
        proposal_ids TEXT NOT NULL DEFAULT '[]',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE INDEX IF NOT EXISTS crm_analysis_attempts_person
       ON crm_analysis_attempts(person_id, created_at)""",
    """CREATE TABLE IF NOT EXISTS crm_proposals (
        proposal_id TEXT PRIMARY KEY,
        person_id TEXT NOT NULL REFERENCES people(person_id),
        target_kind TEXT NOT NULL CHECK (target_kind IN ('description', 'task_create', 'task_update')),
        target_id TEXT,
        target_slot TEXT CHECK (target_slot IN ('general', 'detailed')),
        proposed_value TEXT NOT NULL,
        expected_target_version INTEGER NOT NULL CHECK (expected_target_version >= 0),
        version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
        reason TEXT NOT NULL CHECK (length(reason) BETWEEN 1 AND 500),
        source_refs TEXT NOT NULL,
        intent_key TEXT NOT NULL,
        support_source_fingerprint TEXT NOT NULL,
        input_revision INTEGER NOT NULL,
        state TEXT NOT NULL CHECK (state IN ('pending', 'accepted', 'dismissed')),
        resolution TEXT,
        change_id TEXT REFERENCES crm_changes(change_id),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(person_id, intent_key, support_source_fingerprint),
        CHECK ((target_kind = 'description' AND target_slot IN ('general', 'detailed'))
            OR (target_kind != 'description' AND target_slot IS NULL))
    )""",
)
