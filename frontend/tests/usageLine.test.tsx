import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

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
  return {
    metrics,
    budget,
    live: false,
    compactions: 0,
    settling: false,
    partial: false,
    ...over,
  };
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
          metrics: { ...metrics, retry_count: 1 },
          compactions: 2,
        })}
      />,
    );
    expect(line()).toHaveTextContent("1 retry");
    expect(line()).toHaveTextContent("2 compactions");
  });

  it("counts compactions from the thread's receipts, not from the telemetry count", () => {
    const { rerender } = render(
      <UsageLine
        source={last({ metrics: { ...metrics, compaction_count: 5 } })}
      />,
    );
    expect(line()).not.toHaveTextContent(/compaction/i);

    rerender(<UsageLine source={last({ compactions: 1 })} />);
    expect(line()).toHaveTextContent("1 compaction");
    expect(line()).not.toHaveTextContent("1 compactions");
  });

  it("keeps the last-run label when only a chip has anything to report", () => {
    render(
      <UsageLine
        source={last({
          metrics: { ...unreported, retry_count: 2 },
          budget: undefined,
        })}
      />,
    );

    expect(line()).toHaveTextContent("Last run");
    expect(line()).toHaveTextContent("2 retries");
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
        source={last({
          metrics: { ...metrics, retry_count: 3 },
          compactions: 1,
        })}
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
  const idle = { live: false, compactions: 0, settling: false, partial: false };
  const working = { live: true, compactions: 0, settling: false, partial: false };

  it("shows nothing for an empty thread", () => {
    expect(pickUsage([], null, false)).toBeNull();
  });

  it("describes the finished run after the run ends", () => {
    const picked = pickUsage([user, assistant("complete", budget)], metrics, false);

    expect(picked).toEqual({ metrics, budget, ...idle });
  });

  it("drops the previous run's telemetry and steps until the new run reports its own", () => {
    // The new run has not announced itself yet: the latest message is the old run.
    const early = pickUsage(
      [user, assistant("complete", budget), user],
      metrics,
      true,
    );
    expect(early).toEqual({ metrics: null, budget: undefined, ...working });

    const started = pickUsage(
      [user, assistant("complete", budget), user, assistant("running")],
      metrics,
      true,
    );
    expect(started).toEqual({ metrics: null, budget: undefined, ...working });
  });

  it("uses the live run's telemetry and budget while it works", () => {
    const liveBudget = { ...budget, state: "running" as const };
    const picked = pickUsage(
      [user, assistant("running", liveBudget)],
      live,
      true,
    );

    expect(picked).toEqual({ metrics: live, budget: liveBudget, ...working });
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

  const compacted = (state: SourcecadoStructuredMessage["state"] = "complete") => ({
    ...assistant(state, budget),
    compacted: true,
  });

  it("counts the compaction the latest run reported", () => {
    const picked = pickUsage([user, compacted()], metrics, false);

    expect(picked?.compactions).toBe(1);
  });

  it("does not carry an earlier run's compaction into a run that compacted nothing", () => {
    const picked = pickUsage(
      [user, compacted(), user, assistant("complete", budget)],
      metrics,
      false,
    );

    expect(picked?.compactions).toBe(0);
  });

  it("counts none while a run works", () => {
    const picked = pickUsage(
      [user, compacted(), user, assistant("running")],
      metrics,
      true,
    );

    expect(picked?.compactions).toBe(0);
  });

  it("waits for the final reading before it calls a finished run the last run", () => {
    const messages = [user, assistant("complete", budget)];

    expect(pickUsage(messages, metrics, false, "pending")).toMatchObject({
      live: false,
      settling: true,
      partial: false,
    });
    expect(pickUsage(messages, metrics, false, "done")).toMatchObject({
      settling: false,
      partial: false,
    });
  });

  it("marks values partial only when the final reading failed and something was read before", () => {
    const messages = [user, assistant("complete", budget)];

    expect(pickUsage(messages, metrics, false, "failed")?.partial).toBe(true);
    expect(pickUsage(messages, null, false, "failed")?.partial).toBe(false);
    expect(pickUsage(messages, live, true, "failed")?.partial).toBe(false);
  });
});

describe("UsageLine final reading", () => {
  it("keeps the this-run label until the final reading is in", () => {
    const { rerender } = render(<UsageLine source={last({ settling: true })} />);

    const settling = screen.getByRole("group", { name: "Usage, this run" });
    expect(settling).not.toHaveTextContent("Last run");
    expect(settling.closest(".sourcecado-usage")).toHaveAttribute("data-scope", "live");

    rerender(<UsageLine source={last()} />);
    expect(line()).toHaveTextContent("Last run");
  });

  it("says the values may be incomplete when the final reading failed", () => {
    render(<UsageLine source={last({ partial: true })} />);

    const usage = screen.getByRole("group", { name: "Usage, last run, may be incomplete" });
    expect(usage).toHaveTextContent("Last run, may be incomplete");
    fireEvent.focus(usage);
    expect(screen.getByRole("tooltip")).toHaveTextContent(
      "The final reading failed. These values are from before the run ended.",
    );
  });
});

describe("UsageLine Escape", () => {
  it("closes a breakdown opened by hover while focus is elsewhere, and leaves focus alone", () => {
    const { container } = render(
      <>
        <UsageLine source={last()} />
        <textarea aria-label="Message" />
      </>,
    );
    const box = screen.getByLabelText("Message");
    box.focus();
    const wrapper = container.querySelector(".sourcecado-usage")!;
    const detail = screen.getByRole("tooltip", { hidden: true });
    fireEvent.mouseEnter(wrapper);
    expect(detail).toBeVisible();

    fireEvent.keyDown(box, { key: "Escape" });

    expect(detail).not.toBeVisible();
    expect(box).toHaveFocus();

    // The pointer coming back opens it again.
    fireEvent.mouseLeave(wrapper);
    fireEvent.mouseEnter(wrapper);
    expect(detail).toBeVisible();
  });

  it("takes the first Escape itself, so the composer does not stop the run", () => {
    const composer = vi.fn();
    const radixStyle = vi.fn();
    document.addEventListener("keydown", radixStyle, true);
    const { container } = render(
      <>
        <UsageLine source={last()} />
        <textarea aria-label="Message" onKeyDown={composer} />
      </>,
    );
    const box = screen.getByLabelText("Message");
    const wrapper = container.querySelector(".sourcecado-usage")!;

    fireEvent.keyDown(box, { key: "Escape" });
    expect(composer).toHaveBeenCalledTimes(1);

    fireEvent.mouseEnter(wrapper);
    composer.mockClear();
    radixStyle.mockClear();
    fireEvent.keyDown(box, { key: "Escape" });
    expect(composer).not.toHaveBeenCalled();
    expect(radixStyle).not.toHaveBeenCalled();

    // With the breakdown closed, the next Escape reaches the composer again.
    fireEvent.keyDown(box, { key: "Escape" });
    expect(composer).toHaveBeenCalledTimes(1);
    document.removeEventListener("keydown", radixStyle, true);
  });
});
