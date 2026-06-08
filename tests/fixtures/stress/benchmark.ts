import type { ActorType } from "../../../src/read-service.js";

// Stable source_id slugs produced by the path-slug rule for the committed
// stress corpus under tests/fixtures/stress/corpus/. Centralized here so the
// harness, the permission seeding, and the benchmark expectations all agree on
// identity. NEVER a SQLite autoincrement id.
export const STRESS_SOURCE_IDS = {
  ai: "spring-2026/ai/outreach-ai-csv",
  robotics: "spring-2026/robotics/outreach-robotics-csv",
  biotech: "spring-2026/biotech/apollo-biotech-csv",
  restrictedBiotech: "spring-2026/biotech/restricted-biotech-csv",
  broken: "spring-2026/ai/broken-csv"
} as const;

// The non-restricted corpus a standard sourcing client is allowed to read. The
// restricted biotech source is intentionally excluded; scope is enforced in SQL
// before retrieval (ADR 0001), never by post-filtering the answer text.
export const STRESS_ALLOWED_SOURCE_IDS: readonly string[] = [
  STRESS_SOURCE_IDS.ai,
  STRESS_SOURCE_IDS.robotics,
  STRESS_SOURCE_IDS.biotech
];

// A subject that lives only in the restricted biotech source. It must never
// appear in any answer produced for a client without restricted access, and
// never in the rendered StressReport text.
export const RESTRICTED_SUBJECTS: readonly string[] = ["Odalys Brennan", "Lior Kessler"];

export const STRESS_CLIENT: { actorType: ActorType; actorId: string } = {
  actorType: "test_client",
  actorId: "stress-bench-client"
};

export interface BenchmarkCase {
  id: string;
  question: string;
  // Principal asking the question. Defaults to STRESS_CLIENT when omitted by
  // the harness; carried explicitly so future cases can model other clients.
  client: { actorType: ActorType; actorId: string };
  expectations: {
    // Subjects (people/orgs) that MUST be named somewhere in the Answer text.
    mustMentionSubjects: string[];
    // Citation fragments that MUST appear in the Evidence section.
    mustCiteSources: string[];
    // Subjects that MUST NOT appear anywhere in the rendered answer.
    forbiddenSubjects: string[];
    // Whether the case expects any substantive answer at all.
    expectSomeAnswer: boolean;
  };
}

// Drawn from the spec 'Benchmark Questions'. CSV-only and deterministic so the
// harness needs no API key.
export const BENCHMARK_CASES: readonly BenchmarkCase[] = [
  {
    id: "ai-responded",
    question: "Who responded for AI?",
    client: STRESS_CLIENT,
    expectations: {
      mustMentionSubjects: ["Nadia Okonkwo"],
      mustCiteSources: ["outreach-ai.csv"],
      forbiddenSubjects: [...RESTRICTED_SUBJECTS],
      expectSomeAnswer: true
    }
  },
  {
    id: "robotics-needs-follow-up",
    question: "Who needs follow-up for robotics?",
    client: STRESS_CLIENT,
    expectations: {
      mustMentionSubjects: ["Bao Tran"],
      mustCiteSources: ["outreach-robotics.csv"],
      forbiddenSubjects: [...RESTRICTED_SUBJECTS],
      expectSomeAnswer: true
    }
  },
  {
    id: "biotech-no-response",
    question: "Who did not respond for biotech?",
    client: STRESS_CLIENT,
    expectations: {
      mustMentionSubjects: ["Felix Nakamura"],
      mustCiteSources: ["apollo-biotech.csv"],
      forbiddenSubjects: [...RESTRICTED_SUBJECTS],
      expectSomeAnswer: true
    }
  },
  {
    id: "biotech-worked-with",
    question: "Which companies or people worked with Codeology before in biotech?",
    client: STRESS_CLIENT,
    expectations: {
      mustMentionSubjects: ["Soren Halvorsen"],
      mustCiteSources: ["apollo-biotech.csv"],
      forbiddenSubjects: [...RESTRICTED_SUBJECTS],
      expectSomeAnswer: true
    }
  },
  {
    id: "restricted-leak-guard",
    question: "Who needs follow-up for confidential biotech?",
    client: STRESS_CLIENT,
    expectations: {
      // The restricted source is out of scope; we expect NO restricted subject
      // and certainly no restricted citation. expectSomeAnswer is false because
      // a correctly scoped client has nothing to surface here.
      mustMentionSubjects: [],
      mustCiteSources: [],
      forbiddenSubjects: [...RESTRICTED_SUBJECTS],
      expectSomeAnswer: false
    }
  }
];
