import postgres from "postgres";

let _db: postgres.Sql | null = null;

export function getDb(): postgres.Sql {
  if (!_db) {
    const url = process.env.DATABASE_URL;
    if (!url) throw new Error("DATABASE_URL is not set");
    _db = postgres(url, { onnotice: () => {} });
  }
  return _db;
}

export async function closeDb(): Promise<void> {
  if (!_db) return;

  await _db.end();
  _db = null;
}
