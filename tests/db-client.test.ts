import { closeDb, getDb } from "@/lib/db";

afterEach(async () => {
  await closeDb();
});

describe("getDb()", () => {
  it("connects to Postgres and returns a result for SELECT 1", async () => {
    const db = getDb();
    const result = await db`SELECT 1 AS value`;
    expect(result[0].value).toBe(1);
  });

  it("creates a fresh client after closing the singleton", async () => {
    const first = getDb();
    await first`SELECT 1`;
    await closeDb();

    const second = getDb();
    const result = await second`SELECT 1 AS value`;
    expect(result[0].value).toBe(1);
  });
});
