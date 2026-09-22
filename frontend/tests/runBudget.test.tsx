import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ChatPage } from "../src/routes/ChatPage";
import {
  parseChatEvent,
  runBudgetStatus,
  runBudgetStopText,
  type RunBudgetStatus,
} from "../src/chat/protocol";

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

const LIMITS = {
  model_turns: 40,
  tool_calls: 120,
  elapsed_seconds: 900,
  input_tokens: 2000000,
  output_tokens: 200000,
  estimated_cost_usd: 2,
};

function envelope(type: string, extra: Record<string, unknown>) {
  return {
    version: 2,
    session_id: "thread-alpha",
    run_id: "run-budget",
    message_id: "assistant-budget",
    part_id: "assistant-budget-part",
    event_id: `event-${type}-${String(extra.id ?? extra.state ?? "x")}`,
    type,
    ...extra,
  };
}

const EXHAUSTED_BUDGET = {
  state: "exhausted",
  stopped_by: "model_turns",
  exhausted: ["model_turns"],
  repeats: 0,
  consumed: {
    model_turns: 40,
    tool_calls: 63,
    elapsed_seconds: 214,
    input_tokens: 91000,
    output_tokens: 4200,
    estimated_cost_usd: 0.12,
  },
  limits: LIMITS,
  warning: null,
  completed: [
    { id: "call-1", name: "board_query", ok: true },
    { id: "call-2", name: "drive_search", ok: false },
  ],
  remaining: {
    requested_tools: [{ id: "call-3", name: "gmail_draft" }],
    final_answer: false,
  },
  unpriced_requests: 0,
  unmeasured_requests: 0,
  continue_available: true,
};

describe("run budget projection", () => {
  it("keeps only contract fields and never the backend's own prose", () => {
    const status = runBudgetStatus({
      ...EXHAUSTED_BUDGET,
      message: "Shortlist complete, nothing further is needed.",
      stopped_by: "wallet",
      exhausted: ["wallet", "tool_calls"],
      completed: [{ id: "call-1", name: "board_query", ok: true }, "junk"],
      extra_field: "PLANTED",
    }) as RunBudgetStatus;

    expect(status).not.toHaveProperty("message");
    expect(status).not.toHaveProperty("extra_field");
    expect(status.stopped_by).toBeNull();
    expect(status.exhausted).toEqual(["tool_calls"]);
    expect(status.completed).toEqual([
      { id: "call-1", name: "board_query", ok: true },
    ]);
    expect(runBudgetStopText(status)).not.toContain("nothing further");
  });

  it("treats a missing final answer as unfinished rather than complete", () => {
    const status = runBudgetStatus({
      state: "exhausted",
      stopped_by: "elapsed_seconds",
      exhausted: ["elapsed_seconds"],
      consumed: LIMITS,
      limits: LIMITS,
    }) as RunBudgetStatus;

    expect(status.remaining.final_answer).toBe(false);
    expect(runBudgetStopText(status)).toContain("No final answer was written");
  });

  it("names a stuck run as stuck, not as expensive", () => {
    const status = runBudgetStatus({
      state: "exhausted",
      stopped_by: "loop",
      exhausted: [],
      repeats: 3,
      consumed: LIMITS,
      limits: LIMITS,
      completed: [{ id: "a", name: "drive_read", ok: true }],
      remaining: { requested_tools: [], final_answer: false },
      continue_available: true,
    }) as RunBudgetStatus;

    const text = runBudgetStopText(status);
    expect(text).toContain("repeating itself");
    expect(text).not.toContain("budget.");
  });

  it("rebuilds the payload when it arrives on a turn_end event", () => {
    const event = parseChatEvent(
      envelope("turn_end", {
        text: "Partial.",
        state: "stopped",
        run_budget: { ...EXHAUSTED_BUDGET, message: "PLANTED" },
      }),
    );
    expect(event.type).toBe("turn_end");
    const budget = (event as { run_budget?: RunBudgetStatus }).run_budget;
    expect(budget?.stopped_by).toBe("model_turns");
    expect(budget).not.toHaveProperty("message");
  });
});

describe("ChatPage run budget", () => {
  beforeEach(() => {
    window.localStorage.clear();
    chatSend.mockReset();
    onChatEvent = undefined;
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
      configurable: true,
      value: vi.fn(),
    });
    api.getHealth.mockResolvedValue({
      status: "ok",
      piece: "desktop",
      slice: 1,
      model: "operator-model",
    });
    api.getSessions.mockResolvedValue({ sessions: [], open_id: "thread-alpha" });
    api.getPersona.mockResolvedValue({
      id: "sourcing",
      name: "Sourcing Operator",
      tools: [],
    });
    api.getSchedule.mockResolvedValue({ jobs: [], runs: [] });
    api.getGmail.mockResolvedValue({ connected: false, email: null });
    api.getConnectors.mockResolvedValue({ connectors: [] });
    api.getSettings.mockResolvedValue({
      persona: { id: "sourcing", name: "Sourcing Operator" },
      model: "operator-model",
      gmail: { connected: false, email: null },
      apollo: { configured: false },
    });
    api.getInbox.mockResolvedValue({ items: [] });
    api.getCurrentRunTelemetry.mockResolvedValue({
      version: 1,
      session_id: "thread-alpha",
      current_run: null,
    });
    api.openChat.mockImplementation((callback: typeof onChatEvent) => {
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
    api.getSession.mockResolvedValue({
      id: "thread-alpha",
      title: "Budgeted run",
      messages: [],
      events: [],
    });
  });

  it("shows what the run is doing and warns before a budget is exhausted", async () => {
    render(<ChatPage sessionId="thread-alpha" />);
    await waitFor(() => expect(onChatEvent).toBeDefined());

    act(() => {
      onChatEvent?.(envelope("turn_start", { state: "running" }));
      onChatEvent?.(
        envelope("tool_started", {
          id: "call-1",
          name: "drive_search",
          arguments: {},
          run_budget: {
            state: "warning",
            stopped_by: null,
            exhausted: [],
            consumed: {
              model_turns: 33,
              tool_calls: 61,
              elapsed_seconds: 214,
              input_tokens: 91000,
              output_tokens: 4200,
              estimated_cost_usd: 0.12,
            },
            limits: LIMITS,
            warning: {
              budget: "model_turns",
              used: 33,
              limit: 40,
              used_ratio: 0.825,
            },
          },
        }),
      );
    });

    // Current activity is named, and the run's spend sits beside it.
    const strip = await screen.findByRole("region", { name: "Run budget" });
    const activity = document.querySelector(".sourcecado-activity");
    expect(activity).toHaveTextContent("Searched Drive");
    expect(activity).toHaveTextContent("Running");
    expect(strip).toHaveTextContent("Step 33 of 40");
    expect(strip).toHaveTextContent("61 of 120 tool calls");
    expect(strip).toHaveTextContent("3m 34s of 15m 00s");
    expect(strip).toHaveTextContent("$0.12 of $2.00 est.");
    expect(
      screen.getByText(/used 83% of its model turns budget/i),
    ).toBeInTheDocument();
    // The warning arrives before the stop, not with it.
    expect(screen.queryByRole("note")).not.toBeInTheDocument();
  });

  it("marks an exhausted run unfinished, shows receipts, and offers Continue", async () => {
    render(<ChatPage sessionId="thread-alpha" />);
    await waitFor(() => expect(onChatEvent).toBeDefined());

    act(() => {
      onChatEvent?.(envelope("turn_start", { state: "running" }));
      onChatEvent?.(
        envelope("assistant_delta", { delta: "Three names so far." }),
      );
      onChatEvent?.(
        envelope("turn_end", {
          text: "Three names so far.",
          state: "stopped",
          run_budget: EXHAUSTED_BUDGET,
        }),
      );
    });

    const note = await screen.findByRole("note", {
      name: "Run stopped before finishing",
    });
    expect(note).toHaveTextContent("This run did not finish.");
    expect(note).toHaveTextContent("stopped this run at its model turns budget");
    expect(note).toHaveTextContent("2 tool steps completed.");
    expect(note).toHaveTextContent("1 of them failed.");
    expect(note).toHaveTextContent("1 more was queued and never ran.");
    expect(note).toHaveTextContent("No final answer was written");
    const receipts = screen.getByRole("list", { name: "Completed tool steps" });
    expect(receipts).toHaveTextContent("board_query");
    expect(receipts).toHaveTextContent("drive_search — failed");

    fireEvent.click(screen.getByRole("button", { name: "Continue this run" }));

    await waitFor(() =>
      expect(chatSend).toHaveBeenCalledWith(
        "Continue this run from where it stopped.",
        "thread-alpha",
      ),
    );
  });

  it("shows no stop card when the run actually finished", async () => {
    render(<ChatPage sessionId="thread-alpha" />);
    await waitFor(() => expect(onChatEvent).toBeDefined());

    act(() => {
      onChatEvent?.(envelope("turn_start", { state: "running" }));
      onChatEvent?.(envelope("assistant_delta", { delta: "Here is the shortlist." }));
      onChatEvent?.(
        envelope("turn_end", {
          text: "Here is the shortlist.",
          state: "complete",
          run_budget: {
            ...EXHAUSTED_BUDGET,
            state: "finished",
            stopped_by: null,
            exhausted: [],
            remaining: { requested_tools: [], final_answer: true },
            continue_available: false,
          },
        }),
      );
    });

    expect(await screen.findByText("Here is the shortlist.")).toBeInTheDocument();
    expect(screen.queryByRole("note")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Continue this run" }),
    ).not.toBeInTheDocument();
  });

  it("still offers Continue after the UI is restarted and the run is restored", async () => {
    api.getSession.mockResolvedValue({
      id: "thread-alpha",
      title: "Budgeted run",
      messages: [
        { role: "user", content: "Build the shortlist." },
        { role: "assistant", content: "Three names so far." },
      ],
      events: [
        envelope("turn_start", { state: "running" }),
        envelope("assistant_delta", { delta: "Three names so far." }),
        envelope("turn_end", {
          text: "Three names so far.",
          state: "stopped",
          run_budget: EXHAUSTED_BUDGET,
        }),
      ],
    });

    render(<ChatPage sessionId="thread-alpha" />);

    const note = await screen.findByRole("note", {
      name: "Run stopped before finishing",
    });
    expect(note).toHaveTextContent("This run did not finish.");
    fireEvent.click(screen.getByRole("button", { name: "Continue this run" }));
    await waitFor(() =>
      expect(chatSend).toHaveBeenCalledWith(
        "Continue this run from where it stopped.",
        "thread-alpha",
      ),
    );
  });
});
