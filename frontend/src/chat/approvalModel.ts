import type { ToolCallMessagePartProps } from "@assistant-ui/react";

import type { ApprovalIconName } from "./approvalIcons";
import { sanitizeApolloNameMasks, withoutApolloNameMasks } from "../personName";

/**
 * What an approval card shows and what it may offer, derived from one pending
 * tool call. Pure functions only, so the rules that matter for safety (what is
 * shown as unavailable, who may be offered a standing grant) are tested without
 * a DOM.
 *
 * Payload shapes come from the backend:
 * - chat `gmail_send`: resource `gmail_draft` (turn.py `approval_resource`)
 * - person-bound send: resource `gmail_send_authority` (gmail.py `SendAuthority`)
 * - person-bound enrichment: resource `apollo_enrichment` (apollo.py)
 * - chat `apollo_enrich_contact`: arguments only, no resource
 * No payload carries a Gmail conversation, a reply thread, an Apollo credit
 * balance or a credit budget. Those rows say "Unavailable".
 */

export type ApprovalState =
  | "pending"
  | "submitting"
  | "allowed"
  | "denied"
  | "expired"
  | "cancelled"
  | "failed-submit"
  | "resolved-elsewhere";

export type ApprovalAudit = {
  readonly approvalState: ApprovalState;
  readonly actor: string | null;
  readonly requestedAt: string | null;
  readonly resolvedAt: string | null;
  readonly scope: string | null;
  readonly executionStatus: string | null;
  readonly executionError: string | null;
};

type Args = Readonly<Record<string, unknown>>;

const SURNAME_HIDDEN = "(surname hidden by Apollo)";

/**
 * Replaces a masked Apollo surname with words. The backend already appends
 * "(surname hidden by Apollo)" to the person it names, so a replaced mask would
 * leave the phrase twice. Collapse the repeat.
 */
export function cleanApolloText(text: string): string {
  return withoutApolloNameMasks(text)
    .replace(/\(surname hidden by Apollo\)(\s*\(surname hidden by Apollo\))+/g, SURNAME_HIDDEN)
    .trim();
}

function sourcecadoMetadata(
  part: ToolCallMessagePartProps,
): Record<string, unknown> | undefined {
  return part.providerMetadata?.sourcecado as Record<string, unknown> | undefined;
}

export function auditOf(part: ToolCallMessagePartProps): ApprovalAudit {
  const raw = sourcecadoMetadata(part);
  const standardState: ApprovalState = part.approval?.resolution
    ? part.approval.resolution
    : part.approval?.approved === true
      ? "allowed"
      : part.approval?.approved === false
        ? "denied"
        : "pending";
  const text = (value: unknown): string | null =>
    typeof value === "string" ? value : null;
  return {
    approvalState:
      typeof raw?.approvalState === "string"
        ? (raw.approvalState as ApprovalState)
        : standardState,
    actor: text(raw?.actor),
    requestedAt: text(raw?.requestedAt),
    resolvedAt: text(raw?.resolvedAt),
    scope: text(raw?.scope),
    executionStatus: text(raw?.executionStatus),
    executionError: text(raw?.executionError),
  };
}

export type ShellCommandResource = {
  readonly executionTarget: "docker" | "host" | "unknown";
  readonly commandSummary: string;
  readonly commandDisplay: string;
  readonly environmentKeys: readonly string[];
  readonly cwd: string | null;
  readonly fingerprint: string | null;
  readonly unsandboxed: boolean;
  readonly permanentEligible: boolean;
};

type ResourceRecord = Record<string, unknown>;

function resourceRecord(part: ToolCallMessagePartProps): ResourceRecord | null {
  const resource = sourcecadoMetadata(part)?.resource;
  return resource && typeof resource === "object" && !Array.isArray(resource)
    ? (resource as ResourceRecord)
    : null;
}

function str(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function shellCommandResourceOf(
  part: ToolCallMessagePartProps,
): ShellCommandResource | null {
  const record = resourceRecord(part);
  if (
    !record ||
    record.kind !== "shell_command" ||
    !["docker", "host", "unknown"].includes(String(record.execution_target)) ||
    typeof record.command_summary !== "string" ||
    typeof record.command_display !== "string" ||
    !Array.isArray(record.environment_keys) ||
    !record.environment_keys.every((key) => typeof key === "string") ||
    typeof record.unsandboxed !== "boolean" ||
    typeof record.permanent_eligible !== "boolean"
  ) {
    return null;
  }
  return {
    executionTarget: record.execution_target as "docker" | "host" | "unknown",
    commandSummary: record.command_summary,
    commandDisplay: record.command_display,
    environmentKeys: record.environment_keys as string[],
    cwd: typeof record.cwd === "string" ? record.cwd : null,
    fingerprint:
      typeof record.fingerprint === "string" ? record.fingerprint : null,
    unsandboxed: record.unsandboxed,
    permanentEligible: record.permanent_eligible === true,
  };
}

type SendFacts = {
  readonly account: string | null;
  readonly to: string | null;
  readonly subject: string | null;
  readonly draftId: string | null;
  readonly bodyDigest: string | null;
  readonly known: boolean;
};

function sendFactsOf(part: ToolCallMessagePartProps, args: Args): SendFacts {
  const record = resourceRecord(part);
  const argDraft = str(args.draft_id);
  if (
    record &&
    (record.kind === "gmail_draft" || record.kind === "gmail_send_authority")
  ) {
    return {
      account: str(record.account),
      to: str(record.to),
      subject: str(record.subject),
      draftId: str(record.draft_id) ?? argDraft,
      bodyDigest:
        record.kind === "gmail_send_authority" ? str(record.body_digest) : null,
      known: true,
    };
  }
  return {
    account: null,
    to: null,
    subject: null,
    draftId: argDraft,
    bodyDigest: null,
    known: false,
  };
}

type EnrichFacts = {
  readonly person: string | null;
  readonly title: string | null;
  readonly company: string | null;
  readonly matchedOn: string | null;
  readonly credits: number | null;
};

const MATCH_LABELS: Readonly<Record<string, string>> = {
  email: "Email",
  apollo_id: "Apollo ID",
  name: "Name",
};

function enrichFactsOf(part: ToolCallMessagePartProps, args: Args): EnrichFacts {
  const record = resourceRecord(part);
  if (record && record.kind === "apollo_enrichment") {
    const person = str(record.person);
    return {
      person: person ? cleanApolloText(person) : null,
      title: str(record.title),
      company: str(record.company),
      matchedOn: MATCH_LABELS[String(record.matched_on)] ?? null,
      credits:
        typeof record.credits === "number" && Number.isFinite(record.credits)
          ? record.credits
          : null,
    };
  }
  const safe = sanitizeApolloNameMasks(args) as Args;
  const first = str(safe.firstName);
  const last = str(safe.lastName);
  const email = str(safe.email);
  const name = [first, last].filter(Boolean).join(" ");
  return {
    person: name || email,
    title: null,
    company: str(safe.organizationName),
    matchedOn: email ? "Email" : first && last ? "Name" : null,
    credits: null,
  };
}

/**
 * The fields Apollo's match returns, and so the fields this request seeks. It
 * names fields only. The values do not exist until the provider answers.
 */
const ENRICH_FIELDS_SOUGHT = "Email, phone, title, company, LinkedIn";

export type ApprovalRow = {
  readonly key: string;
  readonly label: string;
  readonly icon: ApprovalIconName;
  /** null means the payload does not carry it; the card says so. */
  readonly value: string | null;
  readonly unavailable: string;
  readonly variant?: "body" | "code";
};

export type ApprovalView = {
  readonly title: string;
  readonly icon: ApprovalIconName;
  readonly rows: readonly ApprovalRow[];
  readonly spendsCredits: boolean;
  readonly calendarWrite: boolean;
  readonly shell: ShellCommandResource | null;
  /** Shown above the buttons when the payload cannot support the decision. */
  readonly caveat: string | null;
  /** Shown as a warning above the buttons. */
  readonly warning: string | null;
};

function row(
  key: string,
  label: string,
  icon: ApprovalIconName,
  value: string | null,
  unavailable = "Unavailable",
  variant?: "body" | "code",
): ApprovalRow {
  return { key, label, icon, value, unavailable, ...(variant ? { variant } : {}) };
}

const SENSITIVE_KEY = /token|secret|password|authorization|api[_-]?key|credential/i;

function humanKey(key: string): string {
  const spaced = key
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/[_-]+/g, " ")
    .trim()
    .toLowerCase();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

/** Scalar arguments as rows. Nested values are counted, not dumped. */
function genericRows(args: Args): ApprovalRow[] {
  return Object.entries(args)
    .filter(([key]) => !SENSITIVE_KEY.test(key))
    .map(([key, value]) => {
      let text: string | null;
      if (typeof value === "string") text = value.trim() || null;
      else if (typeof value === "number" || typeof value === "boolean")
        text = String(value);
      else if (Array.isArray(value)) text = `${value.length} items`;
      else if (value && typeof value === "object")
        text = `${Object.keys(value).length} fields`;
      else text = null;
      return row(`arg-${key}`, humanKey(key), "text", text);
    });
}

const WORKSPACE_TITLES: Readonly<Record<string, string>> = {
  fs_mkdir: "Create workspace directory",
  fs_write: "Write workspace file",
  fs_patch: "Update workspace file",
  fs_copy: "Copy workspace file",
  fs_move: "Move workspace file",
  fs_trash: "Trash workspace item",
  request_directory: "Request workspace access",
  shell_write_stdin: "Send input to workspace command",
  shell_kill: "Cancel workspace command",
};

function creditsLabel(credits: number): string {
  return `${credits} Apollo ${credits === 1 ? "credit" : "credits"}`;
}

export function approvalView(part: ToolCallMessagePartProps): ApprovalView {
  const toolName = part.toolName;
  const args = part.args as Args;
  const base = {
    spendsCredits: false,
    calendarWrite: false,
    shell: null as ShellCommandResource | null,
    caveat: null as string | null,
    warning: null as string | null,
  };

  if (toolName === "gmail_send") {
    const facts = sendFactsOf(part, args);
    return {
      ...base,
      title: facts.to ? `Send email to ${facts.to}` : "Send email",
      icon: "mail-send",
      caveat: facts.known
        ? null
        : "Sourcecado can’t show the recipient or subject for this approval. Review the drafted email before choosing Allow once.",
      rows: [
        row("account", "Account", "mail", facts.account, "Account could not be determined"),
        row("to", "To", "user", facts.to, "Recipient could not be determined"),
        row("subject", "Subject", "text", facts.subject, "Subject could not be determined"),
        row(
          "message",
          "Message",
          "message",
          facts.draftId ? `Gmail draft ${facts.draftId}` : null,
        ),
        row(
          "version",
          "Body version",
          "hash",
          facts.bodyDigest ? facts.bodyDigest.slice(0, 12) : null,
        ),
        row("conversation", "Conversation", "thread", null),
        row("effect", "Effect", "bolt", "Sends now. Cannot be undone."),
      ],
    };
  }

  if (toolName === "gmail_draft" || toolName === "gmail_create_draft") {
    const to = str(args.to);
    return {
      ...base,
      title: to ? `Create Gmail draft for ${to}` : "Create Gmail draft",
      icon: "mail-draft",
      rows: [
        row("to", "To", "user", to, "Recipient could not be determined"),
        row("subject", "Subject", "text", str(args.subject), "Subject could not be determined"),
        row("message", "Message", "message", str(args.body), "Unavailable", "body"),
        row("effect", "Effect", "bolt", "Creates a draft. Not sent."),
      ],
    };
  }

  if (toolName === "apollo_enrich_contact") {
    const facts = enrichFactsOf(part, args);
    return {
      ...base,
      title: facts.person ? `Enrich ${facts.person} with Apollo` : "Enrich contact with Apollo",
      icon: "sparkles",
      spendsCredits: true,
      warning: "Spends Apollo credits. No credit is spent until you choose Allow once.",
      rows: [
        row("person", "Person", "user", facts.person),
        ...(facts.title ? [row("title", "Title", "text", facts.title)] : []),
        row("company", "Company", "building", facts.company),
        row("match", "Matched on", "search", facts.matchedOn),
        row("fields", "Fields sought", "list", ENRICH_FIELDS_SOUGHT),
        row(
          "cost",
          "Estimated cost",
          "coins",
          facts.credits === null ? null : creditsLabel(facts.credits),
          "Unknown",
        ),
        row("budget", "Credit budget", "flag", null, "Not reported"),
      ],
    };
  }

  if (toolName === "calendar_create" || toolName === "calendar_update") {
    return {
      ...base,
      title:
        toolName === "calendar_create"
          ? "Create calendar event"
          : "Update calendar event",
      icon: "calendar",
      calendarWrite: true,
      rows: [],
    };
  }

  if (toolName === "board_delete") {
    return {
      ...base,
      title: "Delete person file",
      icon: "trash",
      rows: [
        row("person", "Person file", "file", str(args.person_id)),
        row(
          "version",
          "Expected version",
          "hash",
          typeof args.expected_version === "number"
            ? String(args.expected_version)
            : str(args.expected_version),
        ),
        row("reason", "Reason", "text", str(args.rationale_summary)),
      ],
    };
  }

  if (toolName === "shell_exec") {
    const shell = shellCommandResourceOf(part);
    return {
      ...base,
      title: "Run workspace command",
      icon: "terminal",
      shell,
      warning: shell
        ? shell.unsandboxed
          ? "This runs directly under your macOS account and can access everything that account can access."
          : "The real workspace is mounted read-write and container networking is unrestricted."
        : null,
      rows: [
        row(
          "sandbox",
          "Sandbox",
          "shield",
          shell ? (shell.unsandboxed ? "Not sandboxed" : "Docker sandbox") : null,
        ),
        row(
          "command",
          "Command",
          "terminal",
          shell?.commandDisplay ?? null,
          "Command details unavailable",
          "code",
        ),
        row("cwd", "Working folder", "folder", shell?.cwd ?? null, "Could not be resolved"),
        row(
          "env",
          "Environment",
          "list",
          shell
            ? shell.environmentKeys.length
              ? shell.environmentKeys.join(", ")
              : "No additional environment values."
            : null,
        ),
      ],
    };
  }

  const workspaceTitle = WORKSPACE_TITLES[toolName];
  return {
    ...base,
    title: workspaceTitle ?? "Run Sourcecado action",
    icon: toolName.startsWith("fs_") || toolName === "request_directory" ? "folder" : "bolt",
    rows: genericRows(args),
  };
}

/**
 * Never offered for email sends or paid enrichment, whatever the payload says.
 * The explicit deny list is deliberate: the shell grant is the only standing
 * permission that exists, and it must not become reachable for these tools by a
 * change to the allow rule below.
 */
const NEVER_STANDING = new Set(["gmail_send", "apollo_enrich_contact"]);

export function canOfferAlways(
  toolName: string,
  shell: ShellCommandResource | null,
): boolean {
  if (NEVER_STANDING.has(toolName)) return false;
  return (
    toolName === "shell_exec" &&
    shell !== null &&
    shell.unsandboxed &&
    shell.fingerprint !== null &&
    shell.permanentEligible
  );
}

/** The backend's generic policy text is not something the operator decides on. */
export function visibleReason(
  reason: string | undefined,
  toolName: string,
): string | null {
  const text = reason?.trim();
  if (!text || /^consequential\b/i.test(text)) return null;
  return toolName === "apollo_enrich_contact" ? cleanApolloText(text) : text;
}

export function displayedArguments(toolName: string, args: Args): Args {
  if (toolName !== "apollo_enrich_contact") return args;
  return sanitizeApolloNameMasks(args) as Args;
}

/** The raw request the Details toggle shows. Never the shell command. */
export function rawRequestText(part: ToolCallMessagePartProps): string {
  const record = resourceRecord(part);
  const resource =
    record && record.kind !== "shell_command"
      ? Object.fromEntries(
          Object.entries(record)
            .filter(([key]) => key !== "body")
            .map(([key, value]) => [
              key,
              key === "person" && typeof value === "string"
                ? cleanApolloText(value)
                : value,
            ]),
        )
      : null;
  return JSON.stringify(
    {
      tool: part.toolName,
      arguments: displayedArguments(part.toolName, part.args as Args),
      ...(resource ? { resource } : {}),
    },
    null,
    2,
  );
}

export function scopeStatement(toolName: string): string {
  if (toolName === "gmail_send") {
    return "Scope: allow once. Sourcecado will send this email now.";
  }
  if (toolName === "gmail_draft" || toolName === "gmail_create_draft") {
    return "Scope: allow once. Sourcecado will not send email.";
  }
  if (toolName === "calendar_create" || toolName === "calendar_update") {
    return "Scope: allow once. This changes Google Calendar and will not send email.";
  }
  if (toolName === "shell_exec") {
    return "Scope: allow once. Only this exact command request will run.";
  }
  if (toolName === "apollo_enrich_contact") {
    return "Scope: allow once. This spends credits for this one request only.";
  }
  return "Scope: allow once.";
}

export type ReceiptTone = "ok" | "neutral" | "warn";

export type ReceiptView = {
  readonly label: string;
  readonly tone: ReceiptTone;
  readonly icon: ApprovalIconName;
  readonly note: string | null;
};

/**
 * One decision, one word. A denial never carries a failure note: the backend
 * closes a denied call with ok=false, and that is not the call failing.
 */
export function receiptOf(audit: ApprovalAudit): ReceiptView {
  if (audit.executionStatus === "interrupted") {
    return { label: "Outcome unknown", tone: "warn", icon: "alert", note: null };
  }
  switch (audit.approvalState) {
    case "allowed":
      return audit.executionStatus === "failed"
        ? { label: "Allowed", tone: "warn", icon: "alert", note: "Action failed" }
        : { label: "Allowed", tone: "ok", icon: "check", note: null };
    case "denied":
      return { label: "Denied", tone: "neutral", icon: "x", note: null };
    case "expired":
      return { label: "Expired", tone: "warn", icon: "clock", note: null };
    case "cancelled":
      return { label: "Cancelled", tone: "neutral", icon: "minus", note: null };
    case "resolved-elsewhere":
      return { label: "Resolved elsewhere", tone: "neutral", icon: "minus", note: null };
    default:
      return { label: "Pending", tone: "neutral", icon: "clock", note: null };
  }
}

export function formatDecisionTime(iso: string | null): string | null {
  if (!iso) return null;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

export function executionText(status: string | null): string {
  if (!status) return "not recorded";
  return status === "not_run" ? "not run" : status.replace(/_/g, " ");
}
