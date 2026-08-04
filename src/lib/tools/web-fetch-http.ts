import { request as httpRequest } from "node:http";
import { request as httpsRequest } from "node:https";
import type { IncomingMessage } from "node:http";
import type { LookupFunction } from "node:net";

// Low-level transport for web_fetch. Split from web-fetch.ts because it answers
// a different question: web-fetch.ts decides *whether* a URL may be fetched,
// this decides *how* the bytes arrive. Two things global fetch() cannot do:
//
//  1. Pin the connection to an already-validated IP. `fetch` re-resolves the
//     hostname at connect time, so an attacker-controlled DNS record can return
//     a public address to our SSRF check and a private one to the socket.
//  2. Stop reading at a byte limit. `res.text()` buffers the whole body first,
//     so a size cap applied afterwards bounds the string, not memory.

export interface CappedResponse {
  status: number;
  statusText: string;
  contentType: string | null;
  location: string | null;
  text: string;
  truncated: boolean;
}

export interface FetchCappedOptions {
  url: string;
  /** Pre-validated address to pin the connection to. */
  address: string;
  family: number;
  maxBytes: number;
  timeoutMs: number;
}

// Hands the connection the address the caller already validated instead of
// re-resolving. SNI and certificate validation still use the hostname, so
// pinning costs no TLS safety — only the address lookup is short-circuited.
export function pinnedLookup(address: string, family: number): LookupFunction {
  return (_hostname, options, callback) => {
    if (options?.all === true) callback(null, [{ address, family }]);
    else callback(null, address, family);
  };
}

function header(res: IncomingMessage, name: string): string | null {
  const value = res.headers[name];
  if (value === undefined) return null;
  return Array.isArray(value) ? (value[0] ?? null) : value;
}

export function fetchCapped(opts: FetchCappedOptions): Promise<CappedResponse> {
  const url = new URL(opts.url);
  const isHttps = url.protocol === "https:";
  const request = isHttps ? httpsRequest : httpRequest;

  return new Promise<CappedResponse>((resolve, reject) => {
    let settled = false;
    const settle = (fn: () => void): void => {
      if (settled) return;
      settled = true;
      clearTimeout(deadline);
      fn();
    };

    const req = request(
      {
        protocol: url.protocol,
        host: url.hostname, // Host header, SNI, and cert validation all use this
        port: url.port || (isHttps ? 443 : 80),
        path: `${url.pathname}${url.search}`,
        lookup: pinnedLookup(opts.address, opts.family), // ...only the socket is pinned
      },
      (res) => {
        const base = {
          status: res.statusCode ?? 0,
          statusText: res.statusMessage ?? "",
          contentType: header(res, "content-type"),
          location: header(res, "location"),
        };

        // Redirect bodies are never used — discard rather than buffer.
        if (base.status >= 300 && base.status < 400) {
          res.resume();
          settle(() => resolve({ ...base, text: "", truncated: false }));
          return;
        }

        const chunks: Buffer[] = [];
        let received = 0;
        let truncated = false;

        const body = (): string =>
          Buffer.concat(chunks).subarray(0, opts.maxBytes).toString("utf8");

        res.on("data", (chunk: Buffer) => {
          if (settled) return;
          chunks.push(chunk);
          received += chunk.length;
          if (received <= opts.maxBytes) return;
          // Past the cap: kill the connection so the rest of the body is never
          // transferred. Overshoot is bounded by one socket read (64KB), which
          // is what lets a body landing exactly on the cap be reported as
          // complete rather than truncated.
          truncated = true;
          req.destroy();
          settle(() => resolve({ ...base, text: body(), truncated }));
        });

        res.on("end", () => settle(() => resolve({ ...base, text: body(), truncated })));
        res.on("error", (err) => settle(() => reject(err)));
      },
    );

    const deadline = setTimeout(() => {
      req.destroy(new Error(`web_fetch: timed out after ${opts.timeoutMs}ms`));
    }, opts.timeoutMs);

    // Fires for connection failures and for our own destroy() on timeout. A
    // destroy() after the cap was hit arrives post-settle and is ignored.
    req.on("error", (err) => settle(() => reject(err)));
    req.end();
  });
}
