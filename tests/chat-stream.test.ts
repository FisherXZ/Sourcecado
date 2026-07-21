import { vi } from "vitest";
import { applyChunk, drainSse, runChat, selectContactCard, type AssistantTurn, type ChatStep } from "@/app/chat/stream";

const empty: AssistantTurn = { steps: [], answer: "" };

describe("drainSse", () => {
  it("parses complete SSE data events into chunks and leaves no remainder", () => {
    const buf =
      'data: {"type":"data-step","id":"step-1","data":{"index":1,"tool":"search_memory","ok":true,"detail":"2 facts, 1 chunk"}}\n\n' +
      'data: {"type":"text-delta","id":"answer","delta":"Hello"}\n\n';
    const { chunks, rest } = drainSse(buf);
    expect(chunks).toHaveLength(2);
    expect(chunks[0]).toMatchObject({ type: "data-step" });
    expect(chunks[1]).toMatchObject({ type: "text-delta", delta: "Hello" });
    expect(rest).toBe("");
  });

  it("buffers a partial trailing event in rest until it completes", () => {
    const { chunks, rest } = drainSse('data: {"type":"text-delta","id":"answer","delta":"Hi"}\n\ndata: {"type":"data-met');
    expect(chunks).toHaveLength(1);
    expect(rest).toContain("data-met");
  });

  it("ignores the [DONE] sentinel and non-JSON lines", () => {
    const { chunks } = drainSse("data: [DONE]\n\n: keep-alive comment\n\n");
    expect(chunks).toHaveLength(0);
  });
});

describe("applyChunk", () => {
  it("appends a new step and updates an existing step by index (reconciliation)", () => {
    let turn = applyChunk(empty, {
      type: "data-step",
      data: { index: 1, tool: "search_memory", ok: true, detail: "loading" },
    });
    expect(turn.steps).toHaveLength(1);
    turn = applyChunk(turn, {
      type: "data-step",
      data: { index: 1, tool: "search_memory", ok: true, detail: "2 facts, 1 chunk" },
    });
    expect(turn.steps).toHaveLength(1); // same index reconciles, not duplicates
    expect(turn.steps[0].detail).toBe("2 facts, 1 chunk");
  });

  it("concatenates text-delta chunks into the answer", () => {
    let turn = applyChunk(empty, { type: "text-delta", delta: "Acme " });
    turn = applyChunk(turn, { type: "text-delta", delta: "Robotics" });
    expect(turn.answer).toBe("Acme Robotics");
  });

  it("sets meta from a data-meta chunk", () => {
    const turn = applyChunk(empty, {
      type: "data-meta",
      data: { runId: 42, status: "succeeded", steps: 1, invalidCitations: [] },
    });
    expect(turn.meta).toMatchObject({ runId: 42, status: "succeeded" });
  });

  it("ignores envelope chunks (start / text-start / text-end / finish)", () => {
    let turn = applyChunk(empty, { type: "start" });
    turn = applyChunk(turn, { type: "text-start", id: "answer" });
    turn = applyChunk(turn, { type: "text-end", id: "answer" });
    turn = applyChunk(turn, { type: "finish" });
    expect(turn).toEqual(empty);
  });

  it("sets pendingTool from a data-tool-pending chunk", () => {
    const turn = applyChunk(empty, { type: "data-tool-pending", data: { tool: "search_memory" } });
    expect(turn.pendingTool).toBe("search_memory");
  });

  it("clears pendingTool once the matching step settles", () => {
    let turn = applyChunk(empty, { type: "data-tool-pending", data: { tool: "search_memory" } });
    turn = applyChunk(turn, {
      type: "data-step",
      data: { index: 1, tool: "search_memory", ok: true, detail: "2 facts, 1 chunk" },
    });
    expect(turn.pendingTool).toBeUndefined();
  });

  it("clears pendingTool once the run's meta lands", () => {
    let turn = applyChunk(empty, { type: "data-tool-pending", data: { tool: "search_memory" } });
    turn = applyChunk(turn, {
      type: "data-meta",
      data: { runId: 1, status: "succeeded", steps: 1, invalidCitations: [] },
    });
    expect(turn.pendingTool).toBeUndefined();
  });
});

function sseResponse(body: string, init: { status?: number } = {}): Response {
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(body));
      controller.close();
    },
  });
  return new Response(stream, { status: init.status ?? 200 });
}

describe("runChat", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("throws on a non-ok response instead of resolving an empty turn", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ error: "question is required" }), { status: 400 })
      )
    );
    await expect(runChat("", [], () => {})).rejects.toThrow(/400/);
  });

  it("resolves the accumulated turn on a 200 SSE response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        sseResponse('data: {"type":"text-delta","id":"answer","delta":"Hi"}\n\n')
      )
    );
    const turn = await runChat("hi", [], () => {});
    expect(turn.answer).toBe("Hi");
  });
});

describe("selectContactCard", () => {
  const contactStep: ChatStep = {
    index: 1,
    tool: "get_contact",
    ok: true,
    detail: "found",
    contactCard: { id: 1, canonicalName: "Jane Smith", role: "PM", organizationName: "Acme" },
  };

  it("returns undefined when no step has a contactCard", () => {
    expect(selectContactCard([{ index: 1, tool: "search_memory", ok: true, detail: "0 facts, 0 chunks" }])).toBeUndefined();
  });

  it("returns the contact alone, with empty arrays, when no history/facts steps are present", () => {
    const view = selectContactCard([contactStep]);
    expect(view).toEqual({
      contact: contactStep.contactCard,
      history: [],
      acceptedFacts: [],
      gapFacts: [],
    });
  });

  it("merges in outreach history and splits memoryFacts into accepted vs gap by status", () => {
    const historyStep: ChatStep = {
      index: 2,
      tool: "list_outreach_history",
      ok: true,
      detail: "1 entry",
      outreachHistory: [{ occurredAt: "2026-01-01T00:00:00.000Z", channel: "email", summary: "Intro", citation: null }],
    };
    const factsStep: ChatStep = {
      index: 3,
      tool: "search_memory",
      ok: true,
      detail: "2 facts, 0 chunks",
      memoryFacts: [
        { subject: "Jane", predicate: "role", object: "PM", citation: "c1", status: "accepted" },
        { subject: "Jane", predicate: "last_contacted", object: "unknown", citation: null, status: "candidate" },
      ],
    };

    const view = selectContactCard([contactStep, historyStep, factsStep]);
    expect(view?.history).toHaveLength(1);
    expect(view?.acceptedFacts).toEqual([
      { subject: "Jane", predicate: "role", object: "PM", citation: "c1", status: "accepted" },
    ]);
    expect(view?.gapFacts).toEqual([
      { subject: "Jane", predicate: "last_contacted", object: "unknown", citation: null, status: "candidate" },
    ]);
  });
});
