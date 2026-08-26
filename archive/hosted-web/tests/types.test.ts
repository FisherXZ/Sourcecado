import {
  ENTITY_TYPES,
  RELATIONSHIP_TYPES,
  SEMANTIC_FACT_STATUSES,
  SOURCE_TYPES,
  type ExtractedCandidate
} from "../src/types.js";

describe("memory type contracts", () => {
  it("defines the MVP source and entity taxonomies", () => {
    expect(SOURCE_TYPES).toEqual(["markdown", "text", "csv", "email"]);
    expect(ENTITY_TYPES).toEqual([
      "person",
      "organization",
      "project",
      "event",
      "semester",
      "domain"
    ]);
    expect(SEMANTIC_FACT_STATUSES).toEqual([
      "candidate",
      "accepted",
      "conflicted",
      "stale"
    ]);
    expect(RELATIONSHIP_TYPES).toEqual([
      "works_at",
      "contacted",
      "responded",
      "worked_with",
      "needs_follow_up",
      "associated_with",
      "relevant_to_domain"
    ]);
  });

  it("allows extracted candidates to share one structured shape", () => {
    const candidate: ExtractedCandidate = {
      kind: "semantic_fact",
      subject: "Jane Doe",
      predicate: "needs_follow_up",
      object: "AI safety event",
      relationshipType: "needs_follow_up",
      confidence: 0.82,
      evidenceText: "Jane asked for details after finals."
    };

    expect(candidate.kind).toBe("semantic_fact");
    expect(candidate.confidence).toBeGreaterThan(0);
  });
});
