import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { loadProcedures } from "../src/procedures.js";

const tempDirs: string[] = [];

function tempProcedureDir(): string {
  const dir = mkdtempSync(join(tmpdir(), "sourcyavo-procedure-test-"));
  tempDirs.push(dir);
  return dir;
}

afterEach(() => {
  for (const dir of tempDirs.splice(0)) {
    rmSync(dir, { recursive: true, force: true });
  }
});

describe("procedure memory", () => {
  it("loads markdown procedure files in stable filename order", () => {
    const dir = tempProcedureDir();
    writeFileSync(join(dir, "z-last.md"), "# Last\n\nUse last.");
    writeFileSync(join(dir, "a-first.md"), "# First\n\nUse first.");
    writeFileSync(join(dir, "notes.txt"), "not procedure memory");

    const procedures = loadProcedures(dir);

    expect(procedures.map((procedure) => procedure.name)).toEqual(["a-first.md", "z-last.md"]);
    expect(procedures[0]?.content).toContain("Use first.");
  });

  it("returns an empty list when the procedure directory does not exist", () => {
    expect(loadProcedures(join(tempProcedureDir(), "missing"))).toEqual([]);
  });
});
