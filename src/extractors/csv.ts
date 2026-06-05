import type { ExtractedCandidate, RelationshipType } from "../types.js";
import { normalizeCsvHeader, parseCsvRecords } from "../csv.js";
import type { ExtractionInput, Extractor } from "./types.js";

export const CSV_EXTRACTOR_VERSION = "2";

type Row = Record<string, string>;

const HEADER_ALIASES = {
  contact: ["contact", "name", "person", "poc_name", "full_name"],
  firstName: ["first_name", "poc_first_name"],
  lastName: ["last_name", "poc_last_name"],
  organization: ["company", "company_name", "organization", "org"],
  domain: ["domain"],
  status: ["status"],
  outcome: ["outcome"],
  notes: ["notes"],
  followUp: ["follow_up", "follow-up", "needs_follow_up"],
  reason: ["reason"],
  email: ["email", "poc_email", "email_address"],
  emailStatus: ["email_status"],
  title: ["title", "poc_title"],
  interest: ["interest"],
  owner: ["owner", "contact_owner", "cody_poc", "cody_pocs"],
  departments: ["departments", "department"]
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
    const contact =
      getAliasedValue(row, HEADER_ALIASES.contact) ||
      composeName(
        getAliasedValue(row, HEADER_ALIASES.firstName),
        getAliasedValue(row, HEADER_ALIASES.lastName)
      );
    const organization = getAliasedValue(row, HEADER_ALIASES.organization);
    const domain = getAliasedValue(row, HEADER_ALIASES.domain);
    const status = getAliasedValue(row, HEADER_ALIASES.status);
    const outcome = getAliasedValue(row, HEADER_ALIASES.outcome);
    const notes = getAliasedValue(row, HEADER_ALIASES.notes);
    const followUp = getAliasedValue(row, HEADER_ALIASES.followUp);
    const reason = getAliasedValue(row, HEADER_ALIASES.reason);
    const email = getAliasedValue(row, HEADER_ALIASES.email);
    const emailStatus = getAliasedValue(row, HEADER_ALIASES.emailStatus);
    const title = getAliasedValue(row, HEADER_ALIASES.title);
    const interest = getAliasedValue(row, HEADER_ALIASES.interest);
    const owner = getAliasedValue(row, HEADER_ALIASES.owner);
    const departments = getAliasedValue(row, HEADER_ALIASES.departments);
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
    if (subject && (isAffirmative(followUp) || indicatesFollowUp(notes))) {
      candidates.push(
        relationshipCandidate(subject, "needs_follow_up", reason || notes || "follow-up", evidenceText)
      );
      candidates.push(factCandidate(subject, "needs_follow_up", reason || notes || "yes", evidenceText));
    }
    if (subject && reason) {
      candidates.push(factCandidate(subject, "reason", reason, evidenceText));
    }
    if (subject && email) {
      candidates.push(factCandidate(subject, "email", email, evidenceText));
    }
    if (subject && emailStatus) {
      candidates.push(factCandidate(subject, "email_status", emailStatus, evidenceText));
    }
    if (subject && title) {
      candidates.push(factCandidate(subject, "title", title, evidenceText));
    }
    if (subject && interest) {
      candidates.push(factCandidate(subject, "interest", interest, evidenceText));
    }
    if (subject && owner) {
      candidates.push(factCandidate(subject, "codeology_owner", owner, evidenceText));
    }
    if (subject && departments) {
      candidates.push(factCandidate(subject, "departments", departments, evidenceText));
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

function indicatesFollowUp(value: string): boolean {
  const normalized = value.trim().toLowerCase();
  return normalized.includes("follow-up") || normalized.includes("follow up");
}

function composeName(firstName: string, lastName: string): string {
  return [firstName, lastName].filter(Boolean).join(" ").trim();
}

function getAliasedValue(row: Row, aliases: readonly string[]): string {
  for (const alias of aliases) {
    const value = row[normalizeCsvHeader(alias)];
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
  const records = parseCsvRecords(content);
  const [headers, ...body] = records;
  if (!headers) {
    return [];
  }

  const normalizedHeaders = headers.map(normalizeCsvHeader);
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
