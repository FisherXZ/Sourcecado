import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { BoardView } from "../src/Board";
import { PersonFileView } from "../src/PersonFile";
import { livingBrief } from "./livingBrief";

const api = vi.hoisted(() => ({
  attachPersonMeeting: vi.fn(),
  attachPersonDriveEvidence: vi.fn(),
  getBoard: vi.fn(),
  getPerson: vi.fn(),
  savePersonHandoff: vi.fn(),
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
  getPerson: api.getPerson,
  openPersonSourcingChat: api.openPersonSourcingChat,
  refreshPersonMeetings: api.refreshPersonMeetings,
  refreshReplies: api.refreshReplies,
  rejectPersonMeeting: api.rejectPersonMeeting,
  revertPerson: api.revertPerson,
  savePersonHandoff: api.savePersonHandoff,
  searchPersonDriveEvidence: api.searchPersonDriveEvidence,
  setPersonSequence: api.setPersonSequence,
}));

const WAITING = {
  person_id: "person-waiting",
  first_name: "Ada",
  last_name: "Analytic",
  title: "Head of Data",
  company: "Analytic",
  sequence_state: "open",
  last_contact_at: "2026-08-20T10:00:00+00:00",
  last_contact_direction: "outbound" as const,
  replied: false,
  replied_at: null,
  follow_up: { needed: false, reason: null },
};

const REPLIED = {
  person_id: "person-replied",
  first_name: "Bob",
  last_name: "Builder",
  title: "Staff Engineer",
  company: "Analytic",
  sequence_state: "in_conversation",
  last_contact_at: "2026-08-26T09:15:00+00:00",
  last_contact_direction: "inbound" as const,
  replied: true,
  replied_at: "2026-08-26T09:15:00+00:00",
  follow_up: { needed: true, reason: "reply_unanswered" as const },
};

const NEEDS_REVIEW = {
  ...WAITING,
  person_id: "person-review",
  first_name: "Cleo",
  last_name: "Chen",
  follow_up: { needed: true, reason: "reply_needs_review" as const },
};

function boardWith(open: unknown[], inConversation: unknown[]) {
  return { open, in_conversation: inConversation, done: [] };
}

function personFile(overrides: Record<string, unknown> = {}) {
  return {
    person: {
      person_id: "person-replied",
      first_name: "Bob",
      last_name: "Builder",
      sequence_state: "in_conversation",
      version: 3,
      replied: true,
      last_contact_direction: "inbound",
      last_contact_at: "2026-08-26T09:15:00+00:00",
      follow_up: { needed: true, reason: "reply_unanswered" },
      sources: [],
      knowledge_gaps: [],
      ...((overrides.person as Record<string, unknown>) ?? {}),
    },
    brief: livingBrief({
      who: "Bob Builder",
      why: "Strong systems fit.",
      learned: [],
      missing: [],
      sources: ["gmail"],
    }),
    timeline: [
      {
        event_id: "event-send",
        source: "gmail",
        kind: "send",
        summary: "Sent approved outreach to bob@analytic.example",
        payload: { sent: true, message_id: "out_1", thread_id: "thread_1" },
      },
      {
        event_id: "event-reply",
        source: "gmail",
        kind: "mail",
        summary: "Reply from bob@analytic.example: Re: Thursday?",
        payload: {
          direction: "inbound",
          message_id: "in_1",
          thread_id: "thread_1",
          from: "bob@analytic.example",
          subject: "Re: Thursday?",
          snippet: "Thursday works. Send an invite.",
          received_at: "2026-08-26T09:15:00+00:00",
          source_ref: {
            provider: "Gmail",
            message_id: "in_1",
            thread_id: "thread_1",
            url: "https://mail.google.com/mail/u/0/#all/thread_1",
          },
        },
      },
    ],
    versions: [],
    sourcing_chat: null,
    meeting_evidence: { attached: [], proposed: [], rejected: [] },
    ...overrides,
  };
}

describe("reply filing on the Board and the person file", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getBoard.mockResolvedValue(boardWith([WAITING], [REPLIED]));
    api.getPerson.mockResolvedValue(personFile());
    api.refreshReplies.mockResolvedValue({
      refresh: { status: "ok", mode: "incremental", scanned: 3, filed: 1, unassigned: 0 },
      board: boardWith([WAITING], [REPLIED]),
    });
    api.searchPersonDriveEvidence.mockResolvedValue({ files: [] });
  });

  it("shows last contact, replied state, and the follow-up flag on the board", async () => {
    render(<BoardView />);

    const waiting = await screen.findByRole("link", { name: /Ada Analytic/ });
    expect(waiting).toHaveTextContent("We wrote · 2026-08-20");
    expect(waiting).not.toHaveTextContent("Needs follow-up");

    const talking = screen.getByRole("link", { name: /Bob Builder/ });
    expect(talking).toHaveTextContent("They replied · 2026-08-26");
    expect(talking).toHaveTextContent("Needs follow-up");
  });

  it("flags an unassigned reply as needing review rather than as a reply", async () => {
    api.getBoard.mockResolvedValue(boardWith([NEEDS_REVIEW], []));
    render(<BoardView />);

    const row = await screen.findByRole("link", { name: /Cleo Chen/ });
    expect(row).toHaveTextContent("Reply needs review");
    expect(row).not.toHaveTextContent("They replied");
  });

  it("says a person with no contact has none rather than showing a blank date", async () => {
    api.getBoard.mockResolvedValue(
      boardWith(
        [{ ...WAITING, last_contact_at: null, last_contact_direction: null }],
        [],
      ),
    );
    render(<BoardView />);

    expect(await screen.findByRole("link", { name: /Ada Analytic/ })).toHaveTextContent(
      "No contact yet",
    );
  });

  it("checks for replies on demand and reports what the refresh did", async () => {
    render(<BoardView />);
    await screen.findByRole("link", { name: /Ada Analytic/ });

    fireEvent.click(screen.getByRole("button", { name: "Check for replies" }));

    await waitFor(() => expect(api.refreshReplies).toHaveBeenCalledTimes(1));
    expect(await screen.findByText("Filed 1 reply.")).toBeInTheDocument();
  });

  it("reports replies that need review instead of calling them filed", async () => {
    api.refreshReplies.mockResolvedValue({
      refresh: { status: "ok", mode: "incremental", scanned: 2, filed: 0, unassigned: 2 },
      board: boardWith([NEEDS_REVIEW], []),
    });
    render(<BoardView />);
    await screen.findByRole("link", { name: /Ada Analytic/ });
    fireEvent.click(screen.getByRole("button", { name: "Check for replies" }));

    expect(await screen.findByText("2 replies need review.")).toBeInTheDocument();
  });

  it("says nothing arrived when the refresh read messages and filed none", async () => {
    api.refreshReplies.mockResolvedValue({
      refresh: { status: "ok", mode: "incremental", scanned: 4, filed: 0, unassigned: 0 },
      board: boardWith([WAITING], []),
    });
    render(<BoardView />);
    await screen.findByRole("link", { name: /Ada Analytic/ });
    fireEvent.click(screen.getByRole("button", { name: "Check for replies" }));

    expect(
      await screen.findByText("Checked 4 messages. No new replies."),
    ).toBeInTheDocument();
  });

  it("keeps the board and says the cursor held when Gmail is unreachable", async () => {
    api.refreshReplies.mockResolvedValue({
      refresh: { status: "failed", mode: "incremental", scanned: 0, filed: 0, unassigned: 0 },
      board: boardWith([WAITING], []),
    });
    render(<BoardView />);
    await screen.findByRole("link", { name: /Ada Analytic/ });
    fireEvent.click(screen.getByRole("button", { name: "Check for replies" }));

    expect(
      await screen.findByText(
        "Couldn’t reach Gmail. The last checked point is unchanged.",
      ),
    ).toBeInTheDocument();
  });

  it("shows the reply summary, timestamp, source, and follow-up state on the person", async () => {
    render(<PersonFileView personId="person-replied" />);

    const section = await screen.findByRole("region", { name: "Replies" });
    expect(
      within(section).getByText("They replied and are waiting on a response."),
    ).toBeInTheDocument();
    expect(
      within(section).getByText("bob@analytic.example — Re: Thursday?"),
    ).toBeInTheDocument();
    expect(
      within(section).getByText("Thursday works. Send an invite."),
    ).toBeInTheDocument();
    expect(within(section).getByText(/2026-08-26T09:15:00/)).toBeInTheDocument();
    expect(
      within(section).getByRole("link", { name: /Open Gmail thread thread_1/ }),
    ).toHaveAttribute("href", "https://mail.google.com/mail/u/0/#all/thread_1");
  });

  it("shows an unassigned reply as a question, not as this person's reply", async () => {
    api.getPerson.mockResolvedValue(
      personFile({
        person: {
          replied: false,
          follow_up: { needed: true, reason: "reply_needs_review" },
          knowledge_gaps: [
            {
              id: "knowledge_gap_1",
              person_id: "person-replied",
              type: "knowledge_gap",
              restricted: false,
              fields: {
                kind: "unassigned_reply",
                provider: "Gmail",
                evidence: "ambiguous",
                message_id: "in_2",
                thread_id: "thread_1",
                reason: "sender_is_not_the_recipient",
                question:
                  "Who sent this reply? It arrived on this person's Gmail thread from an address we did not write to.",
                candidate_count: 1,
                received_at: "2026-08-27T08:00:00+00:00",
              },
            },
          ],
        },
        timeline: [],
      }),
    );
    render(<PersonFileView personId="person-replied" />);

    const section = await screen.findByRole("region", { name: "Replies" });
    expect(
      within(section).getByText(
        "A reply arrived that could not be tied to this person.",
      ),
    ).toBeInTheDocument();
    expect(within(section).getByRole("heading", { name: "Unassigned replies" })).toBeInTheDocument();
    expect(within(section).getByText(/Who sent this reply\?/)).toBeInTheDocument();
    expect(
      within(section).getByText(/Nothing was filed on this person/),
    ).toBeInTheDocument();
    expect(within(section).getByText("No inbound reply filed yet.")).toBeInTheDocument();
  });

  it("says no reply yet when nothing has come back", async () => {
    api.getPerson.mockResolvedValue(
      personFile({
        person: {
          sequence_state: "open",
          replied: false,
          last_contact_direction: "outbound",
          follow_up: { needed: false, reason: null },
        },
        timeline: [],
      }),
    );
    render(<PersonFileView personId="person-replied" />);

    const section = await screen.findByRole("region", { name: "Replies" });
    expect(within(section).getByText("No reply yet.")).toBeInTheDocument();
  });
});
