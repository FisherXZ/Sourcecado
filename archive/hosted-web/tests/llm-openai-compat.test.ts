import { vi } from "vitest";

const createMock = vi.fn();
const ctorMock = vi.fn();
vi.mock("openai", () => ({
  default: vi.fn().mockImplementation((opts) => {
    ctorMock(opts);
    return { chat: { completions: { create: createMock } } };
  }),
}));

async function* fakeStream(chunks: unknown[]) {
  for (const c of chunks) yield c;
}

describe("createOpenAiCompatAdapter", () => {
  const savedDeepseek = process.env.DEEPSEEK_API_KEY;
  const savedOpenai = process.env.OPENAI_API_KEY;
  beforeEach(() => {
    process.env.DEEPSEEK_API_KEY = "ds-test";
    process.env.OPENAI_API_KEY = "oa-test";
    createMock.mockReset();
    ctorMock.mockReset();
  });
  afterAll(() => {
    if (savedDeepseek === undefined) delete process.env.DEEPSEEK_API_KEY; else process.env.DEEPSEEK_API_KEY = savedDeepseek;
    if (savedOpenai === undefined) delete process.env.OPENAI_API_KEY; else process.env.OPENAI_API_KEY = savedOpenai;
  });

  it("constructs the deepseek client with the deepseek base URL", async () => {
    createMock.mockResolvedValue(fakeStream([{ choices: [{ delta: {}, finish_reason: "stop" }], usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 } }]));
    const { createOpenAiCompatAdapter } = await import("@/lib/llm/openai-compat");
    const adapter = createOpenAiCompatAdapter("deepseek");
    for await (const _ of adapter({ model: "deepseek-chat", messages: [{ role: "system", content: "s" }], tools: [] })) { /* drain */ }
    expect(ctorMock).toHaveBeenCalledWith(expect.objectContaining({ baseURL: "https://api.deepseek.com" }));
  });

  it("constructs the openai client with no base URL override", async () => {
    createMock.mockResolvedValue(fakeStream([{ choices: [{ delta: {}, finish_reason: "stop" }], usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 } }]));
    const { createOpenAiCompatAdapter } = await import("@/lib/llm/openai-compat");
    const adapter = createOpenAiCompatAdapter("openai");
    for await (const _ of adapter({ model: "gpt-5", messages: [{ role: "system", content: "s" }], tools: [] })) { /* drain */ }
    expect(ctorMock).toHaveBeenCalledWith(expect.not.objectContaining({ baseURL: expect.anything() }));
  });

  it("normalizes a plain text turn", async () => {
    createMock.mockResolvedValue(
      fakeStream([
        { choices: [{ delta: { content: "Hel" }, finish_reason: null }] },
        { choices: [{ delta: { content: "lo" }, finish_reason: "stop" }], usage: { prompt_tokens: 5, completion_tokens: 2, total_tokens: 7 } },
      ]),
    );
    const { createOpenAiCompatAdapter } = await import("@/lib/llm/openai-compat");
    const adapter = createOpenAiCompatAdapter("deepseek");
    const events = [];
    for await (const e of adapter({ model: "deepseek-chat", messages: [{ role: "system", content: "s" }], tools: [] })) events.push(e);
    expect(events).toEqual([
      { type: "text_delta", delta: "Hel" },
      { type: "text_delta", delta: "lo" },
      { type: "turn_end", stopReason: "end", usage: { inputTokens: 5, outputTokens: 2, totalTokens: 7 } },
    ]);
  });

  it("normalizes a single tool call across argument chunks", async () => {
    createMock.mockResolvedValue(
      fakeStream([
        { choices: [{ delta: { tool_calls: [{ index: 0, id: "call_1", function: { name: "search_memory", arguments: "" } }] }, finish_reason: null }] },
        { choices: [{ delta: { tool_calls: [{ index: 0, function: { arguments: '{"q":' } }] }, finish_reason: null }] },
        { choices: [{ delta: { tool_calls: [{ index: 0, function: { arguments: '"x"}' } }] }, finish_reason: "tool_calls" }], usage: { prompt_tokens: 10, completion_tokens: 4, total_tokens: 14 } },
      ]),
    );
    const { createOpenAiCompatAdapter } = await import("@/lib/llm/openai-compat");
    const adapter = createOpenAiCompatAdapter("deepseek");
    const events = [];
    for await (const e of adapter({ model: "deepseek-chat", messages: [{ role: "system", content: "s" }], tools: [] })) events.push(e);
    expect(events).toEqual([
      { type: "tool_call_start", id: "call_1", name: "search_memory" },
      { type: "tool_call_delta", id: "call_1", delta: '{"q":' },
      { type: "tool_call_delta", id: "call_1", delta: '"x"}' },
      { type: "tool_call_end", id: "call_1", name: "search_memory", input: { q: "x" } },
      { type: "turn_end", stopReason: "tool_use", usage: { inputTokens: 10, outputTokens: 4, totalTokens: 14 } },
    ]);
  });

  it("maps length finish_reason to max_tokens", async () => {
    createMock.mockResolvedValue(fakeStream([{ choices: [{ delta: {}, finish_reason: "length" }], usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 } }]));
    const { createOpenAiCompatAdapter } = await import("@/lib/llm/openai-compat");
    const adapter = createOpenAiCompatAdapter("deepseek");
    const events = [];
    for await (const e of adapter({ model: "deepseek-chat", messages: [{ role: "system", content: "s" }], tools: [] })) events.push(e);
    expect(events.at(-1)).toMatchObject({ type: "turn_end", stopReason: "max_tokens" });
  });

  it("does not throw on incomplete tool-argument JSON when a length finish truncates mid-arguments; the turn keeps its real stop reason", async () => {
    createMock.mockResolvedValue(
      fakeStream([
        { choices: [{ delta: { tool_calls: [{ index: 0, id: "call_1", function: { name: "search_memory", arguments: "" } }] }, finish_reason: null }] },
        { choices: [{ delta: { tool_calls: [{ index: 0, function: { arguments: '{"q": "unfini' } }] }, finish_reason: "length" }], usage: { prompt_tokens: 10, completion_tokens: 4, total_tokens: 14 } },
      ]),
    );
    const { createOpenAiCompatAdapter } = await import("@/lib/llm/openai-compat");
    const adapter = createOpenAiCompatAdapter("deepseek");
    const events = [];
    for await (const e of adapter({ model: "deepseek-chat", messages: [{ role: "system", content: "s" }], tools: [] })) events.push(e);
    expect(events).toContainEqual({ type: "tool_call_end", id: "call_1", name: "search_memory", input: {} });
    expect(events.at(-1)).toMatchObject({ type: "turn_end", stopReason: "max_tokens" });
  });

  it("throws synchronously when DEEPSEEK_API_KEY is missing", async () => {
    delete process.env.DEEPSEEK_API_KEY;
    const { createOpenAiCompatAdapter } = await import("@/lib/llm/openai-compat");
    const adapter = createOpenAiCompatAdapter("deepseek");
    const gen = adapter({ model: "deepseek-chat", messages: [{ role: "system", content: "s" }], tools: [] });
    await expect(gen.next()).rejects.toThrow(/DEEPSEEK_API_KEY/);
  });

  it("throws synchronously when OPENAI_API_KEY is missing", async () => {
    delete process.env.OPENAI_API_KEY;
    const { createOpenAiCompatAdapter } = await import("@/lib/llm/openai-compat");
    const adapter = createOpenAiCompatAdapter("openai");
    const gen = adapter({ model: "gpt-5", messages: [{ role: "system", content: "s" }], tools: [] });
    await expect(gen.next()).rejects.toThrow(/OPENAI_API_KEY/);
  });

  it("converts a multi-turn transcript with tool_use/tool_result and non-empty tools into the wire request", async () => {
    createMock.mockResolvedValue(
      fakeStream([{ choices: [{ delta: {}, finish_reason: "stop" }], usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 } }]),
    );
    const { createOpenAiCompatAdapter } = await import("@/lib/llm/openai-compat");
    const adapter = createOpenAiCompatAdapter("deepseek");
    for await (const _ of adapter({
      model: "deepseek-chat",
      messages: [
        { role: "system", content: "s" },
        { role: "user", content: "hi" },
        {
          role: "assistant",
          content: [
            { type: "text", text: "let me check" },
            { type: "tool_use", id: "t1", name: "search_memory", input: { q: "x" } },
          ],
        },
        {
          role: "tool_result",
          content: [{ toolUseId: "t1", toolName: "search_memory", content: "[]", isError: false }],
        },
      ],
      tools: [{ name: "search_memory", description: "search", inputSchema: { type: "object" } }],
    })) { /* drain */ }

    expect(createMock).toHaveBeenCalledWith(
      expect.objectContaining({
        messages: [
          { role: "system", content: "s" },
          { role: "user", content: "hi" },
          {
            role: "assistant",
            content: "let me check",
            tool_calls: [{ id: "t1", type: "function", function: { name: "search_memory", arguments: '{"q":"x"}' } }],
          },
          { role: "tool", tool_call_id: "t1", content: "[]" },
        ],
        tools: [{ type: "function", function: { name: "search_memory", description: "search", parameters: { type: "object" } } }],
      }),
      expect.anything(),
    );
  });
});
