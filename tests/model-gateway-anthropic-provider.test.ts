import { z } from "zod";
import { closeDb, getDb } from "@/lib/db";
import { runMigrations } from "@/lib/migrate";

const { messagesCreateMock, anthropicCtorMock } = vi.hoisted(() => ({
  messagesCreateMock: vi.fn(),
  anthropicCtorMock: vi.fn(),
}));
vi.mock("@anthropic-ai/sdk", () => ({
  default: class {
    messages = { create: messagesCreateMock };
    constructor(opts: unknown) {
      anthropicCtorMock(opts);
    }
  },
}));

// Import after the mock so callModel picks up the mocked constructor.
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

describe("callModel() — Anthropic raw SDK path", () => {
  const savedKey = process.env.ANTHROPIC_API_KEY;

  beforeEach(async () => {
    await resetLedgerTables();
    process.env.ANTHROPIC_API_KEY = "sk-ant-test-key";
    messagesCreateMock.mockReset();
    anthropicCtorMock.mockReset();
  });

  afterEach(async () => {
    if (savedKey === undefined) delete process.env.ANTHROPIC_API_KEY;
    else process.env.ANTHROPIC_API_KEY = savedKey;
    await closeDb();
  });

  it("constructs the client with the bare host (no /v1 suffix)", async () => {
    messagesCreateMock.mockResolvedValue({
      content: [{ type: "text", text: "hi" }],
      usage: { input_tokens: 1, output_tokens: 1 },
    });
    await callModel(getDb(), {
      kind: "generate_text",
      taskName: "t",
      promptVersion: "1",
      prompt: "hi",
      providerName: "anthropic",
      model: "claude-sonnet-4-6",
    });
    expect(anthropicCtorMock).toHaveBeenCalledWith(
      expect.objectContaining({ baseURL: "https://api.anthropic.com" }),
    );
  });

  it("generate_text concatenates text blocks and maps usage", async () => {
    messagesCreateMock.mockResolvedValue({
      content: [{ type: "text", text: "Hello " }, { type: "text", text: "world" }],
      usage: { input_tokens: 10, output_tokens: 5 },
    });
    const result = await callModel(getDb(), {
      kind: "generate_text",
      taskName: "t",
      promptVersion: "1",
      prompt: "hi",
      providerName: "anthropic",
      model: "claude-sonnet-4-6",
    });
    expect(result.text).toBe("Hello world");
    expect(result.usage).toMatchObject({ inputTokens: 10, outputTokens: 5, totalTokens: 15 });
    expect(messagesCreateMock).toHaveBeenCalledWith(
      expect.objectContaining({ max_tokens: 64000, model: "claude-sonnet-4-6" }),
    );
  });

  it("falls back to 4096 max_tokens for a non-sonnet-4 model family", async () => {
    messagesCreateMock.mockResolvedValue({
      content: [{ type: "text", text: "hi" }],
      usage: { input_tokens: 1, output_tokens: 1 },
    });
    await callModel(getDb(), {
      kind: "generate_text",
      taskName: "t",
      promptVersion: "1",
      prompt: "hi",
      providerName: "anthropic",
      model: "claude-haiku-4-5",
    });
    expect(messagesCreateMock).toHaveBeenCalledWith(
      expect.objectContaining({ max_tokens: 4096 }),
    );
  });

  it("generate_object forces a single tool call and extracts its input", async () => {
    messagesCreateMock.mockResolvedValue({
      content: [{ type: "tool_use", id: "toolu_1", name: "emit_object", input: { ok: true } }],
      usage: { input_tokens: 4, output_tokens: 2 },
    });
    const result = await callModel(getDb(), {
      kind: "generate_object",
      taskName: "t",
      promptVersion: "1",
      prompt: "hi",
      schema: z.object({ ok: z.boolean() }),
      providerName: "anthropic",
      model: "claude-sonnet-4-6",
    });
    expect(result.object).toEqual({ ok: true });
    const call = messagesCreateMock.mock.calls[0][0];
    expect(call.tool_choice).toEqual({ type: "tool", name: "emit_object" });
    expect(call.tools).toHaveLength(1);
    expect(call.tools[0].name).toBe("emit_object");
  });

  it("raises schema_error when the tool call's input fails schema validation", async () => {
    messagesCreateMock.mockResolvedValue({
      content: [{ type: "tool_use", id: "toolu_1", name: "emit_object", input: { ok: "not-a-bool" } }],
      usage: { input_tokens: 4, output_tokens: 2 },
    });
    await expect(
      callModel(getDb(), {
        kind: "generate_object",
        taskName: "t",
        promptVersion: "1",
        prompt: "hi",
        schema: z.object({ ok: z.boolean() }),
        providerName: "anthropic",
        model: "claude-sonnet-4-6",
      }),
    ).rejects.toMatchObject({ code: "schema_error" });
  });
});
