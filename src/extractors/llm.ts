import { sha256 } from "../chunk.js";
import {
  ENTITY_TYPES,
  RELATIONSHIP_TYPES,
  type EntityType,
  type ExtractedCandidate,
  type ExtractedCandidateKind,
  type RelationshipType,
  type SourceType
} from "../types.js";
import {
  ExtractionError,
  type Extractor
} from "./types.js";

export { ExtractionError } from "./types.js";

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
  const apiKey = config.apiKey ?? process.env.OPENAI_API_KEY ?? "";
  const model = config.model ?? process.env.SOURCYAVO_LLM_MODEL ?? "";

  if (!apiKey.trim()) {
    throw new ExtractionError(
      "missing_config",
      "OPENAI_API_KEY is required for unstructured LLM extraction."
    );
  }
  if (!model.trim()) {
    throw new ExtractionError(
      "missing_config",
      "SOURCYAVO_LLM_MODEL is required for unstructured LLM extraction."
    );
  }

  const provider = config.provider ?? createOpenAiResponsesProvider(apiKey);

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

function createOpenAiResponsesProvider(apiKey: string): LlmProvider {
  return async (request) => {
    const response = await fetch("https://api.openai.com/v1/responses", {
      method: "POST",
      headers: {
        authorization: `Bearer ${apiKey}`,
        "content-type": "application/json"
      },
      body: JSON.stringify({
        model: request.model,
        instructions: request.systemPrompt,
        input: [
          {
            role: "user",
            content: [
              {
                type: "input_text",
                text: [
                  `Source type: ${request.sourceType}`,
                  `Source path: ${request.sourcePath}`,
                  "",
                  request.content
                ].join("\n")
              }
            ]
          }
        ],
        text: {
          format: {
            type: "json_schema",
            name: "sourcyavo_memory_candidates",
            strict: true,
            schema: candidateResponseSchema()
          }
        }
      })
    });

    if (!response.ok) {
      throw new ExtractionError(
        "provider_error",
        `OpenAI Responses API request failed with ${response.status}: ${await response.text()}`
      );
    }

    return extractResponseText(await response.json());
  };
}

function candidateResponseSchema(): Record<string, unknown> {
  return {
    type: "object",
    additionalProperties: false,
    required: ["candidates"],
    properties: {
      candidates: {
        type: "array",
        items: {
          type: "object",
          additionalProperties: false,
          required: [
            "kind",
            "subject",
            "predicate",
            "object",
            "entityType",
            "relationshipType",
            "confidence",
            "evidenceText"
          ],
          properties: {
            kind: { type: "string", enum: ["entity", "relationship", "semantic_fact"] },
            subject: { type: ["string", "null"] },
            predicate: { type: ["string", "null"] },
            object: { type: ["string", "null"] },
            entityType: { type: ["string", "null"], enum: [...ENTITY_TYPES, null] },
            relationshipType: { type: ["string", "null"], enum: [...RELATIONSHIP_TYPES, null] },
            confidence: { type: "number", minimum: 0, maximum: 1 },
            evidenceText: { type: "string" }
          }
        }
      }
    }
  };
}

function extractResponseText(response: unknown): string {
  if (isObject(response) && typeof response.output_text === "string") {
    return response.output_text;
  }

  if (isObject(response) && Array.isArray(response.output)) {
    for (const outputItem of response.output) {
      if (!isObject(outputItem) || !Array.isArray(outputItem.content)) {
        continue;
      }
      for (const contentItem of outputItem.content) {
        if (isObject(contentItem) && typeof contentItem.text === "string") {
          return contentItem.text;
        }
      }
    }
  }

  throw new ExtractionError(
    "provider_error",
    "OpenAI Responses API response did not include output text."
  );
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
