import { vi } from "vitest";

const createMock = vi.fn();
vi.mock("@anthropic-ai/sdk", () => ({
  default: vi.fn().mockImplementation(() => ({
    messages: { create: createMock },
  })),
}));

async function* fakeStream(events: unknown[]) {
  for (const e of events) yield e;
}

describe("anthropicAdapter", () => {
  const savedKey = process.env.ANTHROPIC_API_KEY;
  beforeEach(() => {
    process.env.ANTHROPIC_API_KEY = "sk-ant-test";
    createMock.mockReset();
  });
  afterAll(() => {
    if (savedKey === undefined) delete process.env.ANTHROPIC_API_KEY;
    else process.env.ANTHROPIC_API_KEY = savedKey;
  });

  it("normalizes a plain text turn", async () => {
    createMock.mockResolvedValue(
      fakeStream([
        { type: "message_delta", delta: { stop_reason: "end_turn" }, usage: { input_tokens: 5, output_tokens: 3 } },
      ]),
    );
    const { anthropicAdapter } = await import("@/lib/llm/anthropic");
    const events = [];
    for await (const e of anthropicAdapter(
      { model: "claude-sonnet-4-6", messages: [{ role: "system", content: "s" }, { role: "user", content: "hi" }], tools: [] },
    )) {
      events.push(e);
    }
    expect(events).toEqual([
      { type: "turn_end", stopReason: "end", usage: { inputTokens: 5, outputTokens: 3, totalTokens: 8 } },
    ]);
  });

  it("normalizes a tool_use turn with accumulated JSON args", async () => {
    createMock.mockResolvedValue(
      fakeStream([
        { type: "content_block_start", content_block: { type: "tool_use", id: "t1", name: "search_memory" } },
        { type: "content_block_delta", delta: { type: "input_json_delta", partial_json: '{"q":' } },
        { type: "content_block_delta", delta: { type: "input_json_delta", partial_json: '"x"}' } },
        { type: "content_block_stop" },
        { type: "message_delta", delta: { stop_reason: "tool_use" }, usage: { input_tokens: 10, output_tokens: 4 } },
      ]),
    );
    const { anthropicAdapter } = await import("@/lib/llm/anthropic");
    const events = [];
    for await (const e of anthropicAdapter(
      { model: "claude-sonnet-4-6", messages: [{ role: "system", content: "s" }, { role: "user", content: "hi" }], tools: [] },
    )) {
      events.push(e);
    }
    expect(events).toEqual([
      { type: "tool_call_start", id: "t1", name: "search_memory" },
      { type: "tool_call_delta", id: "t1", delta: '{"q":' },
      { type: "tool_call_delta", id: "t1", delta: '"x"}' },
      { type: "tool_call_end", id: "t1", name: "search_memory", input: { q: "x" } },
      { type: "turn_end", stopReason: "tool_use", usage: { inputTokens: 10, outputTokens: 4, totalTokens: 14 } },
    ]);
  });

  it("maps max_tokens stop reason", async () => {
    createMock.mockResolvedValue(
      fakeStream([{ type: "message_delta", delta: { stop_reason: "max_tokens" }, usage: { input_tokens: 1, output_tokens: 1 } }]),
    );
    const { anthropicAdapter } = await import("@/lib/llm/anthropic");
    const events = [];
    for await (const e of anthropicAdapter(
      { model: "claude-sonnet-4-6", messages: [{ role: "system", content: "s" }, { role: "user", content: "hi" }], tools: [] },
    )) events.push(e);
    expect(events.at(-1)).toMatchObject({ type: "turn_end", stopReason: "max_tokens" });
  });

  it("throws synchronously when ANTHROPIC_API_KEY is missing", async () => {
    delete process.env.ANTHROPIC_API_KEY;
    const { anthropicAdapter } = await import("@/lib/llm/anthropic");
    const gen = anthropicAdapter(
      { model: "claude-sonnet-4-6", messages: [{ role: "system", content: "s" }, { role: "user", content: "hi" }], tools: [] },
    );
    await expect(gen.next()).rejects.toThrow(/ANTHROPIC_API_KEY/);
  });

  it("forwards the abort signal to the SDK call", async () => {
    createMock.mockResolvedValue(fakeStream([{ type: "message_delta", delta: { stop_reason: "end_turn" }, usage: { input_tokens: 1, output_tokens: 1 } }]));
    const { anthropicAdapter } = await import("@/lib/llm/anthropic");
    const controller = new AbortController();
    const events = [];
    for await (const e of anthropicAdapter(
      { model: "claude-sonnet-4-6", messages: [{ role: "system", content: "s" }, { role: "user", content: "hi" }], tools: [] },
      controller.signal,
    )) events.push(e);
    expect(createMock).toHaveBeenCalledWith(expect.anything(), expect.objectContaining({ signal: controller.signal }));
  });

  it("converts a multi-turn transcript with tool_use/tool_result and non-empty tools into the wire request", async () => {
    createMock.mockResolvedValue(
      fakeStream([{ type: "message_delta", delta: { stop_reason: "end_turn" }, usage: { input_tokens: 1, output_tokens: 1 } }]),
    );
    const { anthropicAdapter } = await import("@/lib/llm/anthropic");
    const events = [];
    for await (const e of anthropicAdapter({
      model: "claude-sonnet-4-6",
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
    })) events.push(e);

    expect(createMock).toHaveBeenCalledWith(
      expect.objectContaining({
        system: "s",
        tools: [{ name: "search_memory", description: "search", input_schema: { type: "object" } }],
        messages: [
          { role: "user", content: "hi" },
          {
            role: "assistant",
            content: [
              { type: "text", text: "let me check" },
              { type: "tool_use", id: "t1", name: "search_memory", input: { q: "x" } },
            ],
          },
          {
            role: "user",
            content: [{ type: "tool_result", tool_use_id: "t1", content: "[]", is_error: false }],
          },
        ],
      }),
      expect.anything(),
    );
  });
});
