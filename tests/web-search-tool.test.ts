import { getDb } from "@/lib/db";
import { webSearchArgsSchema, webSearchTool } from "@/lib/tools/web-search";

const ORIGINAL_TAVILY_KEY = process.env.TAVILY_API_KEY;

describe("webSearchTool", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    if (ORIGINAL_TAVILY_KEY === undefined) {
      delete process.env.TAVILY_API_KEY;
    } else {
      process.env.TAVILY_API_KEY = ORIGINAL_TAVILY_KEY;
    }
  });

  it("is an enrich-class tool named web_search", () => {
    expect(webSearchTool.name).toBe("web_search");
    expect(webSearchTool.permissionClass).toBe("enrich");
  });

  it("rejects args without a query", () => {
    expect(webSearchArgsSchema.safeParse({}).success).toBe(false);
  });

  it("throws a clean error when TAVILY_API_KEY is not configured", async () => {
    delete process.env.TAVILY_API_KEY;
    await expect(
      webSearchTool.execute({ query: "sourcing directors" }, { db: getDb(), runId: 0, parentStepId: 0 }),
    ).rejects.toThrow(/TAVILY_API_KEY/);
  });

  it("returns mapped results on a successful Tavily response", async () => {
    process.env.TAVILY_API_KEY = "test-key";
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      statusText: "OK",
      json: async () => ({
        results: [{ title: "Result 1", url: "https://example.com/1", content: "snippet", score: 0.9 }],
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await webSearchTool.execute(
      { query: "sourcing directors" },
      { db: getDb(), runId: 0, parentStepId: 0 },
    );

    expect(result.results).toEqual([
      { title: "Result 1", url: "https://example.com/1", content: "snippet", score: 0.9 },
    ]);
    expect(fetchMock).toHaveBeenCalledWith(
      "https://api.tavily.com/search",
      expect.objectContaining({ method: "POST" }),
    );
    const [, options] = fetchMock.mock.calls[0];
    const body = JSON.parse(options.body as string);
    expect(body).toMatchObject({ api_key: "test-key", query: "sourcing directors", max_results: 5 });
  });

  it("throws a clean error on a non-OK Tavily response", async () => {
    process.env.TAVILY_API_KEY = "test-key";
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: false, status: 500, statusText: "Internal Server Error" }),
    );
    await expect(
      webSearchTool.execute({ query: "x" }, { db: getDb(), runId: 0, parentStepId: 0 }),
    ).rejects.toThrow(/Tavily search failed: 500/);
  });

  it("throws a clean error when fetch itself rejects (network failure)", async () => {
    process.env.TAVILY_API_KEY = "test-key";
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("ENOTFOUND api.tavily.com")));
    await expect(
      webSearchTool.execute({ query: "x" }, { db: getDb(), runId: 0, parentStepId: 0 }),
    ).rejects.toThrow(/ENOTFOUND/);
  });

  it("throws a clean error when the response body is not valid JSON", async () => {
    process.env.TAVILY_API_KEY = "test-key";
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        statusText: "OK",
        json: async () => {
          throw new SyntaxError("Unexpected token in JSON");
        },
      }),
    );
    await expect(
      webSearchTool.execute({ query: "x" }, { db: getDb(), runId: 0, parentStepId: 0 }),
    ).rejects.toThrow(/Unexpected token/);
  });

  it.skipIf(!process.env.TAVILY_API_KEY)(
    "live: searches Tavily for a real query when TAVILY_API_KEY is present",
    async () => {
      const result = await webSearchTool.execute(
        { query: "Sourcecado sourcing operating system" },
        { db: getDb(), runId: 0, parentStepId: 0 },
      );
      expect(Array.isArray(result.results)).toBe(true);
    },
  );
});
