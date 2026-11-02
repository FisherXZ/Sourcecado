import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ChatPage } from "../src/routes/ChatPage";

const api = vi.hoisted(() => ({
  getHealth: vi.fn(),
  getSessions: vi.fn(),
  getPersona: vi.fn(),
  getSchedule: vi.fn(),
  getGmail: vi.fn(),
  getConnectors: vi.fn(),
  getSettings: vi.fn(),
  getInbox: vi.fn(),
  getSession: vi.fn(),
  getCurrentRunTelemetry: vi.fn(),
  openChat: vi.fn(),
}));
const chatSend = vi.fn();
const chatQueue = vi.fn();
let onChatEvent: ((event: Record<string, unknown>) => void) | undefined;

vi.mock("../src/api", () => ({
  connectCalendar: vi.fn(),
  connectDrive: vi.fn(),
  connectGmail: vi.fn(),
  connectGranola: vi.fn(),
  disconnectGmail: vi.fn(),
  getHealth: api.getHealth,
  getSessions: api.getSessions,
  getPersona: api.getPersona,
  getSchedule: api.getSchedule,
  getGmail: api.getGmail,
  getConnectors: api.getConnectors,
  getSettings: api.getSettings,
  getInbox: api.getInbox,
  getSession: api.getSession,
  getCurrentRunTelemetry: api.getCurrentRunTelemetry,
  hasToken: () => true,
  openChat: api.openChat,
  resolveInbox: vi.fn(),
  runScheduleJob: vi.fn(),
  setPersona: vi.fn(),
}));

const envelope = (turn: number) =>
  ({
    version: 2,
    session_id: "thread-alpha",
    run_id: `run-${turn}`,
    message_id: `message-${turn}`,
    part_id: `part-${turn}`,
  }) as const;

const emit = (...events: Record<string, unknown>[]) =>
  act(() => {
    for (const event of events) onChatEvent?.(event);
  });

const start = (turn: number) => ({
  ...envelope(turn),
  event_id: `live-${turn}-0`,
  type: "turn_start",
  state: "running",
});
const finish = (turn: number, answer: string) => [
  { ...envelope(turn), event_id: `live-${turn}-1`, type: "assistant_delta", delta: answer },
  { ...envelope(turn), event_id: `live-${turn}-2`, type: "turn_end", state: "complete", text: answer },
];

/** The backend's queue snapshot, as it sends one to every socket. */
function queueSnapshot(status: string, items: { id: string; text: string; state: string }[]) {
  return {
    version: 2,
    type: "queue_snapshot",
    session_id: "thread-alpha",
    command_id: `command-${status}`,
    status,
    paused: false,
    items: items.map((item, position) => ({
      session_id: "thread-alpha",
      position,
      error: null,
      created_at: "2026-10-27T12:00:00Z",
      updated_at: "2026-10-27T12:00:00Z",
      ...item,
    })),
  };
}

const composerBox = () =>
  screen.getByRole("textbox", { name: "Message Sourcecado" }) as HTMLTextAreaElement;

function type(text: string) {
  fireEvent.change(composerBox(), { target: { value: text } });
}

/** What the operator sees in the transcript, top to bottom. */
function bubbles() {
  const log = screen.getByRole("log", { name: "Conversation" });
  return Array.from(log.querySelectorAll<HTMLElement>("[data-message-id]")).map((element) => ({
    role: element.classList.contains("sourcecado-user-message") ? "user" : "assistant",
    text: (element.textContent ?? "").replace(/\s+/g, " ").trim(),
  }));
}

const strip = () => screen.queryByRole("region", { name: "Queued messages" });

function openWithOneExchange() {
  api.getSession.mockResolvedValue({
    id: "thread-alpha",
    title: "Queue",
    queue: [],
    queue_paused: false,
    messages: [
      { role: "user", content: "Earlier question" },
      { role: "assistant", content: "Earlier answer." },
    ],
    events: [],
  });
  return render(<ChatPage sessionId="thread-alpha" />);
}

describe("A queued message", () => {
  beforeEach(() => {
    window.localStorage.clear();
    chatSend.mockReset();
    chatQueue.mockReset();
    onChatEvent = undefined;
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
      configurable: true,
      value: vi.fn(),
    });
    api.getHealth.mockResolvedValue({ status: "ok", piece: "desktop", slice: 1, model: "m" });
    api.getSessions.mockResolvedValue({ sessions: [], open_id: "thread-alpha" });
    api.getPersona.mockResolvedValue({ id: "sourcing", name: "Sourcing Operator", tools: [] });
    api.getSchedule.mockResolvedValue({ jobs: [], runs: [] });
    api.getGmail.mockResolvedValue({ connected: false, email: null });
    api.getConnectors.mockResolvedValue({ connectors: [] });
    api.getSettings.mockResolvedValue({
      persona: { id: "sourcing", name: "Sourcing Operator" },
      model: "m",
      gmail: { connected: false, email: null },
      apollo: { configured: false },
    });
    api.getInbox.mockResolvedValue({ items: [] });
    api.getCurrentRunTelemetry.mockResolvedValue({
      version: 1,
      session_id: "thread-alpha",
      current_run: null,
    });
    api.openChat.mockImplementation((callback) => {
      onChatEvent = callback;
      return {
        send: chatSend,
        approve: vi.fn(),
        cancel: vi.fn(),
        queue: chatQueue,
        recover: vi.fn(),
        close: vi.fn(),
      };
    });
  });

  /** Alpha runs. Beta is queued behind it. Alpha ends and the queue drains Beta. */
  async function queueBehindARun() {
    openWithOneExchange();
    await screen.findByText("Earlier answer.");
    type("Alpha");
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    await waitFor(() => expect(chatSend).toHaveBeenCalledWith("Alpha", "thread-alpha"));
    emit(start(1));
    type("Beta queued");
    fireEvent.keyDown(composerBox(), { key: "Enter", code: "Enter" });
    await waitFor(() => expect(chatQueue).toHaveBeenCalledOnce());
    const itemId = chatQueue.mock.calls[0]?.[0].item_id as string;
    emit(queueSnapshot("accepted", [{ id: itemId, text: "Beta queued", state: "waiting" }]));
    return itemId;
  }

  it("shows its bubble above its answer when its run starts, without a reload", async () => {
    const itemId = await queueBehindARun();
    expect(within(strip()!).getByText("Beta queued")).toBeInTheDocument();

    // The backend ends Alpha, then takes Beta from the queue and starts its run.
    emit(...finish(1, "Alpha answer."));
    emit(
      queueSnapshot("draining", [{ id: itemId, text: "Beta queued", state: "sending" }]),
      start(2),
      ...finish(2, "Beta answer."),
      queueSnapshot("idle", []),
    );

    await screen.findByText("Beta answer.");
    expect(bubbles()).toEqual([
      { role: "user", text: "Earlier question" },
      { role: "assistant", text: expect.stringContaining("Earlier answer.") },
      { role: "user", text: "Alpha" },
      { role: "assistant", text: expect.stringContaining("Alpha answer.") },
      { role: "user", text: "Beta queued" },
      { role: "assistant", text: expect.stringContaining("Beta answer.") },
    ]);
    expect(strip()).toBeNull();
  });

  it("is sent again by Retry on its answer, not an earlier message", async () => {
    const itemId = await queueBehindARun();
    emit(...finish(1, "Alpha answer."));
    emit(
      queueSnapshot("draining", [{ id: itemId, text: "Beta queued", state: "sending" }]),
      start(2),
      ...finish(2, "Beta answer."),
      queueSnapshot("idle", []),
    );
    await screen.findByText("Beta answer.");
    chatSend.mockClear();

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    await waitFor(() => expect(chatSend).toHaveBeenCalledWith("Beta queued", "thread-alpha"));
    expect(chatSend).toHaveBeenCalledTimes(1);
  });

  it("stays in the queue strip when it was queued while the thread restored", async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    // The backend answers the restore at once, with the queue as it was then.
    const snapshot = {
      id: "thread-alpha",
      title: "Queue",
      queue: [],
      queue_paused: false,
      messages: [
        { role: "user", content: "Earlier question" },
        { role: "assistant", content: "Earlier answer." },
      ],
      events: [],
    };
    api.getSession.mockImplementation(() => gate.then(() => snapshot));
    render(<ChatPage sessionId="thread-alpha" />);

    emit(queueSnapshot("accepted", [{ id: "queue-beta", text: "Beta queued", state: "waiting" }]));
    await act(async () => release());

    await screen.findByText("Earlier answer.");
    expect(within(strip()!).getByText("Beta queued")).toBeInTheDocument();

    // It drains: the strip empties and the bubble shows.
    emit(
      queueSnapshot("draining", [{ id: "queue-beta", text: "Beta queued", state: "sending" }]),
      start(1),
      ...finish(1, "Beta answer."),
      queueSnapshot("idle", []),
    );
    await screen.findByText("Beta answer.");
    expect(strip()).toBeNull();
    expect(bubbles().filter((bubble) => bubble.role === "user").map((bubble) => bubble.text)).toEqual([
      "Earlier question",
      "Beta queued",
    ]);
  });

  it("uses the restore's queue when no queue snapshot came while it was in flight", async () => {
    api.getSession.mockResolvedValue({
      id: "thread-alpha",
      title: "Queue",
      queue: [
        {
          id: "queue-restored",
          session_id: "thread-alpha",
          text: "Restored item",
          position: 0,
          state: "waiting",
          error: null,
          created_at: "2026-10-27T12:00:00Z",
          updated_at: "2026-10-27T12:00:00Z",
        },
      ],
      queue_paused: false,
      messages: [],
      events: [],
    });
    render(<ChatPage sessionId="thread-alpha" />);

    expect(await screen.findByText("Restored item")).toBeInTheDocument();
  });
});
