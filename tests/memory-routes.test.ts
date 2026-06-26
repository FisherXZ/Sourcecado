import { vi } from "vitest";

const { listSourcesMock, setArchivedMock, addNoteMock } = vi.hoisted(() => ({
  listSourcesMock: vi.fn(),
  setArchivedMock: vi.fn(),
  addNoteMock: vi.fn(),
}));
vi.mock("@/lib/memory/sources", () => ({
  listSources: listSourcesMock,
  setSourceArchived: setArchivedMock,
}));
vi.mock("@/lib/memory/notes", () => ({ addMemoryNote: addNoteMock }));
vi.mock("@/lib/db", () => ({ getDb: vi.fn().mockReturnValue({}) }));

import { GET as getSources } from "@/app/api/memory/sources/route";
import { POST as archive } from "@/app/api/memory/sources/[id]/archive/route";
import { POST as addNote } from "@/app/api/memory/note/route";

function jsonReq(url: string, body: unknown): Request {
  return new Request(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

describe("GET /api/memory/sources", () => {
  beforeEach(() => listSourcesMock.mockReset());

  it("returns the source list", async () => {
    listSourcesMock.mockResolvedValue([
      { sourceId: "acme", title: "Acme", sourceType: "markdown", updatedAt: "2026-06-25T00:00:00.000Z", archived: false },
    ]);
    const res = await getSources();
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.sources[0].sourceId).toBe("acme");
  });
});

describe("POST /api/memory/sources/[id]/archive", () => {
  beforeEach(() => setArchivedMock.mockReset());

  const req = (body: unknown) => jsonReq("http://localhost/api/memory/sources/acme/archive", body);

  it("archives a source and returns the new state", async () => {
    setArchivedMock.mockResolvedValue({ sourceId: "acme", archived: true });
    const res = await archive(req({ archived: true }), { params: Promise.resolve({ id: "acme" }) });
    expect(res.status).toBe(200);
    await expect(res.json()).resolves.toMatchObject({ sourceId: "acme", archived: true });
    expect(setArchivedMock).toHaveBeenCalledWith(expect.anything(), { sourceId: "acme", archived: true });
  });

  it("defaults to archived=true when the body omits it", async () => {
    setArchivedMock.mockResolvedValue({ sourceId: "acme", archived: true });
    await archive(req({}), { params: Promise.resolve({ id: "acme" }) });
    expect(setArchivedMock).toHaveBeenCalledWith(expect.anything(), { sourceId: "acme", archived: true });
  });

  it("un-archives when archived=false", async () => {
    setArchivedMock.mockResolvedValue({ sourceId: "acme", archived: false });
    await archive(req({ archived: false }), { params: Promise.resolve({ id: "acme" }) });
    expect(setArchivedMock).toHaveBeenCalledWith(expect.anything(), { sourceId: "acme", archived: false });
  });

  it("returns 404 when the source is unknown/not permitted", async () => {
    setArchivedMock.mockResolvedValue(null);
    const res = await archive(req({ archived: true }), { params: Promise.resolve({ id: "ghost" }) });
    expect(res.status).toBe(404);
  });
});

describe("POST /api/memory/note", () => {
  beforeEach(() => addNoteMock.mockReset());

  it("adds a note and returns its sourceId", async () => {
    addNoteMock.mockResolvedValue({ sourceId: "note-abc" });
    const res = await addNote(jsonReq("http://localhost/api/memory/note", { title: "T", text: "body" }));
    expect(res.status).toBe(200);
    await expect(res.json()).resolves.toMatchObject({ sourceId: "note-abc" });
    expect(addNoteMock).toHaveBeenCalledWith(expect.anything(), { title: "T", text: "body" });
  });

  it("returns 400 when title or text is missing", async () => {
    const res = await addNote(jsonReq("http://localhost/api/memory/note", { title: "only title" }));
    expect(res.status).toBe(400);
    expect(addNoteMock).not.toHaveBeenCalled();
  });
});
