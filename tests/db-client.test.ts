import { getDb } from "@/lib/db";

describe("getDb()", () => {
  it("connects to Postgres and returns a result for SELECT 1", async () => {
    const db = getDb();
    const result = await db`SELECT 1 AS value`;
    expect(result[0].value).toBe(1);
    await db.end();
  });
});
