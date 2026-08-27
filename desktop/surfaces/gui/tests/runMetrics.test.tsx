import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RunMetrics } from "../src/chat/RunMetrics";

const base = {
  run_id: "run-1",
  status: "running" as const,
  input_tokens: 80,
  output_tokens: 20,
  total_tokens: 100,
  cache_hit_input_tokens: 30,
  cache_miss_input_tokens: 50,
  cache_write_input_tokens: 5,
  reasoning_tokens: 4,
  current_context_tokens: 80,
  context_window_tokens: 1_000_000,
  context_use_ratio: 0.00008,
  elapsed_ms: 1250,
  estimated_cost_usd: 0.00003925875,
  retry_count: 0,
  compaction_count: 0,
};

describe("RunMetrics", () => {
  it("keeps small known context and cost values visible instead of rounding to zero", () => {
    render(<RunMetrics metrics={base} />);

    const metrics = screen.getByRole("region", { name: "Current run metrics" });
    expect(metrics).toHaveTextContent("Context <1%");
    expect(metrics).toHaveTextContent("$0.000039 est.");
  });

  it("states when context capacity and cost are unavailable", () => {
    render(
      <RunMetrics
        metrics={{
          ...base,
          context_window_tokens: null,
          context_use_ratio: null,
          estimated_cost_usd: null,
        }}
      />,
    );

    const metrics = screen.getByRole("region", { name: "Current run metrics" });
    expect(metrics).toHaveTextContent("Context 80 tokens");
    expect(metrics).toHaveTextContent("Cost —");
  });
});
