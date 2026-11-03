import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { MemoryItem } from "../src/api";
import { MemoryPage } from "../src/routes/MemoryPage";

const api = vi.hoisted(() => ({
  getMemories: vi.fn(),
  createMemory: vi.fn(),
  updateMemory: vi.fn(),
  classifyMemory: vi.fn(),
  forgetMemory: vi.fn(),
}));

vi.mock("../src/api", () => api);

function memory(id: number, content: string, inUse: boolean): MemoryItem {
  return {
    id,
    content,
    created_at: "2026-11-01T09:12:00+00:00",
    updated_at: "2026-11-01T09:12:00+00:00",
    in_use: inUse,
  };
}

/** What the mocked backend holds; every write changes it like the store does. */
let rows: MemoryItem[] = [];
let nextId = 1;

beforeEach(() => {
  rows = [
    memory(1, "Keep first emails under 120 words.", true),
    memory(2, "Sign emails as Fisher.", true),
    memory(4, "Fisher prefers Tuesday sends.", false),
  ];
  nextId = 5;
  for (const mock of Object.values(api)) mock.mockReset();
  api.getMemories.mockImplementation(async () => ({ items: [...rows].reverse(), maxChars: 140 }));
  api.createMemory.mockImplementation(async (content: string) => {
    const item = memory(nextId++, content, true);
    rows = [...rows, item];
    return item;
  });
  api.updateMemory.mockImplementation(async (id: number, content: string) => {
    rows = rows.map((row) =>
      row.id === id ? { ...row, content, in_use: true, updated_at: "2026-11-02T10:00:00+00:00" } : row,
    );
    return rows.find((row) => row.id === id);
  });
  api.classifyMemory.mockImplementation(async (id: number) => {
    rows = rows.map((row) => (row.id === id ? { ...row, in_use: true } : row));
    return { memory: rows.find((row) => row.id === id) };
  });
  api.forgetMemory.mockImplementation(async (id: number) => {
    rows = rows.filter((row) => row.id !== id);
    return { forgotten: true, id };
  });
});

async function openPage() {
  render(<MemoryPage />);
  return screen.findByRole("list", { name: "Memories" });
}

function rowFor(id: number) {
  return screen.getByRole("listitem", { name: `Memory #${id}` });
}

describe("MemoryPage", () => {
  it("shows a loading state with one page title", () => {
    api.getMemories.mockReturnValue(new Promise(() => {}));
    render(<MemoryPage />);

    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByRole("status", { name: "Loading memory" })).toBeInTheDocument();
  });

  it("lists memories with their ids, waiting ones first, without narrating copy", async () => {
    const list = await openPage();

    const items = within(list).getAllByRole("listitem");
    expect(items.map((item) => item.getAttribute("aria-label"))).toEqual([
      "Memory #4",
      "Memory #2",
      "Memory #1",
    ]);
    expect(screen.getByText("3")).toHaveClass("sm-count");
    expect(within(rowFor(4)).getByText("#4")).toHaveAttribute("title", "sourcecado:memory/4");
    expect(within(rowFor(4)).getByText("Review")).toBeInTheDocument();
    expect(within(rowFor(4)).getByRole("button", { name: "Keep memory #4" })).toBeInTheDocument();
    expect(within(rowFor(2)).queryByText("Review")).not.toBeInTheDocument();
    expect(within(rowFor(2)).queryByRole("button", { name: /Keep/ })).not.toBeInTheDocument();
    expect(screen.queryByText(/Sourcecado still has every one/)).not.toBeInTheDocument();
    for (const button of screen.getAllByRole("button")) {
      expect(button.querySelector("svg")).not.toBeNull();
    }
  });

  it("creates a memory that is in use and tells the rail to recount", async () => {
    const changed = vi.fn();
    window.addEventListener("sourcecado:memory-changed", changed);
    await openPage();

    fireEvent.click(screen.getByRole("button", { name: "New memory" }));
    const box = screen.getByRole("textbox", { name: "New memory" });
    expect(box).toHaveFocus();
    fireEvent.change(box, { target: { value: "Never email on Sundays." } });
    expect(screen.getByText("23 / 140")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(rowFor(5)).toHaveTextContent("Never email on Sundays."));
    expect(api.createMemory).toHaveBeenCalledWith("Never email on Sundays.");
    expect(screen.queryByRole("textbox", { name: "New memory" })).not.toBeInTheDocument();
    expect(changed).toHaveBeenCalled();
    window.removeEventListener("sourcecado:memory-changed", changed);
  });

  it("will not save text longer than the prompt shows whole", async () => {
    await openPage();
    fireEvent.click(screen.getByRole("button", { name: "New memory" }));
    fireEvent.change(screen.getByRole("textbox", { name: "New memory" }), {
      target: { value: "a".repeat(141) },
    });

    expect(screen.getByText("141 / 140")).toHaveClass("is-over");
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("keeps the text when a save is refused and shows the reason", async () => {
    api.createMemory.mockRejectedValueOnce(new Error("Keep it under 140 characters."));
    await openPage();
    fireEvent.click(screen.getByRole("button", { name: "New memory" }));
    const box = screen.getByRole("textbox", { name: "New memory" });
    fireEvent.change(box, { target: { value: "Prefer warm intros." } });
    fireEvent.keyDown(box, { key: "Enter" });

    expect(await screen.findByRole("alert")).toHaveTextContent("Keep it under 140 characters.");
    expect(box).toHaveValue("Prefer warm intros.");
  });

  it("edits a memory in place", async () => {
    await openPage();
    fireEvent.click(screen.getByRole("button", { name: "Edit memory #2" }));
    const box = screen.getByRole("textbox", { name: "Edit memory #2" });
    expect(box).toHaveValue("Sign emails as Fisher.");

    fireEvent.change(box, { target: { value: "Sign emails as Fisher X." } });
    fireEvent.keyDown(box, { key: "Enter" });

    await waitFor(() => expect(rowFor(2)).toHaveTextContent("Sign emails as Fisher X."));
    expect(api.updateMemory).toHaveBeenCalledWith(2, "Sign emails as Fisher X.");
    expect(within(rowFor(2)).queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("asks before deleting and can be cancelled", async () => {
    await openPage();

    fireEvent.click(screen.getByRole("button", { name: "Delete memory #2" }));
    expect(within(rowFor(2)).getByText("Delete #2?")).toBeInTheDocument();
    fireEvent.click(within(rowFor(2)).getByRole("button", { name: "Cancel" }));
    expect(api.forgetMemory).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Delete memory #2" }));
    fireEvent.click(within(rowFor(2)).getByRole("button", { name: "Delete" }));
    await waitFor(() =>
      expect(screen.queryByRole("listitem", { name: "Memory #2" })).not.toBeInTheDocument(),
    );
    expect(api.forgetMemory).toHaveBeenCalledWith(2);
  });

  it("keeps a memory the assistant saved so it comes into use", async () => {
    await openPage();
    fireEvent.click(screen.getByRole("button", { name: "Keep memory #4" }));

    await waitFor(() => expect(within(rowFor(4)).queryByText("Review")).not.toBeInTheDocument());
    expect(api.classifyMemory).toHaveBeenCalledWith(4);
  });

  it("shows a refused Keep on its row", async () => {
    api.classifyMemory.mockRejectedValueOnce(
      new Error("a Person- or session-scoped memory cannot become a global operator preference"),
    );
    await openPage();
    fireEvent.click(screen.getByRole("button", { name: "Keep memory #4" }));

    expect(await within(rowFor(4)).findByRole("alert")).toHaveTextContent(
      "cannot become a global operator preference",
    );
    expect(within(rowFor(4)).getByText("Review")).toBeInTheDocument();
  });

  it("shows a failed load and retries", async () => {
    api.getMemories.mockRejectedValueOnce(new Error("memory 500"));
    render(<MemoryPage />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Memory didn’t load.");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("list", { name: "Memories" })).toBeInTheDocument();
  });

  it("starts an empty memory from one New memory button", async () => {
    rows = [];
    render(<MemoryPage />);

    expect(await screen.findByText("No memories yet")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "New memory" })).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "New memory" }));
    expect(screen.getByRole("textbox", { name: "New memory" })).toHaveFocus();
  });
});
