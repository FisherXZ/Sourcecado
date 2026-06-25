import { vi } from "vitest";
import { closeDb, getDb } from "@/lib/db";
import { getRunTrace } from "@/lib/ledger";
import { runMigrations } from "@/lib/migrate";
import type { ModelGatewayProvider } from "@/lib/model-gateway";
import { runAgent } from "@/lib/harness";
import { collectAllowedCitations, checkCitations, collectBundlesFromTrace } from "@/lib/memory/citations";
import { memoryRegistry } from "@/lib/memory/answer-config";
import { DEFAULT_ACTOR, type MemoryActor } from "@/lib/memory/actor";
import { embedText, toVectorLiteral } from "@/lib/memory/embed";
import type { MemoryBundle } from "@/lib/memory/retrieve";

type Db = ReturnType<typeof getDb>;

// ---------------------------------------------------------------------------
// DB helpers (mirrors memory-retrieve.test.ts pattern)
// ---------------------------------------------------------------------------

async function resetAllTables(db: Db): Promise<void> {
  await db`DROP TABLE IF EXISTS source_permissions CASCADE`;
  await db`DROP TABLE IF EXISTS extraction_runs CASCADE`;
  await db`DROP TABLE IF EXISTS semantic_facts CASCADE`;
  await db`DROP TABLE IF EXISTS memory_chunks CASCADE`;
  await db`DROP TABLE IF EXISTS source_records CASCADE`;
  await db`DROP TABLE IF EXISTS tool_calls CASCADE`;
  await db`DROP TABLE IF EXISTS model_calls CASCADE`;
  await db`DROP TABLE IF EXISTS run_steps CASCADE`;
  await db`DROP TABLE IF EXISTS runs CASCADE`;
  await db`DROP TABLE IF EXISTS schema_migrations CASCADE`;
  await runMigrations(db);
}

async function insertSource(db: Db, sourceId: string): Promise<string> {
  const [row] = await db<{ id: string }[]>`
    INSERT INTO source_records (source_id, path, source_type, content_hash)
    VALUES (${sourceId}, ${"/test/" + sourceId}, 'markdown', ${"hash-" + sourceId})
    RETURNING id
  `;
  return row.id;
}

async function grantRead(db: Db, actor: MemoryActor, sourceId: string): Promise<void> {
  await db`
    INSERT INTO source_permissions (principal_type, principal_id, source_id, access)
    VALUES (${actor.actorType}, ${actor.actorId}, ${sourceId}, 'read')
  `;
}

async function insertChunk(
  db: Db,
  opts: { sourceRecordId: string; text: string; citation: string; chunkIndex?: number }
): Promise<string> {
  const vec = await embedText(db, opts.text);
  const vecLiteral = toVectorLiteral(vec);
  const [row] = await db<{ id: string }[]>`
    INSERT INTO memory_chunks (source_record_id, chunk_index, text, chunk_hash, embedding, citation)
    VALUES (
      ${opts.sourceRecordId}, ${opts.chunkIndex ?? 0}, ${opts.text},
      ${"chunk-hash-" + opts.citation}, ${vecLiteral}::vector, ${opts.citation}
    )
    RETURNING id
  `;
  return row.id;
}

async function insertFact(
  db: Db,
  opts: {
    subject: string;
    predicate: string;
    object: string;
    status: string;
    sourceRecordId: string;
    sourceChunkId?: string | null;
    confidence?: number;
  }
): Promise<void> {
  await db`
    INSERT INTO semantic_facts
      (subject, predicate, object, source_record_id, source_chunk_id, confidence, status)
    VALUES (
      ${opts.subject}, ${opts.predicate}, ${opts.object},
      ${opts.sourceRecordId}, ${opts.sourceChunkId ?? null},
      ${opts.confidence ?? 0.9}, ${opts.status}
    )
  `;
}


// ---------------------------------------------------------------------------
// Pure unit tests — collectAllowedCitations
// ---------------------------------------------------------------------------

describe("collectAllowedCitations", () => {
  it("unions citations from acceptedFacts, gapFacts, and chunks", () => {
    const bundle: MemoryBundle = {
      intent: "generic",
      acceptedFacts: [
        { subject: "A", predicate: "p", object: "o", confidence: 0.9, status: "accepted", citation: "src1#chunk-1" },
      ],
      gapFacts: [
        { subject: "B", predicate: "p", object: "o", confidence: 0.5, status: "candidate", citation: "src1#chunk-2" },
      ],
      chunks: [{ text: "t", citation: "src1#chunk-3", score: 0.8 }],
    };
    const allowed = collectAllowedCitations([bundle]);
    expect(allowed.has("src1#chunk-1")).toBe(true);
    expect(allowed.has("src1#chunk-2")).toBe(true);
    expect(allowed.has("src1#chunk-3")).toBe(true);
    expect(allowed.size).toBe(3);
  });

  it("skips null citations in facts", () => {
    const bundle: MemoryBundle = {
      intent: "generic",
      acceptedFacts: [
        { subject: "A", predicate: "p", object: "o", confidence: 0.9, status: "accepted", citation: null },
      ],
      gapFacts: [],
      chunks: [],
    };
    const allowed = collectAllowedCitations([bundle]);
    expect(allowed.size).toBe(0);
  });

  it("unions across multiple bundles", () => {
    const b1: MemoryBundle = {
      intent: "generic",
      acceptedFacts: [],
      gapFacts: [],
      chunks: [{ text: "t", citation: "src1#chunk-1", score: 0.8 }],
    };
    const b2: MemoryBundle = {
      intent: "generic",
      acceptedFacts: [],
      gapFacts: [],
      chunks: [{ text: "t", citation: "src2#chunk-1", score: 0.8 }],
    };
    const allowed = collectAllowedCitations([b1, b2]);
    expect(allowed.has("src1#chunk-1")).toBe(true);
    expect(allowed.has("src2#chunk-1")).toBe(true);
    expect(allowed.size).toBe(2);
  });

  it("returns empty set for empty bundles array", () => {
    expect(collectAllowedCitations([])).toEqual(new Set());
  });
});

// ---------------------------------------------------------------------------
// Pure unit tests — checkCitations
// ---------------------------------------------------------------------------

describe("checkCitations", () => {
  it("returns no invalid when all cited ids are in allowed set", () => {
    const allowed = new Set(["src1#chunk-1", "src1#row-2"]);
    const answer = "Found data src1#chunk-1 and also src1#row-2.";
    const { invalid, sanitizedAnswer } = checkCitations(answer, allowed);
    expect(invalid).toHaveLength(0);
    expect(sanitizedAnswer).toContain("src1#chunk-1");
    expect(sanitizedAnswer).toContain("src1#row-2");
  });

  it("flags invented citation id and replaces it in sanitizedAnswer", () => {
    const allowed = new Set(["src1#chunk-1"]);
    const answer = "Found src1#chunk-1 but invented ghost#chunk-7 is wrong.";
    const { invalid, sanitizedAnswer } = checkCitations(answer, allowed);
    expect(invalid).toContain("ghost#chunk-7");
    expect(invalid).toHaveLength(1);
    expect(sanitizedAnswer).not.toContain("ghost#chunk-7");
    expect(sanitizedAnswer).toContain("src1#chunk-1");
    expect(sanitizedAnswer).toContain("[unverified citation removed]");
  });

  it("leaves valid citations untouched when removing invalid ones", () => {
    const allowed = new Set(["real#chunk-1"]);
    const answer = "Valid: real#chunk-1. Invalid: bad#row-99.";
    const { invalid, sanitizedAnswer } = checkCitations(answer, allowed);
    expect(invalid).toContain("bad#row-99");
    expect(sanitizedAnswer).toContain("real#chunk-1");
    expect(sanitizedAnswer).not.toContain("bad#row-99");
  });

  it("returns empty invalid and unchanged answer when no citations present", () => {
    const { invalid, sanitizedAnswer } = checkCitations("no memory found", new Set());
    expect(invalid).toHaveLength(0);
    expect(sanitizedAnswer).toBe("no memory found");
  });

  it("flags all cited ids when allowed set is empty", () => {
    const { invalid } = checkCitations("see ghost#row-5 for details", new Set());
    expect(invalid).toContain("ghost#row-5");
  });
});

// ---------------------------------------------------------------------------
// Integration tests — agentic flow with mock provider + real Postgres
// ---------------------------------------------------------------------------

describe("search_memory agentic flow (mock provider + postgres)", () => {
  let savedApiKey: string | undefined;

  beforeEach(async () => {
    savedApiKey = process.env.OPENAI_API_KEY;
    delete process.env.OPENAI_API_KEY; // force offline hash embedding
    const db = getDb();
    await resetAllTables(db);
  });

  afterEach(async () => {
    if (savedApiKey !== undefined) {
      process.env.OPENAI_API_KEY = savedApiKey;
    } else {
      delete process.env.OPENAI_API_KEY;
    }
    await closeDb();
  });

  it("tool ran and real citation from bundle validates (no invalid)", async () => {
    const db = getDb();
    const srcId = await insertSource(db, "src-answer-test");
    await grantRead(db, DEFAULT_ACTOR, "src-answer-test");
    const chunkId = await insertChunk(db, {
      sourceRecordId: srcId,
      text: "Alice responded to sourcing outreach",
      citation: "src-answer-test#chunk-1",
    });
    await insertFact(db, {
      subject: "Alice",
      predicate: "status",
      object: "responded",
      status: "accepted",
      sourceRecordId: srcId,
      sourceChunkId: chunkId,
    });

    const provider = vi
      .fn<ModelGatewayProvider>()
      .mockResolvedValueOnce({
        object: { action: "tool", tool: "search_memory", args: '{"query":"who responded"}' },
      })
      .mockResolvedValueOnce({
        object: {
          action: "final",
          answer:
            "Answer: Alice responded src-answer-test#chunk-1\nEvidence: src-answer-test#chunk-1\nGaps: None\nNext Action: Follow up",
        },
      });

    const registry = memoryRegistry();
    const result = await runAgent({
      question: "who responded?",
      registry,
      allowedClasses: new Set(["read"]),
      provider,
      db,
    });

    expect(result.status).toBe("succeeded");
    expect(provider).toHaveBeenCalledTimes(2);

    // Verify tool call is recorded in the ledger
    const trace = await getRunTrace(db, result.runId);
    expect(trace).not.toBeNull();
    const bundles = collectBundlesFromTrace(trace);
    expect(bundles).toHaveLength(1);

    // Citation post-check: the real citation is allowed
    const allowed = collectAllowedCitations(bundles);
    expect(allowed.has("src-answer-test#chunk-1")).toBe(true);

    const { invalid } = checkCitations(result.answer!, allowed);
    expect(invalid).toHaveLength(0);
  });

  it("invented citation is flagged in post-check", async () => {
    const db = getDb();
    const srcId = await insertSource(db, "src-invent-test");
    await grantRead(db, DEFAULT_ACTOR, "src-invent-test");
    await insertChunk(db, {
      sourceRecordId: srcId,
      text: "Bob responded to sourcing outreach",
      citation: "src-invent-test#chunk-1",
    });

    const provider = vi
      .fn<ModelGatewayProvider>()
      .mockResolvedValueOnce({
        object: { action: "tool", tool: "search_memory", args: '{"query":"who responded"}' },
      })
      .mockResolvedValueOnce({
        object: {
          action: "final",
          answer:
            "Answer: Bob responded src-invent-test#chunk-1 and ghost#chunk-99\nEvidence: src-invent-test#chunk-1\nGaps: None\nNext Action: None",
        },
      });

    const registry = memoryRegistry();
    const result = await runAgent({
      question: "who responded?",
      registry,
      allowedClasses: new Set(["read"]),
      provider,
      db,
    });

    expect(result.status).toBe("succeeded");

    const trace = await getRunTrace(db, result.runId);
    const bundles = collectBundlesFromTrace(trace);
    const allowed = collectAllowedCitations(bundles);

    const { invalid } = checkCitations(result.answer!, allowed);
    expect(invalid).toContain("ghost#chunk-99");
  });

  it("refuse-on-empty: empty memory → flow completes as succeeded, no invalid citations", async () => {
    // No data seeded — memory is empty, so search_memory returns empty bundle
    const provider = vi
      .fn<ModelGatewayProvider>()
      .mockResolvedValueOnce({
        object: { action: "tool", tool: "search_memory", args: '{"query":"who responded"}' },
      })
      .mockResolvedValueOnce({
        object: { action: "final", answer: "no relevant memory" },
      });

    const db = getDb();
    const registry = memoryRegistry();
    const result = await runAgent({
      question: "who responded?",
      registry,
      allowedClasses: new Set(["read"]),
      provider,
      db,
    });

    expect(result.status).toBe("succeeded");
    expect(result.answer).toBe("no relevant memory");

    const trace = await getRunTrace(db, result.runId);
    const bundles = collectBundlesFromTrace(trace);
    const allowed = collectAllowedCitations(bundles);
    expect(allowed.size).toBe(0);

    const { invalid } = checkCitations(result.answer!, allowed);
    expect(invalid).toHaveLength(0);
  });
});
