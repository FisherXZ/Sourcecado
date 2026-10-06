import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { TaskPanel } from "../src/crm/TaskPanel";
import { CrmTaskError, type PersonTask, type TaskSave } from "../src/api";

const api = vi.hoisted(() => ({
  getTasks: vi.fn(), getTaskPeople: vi.fn(), getTaskPreferences: vi.fn(),
  createPersonTask: vi.fn(), updatePersonTask: vi.fn(),
}));
vi.mock("../src/api", async (importOriginal) => ({
  ...await importOriginal<typeof import("../src/api")>(), ...api,
}));

let saved: PersonTask[];
const task = (fields: Partial<PersonTask> = {}): PersonTask => ({
  task_id: "task-one", person_id: "maya", person_name: "Maya", person_company: "Example",
  title: "Send examples", details: "Include demo", due_date: "2026-10-06",
  due_timezone: "America/Los_Angeles", version: 1, state: "open", origin: "human",
  created_at: "2026-10-05", updated_at: "2026-10-05", ...fields,
});
const result = (t: PersonTask): TaskSave => ({ task: t, receipt: {
  task_id: t.task_id, version: t.version, change_id: "change", operation_id: "operation",
  actor: "director", reason: "Saved task", created_at: "now",
} });

beforeEach(() => {
  saved = [task()];
  api.getTasks.mockImplementation(async () => ({ tasks: [...saved], next_offset: null }));
  api.getTaskPeople.mockResolvedValue({ people: [{ person_id: "maya", first_name: "Maya", company: "Example" }], next_offset: null });
  api.getTaskPreferences.mockResolvedValue({ timezone: "America/Los_Angeles" });
  api.createPersonTask.mockImplementation(async (pid, fields) => {
    const t = task({ task_id: "task-two", person_id: pid, ...fields });
    saved.push(t);
    return result(t);
  });
  api.updatePersonTask.mockImplementation(async (_pid, id, fields) => {
    const t = task({ ...saved.find(t => t.task_id === id), ...fields, version: 2 });
    saved = saved.map(previous => previous.task_id === id ? t : previous);
    return result(t);
  });
});

it("creates an undated task for a kept person and shares edits across both lists", async () => {
  const { container } = render(<><TaskPanel /><TaskPanel personId="maya" /></>);
  const panels = container.querySelectorAll(".task-panel");
  const global = within(panels[0] as HTMLElement);
  const person = within(panels[1] as HTMLElement);
  await global.findByText("Send examples");
  fireEvent.click(global.getByRole("button", { name: "Add task" }));
  await global.findByRole("combobox", { name: "Person" });
  fireEvent.change(global.getByRole("combobox", { name: "Person" }), { target: { value: "maya" } });
  fireEvent.change(global.getByLabelText("Title"), { target: { value: "Research company" } });
  fireEvent.click(global.getByRole("button", { name: "Save task" }));
  await person.findByText("Research company");
  expect(api.createPersonTask.mock.calls[0][1].due_date).toBeNull();
  fireEvent.click(person.getByRole("button", { name: "Edit Send examples" }));
  fireEvent.change(person.getByLabelText("Title"), { target: { value: "Send project examples" } });
  fireEvent.change(person.getByLabelText("Details"), { target: { value: "Two projects" } });
  fireEvent.change(person.getByLabelText("Due date"), { target: { value: "2026-11-01" } });
  fireEvent.click(person.getByRole("button", { name: "Save task" }));
  await global.findByText("Send project examples");
  expect(saved[0].task_id).toBe("task-one");
  expect(saved[0].details).toBe("Two projects");
  expect(saved[0].due_date).toBe("2026-11-01");
});

it("keeps the draft and retries the identical intent after an uncertain save", async () => {
  api.createPersonTask.mockRejectedValueOnce(new Error("Connection lost"));
  render(<TaskPanel personId="maya" />);
  await screen.findByText("Send examples");
  fireEvent.click(screen.getByRole("button", { name: "Add task" }));
  fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Research company" } });
  fireEvent.click(screen.getByRole("button", { name: "Save task" }));
  await screen.findByText(/couldn’t confirm/i);
  expect(screen.getByLabelText("Title")).toHaveValue("Research company");
  expect(screen.getByLabelText("Title")).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Retry saved request" }));
  await screen.findByText("Research company");
  expect(api.createPersonTask.mock.calls[1]).toEqual(api.createPersonTask.mock.calls[0]);
});

it("shows the newer version without erasing unsaved text on conflict or refresh", async () => {
  api.updatePersonTask.mockRejectedValueOnce(new CrmTaskError("This task changed while you were editing.", 409,
    task({ title: "Newer saved title", version: 3 })));
  render(<TaskPanel personId="maya" />);
  await screen.findByText("Send examples");
  fireEvent.click(screen.getByRole("button", { name: "Edit Send examples" }));
  fireEvent.change(screen.getByLabelText("Title"), { target: { value: "My unsaved title" } });
  fireEvent.click(screen.getByRole("button", { name: "Save task" }));
  await screen.findByText("Newer saved title");
  expect(screen.getByLabelText("Title")).toHaveValue("My unsaved title");
  fireEvent(window, new Event("sourcecado:tasks-changed"));
  await waitFor(() => expect(api.getTasks).toHaveBeenCalledTimes(2));
  expect(screen.getByLabelText("Title")).toHaveValue("My unsaved title");
  fireEvent.click(screen.getByRole("button", { name: "Keep my text and use latest version" }));
  fireEvent.click(screen.getByRole("button", { name: "Save task" }));
  await screen.findByText("My unsaved title");
  expect(api.updatePersonTask.mock.calls[1][2].expected_version).toBe(3);
});

it("preserves rows on failed refresh and blocks navigation until the draft is handled", async () => {
  render(<><a href="#/board">Contacts</a><TaskPanel personId="maya" /></>);
  await screen.findByText("Send examples");
  api.getTasks.mockRejectedValueOnce(new Error("Backend unavailable"));
  fireEvent.click(screen.getByRole("button", { name: "Refresh tasks" }));
  await screen.findByText(/couldn’t load tasks/i);
  expect(screen.getByText("Send examples")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Edit Send examples" }));
  fireEvent.change(screen.getByLabelText("Details"), { target: { value: "Unsaved details" } });
  fireEvent.click(screen.getByRole("link", { name: "Contacts" }));
  await screen.findByText("Save your task before leaving?");
  expect(screen.getByLabelText("Details")).toHaveValue("Unsaved details");
  fireEvent.click(screen.getByRole("button", { name: "Keep editing" }));
  expect(screen.queryByText("Save your task before leaving?")).not.toBeInTheDocument();
});

it("validates incomplete dates before Save and leave, keeping the editor open", async () => {
  render(<><a href="#/board">Contacts</a><TaskPanel personId="maya" /></>);
  await screen.findByText("Send examples");
  fireEvent.click(screen.getByRole("button", { name: "Edit Send examples" }));
  fireEvent.change(screen.getByLabelText("Details"), { target: { value: "Unsaved details" } });
  fireEvent.click(screen.getByRole("link", { name: "Contacts" }));
  // Browsers reject partially entered dates with badInput even though the
  // controlled date value is empty. jsdom does not implement that date UI.
  const validity = vi.spyOn(HTMLFormElement.prototype, "reportValidity").mockReturnValue(false);
  fireEvent.click(screen.getByRole("button", { name: "Save and leave" }));
  expect(validity).toHaveBeenCalled();
  expect(api.updatePersonTask).not.toHaveBeenCalled();
  expect(screen.getByLabelText("Details")).toHaveValue("Unsaved details");
  expect(screen.getByRole("alertdialog")).toBeInTheDocument();
  validity.mockRestore();
});
