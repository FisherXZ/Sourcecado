import { describe, expect, it } from "vitest";
import { createMockExtractor } from "../../src/extractors/mock.js";
import {
  runStressHarness,
  type MissCategory,
  type StressReport
} from "./harness.js";
import {
  BENCHMARK_CASES,
  RESTRICTED_SUBJECTS,
  STRESS_ALLOWED_SOURCE_IDS,
  STRESS_CLIENT,
  STRESS_SOURCE_IDS,
  type BenchmarkCase
} from "../fixtures/stress/benchmark.js";

const STRESS_CLIENT_REF = STRESS_CLIENT;

function reportText(report: StressReport): string {
  return JSON.stringify(report);
}

describe("runStressHarness", () => {
  it("produces a deterministic tally and case set across repeated runs", async () => {
    const first = await runStressHarness();
    const second = await runStressHarness();

    // Snapshot paths are temp-unique; everything else must be deep-equal.
    expect(second.tally).toEqual(first.tally);
    expect(stripSnapshot(second.cases)).toEqual(stripSnapshot(first.cases));
    expect(second.passCount).toEqual(first.passCount);
    expect(second.missCount).toEqual(first.missCount);
  });

  it("always declares all six categories with temporal deferred at zero", async () => {
    const report = await runStressHarness();

    const keys = Object.keys(report.tally).sort();
    expect(keys).toEqual(
      (["citation", "extraction", "missing-source", "permission", "retrieval", "temporal"] as MissCategory[]).sort()
    );
    expect(report.tally.temporal).toBe(0);
    expect(report.permissionState).toBe("reachable");
  });

  it("surfaces the broken file in skippedFiles while its neighbors survive", async () => {
    const report = await runStressHarness();

    expect(report.ingest.skipped).toBeGreaterThanOrEqual(1);
    expect(report.skippedFiles.some((file) => file.path.includes("broken.csv"))).toBe(true);

    // The good AI/robotics/biotech sources still got processed and answered.
    expect(report.ingest.processed).toBeGreaterThanOrEqual(3);
    const aiResponded = report.cases.find((c) => c.id === "ai-responded");
    expect(aiResponded?.outcome).toBe("pass");
  });

  it("includes at least one passing case in the default benchmark", async () => {
    const report = await runStressHarness();
    expect(report.passCount).toBeGreaterThanOrEqual(1);
  });

  it("never leaks a restricted subject into the rendered report (no-leak invariant)", async () => {
    const report = await runStressHarness();
    const text = reportText(report);
    for (const subject of RESTRICTED_SUBJECTS) {
      expect(text).not.toContain(subject);
    }
    // The restricted leak-guard case must pass: a correctly scoped client gets
    // no restricted content.
    const guard = report.cases.find((c) => c.id === "restricted-leak-guard");
    expect(guard?.outcome).toBe("pass");
  });

  it("classifies a missing expected source as missing-source", async () => {
    const cases: BenchmarkCase[] = [
      {
        id: "seed-missing-source",
        question: "Who responded for AI?",
        client: STRESS_CLIENT_REF,
        expectations: {
          mustMentionSubjects: ["Nadia Okonkwo"],
          mustCiteSources: ["does-not-exist.csv"],
          forbiddenSubjects: [...RESTRICTED_SUBJECTS],
          expectSomeAnswer: true
        }
      }
    ];
    const report = await runStressHarness({ cases });
    expectCategory(report, "missing-source");
  });

  it("classifies present-chunk-but-no-facts as extraction", async () => {
    // Inject a CSV extractor that yields nothing: chunks are ingested but no
    // semantic_facts are produced. Deterministic, no LLM.
    const emptyExtractor = createMockExtractor([]);
    const cases: BenchmarkCase[] = [
      {
        id: "seed-extraction",
        question: "Who responded for AI?",
        client: STRESS_CLIENT_REF,
        expectations: {
          mustMentionSubjects: ["Nadia Okonkwo"],
          mustCiteSources: ["outreach-ai.csv"],
          forbiddenSubjects: [...RESTRICTED_SUBJECTS],
          expectSomeAnswer: true
        }
      }
    ];
    const report = await runStressHarness({
      cases,
      refreshOptions: { extractorsBySourceType: { csv: emptyExtractor } }
    });
    expectCategory(report, "extraction");
  });

  it("classifies an accepted-but-unsurfaced fact as retrieval", async () => {
    // Nadia responded; asking the no-response question means the strict
    // intent filters her out of the answer even though her accepted facts are
    // in scope. That gap is a retrieval miss, not extraction or citation.
    const cases: BenchmarkCase[] = [
      {
        id: "seed-retrieval",
        question: "Who did not respond for AI?",
        client: STRESS_CLIENT_REF,
        expectations: {
          mustMentionSubjects: ["Nadia Okonkwo"],
          mustCiteSources: ["outreach-ai.csv"],
          forbiddenSubjects: [...RESTRICTED_SUBJECTS],
          expectSomeAnswer: true
        }
      }
    ];
    const report = await runStressHarness({ cases });
    expectCategory(report, "retrieval");
  });

  it("classifies a surfaced subject with a missing citation as citation", async () => {
    // Scope the client to the AI source only. Nadia is surfaced with her real
    // AI citation, but we demand a citation from the robotics source. Robotics
    // genuinely exists in source_records (so not missing-source) yet is out of
    // this client's scope and therefore never appears in the evidence -> a
    // citation miss.
    const cases: BenchmarkCase[] = [
      {
        id: "seed-citation",
        question: "Who responded for AI?",
        client: STRESS_CLIENT_REF,
        expectations: {
          mustMentionSubjects: ["Nadia Okonkwo"],
          mustCiteSources: ["outreach-ai.csv", "outreach-robotics.csv"],
          forbiddenSubjects: [...RESTRICTED_SUBJECTS],
          expectSomeAnswer: true
        }
      }
    ];
    const report = await runStressHarness({
      cases,
      allowedSourceIds: [STRESS_SOURCE_IDS.ai]
    });
    expectCategory(report, "citation");
  });

  it("classifies a leaked forbidden subject as permission when S2 is present", async () => {
    // Grant the restricted source to the client, then ask the restricted
    // question while forbidding the restricted subject. The leak proves scope
    // was not enforced -> permission category.
    const cases: BenchmarkCase[] = [
      {
        id: "seed-permission",
        question: "Who needs follow-up for confidential biotech?",
        client: STRESS_CLIENT_REF,
        expectations: {
          mustMentionSubjects: [],
          mustCiteSources: [],
          forbiddenSubjects: [...RESTRICTED_SUBJECTS],
          expectSomeAnswer: false
        }
      }
    ];
    const report = await runStressHarness({
      cases,
      allowedSourceIds: [...STRESS_ALLOWED_SOURCE_IDS, STRESS_SOURCE_IDS.restrictedBiotech]
    });
    expectCategory(report, "permission");
  });

  it("degrades permission to an unreachable-pending bucket when S2 is unavailable", async () => {
    const report = await runStressHarness({ simulateMissingPermissions: true });
    expect(report.permissionState).toBe("unreachable-pending");
    // With no permissions written, scoped clients see nothing; the leak-guard
    // case still cannot leak because there is no access at all.
    const text = reportText(report);
    for (const subject of RESTRICTED_SUBJECTS) {
      expect(text).not.toContain(subject);
    }
  });
});

function expectCategory(report: StressReport, category: MissCategory): void {
  expect(report.tally[category]).toBeGreaterThanOrEqual(1);
  expect(report.cases.some((c) => c.category === category)).toBe(true);
}

function stripSnapshot(cases: StressReport["cases"]): StressReport["cases"] {
  return cases.map((c) => ({ ...c }));
}

// Reference the imported default cases so the import is exercised and the
// benchmark set stays wired to the harness default.
void BENCHMARK_CASES;
