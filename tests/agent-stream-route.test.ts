import { vi } from "vitest";

const { runAgentMock, getRunTraceMock } = vi.hoisted(() => ({
  runAgentMock: vi.fn(),
  getRunTraceMock: vi.fn(),
}));
vi.mock("@/lib/harness", () => ({ runAgent: runAgentMock }));
vi.mock("@/lib/ledger", () => ({ getRunTrace: getRunTraceMock }));
vi.mock("@/lib/db", () => ({ getDb: vi.fn().mockReturnValue({}) }));

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
});
