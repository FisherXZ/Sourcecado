import { vi } from "vitest";

// next-auth is stubbed only so the module can be imported: this suite asserts
// on the exported `config.matcher`, not on Auth.js behavior. (Its real
// `next/server` import is also unresolvable under Vitest.)
vi.mock("next-auth", () => ({
  default: vi.fn(() => ({ auth: vi.fn() })),
}));
vi.mock("@/auth.config", () => ({ authConfig: {} }));

import { config } from "@/middleware";

// Mirrors how Next applies `matcher`: each entry is a full-path regex.
function isProtected(path: string): boolean {
  return config.matcher.some((pattern) => new RegExp(`^${pattern}$`).test(path));
}

describe("middleware matcher", () => {
  // Deny-by-default is the point: before H1 these four were reachable with no
  // session at all. A regression here is a silent auth bypass, not a test nit.
  it.each([
    "/",
    "/chat",
    "/memory",
    "/runs/1",
    "/styleguide",
    "/api/agent",
    "/api/agent/stream",
    "/api/memory/note",
    "/api/memory/sources",
    "/api/memory/import",
  ])("protects %s", (path) => {
    expect(isProtected(path)).toBe(true);
  });

  // Excluding these is deliberate: /api/auth would deadlock sign-in against
  // itself, /login must be reachable signed-out, and /api/health is an
  // unauthenticated probe.
  it.each([
    "/login",
    "/api/auth/signin",
    "/api/auth/callback/google",
    "/api/health",
    "/_next/static/chunk.js",
    "/favicon.ico",
  ])("leaves %s open", (path) => {
    expect(isProtected(path)).toBe(false);
  });
});
