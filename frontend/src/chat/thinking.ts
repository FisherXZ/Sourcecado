import type { ReasoningMessagePart } from "@assistant-ui/react";

import { formatElapsed } from "./processState";

/**
 * Thinking time for one turn, from the backend's own timestamps. Finished
 * blocks add up to `settledMs`. A block still streaming has a start time and
 * the row counts up from it. `known` is false when no block carried a time,
 * so the row says "Thought" and never invents seconds.
 */
export type ReasoningTiming = {
  readonly settledMs: number;
  readonly runningSince: number | null;
  readonly known: boolean;
};

export function reasoningParts(
  content: readonly { readonly type: string }[],
): ReasoningMessagePart[] {
  return content.filter(
    (part): part is ReasoningMessagePart =>
      part.type === "reasoning" &&
      typeof (part as ReasoningMessagePart).text === "string" &&
      (part as ReasoningMessagePart).text.trim().length > 0,
  );
}

function sourcecadoTimes(part: ReasoningMessagePart): {
  readonly startedAt?: number;
  readonly endedAt?: number;
} {
  const meta = part.providerMetadata?.sourcecado as
    | { startedAt?: unknown; endedAt?: unknown }
    | undefined;
  return {
    ...(typeof meta?.startedAt === "number" ? { startedAt: meta.startedAt } : {}),
    ...(typeof meta?.endedAt === "number" ? { endedAt: meta.endedAt } : {}),
  };
}

export function reasoningTiming(
  parts: readonly ReasoningMessagePart[],
): ReasoningTiming {
  let settledMs = 0;
  let runningSince: number | null = null;
  let known = false;
  for (const part of parts) {
    const { startedAt, endedAt } = sourcecadoTimes(part);
    if (startedAt === undefined) continue;
    known = true;
    if (part.status?.type === "running") runningSince = startedAt;
    else settledMs += Math.max(0, (endedAt ?? startedAt) - startedAt);
  }
  return { settledMs, runningSince, known };
}

export function thinkingElapsedMs(
  timing: ReasoningTiming,
  now: number,
): number | null {
  if (!timing.known) return null;
  return (
    timing.settledMs +
    (timing.runningSince !== null ? Math.max(0, now - timing.runningSince) : 0)
  );
}

/** "Thought for 12s". Without a recorded time it is only "Thought". */
export function thoughtLabel(ms: number | null): string {
  return ms === null ? "Thought" : `Thought for ${formatElapsed(ms)}`;
}

/** The first line of the reasoning, for the collapsed row. */
export function firstReasoningLine(text: string): string {
  return (
    text
      .split("\n")
      .map((line) => line.trim())
      .find((line) => line.length > 0) ?? ""
  );
}

export function joinReasoning(parts: readonly ReasoningMessagePart[]): string {
  return parts.map((part) => part.text.trim()).join("\n\n");
}
