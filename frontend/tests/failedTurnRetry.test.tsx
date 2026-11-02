import { readFileSync } from "node:fs";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { convertStructuredMessage } from "../src/chat/messageAdapter";
import { SourcecadoChatStore } from "../src/chat/store";
import { READ_ONLY_TOOLS, turnRetryBlock } from "../src/chat/retrySafety";
import { providerLine } from "../src/chat/TurnNotice";
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

  it("sends like the Retry under an answer: the draft stays and focus goes to the composer", async () => {
    const { emit } = await openLive();
    emit(failedEvent);
    const composer = screen.getByRole("textbox", { name: "Message Sourcecado" });
    fireEvent.change(composer, { target: { value: "Half a thought" } });

    fireEvent.click(await screen.findByRole("button", { name: "Retry this turn" }));

    await waitFor(() => expect(chatSend).toHaveBeenCalledTimes(1));
    expect(composer).toHaveValue("Half a thought");
    expect(window.localStorage.getItem("sourcecado.chat.draft.v1:thread-alpha")).toBe("Half a thought");
    await waitFor(() => expect(composer).toHaveFocus());
  });

  it("sends one turn when the notice Retry is activated twice at once", async () => {
    const { emit } = await openLive();
    emit(failedEvent);
    const retry = await screen.findByRole("button", { name: "Retry this turn" });

    act(() => {
      fireEvent.click(retry);
      fireEvent.click(retry);
    });

    await waitFor(() => expect(chatSend).toHaveBeenCalledTimes(1));
  });

  it("shows why Retry is off in a visible line, and shows no line while it is on", async () => {
    const on = await openLive();
    on.emit(failedEvent);
    await screen.findByRole("button", { name: "Retry this turn" });
    expect(screen.queryByText(/Retry is off\./)).not.toBeInTheDocument();
    on.unmount();

    const off = await openLive([]);
    off.emit(failedEvent);
    const line = (await screen.findByText(/Retry is off\./)).closest("p")!;
    expect(line).toHaveTextContent("Retry is off. There is no earlier message to send again.");
    expect(line).toHaveClass("sourcecado-retry-reason");
    // It sits inside the notice, beside the button that it explains.
    expect(screen.getByRole("note")).toContainElement(line);
    expect(screen.getByRole("button", { name: /^Retry unavailable/ })).toHaveAttribute("aria-disabled", "true");
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

  it("allows Retry after steps that only read, and turns it off after a step that writes", async () => {
    const step = (id: string, name: string, eventId: string) => [
      { type: "tool_started", event_id: `${eventId}-s`, id, name, arguments: {} },
      { type: "tool_finished", event_id: `${eventId}-f`, id, name, ok: true, result: { ok: true } },
    ];
    const reading = await openLive();
    for (const event of step("call-read", "board_query", "read")) reading.emit(event);
    reading.emit(failedEvent);
    expect(await screen.findByRole("button", { name: "Retry this turn" })).not.toHaveAttribute("aria-disabled");
    reading.unmount();

    const writing = await openLive();
    for (const event of step("call-keep", "people_keep", "keep")) writing.emit(event);
    writing.emit(failedEvent);
    expect(await screen.findByRole("button", { name: /^Retry unavailable/ })).toHaveAccessibleName(
      "Retry unavailable. This turn ran a step that can change data. Retry could repeat it.",
    );
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
    expect(turnRetryBlock([tool()], "failed")).toBeNull();
    expect(turnRetryBlock([], "failed")).toBeNull();
  });

  it("blocks Retry when a step carried an approval", () => {
    expect(turnRetryBlock([tool({ approval: { id: "a", approved: true } })], "failed")).toBe(
      "This turn used an approval. Retry could repeat the action.",
    );
  });

  it("blocks Retry when a step has no recorded result", () => {
    expect(turnRetryBlock([tool({ result: undefined })], "failed")).toBe(
      "A step has no recorded result. Check it before you retry.",
    );
  });

  it("blocks Retry when a failed step is not retry-safe", () => {
    const step = tool({
      result: { error: "x" },
      isError: true,
      providerMetadata: { sourcecado: { failure: { class: "unknown", retry_safe: false } } },
    });
    expect(turnRetryBlock([step], "failed")).toBe(
      "This turn ran a step that can change data. Retry could repeat it.",
    );
  });

  it("blocks Retry when any step is a send, a paid enrichment, a write, a command or an unknown tool", () => {
    for (const toolName of [
      "gmail_send",
      "gmail_draft",
      "apollo_enrich_contact",
      "people_keep",
      "board_upsert",
      "board_delete",
      "calendar_create",
      "shell_exec",
      "fs_trash",
      "remember",
      "mcp__granola__create_note",
      "a_tool_nobody_has_heard_of",
    ]) {
      expect(turnRetryBlock([tool(), tool({ toolName })], "failed"), toolName).toBe(
        "This turn ran a step that can change data. Retry could repeat it.",
      );
    }
  });

  it("allows Retry only when every step is a known read-only tool", () => {
    for (const toolName of READ_ONLY_TOOLS) {
      expect(turnRetryBlock([tool({ toolName })], "failed"), toolName).toBeNull();
    }
  });

  it("keeps the read-only list equal to RETRY_SAFE in the backend", () => {
    const source = readFileSync("../backend/coworker/permissions.py", "utf8");
    const list = source.match(/RETRY_SAFE = frozenset\(\s*\{([^}]*)\}/)?.[1] ?? "";
    const backend = [...list.matchAll(/"([a-z_]+)"/g)].map((match) => match[1]).sort();
    expect(backend.length).toBeGreaterThan(5);
    expect([...READ_ONLY_TOOLS].sort()).toEqual(backend);
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

  describe("a run the backend lost", () => {
    const startRun = () => {
      act(() => {
        onChatEvent?.({ ...envelope, type: "turn_start", event_id: "run-start", state: "running" });
        onChatEvent?.({ ...envelope, type: "assistant_delta", event_id: "run-delta", delta: "Working on it." });
      });
    };
    const settle = async () => {
      await act(async () => {
        vi.advanceTimersByTime(1600);
      });
    };

    async function openRunning(currentRun: Record<string, unknown> | null) {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      render(<ChatPage sessionId="thread-alpha" />);
      await screen.findByRole("heading", { level: 1, name: "Alpha" });
      startRun();
      expect(screen.getByRole("button", { name: "Stop run" })).toBeInTheDocument();
      api.getCurrentRunTelemetry.mockResolvedValue({
        version: 1,
        session_id: "thread-alpha",
        current_run: currentRun,
      });
    }

    it("settles the header and the composer after a reconnect when no run is alive", async () => {
      await openRunning(null);
      expect(screen.getByRole("banner")).not.toHaveTextContent("Idle");

      change("reconnecting", 1);
      change("connected", 0, "Connected to the backend.");
      await settle();

      await waitFor(() => expect(screen.getByRole("banner")).toHaveTextContent("Idle"));
      expect(screen.queryByRole("button", { name: "Stop run" })).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Send message" })).toBeInTheDocument();
      expect(screen.getByRole("status")).toHaveTextContent("Run interrupted. The connection was lost.");
    });

    it("leaves a live run alone when the backend says it is still running", async () => {
      await openRunning({ run_id: "run-1", status: "running", elapsed_ms: 9000 });

      change("reconnecting", 1);
      change("connected", 0, "Connected to the backend.");
      await settle();

      expect(screen.getByRole("button", { name: "Stop run" })).toBeInTheDocument();
      expect(screen.getByRole("banner")).not.toHaveTextContent("Idle");
    });

    it("leaves a running turn alone when the socket was never down", async () => {
      await openRunning(null);

      change("connected", 0, "Connected to the backend.");
      await settle();

      expect(screen.getByRole("button", { name: "Stop run" })).toBeInTheDocument();
      expect(screen.getByRole("banner")).not.toHaveTextContent("Idle");
    });

    it("leaves the run as it was when the backend cannot answer", async () => {
      await openRunning(null);
      api.getCurrentRunTelemetry.mockRejectedValue(new Error("offline"));

      change("reconnecting", 1);
      change("connected", 0, "Connected to the backend.");
      await settle();

      expect(screen.getByRole("button", { name: "Stop run" })).toBeInTheDocument();
    });

    it("skips a run that a replayed terminal event already settled", async () => {
      await openRunning(null);

      change("reconnecting", 1);
      change("connected", 0, "Connected to the backend.");
      act(() => {
        onChatEvent?.({ ...envelope, type: "turn_end", event_id: "run-end", state: "complete", text: "Working on it." });
      });
      api.getCurrentRunTelemetry.mockClear();
      await settle();

      expect(api.getCurrentRunTelemetry).not.toHaveBeenCalled();
      expect(screen.getByRole("banner")).toHaveTextContent("Idle");
    });
  });
});
