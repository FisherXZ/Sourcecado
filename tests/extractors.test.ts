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

  it("extracts Apollo contact export rows into people, organizations, and contact facts", () => {
    const csv = [
      "First Name,Last Name,Title,Company Name,Email,Email Status,Departments",
      "Ada,Lovelace,Engineering Manager,OpenAI,ada@example.com,Verified,Engineering"
    ].join("\n");

    const candidates = extractCsvCandidates({
      sourceId: "source-apollo",
      sourcePath: "OpenAI.csv",
      sourceType: "csv",
      content: csv
    });

    expect(candidates).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          kind: "entity",
          subject: "Ada Lovelace",
          entityType: "person"
        }),
        expect.objectContaining({
          kind: "entity",
          subject: "OpenAI",
          entityType: "organization"
        }),
        expect.objectContaining({
          kind: "relationship",
          subject: "Ada Lovelace",
          relationshipType: "works_at",
          object: "OpenAI"
        }),
        expect.objectContaining({
          kind: "semantic_fact",
          subject: "Ada Lovelace",
          predicate: "email",
          object: "ada@example.com"
        }),
        expect.objectContaining({
          kind: "semantic_fact",
          subject: "Ada Lovelace",
          predicate: "title",
          object: "Engineering Manager"
        }),
        expect.objectContaining({
          kind: "semantic_fact",
          subject: "Ada Lovelace",
          predicate: "email_status",
          object: "Verified"
        })
      ])
    );
  });

  it("extracts sourcing spreadsheet status, interest, notes, and follow-up state", () => {
    const csv = [
      "Cody POCs,Company,POC First Name,POC Last Name,POC Title,POC Email,Status,Interest,Notes",
      "Victoria,Perplexity,Grace,Hopper,Partnerships,grace@example.com,Responded,High,Needs follow-up after build night"
    ].join("\n");

    const candidates = extractCsvCandidates({
      sourceId: "source-sourcing-sheet",
      sourcePath: "Fall 2025 Sourcing Spreadsheet - Leads.csv",
      sourceType: "csv",
      content: csv
    });

    expect(candidates).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          kind: "entity",
          subject: "Grace Hopper",
          entityType: "person"
        }),
        expect.objectContaining({
          kind: "semantic_fact",
          subject: "Grace Hopper",
          predicate: "status",
          object: "Responded"
        }),
        expect.objectContaining({
          kind: "semantic_fact",
          subject: "Grace Hopper",
          predicate: "interest",
          object: "High"
        }),
        expect.objectContaining({
          kind: "relationship",
          subject: "Grace Hopper",
          relationshipType: "responded",
          object: "Perplexity"
        }),
        expect.objectContaining({
          kind: "relationship",
          subject: "Grace Hopper",
          relationshipType: "needs_follow_up",
          object: "Needs follow-up after build night"
        })
      ])
    );
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

  it("can be constructed without a key; provider error surfaces at call time", async () => {
    // Construction no longer throws — the gateway's requireEnv validates the
    // active provider's key at call time, not at construction.
    const extractor = createLlmExtractor({ model: "test-model" });
    expect(extractor).toBeDefined();

    // A provider that rejects simulates what happens when requireEnv fires at runtime.
    const failingProvider: LlmProvider = async () => {
      throw new Error("DEEPSEEK_API_KEY is required for Model Gateway provider calls.");
    };
    const extractorWithBadProvider = createLlmExtractor({
      model: "test-model",
      provider: failingProvider,
    });
    await expect(extractorWithBadProvider.extract(input)).rejects.toMatchObject({
      name: "ExtractionError",
      code: "provider_error",
    });
  });

  it("does not use the legacy OpenAI Responses API provider", () => {
    const source = readFileSync(join(process.cwd(), "src", "extractors", "llm.ts"), "utf8");
    expect(source).not.toContain("https://api.openai.com/v1/responses");
    expect(source).toContain("callModel");
  });

  describe("provider-aware model default", () => {
    function withEnv(overrides: Record<string, string | undefined>, fn: () => void) {
      const saved: Record<string, string | undefined> = {};
      for (const key of Object.keys(overrides)) {
        saved[key] = process.env[key];
        if (overrides[key] === undefined) {
          delete process.env[key];
        } else {
          process.env[key] = overrides[key];
        }
      }
      try {
        fn();
      } finally {
        for (const key of Object.keys(saved)) {
          if (saved[key] === undefined) delete process.env[key];
          else process.env[key] = saved[key];
        }
      }
    }

    it("uses claude-sonnet-4-6 when provider is anthropic and no model env is set", () => {
      withEnv(
        { SOURCECADO_GENERATION_PROVIDER: "anthropic", SOURCECADO_GENERATION_MODEL: undefined },
        () => {
          expect(createLlmExtractor().modelName).toBe("claude-sonnet-4-6");
        }
      );
    });

    it("uses deepseek-chat when provider is unset and no model env is set", () => {
      withEnv(
        { SOURCECADO_GENERATION_PROVIDER: undefined, SOURCECADO_GENERATION_MODEL: undefined },
        () => {
          expect(createLlmExtractor().modelName).toBe("deepseek-chat");
        }
      );
    });

    it("uses deepseek-chat when provider is 'deepseek' and no model env is set", () => {
      withEnv(
        { SOURCECADO_GENERATION_PROVIDER: "deepseek", SOURCECADO_GENERATION_MODEL: undefined },
        () => {
          expect(createLlmExtractor().modelName).toBe("deepseek-chat");
        }
      );
    });

    it("explicit config.model wins over provider-aware default", () => {
      withEnv(
        { SOURCECADO_GENERATION_PROVIDER: "anthropic", SOURCECADO_GENERATION_MODEL: undefined },
        () => {
          expect(createLlmExtractor({ model: "claude-haiku-4-5" }).modelName).toBe("claude-haiku-4-5");
        }
      );
    });

    it("SOURCECADO_GENERATION_MODEL wins over provider-aware default", () => {
      withEnv(
        { SOURCECADO_GENERATION_PROVIDER: "anthropic", SOURCECADO_GENERATION_MODEL: "claude-opus-4-5" },
        () => {
          expect(createLlmExtractor().modelName).toBe("claude-opus-4-5");
        }
      );
    });
  });
});
