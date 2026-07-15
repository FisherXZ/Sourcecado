import { vi } from "vitest";

const writes: unknown[] = [];
const { createUIMessageStreamMock, createUIMessageStreamResponseMock } = vi.hoisted(() => ({
  createUIMessageStreamMock: vi.fn(),
  createUIMessageStreamResponseMock: vi.fn(() => new Response(null)),
}));
vi.mock("ai", () => ({
  createUIMessageStream: createUIMessageStreamMock,
  createUIMessageStreamResponse: createUIMessageStreamResponseMock,
}));

import { streamAgentResponse, type AgentStreamWriter } from "@/lib/ui-message-stream";

async function capture(run: (writer: AgentStreamWriter) => Promise<void>): Promise<unknown[]> {
  writes.length = 0;
  createUIMessageStreamMock.mockImplementation(({ execute }: { execute: (opts: { writer: { write: (c: unknown) => void } }) => Promise<void> }) => {
    const writer = { write: (chunk: unknown) => writes.push(chunk) };
    return execute({ writer });
  });
  streamAgentResponse(run);
  await new Promise((r) => setTimeout(r, 0));
  return writes;
}

describe("streamAgentResponse writer", () => {
  it("answerDelta streams multiple deltas under one text-start/text-end pair", async () => {
    const out = await capture(async (writer) => {
      writer.answerDelta("Hel");
      writer.answerDelta("lo");
      writer.answerEnd();
    });
    expect(out).toContainEqual({ type: "text-start", id: "answer" });
    expect(out).toContainEqual({ type: "text-delta", id: "answer", delta: "Hel" });
    expect(out).toContainEqual({ type: "text-delta", id: "answer", delta: "lo" });
    expect(out).toContainEqual({ type: "text-end", id: "answer" });
    expect(out.filter((c) => (c as { type: string }).type === "text-start")).toHaveLength(1);
  });

  it("answerEnd is a no-op if nothing was ever started", async () => {
    const out = await capture(async (writer) => {
      writer.answerEnd();
    });
    expect(out.some((c) => (c as { type: string }).type === "text-start")).toBe(false);
    expect(out.some((c) => (c as { type: string }).type === "text-end")).toBe(false);
  });

  it("answerFlush starts fresh when nothing streamed yet (today's one-shot behavior)", async () => {
    const out = await capture(async (writer) => {
      writer.answerFlush("full answer");
    });
    expect(out).toContainEqual({ type: "text-start", id: "answer" });
    expect(out).toContainEqual({ type: "text-delta", id: "answer", delta: "full answer" });
    expect(out).toContainEqual({ type: "text-end", id: "answer" });
  });

  it("answerFlush appends after live deltas instead of restarting the part", async () => {
    const out = await capture(async (writer) => {
      writer.answerDelta("checking... ");
      writer.answerFlush("final answer");
    });
    expect(out.filter((c) => (c as { type: string }).type === "text-start")).toHaveLength(1);
    expect(out).toContainEqual({ type: "text-delta", id: "answer", delta: "checking... " });
    expect(out).toContainEqual({ type: "text-delta", id: "answer", delta: "final answer" });
    expect(out.filter((c) => (c as { type: string }).type === "text-end")).toHaveLength(1);
  });

  it("toolPending writes a data-tool-pending part with the tool name", async () => {
    const out = await capture(async (writer) => {
      writer.toolPending("search_memory");
    });
    expect(out).toContainEqual({ type: "data-tool-pending", id: "tool-pending", data: { tool: "search_memory" } });
  });

  it("step and meta are unchanged", async () => {
    const out = await capture(async (writer) => {
      writer.step("step-1", { ok: true });
      writer.meta({ runId: 1 });
    });
    expect(out).toContainEqual({ type: "data-step", id: "step-1", data: { ok: true } });
    expect(out).toContainEqual({ type: "data-meta", id: "meta", data: { runId: 1 } });
  });
});
