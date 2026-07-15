import { vi } from "vitest";

const { runAgentMock, getRunTraceMock } = vi.hoisted(() => ({
  runAgentMock: vi.fn(),
  getRunTraceMock: vi.fn(),
}));
vi.mock("@/lib/harness", () => ({ runAgent: runAgentMock }));
vi.mock("@/lib/ledger", () => ({ getRunTrace: getRunTraceMock }));
vi.mock("@/lib/db", () => ({ getDb: vi.fn().mockReturnValue({}) }));
vi.mock("@/lib/context", () => ({
  buildMemoryAnswerInstructions: vi.fn().mockResolvedValue("stub instructions"),
}));

import { POST } from "@/app/api/agent/stream/route";

function postRequest(body: unknown): Request {
  return new Request("http://localhost/api/agent/stream", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

async function readAll(res: Response): Promise<string> {
  const reader = res.body!.getReader();
  const dec = new TextDecoder();
  let out = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    out += dec.decode(value, { stream: true });
  }
  return out;
}

describe("POST /api/agent/stream", () => {
  beforeEach(() => {
    runAgentMock.mockReset();
    getRunTraceMock.mockResolvedValue(null); // no trace → citation check is a pass-through
  });

  it("returns 400 when question is missing", async () => {
    const res = await POST(postRequest({}));
    expect(res.status).toBe(400);
  });

  it("streams a data-step part per tool step, then the answer text and a meta part", async () => {
    runAgentMock.mockImplementation(async (input: { onStep?: (e: unknown) => unknown }) => {
      await input.onStep?.({
        index: 1,
        tool: "search_memory",
        thought: "looking it up",
        observation: 'Success: {"acceptedFacts":[1,2],"gapFacts":[],"chunks":[1]}',
        ok: true,
      });
      return {
        runId: 42,
        status: "succeeded",
        answer: "Acme Robotics is a Series B company [acme-md#chunk-1].",
        steps: 1,
      };
    });

    const res = await POST(postRequest({ question: "tell me about acme" }));
    expect(res.status).toBe(200);

    const body = await readAll(res);
    // step part carries the tool, the rationale, and a human summary
    expect(body).toContain("data-step");
    expect(body).toContain("search_memory");
    expect(body).toContain("2 facts, 1 chunk");
    // the final answer is streamed as assistant text
    expect(body).toContain("Acme Robotics is a Series B company");
    // meta part carries the run id + status for the trace link
    expect(body).toContain("data-meta");
    expect(body).toContain("42");
  });

  it("still emits a meta part when the run fails with no answer", async () => {
    runAgentMock.mockResolvedValue({ runId: 9, status: "failed", steps: 8 });
    const res = await POST(postRequest({ question: "loop" }));
    expect(res.status).toBe(200); // the stream itself is a 200; failure is carried in meta
    const body = await readAll(res);
    expect(body).toContain("data-meta");
    expect(body).toContain("failed");
  });

  it("streams the answer live token-by-token when search_memory was never called", async () => {
    runAgentMock.mockImplementation(
      async (input: {
        onStep?: (e: unknown) => unknown;
        onAgentLoopEvent?: (e: unknown) => unknown;
      }) => {
        await input.onAgentLoopEvent?.({ type: "llm", event: { type: "text_delta", delta: "Acme is " } });
        await input.onAgentLoopEvent?.({ type: "llm", event: { type: "text_delta", delta: "a Series B co." } });
        return { runId: 7, status: "succeeded", answer: "Acme is a Series B co.", steps: 0 };
      }
    );

    const res = await POST(postRequest({ question: "what is acme" }));
    const body = await readAll(res);

    // Two separate text-delta writes prove live streaming, not one final flush.
    const deltaCount = (body.match(/"type":"text-delta"/g) ?? []).length;
    expect(deltaCount).toBe(2);
    expect(body).toContain("Acme is ");
    expect(body).toContain("a Series B co.");
    expect(body).toContain("data-meta");
  });

  it("buffers text once search_memory is called and flushes the checked answer once at the end", async () => {
    runAgentMock.mockImplementation(
      async (input: {
        onStep?: (e: unknown) => unknown;
        onAgentLoopEvent?: (e: unknown) => unknown;
      }) => {
        await input.onAgentLoopEvent?.({ type: "tool_start", id: "call-1", name: "search_memory", input: {} });
        // Any text after the tool_start must NOT be forwarded live.
        await input.onAgentLoopEvent?.({ type: "llm", event: { type: "text_delta", delta: "should not stream" } });
        await input.onStep?.({
          index: 1,
          tool: "search_memory",
          observation: 'Success: {"acceptedFacts":[],"gapFacts":[],"chunks":[]}',
          ok: true,
        });
        return {
          runId: 8,
          status: "succeeded",
          answer: "Acme Robotics is a Series B company [acme-md#chunk-1].",
          steps: 1,
        };
      }
    );

    const res = await POST(postRequest({ question: "tell me about acme" }));
    const body = await readAll(res);

    expect(body).not.toContain("should not stream");
    // Exactly one text-delta write carries the final, checked answer.
    const deltaLines = body.split("\n").filter((l) => l.includes('"type":"text-delta"'));
    expect(deltaLines).toHaveLength(1);
    expect(body).toContain("Acme Robotics is a Series B company");
    expect(body).toContain("data-tool-pending");
    expect(body).toContain("search_memory");
  });

  it("still streams the answer in one shot when the caller never emits onAgentLoopEvent (backward compatible)", async () => {
    runAgentMock.mockImplementation(async (input: { onStep?: (e: unknown) => unknown }) => {
      await input.onStep?.({
        index: 1,
        tool: "search_memory",
        observation: 'Success: {"acceptedFacts":[1],"gapFacts":[],"chunks":[1]}',
        ok: true,
      });
      return { runId: 9, status: "succeeded", answer: "Answer with no live events.", steps: 1 };
    });

    const res = await POST(postRequest({ question: "tell me about acme" }));
    const body = await readAll(res);
    expect(body).toContain("Answer with no live events.");
  });

  it("accepts that pre-tool narration streams live AND reappears in the step's thought field (Judgment call #3 — documented, not suppressed)", async () => {
    runAgentMock.mockImplementation(
      async (input: {
        onStep?: (e: unknown) => unknown;
        onAgentLoopEvent?: (e: unknown) => unknown;
      }) => {
        await input.onAgentLoopEvent?.({ type: "llm", event: { type: "text_delta", delta: "checking memory..." } });
        await input.onAgentLoopEvent?.({ type: "tool_start", id: "call-1", name: "search_memory", input: {} });
        await input.onStep?.({
          index: 1,
          tool: "search_memory",
          observation: 'Success: {"acceptedFacts":[],"gapFacts":[],"chunks":[]}',
          ok: true,
          thought: "checking memory...",
        });
        return {
          runId: 10,
          status: "succeeded",
          answer: "Acme is a Series B company.",
          steps: 1,
        };
      }
    );

    const res = await POST(postRequest({ question: "tell me about acme" }));
    const body = await readAll(res);

    // The narration streamed live before the tool_start gate closed...
    expect(body).toContain("checking memory...");
    // ...and also reappears verbatim as the step's thought line — accepted duplication, not suppressed.
    expect(body).toContain('"thought":"checking memory..."');
  });
});
