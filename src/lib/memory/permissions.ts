import type { Sql } from "../tools/types";
import type { MemoryActor } from "./actor";

// Default-deny: returns only source_ids explicitly granted 'read' to this actor.
// No rows → [] (caller must not fetch restricted data).
//
// Archived sources are excluded by default. The JOIN to source_records is the
// single chokepoint that drops archived sources from EVERY retrieval path
// (searchMemory's three loaders all gate on this allowlist). Management views
// (the sources list, archive/un-archive) pass { includeArchived: true } so they
// can still see and act on archived sources.
export async function resolveAllowedSourceIds(
  db: Sql,
  actor: MemoryActor,
  opts: { includeArchived?: boolean } = {}
): Promise<string[]> {
  const includeArchived = opts.includeArchived ?? false;
  const rows = await db<{ source_id: string }[]>`
    SELECT sp.source_id
    FROM source_permissions sp
    JOIN source_records sr ON sr.source_id = sp.source_id
    WHERE sp.principal_type = ${actor.actorType}
      AND sp.principal_id = ${actor.actorId}
      AND sp.access = 'read'
      AND (${includeArchived} OR sr.archived_at IS NULL)
    ORDER BY sp.source_id
  `;
  return rows.map((r) => r.source_id);
}
