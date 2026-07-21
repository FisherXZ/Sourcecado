import { closeDb, getDb } from "@/lib/db";
import { runMigrations } from "@/lib/migrate";

async function resetContactTables(): Promise<void> {
  const db = getDb();
  await db`DROP TABLE IF EXISTS outreach_history CASCADE`;
  await db`DROP TABLE IF EXISTS contact_aliases CASCADE`;
  await db`DROP TABLE IF EXISTS contacts CASCADE`;
  await db`DROP TABLE IF EXISTS organizations CASCADE`;
  await db`DELETE FROM schema_migrations WHERE name IN ('006_contacts.sql', '007_outreach_history.sql', '008_contact_details.sql')`;
  await runMigrations(db);
}

describe("008 contact_details migration", () => {
  beforeEach(async () => {
    await resetContactTables();
  });

  afterAll(async () => {
    await closeDb();
  });

  it("adds phone, email, linkedin_url, and photo_url to contacts, all optional", async () => {
    const db = getDb();
    const [row] = await db<{
      phone: string | null;
      email: string | null;
      linkedin_url: string | null;
      photo_url: string | null;
    }[]>`
      INSERT INTO contacts (canonical_name) VALUES ('Jane Smith')
      RETURNING phone, email, linkedin_url, photo_url
    `;
    expect(row.phone).toBeNull();
    expect(row.email).toBeNull();
    expect(row.linkedin_url).toBeNull();
    expect(row.photo_url).toBeNull();
  });

  it("stores all four fields when provided", async () => {
    const db = getDb();
    const [row] = await db<{
      phone: string | null;
      email: string | null;
      linkedin_url: string | null;
      photo_url: string | null;
    }[]>`
      INSERT INTO contacts (canonical_name, phone, email, linkedin_url, photo_url)
      VALUES ('Jane Smith', '555-0100', 'jane@acme.com', 'https://linkedin.com/in/janesmith', 'https://example.com/jane.jpg')
      RETURNING phone, email, linkedin_url, photo_url
    `;
    expect(row.phone).toBe("555-0100");
    expect(row.email).toBe("jane@acme.com");
    expect(row.linkedin_url).toBe("https://linkedin.com/in/janesmith");
    expect(row.photo_url).toBe("https://example.com/jane.jpg");
  });
});
