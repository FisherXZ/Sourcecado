# R7 — External Tools: web_search, web_fetch, apollo_search_people, apollo_enrich_contact

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add four new `Tool`-shaped modules — `web_search` (Tavily), `web_fetch`
(plain fetch + HTML→text), `apollo_search_people`, and `apollo_enrich_contact`
(mocked-client Apollo basics) — all permission class `enrich`, unit-tested with
a mocked `fetch`, with a missing-API-key path that throws a clean error (never
a crash) so the R3 orchestrator turns it into an `is_error` tool_result.

**Depends on:** R3 (tool orchestrator) — contract only. This slice assumes
`src/lib/tools/types.ts` (`Tool`, `ToolContext`, `PermissionClass`) and
`src/lib/tools/registry.ts` (`createToolRegistry`) exist byte-for-byte as they
do on `main` today (the R3 file-ownership row marks them "unchanged"), and
that a live `executeTool()` choke point (R3) exists somewhere in the runtime
path — but this slice's own files never import or call it. Tool tests call
`tool.execute()` directly, exactly like the existing `tests/echo-tool.test.ts`.

**Context:** Per the sprint spec (§R7) and the R-contracts brief (file
ownership table, row `src/lib/tools/web-search.ts, web-fetch.ts, apollo.ts`),
this slice is scoped to the tool implementations only:
- All four tools are class `enrich`.
- All four register into the live chat registry (`memoryRegistry()` in
  `src/lib/memory/answer-config.ts`) alongside `search_memory` and
  `add_memory_note`, and chat runs allow the `enrich` class so the tools are
  agent-reachable (**Task 5**). This REVERSES the original "defer wiring to
  v2" cut per Fisher's 2026-07-21 decision — see the Reconciliation note
  below; the addendum at the bottom of this file is the authority.
- Apollo is built against real endpoints with a mocked `fetch` in tests —
  "mocked client" per the spec means the API key is absent and tests never
  hit the network, not that the implementation is a stub. Live smoke for
  Apollo is explicitly deferred until `APOLLO_API_KEY` is provided (Decisions
  locked).
- `.env.example` gets `TAVILY_API_KEY` and `APOLLO_API_KEY` (R9 adds provider
  docs separately — not this slice's job).

---

## Reconciliation (2026-07-21 — folds the addendum into executable tasks)

Fisher accepted the 2026-07-15 addendum (bottom of file): wire the tools in
now, harden `web_fetch` for SSRF now. Both are folded into concrete tasks so
this plan reads top-to-bottom without contradiction:

- **Task 2** (`web_fetch`) now includes the SSRF guard (`isBlockedIp` +
  `assertPublicHost`) and a manual per-hop redirect loop, with tests.
- **Task 5** (NEW) registers all four tools into `memoryRegistry()` and adds
  `enrich` to chat runs' `allowedClasses`, with tests.
- **SUPERSEDED — do NOT follow:** the intro's old "does not wire" cut,
  Judgment call #4's original "deferred to v2" text, the dated Eng-Review's
  "NOT in scope: wiring / SSRF" lines, and open item **T4**. The Eng-Review
  section is retained below as a historical record only.

### Triage fixes (2026-07-21, PR #18 bot review — verified against live vendor docs)
Applied on the branch after the initial build (code snippets above are the
pre-triage versions; the shipped code differs in these three spots):
- **web_search (Task 1):** Tavily auth is `Authorization: Bearer <key>`, NOT
  `api_key` in the JSON body (verified against Tavily's OpenAPI `bearerAuth`).
  Header set; `api_key` removed from body.
- **web_fetch (Task 2):** IPv6 link-local check widened from `fe80` (only /16)
  to `fe80::/10` via `/^fe[89ab]/` (covers fe80–febf).
- **apollo (Task 3):** endpoint paths corrected to
  `https://api.apollo.io/api/v1/mixed_people/api_search` and
  `.../api/v1/people/match` (verified against docs.apollo.io). NOTE: Apollo's
  full live wire contract (filters as query params vs body, response shape)
  remains DEFERRED per Decisions locked until `APOLLO_API_KEY` arrives.
- **NEEDS-HUMAN (surfaced to Fisher, not auto-fixed):** DNS-rebinding IP-pinning
  (proper fix needs the `undici` dep → runbook stop-condition) and web_fetch
  post-buffering response cap (streaming byte-limit) — both documented v1
  deferrals; see PR #18 replies.

---

### Task 1: `web_search` (Tavily)

**Files:**
- Create: `src/lib/tools/web-search.ts`
- Create: `tests/web-search-tool.test.ts`

**What to build:** A `Tool<WebSearchArgs, WebSearchResult>` named `web_search`
that POSTs to Tavily's `/search` endpoint using `TAVILY_API_KEY`, and maps the
response into a small normalized shape.

- [ ] **Step 1: Write `src/lib/tools/web-search.ts`**

```ts
import { z } from "zod";
import type { Tool } from "./types";

const TAVILY_SEARCH_URL = "https://api.tavily.com/search";

export const webSearchArgsSchema = z.object({
  query: z.string().min(1),
  maxResults: z.number().int().positive().max(10).optional(),
});
export type WebSearchArgs = z.infer<typeof webSearchArgsSchema>;

export interface WebSearchResultItem {
  title: string;
  url: string;
  content: string;
  score: number | null;
}

export interface WebSearchResult {
  results: WebSearchResultItem[];
}

interface TavilySearchResponse {
  results?: Array<{ title?: string; url?: string; content?: string; score?: number }>;
}

export const webSearchTool: Tool<WebSearchArgs, WebSearchResult> = {
  name: "web_search",
  description:
    "Search the web via Tavily. Returns ranked results with title/url/content snippet. Use when memory doesn't cover something and current external information is needed.",
  permissionClass: "enrich",
  argsSchema: webSearchArgsSchema,
  async execute(args) {
    const apiKey = process.env.TAVILY_API_KEY;
    if (!apiKey) {
      throw new Error("TAVILY_API_KEY is not configured.");
    }

    const res = await fetch(TAVILY_SEARCH_URL, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        api_key: apiKey,
        query: args.query,
        max_results: args.maxResults ?? 5,
      }),
      signal: AbortSignal.timeout(15_000),
    });

    if (!res.ok) {
      throw new Error(`Tavily search failed: ${res.status} ${res.statusText}`);
    }

    const data = (await res.json()) as TavilySearchResponse;
    const results: WebSearchResultItem[] = (data.results ?? []).map((r) => ({
      title: r.title ?? "",
      url: r.url ?? "",
      content: r.content ?? "",
      score: typeof r.score === "number" ? r.score : null,
    }));
    return { results };
  },
};
```

- [ ] **Step 2: Write `tests/web-search-tool.test.ts`**

```ts
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
```

**Acceptance criteria:**
- `webSearchTool.permissionClass === "enrich"`.
- Missing `TAVILY_API_KEY` → `execute()` rejects with a message containing
  `TAVILY_API_KEY`, never an unhandled crash.
- A mocked successful Tavily response maps into `{ results: [...] }` with the
  exact fields asserted above.
- A mocked non-OK Tavily response rejects with a message containing the
  status code.
- The live-smoke test runs (not skipped) only when `TAVILY_API_KEY` is set in
  the environment; it is skipped by default in CI.

**Verify:**
```bash
export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"
npx vitest run tests/web-search-tool.test.ts
```
Expected: PASS (8 tests; the live-smoke test shows as skipped unless
`TAVILY_API_KEY` is set).

- [ ] **Step 3: Commit**

```bash
git add src/lib/tools/web-search.ts tests/web-search-tool.test.ts
git commit -m "feat(r7): web_search tool (Tavily)"
```

---

### Task 2: `web_fetch` (plain fetch + HTML→text)

**Files:**
- Create: `src/lib/tools/web-fetch.ts`
- Create: `tests/web-fetch-tool.test.ts`

**What to build:** A `Tool<WebFetchArgs, WebFetchResult>` named `web_fetch`
that fetches an http(s) URL, rejects other protocols, **refuses non-public
(loopback/private/link-local/metadata) addresses and re-validates on every
redirect hop (SSRF guard — in scope per the addendum)**, strips HTML
tags/decodes common entities, and caps the returned text length.

- [ ] **Step 1: Write `src/lib/tools/web-fetch.ts`**

```ts
import { z } from "zod";
import { lookup } from "node:dns/promises";
import type { Tool } from "./types";

export const WEB_FETCH_MAX_CHARS = 500_000;
const MAX_REDIRECTS = 5;

export const webFetchArgsSchema = z.object({
  url: z.string().min(1),
});
export type WebFetchArgs = z.infer<typeof webFetchArgsSchema>;

export interface WebFetchResult {
  url: string;
  contentType: string | null;
  text: string;
  truncated: boolean;
}

export function htmlToText(html: string): string {
  return html
    .replace(/<script[\s\S]*?<\/script>/gi, " ")
    .replace(/<style[\s\S]*?<\/style>/gi, " ")
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/gi, " ")
    .replace(/&amp;/gi, "&")
    .replace(/&lt;/gi, "<")
    .replace(/&gt;/gi, ">")
    .replace(/&quot;/gi, '"')
    .replace(/&#39;/gi, "'")
    .replace(/\s+/g, " ")
    .trim();
}

// SSRF guard. true for loopback / private / link-local / CGNAT / cloud-metadata
// / unique-local addresses — anything an agent-supplied URL must not reach.
// IPv4-mapped IPv6 (::ffff:a.b.c.d) is unwrapped and checked as IPv4. Malformed
// IPv4 fails closed; unrecognised-but-valid IPv6 (global unicast) is allowed.
export function isBlockedIp(ip: string): boolean {
  const mapped = /^::ffff:(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})$/i.exec(ip);
  const addr = mapped ? mapped[1] : ip;

  if (addr.includes(".")) {
    const parts = addr.split(".").map((p) => Number(p));
    if (parts.length !== 4 || parts.some((n) => !Number.isInteger(n) || n < 0 || n > 255)) {
      return true; // malformed IPv4 → fail closed
    }
    const [a, b] = parts;
    if (a === 0) return true; // 0.0.0.0/8
    if (a === 10) return true; // 10.0.0.0/8 private
    if (a === 127) return true; // 127.0.0.0/8 loopback
    if (a === 169 && b === 254) return true; // 169.254.0.0/16 link-local (incl. 169.254.169.254 metadata)
    if (a === 172 && b >= 16 && b <= 31) return true; // 172.16.0.0/12 private
    if (a === 192 && b === 168) return true; // 192.168.0.0/16 private
    if (a === 100 && b >= 64 && b <= 127) return true; // 100.64.0.0/10 CGNAT
    return false;
  }

  if (!addr.includes(":")) return true; // neither IPv4 nor IPv6 → fail closed
  const v6 = addr.toLowerCase();
  if (v6 === "::1" || v6 === "::") return true; // loopback / unspecified
  if (v6.startsWith("fe80")) return true; // fe80::/10 link-local
  if (v6.startsWith("fc") || v6.startsWith("fd")) return true; // fc00::/7 unique-local
  return false; // global unicast IPv6 allowed
}

// Resolve a host (dns.lookup echoes a literal IP verbatim, so this covers
// literal-IP URLs too) and reject if ANY resolved address is non-public.
// Called on every redirect hop.
export async function assertPublicHost(hostname: string): Promise<void> {
  let addrs: Array<{ address: string }>;
  try {
    addrs = await lookup(hostname, { all: true });
  } catch {
    throw new Error(`web_fetch: cannot resolve host "${hostname}"`);
  }
  if (addrs.length === 0) {
    throw new Error(`web_fetch: cannot resolve host "${hostname}"`);
  }
  for (const { address } of addrs) {
    if (isBlockedIp(address)) {
      throw new Error(`web_fetch: refusing non-public address ${address} for "${hostname}"`);
    }
  }
}

export const webFetchTool: Tool<WebFetchArgs, WebFetchResult> = {
  name: "web_fetch",
  description:
    "Fetch a web page by URL and return its visible text with HTML tags stripped. http(s) URLs only; private/internal addresses are refused; response is size-capped.",
  permissionClass: "enrich",
  argsSchema: webFetchArgsSchema,
  async execute(args) {
    let target = args.url;
    let finalUrl = args.url;

    for (let hop = 0; ; hop++) {
      let parsed: URL;
      try {
        parsed = new URL(target);
      } catch {
        throw new Error(`Invalid URL: ${target}`);
      }
      if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
        throw new Error(`Unsupported URL protocol: ${parsed.protocol} (only http/https allowed)`);
      }
      await assertPublicHost(parsed.hostname); // re-validated every hop
      finalUrl = parsed.toString();

      const res = await fetch(finalUrl, {
        redirect: "manual", // follow manually so each hop's host is re-validated
        signal: AbortSignal.timeout(15_000),
      });

      if (res.status >= 300 && res.status < 400) {
        if (hop >= MAX_REDIRECTS) {
          throw new Error(`web_fetch: too many redirects (>${MAX_REDIRECTS})`);
        }
        const location = res.headers.get("location");
        if (!location) {
          throw new Error(`web_fetch: redirect ${res.status} with no Location header`);
        }
        target = new URL(location, finalUrl).toString();
        continue;
      }

      if (!res.ok) {
        throw new Error(`Fetch failed: ${res.status} ${res.statusText}`);
      }

      const contentType = res.headers.get("content-type");
      const raw = await res.text();
      const truncated = raw.length > WEB_FETCH_MAX_CHARS;
      const capped = truncated ? raw.slice(0, WEB_FETCH_MAX_CHARS) : raw;

      return { url: finalUrl, contentType, text: htmlToText(capped), truncated };
    }
  },
};
```

- [ ] **Step 2: Write `tests/web-fetch-tool.test.ts`**

```ts
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
      "::1", "::", "fe80::1", "fc00::1", "fd12:3456::1", "::ffff:169.254.169.254",
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
      lookupMock.mockImplementation((await vi.importActual<typeof import("node:dns/promises")>("node:dns/promises")).lookup as never);
      const result = await webFetchTool.execute({ url: "https://example.com" }, ctx());
      expect(result.text).toMatch(/Example Domain/i);
    },
  );
});
```

> Note: the live-smoke test restores the real `lookup` (via `vi.importActual`)
> so it resolves `example.com` for real; every other test uses the mocked
> resolver and never touches DNS or the network.

**Acceptance criteria:**
- `webFetchTool.permissionClass === "enrich"`.
- Non-http(s) protocol (e.g. `ftp://`) → rejects with a message matching
  `/protocol/i`, never a crash.
- Unparseable URL → rejects with a message containing `Invalid URL`.
- A mocked HTML response returns `text` with tags/scripts stripped and common
  entities decoded, `contentType` from the response header, `truncated:
  false`.
- A response longer than `WEB_FETCH_MAX_CHARS` → `truncated: true` and
  `text.length <= WEB_FETCH_MAX_CHARS`.
- A mocked non-OK response rejects with a message containing the status code.
- **SSRF: a host resolving to a loopback/private/link-local/metadata address
  is refused before any `fetch()` call; `isBlockedIp` blocks the full range
  table and allows public v4/v6; a redirect to a private address is refused on
  the re-validated hop; a redirect chain longer than `MAX_REDIRECTS` rejects
  cleanly.**
- The live-smoke test runs only when `SOURCECADO_RUN_LIVE_SMOKE` is set;
  skipped by default in CI (see Judgment calls — this key needs no API key,
  so it is gated on an explicit opt-in instead).

**Verify:**
```bash
export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"
npx vitest run tests/web-fetch-tool.test.ts
```
Expected: PASS (14 tests: 13 always-run, 1 conditionally skipped unless
`SOURCECADO_RUN_LIVE_SMOKE` is set).

- [ ] **Step 3: Commit**

```bash
git add src/lib/tools/web-fetch.ts tests/web-fetch-tool.test.ts
git commit -m "feat(r7): web_fetch tool (plain fetch + HTML-to-text)"
```

---

### Task 3: `apollo_search_people` + `apollo_enrich_contact` (mocked client)

**Files:**
- Create: `src/lib/tools/apollo.ts`
- Create: `tests/apollo-tools.test.ts`

**What to build:** Two `Tool`s in one file (matching the brief's file
ownership row, which names a single `apollo.ts`), both class `enrich`,
sharing a small `apolloPost()` helper. Both call Apollo's real REST endpoints
(`/v1/mixed_people/search`, `/v1/people/match`) gated on `APOLLO_API_KEY`;
tests mock `fetch` so nothing hits the network (Apollo live smoke is
deferred per Decisions locked — no live-smoke test exists for these two).

- [ ] **Step 1: Write `src/lib/tools/apollo.ts`**

```ts
import { z } from "zod";
import type { Tool } from "./types";

const APOLLO_SEARCH_URL = "https://api.apollo.io/v1/mixed_people/search";
const APOLLO_MATCH_URL = "https://api.apollo.io/v1/people/match";

function requireApolloApiKey(): string {
  const apiKey = process.env.APOLLO_API_KEY;
  if (!apiKey) {
    throw new Error("APOLLO_API_KEY is not configured.");
  }
  return apiKey;
}

async function apolloPost(url: string, apiKey: string, body: Record<string, unknown>): Promise<unknown> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json", "x-api-key": apiKey },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(15_000),
  });
  if (!res.ok) {
    throw new Error(`Apollo request failed: ${res.status} ${res.statusText}`);
  }
  return res.json();
}

// --- apollo_search_people ---

export const apolloSearchPeopleArgsSchema = z
  .object({
    organizationName: z.string().min(1).optional(),
    personTitles: z.array(z.string().min(1)).min(1).optional(),
    limit: z.number().int().positive().max(25).optional(),
  })
  .refine((v) => Boolean(v.organizationName) || Boolean(v.personTitles?.length), {
    message: "Provide organizationName or personTitles.",
  });
export type ApolloSearchPeopleArgs = z.infer<typeof apolloSearchPeopleArgsSchema>;

export interface ApolloPersonSummary {
  name: string | null;
  title: string | null;
  organizationName: string | null;
  linkedinUrl: string | null;
  email: string | null;
}

export interface ApolloSearchPeopleResult {
  people: ApolloPersonSummary[];
}

interface ApolloSearchResponse {
  people?: Array<{
    name?: string;
    title?: string;
    organization?: { name?: string };
    linkedin_url?: string;
    email?: string;
  }>;
}

export const apolloSearchPeopleTool: Tool<ApolloSearchPeopleArgs, ApolloSearchPeopleResult> = {
  name: "apollo_search_people",
  description:
    "Search for people at a target organization via Apollo. Provide organizationName and/or personTitles.",
  permissionClass: "enrich",
  argsSchema: apolloSearchPeopleArgsSchema,
  async execute(args) {
    const apiKey = requireApolloApiKey();
    const data = (await apolloPost(APOLLO_SEARCH_URL, apiKey, {
      q_organization_name: args.organizationName,
      person_titles: args.personTitles,
      per_page: args.limit ?? 10,
    })) as ApolloSearchResponse;

    const people: ApolloPersonSummary[] = (data.people ?? []).map((p) => ({
      name: p.name ?? null,
      title: p.title ?? null,
      organizationName: p.organization?.name ?? null,
      linkedinUrl: p.linkedin_url ?? null,
      email: p.email ?? null,
    }));
    return { people };
  },
};

// --- apollo_enrich_contact ---

export const apolloEnrichContactArgsSchema = z
  .object({
    email: z.string().min(1).optional(),
    firstName: z.string().min(1).optional(),
    lastName: z.string().min(1).optional(),
    organizationName: z.string().min(1).optional(),
  })
  .refine((v) => Boolean(v.email) || (Boolean(v.firstName) && Boolean(v.lastName)), {
    message: "Provide email, or firstName and lastName.",
  });
export type ApolloEnrichContactArgs = z.infer<typeof apolloEnrichContactArgsSchema>;

export interface ApolloEnrichContactResult {
  name: string | null;
  title: string | null;
  organizationName: string | null;
  linkedinUrl: string | null;
  email: string | null;
  phone: string | null;
}

interface ApolloMatchResponse {
  person?: {
    name?: string;
    title?: string;
    organization?: { name?: string };
    linkedin_url?: string;
    email?: string;
    phone_numbers?: Array<{ raw_number?: string }>;
  };
}

export const apolloEnrichContactTool: Tool<ApolloEnrichContactArgs, ApolloEnrichContactResult> = {
  name: "apollo_enrich_contact",
  description:
    "Enrich a single contact via Apollo. Provide email, or firstName + lastName (+ optional organizationName).",
  permissionClass: "enrich",
  argsSchema: apolloEnrichContactArgsSchema,
  async execute(args) {
    const apiKey = requireApolloApiKey();
    const data = (await apolloPost(APOLLO_MATCH_URL, apiKey, {
      email: args.email,
      first_name: args.firstName,
      last_name: args.lastName,
      organization_name: args.organizationName,
    })) as ApolloMatchResponse;

    const person = data.person;
    return {
      name: person?.name ?? null,
      title: person?.title ?? null,
      organizationName: person?.organization?.name ?? null,
      linkedinUrl: person?.linkedin_url ?? null,
      email: person?.email ?? null,
      phone: person?.phone_numbers?.[0]?.raw_number ?? null,
    };
  },
};
```

- [ ] **Step 2: Write `tests/apollo-tools.test.ts`**

```ts
import { getDb } from "@/lib/db";
import {
  apolloEnrichContactArgsSchema,
  apolloEnrichContactTool,
  apolloSearchPeopleArgsSchema,
  apolloSearchPeopleTool,
} from "@/lib/tools/apollo";

const ORIGINAL_APOLLO_KEY = process.env.APOLLO_API_KEY;

describe("apollo tools", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    if (ORIGINAL_APOLLO_KEY === undefined) {
      delete process.env.APOLLO_API_KEY;
    } else {
      process.env.APOLLO_API_KEY = ORIGINAL_APOLLO_KEY;
    }
  });

  describe("apolloSearchPeopleTool", () => {
    it("is an enrich-class tool named apollo_search_people", () => {
      expect(apolloSearchPeopleTool.name).toBe("apollo_search_people");
      expect(apolloSearchPeopleTool.permissionClass).toBe("enrich");
    });

    it("rejects args with neither organizationName nor personTitles", () => {
      expect(apolloSearchPeopleArgsSchema.safeParse({}).success).toBe(false);
    });

    it("throws a clean error when APOLLO_API_KEY is not configured", async () => {
      delete process.env.APOLLO_API_KEY;
      await expect(
        apolloSearchPeopleTool.execute(
          { organizationName: "Acme" },
          { db: getDb(), runId: 0, parentStepId: 0 },
        ),
      ).rejects.toThrow(/APOLLO_API_KEY/);
    });

    it("returns mapped people on a successful Apollo response", async () => {
      process.env.APOLLO_API_KEY = "test-key";
      const fetchMock = vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        statusText: "OK",
        json: async () => ({
          people: [
            {
              name: "Jane Doe",
              title: "VP Eng",
              organization: { name: "Acme" },
              linkedin_url: "https://linkedin.com/in/janedoe",
              email: "jane@acme.com",
            },
          ],
        }),
      });
      vi.stubGlobal("fetch", fetchMock);

      const result = await apolloSearchPeopleTool.execute(
        { organizationName: "Acme" },
        { db: getDb(), runId: 0, parentStepId: 0 },
      );

      expect(result.people).toEqual([
        {
          name: "Jane Doe",
          title: "VP Eng",
          organizationName: "Acme",
          linkedinUrl: "https://linkedin.com/in/janedoe",
          email: "jane@acme.com",
        },
      ]);
      expect(fetchMock).toHaveBeenCalledWith(
        "https://api.apollo.io/v1/mixed_people/search",
        expect.objectContaining({ method: "POST" }),
      );
    });

    it("throws a clean error when fetch itself rejects (network failure)", async () => {
      process.env.APOLLO_API_KEY = "test-key";
      vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("ENOTFOUND api.apollo.io")));
      await expect(
        apolloSearchPeopleTool.execute(
          { organizationName: "Acme" },
          { db: getDb(), runId: 0, parentStepId: 0 },
        ),
      ).rejects.toThrow(/ENOTFOUND/);
    });

    it("throws a clean error when the response body is not valid JSON", async () => {
      process.env.APOLLO_API_KEY = "test-key";
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
        apolloSearchPeopleTool.execute(
          { organizationName: "Acme" },
          { db: getDb(), runId: 0, parentStepId: 0 },
        ),
      ).rejects.toThrow(/Unexpected token/);
    });
  });

  describe("apolloEnrichContactTool", () => {
    it("is an enrich-class tool named apollo_enrich_contact", () => {
      expect(apolloEnrichContactTool.name).toBe("apollo_enrich_contact");
      expect(apolloEnrichContactTool.permissionClass).toBe("enrich");
    });

    it("rejects args with neither email nor firstName+lastName", () => {
      expect(apolloEnrichContactArgsSchema.safeParse({ firstName: "Jane" }).success).toBe(false);
    });

    it("throws a clean error when APOLLO_API_KEY is not configured", async () => {
      delete process.env.APOLLO_API_KEY;
      await expect(
        apolloEnrichContactTool.execute(
          { email: "jane@acme.com" },
          { db: getDb(), runId: 0, parentStepId: 0 },
        ),
      ).rejects.toThrow(/APOLLO_API_KEY/);
    });

    it("returns a mapped contact on a successful Apollo response", async () => {
      process.env.APOLLO_API_KEY = "test-key";
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue({
          ok: true,
          status: 200,
          statusText: "OK",
          json: async () => ({
            person: {
              name: "Jane Doe",
              title: "VP Eng",
              organization: { name: "Acme" },
              linkedin_url: "https://linkedin.com/in/janedoe",
              email: "jane@acme.com",
              phone_numbers: [{ raw_number: "+1-555-0100" }],
            },
          }),
        }),
      );

      const result = await apolloEnrichContactTool.execute(
        { email: "jane@acme.com" },
        { db: getDb(), runId: 0, parentStepId: 0 },
      );

      expect(result).toEqual({
        name: "Jane Doe",
        title: "VP Eng",
        organizationName: "Acme",
        linkedinUrl: "https://linkedin.com/in/janedoe",
        email: "jane@acme.com",
        phone: "+1-555-0100",
      });
    });

    it("throws a clean error when fetch itself rejects (network failure)", async () => {
      process.env.APOLLO_API_KEY = "test-key";
      vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("ENOTFOUND api.apollo.io")));
      await expect(
        apolloEnrichContactTool.execute(
          { email: "jane@acme.com" },
          { db: getDb(), runId: 0, parentStepId: 0 },
        ),
      ).rejects.toThrow(/ENOTFOUND/);
    });

    it("throws a clean error when the response body is not valid JSON", async () => {
      process.env.APOLLO_API_KEY = "test-key";
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
        apolloEnrichContactTool.execute(
          { email: "jane@acme.com" },
          { db: getDb(), runId: 0, parentStepId: 0 },
        ),
      ).rejects.toThrow(/Unexpected token/);
    });
  });
});
```

**Acceptance criteria:**
- Both tools are `permissionClass === "enrich"`.
- `apolloSearchPeopleArgsSchema` rejects args with neither `organizationName`
  nor `personTitles`; `apolloEnrichContactArgsSchema` rejects args with
  neither `email` nor `firstName`+`lastName`.
- Missing `APOLLO_API_KEY` → both tools' `execute()` reject with a message
  containing `APOLLO_API_KEY`, never a crash.
- Mocked successful Apollo responses map into the exact normalized shapes
  asserted above.
- No live-network test exists for either Apollo tool (deferred per Decisions
  locked).

**Verify:**
```bash
export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"
npx vitest run tests/apollo-tools.test.ts
```
Expected: PASS (12 tests).

- [ ] **Step 3: Commit**

```bash
git add src/lib/tools/apollo.ts tests/apollo-tools.test.ts
git commit -m "feat(r7): apollo_search_people + apollo_enrich_contact tools (mocked client)"
```

---

### Task 4: `.env.example` + full verification

**Files:**
- Modify: `.env.example`

**What to build:** Document the three new environment knobs this slice
introduces.

- [ ] **Step 1: Add to `.env.example`** (after the existing
`ANTHROPIC_API_KEY=` line, at the end of the file)

```
# R7 external tools
TAVILY_API_KEY=
APOLLO_API_KEY=
# Opt-in: run the real-network live-smoke test in tests/web-fetch-tool.test.ts.
# (Tavily's own live-smoke test gates on TAVILY_API_KEY being present instead;
# Apollo has no live-smoke test — its live smoke is deferred until the key is provided.)
SOURCECADO_RUN_LIVE_SMOKE=
```

**Acceptance criteria:** `.env.example` contains `TAVILY_API_KEY`,
`APOLLO_API_KEY`, and `SOURCECADO_RUN_LIVE_SMOKE` keys with no values filled
in (it's a template, never a real secret).

- [ ] **Step 2: Run the full test suite** (checkpoint — the *final* slice
gate is re-run at the end of Task 5, which adds the registry-wiring changes)

```bash
export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"
npx vitest run
```
Expected: PASS — every prior suite plus the three new R7 tool test files green
(live-smoke tests shown as skipped since no live keys/opt-in are set in CI).

- [ ] **Step 3: Lint**

```bash
npm run lint
```
Expected: `✔ No ESLint warnings or errors`.

- [ ] **Step 4: Build**

```bash
npm run build
```
Expected: build succeeds (these are plain library modules, no new routes).

- [ ] **Step 5: Commit**

```bash
git add .env.example
git commit -m "docs(r7): document TAVILY_API_KEY, APOLLO_API_KEY, SOURCECADO_RUN_LIVE_SMOKE"
```

---

### Task 5: Register the four tools into the chat registry + allow `enrich`

Per the 2026-07-21 reconciliation (and the addendum), R7 wires all four tools
into the live chat registry and lets chat runs execute the `enrich` class —
this is what makes the tools agent-reachable. Depends on Tasks 1–3 (the tool
modules must exist to import).

**Files:**
- Modify: `src/lib/memory/answer-config.ts`
- Modify: `src/lib/memory/answer.ts`
- Modify: `tests/memory-answer.test.ts` (its existing `memoryRegistry`
  composition test asserts the old 2-tool set + permission filtering — it MUST
  be updated to the new 6-tool set, or it fails as a real regression)

- [ ] **Step 1: Register all six tools in `src/lib/memory/answer-config.ts`**

```ts
import { createToolRegistry } from "../tools/registry";
import type { ToolRegistry } from "../tools/registry";
import { searchMemoryTool } from "../tools/search-memory";
import { addMemoryNoteTool } from "../tools/add-memory-note";
import { webSearchTool } from "../tools/web-search";
import { webFetchTool } from "../tools/web-fetch";
import { apolloSearchPeopleTool, apolloEnrichContactTool } from "../tools/apollo";

export function memoryRegistry(): ToolRegistry {
  return createToolRegistry([
    searchMemoryTool,
    addMemoryNoteTool,
    webSearchTool,
    webFetchTool,
    apolloSearchPeopleTool,
    apolloEnrichContactTool,
  ]);
}
```

- [ ] **Step 2: Allow the `enrich` class in `src/lib/memory/answer.ts`**

In `answerWithMemory`, extend the `allowedClasses` set (currently
`new Set(["read", "write_internal"])`) to include `"enrich"`:

```ts
    // Chat runs execute read + record-as-note (write_internal) + external
    // enrichment (enrich: web_search / web_fetch / apollo_*). enrich is
    // allowed freely per Fisher's 2026-07-15 call; per-run cost control
    // (credit caps, per-tool budgets) is an URGENT post-R9 follow-up — see
    // progress.md and the session-history-cap ticket's "Related" note.
    allowedClasses: new Set(["read", "write_internal", "enrich"]),
```

- [ ] **Step 3: Update the `memoryRegistry` composition test in
`tests/memory-answer.test.ts`**

The existing test asserts the registry holds exactly `search_memory` +
`add_memory_note` and that permission filtering works. Update it to:
- `memoryRegistry().get(name)` is defined for all six tool names.
- `memoryRegistry().list(new Set(["read", "write_internal", "enrich"]))`
  returns all six tools.
- `memoryRegistry().list(new Set(["read"]))` returns only `search_memory`
  (permission gate still filters `enrich`/`write_internal` out) — this proves
  the class gate holds and enrich is not leaked to a read-only run.

**Acceptance criteria:**
- `memoryRegistry()` registers all six tools; no duplicate-name throw from
  `createToolRegistry`.
- `answerWithMemory` runs with `allowedClasses` including `"enrich"`, so
  `registry.list(allowed)` (inside the loop) exposes the four external tools
  to the model.
- The permission gate still filters by class: a `read`-only allowed set
  exposes only `search_memory`.

**Verify:**
```bash
export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"
npx vitest run tests/memory-answer.test.ts
```
Expected: PASS (updated composition test green; no other memory-answer test
regresses).

- [ ] **Step 4: Commit**

```bash
git add src/lib/memory/answer-config.ts src/lib/memory/answer.ts tests/memory-answer.test.ts
git commit -m "feat(r7): register web/apollo tools into chat registry + allow enrich class"
```

- [ ] **Step 5: Final slice gate** (full suite + lint + build, run last)

```bash
export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"
npx vitest run && npm run lint && npm run build
```
Expected: full suite PASS with zero NEW failures vs. baseline (the 78 legacy
better-sqlite3 suites stay red — that is the standing gate, not a regression);
lint = only the pre-existing `embed.ts` warning; build GREEN (no new routes,
these are library modules + a registry change).

> **PR-body note (required, per Addendum 2):** state loudly that chat runs now
> allow the `enrich` class freely, and that per-run cost control (credit caps,
> per-tool budgets) is an URGENT post-R9 follow-up — not optional polish.

---

## Tests

| File | Covers |
|---|---|
| `tests/web-search-tool.test.ts` | `web_search`: permission class, missing-args, missing-key (clean error), mocked success mapping, mocked non-OK error, fetch-rejects (network failure), malformed-JSON response body, conditional live smoke (`TAVILY_API_KEY` present) |
| `tests/web-fetch-tool.test.ts` | `web_fetch`: permission class, `htmlToText` unit behavior, `isBlockedIp` range table (loopback/private/link-local/metadata/CGNAT/ULA + public allow), non-http(s) rejection, unparseable URL rejection, **SSRF host-refusal (blocked before fetch)**, **redirect-to-private refusal (per-hop re-validation)**, **too-many-redirects**, mocked success + content-type + tag-stripping, size-cap + `truncated` flag, mocked non-OK error, fetch-rejects (network failure), response-body-read failure, conditional live smoke (`SOURCECADO_RUN_LIVE_SMOKE` opt-in) |
| `tests/apollo-tools.test.ts` | `apollo_search_people` + `apollo_enrich_contact`: permission class, args-schema refine rejections, missing-key (clean error) for both, mocked success mapping for both, fetch-rejects (network failure) for both, malformed-JSON response body for both — no live smoke (deferred) |
| `tests/memory-answer.test.ts` (updated) | chat registry composition: `memoryRegistry()` now holds all six tools; `list(read+write_internal+enrich)` returns all six; `list(read)` still returns only `search_memory` (class gate holds, enrich not leaked to read-only runs) |

All three files follow the existing `tests/echo-tool.test.ts` pattern: call
`tool.execute()` directly with `{ db: getDb(), runId: 0, parentStepId: 0 }`,
no orchestrator, no ledger assertions (those are covered once, generically,
by R3's own orchestrator tests).

---

## Judgment calls

- **`web_fetch` size cap is a post-fetch string slice, not a streaming
  byte-limit reader.** `res.text()` fully buffers the response, then the
  decoded text is truncated to `WEB_FETCH_MAX_CHARS`. This satisfies "size
  capped" per the spec with far simpler code and tests than a
  `ReadableStream` reader loop. If a future incident shows real abuse via
  huge unbounded downloads, upgrade to a true streaming cap then — not now.
- **Live-smoke gating differs per tool because only two of the three need a
  key.** `web_search`'s live-smoke test skips unless `TAVILY_API_KEY` is set
  (matches "one live smoke per key present" literally). `web_fetch` needs no
  key, so an unconditional live-smoke test would silently hit the real
  network in every CI run; it's gated instead on a new opt-in env var,
  `SOURCECADO_RUN_LIVE_SMOKE`. Apollo has no live-smoke test at all — the
  spec explicitly defers Apollo live smoke until the key is provided.
- **Apollo endpoint/field mapping is inferred from Apollo's public API**
  (`POST /v1/mixed_people/search` for search, `POST /v1/people/match` for
  enrich; snake_case request/response fields) since the spec only says
  "basics." This is the concrete contract the mocked tests pin down; if the
  real Apollo response shape differs when the live key arrives, the mapping
  in `apollo.ts` is the place to adjust it — not the tool's public
  args/result shape, which stays as documented here.
- **Registry wiring is IN SCOPE (Task 5), reversing the original v2 cut.**
  ~~This slice does not register the four tools into any live registry.~~
  SUPERSEDED by Fisher's 2026-07-21 decision and the addendum: Task 5
  registers all four into `memoryRegistry()` and chat runs allow `enrich`.
- **`web_fetch` SSRF defense is resolve-then-check + per-hop re-validation,
  not IP-pinning.** `assertPublicHost` resolves the host and rejects any
  non-public address, re-run on every redirect hop under `redirect:"manual"`.
  It does NOT pin the resolved IP for the connection, so a DNS-rebind race
  (public on our lookup, private on the OS's connect-time lookup) is a known
  residual; the 15s timeout bounds it and per-hop re-validation closes the
  common redirect-to-internal vector. IP-pinning (a custom fetch dispatcher)
  is deliberately out of scope as over-engineering for v1 — revisit only if an
  incident shows rebinding abuse.
- **Apollo args require a minimal "at least one identifying combo" via
  `.refine()`** — `organizationName` or `personTitles` for search;
  `email` or `firstName`+`lastName` for enrich — chosen as the leanest usable
  contract since the spec doesn't pin exact required fields for the
  "basics" scope.

---

## Eng Review (2026-07-14)

Reviewed headlessly (spawned session — auto-decided recommendations instead of
an interactive AskUserQuestion pass). Grounded against the real repo on
`feat/a-chat-streaming`: `src/lib/tools/types.ts`, `registry.ts`,
`search-memory.ts`, `add-memory-note.ts`, `echo.ts`, `tests/echo-tool.test.ts`,
`src/lib/memory/answer-config.ts`, `src/lib/db.ts`, `.env.example`,
`package.json`, `vitest.config.ts`, and the R-contracts brief
(`2026-07-14-r-contracts-brief.md`) + sprint spec (`2026-07-14-runtime-solidification-sprint-spec.md`, §R7, Decisions locked, Acceptance Criteria, Testing Plan).

### What already exists (reused correctly)
`src/lib/tools/types.ts` and `registry.ts` are byte-for-byte what the plan
claims (`Tool`/`PermissionClass`/`ToolContext`/`ToolRegistry`, "unchanged" per
the contracts brief's file-ownership row) — verified by reading both files.
The three new tool files follow the exact shape and test pattern of the
existing `echo.ts`/`search-memory.ts`/`add-memory-note.ts` +
`tests/echo-tool.test.ts` (direct `tool.execute()` calls, no orchestrator, no
new abstractions). No duplication of existing functionality.

### Contracts-brief conformance
Conforms. All four tools are `permissionClass: "enrich"`; none import or call
`executeTool()`; `memoryRegistry()` in `answer-config.ts` is untouched (still
registers only `search_memory`, confirmed by reading the file); `.env.example`
insertion point (`after the existing ANTHROPIC_API_KEY= line`) matches the
real file's last line. The "Deferred to v2" wiring cut is stated accurately
and matches the brief's own wording verbatim.

### Architecture
- **[P1] (confidence: 8/10) No timeout on any outbound `fetch()` call** —
  `web-search.ts:92`, `web-fetch.ts:295`, `apollo.ts:476` all call `fetch()`
  with no `signal`/`AbortSignal.timeout(...)`. Neither this slice nor the R3
  orchestrator contract (`executeTool()` in the contracts brief §4) bounds a
  tool call's execution time. A hung Tavily/Apollo endpoint, or — once
  `web_fetch` is wired into a live registry — a slow/unresponsive arbitrary
  URL, has unbounded execution time and can pin a run's `tool` step
  "running" indefinitely. This is a "hosted team app" (per `CLAUDE.md`), not
  a local CLI, so a stuck run affects a real user's workflow, not just the
  operator. **Recommend:** add `signal: AbortSignal.timeout(15_000)` (native,
  Layer 1, no library) to all three `fetch()` calls, plus one test per tool
  asserting the rejection surfaces as a clean, non-crashing error. Cheap fix,
  directly reduces blast radius.
- **[P2] (confidence: 8/10) `web_fetch` has no SSRF defense, and the redirect
  path is TOCTOU** — `web-fetch.ts:285-295` validates only the *input* URL's
  protocol, then calls `fetch(parsed.toString(), { redirect: "follow" })`.
  A remote server can redirect an `https://` request to an internal address
  (e.g. cloud metadata `169.254.169.254`, `localhost`, RFC1918 ranges) and the
  protocol/host check is never re-applied per hop. **Dormant today** — R7
  correctly does not wire any tool into a live, reachable registry (confirmed:
  `memoryRegistry()` untouched), so nothing can reach this path yet. But it
  must be closed before the contracts brief's own "Wire enrich tools into
  live registry" v2 ticket lands, since `web_fetch` will then be reachable
  from agent/model-influenced input (prompt injection via search results or
  memory content choosing the URL). **Recommend:** don't block this slice on
  it, but add one line to this plan's "Deferred to v2" / Judgment calls
  referencing it explicitly (so it isn't a silent gap when that ticket is
  picked up) — e.g. "add a private-IP/metadata-address denylist and
  `redirect: 'manual'` + manual re-validation per hop before `web_fetch`
  becomes reachable."

### Code quality
- **[P3] (confidence: 9/10) Test-count nit in `tests/web-fetch-tool.test.ts`
  Verify step** — the plan's own acceptance text says "Expected: PASS (7
  tests; the live-smoke test shows as skipped...)" but the file as written
  defines 8 `it`/`it.skipIf` cases: `is an enrich-class tool`, `htmlToText
  strips tags...`, `rejects a non-http(s) url`, `rejects an unparseable url`,
  `fetches a page and returns HTML-stripped text...`, `caps oversized
  responses...`, `throws a clean error on a non-OK response`, and the
  `it.skipIf` live-smoke case — 7 always-run + 1 conditionally-skipped = 8
  total, not 7. (`web-search-tool.test.ts`'s "6 tests" and
  `apollo-tools.test.ts`'s "8 tests" are both counted correctly.) Cosmetic
  only — fix the number so `npx vitest run` output isn't second-guessed
  against a wrong expectation.
- No DRY violation worth a shared abstraction: the "missing API key → clean
  error" check appears 3 times (Tavily inline, Apollo's own
  `requireApolloApiKey()` reused twice within `apollo.ts`) — 2 call sites
  across files is below the threshold for extracting a cross-file helper.
  Revisit if a 4th key-gated tool shows up.

### Test coverage
Every codepath the plan lists in its own "Tests" table is covered by the
tests as written (permission class, args-schema rejection, missing-key clean
error, mocked success mapping, mocked non-OK error, size-cap/truncation,
conditional live smoke). Two realistic production failure modes are common
to all three tools and are untested in all three files:

```
CODE PATHS (all 3 tool files)                          STATUS
[+] fetch() resolves with ok:false                      [★★  TESTED] all 3 files
[+] missing API key (web_search, apollo x2)              [★★  TESTED] all 3 files
[+] fetch() itself rejects (DNS/ECONNREFUSED/TLS/abort)  [GAP] — no test in any of the 3 files
[+] res.json() throws on malformed 200 body (Tavily, Apollo x2) [GAP] — no test
[+] web_fetch: non-http(s) / unparseable URL             [★★★ TESTED]
[+] web_fetch: oversized response truncation             [★★★ TESTED]

COVERAGE: 4/6 realistic external-call paths tested (67%) | GAPS: 2
```

- **[P2] (confidence: 8/10)** No test simulates `fetch()` rejecting (network
  failure) in any of the three files — only HTTP-level `ok:false` is
  exercised. This is the single most common real-world failure mode for an
  outbound call and is not proven to degrade cleanly (it will, functionally,
  via the R3 orchestrator's generic catch per the contracts brief §4 — but
  no test proves that for these three tools specifically).
- **[P2] (confidence: 7/10)** No test simulates `res.json()` throwing on a
  malformed/non-JSON 200 response for `web-search.ts` or `apollo.ts`. Same
  runtime outcome as above (caught upstream, doesn't crash) but the plan's
  own stated goal — "throws a clean error... never a crash" — is only tested
  for the missing-key path, not for this equally-plausible failure.
- Not flagging these as **critical gaps** per the skill's own bar (a gap
  needs no test AND no error handling AND a silent failure to qualify) —
  both paths do propagate to a real error via the orchestrator, they're just
  untested and the resulting message quality is unverified.

### Performance
No new DB access, no N+1, all response arrays bounded (`limit`/`per_page` capped
at 10/25). `web_fetch`'s `WEB_FETCH_MAX_CHARS` cap is applied after
`res.text()` fully buffers the response — the plan's own Judgment calls
section already surfaces this exact tradeoff and consciously defers a
streaming reader ("if a future incident shows real abuse... upgrade then —
not now"). Reasonable; no new finding.

### NOT in scope (confirmed accurate, not silently dropped)
- Wiring these 4 tools into `memoryRegistry()`/any live registry — named v2
  ticket in the contracts brief, correctly not attempted here.
- Apollo live smoke — deferred until `APOLLO_API_KEY` is provided (Decisions
  locked in the sprint spec).
- `.env.example` provider docs — R9's job, not R7's.
- SSRF/redirect hardening for `web_fetch` (see Architecture P2 above) —
  should be named explicitly in this plan's own deferred list, not left
  implicit.

### Worktree parallelization
This slice has near-zero real dependency on R3 landing first: it needs only
`src/lib/tools/types.ts`/`registry.ts`, which already exist unchanged on
`main` today (verified), and none of its own files import or call
`executeTool()`. The "Depends on: R3" note is contractual/notional, not a
code dependency — R7 could be built and merged in parallel with R3 in a
separate worktree with no file overlap (`web-search.ts`/`web-fetch.ts`/
`apollo.ts`/three test files vs. `orchestrator.ts`). Sequential per the
contracts brief's dependency graph is fine and lower-risk; flagging the
parallel option since it's free given no shared files.

### Implementation Tasks
- [x] **T1 (P1, human: ~30min / CC: ~10min)** — RESOLVED — all 3 tool files —
  add `signal: AbortSignal.timeout(15_000)` to each `fetch()` call
  - Surfaced by: Architecture — no timeout on outbound calls
  - Files: `src/lib/tools/web-search.ts`, `web-fetch.ts`, `apollo.ts`
  - Verify: `AbortSignal.timeout(15_000)` now passed on all three `fetch()`
    calls (Tasks 1-3, Step 1 code blocks above)
- [x] **T2 (P2, human: ~20min / CC: ~10min)** — RESOLVED — test coverage —
  added a network-rejection test (`fetch` mock rejects) to all 3 test files,
  plus a malformed-response-body test to each (`json()` throws for
  `web-search.ts`/`apollo.ts`; `text()` throws for `web-fetch.ts`, since that
  tool reads the body via `res.text()`, not `res.json()`)
  - Surfaced by: Test review — 2 untested realistic failure paths
  - Files: `tests/web-search-tool.test.ts`, `web-fetch-tool.test.ts`,
    `apollo-tools.test.ts`
  - Verify: `npx vitest run tests/web-search-tool.test.ts
    tests/web-fetch-tool.test.ts tests/apollo-tools.test.ts`
- [x] **T3 (P3, human: ~2min / CC: ~1min)** — RESOLVED — doc fix — corrected
  `tests/web-fetch-tool.test.ts` Verify step to "10 tests: 9 always-run, 1
  conditionally skipped" (count now includes the 2 new T2 tests)
  - Surfaced by: Code quality — test-count nit
  - Files: this plan file, Task 2's Verify section
- [x] **T4** — SUPERSEDED by the 2026-07-21 reconciliation. The SSRF gap is
  no longer a v2 note: it is now built and tested in Task 2, because Task 5
  makes `web_fetch` model-reachable. The dormant-gap framing (and the whole
  "Wire enrich tools into live registry" v2 ticket) no longer applies.

### Completion summary
- Architecture: 2 issues found (1 P1 timeout, 1 P2 SSRF-dormant)
- Code quality: 1 issue found (test-count nit), no DRY violations
- Test review: diagram produced, 2 gaps identified (both P2, both non-critical)
- Performance: 0 issues found (existing tradeoff already surfaced by the plan)
- NOT in scope: written above
- What already exists: written above
- Failure modes: 0 critical gaps (both untested paths are caught upstream by
  the R3 orchestrator per the contracts brief; neither is silent)
- Outside voice: skipped (headless/spawned session — no interactive
  cross-model pass run)
- Parallelization: 1 opportunity noted (R7 vs. R3, zero file overlap)

### VERDICT

**approve (revised)** — all three must-fix items are now resolved in this
plan: `AbortSignal.timeout(15_000)` is on all three `fetch()` calls (T1);
network-rejection and malformed-response-body tests were added to all three
test files (T2); and the `tests/web-fetch-tool.test.ts` Verify step test
count is corrected (T3). T4 (naming the SSRF gap explicitly) remains open as
a should-fix, non-blocking documentation follow-up so the v2 wiring ticket
doesn't silently inherit an unticketed gap.

**Must-fix before merge (all resolved):**
1. ~~Add `AbortSignal.timeout(...)` to all three `fetch()` calls (T1).~~ Done.
2. ~~Add network-rejection + malformed-response-body test coverage to all
   three test files (T2).~~ Done.
3. ~~Fix the `tests/web-fetch-tool.test.ts` Verify step test count, 7 → 8
   (T3).~~ Done — corrected to 10 (9 always-run, 1 conditionally skipped).

**Should-fix (not blocking, but don't let it silently drop):**
4. Add one line to this plan's Judgment calls / NOT-in-scope naming the
   `web_fetch` SSRF/redirect gap as a precondition of the v2 "wire into live
   registry" ticket (T4).

**NO UNRESOLVED DECISIONS**

## Addendum (2026-07-15, Fisher's decision — supersedes the v2 wiring cut)

The "do not wire tools into a live registry" cut is REVERSED. R7 now also:
1. Registers web_search, web_fetch, apollo_search_people, apollo_enrich_contact
   into the chat registry (`memoryRegistry()` in answer-config.ts — rename to
   `chatRegistry()` if cleaner), alongside search_memory and add_memory_note
   (the latter registered by the system-prompt hotfix, see
   docs/superpowers/plans/2026-07-15-sourcing-agent-system-prompt.md).
2. Because web_fetch becomes model-reachable, the SSRF/redirect hardening
   flagged in this plan's open questions is now IN SCOPE for R7, not v2:
   block non-http(s) schemes, private/link-local/metadata IP ranges
   (169.254.169.254 etc.), and re-validate on redirects. With tests.
3. Permission classes still gate execution: chat runs default to read+reason —
   decide explicitly which classes chat runs allow (enrich?) and record the
   decision in the PR body.

Addendum 2 (2026-07-15): permission-class decision — chat runs ALLOW the
`enrich` class freely when these tools register (Fisher's call). Ship with a
loud note in the PR + progress.md: per-run cost control (credit caps, per-tool
budgets) is an URGENT post-R9 follow-up, not optional polish.
