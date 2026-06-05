import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it, vi } from "vitest";
import { extractCsvCandidates } from "../src/extractors/csv.js";
import {
  createLlmExtractor,
  ExtractionError,
  type LlmProvider
} from "../src/extractors/llm.js";
import { createMockExtractor } from "../src/extractors/mock.js";
import type { ExtractionInput } from "../src/extractors/types.js";
import type { ExtractedCandidate } from "../src/types.js";

const fixtureRoot = join(process.cwd(), "tests", "fixtures", "seed-data");

describe("CSV extractor", () => {
  it("extracts generic candidate memory from non-Jane outreach tracker rows", () => {
    const csv = readFileSync(join(fixtureRoot, "outreach-tracker.csv"), "utf8");

    const candidates = extractCsvCandidates({
      sourceId: "source-outreach",
      sourcePath: "tests/fixtures/seed-data/outreach-tracker.csv",
      sourceType: "csv",
      content: csv
    });

    expect(candidates).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          kind: "entity",
          subject: "Miguel Alvarez",
          entityType: "person",
          evidenceText: expect.stringContaining("Miguel Alvarez")
        }),
        expect.objectContaining({
          kind: "entity",
          subject: "Civic Data Lab",
          entityType: "organization"
        }),
        expect.objectContaining({
          kind: "entity",
          subject: "AI safety",
          entityType: "domain"
        }),
        expect.objectContaining({
          kind: "semantic_fact",
          subject: "Miguel Alvarez",
          predicate: "domain",
          object: "AI safety"
        }),
        expect.objectContaining({
          kind: "relationship",
          subject: "Miguel Alvarez",
          relationshipType: "works_at",
          object: "Civic Data Lab"
        }),
        expect.objectContaining({
          kind: "relationship",
          subject: "Miguel Alvarez",
          relationshipType: "needs_follow_up",
          object: "Need intro to student organizer"
        }),
        expect.objectContaining({
          kind: "semantic_fact",
          subject: "Miguel Alvarez",
          predicate: "needs_follow_up",
          object: "Need intro to student organizer"
        }),
        expect.objectContaining({
          kind: "semantic_fact",
          subject: "Priya Shah",
          predicate: "outcome",
          object: "declined"
        })
      ])
    );
    expect(candidates.every((candidate) => candidate.confidence > 0)).toBe(true);
    expect(candidates.some((candidate) => candidate.evidenceText.includes("Jane"))).toBe(false);
    expect(candidates.some((candidate) => candidate.evidenceText.includes("Anthropic"))).toBe(false);
  });
});

describe("mock extractor", () => {
  it("returns configured candidates through the extractor contract", async () => {
    const configured: ExtractedCandidate[] = [
      {
        kind: "entity",
        subject: "Ada Chen",
        entityType: "person",
        confidence: 0.91,
        evidenceText: "Ada Chen is organizing a robotics salon."
      }
    ];
    const extractor = createMockExtractor(configured);

    await expect(
      extractor.extract({
        sourceId: "source-md",
        sourcePath: "spring-2026-sourcing.md",
        sourceType: "markdown",
        content: "Ada Chen is organizing a robotics salon."
      })
    ).resolves.toEqual(configured);
    expect(extractor.type).toBe("mock");
    expect(extractor.version).toBeTruthy();
  });
});

describe("LLM extractor", () => {
  const input: ExtractionInput = {
    sourceId: "source-thread",
    sourcePath: "ai-safety-thread.eml",
    sourceType: "email",
    content: "From: Alex\n\nMorgan needs follow-up about AI safety reading group."
  };

  it("validates provider JSON before returning candidate memory", async () => {
    const provider = vi.fn<LlmProvider>().mockResolvedValue(
      JSON.stringify({
        candidates: [
          {
            kind: "entity",
            subject: "Morgan Lee",
            entityType: "person",
            confidence: 0.88,
            evidenceText: "Morgan needs follow-up about AI safety reading group."
          },
          {
            kind: "relationship",
            subject: "Morgan Lee",
            relationshipType: "needs_follow_up",
            object: "AI safety reading group",
            confidence: 0.82,
            evidenceText: "Morgan needs follow-up about AI safety reading group."
          }
        ]
      })
    );
    const extractor = createLlmExtractor({
      apiKey: "test-key",
      model: "test-model",
      provider
    });

    const candidates = await extractor.extract(input);

    expect(candidates).toHaveLength(2);
    expect(candidates[0]).toMatchObject({ kind: "entity", entityType: "person" });
    expect(provider).toHaveBeenCalledWith(
      expect.objectContaining({
        model: "test-model",
        sourceType: "email",
        content: input.content
      })
    );
  });

  it("turns malformed provider JSON into a typed extraction error", async () => {
    const extractor = createLlmExtractor({
      apiKey: "test-key",
      model: "test-model",
      provider: async () => "not json"
    });

    await expect(extractor.extract(input)).rejects.toMatchObject({
      name: "ExtractionError",
      code: "malformed_json"
    });
  });

  it("rejects invalid candidate shapes from provider JSON", async () => {
    const extractor = createLlmExtractor({
      apiKey: "test-key",
      model: "test-model",
      provider: async () =>
        JSON.stringify({
          candidates: [
            {
              kind: "entity",
              subject: "Morgan Lee",
              entityType: "unknown",
              confidence: 0.88,
              evidenceText: "Morgan needs follow-up about AI safety reading group."
            }
          ]
        })
    });

    await expect(extractor.extract(input)).rejects.toMatchObject({
      name: "ExtractionError",
      code: "invalid_output"
    });
  });

  it("requires model config when creating an unstructured LLM extractor", () => {
    expect(() => createLlmExtractor({ apiKey: "test-key", model: "" })).toThrow(ExtractionError);
    expect(() => createLlmExtractor({ apiKey: "", model: "test-model" })).toThrow(
      /OPENAI_API_KEY/
    );
  });
});
