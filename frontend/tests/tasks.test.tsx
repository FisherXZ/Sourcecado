import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { PersonTask } from "../src/api";
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
  let next = 1;
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

  api.listTasks.mockImplementation(async (options: { personId?: string } = {}) => ({
    tasks: [...tasks.values()]
      .filter((task) => !options.personId || task.person_id === options.personId)
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
      };
      tasks.set(task.task_id, task);
      operations.set(input.operationId, task);
      return { task: view(task), change: { change_id: "chg" }, replayed: false };
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
      const updated = { ...current, ...input.changes, version: current.version + 1 };
      tasks.set(taskId, updated);
      return { task: view(updated), change: { change_id: "chg" }, replayed: false };
    },
  );
  return { tasks };
}

async function addTask(scope: HTMLElement, fields: { title: string; due?: string; person?: string }) {
  fireEvent.click(within(scope).getByRole("button", { name: "Add task" }));
  const form = within(scope).getByRole("form", { name: "New task" });
  if (fields.person) {
    fireEvent.change(within(form).getByLabelText("Person"), {
      target: { value: fields.person },
    });
  }
  fireEvent.change(within(form).getByLabelText("Task"), { target: { value: fields.title } });
  if (fields.due) {
    fireEvent.change(within(form).getByLabelText("Due date (optional)"), {
      target: { value: fields.due },
    });
  }
  fireEvent.click(within(form).getByRole("button", { name: "Save task" }));
  await waitFor(() => expect(within(scope).queryByRole("form", { name: "New task" })).toBeNull());
}

describe("shared person tasks", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getBoard.mockResolvedValue({ backlog: [], open: [MAYA], in_conversation: [LEO], done: [] });
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
    await within(person).findByText("No open tasks for this person.");

    await addTask(person, { title: "Send project examples", due: "2026-10-16" });
    await addTask(person, { title: "Ask about spring cohort" });

    fireEvent.click(await within(contacts).findByRole("button", { name: "Tasks" }));
    const global = await within(contacts).findByRole("region", { name: "Open tasks" });
    await within(global).findByText("Send project examples");
    expect(within(global).getByText("Ask about spring cohort")).toBeTruthy();
    expect(within(global).getAllByRole("link", { name: "Maya Patel" })).toHaveLength(2);
    expect(within(global).getByText("Upcoming · 2026-10-16")).toBeTruthy();
    expect(within(global).getByRole("heading", { name: "No date" })).toBeTruthy();

    // Edit the title, description and date from Contacts.
    fireEvent.click(within(global).getByRole("button", { name: "Edit task Send project examples" }));
    const editor = within(global).getByRole("form", { name: "Edit task Send project examples" });
    fireEvent.change(within(editor).getByLabelText("Task"), {
      target: { value: "Send three project examples" },
    });
    fireEvent.change(within(editor).getByLabelText("Description (optional)"), {
      target: { value: "Use the fintech ones." },
    });
    fireEvent.change(within(editor).getByLabelText("Due date (optional)"), {
      target: { value: "2026-10-01" },
    });
    fireEvent.click(within(editor).getByRole("button", { name: "Save" }));

    await within(person).findByText("Send three project examples");
    expect(within(person).getByText("Use the fintech ones.")).toBeTruthy();
    expect(within(person).getByText("Overdue · 2026-10-01")).toBeTruthy();
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
    await screen.findByText("No open tasks for this person.");

    fireEvent.click(screen.getByRole("button", { name: "Add task" }));
    const form = screen.getByRole("form", { name: "New task" });
    fireEvent.change(within(form).getByLabelText("Task"), { target: { value: "Draft proposal" } });
    fireEvent.click(within(form).getByRole("button", { name: "Save task" }));

    await within(form).findByRole("alert");
    expect((within(form).getByLabelText("Task") as HTMLInputElement).value).toBe("Draft proposal");

    api.createTask.mockImplementation(realCreate);
    fireEvent.click(within(form).getByRole("button", { name: "Save task" }));
    await screen.findByText("Draft proposal");
    const [first, second] = api.createTask.mock.calls.map((call) => call[1].operationId);
    expect(first).toBe(second);
  });

  it("locks the new-task fields while a save is in flight so later text can't be dropped", async () => {
    fakeServer();
    let release: (() => void) | null = null;
    api.createTask.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          release = () => resolve({ task: null, change: null, replayed: false });
        }),
    );
    render(<TaskList personId="per_maya" />);
    await screen.findByText("No open tasks for this person.");

    fireEvent.click(screen.getByRole("button", { name: "Add task" }));
    const form = screen.getByRole("form", { name: "New task" });
    fireEvent.change(within(form).getByLabelText("Task"), { target: { value: "Draft proposal" } });
    fireEvent.click(within(form).getByRole("button", { name: "Save task" }));

    // While the save is open the fields are disabled, so a director cannot type
    // text into a form that closes on success and silently lose it.
    await waitFor(() =>
      expect((within(form).getByLabelText("Task") as HTMLInputElement).disabled).toBe(true),
    );
    expect((within(form).getByLabelText("Description (optional)") as HTMLTextAreaElement).disabled).toBe(
      true,
    );
    expect((within(form).getByLabelText("Due date (optional)") as HTMLInputElement).disabled).toBe(true);

    await act(async () => {
      release!();
    });
    await waitFor(() => expect(screen.queryByRole("form", { name: "New task" })).toBeNull());
  });

  it("locks the edit fields while a save is in flight", async () => {
    fakeServer();
    render(<TaskList personId="per_maya" />);
    await screen.findByText("No open tasks for this person.");
    await addTask(document.body, { title: "Draft proposal" });
    await screen.findByText("Draft proposal");

    let release: (() => void) | null = null;
    api.updateTask.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          release = () => resolve({ task: null, change: null, replayed: false });
        }),
    );

    fireEvent.click(screen.getByRole("button", { name: "Edit task Draft proposal" }));
    const editor = screen.getByRole("form", { name: "Edit task Draft proposal" });
    fireEvent.change(within(editor).getByLabelText("Task"), { target: { value: "Rewritten title" } });
    fireEvent.click(within(editor).getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect((within(editor).getByLabelText("Task") as HTMLInputElement).disabled).toBe(true),
    );
    expect(
      (within(editor).getByLabelText("Description (optional)") as HTMLTextAreaElement).disabled,
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
    await screen.findByText("No open tasks for this person.");
    await addTask(document.body, { title: "Draft proposal" });
    await screen.findByText("Draft proposal");

    fireEvent.click(screen.getByRole("button", { name: "Edit task Draft proposal" }));
    const editor = screen.getByRole("form", { name: "Edit task Draft proposal" });
    fireEvent.change(within(editor).getByLabelText("Task"), {
      target: { value: "My rewritten title" },
    });
    // Someone else saves first.
    const saved = server.tasks.get("task_1")!;
    server.tasks.set("task_1", { ...saved, due_date: "2026-10-20", version: 2 });

    fireEvent.click(within(editor).getByRole("button", { name: "Save" }));
    const notice = await within(editor).findByRole("alert");
    expect(notice.textContent).toContain("This task changed while you were editing");
    expect(within(notice).getByText("2026-10-20")).toBeTruthy();
    expect((within(editor).getByLabelText("Task") as HTMLInputElement).value).toBe(
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
    await screen.findByText("No open tasks for this person.");
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
    await within(section).findByText("No open tasks for this person.");
    expect(api.listTasks).toHaveBeenCalledWith({ personId: "per_maya" });
  });
});
