import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { BoardView } from "../src/Board";
import { PersonPreview } from "../src/crm/PersonPreview";
import { TaskDraft } from "../src/crm/TaskDraft";
import { defaultPersonDescriptions, type BoardPerson, type PersonFile } from "../src/api";
import { livingBrief } from "./livingBrief";

const api = vi.hoisted(() => ({ getBoard: vi.fn(), getPerson: vi.fn(), crmRequest: vi.fn(), startCrmRefresh: vi.fn() }));
vi.mock("../src/api", async (original) => ({
  ...await original<typeof import("../src/api")>(),
  ...(await import("./taskApi")).taskApiMock(), ...api,
}));

const maya: BoardPerson = { person_id: "per_maya", first_name: "Maya", last_name: "Patel", title: "Director", company: "Lumen", sequence_state: "open", version: 2,
  attention_rank: 0, attention_reasons: [{ code: "manual", label: "Call after meeting" }] };
const leo: BoardPerson = { ...maya, person_id: "per_leo", first_name: "Leo", last_name: "Chen", attention_rank: 1, attention_reasons: [] };
function file(): PersonFile {
  return { person: maya, descriptions: defaultPersonDescriptions(), brief: livingBrief(), timeline: [], sourcing_chat: null, crm_proposals: [], crm_changes: [] };
}

describe("Contacts attention and preview", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getBoard.mockResolvedValue({ backlog: [], open: [leo, maya], in_conversation: [], done: [] });
    api.getPerson.mockResolvedValue(file());
    api.startCrmRefresh.mockResolvedValue({ status: "pending" });
    api.crmRequest.mockResolvedValue({});
  });

  it("sorts saved attention and filters to people who need attention", async () => {
    render(<BoardView />);
    await screen.findByText("Call after meeting");
    const rows = screen.getAllByRole("row");
    expect(rows[1].textContent).toContain("Maya");
    fireEvent.click(screen.getByRole("button", { name: "Needs attention" }));
    expect(screen.queryByText("Leo Chen")).toBeNull();
    expect(screen.getByText("Maya Patel")).toBeTruthy();
  });

  it("opens the preview without navigating and returns focus on close", async () => {
    render(<BoardView />);
    const trigger = await screen.findByRole("button", { name: "Preview Maya Patel" });
    fireEvent.click(trigger);
    const preview = await screen.findByRole("region", { name: "Person preview" });
    expect(within(preview).getByRole("link", { name: "Open person file" }).getAttribute("href")).toBe("#/people/per_maya");
    fireEvent.click(within(preview).getByRole("button", { name: "Back to Contacts" }));
    expect(screen.queryByRole("region", { name: "Person preview" })).toBeNull();
  });

  it("keeps unsaved description text when closing until the user discards it", async () => {
    const close = vi.fn();
    render(<PersonPreview personId="per_maya" onClose={close} />);
    const text = await screen.findByLabelText("Short description");
    fireEvent.change(text, { target: { value: "Context from an offline meeting" } });
    fireEvent.click(screen.getByRole("button", { name: "Back to Contacts" }));
    expect(close).not.toHaveBeenCalled();
    expect(screen.getByRole("alertdialog", { name: "Unsaved edits" })).toBeTruthy();
    expect((text as HTMLTextAreaElement).value).toContain("offline meeting");
    fireEvent.click(screen.getByRole("button", { name: "Discard edits" }));
    expect(close).toHaveBeenCalledOnce();
  });

  it("keeps table headers visible when filters match nobody", async () => {
    render(<BoardView />);
    fireEvent.change(await screen.findByRole("searchbox"), { target: { value: "Nobody" } });
    expect(screen.getByRole("columnheader", { name: "Contact" })).toBeTruthy();
    expect(screen.getByText(/No contacts match/)).toBeTruthy();
  });

  it("prepares local review text through the task endpoint without sending", async () => {
    api.crmRequest.mockResolvedValue({ artifact: { subject: "Project examples", body: "Hi Maya, [review needed: add links].", thread_id: "thread1" } });
    render(<TaskDraft task={{ task_id: "task_maya", person_id: "per_maya", title: "Send examples", details: "", due_date: null,
      due_timezone: null, state: "open", origin: "human", version: 1, protected_fields: [], source_refs: [],
      created_at: "2026-10-22", updated_at: "2026-10-22", completed_at: null }} />);
    fireEvent.click(screen.getByRole("button", { name: "Prepare draft for Send examples" }));
    await screen.findByRole("region", { name: "Prepared draft for Send examples" });
    expect(screen.getByText("Saved in Sourcecado. Nothing has been sent.")).toBeTruthy();
    expect(screen.getByText(/Threaded draft creation is unavailable/)).toBeTruthy();
    expect(api.crmRequest.mock.calls[0][0]).toBe("/v1/people/per_maya/tasks/task_maya/prepare");
    expect(api.crmRequest).toHaveBeenCalledOnce();
  });
});
