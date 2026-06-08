import Database from "better-sqlite3";
import { mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";

export const DEFAULT_DATABASE_PATH = ".sourcyavo/memory.db";

export type MemoryDatabase = Database.Database;

export function openMemoryDatabase(databasePath = DEFAULT_DATABASE_PATH): MemoryDatabase {
  const resolvedPath = resolve(databasePath);
  mkdirSync(dirname(resolvedPath), { recursive: true });

  const db = new Database(resolvedPath);
  db.pragma("foreign_keys = ON");
  db.pragma("journal_mode = WAL");
  migrate(db);

  return db;
}

export const createDatabase = openMemoryDatabase;

function migrate(db: MemoryDatabase): void {
  db.exec(`
    create table if not exists source_records (
      id integer primary key autoincrement,
      path text not null unique,
      source_id text,
      title text not null,
      source_type text not null,
      content_hash text not null,
      raw_text text not null,
      created_at text not null default (datetime('now')),
      updated_at text not null default (datetime('now'))
    );

    create table if not exists memory_chunks (
      id integer primary key autoincrement,
      source_record_id integer not null,
      chunk_index integer not null,
      text text not null,
      chunk_hash text not null,
      embedding text,
      citation text not null,
      created_at text not null default (datetime('now')),
      unique (source_record_id, chunk_index),
      foreign key (source_record_id) references source_records(id) on delete cascade
    );

    create table if not exists extraction_runs (
      id integer primary key autoincrement,
      source_chunk_id integer,
      cache_key text not null unique,
      chunk_hash text not null,
      extractor_type text not null,
      extractor_version text not null,
      prompt_hash text not null,
      schema_version text not null,
      model_name text not null,
      raw_output text,
      parsed_candidates_json text,
      status text not null,
      error text,
      created_at text not null default (datetime('now')),
      foreign key (source_chunk_id) references memory_chunks(id) on delete set null
    );

    create table if not exists entities (
      id integer primary key autoincrement,
      type text not null,
      name text not null,
      canonical_key text not null unique,
      created_at text not null default (datetime('now'))
    );

    create table if not exists entity_aliases (
      id integer primary key autoincrement,
      entity_id integer not null,
      alias text not null,
      alias_key text not null unique,
      foreign key (entity_id) references entities(id) on delete cascade
    );

    create table if not exists relationships (
      id integer primary key autoincrement,
      from_entity_id integer not null,
      to_entity_id integer not null,
      type text not null,
      source_record_id integer,
      source_chunk_id integer,
      confidence real not null,
      note text,
      created_at text not null default (datetime('now')),
      foreign key (from_entity_id) references entities(id) on delete cascade,
      foreign key (to_entity_id) references entities(id) on delete cascade,
      foreign key (source_record_id) references source_records(id) on delete set null,
      foreign key (source_chunk_id) references memory_chunks(id) on delete set null
    );

    create table if not exists semantic_facts (
      id integer primary key autoincrement,
      subject text not null,
      predicate text not null,
      object text not null,
      source_record_id integer,
      source_chunk_id integer,
      confidence real not null,
      status text not null,
      created_at text not null default (datetime('now')),
      foreign key (source_record_id) references source_records(id) on delete set null,
      foreign key (source_chunk_id) references memory_chunks(id) on delete set null
    );

    create table if not exists ingest_errors (
      id integer primary key autoincrement,
      path text not null,
      category text,
      reason text not null,
      created_at text not null default (datetime('now'))
    );

    create table if not exists source_permissions (
      id integer primary key autoincrement,
      principal_type text not null,
      principal_id text not null,
      source_id text not null,
      access text not null default 'read',
      created_at text not null default (datetime('now')),
      unique (principal_type, principal_id, source_id)
    );

    create table if not exists audit_events (
      id integer primary key autoincrement,
      actor_type text not null,
      actor_id text not null,
      action text not null,
      source_id text,
      created_at text not null default (datetime('now'))
    );
  `);

  backfillSourceIds(db);
  addIngestErrorCategory(db);

  db.exec(`
    create unique index if not exists idx_source_records_source_id
      on source_records(source_id);
    create index if not exists idx_source_permissions_principal
      on source_permissions(principal_type, principal_id, source_id);
    create index if not exists idx_memory_chunks_source_record
      on memory_chunks(source_record_id);
    create index if not exists idx_semantic_facts_source_status
      on semantic_facts(source_record_id, status);
    create index if not exists idx_audit_events_actor
      on audit_events(actor_type, actor_id, created_at);
  `);
}

interface ColumnInfo {
  name: string;
}

interface SourceRecordRow {
  id: number;
  path: string;
}

// For pre-existing DBs created before source_id existed: add the column and
// backfill a deterministic unique slug derived from each row's stored path,
// all before the unique index is created. Idempotent across re-opens.
function backfillSourceIds(db: MemoryDatabase): void {
  const columns = db.prepare("pragma table_info(source_records)").all() as ColumnInfo[];
  const hasSourceId = columns.some((column) => column.name === "source_id");
  if (hasSourceId) {
    return;
  }

  const run = db.transaction(() => {
    db.exec("alter table source_records add column source_id text");

    const rows = db.prepare("select id, path from source_records").all() as SourceRecordRow[];
    const update = db.prepare("update source_records set source_id = ? where id = ?");
    for (const row of rows) {
      update.run(slugifySourceId(row.path), row.id);
    }
  });

  run();
}

// For pre-existing DBs created before ingest_errors.category existed: add the
// column. Existing rows keep a null category and fall back to 'internal-error'
// at read time. Idempotent across re-opens.
function addIngestErrorCategory(db: MemoryDatabase): void {
  const columns = db.prepare("pragma table_info(ingest_errors)").all() as ColumnInfo[];
  const hasCategory = columns.some((column) => column.name === "category");
  if (hasCategory) {
    return;
  }

  db.exec("alter table ingest_errors add column category text");
}

// Deterministic slug of a stable relative path: lowercase, non-alphanumeric runs
// collapse to '-', path separators are preserved.
export function slugifySourceId(relativeLabel: string): string {
  return relativeLabel
    .toLowerCase()
    .split("/")
    .map((segment) =>
      segment
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-+|-+$/g, "")
    )
    .filter((segment) => segment.length > 0)
    .join("/");
}
