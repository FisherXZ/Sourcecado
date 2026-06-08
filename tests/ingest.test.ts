import { existsSync, mkdirSync, mkdtempSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { createDatabase, type MemoryDatabase } from "../src/db.js";
import { formatIngestReport, ingestFolder } from "../src/ingest.js";

const tempDirs: string[] = [];

function tempDir(prefix = "sourcyavo-ingest-test-"): string {
  const dir = mkdtempSync(join(tmpdir(), prefix));
  tempDirs.push(dir);
  return dir;
}

function tempDb(dir: string): MemoryDatabase {
  return createDatabase(join(dir, ".sourcyavo", "memory.db"));
}

function getRows<T>(db: MemoryDatabase, table: string): T[] {
  return db.prepare(`select * from ${table} order by id`).all() as T[];
}

afterEach(() => {
  for (const dir of tempDirs.splice(0)) {
    rmSync(dir, { recursive: true, force: true });
  }
});

describe("ingestFolder", () => {
  it("ingests supported files with metadata, hashes, chunks, embeddings, and citations", () => {
    const dir = tempDir();
    writeFileSync(
      join(dir, "spring.md"),
      [
        "---",
        "title: Spring 2026 Sourcing",
        "source_type: markdown",
        "---",
        "Jane Doe met the AI safety group.",
        "She needs follow-up after finals."
      ].join("\n")
    );
    writeFileSync(join(dir, "notes.txt"), "Plain text sourcing note for Alex.");
    writeFileSync(join(dir, "tracker.csv"), "name,status\nJane,contacted\n");
    writeFileSync(join(dir, "thread.eml"), "Subject: Intro\n\nRaw email body about Maya.");

    const db = tempDb(dir);
    const result = ingestFolder(db, dir);

    const sources = getRows<{
      path: string;
      title: string;
      source_type: string;
      content_hash: string;
      raw_text: string;
    }>(db, "source_records");
    const chunks = getRows<{
      source_record_id: number;
      chunk_index: number;
      text: string;
      chunk_hash: string;
      embedding: string;
      citation: string;
    }>(db, "memory_chunks");
    const errors = getRows<{ path: string; reason: string }>(db, "ingest_errors");

    expect(result).toMatchObject({ processed: 4, skipped: 0, skippedFiles: [] });
    expect(sources).toHaveLength(4);
    expect(sources.map((source) => source.source_type).sort()).toEqual([
      "csv",
      "email",
      "markdown",
      "text"
    ]);
    expect(sources.find((source) => basename(source.path) === "spring.md")).toMatchObject({
      title: "Spring 2026 Sourcing",
      source_type: "markdown",
      raw_text: "Jane Doe met the AI safety group.\nShe needs follow-up after finals."
    });
    expect(sources.every((source) => /^[a-f0-9]{64}$/.test(source.content_hash))).toBe(true);
    expect(chunks.length).toBeGreaterThanOrEqual(4);
    expect(chunks.every((chunk) => /^[a-f0-9]{64}$/.test(chunk.chunk_hash))).toBe(true);
    expect(chunks.every((chunk) => Array.isArray(JSON.parse(chunk.embedding)))).toBe(true);
    expect(chunks.every((chunk) => /#(chunk|row)-\d+$/.test(chunk.citation))).toBe(true);
    expect(chunks.every((chunk) => !chunk.citation.startsWith(dir))).toBe(true);
    expect(errors).toEqual([]);

    db.close();
  });

  it("recursively ingests nested CSV files and keeps each CSV row intact", () => {
    const dir = tempDir();
    const nested = join(dir, "Spring 2026", "Cold Emailing");
    rmSync(nested, { recursive: true, force: true });
    writeFileSync(join(dir, "root.txt"), "Root note should be indexed.");
    mkdirRecursive(nested);
    writeFileSync(
      join(nested, "apollo.csv"),
      [
        "First Name,Last Name,Title,Company Name,Email",
        ...Array.from(
          { length: 40 },
          (_, index) => `First${index + 1},Last${index + 1},Engineer,Company${index + 1},p${index + 1}@example.com`
        )
      ].join("\n")
    );

    const db = tempDb(dir);
    const result = ingestFolder(db, dir);

    const sources = getRows<{ id: number; path: string; source_type: string }>(db, "source_records");
    const csvSource = sources.find((source) => basename(source.path) === "apollo.csv");
    const csvChunks = db
      .prepare("select text, citation from memory_chunks where source_record_id = ? order by chunk_index")
      .all(csvSource?.id) as Array<{ text: string; citation: string }>;

    expect(result).toMatchObject({ processed: 2, skipped: 0 });
    expect(sources).toHaveLength(2);
    expect(csvChunks).toHaveLength(40);
    expect(csvChunks[0].text).toContain("First Name,Last Name,Title,Company Name,Email");
    expect(csvChunks[0].text).toContain("First1,Last1,Engineer,Company1,p1@example.com");
    expect(csvChunks[39].text).toContain("First40,Last40,Engineer,Company40,p40@example.com");
    expect(csvChunks[0].citation).toBe("Spring 2026/Cold Emailing/apollo.csv#row-1");
    expect(csvChunks[39].citation).toBe("Spring 2026/Cold Emailing/apollo.csv#row-40");

    db.close();
  });

  it("logs unsupported files and continues ingesting supported neighbors", () => {
    const dir = tempDir();
    writeFileSync(join(dir, "ok.txt"), "Supported note.");
    writeFileSync(join(dir, "image.png"), "not really an image");

    const db = tempDb(dir);
    const result = ingestFolder(db, dir);

    const sources = getRows<{ path: string }>(db, "source_records");
    const chunks = getRows<{ text: string }>(db, "memory_chunks");
    const errors = getRows<{ path: string; category: string; reason: string }>(db, "ingest_errors");

    expect(result).toMatchObject({ processed: 1, skipped: 1 });
    expect(sources).toHaveLength(1);
    expect(chunks).toHaveLength(1);
    expect(basename(errors[0].path)).toBe("image.png");
    expect(errors[0].category).toBe("unsupported-type");
    expect(errors[0].reason).toContain("Unsupported file extension");

    db.close();
  });

  it("logs empty files without creating source or chunk rows", () => {
    const dir = tempDir();
    writeFileSync(join(dir, "empty.md"), "  \n\t");

    const db = tempDb(dir);
    const result = ingestFolder(db, dir);

    expect(result).toMatchObject({ processed: 0, skipped: 1 });
    expect(getRows(db, "source_records")).toEqual([]);
    expect(getRows(db, "memory_chunks")).toEqual([]);
    expect(getRows<{ path: string; category: string; reason: string }>(db, "ingest_errors")).toMatchObject([
      { category: "empty", reason: "File is empty after parsing" }
    ]);

    db.close();
  });

  it("logs unreadable or malformed file entries without aborting the folder", () => {
    const dir = tempDir();
    writeFileSync(join(dir, "after.txt"), "This should still be indexed.");
    const brokenTarget = join(dir, "missing.txt");
    const brokenLink = join(dir, "broken.txt");
    if (!existsSync(brokenTarget)) {
      symlinkSync(brokenTarget, brokenLink);
    }

    const db = tempDb(dir);
    const result = ingestFolder(db, dir);

    const sources = getRows<{ path: string; title: string }>(db, "source_records");
    const errors = getRows<{ path: string; category: string; reason: string }>(db, "ingest_errors");

    expect(result).toMatchObject({ processed: 1, skipped: 1 });
    expect(sources).toHaveLength(1);
    expect(sources[0].title).toBe("after");
    expect(basename(errors[0].path)).toBe("broken.txt");
    expect(errors[0].category).toBe("unreadable");
    expect(errors[0].reason).toContain("Failed to read file");

    db.close();
  });

  it("classifies a mixed batch and leaves no orphan rows for skipped files", () => {
    const dir = writeMixedBatch();

    const db = tempDb(dir);
    const result = ingestFolder(db, dir);

    const sources = getRows<{ path: string }>(db, "source_records");
    const errors = getRows<{ path: string; category: string; reason: string }>(db, "ingest_errors");

    // good md + good csv processed; the rest skipped.
    expect(result).toMatchObject({ processed: 2, skipped: 5 });
    expect(sources).toHaveLength(2);
    expect(sources.map((source) => basename(source.path)).sort()).toEqual(["good.csv", "good.md"]);

    // No orphan source/chunk rows for any skipped file.
    const skippedNames = result.skippedFiles.map((skipped) => basename(skipped.path));
    for (const name of skippedNames) {
      expect(sources.some((source) => basename(source.path) === name)).toBe(false);
    }

    const categoryByName = new Map(
      errors.map((error) => [basename(error.path), error.category] as const)
    );
    expect(categoryByName.get("unsupported.png")).toBe("unsupported-type");
    expect(categoryByName.get("empty.md")).toBe("empty");
    expect(categoryByName.get("broken.txt")).toBe("unreadable");
    expect(categoryByName.get("header-only.csv")).toBe("parse-error");
    expect(categoryByName.get("bad-frontmatter.md")).toBe("parse-error");
    expect(errors).toHaveLength(5);

    db.close();
  });

  it("renders a report that names skipped files with a tally and no absolute paths", () => {
    const dir = writeMixedBatch();

    const db = tempDb(dir);
    const result = ingestFolder(db, dir);
    const report = formatIngestReport(result, dir);

    expect(report).toContain("Ingested 2 source files; skipped 5.");
    expect(report).toContain("Skipped files:");
    expect(report).toContain("unsupported.png [unsupported-type]");
    expect(report).toContain("empty.md [empty]");
    expect(report).toContain("broken.txt [unreadable]");
    expect(report).toContain("header-only.csv [parse-error]");
    expect(report).toContain("bad-frontmatter.md [parse-error]");
    expect(report).toContain("Skipped by category:");
    expect(report).toContain("parse-error: 2");
    expect(report).toContain("empty: 1");
    expect(report).toContain("unreadable: 1");
    expect(report).toContain("unsupported-type: 1");
    expect(report).not.toContain(dir);

    db.close();
  });

  it("prints only the summary line when nothing was skipped", () => {
    const dir = tempDir();
    writeFileSync(join(dir, "ok.txt"), "Supported note.");

    const db = tempDb(dir);
    const result = ingestFolder(db, dir);
    const report = formatIngestReport(result, dir);

    expect(report).toBe("Ingested 1 source file; skipped 0.");

    db.close();
  });

  it("keeps ingesting neighbors after a skipped file regardless of ordering", () => {
    const dir = tempDir();
    // Names chosen so a failing file sorts between two good files.
    writeFileSync(join(dir, "a-before.txt"), "Indexed before the failure.");
    writeFileSync(join(dir, "m-empty.md"), "   \n\t");
    writeFileSync(join(dir, "z-after.txt"), "Indexed after the failure.");

    const db = tempDb(dir);
    const result = ingestFolder(db, dir);

    const sources = getRows<{ path: string }>(db, "source_records");

    expect(result).toMatchObject({ processed: 2, skipped: 1 });
    expect(sources.map((source) => basename(source.path)).sort()).toEqual([
      "a-before.txt",
      "z-after.txt"
    ]);
    expect(result.skippedFiles.map((skipped) => basename(skipped.path))).toEqual(["m-empty.md"]);

    db.close();
  });

  // The current deterministic hashing embedder cannot throw, so there is no
  // honest way to exercise the embedding-error path without faking it.
  it.todo("classifies embedding failures as embedding-error");
});

function writeMixedBatch(): string {
  const dir = tempDir();
  writeFileSync(
    join(dir, "good.md"),
    ["---", "title: Good Note", "---", "Jane Doe needs follow-up."].join("\n")
  );
  writeFileSync(join(dir, "good.csv"), "name,status\nJane,contacted\n");
  writeFileSync(join(dir, "unsupported.png"), "not really an image");
  writeFileSync(join(dir, "empty.md"), "  \n\t");
  writeFileSync(join(dir, "header-only.csv"), "name,status\n");
  writeFileSync(
    join(dir, "bad-frontmatter.md"),
    ["---", "title: Missing closing fence", "Body without a closing fence."].join("\n")
  );

  const brokenTarget = join(dir, "missing-target.txt");
  const brokenLink = join(dir, "broken.txt");
  if (!existsSync(brokenTarget)) {
    symlinkSync(brokenTarget, brokenLink);
  }

  return dir;
}

function mkdirRecursive(path: string): void {
  mkdirSync(path, { recursive: true });
}
