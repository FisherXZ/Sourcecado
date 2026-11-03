import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { PersonTask } from "../src/api";

const api = vi.hoisted(() => ({ updateTask: vi.fn(), undoTaskChange: vi.fn() }));
vi.mock("../src/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../src/api")>()),
  ...api,
}));

const { NextTasks } = await import("../src/person/NextTasks");

function task(over: Partial<PersonTask> = {}): PersonTask {
  return {
    task_id: "task_1", person_id: "per_1", title: "Send deck", details: "", due_date: "2026-10-10",
    due_timezone: "America/Los_Angeles", state: "open", origin: "human", protected_fields: [], version: 4,
    created_at: "2026-10-01", updated_at: "2026-10-01", completed_at: null, due_status: "overdue", ...over,
  };
}

describe("Overview next tasks", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.updateTask.mockResolvedValue({ task: task({ state: "completed", version: 5 }), change: { change_id: "chg_9" }, replayed: false });
    api.undoTaskChange.mockResolvedValue({ task: task({ version: 6 }), change: null, replayed: false });
  });

  it("completes a task from its checkbox and offers Undo", async () => {
    const changed = vi.fn();
    window.addEventListener("sourcecado:tasks-changed", changed);
    render(<NextTasks tasks={[task()]} onShowAll={() => {}} />);
    const chip = screen.getByText((_, node) => node?.tagName === "TIME" && node.getAttribute("datetime") === "2026-10-10");
    expect(chip.closest(".task-due-chip")).toHaveClass("is-overdue");

    fireEvent.click(screen.getByRole("checkbox", { name: "Mark task Send deck done" }));
    expect(screen.getByRole("checkbox", { name: "Mark task Send deck done" })).toHaveAttribute("aria-checked", "true");
    expect(await screen.findByText("Completed “Send deck”")).toBeTruthy();
    expect(api.updateTask).toHaveBeenCalledWith("per_1", "task_1", expect.objectContaining({
      changes: { state: "completed" }, expectedVersion: 4,
    }));
    expect(changed).toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Undo completing Send deck" }));
    await waitFor(() => expect(api.undoTaskChange).toHaveBeenCalledWith("per_1", "chg_9", expect.anything()));
    window.removeEventListener("sourcecado:tasks-changed", changed);
  });
});
