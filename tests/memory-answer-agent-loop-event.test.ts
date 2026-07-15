import { vi } from "vitest";

const { runAgentMock } = vi.hoisted(() => ({ runAgentMock: vi.fn() }));
vi.mock("@/lib/harness", () => ({ runAgent: runAgentMock }));
vi.mock("@/lib/ledger", () => ({ getRunTrace: vi.fn().mockResolvedValue(null) }));
vi.mock("@/lib/context", () => ({
  buildMemoryAnswerInstructions: vi.fn().mockResolvedValue("instructions"),
}));

import { answerWithMemory } from "@/lib/memory/answer";

describe("answerWithMemory onAgentLoopEvent passthrough", () => {
  // Braced body on purpose: mockReset() returns the mock, and vitest treats a
  // function returned from beforeEach as a cleanup hook (it would then invoke
  // the mock itself with no args during teardown).
  beforeEach(() => {
    runAgentMock.mockReset();
  });

  it("forwards onAgentLoopEvent to runAgent unchanged", async () => {
    runAgentMock.mockImplementation(async (input: { onAgentLoopEvent?: (e: unknown) => void }) => {
      input.onAgentLoopEvent?.({ type: "llm", event: { type: "text_delta", delta: "hi" } });
      return { runId: 1, status: "succeeded", answer: "hi", steps: 1, messages: [] };
    });

    const events: unknown[] = [];
    await answerWithMemory({} as never, {
      question: "q",
      onAgentLoopEvent: (e) => {
        events.push(e);
      },
    });

    expect(events).toHaveLength(1);
    expect(runAgentMock).toHaveBeenCalledWith(
      expect.objectContaining({ onAgentLoopEvent: expect.any(Function) })
    );
  });

  it("works with onAgentLoopEvent omitted (backward compatible)", async () => {
    runAgentMock.mockResolvedValue({ runId: 1, status: "succeeded", answer: "hi", steps: 1, messages: [] });
    const result = await answerWithMemory({} as never, { question: "q" });
    expect(result.answer).toBe("hi");
  });
});
