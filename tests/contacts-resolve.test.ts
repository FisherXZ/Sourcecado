import { closeDb, getDb } from "@/lib/db";
import { runMigrations } from "@/lib/migrate";
import { resolveContact, resolveOrganization } from "@/lib/contacts/resolve";

async function resetContactTables(): Promise<void> {
  const db = getDb();
  await db`DROP TABLE IF EXISTS contact_aliases CASCADE`;
  await db`DROP TABLE IF EXISTS contacts CASCADE`;
  await db`DROP TABLE IF EXISTS organizations CASCADE`;
  await db`DELETE FROM schema_migrations WHERE name IN ('006_contacts.sql', '007_outreach_history.sql', '008_contact_details.sql')`;
  await runMigrations(db);
}

describe("resolveContact", () => {
  beforeEach(async () => {
    await resetContactTables();
  });

  afterAll(async () => {
    await closeDb();
  });

  it("returns not_found when no contact matches", async () => {
    const result = await resolveContact(getDb(), "Nobody Here");
    expect(result).toEqual({ status: "not_found" });
  });

  it("resolves an exact canonical-name match, case-insensitively, with organization info", async () => {
    const db = getDb();
    const [org] = await db<{ id: number }[]>`INSERT INTO organizations (name) VALUES ('Acme Corp') RETURNING id`;
    await db`INSERT INTO contacts (canonical_name, role, organization_id) VALUES ('Jane Smith', 'Engineering Manager', ${org.id})`;

    const result = await resolveContact(db, "jane smith");
    expect(result.status).toBe("found");
    if (result.status === "found") {
      expect(result.contact.canonicalName).toBe("Jane Smith");
      expect(result.contact.role).toBe("Engineering Manager");
      expect(result.contact.organizationName).toBe("Acme Corp");
    }
  });

  it("resolves a contact with no role/org — nulls, not an error", async () => {
    const db = getDb();
    await db`INSERT INTO contacts (canonical_name) VALUES ('Thin Record')`;
    const result = await resolveContact(db, "Thin Record");
    expect(result.status).toBe("found");
    if (result.status === "found") {
      expect(result.contact.role).toBeNull();
      expect(result.contact.organizationId).toBeNull();
      expect(result.contact.organizationName).toBeNull();
    }
  });

  it("resolves via an exact alias match", async () => {
    const db = getDb();
    const [contact] = await db<{ id: number }[]>`INSERT INTO contacts (canonical_name) VALUES ('Jane Smith') RETURNING id`;
    await db`INSERT INTO contact_aliases (contact_id, alias) VALUES (${contact.id}, 'Jane')`;

    const result = await resolveContact(db, "jane");
    expect(result.status).toBe("found");
    if (result.status === "found") {
      expect(result.contact.id).toBe(Number(contact.id));
    }
  });

  it("is ambiguous when two contacts share the exact same canonical name", async () => {
    const db = getDb();
    await db`INSERT INTO contacts (canonical_name) VALUES ('Jane Smith')`;
    await db`INSERT INTO contacts (canonical_name) VALUES ('Jane Smith')`;

    const result = await resolveContact(db, "Jane Smith");
    expect(result.status).toBe("ambiguous");
    if (result.status === "ambiguous") {
      expect(result.candidates).toHaveLength(2);
    }
  });

  it("is ambiguous when the same alias points at two different contacts, and never silently picks one", async () => {
    const db = getDb();
    const [acme] = await db<{ id: number }[]>`INSERT INTO organizations (name) VALUES ('Acme Corp') RETURNING id`;
    const [globex] = await db<{ id: number }[]>`INSERT INTO organizations (name) VALUES ('Globex') RETURNING id`;
    const [a] = await db<{ id: number }[]>`
      INSERT INTO contacts (canonical_name, role, organization_id) VALUES ('Jane Smith', 'PM', ${acme.id}) RETURNING id
    `;
    const [b] = await db<{ id: number }[]>`
      INSERT INTO contacts (canonical_name, role, organization_id) VALUES ('Jane R. Smith', 'Engineer', ${globex.id}) RETURNING id
    `;
    await db`INSERT INTO contact_aliases (contact_id, alias) VALUES (${a.id}, 'Jane')`;
    await db`INSERT INTO contact_aliases (contact_id, alias) VALUES (${b.id}, 'Jane')`;

    const result = await resolveContact(db, "Jane");
    expect(result.status).toBe("ambiguous");
    if (result.status === "ambiguous") {
      const orgNames = result.candidates.map((c) => c.organizationName).sort();
      expect(orgNames).toEqual(["Acme Corp", "Globex"]);
    }
  });

  it("does not fuzzy-match a near-miss with no exact alias or canonical match", async () => {
    const db = getDb();
    await db`INSERT INTO contacts (canonical_name) VALUES ('Jane Smith')`;
    const result = await resolveContact(db, "Jayne Smyth");
    expect(result).toEqual({ status: "not_found" });
  });
});

describe("resolveOrganization", () => {
  beforeEach(async () => {
    await resetContactTables();
  });

  afterAll(async () => {
    await closeDb();
  });

  it("returns not_found when no organization matches", async () => {
    const result = await resolveOrganization(getDb(), "Nonexistent Inc");
    expect(result).toEqual({ status: "not_found" });
  });

  it("resolves an exact name match, case-insensitively, with its known contacts", async () => {
    const db = getDb();
    const [org] = await db<{ id: number }[]>`INSERT INTO organizations (name, domain) VALUES ('Acme Corp', 'acme.com') RETURNING id`;
    await db`INSERT INTO contacts (canonical_name, role, organization_id) VALUES ('Jane Smith', 'PM', ${org.id})`;
    await db`INSERT INTO contacts (canonical_name, organization_id) VALUES ('Bob Jones', ${org.id})`;
    // A contact at a different org must not leak into these results.
    const [other] = await db<{ id: number }[]>`INSERT INTO organizations (name) VALUES ('Globex') RETURNING id`;
    await db`INSERT INTO contacts (canonical_name, organization_id) VALUES ('Someone Else', ${other.id})`;

    const result = await resolveOrganization(db, "acme corp");
    expect(result.status).toBe("found");
    if (result.status === "found") {
      expect(result.organization.domain).toBe("acme.com");
      expect(result.contacts.map((c) => c.canonicalName).sort()).toEqual(["Bob Jones", "Jane Smith"]);
    }
  });

  it("is ambiguous when two organizations share the exact same name", async () => {
    const db = getDb();
    await db`INSERT INTO organizations (name) VALUES ('Acme Corp')`;
    await db`INSERT INTO organizations (name) VALUES ('Acme Corp')`;

    const result = await resolveOrganization(db, "Acme Corp");
    expect(result.status).toBe("ambiguous");
    if (result.status === "ambiguous") {
      expect(result.candidates).toHaveLength(2);
    }
  });
});
