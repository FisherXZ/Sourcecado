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

  it("warns before a budget is exhausted and leaves the numbers to the usage line", async () => {
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

    // The warning is a sentence. The spend moved to the usage line.
    const activity = document.querySelector(".sourcecado-activity");
    expect(activity).toHaveTextContent("Searching Drive");
    expect(activity).toHaveTextContent("Running");
    expect(
      screen.queryByRole("region", { name: "Run budget" }),
    ).not.toBeInTheDocument();
    expect(await screen.findByRole("group", { name: "Usage, this run" })).toHaveTextContent(
      "33/40 steps",
    );
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

    const continueButton = screen.getByRole("button", { name: "Continue this run" });
    expect(continueButton.querySelector("svg")).not.toBeNull();
    fireEvent.click(continueButton);

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

  it("keeps the usage line after the run ends and labels it as the last run", async () => {
    const telemetry = {
      run_id: "run-budget",
      status: "success",
      input_tokens: 5200,
      output_tokens: 190,
      total_tokens: 5390,
      cache_hit_input_tokens: null,
      cache_miss_input_tokens: null,
      cache_write_input_tokens: null,
      reasoning_tokens: null,
      current_context_tokens: 34000,
      context_window_tokens: 100000,
      context_use_ratio: 0.34,
      elapsed_ms: 12400,
      estimated_cost_usd: 0.04,
      retry_count: 1,
      compaction_count: 0,
    };
    api.getCurrentRunTelemetry.mockResolvedValue({
      version: 1,
      session_id: "thread-alpha",
      current_run: telemetry,
    });
    render(<ChatPage sessionId="thread-alpha" />);
    await waitFor(() => expect(onChatEvent).toBeDefined());
    // An empty thread has no run to describe.
    expect(screen.queryByRole("group", { name: /^Usage/ })).not.toBeInTheDocument();

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
            consumed: { ...EXHAUSTED_BUDGET.consumed, model_turns: 6 },
            remaining: { requested_tools: [], final_answer: true },
            continue_available: false,
          },
        }),
      );
    });

    const usage = await screen.findByRole("group", { name: "Usage, last run" });
    await waitFor(() => expect(usage).toHaveTextContent("34%"));
    expect(usage).toHaveTextContent("Last run");
    expect(usage).toHaveTextContent("$0.04 est.");
    expect(usage).toHaveTextContent("6/40 steps");
    expect(usage).toHaveTextContent("1 retry");
    expect(screen.queryByRole("region", { name: "Run budget" })).not.toBeInTheDocument();
  });

  it("restores the steps of a past run when its telemetry is gone, without inventing the rest", async () => {
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

    const usage = await screen.findByRole("group", { name: "Usage, last run" });
    expect(usage).toHaveTextContent("40/40 steps");
    expect(usage).toHaveTextContent("Context window unavailable");
    expect(usage).toHaveTextContent("Estimated cost unavailable");
    expect(usage).not.toHaveTextContent("$0");
  });

  it("shows a compaction as a receipt in the thread and keeps the whole conversation", async () => {
    api.getSession.mockResolvedValue({
      id: "thread-alpha",
      title: "Long run",
      messages: [
        { role: "user", content: "First question of the day." },
        { role: "assistant", content: "First answer of the day." },
      ],
      events: [],
    });
    render(<ChatPage sessionId="thread-alpha" />);
    await waitFor(() => expect(onChatEvent).toBeDefined());

    act(() => {
      onChatEvent?.(envelope("turn_start", { state: "running" }));
      onChatEvent?.(envelope("assistant_delta", { delta: "Latest answer." }));
      onChatEvent?.(
        envelope("turn_end", {
          text: "Latest answer.",
          state: "complete",
          compaction: {
            generation: 1,
            summarized: true,
            compacted_messages: 42,
            retained_director_messages: 12,
            omitted_director_messages: 0,
            measurement: "provider",
            rejected_summaries: 0,
          },
        }),
      );
    });

    const receipt = await screen.findByRole("note");
    expect(receipt).toHaveTextContent("Context compacted");
    expect(receipt).toHaveTextContent("The 42 earliest messages are no longer sent to the model.");
    expect(receipt).toHaveTextContent("The full conversation is still saved on this machine.");
    // Compaction changes what the model is sent, not what the operator can read.
    expect(screen.getByText("First question of the day.")).toBeInTheDocument();
    expect(screen.getByText("First answer of the day.")).toBeInTheDocument();
    expect(screen.getByText("Latest answer.")).toBeInTheDocument();
  });

  describe("usage line telemetry", () => {
    const TELEMETRY = {
      run_id: "run-budget",
      status: "success",
      input_tokens: 5200,
      output_tokens: 190,
      total_tokens: 5390,
      cache_hit_input_tokens: null,
      cache_miss_input_tokens: null,
      cache_write_input_tokens: null,
      reasoning_tokens: null,
      current_context_tokens: 34000,
      context_window_tokens: 100000,
      context_use_ratio: 0.34,
      elapsed_ms: 12400,
      estimated_cost_usd: 0.04,
      retry_count: 0,
      compaction_count: 0,
    };
    const FINISHED = {
      ...EXHAUSTED_BUDGET,
      state: "finished",
      stopped_by: null,
      exhausted: [],
      consumed: { ...EXHAUSTED_BUDGET.consumed, model_turns: 6 },
      remaining: { requested_tools: [], final_answer: true },
      continue_available: false,
    };
    const reply = (over: Record<string, unknown>) => ({
      version: 1,
      session_id: "thread-alpha",
      current_run: { ...TELEMETRY, ...over },
    });
    const startRun = () => {
      act(() => {
        onChatEvent?.(envelope("turn_start", { state: "running" }));
        onChatEvent?.(envelope("assistant_delta", { delta: "Here is the shortlist." }));
      });
    };
    const endRun = (extra: Record<string, unknown> = {}) => {
      act(() => {
        onChatEvent?.(
          envelope("turn_end", {
            text: "Here is the shortlist.",
            state: "complete",
            run_budget: FINISHED,
            ...extra,
          }),
        );
      });
    };

    it("counts a compaction from the turn's own data when the telemetry reports none", async () => {
      api.getCurrentRunTelemetry.mockResolvedValue(reply({ compaction_count: 0 }));
      render(<ChatPage sessionId="thread-alpha" />);
      await waitFor(() => expect(onChatEvent).toBeDefined());

      startRun();
      endRun({
        compaction: {
          generation: 1,
          summarized: true,
          compacted_messages: 42,
          retained_director_messages: 12,
          omitted_director_messages: 0,
          measurement: "provider",
          rejected_summaries: 0,
        },
      });

      const usage = await screen.findByRole("group", { name: "Usage, last run" });
      expect(usage).toHaveTextContent("1 compaction");
      expect(await screen.findByRole("note")).toHaveTextContent("Context compacted");
    });

    it("says one compaction after a restore even when two runs of the thread compacted", async () => {
      const compaction = {
        generation: 1,
        summarized: true,
        compacted_messages: 5,
        retained_director_messages: 2,
        omitted_director_messages: 0,
        measurement: "provider",
        rejected_summaries: 0,
      };
      const run = (n: number) => {
        const ids = { message_id: `message-${n}`, run_id: `run-${n}`, part_id: `part-${n}` };
        return [
          { ...envelope("turn_start", { state: "running" }), ...ids, event_id: `start-${n}` },
          { ...envelope("assistant_delta", { delta: `Answer ${n}.` }), ...ids, event_id: `delta-${n}` },
          {
            ...envelope("turn_end", { text: `Answer ${n}.`, state: "complete", compaction }),
            ...ids,
            event_id: `end-${n}`,
          },
        ];
      };
      api.getSession.mockResolvedValue({
        id: "thread-alpha",
        title: "Long thread",
        messages: [
          { role: "user", content: "One.", message_id: "message-1" },
          { role: "assistant", content: "Answer 1.", message_id: "message-1" },
          { role: "user", content: "Two.", message_id: "message-2" },
          { role: "assistant", content: "Answer 2.", message_id: "message-2" },
        ],
        events: [...run(1), ...run(2)],
      });
      api.getCurrentRunTelemetry.mockResolvedValue(reply({ compaction_count: 0 }));

      render(<ChatPage sessionId="thread-alpha" />);

      const usage = await screen.findByRole("group", { name: "Usage, last run" });
      expect(usage).toHaveTextContent("1 compaction");
      expect(usage).not.toHaveTextContent("2 compactions");
      expect(await screen.findAllByRole("note")).toHaveLength(2);
    });

    it("shows no compaction chip for a run that did not compact", async () => {
      api.getCurrentRunTelemetry.mockResolvedValue(reply({ compaction_count: 0 }));
      render(<ChatPage sessionId="thread-alpha" />);
      await waitFor(() => expect(onChatEvent).toBeDefined());

      startRun();
      endRun();

      const usage = await screen.findByRole("group", { name: "Usage, last run" });
      expect(usage).not.toHaveTextContent(/compaction/i);
    });

    it("tries a failed final reading again and shows the final numbers when a retry works", async () => {
      let mode: "mid" | "fail-once" | "final" = "mid";
      api.getCurrentRunTelemetry.mockImplementation(async () => {
        if (mode === "mid") return reply({ status: "running", estimated_cost_usd: 0.0006 });
        if (mode === "fail-once") {
          mode = "final";
          throw new Error("telemetry unavailable");
        }
        return reply({});
      });
      render(<ChatPage sessionId="thread-alpha" />);
      await waitFor(() => expect(onChatEvent).toBeDefined());
      startRun();
      expect(await screen.findByRole("group", { name: "Usage, this run" })).toHaveTextContent("$0.0006 est.");

      mode = "fail-once";
      endRun();

      const usage = await screen.findByRole(
        "group",
        { name: "Usage, last run" },
        { timeout: 3000 },
      );
      expect(usage).toHaveTextContent("$0.04 est.");
      expect(usage).not.toHaveTextContent("incomplete");
    });

    it("tries the final reading three times, then marks the mid-run numbers as possibly incomplete", async () => {
      let failing = false;
      api.getCurrentRunTelemetry.mockImplementation(async () => {
        if (failing) throw new Error("telemetry unavailable");
        return reply({ status: "running", estimated_cost_usd: 0.0006 });
      });
      render(<ChatPage sessionId="thread-alpha" />);
      await waitFor(() => expect(onChatEvent).toBeDefined());
      startRun();
      await screen.findByRole("group", { name: "Usage, this run" });
      await waitFor(() =>
        expect(screen.getByRole("group", { name: "Usage, this run" })).toHaveTextContent("$0.0006 est."),
      );

      failing = true;
      const before = api.getCurrentRunTelemetry.mock.calls.length;
      endRun();

      const usage = await screen.findByRole(
        "group",
        { name: "Usage, last run, may be incomplete" },
        { timeout: 3000 },
      );
      expect(usage).toHaveTextContent("Last run, may be incomplete");
      expect(usage).toHaveTextContent("$0.0006 est.");
      expect(api.getCurrentRunTelemetry.mock.calls.length - before).toBe(3);

      // The retries are bounded: nothing reads again on its own.
      await new Promise((resolve) => setTimeout(resolve, 1200));
      expect(api.getCurrentRunTelemetry.mock.calls.length - before).toBe(3);
    });

    it("does not call a finished run the last run until its final reading is in", async () => {
      let release: (() => void) | undefined;
      let mode: "mid" | "final" = "mid";
      api.getCurrentRunTelemetry.mockImplementation(async () => {
        if (mode === "mid") return reply({ status: "running", estimated_cost_usd: 0.0006 });
        await new Promise<void>((resolve) => {
          release = resolve;
        });
        return reply({});
      });
      render(<ChatPage sessionId="thread-alpha" />);
      await waitFor(() => expect(onChatEvent).toBeDefined());
      startRun();
      await screen.findByRole("group", { name: "Usage, this run" });

      mode = "final";
      endRun();

      await waitFor(() => expect(release).toBeDefined());
      expect(screen.getByRole("group", { name: "Usage, this run" })).toBeInTheDocument();
      expect(screen.queryByRole("group", { name: "Usage, last run" })).not.toBeInTheDocument();

      act(() => release?.());
      const usage = await screen.findByRole("group", { name: "Usage, last run" });
      expect(usage).toHaveTextContent("$0.04 est.");
    });
  });
});
