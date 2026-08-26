import { mkdtemp, mkdir, rm, writeFile } from "fs/promises";
import { tmpdir } from "os";
import { join } from "path";
import { closeDb, getDb } from "@/lib/db";
import { runMigrations } from "@/lib/migrate";

describe("runMigrations()", () => {
  let tempRoot: string | null = null;
  let cwdSpy: ReturnType<typeof vi.spyOn> | null = null;

  afterEach(async () => {
    cwdSpy?.mockRestore();
    cwdSpy = null;
    if (tempRoot) {
      await rm(tempRoot, { recursive: true, force: true });
      tempRoot = null;
    }
    await closeDb();
  });

  async function resetMigrationTables(): Promise<void> {
    const db = getDb();
    await db`DROP TABLE IF EXISTS tool_calls CASCADE`;
    await db`DROP TABLE IF EXISTS model_calls CASCADE`;
    await db`DROP TABLE IF EXISTS run_steps CASCADE`;
    await db`DROP TABLE IF EXISTS runs CASCADE`;
    await db`DROP TABLE IF EXISTS migration_probe CASCADE`;
    await db`DROP TABLE IF EXISTS schema_migrations CASCADE`;
  }

  it("creates the schema_migrations table on a fresh database", async () => {
    await resetMigrationTables();

    const db = getDb();
    await runMigrations(db);

    const rows = await db`
      SELECT table_name FROM information_schema.tables
      WHERE table_schema = 'public' AND table_name = 'schema_migrations'
    `;
    expect(rows.length).toBe(1);
  });

  it("records applied migration files with the schema change", async () => {
    tempRoot = await mkdtemp(join(tmpdir(), "sourcecado-migrations-"));
    const migrationsDir = join(tempRoot, "src", "migrations");
    await mkdir(migrationsDir, { recursive: true });
    await writeFile(
      join(migrationsDir, "001_create_probe.sql"),
      "CREATE TABLE migration_probe (id INTEGER PRIMARY KEY);",
    );
    cwdSpy = vi.spyOn(process, "cwd").mockReturnValue(tempRoot);

    await resetMigrationTables();

    const db = getDb();
    await runMigrations(db);

    const migrationRows = await db`
      SELECT name FROM schema_migrations WHERE name = '001_create_probe.sql'
    `;
    const tableRows = await db`
      SELECT table_name FROM information_schema.tables
      WHERE table_schema = 'public' AND table_name = 'migration_probe'
    `;
    expect(migrationRows.length).toBe(1);
    expect(tableRows.length).toBe(1);
  });

  it("creates run ledger and model gateway tables", async () => {
    await resetMigrationTables();

    const db = getDb();
    await runMigrations(db);

    const rows = await db`
      SELECT table_name FROM information_schema.tables
      WHERE table_schema = 'public'
        AND table_name IN ('runs', 'run_steps', 'model_calls', 'tool_calls')
      ORDER BY table_name
    `;
    expect(rows.map((row) => row.table_name)).toEqual([
      "model_calls",
      "run_steps",
      "runs",
      "tool_calls",
    ]);
  });

  it("rejects invalid run ledger statuses and step kinds", async () => {
    await resetMigrationTables();

    const db = getDb();
    await runMigrations(db);
    const [run] = await db`
      INSERT INTO runs (run_type, title, status)
      VALUES ('test', 'Constraint test', 'running')
      RETURNING id
    `;

    await expect(db`
      INSERT INTO runs (run_type, status)
      VALUES ('test', 'done')
    `).rejects.toThrow();
    await expect(db`
      INSERT INTO run_steps (run_id, step_kind, name, status)
      VALUES (${run.id}, 'unknown', 'Bad kind', 'running')
    `).rejects.toThrow();
    await expect(db`
      INSERT INTO run_steps (run_id, step_kind, name, status)
      VALUES (${run.id}, 'model', 'Bad status', 'done')
    `).rejects.toThrow();
  });

  it("rejects orphan model and tool call run steps", async () => {
    await resetMigrationTables();

    const db = getDb();
    await runMigrations(db);
    const [run] = await db`
      INSERT INTO runs (run_type, title, status)
      VALUES ('test', 'Foreign key test', 'running')
      RETURNING id
    `;

    await expect(db`
      INSERT INTO model_calls (
        run_id,
        run_step_id,
        task_name,
        prompt_version,
        prompt_hash,
        provider,
        model,
        call_kind,
        status
      )
      VALUES (${run.id}, 999999, 'probe', '1', 'hash', 'test', 'test-model', 'generate_text', 'running')
    `).rejects.toThrow();
    await expect(db`
      INSERT INTO tool_calls (run_id, run_step_id, tool_name, status)
      VALUES (${run.id}, 999999, 'probe_tool', 'running')
    `).rejects.toThrow();
  });

  it("deletes raw model and tool payload rows when a run is deleted", async () => {
    await resetMigrationTables();

    const db = getDb();
    await runMigrations(db);
    const [run] = await db`
      INSERT INTO runs (run_type, title, status)
      VALUES ('test', 'Cascade delete test', 'running')
      RETURNING id
    `;
    const [step] = await db`
      INSERT INTO run_steps (run_id, step_kind, name, status)
      VALUES (${run.id}, 'model', 'capture_payloads', 'running')
      RETURNING id
    `;
    await db`
      INSERT INTO model_calls (
        run_id,
        run_step_id,
        task_name,
        prompt_version,
        prompt_hash,
        provider,
        model,
        call_kind,
        status,
        request_json,
        response_json
      )
      VALUES (
        ${run.id},
        ${step.id},
        'probe',
        '1',
        'hash',
        'test',
        'test-model',
        'generate_text',
        'succeeded',
        ${db.json({ prompt: "private" })},
        ${db.json({ text: "private" })}
      )
    `;
    await db`
      INSERT INTO tool_calls (
        run_id,
        run_step_id,
        tool_name,
        status,
        arguments_json,
        result_json
      )
      VALUES (
        ${run.id},
        ${step.id},
        'probe_tool',
        'succeeded',
        ${db.json({ query: "private" })},
        ${db.json({ result: "private" })}
      )
    `;

    await db`DELETE FROM runs WHERE id = ${run.id}`;

    const [modelCount] = await db`SELECT COUNT(*)::int AS count FROM model_calls`;
    const [toolCount] = await db`SELECT COUNT(*)::int AS count FROM tool_calls`;
    expect(modelCount.count).toBe(0);
    expect(toolCount.count).toBe(0);
  });
});
