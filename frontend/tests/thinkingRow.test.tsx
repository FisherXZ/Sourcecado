import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ChatPage } from "../src/routes/ChatPage";
import { styles } from "./cssBundle";

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
const writeText = vi.fn();
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

const envelope = {
  version: 2,
  session_id: "thread-alpha",
  run_id: "run-think",
  message_id: "message-think",
  part_id: "part-think",
} as const;

const at = (seconds: number) =>
  new Date(Date.parse("2026-10-26T12:00:00.000Z") + seconds * 1000).toISOString();

const THOUGHT = "The director wants ML founders in Berkeley.\nI will search Apollo first.";

function conversation(events: Record<string, unknown>[], messages: unknown[] = []) {
  return {
    id: "thread-alpha",
    title: "Thinking row",
    messages,
    queue: [],
    queue_paused: false,
    events,
  };
}

const restored = (events: Record<string, unknown>[]) =>
  events.map((event, index) => ({
    ...envelope,
    event_id: `restored-${index}`,
    ...event,
  }));

describe("Thinking row", () => {
  beforeEach(() => {
    window.localStorage.clear();
    writeText.mockReset();
    onChatEvent = undefined;
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
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
        send: vi.fn(),
        approve: vi.fn(),
        cancel: vi.fn(),
        queue: vi.fn(),
        recover: vi.fn(),
        close: vi.fn(),
      };
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  async function openLive() {
    api.getSession.mockResolvedValue(conversation([]));
    const view = render(<ChatPage sessionId="thread-alpha" />);
    await waitFor(() => expect(onChatEvent).toBeDefined());
    const emit = (event: Record<string, unknown>) =>
      act(() => onChatEvent?.({ ...envelope, ...event }));
    return { ...view, emit };
  }

  const thinkingToggle = (container: HTMLElement) =>
    container.querySelector<HTMLButtonElement>(".sourcecado-thinking-toggle");

  it("shows a thinking row with a live count while the model reasons, and streams the text under it", async () => {
    const now = vi.spyOn(Date, "now");
    now.mockReturnValue(Date.parse(at(0)));
    const { container, emit } = await openLive();

    emit({ type: "turn_start", event_id: "t-1", state: "running" });
    // The row is not there before there is anything to show.
    expect(thinkingToggle(container)).toBeNull();

    emit({
      type: "reasoning_delta",
      event_id: "t-2",
      step: 1,
      at: at(0),
      delta: "The director wants ML founders",
    });
    const toggle = await waitFor(() => {
      const found = thinkingToggle(container);
      expect(found).not.toBeNull();
      return found!;
    });
    expect(toggle).toHaveTextContent("Thinking…");
    expect(toggle).toHaveTextContent("0s");
    expect(toggle).toHaveTextContent("Model reasoning");
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(
      screen.getByRole("group", { name: "Model reasoning" }),
    ).toHaveTextContent("The director wants ML founders");

    now.mockReturnValue(Date.parse(at(4)));
    emit({
      type: "reasoning_delta",
      event_id: "t-3",
      step: 1,
      at: at(4),
      delta: " in Berkeley.",
    });
    expect(
      screen.getByRole("group", { name: "Model reasoning" }),
    ).toHaveTextContent("The director wants ML founders in Berkeley.");
    expect(toggle).toHaveTextContent("4s");
  });

  it("collapses to Thought for Ns with the first line when the answer starts, and a click reopens it", async () => {
    vi.spyOn(Date, "now").mockReturnValue(Date.parse(at(12)));
    const { container, emit } = await openLive();
    emit({ type: "turn_start", event_id: "c-1", state: "running" });
    emit({ type: "reasoning_delta", event_id: "c-2", step: 1, at: at(0), delta: THOUGHT });
    emit({ type: "reasoning_delta", event_id: "c-3", step: 1, at: at(12), delta: "\nDone." });
    emit({ type: "assistant_delta", event_id: "c-4", delta: "Found two founders." });

    await screen.findByText("Found two founders.");
    const toggle = thinkingToggle(container)!;
    expect(toggle).toHaveTextContent("Thought for 12s");
    expect(toggle).toHaveTextContent("The director wants ML founders in Berkeley.");
    expect(toggle).not.toHaveTextContent("I will search Apollo first.");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    // A closed row controls nothing, so it names no id.
    expect(toggle).not.toHaveAttribute("aria-controls");
    expect(screen.queryByRole("group", { name: "Model reasoning" })).toBeNull();

    fireEvent.click(toggle);
    const body = screen.getByRole("group", { name: "Model reasoning" });
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(toggle.getAttribute("aria-controls")).toBe(body.id);
    expect(body).toHaveTextContent("I will search Apollo first.");
    expect(toggle).not.toHaveTextContent("The director wants ML founders in Berkeley.");
  });

  it("closes on Escape and returns focus to the row", async () => {
    vi.spyOn(Date, "now").mockReturnValue(Date.parse(at(3)));
    const { container, emit } = await openLive();
    emit({ type: "turn_start", event_id: "e-1", state: "running" });
    emit({ type: "reasoning_delta", event_id: "e-2", step: 1, at: at(0), delta: THOUGHT });
    emit({ type: "assistant_delta", event_id: "e-3", delta: "Answer." });
    await screen.findByText("Answer.");
    const toggle = thinkingToggle(container)!;
    fireEvent.click(toggle);

    const body = screen.getByRole("group", { name: "Model reasoning" });
    body.focus();
    fireEvent.keyDown(body, { key: "Escape" });

    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(document.activeElement).toBe(toggle);
  });

  it("keeps the operator's choice when a tool step starts and the row moves into the receipt", async () => {
    vi.spyOn(Date, "now").mockReturnValue(Date.parse(at(5)));
    const { container, emit } = await openLive();
    emit({ type: "turn_start", event_id: "k-1", state: "running" });
    emit({ type: "reasoning_delta", event_id: "k-2", step: 1, at: at(0), delta: THOUGHT });
    // The operator closes the row while the model is still thinking.
    fireEvent.click(thinkingToggle(container)!);
    expect(thinkingToggle(container)).toHaveAttribute("aria-expanded", "false");
    emit({
      type: "tool_started",
      event_id: "k-3",
      id: "call-drive",
      name: "drive_search",
      arguments: { query: "Q3 plan" },
      started_at: at(2),
    });

    await screen.findByText("Searching Drive");
    expect(thinkingToggle(container)).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(thinkingToggle(container)!);
    expect(thinkingToggle(container)).toHaveAttribute("aria-expanded", "true");
    emit({
      type: "tool_finished",
      event_id: "k-4",
      id: "call-drive",
      name: "drive_search",
      ok: true,
      result: { files: [{ id: "doc-1" }] },
      finished_at: at(3),
    });
    // The answer closes the receipt. Opening it again shows the row as the
    // operator left it.
    emit({ type: "assistant_delta", event_id: "k-5", delta: "Found it." });
    await screen.findByText("Found it.");
    expect(thinkingToggle(container)).toBeNull();
    fireEvent.click(container.querySelector(".sourcecado-activity-receipt")!);
    expect(thinkingToggle(container)).toHaveAttribute("aria-expanded", "true");
  });

  it("joins the receipt as its first segment: Thought for 5s · Used 1 source", async () => {
    vi.spyOn(Date, "now").mockReturnValue(Date.parse(at(9)));
    const { container, emit } = await openLive();
    emit({ type: "turn_start", event_id: "r-1", state: "running" });
    emit({ type: "reasoning_delta", event_id: "r-2", step: 1, at: at(0), delta: THOUGHT });
    emit({ type: "reasoning_delta", event_id: "r-3", step: 1, at: at(5), delta: "\nOK." });
    emit({
      type: "tool_started",
      event_id: "r-4",
      id: "call-drive",
      name: "drive_search",
      arguments: { query: "Q3 plan" },
      started_at: at(5),
    });
    emit({
      type: "tool_finished",
      event_id: "r-5",
      id: "call-drive",
      name: "drive_search",
      ok: true,
      result: { files: [{ id: "doc-1" }] },
      finished_at: at(6),
    });
    emit({ type: "assistant_delta", event_id: "r-6", delta: "Found it." });
    emit({ type: "turn_end", event_id: "r-7", state: "complete", text: "" });

    await screen.findByText("Found it.");
    const receipt = container.querySelector(".sourcecado-activity-receipt")!;
    expect(receipt).toHaveTextContent("Thought for 5s · Used 1 source in 1s");
    expect(receipt).toHaveAttribute("aria-expanded", "false");
  });

  it("restores the row after a reload, collapsed, from the conversation transcript", async () => {
    api.getSession.mockResolvedValue(
      conversation(
        restored([
          { type: "turn_start", state: "running" },
          { type: "reasoning_delta", step: 1, at: at(0), delta: THOUGHT },
          { type: "reasoning_delta", step: 1, at: at(7), delta: "\nGo." },
          { type: "assistant_delta", delta: "Two founders found." },
          { type: "turn_end", state: "complete", text: "Two founders found." },
        ]),
        [
          { role: "user", content: "Find ML founders" },
          { role: "assistant", content: "Two founders found." },
        ],
      ),
    );

    const { container } = render(<ChatPage sessionId="thread-alpha" />);

    await screen.findByText("Two founders found.");
    const toggle = thinkingToggle(container)!;
    expect(toggle).toHaveTextContent("Thought for 7s");
    expect(toggle).toHaveTextContent("The director wants ML founders in Berkeley.");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(toggle);
    expect(screen.getByRole("group", { name: "Model reasoning" })).toHaveTextContent(
      "I will search Apollo first.",
    );
  });

  it("goes back to Thinking… when a reload lands in the middle of a reasoning stream", async () => {
    const now = vi.spyOn(Date, "now");
    now.mockReturnValue(Date.parse(at(6)));
    const snapshot = restored([
      { type: "turn_start", state: "running" },
      { type: "reasoning_delta", step: 1, at: at(0), delta: "The director wants founders. " },
      { type: "reasoning_delta", step: 1, at: at(5), delta: "Checking Apollo. " },
    ]);
    api.getSession.mockResolvedValue(conversation(snapshot));
    const { container } = render(<ChatPage sessionId="thread-alpha" />);
    await waitFor(() => expect(onChatEvent).toBeDefined());
    const emit = (event: Record<string, unknown>) =>
      act(() => onChatEvent?.({ ...envelope, ...event }));

    // The snapshot alone cannot say the run is alive.
    const toggle = await waitFor(() => {
      const found = thinkingToggle(container);
      expect(found).not.toBeNull();
      return found!;
    });
    expect(toggle).toHaveTextContent("Thought for 5s");

    // The backend re-announces the run. The row is thinking again at once.
    emit(snapshot[0]!);
    expect(toggle).toHaveTextContent("Thinking…");
    expect(toggle).toHaveAttribute("aria-expanded", "true");

    // More reasoning arrives. It is added to the same row, which keeps counting.
    now.mockReturnValue(Date.parse(at(9)));
    emit({ type: "reasoning_delta", event_id: "live-1", step: 1, at: at(9), delta: "Then LinkedIn." });
    expect(toggle).toHaveTextContent("Thinking…");
    expect(toggle).toHaveTextContent("9s");
    expect(screen.getByRole("group", { name: "Model reasoning" })).toHaveTextContent(
      "The director wants founders. Checking Apollo. Then LinkedIn.",
    );

    emit({ type: "assistant_delta", event_id: "live-2", delta: "Found two founders." });
    await screen.findByText("Found two founders.");
    expect(toggle).toHaveTextContent("Thought for 9s");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
  });

  it("goes back to Thinking… when more reasoning arrives and no re-announcement came", async () => {
    vi.spyOn(Date, "now").mockReturnValue(Date.parse(at(8)));
    api.getSession.mockResolvedValue(
      conversation(
        restored([
          { type: "turn_start", state: "running" },
          { type: "reasoning_delta", step: 1, at: at(0), delta: "First. " },
          { type: "reasoning_delta", step: 1, at: at(5), delta: "Second. " },
        ]),
      ),
    );
    const { container } = render(<ChatPage sessionId="thread-alpha" />);
    await waitFor(() => expect(onChatEvent).toBeDefined());
    const toggle = await waitFor(() => {
      const found = thinkingToggle(container);
      expect(found).not.toBeNull();
      return found!;
    });
    expect(toggle).toHaveTextContent("Thought for 5s");

    act(() =>
      onChatEvent?.({
        ...envelope,
        type: "reasoning_delta",
        event_id: "live-1",
        step: 1,
        at: at(8),
        delta: "Third.",
      }),
    );

    expect(toggle).toHaveTextContent("Thinking…");
    expect(toggle).toHaveTextContent("8s");
  });

  it("stops the live count when a provider attempt fails, and counts again for the retry", async () => {
    const now = vi.spyOn(Date, "now");
    now.mockReturnValue(Date.parse(at(9)));
    const { container, emit } = await openLive();
    emit({ type: "turn_start", event_id: "p-1", state: "running" });
    emit({ type: "reasoning_delta", event_id: "p-2", step: 1, at: at(0), delta: "Plan the search." });
    emit({ type: "reasoning_delta", event_id: "p-3", step: 1, at: at(2), delta: " Go." });
    const toggle = await waitFor(() => {
      const found = thinkingToggle(container);
      expect(found).not.toBeNull();
      return found!;
    });
    expect(toggle).toHaveTextContent("Thinking…");

    emit({
      type: "provider_recovery",
      event_id: "p-4",
      action: "retry",
      provider: "deepseek",
      model: "deepseek-v4-pro",
      attempt: 2,
      reason: "rate_limit",
      delay_ms: 250,
      message: "Retrying the model provider after a temporary failure.",
    });

    // The thinking time is what the model streamed, not the wait for the retry.
    expect(toggle).toHaveTextContent("Thought for 2s");
    expect(toggle).not.toHaveTextContent("Thinking…");

    now.mockReturnValue(Date.parse(at(11)));
    emit({ type: "reasoning_delta", event_id: "p-5", step: 2, at: at(10), delta: "Second attempt." });
    expect(toggle).toHaveTextContent("Thinking…");
    expect(toggle).toHaveTextContent("3s");
  });

  it("shows no row and no gap for a provider that sent no reasoning", async () => {
    api.getSession.mockResolvedValue(
      conversation(
        restored([
          { type: "turn_start", state: "running" },
          { type: "assistant_delta", delta: "Plain answer." },
          { type: "turn_end", state: "complete", text: "Plain answer." },
        ]),
      ),
    );

    const { container } = render(<ChatPage sessionId="thread-alpha" />);

    await screen.findByText("Plain answer.");
    expect(container.querySelector(".sourcecado-thinking")).toBeNull();
    expect(container.querySelector(".sourcecado-activity")).toBeNull();
  });

  it("restores a history from before reasoning events existed", async () => {
    api.getSession.mockResolvedValue(
      conversation([], [
        { role: "user", content: "Find ML founders" },
        { role: "assistant", content: "Two founders found." },
      ]),
    );

    const { container } = render(<ChatPage sessionId="thread-alpha" />);

    await screen.findByText("Two founders found.");
    expect(container.querySelector(".sourcecado-thinking")).toBeNull();
  });

  it("never treats reasoning as a recorded action and keeps it out of Copy response", async () => {
    api.getSession.mockResolvedValue(
      conversation(
        restored([
          { type: "turn_start", state: "running" },
          {
            type: "reasoning_delta",
            step: 1,
            at: at(0),
            delta: "I sent the email to Maya and booked the call.",
          },
          { type: "assistant_delta", delta: "Nothing was sent." },
          { type: "turn_end", state: "complete", text: "Nothing was sent." },
        ]),
      ),
    );

    const { container } = render(<ChatPage sessionId="thread-alpha" />);

    await screen.findByText("Nothing was sent.");
    // Only a tool step row is a recorded action. Reasoning makes none.
    expect(container.querySelectorAll("[data-tool-call-id]")).toHaveLength(0);
    expect(container.querySelector(".sourcecado-step-done")).toBeNull();
    expect(container).not.toHaveTextContent("Completed");
    fireEvent.click(screen.getByRole("button", { name: "Copy response" }));
    expect(writeText).toHaveBeenCalledWith("Nothing was sent.");
  });

  it("suppresses a reasoning event that arrives twice", async () => {
    const { container, emit } = await openLive();
    emit({ type: "turn_start", event_id: "d-1", state: "running" });
    const delta = { type: "reasoning_delta", event_id: "d-2", step: 1, at: at(0), delta: "Once." };
    emit(delta);
    emit(delta);
    emit({ type: "assistant_delta", event_id: "d-3", delta: "Answer." });

    await screen.findByText("Answer.");
    fireEvent.click(thinkingToggle(container)!);
    expect(screen.getByRole("group", { name: "Model reasoning" })).toHaveTextContent(/^Once\.$/);
  });
});

describe("Thinking row styles", () => {
  it("keeps the row at 13px or larger and out of muted ink", () => {
    const block = styles.slice(
      styles.indexOf("/* Thinking row (#225)"),
      styles.indexOf(".sourcecado-apollo-result {"),
    );
    expect(block).toContain(".sourcecado-thinking-body");
    const sizes = [...block.matchAll(/font-size:\s*(\d+)px/g)].map((m) => Number(m[1]));
    expect(Math.min(...sizes)).toBeGreaterThanOrEqual(13);
    expect(block).not.toMatch(/var\(--muted\)/);
  });

  it("stops the pulse for reduced motion and bounds the open text", () => {
    expect(styles).toMatch(
      /@media \(prefers-reduced-motion: reduce\)\s*\{\s*\.sourcecado-thinking-live \.sourcecado-thinking-icon\s*\{\s*animation:\s*none;/,
    );
    expect(styles).toMatch(
      /\.sourcecado-thinking-body\s*\{[^}]*max-height:\s*min\(240px, 35vh\);[^}]*overflow-y:\s*auto;/,
    );
  });
});
