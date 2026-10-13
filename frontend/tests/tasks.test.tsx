import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TasksView } from "../src/Tasks";
import { BoardView } from "../src/Board";

const api = vi.hoisted(() => {
  class TaskConflictError extends Error {}
  return {
    getTasks: vi.fn(),
    getMembers: vi.fn(),
    getBoard: vi.fn(),
    createTask: vi.fn(),
    updateTask: vi.fn(),
    addMember: vi.fn(),
    setPersonOwner: vi.fn(),
    setPersonSequence: vi.fn(),
    refreshReplies: vi.fn(),
    TaskConflictError,
  };
});

vi.mock("../src/api", () => api);

function isoOffset(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(
    d.getDate(),
  ).padStart(2, "0")}`;
}

function task(overrides: Record<string, unknown>) {
  return {
    task_id: "t1",
    person_id: "p1",
    person_name: "Maya Chen",
    person_company: "Stripe",
    title: "A task",
    details: null,
    due_date: null,
    due_timezone: null,
    state: "open",
    origin: "human",
    assignee_member_id: null,
    assignee_name: null,
    version: 1,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
    completed_at: null,
    ...overrides,
  };
}

const SAMPLE = [
  task({ task_id: "overdue", title: "Send examples", due_date: isoOffset(-1), assignee_member_id: "m1", assignee_name: "Milan" }),
  task({ task_id: "today", title: "Reply to Leo", due_date: isoOffset(0), person_name: "Leo Park" }),
  task({ task_id: "soon", title: "Schedule call", due_date: isoOffset(3) }),
  task({ task_id: "nodate", title: "Send portfolio", due_date: null }),
  task({ task_id: "done1", title: "Old thing", state: "completed" }),
];

describe("Tasks view", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getTasks.mockResolvedValue(SAMPLE);
    api.getMembers.mockResolvedValue([{ member_id: "m1", name: "Milan", archived_at: null, created_at: "x" }]);
    api.getBoard.mockResolvedValue({
      open: [{ person_id: "p1", first_name: "Maya", last_name: "Chen", title: "Eng", company: "Stripe", sequence_state: "open" }],
      in_conversation: [],
      done: [],
    });
    api.updateTask.mockResolvedValue(task({ state: "completed" }));
  });

  it("groups open tasks by urgency with counts", async () => {
    render(<TasksView />);
    await screen.findByText("Send examples");
    // "Overdue"/"Upcoming" are unique group labels; "Today"/"No date" also
    // appear as a row's due text, so assert at least one of each.
    expect(screen.getByText("Overdue")).toBeInTheDocument();
    expect(screen.getByText("Upcoming")).toBeInTheDocument();
    expect(screen.getAllByText("Today").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("No date").length).toBeGreaterThanOrEqual(1);
    // completed task is not in the open groups
    expect(screen.queryByText("Old thing")).not.toBeInTheDocument();
  });

  it("shows a member's assigned tasks only after one is chosen", async () => {
    render(<TasksView />);
    await screen.findByText("Send examples");
    // before choosing, the right column prompts and the task appears once (left only)
    expect(screen.getByText("Pick a committee member to see their tasks.")).toBeInTheDocument();
    expect(screen.getAllByText("Send examples")).toHaveLength(1);

    fireEvent.change(screen.getByLabelText("Filter by member"), { target: { value: "m1" } });
    // now it appears in both columns
    expect(screen.getAllByText("Send examples")).toHaveLength(2);
    // a task not assigned to Milan does not appear in the assigned column
    expect(screen.getAllByText("Reply to Leo")).toHaveLength(1);
  });

  it("completes a task through the checkbox", async () => {
    render(<TasksView />);
    await screen.findByText("Send examples");
    const checkbox = screen.getByLabelText("Complete task: Reply to Leo");
    fireEvent.click(checkbox);
    await waitFor(
      () =>
        expect(api.updateTask).toHaveBeenCalledWith(
          "p1",
          "today",
          expect.objectContaining({ state: "completed" }),
        ),
      { timeout: 1500 },
    );
  });

  it("offers an Undo after completing, which reopens the task", async () => {
    render(<TasksView />);
    await screen.findByText("Send examples");
    fireEvent.click(screen.getByLabelText("Complete task: Reply to Leo"));
    const undo = await screen.findByRole("button", { name: "Undo" }, { timeout: 1500 });
    api.updateTask.mockClear();
    fireEvent.click(undo);
    await waitFor(() =>
      expect(api.updateTask).toHaveBeenCalledWith(
        "p1",
        "today",
        expect.objectContaining({ state: "open" }),
      ),
    );
  });

  it("opens the edit modal from the per-row Edit button", async () => {
    render(<TasksView />);
    await screen.findByText("Send examples");
    fireEvent.click(screen.getByLabelText("Edit task: Reply to Leo"));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });

  it("reveals completed tasks on demand", async () => {
    render(<TasksView />);
    await screen.findByText("Send examples");
    expect(screen.queryByText("Old thing")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Show completed/ }));
    expect(screen.getByText("Old thing")).toBeInTheDocument();
  });

  it("keeps the typed text when a save conflicts", async () => {
    api.updateTask.mockRejectedValueOnce(new api.TaskConflictError("stale"));
    render(<TasksView />);
    await screen.findByText("Send examples");
    fireEvent.click(screen.getByText("Send examples")); // open edit modal
    const dialog = await screen.findByRole("dialog");
    const titleInput = within(dialog).getByDisplayValue("Send examples") as HTMLInputElement;
    fireEvent.change(titleInput, { target: { value: "Send examples v2" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save changes" }));
    await within(dialog).findByRole("alert");
    // text is preserved, dialog still open
    expect((within(dialog).getByDisplayValue("Send examples v2") as HTMLInputElement).value).toBe(
      "Send examples v2",
    );
  });
});

describe("Contacts table sorting and owner", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getBoard.mockResolvedValue({
      open: [
        { person_id: "p1", first_name: "Maya", last_name: "Chen", title: "Eng", company: "Zebra Inc", sequence_state: "open", owner_name: "Priya" },
        { person_id: "p2", first_name: "Leo", last_name: "Park", title: "PM", company: "Acme", sequence_state: "open", owner_name: "Milan" },
      ],
      in_conversation: [],
      done: [],
    });
  });

  it("changes a contact's sequence from the table dropdown", async () => {
    api.setPersonSequence.mockResolvedValue({ person: {} });
    render(<BoardView />);
    await screen.findByRole("link", { name: /Maya Chen/ });
    fireEvent.change(screen.getByLabelText("Sequence for Maya Chen"), {
      target: { value: "done" },
    });
    await waitFor(() =>
      expect(api.setPersonSequence).toHaveBeenCalledWith("p1", "done"),
    );
  });

  it("shows an owner column and sorts by company", async () => {
    render(<BoardView />);
    await screen.findByRole("link", { name: /Maya Chen/ });
    expect(screen.getByText("Priya")).toBeInTheDocument();
    expect(screen.getByText("Milan")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Role & company/ }));
    const names = screen
      .getAllByRole("row")
      .slice(1) // skip header
      .map((row) => row.textContent ?? "");
    // Acme (Leo) sorts before Zebra Inc (Maya)
    expect(names[0]).toContain("Leo");
    expect(names[1]).toContain("Maya");
  });
});
