import type { ComponentType } from "react";

import { ApolloPeopleResult } from "../generative/ApolloPeopleResult";
import { CalendarEventResult } from "../generative/CalendarEventResult";
import { GmailDraftResult } from "../generative/GmailDraftResult";
import { LivingBriefResult } from "../generative/LivingBriefResult";

export type DomainRendererSlot =
  | "apollo"
  | "gmail"
  | "calendar"
  | "brief"
  | "evidence";

export type DomainRendererProps = {
  readonly toolCallId: string;
  readonly toolName: string;
  readonly args?: unknown;
  readonly result: unknown;
  readonly status: "loading" | "success" | "error";
};

export type DomainRendererRegistry = Partial<
  Record<DomainRendererSlot, ComponentType<DomainRendererProps>>
>;

export type ToolIconKey =
  | "apollo"
  | "gmail"
  | "drive"
  | "calendar"
  | "granola"
  | "person"
  | "file"
  | "terminal"
  | "web"
  | "clock"
  | "tool";

export type ToolPresentation = {
  /** Past tense verb and object: "Searched Apollo". Shown once the step ends. */
  readonly label: string;
  /** The same step while it runs: "Searching Apollo". */
  readonly activeLabel: string;
  readonly category: "source" | "action";
  readonly icon: ToolIconKey;
  readonly slot?: DomainRendererSlot;
  /** What the step was asked, for the row: `for "Q3 sourcing"`. */
  readonly argsSummary?: (args: unknown) => string | null;
  /** What the step returned, for the right edge of the row: "12 people". */
  readonly resultCount?: (result: unknown) => string | null;
};

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function clip(value: string, limit = 200): string {
  return value.length > limit ? `${value.slice(0, limit - 1).trimEnd()}…` : value;
}

function forQuery(args: unknown): string | null {
  const query = text(record(args)?.query);
  return query ? `for “${clip(query)}”` : null;
}

function pathOf(args: unknown): string | null {
  const path = text(record(args)?.path);
  return path ? clip(path) : null;
}

function apolloSearchSummary(args: unknown): string | null {
  const fields = record(args);
  const titles = Array.isArray(fields?.personTitles)
    ? fields.personTitles.map(text).filter((title): title is string => title !== null)
    : [];
  const organization = text(fields?.organizationName);
  const who = titles.length > 0 ? clip(titles.join(", ")) : null;
  if (who && organization) return `for ${who} at ${clip(organization)}`;
  if (who) return `for ${who}`;
  return organization ? `at ${clip(organization)}` : null;
}

function apolloEnrichSummary(args: unknown): string | null {
  const fields = record(args);
  const first = text(fields?.firstName);
  const organization = text(fields?.organizationName);
  if (first && organization) return `for ${clip(first)} at ${clip(organization)}`;
  return first ? `for ${clip(first)}` : null;
}

function countOf(key: string, singular: string, plural: string) {
  return (result: unknown): string | null => {
    const rows = record(result)?.[key];
    if (!Array.isArray(rows)) return null;
    return `${rows.length} ${rows.length === 1 ? singular : plural}`;
  };
}

const peopleCount = countOf("people", "person", "people");
const messageCount = countOf("messages", "message", "messages");
const fileCount = countOf("files", "file", "files");
const eventCount = countOf("events", "event", "events");
const resultCount = countOf("results", "result", "results");
const meetingCount = countOf("meetings", "meeting", "meetings");

const KNOWN_TOOLS: Readonly<Record<string, ToolPresentation>> = {
  apollo_search_people: {
    label: "Searched Apollo",
    activeLabel: "Searching Apollo",
    category: "source",
    icon: "apollo",
    slot: "apollo",
    argsSummary: apolloSearchSummary,
    resultCount: peopleCount,
  },
  apollo_enrich_contact: {
    label: "Enriched contact with Apollo",
    activeLabel: "Enriching contact with Apollo",
    category: "source",
    icon: "apollo",
    slot: "apollo",
    argsSummary: apolloEnrichSummary,
  },
  people_keep: {
    label: "Kept people",
    activeLabel: "Keeping people",
    category: "action",
    icon: "person",
    argsSummary: (args) => {
      const rows = record(args)?.people;
      return Array.isArray(rows)
        ? `${rows.length} ${rows.length === 1 ? "person" : "people"}`
        : null;
    },
  },
  load_skill: {
    label: "Loaded skill",
    activeLabel: "Loading skill",
    category: "action",
    icon: "tool",
  },
  remember: {
    label: "Saved to memory",
    activeLabel: "Saving to memory",
    category: "action",
    icon: "tool",
  },
  memory_update: {
    label: "Updated memory",
    activeLabel: "Updating memory",
    category: "action",
    icon: "tool",
  },
  memory_forget: {
    label: "Removed from memory",
    activeLabel: "Removing from memory",
    category: "action",
    icon: "tool",
  },
  web_search: {
    label: "Searched the web",
    activeLabel: "Searching the web",
    category: "source",
    icon: "web",
    argsSummary: forQuery,
    resultCount,
  },
  drive_search: {
    label: "Searched Drive",
    activeLabel: "Searching Drive",
    category: "source",
    icon: "drive",
    slot: "evidence",
    argsSummary: forQuery,
    resultCount: fileCount,
  },
  drive_list_folder: {
    label: "Listed Drive folder",
    activeLabel: "Listing Drive folder",
    category: "source",
    icon: "drive",
    slot: "evidence",
    resultCount: fileCount,
  },
  drive_read: {
    label: "Read Drive evidence",
    activeLabel: "Reading Drive evidence",
    category: "source",
    icon: "drive",
    slot: "evidence",
  },
  gmail_search: {
    label: "Searched Gmail",
    activeLabel: "Searching Gmail",
    category: "source",
    icon: "gmail",
    slot: "gmail",
    argsSummary: forQuery,
    resultCount: messageCount,
  },
  gmail_read: {
    label: "Read Gmail evidence",
    activeLabel: "Reading Gmail evidence",
    category: "source",
    icon: "gmail",
    slot: "gmail",
  },
  gmail_draft: {
    label: "Prepared Gmail draft",
    activeLabel: "Preparing Gmail draft",
    category: "action",
    icon: "gmail",
    slot: "gmail",
  },
  gmail_create_draft: {
    label: "Prepared Gmail draft",
    activeLabel: "Preparing Gmail draft",
    category: "action",
    icon: "gmail",
    slot: "gmail",
  },
  gmail_send: {
    label: "Send Gmail draft",
    activeLabel: "Sending Gmail draft",
    category: "action",
    icon: "gmail",
    slot: "gmail",
  },
  calendar_list: {
    label: "Checked calendar",
    activeLabel: "Checking calendar",
    category: "source",
    icon: "calendar",
    slot: "calendar",
    resultCount: eventCount,
  },
  calendar_create: {
    label: "Prepared calendar event",
    activeLabel: "Preparing calendar event",
    category: "action",
    icon: "calendar",
    slot: "calendar",
  },
  calendar_update: {
    label: "Updated calendar event",
    activeLabel: "Updating calendar event",
    category: "action",
    icon: "calendar",
    slot: "calendar",
  },
  board_get: {
    label: "Read person file",
    activeLabel: "Reading person file",
    category: "source",
    icon: "person",
    slot: "brief",
  },
  board_query: {
    label: "Queried Board",
    activeLabel: "Querying Board",
    category: "source",
    icon: "person",
  },
  board_upsert: {
    label: "Updated person file",
    activeLabel: "Updating person file",
    category: "action",
    icon: "person",
  },
  board_mutate: {
    label: "Updated person file",
    activeLabel: "Updating person file",
    category: "action",
    icon: "person",
  },
  board_delete: {
    label: "Deleted person file",
    activeLabel: "Deleting person file",
    category: "action",
    icon: "person",
  },
  fs_stat: {
    label: "Inspected workspace path",
    activeLabel: "Inspecting workspace path",
    category: "source",
    icon: "file",
    argsSummary: pathOf,
  },
  fs_list: {
    label: "Listed workspace directory",
    activeLabel: "Listing workspace directory",
    category: "source",
    icon: "file",
    argsSummary: pathOf,
  },
  fs_find: {
    label: "Found workspace files",
    activeLabel: "Finding workspace files",
    category: "source",
    icon: "file",
  },
  fs_search: {
    label: "Searched workspace files",
    activeLabel: "Searching workspace files",
    category: "source",
    icon: "file",
    argsSummary: forQuery,
  },
  fs_read: {
    label: "Read workspace file",
    activeLabel: "Reading workspace file",
    category: "source",
    icon: "file",
    argsSummary: pathOf,
  },
  fs_mkdir: {
    label: "Created workspace directory",
    activeLabel: "Creating workspace directory",
    category: "action",
    icon: "file",
    argsSummary: pathOf,
  },
  fs_write: {
    label: "Wrote workspace file",
    activeLabel: "Writing workspace file",
    category: "action",
    icon: "file",
    argsSummary: pathOf,
  },
  fs_patch: {
    label: "Updated workspace file",
    activeLabel: "Updating workspace file",
    category: "action",
    icon: "file",
    argsSummary: pathOf,
  },
  fs_copy: {
    label: "Copied workspace file",
    activeLabel: "Copying workspace file",
    category: "action",
    icon: "file",
    argsSummary: pathOf,
  },
  fs_move: {
    label: "Moved workspace file",
    activeLabel: "Moving workspace file",
    category: "action",
    icon: "file",
    argsSummary: pathOf,
  },
  fs_trash: {
    label: "Trashed workspace item",
    activeLabel: "Trashing workspace item",
    category: "action",
    icon: "file",
    argsSummary: pathOf,
  },
  request_directory: {
    label: "Requested workspace access",
    activeLabel: "Requesting workspace access",
    category: "action",
    icon: "file",
  },
  // No argument summary for shell tools: a command line can carry a secret.
  shell_exec: {
    label: "Run workspace command",
    activeLabel: "Running workspace command",
    category: "action",
    icon: "terminal",
  },
  shell_poll: {
    label: "Checked command output",
    activeLabel: "Checking command output",
    category: "source",
    icon: "terminal",
  },
  shell_write_stdin: {
    label: "Sent command input",
    activeLabel: "Sending command input",
    category: "action",
    icon: "terminal",
  },
  shell_kill: {
    label: "Cancelled workspace command",
    activeLabel: "Cancelling workspace command",
    category: "action",
    icon: "terminal",
  },
  now: {
    label: "Checked current time",
    activeLabel: "Checking current time",
    category: "source",
    icon: "clock",
  },
};

const GENERIC_TOOL: ToolPresentation = {
  label: "Ran Sourcecado action",
  activeLabel: "Running Sourcecado action",
  category: "action",
  icon: "tool",
};

export function toolPresentation(toolName: string): ToolPresentation {
  if (
    toolName.startsWith("mcp__granola__") &&
    /__(?:list|search|read|get)_/.test(toolName)
  ) {
    return {
      label: "Checked Granola",
      activeLabel: "Checking Granola",
      category: "source",
      icon: "granola",
      slot: "evidence",
      argsSummary: forQuery,
      resultCount: meetingCount,
    };
  }
  return KNOWN_TOOLS[toolName] ?? GENERIC_TOOL;
}

export function isEvidenceToolName(toolName: string): boolean {
  return toolPresentation(toolName).slot === "evidence";
}

const DOMAIN_RENDERERS: DomainRendererRegistry = {
  apollo: ApolloPeopleResult,
  calendar: CalendarEventResult,
  gmail: GmailDraftResult,
  brief: LivingBriefResult,
};

export function domainRendererFor(
  toolName: string,
): ComponentType<DomainRendererProps> | null {
  const slot = toolPresentation(toolName).slot;
  return slot ? DOMAIN_RENDERERS[slot] ?? null : null;
}
