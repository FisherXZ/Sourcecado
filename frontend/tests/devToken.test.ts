// @vitest-environment node
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { build } from "vite";
import config from "../vite.config";

const paths = vi.hoisted(() => ({ home: "" }));
vi.mock("node:os", async (original) => ({
  ...(await original<typeof import("node:os")>()),
  homedir: () => paths.home,
}));

let root: string;

beforeEach(() => {
  root = mkdtempSync(join(tmpdir(), "sourcecado-dev-token-"));
  paths.home = join(root, "home");
  mkdirSync(join(paths.home, ".config", "club"), { recursive: true });
  writeFileSync(join(paths.home, ".config", "club", "backend-8765.token"), "default-test-token\n");
  vi.stubEnv("VITE_CLUB_TOKEN", "");
  vi.stubEnv("CLUB_API_TOKEN", "");
  vi.stubEnv("CLUB_STATE_DIR", "");
});

afterEach(() => {
  vi.unstubAllEnvs();
  rmSync(root, { recursive: true, force: true });
});

function token(command: "serve" | "build" = "serve") {
  return config({ command, mode: "development" }).define?.__CLUB_DEV_TOKEN__;
}

it("uses the isolated backend's token when CLUB_STATE_DIR is set", () => {
  const state = join(root, "isolated");
  mkdirSync(state);
  writeFileSync(join(state, "backend-8765.token"), "isolated-test-token\n");
  vi.stubEnv("CLUB_STATE_DIR", state);
  expect(token()).toBe(JSON.stringify("isolated-test-token"));
});

it("expands a home-relative state directory like the backend", () => {
  mkdirSync(join(paths.home, "sandbox"));
  writeFileSync(join(paths.home, "sandbox", "backend-8765.token"), "sandbox-test-token\n");
  vi.stubEnv("CLUB_STATE_DIR", "~/sandbox");
  expect(token()).toBe(JSON.stringify("sandbox-test-token"));
});

it("does not fall back to another installation's token when isolated state is missing", () => {
  vi.stubEnv("CLUB_STATE_DIR", join(root, "absent"));
  expect(token()).toBe(JSON.stringify(""));
});

it("keeps the default state location when there is no override", () => {
  expect(token()).toBe(JSON.stringify("default-test-token"));
});

it("prefers explicitly supplied tokens over the state file", () => {
  vi.stubEnv("CLUB_API_TOKEN", "backend-test-token");
  expect(token()).toBe(JSON.stringify("backend-test-token"));
  vi.stubEnv("VITE_CLUB_TOKEN", "vite-test-token");
  expect(token()).toBe(JSON.stringify("vite-test-token"));
});

it("omits the development token define in build mode", () => {
  vi.stubEnv("VITE_CLUB_TOKEN", "vite-test-token");
  expect(token("build")).toBe(JSON.stringify(""));
});

it("builds the shipped app without embedding a token from the environment", async () => {
  const secret = "sourcecado-build-test-token-must-not-ship";
  vi.stubEnv("VITE_CLUB_TOKEN", secret);
  const result = await build({
    ...config({ command: "build", mode: "production" }),
    configFile: false,
    logLevel: "silent",
    build: { write: false, minify: false },
  });
  const bundles = Array.isArray(result) ? result : [result];
  for (const bundle of bundles) {
    if (!("output" in bundle)) throw new Error("Expected a completed build");
    for (const item of bundle.output) {
      const content = item.type === "chunk" ? item.code : String(item.source);
      expect(content.includes(secret)).toBe(false);
    }
  }
}, 30_000);
