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

vi.mock("../src/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../src/api")>()),
  ...(await import("./taskApi")).taskApiMock(),
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

  it("renders Contacts as a direct durable rail destination", async () => {
    window.location.hash = "#/board";

    render(<App />);

    expect(await screen.findByRole("heading", { level: 1, name: "Contacts" })).toBeInTheDocument();
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByRole("link", { name: "Contacts" })).toHaveAttribute("aria-current", "page");
  });

  it("renders a decoded person file under the active Contacts destination", async () => {
    window.location.hash = "#/people/person%20one";

    render(<App />);

    expect(await screen.findByRole("heading", { level: 1, name: "Alyssa" })).toBeInTheDocument();
    expect(api.getPerson).toHaveBeenCalledWith("person one");
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByRole("link", { name: "Contacts" })).toHaveAttribute("aria-current", "page");
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

    expect(
      await screen.findByRole("heading", { level: 1, name: "Scheduled tasks" }),
    ).toBeInTheDocument();
  });

  it("restores a direct Scheduled task detail route", async () => {
    window.location.hash = "#/scheduled/4";
    api.getSchedule.mockResolvedValue({
      jobs: [
        {
          id: 4,
          name: "Weekly priority review",
          templateId: "weekly_sourcing_review",
          cadence: "weekly_monday_0900",
          cron: "0 9 * * 1",
          prompt: "Review priority sourcing work.",
          createdAt: "2026-08-25T10:00:00Z",
          nextRunAt: "2026-08-31T09:00:00-07:00",
        },
      ],
      runs: [],
      templates: [],
    });

    render(<App />);

    expect(
      await screen.findByRole("heading", { level: 1, name: "Weekly priority review" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Breadcrumb" })).toBeInTheDocument();
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
    expect(within(nav).getByRole("link", { name: "Contacts" })).toBeInTheDocument();
    expect(within(nav).getByRole("link", { name: "Scheduled" })).toBeInTheDocument();
    expect(within(nav).getByRole("link", { name: "Connections" })).toBeInTheDocument();
    expect(within(nav).getByRole("link", { name: "Skills" })).toHaveAttribute("aria-current", "page");
    expect(within(nav).getByRole("link", { name: "Inbox" })).toHaveAttribute(
      "href",
      "#/quarantine",
    );
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

    expect(
      await screen.findByRole("heading", { level: 1, name: "Scheduled tasks" }),
    ).toBeInTheDocument();
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

  it("reconciles a cached person chat against fresh sessions before loading it", async () => {
    window.location.hash = "";
    window.localStorage.setItem("sourcecado.shell.sessions.v1", JSON.stringify({
      sessions: [
        {
          session_id: "missing",
          title: "Stale person chat",
          n_msgs: 2,
          pinned: false,
          opened_at: "1",
          updated_at: "1",
        },
      ],
      open_id: "missing",
      last_destination: "#/chat/missing/person/person-old",
    }));
    const listing = deferred<{
      sessions: Array<{
        session_id: string;
        title: string;
        n_msgs: number;
        pinned: boolean;
        opened_at: string;
        updated_at: string;
      }>;
      open_id: string;
      last_destination: string;
    }>();
    api.getSessions.mockReturnValue(listing.promise);
    api.getSession.mockImplementation(async (id: string) => {
      if (id === "missing") throw new Error("session 404");
      return { id, title: "Alpha", messages: [], events: [] };
    });

    render(<App />);

    await waitFor(() => expect(api.getSessions).toHaveBeenCalledTimes(1));
    expect(api.getSession).not.toHaveBeenCalled();

    listing.resolve({
      sessions: [
        {
          session_id: "alpha",
          title: "Alpha",
          n_msgs: 1,
          pinned: false,
          opened_at: "2",
          updated_at: "2",
        },
      ],
      open_id: "alpha",
      last_destination: "#/chat/missing/person/person-old",
    });

    await waitFor(() => expect(window.location.hash).toBe("#/chat/alpha"));
    await waitFor(() => expect(api.getSession).toHaveBeenCalledWith("alpha"));
    expect(api.getSession).not.toHaveBeenCalledWith("missing", "person-old");
    expect(screen.queryByText("We couldn’t load this conversation.")).not.toBeInTheDocument();
  });

  it("reconciles a webview-restored person chat hash before loading it", async () => {
    window.location.hash = "#/chat/missing/person/person-old";
    window.localStorage.setItem("sourcecado.shell.sessions.v1", JSON.stringify({
      sessions: [
        {
          session_id: "missing",
          title: "Stale person chat",
          n_msgs: 2,
          pinned: false,
          opened_at: "1",
          updated_at: "1",
        },
      ],
      open_id: "missing",
      last_destination: "#/chat/missing/person/person-old",
    }));
    const listing = deferred<{
      sessions: Array<{
        session_id: string;
        title: string;
        n_msgs: number;
        pinned: boolean;
        opened_at: string;
        updated_at: string;
      }>;
      open_id: string;
      last_destination: string;
    }>();
    api.getSessions.mockReturnValue(listing.promise);
    api.getSession.mockImplementation(async (id: string) => {
      if (id === "missing") throw new Error("session 404");
      return { id, title: "Alpha", messages: [], events: [] };
    });

    render(<App />);

    await waitFor(() => expect(api.getSessions).toHaveBeenCalledTimes(1));
    expect(api.getSession).not.toHaveBeenCalled();

    listing.resolve({
      sessions: [
        {
          session_id: "alpha",
          title: "Alpha",
          n_msgs: 1,
          pinned: false,
          opened_at: "2",
          updated_at: "2",
        },
      ],
      open_id: "alpha",
      last_destination: "#/chat/missing/person/person-old",
    });

    await waitFor(() => expect(window.location.hash).toBe("#/chat/alpha"));
    await waitFor(() => expect(api.getSession).toHaveBeenCalledWith("alpha"));
    expect(api.getSession).not.toHaveBeenCalledWith("missing", "person-old");
  });

  it("recovers a stale person-chat draft in a new unbound conversation", async () => {
    window.location.hash = "";
    window.localStorage.setItem("sourcecado.shell.sessions.v1", JSON.stringify({
      sessions: [
        {
          session_id: "missing",
          title: "Stale person chat",
          n_msgs: 2,
          pinned: false,
          opened_at: "1",
          updated_at: "1",
        },
      ],
      open_id: "missing",
      last_destination: "#/chat/missing/person/person-old",
    }));
    window.localStorage.setItem("sourcecado.chat.draft.v1:missing", "Unsent note");
    api.getSessions.mockResolvedValue({
      sessions: [
        {
          session_id: "alpha",
          title: "Another person",
          n_msgs: 1,
          pinned: false,
          opened_at: "2",
          updated_at: "2",
        },
      ],
      open_id: "alpha",
      last_destination: "#/chat/missing/person/person-old",
    });
    api.createSession.mockResolvedValue({ id: "recovered-draft", title: null, n_msgs: 0 });
    api.getSession.mockImplementation(async (id: string) => ({
      id,
      title: null,
      messages: [],
      events: [],
    }));

    render(<App />);

    await waitFor(() => expect(api.createSession).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(window.location.hash).toBe("#/chat/recovered-draft"));
    expect(await screen.findByLabelText("Message Sourcecado")).toHaveValue("Unsent note");
    expect(api.getSession).not.toHaveBeenCalledWith("alpha");
  });

  it("creates a usable empty conversation when no restored session survives", async () => {
    window.location.hash = "";
    window.localStorage.setItem("sourcecado.shell.sessions.v1", JSON.stringify({
      sessions: [
        {
          session_id: "missing",
          title: "Stale chat",
          n_msgs: 2,
          pinned: false,
          opened_at: "1",
          updated_at: "1",
        },
      ],
      open_id: "missing",
      last_destination: "#/chat/missing",
    }));
    api.getSessions.mockResolvedValue({
      sessions: [],
      open_id: null,
      last_destination: "#/chat/missing",
    });
    api.createSession.mockResolvedValue({ id: "new-chat", title: null, n_msgs: 0 });
    api.getSession.mockResolvedValue({
      id: "new-chat",
      title: null,
      messages: [],
      events: [],
    });

    render(<App />);

    await waitFor(() => expect(api.createSession).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(window.location.hash).toBe("#/chat/new-chat"));
    expect(await screen.findByLabelText("Message Sourcecado")).toBeEnabled();
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
    api.getSessions.mockRejectedValue(new Error("backend offline"));

    render(<App />);

    expect(
      await screen.findByRole("heading", { level: 1, name: "Scheduled tasks" }),
    ).toBeInTheDocument();
    expect(api.setLastDestination).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("link", { name: "Skills" }));
    await waitFor(
      () => expect(api.setLastDestination).toHaveBeenCalledWith("#/skills"),
      { timeout: 2_500 },
    );
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

  it("navigates to Contacts from command search", async () => {
    render(<App />);

    fireEvent.keyDown(window, { key: "k", metaKey: true });
    const dialog = await screen.findByRole("dialog", { name: "Search Sourcecado" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Contacts" }));

    expect(await screen.findByRole("heading", { level: 1, name: "Contacts" })).toBeInTheDocument();
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

  it("automatically retries a transient first-paint session-list failure", async () => {
    window.location.hash = "#/skills";
    api.getSessions
      .mockRejectedValueOnce(new Error("Load failed"))
      .mockResolvedValueOnce({
        sessions: [
          {
            session_id: "thread-alpha",
            title: "QA Full User Trail",
            n_msgs: 4,
            pinned: false,
            opened_at: "1",
            updated_at: "2",
          },
        ],
        open_id: "thread-alpha",
        last_destination: "#/skills",
      });

    render(<App />);

    await waitFor(() => expect(api.getSessions).toHaveBeenCalledTimes(2), {
      timeout: 2_000,
    });
    expect(await screen.findByRole("link", { name: "QA Full User Trail" })).toBeInTheDocument();
    expect(screen.queryByText(/Couldn’t refresh conversations/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Cached conversations may be stale/i)).not.toBeInTheDocument();
  });

  it("does not call cached conversations stale while a bounded retry is pending", async () => {
    window.location.hash = "#/skills";
    window.localStorage.setItem("sourcecado.shell.sessions.v1", JSON.stringify({
      sessions: [
        {
          session_id: "cached",
          title: "Cached conversation",
          n_msgs: 3,
          pinned: false,
          opened_at: "1",
          updated_at: "1",
        },
      ],
      open_id: "cached",
      last_destination: "#/skills",
    }));
    const listing = deferred<{
      sessions: Array<{
        session_id: string;
        title: string;
        n_msgs: number;
        pinned: boolean;
        opened_at: string;
        updated_at: string;
      }>;
      open_id: string;
      last_destination: string;
    }>();
    api.getSessions
      .mockRejectedValueOnce(new Error("Load failed"))
      .mockReturnValueOnce(listing.promise);

    render(<App />);

    await waitFor(() => expect(api.getSessions).toHaveBeenCalledTimes(2), {
      timeout: 2_000,
    });
    expect(screen.getByRole("link", { name: "Cached conversation" })).toBeInTheDocument();
    expect(screen.queryByText(/Couldn’t refresh conversations/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Cached conversations may be stale/i)).not.toBeInTheDocument();

    listing.resolve({
      sessions: [
        {
          session_id: "fresh",
          title: "Fresh conversation",
          n_msgs: 4,
          pinned: false,
          opened_at: "2",
          updated_at: "2",
        },
      ],
      open_id: "fresh",
      last_destination: "#/skills",
    });

    expect(await screen.findByRole("link", { name: "Fresh conversation" })).toBeInTheDocument();
    expect(screen.queryByText(/Cached conversations may be stale/i)).not.toBeInTheDocument();
  });

  it("keeps navigation usable after bounded boot retries are exhausted", async () => {
    api.getSessions.mockRejectedValue(new Error("backend offline"));

    render(<App />);

    const alert = await screen.findByRole("alert", undefined, { timeout: 2_500 });
    expect(alert).toHaveTextContent("backend offline");
    expect(api.getSessions).toHaveBeenCalledTimes(4);
    expect(screen.getByRole("navigation", { name: "Sourcecado" })).toBeInTheDocument();
    api.getSessions.mockResolvedValueOnce({
      sessions: [],
      open_id: null,
      last_destination: "#/skills",
    });
    fireEvent.click(within(alert).getByRole("button", { name: "Retry" }));

    await waitFor(() => expect(api.getSessions).toHaveBeenCalledTimes(5));
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
  });

  it("keeps stale person-chat recovery intact across exhausted retries", async () => {
    window.location.hash = "";
    window.localStorage.setItem("sourcecado.shell.sessions.v1", JSON.stringify({
      sessions: [
        {
          session_id: "missing",
          title: "Stale person chat",
          n_msgs: 2,
          pinned: false,
          opened_at: "1",
          updated_at: "1",
        },
      ],
      open_id: "missing",
      last_destination: "#/chat/missing/person/person-old",
    }));
    window.localStorage.setItem("sourcecado.chat.draft.v1:missing", "Unsent note");
    api.getSessions.mockRejectedValue(new Error("backend offline"));
    api.createSession.mockResolvedValue({
      id: "recovered-draft",
      title: null,
      n_msgs: 0,
    });
    api.getSession.mockResolvedValue({
      id: "recovered-draft",
      title: null,
      messages: [],
      events: [],
    });

    render(<App />);

    const alert = await screen.findByRole("alert", undefined, { timeout: 2_500 });
    expect(api.getSessions).toHaveBeenCalledTimes(4);
    api.getSessions.mockResolvedValueOnce({
      sessions: [
        {
          session_id: "alpha",
          title: "Alpha",
          n_msgs: 1,
          pinned: false,
          opened_at: "2",
          updated_at: "2",
        },
      ],
      open_id: "alpha",
      last_destination: "#/chat/missing/person/person-old",
    });
    await waitFor(() =>
      expect(api.getSession).toHaveBeenCalledWith("missing", "person-old"),
    );
    const staleLoadsBeforeRetry = api.getSession.mock.calls.filter(
      ([sessionId, personId]) => sessionId === "missing" && personId === "person-old",
    ).length;
    fireEvent.click(within(alert).getByRole("button", { name: "Retry" }));

    await waitFor(() => expect(api.createSession).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(window.location.hash).toBe("#/chat/recovered-draft"));
    expect(await screen.findByLabelText("Message Sourcecado")).toHaveValue("Unsent note");
    expect(
      api.getSession.mock.calls.filter(
        ([sessionId, personId]) => sessionId === "missing" && personId === "person-old",
      ),
    ).toHaveLength(staleLoadsBeforeRetry);
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
    expect(
      await screen.findByText(
        /Cached conversations may be stale/i,
        undefined,
        { timeout: 2_500 },
      ),
    ).toBeInTheDocument();
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

  it("does not blame the backend when a person-bound conversation is missing", async () => {
    window.location.hash = "#/chat/missing/person/person-old";
    api.getSessions.mockResolvedValue({
      sessions: [
        { session_id: "alpha", title: "Alpha", n_msgs: 2, pinned: false, opened_at: "1", updated_at: "1" },
      ],
      open_id: "alpha",
      last_destination: "#/chat/alpha",
    });
    api.getSession.mockRejectedValue(new Error("session 404"));

    render(<App />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Your draft is still here. Retry, or choose another conversation.");
    expect(alert).not.toHaveTextContent("backend");
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
  describe("rebuilt sidebar", () => {
    const threads = {
      sessions: [
        { session_id: "alpha", title: "Alpha", n_msgs: 2, pinned: true, opened_at: "2", updated_at: "2" },
        { session_id: "beta", title: "Beta", n_msgs: 1, pinned: false, opened_at: "1", updated_at: "1" },
      ],
      open_id: "beta",
      last_destination: "#/skills",
    };

    it("gives every destination row, the search row and New chat an icon", async () => {
      render(<App />);

      const nav = await screen.findByRole("navigation", { name: "Sourcecado" });
      for (const name of ["Contacts", "Scheduled", "Connections", "Skills", "Memory", "Inbox", "Settings"]) {
        expect(
          within(nav).getByRole("link", { name }).querySelector("svg.rail-icon[aria-hidden='true']"),
          name,
        ).not.toBeNull();
      }
      expect(within(nav).getByRole("button", { name: "New chat" }).querySelector("svg.rail-icon")).not.toBeNull();
      expect(within(nav).getByRole("button", { name: /Search/ }).querySelector("svg.rail-icon")).not.toBeNull();
    });

    it("shows a count badge on Inbox, Scheduled and Memory from the existing endpoints", async () => {
      api.getInbox.mockResolvedValue({
        items: [
          { id: "a1", state: "pending", session_id: "sched-4" },
          { id: "a2", state: "pending", session_id: "sched-7" },
          { id: "a3", state: "pending", session_id: "thread-alpha" },
        ],
      });
      api.getMemoryBacklog.mockResolvedValue({ needs_review: 5, classified: 0, items: [] });
      api.getQuarantinedEffects.mockResolvedValue([{ id: "e1" }, { id: "e2" }, { id: "e3" }]);

      render(<App />);

      const nav = await screen.findByRole("navigation", { name: "Sourcecado" });
      const badgeIn = async (name: string) => {
        const link = within(nav).getByRole("link", { name });
        await waitFor(() => expect(link.querySelector(".rail-inbox-badge")).not.toBeNull());
        return link.querySelector(".rail-inbox-badge")!.textContent;
      };
      expect(await badgeIn("Inbox")).toBe("3");
      expect(await badgeIn("Scheduled")).toBe("2");
      expect(await badgeIn("Memory")).toBe("5");
      expect(within(nav).getByRole("link", { name: "Contacts" }).querySelector(".rail-inbox-badge")).toBeNull();
    });

    it("shows no badge when nothing is pending", async () => {
      render(<App />);

      const nav = await screen.findByRole("navigation", { name: "Sourcecado" });
      await waitFor(() => expect(api.getMemoryBacklog).toHaveBeenCalled());
      expect(nav.querySelector(".rail-inbox-badge")).toBeNull();
    });

    it("offers rename and pin as icon buttons with no glyph characters", async () => {
      api.getSessions.mockResolvedValue(threads);

      render(<App />);

      const rename = await screen.findByRole("button", { name: "Rename Beta" });
      const pin = screen.getByRole("button", { name: "Pin Beta" });
      const unpin = screen.getByRole("button", { name: "Unpin Alpha" });
      for (const button of [rename, pin, unpin]) {
        expect(button.querySelector("svg.rail-icon[aria-hidden='true']")).not.toBeNull();
        expect(button.textContent).toBe("");
      }
      expect(pin.querySelector("svg")).not.toHaveAttribute("data-filled");
      expect(unpin.querySelector("svg")).toHaveAttribute("data-filled", "true");
      const rail = document.getElementById("app-rail")!;
      for (const glyph of ["✎", "●", "○", "☰", "×", "＋"]) {
        expect(rail.textContent).not.toContain(glyph);
      }
      expect(document.querySelector(".rail-trigger")!.textContent).toBe("");
    });

    it("collapses the rail to an icon column, remembers it, and expands again", async () => {
      api.getSessions.mockResolvedValue(threads);
      const first = render(<App />);

      fireEvent.click(await screen.findByRole("button", { name: "Collapse sidebar" }));

      const rail = document.getElementById("app-rail")!;
      expect(rail).toHaveClass("is-collapsed");
      expect(screen.getByRole("link", { name: "Contacts" })).toHaveAttribute("data-tooltip", "Contacts");
      expect(window.localStorage.getItem("sourcecado.shell.rail-collapsed.v1")).toBe("1");
      first.unmount();

      render(<App />);
      expect(document.getElementById("app-rail")).toHaveClass("is-collapsed");

      fireEvent.click(await screen.findByRole("button", { name: "Expand sidebar" }));
      expect(document.getElementById("app-rail")).not.toHaveClass("is-collapsed");
      expect(window.localStorage.getItem("sourcecado.shell.rail-collapsed.v1")).toBe("0");
    });

    it("starts expanded when the stored choice is unreadable", async () => {
      window.localStorage.setItem("sourcecado.shell.rail-collapsed.v1", "garbage");

      render(<App />);

      await screen.findByRole("navigation", { name: "Sourcecado" });
      expect(document.getElementById("app-rail")).not.toHaveClass("is-collapsed");
    });

    it("keeps the hamburger and focus trap working at narrow width with a collapsed choice stored", async () => {
      window.localStorage.setItem("sourcecado.shell.rail-collapsed.v1", "1");
      vi.stubGlobal("matchMedia", (query: string) => ({
        matches: query.includes("max-width"),
        media: query,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      }));
      try {
        render(<App />);

        const rail = document.getElementById("app-rail")!;
        expect(rail).toHaveAttribute("data-open", "false");
        expect(rail).toHaveAttribute("inert");
        expect(rail).not.toHaveClass("is-collapsed");
        const trigger = screen.getByRole("button", { name: "Open navigation" });
        fireEvent.click(trigger);
        const close = await screen.findByRole("button", { name: "Close navigation" });
        expect(close).toHaveFocus();
        expect(rail).not.toHaveAttribute("inert");
        const settings = within(rail).getByRole("link", { name: "Settings" });
        settings.focus();
        fireEvent.keyDown(settings, { key: "Tab" });
        expect(close).toHaveFocus();
        fireEvent.keyDown(window, { key: "Escape" });
        await waitFor(() => expect(trigger).toHaveFocus());
        expect(rail).toHaveAttribute("inert");
      } finally {
        vi.unstubAllGlobals();
      }
    });

    it("shows the connected account and the persona name from settings in the footer", async () => {
      api.getSettings.mockResolvedValue({
        persona: { id: "analyst", name: "Research Analyst" },
        model: "test",
        gmail: { connected: true, email: "dana@codeology.club" },
        apollo: { configured: false },
      });

      render(<App />);

      const footer = await screen.findByRole("group", { name: "Signed-in operator" });
      await waitFor(() => expect(footer).toHaveTextContent("Research Analyst"));
      expect(footer).toHaveTextContent("dana@codeology.club");
      expect(footer).not.toHaveTextContent("Sourcing Director");
    });

    it("falls back to the operator label and omits the persona when settings fail", async () => {
      api.getSettings.mockRejectedValue(new Error("settings 500"));

      render(<App />);

      const footer = await screen.findByRole("group", { name: "Signed-in operator" });
      await waitFor(() => expect(api.getSettings).toHaveBeenCalled());
      expect(footer).toHaveTextContent("Operator");
      expect(footer).not.toHaveTextContent("Sourcing Director");
      expect(footer.querySelector(".operator-persona")).toBeNull();
    });

    it("refreshes the footer persona when settings change", async () => {
      api.getSettings
        .mockResolvedValueOnce({
          persona: { id: "sourcing", name: "Sourcing Director" },
          model: "test",
          gmail: { connected: false, email: null },
          apollo: { configured: false },
        })
        .mockResolvedValue({
          persona: { id: "analyst", name: "Research Analyst" },
          model: "test",
          gmail: { connected: false, email: null },
          apollo: { configured: false },
        });

      render(<App />);
      const footer = await screen.findByRole("group", { name: "Signed-in operator" });
      await waitFor(() => expect(footer).toHaveTextContent("Sourcing Director"));

      window.dispatchEvent(new Event("sourcecado:settings-changed"));

      await waitFor(() => expect(footer).toHaveTextContent("Research Analyst"));
    });
  });
  describe("sidebar keyboard flow, badge cap and titles", () => {
    const alpha = {
      session_id: "alpha",
      title: "Alpha",
      n_msgs: 2,
      pinned: false,
      opened_at: "1",
      updated_at: "1",
    };

    function twoThreads(pinned = false) {
      api.getSessions.mockResolvedValue({
        sessions: [{ ...alpha, pinned }],
        open_id: "alpha",
        last_destination: "#/skills",
      });
      api.pinSession.mockResolvedValue({ id: "alpha", title: "Alpha", pinned: !pinned });
    }

    async function startRename(title = "Alpha") {
      const rename = await screen.findByRole("button", { name: `Rename ${title}` });
      rename.focus();
      fireEvent.click(rename);
      return screen.getByRole("textbox", { name: "Conversation title" });
    }

    it("returns focus to the rename button after Save", async () => {
      twoThreads();
      render(<App />);

      const input = await startRename();
      fireEvent.change(input, { target: { value: "Renamed Alpha" } });
      fireEvent.click(screen.getByRole("button", { name: "Save Alpha" }));

      const rename = await screen.findByRole("button", { name: "Rename Renamed Alpha" });
      await waitFor(() => expect(rename).toHaveFocus());
    });

    it("returns focus to the rename button after Cancel", async () => {
      twoThreads();
      render(<App />);

      await startRename();
      fireEvent.click(screen.getByRole("button", { name: "Cancel renaming Alpha" }));

      await waitFor(() =>
        expect(screen.getByRole("button", { name: "Rename Alpha" })).toHaveFocus(),
      );
      expect(api.renameSession).not.toHaveBeenCalled();
    });

    it("cancels the rename on Escape, discards the draft and returns focus", async () => {
      twoThreads();
      render(<App />);

      const input = await startRename();
      fireEvent.change(input, { target: { value: "Half typed" } });
      fireEvent.keyDown(input, { key: "Escape" });

      await waitFor(() =>
        expect(screen.getByRole("button", { name: "Rename Alpha" })).toHaveFocus(),
      );
      expect(screen.queryByRole("textbox", { name: "Conversation title" })).not.toBeInTheDocument();
      expect(api.renameSession).not.toHaveBeenCalled();
      fireEvent.click(screen.getByRole("button", { name: "Rename Alpha" }));
      expect(screen.getByRole("textbox", { name: "Conversation title" })).toHaveValue("Alpha");
    });

    it("keeps the open edge sheet open when Escape cancels a rename", async () => {
      twoThreads();
      vi.stubGlobal("matchMedia", (query: string) => ({
        matches: query.includes("max-width"),
        media: query,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      }));
      try {
        render(<App />);
        fireEvent.click(screen.getByRole("button", { name: "Open navigation" }));
        const input = await startRename();

        fireEvent.keyDown(input, { key: "Escape" });

        await waitFor(() =>
          expect(screen.getByRole("button", { name: "Rename Alpha" })).toHaveFocus(),
        );
        expect(document.getElementById("app-rail")).toHaveAttribute("data-open", "true");
      } finally {
        vi.unstubAllGlobals();
      }
    });

    it("moves focus to the pin button in the Pinned group after a keyboard pin", async () => {
      twoThreads(false);
      render(<App />);

      const pin = await screen.findByRole("button", { name: "Pin Alpha" });
      pin.focus();
      fireEvent.click(pin);

      const unpin = await screen.findByRole("button", { name: "Unpin Alpha" });
      expect(within(screen.getByRole("group", { name: "Pinned threads" })).getByRole("button", { name: "Unpin Alpha" })).toBe(unpin);
      await waitFor(() => expect(unpin).toHaveFocus());
    });

    it("moves focus to the pin button in the Recent group after a keyboard unpin", async () => {
      twoThreads(true);
      render(<App />);

      const unpin = await screen.findByRole("button", { name: "Unpin Alpha" });
      unpin.focus();
      fireEvent.click(unpin);

      const pin = await screen.findByRole("button", { name: "Pin Alpha" });
      expect(within(screen.getByRole("group", { name: "Recent threads" })).getByRole("button", { name: "Pin Alpha" })).toBe(pin);
      await waitFor(() => expect(pin).toHaveFocus());
    });

    it("leaves focus where the operator moved it while a pin was in flight", async () => {
      twoThreads(false);
      const pending = deferred<{ id: string; title: string; pinned: boolean }>();
      api.pinSession.mockReturnValue(pending.promise);
      render(<App />);

      const pin = await screen.findByRole("button", { name: "Pin Alpha" });
      pin.focus();
      fireEvent.click(pin);
      const search = screen.getByRole("button", { name: "Search conversations and destinations" });
      search.focus();
      pending.resolve({ id: "alpha", title: "Alpha", pinned: true });

      await screen.findByRole("button", { name: "Unpin Alpha" });
      expect(search).toHaveFocus();
    });

    it("caps a large badge at 99+ and keeps the exact count in the accessible name", async () => {
      api.getMemoryBacklog.mockResolvedValue({ needs_review: 1003, classified: 0, items: [] });
      api.getQuarantinedEffects.mockResolvedValue(Array.from({ length: 99 }, (_, i) => ({ id: `e${i}` })));

      render(<App />);

      const memory = await screen.findByRole("link", { name: "Memory" });
      await waitFor(() => expect(memory.querySelector(".rail-inbox-badge")).not.toBeNull());
      const badge = memory.querySelector(".rail-inbox-badge")!;
      expect(badge).toHaveTextContent(/^99\+$/);
      expect(badge).toHaveAttribute("aria-label", "Saved memory: 1003 waiting for review");
      expect(memory).toHaveAccessibleDescription("Saved memory: 1003 waiting for review");
      const inbox = screen.getByRole("link", { name: "Inbox" });
      await waitFor(() => expect(inbox.querySelector(".rail-inbox-badge")).not.toBeNull());
      expect(inbox.querySelector(".rail-inbox-badge")).toHaveTextContent(/^99$/);
    });

    it("keeps a long thread title in an ellipsis-capable span", async () => {
      const long = "Find every founder at a seed stage robotics company in the Bay Area";
      api.getSessions.mockResolvedValue({
        sessions: [{ ...alpha, title: long }],
        open_id: "alpha",
        last_destination: "#/skills",
      });

      render(<App />);

      const link = await screen.findByRole("link", { name: long });
      expect(link.querySelector("span.thread-title")).toHaveTextContent(long);
    });

    it("names an untitled thread in the rail when its run starts or ends", async () => {
      window.location.hash = "#/chat/fresh";
      api.getSessions
        .mockResolvedValueOnce({
          sessions: [{ ...alpha, session_id: "fresh", title: null, n_msgs: 0 }],
          open_id: "fresh",
          last_destination: "#/chat/fresh",
        })
        .mockResolvedValue({
          sessions: [{ ...alpha, session_id: "fresh", title: "Founders at Acme", n_msgs: 2 }],
          open_id: "fresh",
          last_destination: "#/chat/fresh",
        });
      api.getSession.mockResolvedValue({ id: "fresh", title: null, messages: [] });

      render(<App />);
      const rail = document.getElementById("app-rail")!;
      expect(await within(rail).findByRole("link", { name: "New conversation" })).toBeInTheDocument();

      window.dispatchEvent(new Event("sourcecado:sessions-changed"));

      expect(await within(rail).findByRole("link", { name: "Founders at Acme" })).toBeInTheDocument();
      expect(within(rail).queryByRole("link", { name: "New conversation" })).not.toBeInTheDocument();
    });

    it("never overwrites a title the rail already has when the list refreshes", async () => {
      api.getSessions.mockResolvedValue({
        sessions: [
          { ...alpha, title: "Local name" },
          { ...alpha, session_id: "fresh", title: null, n_msgs: 0 },
        ],
        open_id: "alpha",
        last_destination: "#/skills",
      });
      render(<App />);
      await screen.findByRole("link", { name: "Local name" });
      api.getSessions.mockResolvedValue({
        sessions: [
          { ...alpha, title: "Backend name" },
          { ...alpha, session_id: "fresh", title: "Named later", n_msgs: 2 },
        ],
        open_id: "alpha",
        last_destination: "#/skills",
      });

      window.dispatchEvent(new Event("sourcecado:sessions-changed"));

      expect(await screen.findByRole("link", { name: "Named later" })).toBeInTheDocument();
      expect(screen.getByRole("link", { name: "Local name" })).toBeInTheDocument();
      expect(screen.queryByRole("link", { name: "Backend name" })).not.toBeInTheDocument();
    });

    it("does not ask for the list again when every thread already has a title", async () => {
      twoThreads();
      render(<App />);
      await screen.findByRole("link", { name: "Alpha" });
      const before = api.getSessions.mock.calls.length;

      window.dispatchEvent(new Event("sourcecado:sessions-changed"));
      await new Promise((resolve) => setTimeout(resolve, 20));

      expect(api.getSessions.mock.calls.length).toBe(before);
    });
  });
});
