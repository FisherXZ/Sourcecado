import { createHash } from "node:crypto";
import { parseCsvRecords, serializeCsvRecord } from "./csv.js";

export interface TextChunk {
  chunkIndex: number;
  text: string;
  chunkHash: string;
}

const MAX_CHUNK_CHARACTERS = 1_000;

export function chunkText(text: string): TextChunk[] {
  const paragraphs = text
    .split(/\n{2,}/)
    .map((paragraph) => paragraph.trim())
    .filter(Boolean);
  const chunks: string[] = [];

  for (const paragraph of paragraphs.length > 0 ? paragraphs : [text.trim()]) {
    if (paragraph.length <= MAX_CHUNK_CHARACTERS) {
      chunks.push(paragraph);
      continue;
    }

    chunks.push(...splitLongText(paragraph));
  }

  if (chunks.length === 0) {
    throw new Error("No chunks created");
  }

  return chunks.map((chunk, index) => ({
    chunkIndex: index,
    text: chunk,
    chunkHash: sha256(chunk)
  }));
}

export function chunkCsvRows(text: string): TextChunk[] {
  const [headers, ...rows] = parseCsvRecords(text);
  if (!headers) {
    throw new Error("CSV file has no header row");
  }

  const dataRows = rows.filter((row) => row.some((value) => value.trim()));
  if (dataRows.length === 0) {
    throw new Error("CSV file has no data rows");
  }

  return dataRows.map((row, index) => {
    const chunk = [serializeCsvRecord(headers), serializeCsvRecord(row)].join("\n");
    return {
      chunkIndex: index,
      text: chunk,
      chunkHash: sha256(chunk)
    };
  });
}

export function sha256(text: string): string {
  return createHash("sha256").update(text).digest("hex");
}

function splitLongText(text: string): string[] {
  const words = text.split(/\s+/).filter(Boolean);
  const chunks: string[] = [];
  let current = "";

  for (const word of words) {
    if (!current) {
      current = word;
      continue;
    }

    const candidate = `${current} ${word}`;
    if (candidate.length > MAX_CHUNK_CHARACTERS) {
      chunks.push(current);
      current = word;
    } else {
      current = candidate;
    }
  }

  if (current) {
    chunks.push(current);
  }

  return chunks.flatMap((chunk) => splitOversizedToken(chunk));
}

function splitOversizedToken(text: string): string[] {
  if (text.length <= MAX_CHUNK_CHARACTERS) {
    return [text];
  }

  const chunks: string[] = [];
  for (let index = 0; index < text.length; index += MAX_CHUNK_CHARACTERS) {
    chunks.push(text.slice(index, index + MAX_CHUNK_CHARACTERS));
  }

  return chunks;
}
