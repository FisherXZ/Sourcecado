import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { PersonFileView } from "../src/PersonFile";
import { livingBrief } from "./livingBrief";

const api = vi.hoisted(() => ({
  getPerson: vi.fn(),
  setPersonSequence: vi.fn(),
  undoTaskChange: vi.fn(),
  getPersonEnrichment: vi.fn(),
  preparePersonEnrichment: vi.fn(),
}));

const lookup = {
  person: { person_id: "per_maya", name: "Maya Patel", title: "Engineering Lead", company: "Example Labs", version: 2, identity_revision: 0 },
  known: { email: null, phone: null, linkedin_url: null },
  match: { email: "maya@example.test" },
  credits_estimated: null,
  session_id: null,
  status: "not_enriched",
  latest: null,
  pending: null,
  can_request: true,
  problem: null,
  attempts: [],
};

vi.mock("../src/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../src/api")>()),
  ...(await import("./taskApi")).taskApiMock(),
  ...api,
}));

function personFile(sequence: string | null = "open") {
  return {
    person: {
      person_id: "per_maya",
      first_name: "Maya",
      last_name: "Patel",
      title: "Engineering Lead",
      company: "Example Labs",
      email: "maya@example.test",
      phone: null,
      linkedin_url: "https://www.linkedin.com/in/maya-example",
      sequence_state: sequence,
      version: 2,
      sources: [],
    },
    brief: livingBrief({ who: "Maya Patel, Engineering Lead at Example Labs" }),
    timeline: [
      {
        event_id: "mail-1",
        source: "Gmail",
        kind: "reply",
        summary: "Reply",
        payload: { direction: "inbound", subject: "Re: hello", from: "maya@example.test" },
      },
    ],
    versions: [],
    sourcing_chat: null,
    meeting_evidence: { attached: [], proposed: [], rejected: [] },
    crm_changes: [],
  };
}

describe("Person File record layout", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    api.getPerson.mockResolvedValue(personFile());
    api.getPersonEnrichment.mockResolvedValue({ ...lookup, can_request: false, problem: "apollo_not_configured" });
  });

  it("shows the name, role line and avatar initials in the header", async () => {
    render(<PersonFileView personId="per_maya" />);
    expect(await screen.findByRole("heading", { level: 1, name: "Maya Patel" })).toBeInTheDocument();
    expect(screen.getByText("Engineering Lead · Example Labs")).toBeInTheDocument();
    expect(screen.getByText("MP")).toHaveAttribute("aria-hidden", "true");
    expect(screen.getByRole("link", { name: "Back to Contacts" })).toHaveAttribute("href", "#/board");
  });

  it("switches tabs with arrow keys and keeps every panel mounted", async () => {
    render(<PersonFileView personId="per_maya" />);
    const tablist = await screen.findByRole("tablist", { name: "Person file sections" });
    const overview = within(tablist).getByRole("tab", { name: "Overview" });
    expect(overview).toHaveAttribute("aria-selected", "true");
    expect(within(tablist).getByRole("tab", { name: "Emails 1" })).toBeInTheDocument();

    overview.focus();
    fireEvent.keyDown(overview, { key: "ArrowRight" });
    const emails = within(tablist).getByRole("tab", { name: "Emails 1" });
    expect(emails).toHaveAttribute("aria-selected", "true");
    expect(emails).toHaveFocus();
    fireEvent.keyDown(emails, { key: "End" });
    expect(within(tablist).getByRole("tab", { name: "History" })).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(tablist, { key: "Home" });
    expect(overview).toHaveAttribute("aria-selected", "true");

    // Inactive panels are hidden by a class, not unmounted or aria-hidden.
    const history = document.getElementById("pf-panel-history")!;
    expect(history).toHaveClass("is-inactive");
    expect(history).not.toHaveAttribute("hidden");
    expect(history).not.toHaveAttribute("aria-hidden");
    expect(within(history).getByRole("heading", { name: "Timeline" })).toBeInTheDocument();
  });

  it("keeps an unsaved handoff edit across a tab switch", async () => {
    render(<PersonFileView personId="per_maya" />);
    fireEvent.click(await screen.findByRole("tab", { name: "Notes" }));
    const field = screen.getByLabelText("What happened");
    fireEvent.change(field, { target: { value: "Unsaved context" } });
    fireEvent.click(screen.getByRole("tab", { name: "History" }));
    fireEvent.click(screen.getByRole("tab", { name: "Notes" }));
    expect(screen.getByLabelText("What happened")).toHaveValue("Unsaved context");
  });

  it("lists details as icon and value lines, and opens the editor from a missing value", async () => {
    render(<PersonFileView personId="per_maya" />);
    const details = await screen.findByRole("region", { name: "Person details" });
    expect(within(details).getByRole("button", { name: "Edit email: maya@example.test" })).toBeInTheDocument();
    expect(within(details).getByRole("button", { name: "Copy email" })).toBeInTheDocument();
    expect(within(details).getByText("linkedin.com/in/maya-example")).toBeInTheDocument();
    expect(within(details).getByRole("link", { name: "Open LinkedIn profile" })).toHaveAttribute(
      "href",
      "https://www.linkedin.com/in/maya-example",
    );
    expect(within(details).queryByText("Email")).toBeNull();

    // One line turns into an input in place: icon kept, no label, no form.
    fireEvent.click(within(details).getByRole("button", { name: "Add phone" }));
    await waitFor(() => expect(within(details).getByLabelText("Phone")).toHaveFocus());
    expect(within(details).getByLabelText("Phone")).toHaveAttribute("placeholder", "Phone");
    expect(within(details).getAllByRole("textbox")).toHaveLength(1);
    expect(within(details).getByRole("button", { name: "Save phone" })).toBeInTheDocument();
    expect(within(details).queryByRole("button", { name: "Review changes" })).toBeNull();
  });

  it("changes the sequence state from the header pill and offers Undo", async () => {
    api.setPersonSequence.mockResolvedValue({
      person: { person_id: "per_maya", sequence_state: "in_conversation" },
      change: { change_id: "chg_status" },
    });
    api.undoTaskChange.mockResolvedValue({});
    render(<PersonFileView personId="per_maya" />);
    fireEvent.click(await screen.findByRole("button", { name: "Sequence state: Open" }));
    fireEvent.click(screen.getByRole("option", { name: "In conversation" }));
    await waitFor(() =>
      expect(api.setPersonSequence).toHaveBeenCalledWith("per_maya", "in_conversation"),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Undo sequence change" }));
    await waitFor(() =>
      expect(api.undoTaskChange).toHaveBeenCalledWith("per_maya", "chg_status", {
        operationId: "ui-test-operation",
      }),
    );
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Undo sequence change" })).toBeNull(),
    );
  });

  it("reads the person file again after the first lookup opens the sourcing chat", async () => {
    const pending = {
      id: "lookup-1", status: "awaiting_approval", provider: "apollo", person: "Maya Patel", title: "Engineering Lead",
      company: "Example Labs", match: { email: "maya@example.test" }, matched_on: "email", credits_estimated: null,
      credits_actual: null, fetched_at: null, created_at: "2026-11-02", fields_found: [], fields_applied: [], conflicts: [],
      stale_identity: false,
    };
    api.getPersonEnrichment.mockResolvedValue(lookup);
    api.preparePersonEnrichment.mockResolvedValue({
      ...lookup, session_id: "sess_maya", status: "awaiting_approval", latest: pending, pending, can_request: false,
    });
    api.getPerson
      .mockResolvedValueOnce(personFile())
      .mockResolvedValue({ ...personFile(), sourcing_chat: { session_id: "sess_maya" } });
    render(<PersonFileView personId="per_maya" />);
    expect(await screen.findByRole("button", { name: "Start chat" })).toBeInTheDocument();

    fireEvent.click(await screen.findByRole("button", { name: "Find contact details" }));

    await screen.findByRole("button", { name: "Approve lookup once" });
    expect(await screen.findByRole("button", { name: "Open chat" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Start chat" })).toBeNull();
  });

  it("opens the email line in place from a no-match lookup", async () => {
    api.getPerson.mockResolvedValue({ ...personFile(), person: { ...personFile().person, email: null } });
    api.getPersonEnrichment.mockResolvedValue({
      ...lookup,
      session_id: "sess_maya",
      status: "no_match",
      latest: {
        id: "lookup-1", status: "no_match", provider: "apollo", person: "Maya Patel", title: null, company: "Example Labs",
        match: null, matched_on: "name", credits_estimated: null, credits_actual: 1, fetched_at: "2026-11-02T12:00:00Z",
        created_at: "2026-11-02", fields_found: [], fields_applied: [], conflicts: [], stale_identity: false,
      },
    });
    render(<PersonFileView personId="per_maya" />);
    await screen.findByText("Apollo found no match for Maya Patel at Example Labs.");
    const card = screen.getByRole("region", { name: "Contact enrichment" });
    fireEvent.click(within(card).getByRole("button", { name: "Add email" }));
    const details = screen.getByRole("region", { name: "Person details" });
    await waitFor(() => expect(within(details).getByLabelText("Email")).toHaveFocus());
  });

  it("offers Add buttons instead of empty description editors", async () => {
    render(<PersonFileView personId="per_maya" />);
    const overview = await screen.findByRole("tabpanel", { name: "Overview" });
    expect(within(overview).getByRole("button", { name: "Add description" })).toBeInTheDocument();
    expect(within(overview).queryByLabelText("Short description")).toBeNull();
    expect(within(overview).queryByText("0 / 500")).toBeNull();

    fireEvent.click(screen.getByRole("tab", { name: "Notes" }));
    const notes = screen.getByRole("tabpanel", { name: "Notes" });
    fireEvent.click(within(notes).getByRole("button", { name: "Add detailed description" }));
    expect(within(notes).getByRole("textbox", { name: "Detailed description" })).toHaveFocus();
  });

  it("shows a person not yet in a sequence as Not started", async () => {
    api.getPerson.mockResolvedValue(personFile(null));
    render(<PersonFileView personId="per_maya" />);
    fireEvent.click(await screen.findByRole("button", { name: "Sequence state: Not started" }));
    const list = screen.getByRole("listbox", { name: "Sequence state" });
    expect(within(list).getAllByRole("option")).toHaveLength(4);
    const notStarted = within(list).getByRole("option", { name: "Not started" });
    expect(notStarted).toHaveAttribute("aria-selected", "true");
    expect(notStarted).toHaveAttribute("aria-disabled", "true");
    fireEvent.click(notStarted);
    expect(api.setPersonSequence).not.toHaveBeenCalled();
  });
});
