import { z } from "zod";
import { closeDb, getDb } from "@/lib/db";
import { runMigrations } from "@/lib/migrate";
import { callModel, ModelGatewayError } from "@/lib/model-gateway";

async function resetLedgerTables(): Promise<void> {
  const db = getDb();
  await db`DROP TABLE IF EXISTS tool_calls CASCADE`;
  await db`DROP TABLE IF EXISTS model_calls CASCADE`;
  await db`DROP TABLE IF EXISTS run_steps CASCADE`;
  await db`DROP TABLE IF EXISTS runs CASCADE`;
  await db`DROP TABLE IF EXISTS schema_migrations CASCADE`;
  await runMigrations(db);
}

describe("callModel() generation provider routing", () => {
  const savedProvider = process.env.SOURCECADO_GENERATION_PROVIDER;
  const savedModel = process.env.SOURCECADO_GENERATION_MODEL;
  const savedKey = process.env.ANTHROPIC_API_KEY;

  beforeEach(async () => {
    await resetLedgerTables();
  });

  afterEach(async () => {
    restore("SOURCECADO_GENERATION_PROVIDER", savedProvider);
    restore("SOURCECADO_GENERATION_MODEL", savedModel);
    restore("ANTHROPIC_API_KEY", savedKey);
    await closeDb();
  });

  function restore(name: string, value: string | undefined) {
    if (value === undefined) delete process.env[name];
    else process.env[name] = value;
  }

  it("routes generation to Anthropic and requires ANTHROPIC_API_KEY", async () => {
    process.env.SOURCECADO_GENERATION_PROVIDER = "anthropic";
    delete process.env.SOURCECADO_GENERATION_MODEL;
    delete process.env.ANTHROPIC_API_KEY;

    const error = await callModel(getDb(), {
      kind: "generate_object",
      taskName: "routing_probe",
      promptVersion: "1",
      prompt: "hi",
      schema: z.object({ ok: z.boolean() }),
    }).catch((e) => e);

    expect(error).toBeInstanceOf(ModelGatewayError);
    expect((error as ModelGatewayError).code).toBe("missing_config");
    expect((error as Error).message).toMatch(/ANTHROPIC_API_KEY/);
  });
});
