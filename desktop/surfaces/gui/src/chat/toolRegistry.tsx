import type { ComponentType } from "react";

import { ApolloPeopleResult } from "../generative/ApolloPeopleResult";
import { CalendarEventResult } from "../generative/CalendarEventResult";
import { GmailDraftResult } from "../generative/GmailDraftResult";

export type DomainRendererSlot =
  | "apollo"
  | "gmail"
  | "calendar"
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

export type ToolPresentation = {
  readonly label: string;
  readonly category: "source" | "action";
  readonly slot?: DomainRendererSlot;
};

const KNOWN_TOOLS: Readonly<Record<string, ToolPresentation>> = {
  apollo_search_people: {
    label: "Searched Apollo",
    category: "source",
    slot: "apollo",
  },
  apollo_enrich_contact: {
    label: "Enriched contact with Apollo",
    category: "source",
    slot: "apollo",
  },
  drive_search: {
    label: "Searched Drive",
    category: "source",
    slot: "evidence",
  },
  drive_list_folder: {
    label: "Listed Drive folder",
    category: "source",
    slot: "evidence",
  },
  drive_read: {
    label: "Read Drive evidence",
    category: "source",
    slot: "evidence",
  },
  gmail_search: {
    label: "Searched Gmail",
    category: "source",
    slot: "gmail",
  },
  gmail_read: {
    label: "Read Gmail evidence",
    category: "source",
    slot: "gmail",
  },
  gmail_draft: {
    label: "Prepared Gmail draft",
    category: "action",
    slot: "gmail",
  },
  gmail_create_draft: {
    label: "Prepared Gmail draft",
    category: "action",
    slot: "gmail",
  },
  gmail_send: {
    label: "Send Gmail draft",
    category: "action",
    slot: "gmail",
  },
  calendar_list: {
    label: "Checked calendar",
    category: "source",
    slot: "calendar",
  },
  calendar_create: {
    label: "Prepared calendar event",
    category: "action",
    slot: "calendar",
  },
  calendar_update: {
    label: "Updated calendar event",
    category: "action",
    slot: "calendar",
  },
  board_get: { label: "Read person file", category: "source" },
  board_query: { label: "Queried Board", category: "source" },
  board_upsert: { label: "Updated person file", category: "action" },
  board_mutate: { label: "Updated person file", category: "action" },
  board_delete: { label: "Deleted person file", category: "action" },
  fs_stat: { label: "Inspected workspace path", category: "source" },
  fs_list: { label: "Listed workspace directory", category: "source" },
  fs_find: { label: "Found workspace files", category: "source" },
  fs_search: { label: "Searched workspace files", category: "source" },
  fs_read: { label: "Read workspace file", category: "source" },
  fs_mkdir: { label: "Created workspace directory", category: "action" },
  fs_write: { label: "Wrote workspace file", category: "action" },
  fs_patch: { label: "Updated workspace file", category: "action" },
  fs_copy: { label: "Copied workspace file", category: "action" },
  fs_move: { label: "Moved workspace file", category: "action" },
  fs_trash: { label: "Trashed workspace item", category: "action" },
  request_directory: { label: "Requested workspace access", category: "action" },
  shell_exec: { label: "Run workspace command", category: "action" },
  shell_poll: { label: "Checked command output", category: "source" },
  shell_write_stdin: { label: "Sent command input", category: "action" },
  shell_kill: { label: "Cancelled workspace command", category: "action" },
  now: { label: "Checked current time", category: "source" },
};

export function toolPresentation(toolName: string): ToolPresentation {
  if (
    toolName.startsWith("mcp__granola__") &&
    /__(?:list|search|read|get)_/.test(toolName)
  ) {
    return {
      label: "Checked Granola",
      category: "source",
      slot: "evidence",
    };
  }
  return (
    KNOWN_TOOLS[toolName] ?? {
      label: "Ran Sourcecado action",
      category: "action",
    }
  );
}

export function isEvidenceToolName(toolName: string): boolean {
  return toolPresentation(toolName).slot === "evidence";
}

const DOMAIN_RENDERERS: DomainRendererRegistry = {
  apollo: ApolloPeopleResult,
  calendar: CalendarEventResult,
  gmail: GmailDraftResult,
};

export function domainRendererFor(
  toolName: string,
): ComponentType<DomainRendererProps> | null {
  const slot = toolPresentation(toolName).slot;
  return slot ? DOMAIN_RENDERERS[slot] ?? null : null;
}
