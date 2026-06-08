import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { main } from "../src/cli.js";
import { openMemoryDatabase } from "../src/db.js";

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

    // Grant the scoped client read access to every ingested source so the
    // happy-path ask returns the ingested memory.
    const grantDb = openMemoryDatabase(".sourcyavo/memory.db");
    const sourceIds = (
      grantDb
        .prepare("select source_id from source_records where source_id is not null")
        .all() as Array<{ source_id: string }>
    ).map((row) => row.source_id);
    const grant = grantDb.prepare(
      "insert into source_permissions (principal_type, principal_id, source_id) values ('test_client', 'qa-client', ?)"
    );
    for (const sourceId of sourceIds) {
      grant.run(sourceId);
    }
    grantDb.close();

    await expect(
      Promise.resolve(main(["ask", "--client", "qa-client", "Who needs follow-up for AI safety?"]))
    ).resolves.toBe(0);

    const output = log.mock.calls.flat().join("\n");
    expect(output).toContain("Ingested 1 source file");
    expect(output).toContain("Refresh complete");
    expect(output).toContain("Miguel Alvarez");
    expect(output).toContain("Evidence:");
  });

  it("refuses an ask without --client and exits 1", async () => {
    const dir = tempDir();
    process.chdir(dir);

    const error = vi.spyOn(console, "error").mockImplementation(() => undefined);

    await expect(
      Promise.resolve(main(["ask", "Who needs follow-up for AI safety?"]))
    ).resolves.toBe(1);

    const output = error.mock.calls.flat().join("\n");
    expect(output).toContain("Refusing unscoped read");
  });
});
