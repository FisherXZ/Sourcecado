import { vi } from "vitest";

const { runAgentMock, getRunTraceMock } = vi.hoisted(() => ({
  runAgentMock: vi.fn(),
  getRunTraceMock: vi.fn(),
}));
vi.mock("@/lib/harness", () => ({ runAgent: runAgentMock }));
vi.mock("@/lib/ledger", () => ({ getRunTrace: getRunTraceMock }));
vi.mock("@/lib/db", () => ({ getDb: vi.fn().mockReturnValue({}) }));

import { POST } from "@/app/api/agent/route";

function post(body: unknown): Request {
  return new Request("http://localhost/api/agent", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

describe("POST /api/agent — multi-turn history", () => {
  beforeEach(() => {
    runAgentMock.mockReset();
    runAgentMock.mockResolvedValue({ runId: 1, status: "succeeded", answer: "ok", steps: 1 });
    getRunTraceMock.mockResolvedValue(null);
  });

  it("forwards conversation history to runAgent", async () => {
    const history = [
      { role: "user", content: "Who is Acme?" },
      { role: "assistant", content: "A fintech." },
    ];
    await POST(post({ question: "And funding?", history }));
    expect(runAgentMock).toHaveBeenCalledTimes(1);
    expect(runAgentMock.mock.calls[0][0]).toMatchObject({ history });
  });

  it("works without history (back-compat)", async () => {
    const res = await POST(post({ question: "solo" }));
    expect(res.status).toBe(200);
    expect(runAgentMock.mock.calls[0][0].question).toBe("solo");
  });

  it("ignores a malformed history (not an array) instead of 500ing", async () => {
    const res = await POST(post({ question: "q", history: "nope" }));
    expect(res.status).toBe(200);
    const arg = runAgentMock.mock.calls[0][0];
    expect(arg.history === undefined || Array.isArray(arg.history)).toBe(true);
  });
});
