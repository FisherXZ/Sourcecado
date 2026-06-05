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
      reason text not null,
      created_at text not null default (datetime('now'))
    );
  `);
}
