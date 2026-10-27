import { useEffect, useRef, useState } from "react";

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

/** Shortest time a live state stays on screen before another one replaces it. */
const MIN_HOLD_MS = 400;

/**
 * The status to show, kept steady.
 *
 * A fast tool turns Thinking into Working and back several times in one
 * second, which reads as a flicker. A live state therefore stays up for at
 * least MIN_HOLD_MS before another state replaces it. If the real state has
 * moved back to the shown one by then, nothing changes at all. The first state
 * after the chip appears, a run starting from Idle, and Needs approval show at
 * once, because the operator is waiting for them.
 */
function useSteadyStatus(status: RunStatus | null): RunStatus | null {
  const [held, setHeld] = useState(status);
  const heldSince = useRef(Date.now());
  const latest = useRef(status);
  latest.current = status;
  const atOnce =
    held === null || held === "idle" || status === null || status === "approval";
  const shown = atOnce ? status : held;

  useEffect(() => {
    if (shown !== held) {
      heldSince.current = Date.now();
      setHeld(shown);
      return;
    }
    if (status === held) return;
    const wait = Math.max(0, MIN_HOLD_MS - (Date.now() - heldSince.current));
    const timer = window.setTimeout(() => {
      if (latest.current === held) return;
      heldSince.current = Date.now();
      setHeld(latest.current);
    }, wait);
    return () => window.clearTimeout(timer);
  }, [status, held, shown]);

  return shown;
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
  /** Null while the thread loads: no chip, because Idle would be a guess. */
  readonly status?: RunStatus | null;
  /** Backend-measured run time. Null when it is not known; never shown as 0. */
  readonly elapsedMs?: number | null;
}) {
  const shown = useSteadyStatus(status);
  const name = title?.trim() || "New sourcing conversation";
  return (
    <header className="sourcecado-thread-header">
      <h1 title={name}>{name}</h1>
      {activePerson ? (
        <a
          className="sourcecado-active-person"
          href={`#/people/${encodeURIComponent(activePerson.person_id)}`}
          aria-label={`Active person: ${activePerson.label}`}
          title={activePerson.label}
        >
          <Icon name="person" />
          <span>{activePerson.label}</span>
        </a>
      ) : null}
      {shown ? (
        <p
          className={`sourcecado-run-status sourcecado-run-status-${shown}`}
          data-status={shown}
        >
          <Icon name={shown} />
          <span>{STATUS_LABEL[shown]}</span>
          {shown !== "idle" && elapsedMs !== null ? (
            <span className="sourcecado-run-status-time">
              {elapsedLabel(elapsedMs)}
            </span>
          ) : null}
        </p>
      ) : null}
    </header>
  );
}
