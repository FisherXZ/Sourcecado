import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { CurrentRunMetrics } from "../src/api";
import type { SourcecadoStructuredMessage } from "../src/chat/messageAdapter";
import type { RunBudgetStatus } from "../src/chat/protocol";
import { pickUsage, UsageLine, type UsageSource } from "../src/chat/UsageLine";

const metrics: CurrentRunMetrics = {
  run_id: "run-1",
  status: "success",
  input_tokens: 5200,
  output_tokens: 190,
  total_tokens: 5390,
  cache_hit_input_tokens: 0,
  cache_miss_input_tokens: 5200,
  cache_write_input_tokens: 0,
  reasoning_tokens: 33,
  current_context_tokens: 34_000,
  context_window_tokens: 100_000,
  context_use_ratio: 0.34,
  elapsed_ms: 12_400,
  estimated_cost_usd: 0.04,
  retry_count: 0,
  compaction_count: 0,
};

const unreported: CurrentRunMetrics = {
  ...metrics,
  input_tokens: null,
  output_tokens: null,
  total_tokens: null,
  reasoning_tokens: null,
  current_context_tokens: null,
  context_window_tokens: null,
  context_use_ratio: null,
  elapsed_ms: null,
  estimated_cost_usd: null,
};

const budget: RunBudgetStatus = {
  state: "finished",
  stopped_by: null,
  exhausted: [],
  repeats: 0,
  consumed: {
    model_turns: 6,
    tool_calls: 12,
    elapsed_seconds: 12,
    input_tokens: 5200,
    output_tokens: 190,
    estimated_cost_usd: 0.04,
  },
  limits: {
    model_turns: 40,
    tool_calls: 120,
    elapsed_seconds: 900,
    input_tokens: 2_000_000,
    output_tokens: 200_000,
    estimated_cost_usd: 2,
  },
  warning: null,
  completed: [],
  remaining: { requested_tools: [], final_answer: true },
  unpriced_requests: 0,
  unmeasured_requests: 0,
  continue_available: false,
};

function last(over: Partial<UsageSource> = {}): UsageSource {
  return { metrics, budget, live: false, ...over };
}

function line(name = "Usage, last run") {
  return screen.getByRole("group", { name });
}

describe("UsageLine values", () => {
  it("shows context share, estimated cost and steps against the limit, labeled as the last run", () => {
    render(<UsageLine source={last()} />);

    const usage = line();
    expect(usage).toHaveTextContent("Last run");
    expect(usage).toHaveTextContent("34%");
    expect(usage).toHaveTextContent("$0.04 est.");
    expect(usage).toHaveTextContent("6/40 steps");
    expect(
      within(usage).getByRole("meter", { name: "Context window used" }),
    ).toHaveAttribute("aria-valuenow", "34");
    // One icon per value, drawn at 13px and hidden from assistive tech.
    const icons = usage.querySelectorAll("svg.sourcecado-usage-icon");
    expect(icons).toHaveLength(3);
    for (const icon of icons) {
      expect(icon).toHaveAttribute("width", "13");
      expect(icon).toHaveAttribute("aria-hidden", "true");
    }
  });

  it("keeps a tiny known context share and cost visible instead of rounding to zero", () => {
    render(
      <UsageLine
        source={last({
          metrics: {
            ...metrics,
            context_use_ratio: 0.00008,
            estimated_cost_usd: 0.00003925875,
          },
        })}
      />,
    );

    expect(line()).toHaveTextContent("<1%");
    expect(line()).toHaveTextContent("$0.000039 est.");
  });

  it("falls back to the context token count when the window size is not known", () => {
    render(
      <UsageLine
        source={last({
          metrics: {
            ...metrics,
            context_window_tokens: null,
            context_use_ratio: null,
            current_context_tokens: 80,
          },
        })}
      />,
    );

    expect(line()).toHaveTextContent("80 tokens");
    expect(screen.queryByRole("meter")).not.toBeInTheDocument();
  });

  it("says unavailable for every value the run did not report, and never shows zero", () => {
    render(<UsageLine source={last({ metrics: unreported, budget: undefined })} />);

    const usage = line();
    expect(usage).toHaveTextContent("Context window unavailable");
    expect(usage).toHaveTextContent("Estimated cost unavailable");
    expect(usage).toHaveTextContent("Steps unavailable");
    expect(usage).not.toHaveTextContent("0%");
    expect(usage).not.toHaveTextContent("$0");
    expect(usage).not.toHaveTextContent("0/");
    expect(screen.queryByRole("meter")).not.toBeInTheDocument();
    // Nothing was reported, so there is nothing to call the last run's value.
    expect(usage).not.toHaveTextContent("Last run");
  });

  it("treats missing telemetry as unavailable, not as a zero run", () => {
    render(<UsageLine source={last({ metrics: null })} />);

    expect(line()).toHaveTextContent("Context window unavailable");
    expect(line()).toHaveTextContent("Estimated cost unavailable");
    expect(line()).toHaveTextContent("6/40 steps");
  });

  it("does not label a live run's numbers as the last run", () => {
    render(<UsageLine source={last({ live: true })} />);

    const usage = screen.getByRole("group", { name: "Usage, this run" });
    expect(usage).not.toHaveTextContent("Last run");
    expect(usage.closest(".sourcecado-usage")).toHaveAttribute("data-scope", "live");
  });
});

describe("UsageLine chips", () => {
  it("shows retries and compactions only when they are above zero", () => {
    const { rerender } = render(<UsageLine source={last()} />);
    expect(line()).not.toHaveTextContent(/retr/i);
    expect(line()).not.toHaveTextContent(/compaction/i);

    rerender(
      <UsageLine
        source={last({
          metrics: { ...metrics, retry_count: 1, compaction_count: 2 },
        })}
      />,
    );
    expect(line()).toHaveTextContent("1 retry");
    expect(line()).toHaveTextContent("2 compactions");
  });
});

describe("UsageLine breakdown", () => {
  it("opens on hover with tokens, reasoning tokens, time and the budget limits", () => {
    const { container } = render(<UsageLine source={last()} />);
    const detail = screen.getByRole("tooltip", { hidden: true });
    expect(detail).not.toBeVisible();

    fireEvent.mouseEnter(container.querySelector(".sourcecado-usage")!);

    expect(detail).toBeVisible();
    for (const text of [
      "Last run",
      "34%, 34,000 of 100,000 tokens",
      "$0.04 est. of $2.00",
      "6 of 40",
      "12 of 120",
      "12s of 15m 00s",
      "5,200",
      "190",
      "33",
      "5,390",
    ]) {
      expect(detail).toHaveTextContent(text);
    }

    fireEvent.mouseLeave(container.querySelector(".sourcecado-usage")!);
    expect(detail).not.toBeVisible();
  });

  it("opens on keyboard focus, is described by the line, and closes on Escape", () => {
    render(<UsageLine source={last()} />);
    const usage = line();
    const detail = screen.getByRole("tooltip", { hidden: true });
    expect(usage).toHaveAttribute("tabindex", "0");
    expect(usage).toHaveAttribute("aria-describedby", detail.id);

    fireEvent.focus(usage);
    expect(detail).toBeVisible();

    fireEvent.keyDown(usage, { key: "Escape" });
    expect(detail).not.toBeVisible();

    fireEvent.blur(usage);
    fireEvent.focus(usage);
    expect(detail).toBeVisible();
  });

  it("says a token count is unavailable when the provider did not report it", () => {
    render(
      <UsageLine
        source={last({
          metrics: { ...metrics, reasoning_tokens: null, total_tokens: null },
        })}
      />,
    );
    fireEvent.focus(line());

    const detail = screen.getByRole("tooltip");
    const rows = Object.fromEntries(
      [...detail.querySelectorAll("dt")].map((term) => [
        term.textContent,
        term.nextElementSibling?.textContent,
      ]),
    );
    expect(rows["Reasoning tokens"]).toBe("unavailable");
    expect(rows["Total tokens"]).toBe("unavailable");
    expect(rows["Input tokens"]).toBe("5,200");
  });

  it("lists retries and compactions in the breakdown only when above zero", () => {
    const { rerender } = render(<UsageLine source={last()} />);
    expect(screen.getByRole("tooltip", { hidden: true })).not.toHaveTextContent(
      "Retries",
    );

    rerender(
      <UsageLine
        source={last({ metrics: { ...metrics, retry_count: 3, compaction_count: 1 } })}
      />,
    );
    const detail = screen.getByRole("tooltip", { hidden: true });
    expect(detail).toHaveTextContent("Retries");
    expect(detail).toHaveTextContent("Compactions");
  });

  it("warns that the cost is a minimum when some requests had no price", () => {
    render(
      <UsageLine source={last({ budget: { ...budget, unpriced_requests: 2 } })} />,
    );

    expect(screen.getByRole("tooltip", { hidden: true })).toHaveTextContent(
      "2 requests have no price, so the cost is a minimum.",
    );
  });

  it("never calls context share account quota or plan usage", () => {
    const { container } = render(<UsageLine source={last()} />);
    fireEvent.focus(line());

    expect(container.textContent).toMatch(/share of the model's window/);
    expect(container.textContent).not.toMatch(/quota|plan usage|account usage|\bplan\b/i);
  });
});

function assistant(
  state: SourcecadoStructuredMessage["state"],
  runBudget?: RunBudgetStatus,
  parts: SourcecadoStructuredMessage["parts"] = [],
): SourcecadoStructuredMessage {
  return { id: `a-${state}`, role: "assistant", state, parts, ...(runBudget ? { runBudget } : {}) };
}

const user: SourcecadoStructuredMessage = {
  id: "u1",
  role: "user",
  state: "complete",
  parts: [],
};

describe("pickUsage", () => {
  const live = { ...metrics, status: "running" as const };

  it("shows nothing for an empty thread", () => {
    expect(pickUsage([], null, false)).toBeNull();
  });

  it("describes the finished run after the run ends", () => {
    const picked = pickUsage([user, assistant("complete", budget)], metrics, false);

    expect(picked).toEqual({ metrics, budget, live: false });
  });

  it("drops the previous run's telemetry and steps until the new run reports its own", () => {
    // The new run has not announced itself yet: the latest message is the old run.
    const early = pickUsage(
      [user, assistant("complete", budget), user],
      metrics,
      true,
    );
    expect(early).toEqual({ metrics: null, budget: undefined, live: true });

    const started = pickUsage(
      [user, assistant("complete", budget), user, assistant("running")],
      metrics,
      true,
    );
    expect(started).toEqual({ metrics: null, budget: undefined, live: true });
  });

  it("uses the live run's telemetry and budget while it works", () => {
    const liveBudget = { ...budget, state: "running" as const };
    const picked = pickUsage(
      [user, assistant("running", liveBudget)],
      live,
      true,
    );

    expect(picked).toEqual({ metrics: live, budget: liveBudget, live: true });
  });

  it("skips a compaction notice when it looks for the run's budget", () => {
    const notice = assistant("partial", undefined, [
      { type: "notice", id: "n1", code: "compacted", message: "x", recoverable: true },
    ]);
    const picked = pickUsage(
      [user, assistant("complete", budget), notice],
      metrics,
      false,
    );

    expect(picked?.budget).toBe(budget);
  });
});
