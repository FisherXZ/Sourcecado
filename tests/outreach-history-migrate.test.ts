import { closeDb, getDb } from "@/lib/db";
import { runMigrations } from "@/lib/migrate";

async function resetOutreachTables(): Promise<void> {
  const db = getDb();
  await db`DROP TABLE IF EXISTS outreach_history CASCADE`;
  await db`DROP TABLE IF EXISTS contact_aliases CASCADE`;
  await db`DROP TABLE IF EXISTS contacts CASCADE`;
  await db`DROP TABLE IF EXISTS organizations CASCADE`;
  await db`DELETE FROM schema_migrations WHERE name IN ('006_contacts.sql', '007_outreach_history.sql')`;
  await runMigrations(db);
}

describe("007 outreach_history migration", () => {
  beforeEach(async () => {
    await resetOutreachTables();
  });

  afterAll(async () => {
    await closeDb();
  });

  it("creates outreach_history", async () => {
    const db = getDb();
    const result = await db<{ table_name: string }[]>`
      SELECT table_name FROM information_schema.tables
      WHERE table_schema = 'public' AND table_name = 'outreach_history'
    `;
    expect(result).toHaveLength(1);
  });

  it("requires contact_id, occurred_at, and summary", async () => {
    const db = getDb();
    const [contact] = await db<{ id: number }[]>`INSERT INTO contacts (canonical_name) VALUES ('Jane Smith') RETURNING id`;

    await expect(
      db`INSERT INTO outreach_history (occurred_at, summary) VALUES (now(), 'x')`
    ).rejects.toThrow();
    await expect(
      db`INSERT INTO outreach_history (contact_id, summary) VALUES (${contact.id}, 'x')`
    ).rejects.toThrow();
    await expect(
      db`INSERT INTO outreach_history (contact_id, occurred_at) VALUES (${contact.id}, now())`
    ).rejects.toThrow();
  });

  it("channel and citation are optional", async () => {
    const db = getDb();
    const [contact] = await db<{ id: number }[]>`INSERT INTO contacts (canonical_name) VALUES ('Jane Smith') RETURNING id`;
    const [row] = await db<{ channel: string | null; citation: string | null }[]>`
      INSERT INTO outreach_history (contact_id, occurred_at, summary)
      VALUES (${contact.id}, now(), 'Met at a conference')
      RETURNING channel, citation
    `;
    expect(row.channel).toBeNull();
    expect(row.citation).toBeNull();
  });

  it("rejects an outreach_history row for a nonexistent contact", async () => {
    const db = getDb();
    await expect(
      db`INSERT INTO outreach_history (contact_id, occurred_at, summary) VALUES (999999, now(), 'x')`
    ).rejects.toThrow();
  });

  it("deleting a contact cascades to its outreach history", async () => {
    const db = getDb();
    const [contact] = await db<{ id: number }[]>`INSERT INTO contacts (canonical_name) VALUES ('Jane Smith') RETURNING id`;
    await db`INSERT INTO outreach_history (contact_id, occurred_at, summary) VALUES (${contact.id}, now(), 'Intro call')`;
    await db`DELETE FROM contacts WHERE id = ${contact.id}`;
    const remaining = await db`SELECT 1 FROM outreach_history WHERE contact_id = ${contact.id}`;
    expect(remaining).toHaveLength(0);
  });

  it("outreach_history_contact_idx exists", async () => {
    const db = getDb();
    const result = await db<{ indexname: string }[]>`
      SELECT indexname FROM pg_indexes
      WHERE schemaname = 'public' AND indexname = 'outreach_history_contact_idx'
    `;
    expect(result).toHaveLength(1);
  });
});
