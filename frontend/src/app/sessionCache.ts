import type { SessionListing } from "../api";

export const SHELL_CACHE_KEY = "sourcecado.shell.sessions.v1";

export function readShellCache(): SessionListing | null {
  try {
    const raw = window.localStorage.getItem(SHELL_CACHE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as SessionListing;
    return Array.isArray(parsed.sessions) ? parsed : null;
  } catch {
    return null;
  }
}

export function writeShellCache(listing: SessionListing) {
  try {
    window.localStorage.setItem(SHELL_CACHE_KEY, JSON.stringify(listing));
  } catch {
    // A full or unavailable cache must never prevent live navigation.
  }
}
