import { vi } from "vitest";

const { runAgentMock } = vi.hoisted(() => ({ runAgentMock: vi.fn() }));
vi.mock("@/lib/harness", () => ({ runAgent: runAgentMock }));

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

  it("returns structured JSON error when runAgent throws (e.g. DB init failure)", async () => {
    runAgentMock.mockRejectedValue(new Error("DB connection refused"));
    const res = await POST(postRequest({ question: "anything" }));
    expect(res.status).toBe(500);
    await expect(res.json()).resolves.toMatchObject({ error: "DB connection refused" });
  });
});
