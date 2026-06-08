import { buildSourcingMemoryAnswer } from "./answer.js";
import type { MemoryDatabase } from "./db.js";
import { cosineSimilarity, deserializeEmbedding, embedText } from "./embeddings.js";

export const READ_ACTIONS = [
  "ask",
  "search_memory",
  "get_source",
  "list_gaps",
  "denied_read"
] as const;
export type ReadAction = (typeof READ_ACTIONS)[number];

export const GET_SOURCE_DENIAL_REASONS = ["denied", "missing", "malformed"] as const;
export type GetSourceDenialReason = (typeof GET_SOURCE_DENIAL_REASONS)[number];

const MAX_SOURCE_ID_LENGTH = 512;
const DEFAULT_SEARCH_LIMIT = 5;

export interface SearchResultRow {
  sourceId: string;
  text: string;
  citation: string;
  score: number;
}

export interface SourceSummary {
  sourceId: string;
  title: string;
  sourceType: string;
  path: string;
}

export type GetSourceResult =
  | { ok: true; source: SourceSummary }
  | { ok: false; reason: GetSourceDenialReason };

export interface GapItem {
  subject: string;
  predicate: string;
  object: string;
  status: string;
  citation: string | null;
}

export const ACTOR_TYPES = ["user", "oauth_client", "test_client"] as const;
export type ActorType = (typeof ACTOR_TYPES)[number];

export interface AccessContext {
  actorType: ActorType;
  actorId: string;
  allowedSourceIds: string[];
  deniedSourceIds: string[];
  auditLabel: string;
}

export interface SourceScope {
  allowedSourceIds: string[];
}

export interface ActorIdentity {
  actorType: ActorType;
  actorId: string;
}

// Resolve a default-deny AccessContext from the source_permissions allowlist.
// An empty allowedSourceIds means no access (default-deny enforced in SQL by
// callers). deniedSourceIds is informational only and never used for filtering.
export function resolveAccessContext(
  db: MemoryDatabase,
  { actorType, actorId }: ActorIdentity
): AccessContext {
  const allowedRows = db
    .prepare(
      "select source_id from source_permissions where principal_type = ? and principal_id = ? and access = 'read' order by source_id"
    )
    .all(actorType, actorId) as Array<{ source_id: string }>;
  const allowedSourceIds = allowedRows.map((row) => row.source_id);

  const allowedSet = new Set(allowedSourceIds);
  const deniedRows = db
    .prepare("select source_id from source_records order by source_id")
    .all() as Array<{ source_id: string | null }>;
  const deniedSourceIds = deniedRows
    .map((row) => row.source_id)
    .filter((sourceId): sourceId is string => sourceId !== null && !allowedSet.has(sourceId));

  return {
    actorType,
    actorId,
    allowedSourceIds,
    deniedSourceIds,
    auditLabel: `${actorType}:${actorId}`
  };
}

// Build a parameterized `alias.source_id in (?, ?, ...)` clause from a scope.
// An empty scope yields a clause that matches no rows, preserving default-deny
// even if a caller forgets to short-circuit.
export function sourceIdInClause(
  scope: SourceScope,
  alias = "sr"
): { sql: string; params: string[] } {
  if (scope.allowedSourceIds.length === 0) {
    return { sql: "1 = 0", params: [] };
  }
  const placeholders = scope.allowedSourceIds.map(() => "?").join(", ");
  return {
    sql: `${alias}.source_id in (${placeholders})`,
    params: [...scope.allowedSourceIds]
  };
}

// Explicit no-access Answer rendered when a caller has zero allowed sources.
// Every section is present and the language states plainly there is no access,
// so the caller never receives a silent empty payload.
function noAccessAnswer(question: string): string {
  const questionLine = question.trim() ? ` for "${question.trim()}"` : "";
  return [
    "Answer:",
    `You have no access to any sources, so I cannot answer${questionLine}.`,
    "",
    "Evidence:",
    "- No accessible sources: you have no access to any sources.",
    "",
    "Gaps:",
    "- Your access scope is empty; no candidate, conflicted, or stale memory is visible to you.",
    "",
    "Next Action:",
    "- Request read access to the relevant sources, then ask again."
  ].join("\n");
}

export class MemoryReader {
  constructor(
    private readonly db: MemoryDatabase,
    private readonly ctx: AccessContext
  ) {}

  private get scope(): SourceScope {
    return { allowedSourceIds: this.ctx.allowedSourceIds };
  }

  private hasNoAccess(): boolean {
    return this.ctx.allowedSourceIds.length === 0;
  }

  // Append one audit row per read. Denied reads use action 'denied_read' and
  // record the requested source_id when one was supplied.
  private recordAudit(action: ReadAction, sourceId?: string): void {
    this.db
      .prepare(
        "insert into audit_events (actor_type, actor_id, action, source_id) values (?, ?, ?, ?)"
      )
      .run(this.ctx.actorType, this.ctx.actorId, action, sourceId ?? null);
  }

  ask(question: string): string {
    if (this.hasNoAccess()) {
      this.recordAudit("denied_read");
      return noAccessAnswer(question);
    }
    this.recordAudit("ask");
    return buildSourcingMemoryAnswer(this.db, question, this.scope);
  }

  // Scope-filtered semantic search. Only chunks from allowed sources can be
  // ranked or returned; the scope filter lives in SQL so a higher-scoring
  // restricted chunk is never a retrieval candidate in the first place.
  searchMemory(query: string, options: { limit?: number } = {}): SearchResultRow[] {
    if (this.hasNoAccess()) {
      this.recordAudit("denied_read");
      return [];
    }
    this.recordAudit("search_memory");

    const limit = normalizeLimit(options.limit);
    const { sql: scopeSql, params } = sourceIdInClause(this.scope, "sr");
    const rows = this.db
      .prepare(
        [
          "select sr.source_id as sourceId, memory_chunks.text as text,",
          "memory_chunks.citation as citation, memory_chunks.embedding as embedding",
          "from memory_chunks",
          "join source_records sr on sr.id = memory_chunks.source_record_id",
          `where ${scopeSql}`,
          "order by memory_chunks.id"
        ].join(" ")
      )
      .all(...params) as Array<{
      sourceId: string;
      text: string;
      citation: string;
      embedding: string | null;
    }>;

    const queryEmbedding = embedText(query);
    return rows
      .map((row) => ({
        sourceId: row.sourceId,
        text: row.text,
        citation: row.citation,
        score: cosineSimilarity(queryEmbedding, deserializeEmbedding(row.embedding))
      }))
      .filter((result) => result.score > 0)
      .sort((left, right) => right.score - left.score)
      .slice(0, limit);
  }

  // Resolve a source the caller is allowed to read. Denials never reveal whether
  // an out-of-scope id exists: any id not in the caller's allowed scope returns
  // 'denied'. 'missing' is reserved for an allowed id that is absent from
  // source_records (a permission referencing a deleted source).
  getSource(sourceId: unknown): GetSourceResult {
    if (
      typeof sourceId !== "string" ||
      sourceId.length === 0 ||
      sourceId.length > MAX_SOURCE_ID_LENGTH
    ) {
      this.recordAudit("denied_read");
      return { ok: false, reason: "malformed" };
    }

    if (this.hasNoAccess() || !this.ctx.allowedSourceIds.includes(sourceId)) {
      this.recordAudit("denied_read", sourceId);
      return { ok: false, reason: "denied" };
    }

    const { sql: scopeSql, params } = sourceIdInClause(this.scope, "sr");
    const row = this.db
      .prepare(
        [
          "select sr.source_id as sourceId, sr.title as title,",
          "sr.source_type as sourceType, sr.path as path",
          "from source_records sr",
          `where ${scopeSql} and sr.source_id = ?`,
          "limit 1"
        ].join(" ")
      )
      .get(...params, sourceId) as SourceSummary | undefined;

    if (!row) {
      this.recordAudit("denied_read", sourceId);
      return { ok: false, reason: "missing" };
    }

    this.recordAudit("get_source", sourceId);
    return { ok: true, source: row };
  }

  // Scoped gaps: candidate/conflicted/stale facts from allowed sources only.
  // Facts from restricted sources are filtered in SQL and never surface.
  listGaps(): GapItem[] {
    if (this.hasNoAccess()) {
      this.recordAudit("denied_read");
      return [];
    }
    this.recordAudit("list_gaps");

    const { sql: scopeSql, params } = sourceIdInClause(this.scope, "sr");
    return this.db
      .prepare(
        [
          "select semantic_facts.subject as subject, semantic_facts.predicate as predicate,",
          "semantic_facts.object as object, semantic_facts.status as status,",
          "memory_chunks.citation as citation",
          "from semantic_facts",
          "join source_records sr on sr.id = semantic_facts.source_record_id",
          "left join memory_chunks on memory_chunks.id = semantic_facts.source_chunk_id",
          `where semantic_facts.status in ('candidate', 'conflicted', 'stale') and ${scopeSql}`,
          "order by semantic_facts.status, semantic_facts.subject"
        ].join(" ")
      )
      .all(...params) as GapItem[];
  }
}

function normalizeLimit(limit: number | undefined): number {
  if (typeof limit !== "number" || !Number.isFinite(limit) || limit <= 0) {
    return DEFAULT_SEARCH_LIMIT;
  }
  return Math.floor(limit);
}
