"""Sourcecado Doctor: inspect local state before a build, an update, or a recovery.

Doctor is diagnostic by default. It opens no model, no connector, and no socket;
every database is read through a read-only handle, so a check can never write.

A repair runs only when it is asked for, only after a timestamped backup, and
only for the narrow set of changes whose correct outcome is not a judgement
call: tightening a state file's permissions, dropping a single torn line a crash
left at the end of an append-only log, and applying a registered migration. A
corrupt row, an orphaned link, an interrupted approval, or anything touching a
person file or an external outcome is reported for review and left alone.

Output is bounded and redacted. Doctor reports store ids, versions, check names,
and counts. It never prints a row, a message body, a token, an authorization
header, or a path outside the state directory.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from importlib.util import find_spec
from pathlib import Path
from typing import Any

from coworker import migrations
from coworker.migrations import (
    BackupFailed,
    StoreKind,
    StoreStatus,
    open_readonly,
    state_root,
    store_path,
    store_present,
)

MAX_DETAIL_ROWS = 8
MAX_DETAIL_CHARS = 200
MAX_FINDINGS = 60
MAX_REPORT_CHARS = 16000
STATE_LABEL = "<state>"

# Runtime modules the sidecar cannot start without. Presence is resolved through
# the import system's finder, which locates a module without executing it.
REQUIRED_DEPENDENCIES = ("fastapi", "uvicorn", "httpx", "sqlite3")
OPTIONAL_DEPENDENCIES = ("mcp", "pypdf")

# Run statuses the schedule contract allows. Restated here so Doctor can read
# the runs table without importing the scheduler, which pulls in the agent loop.
KNOWN_RUN_STATUSES = frozenset(
    {"running", "success", "failed", "waiting_approval", "partial", "interrupted"}
)

_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(?P<key>(?:api[_-]?key|access[_-]?token|refresh[_-]?token|auth[_-]?token"
    r"|authorization|bearer|token|secret|password))"
    r"(?P<separator>\s*[:=]\s*)(?P<value>\S+)"
)
_PRIVATE_KEY = re.compile(
    r"-----BEGIN (?:(?:RSA|EC|DSA|OPENSSH|ENCRYPTED) )?PRIVATE KEY-----.*?"
    r"-----END (?:(?:RSA|EC|DSA|OPENSSH|ENCRYPTED) )?PRIVATE KEY-----",
    re.DOTALL,
)
# Issuer prefixes that mean "credential" on their own.
_CREDENTIAL_PREFIX = re.compile(
    r"(?i)^(?:sk-|pk-|rk-|ya29\.|1//|gh[pousr]_|github_pat_|xox[baprs]-|AKIA|ASIA|AIza)"
)
# Any long run that could be a bare token. Deciding is left to
# _looks_like_credential so that Sourcecado's own identifiers survive.
_TOKEN_RUN = re.compile(r"[A-Za-z0-9._\-+/]{24,}")


def _looks_like_credential(value: str) -> bool:
    """True for a bare secret, false for a Sourcecado identifier.

    The identifiers Doctor must keep printable — `per_<32 hex>`, `run_<32 hex>`,
    `receipt_<32 hex>`, a sha256, an ISO timestamp — draw on at most two of
    lowercase, uppercase, and digits. Issued credentials almost always mix all
    three, or announce themselves with a known prefix. Anything shorter than 24
    characters is left alone; it cannot carry a usable secret.
    """
    core = value.strip("._-+/")
    if len(core) < 24:
        return False
    if _CREDENTIAL_PREFIX.match(value):
        return True
    classes = sum(
        (
            any(character.islower() for character in core),
            any(character.isupper() for character in core),
            any(character.isdigit() for character in core),
        )
    )
    return classes >= 3


class Severity(StrEnum):
    INFO = "info"
    WARN = "warn"
    ERROR = "error"


class Repair(StrEnum):
    NONE = "none"
    AUTOMATIC = "automatic"
    REVIEW_REQUIRED = "review_required"


@dataclass(frozen=True)
class Finding:
    check: str
    store_id: str
    severity: Severity
    repair: Repair
    summary: str
    record_count: int = 0
    detail: tuple[str, ...] = ()
    # A blocking finding means Doctor cannot trust what it read, so no repair
    # may run at all. A merely serious finding — a corrupt row, an orphaned
    # link — still leaves an unrelated chmod safe to apply.
    blocking: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "check": self.check,
            "store_id": self.store_id,
            "severity": self.severity.value,
            "repair": self.repair.value,
            "summary": self.summary,
            "record_count": self.record_count,
            "detail": list(self.detail),
            "blocking": self.blocking,
        }


@dataclass(frozen=True)
class StoreReport:
    store_id: str
    kind: str
    version_channel: str
    present: bool
    version: int | None
    target_version: int
    status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "store_id": self.store_id,
            "kind": self.kind,
            "version_channel": self.version_channel,
            "present": self.present,
            "version": self.version,
            "target_version": self.target_version,
            "status": self.status,
        }


@dataclass(frozen=True)
class Dependency:
    name: str
    required: bool
    present: bool

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "required": self.required, "present": self.present}


@dataclass(frozen=True)
class ProposedRepair:
    action: str
    store_id: str
    description: str
    record_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "store_id": self.store_id,
            "description": self.description,
            "record_count": self.record_count,
        }


@dataclass(frozen=True)
class DoctorReport:
    generated_at: str
    stores: tuple[StoreReport, ...]
    findings: tuple[Finding, ...]
    dependencies: tuple[Dependency, ...]
    proposed_repairs: tuple[ProposedRepair, ...] = ()
    applied_repairs: tuple[str, ...] = ()
    backup_id: str | None = None

    @property
    def blocked(self) -> bool:
        return any(item.blocking for item in self.findings)

    @property
    def healthy(self) -> bool:
        return not any(item.severity is not Severity.INFO for item in self.findings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "state_root": STATE_LABEL,
            "healthy": self.healthy,
            "blocked": self.blocked,
            "backup_id": self.backup_id,
            "applied_repairs": list(self.applied_repairs),
            "stores": [item.to_dict() for item in self.stores],
            "findings": [item.to_dict() for item in self.findings],
            "dependencies": [item.to_dict() for item in self.dependencies],
            "proposed_repairs": [item.to_dict() for item in self.proposed_repairs],
        }

    def render(self) -> str:
        return _render(self)


# --- redaction and bounding ---------------------------------------------


def _relative(path: Path, root: Path) -> str:
    """Every path Doctor prints is anchored at the state directory."""
    try:
        return f"{STATE_LABEL}/{path.relative_to(root)}"
    except ValueError:
        return STATE_LABEL


def redact(text: str, root: Path) -> str:
    """Strip credentials and absolute paths, then bound the length."""
    value = str(text or "")
    value = value.replace(str(root), STATE_LABEL)
    value = value.replace(str(Path.home()), "<home>")
    value = _PRIVATE_KEY.sub("[redacted private key]", value)
    value = _SECRET_ASSIGNMENT.sub(
        lambda match: f"{match.group('key')}{match.group('separator')}[redacted]", value
    )
    # Backstop for a bare token sitting in a field Doctor prints verbatim, such
    # as a session id or an unexpected run status.
    value = _TOKEN_RUN.sub(
        lambda match: "[redacted]" if _looks_like_credential(match.group(0)) else match.group(0),
        value,
    )
    if len(value) > MAX_DETAIL_CHARS:
        value = value[: MAX_DETAIL_CHARS - 1] + "…"
    return value


def _bounded(lines: list[str], root: Path) -> tuple[str, ...]:
    """At most MAX_DETAIL_ROWS lines in total, counting the overflow marker."""
    if len(lines) <= MAX_DETAIL_ROWS:
        return tuple(redact(line, root) for line in lines)
    kept = [redact(line, root) for line in lines[: MAX_DETAIL_ROWS - 1]]
    kept.append(f"and {len(lines) - (MAX_DETAIL_ROWS - 1)} more")
    return tuple(kept)


@dataclass
class _Collector:
    root: Path
    findings: list[Finding] = field(default_factory=list)

    def add(
        self,
        check: str,
        store_id: str,
        severity: Severity,
        repair: Repair,
        summary: str,
        *,
        count: int = 0,
        detail: list[str] | None = None,
        blocking: bool = False,
    ) -> None:
        if len(self.findings) >= MAX_FINDINGS:
            return
        self.findings.append(
            Finding(
                check=check,
                store_id=store_id,
                severity=severity,
                repair=repair,
                summary=redact(summary, self.root),
                record_count=count,
                detail=_bounded(detail or [], self.root),
                blocking=blocking,
            )
        )


# --- reading state safely ------------------------------------------------


def _read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)


def _jsonl_files(root: Path, spec) -> list[Path]:
    if spec.kind is StoreKind.JSONL_DIR:
        return sorted(store_path(root, spec).glob("*.jsonl"))
    path = store_path(root, spec)
    return [path] if path.is_file() else []


@dataclass(frozen=True)
class _TornLog:
    path: Path
    torn_tail: bool
    torn_lines: int
    record_version_misses: int


def _scan_jsonl(path: Path, record_version: int | None) -> _TornLog:
    lines = _read_lines(path)
    torn_tail = False
    torn_lines = 0
    misses = 0
    for index, raw in enumerate(lines):
        text = raw.strip()
        if not text:
            continue
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            # A crash mid-append leaves exactly one unterminated final line.
            # Anything earlier is damage inside the log, not a torn tail.
            if index == len(lines) - 1 and not raw.endswith("\n"):
                torn_tail = True
            else:
                torn_lines += 1
            continue
        if (
            record_version is not None
            and isinstance(parsed, dict)
            and parsed.get("version") != record_version
        ):
            misses += 1
    return _TornLog(path, torn_tail, torn_lines, misses)


def _sqlite_rows(root: Path, spec, sql: str) -> list[sqlite3.Row]:
    conn = open_readonly(store_path(root, spec))
    try:
        return list(conn.execute(sql))
    finally:
        conn.close()


# --- individual checks ---------------------------------------------------


def _check_versions(collector: _Collector, plan) -> None:
    for item in plan.stores:
        spec = migrations.spec_for(item.store_id)
        if item.status is StoreStatus.UNSUPPORTED_FUTURE:
            collector.add(
                "store.version_ahead",
                item.store_id,
                Severity.ERROR,
                Repair.NONE,
                f"{item.store_id} was written by a newer Sourcecado "
                f"(version {item.from_version}, this build knows {spec.current_version}). "
                "Doctor will not touch it.",
                blocking=True,
            )
        elif item.status is StoreStatus.UNREADABLE:
            collector.add(
                "store.unreadable",
                item.store_id,
                Severity.ERROR,
                Repair.REVIEW_REQUIRED,
                f"{item.store_id} cannot be read well enough to state its version.",
                detail=[item.detail],
                blocking=True,
            )
        elif item.status is StoreStatus.MIGRATION_PATH_MISSING:
            collector.add(
                "store.migration_path_missing",
                item.store_id,
                Severity.ERROR,
                Repair.REVIEW_REQUIRED,
                f"{item.store_id} has no registered upgrade from version {item.from_version}.",
                blocking=True,
            )
        elif item.status is StoreStatus.PENDING:
            collector.add(
                "store.version_behind",
                item.store_id,
                Severity.WARN,
                Repair.AUTOMATIC,
                f"{item.store_id} is at version {item.from_version}, "
                f"current is {item.to_version}.",
                count=item.record_count,
                detail=[step.description for step in item.steps],
            )


def _check_sqlite_integrity(collector: _Collector, root: Path, spec) -> bool:
    """Returns whether the database is sound enough for the row-level checks."""
    try:
        conn = open_readonly(store_path(root, spec))
    except sqlite3.Error as exc:
        collector.add(
            "sqlite.integrity",
            spec.store_id,
            Severity.ERROR,
            Repair.REVIEW_REQUIRED,
            f"{spec.store_id} cannot be opened.",
            detail=[str(exc)],
            blocking=True,
        )
        return False
    try:
        rows = [str(row[0]) for row in conn.execute("PRAGMA integrity_check")]
    except sqlite3.DatabaseError as exc:
        collector.add(
            "sqlite.integrity",
            spec.store_id,
            Severity.ERROR,
            Repair.REVIEW_REQUIRED,
            f"{spec.store_id} failed its integrity check. Restore it from a backup.",
            detail=[str(exc)],
            blocking=True,
        )
        return False
    finally:
        conn.close()
    if rows != ["ok"]:
        collector.add(
            "sqlite.integrity",
            spec.store_id,
            Severity.ERROR,
            Repair.REVIEW_REQUIRED,
            f"{spec.store_id} failed its integrity check. Restore it from a backup.",
            count=len(rows),
            detail=rows,
            blocking=True,
        )
        return False
    return True


def _check_json_columns(collector: _Collector, root: Path, spec) -> None:
    conn = open_readonly(store_path(root, spec))
    broken: list[str] = []
    try:
        tables = migrations.table_names(conn)
        for table, column in spec.json_columns:
            if table not in tables or column not in migrations.column_names(conn, table):
                continue
            for row in conn.execute(
                f"SELECT rowid AS rid, {column} AS value FROM {table} "
                f"WHERE {column} IS NOT NULL"
            ):
                try:
                    json.loads(str(row["value"]))
                except (json.JSONDecodeError, TypeError):
                    broken.append(f"{table}.{column} rowid {row['rid']}")
    finally:
        conn.close()
    if broken:
        collector.add(
            "sqlite.corrupt_row",
            spec.store_id,
            Severity.ERROR,
            Repair.REVIEW_REQUIRED,
            f"{spec.store_id} holds rows whose JSON column will not parse. "
            "Repairing them means guessing what was meant.",
            count=len(broken),
            detail=broken,
        )


def _check_jsonl(collector: _Collector, root: Path, spec) -> list[Path]:
    torn_tails: list[Path] = []
    torn_lines: list[str] = []
    version_misses: list[str] = []
    for path in _jsonl_files(root, spec):
        scan = _scan_jsonl(path, spec.record_version)
        if scan.torn_tail:
            torn_tails.append(path)
        if scan.torn_lines:
            torn_lines.append(f"{_relative(path, root)}: {scan.torn_lines} lines")
        if scan.record_version_misses:
            version_misses.append(
                f"{_relative(path, root)}: {scan.record_version_misses} records"
            )
    if torn_tails:
        collector.add(
            "jsonl.torn_tail",
            spec.store_id,
            Severity.WARN,
            Repair.AUTOMATIC,
            "An append was cut off mid-line by a crash. The unterminated last "
            "line is already skipped on read and can be dropped.",
            count=len(torn_tails),
            detail=[_relative(path, root) for path in torn_tails],
        )
    if torn_lines:
        collector.add(
            "jsonl.torn_line",
            spec.store_id,
            Severity.ERROR,
            Repair.REVIEW_REQUIRED,
            "Damaged lines sit inside the log, not at its end. What they held "
            "cannot be reconstructed, so Doctor leaves them.",
            count=len(torn_lines),
            detail=torn_lines,
        )
    if version_misses:
        collector.add(
            "jsonl.record_version",
            spec.store_id,
            Severity.WARN,
            Repair.REVIEW_REQUIRED,
            f"Records are not at version {spec.record_version}.",
            count=len(version_misses),
            detail=version_misses,
        )
    return torn_tails


def _expected_mode(path: Path, spec) -> int:
    return spec.dir_mode if path.is_dir() else spec.file_mode


def _permission_drift(root: Path) -> list[tuple[Path, int, int]]:
    """Anything under the state directory that group or other can reach."""
    drift: list[tuple[Path, int, int]] = []
    root_mode = root.stat().st_mode & 0o777
    if root_mode & 0o077:
        drift.append((root, root_mode, migrations.DIR_MODE))
    for spec in migrations.REGISTRY:
        if not store_present(root, spec):
            continue
        path = store_path(root, spec)
        targets = [path]
        if spec.is_directory:
            targets.extend(sorted(path.rglob("*")))
        for target in targets:
            mode = target.stat().st_mode & 0o777
            expected = _expected_mode(target, spec)
            if mode & 0o077:
                drift.append((target, mode, expected))
    return drift


def _check_permissions(collector: _Collector, root: Path) -> list[tuple[Path, int, int]]:
    drift = _permission_drift(root)
    if drift:
        collector.add(
            "permissions.drift",
            "state_root",
            Severity.WARN,
            Repair.AUTOMATIC,
            "State files are readable or writable beyond their owner.",
            count=len(drift),
            detail=[
                f"{_relative(path, root)} is {oct(mode)}, expected {oct(expected)}"
                for path, mode, expected in drift
            ],
        )
    return drift


def _check_dependencies(collector: _Collector) -> tuple[Dependency, ...]:
    found: list[Dependency] = []
    for name in REQUIRED_DEPENDENCIES:
        found.append(Dependency(name=name, required=True, present=find_spec(name) is not None))
    for name in OPTIONAL_DEPENDENCIES:
        found.append(Dependency(name=name, required=False, present=find_spec(name) is not None))
    missing = [item.name for item in found if item.required and not item.present]
    if missing:
        collector.add(
            "dependencies.missing",
            "runtime",
            Severity.ERROR,
            Repair.REVIEW_REQUIRED,
            "Runtime dependencies are missing. Reinstall them with `make setup`.",
            count=len(missing),
            detail=missing,
        )
    optional_missing = [item.name for item in found if not item.required and not item.present]
    if optional_missing:
        collector.add(
            "dependencies.optional_missing",
            "runtime",
            Severity.INFO,
            Repair.NONE,
            "Optional dependencies are absent; the features that need them stay off.",
            count=len(optional_missing),
            detail=optional_missing,
        )
    return tuple(found)


def _is_timestamp(value: object) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return False
    return True


def _check_schedule(collector: _Collector, root: Path, spec) -> None:
    """Read-only consistency checks over jobs and runs.

    Nothing here needs a canonical Agent Run identity. Checks that do — a stale
    run owner, a dead lease, an impossible checkpoint transition — belong in
    `_check_run_ownership`, which stays empty until issue #63 lands that identity.
    """
    conn = open_readonly(store_path(root, spec))
    try:
        tables = migrations.table_names(conn)
        if not {"jobs", "runs"} <= tables:
            return
        run_columns = migrations.column_names(conn, "runs")
        orphans = [
            f"run {row['id']} points at job {row['job_id']}"
            for row in conn.execute(
                "SELECT id, job_id FROM runs WHERE job_id NOT IN (SELECT id FROM jobs)"
            )
        ]
        if orphans:
            collector.add(
                "schedule.orphaned_run",
                spec.store_id,
                Severity.WARN,
                Repair.REVIEW_REQUIRED,
                "Runs reference a job that no longer exists. A run is a record "
                "of work that happened, so Doctor never deletes one.",
                count=len(orphans),
                detail=orphans,
            )
        if "next_run_at" in migrations.column_names(conn, "jobs"):
            unparsable = [
                f"job {row['id']}"
                for row in conn.execute(
                    "SELECT id, next_run_at FROM jobs WHERE next_run_at IS NOT NULL"
                )
                if not _is_timestamp(row["next_run_at"])
            ]
            if unparsable:
                collector.add(
                    "schedule.unparsable_next_run",
                    spec.store_id,
                    Severity.WARN,
                    Repair.REVIEW_REQUIRED,
                    "Jobs carry a next run time that will not parse, so they "
                    "will never come due. Choosing a new time is an operator call.",
                    count=len(unparsable),
                    detail=unparsable,
                )
        unknown = [
            f"run {row['id']} status {str(row['status'])[:24]!r}"
            for row in conn.execute("SELECT id, status FROM runs")
            if str(row["status"]) not in KNOWN_RUN_STATUSES
        ]
        if unknown:
            collector.add(
                "schedule.unknown_run_status",
                spec.store_id,
                Severity.WARN,
                Repair.REVIEW_REQUIRED,
                "Runs carry a status outside the schedule contract.",
                count=len(unknown),
                detail=unknown,
            )
        if "finished_at" in run_columns:
            unfinished = [
                f"run {row['id']}"
                for row in conn.execute(
                    "SELECT id FROM runs WHERE status != 'running' AND finished_at IS NULL"
                )
            ]
            if unfinished:
                collector.add(
                    "schedule.unfinished_terminal_run",
                    spec.store_id,
                    Severity.WARN,
                    Repair.REVIEW_REQUIRED,
                    "Runs are in a final status but never recorded a finish time.",
                    count=len(unfinished),
                    detail=unfinished,
                )
    finally:
        conn.close()


def _check_run_ownership(collector: _Collector, root: Path, spec) -> None:
    """Extension point for issue #63.

    Stale run owner, dead lease, and impossible checkpoint transition checks all
    need the canonical Agent Run identity that #63 introduces. There is nothing
    on disk today that can distinguish an abandoned owner from a live one, so
    this deliberately reports nothing rather than guessing.
    """
    return


def _check_links(collector: _Collector, root: Path) -> None:
    conversation = migrations.spec_for("conversation_db")
    people = migrations.spec_for("people_db")
    if not store_present(root, conversation):
        return
    conn = open_readonly(store_path(root, conversation))
    try:
        tables = migrations.table_names(conn)
        sessions = {str(row["session_id"]) for row in conn.execute("SELECT session_id FROM sessions")}
        transcripts = {
            path.stem
            for path in (root / "conversations").glob("*.jsonl")
            if (root / "conversations").is_dir()
        }
        known = sessions | transcripts
        if "chat_queue" in tables:
            stranded = [
                f"session {str(row['session_id'])[:48]} has {row['n']} queued items"
                for row in conn.execute(
                    "SELECT session_id, COUNT(*) AS n FROM chat_queue GROUP BY session_id"
                )
                if str(row["session_id"]) not in known
            ]
            if stranded:
                collector.add(
                    "queue.orphaned_session",
                    conversation.store_id,
                    Severity.WARN,
                    Repair.REVIEW_REQUIRED,
                    "Queued messages belong to a session that no longer exists. "
                    "They hold text a person wrote, so Doctor never drops them.",
                    count=len(stranded),
                    detail=stranded,
                )
        inbox_columns = migrations.column_names(conn, "inbox") if "inbox" in tables else set()
        if "session_id" in inbox_columns:
            stranded = [
                f"approval {str(row['id'])[:48]}"
                for row in conn.execute(
                    "SELECT id, session_id FROM inbox WHERE session_id IS NOT NULL"
                )
                if str(row["session_id"]) not in known
            ]
            if stranded:
                collector.add(
                    "approval.orphaned_session",
                    conversation.store_id,
                    Severity.WARN,
                    Repair.REVIEW_REQUIRED,
                    "Approvals point at a session that no longer exists.",
                    count=len(stranded),
                    detail=stranded,
                )
        if "original_call_id" in inbox_columns:
            broken = [
                f"approval {str(row['id'])[:48]}"
                for row in conn.execute(
                    "SELECT id FROM inbox WHERE original_call_id IS NOT NULL "
                    "AND original_call_id NOT IN (SELECT id FROM inbox)"
                )
            ]
            if broken:
                collector.add(
                    "approval.orphaned_chain",
                    conversation.store_id,
                    Severity.WARN,
                    Repair.REVIEW_REQUIRED,
                    "Retried approvals point at an original approval that is gone.",
                    count=len(broken),
                    detail=broken,
                )
        if "execution_status" in inbox_columns:
            unresolved = [
                f"approval {str(row['id'])[:48]} is {str(row['execution_status'])}"
                for row in conn.execute(
                    "SELECT id, execution_status FROM inbox WHERE execution_status IN "
                    "('interrupted', 'expired')"
                )
            ]
            if unresolved:
                collector.add(
                    "approval.interrupted",
                    conversation.store_id,
                    Severity.WARN,
                    Repair.REVIEW_REQUIRED,
                    "Approvals were authorised but never reported an outcome. "
                    "Whether the external action happened is unknown, so this "
                    "must be verified by hand.",
                    count=len(unresolved),
                    detail=unresolved,
                )
        if "chat_queue" in tables:
            interrupted = [
                f"session {str(row['session_id'])[:48]} has {row['n']} interrupted items"
                for row in conn.execute(
                    "SELECT session_id, COUNT(*) AS n FROM chat_queue "
                    "WHERE state = 'interrupted' GROUP BY session_id"
                )
            ]
            if interrupted:
                collector.add(
                    "queue.interrupted",
                    conversation.store_id,
                    Severity.WARN,
                    Repair.REVIEW_REQUIRED,
                    "Queued runs were cut off by a restart and are paused for review.",
                    count=len(interrupted),
                    detail=interrupted,
                )
    finally:
        conn.close()

    if not store_present(root, people):
        return
    conn = open_readonly(store_path(root, people))
    try:
        tables = migrations.table_names(conn)
        if "session_people" in tables:
            stranded = [
                f"binding for session {str(row['session_id'])[:48]}"
                for row in conn.execute("SELECT session_id FROM session_people")
                if str(row["session_id"]) not in known
            ]
            if stranded:
                collector.add(
                    "person.orphaned_session_binding",
                    people.store_id,
                    Severity.WARN,
                    Repair.REVIEW_REQUIRED,
                    "Person bindings point at a session that no longer exists.",
                    count=len(stranded),
                    detail=stranded,
                )
        if {"events", "people"} <= tables:
            orphans = [
                f"event {str(row['event_id'])[:48]}"
                for row in conn.execute(
                    "SELECT event_id FROM events WHERE person_id NOT IN "
                    "(SELECT person_id FROM people)"
                )
            ]
            if orphans:
                collector.add(
                    "person.orphaned_event",
                    people.store_id,
                    Severity.WARN,
                    Repair.REVIEW_REQUIRED,
                    "Ledger events point at a person file that is gone. Person "
                    "history is never deleted by Doctor.",
                    count=len(orphans),
                    detail=orphans,
                )
    finally:
        conn.close()


# --- diagnose ------------------------------------------------------------


@dataclass
class _Scan:
    report: DoctorReport
    plan: Any
    torn_tails: list[Path]
    drift: list[tuple[Path, int, int]]


def _scan(root: Path) -> _Scan:
    root = Path(root).expanduser()
    collector = _Collector(root)
    plan = migrations.plan_migrations(root)
    _check_versions(collector, plan)

    unreadable = {
        item.store_id
        for item in plan.stores
        if item.status in (StoreStatus.UNREADABLE, StoreStatus.UNSUPPORTED_FUTURE)
    }
    torn_tails: list[Path] = []
    for spec in migrations.REGISTRY:
        if not store_present(root, spec) or spec.store_id in unreadable:
            continue
        if spec.kind is StoreKind.SQLITE:
            if _check_sqlite_integrity(collector, root, spec):
                _check_json_columns(collector, root, spec)
                if spec.store_id == "conversation_db":
                    _check_schedule(collector, root, spec)
                    _check_run_ownership(collector, root, spec)
        elif spec.kind in (StoreKind.JSONL_DIR, StoreKind.JSONL_LOG):
            torn_tails.extend(_check_jsonl(collector, root, spec))
    if not unreadable:
        _check_links(collector, root)
    drift = _check_permissions(collector, root)
    dependencies = _check_dependencies(collector)

    stores = tuple(
        StoreReport(
            store_id=item.store_id,
            kind=item.kind.value,
            version_channel=migrations.spec_for(item.store_id).version_channel.value,
            present=item.status is not StoreStatus.ABSENT,
            version=item.from_version,
            target_version=item.to_version,
            status=item.status.value,
        )
        for item in plan.stores
    )
    findings = tuple(collector.findings)
    blocked = any(item.blocking for item in findings)
    proposed = () if blocked else _propose(plan, torn_tails, drift, root)
    return _Scan(
        report=DoctorReport(
            generated_at=datetime.now(UTC).isoformat(),
            stores=stores,
            findings=findings,
            dependencies=dependencies,
            proposed_repairs=proposed,
        ),
        plan=plan,
        torn_tails=torn_tails,
        drift=drift,
    )


def _propose(
    plan, torn_tails: list[Path], drift: list, root: Path
) -> tuple[ProposedRepair, ...]:
    proposed: list[ProposedRepair] = []
    for item in plan.pending:
        proposed.append(
            ProposedRepair(
                action="migration.apply",
                store_id=item.store_id,
                description=f"Upgrade from version {item.from_version} to {item.to_version}: "
                + "; ".join(step.description for step in item.steps),
                record_count=item.record_count,
            )
        )
    if torn_tails:
        proposed.append(
            ProposedRepair(
                action="jsonl.truncate_torn_tail",
                store_id="jsonl_logs",
                description="Drop the unterminated final line from "
                + ", ".join(_relative(path, root) for path in torn_tails[:MAX_DETAIL_ROWS]),
                record_count=len(torn_tails),
            )
        )
    if drift:
        proposed.append(
            ProposedRepair(
                action="permissions.tighten",
                store_id="state_root",
                description="Restore owner-only permissions on "
                f"{len(drift)} state file(s) and directory(ies)",
                record_count=len(drift),
            )
        )
    return tuple(proposed)


def diagnose(root: str | Path | None = None) -> DoctorReport:
    """Inspect state and report. Never writes."""
    return _scan(Path(root) if root is not None else state_root()).report


# --- repair --------------------------------------------------------------


def _truncate_torn_tail(path: Path) -> bool:
    raw = path.read_text(encoding="utf-8", errors="replace")
    lines = raw.splitlines(keepends=True)
    if not lines or lines[-1].endswith("\n"):
        return False
    kept = "".join(lines[:-1])
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, migrations.FILE_MODE)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(kept)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(path, migrations.FILE_MODE)
    return True


def repair(root: str | Path | None = None) -> DoctorReport:
    """Apply only the deterministic safe repairs, after a backup.

    Anything reported for review is left exactly as it was found. Doctor expects
    the sidecar to be stopped; it does not coordinate with a running process.
    """
    root = Path(root) if root is not None else state_root()
    scan = _scan(root)
    report = scan.report
    if report.blocked or not report.proposed_repairs:
        return report

    store_ids = {item.store_id for item in scan.plan.pending}
    if scan.torn_tails or scan.drift:
        store_ids.update(spec.store_id for spec in migrations.REGISTRY)
    try:
        backup = migrations.create_backup(root, sorted(store_ids), reason="doctor repair")
    except BackupFailed as exc:
        findings = report.findings + (
            Finding(
                check="repair.backup_failed",
                store_id="state_root",
                severity=Severity.ERROR,
                repair=Repair.REVIEW_REQUIRED,
                blocking=True,
                summary=redact(f"No repair ran: {exc}", root),
            ),
        )
        return DoctorReport(
            generated_at=report.generated_at,
            stores=report.stores,
            findings=findings,
            dependencies=report.dependencies,
            proposed_repairs=(),
        )

    applied: list[str] = []
    if scan.plan.pending:
        outcome = migrations.apply_migrations(root, plan=scan.plan, backup=backup)
        if outcome.error is not None:
            findings = report.findings + (
                Finding(
                    check="repair.migration_failed",
                    store_id="state_root",
                    severity=Severity.ERROR,
                    repair=Repair.REVIEW_REQUIRED,
                    summary=redact(
                        f"A migration failed and was rolled back: {outcome.error}", root
                    ),
                ),
            )
            return DoctorReport(
                generated_at=report.generated_at,
                stores=report.stores,
                findings=findings,
                dependencies=report.dependencies,
                proposed_repairs=(),
                backup_id=backup.backup_id,
            )
        if outcome.applied:
            applied.append("migration.apply")
    if any(_truncate_torn_tail(path) for path in scan.torn_tails):
        applied.append("jsonl.truncate_torn_tail")
    for path, _mode, expected in scan.drift:
        os.chmod(path, expected)
    if scan.drift:
        applied.append("permissions.tighten")

    after = _scan(root).report
    return DoctorReport(
        generated_at=after.generated_at,
        stores=after.stores,
        findings=after.findings,
        dependencies=after.dependencies,
        proposed_repairs=after.proposed_repairs,
        applied_repairs=tuple(applied),
        backup_id=backup.backup_id,
    )


def backups(root: str | Path | None = None) -> list[dict[str, Any]]:
    return migrations.list_backups(Path(root) if root is not None else state_root())


# --- rendering -----------------------------------------------------------


def _render(report: DoctorReport) -> str:
    lines: list[str] = ["Sourcecado Doctor", f"state directory {STATE_LABEL}", ""]
    lines.append("stores")
    for item in report.stores:
        version = "-" if item.version is None else str(item.version)
        presence = item.status if item.present else "absent"
        lines.append(
            f"  {item.store_id:<26} version {version:>3} of {item.target_version:<3} {presence}"
        )
    missing = [item.name for item in report.dependencies if item.required and not item.present]
    lines.append("")
    lines.append(
        "dependencies  all required present"
        if not missing
        else f"dependencies  missing {', '.join(missing)}"
    )
    lines.append("")
    if not report.findings:
        lines.append("findings      none")
    else:
        lines.append("findings")
        for item in report.findings:
            lines.append(
                f"  [{item.severity.value}] {item.check} ({item.store_id}) "
                f"{item.record_count} affected — {item.repair.value}"
            )
            lines.append(f"      {item.summary}")
            for entry in item.detail:
                lines.append(f"      - {entry}")
    lines.append("")
    if report.applied_repairs:
        lines.append(f"applied       {', '.join(report.applied_repairs)}")
        lines.append(f"backup        {STATE_LABEL}/{migrations.BACKUPS_DIR_NAME}/{report.backup_id}")
    elif report.blocked:
        lines.append("repairs       blocked; Doctor changed nothing")
    elif report.proposed_repairs:
        lines.append("proposed repairs (dry run, nothing changed)")
        for item in report.proposed_repairs:
            lines.append(
                f"  {item.action} ({item.store_id}) affects {item.record_count} record(s)"
            )
            lines.append(f"      {item.description}")
        lines.append("")
        lines.append("run `make doctor-repair` to apply these after a backup")
    else:
        lines.append("repairs       none needed")
    rendered = "\n".join(lines) + "\n"
    if len(rendered) > MAX_REPORT_CHARS:
        rendered = rendered[: MAX_REPORT_CHARS - 32].rstrip() + "\n… output truncated\n"
    return rendered


def _render_backups(found: list[dict[str, Any]]) -> str:
    if not found:
        return "no backups\n"
    lines = ["backups (newest first)"]
    for item in found:
        stores = ", ".join(sorted(entry["store_id"] for entry in item["entries"]))
        lines.append(f"  {item['backup_id']}  {item['created_at']}  {item['reason']}")
        lines.append(f"      {stores}")
    lines.append("")
    lines.append("restore one with `python -m coworker.doctor restore <backup-id>`")
    return "\n".join(lines) + "\n"


# --- command line --------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m coworker.doctor",
        description="Inspect and safely repair Sourcecado's local state.",
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="check",
        choices=("check", "repair", "backups", "restore"),
        help="check (default, changes nothing), repair, backups, restore",
    )
    parser.add_argument("backup_id", nargs="?", help="backup to restore")
    parser.add_argument("--state", help="state directory (defaults to CLUB_STATE_DIR)")
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = parser.parse_args(argv)
    root = Path(args.state).expanduser() if args.state else state_root()

    if args.command == "backups":
        found = backups(root)
        print(json.dumps(found, indent=2) if args.json else _render_backups(found), end="")
        return 0
    if args.command == "restore":
        if not args.backup_id:
            print("restore needs a backup id; run `backups` to list them", file=sys.stderr)
            return 2
        try:
            result = migrations.restore_backup(root, args.backup_id)
        except BackupFailed as exc:
            print(redact(str(exc), root), file=sys.stderr)
            return 2
        print(
            f"restored {', '.join(result['restored']) or 'nothing'}; "
            f"previous state saved as {result['safety_backup_id']}"
        )
        return 0

    report = repair(root) if args.command == "repair" else diagnose(root)
    print(json.dumps(report.to_dict(), indent=2) if args.json else report.render(), end="")
    if report.blocked:
        return 2
    return 0 if report.healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
