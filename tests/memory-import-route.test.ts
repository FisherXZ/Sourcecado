import { vi } from "vitest";

const { ingestFilesMock } = vi.hoisted(() => ({ ingestFilesMock: vi.fn() }));
vi.mock("@/lib/memory/ingest", () => ({ ingestFiles: ingestFilesMock }));
vi.mock("@/lib/db", () => ({ getDb: vi.fn().mockReturnValue({}) }));

import { POST } from "@/app/api/memory/import/route";

function bytes(text: string): Uint8Array {
  return new TextEncoder().encode(text);
}

function importRequest(form: FormData): Request {
  return new Request("http://localhost/api/memory/import", { method: "POST", body: form });
}

describe("POST /api/memory/import", () => {
  beforeEach(() => {
    ingestFilesMock.mockReset();
    ingestFilesMock.mockResolvedValue({ processed: 2, skipped: 0, skippedFiles: [] });
  });

  it("accepts multipart uploads, calls ingestFiles with {name,bytes}, returns the result", async () => {
    const form = new FormData();
    form.append("files", new File([bytes("# Acme\n\nx")], "acme.md"));
    form.append("files", new File([bytes("name,status\nJane,contacted\n")], "contacts.csv"));

    const res = await POST(importRequest(form));

    expect(res.status).toBe(200);
    await expect(res.json()).resolves.toMatchObject({ processed: 2, skipped: 0 });

    expect(ingestFilesMock).toHaveBeenCalledTimes(1);
    const files = ingestFilesMock.mock.calls[0][1] as { name: string; bytes: Uint8Array }[];
    expect(files.map((f) => f.name)).toEqual(["acme.md", "contacts.csv"]);
    expect(files[0].bytes).toBeInstanceOf(Uint8Array);
  });

  it("returns 200 with the per-file skip list — one bad file does not sink the batch", async () => {
    ingestFilesMock.mockResolvedValue({
      processed: 1,
      skipped: 1,
      skippedFiles: [{ path: "image.png", category: "unsupported-type", reason: "Unsupported file extension: image.png" }],
    });

    const form = new FormData();
    form.append("files", new File([bytes("ok")], "ok.txt"));
    form.append("files", new File([bytes("x")], "image.png"));

    const res = await POST(importRequest(form));

    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.skippedFiles[0].path).toBe("image.png");
  });

  it("returns 400 when the request is not multipart/form-data", async () => {
    const res = await POST(
      new Request("http://localhost/api/memory/import", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({}),
      })
    );

    expect(res.status).toBe(400);
    expect(ingestFilesMock).not.toHaveBeenCalled();
  });

  it("returns 400 when no files are attached", async () => {
    const res = await POST(importRequest(new FormData()));

    expect(res.status).toBe(400);
    expect(ingestFilesMock).not.toHaveBeenCalled();
  });
});
