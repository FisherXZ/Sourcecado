import type { Sql } from "./tools/types";
import { DEFAULT_ACTOR, type MemoryActor } from "./memory/actor";
import { listMemoryIndexRows } from "./memory/sources";

export interface SystemPromptSection {
  title: string;
  body: string;
}

// Joins sections into one system-prompt string. Order matters — callers pass
// sections in the order they want them to appear.
export function buildSystemPrompt(sections: SystemPromptSection[]): string {
  return sections.map((s) => `## ${s.title}\n${s.body}`).join("\n\n");
}

export const IDENTITY_SECTION: SystemPromptSection = {
  title: "Identity",
  body:
    "You are a sourcing agent with access to team memory and tools. Decide when to search, when to answer directly, and when to record a finding.",
};

// Replaces the deleted MEMORY_INSTRUCTIONS four-section contract. Free-format:
// no fixed section headers, no "call search_memory every turn."
export const TOOL_USE_GUIDANCE_SECTION: SystemPromptSection = {
  title: "Tool-Use Guidance",
  body:
    "Search memory when the index below isn't enough to answer confidently — you decide when that is. Whenever you cite memory, cite inline as `sourceId#chunk-N` (or `#row-N`); never invent a citation id. If memory doesn't cover something, say so plainly instead of guessing.",
};

const MEMORY_INDEX_MAX_CHARS = 4000;

// Built once per run from a SQL query (title/date/kind for every permitted,
// non-archived source, plus the last ~20 memory notes), rendered as capped
// markdown. Truncates whole lines only, never mid-line.
export async function buildMemoryIndexSection(
  db: Sql,
  actor: MemoryActor = DEFAULT_ACTOR
): Promise<SystemPromptSection> {
  const { sources, recentNotes } = await listMemoryIndexRows(db, actor);

  const lines: string[] = [];
  if (sources.length === 0) {
    lines.push("No memory sources are indexed yet.");
  } else {
    lines.push("Sources:");
    for (const s of sources) {
      lines.push(
        `- ${s.sourceId} (${s.sourceType}, updated ${s.updatedAt.slice(0, 10)}): ${s.title ?? "(untitled)"}`
      );
    }
  }
  if (recentNotes.length > 0) {
    lines.push("", "Recent notes:");
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

// The memory-chat path's system-prompt composer: identity + tool-use
// guidance + the injected memory index, in that order. Callers (e.g.
// answerWithMemory) pass the returned string into runAgent()'s existing
// per-run `instructions` slot.
export async function buildMemoryAnswerInstructions(
  db: Sql,
  actor: MemoryActor = DEFAULT_ACTOR
): Promise<string> {
  const memoryIndex = await buildMemoryIndexSection(db, actor);
  return buildSystemPrompt([IDENTITY_SECTION, TOOL_USE_GUIDANCE_SECTION, memoryIndex]);
}
