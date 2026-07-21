import { closeDb, getDb } from "@/lib/db";
import { runMigrations } from "@/lib/migrate";
import { createContact } from "@/lib/contacts/create";

async function resetContactTables(): Promise<void> {
  const db = getDb();
  await db`DROP TABLE IF EXISTS contact_aliases CASCADE`;
  await db`DROP TABLE IF EXISTS contacts CASCADE`;
  await db`DROP TABLE IF EXISTS organizations CASCADE`;
  await db`DELETE FROM schema_migrations WHERE name IN ('006_contacts.sql', '007_outreach_history.sql', '008_contact_details.sql')`;
  await runMigrations(db);
}

describe("createContact", () => {
  beforeEach(async () => {
    await resetContactTables();
  });

  afterAll(async () => {
    await closeDb();
  });

  it("creates a contact with only a name — role and org are gaps (null), not blockers", async () => {
    const result = await createContact(getDb(), { name: "Thin Record" });
    expect(result.status).toBe("created");
    if (result.status === "created") {
      expect(result.contact.canonicalName).toBe("Thin Record");
      expect(result.contact.role).toBeNull();
      expect(result.contact.organizationId).toBeNull();
      expect(result.contact.organizationName).toBeNull();
    }
  });

  it("creates a new organization when the named org doesn't exist yet", async () => {
    const db = getDb();
    const result = await createContact(db, { name: "Jane Smith", role: "PM", organizationName: "Brand New Co" });
    expect(result.status).toBe("created");
    if (result.status === "created") {
      expect(result.contact.organizationName).toBe("Brand New Co");
      expect(result.contact.organizationId).not.toBeNull();
    }
    const orgs = await db`SELECT count(*) FROM organizations WHERE name = 'Brand New Co'`;
    expect(Number(orgs[0].count)).toBe(1);
  });

  it("reuses an existing organization instead of creating a duplicate", async () => {
    const db = getDb();
    const [org] = await db<{ id: number }[]>`INSERT INTO organizations (name) VALUES ('Acme Corp') RETURNING id`;

    const result = await createContact(db, { name: "Jane Smith", organizationName: "acme corp" });
    expect(result.status).toBe("created");
    if (result.status === "created") {
      expect(result.contact.organizationId).toBe(Number(org.id));
    }
    const orgs = await db`SELECT count(*) FROM organizations WHERE lower(name) = 'acme corp'`;
    expect(Number(orgs[0].count)).toBe(1);
  });

  it("refuses to write and returns ambiguous_organization when the org name matches two existing orgs", async () => {
    const db = getDb();
    await db`INSERT INTO organizations (name) VALUES ('Acme Corp')`;
    await db`INSERT INTO organizations (name) VALUES ('Acme Corp')`;

    const result = await createContact(db, { name: "Jane Smith", organizationName: "Acme Corp" });
    expect(result.status).toBe("ambiguous_organization");
    if (result.status === "ambiguous_organization") {
      expect(result.candidates).toHaveLength(2);
    }
    const contacts = await db`SELECT count(*) FROM contacts`;
    expect(Number(contacts[0].count)).toBe(0);
  });

  it("two contacts of the same name can both be created — no dedup/merge on write", async () => {
    const db = getDb();
    await createContact(db, { name: "Jane Smith" });
    await createContact(db, { name: "Jane Smith" });
    const rows = await db`SELECT id FROM contacts WHERE canonical_name = 'Jane Smith'`;
    expect(rows).toHaveLength(2);
  });
});
