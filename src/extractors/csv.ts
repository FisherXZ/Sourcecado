import type { ExtractedCandidate, RelationshipType } from "../types.js";
import type { ExtractionInput, Extractor } from "./types.js";

export const CSV_EXTRACTOR_VERSION = "1";

type Row = Record<string, string>;

const HEADER_ALIASES = {
  contact: ["contact", "name", "person"],
  organization: ["company", "organization", "org"],
  domain: ["domain"],
  status: ["status"],
  outcome: ["outcome"],
  notes: ["notes"],
  followUp: ["follow_up", "follow-up", "needs_follow_up"],
  reason: ["reason"]
} as const;

export function createCsvExtractor(): Extractor {
  return {
    type: "csv",
    version: CSV_EXTRACTOR_VERSION,
    promptHash: "none",
    schemaVersion: "1",
    modelName: "local",
    async extract(input) {
      return extractCsvCandidates(input);
    }
  };
}

export function extractCsvCandidates(input: ExtractionInput): ExtractedCandidate[] {
  const rows = parseCsv(input.content);
  const candidates: ExtractedCandidate[] = [];

  for (const row of rows) {
    const contact = getAliasedValue(row, HEADER_ALIASES.contact);
    const organization = getAliasedValue(row, HEADER_ALIASES.organization);
    const domain = getAliasedValue(row, HEADER_ALIASES.domain);
    const status = getAliasedValue(row, HEADER_ALIASES.status);
    const outcome = getAliasedValue(row, HEADER_ALIASES.outcome);
    const notes = getAliasedValue(row, HEADER_ALIASES.notes);
    const followUp = getAliasedValue(row, HEADER_ALIASES.followUp);
    const reason = getAliasedValue(row, HEADER_ALIASES.reason);
    const evidenceText = rowEvidence(row);
    const subject = contact || organization || domain;

    if (contact) {
      candidates.push(entityCandidate(contact, "person", evidenceText));
    }
    if (organization) {
      candidates.push(entityCandidate(organization, "organization", evidenceText));
    }
    if (domain) {
      candidates.push(entityCandidate(domain, "domain", evidenceText));
    }

    if (contact && organization) {
      candidates.push(relationshipCandidate(contact, "works_at", organization, evidenceText));
    }
    if (contact && domain) {
      candidates.push(relationshipCandidate(contact, "relevant_to_domain", domain, evidenceText));
    }

    if (subject && status) {
      candidates.push(factCandidate(subject, "status", status, evidenceText));
      const relationshipType = relationshipFromStatus(status);
      if (relationshipType) {
        candidates.push(relationshipCandidate(subject, relationshipType, organization || domain || status, evidenceText));
      }
    }
    if (subject && domain) {
      candidates.push(factCandidate(subject, "domain", domain, evidenceText));
    }
    if (subject && outcome) {
      candidates.push(factCandidate(subject, "outcome", outcome, evidenceText));
    }
    if (subject && notes) {
      candidates.push(factCandidate(subject, "notes", notes, evidenceText));
    }
    if (subject && isAffirmative(followUp)) {
      candidates.push(
        relationshipCandidate(subject, "needs_follow_up", reason || notes || "follow-up", evidenceText)
      );
      candidates.push(factCandidate(subject, "needs_follow_up", reason || notes || "yes", evidenceText));
    }
    if (subject && reason) {
      candidates.push(factCandidate(subject, "reason", reason, evidenceText));
    }
  }

  return candidates;
}

function entityCandidate(
  subject: string,
  entityType: ExtractedCandidate["entityType"],
  evidenceText: string
): ExtractedCandidate {
  return {
    kind: "entity",
    subject,
    entityType,
    confidence: 0.95,
    evidenceText
  };
}

function relationshipCandidate(
  subject: string,
  relationshipType: RelationshipType,
  object: string,
  evidenceText: string
): ExtractedCandidate {
  return {
    kind: "relationship",
    subject,
    relationshipType,
    object,
    confidence: 0.82,
    evidenceText
  };
}

function factCandidate(
  subject: string,
  predicate: string,
  object: string,
  evidenceText: string
): ExtractedCandidate {
  return {
    kind: "semantic_fact",
    subject,
    predicate,
    object,
    confidence: 0.86,
    evidenceText
  };
}

function relationshipFromStatus(status: string): RelationshipType | undefined {
  const normalized = status.toLowerCase();
  if (normalized.includes("respond")) {
    return "responded";
  }
  if (normalized.includes("contact")) {
    return "contacted";
  }
  return undefined;
}

function isAffirmative(value: string): boolean {
  return ["yes", "true", "y", "1", "needed", "needs follow up", "needs_follow_up"].includes(
    value.trim().toLowerCase()
  );
}

function getAliasedValue(row: Row, aliases: readonly string[]): string {
  for (const alias of aliases) {
    const value = row[normalizeHeader(alias)];
    if (value?.trim()) {
      return value.trim();
    }
  }
  return "";
}

function rowEvidence(row: Row): string {
  return Object.values(row)
    .filter((value) => value.trim())
    .join(" | ");
}

function parseCsv(content: string): Row[] {
  const records = parseRecords(content);
  const [headers, ...body] = records;
  if (!headers) {
    return [];
  }

  const normalizedHeaders = headers.map(normalizeHeader);
  return body
    .filter((record) => record.some((value) => value.trim()))
    .map((record) => {
      const row: Row = {};
      normalizedHeaders.forEach((header, index) => {
        row[header] = record[index]?.trim() ?? "";
      });
      return row;
    });
}

function parseRecords(content: string): string[][] {
  const records: string[][] = [];
  let record: string[] = [];
  let field = "";
  let inQuotes = false;

  for (let index = 0; index < content.length; index += 1) {
    const char = content[index];
    const next = content[index + 1];

    if (char === '"') {
      if (inQuotes && next === '"') {
        field += '"';
        index += 1;
      } else {
        inQuotes = !inQuotes;
      }
      continue;
    }

    if (char === "," && !inQuotes) {
      record.push(field);
      field = "";
      continue;
    }

    if ((char === "\n" || char === "\r") && !inQuotes) {
      if (char === "\r" && next === "\n") {
        index += 1;
      }
      record.push(field);
      records.push(record);
      record = [];
      field = "";
      continue;
    }

    field += char;
  }

  if (field.length > 0 || record.length > 0) {
    record.push(field);
    records.push(record);
  }

  return records;
}

function normalizeHeader(header: string): string {
  return header.trim().toLowerCase().replace(/\s+/g, "_");
}
