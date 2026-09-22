import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { BoardView } from "../src/Board";
import { PersonFileView } from "../src/PersonFile";
import { claim, livingBrief } from "./livingBrief";

const api = vi.hoisted(() => ({
  attachPersonMeeting: vi.fn(),
  attachPersonDriveEvidence: vi.fn(),
  getBoard: vi.fn(),
  savePersonHandoff: vi.fn(),
  getPerson: vi.fn(),
  openPersonSourcingChat: vi.fn(),
  refreshPersonMeetings: vi.fn(),
  refreshReplies: vi.fn(),
  rejectPersonMeeting: vi.fn(),
  revertPerson: vi.fn(),
  searchPersonDriveEvidence: vi.fn(),
  setPersonSequence: vi.fn(),
}));

vi.mock("../src/api", () => ({
  attachPersonMeeting: api.attachPersonMeeting,
  attachPersonDriveEvidence: api.attachPersonDriveEvidence,
  getBoard: api.getBoard,
  savePersonHandoff: api.savePersonHandoff,
  getPerson: api.getPerson,
  openPersonSourcingChat: api.openPersonSourcingChat,
  refreshPersonMeetings: api.refreshPersonMeetings,
  refreshReplies: api.refreshReplies,
  rejectPersonMeeting: api.rejectPersonMeeting,
  revertPerson: api.revertPerson,
  searchPersonDriveEvidence: api.searchPersonDriveEvidence,
  setPersonSequence: api.setPersonSequence,
}));

describe("Contacts and person-file routes", () => {
  beforeEach(() => {
    api.getBoard.mockResolvedValue({
      open: [
        {
          person_id: "person one",
          first_name: "Alyssa",
          last_name: "Lee",
          title: "Sourcing lead",
          company: "Codeology",
          sequence_state: "open",
        },
      ],
      in_conversation: [],
      done: [],
    });
    api.revertPerson.mockResolvedValue({
      person: { person_id: "person one", sequence_state: "open", version: 3, title: "Founder" },
    });
    api.getPerson.mockResolvedValue({
      person: {
        person_id: "person one",
        sequence_state: "open",
        version: 2,
        sources: [
          {
            id: "source_ref_drive1",
            person_id: "person one",
            type: "source_ref",
            restricted: false,
            fields: {
              provider: "Google Drive",
              title: "Fall sourcing masterdoc",
              extraction_status: "metadata_only",
              sensitivity: "standard",
              out_of_scope: false,
            },
          },
        ],
      },
      brief: livingBrief(),
      timeline: [
        {
          event_id: "event-1",
          source: "Apollo",
          kind: "search",
          summary: "Found in a sourcing search.",
          payload: {},
        },
      ],
      versions: [
        { version: 1, created_at: "2026-08-26T10:00:00Z" },
        { version: 2, created_at: "2026-08-26T11:00:00Z" },
      ],
      sourcing_chat: null,
      meeting_evidence: {
        attached: [
          {
            evidence_id: "meeting-calendar",
            provider: "calendar",
            provider_id: "cal-1",
            title: "Calendar review",
            starts_at: "2026-09-01T10:00:00Z",
            ends_at: "2026-09-01T10:30:00Z",
            participants: [{ name: "Ada", email: "ada@example.test" }],
            source_ref: {
              id: "calendar:cal-1",
              title: "Calendar review",
              url: "https://calendar.test/cal-1",
              provider: "Google Calendar",
            },
            notes: null,
            status: "attached",
            match_reason: "exact_email",
          },
        ],
        proposed: [
          {
            evidence_id: "meeting-granola",
            provider: "granola",
            provider_id: "granola-1",
            title: "Granola review",
            starts_at: "2026-09-02T10:00:00Z",
            ends_at: null,
            participants: [{ name: "Alyssa Lee", email: null }],
            source_ref: {
              id: "granola:granola-1",
              title: "Granola review",
              url: null,
              provider: "Granola",
            },
            notes: "Meeting notes",
            status: "proposed",
            match_reason: "name_only",
          },
        ],
        rejected: [],
      },
    });
    api.attachPersonMeeting.mockResolvedValue({ meeting: { status: "attached" } });
    api.rejectPersonMeeting.mockResolvedValue({ meeting: { status: "rejected" } });
    api.refreshPersonMeetings.mockResolvedValue({
      sources: {
        calendar: { status: "ok", records: 1 },
        granola: { status: "ok", records: 1 },
      },
    });
    api.openPersonSourcingChat.mockResolvedValue({
      created: true,
      session: { id: "thread one", title: "Sourcing · Alyssa Lee", n_msgs: 0 },
      active_person: { person_id: "person one", version: 2, label: "Alyssa Lee" },
    });
    api.setPersonSequence.mockResolvedValue({
      person: { person_id: "person one", sequence_state: "in_conversation" },
    });
    api.searchPersonDriveEvidence.mockResolvedValue({
      files: [
        {
          id: "drive-1",
          name: "Q3 sourcing notes",
          mimeType: "application/vnd.google-apps.document",
          modifiedTime: "2026-08-01T10:00:00Z",
          parents: [],
          webViewLink: "https://drive.google.com/open?id=drive-1",
          status: "metadata_only",
        },
      ],
    });
    api.attachPersonDriveEvidence.mockResolvedValue({
      source: { id: "source_ref_drive2", restricted: false },
      person: { person_id: "person one" },
    });
    api.savePersonHandoff.mockResolvedValue({
      person: { person_id: "person one" },
      brief: livingBrief(),
    });
  });

  it("announces that Contacts is loading", () => {
    api.getBoard.mockReturnValue(new Promise(() => {}));

    render(<BoardView />);

    expect(screen.getByRole("status")).toHaveTextContent("Loading contacts");
  });

  it("recovers from a Contacts load failure without leaking details", async () => {
    api.getBoard
      .mockRejectedValueOnce(new Error("contacts 500 token=private /state/path"))
      .mockResolvedValueOnce({ open: [], in_conversation: [], done: [] });

    const { container } = render(<BoardView />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Couldn’t load contacts");
    expect(container).not.toHaveTextContent("token=private");
    expect(container).not.toHaveTextContent("/state/path");

    fireEvent.click(within(alert).getByRole("button", { name: "Retry" }));

    expect(await screen.findByRole("heading", { name: "No active contacts" })).toBeInTheDocument();
    expect(api.getBoard).toHaveBeenCalledTimes(2);
  });

  it("renders active sequences in a Contacts table", async () => {
    render(<BoardView />);

    expect(
      await screen.findByRole("heading", { level: 1, name: "Contacts" }),
    ).toBeInTheDocument();
    const table = screen.getByRole("table", { name: "Active sourcing contacts" });
    expect(within(table).getByRole("columnheader", { name: "Contact" })).toBeInTheDocument();
    expect(within(table).getByRole("columnheader", { name: "Role & company" })).toBeInTheDocument();
    expect(within(table).getByRole("columnheader", { name: "Sequence" })).toBeInTheDocument();
    expect(within(table).getByRole("columnheader", { name: "Last contact" })).toBeInTheDocument();
    expect(within(table).getByRole("columnheader", { name: "Attention" })).toBeInTheDocument();
    expect(within(table).getByRole("link", { name: /Alyssa Lee/ })).toHaveAttribute(
      "href",
      "#/people/person%20one",
    );
    expect(screen.queryByRole("region", { name: "Open" })).not.toBeInTheDocument();
  });

  it("opens the Person File from pointer or keyboard interaction anywhere in a row", async () => {
    window.location.hash = "#/board";
    render(<BoardView />);

    const row = await screen.findByRole("row", { name: "Open Person File for Alyssa Lee" });
    expect(row).toHaveAttribute("tabindex", "0");

    fireEvent.click(within(row).getByText("Codeology"));
    expect(window.location.hash).toBe("#/people/person%20one");

    window.location.hash = "#/board";
    row.focus();
    fireEvent.keyDown(row, { key: "Enter" });
    expect(window.location.hash).toBe("#/people/person%20one");
  });

  it("filters Contacts by the three sequence states", async () => {
    api.getBoard.mockResolvedValue({
      backlog: [
        {
          person_id: "person-backlog",
          first_name: "Hidden",
          last_name: "Backlog",
          sequence_state: null,
        },
      ],
      open: [
        {
          person_id: "person-open",
          first_name: "Olive",
          last_name: "Open",
          title: "Founder",
          company: "Open Labs",
          sequence_state: "open",
        },
      ],
      in_conversation: [
        {
          person_id: "person-talking",
          first_name: "Connie",
          last_name: "Conversation",
          title: "VP Engineering",
          company: "Talking Systems",
          sequence_state: "in_conversation",
        },
      ],
      done: [
        {
          person_id: "person-done",
          first_name: "Dana",
          last_name: "Done",
          title: "Product Lead",
          company: "Done Works",
          sequence_state: "done",
        },
      ],
    });

    render(<BoardView />);

    const filters = await screen.findByRole("group", {
      name: "Filter contacts by sequence status",
    });
    expect(within(filters).getByRole("button", { name: "All 3" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(within(filters).getByRole("button", { name: "Open 1" })).toBeInTheDocument();
    expect(
      within(filters).getByRole("button", { name: "In conversation 1" }),
    ).toBeInTheDocument();
    expect(within(filters).getByRole("button", { name: "Done 1" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Hidden Backlog/ })).not.toBeInTheDocument();

    fireEvent.click(within(filters).getByRole("button", { name: "In conversation 1" }));

    expect(screen.getByRole("link", { name: /Connie Conversation/ })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Olive Open/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Dana Done/ })).not.toBeInTheDocument();
  });

  it("searches active Contacts by identity, role, and company", async () => {
    api.getBoard.mockResolvedValue({
      backlog: [],
      open: [
        {
          person_id: "person-maya",
          first_name: "Maya",
          last_name: "Chen",
          title: "VP Product Engineering",
          company: "Northstar Labs",
          sequence_state: "open",
        },
        {
          person_id: "person-jordan",
          first_name: "Jordan",
          last_name: "Patel",
          title: "Founder",
          company: "Applied Bio Systems",
          sequence_state: "open",
        },
      ],
      in_conversation: [],
      done: [],
    });

    render(<BoardView />);

    const search = await screen.findByRole("searchbox", { name: "Search active contacts" });
    fireEvent.change(search, { target: { value: "northstar" } });

    expect(screen.getByRole("link", { name: /Maya Chen/ })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Jordan Patel/ })).not.toBeInTheDocument();

    fireEvent.change(search, { target: { value: "no such contact" } });

    expect(screen.getByRole("status")).toHaveTextContent("No contacts match");
  });

  it("labels an Apollo-hidden surname honestly on Board and person file", async () => {
    api.getBoard.mockResolvedValue({
      open: [
        {
          person_id: "person one",
          first_name: "Hudson",
          last_name: null,
          last_name_status: "hidden_by_apollo",
          title: "CEO",
          company: "The Hog",
          sequence_state: "open",
        },
      ],
      in_conversation: [],
      done: [],
    });
    api.getPerson.mockResolvedValue({
      ...(await api.getPerson()),
      person: {
        person_id: "person one",
        first_name: "Hudson",
        last_name: null,
        last_name_status: "hidden_by_apollo",
        sequence_state: "open",
        version: 2,
        sources: [],
      },
      brief: livingBrief({
        who: "Hudson, CEO at The Hog",
        why: "YC F25 target",
        learned: [],
        missing: [],
        sources: [],
      }),
    });

    const board = render(<BoardView />);
    expect(await screen.findByText("Surname hidden by Apollo")).toBeInTheDocument();
    expect(board.container).not.toHaveTextContent("***");
    board.unmount();

    const person = render(<PersonFileView personId="person one" />);
    expect(await screen.findByText(/Enrich to verify the full name/i)).toBeInTheDocument();
    expect(person.container).not.toHaveTextContent("***");
  });

  it("keeps unsequenced backlog people off Contacts", async () => {
    api.getBoard.mockResolvedValue({
      backlog: [
        {
          person_id: "person-kept",
          first_name: "Hudson",
          last_name: "Liao",
          title: "CEO",
          company: "The Hog",
          sequence_state: null,
          board_lane: "backlog",
        },
      ],
      open: [],
      in_conversation: [],
      done: [],
    });

    render(<BoardView />);

    expect(await screen.findByRole("heading", { name: "No active contacts" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Hudson Liao/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("table", { name: "Active sourcing contacts" })).not.toBeInTheDocument();
  });

  it("updates a person sequence through labeled pressed-state controls", async () => {
    const { container } = render(<PersonFileView personId="person one" />);

    expect(await screen.findByRole("heading", { level: 1, name: "Alyssa Lee" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: "In conversation" }));

    await waitFor(() =>
      expect(api.setPersonSequence).toHaveBeenCalledWith(
        "person one",
        "in_conversation",
      ),
    );
    expect(screen.getByText("Found in a sourcing search.")).toBeInTheDocument();
    expect(container.querySelector(".tool-card")).not.toBeInTheDocument();
  });

  it("refreshes Contacts after a person-file write", async () => {
    render(<BoardView />);
    await screen.findByRole("link", { name: /Alyssa Lee/ });
    window.dispatchEvent(new CustomEvent("sourcecado:board-changed"));
    await waitFor(() => expect(api.getBoard).toHaveBeenCalledTimes(2));
  });

  it("can inspect and revert a prior person-file version", async () => {
    render(<PersonFileView personId="person one" />);
    expect(await screen.findByRole("heading", { name: "Version history" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Revert to version 1" }));
    await waitFor(() =>
      expect(api.revertPerson).toHaveBeenCalledWith("person one", {
        toVersion: 1,
        expectedVersion: 2,
        rationaleSummary: "Restore version 1 from person history.",
      }),
    );
  });

  it("creates the person's sourcing chat and navigates with both identities", async () => {
    window.location.hash = "#/people/person%20one";
    render(<PersonFileView personId="person one" />);
    fireEvent.click(
      await screen.findByRole("button", { name: "Create sourcing chat" }),
    );

    await waitFor(() =>
      expect(api.openPersonSourcingChat).toHaveBeenCalledWith("person one", 2),
    );
    await waitFor(() =>
      expect(window.location.hash).toBe(
        "#/chat/thread%20one/person/person%20one",
      ),
    );
  });

  it("offers the same single action as Open when the person is already bound", async () => {
    api.getPerson.mockResolvedValueOnce({
      ...(await api.getPerson()),
      sourcing_chat: { session_id: "thread one", person_id: "person one" },
    });
    render(<PersonFileView personId="person one" />);

    expect(
      await screen.findByRole("button", { name: "Open sourcing chat" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Create sourcing chat" }),
    ).not.toBeInTheDocument();
  });

  it("keeps a stale person-chat failure visible without navigating", async () => {
    window.location.hash = "#/people/person%20one";
    api.openPersonSourcingChat.mockRejectedValueOnce(new Error("stale version"));
    render(<PersonFileView personId="person one" />);
    fireEvent.click(
      await screen.findByRole("button", { name: "Create sourcing chat" }),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Refresh the person file",
    );
    expect(window.location.hash).toBe("#/people/person%20one");
  });

  it("shows missing person recovery without offering a chat action", async () => {
    api.getPerson.mockRejectedValueOnce(new Error("not found"));
    render(<PersonFileView personId="missing-person" />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Couldn’t load this person file",
    );
    expect(
      screen.queryByRole("button", { name: /sourcing chat/i }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to Contacts" })).toHaveAttribute(
      "href",
      "#/board",
    );
  });

  it("renders attached meetings and explicit review actions for uncertain matches", async () => {
    render(<PersonFileView personId="person one" />);

    expect(await screen.findByRole("heading", { name: "Meeting evidence" })).toBeInTheDocument();
    expect(screen.getByText("Calendar review")).toBeInTheDocument();
    expect(screen.getByText("Granola review")).toBeInTheDocument();
    expect(screen.getByText("Name-only match — review required")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Attach Granola review" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reject Granola review" })).toBeInTheDocument();
  });

  it("refreshes, attaches, and rejects meeting evidence from the person view", async () => {
    render(<PersonFileView personId="person one" />);
    await screen.findByRole("heading", { name: "Meeting evidence" });

    fireEvent.click(screen.getByRole("button", { name: "Refresh meeting evidence" }));
    await waitFor(() => expect(api.refreshPersonMeetings).toHaveBeenCalledWith("person one"));
    expect(await screen.findByText("Calendar and Granola refreshed.")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Attach Granola review" }));
    await waitFor(() =>
      expect(api.attachPersonMeeting).toHaveBeenCalledWith(
        "person one",
        "meeting-granola",
      ),
    );

    fireEvent.click(screen.getByRole("button", { name: "Reject Granola review" }));
    await waitFor(() =>
      expect(api.rejectPersonMeeting).toHaveBeenCalledWith(
        "person one",
        "meeting-granola",
      ),
    );
  });

  it("shows independent partial-source refresh status", async () => {
    api.refreshPersonMeetings.mockResolvedValueOnce({
      sources: {
        calendar: { status: "failed", error: "unavailable" },
        granola: { status: "ok", records: 1 },
      },
    });
    render(<PersonFileView personId="person one" />);
    await screen.findByRole("heading", { name: "Meeting evidence" });

    fireEvent.click(screen.getByRole("button", { name: "Refresh meeting evidence" }));

    expect(
      await screen.findByText("Granola refreshed; Calendar is unavailable."),
    ).toBeInTheDocument();
  });

  it("shows meeting evidence and Drive evidence as sibling sections on one person file", async () => {
    render(<PersonFileView personId="person one" />);

    const meetings = await screen.findByRole("region", { name: "Meeting evidence" });
    const drive = screen.getByRole("region", { name: "Drive evidence" });

    expect(within(meetings).getByText("Calendar review")).toBeInTheDocument();
    expect(within(meetings).getByText("Granola review")).toBeInTheDocument();
    expect(
      within(meetings).getByRole("button", { name: "Refresh meeting evidence" }),
    ).toBeInTheDocument();
    expect(within(meetings).queryByText("Fall sourcing masterdoc")).not.toBeInTheDocument();
    expect(within(meetings).queryByLabelText("Search Drive")).not.toBeInTheDocument();

    expect(within(drive).getByText("Fall sourcing masterdoc")).toBeInTheDocument();
    expect(within(drive).getByLabelText("Search Drive")).toBeInTheDocument();
    expect(within(drive).queryByText("Calendar review")).not.toBeInTheDocument();
    expect(
      within(drive).queryByRole("button", { name: "Refresh meeting evidence" }),
    ).not.toBeInTheDocument();
  });

  it("renders attached Drive evidence on the person file", async () => {
    render(<PersonFileView personId="person one" />);

    expect(await screen.findByRole("heading", { name: "Drive evidence" })).toBeInTheDocument();
    expect(screen.getByText("Fall sourcing masterdoc")).toBeInTheDocument();
  });

  it("searches Drive and attaches a result with an explicit action", async () => {
    render(<PersonFileView personId="person one" />);
    await screen.findByRole("heading", { name: "Drive evidence" });

    fireEvent.change(screen.getByLabelText("Search Drive"), {
      target: { value: "sourcing" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Search Drive" }));

    await waitFor(() =>
      expect(api.searchPersonDriveEvidence).toHaveBeenCalledWith("person one", "sourcing"),
    );
    expect(await screen.findByText("Q3 sourcing notes")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Attach Q3 sourcing notes" }));

    await waitFor(() =>
      expect(api.attachPersonDriveEvidence).toHaveBeenCalledWith("person one", {
        kind: "search_result",
        fileId: "drive-1",
      }),
    );
    expect(
      await screen.findByText("Attached “Q3 sourcing notes” to this person."),
    ).toBeInTheDocument();
  });
});
