import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { createDatabase, type MemoryDatabase } from "../src/db.js";
import { serializeEmbedding } from "../src/embeddings.js";
import {
  MemoryReader,
  resolveAccessContext,
  sourceIdInClause
} from "../src/read-service.js";

const tempDirs: string[] = [];

function tempDb(): MemoryDatabase {
  const dir = mkdtempSync(join(tmpdir(), "sourcyavo-read-service-test-"));
  tempDirs.push(dir);
  return createDatabase(join(dir, ".sourcyavo", "memory.db"));
}

function seedSource(
  db: MemoryDatabase,
  sourceId: string,
  options: {
    factSubject: string;
    factObject: string;
    chunkText: string;
    citation: string;
  }
): void {
  const sourceRecordId = db
    .prepare(
      "insert into source_records (path, source_id, title, source_type, content_hash, raw_text) values (?, ?, ?, ?, ?, ?) returning id"
    )
    .get(
      `${sourceId}.csv`,
      sourceId,
      sourceId,
      "csv",
      `hash-${sourceId}`,
      options.chunkText
    ) as { id: number };

  const chunkId = db
    .prepare(
      "insert into memory_chunks (source_record_id, chunk_index, text, chunk_hash, embedding, citation) values (?, ?, ?, ?, ?, ?) returning id"
    )
    .get(
      sourceRecordId.id,
      0,
      options.chunkText,
      `chunk-${sourceId}`,
      serializeEmbedding(options.chunkText),
      options.citation
    ) as { id: number };

  db.prepare(
    "insert into semantic_facts (subject, predicate, object, source_record_id, source_chunk_id, confidence, status) values (?, ?, ?, ?, ?, ?, ?)"
  ).run(
    options.factSubject,
    "needs_follow_up",
    options.factObject,
    sourceRecordId.id,
    chunkId.id,
    0.9,
    "accepted"
  );
}

function grant(
  db: MemoryDatabase,
  principalType: string,
  principalId: string,
  sourceId: string
): void {
  db.prepare(
    "insert into source_permissions (principal_type, principal_id, source_id, access) values (?, ?, ?, 'read')"
  ).run(principalType, principalId, sourceId);
}

function seedGapFact(
  db: MemoryDatabase,
  sourceId: string,
  options: { subject: string; object: string; status: string }
): void {
  const sourceRecordId = db
    .prepare("select id from source_records where source_id = ?")
    .get(sourceId) as { id: number };
  const chunkId = db
    .prepare("select id from memory_chunks where source_record_id = ? limit 1")
    .get(sourceRecordId.id) as { id: number };

  db.prepare(
    "insert into semantic_facts (subject, predicate, object, source_record_id, source_chunk_id, confidence, status) values (?, ?, ?, ?, ?, ?, ?)"
  ).run(
    options.subject,
    "needs_follow_up",
    options.object,
    sourceRecordId.id,
    chunkId.id,
    0.4,
    options.status
  );
}

function auditRows(
  db: MemoryDatabase
): Array<{ actor_type: string; actor_id: string; action: string; source_id: string | null }> {
  return db
    .prepare("select actor_type, actor_id, action, source_id from audit_events order by id")
    .all() as Array<{
    actor_type: string;
    actor_id: string;
    action: string;
    source_id: string | null;
  }>;
}

function seedTwoSources(db: MemoryDatabase): void {
  // Both chunks lexically match "robotics" so a scope leak would surface src-beta
  // content for client-a if the filter were post-retrieval instead of in SQL.
  seedSource(db, "src-alpha", {
    factSubject: "Alpha Contact",
    factObject: "robotics intro for alpha",
    chunkText: "Alpha Contact needs a follow-up about the robotics partnership.",
    citation: "src-alpha.csv#row-1"
  });
  seedSource(db, "src-beta", {
    factSubject: "Beta Contact",
    factObject: "robotics intro for beta",
    chunkText: "Beta Contact needs a follow-up about the robotics partnership.",
    citation: "src-beta.csv#row-1"
  });

  grant(db, "test_client", "client-a", "src-alpha");
  grant(db, "test_client", "client-b", "src-beta");
}

afterEach(() => {
  for (const dir of tempDirs.splice(0)) {
    rmSync(dir, { recursive: true, force: true });
  }
});

describe("resolveAccessContext", () => {
  it("resolves a single granted source for a principal", () => {
    const db = tempDb();
    seedTwoSources(db);

    const ctx = resolveAccessContext(db, { actorType: "test_client", actorId: "client-a" });
    expect(ctx.allowedSourceIds).toEqual(["src-alpha"]);
    expect(ctx.auditLabel).toBe("test_client:client-a");
    expect(ctx.deniedSourceIds).toContain("src-beta");
    db.close();
  });

  it("resolves multiple granted sources", () => {
    const db = tempDb();
    seedTwoSources(db);
    grant(db, "test_client", "client-a", "src-beta");

    const ctx = resolveAccessContext(db, { actorType: "test_client", actorId: "client-a" });
    expect(ctx.allowedSourceIds).toEqual(["src-alpha", "src-beta"]);
    expect(ctx.deniedSourceIds).toEqual([]);
    db.close();
  });

  it("default-denies an unknown principal", () => {
    const db = tempDb();
    seedTwoSources(db);

    const ctx = resolveAccessContext(db, { actorType: "test_client", actorId: "mallory" });
    expect(ctx.allowedSourceIds).toEqual([]);
    expect(ctx.deniedSourceIds).toEqual(["src-alpha", "src-beta"]);
    db.close();
  });
});

describe("sourceIdInClause", () => {
  it("builds a never-matching clause for an empty scope", () => {
    const clause = sourceIdInClause({ allowedSourceIds: [] });
    expect(clause.sql).toBe("1 = 0");
    expect(clause.params).toEqual([]);
  });

  it("builds a parameterized in-clause with the given alias", () => {
    const clause = sourceIdInClause({ allowedSourceIds: ["a", "b"] }, "x");
    expect(clause.sql).toBe("x.source_id in (?, ?)");
    expect(clause.params).toEqual(["a", "b"]);
  });
});

describe("MemoryReader scoped ask", () => {
  it("returns different answers and evidence for two clients on the same question", () => {
    const db = tempDb();
    seedTwoSources(db);

    const ctxA = resolveAccessContext(db, { actorType: "test_client", actorId: "client-a" });
    const ctxB = resolveAccessContext(db, { actorType: "test_client", actorId: "client-b" });

    const answerA = new MemoryReader(db, ctxA).ask("Who needs follow-up for robotics?");
    const answerB = new MemoryReader(db, ctxB).ask("Who needs follow-up for robotics?");

    expect(answerA).not.toBe(answerB);
    expect(answerA).toContain("Alpha Contact");
    expect(answerA).toContain("src-alpha.csv#row-1");
    expect(answerB).toContain("Beta Contact");
    expect(answerB).toContain("src-beta.csv#row-1");
    db.close();
  });

  it("never leaks a restricted fact, chunk, or citation across the scope boundary", () => {
    const db = tempDb();
    seedTwoSources(db);

    const ctxA = resolveAccessContext(db, { actorType: "test_client", actorId: "client-a" });
    const answerA = new MemoryReader(db, ctxA).ask("Who needs follow-up for robotics?");

    // The restricted accepted fact subject and object must be absent.
    expect(answerA).not.toContain("Beta Contact");
    expect(answerA).not.toContain("robotics intro for beta");
    // The restricted chunk's citation must be absent even though its text
    // lexically matches the query ("robotics").
    expect(answerA).not.toContain("src-beta.csv#row-1");
    db.close();
  });

  it("returns the explicit no-access answer for an unknown principal without querying globally", () => {
    const db = tempDb();
    seedTwoSources(db);

    const ctx = resolveAccessContext(db, { actorType: "test_client", actorId: "mallory" });
    const answer = new MemoryReader(db, ctx).ask("Who needs follow-up for robotics?");

    expect(answer.toLowerCase()).toContain("no access to any sources");
    expect(answer).not.toContain("Alpha Contact");
    expect(answer).not.toContain("Beta Contact");
    db.close();
  });
});

describe("MemoryReader.searchMemory", () => {
  it("returns only chunks from allowed sources, ranked by score", () => {
    const db = tempDb();
    seedTwoSources(db);

    const ctxA = resolveAccessContext(db, { actorType: "test_client", actorId: "client-a" });
    const results = new MemoryReader(db, ctxA).searchMemory("robotics partnership follow-up");

    expect(results.length).toBeGreaterThan(0);
    for (const result of results) {
      expect(result.sourceId).toBe("src-alpha");
    }
    expect(results.map((result) => result.citation)).toContain("src-alpha.csv#row-1");
    db.close();
  });

  it("never surfaces a higher-lexical-score restricted chunk", () => {
    const db = tempDb();
    seedTwoSources(db);
    // A restricted source whose chunk is an even stronger lexical/semantic match
    // for the query than the allowed one. A post-retrieval filter could rank it
    // first and then expose it; an in-SQL scope filter must drop it entirely.
    seedSource(db, "src-secret", {
      factSubject: "Secret Contact",
      factObject: "robotics robotics robotics partnership",
      chunkText:
        "robotics robotics robotics partnership follow-up robotics partnership robotics.",
      citation: "src-secret.csv#row-1"
    });

    const ctxA = resolveAccessContext(db, { actorType: "test_client", actorId: "client-a" });
    const results = new MemoryReader(db, ctxA).searchMemory("robotics partnership follow-up");

    expect(results.every((result) => result.sourceId === "src-alpha")).toBe(true);
    expect(results.map((result) => result.citation)).not.toContain("src-secret.csv#row-1");
    db.close();
  });

  it("returns an empty list for a zero-scope caller", () => {
    const db = tempDb();
    seedTwoSources(db);

    const ctx = resolveAccessContext(db, { actorType: "test_client", actorId: "mallory" });
    expect(new MemoryReader(db, ctx).searchMemory("robotics")).toEqual([]);
    db.close();
  });
});

describe("MemoryReader.getSource", () => {
  it("returns the source for an allowed id", () => {
    const db = tempDb();
    seedTwoSources(db);

    const ctxA = resolveAccessContext(db, { actorType: "test_client", actorId: "client-a" });
    const result = new MemoryReader(db, ctxA).getSource("src-alpha");

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.source.sourceId).toBe("src-alpha");
      expect(result.source.sourceType).toBe("csv");
    }
    db.close();
  });

  it("denies an existing but out-of-scope id without leaking existence", () => {
    const db = tempDb();
    seedTwoSources(db);

    const ctxA = resolveAccessContext(db, { actorType: "test_client", actorId: "client-a" });
    const reader = new MemoryReader(db, ctxA);

    // src-beta exists but is not in client-a's scope.
    expect(reader.getSource("src-beta")).toEqual({ ok: false, reason: "denied" });
    // A non-existent id must produce the SAME response so existence cannot be
    // probed by comparing reasons.
    expect(reader.getSource("src-does-not-exist")).toEqual({ ok: false, reason: "denied" });
    db.close();
  });

  it("reports missing only for an allowed id absent from source_records", () => {
    const db = tempDb();
    seedTwoSources(db);
    // Grant read on a source_id that has no source_records row (e.g. deleted).
    grant(db, "test_client", "client-a", "src-deleted");

    const ctxA = resolveAccessContext(db, { actorType: "test_client", actorId: "client-a" });
    expect(new MemoryReader(db, ctxA).getSource("src-deleted")).toEqual({
      ok: false,
      reason: "missing"
    });
    db.close();
  });

  it("reports malformed for empty, non-string, or oversize input", () => {
    const db = tempDb();
    seedTwoSources(db);

    const ctxA = resolveAccessContext(db, { actorType: "test_client", actorId: "client-a" });
    const reader = new MemoryReader(db, ctxA);

    expect(reader.getSource("")).toEqual({ ok: false, reason: "malformed" });
    expect(reader.getSource(42)).toEqual({ ok: false, reason: "malformed" });
    expect(reader.getSource(null)).toEqual({ ok: false, reason: "malformed" });
    expect(reader.getSource("x".repeat(1000))).toEqual({ ok: false, reason: "malformed" });
    db.close();
  });

  it("denies every getSource for a zero-scope caller", () => {
    const db = tempDb();
    seedTwoSources(db);

    const ctx = resolveAccessContext(db, { actorType: "test_client", actorId: "mallory" });
    expect(new MemoryReader(db, ctx).getSource("src-alpha")).toEqual({
      ok: false,
      reason: "denied"
    });
    db.close();
  });
});

describe("MemoryReader.listGaps", () => {
  it("returns only gaps from allowed sources and hides restricted gaps", () => {
    const db = tempDb();
    seedTwoSources(db);
    seedGapFact(db, "src-alpha", {
      subject: "Alpha Contact",
      object: "candidate alpha detail",
      status: "candidate"
    });
    seedGapFact(db, "src-beta", {
      subject: "Beta Contact",
      object: "candidate beta detail",
      status: "candidate"
    });

    const ctxA = resolveAccessContext(db, { actorType: "test_client", actorId: "client-a" });
    const gaps = new MemoryReader(db, ctxA).listGaps();

    expect(gaps.length).toBe(1);
    expect(gaps[0]?.subject).toBe("Alpha Contact");
    expect(gaps.every((gap) => gap.object !== "candidate beta detail")).toBe(true);
    db.close();
  });

  it("returns an empty list for a zero-scope caller", () => {
    const db = tempDb();
    seedTwoSources(db);
    seedGapFact(db, "src-alpha", {
      subject: "Alpha Contact",
      object: "candidate alpha detail",
      status: "candidate"
    });

    const ctx = resolveAccessContext(db, { actorType: "test_client", actorId: "mallory" });
    expect(new MemoryReader(db, ctx).listGaps()).toEqual([]);
    db.close();
  });
});

describe("MemoryReader no-access UX", () => {
  it("renders an explicit no-access Answer with all four sections from ask", () => {
    const db = tempDb();
    seedTwoSources(db);

    const ctx = resolveAccessContext(db, { actorType: "test_client", actorId: "mallory" });
    const answer = new MemoryReader(db, ctx).ask("Who needs follow-up for robotics?");

    expect(answer).toContain("Answer:");
    expect(answer).toContain("Evidence:");
    expect(answer).toContain("Gaps:");
    expect(answer).toContain("Next Action:");
    expect(answer.toLowerCase()).toContain("no access to any sources");
    expect(answer).not.toContain("Alpha Contact");
    expect(answer).not.toContain("Beta Contact");
    db.close();
  });
});

describe("MemoryReader audit", () => {
  it("writes a success audit row with correct actor and action for each read", () => {
    const db = tempDb();
    seedTwoSources(db);
    seedGapFact(db, "src-alpha", {
      subject: "Alpha Contact",
      object: "candidate alpha detail",
      status: "candidate"
    });

    const ctxA = resolveAccessContext(db, { actorType: "test_client", actorId: "client-a" });
    const reader = new MemoryReader(db, ctxA);

    reader.ask("Who needs follow-up for robotics?");
    reader.searchMemory("robotics");
    reader.getSource("src-alpha");
    reader.listGaps();

    const rows = auditRows(db);
    expect(rows.map((row) => row.action)).toEqual([
      "ask",
      "search_memory",
      "get_source",
      "list_gaps"
    ]);
    for (const row of rows) {
      expect(row.actor_type).toBe("test_client");
      expect(row.actor_id).toBe("client-a");
    }
    const getSourceRow = rows.find((row) => row.action === "get_source");
    expect(getSourceRow?.source_id).toBe("src-alpha");
    db.close();
  });

  it("writes a denied_read audit row on denied getSource", () => {
    const db = tempDb();
    seedTwoSources(db);

    const ctxA = resolveAccessContext(db, { actorType: "test_client", actorId: "client-a" });
    new MemoryReader(db, ctxA).getSource("src-beta");

    const rows = auditRows(db);
    expect(rows.length).toBe(1);
    expect(rows[0]?.action).toBe("denied_read");
    expect(rows[0]?.actor_type).toBe("test_client");
    expect(rows[0]?.actor_id).toBe("client-a");
    expect(rows[0]?.source_id).toBe("src-beta");
    db.close();
  });

  it("writes a denied_read audit row for a zero-scope ask", () => {
    const db = tempDb();
    seedTwoSources(db);

    const ctx = resolveAccessContext(db, { actorType: "test_client", actorId: "mallory" });
    new MemoryReader(db, ctx).ask("Who needs follow-up?");

    const rows = auditRows(db);
    expect(rows.length).toBe(1);
    expect(rows[0]?.action).toBe("denied_read");
    expect(rows[0]?.actor_id).toBe("mallory");
    expect(rows[0]?.source_id).toBe(null);
    db.close();
  });
});
