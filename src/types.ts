export const SOURCE_TYPES = ["markdown", "text", "csv", "email"] as const;
export type SourceType = (typeof SOURCE_TYPES)[number];

export const ENTITY_TYPES = [
  "person",
  "organization",
  "project",
  "event",
  "semester",
  "domain"
] as const;
export type EntityType = (typeof ENTITY_TYPES)[number];

export const RELATIONSHIP_TYPES = [
  "works_at",
  "contacted",
  "responded",
  "worked_with",
  "needs_follow_up",
  "associated_with",
  "relevant_to_domain"
] as const;
export type RelationshipType = (typeof RELATIONSHIP_TYPES)[number];

export const SEMANTIC_FACT_STATUSES = [
  "candidate",
  "accepted",
  "conflicted",
  "stale"
] as const;
export type SemanticFactStatus = (typeof SEMANTIC_FACT_STATUSES)[number];

export type ExtractedCandidateKind = "entity" | "relationship" | "semantic_fact";

export interface ExtractedCandidate {
  kind: ExtractedCandidateKind;
  subject?: string;
  predicate?: string;
  object?: string;
  entityType?: EntityType;
  relationshipType?: RelationshipType;
  confidence: number;
  evidenceText: string;
}

export interface ProcedureDoc {
  name: string;
  path: string;
  content: string;
}
