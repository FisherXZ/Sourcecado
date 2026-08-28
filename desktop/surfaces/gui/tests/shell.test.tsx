import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "../src/App";
import { livingBrief } from "./livingBrief";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

const api = vi.hoisted(() => ({
  createScheduleJob: vi.fn(),
  createSession: vi.fn(),
  getBoard: vi.fn(),
  getConnectors: vi.fn(),
  getCurrentRunTelemetry: vi.fn(),
  getGmail: vi.fn(),
  getHealth: vi.fn(),
  getInbox: vi.fn(),
  getMemoryBacklog: vi.fn(),
  getQuarantinedEffects: vi.fn(),
  getPersona: vi.fn(),
  getPerson: vi.fn(),
  getSchedule: vi.fn(),
  getSession: vi.fn(),
  getSessions: vi.fn(),
  getSettings: vi.fn(),
  getSkills: vi.fn(),
  hasToken: vi.fn(),
  openChat: vi.fn(),
  openPersonSourcingChat: vi.fn(),
  pinSession: vi.fn(),
  renameSession: vi.fn(),
  setLastDestination: vi.fn(),
  setPersonSequence: vi.fn(),
}));

vi.mock("../src/api", () => ({
  connectCalendar: vi.fn(),
  connectDrive: vi.fn(),
  connectGmail: vi.fn(),
  connectGranola: vi.fn(),
  createScheduleJob: api.createScheduleJob,
  createSession: api.createSession,
  disconnectGmail: vi.fn(),
  getBoard: api.getBoard,
  getConnectors: api.getConnectors,
  getCurrentRunTelemetry: api.getCurrentRunTelemetry,
  getGmail: api.getGmail,
  getHealth: api.getHealth,
  getInbox: api.getInbox,
  getMemoryBacklog: api.getMemoryBacklog,
  getQuarantinedEffects: api.getQuarantinedEffects,
  getPersona: api.getPersona,
  getPerson: api.getPerson,
  getSchedule: api.getSchedule,
  getSession: api.getSession,
  getSessions: api.getSessions,
  getSettings: api.getSettings,
  getSkills: api.getSkills,
  hasToken: api.hasToken,
  openChat: api.openChat,
  openPersonSourcingChat: api.openPersonSourcingChat,
  pinSession: api.pinSession,
  renameSession: api.renameSession,
  resolveInbox: vi.fn(),
  runScheduleJob: vi.fn(),
  setPersona: vi.fn(),
  setLastDestination: api.setLastDestination,
  setPersonSequence: api.setPersonSequence,
}));

describe("App shell routing", () => {
  beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn();
    window.localStorage.clear();
    window.location.hash = "#/skills";
    api.getConnectors.mockResolvedValue({ connectors: [] });
    api.getCurrentRunTelemetry.mockResolvedValue({
      version: 1,
      session_id: "thread-alpha",
      current_run: null,
    });
    api.getBoard.mockResolvedValue({ open: [], in_conversation: [], done: [] });
    api.getGmail.mockResolvedValue({ connected: false, email: null });
    api.getHealth.mockResolvedValue({ status: "ok", piece: "test", slice: 1, model: "test" });
    api.getInbox.mockResolvedValue({ items: [] });
    api.getMemoryBacklog.mockResolvedValue({ needs_review: 0, classified: 0, items: [] });
    api.getQuarantinedEffects.mockResolvedValue([]);
    api.getPersona.mockResolvedValue({ id: "sourcing", name: "Sourcing Director", tools: [] });
    api.getPerson.mockResolvedValue({
      person: { person_id: "person-1", sequence_state: "open" },
      brief: livingBrief({ who: "Alyssa", why: "Strong fit", learned: [], missing: [], sources: [] }),
      timeline: [],
    });
    api.getSchedule.mockResolvedValue({ jobs: [], runs: [] });
    api.getSessions.mockResolvedValue({ sessions: [], open_id: null, last_destination: null });
    api.getSession.mockResolvedValue({ id: "", title: null, messages: [] });
    api.getSettings.mockResolvedValue({
      persona: { id: "sourcing", name: "Sourcing Director" },
      model: "test",
      gmail: { connected: false, email: null },
      apollo: { configured: false },
    });
    api.getSkills.mockResolvedValue({ skills: [] });
    api.hasToken.mockReturnValue(true);
    api.openChat.mockReturnValue({ send: vi.fn(), approve: vi.fn(), close: vi.fn() });
    api.openPersonSourcingChat.mockResolvedValue({
      created: true,
      session: { id: "thread-person", title: "Sourcing · Alyssa", n_msgs: 0 },
      active_person: { person_id: "person-1", version: 1, label: "Alyssa" },
    });
    api.pinSession.mockResolvedValue({ id: "alpha", title: "Alpha", pinned: true });
    api.renameSession.mockResolvedValue({ id: "alpha", title: "Renamed Alpha" });
    api.setLastDestination.mockResolvedValue({ destination: "#/skills" });
    api.setPersonSequence.mockResolvedValue({
      person: { person_id: "person-1", sequence_state: "open" },
    });
  });

  it("renders a direct Skills hash route", async () => {
    render(<App />);

    expect(await screen.findByRole("heading", { level: 1, name: "Skills" })).toBeInTheDocument();
  });

  it("renders Board as a direct durable rail destination", async () => {
    window.location.hash = "#/board";

    render(<App />);

    expect(await screen.findByRole("heading", { level: 1, name: "Board" })).toBeInTheDocument();
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByRole("link", { name: "Board" })).toHaveAttribute("aria-current", "page");
  });

  it("renders a decoded person file under the active Board destination", async () => {
    window.location.hash = "#/people/person%20one";

    render(<App />);

    expect(await screen.findByRole("heading", { level: 1, name: "Alyssa" })).toBeInTheDocument();
    expect(api.getPerson).toHaveBeenCalledWith("person one");
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByRole("link", { name: "Board" })).toHaveAttribute("aria-current", "page");
  });

  it("opens a newly created person chat before the cached session list refreshes", async () => {
    window.location.hash = "#/people/person-1";
    api.getPerson.mockResolvedValue({
      person: { person_id: "person-1", sequence_state: "open", version: 1 },
      brief: livingBrief({ who: "Alyssa", why: "Strong fit", learned: [], missing: [], sources: [] }),
      timeline: [],
      sourcing_chat: null,
    });
    api.getSessions.mockResolvedValue({ sessions: [], open_id: null, last_destination: null });
    api.getSession.mockResolvedValue({
      id: "thread-person",
      title: "Sourcing · Alyssa",
      messages: [],
      events: [],
      active_person: { person_id: "person-1", version: 1, label: "Alyssa" },
    });
    render(<App />);

    fireEvent.click(
      await screen.findByRole("button", { name: "Create sourcing chat" }),
    );

    await waitFor(() =>
      expect(api.getSession).toHaveBeenCalledWith("thread-person", "person-1"),
    );
    expect(
      screen.getByRole("link", { name: "Active person: Alyssa" }),
    ).toBeInTheDocument();
  });

  it("renders a direct Scheduled hash route", async () => {
    window.location.hash = "#/scheduled";

    render(<App />);

    expect(await screen.findByRole("heading", { level: 1, name: "Scheduled" })).toBeInTheDocument();
  });

  it("renders a direct Connections hash route", async () => {
    window.location.hash = "#/connections";

    render(<App />);

    expect(await screen.findByRole("heading", { level: 1, name: "Connections" })).toBeInTheDocument();
  });

  it("renders a direct connector detail hash with one page heading", async () => {
    window.location.hash = "#/connections/google-drive";

    render(<App />);

    expect(await screen.findAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByText("google-drive")).toBeInTheDocument();
  });

  it("renders a direct Settings hash route", async () => {
    window.location.hash = "#/settings";

    render(<App />);

    expect(await screen.findByRole("heading", { level: 1, name: "Settings" })).toBeInTheDocument();
  });

  it("loads the thread named by a direct chat hash route", async () => {
    window.location.hash = "#/chat/thread-alpha";
    api.getSession.mockResolvedValue({ id: "thread-alpha", title: "Alpha", messages: [] });

    render(<App />);

    await waitFor(() => expect(api.getSession).toHaveBeenCalledWith("thread-alpha"));
  });

  it("keeps the person identity on the canonical bound-chat route", async () => {
    window.location.hash = "#/chat/thread-alpha/person/person%20one";
    api.getSessions.mockResolvedValue({
      sessions: [
        {
          session_id: "thread-alpha",
          title: "Sourcing · Alyssa",
          n_msgs: 0,
          pinned: false,
          opened_at: "2026-08-27T10:00:00Z",
          updated_at: "2026-08-27T10:00:00Z",
        },
      ],
      open_id: "thread-alpha",
      last_destination: "#/chat/thread-alpha/person/person%20one",
    });
    api.getSession.mockResolvedValue({
      id: "thread-alpha",
      title: "Sourcing · Alyssa",
      messages: [],
      events: [],
      active_person: {
        person_id: "person one",
        version: 2,
        label: "Alyssa Lee",
      },
    });

    render(<App />);

    await waitFor(() =>
      expect(api.getSession).toHaveBeenCalledWith("thread-alpha", "person one"),
    );
    expect(
      screen.getByRole("link", { name: "Active person: Alyssa Lee" }),
    ).toHaveAttribute("href", "#/people/person%20one");
  });

  it("does not persist a scheduled transcript over the last normal destination", async () => {
    window.location.hash = "#/chat/sched-4";
    api.getSessions.mockResolvedValue({
      sessions: [
        {
          session_id: "thread-alpha",
          title: "Alpha",
          n_msgs: 1,
          pinned: false,
          opened_at: "2026-08-25T10:00:00Z",
          updated_at: "2026-08-25T10:00:00Z",
        },
      ],
      open_id: "thread-alpha",
      last_destination: "#/chat/thread-alpha",
    });
    api.getSession.mockResolvedValue({
      id: "sched-4",
      title: "Weekly priority review",
      messages: [],
      events: [],
    });

    render(<App />);

    await waitFor(() => expect(api.getSession).toHaveBeenCalledWith("sched-4"));
    expect(api.setLastDestination).not.toHaveBeenCalledWith("#/chat/sched-4");
  });

  it("renders one labeled app rail with an active durable destination", async () => {
    render(<App />);

    const nav = await screen.findByRole("navigation", { name: "Sourcecado" });
    expect(within(nav).getByRole("button", { name: "New chat" })).toBeInTheDocument();
    expect(within(nav).getByRole("button", { name: /Search/ })).toHaveTextContent("⌘K");
    expect(within(nav).getByRole("link", { name: "Board" })).toBeInTheDocument();
    expect(within(nav).getByRole("link", { name: "Scheduled" })).toBeInTheDocument();
    expect(within(nav).getByRole("link", { name: "Connections" })).toBeInTheDocument();
    expect(within(nav).getByRole("link", { name: "Skills" })).toHaveAttribute("aria-current", "page");
    expect(within(nav).getByRole("link", { name: "Settings" })).toBeInTheDocument();
  });

  it("surfaces scheduled approvals as an Inbox badge linked to context", async () => {
    api.getInbox.mockResolvedValue({
      items: [
        { id: "approval-1", state: "pending", session_id: "sched-4" },
        { id: "approval-2", state: "pending", session_id: "sched-7" },
        { id: "approval-chat", state: "pending", session_id: "thread-alpha" },
      ],
    });

    render(<App />);

    const nav = await screen.findByRole("navigation", { name: "Sourcecado" });
    const scheduled = within(nav).getByRole("link", { name: /Scheduled/ });
    expect(scheduled).toHaveAttribute("href", "#/scheduled");
    expect(within(scheduled).getByLabelText("Inbox: 2 waiting approvals")).toHaveTextContent("2");
  });

  it("refreshes the Inbox badge after a scheduled run changes approvals", async () => {
    api.getInbox
      .mockResolvedValueOnce({
        items: [{ id: "approval-1", state: "pending", session_id: "sched-4" }],
      })
      .mockResolvedValueOnce({ items: [] });

    render(<App />);
    const nav = await screen.findByRole("navigation", { name: "Sourcecado" });
    expect(within(nav).getByLabelText("Inbox: 1 waiting approval")).toBeInTheDocument();

    window.dispatchEvent(new CustomEvent("sourcecado:inbox-changed"));

    await waitFor(() => expect(api.getInbox).toHaveBeenCalledTimes(2));
    expect(within(nav).queryByLabelText(/Inbox:/)).not.toBeInTheDocument();
  });

  it("updates the route outlet and active state after hash navigation", async () => {
    render(<App />);

    fireEvent.click(screen.getByRole("link", { name: "Scheduled" }));

    expect(await screen.findByRole("heading", { level: 1, name: "Scheduled" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Scheduled" })).toHaveAttribute("aria-current", "page");
  });

  it("restores the durable last destination when booting without a hash", async () => {
    window.location.hash = "";
    api.getSessions.mockResolvedValue({
      sessions: [],
      open_id: null,
      last_destination: "#/settings",
    });

    render(<App />);

    expect(await screen.findByRole("heading", { level: 1, name: "Settings" })).toBeInTheDocument();
    expect(window.location.hash).toBe("#/settings");
  });

  it("restores the same canonical person-bound chat after restart", async () => {
    window.location.hash = "";
    api.getSessions.mockResolvedValue({
      sessions: [
        {
          session_id: "thread-alpha",
          title: "Sourcing · Alyssa",
          n_msgs: 0,
          pinned: false,
          opened_at: "2026-08-27T10:00:00Z",
          updated_at: "2026-08-27T10:00:00Z",
        },
      ],
      open_id: "thread-alpha",
      last_destination: "#/chat/thread-alpha/person/person%20one",
    });
    api.getSession.mockResolvedValue({
      id: "thread-alpha",
      title: "Sourcing · Alyssa",
      messages: [],
      events: [],
      active_person: { person_id: "person one", version: 2, label: "Alyssa Lee" },
    });

    render(<App />);

    await waitFor(() =>
      expect(api.getSession).toHaveBeenCalledWith("thread-alpha", "person one"),
    );
    expect(window.location.hash).toBe(
      "#/chat/thread-alpha/person/person%20one",
    );
  });

  it("does not persist a cached destination before fresh session state resolves", async () => {
    window.location.hash = "";
    window.localStorage.setItem("sourcecado.shell.sessions.v1", JSON.stringify({
      sessions: [],
      open_id: null,
      last_destination: "#/scheduled",
    }));
    const listing = deferred<{
      sessions: never[];
      open_id: null;
      last_destination: string;
    }>();
    api.getSessions.mockReturnValue(listing.promise);

    render(<App />);

    await waitFor(() => expect(window.location.hash).toBe("#/scheduled"));
    expect(api.setLastDestination).not.toHaveBeenCalled();

    listing.resolve({ sessions: [], open_id: null, last_destination: "#/skills" });
    await waitFor(() => expect(window.location.hash).toBe("#/skills"));
  });

  it("keeps a stale destination read-only after refresh failure but persists later navigation", async () => {
    window.location.hash = "";
    window.localStorage.setItem("sourcecado.shell.sessions.v1", JSON.stringify({
      sessions: [],
      open_id: null,
      last_destination: "#/scheduled",
    }));
    api.getSessions.mockRejectedValue(new Error("sidecar offline"));

    render(<App />);

    expect(await screen.findByRole("heading", { level: 1, name: "Scheduled" })).toBeInTheDocument();
    expect(api.setLastDestination).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("link", { name: "Skills" }));
    await waitFor(() => expect(api.setLastDestination).toHaveBeenCalledWith("#/skills"));
  });

  it("escapes the boot skeleton when sessions exist but no restore target is known", async () => {
    window.location.hash = "";
    api.getSessions.mockResolvedValue({
      sessions: [
        { session_id: "alpha", title: "Alpha", n_msgs: 2, pinned: false, opened_at: "1", updated_at: "1" },
      ],
      open_id: null,
      last_destination: null,
    });

    render(<App />);

    await waitFor(() => expect(window.location.hash).not.toBe(""));
    expect(screen.queryByLabelText("Restoring workspace")).not.toBeInTheDocument();
  });

  it("persists a navigated destination for the next launch", async () => {
    render(<App />);

    fireEvent.click(screen.getByRole("link", { name: "Scheduled" }));

    await waitFor(() => expect(api.setLastDestination).toHaveBeenCalledWith("#/scheduled"));
  });

  it("separates pinned threads from API-ordered recent threads", async () => {
    api.getSessions.mockResolvedValue({
      sessions: [
        { session_id: "alpha", title: "Alpha", n_msgs: 2, pinned: true, opened_at: "3", updated_at: "3" },
        { session_id: "beta", title: "Beta", n_msgs: 1, pinned: false, opened_at: "2", updated_at: "2" },
        { session_id: "gamma", title: "Gamma", n_msgs: 1, pinned: false, opened_at: "1", updated_at: "1" },
      ],
      open_id: "beta",
      last_destination: "#/skills",
    });

    render(<App />);

    const pinned = await screen.findByRole("group", { name: "Pinned threads" });
    const recent = screen.getByRole("group", { name: "Recent threads" });
    expect(within(pinned).getAllByRole("link").map((link) => link.textContent)).toEqual(["Alpha"]);
    expect(within(recent).getAllByRole("link").map((link) => link.textContent)).toEqual(["Beta", "Gamma"]);
  });

  it("creates a new chat and navigates to its durable hash", async () => {
    api.createSession.mockResolvedValue({ id: "fresh-thread", title: null, n_msgs: 0 });

    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "New chat" }));

    await waitFor(() => expect(api.createSession).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(window.location.hash).toBe("#/chat/fresh-thread"));
  });

  it("pins a thread from a labeled keyboard-accessible control", async () => {
    api.getSessions.mockResolvedValue({
      sessions: [
        { session_id: "alpha", title: "Alpha", n_msgs: 2, pinned: false, opened_at: "1", updated_at: "1" },
      ],
      open_id: "alpha",
      last_destination: "#/skills",
    });

    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "Pin Alpha" }));

    await waitFor(() => expect(api.pinSession).toHaveBeenCalledWith("alpha", true));
    expect(within(screen.getByRole("group", { name: "Pinned threads" })).getByRole("link", { name: "Alpha" })).toBeInTheDocument();
  });

  it("renames a thread through an inline keyboard-accessible editor", async () => {
    api.getSessions.mockResolvedValue({
      sessions: [
        { session_id: "alpha", title: "Alpha", n_msgs: 2, pinned: false, opened_at: "1", updated_at: "1" },
      ],
      open_id: "alpha",
      last_destination: "#/skills",
    });

    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "Rename Alpha" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Conversation title" }), {
      target: { value: "Renamed Alpha" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save Alpha" }));

    await waitFor(() => expect(api.renameSession).toHaveBeenCalledWith("alpha", "Renamed Alpha"));
    expect(await screen.findByRole("link", { name: "Renamed Alpha" })).toBeInTheDocument();
  });

  it("opens the labeled command search with Cmd-K and navigates to a destination", async () => {
    render(<App />);

    fireEvent.keyDown(window, { key: "k", metaKey: true });
    const dialog = await screen.findByRole("dialog", { name: "Search Sourcecado" });
    expect(within(dialog).getByRole("searchbox", { name: "Search destinations and conversations" })).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "Connections" }));

    expect(await screen.findByRole("heading", { level: 1, name: "Connections" })).toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "Search Sourcecado" })).not.toBeInTheDocument();
  });

  it("navigates to Board from command search", async () => {
    render(<App />);

    fireEvent.keyDown(window, { key: "k", metaKey: true });
    const dialog = await screen.findByRole("dialog", { name: "Search Sourcecado" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Board" }));

    expect(await screen.findByRole("heading", { level: 1, name: "Board" })).toBeInTheDocument();
    expect(window.location.hash).toBe("#/board");
  });

  it("returns focus to the command-search trigger when Escape closes it", async () => {
    render(<App />);

    const trigger = screen.getByRole("button", {
      name: "Search conversations and destinations",
    });
    trigger.focus();
    fireEvent.click(trigger);
    const dialog = await screen.findByRole("dialog", { name: "Search Sourcecado" });
    expect(
      within(dialog).getByRole("searchbox", {
        name: "Search destinations and conversations",
      }),
    ).toHaveFocus();

    fireEvent.keyDown(dialog, { key: "Escape" });
    await waitFor(() =>
      expect(
        screen.queryByRole("dialog", { name: "Search Sourcecado" }),
      ).not.toBeInTheDocument(),
    );
    await waitFor(() => expect(trigger).toHaveFocus());
  });

  it("shows a first-run welcome with chat and connection recovery paths", async () => {
    window.location.hash = "";

    render(<App />);

    expect(await screen.findByRole("heading", { level: 1, name: "Welcome to Sourcecado" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start a chat" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open Connections" })).toHaveAttribute("href", "#/connections");
    expect(document.querySelector(".welcome-page")).toBeInTheDocument();
    expect(document.querySelector("img.app-mark")).toHaveAttribute("src", "/favicon.png");
  });

  it("keeps navigation usable through boot failure and retry", async () => {
    api.getSessions
      .mockRejectedValueOnce(new Error("sidecar offline"))
      .mockResolvedValueOnce({ sessions: [], open_id: null, last_destination: "#/skills" });

    render(<App />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("sidecar offline");
    expect(screen.getByRole("navigation", { name: "Sourcecado" })).toBeInTheDocument();
    fireEvent.click(within(alert).getByRole("button", { name: "Retry" }));

    await waitFor(() => expect(api.getSessions).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
  });

  it("renders cached thread navigation as stale when reconnect fails", async () => {
    window.localStorage.setItem("sourcecado.shell.sessions.v1", JSON.stringify({
      sessions: [
        { session_id: "cached", title: "Cached search", n_msgs: 3, pinned: false, opened_at: "1", updated_at: "1" },
      ],
      open_id: "cached",
      last_destination: "#/skills",
    }));
    api.getSessions.mockRejectedValue(new Error("offline"));

    render(<App />);

    expect(await screen.findByRole("link", { name: "Cached search" })).toBeInTheDocument();
    expect(screen.getByText(/Cached conversations may be stale/i)).toBeInTheDocument();
  });

  it("renders a recoverable one-line state for a missing thread", async () => {
    window.location.hash = "#/chat/missing";
    api.getSessions.mockResolvedValue({
      sessions: [
        { session_id: "alpha", title: "Alpha", n_msgs: 2, pinned: false, opened_at: "1", updated_at: "1" },
      ],
      open_id: "alpha",
      last_destination: "#/chat/alpha",
    });
    api.getSession.mockRejectedValue(new Error("session 404"));

    render(<App />);

    expect(await screen.findByText("This conversation is unavailable.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open most recent conversation" })).toHaveAttribute("href", "#/chat/alpha");
  });

  it("moves an opened thread to the front of recent navigation", async () => {
    api.getSessions.mockResolvedValue({
      sessions: [
        { session_id: "beta", title: "Beta", n_msgs: 1, pinned: false, opened_at: "2", updated_at: "2" },
        { session_id: "alpha", title: "Alpha", n_msgs: 2, pinned: false, opened_at: "1", updated_at: "1" },
      ],
      open_id: "beta",
      last_destination: "#/skills",
    });

    render(<App />);
    const recent = await screen.findByRole("group", { name: "Recent threads" });
    fireEvent.click(within(recent).getByRole("link", { name: "Alpha" }));

    await waitFor(() => expect(window.location.hash).toBe("#/chat/alpha"));
    expect(within(screen.getByRole("group", { name: "Recent threads" })).getAllByRole("link").map((link) => link.textContent)).toEqual(["Alpha", "Beta"]);
  });

  it("loads the next transcript when navigating between chat hashes", async () => {
    window.location.hash = "#/chat/alpha";
    api.getSessions.mockResolvedValue({
      sessions: [
        { session_id: "alpha", title: "Alpha", n_msgs: 2, pinned: false, opened_at: "2", updated_at: "2" },
        { session_id: "beta", title: "Beta", n_msgs: 1, pinned: false, opened_at: "1", updated_at: "1" },
      ],
      open_id: "alpha",
      last_destination: "#/chat/alpha",
    });
    api.getSession.mockImplementation(async (id: string) => ({ id, title: id, messages: [] }));

    render(<App />);
    await waitFor(() => expect(api.getSession).toHaveBeenCalledWith("alpha"));
    fireEvent.click((await screen.findAllByRole("link", { name: "Beta" }))[0]);

    await waitFor(() => expect(api.getSession).toHaveBeenCalledWith("beta"));
  });

  it("keeps the chat route inside the single unified app rail", async () => {
    window.location.hash = "#/chat/alpha";
    api.getSessions.mockResolvedValue({
      sessions: [
        { session_id: "alpha", title: "Alpha", n_msgs: 2, pinned: false, opened_at: "1", updated_at: "1" },
      ],
      open_id: "alpha",
      last_destination: "#/chat/alpha",
    });
    api.getSession.mockResolvedValue({ id: "alpha", title: "Alpha", messages: [] });

    render(<App />);
    await waitFor(() => expect(api.getSession).toHaveBeenCalledWith("alpha"));

    expect(screen.getAllByRole("navigation")).toHaveLength(1);
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
  });

  it("opens the edge-sheet rail with focused close control and closes on Escape", async () => {
    render(<App />);

    const trigger = screen.getByRole("button", { name: "Open navigation" });
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(await screen.findByRole("button", { name: "Close navigation" })).toHaveFocus();

    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(trigger).toHaveAttribute("aria-expanded", "false"));
  });
});
