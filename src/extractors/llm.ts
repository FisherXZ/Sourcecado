import { sha256 } from "../chunk";
import { getDb } from "../lib/db";
import { callModel } from "../lib/model-gateway";
import {
  ENTITY_TYPES,
  RELATIONSHIP_TYPES,
  type EntityType,
  type ExtractedCandidate,
  type ExtractedCandidateKind,
  type RelationshipType,
  type SourceType
} from "../types";
import {
  ExtractionError,
  type Extractor
} from "./types";
import { z } from "zod";

export { ExtractionError } from "./types";

export const LLM_EXTRACTOR_VERSION = "1";
export const LLM_SCHEMA_VERSION = "1";

export interface LlmProviderRequest {
  model: string;
  sourceType: SourceType;
  sourcePath: string;
  content: string;
  schemaVersion: string;
  systemPrompt: string;
}

export type LlmProvider = (request: LlmProviderRequest) => Promise<string>;

export interface LlmExtractorConfig {
  apiKey?: string;
  model?: string;
  provider?: LlmProvider;
}

export function createLlmExtractor(config: LlmExtractorConfig = {}): Extractor {
  const generationProvider = process.env.SOURCECADO_GENERATION_PROVIDER?.trim() || "deepseek";
  const defaultModel = generationProvider === "anthropic" ? "claude-sonnet-4-6" : "deepseek-chat";
  const model = (config.model ?? process.env.SOURCECADO_GENERATION_MODEL?.trim()) || defaultModel;

  // If an explicit apiKey is supplied via config, propagate it to the environment
  // so the Model Gateway can pick it up at call time via requireEnv.
  if (!config.provider && config.apiKey?.trim() && !process.env.DEEPSEEK_API_KEY?.trim()) {
    process.env.DEEPSEEK_API_KEY = config.apiKey;
  }

  const provider = config.provider ?? createModelGatewayProvider();

  return {
    type: "llm",
    version: LLM_EXTRACTOR_VERSION,
    promptHash: sha256(buildSystemPrompt()),
    schemaVersion: LLM_SCHEMA_VERSION,
    modelName: model,
    async extract(input) {
      let rawOutput: string;
      try {
        rawOutput = await provider({
          model,
          sourceType: input.sourceType,
          sourcePath: input.sourcePath,
          content: input.content,
          schemaVersion: LLM_SCHEMA_VERSION,
          systemPrompt: buildSystemPrompt()
        });
      } catch (error) {
        if (error instanceof ExtractionError) {
          throw error;
        }
        throw new ExtractionError("provider_error", "LLM extraction provider failed.", {
          cause: error
        });
      }

      return parseLlmCandidates(rawOutput);
    }
  };
}

export function parseLlmCandidates(rawOutput: string): ExtractedCandidate[] {
  let parsed: unknown;
  try {
    parsed = JSON.parse(rawOutput);
  } catch (error) {
    throw new ExtractionError("malformed_json", "LLM extractor returned malformed JSON.", {
      cause: error
    });
  }

  if (!isObject(parsed) || !Array.isArray(parsed.candidates)) {
    throw new ExtractionError(
      "invalid_output",
      "LLM extractor JSON must contain a candidates array."
    );
  }

  return parsed.candidates.map((candidate, index) => validateCandidate(candidate, index));
}

function validateCandidate(candidate: unknown, index: number): ExtractedCandidate {
  if (!isObject(candidate)) {
    throw invalidCandidate(index, "candidate must be an object");
  }

  const kind = candidate.kind;
  if (!isCandidateKind(kind)) {
    throw invalidCandidate(index, "kind must be entity, relationship, or semantic_fact");
  }

  const confidence = candidate.confidence;
  if (typeof confidence !== "number" || confidence < 0 || confidence > 1) {
    throw invalidCandidate(index, "confidence must be a number between 0 and 1");
  }

  const evidenceText = candidate.evidenceText;
  if (typeof evidenceText !== "string" || !evidenceText.trim()) {
    throw invalidCandidate(index, "evidenceText is required");
  }

  const normalized: ExtractedCandidate = {
    kind,
    confidence,
    evidenceText
  };

  if (candidate.subject !== undefined && candidate.subject !== null) {
    if (typeof candidate.subject !== "string" || !candidate.subject.trim()) {
      throw invalidCandidate(index, "subject must be a non-empty string when present");
    }
    normalized.subject = candidate.subject;
  }
  if (candidate.predicate !== undefined && candidate.predicate !== null) {
    if (typeof candidate.predicate !== "string" || !candidate.predicate.trim()) {
      throw invalidCandidate(index, "predicate must be a non-empty string when present");
    }
    normalized.predicate = candidate.predicate;
  }
  if (candidate.object !== undefined && candidate.object !== null) {
    if (typeof candidate.object !== "string" || !candidate.object.trim()) {
      throw invalidCandidate(index, "object must be a non-empty string when present");
    }
    normalized.object = candidate.object;
  }
  if (candidate.entityType !== undefined && candidate.entityType !== null) {
    if (!isEntityType(candidate.entityType)) {
      throw invalidCandidate(index, "entityType is not in the supported entity taxonomy");
    }
    normalized.entityType = candidate.entityType;
  }
  if (candidate.relationshipType !== undefined && candidate.relationshipType !== null) {
    if (!isRelationshipType(candidate.relationshipType)) {
      throw invalidCandidate(
        index,
        "relationshipType is not in the supported relationship taxonomy"
      );
    }
    normalized.relationshipType = candidate.relationshipType;
  }

  if (kind === "entity" && (!normalized.subject || !normalized.entityType)) {
    throw invalidCandidate(index, "entity candidates require subject and entityType");
  }
  if (
    kind === "relationship" &&
    (!normalized.subject || !normalized.object || !normalized.relationshipType)
  ) {
    throw invalidCandidate(
      index,
      "relationship candidates require subject, object, and relationshipType"
    );
  }
  if (kind === "semantic_fact" && (!normalized.subject || !normalized.predicate || !normalized.object)) {
    throw invalidCandidate(
      index,
      "semantic_fact candidates require subject, predicate, and object"
    );
  }

  return normalized;
}

function buildSystemPrompt(): string {
  return [
    "Extract SourcyAvo memory candidates from the provided markdown, text, or email source.",
    "Return strict JSON with a candidates array.",
    "Each candidate must match the shared ExtractedCandidate shape and include evidenceText."
  ].join(" ");
}

function createModelGatewayProvider(): LlmProvider {
  return async (request) => {
    const result = await callModel(getDb(), {
      kind: "generate_object",
      taskName: "extract_memory_candidates",
      promptVersion: request.schemaVersion,
      prompt: [
        `Source type: ${request.sourceType}`,
        `Source path: ${request.sourcePath}`,
        "",
        request.content
      ].join("\n"),
      system: request.systemPrompt,
      schema: candidateResponseSchema(),
      schemaName: "sourcyavo_memory_candidates",
      // providerName omitted: gateway resolves via SOURCECADO_GENERATION_PROVIDER env
      // (defaults to "deepseek" unless SOURCECADO_GENERATION_PROVIDER overrides it)
      model: request.model
    });
    return JSON.stringify(result.object);
  };
}

function candidateResponseSchema() {
  return z.object({
    candidates: z.array(
      z.object({
        kind: z.enum(["entity", "relationship", "semantic_fact"]),
        subject: z.string().optional(),
        predicate: z.string().optional(),
        object: z.string().optional(),
        entityType: z.enum(ENTITY_TYPES).optional(),
        relationshipType: z.enum(RELATIONSHIP_TYPES).optional(),
        confidence: z.number(),
        evidenceText: z.string(),
      })
    ),
  });
}

function invalidCandidate(index: number, detail: string): ExtractionError {
  return new ExtractionError("invalid_output", `Invalid LLM candidate at index ${index}: ${detail}.`);
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isCandidateKind(value: unknown): value is ExtractedCandidateKind {
  return value === "entity" || value === "relationship" || value === "semantic_fact";
}

function isEntityType(value: unknown): value is EntityType {
  return typeof value === "string" && ENTITY_TYPES.includes(value as EntityType);
}

function isRelationshipType(value: unknown): value is RelationshipType {
  return typeof value === "string" && RELATIONSHIP_TYPES.includes(value as RelationshipType);
}
