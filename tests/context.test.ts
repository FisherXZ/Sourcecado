import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { closeDb, getDb } from "@/lib/db";
import { runMigrations } from "@/lib/migrate";
import { DEFAULT_ACTOR } from "@/lib/memory/actor";
import {
  buildSystemPrompt,
  buildMemoryIndexSection,
  buildMemoryAnswerInstructions,
  IDENTITY_SECTION,
  TOOL_USE_GUIDANCE_SECTION,
  type SystemPromptSection,
} from "@/lib/context";

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

async function insertSourceRow(
  db: ReturnType<typeof getDb>,
  opts: { sourceId: string; title: string }
): Promise<void> {
  await db`
    INSERT INTO source_records (source_id, path, title, source_type, content_hash, raw_text)
    VALUES (${opts.sourceId}, ${"/test/" + opts.sourceId}, ${opts.title}, 'markdown', ${"hash-" + opts.sourceId}, '')
  `;
  await db`
    INSERT INTO source_permissions (principal_type, principal_id, source_id, access)
    VALUES (${DEFAULT_ACTOR.actorType}, ${DEFAULT_ACTOR.actorId}, ${opts.sourceId}, 'read')
  `;
}

describe("buildSystemPrompt", () => {
  it("joins sections as '## Title\\nBody' separated by blank lines, in order", () => {
    const sections: SystemPromptSection[] = [
      { title: "One", body: "first" },
      { title: "Two", body: "second" },
    ];
    const prompt = buildSystemPrompt(sections);
    expect(prompt).toBe("## One\nfirst\n\n## Two\nsecond");
  });

  it("returns an empty string for an empty section list", () => {
    expect(buildSystemPrompt([])).toBe("");
  });
});

describe("fixed sections", () => {
  it("IDENTITY_SECTION and TOOL_USE_GUIDANCE_SECTION carry no fixed four-section format language", () => {
    expect(IDENTITY_SECTION.title).toBe("Identity");
    expect(TOOL_USE_GUIDANCE_SECTION.body).not.toMatch(/Answer:|Evidence:|Gaps:|Next Action:/);
    expect(TOOL_USE_GUIDANCE_SECTION.body).toMatch(/sourceId#chunk-N/);
  });
});

describe("buildMemoryIndexSection (postgres)", () => {
  let savedApiKey: string | undefined;

  beforeEach(async () => {
    savedApiKey = process.env.OPENAI_API_KEY;
    delete process.env.OPENAI_API_KEY;
    await resetMemoryTables();
  });

  afterEach(async () => {
    if (savedApiKey !== undefined) process.env.OPENAI_API_KEY = savedApiKey;
    else delete process.env.OPENAI_API_KEY;
    await closeDb();
  });

  it("renders a 'No memory sources are indexed yet.' body when nothing is indexed", async () => {
    const db = getDb();
    const section = await buildMemoryIndexSection(db, DEFAULT_ACTOR);
    expect(section.title).toBe("Memory Index");
    expect(section.body).toContain("No memory sources are indexed yet.");
  });

  it("lists permitted sources with title/type/date", async () => {
    const db = getDb();
    await insertSourceRow(db, { sourceId: "acme", title: "Acme" });
    const section = await buildMemoryIndexSection(db, DEFAULT_ACTOR);
    expect(section.body).toContain("acme");
    expect(section.body).toContain("Acme");
    expect(section.body).toContain("markdown");
  });

  it("caps the rendered body and appends an overflow notice when too many sources are indexed", async () => {
    const db = getDb();
    const longTitle = "X".repeat(80);
    for (let i = 0; i < 150; i++) {
      await insertSourceRow(db, { sourceId: `src-${i}`, title: longTitle });
    }
    const section = await buildMemoryIndexSection(db, DEFAULT_ACTOR);
    expect(section.body).toMatch(/\.\.\.\(\d+ more sources not shown\)/);
    // Cap check: strip the overflow-notice line before measuring — the
    // capped list content itself must not exceed 4000 chars.
    const withoutNotice = section.body.replace(/\n\.\.\.\(\d+ more sources not shown\)$/, "");
    expect(withoutNotice.length).toBeLessThanOrEqual(4000);
  });
});

describe("buildMemoryAnswerInstructions (postgres)", () => {
  beforeEach(async () => {
    delete process.env.OPENAI_API_KEY;
    await resetMemoryTables();
  });

  afterEach(async () => {
    await closeDb();
  });

  it("composes identity + tool-use guidance + memory index, in that order", async () => {
    const db = getDb();
    const instructions = await buildMemoryAnswerInstructions(db, DEFAULT_ACTOR);
    const identityIdx = instructions.indexOf("## Identity");
    const guidanceIdx = instructions.indexOf("## Tool-Use Guidance");
    const indexIdx = instructions.indexOf("## Memory Index");
    expect(identityIdx).toBeGreaterThanOrEqual(0);
    expect(guidanceIdx).toBeGreaterThan(identityIdx);
    expect(indexIdx).toBeGreaterThan(guidanceIdx);
  });
});
