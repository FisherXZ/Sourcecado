import { createHash } from "node:crypto";

export const EMBEDDING_DIMENSIONS = 64;

export function embedText(text: string): number[] {
  const vector = Array.from({ length: EMBEDDING_DIMENSIONS }, () => 0);
  const tokens = text.toLowerCase().match(/[a-z0-9]+/g) ?? [];

  for (const token of tokens) {
    const hash = createHash("sha256").update(token).digest();
    const index = hash.readUInt32BE(0) % EMBEDDING_DIMENSIONS;
    vector[index] += 1;
  }

  const magnitude = Math.sqrt(vector.reduce((sum, value) => sum + value * value, 0));
  if (magnitude === 0) {
    return vector;
  }

  return vector.map((value) => Number((value / magnitude).toFixed(6)));
}

export function serializeEmbedding(text: string): string {
  return JSON.stringify(embedText(text));
}

export function deserializeEmbedding(serialized: string | null): number[] {
  if (!serialized) {
    return [];
  }
  const parsed = JSON.parse(serialized) as unknown;
  return Array.isArray(parsed) && parsed.every((value) => typeof value === "number") ? parsed : [];
}

export function cosineSimilarity(left: number[], right: number[]): number {
  const length = Math.min(left.length, right.length);
  if (length === 0) {
    return 0;
  }

  let dot = 0;
  let leftMagnitude = 0;
  let rightMagnitude = 0;
  for (let index = 0; index < length; index += 1) {
    const leftValue = left[index] ?? 0;
    const rightValue = right[index] ?? 0;
    dot += leftValue * rightValue;
    leftMagnitude += leftValue * leftValue;
    rightMagnitude += rightValue * rightValue;
  }

  if (leftMagnitude === 0 || rightMagnitude === 0) {
    return 0;
  }

  return dot / (Math.sqrt(leftMagnitude) * Math.sqrt(rightMagnitude));
}
