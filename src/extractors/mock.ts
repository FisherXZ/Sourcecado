import type { ExtractedCandidate } from "../types";
import type { ExtractionInput, Extractor } from "./types";

export const MOCK_EXTRACTOR_VERSION = "1";

export function createMockExtractor(
  candidatesOrFactory:
    | ExtractedCandidate[]
    | ((input: ExtractionInput) => ExtractedCandidate[] | Promise<ExtractedCandidate[]>)
): Extractor {
  return {
    type: "mock",
    version: MOCK_EXTRACTOR_VERSION,
    promptHash: "none",
    schemaVersion: "1",
    modelName: "mock",
    async extract(input) {
      if (typeof candidatesOrFactory === "function") {
        return candidatesOrFactory(input);
      }
      return candidatesOrFactory;
    }
  };
}
