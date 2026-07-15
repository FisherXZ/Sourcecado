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

import { POST } from "@/app/api/agent/route";

function postRequest(body: unknown): Request {
  return new Request("http://localhost/api/agent", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

describe("POST /api/agent", () => {
  beforeEach(() => {
    runAgentMock.mockReset();
    getRunTraceMock.mockResolvedValue(null); // no trace → skip citation check
  });

  it("returns 400 when question is missing", async () => {
    const res = await POST(postRequest({}));
    expect(res.status).toBe(400);
  });

  it("runs the agent and returns the run id on success", async () => {
    runAgentMock.mockResolvedValue({ runId: 7, status: "succeeded", answer: "hi", steps: 2 });
    const res = await POST(postRequest({ question: "echo hi" }));
    expect(res.status).toBe(200);
    await expect(res.json()).resolves.toMatchObject({ runId: 7, status: "succeeded" });
    expect(runAgentMock).toHaveBeenCalledTimes(1);
  });

  it("returns 500 when the run fails", async () => {
    runAgentMock.mockResolvedValue({ runId: 9, status: "failed", steps: 8 });
    const res = await POST(postRequest({ question: "loop" }));
    expect(res.status).toBe(500);
    await expect(res.json()).resolves.toMatchObject({ runId: 9, status: "failed" });
  });

  it("returns invalidCitations in the response body", async () => {
    runAgentMock.mockResolvedValue({ runId: 7, status: "succeeded", answer: "hi", steps: 1 });
    const res = await POST(postRequest({ question: "echo hi" }));
    const body = await res.json();
    expect(Array.isArray(body.invalidCitations)).toBe(true);
  });

  it("flags an invented citation while leaving a valid one unflagged", async () => {
    // Final answer cites a valid id (from the bundle) AND an invented one.
    runAgentMock.mockResolvedValue({
      runId: 11,
      status: "succeeded",
      answer: "Answer: see real-src#chunk-1 and ghost#chunk-7",
      steps: 2,
    });
    // Override the shared null mock: return a trace whose search_memory tool
    // call produced a bundle containing only the valid citation.
    getRunTraceMock.mockResolvedValue({
      steps: [
        {
          children: [],
          toolCalls: [
            {
              toolName: "search_memory",
              status: "succeeded",
              result: {
                intent: "generic",
                acceptedFacts: [],
                gapFacts: [],
                chunks: [{ text: "t", citation: "real-src#chunk-1", score: 0.9 }],
              },
            },
          ],
        },
      ],
    });

    const res = await POST(postRequest({ question: "who responded?" }));
    expect(res.status).toBe(200);
    const body = await res.json();

    expect(body.invalidCitations).toContain("ghost#chunk-7");
    expect(body.invalidCitations).not.toContain("real-src#chunk-1");
    // Invalid token is sanitized out; the valid one survives.
    expect(body.answer).toContain("real-src#chunk-1");
    expect(body.answer).not.toContain("ghost#chunk-7");
  });

  it("returns structured JSON error when runAgent throws (e.g. DB init failure)", async () => {
    runAgentMock.mockRejectedValue(new Error("DB connection refused"));
    const res = await POST(postRequest({ question: "anything" }));
    expect(res.status).toBe(500);
    await expect(res.json()).resolves.toMatchObject({ error: "DB connection refused" });
  });
});
