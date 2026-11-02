import type { Board, BoardPerson, Connector, SkillInfo } from "../api";
import { connectorStatus } from "../connectorStatus";
import { hasApolloNameMask } from "../personName";

export type PromptIcon = "target" | "people" | "plug" | "skill";

export type PromptChip = {
  readonly id: "target" | "follow-up" | "connector" | "skill";
  readonly icon: PromptIcon;
  /** The words on the chip. */
  readonly label: string;
  /** What a click puts in the composer. It is never sent for the operator. */
  readonly prompt: string;
};

/** What the three sources returned. `null` means the source had no answer. */
export type PromptSources = {
  readonly board: Board | null;
  readonly connectors: readonly Connector[] | null;
  readonly skills: readonly SkillInfo[] | null;
};

export function greetingFor(now: Date): string {
  const hour = now.getHours();
  if (hour >= 5 && hour < 12) return "Good morning";
  if (hour >= 12 && hour < 17) return "Good afternoon";
  return "Good evening";
}

function nameOf(person: BoardPerson): string | null {
  const first = person.first_name?.trim() ?? "";
  const last = person.last_name?.trim() ?? "";
  // Apollo can hide a surname. Show the first name alone, never the mask.
  const lastShown =
    last &&
    !hasApolloNameMask(last) &&
    person.last_name_status !== "hidden_by_apollo" &&
    person.last_name_status !== "missing";
  return [first, lastShown ? last : ""].filter(Boolean).join(" ") || null;
}

/** A lane the backend left out, or sent as something else, is an empty lane. */
function lane(board: Board, name: "open" | "in_conversation" | "done"): BoardPerson[] {
  const people: unknown = board[name];
  return Array.isArray(people) ? people : [];
}

/** The Contacts page lists these three lanes. Backlog stays internal. */
function contactsOf(board: Board): BoardPerson[] {
  return [...lane(board, "open"), ...lane(board, "in_conversation"), ...lane(board, "done")];
}

const ZONELESS = /^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)$/;

/**
 * The backend stamps a sent message "2026-10-27 02:48:49" (UTC, no zone, from
 * SQLite) and a received one "2026-10-27T05:48:49Z". Date.parse reads a stamp
 * with no zone as local time, which puts a send hours off from a reply. Read
 * a stamp with no zone as UTC. Returns NaN for text that is not a time.
 */
export function contactTime(stamp: string | null | undefined): number {
  const text = (stamp ?? "").trim();
  const zoneless = ZONELESS.exec(text);
  return Date.parse(zoneless ? `${zoneless[1]}T${zoneless[2]}Z` : text);
}

function lastActiveTarget(board: Board): PromptChip | null {
  let latest: { person: BoardPerson; at: number } | null = null;
  for (const person of [...lane(board, "open"), ...lane(board, "in_conversation")]) {
    const at = contactTime(person.last_contact_at);
    if (Number.isNaN(at)) continue;
    if (latest === null || at > latest.at) latest = { person, at };
  }
  if (latest === null) return null;
  // The target is the director's own words for whom they are looking for. A
  // person kept without one still has a company, then a name, to go by.
  const subject =
    latest.person.target?.trim() || latest.person.company?.trim() || nameOf(latest.person);
  if (!subject) return null;
  return {
    id: "target",
    icon: "target",
    label: `Catch up on ${subject}`,
    prompt: `Catch me up on ${subject}: who we contacted, who replied, and the next step.`,
  };
}

function followUps(board: Board): PromptChip | null {
  const people = contactsOf(board).filter((person) => person.follow_up?.needed);
  if (people.length === 0) return null;
  if (people.length === 1) {
    const name = nameOf(people[0]);
    return {
      id: "follow-up",
      icon: "people",
      label: name ? `Follow up with ${name}` : "Follow up with 1 person",
      prompt: name
        ? `Review ${name} and propose the next follow-up.`
        : "Review the contact that needs follow-up and propose the next step.",
    };
  }
  return {
    id: "follow-up",
    icon: "people",
    label: `Follow up with ${people.length} people`,
    prompt: `Review the ${people.length} contacts that need follow-up and propose the next step for each.`,
  };
}

function connectorNeedingAttention(connectors: readonly Connector[]): PromptChip | null {
  for (const connector of connectors) {
    const status = connectorStatus(connector);
    if (status.tone !== "warn" && status.tone !== "error") continue;
    return {
      id: "connector",
      icon: "plug",
      label: `Fix ${connector.title}`,
      prompt: `${connector.title} shows “${status.label}”. What can you not do until it is fixed, and how do I fix it?`,
    };
  }
  return null;
}

function humanizeSkillName(name: string): string {
  const words = name.trim().replace(/[-_]+/g, " ").replace(/\s+/g, " ");
  return words ? `${words[0].toUpperCase()}${words.slice(1)}` : name;
}

function savedSkill(skills: readonly SkillInfo[]): PromptChip | null {
  // The operator's own skills come before the ones that ship with Sourcecado.
  const skill = skills.find((item) => item.source === "workspace") ?? skills[0];
  if (!skill) return null;
  return {
    id: "skill",
    icon: "skill",
    label: `Run ${humanizeSkillName(skill.name)}`,
    prompt: `Use the ${skill.name} skill.`,
  };
}

/**
 * Up to four prompts, each read from the operator's own data. A source with
 * no answer or no matching row adds nothing. The list gets shorter. It never
 * gets padded with a made-up prompt.
 */
export function buildPrompts({ board, connectors, skills }: PromptSources): PromptChip[] {
  // One malformed answer must cost its own chips and no others.
  const safely = (build: () => PromptChip | null): PromptChip | null => {
    try {
      return build();
    } catch {
      return null;
    }
  };
  const chips = [
    board ? safely(() => lastActiveTarget(board)) : null,
    board ? safely(() => followUps(board)) : null,
    connectors ? safely(() => connectorNeedingAttention(connectors)) : null,
    skills ? safely(() => savedSkill(skills)) : null,
  ];
  return chips.filter((chip): chip is PromptChip => chip !== null);
}
