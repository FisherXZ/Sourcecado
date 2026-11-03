import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { PersonFileView } from "../src/PersonFile";
import { livingBrief } from "./livingBrief";

const api = vi.hoisted(() => ({
  getPerson: vi.fn(),
  setPersonSequence: vi.fn(),
  undoTaskChange: vi.fn(),
}));

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
    expect(within(details).getByRole("link", { name: "maya@example.test" })).toHaveAttribute(
      "href",
      "mailto:maya@example.test",
    );
    expect(within(details).getByRole("button", { name: "Copy email" })).toBeInTheDocument();
    expect(within(details).getByRole("link", { name: "linkedin.com/in/maya-example" })).toHaveAttribute(
      "href",
      "https://www.linkedin.com/in/maya-example",
    );
    expect(within(details).queryByText("Email")).toBeNull();

    fireEvent.click(within(details).getByRole("button", { name: "Add phone" }));
    await waitFor(() => expect(within(details).getByLabelText("Phone")).toHaveFocus());
    expect(within(details).getByRole("button", { name: "Review changes" })).toBeDisabled();
  });

  it("changes the sequence state from the header pill and offers Undo", async () => {
    api.setPersonSequence.mockResolvedValue({
      person: { person_id: "per_maya", sequence_state: "in_conversation" },
      change: { change_id: "chg_status" },
    });
    api.undoTaskChange.mockResolvedValue({});
    render(<PersonFileView personId="per_maya" />);
    const sequence = await screen.findByRole("combobox", { name: "Sequence state" });
    expect(sequence).toHaveValue("open");

    fireEvent.change(sequence, { target: { value: "in_conversation" } });
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
    const sequence = await screen.findByRole("combobox", { name: "Sequence state" });
    expect(sequence).toHaveValue("");
    expect(within(sequence).getByRole("option", { name: "Not started" })).toBeDisabled();
  });
});
