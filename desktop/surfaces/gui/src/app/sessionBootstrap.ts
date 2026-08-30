import { getSessions } from "../api";

const SESSION_LIST_RETRY_DELAYS_MS = [100, 300, 900] as const;

/** Absorb the short sidecar/token startup race without hiding a real outage. */
export async function getSessionsForBoot(isActive: () => boolean) {
  let lastError: unknown;
  for (let attempt = 0; attempt <= SESSION_LIST_RETRY_DELAYS_MS.length; attempt += 1) {
    if (!isActive()) throw lastError ?? new Error("session bootstrap cancelled");
    try {
      return await getSessions();
    } catch (error: unknown) {
      lastError = error;
      if (!isActive()) throw error;
      const delay = SESSION_LIST_RETRY_DELAYS_MS[attempt];
      if (delay === undefined) throw error;
      await new Promise<void>((resolve) => window.setTimeout(resolve, delay));
    }
  }
  throw lastError;
}
