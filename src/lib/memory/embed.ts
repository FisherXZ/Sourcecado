import { createHash } from "node:crypto";
import { callModel } from "../model-gateway";
import type { Sql } from "../tools/types";

export const MEMORY_EMBEDDING_DIMENSIONS = 1536;

export function usesRealEmbeddings(): boolean {
  return !!(process.env.OPENAI_API_KEY?.trim());
}

export function toVectorLiteral(vec: number[]): string {
  return `[${vec.join(",")}]`;
}

export async function embedText(db: Sql, text: string): Promise<number[]> {
  if (usesRealEmbeddings()) {
    const result = await callModel(db, {
      kind: "embed",
      taskName: "embed_memory_text",
      promptVersion: "1",
      value: text,
    });
    // eslint-disable-next-line @typescript-eslint/no-non-null-assertion
    return result.embedding!;
  }
  return fallbackEmbed(text);
}

function fallbackEmbed(text: string): number[] {
  const vector = Array.from({ length: MEMORY_EMBEDDING_DIMENSIONS }, () => 0);
  const tokens = text.toLowerCase().match(/[a-z0-9]+/g) ?? [];

  for (const token of tokens) {
    const hash = createHash("sha256").update(token).digest();
    const index = hash.readUInt32BE(0) % MEMORY_EMBEDDING_DIMENSIONS;
    vector[index] += 1;
  }

  const magnitude = Math.sqrt(vector.reduce((sum, value) => sum + value * value, 0));
  if (magnitude === 0) {
    return vector;
  }

  return vector.map((value) => value / magnitude);
}
