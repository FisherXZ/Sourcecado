import type { ToolCallMessagePart } from "@assistant-ui/react";
import { describe, expect, it } from "vitest";

import {
  answerHasStarted,
  formatElapsed,
  layerElapsedMs,
  layerState,
  receiptState,
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
    [0, true, "<1s"],
    [900, true, "<1s"],
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

  it("shows a pending approval as waiting whether or not the turn is live", () => {
    const retried = tool({ result: undefined, approval: { id: "a-2" } });
    expect(stepState(retried, "running")).toBe("waiting");
    expect(stepState(retried, "waiting-approval")).toBe("waiting");
    expect(stepState(retried, "complete")).toBe("waiting");
    expect(stepState(retried, "stopped")).toBe("interrupted");
  });

  it("does not call a step failed before its denial arrives", () => {
    // The failed result reaches the browser before the decision does.
    const inFlight = tool({
      result: { error: "denied" },
      isError: true,
      approval: { id: "a-1" },
    });
    expect(stepState(inFlight, "running")).toBe("waiting");
    expect(
      stepState({ ...inFlight, approval: { id: "a-1", approved: false } }, "running"),
    ).toBe("denied");
  });

  it("does not wait on an approval that was resolved somewhere else", () => {
    const elsewhere = tool({
      result: { error: "approval resolved elsewhere" },
      isError: true,
      approval: { id: "a-1" },
      providerMetadata: { sourcecado: { approvalState: "resolved-elsewhere" } },
    });
    expect(stepState(elsewhere, "running")).toBe("failed");
  });

  it("separates a refusal with no approval record from an ordinary failure", () => {
    const failure = (cls: string) => ({
      sourcecado: { failure: { class: cls, source: "Board", summary: "x" } },
    });
    const refused = tool({
      isError: true,
      result: { error: "no" },
      providerMetadata: failure("permission"),
    });
    expect(stepState(refused, "complete")).toBe("blocked");
    expect(
      stepState({ ...refused, providerMetadata: failure("timeout_network") }, "complete"),
    ).toBe("failed");
    expect(
      stepState({ ...refused, approval: { id: "a", approved: true } }, "complete"),
    ).toBe("failed");
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
    [["blocked", "blocked"], "blocked"],
    [["blocked", "denied"], "blocked"],
  ])("reduces %j to %s", (states, expected) => {
    expect(layerState(states)).toBe(expected);
  });
});

describe("receiptState", () => {
  it("shows a working mark instead of a check while the turn still works", () => {
    expect(receiptState("done", "running", false)).toBe("running");
    expect(receiptState("done", "running", true)).toBe("done");
    expect(receiptState("done", "complete", false)).toBe("done");
  });

  it("says Interrupted after a stop even when every step finished", () => {
    expect(receiptState("done", "cancelled", false)).toBe("interrupted");
    expect(receiptState("done", "stopped", true)).toBe("interrupted");
  });

  it("leaves every other outcome as it is", () => {
    expect(receiptState("failed", "running", false)).toBe("failed");
    expect(receiptState("unknown", "stopped", false)).toBe("unknown");
  });
});

describe("answerHasStarted", () => {
  const step = { type: "tool-call" } as const;
  const text = (value: string) => ({ type: "text", text: value }) as const;

  it("is false for a lead-in sentence between steps", () => {
    expect(answerHasStarted([text("Now I will query the Board."), step])).toBe(false);
    expect(answerHasStarted([step, text("Now I will query the Board.")])).toBe(false);
    expect(answerHasStarted([step, text("\n\n  ")])).toBe(false);
  });

  it("is true once long or multi-line text follows the last step", () => {
    expect(answerHasStarted([step, text("## Final answer\n\n- one")])).toBe(true);
    expect(answerHasStarted([step, text("word ".repeat(30))])).toBe(true);
  });

  it("is false when there is no step", () => {
    expect(answerHasStarted([text("word ".repeat(30))])).toBe(false);
  });

  it("ignores text before the last step however long it is", () => {
    expect(answerHasStarted([text("word ".repeat(30)), step])).toBe(false);
  });
});

describe("elapsed time", () => {
  it("gives a stopped step no duration instead of counting up", () => {
    const stopped = tool({ result: undefined, timing: { startedAt: 1000 } });
    expect(stepElapsedMs(stopped, "interrupted", 90_000)).toBeNull();
    expect(stepElapsedMs(stopped, "running", 4000)).toBe(3000);
  });

  it("counts the time steps ran, not the model time between them", () => {
    const tools = [
      tool({ timing: { startedAt: 0, completedAt: 100 } }),
      tool({ toolCallId: "b", timing: { startedAt: 8000, completedAt: 8100 } }),
    ];
    expect(layerElapsedMs(tools, ["done", "done"], 99_000)).toBe(200);
  });

  it("spans the first start to the last end for steps that overlap", () => {
    const tools = [
      tool({ timing: { startedAt: 1000, completedAt: 2000 } }),
      tool({ toolCallId: "b", timing: { startedAt: 1500, completedAt: 5000 } }),
    ];
    expect(layerElapsedMs(tools, ["done", "done"], 99_000)).toBe(4000);
    const running = [
      tools[0]!,
      tool({ toolCallId: "b", result: undefined, timing: { startedAt: 1500 } }),
    ];
    expect(layerElapsedMs(running, ["done", "running"], 9000)).toBe(8000);
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
