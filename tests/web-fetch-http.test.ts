import { createServer, type Server } from "node:http";
import { AddressInfo } from "node:net";
import { gzipSync } from "node:zlib";
import { fetchCapped, pinnedLookup } from "@/lib/tools/web-fetch-http";

// Transport-level tests. These run against a real loopback HTTP server rather
// than a mock, because the two things under test — that the connection goes to
// the pinned address, and that an oversized body is aborted instead of buffered
// — are properties of the socket, not of our code's control flow. A mock would
// assert we called something, not that the bytes stopped arriving.

function listen(handler: Parameters<typeof createServer>[1]): Promise<{ server: Server; port: number }> {
  return new Promise((resolve) => {
    const server = createServer(handler);
    server.listen(0, "127.0.0.1", () => {
      resolve({ server, port: (server.address() as AddressInfo).port });
    });
  });
}

const close = (server: Server) => new Promise<void>((resolve) => server.close(() => resolve()));

describe("pinnedLookup", () => {
  it("returns the pinned address for any hostname (callback form)", async () => {
    const result = await new Promise<[unknown, unknown, unknown]>((resolve) => {
      pinnedLookup("93.184.216.34", 4)("anything.test", {}, (...args) => resolve(args as never));
    });
    expect(result).toEqual([null, "93.184.216.34", 4]);
  });

  it("returns the pinned address in array form when options.all is set", async () => {
    const result = await new Promise<[unknown, unknown]>((resolve) => {
      pinnedLookup("93.184.216.34", 4)("anything.test", { all: true }, (...args) => resolve(args as never));
    });
    expect(result[0]).toBeNull();
    expect(result[1]).toEqual([{ address: "93.184.216.34", family: 4 }]);
  });
});

describe("fetchCapped", () => {
  it("connects to the pinned address while keeping the hostname in the Host header", async () => {
    // The rebinding fix, proven at the transport layer: the URL says
    // "pinned.test" (which does not resolve at all) and the connection still
    // lands on our loopback server because the address was pinned.
    const { server, port } = await listen((req, res) => {
      res.writeHead(200, { "content-type": "text/plain" });
      res.end(`host=${req.headers.host}`);
    });
    try {
      const res = await fetchCapped({
        url: `http://pinned.test:${port}/page`,
        address: "127.0.0.1",
        family: 4,
        maxBytes: 10_000,
        timeoutMs: 5_000,
      });
      expect(res.status).toBe(200);
      expect(res.text).toBe(`host=pinned.test:${port}`);
      expect(res.contentType).toBe("text/plain");
      expect(res.truncated).toBe(false);
    } finally {
      await close(server);
    }
  });

  it("aborts an oversized body at the cap instead of buffering it", async () => {
    let pushed = 0;
    let clientGone = false;
    const { server, port } = await listen((_req, res) => {
      res.writeHead(200, { "content-type": "text/plain" });
      const chunk = "a".repeat(64 * 1024);
      const pump = (): void => {
        // 8MB ceiling so a broken cap fails the test instead of hanging forever.
        if (clientGone || pushed > 8 * 1024 * 1024) return void res.end();
        pushed += chunk.length;
        if (res.write(chunk)) setImmediate(pump);
        else res.once("drain", pump);
      };
      res.on("close", () => { clientGone = true; });
      pump();
    });
    try {
      const res = await fetchCapped({
        url: `http://127.0.0.1:${port}/big`,
        address: "127.0.0.1",
        family: 4,
        maxBytes: 100_000,
        timeoutMs: 5_000,
      });
      expect(res.truncated).toBe(true);
      expect(res.text.length).toBe(100_000);
      // The point of the fix: the transfer stopped early. Without the abort the
      // server would have pushed its full 8MB.
      expect(pushed).toBeLessThan(4 * 1024 * 1024);
    } finally {
      await close(server);
    }
  });

  it("does not mark a body that lands exactly on the cap as truncated", async () => {
    const exact = "b".repeat(4096);
    const { server, port } = await listen((_req, res) => {
      res.writeHead(200, { "content-type": "text/plain" });
      res.end(exact);
    });
    try {
      const res = await fetchCapped({
        url: `http://127.0.0.1:${port}/exact`,
        address: "127.0.0.1",
        family: 4,
        maxBytes: exact.length,
        timeoutMs: 5_000,
      });
      expect(res.truncated).toBe(false);
      expect(res.text).toBe(exact);
    } finally {
      await close(server);
    }
  });

  it("returns a redirect's location without reading its body", async () => {
    const { server, port } = await listen((_req, res) => {
      res.writeHead(302, { location: "https://elsewhere.test/next" });
      res.end("ignored body");
    });
    try {
      const res = await fetchCapped({
        url: `http://127.0.0.1:${port}/go`,
        address: "127.0.0.1",
        family: 4,
        maxBytes: 10_000,
        timeoutMs: 5_000,
      });
      expect(res.status).toBe(302);
      expect(res.location).toBe("https://elsewhere.test/next");
      expect(res.text).toBe("");
    } finally {
      await close(server);
    }
  });

  it("closes the socket on a redirect instead of draining its body", async () => {
    // Without the destroy, res.resume() keeps reading in the background after
    // the promise settles — past the byte cap and past the cleared deadline.
    let pushed = 0;
    let clientGone = false;
    const { server, port } = await listen((_req, res) => {
      res.writeHead(302, { location: "https://elsewhere.test/next" });
      const chunk = "x".repeat(64 * 1024);
      const pump = (): void => {
        if (clientGone || pushed > 8 * 1024 * 1024) return void res.end();
        pushed += chunk.length;
        if (res.write(chunk)) setImmediate(pump);
        else res.once("drain", pump);
      };
      res.on("close", () => { clientGone = true; });
      pump();
    });
    try {
      const res = await fetchCapped({
        url: `http://127.0.0.1:${port}/go`,
        address: "127.0.0.1",
        family: 4,
        maxBytes: 10_000,
        timeoutMs: 5_000,
      });
      expect(res.status).toBe(302);
      await new Promise((r) => setTimeout(r, 250)); // let a leaked drain accumulate
      expect(clientGone).toBe(true);
      expect(pushed).toBeLessThan(4 * 1024 * 1024);
    } finally {
      await close(server);
    }
  });

  it("requests identity encoding and refuses a compressed response", async () => {
    // http.request sends no Accept-Encoding of its own and does not decompress,
    // so an encoded body would reach htmlToText() as binary garbage.
    let accept: string | undefined;
    const { server, port } = await listen((req, res) => {
      accept = req.headers["accept-encoding"];
      res.writeHead(200, { "content-type": "text/html", "content-encoding": "gzip" });
      res.end(gzipSync(Buffer.from("<h1>Hello</h1>")));
    });
    try {
      await expect(
        fetchCapped({
          url: `http://127.0.0.1:${port}/gz`,
          address: "127.0.0.1",
          family: 4,
          maxBytes: 10_000,
          timeoutMs: 5_000,
        }),
      ).rejects.toThrow(/refusing gzip-encoded response/);
      expect(accept).toBe("identity");
    } finally {
      await close(server);
    }
  });

  it("does not reuse pooled sockets between calls to the same host and port", async () => {
    // The default global agent keys its pool on host:port and ignores `lookup`,
    // so a reused socket could serve a hop that never ran pinnedLookup.
    let connections = 0;
    const { server, port } = await listen((_req, res) => {
      res.writeHead(200, { "content-type": "text/plain" });
      res.end("ok");
    });
    server.on("connection", () => { connections++; });
    try {
      const opts = { address: "127.0.0.1", family: 4, maxBytes: 10_000, timeoutMs: 5_000 };
      await fetchCapped({ url: `http://127.0.0.1:${port}/one`, ...opts });
      await fetchCapped({ url: `http://127.0.0.1:${port}/two`, ...opts });
      expect(connections).toBe(2);
    } finally {
      await close(server);
    }
  });

  it("returns a non-OK status rather than throwing (the caller decides)", async () => {
    const { server, port } = await listen((_req, res) => {
      res.writeHead(404, { "content-type": "text/plain" });
      res.end("nope");
    });
    try {
      const res = await fetchCapped({
        url: `http://127.0.0.1:${port}/missing`,
        address: "127.0.0.1",
        family: 4,
        maxBytes: 10_000,
        timeoutMs: 5_000,
      });
      expect(res.status).toBe(404);
      expect(res.statusText).toBe("Not Found");
    } finally {
      await close(server);
    }
  });

  it("rejects when the connection fails", async () => {
    const { server, port } = await listen((_req, res) => res.end());
    await close(server); // nothing is listening on `port` any more
    await expect(
      fetchCapped({
        url: `http://127.0.0.1:${port}/gone`,
        address: "127.0.0.1",
        family: 4,
        maxBytes: 10_000,
        timeoutMs: 5_000,
      }),
    ).rejects.toThrow(/ECONNREFUSED/);
  });

  it("rejects when the response stalls past the timeout", async () => {
    const { server, port } = await listen((_req, res) => {
      res.writeHead(200, { "content-type": "text/plain" });
      res.write("start"); // headers sent, body never completes
    });
    try {
      await expect(
        fetchCapped({
          url: `http://127.0.0.1:${port}/stall`,
          address: "127.0.0.1",
          family: 4,
          maxBytes: 10_000,
          timeoutMs: 300,
        }),
      ).rejects.toThrow(/timed out/);
    } finally {
      await close(server);
    }
  });
});
