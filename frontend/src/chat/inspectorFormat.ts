import type { StepState } from "./processState";

/** The words for a step state, in the inspector header and the Progress list. */
export const STEP_STATE_LABEL: Readonly<Record<StepState, string>> = {
  running: "Running",
  waiting: "Needs approval",
  done: "Done",
  partial: "Partial",
  failed: "Failed",
  blocked: "Not allowed",
  denied: "Denied",
  interrupted: "Interrupted",
  unknown: "Outcome unknown",
};

const two = (value: number) => String(value).padStart(2, "0");

/** Local time of day, 24 hour clock: "14:02:11". */
export function formatClock(epochMs: number): string {
  const date = new Date(epochMs);
  return `${two(date.getHours())}:${two(date.getMinutes())}:${two(date.getSeconds())}`;
}

/**
 * How long a step took: "0.8s" under ten seconds, "12s" under a minute,
 * "1m 05s" after that. A real zero reads "<0.1s", never "0s".
 */
export function formatDuration(ms: number): string {
  const safe = Math.max(0, ms);
  if (safe < 100) return "<0.1s";
  if (safe < 10_000) return `${(safe / 1000).toFixed(1)}s`;
  const seconds = Math.round(safe / 1000);
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)}m ${two(seconds % 60)}s`;
}

/**
 * "Started 14:02:11 · 0.8s". A step with no recorded start has no honest
 * timing, so it returns null. A step that stopped with no recorded end shows
 * only its start.
 */
export function formatTiming(
  timing: { readonly startedAt: number; readonly completedAt?: number } | undefined,
  running: boolean,
  now: number,
): string | null {
  if (!timing) return null;
  const started = `Started ${formatClock(timing.startedAt)}`;
  if (timing.completedAt !== undefined) {
    return `${started} · ${formatDuration(timing.completedAt - timing.startedAt)}`;
  }
  return running ? `${started} · ${formatDuration(now - timing.startedAt)}` : started;
}

/** "personTitles" and "organization_name" become "Person titles" and "Organization name". */
export function humanizeKey(key: string): string {
  const spaced = key
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/[_\-.]+/g, " ")
    .trim();
  if (!spaced) return key;
  return spaced.charAt(0).toUpperCase() + spaced.slice(1).toLowerCase();
}

/**
 * An http or https link, or null for anything else: a `javascript:` URL, a
 * bad string, or an address with a user name or password in it. The link goes
 * to the operator's browser, so it must not carry a credential.
 */
export function safeExternalUrl(value: string | null | undefined): string | null {
  if (!value) return null;
  try {
    const parsed = new URL(value);
    if (parsed.username || parsed.password) return null;
    return parsed.protocol === "http:" || parsed.protocol === "https:"
      ? parsed.toString()
      : null;
  } catch {
    return null;
  }
}

/**
 * Shows an address that has a user name or a password in it without them:
 * `https://alice:pw@host/x` reads `https://***@host/x`. The user info ends at
 * the first `/`, `?` or `#`, so a path or query that holds an `@` is left
 * alone. This is for display only. The stored value does not change.
 */
export function maskUrlCredentials(value: unknown): unknown {
  if (typeof value === "string") {
    return value.replace(/(https?:\/\/)[^\s/?#@]*@/gi, "$1***@");
  }
  if (Array.isArray(value)) return value.map(maskUrlCredentials);
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [key, maskUrlCredentials(item)]),
    );
  }
  return value;
}
