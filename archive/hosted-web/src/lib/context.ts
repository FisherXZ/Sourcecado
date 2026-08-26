import type { Sql } from "./tools/types";
import { DEFAULT_ACTOR, type MemoryActor } from "./memory/actor";
import { listMemoryIndexRows } from "./memory/sources";
import { loadPersona, type Persona } from "./persona";

export interface SystemPromptSection {
  title: string;
  body: string;
}

// Joins sections into one system-prompt string. Order matters — callers pass
// sections in the order they want them to appear.
export function buildSystemPrompt(sections: SystemPromptSection[]): string {
  return sections.map((s) => `## ${s.title}\n${s.body}`).join("\n\n");
}

const MEMORY_INDEX_MAX_CHARS = 4000;

// Built once per run from a SQL query (title/date/kind for every permitted,
// non-archived source, plus the last ~20 memory notes), rendered as capped
// markdown. Truncates whole lines only, never mid-line.
export async function buildMemoryIndexSection(
  db: Sql,
  actor: MemoryActor = DEFAULT_ACTOR
): Promise<SystemPromptSection> {
  const { sources, recentNotes } = await listMemoryIndexRows(db, actor);

  // `sources` and `recentNotes` are disjoint (listMemoryIndexRows splits notes
  // out), so each entry renders in exactly one section.
  const lines: string[] = [];
  if (sources.length === 0 && recentNotes.length === 0) {
    lines.push("No memory sources are indexed yet.");
  }
  if (sources.length > 0) {
    lines.push("Sources:");
    for (const s of sources) {
      lines.push(
        `- ${s.sourceId} (${s.sourceType}, updated ${s.updatedAt.slice(0, 10)}): ${s.title ?? "(untitled)"}`
      );
    }
  }
  if (recentNotes.length > 0) {
    if (lines.length > 0) lines.push("");
    lines.push("Recent notes:");
    for (const n of recentNotes) {
      lines.push(`- ${n.sourceId} (updated ${n.updatedAt.slice(0, 10)}): ${n.title ?? "(untitled)"}`);
    }
  }

  return { title: "Memory Index", body: capMemoryIndexLines(lines) };
}

function capMemoryIndexLines(lines: string[]): string {
  let body = "";
  let shown = 0;
  for (const line of lines) {
    const candidate = body ? `${body}\n${line}` : line;
    if (candidate.length > MEMORY_INDEX_MAX_CHARS) break;
    body = candidate;
    shown++;
  }
  const omitted = lines.length - shown;
  if (omitted > 0) {
    body += `\n...(${omitted} more sources not shown)`;
  }
  return body;
}

// A Sourcing Lead is defined by timeliness, so ranking needs today's date. Built
// per run from new Date() at call time and appended below the cache boundary
// (after the memory index), never part of the persona's static sections. Rendered in the
// Codeology team's timezone (America/Los_Angeles), not UTC — otherwise "today"
// flips a calendar day early every evening for the Berkeley team. en-CA gives
// the YYYY-MM-DD ISO shape.
export function buildEnvironmentSection(now: Date = new Date()): SystemPromptSection {
  const today = new Intl.DateTimeFormat("en-CA", { timeZone: "America/Los_Angeles" }).format(now);
  return { title: "Environment", body: `Today's date: ${today}` };
}

// The memory-chat path's system-prompt composer: the persona's static sections
// (§1–§7, read from personas/<id>.md), then the per-run memory index, then the
// dynamic Environment date — in that order. Callers (e.g. answerWithMemory) pass
// the returned string into runAgent()'s existing per-run `instructions` slot.
export async function buildMemoryAnswerInstructions(
  db: Sql,
  actor: MemoryActor = DEFAULT_ACTOR,
  persona: Persona = loadPersona()
): Promise<string> {
  const memoryIndex = await buildMemoryIndexSection(db, actor);
  const environment = buildEnvironmentSection();
  return buildSystemPrompt([...persona.sections, memoryIndex, environment]);
}
