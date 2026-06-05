import { extname, basename } from "node:path";
import { SOURCE_TYPES, type SourceType } from "./types.js";

export interface ParsedSource {
  title: string;
  sourceType: SourceType;
  rawText: string;
}

const EXTENSION_SOURCE_TYPES = new Map<string, SourceType>([
  [".md", "markdown"],
  [".txt", "text"],
  [".csv", "csv"],
  [".eml", "email"]
]);

export function isSupportedSourcePath(filePath: string): boolean {
  return EXTENSION_SOURCE_TYPES.has(extname(filePath).toLowerCase());
}

export function defaultSourceTypeForPath(filePath: string): SourceType | undefined {
  return EXTENSION_SOURCE_TYPES.get(extname(filePath).toLowerCase());
}

export function parseSourceFile(filePath: string, content: string): ParsedSource {
  const sourceTypeFromExtension = defaultSourceTypeForPath(filePath);
  if (!sourceTypeFromExtension) {
    throw new Error(`Unsupported file extension: ${extname(filePath) || "(none)"}`);
  }

  const { metadata, body } =
    sourceTypeFromExtension === "markdown" ? parseFrontmatter(content) : { metadata: {}, body: content };
  const sourceType = parseSourceType(metadata.source_type) ?? sourceTypeFromExtension;
  const title = metadata.title?.trim() || basename(filePath, extname(filePath));
  const rawText = body.trim();

  if (!rawText) {
    throw new Error("File is empty after parsing");
  }

  return {
    title,
    sourceType,
    rawText
  };
}

function parseFrontmatter(content: string): { metadata: Record<string, string>; body: string } {
  if (!content.startsWith("---\n") && !content.startsWith("---\r\n")) {
    return { metadata: {}, body: content };
  }

  const newline = content.startsWith("---\r\n") ? "\r\n" : "\n";
  const closingFence = `${newline}---${newline}`;
  const closingIndex = content.indexOf(closingFence, 3);
  if (closingIndex === -1) {
    throw new Error("Malformed frontmatter: missing closing fence");
  }

  const metadataText = content.slice(3 + newline.length, closingIndex);
  const body = content.slice(closingIndex + closingFence.length);
  return {
    metadata: parseMetadata(metadataText),
    body
  };
}

function parseMetadata(metadataText: string): Record<string, string> {
  const metadata: Record<string, string> = {};

  for (const line of metadataText.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed) {
      continue;
    }

    const separatorIndex = trimmed.indexOf(":");
    if (separatorIndex === -1) {
      throw new Error(`Malformed frontmatter line: ${trimmed}`);
    }

    const key = trimmed.slice(0, separatorIndex).trim();
    const value = trimmed.slice(separatorIndex + 1).trim();
    if (key) {
      metadata[key] = stripYamlishQuotes(value);
    }
  }

  return metadata;
}

function stripYamlishQuotes(value: string): string {
  if (
    (value.startsWith('"') && value.endsWith('"')) ||
    (value.startsWith("'") && value.endsWith("'"))
  ) {
    return value.slice(1, -1);
  }

  return value;
}

function parseSourceType(value: string | undefined): SourceType | undefined {
  if (!value) {
    return undefined;
  }

  if (SOURCE_TYPES.includes(value as SourceType)) {
    return value as SourceType;
  }

  throw new Error(`Unsupported source_type metadata: ${value}`);
}
