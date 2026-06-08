import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { createDatabase, openMemoryDatabase } from "../src/db.js";

const tempDirs: string[] = [];

function tempDbPath(): string {
  const dir = mkdtempSync(join(tmpdir(), "sourcyavo-db-test-"));
  tempDirs.push(dir);
  return join(dir, ".sourcyavo", "memory.db");
}

afterEach(() => {
  for (const dir of tempDirs.splice(0)) {
    rmSync(dir, { recursive: true, force: true });
  }
});

describe("SQLite memory schema", () => {
  it("creates all MVP tables and enables foreign keys and WAL", () => {
    const db = createDatabase(tempDbPath());

    const tables = db
      .prepare(
        "select name from sqlite_master where type = 'table' and name not like 'sqlite_%' order by name"
      )
      .all()
      .map((row) => (row as { name: string }).name);

    expect(tables).toEqual([
      "audit_events",
      "entities",
      "entity_aliases",
      "extraction_runs",
      "ingest_errors",
      "memory_chunks",
      "relationships",
      "semantic_facts",
      "source_permissions",
      "source_records"
    ]);
    expect(db.pragma("foreign_keys", { simple: true })).toBe(1);
    expect(db.pragma("journal_mode", { simple: true })).toBe("wal");

    db.close();
  });

  it("enforces required unique constraints", () => {
    const db = openMemoryDatabase(tempDbPath());

    db.prepare(
      "insert into source_records (path, title, source_type, content_hash, raw_text) values (?, ?, ?, ?, ?)"
    ).run("seed-data/a.md", "A", "markdown", "hash-a", "hello");
    expect(() =>
      db
        .prepare(
          "insert into source_records (path, title, source_type, content_hash, raw_text) values (?, ?, ?, ?, ?)"
        )
        .run("seed-data/a.md", "A2", "markdown", "hash-b", "hello again")
    ).toThrow();

    db.prepare("insert into entities (type, name, canonical_key) values (?, ?, ?)").run(
      "person",
      "Jane Doe",
      "person:jane-doe"
    );
    expect(() =>
      db
        .prepare("insert into entities (type, name, canonical_key) values (?, ?, ?)")
        .run("person", "Jane D.", "person:jane-doe")
    ).toThrow();

    db.prepare("insert into entity_aliases (entity_id, alias, alias_key) values (?, ?, ?)").run(
      1,
      "Jane",
      "jane"
    );
    expect(() =>
      db
        .prepare("insert into entity_aliases (entity_id, alias, alias_key) values (?, ?, ?)")
        .run(1, "JANE", "jane")
    ).toThrow();

    db.prepare(
      "insert into extraction_runs (source_chunk_id, cache_key, chunk_hash, extractor_type, extractor_version, prompt_hash, schema_version, model_name, status) values (?, ?, ?, ?, ?, ?, ?, ?, ?)"
    ).run(null, "cache-1", "chunk-hash", "mock", "1", "prompt", "1", "none", "succeeded");
    expect(() =>
      db
        .prepare(
          "insert into extraction_runs (source_chunk_id, cache_key, chunk_hash, extractor_type, extractor_version, prompt_hash, schema_version, model_name, status) values (?, ?, ?, ?, ?, ?, ?, ?, ?)"
        )
        .run(null, "cache-1", "chunk-hash", "mock", "1", "prompt", "1", "none", "succeeded")
    ).toThrow();

    db.close();
  });

  it("enforces a unique source_id across source records", () => {
    const db = openMemoryDatabase(tempDbPath());

    db.prepare(
      "insert into source_records (path, source_id, title, source_type, content_hash, raw_text) values (?, ?, ?, ?, ?, ?)"
    ).run("seed-data/a.md", "seed-data/a", "A", "markdown", "hash-a", "hello");
    expect(() =>
      db
        .prepare(
          "insert into source_records (path, source_id, title, source_type, content_hash, raw_text) values (?, ?, ?, ?, ?, ?)"
        )
        .run("seed-data/b.md", "seed-data/a", "B", "markdown", "hash-b", "world")
    ).toThrow();

    db.close();
  });

  it("enforces source_permissions uniqueness per principal and source", () => {
    const db = openMemoryDatabase(tempDbPath());

    db.prepare(
      "insert into source_permissions (principal_type, principal_id, source_id) values (?, ?, ?)"
    ).run("user", "alice", "seed-data/a");
    expect(() =>
      db
        .prepare(
          "insert into source_permissions (principal_type, principal_id, source_id) values (?, ?, ?)"
        )
        .run("user", "alice", "seed-data/a")
    ).toThrow();

    db.close();
  });

  it("re-opens an existing database file without throwing (idempotent migrate)", () => {
    const dbPath = tempDbPath();

    const first = openMemoryDatabase(dbPath);
    first.prepare(
      "insert into source_records (path, source_id, title, source_type, content_hash, raw_text) values (?, ?, ?, ?, ?, ?)"
    ).run("seed-data/a.md", "seed-data/a", "A", "markdown", "hash-a", "hello");
    first.close();

    expect(() => {
      const second = openMemoryDatabase(dbPath);
      const rows = second
        .prepare("select source_id from source_records")
        .all() as Array<{ source_id: string }>;
      expect(rows).toEqual([{ source_id: "seed-data/a" }]);
      second.close();
    }).not.toThrow();
  });

  it("enforces chunk and relationship foreign keys", () => {
    const db = openMemoryDatabase(tempDbPath());

    expect(() =>
      db
        .prepare(
          "insert into memory_chunks (source_record_id, chunk_index, text, chunk_hash, citation) values (?, ?, ?, ?, ?)"
        )
        .run(404, 0, "missing source", "missing-hash", "missing.md")
    ).toThrow();

    db.prepare(
      "insert into source_records (path, title, source_type, content_hash, raw_text) values (?, ?, ?, ?, ?)"
    ).run("seed-data/a.md", "A", "markdown", "hash-a", "hello");
    db.prepare(
      "insert into memory_chunks (source_record_id, chunk_index, text, chunk_hash, citation) values (?, ?, ?, ?, ?)"
    ).run(1, 0, "hello", "chunk-a", "seed-data/a.md");
    db.prepare("insert into entities (type, name, canonical_key) values (?, ?, ?)").run(
      "person",
      "Jane Doe",
      "person:jane-doe"
    );

    expect(() =>
      db
        .prepare(
          "insert into relationships (from_entity_id, to_entity_id, type, source_record_id, source_chunk_id, confidence) values (?, ?, ?, ?, ?, ?)"
        )
        .run(1, 404, "contacted", 1, 1, 0.9)
    ).toThrow();

    db.close();
  });
});
