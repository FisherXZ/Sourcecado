import { closeDb, getDb } from "@/lib/db";
import { runMigrations } from "@/lib/migrate";
import { listOutreachHistoryTool } from "@/lib/tools/list-outreach-history";

async function resetOutreachTables(): Promise<void> {
  const db = getDb();
  await db`DROP TABLE IF EXISTS outreach_history CASCADE`;
  await db`DROP TABLE IF EXISTS contact_aliases CASCADE`;
  await db`DROP TABLE IF EXISTS contacts CASCADE`;
  await db`DROP TABLE IF EXISTS organizations CASCADE`;
  await db`DELETE FROM schema_migrations WHERE name IN ('006_contacts.sql', '007_outreach_history.sql')`;
  await runMigrations(db);
}

describe("listOutreachHistoryTool", () => {
  beforeEach(async () => {
    await resetOutreachTables();
  });

  afterAll(async () => {
    await closeDb();
  });

  it("is a read-class tool named list_outreach_history", () => {
    expect(listOutreachHistoryTool.name).toBe("list_outreach_history");
    expect(listOutreachHistoryTool.permissionClass).toBe("read");
  });

  it("resolves a contact's history end-to-end through the tool", async () => {
    const db = getDb();
    const [contact] = await db<{ id: number }[]>`INSERT INTO contacts (canonical_name) VALUES ('Jane Smith') RETURNING id`;
    await db`INSERT INTO outreach_history (contact_id, occurred_at, summary) VALUES (${contact.id}, now(), 'Intro call')`;

    const result = await listOutreachHistoryTool.execute(
      { contactId: Number(contact.id) },
      { db, runId: 0, parentStepId: 0 },
    );
    expect(result).toHaveLength(1);
  });

  it("rejects a non-positive contactId via the args schema", () => {
    expect(() => listOutreachHistoryTool.argsSchema.parse({ contactId: 0 })).toThrow();
    expect(() => listOutreachHistoryTool.argsSchema.parse({ contactId: -1 })).toThrow();
  });
});
