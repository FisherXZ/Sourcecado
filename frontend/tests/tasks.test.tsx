import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { PersonTask, TaskChange } from "../src/api";
import { BoardView } from "../src/Board";
import { PersonFileView } from "../src/PersonFile";
import { TaskList } from "../src/Tasks";
import { livingBrief } from "./livingBrief";

const api = vi.hoisted(() => ({
  getBoard: vi.fn(),
  getPerson: vi.fn(),
  listTasks: vi.fn(),
  createTask: vi.fn(),
  updateTask: vi.fn(),
  listTaskChanges: vi.fn(),
  undoTaskChange: vi.fn(),
}));

vi.mock("../src/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../src/api")>()),
  ...api,
}));

const { TaskSaveError } = await import("../src/api");

const MAYA = {
  person_id: "per_maya",
  first_name: "Maya",
  last_name: "Patel",
  title: "Head of Talent",
  company: "Lumen",
  sequence_state: "open",
};
const LEO = {
  person_id: "per_leo",
  first_name: "Leo",
  last_name: "Ng",
  title: "Recruiter",
  company: "Harbor",
  sequence_state: "in_conversation",
};

/** A tiny in-memory server: both views read and write the same records. */
function fakeServer() {
  const tasks = new Map<string, PersonTask>();
  const operations = new Map<string, PersonTask>();
  const changes = new Map<string, TaskChange & { task_id: string; before: PersonTask }>();
  let next = 1;
  let changeNext = 1;

  function record(
    before: PersonTask,
    after: PersonTask,
    action: TaskChange["action"],
    compensates: string | null = null,
  ): TaskChange {
    const change = {
      change_id: `chg_${changeNext}`,
      task_id: after.task_id,
      before,
      entity_version: after.version,
      action,
      actor: "director" as const,
      reason: "",
      operation_id: "op",
      compensates_change_id: compensates,
      created_at: `2026-10-12T11:00:0${changeNext}Z`,
    };
    changeNext += 1;
    changes.set(change.change_id, change);
    return change;
  }
  const people: Record<string, typeof MAYA> = { per_maya: MAYA, per_leo: LEO };

  function status(due: string | null): PersonTask["due_status"] {
    if (!due) return "none";
    if (due < "2026-10-12") return "overdue";
    if (due === "2026-10-12") return "today";
    return "upcoming";
  }

  function view(task: PersonTask): PersonTask {
    const person = people[task.person_id];
    return {
      ...task,
      due_status: status(task.due_date),
      person: {
        person_id: person.person_id,
        first_name: person.first_name,
        last_name: person.last_name,
        company: person.company,
        sequence_state: person.sequence_state,
      },
    };
  }

  api.listTasks.mockImplementation(async (options: { personId?: string; state?: string } = {}) => ({
    tasks: [...tasks.values()]
      .filter((task) => !options.personId || task.person_id === options.personId)
      .filter((task) => options.state === "all" || task.state === "open")
      .map(view),
    truncated: false,
    timezone: "America/Los_Angeles",
    today: "2026-10-12",
  }));
  api.createTask.mockImplementation(
    async (
      personId: string,
      input: { title: string; details: string; due_date: string | null; operationId: string },
    ) => {
      const replay = operations.get(input.operationId);
      if (replay) return { task: view(replay), change: null, replayed: true };
      const task: PersonTask = {
        task_id: `task_${next++}`,
        person_id: personId,
        title: input.title,
        details: input.details,
        due_date: input.due_date,
        due_timezone: input.due_date ? "America/Los_Angeles" : null,
        state: "open",
        origin: "human",
        protected_fields: [],
        version: 1,
        created_at: `2026-10-12T10:00:0${next}Z`,
        updated_at: `2026-10-12T10:00:0${next}Z`,
        completed_at: null,
      };
      tasks.set(task.task_id, task);
      operations.set(input.operationId, task);
      const change = record(task, task, "create");
      return { task: view(task), change: { change_id: change.change_id }, replayed: false };
    },
  );
  api.updateTask.mockImplementation(
    async (
      _personId: string,
      taskId: string,
      input: { changes: Partial<PersonTask>; expectedVersion: number; operationId: string },
    ) => {
      const current = tasks.get(taskId)!;
      if (current.version !== input.expectedVersion) {
        throw new TaskSaveError("changed", 409, "task_conflict", view(current));
      }
      const updated = {
        ...current,
        ...input.changes,
        version: current.version + 1,
        updated_at: `2026-10-12T12:00:0${current.version + 1}Z`,
      };
      tasks.set(taskId, updated);
      const action =
        input.changes.state === "completed"
          ? "complete"
          : input.changes.state === "cancelled"
            ? "cancel"
            : input.changes.state === "open"
              ? "reopen"
              : "update";
      const change = record(current, updated, action);
      return { task: view(updated), change: { change_id: change.change_id }, replayed: false };
    },
  );
  api.listTaskChanges.mockImplementation(async (_personId: string, taskId: string) =>
    [...changes.values()].filter((change) => change.task_id === taskId),
  );
  api.undoTaskChange.mockImplementation(async (_personId: string, changeId: string) => {
    const change = changes.get(changeId)!;
    const current = tasks.get(change.task_id)!;
    if (current.version !== change.entity_version) {
      throw new TaskSaveError("moved on", 409, "task_conflict", view(current));
    }
    const restored = {
      ...current,
      state: change.action === "create" ? "cancelled" as const : change.before.state,
      title: change.before.title,
      version: current.version + 1,
    };
    tasks.set(current.task_id, restored);
    const undo = record(current, restored, "undo", changeId);
    return { task: view(restored), change: { change_id: undo.change_id }, replayed: false };
  });
  return { tasks, changes };
}

async function addTask(scope: HTMLElement, fields: { title: string; due?: string; person?: string }) {
  fireEvent.click(within(scope).getByRole("button", { name: "Add task" }));
  const form = within(scope).getByRole("form", { name: "New task" });
  if (fields.person) {
    fireEvent.change(within(form).getByLabelText("Person"), {
      target: { value: fields.person },
    });
  }
  fireEvent.change(within(form).getByLabelText("Task title"), { target: { value: fields.title } });
  if (fields.due) {
    fireEvent.click(within(form).getByRole("button", { name: "Set due date for the new task" }));
    fireEvent.change(within(form).getByLabelText("Due date"), { target: { value: fields.due } });
    fireEvent.click(within(form).getByRole("button", { name: "Set date" }));
  }
  fireEvent.click(within(form).getByRole("button", { name: "Save task" }));
  await waitFor(() => expect(within(scope).queryByRole("form", { name: "New task" })).toBeNull());
}

/** Opens a row's More actions menu and picks one item. */
function chooseMenu(scope: HTMLElement, title: string, item: string) {
  fireEvent.click(within(scope).getByRole("button", { name: `More actions for ${title}` }));
  const menu = within(scope).getByRole("menu", { name: `Actions for ${title}` });
  fireEvent.click(within(menu).getByRole("menuitem", { name: item }));
}

function dueChip(scope: HTMLElement, title: string) {
  return within(scope).getByRole("button", { name: new RegExp(`^Due date for ${title}:`) });
}

describe("shared person tasks", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getBoard.mockResolvedValue({ backlog: [], open: [MAYA], in_conversation: [LEO], done: [] });
  });

  it("offers tasks for saved people before any sequence starts", async () => {
    fakeServer();
    api.getBoard.mockResolvedValue({ backlog: [{ ...MAYA, sequence_state: null }], open: [], in_conversation: [], done: [] });
    render(<BoardView />);
    fireEvent.click(await screen.findByRole("button", { name: "Tasks" }));
    const tasks = await screen.findByRole("region", { name: "Open tasks" });
    expect(within(tasks).getByRole("button", { name: "Add task" }).hasAttribute("disabled")).toBe(false);
    await addTask(tasks, { title: "Research before outreach", person: "per_maya" });
    await within(tasks).findByText("Research before outreach");
    fireEvent.click(screen.getByRole("button", { name: "People" }));
    // Saving a task leaves sequence membership unchanged: Maya is listed as Not started.
    const row = await screen.findByRole("row", { name: /Maya/ });
    expect(row).toHaveTextContent("Not started");
  });

  it("shows the same saved tasks in Contacts and in the person's list, and edits in either", async () => {
    fakeServer();
    render(
      <>
        <div data-testid="person">
          <TaskList personId="per_maya" heading="Maya's tasks" />
        </div>
        <div data-testid="contacts">
          <BoardView />
        </div>
      </>,
    );
    const person = screen.getByTestId("person");
    const contacts = screen.getByTestId("contacts");
    await within(person).findByText("No open tasks");

    await addTask(person, { title: "Send project examples", due: "2026-10-16" });
    await addTask(person, { title: "Ask about spring cohort" });

    fireEvent.click(await within(contacts).findByRole("button", { name: "Tasks" }));
    const global = await within(contacts).findByRole("region", { name: "Open tasks" });
    await within(global).findByText("Send project examples");
    expect(within(global).getByText("Ask about spring cohort")).toBeTruthy();
    expect(within(global).getAllByRole("link", { name: "Maya Patel" })).toHaveLength(2);
    const chip = dueChip(global, "Send project examples");
    expect(chip.querySelector("time")?.getAttribute("datetime")).toBe("2026-10-16");
    expect(chip).not.toHaveClass("is-overdue");
    expect(within(global).getByRole("heading", { name: "Upcoming" })).toBeTruthy();
    expect(within(global).getByRole("heading", { name: "No date" })).toBeTruthy();
    // No narrating timezone line under the list.
    expect(document.body.textContent).not.toContain("Existing tasks keep their saved timezone");

    // Edit the title, description and date from Contacts, through the row menu.
    chooseMenu(global, "Send project examples", "Edit details");
    const editor = within(global).getByRole("form", { name: "Edit task Send project examples" });
    fireEvent.change(within(editor).getByLabelText("Task title"), {
      target: { value: "Send three project examples" },
    });
    fireEvent.change(within(editor).getByLabelText("Description"), {
      target: { value: "Use the fintech ones." },
    });
    fireEvent.click(dueChip(editor, "Send project examples"));
    fireEvent.change(within(editor).getByLabelText("Due date"), { target: { value: "2026-10-01" } });
    fireEvent.click(within(editor).getByRole("button", { name: "Set date" }));
    fireEvent.click(within(editor).getByRole("button", { name: "Save" }));

    await within(person).findByText("Send three project examples");
    expect(within(person).getByText("Use the fintech ones.")).toBeTruthy();
    const overdue = dueChip(person, "Send three project examples");
    expect(overdue).toHaveClass("is-overdue");
    expect(overdue.querySelector("time")?.getAttribute("datetime")).toBe("2026-10-01");
    expect(api.updateTask).toHaveBeenCalledWith(
      "per_maya",
      "task_1",
      expect.objectContaining({
        expectedVersion: 1,
        changes: {
          title: "Send three project examples",
          details: "Use the fintech ones.",
          due_date: "2026-10-01",
        },
      }),
    );
    // The edit is confirmed with Undo.
    expect(await within(global).findByText("Saved “Send three project examples”")).toBeTruthy();
  });

  it("offers Prepare draft on open tasks only, opening that task's follow-up", async () => {
    fakeServer();
    render(<TaskList personId="per_maya" heading="Maya's tasks" />);
    const list = await screen.findByRole("region", { name: "Maya's tasks" });
    await within(list).findByText("No open tasks");
    await addTask(list, { title: "Send project examples" });

    const prepare = await within(list).findByRole("link", {
      name: "Prepare a follow-up draft for Send project examples",
    });
    expect(prepare.getAttribute("href")).toBe("#/people/per_maya/follow-up/task_1");
    // Reviewed Gmail drafting is the primary action; local text stays behind the row menu.
    expect(within(list).queryByRole("button", {
      name: "Prepare draft for Send project examples",
    })).toBeNull();
    expect(within(list).queryByText("Local draft text")).toBeNull();
    chooseMenu(list, "Send project examples", "Local draft text");
    expect(within(list).getByRole("button", {
      name: "Prepare draft for Send project examples",
    })).toBeVisible();
    // Preparing is navigation only: it never saves the task.
    expect(api.updateTask).not.toHaveBeenCalled();

    fireEvent.click(within(list).getByRole("checkbox", { name: "Mark task Send project examples done" }));
    await waitFor(() =>
      expect(
        within(list).queryByRole("link", {
          name: "Prepare a follow-up draft for Send project examples",
        }),
      ).toBeNull(),
    );
  });

  it("filters the Tasks view by the same status filter and search as Contacts", async () => {
    fakeServer();
    render(<BoardView />);
    fireEvent.click(await screen.findByRole("button", { name: "Tasks" }));
    const global = await screen.findByRole("region", { name: "Open tasks" });
    await addTask(global, { title: "Maya follow-up", person: "per_maya" });
    await addTask(global, { title: "Leo proposal", person: "per_leo" });

    fireEvent.click(screen.getByRole("button", { name: /^In conversation/ }));
    await waitFor(() => expect(within(global).queryByText("Maya follow-up")).toBeNull());
    expect(within(global).getByText("Leo proposal")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /^All/ }));
    fireEvent.change(screen.getByRole("searchbox", { name: "Search tasks" }), {
      target: { value: "lumen" },
    });
    await within(global).findByText("Maya follow-up");
    expect(within(global).queryByText("Leo proposal")).toBeNull();
  });

  it("keeps unsaved text and retries with the same operation after a failed save", async () => {
    fakeServer();
    const realCreate = api.createTask.getMockImplementation()!;
    api.createTask.mockRejectedValueOnce(new Error("network down"));
    render(<TaskList personId="per_maya" />);
    await screen.findByText("No open tasks");

    fireEvent.click(screen.getByRole("button", { name: "Add task" }));
    const form = screen.getByRole("form", { name: "New task" });
    fireEvent.change(within(form).getByLabelText("Task title"), { target: { value: "Draft proposal" } });
    fireEvent.click(within(form).getByRole("button", { name: "Save task" }));

    await within(form).findByRole("alert");
    expect((within(form).getByLabelText("Task title") as HTMLInputElement).value).toBe("Draft proposal");

    api.createTask.mockImplementation(realCreate);
    fireEvent.click(within(form).getByRole("button", { name: "Save task" }));
    await screen.findByText("Draft proposal");
    const [first, second] = api.createTask.mock.calls.map((call) => call[1].operationId);
    expect(first).toBe(second);
  });

  it("locks the new-task row while a save is in flight so later text can't be dropped", async () => {
    fakeServer();
    let release: (() => void) | null = null;
    api.createTask.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          release = () => resolve({ task: null, change: null, replayed: false });
        }),
    );
    render(<TaskList personId="per_maya" />);
    await screen.findByText("No open tasks");

    fireEvent.click(screen.getByRole("button", { name: "Add task" }));
    const form = screen.getByRole("form", { name: "New task" });
    fireEvent.change(within(form).getByLabelText("Task title"), { target: { value: "Draft proposal" } });
    fireEvent.click(within(form).getByRole("button", { name: "Save task" }));

    // While the save is open the row is disabled, so a director cannot type
    // text into a row that closes on success and silently lose it.
    await waitFor(() =>
      expect((within(form).getByLabelText("Task title") as HTMLInputElement).disabled).toBe(true),
    );
    expect(within(form).getByRole("button", { name: "Save task" })).toBeDisabled();
    expect(within(form).getByRole("button", { name: "Set due date for the new task" })).toBeDisabled();

    await act(async () => {
      release!();
    });
    await waitFor(() => expect(screen.queryByRole("form", { name: "New task" })).toBeNull());
  });

  it("locks the edit fields while a save is in flight", async () => {
    fakeServer();
    render(<TaskList personId="per_maya" />);
    await screen.findByText("No open tasks");
    await addTask(document.body, { title: "Draft proposal" });
    await screen.findByText("Draft proposal");

    let release: (() => void) | null = null;
    api.updateTask.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          release = () => resolve({ task: null, change: null, replayed: false });
        }),
    );

    chooseMenu(document.body, "Draft proposal", "Edit details");
    const editor = screen.getByRole("form", { name: "Edit task Draft proposal" });
    fireEvent.change(within(editor).getByLabelText("Task title"), { target: { value: "Rewritten title" } });
    fireEvent.click(within(editor).getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect((within(editor).getByLabelText("Task title") as HTMLInputElement).disabled).toBe(true),
    );
    expect(
      (within(editor).getByLabelText("Description") as HTMLTextAreaElement).disabled,
    ).toBe(true);

    await act(async () => {
      release!();
    });
    await waitFor(() =>
      expect(screen.queryByRole("form", { name: "Edit task Draft proposal" })).toBeNull(),
    );
  });

  it("shows the newer saved task on a conflict and keeps the director's text", async () => {
    const server = fakeServer();
    render(<TaskList personId="per_maya" />);
    await screen.findByText("No open tasks");
    await addTask(document.body, { title: "Draft proposal" });
    await screen.findByText("Draft proposal");

    chooseMenu(document.body, "Draft proposal", "Edit details");
    const editor = screen.getByRole("form", { name: "Edit task Draft proposal" });
    fireEvent.change(within(editor).getByLabelText("Task title"), {
      target: { value: "My rewritten title" },
    });
    // Someone else saves first.
    const saved = server.tasks.get("task_1")!;
    server.tasks.set("task_1", { ...saved, due_date: "2026-10-20", version: 2 });

    fireEvent.click(within(editor).getByRole("button", { name: "Save" }));
    const notice = await within(editor).findByRole("alert");
    expect(notice.textContent).toContain("This task changed while you were editing");
    expect(notice.querySelector("time")?.getAttribute("datetime")).toBe("2026-10-20");
    expect((within(editor).getByLabelText("Task title") as HTMLInputElement).value).toBe(
      "My rewritten title",
    );

    // Saving again is a deliberate choice against the newer version.
    await act(async () => {
      fireEvent.click(within(editor).getByRole("button", { name: "Save" }));
    });
    await screen.findByText("My rewritten title");
    expect(api.updateTask).toHaveBeenLastCalledWith(
      "per_maya",
      "task_1",
      expect.objectContaining({ expectedVersion: 2 }),
    );
  });

  it("keeps the last loaded tasks visible when a reload fails", async () => {
    fakeServer();
    render(<TaskList personId="per_maya" />);
    await screen.findByText("No open tasks");
    await addTask(document.body, { title: "Call Maya" });
    await screen.findByText("Call Maya");

    api.listTasks.mockRejectedValueOnce(new Error("offline"));
    act(() => {
      window.dispatchEvent(new Event("sourcecado:tasks-changed"));
    });
    await screen.findByText("Couldn’t refresh tasks. Showing what was last loaded.");
    expect(screen.getByText("Call Maya")).toBeTruthy();
  });

  it("renders the task list inside the person file", async () => {
    fakeServer();
    api.getPerson.mockResolvedValue({
      person: { ...MAYA, version: 2, sources: [] },
      brief: livingBrief(),
      timeline: [],
      versions: [],
      sourcing_chat: null,
      meeting_evidence: { attached: [], proposed: [], rejected: [] },
    });
    render(<PersonFileView personId="per_maya" />);
    const section = await screen.findByRole("region", { name: "Tasks" });
    await within(section).findByText("No open tasks");
    expect(api.listTasks).toHaveBeenCalledWith({ personId: "per_maya", state: "all", limit: 200, offset: 0 });
  });

  it("completes, cancels and reopens from either list, keeping finished tasks under Completed", async () => {
    fakeServer();
    render(
      <>
        <div data-testid="person">
          <TaskList personId="per_maya" heading="Maya's tasks" />
        </div>
        <div data-testid="contacts">
          <BoardView />
        </div>
      </>,
    );
    const person = screen.getByTestId("person");
    const contacts = screen.getByTestId("contacts");
    await within(person).findByText("No open tasks");
    await addTask(person, { title: "Send project examples", due: "2026-10-16" });
    await addTask(person, { title: "Ask about spring cohort" });
    fireEvent.click(await within(contacts).findByRole("button", { name: "Tasks" }));
    const global = await within(contacts).findByRole("region", { name: "Open tasks" });
    await within(global).findByText("Send project examples");

    // Done from Contacts: it stays there, ticked and struck through, while its
    // toast shows. The person list moves it under a collapsed Completed toggle.
    fireEvent.click(within(global).getByRole("checkbox", { name: "Mark task Send project examples done" }));
    await within(global).findByText("Completed “Send project examples”");
    const completed = await within(person).findByRole("button", { name: /^Completed/ });
    expect(completed).toHaveAttribute("aria-expanded", "false");
    expect(within(person).queryByText("Send project examples")).toBeNull();
    expect(within(person).getByRole("heading", { name: "No date" })).toBeTruthy();
    expect(within(person).queryByRole("heading", { name: "Upcoming" })).toBeNull();
    const ticked = await within(global).findByRole("checkbox", { name: "Reopen task Send project examples" });
    expect(ticked).toHaveAttribute("aria-checked", "true");
    expect(ticked.closest("li")).toHaveClass("is-state-completed");
    expect(api.updateTask).toHaveBeenLastCalledWith(
      "per_maya",
      "task_1",
      expect.objectContaining({ changes: { state: "completed" }, expectedVersion: 1 }),
    );
    // The other task is untouched.
    expect(within(global).getByRole("checkbox", { name: "Mark task Ask about spring cohort done" })).toBeTruthy();

    // Cancel from the person file's row menu. One toast at a time: Contacts' toast closes.
    chooseMenu(person, "Ask about spring cohort", "Cancel task");
    await within(person).findByText("Cancelled “Ask about spring cohort”");
    expect(within(global).queryByText("Completed “Send project examples”")).toBeNull();
    fireEvent.click(within(person).getByRole("button", { name: "Dismiss" }));
    await within(person).findByText("No open tasks");

    // Reopen from Contacts' Completed list.
    fireEvent.click(await within(global).findByRole("button", { name: /^Completed/ }));
    await within(global).findByText("Cancelled");
    fireEvent.click(within(global).getByRole("checkbox", { name: "Reopen task Ask about spring cohort" }));
    await within(person).findByRole("checkbox", { name: "Mark task Ask about spring cohort done" });
    expect(within(person).queryByText("Cancelled")).toBeNull();
    expect(within(global).getByText("Reopened “Ask about spring cohort”")).toBeTruthy();
  });

  it("completes from the checkbox and restores the task from the toast's Undo", async () => {
    fakeServer();
    render(<TaskList personId="per_maya" />);
    await screen.findByText("No open tasks");
    await addTask(document.body, { title: "Send deck" });

    fireEvent.click(await screen.findByRole("checkbox", { name: "Mark task Send deck done" }));
    const toast = await screen.findByText("Completed “Send deck”");
    expect(toast.closest('[role="status"]')).toBeTruthy();
    const ticked = await screen.findByRole("checkbox", { name: "Reopen task Send deck" });
    expect(ticked.closest("li")).toHaveClass("is-state-completed");

    fireEvent.click(screen.getByRole("button", { name: "Undo completing Send deck" }));
    await screen.findByRole("checkbox", { name: "Mark task Send deck done" });
    expect(api.undoTaskChange).toHaveBeenCalledWith(
      "per_maya",
      "chg_2",
      expect.objectContaining({ operationId: expect.any(String) }),
    );
    await waitFor(() => expect(screen.queryByText("Completed “Send deck”")).toBeNull());
    expect(screen.queryByRole("button", { name: /^Completed/ })).toBeNull();
  });

  it("collects finished tasks behind a Completed toggle", async () => {
    fakeServer();
    render(<TaskList personId="per_maya" />);
    await screen.findByText("No open tasks");
    await addTask(document.body, { title: "Send deck" });
    await addTask(document.body, { title: "Book call" });
    fireEvent.click(await screen.findByRole("checkbox", { name: "Mark task Send deck done" }));
    await screen.findByText("Completed “Send deck”");
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));

    const toggle = await screen.findByRole("button", { name: /^Completed/ });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(toggle).toHaveTextContent("1");
    expect(screen.queryByText("Send deck")).toBeNull();
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("checkbox", { name: "Reopen task Send deck" })).toHaveAttribute("aria-checked", "true");
    fireEvent.click(toggle);
    expect(screen.queryByText("Send deck")).toBeNull();
    expect(screen.getByText("Book call")).toBeTruthy();
  });

  it("works the row menu from the keyboard", async () => {
    fakeServer();
    render(<TaskList personId="per_maya" />);
    await screen.findByText("No open tasks");
    await addTask(document.body, { title: "Send deck" });

    const trigger = await screen.findByRole("button", { name: "More actions for Send deck" });
    expect(trigger).toHaveAttribute("aria-haspopup", "menu");
    fireEvent.keyDown(trigger, { key: "ArrowDown" });
    const menu = screen.getByRole("menu", { name: "Actions for Send deck" });
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    const items = within(menu).getAllByRole("menuitem");
    expect(items.map((item) => item.textContent)).toEqual([
      "Edit details",
      "Changes",
      "Local draft text",
      "Cancel task",
    ]);
    expect(items[0]).toHaveFocus();
    fireEvent.keyDown(items[0], { key: "ArrowDown" });
    expect(items[1]).toHaveFocus();
    fireEvent.keyDown(items[1], { key: "End" });
    expect(items[3]).toHaveFocus();
    fireEvent.keyDown(items[3], { key: "ArrowDown" });
    expect(items[0]).toHaveFocus();
    fireEvent.keyDown(items[0], { key: "ArrowUp" });
    expect(items[3]).toHaveFocus();
    fireEvent.keyDown(items[3], { key: "Escape" });
    expect(screen.queryByRole("menu")).toBeNull();
    expect(trigger).toHaveFocus();
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(api.updateTask).not.toHaveBeenCalled();

    // An item runs its action; Escape closes the unchanged editor it opened.
    chooseMenu(document.body, "Send deck", "Edit details");
    const editor = screen.getByRole("form", { name: "Edit task Send deck" });
    expect(within(editor).getByLabelText("Task title")).toHaveFocus();
    fireEvent.keyDown(within(editor).getByLabelText("Task title"), { key: "Escape" });
    expect(screen.queryByRole("form", { name: "Edit task Send deck" })).toBeNull();
  });

  it("needs a second Escape to discard changed details", async () => {
    fakeServer();
    render(<TaskList personId="per_maya" />);
    await screen.findByText("No open tasks");
    await addTask(document.body, { title: "Send deck" });

    chooseMenu(document.body, "Send deck", "Edit details");
    const editor = screen.getByRole("form", { name: "Edit task Send deck" });
    const details = within(editor).getByLabelText("Description");
    fireEvent.change(details, { target: { value: "Attach the fintech case." } });
    fireEvent.keyDown(details, { key: "Escape" });
    expect(within(editor).getByText("Press Escape again to discard your changes.")).toBeTruthy();
    expect(details).toHaveValue("Attach the fintech case.");
    fireEvent.keyDown(details, { key: "Escape" });
    expect(screen.queryByRole("form", { name: "Edit task Send deck" })).toBeNull();
    expect(api.updateTask).not.toHaveBeenCalled();
  });

  it("edits a title in place: Enter saves, Escape cancels", async () => {
    fakeServer();
    render(<TaskList personId="per_maya" />);
    await screen.findByText("No open tasks");
    await addTask(document.body, { title: "Send deck" });

    fireEvent.click(await screen.findByRole("button", { name: "Edit title: Send deck" }));
    let field = screen.getByLabelText("Task title");
    expect(field).toHaveFocus();
    expect(field).toHaveValue("Send deck");
    fireEvent.change(field, { target: { value: "Send the deck" } });
    fireEvent.keyDown(field, { key: "Escape" });
    expect(screen.queryByLabelText("Task title")).toBeNull();
    expect(screen.getByRole("button", { name: "Edit title: Send deck" })).toBeTruthy();
    expect(api.updateTask).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Edit title: Send deck" }));
    field = screen.getByLabelText("Task title");
    fireEvent.change(field, { target: { value: "Send the deck" } });
    // Enter submits the one-field form.
    fireEvent.submit(screen.getByRole("form", { name: "Edit title of Send deck" }));
    await screen.findByRole("button", { name: "Edit title: Send the deck" });
    expect(api.updateTask).toHaveBeenCalledWith(
      "per_maya",
      "task_1",
      expect.objectContaining({ changes: { title: "Send the deck" }, expectedVersion: 1 }),
    );
    expect(await screen.findByText("Saved “Send the deck”")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Undo the edit to Send the deck" })).toBeTruthy();
  });

  it("changes a due date from its chip with one quick pick", async () => {
    fakeServer();
    render(<TaskList personId="per_maya" />);
    await screen.findByText("No open tasks");
    await addTask(document.body, { title: "Send deck", due: "2026-10-16" });

    fireEvent.click(dueChip(document.body, "Send deck"));
    const picker = screen.getByRole("dialog", { name: "Due date for Send deck" });
    fireEvent.click(within(picker).getByRole("button", { name: /^Today/ }));
    await waitFor(() => expect(dueChip(document.body, "Send deck")).toHaveClass("is-today"));
    expect(dueChip(document.body, "Send deck")).toHaveTextContent("Today");
    expect(api.updateTask).toHaveBeenCalledWith(
      "per_maya",
      "task_1",
      expect.objectContaining({ changes: { due_date: "2026-10-12" }, expectedVersion: 1 }),
    );
    expect(await screen.findByText("Due today")).toBeTruthy();

    // Escape closes the picker without saving.
    fireEvent.click(dueChip(document.body, "Send deck"));
    fireEvent.keyDown(screen.getByRole("dialog", { name: "Due date for Send deck" }), { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(api.updateTask).toHaveBeenCalledTimes(1);
  });

  it("adds a task as an inline row at the top, with an optional due chip", async () => {
    fakeServer();
    render(<TaskList personId="per_maya" />);
    await screen.findByText("No open tasks");
    await addTask(document.body, { title: "First" });

    fireEvent.click(screen.getByRole("button", { name: "Add task" }));
    const form = screen.getByRole("form", { name: "New task" });
    const title = within(form).getByLabelText("Task title");
    expect(title).toHaveFocus();
    // The new row sits above the saved rows and has no labelled fields.
    expect(form.compareDocumentPosition(screen.getByText("First")) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(within(form).queryByLabelText("Description")).toBeNull();
    expect(form.querySelector("label")).toBeNull();

    fireEvent.change(title, { target: { value: "Second" } });
    fireEvent.click(within(form).getByRole("button", { name: "Set due date for the new task" }));
    fireEvent.click(within(form).getByRole("button", { name: /^Tomorrow/ }));
    expect(within(form).getByRole("button", { name: "Due date for the new task: Tomorrow" })).toBeTruthy();
    fireEvent.submit(form);
    await waitFor(() => expect(screen.queryByRole("form", { name: "New task" })).toBeNull());
    expect(api.createTask).toHaveBeenLastCalledWith(
      "per_maya",
      expect.objectContaining({ title: "Second", details: "", due_date: "2026-10-13" }),
    );

    // Escape drops a new row.
    fireEvent.click(await screen.findByRole("button", { name: "Add task" }));
    fireEvent.keyDown(screen.getByLabelText("Task title"), { key: "Escape" });
    expect(screen.queryByRole("form", { name: "New task" })).toBeNull();
    await waitFor(() => expect(screen.getByRole("button", { name: "Add task" })).toHaveFocus());
    expect(api.createTask).toHaveBeenCalledTimes(2);
  });

  it("undoes a change from the task's history, and reports a conflict after a newer edit", async () => {
    const server = fakeServer();
    render(<TaskList personId="per_maya" />);
    await screen.findByText("No open tasks");
    await addTask(document.body, { title: "Draft proposal" });
    await screen.findByText("Draft proposal");

    fireEvent.click(screen.getByRole("checkbox", { name: "Mark task Draft proposal done" }));
    await screen.findByRole("checkbox", { name: "Reopen task Draft proposal" });
    chooseMenu(document.body, "Draft proposal", "Changes");
    const history = await screen.findByRole("group", { name: "Changes to Draft proposal" });
    await within(history).findByText("Marked done");
    expect(within(history).getByText("Created")).toBeTruthy();
    expect(within(history).getAllByText("You").length).toBeGreaterThan(0);
    expect(within(history).queryByRole("button", { name: /Undo created/ })).toBeNull();

    fireEvent.click(within(history).getByRole("button", { name: "Undo marked done on Draft proposal" }));
    await screen.findByRole("checkbox", { name: "Mark task Draft proposal done" });
    expect(api.undoTaskChange).toHaveBeenCalledWith("per_maya", "chg_2", expect.anything());

    // A newer edit lands; undoing the earlier completion must not erase it.
    fireEvent.click(screen.getByRole("checkbox", { name: "Mark task Draft proposal done" }));
    await screen.findByRole("checkbox", { name: "Reopen task Draft proposal" });
    const saved = server.tasks.get("task_1")!;
    server.tasks.set("task_1", { ...saved, title: "Draft the proposal", version: saved.version + 1 });
    const undoButtons = await within(history).findAllByRole("button", { name: /^Undo marked done/ });
    fireEvent.click(undoButtons[0]);
    const notice = await within(history).findByRole("alert");
    expect(notice.textContent).toContain("newer edit");
    await screen.findByText("Draft the proposal");
    expect(screen.getByRole("checkbox", { name: "Reopen task Draft the proposal" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
  });

  it("shows permitted task sources and the assistant's reason without turning unsafe URLs into links", async () => {
    const server = fakeServer();
    await api.createTask("per_maya", { title: "Send proposal", details: "", due_date: "2026-10-16", operationId: "source-fixture" });
    const saved = server.tasks.get("task_1")!;
    const sourceLinks = [
      { id: "src_mail", label: "Friday request", url: "https://mail.google.com/mail/u/0/#all/thread-1" },
      { id: "src_note", label: "Conversation note", url: null },
      { id: "src_unsafe", label: "Unsafe source", url: "javascript:alert(1)" },
    ];
    server.tasks.set(saved.task_id, { ...saved, origin: "assistant", source_refs: ["src_mail"], source_links: sourceLinks });
    const change = server.changes.get("chg_1")!;
    server.changes.set(change.change_id, {
      ...change,
      actor: "assistant",
      reason: "Maya asked for the proposal by Friday.",
      source_links: sourceLinks,
      can_undo: true,
    });
    render(<TaskList personId="per_maya" />);
    const link = await screen.findByRole("link", { name: "Source: Friday request" });
    expect(link.getAttribute("href")).toBe(sourceLinks[0].url);
    expect(link.getAttribute("rel")).toContain("noreferrer");
    expect(screen.getByText("Source: Conversation note")).toBeTruthy();
    expect(screen.queryByRole("link", { name: "Source: Unsafe source" })).toBeNull();
    expect(document.body.textContent).not.toContain("src_mail");
    // An assistant-made task carries a small origin badge; a director's task carries none.
    expect(screen.getByTitle("Added by the assistant")).toHaveTextContent("Assistant");

    chooseMenu(document.body, "Send proposal", "Changes");
    const history = await screen.findByRole("group", { name: "Changes to Send proposal" });
    await within(history).findByText("Maya asked for the proposal by Friday.");
    expect(within(history).getByText("Assistant")).toBeTruthy();
    expect(within(history).getByRole("link", { name: "Source: Friday request" })).toBeTruthy();
  });

  it("undoes an eligible automatic creation in every list and hides creation undo without server eligibility", async () => {
    const server = fakeServer();
    await api.createTask("per_maya", { title: "Send proposal", details: "", due_date: "2026-10-16", operationId: "automatic-fixture" });
    const saved = server.tasks.get("task_1")!;
    server.tasks.set(saved.task_id, { ...saved, origin: "assistant" });
    const change = server.changes.get("chg_1")!;
    server.changes.set(change.change_id, { ...change, actor: "assistant", can_undo: true });
    render(<>
      <div data-testid="person"><TaskList personId="per_maya" /></div>
      <div data-testid="global"><TaskList people={[MAYA]} /></div>
    </>);
    const person = screen.getByTestId("person");
    const global = screen.getByTestId("global");
    await within(person).findByText("Send proposal");
    chooseMenu(person, "Send proposal", "Changes");
    const history = await within(person).findByRole("group", { name: "Changes to Send proposal" });
    fireEvent.click(await within(history).findByRole("button", { name: "Undo created on Send proposal" }));
    fireEvent.click(await within(person).findByRole("button", { name: /^Completed/ }));
    await within(person).findByText("Cancelled");
    fireEvent.click(await within(global).findByRole("button", { name: /^Completed/ }));
    await within(global).findByText("Cancelled");
    expect(api.undoTaskChange).toHaveBeenCalledWith("per_maya", "chg_1", expect.objectContaining({ operationId: expect.any(String) }));

    // A creation marked ineligible by the server must not offer a stale undo.
    server.changes.set(change.change_id, { ...change, actor: "assistant", can_undo: false });
    chooseMenu(global, "Send proposal", "Changes");
    const globalHistory = await within(global).findByRole("group", { name: "Changes to Send proposal" });
    await within(globalHistory).findByText("Created");
    expect(within(globalHistory).queryByRole("button", { name: "Undo created on Send proposal" })).toBeNull();
  });
});
