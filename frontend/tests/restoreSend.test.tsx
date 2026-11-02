import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
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

type Stored = { role: "user" | "assistant"; content: string };

/**
 * A stand-in for the backend's stored transcript. The response to the restore
 * request waits, as on a slow network, until the test lets it through.
 * `readWhen` says when the backend reads its transcript for that response:
 * "asked" is at once, "answered" is just before it goes out.
 */
function slowBackend(stored: Stored[], readWhen: "asked" | "answered" = "asked") {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const events: Record<string, unknown>[] = [];
  const read = () => ({
    id: "thread-alpha",
    title: "Restore race",
    queue: [],
    queue_paused: false,
    messages: stored.map((message) => ({ ...message })),
    events: [...events],
  });
  api.getSession.mockImplementation(() => {
    if (readWhen === "answered") return gate.then(read);
    const snapshot = read();
    return gate.then(() => snapshot);
  });
  // The backend stores a message the moment it is sent.
  chatSend.mockImplementation((text: string) => {
    stored.push({ role: "user", content: text });
  });
  return { release: () => act(async () => release()), events };
}

const envelope = (turn: number) =>
  ({
    version: 2,
    session_id: "thread-alpha",
    run_id: `run-${turn}`,
    message_id: `message-${turn}`,
    part_id: `part-${turn}`,
  }) as const;

/** A whole answer arriving on the socket. */
function liveAnswer(turn: number, answer: string) {
  act(() => {
    onChatEvent?.({ ...envelope(turn), event_id: `live-${turn}-0`, type: "turn_start", state: "running" });
    onChatEvent?.({ ...envelope(turn), event_id: `live-${turn}-1`, type: "assistant_delta", delta: answer });
    onChatEvent?.({ ...envelope(turn), event_id: `live-${turn}-2`, type: "turn_end", state: "complete", text: answer });
  });
}

const composerBox = () =>
  screen.getByRole("textbox", { name: "Message Sourcecado" }) as HTMLTextAreaElement;

function sendFromComposer(text: string) {
  fireEvent.change(composerBox(), { target: { value: text } });
  fireEvent.click(screen.getByRole("button", { name: "Send message" }));
}

/** What the operator sees in the transcript, top to bottom. */
function bubbles() {
  const log = screen.getByRole("log", { name: "Conversation" });
  return Array.from(log.querySelectorAll<HTMLElement>("[data-message-id]")).map((element) => ({
    role: element.classList.contains("sourcecado-user-message") ? "user" : "assistant",
    text: element.textContent ?? "",
  }));
}

const userBubbles = () =>
  bubbles()
    .filter((bubble) => bubble.role === "user")
    .map((bubble) => bubble.text);

describe("A message sent while the thread restores", () => {
  beforeEach(() => {
    window.localStorage.clear();
    chatSend.mockReset();
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
        queue: vi.fn(),
        recover: vi.fn(),
        close: vi.fn(),
      };
    });
  });

  it("keeps its bubble above the answer, and the bubbles match what the backend stored", async () => {
    const stored: Stored[] = [
      { role: "user", content: "First question" },
      { role: "assistant", content: "First answer." },
    ];
    const backend = slowBackend(stored);
    render(<ChatPage sessionId="thread-alpha" />);

    // The restore was answered at once, but its response is still on the way.
    sendFromComposer("Second question");
    await waitFor(() => expect(chatSend).toHaveBeenCalledWith("Second question", "thread-alpha"));
    liveAnswer(1, "Second answer.");
    await backend.release();

    expect(await screen.findByText("Second answer.")).toBeInTheDocument();
    const storedUsers = stored.filter((message) => message.role === "user");
    expect(storedUsers.map((message) => message.content)).toEqual([
      "First question",
      "Second question",
    ]);
    expect(userBubbles()).toEqual(storedUsers.map((message) => message.content));
    expect(bubbles().map((bubble) => bubble.role)).toEqual([
      "user",
      "assistant",
      "user",
      "assistant",
    ]);
  });

  it("shows the message once when the backend stored it before it answered the restore", async () => {
    const stored: Stored[] = [];
    const backend = slowBackend(stored, "answered");
    render(<ChatPage sessionId="thread-alpha" />);

    sendFromComposer("Find ML founders");
    await waitFor(() => expect(chatSend).toHaveBeenCalledTimes(1));
    // The run starts on the socket and in the event log the restore reads.
    const started = {
      ...envelope(1),
      event_id: "live-1-0",
      type: "turn_start",
      state: "running",
    };
    backend.events.push(started);
    act(() => {
      onChatEvent?.(started);
    });
    await backend.release();

    await screen.findByRole("heading", { level: 1, name: "Restore race" });
    expect(stored).toHaveLength(1);
    expect(userBubbles()).toEqual(["Find ML founders"]);
  });

  it("sends the gap message again on Retry, not an earlier one", async () => {
    const stored: Stored[] = [
      { role: "user", content: "First question" },
      { role: "assistant", content: "First answer." },
    ];
    const backend = slowBackend(stored);
    render(<ChatPage sessionId="thread-alpha" />);

    sendFromComposer("Second question");
    await waitFor(() => expect(chatSend).toHaveBeenCalledTimes(1));
    liveAnswer(1, "Second answer.");
    await backend.release();
    await screen.findByText("Second answer.");

    chatSend.mockClear();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    await waitFor(() =>
      expect(chatSend).toHaveBeenCalledWith("Second question", "thread-alpha"),
    );
    expect(chatSend).toHaveBeenCalledTimes(1);
  });
});
