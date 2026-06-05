import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { main } from "../src/cli.js";

const tempDirs: string[] = [];
const originalCwd = process.cwd();

function tempDir(): string {
  const dir = mkdtempSync(join(tmpdir(), "sourcyavo-cli-test-"));
  tempDirs.push(dir);
  return dir;
}

afterEach(() => {
  process.chdir(originalCwd);
  vi.restoreAllMocks();
  for (const dir of tempDirs.splice(0)) {
    rmSync(dir, { recursive: true, force: true });
  }
});

describe("sourcyavo CLI", () => {
  it("runs ingest, refresh, and ask against a temp local database", async () => {
    const dir = tempDir();
    const seedData = join(dir, "seed-data");
    mkdirSync(seedData);
    writeFileSync(
      join(seedData, "outreach.csv"),
      [
        "contact,organization,domain,status,outcome,notes,needs_follow_up,reason",
        "Miguel Alvarez,Civic Data Lab,AI safety,contacted,interested,Asked for intro.,yes,Need intro to student organizer"
      ].join("\n")
    );
    process.chdir(dir);

    const log = vi.spyOn(console, "log").mockImplementation(() => undefined);

    await expect(Promise.resolve(main(["ingest", "seed-data"]))).resolves.toBe(0);
    await expect(Promise.resolve(main(["refresh"]))).resolves.toBe(0);
    await expect(
      Promise.resolve(main(["ask", "Who needs follow-up for AI safety?"]))
    ).resolves.toBe(0);

    const output = log.mock.calls.flat().join("\n");
    expect(output).toContain("Ingested 1 source file");
    expect(output).toContain("Refresh complete");
    expect(output).toContain("Miguel Alvarez");
    expect(output).toContain("Evidence:");
  });
});
