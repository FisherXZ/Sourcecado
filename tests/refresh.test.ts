import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createDatabase, type MemoryDatabase } from "../src/db.js";
import { createMockExtractor } from "../src/extractors/mock.js";
import { ingestFolder } from "../src/ingest.js";
import { refreshMemory } from "../src/refresh.js";
import type { Extractor } from "../src/extractors/types.js";
import type { ExtractedCandidate } from "../src/types.js";

const tempDirs: string[] = [];

function tempDir(prefix = "sourcyavo-refresh-test-"): string {
  const dir = mkdtempSync(join(tmpdir(), prefix));
  tempDirs.push(dir);
  return dir;
}

function tempDb(dir: string): MemoryDatabase {
  return createDatabase(join(dir, ".sourcyavo", "memory.db"));
}

function getRows<T>(db: MemoryDatabase, sql: string): T[] {
  return db.prepare(sql).all() as T[];
}

function seedAndRefreshCsv(db: MemoryDatabase, dir: string, name: string): ReturnType<typeof refreshMemory> {
  writeFileSync(join(dir, name), readFixture(name));
  ingestFolder(db, dir);
  return refreshMemory(db);
}

function readFixture(name: string): string {
  return readFileSync(join(process.cwd(), "tests", "fixtures", "seed-data", name), "utf8");
}

function restoreEnv(
  name: "DEEPSEEK_API_KEY" | "SOURCECADO_GENERATION_MODEL",
  value: string | undefined
): void {
  if (value === undefined) {
    delete process.env[name];
  } else {
    process.env[name] = value;
  }
}

afterEach(() => {
  for (const dir of tempDirs.splice(0)) {
    rmSync(dir, { recursive: true, force: true });
  }
});

describe("refreshMemory", () => {
  it("refreshes CSV-only memory without LLM model configuration", async () => {
    const previousKey = process.env.DEEPSEEK_API_KEY;
    const previousModel = process.env.SOURCECADO_GENERATION_MODEL;
    delete process.env.DEEPSEEK_API_KEY;
    delete process.env.SOURCECADO_GENERATION_MODEL;

    const dir = tempDir();
    const db = tempDb(dir);
    writeFileSync(
      join(dir, "csv-only.csv"),
      [
        "contact,organization,domain,status,outcome,notes,follow_up,reason",
        "Miguel Alvarez,Civic Data Lab,AI safety,contacted,interested,Asked for intro,yes,Need intro"
      ].join("\n")
    );
    ingestFolder(db, dir);

    try {
      await expect(refreshMemory(db)).resolves.toMatchObject({ failed: 0, extracted: 1 });
    } finally {
      restoreEnv("DEEPSEEK_API_KEY", previousKey);
      restoreEnv("SOURCECADO_GENERATION_MODEL", previousModel);
    }
    db.close();
  });

  it("records a failed extraction when unstructured sources need LLM extraction without config", async () => {
    // The construction-time guard was removed from createLlmExtractor so the gateway can
    // validate the active provider's key at call time (supporting non-DeepSeek providers via
    // SOURCECADO_GENERATION_PROVIDER). Without a key, extraction now fails gracefully (failed: 1)
    // rather than throwing from the extractor selection step.
    const previousKey = process.env.DEEPSEEK_API_KEY;
    const previousModel = process.env.SOURCECADO_GENERATION_MODEL;
    delete process.env.DEEPSEEK_API_KEY;
    delete process.env.SOURCECADO_GENERATION_MODEL;

    const dir = tempDir();
    const db = tempDb(dir);
    writeFileSync(join(dir, "thread.eml"), "Subject: AI safety\n\nMorgan needs follow-up.");
    ingestFolder(db, dir);

    try {
      const result = await refreshMemory(db);
      expect(result.extracted).toBe(0);
      expect(result.failed).toBeGreaterThan(0);
    } finally {
      restoreEnv("DEEPSEEK_API_KEY", previousKey);
      restoreEnv("SOURCECADO_GENERATION_MODEL", previousModel);
    }
    db.close();
  });

  it("dedupes duplicate semantic facts from repeated extraction evidence", async () => {
    const dir = tempDir();
    const db = tempDb(dir);
    const candidates: ExtractedCandidate[] = [
      {
        kind: "semantic_fact",
        subject: "Ada Chen",
        predicate: "status",
        object: "contacted",
        confidence: 0.91,
        evidenceText: "Ada Chen was contacted."
      },
      {
        kind: "semantic_fact",
        subject: "Ada Chen",
        predicate: "status",
        object: "contacted",
        confidence: 0.88,
        evidenceText: "Duplicate row says Ada Chen was contacted."
      }
    ];
    const extractor = createMockExtractor(candidates);

    writeFileSync(join(dir, "notes.txt"), "Ada Chen was contacted twice.");
    ingestFolder(db, dir);
    await refreshMemory(db, { extractorsBySourceType: { text: extractor } });

    const facts = getRows<{ subject: string; predicate: string; object: string; status: string }>(
      db,
      "select subject, predicate, object, status from semantic_facts"
    );

    expect(facts).toEqual([
      { subject: "Ada Chen", predicate: "status", object: "contacted", status: "accepted" }
    ]);
    db.close();
  });

  it("merges entity aliases that normalize to overlapping canonical people", async () => {
    const dir = tempDir();
    const db = tempDb(dir);

    await seedAndRefreshCsv(db, dir, "duplicate-aliases.csv");

    const people = getRows<{ id: number; name: string; canonical_key: string }>(
      db,
      "select id, name, canonical_key from entities where type = 'person' order by id"
    );
    const aliases = getRows<{ alias: string; alias_key: string; entity_id: number }>(
      db,
      "select alias, alias_key, entity_id from entity_aliases order by alias_key"
    );

    expect(people).toHaveLength(1);
    expect(people[0].name).toBe("Alex Rivera");
    expect(aliases).toEqual(
      expect.arrayContaining([
        { alias: "Alex Rivera", alias_key: "person:alex-rivera", entity_id: people[0].id },
        { alias: "Alex R.", alias_key: "person:alex-r", entity_id: people[0].id }
      ])
    );
    db.close();
  });

  it("does not merge different people just because they share a first name", async () => {
    const dir = tempDir();
    const db = tempDb(dir);

    writeFileSync(
      join(dir, "same-first-name.csv"),
      [
        "contact,organization,domain,status,outcome,notes,follow_up,reason",
        "Alex Rivera,Open Robotics Collective,robotics,contacted,interested,Met at demo night,no,",
        "Alex Morgan,Climate Data Guild,climate,responded,interested,Met through climate fellows,no,"
      ].join("\n")
    );
    ingestFolder(db, dir);
    await refreshMemory(db);

    const people = getRows<{ name: string }>(
      db,
      "select name from entities where type = 'person' order by name"
    );

    expect(people).toEqual([{ name: "Alex Morgan" }, { name: "Alex Rivera" }]);
    db.close();
  });

  it("marks same subject and predicate facts with different objects as conflicted", async () => {
    const dir = tempDir();
    const db = tempDb(dir);

    await seedAndRefreshCsv(db, dir, "conflicting-status.csv");

    const statuses = getRows<{ subject: string; predicate: string; object: string; status: string }>(
      db,
      "select subject, predicate, object, status from semantic_facts where subject = 'Maya Chen' and predicate = 'status' order by object"
    );

    expect(statuses).toEqual([
      { subject: "Maya Chen", predicate: "status", object: "contacted", status: "conflicted" },
      { subject: "Maya Chen", predicate: "status", object: "declined", status: "conflicted" }
    ]);
    db.close();
  });

  it("keeps low-confidence semantic facts as candidates", async () => {
    const dir = tempDir();
    const db = tempDb(dir);
    const extractor = createMockExtractor([
      {
        kind: "semantic_fact",
        subject: "Noor Patel",
        predicate: "status",
        object: "maybe interested",
        confidence: 0.62,
        evidenceText: "Noor Patel might be interested."
      }
    ]);

    writeFileSync(join(dir, "note.txt"), "Noor Patel might be interested.");
    ingestFolder(db, dir);
    await refreshMemory(db, { extractorsBySourceType: { text: extractor } });

    expect(getRows<{ status: string }>(db, "select status from semantic_facts")).toEqual([
      { status: "candidate" }
    ]);
    db.close();
  });

  it("reuses cached extraction runs and does not call extractor twice for unchanged chunks", async () => {
    const dir = tempDir();
    const db = tempDb(dir);
    const extract = vi.fn<Extractor["extract"]>().mockResolvedValue([
      {
        kind: "semantic_fact",
        subject: "Sam Wu",
        predicate: "status",
        object: "responded",
        confidence: 0.9,
        evidenceText: "Sam Wu responded."
      }
    ]);
    const extractor: Extractor = {
      type: "mock",
      version: "1",
      extract
    };

    writeFileSync(join(dir, "sam.txt"), "Sam Wu responded.");
    ingestFolder(db, dir);
    await refreshMemory(db, { extractorsBySourceType: { text: extractor } });
    await refreshMemory(db, { extractorsBySourceType: { text: extractor } });

    expect(extract).toHaveBeenCalledTimes(1);
    expect(getRows(db, "select * from extraction_runs where status = 'succeeded'")).toHaveLength(1);
    expect(getRows(db, "select * from semantic_facts")).toHaveLength(1);
    db.close();
  });

  it("does not reuse stale CSV cache entries from older extractor versions", async () => {
    const dir = tempDir();
    const db = tempDb(dir);
    writeFileSync(
      join(dir, "apollo.csv"),
      [
        "First Name,Last Name,Title,Company Name,Email",
        "Ada,Lovelace,Engineer,OpenAI,ada@example.com"
      ].join("\n")
    );
    ingestFolder(db, dir);

    const chunk = db
      .prepare("select id, chunk_hash from memory_chunks where citation = ?")
      .get("apollo.csv#row-1") as { id: number; chunk_hash: string };
    db.prepare(
      [
        "insert into extraction_runs (",
        "source_chunk_id, cache_key, chunk_hash, extractor_type, extractor_version,",
        "prompt_hash, schema_version, model_name, raw_output, parsed_candidates_json, status, error",
        ") values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
      ].join(" ")
    ).run(
      chunk.id,
      `${chunk.chunk_hash}:csv:1:none:1:local`,
      chunk.chunk_hash,
      "csv",
      "1",
      "none",
      "1",
      "local",
      JSON.stringify([]),
      JSON.stringify([
        {
          kind: "semantic_fact",
          subject: "Stale Cache",
          predicate: "status",
          object: "old",
          confidence: 0.9,
          evidenceText: "old"
        }
      ]),
      "succeeded",
      null
    );

    const result = await refreshMemory(db);

    expect(result).toMatchObject({ extracted: 1, reused: 0, failed: 0 });
    expect(
      getRows<{ subject: string; predicate: string; object: string }>(
        db,
        "select subject, predicate, object from semantic_facts where subject in ('Ada Lovelace', 'Stale Cache') order by subject, predicate"
      )
    ).toEqual(
      expect.arrayContaining([
        { subject: "Ada Lovelace", predicate: "email", object: "ada@example.com" },
        { subject: "Ada Lovelace", predicate: "title", object: "Engineer" }
      ])
    );
    expect(
      getRows<{ subject: string }>(
        db,
        "select subject from semantic_facts where subject = 'Stale Cache'"
      )
    ).toEqual([]);
    db.close();
  });

  it("does not force non-person relationship subjects into person entities", async () => {
    const dir = tempDir();
    const db = tempDb(dir);
    const extractor = createMockExtractor([
      {
        kind: "entity",
        subject: "Civic Data Lab",
        entityType: "organization",
        confidence: 0.94,
        evidenceText: "Civic Data Lab works with climate fellows."
      },
      {
        kind: "relationship",
        subject: "Civic Data Lab",
        relationshipType: "relevant_to_domain",
        object: "climate adaptation",
        confidence: 0.86,
        evidenceText: "Civic Data Lab works with climate fellows."
      }
    ]);

    writeFileSync(join(dir, "org.txt"), "Civic Data Lab works with climate fellows.");
    ingestFolder(db, dir);
    await refreshMemory(db, { extractorsBySourceType: { text: extractor } });

    const entities = getRows<{ type: string; name: string }>(
      db,
      "select type, name from entities where name = 'Civic Data Lab' order by type"
    );

    expect(entities).toEqual([{ type: "organization", name: "Civic Data Lab" }]);
    db.close();
  });

  it("keeps removed accepted facts as stale gaps after a later refresh", async () => {
    const dir = tempDir();
    const db = tempDb(dir);
    const extractor = createMockExtractor((input) =>
      input.content.includes("Sam Wu")
        ? [
            {
              kind: "semantic_fact",
              subject: "Sam Wu",
              predicate: "status",
              object: "responded",
              confidence: 0.9,
              evidenceText: "Sam Wu responded."
            }
          ]
        : []
    );

    writeFileSync(join(dir, "sam.txt"), "Sam Wu responded.");
    ingestFolder(db, dir);
    await refreshMemory(db, { extractorsBySourceType: { text: extractor } });
    writeFileSync(join(dir, "sam.txt"), "No sourcing memory remains in this file.");
    ingestFolder(db, dir);
    await refreshMemory(db, { extractorsBySourceType: { text: extractor } });

    expect(
      getRows<{ subject: string; status: string }>(
        db,
        "select subject, status from semantic_facts where subject = 'Sam Wu'"
      )
    ).toEqual([{ subject: "Sam Wu", status: "stale" }]);
    db.close();
  });
});
