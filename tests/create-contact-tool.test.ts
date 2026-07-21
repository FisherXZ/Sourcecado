import { closeDb, getDb } from "@/lib/db";
import { runMigrations } from "@/lib/migrate";
import { createContactTool } from "@/lib/tools/create-contact";

async function resetContactTables(): Promise<void> {
  const db = getDb();
  await db`DROP TABLE IF EXISTS contact_aliases CASCADE`;
  await db`DROP TABLE IF EXISTS contacts CASCADE`;
  await db`DROP TABLE IF EXISTS organizations CASCADE`;
  await db`DELETE FROM schema_migrations WHERE name IN ('006_contacts.sql', '007_outreach_history.sql', '008_contact_details.sql')`;
  await runMigrations(db);
}

describe("createContactTool", () => {
  beforeEach(async () => {
    await resetContactTables();
  });

  afterAll(async () => {
    await closeDb();
  });

  it("is a write_internal-class tool named create_contact", () => {
    expect(createContactTool.name).toBe("create_contact");
    expect(createContactTool.permissionClass).toBe("write_internal");
  });

  it("creates a contact end-to-end through the tool", async () => {
    const db = getDb();
    const result = await createContactTool.execute(
      { name: "Jane Smith", role: "PM", organizationName: "Acme Corp" },
      { db, runId: 0, parentStepId: 0 },
    );
    expect(result.status).toBe("created");
  });

  it("rejects an empty name via the args schema", () => {
    expect(() => createContactTool.argsSchema.parse({ name: "" })).toThrow();
  });

  it("allows name alone, with role and organizationName omitted", () => {
    expect(() => createContactTool.argsSchema.parse({ name: "Jane Smith" })).not.toThrow();
  });

  it("declares phone, email, linkedinUrl, and photoUrl in its args schema (not just pass-through)", () => {
    const shape = (createContactTool.argsSchema as unknown as { shape: Record<string, unknown> }).shape;
    expect(Object.keys(shape).sort()).toEqual([
      "email",
      "linkedinUrl",
      "name",
      "organizationName",
      "phone",
      "photoUrl",
      "role",
    ]);
  });

  it("accepts phone, email, linkedinUrl, and photoUrl and writes them through", async () => {
    const db = getDb();
    const result = await createContactTool.execute(
      {
        name: "Jane Smith",
        phone: "555-0100",
        email: "jane@acme.com",
        linkedinUrl: "https://linkedin.com/in/janesmith",
        photoUrl: "https://example.com/jane.jpg",
      },
      { db, runId: 0, parentStepId: 0 },
    );
    expect(result.status).toBe("created");
    if (result.status === "created") {
      expect(result.contact.phone).toBe("555-0100");
      expect(result.contact.linkedinUrl).toBe("https://linkedin.com/in/janesmith");
    }
  });
});
