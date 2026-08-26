export const INGEST_ERROR_CATEGORIES = [
  "unsupported-type",
  "unreadable",
  "empty",
  "parse-error",
  "embedding-error",
  "internal-error"
] as const;
export type IngestErrorCategory = (typeof INGEST_ERROR_CATEGORIES)[number];

export class IngestError extends Error {
  readonly category: IngestErrorCategory;

  constructor(category: IngestErrorCategory, message: string) {
    super(message);
    this.name = "IngestError";
    this.category = category;
  }
}

export function classifyIngestError(error: unknown): IngestErrorCategory {
  if (error instanceof IngestError) {
    return error.category;
  }

  return "internal-error";
}
