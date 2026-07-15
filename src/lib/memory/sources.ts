import type { Sql } from "../tools/types";
import { DEFAULT_ACTOR, type MemoryActor } from "./actor";
import { resolveAllowedSourceIds } from "./permissions";

export interface SourceListItem {
  sourceId: string;
  title: string | null;
  sourceType: string;
  updatedAt: string;
  archived: boolean;
}

// Management list: the actor's permitted sources INCLUDING archived (so they can
// be shown + un-archived). Active first, then archived; newest-updated first.
export async function listSources(
  db: Sql,
  actor: MemoryActor = DEFAULT_ACTOR
): Promise<SourceListItem[]> {
  const allowed = await resolveAllowedSourceIds(db, actor, { includeArchived: true });
  if (allowed.length === 0) return [];

  const rows = await db<
    {
      source_id: string;
      title: string | null;
      source_type: string;
      updated_at: Date;
      archived_at: Date | null;
    }[]
  >`
    SELECT source_id, title, source_type, updated_at, archived_at
    FROM source_records
    WHERE source_id = ANY(${allowed})
    ORDER BY (archived_at IS NOT NULL), updated_at DESC
  `;

  return rows.map((r) => ({
    sourceId: r.source_id,
    title: r.title,
    sourceType: r.source_type,
    updatedAt: r.updated_at.toISOString(),
    archived: r.archived_at != null,
  }));
}

// Soft-archive / un-archive a source the actor is permitted to manage. Returns
// null when the source is unknown or not permitted (default-deny).
export async function setSourceArchived(
  db: Sql,
  args: { sourceId: string; archived: boolean; actor?: MemoryActor }
): Promise<{ sourceId: string; archived: boolean } | null> {
  const { sourceId, archived, actor = DEFAULT_ACTOR } = args;

  const allowed = await resolveAllowedSourceIds(db, actor, { includeArchived: true });
  if (!allowed.includes(sourceId)) return null;

  const [row] = await db<{ source_id: string; archived_at: Date | null }[]>`
    UPDATE source_records
    SET archived_at = CASE WHEN ${archived} THEN now() ELSE NULL END,
        updated_at = now()
    WHERE source_id = ${sourceId}
    RETURNING source_id, archived_at
  `;
  if (!row) return null;
  return { sourceId: row.source_id, archived: row.archived_at != null };
}

export interface MemoryIndexRow {
  sourceId: string;
  title: string | null;
  sourceType: string;
  updatedAt: string;
}

export interface MemoryIndexRows {
  sources: MemoryIndexRow[];
  recentNotes: MemoryIndexRow[];
}

// Memory-index query for R4 context assembly (src/lib/context.ts): the
// actor's permitted, non-archived sources (title/type/date), newest-updated
// first, plus the subset that are memory notes capped to the last 20. Kept
// here (not context.ts) — same DB-query-over-source_records concern as
// listSources/setSourceArchived above.
export async function listMemoryIndexRows(
  db: Sql,
  actor: MemoryActor = DEFAULT_ACTOR
): Promise<MemoryIndexRows> {
  const allowed = await resolveAllowedSourceIds(db, actor);
  if (allowed.length === 0) return { sources: [], recentNotes: [] };

  const rows = await db<
    { source_id: string; title: string | null; source_type: string; updated_at: Date }[]
  >`
    SELECT source_id, title, source_type, updated_at
    FROM source_records
    WHERE source_id = ANY(${allowed})
    ORDER BY updated_at DESC
  `;

  const sources: MemoryIndexRow[] = rows.map((r) => ({
    sourceId: r.source_id,
    title: r.title,
    sourceType: r.source_type,
    updatedAt: r.updated_at.toISOString(),
  }));
  const recentNotes = sources.filter((s) => s.sourceType === "note").slice(0, 20);

  return { sources, recentNotes };
}
