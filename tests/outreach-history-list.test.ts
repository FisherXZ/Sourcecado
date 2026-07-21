import { closeDb, getDb } from "@/lib/db";
import { runMigrations } from "@/lib/migrate";
import { listOutreachHistory } from "@/lib/contacts/outreach";

async function resetOutreachTables(): Promise<void> {
  const db = getDb();
  await db`DROP TABLE IF EXISTS outreach_history CASCADE`;
  await db`DROP TABLE IF EXISTS contact_aliases CASCADE`;
  await db`DROP TABLE IF EXISTS contacts CASCADE`;
  await db`DROP TABLE IF EXISTS organizations CASCADE`;
  await db`DELETE FROM schema_migrations WHERE name IN ('006_contacts.sql', '007_outreach_history.sql')`;
  await runMigrations(db);
}

describe("listOutreachHistory", () => {
  beforeEach(async () => {
    await resetOutreachTables();
  });

  afterAll(async () => {
    await closeDb();
  });

  it("returns an empty array for a contact with no history", async () => {
    const db = getDb();
    const [contact] = await db<{ id: number }[]>`INSERT INTO contacts (canonical_name) VALUES ('Jane Smith') RETURNING id`;
    const result = await listOutreachHistory(db, Number(contact.id));
    expect(result).toEqual([]);
  });

  it("returns entries ordered most-recent-first", async () => {
    const db = getDb();
    const [contact] = await db<{ id: number }[]>`INSERT INTO contacts (canonical_name) VALUES ('Jane Smith') RETURNING id`;
    await db`
      INSERT INTO outreach_history (contact_id, occurred_at, channel, summary, citation)
      VALUES
        (${contact.id}, '2026-01-01', 'email', 'First cold outreach', 'note-1#chunk-1'),
        (${contact.id}, '2026-05-01', 'call', 'Follow-up call, went well', NULL)
    `;

    const result = await listOutreachHistory(db, Number(contact.id));
    expect(result).toHaveLength(2);
    expect(result[0].summary).toBe("Follow-up call, went well");
    expect(result[0].citation).toBeNull();
    expect(result[1].summary).toBe("First cold outreach");
    expect(result[1].citation).toBe("note-1#chunk-1");
  });

  it("only returns history for the requested contact, not others", async () => {
    const db = getDb();
    const [a] = await db<{ id: number }[]>`INSERT INTO contacts (canonical_name) VALUES ('Jane Smith') RETURNING id`;
    const [b] = await db<{ id: number }[]>`INSERT INTO contacts (canonical_name) VALUES ('Bob Jones') RETURNING id`;
    await db`INSERT INTO outreach_history (contact_id, occurred_at, summary) VALUES (${a.id}, now(), 'About Jane')`;
    await db`INSERT INTO outreach_history (contact_id, occurred_at, summary) VALUES (${b.id}, now(), 'About Bob')`;

    const result = await listOutreachHistory(db, Number(a.id));
    expect(result).toHaveLength(1);
    expect(result[0].summary).toBe("About Jane");
  });
});
