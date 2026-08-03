import { readFileSync } from "node:fs";
import { join } from "node:path";

describe("package.json dependencies", () => {
  const pkg = JSON.parse(readFileSync(join(process.cwd(), "package.json"), "utf8"));
  const depNames = [
    ...Object.keys(pkg.dependencies ?? {}),
    ...Object.keys(pkg.devDependencies ?? {}),
  ];

  it("does not depend on the @ai-sdk/* provider packages", () => {
    const aiSdkDeps = depNames.filter((name) => name.startsWith("@ai-sdk/"));
    expect(aiSdkDeps).toEqual([]);
  });

  it("still depends on `ai` (R5's ui-message-stream SSE transport)", () => {
    // Full removal of `ai` is ticketed separately (rewrite the SSE transport
    // off createUIMessageStream — docs/superpowers/plans/2026-07-21-ticket-remove-ai-sdk-ui-stream.md).
    // Until then `ai` is a deliberate dependency.
    expect(pkg.dependencies).toHaveProperty("ai");
  });

  it("depends on the raw Anthropic and OpenAI SDKs", () => {
    expect(pkg.dependencies).toHaveProperty("@anthropic-ai/sdk");
    expect(pkg.dependencies).toHaveProperty("openai");
  });

  it("does not depend on better-sqlite3 (R10 removed the legacy CLI stack)", () => {
    // The native binding was never compiled on Node 26, which is why the repo
    // installed with --ignore-scripts. Postgres + pgvector replaced it; keeping
    // the dep out is what lets `npm install` run unpatched.
    const sqliteDeps = depNames.filter((name) => name.includes("better-sqlite3"));
    expect(sqliteDeps).toEqual([]);
  });
});
