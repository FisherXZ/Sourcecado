import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import { READ_ONLY_TOOLS } from "../src/chat/retrySafety";

/**
 * Retry is on only for read-only tools. The frontend list is a copy of what
 * the backend decides, so these tests read `permissions.py` and fail when the
 * two drift apart.
 */
const source = readFileSync("../backend/coworker/permissions.py", "utf8");

function names(variable: string): Set<string> {
  const match = source.match(
    new RegExp(`^${variable} = frozenset\\(\\s*\\{([^}]*)\\}`, "m"),
  );
  expect(match, `${variable} in permissions.py`).not.toBeNull();
  return new Set(Array.from(match![1]!.matchAll(/"([a-z_]+)"/g), (item) => item[1]!));
}

describe("the read-only tool list for Retry", () => {
  const auto = names("AUTO");
  const ask = names("ASK");
  const retrySafe = names("RETRY_SAFE");
  const workspaceAuto = names("_WORKSPACE_AUTO");

  it("keeps every tool the backend retries as safe", () => {
    for (const name of retrySafe) expect(READ_ONLY_TOOLS.has(name), name).toBe(true);
  });

  it("names only tools the backend runs without approval", () => {
    for (const name of READ_ONLY_TOOLS) {
      expect(auto.has(name) || workspaceAuto.has(name), name).toBe(true);
      expect(ask.has(name), name).toBe(false);
    }
  });

  // The backend's AUTO list has writes in it, so "automatic" alone is not "safe".
  it("leaves out every automatic tool that writes or acts", () => {
    const writes = [
      "remember",
      "memory_update",
      "memory_forget",
      "people_keep",
      "board_upsert",
      "board_mutate",
      "request_directory",
      "shell_poll",
      "shell_kill",
    ];
    for (const name of writes) expect(READ_ONLY_TOOLS.has(name), name).toBe(false);
  });

  it("leaves out every approval-gated tool", () => {
    for (const name of ask) expect(READ_ONLY_TOOLS.has(name), name).toBe(false);
  });
});
