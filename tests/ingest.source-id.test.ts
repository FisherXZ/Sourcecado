import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { createDatabase, type MemoryDatabase } from "../src/db.js";
import { ingestFolder } from "../src/ingest.js";

const tempDirs: string[] = [];

function tempDir(prefix = "sourcyavo-source-id-test-"): string {
  const dir = mkdtempSync(join(tmpdir(), prefix));
  tempDirs.push(dir);
  return dir;
}

function tempDb(dir: string): MemoryDatabase {
  return createDatabase(join(dir, ".sourcyavo", "memory.db"));
}

function sourceRows(
  db: MemoryDatabase
): Array<{ id: number; path: string; source_id: string }> {
  return db
    .prepare("select id, path, source_id from source_records order by id")
    .all() as Array<{ id: number; path: string; source_id: string }>;
}

afterEach(() => {
  for (const dir of tempDirs.splice(0)) {
    rmSync(dir, { recursive: true, force: true });
  }
});

describe("ingest source_id", () => {
  it("derives a deterministic source_id from the stable relative path", () => {
    const dir = tempDir();
    const nested = join(dir, "Spring 2026", "Cold Emailing");
    mkdirSync(nested, { recursive: true });
    writeFileSync(join(nested, "Apollo.csv"), "name,status\nJane,contacted\n");

    const db = tempDb(dir);
    ingestFolder(db, dir);

    const rows = sourceRows(db);
    expect(rows).toHaveLength(1);
    // The relative path retains the .csv extension, so it slugs into the id.
    expect(rows[0].source_id).toBe("spring-2026/cold-emailing/apollo-csv");

    db.close();
  });

  it("lets a frontmatter source_id override win over the path slug", () => {
    const dir = tempDir();
    writeFileSync(
      join(dir, "spring.md"),
      [
        "---",
        "source_id: custom/identity-key",
        "---",
        "Jane Doe met the AI safety group."
      ].join("\n")
    );

    const db = tempDb(dir);
    ingestFolder(db, dir);

    const rows = sourceRows(db);
    expect(rows).toHaveLength(1);
    expect(rows[0].source_id).toBe("custom/identity-key");

    db.close();
  });

  it("preserves source_id and row id on reimport even when frontmatter source_id changes", () => {
    const dir = tempDir();
    const file = join(dir, "spring.md");
    writeFileSync(
      file,
      ["---", "source_id: original-id", "---", "First version of the note."].join("\n")
    );

    const db = tempDb(dir);
    ingestFolder(db, dir);
    const before = sourceRows(db);
    expect(before).toHaveLength(1);
    expect(before[0].source_id).toBe("original-id");

    writeFileSync(
      file,
      ["---", "source_id: changed-id", "---", "Second version of the note."].join("\n")
    );
    ingestFolder(db, dir);

    const after = sourceRows(db);
    expect(after).toHaveLength(1);
    expect(after[0].id).toBe(before[0].id);
    expect(after[0].source_id).toBe("original-id");

    db.close();
  });

  it("preserves a seeded source_permissions mapping across reimport", () => {
    const dir = tempDir();
    const file = join(dir, "notes.txt");
    writeFileSync(file, "Sourcing note for Alex.");

    const db = tempDb(dir);
    ingestFolder(db, dir);

    const sourceId = sourceRows(db)[0].source_id;
    db.prepare(
      "insert into source_permissions (principal_type, principal_id, source_id) values (?, ?, ?)"
    ).run("user", "alice", sourceId);

    writeFileSync(file, "Updated sourcing note for Alex.");
    ingestFolder(db, dir);

    const permissions = db
      .prepare("select principal_id, source_id from source_permissions")
      .all() as Array<{ principal_id: string; source_id: string }>;
    expect(permissions).toEqual([{ principal_id: "alice", source_id: sourceId }]);

    db.close();
  });

  it("logs a duplicate source_id across different paths as an ingest error", () => {
    const dir = tempDir();
    writeFileSync(
      join(dir, "a.md"),
      ["---", "source_id: shared-id", "---", "First doc."].join("\n")
    );
    writeFileSync(
      join(dir, "b.md"),
      ["---", "source_id: shared-id", "---", "Second doc."].join("\n")
    );

    const db = tempDb(dir);
    const result = ingestFolder(db, dir);

    expect(result).toMatchObject({ processed: 1, skipped: 1 });
    expect(sourceRows(db)).toHaveLength(1);
    const errors = db
      .prepare("select path, reason from ingest_errors")
      .all() as Array<{ path: string; reason: string }>;
    expect(errors).toHaveLength(1);
    expect(errors[0].reason).toMatch(/unique/i);

    db.close();
  });
});
