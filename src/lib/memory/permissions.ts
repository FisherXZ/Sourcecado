import type { Sql } from "../tools/types";
import type { MemoryActor } from "./actor";

// Default-deny: returns only source_ids explicitly granted 'read' to this actor.
// No rows → [] (caller must not fetch restricted data).
export async function resolveAllowedSourceIds(db: Sql, actor: MemoryActor): Promise<string[]> {
  const rows = await db<{ source_id: string }[]>`
    SELECT source_id
    FROM source_permissions
    WHERE principal_type = ${actor.actorType}
      AND principal_id = ${actor.actorId}
      AND access = 'read'
    ORDER BY source_id
  `;
  return rows.map((r) => r.source_id);
}
