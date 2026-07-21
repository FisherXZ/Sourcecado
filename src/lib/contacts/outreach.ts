import type { Sql } from "../tools/types";

export interface OutreachHistoryEntry {
  id: number;
  occurredAt: Date;
  channel: string | null;
  summary: string;
  citation: string | null;
}

interface OutreachHistoryRow {
  id: number | string;
  occurred_at: Date;
  channel: string | null;
  summary: string;
  citation: string | null;
}

function mapEntry(row: OutreachHistoryRow): OutreachHistoryEntry {
  return {
    id: Number(row.id),
    occurredAt: row.occurred_at,
    channel: row.channel,
    summary: row.summary,
    citation: row.citation,
  };
}

export async function listOutreachHistory(db: Sql, contactId: number): Promise<OutreachHistoryEntry[]> {
  const rows = await db<OutreachHistoryRow[]>`
    SELECT id, occurred_at, channel, summary, citation
    FROM outreach_history
    WHERE contact_id = ${contactId}
    ORDER BY occurred_at DESC
  `;
  return rows.map(mapEntry);
}
