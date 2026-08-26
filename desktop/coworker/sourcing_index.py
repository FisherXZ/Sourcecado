"""Durable Board and sourcing-index domain records."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

RECORD_TYPES = frozenset(
    {
        "semester",
        "company",
        "contact",
        "opportunity",
        "touchpoint",
        "action",
        "knowledge_gap",
        "artifact",
        "source_ref",
    }
)
OPPORTUNITY_TRANSITIONS = {
    "research": frozenset({"ready", "closed"}),
    "ready": frozenset({"contacted", "closed"}),
    "contacted": frozenset({"replied", "closed"}),
    "replied": frozenset({"qualified", "closed"}),
    "qualified": frozenset({"closed"}),
    "closed": frozenset(),
}


class SourcingIndex:
    def __init__(self, base_dir: str | Path) -> None:
        self.base = Path(base_dir).expanduser()
        self.base.mkdir(parents=True, exist_ok=True)
        self.db_path = self.base / "sourcing-index.db"
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sourcing_records (
                id TEXT PRIMARY KEY,
                record_type TEXT NOT NULL,
                fields_json TEXT NOT NULL,
                source_refs_json TEXT NOT NULL DEFAULT '[]',
                idempotency_key TEXT,
                version INTEGER NOT NULL DEFAULT 1,
                restricted INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(record_type, idempotency_key)
            );
            CREATE TABLE IF NOT EXISTS sourcing_receipts (
                id TEXT PRIMARY KEY,
                record_id TEXT NOT NULL,
                operation TEXT NOT NULL,
                before_json TEXT,
                after_json TEXT,
                actor TEXT NOT NULL,
                session_id TEXT,
                run_id TEXT,
                rationale_summary TEXT NOT NULL,
                source_refs_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sourcing_links (
                from_id TEXT NOT NULL,
                to_id TEXT NOT NULL,
                relationship TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (from_id, to_id, relationship)
            );
            """
        )
        self._conn.commit()

    @staticmethod
    def _record(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": str(row["id"]),
            "type": str(row["record_type"]),
            "fields": json.loads(str(row["fields_json"])),
            "source_refs": json.loads(str(row["source_refs_json"])),
            "version": int(row["version"]),
            "restricted": bool(row["restricted"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }

    @staticmethod
    def _require_audit(actor: str, rationale_summary: str) -> None:
        if not actor.strip() or not rationale_summary.strip():
            raise ValueError("actor and rationale_summary are required")

    def get(
        self,
        record_id: str,
        *,
        expand_sources: bool = False,
        allowed_source_ids: set[str] | None = None,
    ) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM sourcing_records WHERE id = ?", (record_id,)
            ).fetchone()
            if row is None:
                return None
            record = self._record(row)
            allowed = allowed_source_ids or set()
            if record["restricted"] and record["id"] not in allowed:
                return None
            if not expand_sources:
                return record
            sources: list[dict[str, Any]] = []
            restricted_count = 0
            for source_id in record["source_refs"]:
                source_row = self._conn.execute(
                    "SELECT * FROM sourcing_records WHERE id = ? AND record_type = 'source_ref'",
                    (source_id,),
                ).fetchone()
                if source_row is None:
                    continue
                source = self._record(source_row)
                if source["restricted"] and source["id"] not in allowed:
                    restricted_count += 1
                    continue
                sources.append(source)
        return {
            **record,
            "sources": sources,
            "restricted_source_count": restricted_count,
        }

    def query(
        self,
        *,
        record_type: str | None = None,
        filters: dict[str, Any] | None = None,
        expand_sources: bool = False,
        allowed_source_ids: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        if record_type is not None and record_type not in RECORD_TYPES:
            raise ValueError(f"unknown record type {record_type}")
        with self._lock:
            if record_type is None:
                rows = self._conn.execute(
                    "SELECT * FROM sourcing_records ORDER BY updated_at DESC, id"
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """
                    SELECT * FROM sourcing_records
                    WHERE record_type = ? ORDER BY updated_at DESC, id
                    """,
                    (record_type,),
                ).fetchall()
        expected = filters or {}
        allowed = allowed_source_ids or set()
        records = [
            self._record(row)
            for row in rows
            if not bool(row["restricted"]) or str(row["id"]) in allowed
        ]
        matched = [
            record
            for record in records
            if all(record["fields"].get(key) == value for key, value in expected.items())
        ]
        if not expand_sources:
            return matched
        return [
            expanded
            for record in matched
            if (
                expanded := self.get(
                    record["id"],
                    expand_sources=True,
                    allowed_source_ids=allowed,
                )
            )
            is not None
        ]

    def _append_receipt(
        self,
        *,
        record_id: str,
        operation: str,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
        actor: str,
        session_id: str | None,
        run_id: str | None,
        rationale_summary: str,
        source_refs: list[str],
        created_at: str,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO sourcing_receipts (
                id, record_id, operation, before_json, after_json, actor,
                session_id, run_id, rationale_summary, source_refs_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"receipt_{uuid.uuid4().hex}",
                record_id,
                operation,
                json.dumps(before, sort_keys=True) if before is not None else None,
                json.dumps(after, sort_keys=True) if after is not None else None,
                actor,
                session_id,
                run_id,
                rationale_summary,
                json.dumps(source_refs),
                created_at,
            ),
        )

    def upsert(
        self,
        *,
        record_type: str,
        fields: dict[str, Any],
        idempotency_key: str,
        actor: str,
        rationale_summary: str,
        session_id: str | None = None,
        run_id: str | None = None,
        source_refs: list[str] | None = None,
    ) -> dict[str, Any]:
        if record_type not in RECORD_TYPES:
            raise ValueError(f"unknown record type {record_type}")
        if not isinstance(fields, dict):
            raise ValueError("fields must be an object")
        if not idempotency_key.strip():
            raise ValueError("idempotency_key is required")
        self._require_audit(actor, rationale_summary)
        refs = [str(value) for value in source_refs or [] if str(value)]
        with self._lock:
            existing = self._conn.execute(
                "SELECT * FROM sourcing_records WHERE record_type = ? AND idempotency_key = ?",
                (record_type, idempotency_key),
            ).fetchone()
            if existing is not None:
                current = self._record(existing)
                if current["fields"] != fields or current["source_refs"] != refs:
                    raise ValueError(
                        "idempotency conflict: key already belongs to different record facts"
                    )
                return current
            now = datetime.now(UTC).isoformat()
            record_id = f"{record_type}_{uuid.uuid4().hex}"
            restricted = int(
                record_type == "source_ref"
                and str(fields.get("sensitivity") or "").lower() == "restricted"
            )
            self._conn.execute(
                """
                INSERT INTO sourcing_records (
                    id, record_type, fields_json, source_refs_json,
                    idempotency_key, version, restricted, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (
                    record_id,
                    record_type,
                    json.dumps(fields, sort_keys=True),
                    json.dumps(refs),
                    idempotency_key,
                    restricted,
                    now,
                    now,
                ),
            )
            row = self._conn.execute(
                "SELECT * FROM sourcing_records WHERE id = ?", (record_id,)
            ).fetchone()
            assert row is not None
            record = self._record(row)
            self._append_receipt(
                record_id=record_id,
                operation="create",
                before=None,
                after=record,
                actor=actor,
                session_id=session_id,
                run_id=run_id,
                rationale_summary=rationale_summary,
                source_refs=refs,
                created_at=now,
            )
            self._conn.commit()
        return record

    def patch(
        self,
        record_id: str,
        *,
        fields: dict[str, Any],
        expected_version: int,
        actor: str,
        rationale_summary: str,
        session_id: str | None = None,
        run_id: str | None = None,
        source_refs: list[str] | None = None,
        _operation: str = "patch",
    ) -> dict[str, Any]:
        if not isinstance(fields, dict):
            raise ValueError("fields must be an object")
        self._require_audit(actor, rationale_summary)
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM sourcing_records WHERE id = ?", (record_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"unknown record {record_id}")
            before = self._record(row)
            if before["version"] != expected_version:
                raise ValueError(
                    f"stale record version: expected {expected_version}, current {before['version']}"
                )
            next_fields = dict(before["fields"])
            next_fields.update(fields)
            refs = list(dict.fromkeys([*before["source_refs"], *(source_refs or [])]))
            now = datetime.now(UTC).isoformat()
            restricted = int(
                before["type"] == "source_ref"
                and str(next_fields.get("sensitivity") or "").lower() == "restricted"
            )
            cursor = self._conn.execute(
                """
                UPDATE sourcing_records
                SET fields_json = ?, source_refs_json = ?, version = version + 1,
                    restricted = ?, updated_at = ?
                WHERE id = ? AND version = ?
                """,
                (
                    json.dumps(next_fields, sort_keys=True),
                    json.dumps(refs),
                    restricted,
                    now,
                    record_id,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                self._conn.rollback()
                raise ValueError("stale record version")
            updated_row = self._conn.execute(
                "SELECT * FROM sourcing_records WHERE id = ?", (record_id,)
            ).fetchone()
            assert updated_row is not None
            after = self._record(updated_row)
            self._append_receipt(
                record_id=record_id,
                operation=_operation,
                before=before,
                after=after,
                actor=actor,
                session_id=session_id,
                run_id=run_id,
                rationale_summary=rationale_summary,
                source_refs=[str(value) for value in source_refs or []],
                created_at=now,
            )
            self._conn.commit()
        return after

    def transition(
        self,
        record_id: str,
        *,
        to_stage: str,
        evidence_record_ids: list[str],
        expected_version: int,
        actor: str,
        rationale_summary: str,
        session_id: str | None = None,
        run_id: str | None = None,
        source_refs: list[str] | None = None,
    ) -> dict[str, Any]:
        record = self.get(record_id)
        if record is None or record["type"] != "opportunity":
            raise ValueError("stage transitions require an opportunity")
        current = str(record["fields"].get("stage") or "research")
        if to_stage not in OPPORTUNITY_TRANSITIONS.get(current, frozenset()):
            raise ValueError(f"invalid opportunity transition {current} -> {to_stage}")
        evidence = [self.get(evidence_id) for evidence_id in evidence_record_ids]
        evidence = [item for item in evidence if item is not None]
        if to_stage == "contacted" and not any(
            item["type"] == "touchpoint"
            and item["fields"].get("direction") == "outbound"
            and item["fields"].get("status") in {"sent", "completed"}
            for item in evidence
        ):
            raise ValueError("contacted requires an outbound sent touchpoint")
        if to_stage == "replied" and not any(
            item["type"] == "touchpoint"
            and item["fields"].get("direction") == "inbound"
            and item["fields"].get("status") in {"received", "replied", "completed"}
            for item in evidence
        ):
            raise ValueError("replied requires an inbound reply touchpoint")
        if to_stage == "qualified" and not any(
            item["type"] == "touchpoint"
            and (
                item["fields"].get("qualified") is True
                or item["fields"].get("status") == "qualified"
            )
            for item in evidence
        ):
            raise ValueError("qualified requires a qualified touchpoint")
        return self.patch(
            record_id,
            fields={"stage": to_stage, "stage_evidence_ids": evidence_record_ids},
            expected_version=expected_version,
            actor=actor,
            session_id=session_id,
            run_id=run_id,
            rationale_summary=rationale_summary,
            source_refs=source_refs,
            _operation="transition",
        )

    def complete_action(
        self,
        record_id: str,
        *,
        expected_version: int,
        actor: str,
        rationale_summary: str,
        session_id: str | None = None,
        run_id: str | None = None,
        source_refs: list[str] | None = None,
    ) -> dict[str, Any]:
        record = self.get(record_id)
        if record is None or record["type"] != "action":
            raise ValueError("action completion requires an action")
        return self.patch(
            record_id,
            fields={"status": "completed", "completed_at": datetime.now(UTC).isoformat()},
            expected_version=expected_version,
            actor=actor,
            session_id=session_id,
            run_id=run_id,
            rationale_summary=rationale_summary,
            source_refs=source_refs,
            _operation="complete_action",
        )

    def capture_outcome(
        self,
        record_id: str,
        *,
        outcome: str,
        expected_version: int,
        actor: str,
        rationale_summary: str,
        session_id: str | None = None,
        run_id: str | None = None,
        source_refs: list[str] | None = None,
    ) -> dict[str, Any]:
        record = self.get(record_id)
        if record is None or record["type"] != "opportunity":
            raise ValueError("outcome capture requires an opportunity")
        if not outcome.strip():
            raise ValueError("outcome is required")
        return self.patch(
            record_id,
            fields={"outcome": outcome.strip(), "outcome_at": datetime.now(UTC).isoformat()},
            expected_version=expected_version,
            actor=actor,
            session_id=session_id,
            run_id=run_id,
            rationale_summary=rationale_summary,
            source_refs=source_refs,
            _operation="capture_outcome",
        )

    def revert(
        self,
        record_id: str,
        *,
        to_version: int,
        expected_version: int,
        actor: str,
        rationale_summary: str,
        session_id: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        self._require_audit(actor, rationale_summary)
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM sourcing_records WHERE id = ?", (record_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"unknown record {record_id}")
            before = self._record(row)
            if before["version"] != expected_version:
                raise ValueError(
                    f"stale record version: expected {expected_version}, current {before['version']}"
                )
            target = next(
                (
                    receipt["after"]
                    for receipt in self.receipts(record_id)
                    if receipt["after"] is not None
                    and receipt["after"].get("version") == to_version
                ),
                None,
            )
            if target is None:
                raise ValueError(f"unknown record version {to_version}")
            now = datetime.now(UTC).isoformat()
            next_version = expected_version + 1
            self._conn.execute(
                """
                UPDATE sourcing_records
                SET fields_json = ?, source_refs_json = ?, version = ?, restricted = ?, updated_at = ?
                WHERE id = ? AND version = ?
                """,
                (
                    json.dumps(target["fields"], sort_keys=True),
                    json.dumps(target["source_refs"]),
                    next_version,
                    int(bool(target["restricted"])),
                    now,
                    record_id,
                    expected_version,
                ),
            )
            after_row = self._conn.execute(
                "SELECT * FROM sourcing_records WHERE id = ?", (record_id,)
            ).fetchone()
            assert after_row is not None
            after = self._record(after_row)
            self._append_receipt(
                record_id=record_id,
                operation="revert",
                before=before,
                after=after,
                actor=actor,
                session_id=session_id,
                run_id=run_id,
                rationale_summary=rationale_summary,
                source_refs=[],
                created_at=now,
            )
            self._conn.commit()
        return after

    def delete(
        self,
        record_id: str,
        *,
        expected_version: int,
        actor: str,
        rationale_summary: str,
        session_id: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        self._require_audit(actor, rationale_summary)
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM sourcing_records WHERE id = ?", (record_id,)
            ).fetchone()
            if row is None:
                return {"deleted": False, "id": record_id}
            before = self._record(row)
            if before["version"] != expected_version:
                raise ValueError(
                    f"stale record version: expected {expected_version}, current {before['version']}"
                )
            self._conn.execute(
                "DELETE FROM sourcing_links WHERE from_id = ? OR to_id = ?",
                (record_id, record_id),
            )
            self._conn.execute("DELETE FROM sourcing_records WHERE id = ?", (record_id,))
            now = datetime.now(UTC).isoformat()
            self._append_receipt(
                record_id=record_id,
                operation="delete",
                before=before,
                after=None,
                actor=actor,
                session_id=session_id,
                run_id=run_id,
                rationale_summary=rationale_summary,
                source_refs=[],
                created_at=now,
            )
            self._conn.commit()
        return {"deleted": True, "id": record_id}

    def links(self, record_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT from_id, to_id, relationship, created_at
                FROM sourcing_links
                WHERE from_id = ? OR to_id = ?
                ORDER BY created_at, from_id, to_id, relationship
                """,
                (record_id, record_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def link(
        self,
        from_id: str,
        to_id: str,
        *,
        relationship: str,
        actor: str,
        rationale_summary: str,
        session_id: str | None = None,
        run_id: str | None = None,
        source_refs: list[str] | None = None,
    ) -> dict[str, Any]:
        if not relationship.strip():
            raise ValueError("relationship is required")
        self._require_audit(actor, rationale_summary)
        with self._lock:
            count = self._conn.execute(
                "SELECT COUNT(*) FROM sourcing_records WHERE id IN (?, ?)",
                (from_id, to_id),
            ).fetchone()[0]
            if int(count) != 2:
                raise ValueError("link records must exist")
            existing = self._conn.execute(
                """
                SELECT from_id, to_id, relationship, created_at FROM sourcing_links
                WHERE from_id = ? AND to_id = ? AND relationship = ?
                """,
                (from_id, to_id, relationship),
            ).fetchone()
            if existing is not None:
                return dict(existing)
            now = datetime.now(UTC).isoformat()
            self._conn.execute(
                """
                INSERT INTO sourcing_links (from_id, to_id, relationship, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (from_id, to_id, relationship, now),
            )
            link = {
                "from_id": from_id,
                "to_id": to_id,
                "relationship": relationship,
                "created_at": now,
            }
            self._append_receipt(
                record_id=from_id,
                operation="link",
                before=None,
                after=link,
                actor=actor,
                session_id=session_id,
                run_id=run_id,
                rationale_summary=rationale_summary,
                source_refs=[str(value) for value in source_refs or []],
                created_at=now,
            )
            self._conn.commit()
        return link

    def unlink(
        self,
        from_id: str,
        to_id: str,
        *,
        relationship: str,
        actor: str,
        rationale_summary: str,
        session_id: str | None = None,
        run_id: str | None = None,
        source_refs: list[str] | None = None,
    ) -> dict[str, Any]:
        self._require_audit(actor, rationale_summary)
        with self._lock:
            existing = self._conn.execute(
                """
                SELECT from_id, to_id, relationship, created_at FROM sourcing_links
                WHERE from_id = ? AND to_id = ? AND relationship = ?
                """,
                (from_id, to_id, relationship),
            ).fetchone()
            if existing is None:
                return {"removed": False}
            before = dict(existing)
            self._conn.execute(
                """
                DELETE FROM sourcing_links
                WHERE from_id = ? AND to_id = ? AND relationship = ?
                """,
                (from_id, to_id, relationship),
            )
            now = datetime.now(UTC).isoformat()
            self._append_receipt(
                record_id=from_id,
                operation="unlink",
                before=before,
                after=None,
                actor=actor,
                session_id=session_id,
                run_id=run_id,
                rationale_summary=rationale_summary,
                source_refs=[str(value) for value in source_refs or []],
                created_at=now,
            )
            self._conn.commit()
        return {"removed": True}

    def receipts(self, record_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM sourcing_receipts WHERE record_id = ? ORDER BY created_at, id",
                (record_id,),
            ).fetchall()
        return [
            {
                "id": str(row["id"]),
                "record_id": str(row["record_id"]),
                "operation": str(row["operation"]),
                "before": json.loads(str(row["before_json"])) if row["before_json"] else None,
                "after": json.loads(str(row["after_json"])) if row["after_json"] else None,
                "actor": str(row["actor"]),
                "session_id": row["session_id"],
                "run_id": row["run_id"],
                "rationale_summary": str(row["rationale_summary"]),
                "source_refs": json.loads(str(row["source_refs_json"])),
                "created_at": str(row["created_at"]),
            }
            for row in rows
        ]
