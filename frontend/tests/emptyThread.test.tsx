import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Board, Connector, SkillInfo } from "../src/api";
import { ChatPage } from "../src/routes/ChatPage";
import { connector } from "./connectorFixtures";

const api = vi.hoisted(() => ({
  getSessions: vi.fn(),
  getSession: vi.fn(),
  getCurrentRunTelemetry: vi.fn(),
  getBoard: vi.fn(),
  getConnectors: vi.fn(),
  getSkills: vi.fn(),
  openChat: vi.fn(),
}));
const chatSend = vi.fn();

vi.mock("../src/api", () => ({
  getSessions: api.getSessions,
  getSession: api.getSession,
  getCurrentRunTelemetry: api.getCurrentRunTelemetry,
  getBoard: api.getBoard,
  getConnectors: api.getConnectors,
  getSkills: api.getSkills,
  hasToken: () => true,
  openChat: api.openChat,
}));

const GREETING = /^Good (morning|afternoon|evening)$/;

function board(overrides: Partial<Board> = {}): Board {
  return { backlog: [], open: [], in_conversation: [], done: [], ...overrides };
}

const seededBoard = board({
  open: [
    {
      person_id: "maya",
      first_name: "Maya",
      last_name: "Chen",
      title: "Founder",
      company: "Acme Robotics",
      target: "Bay Area robotics founders",
      sequence_state: "open",
      last_contact_at: "2026-10-20T10:00:00Z",
      follow_up: { needed: true, reason: "reply_unanswered" },
    },
    {
      person_id: "dev",
      first_name: "Dev",
      last_name: "Patel",
      title: null,
      company: null,
      sequence_state: "open",
      last_contact_at: "2026-10-12T10:00:00Z",
      follow_up: { needed: true, reason: "reply_needs_review" },
    },
  ],
});

const skills: SkillInfo[] = [
  {
    name: "weekly-sourcing",
    purpose: "Builds a shortlist.",
    useWhen: "Weekly check-in.",
    source: "builtin",
    status: "ready",
    instructions: "",
  },
];

const connectors: Connector[] = [
  connector({ id: "gmail", title: "Gmail", status: "reconnect_required" }),
];

function seed(overrides: { board?: Board; connectors?: Connector[]; skills?: SkillInfo[] } = {}) {
  api.getBoard.mockResolvedValue(overrides.board ?? seededBoard);
  api.getConnectors.mockResolvedValue({ connectors: overrides.connectors ?? connectors });
  api.getSkills.mockResolvedValue({ skills: overrides.skills ?? skills });
}

async function renderEmpty() {
  render(<ChatPage sessionId="thread-alpha" />);
  await screen.findByRole("heading", { name: GREETING });
  // Let the three reads settle.
  await act(async () => {});
}

describe("Empty thread", () => {
  beforeEach(() => {
    window.localStorage.clear();
    chatSend.mockReset();
    api.getSessions.mockResolvedValue({ sessions: [], open_id: "thread-alpha" });
    api.getSession.mockResolvedValue({
      id: "thread-alpha",
      title: null,
      messages: [],
      events: [],
    });
    api.getCurrentRunTelemetry.mockResolvedValue({
      version: 1,
      session_id: "thread-alpha",
      current_run: null,
    });
    api.openChat.mockImplementation(() => ({
      send: chatSend,
      approve: vi.fn(),
      cancel: vi.fn(),
      queue: vi.fn(),
      recover: vi.fn(),
      close: vi.fn(),
    }));
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
      configurable: true,
      value: vi.fn(),
    });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  it("greets by time of day with no name, above the composer", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date(2026, 9, 26, 14, 30));
    seed();
    render(<ChatPage sessionId="thread-alpha" />);

    const greeting = await screen.findByRole("heading", { name: "Good afternoon" });
    const composer = screen.getByRole("textbox", { name: "Message Sourcecado" });
    expect(
      greeting.compareDocumentPosition(composer) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    // The owl is decoration: it carries no name and no text.
    expect(greeting.querySelector(".sourcecado-empty-owl")).toHaveAttribute("aria-hidden", "true");
    expect(greeting).toHaveTextContent(/^Good afternoon$/);
  });

  it("shows four chips from the operator's own data, under the composer", async () => {
    seed();
    await renderEmpty();

    const group = screen.getByRole("group", { name: "Suggested prompts" });
    const chips = within(group).getAllByRole("button");
    expect(chips.map((chip) => chip.textContent)).toEqual([
      "Catch up on Bay Area robotics founders",
      "Follow up with 2 people",
      "Fix Gmail",
      "Run Weekly sourcing",
    ]);
    // Every chip carries an icon, hidden from the accessibility tree.
    for (const chip of chips) {
      expect(chip.querySelector("svg")?.closest("[aria-hidden='true']")).not.toBeNull();
    }
    const composer = screen.getByRole("textbox", { name: "Message Sourcecado" });
    expect(
      composer.compareDocumentPosition(group) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("fills the composer on a click and does not send", async () => {
    seed();
    await renderEmpty();

    fireEvent.click(screen.getByRole("button", { name: "Run Weekly sourcing" }));

    const composer = screen.getByRole("textbox", { name: "Message Sourcecado" });
    expect(composer).toHaveValue("Use the weekly-sourcing skill.");
    expect(composer).toHaveFocus();
    expect(chatSend).not.toHaveBeenCalled();
    expect(window.localStorage.getItem("sourcecado.chat.draft.v1:thread-alpha")).toBe(
      "Use the weekly-sourcing skill.",
    );
    // Only the open thread keeps the draft.
    expect(window.localStorage.getItem("sourcecado.chat.draft.v1:thread-beta")).toBeNull();
    expect(screen.queryByRole("log")?.textContent ?? "").not.toContain("weekly-sourcing");

    fireEvent.click(screen.getByRole("button", { name: "Fix Gmail" }));
    expect(composer).toHaveValue(
      "Gmail shows “Reconnect required”. What can you not do until it is fixed, and how do I fix it?",
    );
    expect(chatSend).not.toHaveBeenCalled();
  });

  it("shows no chip and no placeholder when there is no data", async () => {
    seed({ board: board(), connectors: [], skills: [] });
    await renderEmpty();

    expect(screen.queryByRole("group", { name: "Suggested prompts" })).toBeNull();
    expect(screen.getByRole("textbox", { name: "Message Sourcecado" })).toBeInTheDocument();
  });

  it("shows fewer chips when one source fails, and never invents a replacement", async () => {
    api.getBoard.mockRejectedValue(new Error("board 500"));
    api.getConnectors.mockResolvedValue({ connectors: [] });
    api.getSkills.mockResolvedValue({ skills });
    await renderEmpty();

    const chips = within(screen.getByRole("group", { name: "Suggested prompts" })).getAllByRole(
      "button",
    );
    expect(chips.map((chip) => chip.textContent)).toEqual(["Run Weekly sourcing"]);
  });

  it("shows each chip as its own source answers, and a source that never answers holds back no other", async () => {
    api.getBoard.mockResolvedValue(seededBoard);
    api.getSkills.mockResolvedValue({ skills });
    api.getConnectors.mockReturnValue(new Promise(() => {}));
    await renderEmpty();

    const chips = within(screen.getByRole("group", { name: "Suggested prompts" })).getAllByRole(
      "button",
    );
    expect(chips.map((chip) => chip.textContent)).toEqual([
      "Catch up on Bay Area robotics founders",
      "Follow up with 2 people",
      "Run Weekly sourcing",
    ]);
  });

  it("keeps the other chips when the board answers 200 with no lanes", async () => {
    seed();
    api.getBoard.mockResolvedValue({});
    await renderEmpty();

    const chips = within(screen.getByRole("group", { name: "Suggested prompts" })).getAllByRole(
      "button",
    );
    expect(chips.map((chip) => chip.textContent)).toEqual(["Fix Gmail", "Run Weekly sourcing"]);
  });

  it("shows neither greeting nor chips on a thread that already has messages", async () => {
    seed();
    api.getSession.mockResolvedValue({
      id: "thread-alpha",
      title: "Find robotics founders",
      messages: [{ role: "user", content: "Find robotics founders" }],
      events: [],
    });
    render(<ChatPage sessionId="thread-alpha" />);

    expect(await screen.findByText("Find robotics founders", { selector: "p, div, span" })).toBeTruthy();
    expect(screen.queryByRole("heading", { name: GREETING })).toBeNull();
    expect(screen.queryByRole("group", { name: "Suggested prompts" })).toBeNull();
  });
});
