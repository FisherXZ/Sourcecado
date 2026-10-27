import type { ActivePerson } from "../api";
import type {
  SourcecadoStructuredMessage,
  SourcecadoToolPart,
} from "./messageAdapter";

export type RunStatus = "idle" | "thinking" | "working" | "approval";

const STATUS_LABEL: Readonly<Record<RunStatus, string>> = {
  idle: "Idle",
  thinking: "Thinking",
  working: "Working",
  approval: "Needs approval",
};

/**
 * One word for what the run is doing right now.
 *
 * Approval comes first and does not need a live run: a thread restored with a
 * parked approval still needs the operator. A finished turn never asks.
 * Working means a tool call is in flight. Thinking is every other live
 * moment: the model is reading, reasoning or writing the answer.
 */
export function runStatusOf(
  messages: readonly SourcecadoStructuredMessage[],
  running: boolean,
): RunStatus {
  const latest = [...messages].reverse().find((m) => m.role === "assistant");
  const tools = (latest?.parts ?? []).filter(
    (part): part is SourcecadoToolPart => part.type === "tool",
  );
  if (
    latest?.state === "waiting-approval" &&
    tools.some((tool) => tool.approval?.state === "pending")
  ) {
    return "approval";
  }
  if (!running) return "idle";
  return tools.some((tool) => tool.state === "running") ? "working" : "thinking";
}

function elapsedLabel(elapsedMs: number): string {
  const seconds = Math.floor(elapsedMs / 1000);
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)}m ${String(seconds % 60).padStart(2, "0")}s`;
}

const ICON_PATHS: Readonly<Record<RunStatus | "person", string>> = {
  idle: "M10 3.5a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13Z",
  thinking: "M10 2.5l1.7 4.8 4.8 1.7-4.8 1.7L10 15.5l-1.7-4.8L3.5 9l4.8-1.7z",
  working: "M2.5 10h3.5l2-5.5 4 11 2-5.5h3.5",
  approval:
    "M10 2.5l6 2v5c0 4-2.6 6.5-6 8-3.4-1.5-6-4-6-8v-5zM7.4 10l2 2 3.4-4",
  person:
    "M10 9.5a3 3 0 1 0 0-6 3 3 0 0 0 0 6ZM4 16.5c.6-3 3-4.5 6-4.5s5.4 1.5 6 4.5",
};

function Icon({ name }: { readonly name: RunStatus | "person" }) {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true" focusable="false">
      <path d={ICON_PATHS[name]} />
    </svg>
  );
}

export function ThreadHeader({
  title,
  activePerson,
  status = "idle",
  elapsedMs = null,
}: {
  readonly title: string | null;
  readonly activePerson?: ActivePerson | null;
  readonly status?: RunStatus;
  /** Backend-measured run time. Null when it is not known; never shown as 0. */
  readonly elapsedMs?: number | null;
}) {
  return (
    <header className="sourcecado-thread-header">
      <h1>{title?.trim() || "New sourcing conversation"}</h1>
      <p
        className={`sourcecado-run-status sourcecado-run-status-${status}`}
        data-status={status}
      >
        <Icon name={status} />
        <span>{STATUS_LABEL[status]}</span>
        {status !== "idle" && elapsedMs !== null ? (
          <span className="sourcecado-run-status-time">
            {elapsedLabel(elapsedMs)}
          </span>
        ) : null}
      </p>
      {activePerson ? (
        <a
          className="sourcecado-active-person"
          href={`#/people/${encodeURIComponent(activePerson.person_id)}`}
          aria-label={`Active person: ${activePerson.label}`}
        >
          <Icon name="person" />
          <span>{activePerson.label}</span>
        </a>
      ) : null}
    </header>
  );
}
