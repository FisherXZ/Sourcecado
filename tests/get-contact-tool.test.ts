import { closeDb, getDb } from "@/lib/db";
import { runMigrations } from "@/lib/migrate";
import { getContactTool } from "@/lib/tools/get-contact";
import { getOrganizationTool } from "@/lib/tools/get-organization";

async function resetContactTables(): Promise<void> {
  const db = getDb();
  await db`DROP TABLE IF EXISTS contact_aliases CASCADE`;
  await db`DROP TABLE IF EXISTS contacts CASCADE`;
  await db`DROP TABLE IF EXISTS organizations CASCADE`;
  await db`DELETE FROM schema_migrations WHERE name IN ('006_contacts.sql', '007_outreach_history.sql', '008_contact_details.sql')`;
  await runMigrations(db);
}

describe("getContactTool", () => {
  beforeEach(async () => {
    await resetContactTables();
  });

  afterAll(async () => {
    await closeDb();
  });

  it("is a read-class tool named get_contact", () => {
    expect(getContactTool.name).toBe("get_contact");
    expect(getContactTool.permissionClass).toBe("read");
  });

  it("resolves a contact end-to-end through the tool", async () => {
    const db = getDb();
    await db`INSERT INTO contacts (canonical_name, role) VALUES ('Jane Smith', 'PM')`;

    const result = await getContactTool.execute(
      { name: "Jane Smith" },
      { db, runId: 0, parentStepId: 0 },
    );
    expect(result.status).toBe("found");
  });

  it("rejects an empty name via the args schema", () => {
    expect(() => getContactTool.argsSchema.parse({ name: "" })).toThrow();
  });
});

describe("getOrganizationTool", () => {
  beforeEach(async () => {
    await resetContactTables();
  });

  afterAll(async () => {
    await closeDb();
  });

  it("is a read-class tool named get_organization", () => {
    expect(getOrganizationTool.name).toBe("get_organization");
    expect(getOrganizationTool.permissionClass).toBe("read");
  });

  it("resolves an organization end-to-end through the tool", async () => {
    const db = getDb();
    await db`INSERT INTO organizations (name) VALUES ('Acme Corp')`;

    const result = await getOrganizationTool.execute(
      { name: "Acme Corp" },
      { db, runId: 0, parentStepId: 0 },
    );
    expect(result.status).toBe("found");
  });
});
