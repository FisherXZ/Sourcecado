import { closeDb, getDb } from "@/lib/db";
import { runMigrations } from "@/lib/migrate";

const { embeddingsCreateMock, openaiCtorMock } = vi.hoisted(() => ({
  embeddingsCreateMock: vi.fn(),
  openaiCtorMock: vi.fn(),
}));
vi.mock("openai", () => ({
  default: class {
    chat = { completions: { create: vi.fn() } };
    embeddings = { create: embeddingsCreateMock };
    constructor(opts: unknown) {
      openaiCtorMock(opts);
    }
  },
}));

const { callModel } = await import("@/lib/model-gateway");

async function resetLedgerTables(): Promise<void> {
  const db = getDb();
  await db`DROP TABLE IF EXISTS tool_calls CASCADE`;
  await db`DROP TABLE IF EXISTS model_calls CASCADE`;
  await db`DROP TABLE IF EXISTS run_steps CASCADE`;
  await db`DROP TABLE IF EXISTS runs CASCADE`;
  await db`DROP TABLE IF EXISTS schema_migrations CASCADE`;
  await runMigrations(db);
}

describe("callModel() — OpenAI embeddings raw SDK path", () => {
  const savedKey = process.env.OPENAI_API_KEY;

  beforeEach(async () => {
    await resetLedgerTables();
    process.env.OPENAI_API_KEY = "sk-openai-test-key";
    embeddingsCreateMock.mockReset();
    openaiCtorMock.mockReset();
  });

  afterEach(async () => {
    if (savedKey === undefined) delete process.env.OPENAI_API_KEY;
    else process.env.OPENAI_API_KEY = savedKey;
    await closeDb();
  });

  it("embed maps data[0].embedding and usage.total_tokens", async () => {
    embeddingsCreateMock.mockResolvedValue({
      data: [{ index: 0, embedding: [0.1, 0.2, 0.3] }],
      usage: { prompt_tokens: 3, total_tokens: 3 },
    });
    const result = await callModel(getDb(), {
      kind: "embed",
      taskName: "t",
      promptVersion: "1",
      value: "hello",
      providerName: "openai",
      model: "text-embedding-3-small",
    });
    expect(result.embedding).toEqual([0.1, 0.2, 0.3]);
    // callModel returns NORMALIZED usage; the provider's {tokens:3} maps to
    // inputTokens+totalTokens (normalizeUsage's embedding-usage branch).
    expect(result.usage).toMatchObject({ inputTokens: 3, totalTokens: 3 });
  });

  it("embed_many re-sorts by index before mapping embeddings", async () => {
    embeddingsCreateMock.mockResolvedValue({
      data: [
        { index: 1, embedding: [0.2] },
        { index: 0, embedding: [0.1] },
      ],
      usage: { prompt_tokens: 2, total_tokens: 2 },
    });
    const result = await callModel(getDb(), {
      kind: "embed_many",
      taskName: "t",
      promptVersion: "1",
      values: ["a", "b"],
      providerName: "openai",
      model: "text-embedding-3-small",
    });
    expect(result.embeddings).toEqual([[0.1], [0.2]]);
  });

  it("requires OPENAI_API_KEY", async () => {
    delete process.env.OPENAI_API_KEY;
    await expect(
      callModel(getDb(), {
        kind: "embed",
        taskName: "t",
        promptVersion: "1",
        value: "hello",
        providerName: "openai",
        model: "text-embedding-3-small",
      }),
    ).rejects.toMatchObject({ code: "missing_config" });
  });
});
