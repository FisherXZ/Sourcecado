"""The durable authority for Agent Run identity, ownership, and progress.

One run identity is shared by chat, queued chat, and scheduled work. Exactly one
process may drive a run at a time, and it proves that right with a lease. Every
durable write is a compare-and-swap against `(run_id, version, lease_owner)`:
the version is the fencing token, so a superseded or duplicated owner writes
nothing instead of writing second.

The tables live in Postgres. `supabase/migrations/20261110000200_agent_runs.sql`
defines them. Every row belongs to one user, and every query names that user.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Iterator

import psycopg
from psycopg.rows import dict_row

from coworker import db
from coworker.agent_run_approval import (
    AgentRunEffectFenced,
    EffectStatus,
    bounded_actor,
    bounded_note,
    effect_fingerprint,
    operator_decision,
    replay_class,
)
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

# The class half of the two-key advisory lock that `_write` takes. The other
# half is a hash of the user id. The value only has to differ from the classes
# that other stores use.
_WRITE_LOCK_CLASS = 0x5302
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


@dataclass(frozen=True)
class EffectCommit:
    """One external-effect write: run row, checkpoint, and effect row together."""

    run: dict[str, Any]
    checkpoint: dict[str, Any]
    effect: dict[str, Any]
    # None once the quarantine parked the run: nobody holds authority over it.
    lease: AgentRunLease | None


class AgentRunRepository:
    def __init__(
        self, base_dir: str | Path, *, registry: OwnerRegistry | None = None
    ) -> None:
        self.base = Path(base_dir).expanduser()
        self.base.mkdir(parents=True, exist_ok=True)
        os.chmod(self.base, 0o700)
        self.registry = registry or OwnerRegistry(self.base)
        self._db, self._user_id = db.resolve(self.base)

    def close(self) -> None:
        """Nothing to close. The connection pool belongs to `coworker.db`."""

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
        with self._write() as cur:
            cur.execute(
                """
                INSERT INTO agent_runs (
                    user_id, run_id, session_id, person_id, parent_run_id, trigger,
                    goal_fingerprint, provider_model_id, current_state, version,
                    checkpoint_sequence, lease_owner, lease_owner_host,
                    lease_owner_pid, lease_expires_at, created_at, updated_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, 'running', 1, 1,
                    %s, %s, %s, %s, %s, %s
                )
                """,
                (
                    self._user_id,
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
            self._append_checkpoint(
                cur, identity, 1, "run_started", "running", {}, stamp
            )
            run = _run_row(self._row(cur, identity))
        return StartedRun(
            run=run,
            lease=AgentRunLease(identity, owner.owner_id, 1, expires_at),
        )

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._read() as cur:
            row = self._row(cur, run_id)
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
        clauses: list[str] = ["user_id = %s"]
        params: list[Any] = [self._user_id]
        if session_id is not None:
            clauses.append("session_id = %s")
            params.append(str(session_id))
        if person_id is not None:
            clauses.append("person_id = %s")
            params.append(str(person_id))
        if trigger is not None:
            clauses.append("trigger = %s")
            params.append(str(trigger))
        if created_after is not None:
            clauses.append("created_at >= %s")
            params.append(_stamp(created_after))
        if created_before is not None:
            clauses.append("created_at <= %s")
            params.append(_stamp(created_before))
        wanted = [str(state) for state in states or ()]
        if wanted:
            clauses.append("current_state = ANY(%s)")
            params.append(wanted)
        params.append(max(1, int(limit)))
        with self._read() as cur:
            rows = cur.execute(
                f"SELECT * FROM agent_runs WHERE {' AND '.join(clauses)} "
                "ORDER BY created_at DESC, insert_order DESC LIMIT %s",
                params,
            ).fetchall()
        return [_run_row(row) for row in rows]

    def list_checkpoints(self, run_id: str) -> list[dict[str, Any]]:
        with self._read() as cur:
            rows = cur.execute(
                "SELECT * FROM agent_run_checkpoints "
                "WHERE user_id = %s AND run_id = %s ORDER BY sequence",
                (self._user_id, str(run_id)),
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
        with self._write() as cur:
            row = self._row(cur, run_id)
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
            changed = cur.execute(
                """
                UPDATE agent_runs SET
                    lease_owner = %s, lease_owner_host = %s, lease_owner_pid = %s,
                    lease_expires_at = %s, version = version + 1, updated_at = %s
                WHERE user_id = %s AND run_id = %s AND version = %s
                """,
                (
                    owner.owner_id,
                    owner.host,
                    int(owner.pid),
                    expires_at,
                    stamp,
                    self._user_id,
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
        with self._write() as cur:
            changed = cur.execute(
                """
                UPDATE agent_runs SET
                    lease_expires_at = %s, version = version + 1, updated_at = %s
                WHERE user_id = %s AND run_id = %s AND version = %s
                  AND lease_owner = %s AND lease_expires_at > %s
                """,
                (
                    expires_at,
                    stamp,
                    self._user_id,
                    lease.run_id,
                    lease.version,
                    lease.owner_id,
                    stamp,
                ),
            ).rowcount
            if changed != 1:
                self._raise_fence_failure(cur, lease, stamp)
        return AgentRunLease(
            lease.run_id, lease.owner_id, lease.version + 1, expires_at
        )

    def release_lease(self, lease: AgentRunLease, now: datetime | None = None) -> None:
        stamp = _stamp(now)
        with self._write() as cur:
            changed = cur.execute(
                """
                UPDATE agent_runs SET
                    lease_owner = NULL, lease_owner_host = NULL,
                    lease_owner_pid = NULL, lease_expires_at = NULL,
                    version = version + 1, updated_at = %s
                WHERE user_id = %s AND run_id = %s AND version = %s
                  AND lease_owner = %s
                """,
                (stamp, self._user_id, lease.run_id, lease.version, lease.owner_id),
            ).rowcount
            if changed != 1:
                self._raise_fence_failure(cur, lease, stamp)

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
        with self._write() as cur:
            row = self._row(cur, lease.run_id)
            if row is None:
                raise KeyError(lease.run_id)
            self._check_fence(cur, row, lease, stamp)
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
            changed = cur.execute(
                """
                UPDATE agent_runs SET
                    current_state = %s, checkpoint_sequence = %s,
                    version = version + 1, usage = %s, source_refs = %s,
                    artifact_refs = %s, approval_ids = %s, terminal_result = %s,
                    person_id = COALESCE(%s, person_id),
                    provider_model_id = COALESCE(%s, provider_model_id),
                    updated_at = %s, finished_at = %s,
                    lease_owner = CASE WHEN %s THEN NULL ELSE lease_owner END,
                    lease_owner_host = CASE WHEN %s THEN NULL ELSE lease_owner_host END,
                    lease_owner_pid = CASE WHEN %s THEN NULL ELSE lease_owner_pid END,
                    lease_expires_at = CASE WHEN %s THEN NULL ELSE lease_expires_at END
                WHERE user_id = %s AND run_id = %s AND version = %s
                  AND lease_owner = %s
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
                    self._user_id,
                    lease.run_id,
                    lease.version,
                    lease.owner_id,
                ),
            ).rowcount
            if changed != 1:
                self._raise_fence_failure(cur, lease, stamp)
            checkpoint = self._append_checkpoint(
                cur, lease.run_id, sequence, kind, target, payload, stamp
            )
            run = _run_row(self._row(cur, lease.run_id))
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

    # --- approval fencing ------------------------------------------------

    def resume_from_approval(
        self,
        run_id: str,
        owner: RunOwner,
        *,
        approval_id: str,
        decision: str,
        lease_seconds: int | float = DEFAULT_LEASE_SECONDS,
        now: datetime | None = None,
    ) -> CheckpointCommit | None:
        """Give a parked run back to a process, against a named resolution.

        `acquire_lease` refuses waiting states on purpose: a parked run belongs
        to a person, and a process that wants it back has to say which decision
        released it. This is that door and it is deliberately narrow.

        The lease and the move to `running` commit together. A run must never
        rest in a waiting state while holding a lease -- waiting states hold no
        lease, and Doctor reports the pair as a record contradicting itself.

        A run parked in `waiting_external` is not opened here at all. Its effect
        is quarantined, and an approval decision says nothing about whether a
        send already went out. Only `resolve_quarantined_effect` settles that.
        """
        if decision not in {"allow", "deny"}:
            raise ValueError(f"an approval resolves to allow or deny, not {decision!r}")
        named = _bounded_id(approval_id, "approval_id")
        seconds = _lease_seconds(lease_seconds)
        stamp = _stamp(now)
        expires_at = _stamp(_parse(stamp) + timedelta(seconds=seconds))
        with self._write() as cur:
            row = self._row(cur, run_id)
            if row is None:
                raise KeyError(run_id)
            state = str(row["current_state"])
            if state == "waiting_external":
                raise AgentRunEffectFenced(
                    f"run {run_id} is parked on an external effect whose outcome "
                    "is unknown; an approval decision does not settle that"
                )
            if not is_waiting(state):
                return None
            if named not in json_list(row["approval_ids"]):
                raise ValueError(f"run {run_id} never raised approval {named!r}")
            version = int(row["version"])
            sequence = int(row["checkpoint_sequence"]) + 1
            validate_transition("approval_resolved", state, "running")
            changed = cur.execute(
                """
                UPDATE agent_runs SET
                    current_state = 'running', checkpoint_sequence = %s,
                    version = version + 1, updated_at = %s,
                    lease_owner = %s, lease_owner_host = %s, lease_owner_pid = %s,
                    lease_expires_at = %s
                WHERE user_id = %s AND run_id = %s AND version = %s
                  AND lease_owner IS NULL
                """,
                (
                    sequence,
                    stamp,
                    owner.owner_id,
                    owner.host,
                    int(owner.pid),
                    expires_at,
                    self._user_id,
                    str(run_id),
                    version,
                ),
            ).rowcount
            if changed != 1:
                return None
            checkpoint = self._append_checkpoint(
                cur,
                str(run_id),
                sequence,
                "approval_resolved",
                "running",
                {"approval_id": named, "status": decision},
                stamp,
            )
            run = _run_row(self._row(cur, run_id))
        return CheckpointCommit(
            run=run,
            checkpoint=checkpoint,
            lease=AgentRunLease(str(run_id), owner.owner_id, version + 1, expires_at),
        )

    # --- external effects ------------------------------------------------
    #
    # Three writes with one shape: compare-and-swap the run row against the
    # lease, append the checkpoint, and move the effect row, all in one
    # transaction. The run store is the fence of record for external effects
    # precisely because these three facts commit together or not at all.
    #
    # The ordering that makes the window recoverable is the caller's job and is
    # not negotiable: `dispatch_effect` commits *before* the external call, and
    # `record_effect_outcome` commits *after* it. A process that dies before the
    # dispatch commit made no call. A process that dies after it may or may not
    # have, and that is exactly what `quarantine_effect` records.

    def dispatch_effect(
        self,
        lease: AgentRunLease,
        *,
        tool_name: str,
        arguments: Any = None,
        effect_id: str | None = None,
        tool_call_id: str | None = None,
        approval_id: str | None = None,
        step: int | None = None,
        now: datetime | None = None,
    ) -> EffectCommit:
        """Record that an external effect is about to be attempted, before it is.

        The arguments are fingerprinted, never stored. A recipient, a subject,
        and a body belong to the transcript and the approval record; what this
        store needs is only whether a call it is about to make is the call it
        already dispatched.
        """
        identity = _bounded_id(effect_id or f"effect-{uuid.uuid4().hex}", "effect_id")
        name = _bounded_id(tool_name, "tool_name")
        stamp = _stamp(now)
        payload = {
            "attempt_id": identity,
            "tool_name": name,
            "tool_call_id": tool_call_id,
            "approval_id": approval_id,
            "step": step,
            "status": str(EffectStatus.DISPATCHED),
        }
        with self._write() as cur:
            row = self._effect_checkpoint(
                cur, lease, "tool_pending", "running", payload, stamp, release=False
            )
            try:
                cur.execute(
                    """
                    INSERT INTO agent_run_effects (
                        user_id, effect_id, run_id, tool_name, tool_call_id,
                        approval_id, replay_class, arguments_fingerprint, status,
                        dispatched_by, dispatched_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        self._user_id,
                        identity,
                        lease.run_id,
                        name,
                        _optional_id(tool_call_id),
                        _optional_id(approval_id),
                        str(replay_class(name)),
                        effect_fingerprint(name, arguments),
                        str(EffectStatus.DISPATCHED),
                        lease.owner_id,
                        stamp,
                    ),
                )
            except psycopg.IntegrityError as exc:
                raise AgentRunEffectFenced(
                    f"effect {identity} is already on record"
                ) from exc
            effect = self._effect_row(cur, identity)
        return EffectCommit(
            run=row["run"],
            checkpoint=row["checkpoint"],
            effect=effect,
            lease=row["lease"],
        )

    def record_effect_outcome(
        self,
        lease: AgentRunLease,
        effect_id: str,
        *,
        ok: bool,
        outcome_ref: str | None = None,
        error_class: str | None = None,
        error_summary: str | None = None,
        duration_ms: int | None = None,
        person_id: str | None = None,
        source_refs: Iterable[dict[str, Any]] = (),
        artifact_refs: Iterable[dict[str, Any]] = (),
        now: datetime | None = None,
    ) -> EffectCommit:
        """Close the window. Only the process that dispatched may say what happened.

        The owner check is not politeness. A second owner did not make the call,
        so it has nothing to report, and letting it write an outcome is how an
        unknown quietly becomes a certainty.
        """
        status = EffectStatus.SUCCEEDED if ok else EffectStatus.FAILED
        stamp = _stamp(now)
        payload = {
            "attempt_id": str(effect_id),
            "status": str(status),
            "error_class": error_class,
            "error_summary": error_summary,
            "duration_ms": duration_ms,
        }
        with self._write() as cur:
            # The effect fence is checked before the run's own state machine so
            # that "this effect is already quarantined" is what the caller hears,
            # whatever shape the run happens to be in. Both layers refuse; this
            # one has the answer an operator needs.
            existing = self._require_dispatched(
                cur, effect_id, lease.run_id, owner_id=lease.owner_id
            )
            payload["tool_name"] = existing["tool_name"]
            # Carried so a receipt pairs this checkpoint with the `tool_pending`
            # the dispatch wrote, exactly as `quarantine_effect` does. Without
            # it a fenced call reads as one call that never finished plus one
            # outcome belonging to nothing.
            payload["tool_call_id"] = existing["tool_call_id"]
            payload["approval_id"] = existing["approval_id"]
            row = self._effect_checkpoint(
                cur, lease, "tool_completed", "running", payload, stamp, release=False,
                person_id=person_id, source_refs=source_refs, artifact_refs=artifact_refs,
            )
            changed = cur.execute(
                """
                UPDATE agent_run_effects SET
                    status = %s, settled_at = %s,
                    outcome_ref = COALESCE(%s, outcome_ref)
                WHERE user_id = %s AND effect_id = %s AND run_id = %s
                  AND status = %s AND dispatched_by = %s
                """,
                (
                    str(status),
                    stamp,
                    _optional_id(outcome_ref),
                    self._user_id,
                    str(effect_id),
                    lease.run_id,
                    str(EffectStatus.DISPATCHED),
                    lease.owner_id,
                ),
            ).rowcount
            if changed != 1:
                raise AgentRunEffectFenced(
                    f"effect {effect_id} is {existing['status']}, and only its "
                    "dispatching owner may report an outcome, and only once"
                )
            effect = self._effect_row(cur, effect_id)
        return EffectCommit(
            run=row["run"],
            checkpoint=row["checkpoint"],
            effect=effect,
            lease=row["lease"],
        )

    def quarantine_effect(
        self,
        lease: AgentRunLease,
        effect_id: str,
        *,
        reason: str,
        state: str = "interrupted",
        now: datetime | None = None,
    ) -> EffectCommit:
        """Move an effect that never reported back into review, and park the run.

        Any owner may do this, including one that did not dispatch, because
        quarantining claims nothing about the world. It records that nobody
        knows, which is true for every process that reads the row.

        The run comes to rest holding no lease. `interrupted` is the resting
        place after a crash: the work needs classification, and a person now
        owns that. `waiting_external` is the resting place when the owner is
        still alive and its call simply never answered.
        """
        kinds = {
            "interrupted": "tool_outcome_unknown",
            "waiting_external": "waiting_external",
        }
        if state not in kinds:
            raise ValueError(
                f"a quarantine leaves a run in {sorted(kinds)}, not {state!r}"
            )
        stamp = _stamp(now)
        with self._write() as cur:
            existing = self._require_dispatched(cur, effect_id, lease.run_id)
            payload = {
                "attempt_id": str(effect_id),
                "tool_name": existing["tool_name"],
                "tool_call_id": existing["tool_call_id"],
                "approval_id": existing["approval_id"],
                "status": str(EffectStatus.AMBIGUOUS),
                "reason": reason,
            }
            row = self._effect_checkpoint(
                cur, lease, kinds[state], state, payload, stamp, release=True
            )
            changed = cur.execute(
                """
                UPDATE agent_run_effects SET
                    status = %s, settled_at = %s, reason = %s
                WHERE user_id = %s AND effect_id = %s AND run_id = %s
                  AND status = %s
                """,
                (
                    str(EffectStatus.AMBIGUOUS),
                    stamp,
                    bounded_note(reason),
                    self._user_id,
                    str(effect_id),
                    lease.run_id,
                    str(EffectStatus.DISPATCHED),
                ),
            ).rowcount
            if changed != 1:
                raise AgentRunEffectFenced(
                    f"effect {effect_id} is {existing['status']}, which is already "
                    "settled or already quarantined"
                )
            effect = self._effect_row(cur, effect_id)
        return EffectCommit(
            run=row["run"],
            checkpoint=row["checkpoint"],
            effect=effect,
            lease=row["lease"],
        )

    def resolve_quarantined_effect(
        self,
        effect_id: str,
        *,
        decision: str,
        operator: str,
        note: str | None = None,
        outcome_ref: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """A person settles what the machine could not.

        This write takes no lease, and that is deliberate: a quarantined run is
        parked and holds none. Like `prune_checkpoints` it is narrow on purpose.
        It touches one row of `agent_run_effects` and `agent_runs` never, it
        moves only out of `ambiguous`, and its target statuses are the three the
        table refuses to hold without a named person.

        Settling the effect does not restart the run. What to do with a run
        whose send turned out to have gone through is the operator's next
        decision, not a consequence of recording this one.
        """
        status = operator_decision(decision)
        actor = bounded_actor(operator)
        stamp = _stamp(now)
        with self._write() as cur:
            existing = self._effect_row(cur, effect_id)
            changed = cur.execute(
                """
                UPDATE agent_run_effects SET
                    status = %s, resolved_by = %s, resolved_note = %s,
                    resolved_at = %s, outcome_ref = COALESCE(%s, outcome_ref)
                WHERE user_id = %s AND effect_id = %s AND status = %s
                """,
                (
                    str(status),
                    actor,
                    bounded_note(note),
                    stamp,
                    _optional_id(outcome_ref),
                    self._user_id,
                    str(effect_id),
                    str(EffectStatus.AMBIGUOUS),
                ),
            ).rowcount
            if changed != 1:
                raise AgentRunEffectFenced(
                    f"effect {effect_id} is {existing['status']}; only a "
                    "quarantined effect is a person's to settle"
                )
            resolved = self._effect_row(cur, effect_id)
        return resolved

    def list_effects(self, run_id: str) -> list[dict[str, Any]]:
        with self._read() as cur:
            rows = cur.execute(
                "SELECT * FROM agent_run_effects WHERE user_id = %s AND run_id = %s "
                "ORDER BY dispatched_at, insert_order",
                (self._user_id, str(run_id)),
            ).fetchall()
        return [_effect_row(row) for row in rows]

    def effects_for_approval(self, approval_id: str) -> list[dict[str, Any]]:
        """Every effect opened under one approval, whatever run dispatched it.

        Read only. An approval decided from the operator surface is executed by
        the server under its own run, so the approval -- not the run -- is the
        key that joins the inbox to this store. There is no index on
        `approval_id`: adding one is a schema change, and this table holds one
        row per consequential call, read only when a person opens a receipt.
        """
        with self._read() as cur:
            rows = cur.execute(
                "SELECT * FROM agent_run_effects "
                "WHERE user_id = %s AND approval_id = %s "
                "ORDER BY dispatched_at, insert_order",
                (self._user_id, str(approval_id)),
            ).fetchall()
        return [_effect_row(row) for row in rows]

    def list_quarantined_effects(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """The review queue: every effect nobody knows the outcome of."""
        with self._read() as cur:
            rows = cur.execute(
                "SELECT * FROM agent_run_effects WHERE user_id = %s AND status = %s "
                "ORDER BY dispatched_at, insert_order LIMIT %s",
                (self._user_id, str(EffectStatus.AMBIGUOUS), max(1, int(limit))),
            ).fetchall()
        return [_effect_row(row) for row in rows]

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
        readable = sorted(CHECKPOINT_KINDS - INCOMPLETE_RECORD_KINDS)
        pruned: list[str] = []
        with self._write() as cur:
            rows = cur.execute(
                """
                SELECT run_id FROM agent_runs
                WHERE user_id = %s
                  AND finished_at IS NOT NULL AND finished_at < %s
                  AND NOT EXISTS (
                      SELECT 1 FROM agent_run_checkpoints c
                      WHERE c.user_id = agent_runs.user_id
                        AND c.run_id = agent_runs.run_id
                        AND NOT (c.kind = ANY(%s))
                  )
                ORDER BY finished_at
                """,
                (self._user_id, cutoff, readable),
            ).fetchall()
            for row in rows:
                run_id = str(row["run_id"])
                removed = cur.execute(
                    "DELETE FROM agent_run_checkpoints "
                    "WHERE user_id = %s AND run_id = %s",
                    (self._user_id, run_id),
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
        restart means every previous owner is gone. A second backend starting
        beside a working one must leave that one's runs alone.
        """
        return self._reconcile(_stamp(now), prove_dead=True)

    def _reconcile(self, stamp: str, *, prove_dead: bool) -> list[dict[str, Any]]:
        recovered: list[dict[str, Any]] = []
        verdicts: dict[str, Liveness] = {}
        with self._write() as cur:
            rows = cur.execute(
                "SELECT * FROM agent_runs WHERE user_id = %s "
                "AND (lease_owner IS NOT NULL OR current_state = 'running') "
                "ORDER BY created_at, insert_order",
                (self._user_id,),
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
                changed = cur.execute(
                    """
                    UPDATE agent_runs SET
                        current_state = %s, checkpoint_sequence = %s,
                        lease_owner = NULL, lease_owner_host = NULL,
                        lease_owner_pid = NULL, lease_expires_at = NULL,
                        version = version + 1, updated_at = %s
                    WHERE user_id = %s AND run_id = %s AND version = %s
                    """,
                    (
                        "interrupted" if interrupts else state,
                        sequence,
                        stamp,
                        self._user_id,
                        run_id,
                        version,
                    ),
                ).rowcount
                if changed != 1:
                    continue
                if interrupts:
                    self._append_checkpoint(
                        cur,
                        run_id,
                        sequence,
                        "process_interrupted",
                        "interrupted",
                        {"reason": reason},
                        stamp,
                    )
                recovered.append(_run_row(self._row(cur, run_id)))
        for owner_id, verdict in verdicts.items():
            if verdict is Liveness.DEAD:
                self.registry.forget(owner_id)
        return recovered

    def _reclaim_reason(
        self,
        row: dict[str, Any],
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
    def _read(self) -> Iterator[psycopg.Cursor]:
        with self._db.tx(self._user_id) as conn:
            yield conn.cursor(row_factory=dict_row)

    @contextmanager
    def _write(self) -> Iterator[psycopg.Cursor]:
        """One transaction that is the only writer of this user's runs.

        SQLite took the store's write lock with `BEGIN IMMEDIATE`, and the store
        was one file for one user. The same scope here is a transaction-level
        advisory lock on the user id: writers of one user's runs queue, writers
        of other users do not wait, readers never wait, and the lock goes away
        at commit or rollback. The compare-and-swap on
        `(run_id, version, lease_owner)` stays the fence in every write; the
        lock only keeps each read-then-write as one step, as it was before.
        """
        with self._db.tx(self._user_id) as conn:
            cur = conn.cursor(row_factory=dict_row)
            cur.execute(
                "SELECT pg_advisory_xact_lock(%s, hashtext(%s))",
                (_WRITE_LOCK_CLASS, str(self._user_id)),
            )
            yield cur

    def _row(self, cur: psycopg.Cursor, run_id: str) -> dict[str, Any] | None:
        return cur.execute(
            "SELECT * FROM agent_runs WHERE user_id = %s AND run_id = %s",
            (self._user_id, str(run_id)),
        ).fetchone()

    def _append_checkpoint(
        self,
        cur: psycopg.Cursor,
        run_id: str,
        sequence: int,
        kind: str,
        state: str,
        payload: dict[str, Any] | None,
        stamp: str,
    ) -> dict[str, Any]:
        projected = checkpoint_payload(payload)
        cur.execute(
            "INSERT INTO agent_run_checkpoints "
            "(user_id, run_id, sequence, kind, state, payload, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (self._user_id, run_id, sequence, kind, state, _dump(projected), stamp),
        )
        return {
            "run_id": run_id,
            "sequence": sequence,
            "kind": kind,
            "state": state,
            "payload": projected,
            "created_at": stamp,
        }

    def _effect_checkpoint(
        self,
        cur: psycopg.Cursor,
        lease: AgentRunLease,
        kind: str,
        state: str,
        payload: dict[str, Any],
        stamp: str,
        *,
        release: bool,
        person_id: str | None = None,
        source_refs: Iterable[dict[str, Any]] = (),
        artifact_refs: Iterable[dict[str, Any]] = (),
    ) -> dict[str, Any]:
        """The run half of an external-effect write. Caller is inside `_write`.

        Completion references merge in this transaction, beside the effect's
        settled outcome. Calling the public checkpoint writer here would open
        a second transaction. Usage and terminal results stay separate.
        """
        row = self._row(cur, lease.run_id)
        if row is None:
            raise KeyError(lease.run_id)
        self._check_fence(cur, row, lease, stamp)
        current = str(row["current_state"])
        validate_transition(kind, current, state)
        sequence = int(row["checkpoint_sequence"]) + 1
        sources = project_source_refs(list(source_refs)[:50])
        artifacts = project_artifact_refs(list(artifact_refs)[:50])
        if sources:
            payload["source_ref_ids"] = [ref["id"] for ref in sources]
        if artifacts:
            payload["artifact_ref_ids"] = [ref["id"] for ref in artifacts]
        changed = cur.execute(
            """
            UPDATE agent_runs SET
                current_state = %s, checkpoint_sequence = %s,
                version = version + 1, updated_at = %s,
                person_id = COALESCE(%s, person_id),
                source_refs = %s, artifact_refs = %s,
                lease_owner = CASE WHEN %s THEN NULL ELSE lease_owner END,
                lease_owner_host = CASE WHEN %s THEN NULL ELSE lease_owner_host END,
                lease_owner_pid = CASE WHEN %s THEN NULL ELSE lease_owner_pid END,
                lease_expires_at = CASE WHEN %s THEN NULL ELSE lease_expires_at END
            WHERE user_id = %s AND run_id = %s AND version = %s
              AND lease_owner = %s
            """,
            (
                state,
                sequence,
                stamp,
                _optional_id(person_id),
                _dump(merge_unique_refs(json_list(row["source_refs"]), sources)),
                _dump(merge_unique_refs(json_list(row["artifact_refs"]), artifacts)),
                release,
                release,
                release,
                release,
                self._user_id,
                lease.run_id,
                lease.version,
                lease.owner_id,
            ),
        ).rowcount
        if changed != 1:
            self._raise_fence_failure(cur, lease, stamp)
        checkpoint = self._append_checkpoint(
            cur, lease.run_id, sequence, kind, state, payload, stamp
        )
        return {
            "run": _run_row(self._row(cur, lease.run_id)),
            "checkpoint": checkpoint,
            "lease": (
                None
                if release
                else AgentRunLease(
                    lease.run_id, lease.owner_id, lease.version + 1, lease.expires_at
                )
            ),
        }

    def _require_dispatched(
        self,
        cur: psycopg.Cursor,
        effect_id: str,
        run_id: str,
        *,
        owner_id: str | None = None,
    ) -> dict[str, Any]:
        """Refuse anything but an open effect, and say why. Caller is in `_write`.

        The guarantee itself is the `WHERE status = 'dispatched'` on the update
        that follows and the triggers under it. This is the sentence an operator
        reads when the guarantee fires.
        """
        effect = self._effect_row(cur, effect_id, run_id=run_id)
        status = effect["status"]
        if status != str(EffectStatus.DISPATCHED):
            raise AgentRunEffectFenced(
                f"effect {effect_id} is {status}: "
                + (
                    "a quarantined external effect is settled by a person, "
                    "never by a retry, a resume, or a second owner"
                    if status == str(EffectStatus.AMBIGUOUS)
                    else "a settled external effect never changes outcome"
                )
            )
        if owner_id is not None and effect["dispatched_by"] != owner_id:
            raise AgentRunEffectFenced(
                f"effect {effect_id} was dispatched by another process, which is "
                "the only one that can say what its call did"
            )
        return effect

    def _effect_row(
        self, cur: psycopg.Cursor, effect_id: str, run_id: str | None = None
    ) -> dict[str, Any]:
        row = cur.execute(
            "SELECT * FROM agent_run_effects WHERE user_id = %s AND effect_id = %s",
            (self._user_id, str(effect_id)),
        ).fetchone()
        if row is None or (run_id is not None and str(row["run_id"]) != str(run_id)):
            raise KeyError(effect_id)
        return _effect_row(row)

    def _check_fence(
        self,
        cur: psycopg.Cursor,
        row: dict[str, Any],
        lease: AgentRunLease,
        stamp: str,
    ) -> None:
        if (
            row["lease_owner"] is not None
            and str(row["lease_owner"]) == lease.owner_id
            and row["lease_expires_at"] is not None
            and str(row["lease_expires_at"]) > stamp
            and int(row["version"]) == lease.version
        ):
            return
        self._raise_fence_failure(cur, lease, stamp)

    def _raise_fence_failure(
        self, cur: psycopg.Cursor, lease: AgentRunLease, stamp: str
    ) -> None:
        """Say which half of the compare-and-swap failed, without guessing."""
        row = self._row(cur, lease.run_id)
        if row is None:
            raise KeyError(lease.run_id)
        if row["lease_owner"] is None or str(row["lease_owner"]) != lease.owner_id:
            raise AgentRunLeaseLost(lease.run_id)
        if row["lease_expires_at"] is None or str(row["lease_expires_at"]) <= stamp:
            raise AgentRunLeaseLost(lease.run_id)
        if int(row["version"]) != lease.version:
            raise AgentRunVersionConflict(lease.run_id)
        raise AgentRunLeaseLost(lease.run_id)


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


def _run_row(row: dict[str, Any]) -> dict[str, Any]:
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


def _effect_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "effect_id": str(row["effect_id"]),
        "run_id": str(row["run_id"]),
        "tool_name": str(row["tool_name"]),
        "tool_call_id": row["tool_call_id"],
        "approval_id": row["approval_id"],
        "replay_class": str(row["replay_class"]),
        "arguments_fingerprint": str(row["arguments_fingerprint"]),
        "status": str(row["status"]),
        "dispatched_by": str(row["dispatched_by"]),
        "dispatched_at": str(row["dispatched_at"]),
        "settled_at": row["settled_at"],
        "outcome_ref": row["outcome_ref"],
        "reason": row["reason"],
        "resolved_by": row["resolved_by"],
        "resolved_note": row["resolved_note"],
        "resolved_at": row["resolved_at"],
    }


def _checkpoint_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": str(row["run_id"]),
        "sequence": int(row["sequence"]),
        "kind": str(row["kind"]),
        "state": str(row["state"]),
        "payload": json_object(row["payload"]),
        "created_at": str(row["created_at"]),
    }
