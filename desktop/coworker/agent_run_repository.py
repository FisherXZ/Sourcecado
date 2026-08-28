"""The durable authority for Agent Run identity, ownership, and progress.

One run identity is shared by chat, queued chat, and scheduled work. Exactly one
process may drive a run at a time, and it proves that right with a lease. Every
durable write is a compare-and-swap against `(run_id, version, lease_owner)`:
the version is the fencing token, so a superseded or duplicated owner writes
nothing instead of writing second.

The store is its own SQLite database with its own `SCHEMA_VERSION`, stamped in
`PRAGMA user_version` for the migration registry to read.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Iterator

from coworker.agent_run_owner import Liveness, OwnerRegistry, RunOwner
from coworker.agent_run_state import (
    is_leasable,
    is_terminal,
    is_waiting,
    validate_transition,
)
from coworker.agent_runs import (
    CHECKPOINT_KINDS,
    INCOMPLETE_RECORD_KINDS,
    RUN_TRIGGERS,
    add_usage,
    checkpoint_payload,
    goal_fingerprint,
    json_list,
    json_object,
    merge_unique_refs,
    merge_unique_strings,
    nullable_json_object,
    project_artifact_refs,
    project_source_refs,
    project_terminal_result,
)

SCHEMA_VERSION = 1
DB_NAME = "agent_runs.db"
# Long enough for a slow provider call, short enough that crashed work is not
# stranded for an operator's whole afternoon.
DEFAULT_LEASE_SECONDS = 120
MAX_LEASE_SECONDS = 60 * 60
_MAX_ID = 256
_SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_STAMP_FORMAT = "%Y-%m-%dT%H:%M:%S.%f+00:00"


class AgentRunVersionConflict(RuntimeError):
    """The caller's compare-and-swap version is no longer the current one."""


class AgentRunLeaseLost(RuntimeError):
    """The lease expired, was released, or now belongs to another owner."""


@dataclass(frozen=True)
class AgentRunLease:
    """Proof of the right to write to one run, valid until `expires_at`."""

    run_id: str
    owner_id: str
    version: int
    expires_at: str


@dataclass(frozen=True)
class StartedRun:
    run: dict[str, Any]
    lease: AgentRunLease


@dataclass(frozen=True)
class CheckpointCommit:
    run: dict[str, Any]
    checkpoint: dict[str, Any]
    # None once the run parked or finished: nobody holds authority over it.
    lease: AgentRunLease | None


class AgentRunRepository:
    def __init__(
        self, base_dir: str | Path, *, registry: OwnerRegistry | None = None
    ) -> None:
        self.base = Path(base_dir).expanduser()
        self.base.mkdir(parents=True, exist_ok=True)
        os.chmod(self.base, 0o700)
        self.path = self.base / DB_NAME
        self.registry = registry or OwnerRegistry(self.base)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            self.path, check_same_thread=False, isolation_level=None, timeout=10.0
        )
        self._conn.row_factory = sqlite3.Row
        os.chmod(self.path, 0o600)
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous = FULL")
        self._conn.execute("PRAGMA busy_timeout = 10000")
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._initialize_schema()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # --- run identity ----------------------------------------------------

    def create_run(
        self,
        *,
        session_id: str,
        trigger: str,
        goal: str,
        owner: RunOwner,
        run_id: str | None = None,
        parent_run_id: str | None = None,
        person_id: str | None = None,
        provider_model_id: str | None = None,
        lease_seconds: int | float = DEFAULT_LEASE_SECONDS,
        now: datetime | None = None,
    ) -> StartedRun:
        """Create a run already owned by its creator.

        A run is never durable without an owner: `running` with no lease is a
        crash signal, not a normal state.
        """
        if trigger not in RUN_TRIGGERS:
            raise ValueError(f"unknown Agent Run trigger {trigger!r}")
        if not _SESSION_ID.fullmatch(str(session_id or "")):
            raise ValueError(f"invalid session id {session_id!r}")
        seconds = _lease_seconds(lease_seconds)
        identity = _bounded_id(run_id or f"run-{uuid.uuid4().hex}", "run_id")
        stamp = _stamp(now)
        expires_at = _stamp(_parse(stamp) + timedelta(seconds=seconds))
        with self._write():
            self._conn.execute(
                """
                INSERT INTO agent_runs (
                    run_id, session_id, person_id, parent_run_id, trigger,
                    goal_fingerprint, provider_model_id, current_state, version,
                    checkpoint_sequence, lease_owner, lease_owner_host,
                    lease_owner_pid, lease_expires_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'running', 1, 1, ?, ?, ?, ?, ?, ?)
                """,
                (
                    identity,
                    str(session_id),
                    _optional_id(person_id),
                    _optional_id(parent_run_id),
                    trigger,
                    goal_fingerprint(goal),
                    _optional_id(provider_model_id),
                    owner.owner_id,
                    owner.host,
                    int(owner.pid),
                    expires_at,
                    stamp,
                    stamp,
                ),
            )
            self._append_checkpoint(identity, 1, "run_started", "running", {}, stamp)
            run = _run_row(self._row(identity))
        return StartedRun(
            run=run,
            lease=AgentRunLease(identity, owner.owner_id, 1, expires_at),
        )

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._row(run_id)
        return None if row is None else _run_row(row)

    def list_runs(
        self,
        *,
        session_id: str | None = None,
        person_id: str | None = None,
        trigger: str | None = None,
        states: Iterable[str] | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if session_id is not None:
            clauses.append("session_id = ?")
            params.append(str(session_id))
        if person_id is not None:
            clauses.append("person_id = ?")
            params.append(str(person_id))
        if trigger is not None:
            clauses.append("trigger = ?")
            params.append(str(trigger))
        if created_after is not None:
            clauses.append("created_at >= ?")
            params.append(_stamp(created_after))
        if created_before is not None:
            clauses.append("created_at <= ?")
            params.append(_stamp(created_before))
        wanted = tuple(states or ())
        if wanted:
            clauses.append(f"current_state IN ({','.join('?' * len(wanted))})")
            params.extend(wanted)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, int(limit)))
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM agent_runs {where} "
                "ORDER BY created_at DESC, rowid DESC LIMIT ?",
                params,
            ).fetchall()
        return [_run_row(row) for row in rows]

    def list_checkpoints(self, run_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM agent_run_checkpoints WHERE run_id = ? "
                "ORDER BY sequence",
                (str(run_id),),
            ).fetchall()
        return [_checkpoint_row(row) for row in rows]

    # --- leases ----------------------------------------------------------

    def acquire_lease(
        self,
        run_id: str,
        owner: RunOwner,
        lease_seconds: int | float = DEFAULT_LEASE_SECONDS,
        now: datetime | None = None,
    ) -> AgentRunLease | None:
        """Take execution authority, or return None because someone else has it."""
        seconds = _lease_seconds(lease_seconds)
        stamp = _stamp(now)
        expires_at = _stamp(_parse(stamp) + timedelta(seconds=seconds))
        with self._write():
            row = self._row(run_id)
            if row is None:
                raise KeyError(run_id)
            if not is_leasable(str(row["current_state"])):
                return None
            held_by_other = (
                row["lease_owner"] is not None
                and str(row["lease_owner"]) != owner.owner_id
                and row["lease_expires_at"] is not None
                and str(row["lease_expires_at"]) > stamp
            )
            if held_by_other:
                return None
            version = int(row["version"])
            changed = self._conn.execute(
                """
                UPDATE agent_runs SET
                    lease_owner = ?, lease_owner_host = ?, lease_owner_pid = ?,
                    lease_expires_at = ?, version = version + 1, updated_at = ?
                WHERE run_id = ? AND version = ?
                """,
                (
                    owner.owner_id,
                    owner.host,
                    int(owner.pid),
                    expires_at,
                    stamp,
                    str(run_id),
                    version,
                ),
            ).rowcount
            if changed != 1:
                return None
        return AgentRunLease(str(run_id), owner.owner_id, version + 1, expires_at)

    def renew_lease(
        self,
        lease: AgentRunLease,
        lease_seconds: int | float = DEFAULT_LEASE_SECONDS,
        now: datetime | None = None,
    ) -> AgentRunLease:
        seconds = _lease_seconds(lease_seconds)
        stamp = _stamp(now)
        expires_at = _stamp(_parse(stamp) + timedelta(seconds=seconds))
        with self._write():
            changed = self._conn.execute(
                """
                UPDATE agent_runs SET
                    lease_expires_at = ?, version = version + 1, updated_at = ?
                WHERE run_id = ? AND version = ? AND lease_owner = ?
                  AND lease_expires_at > ?
                """,
                (expires_at, stamp, lease.run_id, lease.version, lease.owner_id, stamp),
            ).rowcount
            if changed != 1:
                self._raise_fence_failure(lease, stamp)
        return AgentRunLease(
            lease.run_id, lease.owner_id, lease.version + 1, expires_at
        )

    def release_lease(self, lease: AgentRunLease, now: datetime | None = None) -> None:
        stamp = _stamp(now)
        with self._write():
            changed = self._conn.execute(
                """
                UPDATE agent_runs SET
                    lease_owner = NULL, lease_owner_host = NULL,
                    lease_owner_pid = NULL, lease_expires_at = NULL,
                    version = version + 1, updated_at = ?
                WHERE run_id = ? AND version = ? AND lease_owner = ?
                """,
                (stamp, lease.run_id, lease.version, lease.owner_id),
            ).rowcount
            if changed != 1:
                self._raise_fence_failure(lease, stamp)

    # --- checkpoints -----------------------------------------------------

    def checkpoint(
        self,
        lease: AgentRunLease,
        *,
        kind: str,
        state: str | None = None,
        payload: dict[str, Any] | None = None,
        usage: dict[str, Any] | None = None,
        source_refs: Iterable[dict[str, Any]] = (),
        artifact_refs: Iterable[dict[str, Any]] = (),
        approval_ids: Iterable[str] = (),
        terminal_result: dict[str, Any] | None = None,
        person_id: str | None = None,
        provider_model_id: str | None = None,
        now: datetime | None = None,
    ) -> CheckpointCommit:
        """Commit one unit of semantic progress against this lease, or nothing."""
        stamp = _stamp(now)
        with self._write():
            row = self._row(lease.run_id)
            if row is None:
                raise KeyError(lease.run_id)
            self._check_fence(row, lease, stamp)
            current = str(row["current_state"])
            target = str(state or current)
            validate_transition(kind, current, target)

            sequence = int(row["checkpoint_sequence"]) + 1
            parks = is_waiting(target) or is_terminal(target)
            finished_at = stamp if is_terminal(target) else row["finished_at"]
            merged_usage = add_usage(json_object(row["usage"]), dict(usage or {}))
            merged_sources = merge_unique_refs(
                json_list(row["source_refs"]), project_source_refs(list(source_refs))
            )
            merged_artifacts = merge_unique_refs(
                json_list(row["artifact_refs"]),
                project_artifact_refs(list(artifact_refs)),
            )
            merged_approvals = merge_unique_strings(
                json_list(row["approval_ids"]), list(approval_ids)
            )
            result = (
                _dump(project_terminal_result(terminal_result))
                if terminal_result is not None
                else row["terminal_result"]
            )
            changed = self._conn.execute(
                """
                UPDATE agent_runs SET
                    current_state = ?, checkpoint_sequence = ?, version = version + 1,
                    usage = ?, source_refs = ?, artifact_refs = ?, approval_ids = ?,
                    terminal_result = ?, person_id = COALESCE(?, person_id),
                    provider_model_id = COALESCE(?, provider_model_id),
                    updated_at = ?, finished_at = ?,
                    lease_owner = CASE WHEN ? THEN NULL ELSE lease_owner END,
                    lease_owner_host = CASE WHEN ? THEN NULL ELSE lease_owner_host END,
                    lease_owner_pid = CASE WHEN ? THEN NULL ELSE lease_owner_pid END,
                    lease_expires_at = CASE WHEN ? THEN NULL ELSE lease_expires_at END
                WHERE run_id = ? AND version = ? AND lease_owner = ?
                """,
                (
                    target,
                    sequence,
                    _dump(merged_usage),
                    _dump(merged_sources),
                    _dump(merged_artifacts),
                    _dump(merged_approvals),
                    result,
                    _optional_id(person_id),
                    _optional_id(provider_model_id),
                    stamp,
                    finished_at,
                    parks,
                    parks,
                    parks,
                    parks,
                    lease.run_id,
                    lease.version,
                    lease.owner_id,
                ),
            ).rowcount
            if changed != 1:
                self._raise_fence_failure(lease, stamp)
            checkpoint = self._append_checkpoint(
                lease.run_id, sequence, kind, target, payload, stamp
            )
            run = _run_row(self._row(lease.run_id))
        return CheckpointCommit(
            run=run,
            checkpoint=checkpoint,
            lease=(
                None
                if parks
                else AgentRunLease(
                    lease.run_id, lease.owner_id, lease.version + 1, lease.expires_at
                )
            ),
        )

    # --- retention -------------------------------------------------------

    def prune_checkpoints(self, *, finished_before: datetime) -> list[str]:
        """Drop step detail for runs that finished before the cutoff.

        This is the only write in the store that is not a compare-and-swap
        against a lease, and it is deliberately narrow: it deletes rows from
        `agent_run_checkpoints` and touches `agent_runs` never. Run identity,
        person, source and artifact references, usage, approvals, and outcome
        all live on the run row and survive. A terminal run never moves again,
        so no lease can be racing this.

        A run whose record marks a hole, or carries a checkpoint kind this
        build cannot read, is skipped: retention must not delete the evidence
        that evidence is missing.
        """
        cutoff = _stamp(finished_before)
        readable = tuple(sorted(CHECKPOINT_KINDS - INCOMPLETE_RECORD_KINDS))
        pruned: list[str] = []
        with self._write():
            rows = self._conn.execute(
                f"""
                SELECT run_id FROM agent_runs
                WHERE finished_at IS NOT NULL AND finished_at < ?
                  AND NOT EXISTS (
                      SELECT 1 FROM agent_run_checkpoints c
                      WHERE c.run_id = agent_runs.run_id
                        AND c.kind NOT IN ({','.join('?' * len(readable))})
                  )
                ORDER BY finished_at
                """,
                (cutoff, *readable),
            ).fetchall()
            for row in rows:
                run_id = str(row["run_id"])
                removed = self._conn.execute(
                    "DELETE FROM agent_run_checkpoints WHERE run_id = ?", (run_id,)
                ).rowcount
                if removed:
                    pruned.append(run_id)
        return pruned

    # --- recovery --------------------------------------------------------

    def reconcile_expired_leases(
        self, now: datetime | None = None
    ) -> list[dict[str, Any]]:
        """Release leases that have run out of time. Live owners are untouched."""
        return self._reconcile(_stamp(now), prove_dead=False)

    def reclaim_dead_owner_leases(
        self, now: datetime | None = None
    ) -> list[dict[str, Any]]:
        """Startup recovery: also take leases whose owner is proven dead.

        Proof comes from `OwnerRegistry`, never from the assumption that a
        restart means every previous owner is gone. A second sidecar starting
        beside a working one must leave that one's runs alone.
        """
        return self._reconcile(_stamp(now), prove_dead=True)

    def _reconcile(self, stamp: str, *, prove_dead: bool) -> list[dict[str, Any]]:
        recovered: list[dict[str, Any]] = []
        verdicts: dict[str, Liveness] = {}
        with self._write():
            rows = self._conn.execute(
                "SELECT * FROM agent_runs "
                "WHERE lease_owner IS NOT NULL OR current_state = 'running' "
                "ORDER BY created_at, rowid"
            ).fetchall()
            for row in rows:
                reason = self._reclaim_reason(row, stamp, prove_dead, verdicts)
                if reason is None:
                    continue
                run_id = str(row["run_id"])
                version = int(row["version"])
                state = str(row["current_state"])
                interrupts = state == "running"
                sequence = int(row["checkpoint_sequence"]) + (1 if interrupts else 0)
                changed = self._conn.execute(
                    """
                    UPDATE agent_runs SET
                        current_state = ?, checkpoint_sequence = ?,
                        lease_owner = NULL, lease_owner_host = NULL,
                        lease_owner_pid = NULL, lease_expires_at = NULL,
                        version = version + 1, updated_at = ?
                    WHERE run_id = ? AND version = ?
                    """,
                    (
                        "interrupted" if interrupts else state,
                        sequence,
                        stamp,
                        run_id,
                        version,
                    ),
                ).rowcount
                if changed != 1:
                    continue
                if interrupts:
                    self._append_checkpoint(
                        run_id,
                        sequence,
                        "process_interrupted",
                        "interrupted",
                        {"reason": reason},
                        stamp,
                    )
                recovered.append(_run_row(self._row(run_id)))
        for owner_id, verdict in verdicts.items():
            if verdict is Liveness.DEAD:
                self.registry.forget(owner_id)
        return recovered

    def _reclaim_reason(
        self,
        row: sqlite3.Row,
        stamp: str,
        prove_dead: bool,
        verdicts: dict[str, Liveness],
    ) -> str | None:
        owner_id = row["lease_owner"]
        if owner_id is None:
            # Only a crash between creating a run and owning it can produce this.
            if prove_dead and str(row["current_state"]) == "running":
                return "no_recorded_owner"
            return None
        expiry = row["lease_expires_at"]
        if expiry is None or str(expiry) <= stamp:
            return "lease_expired"
        if not prove_dead:
            return None
        owner_id = str(owner_id)
        if owner_id not in verdicts:
            verdicts[owner_id] = self.registry.liveness_of(
                owner_id, row["lease_owner_host"]
            )
        return "owner_process_dead" if verdicts[owner_id] is Liveness.DEAD else None

    # --- internals -------------------------------------------------------

    @contextmanager
    def _write(self) -> Iterator[None]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield
            except BaseException:
                self._conn.rollback()
                raise
            self._conn.commit()

    def _row(self, run_id: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM agent_runs WHERE run_id = ?", (str(run_id),)
        ).fetchone()

    def _append_checkpoint(
        self,
        run_id: str,
        sequence: int,
        kind: str,
        state: str,
        payload: dict[str, Any] | None,
        stamp: str,
    ) -> dict[str, Any]:
        projected = checkpoint_payload(payload)
        self._conn.execute(
            "INSERT INTO agent_run_checkpoints "
            "(run_id, sequence, kind, state, payload, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, sequence, kind, state, _dump(projected), stamp),
        )
        return {
            "run_id": run_id,
            "sequence": sequence,
            "kind": kind,
            "state": state,
            "payload": projected,
            "created_at": stamp,
        }

    def _check_fence(
        self, row: sqlite3.Row, lease: AgentRunLease, stamp: str
    ) -> None:
        if (
            row["lease_owner"] is not None
            and str(row["lease_owner"]) == lease.owner_id
            and row["lease_expires_at"] is not None
            and str(row["lease_expires_at"]) > stamp
            and int(row["version"]) == lease.version
        ):
            return
        self._raise_fence_failure(lease, stamp)

    def _raise_fence_failure(self, lease: AgentRunLease, stamp: str) -> None:
        """Say which half of the compare-and-swap failed, without guessing."""
        row = self._row(lease.run_id)
        if row is None:
            raise KeyError(lease.run_id)
        if row["lease_owner"] is None or str(row["lease_owner"]) != lease.owner_id:
            raise AgentRunLeaseLost(lease.run_id)
        if row["lease_expires_at"] is None or str(row["lease_expires_at"]) <= stamp:
            raise AgentRunLeaseLost(lease.run_id)
        if int(row["version"]) != lease.version:
            raise AgentRunVersionConflict(lease.run_id)
        raise AgentRunLeaseLost(lease.run_id)

    def _initialize_schema(self) -> None:
        with self._lock:
            version = int(self._conn.execute("PRAGMA user_version").fetchone()[0])
            if version > SCHEMA_VERSION:
                self._conn.close()
                raise RuntimeError(
                    f"{DB_NAME} is at schema {version}, newer than this build's "
                    f"{SCHEMA_VERSION}"
                )
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS agent_runs (
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
                CREATE TABLE IF NOT EXISTS agent_run_checkpoints (
                    run_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    state TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, sequence),
                    FOREIGN KEY (run_id) REFERENCES agent_runs(run_id)
                );
                CREATE INDEX IF NOT EXISTS agent_runs_session_created
                    ON agent_runs(session_id, created_at);
                CREATE INDEX IF NOT EXISTS agent_runs_live_lease
                    ON agent_runs(lease_expires_at) WHERE lease_owner IS NOT NULL;
                """
            )
            self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def _stamp(value: datetime | None = None) -> str:
    """Fixed-width UTC text, so string order is time order in SQL and Python."""
    moment = value or datetime.now(UTC)
    return moment.astimezone(UTC).strftime(_STAMP_FORMAT)


def _parse(value: str) -> datetime:
    return datetime.strptime(value, _STAMP_FORMAT).replace(tzinfo=UTC)


def _lease_seconds(value: int | float) -> float:
    seconds = float(value)
    if not 0 < seconds <= MAX_LEASE_SECONDS:
        raise ValueError(f"lease seconds must be within (0, {MAX_LEASE_SECONDS}]")
    return seconds


def _bounded_id(value: str, field: str) -> str:
    text = str(value or "").strip()
    if not text or len(text) > _MAX_ID or "\n" in text:
        raise ValueError(f"invalid {field}")
    return text


def _optional_id(value: str | None) -> str | None:
    return None if value in (None, "") else _bounded_id(str(value), "identifier")


def _dump(value: Any) -> str | None:
    return None if value is None else json.dumps(value, separators=(",", ":"))


def _run_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "run_id": str(row["run_id"]),
        "session_id": str(row["session_id"]),
        "person_id": row["person_id"],
        "parent_run_id": row["parent_run_id"],
        "trigger": str(row["trigger"]),
        "goal_fingerprint": str(row["goal_fingerprint"]),
        "provider_model_id": row["provider_model_id"],
        "current_state": str(row["current_state"]),
        "version": int(row["version"]),
        "checkpoint_sequence": int(row["checkpoint_sequence"]),
        "approval_ids": json_list(row["approval_ids"]),
        "source_refs": json_list(row["source_refs"]),
        "artifact_refs": json_list(row["artifact_refs"]),
        "usage": json_object(row["usage"]),
        "terminal_result": nullable_json_object(row["terminal_result"]),
        "lease_owner": row["lease_owner"],
        "lease_owner_host": row["lease_owner_host"],
        "lease_owner_pid": row["lease_owner_pid"],
        "lease_expires_at": row["lease_expires_at"],
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
        "finished_at": row["finished_at"],
    }


def _checkpoint_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "run_id": str(row["run_id"]),
        "sequence": int(row["sequence"]),
        "kind": str(row["kind"]),
        "state": str(row["state"]),
        "payload": json_object(row["payload"]),
        "created_at": str(row["created_at"]),
    }
