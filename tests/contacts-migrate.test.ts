import { closeDb, getDb } from "@/lib/db";
import { runMigrations } from "@/lib/migrate";

async function resetContactTables(): Promise<void> {
  const db = getDb();
  await db`DROP TABLE IF EXISTS contact_aliases CASCADE`;
  await db`DROP TABLE IF EXISTS contacts CASCADE`;
  await db`DROP TABLE IF EXISTS organizations CASCADE`;
  await db`DELETE FROM schema_migrations WHERE name = '006_contacts.sql'`;
  await runMigrations(db);
}

describe("006 contacts migration", () => {
  beforeEach(async () => {
    await resetContactTables();
  });

  afterAll(async () => {
    await closeDb();
  });

  it("creates organizations, contacts, and contact_aliases", async () => {
    const db = getDb();
    const result = await db<{ table_name: string }[]>`
      SELECT table_name FROM information_schema.tables
      WHERE table_schema = 'public' AND table_name IN ('organizations', 'contacts', 'contact_aliases')
      ORDER BY table_name
    `;
    expect(result.map((r) => r.table_name)).toEqual(["contact_aliases", "contacts", "organizations"]);
  });

  it("contacts.canonical_name is required", async () => {
    const db = getDb();
    await expect(
      db`INSERT INTO contacts (canonical_name) VALUES (${null})`
    ).rejects.toThrow();
  });

  it("a contact can be created with only a name — role and organization are optional (gaps, not blockers)", async () => {
    const db = getDb();
    const [contact] = await db<{ id: number; role: string | null; organization_id: number | null }[]>`
      INSERT INTO contacts (canonical_name) VALUES ('Jane Smith') RETURNING id, role, organization_id
    `;
    expect(contact.role).toBeNull();
    expect(contact.organization_id).toBeNull();
  });

  it("two different contacts may share the same canonical_name (ambiguity is a resolution concern, not a schema constraint)", async () => {
    const db = getDb();
    await db`INSERT INTO contacts (canonical_name) VALUES ('Jane Smith')`;
    await db`INSERT INTO contacts (canonical_name) VALUES ('Jane Smith')`;
    const rows = await db`SELECT id FROM contacts WHERE canonical_name = 'Jane Smith'`;
    expect(rows).toHaveLength(2);
  });

  it("deleting an organization sets contacts.organization_id to NULL, not blocking the delete", async () => {
    const db = getDb();
    const [org] = await db<{ id: number }[]>`
      INSERT INTO organizations (name) VALUES ('Acme Corp') RETURNING id
    `;
    const [contact] = await db<{ id: number }[]>`
      INSERT INTO contacts (canonical_name, organization_id) VALUES ('Jane Smith', ${org.id}) RETURNING id
    `;
    await db`DELETE FROM organizations WHERE id = ${org.id}`;
    const [row] = await db<{ organization_id: number | null }[]>`
      SELECT organization_id FROM contacts WHERE id = ${contact.id}
    `;
    expect(row.organization_id).toBeNull();
  });

  it("deleting a contact cascades to its aliases", async () => {
    const db = getDb();
    const [contact] = await db<{ id: number }[]>`
      INSERT INTO contacts (canonical_name) VALUES ('Jane Smith') RETURNING id
    `;
    await db`INSERT INTO contact_aliases (contact_id, alias) VALUES (${contact.id}, 'Jane')`;
    await db`DELETE FROM contacts WHERE id = ${contact.id}`;
    const remaining = await db`SELECT 1 FROM contact_aliases WHERE contact_id = ${contact.id}`;
    expect(remaining).toHaveLength(0);
  });

  it("the same alias string can point at two different contacts (ambiguity must be resolvable, not schema-prevented)", async () => {
    const db = getDb();
    const [a] = await db<{ id: number }[]>`INSERT INTO contacts (canonical_name) VALUES ('Jane Smith (Acme)') RETURNING id`;
    const [b] = await db<{ id: number }[]>`INSERT INTO contacts (canonical_name) VALUES ('Jane Smith (Globex)') RETURNING id`;
    await db`INSERT INTO contact_aliases (contact_id, alias) VALUES (${a.id}, 'Jane')`;
    await db`INSERT INTO contact_aliases (contact_id, alias) VALUES (${b.id}, 'Jane')`;
    const rows = await db`SELECT contact_id FROM contact_aliases WHERE alias = 'Jane'`;
    expect(rows).toHaveLength(2);
  });

  it("a contact cannot have the same alias recorded twice", async () => {
    const db = getDb();
    const [contact] = await db<{ id: number }[]>`
      INSERT INTO contacts (canonical_name) VALUES ('Jane Smith') RETURNING id
    `;
    await db`INSERT INTO contact_aliases (contact_id, alias) VALUES (${contact.id}, 'Jane')`;
    await expect(
      db`INSERT INTO contact_aliases (contact_id, alias) VALUES (${contact.id}, 'Jane')`
    ).rejects.toThrow();
  });

  it("contacts_organization_idx, contacts_canonical_name_idx, and contact_aliases_alias_idx exist", async () => {
    const db = getDb();
    const result = await db<{ indexname: string }[]>`
      SELECT indexname FROM pg_indexes
      WHERE schemaname = 'public'
        AND indexname IN ('contacts_organization_idx', 'contacts_canonical_name_idx', 'contact_aliases_alias_idx')
    `;
    expect(result.map((r) => r.indexname).sort()).toEqual([
      "contact_aliases_alias_idx",
      "contacts_canonical_name_idx",
      "contacts_organization_idx",
    ]);
  });
});
