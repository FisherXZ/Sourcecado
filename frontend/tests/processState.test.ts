import type { ToolCallMessagePart } from "@assistant-ui/react";
import { describe, expect, it } from "vitest";

import {
  formatElapsed,
  layerElapsedMs,
  layerState,
  stepElapsedMs,
  stepState,
  stepsSegment,
  type StepState,
} from "../src/chat/processState";

const tool = (
  overrides: Partial<ToolCallMessagePart> = {},
): ToolCallMessagePart => ({
  type: "tool-call",
  toolCallId: "call-1",
  toolName: "drive_search",
  args: {},
  argsText: "{}",
  result: {},
  ...overrides,
});

describe("formatElapsed", () => {
  it.each([
    [0, true, "0s"],
    [2900, true, "2s"],
    [380, false, "<1s"],
    [600, false, "1s"],
    [3000, false, "3s"],
    [65_000, false, "1m 05s"],
  ] as const)("shows %i ms (live: %s) as %s", (ms, live, text) => {
    expect(formatElapsed(ms, live)).toBe(text);
  });
});

describe("stepState", () => {
  it("lets a denial win over the failed result that comes with it", () => {
    expect(
      stepState(
        tool({
          isError: true,
          result: { error: "denied" },
          approval: { id: "a", approved: false },
        }),
        "complete",
      ),
    ).toBe("denied");
  });

  it("treats a step with no result as unknown once the turn is over, and as running while it is not", () => {
    const open = tool({ result: undefined });
    expect(stepState(open, "running")).toBe("running");
    expect(stepState(open, "complete")).toBe("unknown");
    expect(stepState(open, "held")).toBe("unknown");
    expect(stepState(open, "stopped")).toBe("interrupted");
  });

  it("keeps a partial result out of success", () => {
    expect(stepState(tool({ result: { partial: true } }), "complete")).toBe("partial");
  });
});

describe("layerState", () => {
  it.each<[readonly StepState[], StepState]>([
    [["done", "done"], "done"],
    [["done", "running"], "running"],
    [["done", "unknown"], "unknown"],
    [["done", "interrupted"], "interrupted"],
    [["done", "failed"], "partial"],
    [["failed", "failed"], "failed"],
    [["failed", "denied"], "failed"],
    [["denied", "denied"], "denied"],
  ])("reduces %j to %s", (states, expected) => {
    expect(layerState(states)).toBe(expected);
  });
});

describe("elapsed time", () => {
  it("gives a stopped step no duration instead of counting up", () => {
    const stopped = tool({ result: undefined, timing: { startedAt: 1000 } });
    expect(stepElapsedMs(stopped, "interrupted", 90_000)).toBeNull();
    expect(stepElapsedMs(stopped, "running", 4000)).toBe(3000);
  });

  it("spans the first start to the last end for the layer", () => {
    const tools = [
      tool({ timing: { startedAt: 1000, completedAt: 2000 } }),
      tool({ toolCallId: "b", timing: { startedAt: 1500, completedAt: 5000 } }),
    ];
    expect(layerElapsedMs(tools, ["done", "done"], 99_000)).toBe(4000);
    expect(layerElapsedMs(tools, ["done", "running"], 9000)).toBe(8000);
  });
});

describe("stepsSegment", () => {
  it("counts sources and actions, in the running form while a step runs", () => {
    const source = tool();
    const action = tool({ toolCallId: "b", toolName: "gmail_draft" });
    expect(stepsSegment([source, source], false)).toBe("Used 2 sources");
    expect(stepsSegment([action], false)).toBe("Ran 1 action");
    expect(stepsSegment([source, action], false)).toBe("Used 1 source, ran 1 action");
    expect(stepsSegment([source, source], true)).toBe("Using 2 sources");
  });
});
