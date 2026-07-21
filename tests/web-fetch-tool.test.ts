import { lookup } from "node:dns/promises";
import { getDb } from "@/lib/db";
import { htmlToText, isBlockedIp, webFetchTool, WEB_FETCH_MAX_CHARS } from "@/lib/tools/web-fetch";

// SSRF guard resolves hosts via dns.lookup — mock it so execute() tests never
// touch real DNS. Default: every host resolves to a public address; SSRF tests
// override per-call with mockResolvedValueOnce.
vi.mock("node:dns/promises", () => ({ lookup: vi.fn() }));
const lookupMock = vi.mocked(lookup);
const PUBLIC = [{ address: "93.184.216.34", family: 4 }];

const ctx = () => ({ db: getDb(), runId: 0, parentStepId: 0 });

describe("webFetchTool", () => {
  beforeEach(() => {
    lookupMock.mockReset();
    lookupMock.mockResolvedValue(PUBLIC as never);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("is an enrich-class tool named web_fetch", () => {
    expect(webFetchTool.name).toBe("web_fetch");
    expect(webFetchTool.permissionClass).toBe("enrich");
  });

  it("htmlToText strips tags, scripts, and decodes entities", () => {
    const html = "<html><body><script>evil()</script><h1>Hi &amp; welcome</h1><p>World</p></body></html>";
    expect(htmlToText(html)).toBe("Hi & welcome World");
  });

  it("isBlockedIp blocks non-public ranges and allows public addresses", () => {
    for (const ip of [
      "127.0.0.1", "10.0.0.5", "172.16.4.4", "172.31.255.255", "192.168.1.1",
      "169.254.169.254", "100.64.0.1", "0.0.0.0",
      "::1", "::", "fe80::1", "fea0::1", "feb0::1", "fc00::1", "fd12:3456::1", "::ffff:169.254.169.254",
      // IPv4-mapped IPv6 in hextet form (WHATWG URL parser / some resolvers emit
      // this) must unwrap and block just like the dotted form:
      "::ffff:a9fe:a9fe", // 169.254.169.254 metadata
      "::ffff:7f00:1", // 127.0.0.1 loopback
      "::ffff:0a00:0001", // 10.0.0.1 private
      "::ffff:0:0", // 0.0.0.0
      "::ffff:1234", // unrecognised ::ffff: form → fail closed
      "999.1.1.1", "not-an-ip",
    ]) {
      expect(isBlockedIp(ip)).toBe(true);
    }
    for (const ip of ["93.184.216.34", "8.8.8.8", "1.1.1.1", "2606:2800:220:1:248:1893:25c8:1946"]) {
      expect(isBlockedIp(ip)).toBe(false);
    }
  });

  it("rejects a non-http(s) url", async () => {
    await expect(webFetchTool.execute({ url: "ftp://example.com/file" }, ctx())).rejects.toThrow(/protocol/i);
  });

  it("rejects an unparseable url", async () => {
    await expect(webFetchTool.execute({ url: "not a url" }, ctx())).rejects.toThrow(/Invalid URL/);
  });

  it("refuses a host that resolves to a non-public address (SSRF)", async () => {
    lookupMock.mockResolvedValue([{ address: "169.254.169.254", family: 4 }] as never);
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    await expect(webFetchTool.execute({ url: "https://metadata.evil.test/" }, ctx())).rejects.toThrow(
      /non-public address 169\.254\.169\.254/,
    );
    expect(fetchMock).not.toHaveBeenCalled(); // blocked before any network call
  });

  it("re-validates on redirect and refuses a redirect to a private address (SSRF)", async () => {
    // hop 1: public host, 302 → internal; hop 2: host resolves private → refuse.
    lookupMock
      .mockResolvedValueOnce(PUBLIC as never)
      .mockResolvedValueOnce([{ address: "169.254.169.254", family: 4 }] as never);
    const fetchMock = vi.fn().mockResolvedValueOnce({
      status: 302,
      statusText: "Found",
      headers: { get: (h: string) => (h === "location" ? "http://169.254.169.254/latest/meta-data/" : null) },
    });
    vi.stubGlobal("fetch", fetchMock);
    await expect(webFetchTool.execute({ url: "https://redir.test/go" }, ctx())).rejects.toThrow(
      /non-public address 169\.254\.169\.254/,
    );
    expect(fetchMock).toHaveBeenCalledTimes(1); // second hop refused before fetch
  });

  it("throws a clean error on too many redirects", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        status: 302,
        statusText: "Found",
        headers: { get: (h: string) => (h === "location" ? "https://example.com/next" : null) },
      }),
    );
    await expect(webFetchTool.execute({ url: "https://example.com/start" }, ctx())).rejects.toThrow(
      /too many redirects/,
    );
  });

  it("fetches a page and returns HTML-stripped text with the content type", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        statusText: "OK",
        headers: { get: () => "text/html; charset=utf-8" },
        text: async () => "<html><body><h1>Hi</h1><script>evil()</script><p>World</p></body></html>",
      }),
    );

    const result = await webFetchTool.execute({ url: "https://example.com/page" }, ctx());

    expect(result.text).toBe("Hi World");
    expect(result.contentType).toBe("text/html; charset=utf-8");
    expect(result.truncated).toBe(false);
    expect(result.url).toBe("https://example.com/page");
  });

  it("caps oversized responses and marks them truncated", async () => {
    const big = "a".repeat(WEB_FETCH_MAX_CHARS + 1000);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        statusText: "OK",
        headers: { get: () => "text/plain" },
        text: async () => big,
      }),
    );

    const result = await webFetchTool.execute({ url: "https://example.com/big" }, ctx());

    expect(result.truncated).toBe(true);
    expect(result.text.length).toBeLessThanOrEqual(WEB_FETCH_MAX_CHARS);
  });

  it("throws a clean error on a non-OK response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: false, status: 404, statusText: "Not Found", headers: { get: () => null } }),
    );
    await expect(webFetchTool.execute({ url: "https://example.com/missing" }, ctx())).rejects.toThrow(
      /Fetch failed: 404/,
    );
  });

  it("throws a clean error when fetch itself rejects (network failure)", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("ENOTFOUND example.com")));
    await expect(webFetchTool.execute({ url: "https://example.com/page" }, ctx())).rejects.toThrow(/ENOTFOUND/);
  });

  it("throws a clean error when reading the response body fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        statusText: "OK",
        headers: { get: () => "text/html" },
        text: async () => {
          throw new Error("stream aborted");
        },
      }),
    );
    await expect(webFetchTool.execute({ url: "https://example.com/page" }, ctx())).rejects.toThrow(/stream aborted/);
  });

  it.skipIf(!process.env.SOURCECADO_RUN_LIVE_SMOKE)(
    "live: fetches a real page and strips its HTML",
    async () => {
      const real = await vi.importActual<typeof import("node:dns/promises")>("node:dns/promises");
      lookupMock.mockImplementation(real.lookup as never);
      const result = await webFetchTool.execute({ url: "https://example.com" }, ctx());
      expect(result.text).toMatch(/Example Domain/i);
    },
  );
});
