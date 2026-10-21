"""v7 task evidence DDL; v6 analysis DDL stays frozen for old upgrades."""

CRM_TASK_EVIDENCE_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS crm_task_evidence (
        proposal_id TEXT PRIMARY KEY REFERENCES crm_proposals(proposal_id),
        source_id TEXT NOT NULL REFERENCES person_attachments(id),
        message_id TEXT NOT NULL,
        body_fingerprint TEXT NOT NULL,
        request_start INTEGER NOT NULL CHECK (request_start >= 0),
        request_end INTEGER NOT NULL CHECK (request_end > request_start)
    )""",
)


def add_task_analysis_schema(conn):
    for statement in CRM_TASK_EVIDENCE_STATEMENTS:
        conn.execute(statement)
    if "analysis_version" not in {row[1] for row in conn.execute("PRAGMA table_info(crm_analysis_state)")}:
        conn.execute("ALTER TABLE crm_analysis_state ADD COLUMN analysis_version TEXT NOT NULL DEFAULT ''")
