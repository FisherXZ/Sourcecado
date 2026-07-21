import { z } from "zod";
import { closeDb, getDb } from "@/lib/db";
import { runMigrations } from "@/lib/migrate";

const { chatCreateMock, openaiCtorMock } = vi.hoisted(() => ({
  chatCreateMock: vi.fn(),
  openaiCtorMock: vi.fn(),
}));
vi.mock("openai", () => ({
  default: class {
    chat = { completions: { create: chatCreateMock } };
    embeddings = { create: vi.fn() };
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

describe("callModel() — DeepSeek raw SDK path", () => {
  const savedKey = process.env.DEEPSEEK_API_KEY;

  beforeEach(async () => {
    await resetLedgerTables();
    process.env.DEEPSEEK_API_KEY = "sk-deepseek-test-key";
    chatCreateMock.mockReset();
    openaiCtorMock.mockReset();
  });

  afterEach(async () => {
    if (savedKey === undefined) delete process.env.DEEPSEEK_API_KEY;
    else process.env.DEEPSEEK_API_KEY = savedKey;
    await closeDb();
  });

  it("constructs the client pointed at DeepSeek's base URL", async () => {
    chatCreateMock.mockResolvedValue({
      choices: [{ message: { content: "hi" } }],
      usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 },
    });
    await callModel(getDb(), {
      kind: "generate_text",
      taskName: "t",
      promptVersion: "1",
      prompt: "hi",
      providerName: "deepseek",
      model: "deepseek-chat",
    });
    expect(openaiCtorMock).toHaveBeenCalledWith(
      expect.objectContaining({ baseURL: "https://api.deepseek.com" }),
    );
  });

  it("generate_text maps choices[0].message.content and usage", async () => {
    chatCreateMock.mockResolvedValue({
      choices: [{ message: { content: "Hello there" } }],
      usage: { prompt_tokens: 10, completion_tokens: 5, total_tokens: 15 },
    });
    const result = await callModel(getDb(), {
      kind: "generate_text",
      taskName: "t",
      promptVersion: "1",
      prompt: "hi",
      providerName: "deepseek",
      model: "deepseek-chat",
    });
    expect(result.text).toBe("Hello there");
    expect(result.usage).toMatchObject({ inputTokens: 10, outputTokens: 5, totalTokens: 15 });
  });

  it("generate_object sends response_format json_object and parses the result", async () => {
    chatCreateMock.mockResolvedValue({
      choices: [{ message: { content: JSON.stringify({ candidates: ["Ada"] }) } }],
      usage: { prompt_tokens: 4, completion_tokens: 2, total_tokens: 6 },
    });
    const result = await callModel(getDb(), {
      kind: "generate_object",
      taskName: "t",
      promptVersion: "1",
      prompt: "extract",
      schema: z.object({ candidates: z.array(z.string()) }),
      providerName: "deepseek",
      model: "deepseek-chat",
    });
    expect(result.object).toEqual({ candidates: ["Ada"] });
    const call = chatCreateMock.mock.calls[0][0];
    expect(call.response_format).toEqual({ type: "json_object" });
  });

  it("raises invalid_output when the model returns malformed JSON", async () => {
    chatCreateMock.mockResolvedValue({
      choices: [{ message: { content: "not json" } }],
      usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 },
    });
    await expect(
      callModel(getDb(), {
        kind: "generate_object",
        taskName: "t",
        promptVersion: "1",
        prompt: "extract",
        schema: z.object({ candidates: z.array(z.string()) }),
        providerName: "deepseek",
        model: "deepseek-chat",
      }),
    ).rejects.toMatchObject({ code: "invalid_output" });
  });
});
