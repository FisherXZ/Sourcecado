"""The one versioned migration registry for Sourcecado's durable local state.

Every active SQLite database and durable JSON/JSONL store is declared here once,
with the version it is currently at and the ordered steps that bring an older
copy forward. Doctor, packaging, updates, and rollback all read this registry;
create-if-missing and column-existence probes inside a store constructor are not
release versioning and are not consulted here.

Where the version lives depends on what the format can carry:

- SQLite stores use `PRAGMA user_version`, which needs no table of our own and
  commits inside the same transaction as the migration.
- JSON documents use the top-level `"version"` key those stores already write.
- Append-only JSONL logs, opaque secret files, and config documents cannot carry
  a document header without changing their write path, so their version is
  recorded in `state_versions.json` next to the stores.

Version 0 means "on disk before this registry existed". The 0 to 1 step for each
store adopts it: it completes the shape that store's constructor grew by hand,
then records version 1.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from coworker.mcp import SCHEMA_VERSION as MCP_CONFIG_VERSION
from coworker.people import SCHEMA_VERSION as PEOPLE_DB_VERSION
from coworker.secrets import SCHEMA_VERSION as SECRETS_VERSION
from coworker.store import SCHEMA_VERSION as CONVERSATION_DB_VERSION
from coworker.store import TRANSCRIPT_VERSION
from coworker.workspace import WorkspaceGrantStore
from coworker.workspace_audit import SCHEMA_VERSION as RECEIPTS_VERSION
from coworker.workspace_policy import HostApprovalStore
from coworker.workspace_runtime import DirectoryRequestStore
from coworker.workspace_shell import ShellTaskStore

MANIFEST_NAME = "state_versions.json"
MANIFEST_VERSION = 1
BACKUPS_DIR_NAME = "backups"
BACKUP_MANIFEST_NAME = "manifest.json"
FILE_MODE = 0o600
DIR_MODE = 0o700


class StoreUnreadable(Exception):
    """The store exists but cannot be read well enough to state its version."""


class BackupFailed(Exception):
    """A backup could not be written, so no repair may proceed."""


class StoreKind(StrEnum):
    SQLITE = "sqlite"
    JSON_DOCUMENT = "json_document"
    JSONL_LOG = "jsonl_log"
    JSONL_DIR = "jsonl_dir"
    OPAQUE_FILE = "opaque_file"
    DIRECTORY = "directory"


class VersionChannel(StrEnum):
    SQLITE_USER_VERSION = "sqlite_user_version"
    JSON_VERSION_KEY = "json_version_key"
    MANIFEST = "manifest"
    NONE = "none"


class StoreStatus(StrEnum):
    ABSENT = "absent"
    CURRENT = "current"
    PENDING = "pending"
    UNSUPPORTED_FUTURE = "unsupported_future"
    UNREADABLE = "unreadable"
    MIGRATION_PATH_MISSING = "migration_path_missing"


BLOCKING_STATUSES = frozenset(
    {
        StoreStatus.UNSUPPORTED_FUTURE,
        StoreStatus.UNREADABLE,
        StoreStatus.MIGRATION_PATH_MISSING,
    }
)


@dataclass
class MigrationContext:
    """What one migration step is handed. `connection` is set for SQLite stores."""

    root: Path
    spec: StoreSpec
    path: Path
    connection: sqlite3.Connection | None = None


@dataclass(frozen=True)
class Migration:
    from_version: int
    to_version: int
    description: str
    count: Callable[[MigrationContext], int]
    apply: Callable[[MigrationContext], int]


@dataclass(frozen=True)
class StoreSpec:
    store_id: str
    kind: StoreKind
    relative_path: str
    current_version: int
    version_channel: VersionChannel
    description: str
    migrations: tuple[Migration, ...] = ()
    container_key: str | None = None
    record_version: int | None = None
    secret_bearing: bool = False
    json_columns: tuple[tuple[str, str], ...] = ()
    file_mode: int = FILE_MODE
    dir_mode: int = DIR_MODE

    @property
    def is_directory(self) -> bool:
        return self.kind in (StoreKind.DIRECTORY, StoreKind.JSONL_DIR)


@dataclass(frozen=True)
class StorePlan:
    store_id: str
    kind: StoreKind
    status: StoreStatus
    from_version: int | None
    to_version: int
    steps: tuple[Migration, ...]
    record_count: int
    detail: str = ""


@dataclass(frozen=True)
class MigrationPlan:
    stores: tuple[StorePlan, ...]

    @property
    def pending(self) -> tuple[StorePlan, ...]:
        return tuple(item for item in self.stores if item.status is StoreStatus.PENDING)

    @property
    def blocked(self) -> bool:
        return any(item.status in BLOCKING_STATUSES for item in self.stores)

    @property
    def blockers(self) -> tuple[StorePlan, ...]:
        return tuple(item for item in self.stores if item.status in BLOCKING_STATUSES)


@dataclass(frozen=True)
class AppliedStep:
    store_id: str
    from_version: int
    to_version: int
    description: str
    record_count: int


@dataclass(frozen=True)
class MigrationOutcome:
    applied: tuple[AppliedStep, ...] = ()
    backup_id: str | None = None
    rolled_back: tuple[str, ...] = ()
    error: str | None = None
    blocked: bool = False


@dataclass(frozen=True)
class Backup:
    backup_id: str
    path: Path
    manifest: dict[str, Any] = field(default_factory=dict)


# --- filesystem helpers --------------------------------------------------


def state_root() -> Path:
    """The local state directory. Same contract as coworker.server.state_dir."""
    override = os.environ.get("CLUB_STATE_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "club"


def store_path(root: str | Path, spec: StoreSpec) -> Path:
    return Path(root).expanduser() / spec.relative_path


def _write_private(path: Path, payload: str, *, mode: int = FILE_MODE) -> None:
    """Write atomically, owner-only, without leaving a readable temp behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw = tempfile.mkstemp(prefix=f".{path.name}-", suffix=".tmp", dir=path.parent)
    temp_path = Path(raw)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        temp_path.replace(path)
        os.chmod(path, mode)
    finally:
        temp_path.unlink(missing_ok=True)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def open_readonly(path: Path) -> sqlite3.Connection:
    """Open a SQLite file without creating it and without writing to it."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }


def column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _row_count(conn: sqlite3.Connection, table: str) -> int:
    if table not in table_names(conn):
        return 0
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


# --- version reading and writing -----------------------------------------


def read_manifest(root: str | Path) -> dict[str, Any]:
    path = Path(root).expanduser() / MANIFEST_NAME
    if not path.is_file():
        return {"version": MANIFEST_VERSION, "stores": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StoreUnreadable(f"{MANIFEST_NAME} is unreadable") from exc
    if not isinstance(data, dict) or not isinstance(data.get("stores"), dict):
        raise StoreUnreadable(f"{MANIFEST_NAME} has an unsupported shape")
    return data


def _write_manifest(root: Path, data: dict[str, Any]) -> None:
    _write_private(
        root / MANIFEST_NAME,
        json.dumps(data, indent=2, sort_keys=True) + "\n",
    )


def store_present(root: str | Path, spec: StoreSpec) -> bool:
    path = store_path(root, spec)
    return path.is_dir() if spec.is_directory else path.is_file()


def read_version(root: str | Path, spec: StoreSpec) -> int | None:
    """The version this store is at, or None when the store is not on disk."""
    root = Path(root).expanduser()
    if not store_present(root, spec):
        return None
    path = store_path(root, spec)
    if spec.version_channel is VersionChannel.NONE:
        return None
    if spec.version_channel is VersionChannel.SQLITE_USER_VERSION:
        try:
            conn = open_readonly(path)
        except sqlite3.Error as exc:
            raise StoreUnreadable(f"{spec.store_id} cannot be opened") from exc
        try:
            return int(conn.execute("PRAGMA user_version").fetchone()[0])
        except sqlite3.DatabaseError as exc:
            raise StoreUnreadable(f"{spec.store_id} is not a readable database") from exc
        finally:
            conn.close()
    if spec.version_channel is VersionChannel.JSON_VERSION_KEY:
        document = read_json_document(root, spec)
        version = document.get("version", 0)
        if not isinstance(version, int) or isinstance(version, bool) or version < 0:
            raise StoreUnreadable(f"{spec.store_id} has a non-integer version")
        return version
    manifest = read_manifest(root)
    recorded = manifest["stores"].get(spec.store_id, 0)
    if not isinstance(recorded, int) or isinstance(recorded, bool) or recorded < 0:
        raise StoreUnreadable(f"{spec.store_id} has a non-integer recorded version")
    return recorded


def read_json_document(root: str | Path, spec: StoreSpec) -> dict[str, Any]:
    path = store_path(root, spec)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StoreUnreadable(f"{spec.store_id} is not readable JSON") from exc
    if not isinstance(data, dict):
        raise StoreUnreadable(f"{spec.store_id} is not a JSON object")
    if spec.container_key is not None and not isinstance(
        data.get(spec.container_key, []), list
    ):
        raise StoreUnreadable(f"{spec.store_id} has an unsupported shape")
    return data


def _record_manifest_version(root: Path, store_id: str, version: int) -> None:
    manifest = read_manifest(root)
    manifest["version"] = MANIFEST_VERSION
    manifest["stores"][store_id] = version
    _write_manifest(root, manifest)


def _write_version(root: Path, spec: StoreSpec, version: int) -> None:
    if spec.version_channel is VersionChannel.SQLITE_USER_VERSION:
        return  # stamped inside the step's own transaction
    if spec.version_channel is VersionChannel.JSON_VERSION_KEY:
        document = read_json_document(root, spec)
        document["version"] = version
        _write_private(
            store_path(root, spec),
            json.dumps(document, indent=2, sort_keys=True) + "\n",
        )
        return
    if spec.version_channel is VersionChannel.MANIFEST:
        _record_manifest_version(root, spec.store_id, version)


# --- migration steps -----------------------------------------------------

# The conversation schema that store.py's constructor grew column by column. It
# is restated here because the registry, not the constructor, is the release
# contract for what version 1 means.
_CONVERSATION_ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("jobs", "next_run_at", "TEXT"),
    ("jobs", "name", "TEXT"),
    ("jobs", "template_id", "TEXT"),
    ("jobs", "cadence", "TEXT"),
    ("runs", "started_at", "TEXT"),
    ("runs", "finished_at", "TEXT"),
    ("runs", "duration_ms", "INTEGER"),
    ("runs", "summary", "TEXT"),
    ("runs", "artifacts", "TEXT"),
    ("runs", "session_id", "TEXT"),
    ("runs", "waiting_approval_count", "INTEGER DEFAULT 0"),
    ("sessions", "pinned", "INTEGER NOT NULL DEFAULT 0"),
    ("sessions", "opened_at", "TEXT"),
    ("inbox", "actor", "TEXT"),
    ("inbox", "requested_at", "TEXT"),
    ("inbox", "resolved_at", "TEXT"),
    ("inbox", "scope", "TEXT DEFAULT 'once'"),
    ("inbox", "execution_status", "TEXT DEFAULT 'pending'"),
    ("inbox", "execution_error", "TEXT"),
    ("inbox", "execution_claimant", "TEXT"),
    ("inbox", "execution_result", "TEXT"),
    ("inbox", "expires_at", "TEXT"),
    ("inbox", "reason", "TEXT"),
    ("inbox", "session_id", "TEXT"),
    ("inbox", "run_id", "TEXT"),
    ("inbox", "message_id", "TEXT"),
    ("inbox", "part_id", "TEXT"),
    ("inbox", "recovery_command_id", "TEXT"),
    ("inbox", "original_call_id", "TEXT"),
    ("inbox", "resource", "TEXT"),
)

_CONVERSATION_ADDED_TABLES = {
    "chat_queue": """
        CREATE TABLE IF NOT EXISTS chat_queue (
            session_id TEXT NOT NULL,
            id TEXT NOT NULL,
            text TEXT NOT NULL,
            position INTEGER NOT NULL,
            state TEXT NOT NULL,
            error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (session_id, id)
        );
        CREATE INDEX IF NOT EXISTS chat_queue_session_position
            ON chat_queue(session_id, position);
    """,
    "queue_commands": """
        CREATE TABLE IF NOT EXISTS queue_commands (
            session_id TEXT NOT NULL,
            command_id TEXT NOT NULL,
            command_type TEXT NOT NULL,
            item_id TEXT,
            acknowledgement TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (session_id, command_id)
        );
    """,
    "queue_sessions": """
        CREATE TABLE IF NOT EXISTS queue_sessions (
            session_id TEXT PRIMARY KEY,
            paused INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        );
    """,
    "recovery_commands": """
        CREATE TABLE IF NOT EXISTS recovery_commands (
            session_id TEXT NOT NULL,
            command_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (session_id, command_id)
        );
    """,
}

_PEOPLE_ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("people", "version", "INTEGER NOT NULL DEFAULT 1"),
    ("people", "outcome", "TEXT"),
    ("people", "deleted_at", "TEXT"),
)

_PEOPLE_ADDED_TABLES = {
    "session_people": """
        CREATE TABLE IF NOT EXISTS session_people (
            session_id TEXT PRIMARY KEY,
            person_id TEXT NOT NULL
        );
    """,
    "person_attachments": """
        CREATE TABLE IF NOT EXISTS person_attachments (
            id TEXT PRIMARY KEY,
            person_id TEXT NOT NULL,
            record_type TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            fields_json TEXT NOT NULL,
            restricted INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(person_id, record_type, idempotency_key)
        );
    """,
    "person_versions": """
        CREATE TABLE IF NOT EXISTS person_versions (
            person_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            person_json TEXT NOT NULL,
            attachments_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (person_id, version)
        );
    """,
}


def _sqlite_adoption_count(
    context: MigrationContext,
    added_columns: Sequence[tuple[str, str, str]],
    added_tables: dict[str, str],
) -> int:
    conn = context.connection
    assert conn is not None
    present_tables = table_names(conn)
    touched_tables = {
        table
        for table, column, _definition in added_columns
        if table in present_tables and column not in column_names(conn, table)
    }
    rows = sum(_row_count(conn, table) for table in touched_tables)
    return rows + sum(1 for table in added_tables if table not in present_tables)


def _sqlite_add_columns_and_tables(
    conn: sqlite3.Connection,
    added_columns: Sequence[tuple[str, str, str]],
    added_tables: dict[str, str],
) -> None:
    present_tables = table_names(conn)
    for table, column, definition in added_columns:
        if table not in present_tables:
            continue
        if column in column_names(conn, table):
            continue
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    for table, ddl in added_tables.items():
        conn.executescript(ddl)


def _adopt_conversation_db(context: MigrationContext) -> int:
    conn = context.connection
    assert conn is not None
    touched = _sqlite_adoption_count(
        context, _CONVERSATION_ADDED_COLUMNS, _CONVERSATION_ADDED_TABLES
    )
    _sqlite_add_columns_and_tables(
        conn, _CONVERSATION_ADDED_COLUMNS, _CONVERSATION_ADDED_TABLES
    )
    conn.execute(
        """
        UPDATE jobs SET
            name = COALESCE(name, prompt),
            template_id = COALESCE(template_id, 'legacy'),
            cadence = COALESCE(cadence, cron)
        """
    )
    conn.execute(
        """
        UPDATE runs SET
            started_at = COALESCE(started_at, created_at),
            finished_at = CASE
                WHEN status = 'running' THEN finished_at
                ELSE COALESCE(finished_at, created_at)
            END,
            duration_ms = COALESCE(duration_ms, 0),
            summary = COALESCE(summary, result, ''),
            artifacts = COALESCE(artifacts, '[]'),
            session_id = COALESCE(session_id, 'sched-' || job_id),
            waiting_approval_count = COALESCE(waiting_approval_count, 0)
        """
    )
    conn.execute("UPDATE sessions SET opened_at = updated_at WHERE opened_at IS NULL")
    conn.execute(
        """
        UPDATE inbox SET
            requested_at = COALESCE(requested_at, created_at),
            scope = COALESCE(scope, 'once'),
            execution_status = COALESCE(execution_status, 'pending')
        """
    )
    return touched


def _count_conversation_db(context: MigrationContext) -> int:
    return _sqlite_adoption_count(
        context, _CONVERSATION_ADDED_COLUMNS, _CONVERSATION_ADDED_TABLES
    )


def _adopt_people_db(context: MigrationContext) -> int:
    conn = context.connection
    assert conn is not None
    touched = _sqlite_adoption_count(context, _PEOPLE_ADDED_COLUMNS, _PEOPLE_ADDED_TABLES)
    _sqlite_add_columns_and_tables(conn, _PEOPLE_ADDED_COLUMNS, _PEOPLE_ADDED_TABLES)
    conn.execute("UPDATE people SET version = COALESCE(version, 1)")
    return touched


def _count_people_db(context: MigrationContext) -> int:
    return _sqlite_adoption_count(context, _PEOPLE_ADDED_COLUMNS, _PEOPLE_ADDED_TABLES)


# DriveIngestionStore grew `work_revision` onto jobs after the first ship. The
# constructor still adds it with a column-existence probe; version 1 is that
# column being present and recorded.
_DRIVE_INGESTION_ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("drive_ingestion_jobs", "work_revision", "INTEGER NOT NULL DEFAULT 1"),
)


def _adopt_drive_ingestion_db(context: MigrationContext) -> int:
    conn = context.connection
    assert conn is not None
    touched = _sqlite_adoption_count(context, _DRIVE_INGESTION_ADDED_COLUMNS, {})
    _sqlite_add_columns_and_tables(conn, _DRIVE_INGESTION_ADDED_COLUMNS, {})
    return touched


def _count_drive_ingestion_db(context: MigrationContext) -> int:
    return _sqlite_adoption_count(context, _DRIVE_INGESTION_ADDED_COLUMNS, {})


def _count_json_records(context: MigrationContext) -> int:
    spec = context.spec
    if spec.container_key is None:
        return 0
    document = read_json_document(context.root, spec)
    return len(document.get(spec.container_key) or [])


def _adopt_json_document(context: MigrationContext) -> int:
    """Validate the shape, then let the engine stamp the version key."""
    return _count_json_records(context)


def count_jsonl_records(path: Path) -> int:
    if not path.is_file():
        return 0
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        return sum(1 for line in handle if line.strip())


def _count_log_records(context: MigrationContext) -> int:
    return count_jsonl_records(context.path)


def _count_dir_records(context: MigrationContext) -> int:
    return sum(count_jsonl_records(path) for path in sorted(context.path.glob("*.jsonl")))


def _count_opaque_records(context: MigrationContext) -> int:
    """Number of top-level entries only. No key and no value ever leaves here."""
    try:
        data = json.loads(context.path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    return len(data) if isinstance(data, dict) else 0


def _adopt_recorded_store(context: MigrationContext) -> int:
    """Nothing in the store changes; the engine records the version alongside it."""
    return _RECORDED_COUNTERS[context.spec.store_id](context)


_RECORDED_COUNTERS: dict[str, Callable[[MigrationContext], int]] = {
    "conversation_transcripts": _count_dir_records,
    "presentation_events": _count_dir_records,
    "workspace_receipts": _count_log_records,
    "secrets": _count_opaque_records,
    "mcp_config": _count_opaque_records,
}


def _adopt(description: str, count: Callable, apply: Callable) -> tuple[Migration, ...]:
    return (
        Migration(
            from_version=0,
            to_version=1,
            description=description,
            count=count,
            apply=apply,
        ),
    )


# --- the registry --------------------------------------------------------

REGISTRY: tuple[StoreSpec, ...] = (
    StoreSpec(
        store_id="conversation_db",
        kind=StoreKind.SQLITE,
        relative_path="club.db",
        current_version=CONVERSATION_DB_VERSION,
        version_channel=VersionChannel.SQLITE_USER_VERSION,
        description="Sessions, memories, schedule, approvals, and the chat queue.",
        migrations=_adopt(
            "Adopt the conversation schema shipped before the registry existed.",
            _count_conversation_db,
            _adopt_conversation_db,
        ),
        json_columns=(
            ("inbox", "arguments"),
            ("inbox", "execution_result"),
            ("inbox", "resource"),
            ("runs", "artifacts"),
            ("queue_commands", "acknowledgement"),
        ),
    ),
    StoreSpec(
        store_id="people_db",
        kind=StoreKind.SQLITE,
        relative_path="people.db",
        current_version=PEOPLE_DB_VERSION,
        version_channel=VersionChannel.SQLITE_USER_VERSION,
        description="Person files, their ledger events, attachments, and versions.",
        migrations=_adopt(
            "Adopt the person schema shipped before the registry existed.",
            _count_people_db,
            _adopt_people_db,
        ),
        json_columns=(
            ("events", "payload"),
            ("person_attachments", "fields_json"),
            ("person_versions", "person_json"),
            ("person_versions", "attachments_json"),
        ),
    ),
    StoreSpec(
        store_id="drive_ingestion",
        kind=StoreKind.SQLITE,
        relative_path="drive_ingestion.db",
        current_version=1,
        version_channel=VersionChannel.SQLITE_USER_VERSION,
        description="Drive folder ingestion jobs, indexed sources, and person-file proposals.",
        migrations=_adopt(
            "Adopt the Drive ingestion schema shipped before the registry existed.",
            _count_drive_ingestion_db,
            _adopt_drive_ingestion_db,
        ),
        json_columns=(
            ("drive_ingestion_sources", "citations_json"),
            ("drive_ingestion_sources", "source_safety_json"),
            ("drive_ingestion_proposals", "fields_json"),
            ("drive_ingestion_proposals", "diff_json"),
            ("drive_ingestion_proposals", "source_refs_json"),
        ),
    ),
    StoreSpec(
        store_id="conversation_transcripts",
        kind=StoreKind.JSONL_DIR,
        relative_path="conversations",
        current_version=TRANSCRIPT_VERSION,
        version_channel=VersionChannel.MANIFEST,
        description="Append-only chat transcripts, one file per session.",
        migrations=_adopt(
            "Record the transcript log version alongside the store.",
            _count_dir_records,
            _adopt_recorded_store,
        ),
    ),
    StoreSpec(
        store_id="presentation_events",
        kind=StoreKind.JSONL_DIR,
        relative_path="events",
        current_version=1,
        version_channel=VersionChannel.MANIFEST,
        description="Append-only presentation events, one file per session.",
        migrations=_adopt(
            "Record the presentation event log version alongside the store.",
            _count_dir_records,
            _adopt_recorded_store,
        ),
        record_version=2,
    ),
    StoreSpec(
        store_id="memory_notes",
        kind=StoreKind.DIRECTORY,
        relative_path="memory",
        current_version=0,
        version_channel=VersionChannel.NONE,
        description="Markdown mirror of the memories table. Checked, never migrated.",
    ),
    StoreSpec(
        store_id="workspace_receipts",
        kind=StoreKind.JSONL_LOG,
        relative_path="workspace_receipts.jsonl",
        current_version=RECEIPTS_VERSION,
        version_channel=VersionChannel.MANIFEST,
        description="Immutable receipts for workspace authority and execution.",
        migrations=_adopt(
            "Record the receipt log version alongside the store.",
            _count_log_records,
            _adopt_recorded_store,
        ),
    ),
    StoreSpec(
        store_id="workspace_grants",
        kind=StoreKind.JSON_DOCUMENT,
        relative_path="workspace_grants.json",
        current_version=WorkspaceGrantStore.VERSION,
        version_channel=VersionChannel.JSON_VERSION_KEY,
        description="Operator grants over local directories.",
        container_key="grants",
        migrations=_adopt(
            "Stamp the grant document with its version.",
            _count_json_records,
            _adopt_json_document,
        ),
    ),
    StoreSpec(
        store_id="shell_tasks",
        kind=StoreKind.JSON_DOCUMENT,
        relative_path="shell_tasks.json",
        current_version=ShellTaskStore.VERSION,
        version_channel=VersionChannel.JSON_VERSION_KEY,
        description="Workspace shell tasks and their last known status.",
        container_key="tasks",
        migrations=_adopt(
            "Stamp the shell task document with its version.",
            _count_json_records,
            _adopt_json_document,
        ),
    ),
    StoreSpec(
        store_id="host_command_approvals",
        kind=StoreKind.JSON_DOCUMENT,
        relative_path="host_command_approvals.json",
        current_version=HostApprovalStore.VERSION,
        version_channel=VersionChannel.JSON_VERSION_KEY,
        description="Persistent allow-always decisions for direct host commands.",
        container_key="approvals",
        migrations=_adopt(
            "Stamp the host approval document with its version.",
            _count_json_records,
            _adopt_json_document,
        ),
    ),
    StoreSpec(
        store_id="directory_requests",
        kind=StoreKind.JSON_DOCUMENT,
        relative_path="directory_requests.json",
        current_version=DirectoryRequestStore.VERSION,
        version_channel=VersionChannel.JSON_VERSION_KEY,
        description="Pending and resolved requests for workspace authority.",
        container_key="requests",
        migrations=_adopt(
            "Stamp the directory request document with its version.",
            _count_json_records,
            _adopt_json_document,
        ),
    ),
    StoreSpec(
        store_id="workspace_trash",
        kind=StoreKind.DIRECTORY,
        relative_path="workspace_trash",
        current_version=0,
        version_channel=VersionChannel.NONE,
        description="Reversible workspace deletes. Checked, never migrated.",
    ),
    StoreSpec(
        store_id="secrets",
        kind=StoreKind.OPAQUE_FILE,
        relative_path="secrets.json",
        current_version=SECRETS_VERSION,
        version_channel=VersionChannel.MANIFEST,
        description="OAuth grants and API keys. Never read into a report or a backup.",
        migrations=_adopt(
            "Record the secret store version alongside the store.",
            _count_opaque_records,
            _adopt_recorded_store,
        ),
        secret_bearing=True,
    ),
    StoreSpec(
        store_id="mcp_config",
        kind=StoreKind.JSON_DOCUMENT,
        relative_path="mcp.json",
        current_version=MCP_CONFIG_VERSION,
        version_channel=VersionChannel.MANIFEST,
        description="Optional MCP server registry.",
        migrations=_adopt(
            "Record the MCP config version alongside the store.",
            _count_opaque_records,
            _adopt_recorded_store,
        ),
        secret_bearing=True,
    ),
    StoreSpec(
        store_id="dotenv",
        kind=StoreKind.OPAQUE_FILE,
        relative_path=".env",
        current_version=0,
        version_channel=VersionChannel.NONE,
        description="Local environment overrides. Checked for mode only, never read.",
        secret_bearing=True,
    ),
)

_BY_ID = {spec.store_id: spec for spec in REGISTRY}


def spec_for(store_id: str) -> StoreSpec:
    return _BY_ID[store_id] if store_id in _BY_ID else _lookup(store_id)


def _lookup(store_id: str) -> StoreSpec:
    for spec in REGISTRY:
        if spec.store_id == store_id:
            return spec
    raise KeyError(f"unknown store {store_id}")


# --- planning ------------------------------------------------------------


def _context_for(root: Path, spec: StoreSpec, conn: sqlite3.Connection | None) -> MigrationContext:
    return MigrationContext(root=root, spec=spec, path=store_path(root, spec), connection=conn)


def _steps_from(spec: StoreSpec, version: int) -> tuple[Migration, ...] | None:
    steps: list[Migration] = []
    at = version
    while at < spec.current_version:
        step = next((item for item in spec.migrations if item.from_version == at), None)
        if step is None:
            return None
        steps.append(step)
        at = step.to_version
    return tuple(steps)


def _plan_store(root: Path, spec: StoreSpec) -> StorePlan:
    if not store_present(root, spec):
        return StorePlan(
            store_id=spec.store_id,
            kind=spec.kind,
            status=StoreStatus.ABSENT,
            from_version=None,
            to_version=spec.current_version,
            steps=(),
            record_count=0,
        )
    if spec.version_channel is VersionChannel.NONE:
        return StorePlan(
            store_id=spec.store_id,
            kind=spec.kind,
            status=StoreStatus.CURRENT,
            from_version=None,
            to_version=spec.current_version,
            steps=(),
            record_count=0,
        )
    try:
        version = read_version(root, spec)
    except StoreUnreadable as exc:
        return StorePlan(
            store_id=spec.store_id,
            kind=spec.kind,
            status=StoreStatus.UNREADABLE,
            from_version=None,
            to_version=spec.current_version,
            steps=(),
            record_count=0,
            detail=str(exc),
        )
    assert version is not None
    if version > spec.current_version:
        return StorePlan(
            store_id=spec.store_id,
            kind=spec.kind,
            status=StoreStatus.UNSUPPORTED_FUTURE,
            from_version=version,
            to_version=spec.current_version,
            steps=(),
            record_count=0,
            detail=f"on disk at version {version}, this build knows {spec.current_version}",
        )
    if version == spec.current_version:
        return StorePlan(
            store_id=spec.store_id,
            kind=spec.kind,
            status=StoreStatus.CURRENT,
            from_version=version,
            to_version=version,
            steps=(),
            record_count=0,
        )
    steps = _steps_from(spec, version)
    if steps is None:
        return StorePlan(
            store_id=spec.store_id,
            kind=spec.kind,
            status=StoreStatus.MIGRATION_PATH_MISSING,
            from_version=version,
            to_version=spec.current_version,
            steps=(),
            record_count=0,
            detail=f"no registered step leaves version {version}",
        )
    conn = None
    try:
        if spec.kind is StoreKind.SQLITE:
            conn = open_readonly(store_path(root, spec))
        context = _context_for(root, spec, conn)
        record_count = sum(step.count(context) for step in steps)
    except (sqlite3.Error, StoreUnreadable) as exc:
        return StorePlan(
            store_id=spec.store_id,
            kind=spec.kind,
            status=StoreStatus.UNREADABLE,
            from_version=version,
            to_version=spec.current_version,
            steps=(),
            record_count=0,
            detail=str(exc),
        )
    finally:
        if conn is not None:
            conn.close()
    return StorePlan(
        store_id=spec.store_id,
        kind=spec.kind,
        status=StoreStatus.PENDING,
        from_version=version,
        to_version=spec.current_version,
        steps=steps,
        record_count=record_count,
    )


def plan_migrations(root: str | Path) -> MigrationPlan:
    """Read every registered store and report what it would take to bring it current."""
    root = Path(root).expanduser()
    return MigrationPlan(tuple(_plan_store(root, spec) for spec in REGISTRY))


# --- backup and restore --------------------------------------------------


def _new_backup_id(root: Path) -> str:
    base = root / BACKUPS_DIR_NAME
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    candidate = f"doctor-{stamp}Z"
    suffix = 1
    while (base / candidate).exists():
        candidate = f"doctor-{stamp}-{suffix}Z"
        suffix += 1
    return candidate


def _copy_sqlite(source: Path, target: Path) -> None:
    """Use the online backup API so an open database copies consistently."""
    origin = open_readonly(source)
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, FILE_MODE)
        os.close(descriptor)
        destination = sqlite3.connect(target)
        try:
            origin.backup(destination)
        finally:
            destination.close()
    finally:
        origin.close()
    os.chmod(target, FILE_MODE)


def _copy_store(root: Path, spec: StoreSpec, target_root: Path) -> None:
    source = store_path(root, spec)
    target = target_root / spec.relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    if spec.is_directory:
        shutil.copytree(source, target, dirs_exist_ok=True)
        os.chmod(target, spec.dir_mode)
        for path in target.rglob("*"):
            os.chmod(path, spec.dir_mode if path.is_dir() else spec.file_mode)
        return
    if spec.kind is StoreKind.SQLITE:
        _copy_sqlite(source, target)
        return
    shutil.copyfile(source, target)
    os.chmod(target, spec.file_mode)


def _entry_for(root: Path, spec: StoreSpec, target_root: Path, copied: bool) -> dict[str, Any]:
    source = store_path(root, spec)
    entry: dict[str, Any] = {
        "store_id": spec.store_id,
        "kind": spec.kind.value,
        "relative_path": spec.relative_path,
        "content_backed_up": copied,
        "mode": oct(source.stat().st_mode & 0o777),
    }
    try:
        entry["store_version"] = read_version(root, spec)
    except StoreUnreadable:
        entry["store_version"] = None
    if spec.is_directory:
        files = [path for path in source.rglob("*") if path.is_file()]
        entry["files"] = len(files)
        entry["bytes"] = sum(path.stat().st_size for path in files)
        return entry
    entry["bytes"] = source.stat().st_size
    if copied:
        entry["sha256"] = sha256_file(target_root / spec.relative_path)
    else:
        entry["sha256"] = sha256_file(source)
    return entry


def create_backup(
    root: str | Path, store_ids: Iterable[str], *, reason: str
) -> Backup:
    """Copy the named stores into a timestamped directory before anything changes.

    Secret-bearing stores are recorded but never copied: Doctor never changes
    their contents, so a hash and a mode are enough to verify and restore them,
    and a second copy of a credential on disk is a cost with no benefit.
    """
    root = Path(root).expanduser()
    specs = [spec_for(store_id) for store_id in store_ids]
    present = [spec for spec in specs if store_present(root, spec)]
    backup_id = _new_backup_id(root)
    target_root = root / BACKUPS_DIR_NAME / backup_id
    try:
        target_root.mkdir(parents=True, exist_ok=False)
        os.chmod(root / BACKUPS_DIR_NAME, DIR_MODE)
        os.chmod(target_root, DIR_MODE)
        entries = []
        for spec in present:
            copied = not spec.secret_bearing
            if copied:
                _copy_store(root, spec, target_root)
            entries.append(_entry_for(root, spec, target_root, copied))
        manifest = {
            "version": MANIFEST_VERSION,
            "backup_id": backup_id,
            "created_at": datetime.now(UTC).isoformat(),
            "reason": str(reason),
            "entries": entries,
        }
        _write_private(
            target_root / BACKUP_MANIFEST_NAME,
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        )
    except OSError as exc:
        shutil.rmtree(target_root, ignore_errors=True)
        raise BackupFailed(f"backup could not be written: {exc.strerror or exc}") from exc
    return Backup(backup_id=backup_id, path=target_root, manifest=manifest)


def list_backups(root: str | Path) -> list[dict[str, Any]]:
    """Every backup on disk, newest first, described by its own manifest."""
    base = Path(root).expanduser() / BACKUPS_DIR_NAME
    if not base.is_dir():
        return []
    found = []
    for directory in sorted(base.iterdir(), reverse=True):
        manifest_path = directory / BACKUP_MANIFEST_NAME
        if not manifest_path.is_file():
            continue
        try:
            found.append(json.loads(manifest_path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
    return found


def _restore_entry(root: Path, target_root: Path, entry: dict[str, Any]) -> bool:
    spec = spec_for(str(entry["store_id"]))
    if not entry.get("content_backed_up"):
        return False
    source = target_root / spec.relative_path
    destination = store_path(root, spec)
    if spec.is_directory:
        shutil.rmtree(destination, ignore_errors=True)
        shutil.copytree(source, destination)
        os.chmod(destination, spec.dir_mode)
        for path in destination.rglob("*"):
            os.chmod(path, spec.dir_mode if path.is_dir() else spec.file_mode)
    else:
        shutil.copyfile(source, destination)
        os.chmod(destination, spec.file_mode)
    return True


def restore_backup(root: str | Path, backup_id: str) -> dict[str, Any]:
    """Put a backup's stores back, taking a safety backup of what is there now."""
    root = Path(root).expanduser()
    target_root = root / BACKUPS_DIR_NAME / backup_id
    manifest_path = target_root / BACKUP_MANIFEST_NAME
    if not manifest_path.is_file():
        raise BackupFailed(f"backup {backup_id} was not found")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = [entry for entry in manifest["entries"] if entry.get("content_backed_up")]
    safety = create_backup(
        root, [entry["store_id"] for entry in entries], reason=f"before restoring {backup_id}"
    )
    restored = [
        str(entry["store_id"]) for entry in entries if _restore_entry(root, target_root, entry)
    ]
    return {
        "backup_id": backup_id,
        "safety_backup_id": safety.backup_id,
        "restored": restored,
    }


# --- applying ------------------------------------------------------------


def _apply_store(root: Path, plan: StorePlan, spec: StoreSpec) -> list[AppliedStep]:
    applied: list[AppliedStep] = []
    conn = None
    try:
        if spec.kind is StoreKind.SQLITE:
            conn = sqlite3.connect(store_path(root, spec))
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN IMMEDIATE")
        context = _context_for(root, spec, conn)
        for step in plan.steps:
            count = step.apply(context)
            if conn is not None:
                # user_version cannot be parameterised; the value is an int from
                # the registry, never operator input.
                conn.execute(f"PRAGMA user_version = {int(step.to_version)}")
            else:
                _write_version(root, spec, step.to_version)
            applied.append(
                AppliedStep(
                    store_id=spec.store_id,
                    from_version=step.from_version,
                    to_version=step.to_version,
                    description=step.description,
                    record_count=count,
                )
            )
        if conn is not None:
            conn.commit()
    except Exception:
        if conn is not None:
            conn.rollback()
        raise
    finally:
        if conn is not None:
            conn.close()
    return applied


def apply_migrations(
    root: str | Path,
    *,
    plan: MigrationPlan | None = None,
    backup: Backup | None = None,
) -> MigrationOutcome:
    """Bring every pending store to its current version, backing up first.

    Refuses the whole run when any store is at an unknown future version, is
    unreadable, or has no registered path forward. A step that raises rolls its
    own store back from the backup and stops; stores already migrated in this
    run keep their new version, which a rerun then sees as current.

    Pass `backup` when the caller has already taken one covering these stores,
    so a single repair does not write the same files to disk twice.
    """
    root = Path(root).expanduser()
    plan = plan if plan is not None else plan_migrations(root)
    if plan.blocked:
        reasons = ", ".join(f"{item.store_id}: {item.status.value}" for item in plan.blockers)
        return MigrationOutcome(blocked=True, error=f"refusing to migrate ({reasons})")
    pending = plan.pending
    if not pending:
        return MigrationOutcome()

    try:
        if backup is None:
            backup = create_backup(
                root, [item.store_id for item in pending], reason="migration"
            )
    except BackupFailed as exc:
        return MigrationOutcome(error=str(exc))

    applied: list[AppliedStep] = []
    for store_plan in pending:
        spec = spec_for(store_plan.store_id)
        try:
            applied.extend(_apply_store(root, store_plan, spec))
        except Exception as exc:  # a step failed: put this store back as it was
            entry = next(
                (
                    item
                    for item in backup.manifest["entries"]
                    if item["store_id"] == spec.store_id
                ),
                None,
            )
            if entry is not None:
                _restore_entry(root, backup.path, entry)
            return MigrationOutcome(
                applied=tuple(applied),
                backup_id=backup.backup_id,
                rolled_back=(spec.store_id,),
                error=f"{spec.store_id}: {exc}",
            )
    return MigrationOutcome(applied=tuple(applied), backup_id=backup.backup_id)
