import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { MemoryPage } from "../src/routes/MemoryPage";

const api = vi.hoisted(() => ({
  getMemoryBacklog: vi.fn(),
  classifyMemory: vi.fn(),
  forgetMemory: vi.fn(),
}));

vi.mock("../src/api", () => ({
  getMemoryBacklog: api.getMemoryBacklog,
  classifyMemory: api.classifyMemory,
  forgetMemory: api.forgetMemory,
}));

const WAITING = {
  needs_review: 2,
  classified: 1,
  items: [
    {
      id: 1,
      content: "Codeology sources design-adjacent engineers first.",
      category: "legacy_unclassified",
      classification_status: "needs_review",
      created_at: "2026-08-01T09:12:00+00:00",
    },
    {
      id: 4,
      content: "Ada moved to Analytic in June.",
      category: "unclassified",
      classification_status: "needs_review",
      created_at: "2026-08-20T09:12:00+00:00",
    },
  ],
};

describe("MemoryPage", () => {
  beforeEach(() => {
    api.getMemoryBacklog.mockReset();
    api.classifyMemory.mockReset();
    api.forgetMemory.mockReset();
  });

  it("announces that the backlog is loading", () => {
    api.getMemoryBacklog.mockReturnValue(new Promise(() => {}));

    render(<MemoryPage />);

    expect(screen.getByRole("status")).toHaveTextContent("Loading saved memory");
  });

  it("leads with how many rows are waiting and why they are withheld", async () => {
    api.getMemoryBacklog.mockResolvedValue(WAITING);

    render(<MemoryPage />);

    expect(await screen.findByText("2 waiting for review")).toBeInTheDocument();
    expect(screen.getByText(/1 in use/)).toBeInTheDocument();
    expect(
      screen.getByText(/not being used until you say what each one is/i),
    ).toBeInTheDocument();
    expect(screen.getByText("Codeology sources design-adjacent engineers first.")).toBeInTheDocument();
    expect(screen.getByText("Ada moved to Analytic in June.")).toBeInTheDocument();
  });

  it("says where a fact about one person belongs instead", async () => {
    api.getMemoryBacklog.mockResolvedValue(WAITING);

    render(<MemoryPage />);

    expect(await screen.findByText(/Person File/)).toBeInTheDocument();
  });

  it("keeps a row as a global preference and refreshes the backlog", async () => {
    api.getMemoryBacklog
      .mockResolvedValueOnce(WAITING)
      .mockResolvedValueOnce({ needs_review: 1, classified: 2, items: [WAITING.items[1]] });
    api.classifyMemory.mockResolvedValue({ memory: { id: 1 } });

    render(<MemoryPage />);

    const row = await screen.findByRole("listitem", { name: "Saved memory 1" });
    fireEvent.click(within(row).getByRole("button", { name: "Keep memory 1 as a global preference" }));

    await waitFor(() => expect(api.classifyMemory).toHaveBeenCalledWith(1));
    expect(await screen.findByText("1 waiting for review")).toBeInTheDocument();
  });

  it("deletes a row the director does not want kept", async () => {
    api.getMemoryBacklog
      .mockResolvedValueOnce(WAITING)
      .mockResolvedValueOnce({ needs_review: 1, classified: 1, items: [WAITING.items[0]] });
    api.forgetMemory.mockResolvedValue({ forgotten: true, id: 4 });

    render(<MemoryPage />);

    const row = await screen.findByRole("listitem", { name: "Saved memory 4" });
    fireEvent.click(within(row).getByRole("button", { name: "Delete memory 4" }));

    await waitFor(() => expect(api.forgetMemory).toHaveBeenCalledWith(4));
    expect(await screen.findByText("1 waiting for review")).toBeInTheDocument();
  });

  it("reports a refused classification without leaking internals", async () => {
    api.getMemoryBacklog.mockResolvedValue(WAITING);
    api.classifyMemory.mockRejectedValue(
      new Error("memory classify 400 /Users/private/club.db"),
    );

    const { container } = render(<MemoryPage />);

    const row = await screen.findByRole("listitem", { name: "Saved memory 1" });
    fireEvent.click(within(row).getByRole("button", { name: "Keep memory 1 as a global preference" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("That memory couldn’t be classified");
    expect(container).not.toHaveTextContent("/Users/private");
  });

  it("says so plainly when nothing is waiting", async () => {
    api.getMemoryBacklog.mockResolvedValue({ needs_review: 0, classified: 3, items: [] });

    render(<MemoryPage />);

    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent("Nothing waiting for review"),
    );
    expect(screen.queryByRole("list", { name: "Memory waiting for review" })).not.toBeInTheDocument();
  });

  it("shows a contextual failure and retries the backlog", async () => {
    api.getMemoryBacklog
      .mockRejectedValueOnce(new Error("memory 500 /Users/private/club.db"))
      .mockResolvedValueOnce(WAITING);

    const { container } = render(<MemoryPage />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Saved memory couldn’t be loaded");
    expect(container).not.toHaveTextContent("/Users/private");

    fireEvent.click(within(alert).getByRole("button", { name: "Retry loading saved memory" }));

    expect(await screen.findByText("2 waiting for review")).toBeInTheDocument();
  });
});
