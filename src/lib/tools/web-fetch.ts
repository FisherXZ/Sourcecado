import { z } from "zod";
import { lookup } from "node:dns/promises";
import { fetchCapped } from "./web-fetch-http";
import type { Tool } from "./types";

// Bytes, not characters: the cap now bounds how much we read off the socket, so
// it is a real memory bound rather than a slice applied to an already-buffered
// body.
export const WEB_FETCH_MAX_BYTES = 500_000;
const MAX_REDIRECTS = 5;
const REQUEST_TIMEOUT_MS = 15_000;

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
// IPv4-mapped IPv6 is unwrapped and checked as IPv4 in BOTH presentation forms
// the resolver / URL parser can produce: dotted (`::ffff:169.254.169.254`) and
// hextet (`::ffff:a9fe:a9fe`, which the WHATWG URL parser and some getaddrinfo
// implementations emit). Any other `::ffff:` form we can't cleanly unwrap fails
// closed. Malformed IPv4 fails closed; unrecognised-but-valid IPv6 (global
// unicast) is allowed.
export function isBlockedIp(ip: string): boolean {
  const mappedDotted = /^::ffff:(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})$/i.exec(ip);
  const mappedHex = /^::ffff:([0-9a-f]{1,4}):([0-9a-f]{1,4})$/i.exec(ip);
  let addr = ip;
  if (mappedDotted) {
    addr = mappedDotted[1];
  } else if (mappedHex) {
    const hi = parseInt(mappedHex[1], 16);
    const lo = parseInt(mappedHex[2], 16);
    addr = `${hi >> 8}.${hi & 0xff}.${lo >> 8}.${lo & 0xff}`;
  } else if (/^::ffff:/i.test(ip)) {
    return true; // some other IPv4-mapped form we can't unwrap → fail closed
  }

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
  if (/^fe[89ab]/.test(v6)) return true; // fe80::/10 link-local (fe80–febf)
  if (v6.startsWith("fc") || v6.startsWith("fd")) return true; // fc00::/7 unique-local
  return false; // global unicast IPv6 allowed
}

// Resolve a host (dns.lookup echoes a literal IP verbatim, so this covers
// literal-IP URLs too) and reject if ANY resolved address is non-public.
// Called on every redirect hop.
//
// Returns the address it validated so the caller can pin the connection to it.
// Without that, the transport re-resolves at connect time and a hostname whose
// DNS flips between this check and the socket reaches the address we refused.
export async function assertPublicHost(hostname: string): Promise<{ address: string; family: number }> {
  let addrs: Array<{ address: string; family: number }>;
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
  return addrs[0];
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
      const pinned = await assertPublicHost(parsed.hostname); // re-validated every hop
      finalUrl = parsed.toString();

      // Redirects are followed manually so each hop's host is re-validated, and
      // each hop connects to its own validated address.
      const res = await fetchCapped({
        url: finalUrl,
        address: pinned.address,
        family: pinned.family,
        maxBytes: WEB_FETCH_MAX_BYTES,
        timeoutMs: REQUEST_TIMEOUT_MS,
      });

      if (res.status >= 300 && res.status < 400) {
        if (hop >= MAX_REDIRECTS) {
          throw new Error(`web_fetch: too many redirects (>${MAX_REDIRECTS})`);
        }
        if (!res.location) {
          throw new Error(`web_fetch: redirect ${res.status} with no Location header`);
        }
        target = new URL(res.location, finalUrl).toString();
        continue;
      }

      if (res.status < 200 || res.status >= 300) {
        throw new Error(`Fetch failed: ${res.status} ${res.statusText}`);
      }

      return {
        url: finalUrl,
        contentType: res.contentType,
        text: htmlToText(res.text),
        truncated: res.truncated,
      };
    }
  },
};
