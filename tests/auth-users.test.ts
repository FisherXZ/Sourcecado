import { closeDb, getDb } from "@/lib/db";
import { upsertUser } from "@/lib/auth/users";
import { startRun } from "@/lib/ledger";
import { runMigrations } from "@/lib/migrate";

async function resetAuthTables(): Promise<void> {
  const db = getDb();
  await db`DROP TABLE IF EXISTS tool_calls CASCADE`;
  await db`DROP TABLE IF EXISTS model_calls CASCADE`;
  await db`DROP TABLE IF EXISTS run_steps CASCADE`;
  await db`DROP TABLE IF EXISTS runs CASCADE`;
  await db`DROP TABLE IF EXISTS users CASCADE`;
  await db`DROP TABLE IF EXISTS schema_migrations CASCADE`;
  await runMigrations(db);
}

describe("upsertUser", () => {
  beforeEach(async () => {
    await resetAuthTables();
  });

  afterAll(async () => {
    await closeDb();
  });

  it("creates a user on first sign-in", async () => {
    const user = await upsertUser(getDb(), {
      googleSub: "google-sub-1",
      email: "director@codeology.org",
      name: "A Director",
      imageUrl: "https://example.test/a.png",
    });

    expect(user.id).toBeGreaterThan(0);
    expect(user.googleSub).toBe("google-sub-1");
    expect(user.email).toBe("director@codeology.org");
  });

  it("returns the same id on repeat sign-in rather than creating a second user", async () => {
    const db = getDb();
    const first = await upsertUser(db, { googleSub: "google-sub-1", email: "director@codeology.org" });
    const second = await upsertUser(db, { googleSub: "google-sub-1", email: "director@codeology.org" });

    expect(second.id).toBe(first.id);
    const rows = await db`SELECT count(*)::int AS n FROM users`;
    expect(rows[0].n).toBe(1);
  });

  // The whole reason google_sub is the conflict key rather than email: a
  // director whose Workspace address changes must keep their user id, or every
  // run and memory grant attributed to them is orphaned.
  it("keeps the same id when the email changes but the Google subject does not", async () => {
    const db = getDb();
    const before = await upsertUser(db, { googleSub: "google-sub-1", email: "old@codeology.org" });
    const after = await upsertUser(db, { googleSub: "google-sub-1", email: "new@codeology.org" });

    expect(after.id).toBe(before.id);
    expect(after.email).toBe("new@codeology.org");
  });

  it("treats a different Google subject as a different user", async () => {
    const db = getDb();
    const a = await upsertUser(db, { googleSub: "google-sub-1", email: "a@codeology.org" });
    const b = await upsertUser(db, { googleSub: "google-sub-2", email: "b@codeology.org" });

    expect(b.id).not.toBe(a.id);
  });
});

describe("run attribution", () => {
  beforeEach(async () => {
    await resetAuthTables();
  });

  it("stamps the acting user onto the run", async () => {
    const db = getDb();
    const user = await upsertUser(db, { googleSub: "google-sub-1", email: "director@codeology.org" });
    const actor = { actorType: "user" as const, actorId: String(user.id) };

    const run = await startRun(db, { runType: "agent_chat", title: "who should I work next?", actor });

    expect(run.actor).toEqual(actor);
    const [row] = await db`SELECT actor_type, actor_id FROM runs WHERE id = ${run.id}`;
    expect(row.actor_type).toBe("user");
    expect(row.actor_id).toBe(String(user.id));
  });

  // CLI ingest and maintenance runs have no signed-in user. They record as
  // unattributed rather than borrowing an identity.
  it("leaves the run unattributed when no actor is supplied", async () => {
    const run = await startRun(getDb(), { runType: "ingest", title: "cli ingest" });

    expect(run.actor).toBeNull();
    const [row] = await getDb()`SELECT actor_type, actor_id FROM runs WHERE id = ${run.id}`;
    expect(row.actor_type).toBeNull();
    expect(row.actor_id).toBeNull();
  });
});
