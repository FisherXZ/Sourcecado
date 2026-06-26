import { applyChunk, drainSse, type AssistantTurn } from "@/app/chat/stream";

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
});
