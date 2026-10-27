import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { convertStructuredMessage } from "../src/chat/messageAdapter";
import { SourcecadoChatStore } from "../src/chat/store";
import { failedTurnRetryBlock, providerLine } from "../src/chat/TurnNotice";
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

const envelope = {
  version: 2,
  session_id: "thread-alpha",
  run_id: "run-1",
  message_id: "message-1",
  part_id: "part-1",
} as const;

const providerFailure = {
  code: "authentication",
  provider: "openai",
  model: "gpt-4o-mini",
  attempts: 1,
  recovery_count: 2,
  exhausted: true,
};

const failedEvent = {
  ...envelope,
  type: "error",
  event_id: "event-error",
  state: "failed",
  error_kind: "authentication",
  message: "Model provider authentication failed. Review provider setup in Settings.",
  failure: providerFailure,
} as const;

describe("failed turn notice", () => {
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

  afterEach(() => {
    vi.useRealTimers();
  });

  async function openLive(messages: unknown[] = [{ role: "user", content: "Find five ML founders" }]) {
    api.getSession.mockResolvedValue({
      id: "thread-alpha",
      title: "Failed turn",
      messages,
      queue: [],
      queue_paused: false,
      events: [],
    });
    const view = render(<ChatPage sessionId="thread-alpha" />);
    await screen.findByRole("heading", { level: 1, name: "Failed turn" });
    await waitFor(() => expect(onChatEvent).toBeDefined());
    const emit = (event: Record<string, unknown>) =>
      act(() => onChatEvent?.({ ...envelope, ...event }));
    emit({ type: "turn_start", event_id: "event-start", state: "running" });
    return { ...view, emit };
  }

  it("names the provider that failed and offers Retry as an icon button", async () => {
    const { emit } = await openLive();
    emit(failedEvent);

    const notice = await screen.findByRole("note");
    expect(notice).toHaveTextContent("This turn failed.");
    expect(notice).toHaveTextContent("Provider OpenAI · gpt-4o-mini");
    expect(notice).toHaveTextContent("Model provider authentication failed.");
    const retry = screen.getByRole("button", { name: "Retry this turn" });
    expect(retry).toHaveTextContent("");
    expect(retry.querySelector("svg")).not.toBeNull();
  });

  it("re-sends the operator message that the failed turn answered", async () => {
    const { emit } = await openLive();
    emit(failedEvent);

    fireEvent.click(await screen.findByRole("button", { name: "Retry this turn" }));
    await waitFor(() =>
      expect(chatSend).toHaveBeenCalledWith("Find five ML founders", "thread-alpha"),
    );
  });

  it("says nothing about a provider when the backend names none", async () => {
    const { emit } = await openLive();
    const { failure: _failure, ...withoutProvider } = failedEvent;
    emit(withoutProvider);

    const notice = await screen.findByRole("note");
    expect(notice).not.toHaveTextContent("Provider");
    expect(screen.getByRole("button", { name: "Retry this turn" })).toBeInTheDocument();
  });

  it("turns Retry off, with the reason, when the failed turn already used an approval", async () => {
    const { emit } = await openLive();
    emit({
      type: "tool_started",
      event_id: "event-tool",
      id: "call-draft",
      name: "gmail_draft",
      arguments: { to: "maya@example.com" },
    });
    emit({
      type: "permission_required",
      event_id: "event-permission",
      id: "approval-1",
      name: "gmail_draft",
      arguments: { to: "maya@example.com" },
      reason: "consequential: waits for Allow or Deny",
      requested_at: "2026-10-26T12:00:00.000Z",
      scope: "once",
    });
    emit({
      type: "approval_resolved",
      event_id: "event-resolved",
      id: "approval-1",
      decision: "allow",
      actor: "operator",
      resolved_at: "2026-10-26T12:00:01.000Z",
      scope: "once",
      execution_status: "succeeded",
      execution_error: null,
    });
    emit(failedEvent);

    const retry = await screen.findByRole("button", { name: /^Retry unavailable/ });
    expect(retry).toHaveAttribute("aria-disabled", "true");
    expect(retry).toHaveAccessibleName(
      "Retry unavailable. This turn used an approval. Retry could repeat the action.",
    );
    fireEvent.click(retry);
    expect(chatSend).not.toHaveBeenCalled();
  });

  it("turns Retry off when no operator message came before the failure", async () => {
    const { emit } = await openLive([]);
    emit(failedEvent);

    const retry = await screen.findByRole("button", { name: /^Retry unavailable/ });
    expect(retry).toHaveAccessibleName(
      "Retry unavailable. There is no earlier message to send again.",
    );
  });

  it("leaves a held turn alone: no failure notice, no Retry", async () => {
    const { emit } = await openLive();
    emit({
      type: "error",
      event_id: "event-held",
      state: "held",
      code: "outcome_unknown",
      effect_id: "effect-9f2c",
      message:
        "The outcome of this action is unknown. It is held for review; do not retry it until it is settled.",
    });

    expect(screen.queryByRole("note")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Retry/ })).not.toBeInTheDocument();
  });

  it("keeps Retry on the newest failure only", async () => {
    const { emit } = await openLive();
    emit(failedEvent);
    await screen.findByRole("button", { name: "Retry this turn" });

    emit({ type: "turn_start", event_id: "event-start-2", run_id: "run-2", message_id: "message-2", state: "running" });
    emit({
      ...failedEvent,
      event_id: "event-error-2",
      run_id: "run-2",
      message_id: "message-2",
      error_kind: "rate_limit",
      message: "The model provider remained rate limited after bounded retries.",
      failure: { ...providerFailure, code: "rate_limit", provider: "deepseek", model: "deepseek-v4-pro" },
    });

    const notes = await screen.findAllByRole("note");
    expect(notes).toHaveLength(2);
    expect(notes[0]).not.toHaveTextContent("Retry");
    expect(notes[0]?.querySelector("button")).toBeNull();
    expect(notes[1]?.querySelector("button")).not.toBeNull();
    expect(notes[1]).toHaveTextContent("Provider DeepSeek · deepseek-v4-pro");
  });

  it("restores the notice with its provider and Retry after a reload", async () => {
    api.getSession.mockResolvedValue({
      id: "thread-alpha",
      title: "Failed turn",
      messages: [{ role: "user", content: "Find five ML founders", message_id: "message-user" }],
      queue: [],
      queue_paused: false,
      events: [
        { ...envelope, type: "turn_start", event_id: "restore-1", state: "running" },
        failedEvent,
      ],
    });
    render(<ChatPage sessionId="thread-alpha" />);

    const notice = await screen.findByRole("note");
    expect(notice).toHaveTextContent("Provider OpenAI · gpt-4o-mini");
    fireEvent.click(screen.getByRole("button", { name: "Retry this turn" }));
    await waitFor(() =>
      expect(chatSend).toHaveBeenCalledWith("Find five ML founders", "thread-alpha"),
    );
  });

  it("does not show Retry on a connection or history notice", async () => {
    await openLive();
    act(() =>
      onChatEvent?.({
        type: "error",
        message: "The backend connection is down and the retry buffer is full; the command was dropped.",
      }),
    );

    const notice = await screen.findByRole("note");
    expect(notice).toHaveTextContent("Connection problem.");
    expect(notice.querySelector("button")).toBeNull();
  });
});

describe("failed turn rules", () => {
  const tool = (overrides: Record<string, unknown> = {}) =>
    ({
      type: "tool-call",
      toolCallId: "call-1",
      toolName: "board_query",
      args: {},
      argsText: "{}",
      result: { people: [] },
      ...overrides,
    }) as never;

  it("allows Retry after steps that only read", () => {
    expect(failedTurnRetryBlock([tool()], "failed")).toBeNull();
    expect(failedTurnRetryBlock([], "failed")).toBeNull();
  });

  it("blocks Retry when a step carried an approval", () => {
    expect(failedTurnRetryBlock([tool({ approval: { id: "a", approved: true } })], "failed")).toBe(
      "This turn used an approval. Retry could repeat the action.",
    );
  });

  it("blocks Retry when a step has no recorded result", () => {
    expect(failedTurnRetryBlock([tool({ result: undefined })], "failed")).toBe(
      "A step has no recorded result. Check it before you retry.",
    );
  });

  it("blocks Retry when a failed step is not retry-safe", () => {
    const step = tool({
      result: { error: "x" },
      isError: true,
      providerMetadata: { sourcecado: { failure: { class: "unknown", retry_safe: false } } },
    });
    expect(failedTurnRetryBlock([step], "failed")).toBe("A failed step is not safe to repeat.");
  });

  it("names providers in words", () => {
    expect(providerLine("openai", "gpt-4o-mini")).toBe("OpenAI · gpt-4o-mini");
    expect(providerLine("deepseek", "unknown")).toBe("DeepSeek");
    expect(providerLine("openai_compatible", "")).toBe("OpenAI-compatible provider");
    expect(providerLine("unknown", "x")).toBeNull();
    expect(providerLine(undefined, undefined)).toBeNull();
  });
});

describe("failed turn notice data", () => {
  const start = { ...envelope, type: "turn_start", event_id: "e-1", state: "running" } as const;

  it("keeps the provider on the notice part and in the message metadata", () => {
    const store = new SourcecadoChatStore([{ id: "thread-alpha", messages: [] }], "thread-alpha");
    store.replayChatEvents([start, failedEvent]);

    const notice = store.messagesFor("thread-alpha").flatMap((message) => message.parts).find((part) => part.type === "notice");
    expect(notice).toMatchObject({ provider: "openai", model: "gpt-4o-mini", recoverable: false });
    const message = store.messagesFor("thread-alpha").find((candidate) => candidate.parts.some((part) => part.type === "notice"))!;
    expect(convertStructuredMessage(message).metadata?.custom).toMatchObject({
      sourcecadoNotice: true,
      sourcecadoNoticeFailedTurn: true,
      sourcecadoNoticeProvider: "openai",
      sourcecadoNoticeModel: "gpt-4o-mini",
    });
  });

  it("marks a connection notice as not a failed turn", () => {
    const store = new SourcecadoChatStore([{ id: "thread-alpha", messages: [] }], "thread-alpha");
    store.applyChatEvent({ type: "error", message: "dropped", session_id: "thread-alpha" });

    const message = store.messagesFor("thread-alpha")[0]!;
    expect(convertStructuredMessage(message).metadata?.custom).toMatchObject({
      sourcecadoNotice: true,
      sourcecadoNoticeFailedTurn: false,
    });
  });
});

describe("reconnect chip", () => {
  beforeEach(() => {
    window.localStorage.clear();
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
        send: vi.fn(),
        approve: vi.fn(),
        cancel: vi.fn(),
        queue: vi.fn(),
        recover: vi.fn(),
        close: vi.fn(),
      };
    });
    api.getSession.mockResolvedValue({
      id: "thread-alpha",
      title: "Alpha",
      messages: [],
      queue: [],
      queue_paused: false,
      events: [],
    });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  const change = (status: string, attempt: number, reason = "The backend connection closed (code 1006). Reconnecting.") =>
    act(() => onChatEvent?.({ type: "connection_change", status, attempt, reason }));
  const chips = () => document.querySelectorAll(".sourcecado-connection");

  it("shows one chip above the composer, counts the attempts, and clears when the socket returns", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    render(<ChatPage sessionId="thread-alpha" />);
    await screen.findByRole("heading", { level: 1, name: "Alpha" });
    expect(chips()).toHaveLength(0);

    change("reconnecting", 1);
    expect(chips()).toHaveLength(1);
    expect(chips()[0]).toHaveTextContent("Reconnecting");
    expect(chips()[0]).toHaveTextContent("attempt 1");

    // A later attempt updates the same chip. A second chip never appears.
    change("reconnecting", 2);
    expect(chips()).toHaveLength(1);
    expect(chips()[0]).toHaveTextContent("attempt 2");

    // It sits directly above the message box, in the same flow.
    const composer = document.querySelector(".sourcecado-composer")!;
    expect(chips()[0]!.closest(".sourcecado-connection-region")!.nextElementSibling).toBe(composer);

    change("connected", 0, "Connected to the backend.");
    expect(chips()).toHaveLength(1);
    expect(chips()[0]).toHaveTextContent("Reconnected");
    act(() => {
      vi.advanceTimersByTime(3500);
    });
    expect(chips()).toHaveLength(0);
  });

  it("shows nothing for a socket that was never down", async () => {
    render(<ChatPage sessionId="thread-alpha" />);
    await screen.findByRole("heading", { level: 1, name: "Alpha" });

    change("connected", 0, "Connected to the backend.");
    expect(chips()).toHaveLength(0);
  });

  it("is announced by its own live region and adds no second status region", async () => {
    render(<ChatPage sessionId="thread-alpha" />);
    await screen.findByRole("heading", { level: 1, name: "Alpha" });

    change("reconnecting", 1);
    const region = chips()[0]!.closest(".sourcecado-connection-region")!;
    expect(region).toHaveAttribute("aria-live", "polite");
    expect(screen.getAllByRole("status")).toHaveLength(1);
  });

  it("says Disconnected, with the reason and a Reload button, when the backend refused the token", async () => {
    render(<ChatPage sessionId="thread-alpha" />);
    await screen.findByRole("heading", { level: 1, name: "Alpha" });

    change("offline", 0, "The backend rejected the connection token.");
    expect(chips()[0]).toHaveTextContent("Disconnected");
    expect(chips()[0]).toHaveTextContent("The backend rejected the connection token.");
    const reload = screen.getByRole("button", { name: "Reload Sourcecado" });
    expect(reload.querySelector("svg")).not.toBeNull();
  });
});
