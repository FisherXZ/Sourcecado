import type { ExtractedCandidate, SourceType } from "../types";

export interface ExtractionInput {
  sourceId: string;
  sourcePath: string;
  sourceType: SourceType;
  content: string;
}

export interface Extractor {
  type: string;
  version: string;
  promptHash?: string;
  schemaVersion?: string;
  modelName?: string;
  extract(input: ExtractionInput): Promise<ExtractedCandidate[]>;
}

export type ExtractionErrorCode =
  | "missing_config"
  | "malformed_json"
  | "invalid_output"
  | "provider_error";

export class ExtractionError extends Error {
  readonly code: ExtractionErrorCode;
  readonly cause?: unknown;

  constructor(code: ExtractionErrorCode, message: string, options?: { cause?: unknown }) {
    super(message);
    this.name = "ExtractionError";
    this.code = code;
    this.cause = options?.cause;
  }
}
