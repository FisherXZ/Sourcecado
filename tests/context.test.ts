import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { closeDb, getDb } from "@/lib/db";
import { runMigrations } from "@/lib/migrate";
import { DEFAULT_ACTOR } from "@/lib/memory/actor";
import {
  buildSystemPrompt,
  buildMemoryIndexSection,
  buildMemoryAnswerInstructions,
  buildEnvironmentSection,
  IDENTITY_SECTION,
  STATIC_SECTIONS,
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
  opts: { sourceId: string; title: string; sourceType?: string }
): Promise<void> {
  await db`
    INSERT INTO source_records (source_id, path, title, source_type, content_hash, raw_text)
    VALUES (${opts.sourceId}, ${"/test/" + opts.sourceId}, ${opts.title}, ${opts.sourceType ?? "markdown"}, ${"hash-" + opts.sourceId}, '')
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

describe("static sections (v5)", () => {
  it("STATIC_SECTIONS are the seven v5 sections in §1–§7 order", () => {
    expect(STATIC_SECTIONS.map((s) => s.title)).toEqual([
      "Identity & mission",
      "Persistence",
      "Acting vs asking",
      "Sourcing doctrine",
      "Memory & citations",
      "Capabilities envelope",
      "Communication",
    ]);
    expect(STATIC_SECTIONS[0]).toBe(IDENTITY_SECTION);
  });

  it("carries the v5 doctrine, not the retired free-format guidance", () => {
    const doctrine = STATIC_SECTIONS.find((s) => s.title === "Sourcing doctrine")!;
    expect(doctrine.body).toMatch(/why-now/);
    const memory = STATIC_SECTIONS.find((s) => s.title === "Memory & citations")!;
    expect(memory.body).toMatch(/sourceId#chunk-N/);
    // No section carries the deleted identity text.
    expect(STATIC_SECTIONS.some((s) => /sourcing agent with access to team memory/.test(s.body))).toBe(false);
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

  it("lists a note only once — under 'Recent notes:', not also under 'Sources:'", async () => {
    const db = getDb();
    await insertSourceRow(db, { sourceId: "doc-1", title: "Doc One", sourceType: "markdown" });
    await insertSourceRow(db, { sourceId: "note-1", title: "Note One", sourceType: "note" });
    const section = await buildMemoryIndexSection(db, DEFAULT_ACTOR);

    // The note id must appear exactly once in the whole body.
    const occurrences = section.body.split("note-1").length - 1;
    expect(occurrences).toBe(1);
    // Non-note source lives under Sources:, the note under Recent notes:.
    const sourcesIdx = section.body.indexOf("Sources:");
    const notesIdx = section.body.indexOf("Recent notes:");
    expect(sourcesIdx).toBeGreaterThanOrEqual(0);
    expect(notesIdx).toBeGreaterThan(sourcesIdx);
    expect(section.body.slice(sourcesIdx, notesIdx)).toContain("doc-1");
    expect(section.body.slice(sourcesIdx, notesIdx)).not.toContain("note-1");
    expect(section.body.slice(notesIdx)).toContain("note-1");
  });

  it("renders a note-only workspace under 'Recent notes:' with no empty 'Sources:' header", async () => {
    const db = getDb();
    await insertSourceRow(db, { sourceId: "note-only", title: "Solo Note", sourceType: "note" });
    const section = await buildMemoryIndexSection(db, DEFAULT_ACTOR);
    expect(section.body).toContain("Recent notes:");
    expect(section.body).toContain("note-only");
    expect(section.body).not.toContain("Sources:");
    expect(section.body).not.toContain("No memory sources are indexed yet.");
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

  it("composes the seven static sections, then the memory index, then Environment last", async () => {
    const db = getDb();
    const instructions = await buildMemoryAnswerInstructions(db, DEFAULT_ACTOR);

    // Every static header appears, in §1–§7 order.
    const staticIdxs = STATIC_SECTIONS.map((s) => instructions.indexOf(`## ${s.title}`));
    expect(staticIdxs.every((i) => i >= 0)).toBe(true);
    for (let i = 1; i < staticIdxs.length; i++) {
      expect(staticIdxs[i]).toBeGreaterThan(staticIdxs[i - 1]);
    }

    // Memory Index follows the static sections; Environment follows the index and is last.
    const indexIdx = instructions.indexOf("## Memory Index");
    const envIdx = instructions.indexOf("## Environment");
    expect(indexIdx).toBeGreaterThan(staticIdxs[staticIdxs.length - 1]);
    expect(envIdx).toBeGreaterThan(indexIdx);

    // The Environment section carries today's date and is the trailing section.
    expect(instructions).toMatch(/## Environment\nToday's date: \d{4}-\d{2}-\d{2}$/);
  });
});

describe("buildEnvironmentSection", () => {
  it("renders today's date as an ISO YYYY-MM-DD line", () => {
    const section = buildEnvironmentSection(new Date("2026-07-15T09:30:00Z"));
    expect(section.title).toBe("Environment");
    expect(section.body).toBe("Today's date: 2026-07-15");
  });

  it("uses the team's Los Angeles timezone, not UTC, for the calendar day", () => {
    // 2026-07-16T05:00:00Z is still 2026-07-15 (22:00 PDT) in America/Los_Angeles.
    const section = buildEnvironmentSection(new Date("2026-07-16T05:00:00Z"));
    expect(section.body).toBe("Today's date: 2026-07-15");
  });
});
