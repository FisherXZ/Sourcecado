import { lookup } from "node:dns/promises";
import { getDb } from "@/lib/db";
import { fetchCapped } from "@/lib/tools/web-fetch-http";
import {
  htmlToText,
  isBlockedIp,
  webFetchTool,
  WEB_FETCH_MAX_BYTES,
} from "@/lib/tools/web-fetch";

// SSRF guard resolves hosts via dns.lookup — mock it so execute() tests never
// touch real DNS. Default: every host resolves to a public address; SSRF tests
// override per-call with mockResolvedValueOnce.
vi.mock("node:dns/promises", () => ({ lookup: vi.fn() }));
const lookupMock = vi.mocked(lookup);
const PUBLIC = [{ address: "93.184.216.34", family: 4 }];

// The transport is mocked here so these tests cover the guard and the redirect
// loop. The transport's own behaviour (pinning, byte cap) is covered against a
// real socket in web-fetch-http.test.ts.
vi.mock("@/lib/tools/web-fetch-http", () => ({ fetchCapped: vi.fn() }));
const fetchMock = vi.mocked(fetchCapped);

const ok = (text: string, contentType: string | null = "text/html", truncated = false) => ({
  status: 200,
  statusText: "OK",
  contentType,
  location: null,
  text,
  truncated,
});

const ctx = () => ({ db: getDb(), runId: 0, parentStepId: 0 });

describe("webFetchTool", () => {
  beforeEach(() => {
    lookupMock.mockReset();
    lookupMock.mockResolvedValue(PUBLIC as never);
    fetchMock.mockReset();
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
    await expect(webFetchTool.execute({ url: "https://metadata.evil.test/" }, ctx())).rejects.toThrow(
      /non-public address 169\.254\.169\.254/,
    );
    expect(fetchMock).not.toHaveBeenCalled(); // blocked before any network call
  });

  it("hands the transport the address it validated, not the hostname (DNS-rebinding pin)", async () => {
    // The guard resolves once; that exact address must be what the connection
    // uses. If the transport were left to re-resolve, a hostname whose DNS flips
    // to 169.254.169.254 between validation and connect would reach the metadata
    // service — the TOCTOU window PR #18 left open.
    lookupMock.mockResolvedValue([{ address: "93.184.216.34", family: 4 }] as never);
    fetchMock.mockResolvedValue(ok("<p>hi</p>"));

    await webFetchTool.execute({ url: "https://rebind.test/page" }, ctx());

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toMatchObject({
      url: "https://rebind.test/page",
      address: "93.184.216.34",
      family: 4,
    });
  });

  it("pins each redirect hop to that hop's own validated address", async () => {
    lookupMock
      .mockResolvedValueOnce([{ address: "93.184.216.34", family: 4 }] as never)
      .mockResolvedValueOnce([{ address: "8.8.8.8", family: 4 }] as never);
    fetchMock
      .mockResolvedValueOnce({
        status: 302, statusText: "Found", contentType: null,
        location: "https://second.test/landing", text: "", truncated: false,
      })
      .mockResolvedValueOnce(ok("<p>done</p>"));

    const result = await webFetchTool.execute({ url: "https://first.test/go" }, ctx());

    expect(result.text).toBe("done");
    expect(fetchMock.mock.calls[0][0]).toMatchObject({ url: "https://first.test/go", address: "93.184.216.34" });
    expect(fetchMock.mock.calls[1][0]).toMatchObject({ url: "https://second.test/landing", address: "8.8.8.8" });
  });

  it("re-validates on redirect and refuses a redirect to a private address (SSRF)", async () => {
    // hop 1: public host, 302 → internal; hop 2: host resolves private → refuse.
    lookupMock
      .mockResolvedValueOnce(PUBLIC as never)
      .mockResolvedValueOnce([{ address: "169.254.169.254", family: 4 }] as never);
    fetchMock.mockResolvedValueOnce({
      status: 302, statusText: "Found", contentType: null,
      location: "http://169.254.169.254/latest/meta-data/", text: "", truncated: false,
    });

    await expect(webFetchTool.execute({ url: "https://redir.test/go" }, ctx())).rejects.toThrow(
      /non-public address 169\.254\.169\.254/,
    );
    expect(fetchMock).toHaveBeenCalledTimes(1); // second hop refused before connecting
  });

  it("throws a clean error on a redirect with no Location header", async () => {
    fetchMock.mockResolvedValue({
      status: 302, statusText: "Found", contentType: null,
      location: null, text: "", truncated: false,
    });
    await expect(webFetchTool.execute({ url: "https://example.com/go" }, ctx())).rejects.toThrow(
      /redirect 302 with no Location/,
    );
  });

  it("throws a clean error on too many redirects", async () => {
    fetchMock.mockResolvedValue({
      status: 302, statusText: "Found", contentType: null,
      location: "https://example.com/next", text: "", truncated: false,
    });
    await expect(webFetchTool.execute({ url: "https://example.com/start" }, ctx())).rejects.toThrow(
      /too many redirects/,
    );
  });

  it("fetches a page and returns HTML-stripped text with the content type", async () => {
    fetchMock.mockResolvedValue(
      ok("<html><body><h1>Hi</h1><script>evil()</script><p>World</p></body></html>", "text/html; charset=utf-8"),
    );

    const result = await webFetchTool.execute({ url: "https://example.com/page" }, ctx());

    expect(result.text).toBe("Hi World");
    expect(result.contentType).toBe("text/html; charset=utf-8");
    expect(result.truncated).toBe(false);
    expect(result.url).toBe("https://example.com/page");
  });

  it("passes the byte cap to the transport and surfaces its truncation flag", async () => {
    fetchMock.mockResolvedValue(ok("a".repeat(1000), "text/plain", true));

    const result = await webFetchTool.execute({ url: "https://example.com/big" }, ctx());

    expect(fetchMock.mock.calls[0][0]).toMatchObject({ maxBytes: WEB_FETCH_MAX_BYTES });
    expect(result.truncated).toBe(true);
  });

  it("throws a clean error on a non-OK response", async () => {
    fetchMock.mockResolvedValue({
      status: 404, statusText: "Not Found", contentType: null,
      location: null, text: "nope", truncated: false,
    });
    await expect(webFetchTool.execute({ url: "https://example.com/missing" }, ctx())).rejects.toThrow(
      /Fetch failed: 404/,
    );
  });

  it("propagates a transport failure", async () => {
    fetchMock.mockRejectedValue(new Error("ECONNREFUSED 93.184.216.34:443"));
    await expect(webFetchTool.execute({ url: "https://example.com/page" }, ctx())).rejects.toThrow(/ECONNREFUSED/);
  });

  it.skipIf(!process.env.SOURCECADO_RUN_LIVE_SMOKE)(
    "live: fetches a real page and strips its HTML",
    async () => {
      const realDns = await vi.importActual<typeof import("node:dns/promises")>("node:dns/promises");
      const realTransport =
        await vi.importActual<typeof import("@/lib/tools/web-fetch-http")>("@/lib/tools/web-fetch-http");
      lookupMock.mockImplementation(realDns.lookup as never);
      fetchMock.mockImplementation(realTransport.fetchCapped);
      const result = await webFetchTool.execute({ url: "https://example.com" }, ctx());
      expect(result.text).toMatch(/Example Domain/i);
    },
  );
});
