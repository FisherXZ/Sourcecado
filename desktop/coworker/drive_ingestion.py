"""Durable, source-scoped Google Drive folder ingestion."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable


def _now() -> str:
    return datetime.now(UTC).isoformat()


DRIVE_INDEX_QUERY_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "drive_index_query",
        "description": (
            "Query one completed Sourcecado Drive folder index without rereading Drive. "
            "External sources stay excluded unless include_external is explicitly true."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string"},
                "query": {"type": "string"},
                "include_external": {"type": "boolean"},
                "limit": {"type": "integer"},
            },
            "required": ["job_id", "query"],
            "additionalProperties": False,
        },
    },
}


class DriveIngestionStore:
    def __init__(self, state_dir: str | Path) -> None:
        root = Path(state_dir).expanduser()
        root.mkdir(parents=True, exist_ok=True)
        self.db_path = root / "drive_ingestion.db"
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS drive_ingestion_jobs (
                id TEXT PRIMARY KEY,
                folder_id TEXT NOT NULL,
                resolved_path TEXT NOT NULL,
                status TEXT NOT NULL,
                generation INTEGER NOT NULL,
                cancel_requested INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS drive_ingestion_folders (
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
            CREATE TABLE IF NOT EXISTS drive_ingestion_sources (
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
            CREATE TABLE IF NOT EXISTS drive_ingestion_proposals (
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
        )
        self._conn.execute(
            """
            UPDATE drive_ingestion_jobs
            SET status = 'paused', cancel_requested = 0, updated_at = ?
            WHERE status IN ('running', 'cancel_requested')
            """,
            (_now(),),
        )
        self._conn.commit()

    def create_job(self, *, folder_id: str, resolved_path: str) -> dict[str, Any]:
        folder_id = folder_id.strip()
        if not folder_id:
            raise ValueError("folder_id is required")
        resolved_path = resolved_path.strip().strip("/")
        if not resolved_path:
            raise ValueError("resolved_path is required")
        if any(part in {"", ".", ".."} for part in resolved_path.split("/")):
            raise ValueError("resolved_path must be a resolved Drive path")
        job_id = f"drive_ingest_{uuid.uuid4().hex}"
        now = _now()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO drive_ingestion_jobs
                    (id, folder_id, resolved_path, status, generation, created_at, updated_at)
                VALUES (?, ?, ?, 'pending', 1, ?, ?)
                """,
                (job_id, folder_id, resolved_path, now, now),
            )
            self._conn.execute(
                """
                INSERT INTO drive_ingestion_folders
                    (job_id, drive_id, parent_id, path, status, generation)
                VALUES (?, ?, NULL, ?, 'pending', 1)
                """,
                (job_id, folder_id, resolved_path),
            )
            self._conn.commit()
        job = self.get_job(job_id)
        assert job is not None
        return job

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM drive_ingestion_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                return None
            folder_count = int(
                self._conn.execute(
                    """
                    SELECT COUNT(*) FROM drive_ingestion_folders
                    WHERE job_id = ? AND generation = ?
                    """,
                    (job_id, int(row["generation"])),
                ).fetchone()[0]
            )
            folder_failed = int(
                self._conn.execute(
                    """
                    SELECT COUNT(*) FROM drive_ingestion_folders
                    WHERE job_id = ? AND generation = ? AND status = 'failed'
                    """,
                    (job_id, int(row["generation"])),
                ).fetchone()[0]
            )
            remaining = int(
                self._conn.execute(
                    """
                    SELECT COUNT(*) FROM drive_ingestion_folders
                    WHERE job_id = ? AND status = 'pending'
                    """,
                    (job_id,),
                ).fetchone()[0]
            )
            source_counts = self._conn.execute(
                """
                SELECT
                    SUM(CASE WHEN deleted = 0 THEN 1 ELSE 0 END) AS discovered,
                    SUM(CASE WHEN last_action = 'read' THEN 1 ELSE 0 END) AS read_count,
                    SUM(CASE WHEN last_action = 'metadata_only' THEN 1 ELSE 0 END) AS metadata_count,
                    SUM(CASE WHEN last_action = 'skipped' THEN 1 ELSE 0 END) AS skipped_count,
                    SUM(CASE WHEN last_action = 'failed' THEN 1 ELSE 0 END) AS failed_count,
                    SUM(CASE WHEN deleted = 1 THEN 1 ELSE 0 END) AS deleted_count,
                    SUM(CASE WHEN needs_read = 1 THEN 1 ELSE 0 END) AS remaining_count
                FROM drive_ingestion_sources
                WHERE job_id = ? AND generation = ? AND scope = 'tree'
                """,
                (job_id, int(row["generation"])),
            ).fetchone()
            remaining += int(source_counts["remaining_count"] or 0)
        return {
            "id": row["id"],
            "folder_id": row["folder_id"],
            "resolved_path": row["resolved_path"],
            "status": row["status"],
            "generation": int(row["generation"]),
            "progress": {
                "folders_discovered": folder_count,
                "files_discovered": int(source_counts["discovered"] or 0),
                "read": int(source_counts["read_count"] or 0),
                "metadata_only": int(source_counts["metadata_count"] or 0),
                "skipped": int(source_counts["skipped_count"] or 0),
                "failed": folder_failed + int(source_counts["failed_count"] or 0),
                "deleted": int(source_counts["deleted_count"] or 0),
                "remaining": remaining,
            },
        }

    def list_jobs(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id FROM drive_ingestion_jobs
                ORDER BY created_at DESC, id DESC
                """
            ).fetchall()
        jobs: list[dict[str, Any]] = []
        for row in rows:
            job = self.get_job(str(row["id"]))
            if job is not None:
                jobs.append(job)
        return jobs

    def prepare_rerun(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM drive_ingestion_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                raise ValueError("unknown Drive ingestion job")
            if row["status"] not in {"completed", "completed_with_errors", "paused"}:
                raise ValueError("Drive ingestion job is not ready to rerun")
            generation = int(row["generation"]) + 1
            self._conn.execute(
                """
                DELETE FROM drive_ingestion_folders
                WHERE job_id = ? AND drive_id != ?
                """,
                (job_id, row["folder_id"]),
            )
            self._conn.execute(
                """
                UPDATE drive_ingestion_folders
                SET parent_id = NULL, path = ?, status = 'pending', page_token = NULL,
                    generation = ?, error_kind = NULL
                WHERE job_id = ? AND drive_id = ?
                """,
                (row["resolved_path"], generation, job_id, row["folder_id"]),
            )
            self._conn.execute(
                """
                UPDATE drive_ingestion_jobs
                SET status = 'pending', generation = ?, cancel_requested = 0,
                    updated_at = ?
                WHERE id = ?
                """,
                (generation, _now(), job_id),
            )
            self._conn.commit()
        job = self.get_job(job_id)
        assert job is not None
        return job

    def add_explicit_source(
        self,
        job_id: str,
        *,
        drive_id: str,
        name: str,
        parent_id: str,
        display_path: str,
        mime_type: str,
        modified_time: str | None,
        web_view_link: str | None,
    ) -> dict[str, Any]:
        job = self.get_job(job_id)
        if job is None:
            raise ValueError("unknown Drive ingestion job")
        if job["status"] not in {"completed", "completed_with_errors", "paused"}:
            raise ValueError("Drive ingestion job is not ready for an explicit source")
        self._discover_source(
            job_id=job_id,
            drive_id=drive_id,
            parent_id=parent_id,
            path=display_path,
            name=name,
            mime_type=mime_type,
            modified_time=modified_time,
            web_view_link=web_view_link,
            generation=int(job["generation"]),
            scope="explicit_global",
        )
        self._set_status(job_id, "pending")
        source = next(
            row for row in self.list_sources(job_id) if row["drive_id"] == drive_id
        )
        return source

    def propose_board_attachment(
        self,
        job_id: str,
        *,
        source_drive_id: str,
        person_id: str,
        record_type: str,
        fields: dict[str, Any],
    ) -> dict[str, Any]:
        if record_type not in {"artifact", "knowledge_gap", "source_ref"}:
            raise ValueError("unsupported Board proposal type")
        job = self.get_job(job_id)
        if job is None or job["status"] not in {"completed", "completed_with_errors"}:
            raise ValueError("Drive ingestion index is not complete")
        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM drive_ingestion_sources
                WHERE job_id = ? AND drive_id = ? AND deleted = 0
                """,
                (job_id, source_drive_id),
            ).fetchone()
        if row is None:
            raise ValueError("Drive ingestion source is unavailable")
        source = self._source_dict(row)
        attached_fields = {
            **fields,
            "drive_id": source["drive_id"],
            "path": source["path"],
            "mime_type": source["mime_type"],
            "modified_time": source["modified_time"],
            "sensitivity": source["sensitivity"],
            "source_refs": source["citations"],
        }
        proposal_id = f"drive_proposal_{uuid.uuid4().hex}"
        diff = {
            "operation": "board_upsert",
            "person_id": person_id,
            "record_type": record_type,
            "before": None,
            "after": attached_fields,
        }
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO drive_ingestion_proposals
                    (id, job_id, source_drive_id, person_id, record_type,
                     fields_json, diff_json, source_refs_json, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending_review', ?)
                """,
                (
                    proposal_id,
                    job_id,
                    source_drive_id,
                    person_id,
                    record_type,
                    json.dumps(attached_fields, sort_keys=True),
                    json.dumps(diff, sort_keys=True),
                    json.dumps(source["citations"], sort_keys=True),
                    _now(),
                ),
            )
            self._conn.commit()
        proposal = self.get_board_proposal(proposal_id)
        assert proposal is not None
        return proposal

    def get_board_proposal(self, proposal_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM drive_ingestion_proposals WHERE id = ?",
                (proposal_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "job_id": row["job_id"],
            "source_drive_id": row["source_drive_id"],
            "person_id": row["person_id"],
            "record_type": row["record_type"],
            "fields": json.loads(row["fields_json"]),
            "diff": json.loads(row["diff_json"]),
            "source_refs": json.loads(row["source_refs_json"]),
            "status": row["status"],
            "reviewed_at": row["reviewed_at"],
            "reviewer": row["reviewer"],
        }

    def list_board_proposals(self, job_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id FROM drive_ingestion_proposals
                WHERE job_id = ? ORDER BY created_at, id
                """,
                (job_id,),
            ).fetchall()
        proposals: list[dict[str, Any]] = []
        for row in rows:
            proposal = self.get_board_proposal(str(row["id"]))
            if proposal is not None:
                proposals.append(proposal)
        return proposals

    def apply_board_proposal(
        self, proposal_id: str, *, people: Any, actor: str
    ) -> dict[str, Any]:
        from coworker.board_tools import execute_board_tool

        proposal = self.get_board_proposal(proposal_id)
        if proposal is None:
            raise ValueError("unknown Drive ingestion proposal")
        if proposal["status"] == "applied":
            return proposal
        if proposal["status"] != "pending_review":
            raise ValueError("Drive ingestion proposal is not pending review")
        ok, _result = execute_board_tool(
            "board_upsert",
            {
                "person_id": proposal["person_id"],
                "record_type": proposal["record_type"],
                "fields": proposal["fields"],
                "idempotency_key": proposal["id"],
                "rationale_summary": "Apply reviewed Drive ingestion proposal.",
            },
            people=people,
            actor=actor,
            session_id=f"drive-ingestion:{proposal['job_id']}",
            run_id=proposal["job_id"],
            allowed_source_ids=None,
        )
        if not ok:
            raise ValueError("Board proposal could not be applied")
        with self._lock:
            self._conn.execute(
                """
                UPDATE drive_ingestion_proposals
                SET status = 'applied', reviewed_at = ?, reviewer = ?
                WHERE id = ?
                """,
                (_now(), actor, proposal_id),
            )
            self._conn.commit()
        applied = self.get_board_proposal(proposal_id)
        assert applied is not None
        return applied

    def list_sources(self, job_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM drive_ingestion_sources
                WHERE job_id = ? ORDER BY path, drive_id
                """,
                (job_id,),
            ).fetchall()
        return [self._source_dict(row) for row in rows]

    def list_source_receipts(self, job_id: str) -> list[dict[str, Any]]:
        receipts: list[dict[str, Any]] = []
        for source in self.list_sources(job_id):
            receipts.append(
                {
                    key: value
                    for key, value in source.items()
                    if key not in {"content", "source_safety"}
                }
            )
        return receipts

    def query(
        self,
        job_id: str,
        query: str,
        *,
        include_external: bool = False,
        limit: int = 20,
    ) -> dict[str, Any]:
        job = self.get_job(job_id)
        if job is None:
            raise ValueError("unknown Drive ingestion job")
        if job["status"] not in {"completed", "completed_with_errors"}:
            raise ValueError("Drive ingestion index is not complete")
        needle = query.strip()
        if not needle:
            raise ValueError("query is required")
        scope_clause = "" if include_external else "AND scope = 'tree'"
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT * FROM drive_ingestion_sources
                WHERE job_id = ? AND deleted = 0 AND sensitivity != 'restricted'
                  {scope_clause}
                  AND lower(path || ' ' || name || ' ' || COALESCE(content, ''))
                      LIKE lower(?)
                ORDER BY path, drive_id LIMIT ?
                """,
                (job_id, f"%{needle}%", max(1, min(int(limit), 100))),
            ).fetchall()
        matches: list[dict[str, Any]] = []
        for row in rows:
            source = self._source_dict(row)
            matches.append(
                {
                    "drive_id": source["drive_id"],
                    "path": source["path"],
                    "mime_type": source["mime_type"],
                    "modified_time": source["modified_time"],
                    "sensitivity": source["sensitivity"],
                    "extraction_status": source["extraction_status"],
                    "scope": source["scope"],
                    "out_of_scope": source["scope"] != "tree",
                    "snippet": str(source.get("content") or "")[:500],
                    "citations": source["citations"],
                }
            )
        return {"job_id": job_id, "query": needle, "matches": matches}

    def _source_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "drive_id": row["drive_id"],
            "scope": row["scope"],
            "parent_id": row["parent_id"],
            "path": row["path"],
            "name": row["name"],
            "mime_type": row["mime_type"],
            "modified_time": row["modified_time"],
            "sensitivity": row["sensitivity"],
            "extraction_status": row["extraction_status"],
            "content": row["content"],
            "citations": json.loads(row["citations_json"] or "[]"),
            "redaction_count": int(row["redaction_count"] or 0),
            "source_safety": (
                json.loads(row["source_safety_json"])
                if row["source_safety_json"]
                else None
            ),
            "deleted": bool(row["deleted"]),
            "last_action": row["last_action"],
            "error_kind": row["error_kind"],
        }

    def _set_status(self, job_id: str, status: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE drive_ingestion_jobs SET status = ?, updated_at = ? WHERE id = ?",
                (status, _now(), job_id),
            )
            self._conn.commit()

    def _begin_run(self, job_id: str) -> None:
        with self._lock:
            self._conn.execute(
                """
                UPDATE drive_ingestion_jobs
                SET status = 'running', cancel_requested = 0, updated_at = ?
                WHERE id = ?
                """,
                (_now(), job_id),
            )
            self._conn.commit()

    def request_cancel(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            changed = self._conn.execute(
                """
                UPDATE drive_ingestion_jobs
                SET status = 'cancel_requested', cancel_requested = 1, updated_at = ?
                WHERE id = ? AND status IN ('pending', 'running')
                """,
                (_now(), job_id),
            ).rowcount
            self._conn.commit()
        if not changed:
            job = self.get_job(job_id)
            if job is None:
                raise ValueError("unknown Drive ingestion job")
            return job
        job = self.get_job(job_id)
        assert job is not None
        return job

    def _cancel_was_requested(self, job_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT cancel_requested FROM drive_ingestion_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        return bool(row and row["cancel_requested"])

    def _pause(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            self._conn.execute(
                """
                UPDATE drive_ingestion_jobs
                SET status = 'paused', cancel_requested = 0, updated_at = ?
                WHERE id = ?
                """,
                (_now(), job_id),
            )
            self._conn.commit()
        job = self.get_job(job_id)
        assert job is not None
        return job

    def _next_folder(self, job_id: str) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(
                """
                SELECT * FROM drive_ingestion_folders
                WHERE job_id = ? AND status = 'pending'
                ORDER BY path, drive_id LIMIT 1
                """,
                (job_id,),
            ).fetchone()

    def _discover_folder(
        self,
        *,
        job_id: str,
        drive_id: str,
        parent_id: str,
        path: str,
        generation: int,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO drive_ingestion_folders
                    (job_id, drive_id, parent_id, path, status, generation)
                VALUES (?, ?, ?, ?, 'pending', ?)
                ON CONFLICT(job_id, drive_id) DO UPDATE SET
                    parent_id = excluded.parent_id,
                    path = excluded.path,
                    generation = excluded.generation
                """,
                (job_id, drive_id, parent_id, path, generation),
            )
            self._conn.commit()

    def _checkpoint_folder(
        self, job_id: str, drive_id: str, next_page_token: str | None
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                UPDATE drive_ingestion_folders
                SET status = ?, page_token = ?, error_kind = NULL
                WHERE job_id = ? AND drive_id = ?
                """,
                (
                    "pending" if next_page_token else "listed",
                    next_page_token,
                    job_id,
                    drive_id,
                ),
            )
            self._conn.commit()

    def _fail_folder(self, job_id: str, drive_id: str) -> None:
        with self._lock:
            self._conn.execute(
                """
                UPDATE drive_ingestion_folders
                SET status = 'failed', error_kind = 'folder_list_failed'
                WHERE job_id = ? AND drive_id = ?
                """,
                (job_id, drive_id),
            )
            self._conn.commit()

    def _has_folder_failures(self, job_id: str, generation: int) -> bool:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT 1 FROM drive_ingestion_folders
                WHERE job_id = ? AND generation = ? AND status = 'failed'
                LIMIT 1
                """,
                (job_id, generation),
            ).fetchone()
        return row is not None

    def _discover_source(
        self,
        *,
        job_id: str,
        drive_id: str,
        parent_id: str,
        path: str,
        name: str,
        mime_type: str,
        modified_time: str | None,
        web_view_link: str | None,
        generation: int,
        scope: str = "tree",
    ) -> None:
        with self._lock:
            existing = self._conn.execute(
                """
                SELECT modified_time, extraction_status, deleted
                FROM drive_ingestion_sources WHERE job_id = ? AND drive_id = ?
                """,
                (job_id, drive_id),
            ).fetchone()
            unchanged = bool(
                existing is not None
                and existing["modified_time"] == modified_time
                and existing["extraction_status"] not in {"failed", "pending", "deleted"}
                and not existing["deleted"]
            )
            self._conn.execute(
                """
                INSERT INTO drive_ingestion_sources
                    (job_id, drive_id, scope, parent_id, path, name, mime_type,
                     modified_time, web_view_link, generation, last_action, needs_read)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 1)
                ON CONFLICT(job_id, drive_id) DO UPDATE SET
                    scope = excluded.scope,
                    parent_id = excluded.parent_id,
                    path = excluded.path,
                    name = excluded.name,
                    mime_type = excluded.mime_type,
                    modified_time = excluded.modified_time,
                    web_view_link = excluded.web_view_link,
                    generation = excluded.generation,
                    deleted = 0,
                    error_kind = NULL,
                    last_action = ?,
                    needs_read = ?
                """,
                (
                    job_id,
                    drive_id,
                    scope,
                    parent_id,
                    path,
                    name,
                    mime_type,
                    modified_time,
                    web_view_link,
                    generation,
                    "skipped" if unchanged else "pending",
                    0 if unchanged else 1,
                ),
            )
            self._conn.commit()

    def _next_source(self, job_id: str) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(
                """
                SELECT * FROM drive_ingestion_sources
                WHERE job_id = ? AND needs_read = 1
                ORDER BY path, drive_id LIMIT 1
                """,
                (job_id,),
            ).fetchone()

    def _record_source(self, job_id: str, drive_id: str, result: dict[str, Any]) -> None:
        status = str(result.get("status") or "failed")
        if status not in {"read", "truncated", "metadata_only", "unsupported", "failed"}:
            status = "failed"
        if status in {"read", "truncated"}:
            action = "read"
        elif status in {"metadata_only", "unsupported"}:
            action = "metadata_only"
        else:
            action = "failed"
        sensitivity = "standard"
        if result.get("sensitive_content_redacted"):
            sensitivity = "restricted"
        elif result.get("source_safety"):
            sensitivity = "sensitive"
        with self._lock:
            row = self._conn.execute(
                """
                SELECT path, name, web_view_link FROM drive_ingestion_sources
                WHERE job_id = ? AND drive_id = ?
                """,
                (job_id, drive_id),
            ).fetchone()
            path = str(row["path"] if row is not None else "")
            citations = []
            for source in result.get("sources") or []:
                if not isinstance(source, dict):
                    continue
                citations.append({**source, "path": path})
            if not citations and row is not None:
                citations.append(
                    {
                        "id": drive_id,
                        "title": row["name"],
                        "url": row["web_view_link"],
                        "provider": "Google Drive",
                        "truncated": status == "truncated",
                        "path": path,
                    }
                )
            self._conn.execute(
                """
                UPDATE drive_ingestion_sources SET
                    sensitivity = ?, extraction_status = ?, content = ?,
                    citations_json = ?, redaction_count = ?, source_safety_json = ?,
                    last_action = ?, error_kind = ?, needs_read = 0
                WHERE job_id = ? AND drive_id = ?
                """,
                (
                    sensitivity,
                    status,
                    result.get("content") if status in {"read", "truncated"} else None,
                    json.dumps(citations, sort_keys=True),
                    int(result.get("redaction_count") or 0),
                    (
                        json.dumps(result.get("source_safety"), sort_keys=True)
                        if isinstance(result.get("source_safety"), dict)
                        else None
                    ),
                    action,
                    "source_read_failed" if action == "failed" else None,
                    job_id,
                    drive_id,
                ),
            )
            self._conn.commit()

    def _mark_missing_deleted(self, job_id: str, generation: int) -> None:
        with self._lock:
            self._conn.execute(
                """
                UPDATE drive_ingestion_sources SET
                    generation = ?, deleted = 1, extraction_status = 'deleted',
                    content = NULL, last_action = 'deleted', needs_read = 0,
                    error_kind = NULL
                WHERE job_id = ? AND scope = 'tree' AND generation < ?
                """,
                (generation, job_id, generation),
            )
            self._conn.commit()


class DriveIngestionRunner:
    def __init__(self, store: DriveIngestionStore, drive: Any) -> None:
        self.store = store
        self.drive = drive

    def run(self, job_id: str) -> dict[str, Any]:
        job = self.store.get_job(job_id)
        if job is None:
            raise ValueError("unknown Drive ingestion job")
        if self.store._cancel_was_requested(job_id):
            return self.store._pause(job_id)
        generation = int(job["generation"])
        self.store._begin_run(job_id)

        while folder := self.store._next_folder(job_id):
            try:
                page = self.drive.list_folder(
                    folder["drive_id"],
                    max_results=1000,
                    page_token=folder["page_token"],
                )
            except Exception:
                self.store._fail_folder(job_id, folder["drive_id"])
                continue
            for raw in page.get("files") or []:
                if not isinstance(raw, dict):
                    continue
                drive_id = str(raw.get("id") or "").strip()
                name = str(raw.get("name") or "").strip()
                if not drive_id or not name:
                    continue
                path = f"{folder['path'].rstrip('/')}/{name}"
                mime_type = str(raw.get("mimeType") or "")
                if mime_type == "application/vnd.google-apps.folder":
                    self.store._discover_folder(
                        job_id=job_id,
                        drive_id=drive_id,
                        parent_id=folder["drive_id"],
                        path=path,
                        generation=generation,
                    )
                else:
                    self.store._discover_source(
                        job_id=job_id,
                        drive_id=drive_id,
                        parent_id=folder["drive_id"],
                        path=path,
                        name=name,
                        mime_type=mime_type,
                        modified_time=raw.get("modifiedTime"),
                        web_view_link=raw.get("webViewLink"),
                        generation=generation,
                    )
            next_page_token = str(page.get("nextPageToken") or "") or None
            self.store._checkpoint_folder(
                job_id, folder["drive_id"], next_page_token
            )
            if self.store._cancel_was_requested(job_id):
                return self.store._pause(job_id)

        while source := self.store._next_source(job_id):
            try:
                result = self.drive.read(source["drive_id"], max_chars=20000)
            except Exception:
                result = {"status": "failed"}
            self.store._record_source(job_id, source["drive_id"], result)
            if self.store._cancel_was_requested(job_id):
                return self.store._pause(job_id)

        if not self.store._has_folder_failures(job_id, generation):
            self.store._mark_missing_deleted(job_id, generation)
        progress = self.store.get_job(job_id)["progress"]
        self.store._set_status(
            job_id, "completed_with_errors" if progress["failed"] else "completed"
        )
        completed = self.store.get_job(job_id)
        assert completed is not None
        return completed


class DriveIngestionCoordinator:
    def __init__(
        self,
        store: DriveIngestionStore,
        drive_factory: Callable[[], Any],
    ) -> None:
        self.store = store
        self.drive_factory = drive_factory
        self._tasks: dict[str, asyncio.Task[dict[str, Any]]] = {}

    async def start(self, job_id: str) -> asyncio.Task[dict[str, Any]]:
        existing = self._tasks.get(job_id)
        if existing is not None and not existing.done():
            return existing
        drive = self.drive_factory()
        if drive is None:
            raise ValueError("Drive is not connected")
        task = asyncio.create_task(
            asyncio.to_thread(DriveIngestionRunner(self.store, drive).run, job_id)
        )
        self._tasks[job_id] = task
        return task

    async def wait(self, job_id: str) -> dict[str, Any]:
        task = self._tasks.get(job_id)
        if task is None:
            job = self.store.get_job(job_id)
            if job is None:
                raise ValueError("unknown Drive ingestion job")
            return job
        return await task

    def cancel(self, job_id: str) -> dict[str, Any]:
        return self.store.request_cancel(job_id)
