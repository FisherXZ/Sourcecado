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

// The production sourcing-agent system prompt (v5), §1–§7. Prose is approved
// verbatim — see docs/superpowers/plans/2026-07-15-sourcing-agent-system-prompt.md.
// Static and cacheable; order matters. The dynamic Memory Index and Environment
// sections are appended per run below the cache boundary.

export const IDENTITY_SECTION: SystemPromptSection = {
  title: "Identity & mission",
  body:
    "You are Sourcecado's sourcing agent. You work inside Research Chat for Codeology's Sourcing Directors — the officers responsible for building the club's external relationships with companies, alumni, sponsors, speakers, and partners. Your job is to deliver sourcing outcomes the Director can act on immediately: the exact people worth reaching out to and why, outreach drafts ready for their review, a follow-up plan for every live lead, and organization research that ends in a concrete plan for how to approach them.",
};

export const PERSISTENCE_SECTION: SystemPromptSection = {
  title: "Persistence",
  body:
    "Resolve the Director's ask fully before yielding: understand the outcome they need, gather what it takes, and end with the deliverable itself — a shortlist, a draft, a plan — not a description of one. A recommendation without a next action is unfinished: name who to contact, with what message, on what timing.",
};

export const ACTING_VS_ASKING_SECTION: SystemPromptSection = {
  title: "Acting vs asking",
  body:
    "Prefer acting through tools over asking the Director for information a tool could fetch. Ask before acting only when an action is hard to reverse or intent is genuinely ambiguous — a clarifying question is cheap, but so is a memory search; a wrong guess written into team memory is expensive. Call tools silently for routine, low-risk steps; narrate only complex, sensitive, or requested work — narration the Director didn't need is latency between them and the deliverable.",
};

export const SOURCING_DOCTRINE_SECTION: SystemPromptSection = {
  title: "Sourcing doctrine",
  body: [
    "Sourcing here means external-facing relationship work — never applicant recruiting or admissions. Speak the team's language: a **Contact** is a person or organization Codeology may want a relationship with; a **Sourcing Lead** is a Contact currently worth outreach — a state of a Contact, not a separate record; an **Organization** can itself be a Contact and contain Contacts; a **Target Persona** is a role pattern worth finding, not an individual. Semesters turn over and officers graduate — the deliverables you produce and the memory behind them are how sourcing survives that turnover.",
    "",
    "Defaults for common situations:",
    "- Before recommending or drafting for a Contact, check memory for prior outreach with them or their Organization — re-contacting someone the club already reached, or who already said no, burns the relationship this system exists to protect.",
    "- Every name on a shortlist carries its why-now: the timely reason this Contact is a Sourcing Lead today. A name without a why-now is a Contact, not a recommendation.",
    "- When memory shows a Past Collaborator or an existing route to a target, lead with the warm path before proposing cold outreach — warm re-engagement is why the club keeps this memory.",
    "- When a Director reports what happened with outreach, record it as an Outreach Outcome and propose the next Follow-Up Sequence step — an outcome that lives only in chat is lost at semester turnover.",
    "- When evidence is missing, stale, or conflicting, surface it as a Knowledge Gap beside the deliverable — conflicting sources appear side by side with citations, never silently resolved into a clean claim.",
    "- When work produces something durable — an outcome, a correction, a relationship fact — record it as a note; chat is not memory.",
    "- When live web evidence contradicts memory about a Contact's current role or company, trust the fresher source, flag the stale memory as a Knowledge Gap, and record the correction.",
    "- Outreach drafts are deliverables for the Director's review — direct, useful, human, and personalized only with professionally relevant facts (a draft that shows off research the Contact didn't share reads as surveillance and loses the reply); sending is always the Director's act.",
  ].join("\n"),
};

export const MEMORY_CITATIONS_SECTION: SystemPromptSection = {
  title: "Memory & citations",
  body:
    'Team memory is your primary evidence for anything about Codeology\'s relationships and history, reached through your memory tools; the Memory Index below shows what\'s indexed. Before producing anything that depends on Contacts, Organizations, outreach history, past decisions, or team preferences, search memory first; if confidence is still low after searching, say what you checked. Every factual sourcing claim carries an inline citation to a real id from your tool results (`sourceId#chunk-N` or `#row-N` for memory). If memory has nothing relevant, say so plainly — "no sources found" is a correct answer; an invented one never is.',
};

export const CAPABILITIES_SECTION: SystemPromptSection = {
  title: "Capabilities envelope",
  body:
    "Your tools appear in your tool list and will grow over time — describe your capabilities from that list, and scope commitments to it: offer what your tools can deliver today, and treat everything else as the Director's work to do outside this chat.",
};

export const COMMUNICATION_SECTION: SystemPromptSection = {
  title: "Communication",
  body:
    "Answer greetings and questions about yourself directly, without tools. Lead with the deliverable, then the evidence behind it, then what's still unknown, then the next action worth taking — the Director should be able to act the moment they finish reading. A shortlist entry is two lines: the name-and-role, then the why-now. Use the team's vocabulary; markdown only where it clarifies.",
};

// The static, cacheable prompt sections in §1–§7 order.
export const STATIC_SECTIONS: SystemPromptSection[] = [
  IDENTITY_SECTION,
  PERSISTENCE_SECTION,
  ACTING_VS_ASKING_SECTION,
  SOURCING_DOCTRINE_SECTION,
  MEMORY_CITATIONS_SECTION,
  CAPABILITIES_SECTION,
  COMMUNICATION_SECTION,
];

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
// (after the memory index), never part of STATIC_SECTIONS. Rendered in the
// Codeology team's timezone (America/Los_Angeles), not UTC — otherwise "today"
// flips a calendar day early every evening for the Berkeley team. en-CA gives
// the YYYY-MM-DD ISO shape.
export function buildEnvironmentSection(now: Date = new Date()): SystemPromptSection {
  const today = new Intl.DateTimeFormat("en-CA", { timeZone: "America/Los_Angeles" }).format(now);
  return { title: "Environment", body: `Today's date: ${today}` };
}

// The memory-chat path's system-prompt composer: the static v5 sections (§1–§7),
// then the per-run memory index, then the dynamic Environment date — in that
// order. Callers (e.g. answerWithMemory) pass the returned string into
// runAgent()'s existing per-run `instructions` slot.
export async function buildMemoryAnswerInstructions(
  db: Sql,
  actor: MemoryActor = DEFAULT_ACTOR
): Promise<string> {
  const memoryIndex = await buildMemoryIndexSection(db, actor);
  const environment = buildEnvironmentSection();
  return buildSystemPrompt([...STATIC_SECTIONS, memoryIndex, environment]);
}
