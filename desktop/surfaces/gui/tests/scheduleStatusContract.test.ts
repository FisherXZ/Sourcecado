import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

/**
 * The schedule run statuses are declared twice, in two languages: the backend
 * owns the vocabulary, the client validates against its own copy and refuses
 * anything it does not recognise.
 *
 * That is a silent failure by construction. When the backend gained
 * "interrupted", both suites stayed green -- the client simply discarded the
 * status, because no test on either side crossed the boundary. This test
 * crosses it.
 *
 * It compares source text, so it cannot see runtime behaviour; what it can see
 * is the two lists drifting apart again, which is the failure that actually
 * happened.
 */
// Paths are relative to the vitest root (the gui package), matching the
// convention in tests/cssBundle.ts.
const SCHEDULER_PY = "../../coworker/automation/scheduler.py";
const API_TS = "src/api.ts";

function backendStatuses(): string[] {
  const src = readFileSync(SCHEDULER_PY, "utf8");
  const block = /SCHEDULE_RUN_STATUSES\s*=\s*frozenset\(\s*\{([^}]*)\}/m.exec(src);
  if (!block) throw new Error("SCHEDULE_RUN_STATUSES not found in scheduler.py");
  return [...block[1].matchAll(/"([a-z_]+)"/g)].map((m) => m[1]).sort();
}

function clientStatuses(): string[] {
  const src = readFileSync(API_TS, "utf8");
  const block = /const SCHEDULE_RUN_STATUSES = new Set<ScheduleRunStatus>\(\[([^\]]*)\]/m.exec(src);
  if (!block) throw new Error("SCHEDULE_RUN_STATUSES not found in api.ts");
  return [...block[1].matchAll(/"([a-z_]+)"/g)].map((m) => m[1]).sort();
}

describe("schedule run status vocabulary", () => {
  it("is identical on both sides of the backend boundary", () => {
    expect(clientStatuses()).toEqual(backendStatuses());
  });

  it("includes interrupted, which a restart-cut routine reports", () => {
    expect(backendStatuses()).toContain("interrupted");
    expect(clientStatuses()).toContain("interrupted");
  });
});
