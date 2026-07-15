import { vi } from "vitest";

const { runAgentMock, getRunTraceMock, getOrCreateLatestSessionMock, loadSessionMessagesMock, appendMessagesMock } =
  vi.hoisted(() => ({
    runAgentMock: vi.fn(),
    getRunTraceMock: vi.fn(),
    getOrCreateLatestSessionMock: vi.fn(),
    loadSessionMessagesMock: vi.fn(),
    appendMessagesMock: vi.fn(),
  }));
vi.mock("@/lib/harness", () => ({
  runAgent: runAgentMock,
}));
vi.mock("@/lib/ledger", () => ({ getRunTrace: getRunTraceMock }));
vi.mock("@/lib/db", () => ({ getDb: vi.fn().mockReturnValue({}) }));
vi.mock("@/lib/context", () => ({
  buildMemoryAnswerInstructions: vi.fn().mockResolvedValue("stub instructions"),
}));
vi.mock("@/lib/chat/sessions", () => ({
  getOrCreateLatestSession: getOrCreateLatestSessionMock,
  loadSessionMessages: loadSessionMessagesMock,
  appendMessages: appendMessagesMock,
}));

import { POST } from "@/app/api/agent/stream/route";
import { DEFAULT_ACTOR } from "@/lib/memory/actor";

// Minimal transcript shape (system + user + one produced assistant message)
// for tests that don't care about the exact messages[] content — just that
// `result.messages` exists so the route's persist-after-settle slice doesn't
// crash on `.slice()` of `undefined`.
const STUB_MESSAGES = [
  { role: "system", content: "stub instructions" },
  { role: "user", content: "stub question" },
  { role: "assistant", content: [{ type: "text", text: "stub" }] },
];

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
    getOrCreateLatestSessionMock.mockReset().mockResolvedValue({ id: 7 });
    loadSessionMessagesMock.mockReset().mockResolvedValue([]);
    appendMessagesMock.mockReset().mockResolvedValue(undefined);
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
        messages: STUB_MESSAGES,
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
    runAgentMock.mockResolvedValue({ runId: 9, status: "failed", steps: 8, messages: STUB_MESSAGES });
    const res = await POST(postRequest({ question: "loop" }));
    expect(res.status).toBe(200); // the stream itself is a 200; failure is carried in meta
    const body = await readAll(res);
    expect(body).toContain("data-meta");
    expect(body).toContain("failed");
  });

  it("closes the open answer text part when the run fails after narration streamed and a search fired", async () => {
    // Regression for the mixed live-then-search failure path: pre-search
    // narration opens the "answer" text part (answerStarted), search_memory then
    // gates further streaming, and the run fails with no result.answer. Neither
    // the answerEnd nor the answerFlush branch used to run, so text-start was
    // never matched by text-end and the SSE part was left open. The stream must
    // emit exactly one text-end for the narration part, with no phantom flush.
    runAgentMock.mockImplementation(
      async (input: {
        onStep?: (e: unknown) => unknown;
        onAgentLoopEvent?: (e: unknown) => unknown;
      }) => {
        await input.onAgentLoopEvent?.({ type: "llm", event: { type: "text_delta", delta: "checking memory..." } });
        await input.onAgentLoopEvent?.({ type: "tool_start", id: "call-1", name: "search_memory", input: {} });
        return { runId: 12, status: "failed", steps: 8, messages: STUB_MESSAGES };
      }
    );

    const res = await POST(postRequest({ question: "loop after narration" }));
    const body = await readAll(res);

    expect(body).toContain("checking memory...");
    // The open answer part is closed exactly once, and no answer was flushed.
    const endCount = (body.match(/"type":"text-end"/g) ?? []).length;
    expect(endCount).toBe(1);
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
        return { runId: 7, status: "succeeded", answer: "Acme is a Series B co.", steps: 0, messages: STUB_MESSAGES };
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
          messages: STUB_MESSAGES,
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
      return { runId: 9, status: "succeeded", answer: "Answer with no live events.", steps: 1, messages: STUB_MESSAGES };
    });

    const res = await POST(postRequest({ question: "tell me about acme" }));
    const body = await readAll(res);
    expect(body).toContain("Answer with no live events.");
  });

  it("forwards the request's abort signal into runAgent so a client disconnect terminates the loop", async () => {
    // The AI SDK's UI-message-stream swallows write-after-cancel (safeEnqueue),
    // so a disconnected client never makes writer.write throw — the only thing
    // that actually stops the background loop is the request's AbortSignal,
    // checked between steps and passed to the provider fetch. Guard that it is
    // threaded end-to-end; drop it and runs keep burning credits after a leave.
    let received: AbortSignal | undefined;
    runAgentMock.mockImplementation(async (input: { signal?: AbortSignal }) => {
      received = input.signal;
      return { runId: 11, status: "succeeded", answer: "ok", steps: 0, messages: STUB_MESSAGES };
    });

    const req = postRequest({ question: "hi" });
    await readAll(await POST(req));

    expect(received).toBeInstanceOf(AbortSignal);
    expect(received).toBe(req.signal);
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
          messages: STUB_MESSAGES,
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

  describe("session persistence (R6)", () => {
    it("loads prior session messages before running the turn and threads them in as priorMessages", async () => {
      const prior = [
        { role: "user", content: "earlier question" },
        { role: "assistant", content: [{ type: "text", text: "earlier answer" }] },
      ];
      loadSessionMessagesMock.mockResolvedValue(prior);
      runAgentMock.mockResolvedValue({
        runId: 50,
        status: "succeeded",
        answer: "ok",
        steps: 0,
        messages: [{ role: "system", content: "stub" }, ...prior, { role: "user", content: "follow-up question" }],
      });

      const res = await POST(postRequest({ question: "follow-up question" }));
      await readAll(res);

      expect(getOrCreateLatestSessionMock).toHaveBeenCalledWith(expect.anything(), DEFAULT_ACTOR);
      expect(loadSessionMessagesMock).toHaveBeenCalledWith(expect.anything(), 7);
      expect(runAgentMock).toHaveBeenCalledWith(expect.objectContaining({ priorMessages: prior }));
    });

    it("persists the user message immediately and the produced messages after the turn, tagged with the run id", async () => {
      runAgentMock.mockResolvedValue({
        runId: 51,
        status: "succeeded",
        answer: "ok",
        steps: 0,
        messages: STUB_MESSAGES,
      });

      const res = await POST(postRequest({ question: "hello there" }));
      await readAll(res);

      expect(appendMessagesMock).toHaveBeenCalledTimes(2);
      // first call: the new user message, persisted immediately, no runId
      expect(appendMessagesMock.mock.calls[0][1]).toBe(7);
      expect(appendMessagesMock.mock.calls[0][2]).toEqual([{ role: "user", content: "hello there" }]);
      expect(appendMessagesMock.mock.calls[0][3]).toBeUndefined();
      // second call: the loop's produced messages, tagged with the run id
      expect(appendMessagesMock.mock.calls[1][1]).toBe(7);
      expect(appendMessagesMock.mock.calls[1][3]).toBe(51);
    });

    it("still persists the turn's messages when the loop fails", async () => {
      runAgentMock.mockResolvedValue({
        runId: 52,
        status: "failed",
        steps: 8,
        messages: STUB_MESSAGES,
      });

      const res = await POST(postRequest({ question: "loop" }));
      await readAll(res);

      expect(appendMessagesMock).toHaveBeenCalledTimes(2); // user message + failure message, same as success path
      expect(appendMessagesMock.mock.calls[1][3]).toBe(52);
    });

    it("does not double-feed client-sent history: the persisted session's priorMessages supersede it, and runAgent receives history undefined", async () => {
      const prior = [
        { role: "user", content: "earlier question" },
        { role: "assistant", content: [{ type: "text", text: "earlier answer" }] },
      ];
      loadSessionMessagesMock.mockResolvedValue(prior);
      runAgentMock.mockResolvedValue({
        runId: 61,
        status: "succeeded",
        answer: "ok",
        steps: 0,
        messages: [
          { role: "system", content: "stub" },
          ...prior,
          { role: "user", content: "new question" },
          { role: "assistant", content: [{ type: "text", text: "ok" }] },
        ],
      });

      const res = await POST(
        postRequest({
          question: "new question",
          history: [{ role: "user", content: "client-sent history turn" }],
        })
      );
      await readAll(res);

      expect(runAgentMock).toHaveBeenCalledWith(
        expect.objectContaining({ history: undefined, priorMessages: prior })
      );
    });

    it("persists the citation-checked answer text, not the raw pre-check text, on the final assistant message (tool_use blocks preserved)", async () => {
      const rawAnswer = "Acme is here [bad-doc#chunk-9].";
      const checkedAnswer = "Acme is here [[unverified citation removed]].";
      const toolUseBlock = { type: "tool_use", id: "call-1", name: "noop", input: {} };
      runAgentMock.mockResolvedValue({
        runId: 62,
        status: "succeeded",
        answer: rawAnswer,
        steps: 1,
        messages: [
          { role: "system", content: "stub" },
          { role: "user", content: "tell me about acme" },
          { role: "assistant", content: [{ type: "text", text: rawAnswer }, toolUseBlock] },
        ],
      });
      getRunTraceMock.mockResolvedValue({
        id: 62,
        steps: [
          {
            id: 1,
            children: [],
            toolCalls: [
              {
                toolName: "search_memory",
                status: "succeeded",
                result: { acceptedFacts: [], gapFacts: [], chunks: [{ citation: "good-doc#chunk-1" }] },
              },
            ],
          },
        ],
      });

      const res = await POST(postRequest({ question: "tell me about acme" }));
      await readAll(res);

      expect(appendMessagesMock.mock.calls[1][2]).toEqual([
        { role: "assistant", content: [{ type: "text", text: checkedAnswer }, toolUseBlock] },
      ]);
    });

    it("persists exactly the loop's produced messages for a clean success turn (no system prompt, no dropped assistant reply)", async () => {
      runAgentMock.mockResolvedValue({
        runId: 63,
        status: "succeeded",
        answer: "hi there",
        steps: 0,
        messages: [
          { role: "system", content: "stub instructions" },
          { role: "user", content: "hello there" },
          { role: "assistant", content: [{ type: "text", text: "hi there" }] },
        ],
      });

      const res = await POST(postRequest({ question: "hello there" }));
      await readAll(res);

      expect(appendMessagesMock.mock.calls[1][2]).toEqual([
        { role: "assistant", content: [{ type: "text", text: "hi there" }] },
      ]);
      expect(appendMessagesMock.mock.calls[1][1]).toBe(7);
      expect(appendMessagesMock.mock.calls[1][3]).toBe(63);
    });
  });
});
