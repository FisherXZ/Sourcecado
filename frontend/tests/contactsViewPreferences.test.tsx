import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { BoardView } from "../src/Board";

const api = vi.hoisted(() => ({ getBoard: vi.fn(), startCrmRefresh: vi.fn() }));
vi.mock("../src/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../src/api")>()),
  ...api,
}));
vi.mock("../src/Tasks", () => ({
  TASKS_CHANGED: "sourcecado:tasks-changed",
  TaskList: () => <p>Saved tasks</p>,
}));
vi.mock("../src/crm/GlobalSuggestions", () => ({ GlobalSuggestions: () => null }));

const STORAGE_KEY = "sourcecado:contacts-view:v1";
function person(id: string, name: string, rank = 10, due: string | null = null) {
  return {
    person_id: id,
    first_name: name,
    last_name: "Lee",
    sequence_state: "open",
    attention_rank: rank,
    attention_reasons: [{ code: "task_due", label: "Task due" }],
    next_action: { title: "Follow up", due_date: due },
  };
}
function contactOrder() {
  return within(screen.getByRole("table", { name: "Contacts" }))
    .getAllByRole("row")
    .slice(1)
    .map((row) => row.querySelector(".contacts-avatar")?.id);
}
function expectDefaults() {
  expect(screen.getByRole("button", { name: "People", exact: true })).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByRole("button", { name: /^All / })).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByRole("button", { name: "Needs attention" })).toHaveAttribute("aria-pressed", "false");
  expect(screen.getByRole("searchbox", { name: "Search contacts" })).toHaveValue("");
  expect(screen.getByRole("combobox", { name: "Sort contacts" })).toHaveValue("attention");
}

describe("remembered Contacts view", () => {
  beforeEach(() => {
    localStorage.clear();
    api.startCrmRefresh.mockResolvedValue({});
    api.getBoard.mockResolvedValue({ open: [person("ada", "Ada")], in_conversation: [], done: [] });
  });
  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  it("restores view, attention, status, search and sort after a fresh mount", async () => {
    const first = render(<BoardView />);
    await screen.findByRole("table", { name: "Contacts" });
    fireEvent.change(screen.getByRole("combobox", { name: "Sort contacts" }), { target: { value: "name" } });
    fireEvent.click(screen.getByRole("button", { name: /^Open / }));
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "Ada" } });
    fireEvent.click(screen.getByRole("button", { name: "Needs attention" }));
    fireEvent.click(screen.getByRole("button", { name: "Tasks", exact: true }));
    first.unmount();

    render(<BoardView />);
    await screen.findByText("Saved tasks");
    expect(screen.getByRole("button", { name: "Tasks", exact: true })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: /^Open / })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Needs attention" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("searchbox", { name: "Search tasks" })).toHaveValue("Ada");
    fireEvent.click(screen.getByRole("button", { name: "People", exact: true }));
    expect(screen.getByRole("combobox", { name: "Sort contacts" })).toHaveValue("name");
  });

  it("resets the current and saved view to defaults", async () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ view: "tasks", sequenceFilter: "done", contactQuery: "Nobody", needsAttention: true, sort: "due" }));
    const first = render(<BoardView />);
    await screen.findByText("Saved tasks");
    fireEvent.click(screen.getByRole("button", { name: "Reset view" }));
    expectDefaults();
    first.unmount();
    render(<BoardView />);
    await screen.findByRole("table", { name: "Contacts" });
    expectDefaults();
  });

  it.each(["not json", "null", "[]", JSON.stringify({ view: "board", sort: "random", sequenceFilter: "backlog", contactQuery: 12, needsAttention: "yes" })])(
    "uses defaults for invalid saved preferences: %s", async (saved) => {
      localStorage.setItem(STORAGE_KEY, saved);
      render(<BoardView />);
      await screen.findByRole("table", { name: "Contacts" });
      expectDefaults();
    },
  );

  it("sorts by name with stable person IDs for equal names", async () => {
    api.getBoard.mockResolvedValue({ open: [person("z", "Zara"), person("b", "Ada"), person("a", "Ada")], in_conversation: [], done: [] });
    render(<BoardView />);
    await screen.findByRole("table", { name: "Contacts" });
    fireEvent.change(screen.getByRole("combobox", { name: "Sort contacts" }), { target: { value: "name" } });
    expect(contactOrder()).toEqual(["preview-a", "preview-b", "preview-z"]);
  });

  it("sorts next task dates first, undated last, and equal dates by person ID", async () => {
    api.getBoard.mockResolvedValue({ open: [person("none", "No date"), person("late", "Later", 10, "2026-11-06"), person("b", "Early", 10, "2026-11-03"), person("a", "Early", 10, "2026-11-03")], in_conversation: [], done: [] });
    render(<BoardView />);
    await screen.findByRole("table", { name: "Contacts" });
    fireEvent.change(screen.getByRole("combobox", { name: "Sort contacts" }), { target: { value: "due" } });
    expect(contactOrder()).toEqual(["preview-a", "preview-b", "preview-late", "preview-none"]);
  });

  it("keeps equal attention ranks in stable person ID order", async () => {
    api.getBoard.mockResolvedValue({ open: [person("z", "Zara", 2), person("b", "Bea", 1), person("a", "Ada", 1)], in_conversation: [], done: [] });
    render(<BoardView />);
    await screen.findByRole("table", { name: "Contacts" });
    expect(contactOrder()).toEqual(["preview-a", "preview-b", "preview-z"]);
  });

  it("continues operating when browser storage is unavailable", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("Unavailable"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("Unavailable"); });
    render(<BoardView />);
    await screen.findByRole("table", { name: "Contacts" });
    expectDefaults();
    fireEvent.click(screen.getByRole("button", { name: "Needs attention" }));
    expect(screen.getByRole("button", { name: "Needs attention" })).toHaveAttribute("aria-pressed", "true");
  });
});
