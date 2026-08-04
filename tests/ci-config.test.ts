import { readFileSync } from "node:fs";
import { join } from "node:path";

describe("CI configuration", () => {
  const root = process.cwd();
  const pkg = JSON.parse(readFileSync(join(root, "package.json"), "utf8"));

  it("exposes a typecheck script for CI to call", () => {
    expect(pkg.scripts).toHaveProperty("typecheck");
    expect(pkg.scripts.typecheck).toBe("tsc --noEmit");
  });

  it("pins the Node version to 24 (Vercel supports 22.x/24.x, not 26)", () => {
    const nvmrc = readFileSync(join(root, ".nvmrc"), "utf8").trim();
    expect(nvmrc).toBe("24");
  });

  it("runs CI against the same pgvector image as docker-compose", () => {
    const workflow = readFileSync(join(root, ".github/workflows/ci.yml"), "utf8");
    const compose = readFileSync(join(root, "docker-compose.yml"), "utf8");

    expect(workflow).toContain("pgvector/pgvector:pg16");
    expect(compose).toContain("pgvector/pgvector:pg16");
  });

  it("never enables live provider smoke tests in CI", () => {
    const workflow = readFileSync(join(root, ".github/workflows/ci.yml"), "utf8");
    // Matches a YAML assignment (`SOURCECADO_RUN_LIVE_SMOKE: ...`), not a
    // mention of the name in a comment — the workflow explains in prose why
    // the variable is deliberately absent.
    expect(workflow).not.toMatch(/^\s*SOURCECADO_RUN_LIVE_SMOKE\s*:/m);
  });

  it("does not shard or parallelize the suite (maxWorkers: 1 is deliberate)", () => {
    const workflow = readFileSync(join(root, ".github/workflows/ci.yml"), "utf8");
    // Flag forms only, for the same reason: the Test step's comment cites
    // vitest's maxWorkers setting as the justification for staying serial.
    expect(workflow).not.toContain("--shard");
    expect(workflow).not.toContain("--maxWorkers");
  });
});
