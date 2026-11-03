import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { BoardView } from "../src/Board";
import { PersonPreview } from "../src/crm/PersonPreview";
import { TaskDraft } from "../src/crm/TaskDraft";
import { defaultPersonDescriptions, type BoardPerson, type PersonFile } from "../src/api";
import { livingBrief } from "./livingBrief";

const api = vi.hoisted(() => ({ getBoard: vi.fn(), getPerson: vi.fn(), crmRequest: vi.fn(), startCrmRefresh: vi.fn(), listTasks: vi.fn() }));
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
    window.localStorage.clear();
    api.listTasks.mockResolvedValue({ tasks: [], timezone: "America/Los_Angeles", today: "2026-11-02", truncated: false });
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
    fireEvent.click(within(preview).getByRole("button", { name: "Close preview" }));
    expect(screen.queryByRole("region", { name: "Person preview" })).toBeNull();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Preview Maya Patel" }));
  });

  it("moves the preview through the table's current order with N of M controls", async () => {
    api.getPerson.mockImplementation(async (id: string) => ({ ...file(), person: id === "per_leo" ? leo : maya }));
    render(<BoardView />);
    fireEvent.click(await screen.findByRole("button", { name: "Preview Maya Patel" }));
    const preview = await screen.findByRole("region", { name: "Person preview" });
    await within(preview).findByRole("heading", { name: "Maya Patel" });
    const pager = within(preview).getByRole("group", { name: "Move between people" });
    expect(pager).toHaveTextContent("1 of 2");
    expect(within(pager).getByRole("button", { name: "Previous person" })).toBeDisabled();
    expect(screen.getByRole("row", { name: "Preview person Maya Patel" })).toHaveAttribute("aria-current", "true");

    fireEvent.click(within(pager).getByRole("button", { name: "Next person" }));

    const moved = await screen.findByRole("region", { name: "Person preview" });
    await within(moved).findByRole("heading", { name: "Leo Chen" });
    expect(within(moved).getByRole("group", { name: "Move between people" })).toHaveTextContent("2 of 2");
    expect(within(moved).getByRole("button", { name: "Next person" })).toBeDisabled();
    expect(api.getPerson).toHaveBeenLastCalledWith("per_leo");
    // The last row has no next person, so focus stays inside the preview on Close.
    expect(document.activeElement).toBe(within(moved).getByRole("button", { name: "Close preview" }));
  });

  it("keeps unsaved preview edits when moving to the next person", async () => {
    render(<BoardView />);
    fireEvent.click(await screen.findByRole("button", { name: "Preview Maya Patel" }));
    const preview = await screen.findByRole("region", { name: "Person preview" });
    fireEvent.click(await within(preview).findByRole("button", { name: "Add description" }));
    const text = within(preview).getByLabelText("Short description");
    fireEvent.change(text, { target: { value: "Unsaved context" } });
    fireEvent.click(within(preview).getByRole("button", { name: "Next person" }));
    expect(screen.getByRole("alertdialog", { name: "Unsaved edits" })).toBeInTheDocument();
    expect(within(preview).getByRole("group", { name: "Move between people" })).toHaveTextContent("1 of 2");
    expect((text as HTMLTextAreaElement).value).toBe("Unsaved context");
  });

  it("shows the next task with a due chip and the saved summary on one line", async () => {
    const task = { task_id: "task_maya", person_id: "per_maya", title: "Send examples", details: "", due_date: "2026-10-01",
      due_timezone: null, state: "open", origin: "human", version: 1, protected_fields: [], created_at: "2026-10-01",
      updated_at: "2026-10-01", completed_at: null, due_status: "overdue" } as const;
    const general = { ...defaultPersonDescriptions().general, authority: "human" as const, text: "Warm intro from Lumen" };
    api.getBoard.mockResolvedValue({ backlog: [], open: [{ ...maya, next_action: task,
      descriptions: { ...defaultPersonDescriptions(), general } }, leo], in_conversation: [], done: [] });
    render(<BoardView />);
    const row = await screen.findByRole("row", { name: "Preview person Maya Patel" });
    expect(within(row).getByText("Send examples")).toBeInTheDocument();
    expect(row.querySelector(".contacts-due.is-overdue time")).toHaveAttribute("datetime", "2026-10-01");
    expect(within(row).getByRole("button", { name: "Edit short description: Warm intro from Lumen" })).toHaveTextContent("Warm intro from Lumen");
    const quiet = screen.getByRole("row", { name: "Preview person Leo Chen" });
    expect(within(quiet).getByRole("button", { name: "Edit short description" }).textContent).toBe("");
  });

  it("shows one Gmail status chip instead of contradicting lines", async () => {
    api.getBoard.mockResolvedValue({ backlog: [], open: [maya], in_conversation: [], done: [],
      mail_sync: { status: "failed", last_success_at: null, last_attempt_at: "2026-11-02T10:00:00Z", warning: null } });
    const { container } = render(<BoardView />);
    await screen.findByText("Maya Patel");
    expect(container.querySelectorAll(".contacts-mail-chip")).toHaveLength(1);
    expect(container.querySelector(".contacts-mail-chip")).toHaveTextContent("Gmail check failed");
    expect(container.querySelector(".contacts-mail-chip")).toHaveAttribute("title", expect.stringContaining("Saved activity remains available"));
    expect(container).not.toHaveTextContent("Gmail has not been checked yet");
  });

  it("honors an empty saved attention list after dismissing a reply review", async () => {
    api.getBoard.mockResolvedValue({ backlog: [], open: [{ ...leo, follow_up: { needed: true, reason: "reply_unanswered" } }], in_conversation: [], done: [] });
    render(<BoardView />);
    await screen.findByText("Leo Chen");
    expect(screen.queryByText("Reply to review")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Needs attention" }));
    expect(screen.queryByText("Leo Chen")).toBeNull();
  });

  it("keeps unsaved description text when closing until the user discards it", async () => {
    const close = vi.fn();
    render(<PersonPreview personId="per_maya" onClose={close} />);
    fireEvent.click(await screen.findByRole("button", { name: "Add description" }));
    const text = screen.getByLabelText("Short description");
    fireEvent.change(text, { target: { value: "Context from an offline meeting" } });
    fireEvent.click(screen.getByRole("button", { name: "Close preview" }));
    expect(close).not.toHaveBeenCalled();
    expect(screen.getByRole("alertdialog", { name: "Unsaved edits" })).toBeTruthy();
    expect((text as HTMLTextAreaElement).value).toContain("offline meeting");
    fireEvent.click(screen.getByRole("button", { name: "Discard edits" }));
    expect(close).toHaveBeenCalledOnce();
  });

  it("keeps table headers visible when filters match nobody", async () => {
    render(<BoardView />);
    fireEvent.change(await screen.findByRole("searchbox"), { target: { value: "Nobody" } });
    expect(screen.getByRole("columnheader", { name: "Person" })).toBeTruthy();
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

describe("Preview change review", () => {
  beforeEach(() => {
    window.localStorage.clear();
    api.listTasks.mockResolvedValue({ tasks: [], timezone: "America/Los_Angeles", today: "2026-11-02", truncated: false });
    vi.clearAllMocks();
    api.getPerson.mockResolvedValue(file());
    api.crmRequest.mockResolvedValue({});
  });

  it("offers scoped Undo for a status and accepted suggestion", async () => {
    api.getPerson.mockResolvedValue({ ...file(), crm_changes: [
      {change_id: "status_one", entity_type: "status", action: "update", reason: "Changed status", actor: "director"},
      {change_id: "accepted_one", entity_type: "proposal", action: "accept", reason: "Accepted suggestion", actor: "director"},
      {change_id: "dismissed_one", entity_type: "proposal", action: "dismiss", reason: "Dismissed suggestion", actor: "director"},
    ] });
    render(<PersonPreview personId="per_maya" onClose={() => {}} />);
    await screen.findByText("Changed status");
    const undo = screen.getAllByRole("button", { name: "Undo", hidden: true });
    expect(undo).toHaveLength(2);
    fireEvent.click(undo[0]);
    await waitFor(() => expect(api.crmRequest).toHaveBeenCalledWith(
      "/v1/people/per_maya/crm-changes/status_one/undo", expect.objectContaining({operation_id: expect.any(String)}), "POST"));
  });

  it("shows the current and proposed description before acceptance", async () => {
    api.getPerson.mockResolvedValue({ ...file(), crm_proposals: [{person_id: "per_maya", proposal_id: "prop_description", kind: "description", value: {slot: "general", text: "Suggested new context"}, current_value: {text: "Protected current context"}, reason: "New conversation", source_refs: []}] });
    render(<PersonPreview personId="per_maya" onClose={() => {}} />);
    expect(await screen.findByText("Protected current context")).toBeInTheDocument();
    expect(screen.getByText("Suggested new context")).toBeInTheDocument();
  });


  it("keeps unsaved preview edits before following a task draft link", async () => {
    const startHash = window.location.hash;
    api.listTasks.mockResolvedValue({tasks: [{task_id: "task_maya", person_id: "per_maya", title: "Send examples", details: "", due_date: null, due_timezone: null, state: "open", origin: "human", version: 1, protected_fields: [], source_refs: [], created_at: "2026-11-02", updated_at: "2026-11-02", completed_at: null}], truncated: false, timezone: "America/Los_Angeles", today: "2026-11-02"});
    render(<PersonPreview personId="per_maya" onClose={() => {}} />);
    fireEvent.click(await screen.findByRole("button", {name: "Flag"}));
    fireEvent.change(screen.getByLabelText("Attention reason"), {target: {value: "Keep this unsaved flag"}});
    fireEvent.click(await screen.findByRole("link", {name: "Prepare a follow-up draft for Send examples"}));
    expect(screen.getByRole("alertdialog", {name: "Unsaved edits"})).toBeInTheDocument();
    expect(window.location.hash).toBe(startHash);
    expect(screen.getByLabelText("Attention reason")).toHaveValue("Keep this unsaved flag");
  });
  it("does not offer Undo when the saved receipt cannot be undone", async () => {
    api.getPerson.mockResolvedValue({...file(), crm_changes: [{change_id: "human_create", entity_type: "task", action: "create", reason: "Created a human task", actor: "director", can_undo: false}]});
    render(<PersonPreview personId="per_maya" onClose={() => {}} />);
    await screen.findByText("Created a human task");
    expect(screen.queryByRole("button", {name: "Undo", hidden: true})).toBeNull();
  });

  it("uses a durable operation receipt when changing status", async () => {
    render(<PersonPreview personId="per_maya" onClose={() => {}} />);
    fireEvent.change(await screen.findByLabelText("Relationship status"), {target: {value: "in_conversation"}});
    await waitFor(() => expect(api.crmRequest).toHaveBeenCalledWith(
      "/v1/people/per_maya/sequence", expect.objectContaining({state: "in_conversation", expected_version: 2, operation_id: expect.any(String)}), "POST"));
  });
});

describe("Preview controls", () => {
  beforeEach(() => {
    window.localStorage.clear();
    api.listTasks.mockResolvedValue({ tasks: [], timezone: "America/Los_Angeles", today: "2026-11-02", truncated: false });
    vi.clearAllMocks();
    api.getPerson.mockResolvedValue(file());
    api.crmRequest.mockResolvedValue({});
  });

  it("changes status from the tinted pill through the same receipt path, with Undo", async () => {
    const moved = { ...maya, sequence_state: "in_conversation", version: 3 };
    api.crmRequest.mockResolvedValueOnce({ change: { change_id: "chg_status" }, person: moved });
    // The first read is the saved state; the refresh after the save sees the move.
    api.getPerson.mockResolvedValueOnce(file()).mockResolvedValue({ ...file(), person: moved });
    render(<PersonPreview personId="per_maya" onClose={() => {}} />);
    const pill = await screen.findByRole("combobox", { name: "Relationship status" });
    expect(pill).toHaveValue("open");
    expect(pill.closest("label")).toHaveClass("pf-sequence", "is-open");

    fireEvent.change(pill, { target: { value: "in_conversation" } });

    await waitFor(() => expect(api.crmRequest).toHaveBeenCalledWith("/v1/people/per_maya/sequence",
      expect.objectContaining({ state: "in_conversation", expected_version: 2, operation_id: expect.any(String) }), "POST"));
    await waitFor(() => expect(screen.getByRole("combobox", { name: "Relationship status" })).toHaveValue("in_conversation"));
    fireEvent.click(await screen.findByRole("button", { name: "Undo sequence change" }));
    await waitFor(() => expect(api.crmRequest).toHaveBeenCalledWith("/v1/people/per_maya/crm-changes/chg_status/undo",
      expect.objectContaining({ operation_id: expect.any(String) }), "POST"));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Undo sequence change" })).toBeNull());
  });

  it("keeps the attention form behind one Flag button until it is needed", async () => {
    render(<PersonPreview personId="per_maya" onClose={() => {}} />);
    const flag = await screen.findByRole("button", { name: "Flag" });
    expect(screen.queryByLabelText("Attention reason")).toBeNull();
    expect(screen.queryByLabelText("Flag until (optional)")).toBeNull();

    fireEvent.click(flag);
    expect(screen.getByLabelText("Attention reason")).toHaveFocus();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByLabelText("Attention reason")).toBeNull();
    expect(screen.getByRole("button", { name: "Flag" })).toHaveFocus();

    fireEvent.click(screen.getByRole("button", { name: "Flag" }));
    fireEvent.change(screen.getByLabelText("Attention reason"), { target: { value: "Call after meeting" } });
    fireEvent.change(screen.getByLabelText("Flag until (optional)"), { target: { value: "2026-11-10" } });
    fireEvent.click(screen.getByRole("button", { name: "Save flag" }));
    await waitFor(() => expect(api.crmRequest).toHaveBeenCalledWith("/v1/people/per_maya/attention",
      expect.objectContaining({ reason: "Call after meeting", until: "2026-11-10", expected_version: 2, operation_id: expect.any(String) }), "PATCH"));
    await waitFor(() => expect(screen.queryByLabelText("Attention reason")).toBeNull());
  });

  it("shows a saved flag as a chip with Edit flag and Clear flag", async () => {
    api.getPerson.mockResolvedValue({ ...file(), person: { ...maya, attention_reason: "Call after meeting", attention_until: "2026-11-10" } });
    render(<PersonPreview personId="per_maya" onClose={() => {}} />);
    expect(await screen.findByText(/Call after meeting · until 2026-11-10/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Flag" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Clear flag" }));
    await waitFor(() => expect(api.crmRequest).toHaveBeenCalledWith("/v1/people/per_maya/attention",
      expect.objectContaining({ reason: null, until: null, expected_version: 2 }), "PATCH"));

    fireEvent.click(screen.getByRole("button", { name: "Edit flag" }));
    expect(screen.getByLabelText("Attention reason")).toHaveFocus();
  });

  it("shows contact details once, as icon and value lines", async () => {
    api.getPerson.mockResolvedValue({ ...file(), person: { ...maya, email: "maya@lumen.test" } });
    render(<PersonPreview personId="per_maya" onClose={() => {}} />);
    const details = await screen.findByRole("region", { name: "Person details" });
    expect(within(details).getByRole("link", { name: "maya@lumen.test" })).toHaveAttribute("href", "mailto:maya@lumen.test");
    expect(screen.getAllByText("maya@lumen.test")).toHaveLength(1);
    expect(screen.getAllByText("Lumen")).toHaveLength(1);
    expect(within(details).queryByText("Email")).toBeNull();
    expect(within(details).getByRole("button", { name: "Edit details" })).toBeInTheDocument();
  });

  it("gives every preview button an icon", async () => {
    api.getPerson.mockResolvedValue({ ...file(), crm_proposals: [{ person_id: "per_maya", proposal_id: "prop_task", kind: "task_create",
      value: { title: "Send examples" }, current_value: null, reason: "Asked for examples", source_refs: [] }],
    crm_changes: [{ change_id: "status_one", entity_type: "status", action: "update", reason: "Changed status", actor: "director" }] });
    render(<PersonPreview personId="per_maya" onClose={() => {}} />);
    await screen.findByText("Asked for examples");
    const preview = screen.getByRole("region", { name: "Person preview" });
    const buttons = within(preview).getAllByRole("button", { hidden: true });
    expect(buttons.map(button => button.textContent || button.getAttribute("aria-label"))).toEqual(
      expect.arrayContaining(["Flag", "Accept", "Edit suggestion", "Dismiss", "Undo", "Add description"]));
    for (const button of buttons) {
      expect(button.querySelector("svg"), button.textContent || button.getAttribute("aria-label") || "").not.toBeNull();
    }
  });
});

