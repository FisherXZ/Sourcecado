// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryClient } from "@/app/memory/MemoryClient";

const sourceRow = (over = {}) => ({
  sourceId: "acme",
  title: "Acme Robotics",
  sourceType: "markdown",
  updatedAt: "2026-06-25T00:00:00.000Z",
  archived: false,
  ...over,
});

describe("MemoryClient", () => {
  afterEach(() => vi.restoreAllMocks());

  it("lists sources from the API and flags archived ones", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ sources: [sourceRow(), sourceRow({ sourceId: "beta", title: null, sourceType: "csv", archived: true })] }),
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<MemoryClient />);

    await waitFor(() => expect(screen.getByText("Acme Robotics")).toBeInTheDocument());
    expect(fetchMock).toHaveBeenCalledWith("/api/memory/sources");
    expect(screen.getByText(/archived/i)).toBeInTheDocument();
  });

  it("shows an empty state when there are no sources", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({ sources: [] }) }));
    render(<MemoryClient />);
    await waitFor(() => expect(screen.getByText(/no sources yet/i)).toBeInTheDocument());
  });

  it("archives a source via the archive endpoint", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({ ok: true, json: async () => ({ sources: [sourceRow()] }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ sourceId: "acme", archived: true }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ sources: [sourceRow({ archived: true })] }) });
    vi.stubGlobal("fetch", fetchMock);

    render(<MemoryClient />);
    await waitFor(() => expect(screen.getByText("Acme Robotics")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /^archive$/i }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/memory/sources/acme/archive",
        expect.objectContaining({ method: "POST" })
      )
    );
  });

  it("adds a note via the note endpoint", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({ ok: true, json: async () => ({ sources: [] }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ sourceId: "note-x" }) });
    vi.stubGlobal("fetch", fetchMock);

    render(<MemoryClient />);
    // let the initial sources fetch settle before switching tabs
    await waitFor(() => expect(screen.getByText(/no sources yet/i)).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /add note/i }));
    fireEvent.change(screen.getByLabelText(/note title/i), { target: { value: "Correction" } });
    fireEvent.change(screen.getByLabelText(/note text/i), { target: { value: "Jordan went cold." } });
    fireEvent.click(screen.getByRole("button", { name: /save note/i }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/memory/note",
        expect.objectContaining({ method: "POST" })
      )
    );
  });
});
