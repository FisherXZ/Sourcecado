import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { closeDb, getDb } from "@/lib/db";
import { runMigrations } from "@/lib/migrate";
import { DEFAULT_ACTOR, type MemoryActor } from "@/lib/memory/actor";
import { embedText, toVectorLiteral } from "@/lib/memory/embed";
import {
  asksForUncertainty,
  factIntentScore,
  lexicalScore,
  meaningfulQuestionTerms,
  questionIntent,
  rankRows,
} from "@/lib/memory/rank";
import { resolveAllowedSourceIds } from "@/lib/memory/permissions";
import { searchMemory } from "@/lib/memory/retrieve";

type Db = ReturnType<typeof getDb>;

// ---------------------------------------------------------------------------
// DB helpers
// ---------------------------------------------------------------------------

async function resetMemoryTables(): Promise<void> {
  const db = getDb();
  await db`DROP TABLE IF EXISTS source_permissions CASCADE`;
  await db`DROP TABLE IF EXISTS extraction_runs CASCADE`;
  await db`DROP TABLE IF EXISTS semantic_facts CASCADE`;
  await db`DROP TABLE IF EXISTS memory_chunks CASCADE`;
  await db`DROP TABLE IF EXISTS source_records CASCADE`;
  await db`DROP TABLE IF EXISTS schema_migrations CASCADE`;
  await runMigrations(db);
}

async function insertSource(db: Db, sourceId: string): Promise<string> {
  const [row] = await db<{ id: string }[]>`
    INSERT INTO source_records (source_id, path, source_type, content_hash)
    VALUES (
      ${sourceId},
      ${"/test/" + sourceId + ".md"},
      'markdown',
      ${"hash-" + sourceId}
    )
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
      ${opts.sourceRecordId},
      ${opts.chunkIndex ?? 0},
      ${opts.text},
      ${"chunk-hash-" + opts.citation},
      ${vecLiteral}::vector,
      ${opts.citation}
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
      ${opts.subject},
      ${opts.predicate},
      ${opts.object},
      ${opts.sourceRecordId},
      ${opts.sourceChunkId ?? null},
      ${opts.confidence ?? 0.9},
      ${opts.status}
    )
  `;
}

// ---------------------------------------------------------------------------
// Pure rank.ts unit tests
// ---------------------------------------------------------------------------

describe("questionIntent()", () => {
  it('classifies uncertainty phrases → "uncertainty"', () => {
    expect(questionIntent("what are the gaps?")).toBe("uncertainty");
    expect(questionIntent("any conflict here?")).toBe("uncertainty");
    expect(questionIntent("show stale facts")).toBe("uncertainty");
    expect(questionIntent("what is missing?")).toBe("uncertainty");
  });

  it('classifies collaboration phrases → "worked_with"', () => {
    expect(questionIntent("who worked with us before?")).toBe("worked_with");
    expect(questionIntent("who sponsored our project?")).toBe("worked_with");
    expect(questionIntent("who collaborated on this?")).toBe("worked_with");
  });

  it('classifies ghosted/no-reply phrases → "no_response"', () => {
    expect(questionIntent("who did not respond?")).toBe("no_response");
    expect(questionIntent("who ghosted us?")).toBe("no_response");
    expect(questionIntent("who gave no reply?")).toBe("no_response");
  });

  it('classifies response phrases → "responded"', () => {
    expect(questionIntent("who responded?")).toBe("responded");
    expect(questionIntent("who replied to us?")).toBe("responded");
    expect(questionIntent("who is in discussion?")).toBe("responded");
  });

  it('classifies follow-up phrases → "follow_up"', () => {
    expect(questionIntent("who needs follow-up?")).toBe("follow_up");
    expect(questionIntent("who needs followup?")).toBe("follow_up");
    expect(questionIntent("need follow up on this")).toBe("follow_up");
  });

  it('classifies general questions → "generic"', () => {
    expect(questionIntent("who are our contacts?")).toBe("generic");
    expect(questionIntent("list everyone")).toBe("generic");
    expect(questionIntent("show me AI safety leads")).toBe("generic");
  });
});

describe("asksForUncertainty()", () => {
  it("returns true for uncertainty keywords", () => {
    expect(asksForUncertainty("what are the gaps?")).toBe(true);
    expect(asksForUncertainty("show conflicted facts")).toBe(true);
    expect(asksForUncertainty("any candidate entries?")).toBe(true);
    expect(asksForUncertainty("stale records?")).toBe(true);
    expect(asksForUncertainty("missing info?")).toBe(true);
  });

  it("returns false for non-uncertainty phrases", () => {
    expect(asksForUncertainty("who responded?")).toBe(false);
    expect(asksForUncertainty("who needs follow-up?")).toBe(false);
    expect(asksForUncertainty("who worked with us?")).toBe(false);
  });
});

describe("meaningfulQuestionTerms()", () => {
  it("filters out stop words", () => {
    const terms = meaningfulQuestionTerms("what is the status");
    expect(terms).not.toContain("what");
    expect(terms).not.toContain("is");
    expect(terms).not.toContain("the");
    expect(terms).toContain("status");
  });

  it("filters out terms with length ≤ 2", () => {
    const terms = meaningfulQuestionTerms("is it ok");
    expect(terms).not.toContain("it");
    expect(terms).not.toContain("is");
    expect(terms).not.toContain("ok");
  });

  it("returns empty array for all-stop-word query", () => {
    expect(meaningfulQuestionTerms("what is the")).toHaveLength(0);
  });

  it("lowercases before matching", () => {
    const terms = meaningfulQuestionTerms("Who RESPONDED today");
    expect(terms).toContain("responded");
    expect(terms).toContain("today");
    expect(terms).not.toContain("who");
  });
});

describe("lexicalScore()", () => {
  it("returns 0 when no meaningful query terms appear in text", () => {
    expect(lexicalScore("apple orange banana", "quantum physics relativity")).toBe(0);
  });

  it("counts matching meaningful terms (case-insensitive)", () => {
    // "follow" is the only meaningful term in "who needs follow up"
    expect(lexicalScore("alice follow up contact", "who needs follow up")).toBe(1);
    expect(lexicalScore("ALICE RESPONDED today", "who responded today")).toBeGreaterThan(0);
  });

  it("returns 0 for text that shares only stop words with the query", () => {
    expect(lexicalScore("what is the thing", "what is the answer")).toBe(0);
  });

  it("accumulates score across multiple matching terms", () => {
    const score1 = lexicalScore("alice responded outreach", "responded outreach contact");
    const score2 = lexicalScore("alice responded", "responded outreach contact");
    expect(score1).toBeGreaterThan(score2);
  });
});

describe("factIntentScore()", () => {
  const makeFact = (predicate: string, object: string) => ({
    subject: "Alice",
    predicate,
    object,
    confidence: 0.9,
    status: "accepted",
    citation: null,
  });

  it('returns 2 for follow_up intent on needs_follow_up predicate', () => {
    expect(factIntentScore(makeFact("needs_follow_up", "yes"), "follow_up")).toBe(2);
  });

  it('returns 2 for follow_up intent on reason predicate', () => {
    expect(factIntentScore(makeFact("reason", "needs intro"), "follow_up")).toBe(2);
  });

  it('returns 3 for responded intent on status=responded', () => {
    expect(factIntentScore(makeFact("status", "responded"), "responded")).toBe(3);
  });

  it('returns 3 for no_response intent on status=ghosted', () => {
    expect(factIntentScore(makeFact("status", "ghosted"), "no_response")).toBe(3);
  });

  it('returns 3 for worked_with intent on status=locked in', () => {
    expect(factIntentScore(makeFact("status", "locked in"), "worked_with")).toBe(3);
  });

  it('returns 0 for codeology_owner predicate on worked_with intent', () => {
    expect(factIntentScore(makeFact("codeology_owner", "Rohan"), "worked_with")).toBe(0);
  });

  it('returns 0 for interest predicate on worked_with intent', () => {
    expect(factIntentScore(makeFact("interest", "Client project"), "worked_with")).toBe(0);
  });

  it('returns 0 for generic intent on any fact', () => {
    expect(factIntentScore(makeFact("status", "responded"), "generic")).toBe(0);
    expect(factIntentScore(makeFact("needs_follow_up", "yes"), "generic")).toBe(0);
  });
});

describe("rankRows()", () => {
  const facts = [
    { subject: "Alice", predicate: "status", object: "responded", confidence: 0.9, status: "accepted", citation: null },
    { subject: "Bob", predicate: "needs_follow_up", object: "yes", confidence: 0.8, status: "accepted", citation: null },
    { subject: "Carol", predicate: "domain", object: "robotics design", confidence: 0.7, status: "accepted", citation: null },
  ];
  const getText = (f: typeof facts[0]) => `${f.subject} ${f.predicate} ${f.object}`;

  it("filters to only intent-matching rows on strict intents (responded)", () => {
    const result = rankRows(facts, "who responded?", "responded", getText);
    expect(result.some((f) => f.subject === "Alice")).toBe(true);
    // Bob and Carol have no responded signal
    expect(result.some((f) => f.subject === "Bob")).toBe(false);
    expect(result.some((f) => f.subject === "Carol")).toBe(false);
  });

  it("includes lexically-matching rows on generic intent", () => {
    const result = rankRows(facts, "robotics domain", "generic", getText);
    expect(result.some((f) => f.subject === "Carol")).toBe(true);
  });

  it("returns all rows when no meaningful terms and generic intent", () => {
    const result = rankRows(facts, "what is the", "generic", getText);
    expect(result).toHaveLength(facts.length);
  });

  it("sorts by intentScore DESC then lexicalScore DESC then stable index", () => {
    const result = rankRows(facts, "who responded?", "responded", getText);
    // Alice has the highest intentScore (responded), so she comes first
    expect(result[0]?.subject).toBe("Alice");
  });
});

// ---------------------------------------------------------------------------
// resolveAllowedSourceIds — live Postgres
// ---------------------------------------------------------------------------

describe("resolveAllowedSourceIds (postgres)", () => {
  beforeEach(async () => {
    await resetMemoryTables();
  });

  afterEach(async () => {
    await closeDb();
  });

  it("returns source_ids granted to the actor", async () => {
    const db = getDb();
    await insertSource(db, "src-perm-a");
    await insertSource(db, "src-perm-b");
    await grantRead(db, DEFAULT_ACTOR, "src-perm-a");

    const allowed = await resolveAllowedSourceIds(db, DEFAULT_ACTOR);
    expect(allowed).toContain("src-perm-a");
    expect(allowed).not.toContain("src-perm-b");
  });

  it("returns [] for an actor with no permissions (default-deny)", async () => {
    const db = getDb();
    await insertSource(db, "src-no-perm");
    const noPermsActor: MemoryActor = { actorType: "test_client", actorId: "ghost" };
    const allowed = await resolveAllowedSourceIds(db, noPermsActor);
    expect(allowed).toHaveLength(0);
  });

  it("returns [] when there are no sources at all", async () => {
    const db = getDb();
    const allowed = await resolveAllowedSourceIds(db, DEFAULT_ACTOR);
    expect(allowed).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// searchMemory — live Postgres integration tests
// ---------------------------------------------------------------------------

describe("searchMemory (postgres)", () => {
  let savedApiKey: string | undefined;

  beforeEach(async () => {
    savedApiKey = process.env.OPENAI_API_KEY;
    delete process.env.OPENAI_API_KEY; // force offline hash embedding
    await resetMemoryTables();
  });

  afterEach(async () => {
    if (savedApiKey !== undefined) {
      process.env.OPENAI_API_KEY = savedApiKey;
    } else {
      delete process.env.OPENAI_API_KEY;
    }
    await closeDb();
  });

  it("returns correct bundle shape with expected field types", async () => {
    const db = getDb();
    const srcId = await insertSource(db, "src-shape");
    await grantRead(db, DEFAULT_ACTOR, "src-shape");
    const chunkId = await insertChunk(db, {
      sourceRecordId: srcId,
      text: "Alice responded to sourcing outreach",
      citation: "src-shape#chunk-1",
    });
    await insertFact(db, {
      subject: "Alice",
      predicate: "status",
      object: "responded",
      status: "accepted",
      sourceRecordId: srcId,
      sourceChunkId: chunkId,
    });

    const bundle = await searchMemory(db, { query: "who responded", actor: DEFAULT_ACTOR });

    expect(bundle).toHaveProperty("intent");
    expect(bundle).toHaveProperty("acceptedFacts");
    expect(bundle).toHaveProperty("gapFacts");
    expect(bundle).toHaveProperty("chunks");
    expect(Array.isArray(bundle.acceptedFacts)).toBe(true);
    expect(Array.isArray(bundle.gapFacts)).toBe(true);
    expect(Array.isArray(bundle.chunks)).toBe(true);
    expect(bundle.intent).toBe("responded");

    expect(bundle.acceptedFacts.length).toBeGreaterThan(0);
    const fact = bundle.acceptedFacts[0];
    expect(typeof fact.subject).toBe("string");
    expect(typeof fact.predicate).toBe("string");
    expect(typeof fact.object).toBe("string");
    expect(typeof fact.confidence).toBe("number");
    expect(typeof fact.status).toBe("string");
    // citation may be string or null
    expect(fact.citation === null || typeof fact.citation === "string").toBe(true);
  });

  it("THE acceptance test: restricted source never surfaces in any bundle field", async () => {
    const db = getDb();
    const query = "who responded to sourcing outreach";

    // Source A — granted to DEFAULT_ACTOR
    const srcAId = await insertSource(db, "src-allowed");
    await grantRead(db, DEFAULT_ACTOR, "src-allowed");
    const chunkAId = await insertChunk(db, {
      sourceRecordId: srcAId,
      text: "Alice responded to sourcing outreach contact",
      citation: "src-allowed#chunk-1",
    });
    await insertFact(db, {
      subject: "Alice",
      predicate: "status",
      object: "responded",
      status: "accepted",
      sourceRecordId: srcAId,
      sourceChunkId: chunkAId,
    });

    // Source B — NOT granted (no source_permissions row); text overlaps with query
    const srcBId = await insertSource(db, "src-restricted");
    // intentionally no grantRead
    const chunkBId = await insertChunk(db, {
      sourceRecordId: srcBId,
      text: "Bob responded to sourcing outreach contact",
      citation: "src-restricted#chunk-1",
    });
    await insertFact(db, {
      subject: "Bob",
      predicate: "status",
      object: "responded",
      status: "accepted",
      sourceRecordId: srcBId,
      sourceChunkId: chunkBId,
    });

    const bundle = await searchMemory(db, { query, actor: DEFAULT_ACTOR });

    // Alice (allowed) appears in acceptedFacts
    expect(bundle.acceptedFacts.some((f) => f.subject === "Alice")).toBe(true);

    // Bob (restricted) must NOT appear anywhere
    expect(bundle.acceptedFacts.some((f) => f.subject === "Bob")).toBe(false);
    expect(bundle.gapFacts.some((f) => f.subject === "Bob")).toBe(false);
    expect(bundle.chunks.some((c) => c.citation.includes("src-restricted"))).toBe(false);
  });

  it("empty allowed-set → empty bundle with correct intent (default-deny)", async () => {
    const db = getDb();
    // Insert a source but don't grant the test actor permission
    const srcId = await insertSource(db, "src-no-perm-test");
    await insertFact(db, {
      subject: "Alice",
      predicate: "status",
      object: "responded",
      status: "accepted",
      sourceRecordId: srcId,
    });

    const noPermActor: MemoryActor = { actorType: "test_client", actorId: "no-perms-actor" };
    const bundle = await searchMemory(db, { query: "who responded?", actor: noPermActor });

    expect(bundle.intent).toBe("responded");
    expect(bundle.acceptedFacts).toHaveLength(0);
    expect(bundle.gapFacts).toHaveLength(0);
    expect(bundle.chunks).toHaveLength(0);
  });

  it("candidate/conflicted/stale facts appear in gapFacts only, not acceptedFacts", async () => {
    const db = getDb();
    const srcId = await insertSource(db, "src-gaps");
    await grantRead(db, DEFAULT_ACTOR, "src-gaps");

    await insertFact(db, {
      subject: "Noor",
      predicate: "status",
      object: "maybe interested",
      status: "candidate",
      sourceRecordId: srcId,
    });
    await insertFact(db, {
      subject: "Maya",
      predicate: "status",
      object: "contacted",
      status: "conflicted",
      sourceRecordId: srcId,
    });
    await insertFact(db, {
      subject: "Leo",
      predicate: "status",
      object: "old data",
      status: "stale",
      sourceRecordId: srcId,
    });

    // Use uncertainty query so rankRows doesn't filter gapFacts by lexical match
    const bundle = await searchMemory(db, { query: "what gaps or conflicts exist?", actor: DEFAULT_ACTOR });

    expect(bundle.gapFacts.some((f) => f.subject === "Noor")).toBe(true);
    expect(bundle.gapFacts.some((f) => f.subject === "Maya")).toBe(true);
    expect(bundle.gapFacts.some((f) => f.subject === "Leo")).toBe(true);
    expect(bundle.acceptedFacts.some((f) => f.subject === "Noor")).toBe(false);
    expect(bundle.acceptedFacts.some((f) => f.subject === "Maya")).toBe(false);
    expect(bundle.acceptedFacts.some((f) => f.subject === "Leo")).toBe(false);
  });

  it("lexical gate: chunk with no matching query terms is excluded from chunks", async () => {
    const db = getDb();
    const srcId = await insertSource(db, "src-lexical");
    await grantRead(db, DEFAULT_ACTOR, "src-lexical");

    // This chunk has "follow" — the only meaningful term in "who needs follow up"
    await insertChunk(db, {
      sourceRecordId: srcId,
      text: "alice needs follow up from sourcing team",
      citation: "src-lexical#chunk-1",
      chunkIndex: 0,
    });

    // This chunk shares no meaningful query terms (zxqy/irrelevant won't match "follow")
    await insertChunk(db, {
      sourceRecordId: srcId,
      text: "zxqy irrelevant xywz unrelated data only",
      citation: "src-lexical#chunk-2",
      chunkIndex: 1,
    });

    const bundle = await searchMemory(db, { query: "who needs follow up", actor: DEFAULT_ACTOR });

    // Matching chunk appears
    expect(bundle.chunks.some((c) => c.citation === "src-lexical#chunk-1")).toBe(true);
    // Non-matching chunk is excluded by lexical gate
    expect(bundle.chunks.some((c) => c.citation === "src-lexical#chunk-2")).toBe(false);
  });

  it("each chunk carries a string citation and a numeric score > 0", async () => {
    const db = getDb();
    const srcId = await insertSource(db, "src-chunk-shape");
    await grantRead(db, DEFAULT_ACTOR, "src-chunk-shape");
    await insertChunk(db, {
      sourceRecordId: srcId,
      text: "Alice responded to our sourcing outreach",
      citation: "src-chunk-shape#chunk-1",
    });

    const bundle = await searchMemory(db, { query: "responded sourcing outreach", actor: DEFAULT_ACTOR });

    expect(bundle.chunks.length).toBeGreaterThan(0);
    for (const chunk of bundle.chunks) {
      expect(typeof chunk.text).toBe("string");
      expect(typeof chunk.citation).toBe("string");
      expect(chunk.citation.length).toBeGreaterThan(0);
      expect(typeof chunk.score).toBe("number");
      expect(chunk.score).toBeGreaterThan(0);
    }
  });

  it("uses DEFAULT_ACTOR when actor is omitted", async () => {
    const db = getDb();
    const srcId = await insertSource(db, "src-default-actor");
    await grantRead(db, DEFAULT_ACTOR, "src-default-actor");
    await insertFact(db, {
      subject: "Alice",
      predicate: "status",
      object: "responded",
      status: "accepted",
      sourceRecordId: srcId,
    });

    // No actor arg → should use DEFAULT_ACTOR
    const bundle = await searchMemory(db, { query: "who responded" });
    expect(bundle.acceptedFacts.some((f) => f.subject === "Alice")).toBe(true);
  });
});
