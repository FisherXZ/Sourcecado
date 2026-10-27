import { act, fireEvent, render, screen, within } from "@testing-library/react";
import type { ToolCallMessagePart } from "@assistant-ui/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ActivityGroup } from "../src/chat/ActivityGroup";
import { Inspector, InspectorProvider } from "../src/chat/Inspector";
import { RecoveryProvider } from "../src/chat/recovery";

const tool = (
  overrides: Partial<ToolCallMessagePart> = {},
): ToolCallMessagePart => ({
  type: "tool-call",
  toolCallId: "call-drive",
  toolName: "drive_search",
  args: {},
  argsText: "{}",
  result: { files: [{ id: "a" }, { id: "b" }] },
  ...overrides,
});

const sources = (count: number): ToolCallMessagePart[] =>
  Array.from({ length: count }, (_, index) =>
    tool({ toolCallId: `call-${index}`, toolName: "drive_search" }),
  );

const row = (name: RegExp | string) => screen.getByRole("button", { name });

describe("ActivityGroup step rows", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("shows a running step with its active verb, argument summary and a Running state", () => {
    render(
      <ActivityGroup
        tools={[
          tool({
            toolName: "apollo_search_people",
            args: { personTitles: ["ML founder"], organizationName: "Berkeley" },
            result: undefined,
          }),
        ]}
        messageState="running"
      />,
    );

    const step = row(/Searching Apollo for ML founder at Berkeley/);
    expect(step).toHaveTextContent("Running");
    expect(step).toHaveAttribute("aria-expanded", "false");
  });

  it("shows the finished verb, argument summary and result count on a done step", () => {
    render(
      <ActivityGroup
        tools={[
          tool({
            toolName: "apollo_search_people",
            args: { personTitles: ["ML founder"], organizationName: "Berkeley" },
            result: { people: Array.from({ length: 12 }, (_, i) => ({ id: `p${i}` })) },
          }),
        ]}
        messageState="complete"
      />,
    );

    const step = row(/Searched Apollo for ML founder at Berkeley/);
    expect(step).toHaveTextContent("12 people");
    expect(step).toHaveTextContent("Completed");
  });

  it("falls back to a sensible row for a tool the registry does not know", () => {
    const { container } = render(
      <ActivityGroup
        tools={[tool({ toolName: "private_internal_tool", args: { secret: "x" } })]}
        messageState="complete"
      />,
    );

    expect(row(/Ran Sourcecado action/)).toBeInTheDocument();
    expect(container).not.toHaveTextContent("private_internal_tool");
  });

  it("gives every outcome its own word and icon, and only done gets the success mark", () => {
    const cases: ReadonlyArray<
      readonly [string, string, ToolCallMessagePart, string]
    > = [
      ["running", "Running", tool({ result: undefined }), "running"],
      [
        "waiting",
        "Needs approval",
        tool({ result: undefined, approval: { id: "approval-1" } }),
        "waiting-approval",
      ],
      ["done", "Completed", tool(), "complete"],
      ["partial", "Partial", tool({ result: { partial: true } }), "complete"],
      ["failed", "Failed", tool({ result: { error: "x" }, isError: true }), "complete"],
      [
        "denied",
        "Denied",
        tool({
          result: undefined,
          approval: { id: "approval-1", approved: false },
        }),
        "complete",
      ],
      [
        "interrupted",
        "Interrupted",
        tool({ result: undefined }),
        "interrupted",
      ],
      ["unknown", "Outcome unknown", tool({ result: undefined }), "held"],
    ];
    for (const [state, word, part, messageState] of cases) {
      const { container, unmount } = render(
        <ActivityGroup tools={[part]} messageState={messageState} />,
      );
      const step = container.querySelector(".sourcecado-step-toggle");
      expect(step, state).toHaveTextContent(word);
      expect(
        container.querySelector(`.sourcecado-status-icon-${state}`),
        state,
      ).not.toBeNull();
      unmount();
    }
  });

  it("shows one Denied receipt for a denied step, never Denied and then Failed", () => {
    // A denied approval also arrives as a failed tool result with a failure.
    const { container } = render(
      <ActivityGroup
        tools={[
          tool({
            toolName: "gmail_send",
            result: { error: "approval denied" },
            isError: true,
            approval: { id: "approval-1", approved: false },
            providerMetadata: {
              sourcecado: {
                failure: {
                  class: "permission",
                  connector_id: "gmail",
                  source: "Gmail",
                  retry_safe: false,
                  idempotent: false,
                  summary: "Gmail was not allowed to complete this action.",
                  repair_route: null,
                  detail: "approval denied",
                  call_id: "call-drive",
                  run_id: "run-1",
                  session_id: "thread-1",
                  state: "failed",
                },
              },
            },
          }),
        ]}
        messageState="complete"
      />,
    );

    expect(row(/Denied/)).toBeInTheDocument();
    expect(container).not.toHaveTextContent("Failed");
    fireEvent.click(row(/Denied/));
    expect(screen.getByText("Denied. This step did not run.")).toBeInTheDocument();
    expect(container).not.toHaveTextContent("Failed");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Retry failed step" }),
    ).not.toBeInTheDocument();
  });

  it("keeps a finished turn's step with no recorded result as an unknown outcome, not Running", () => {
    render(
      <ActivityGroup
        tools={[tool({ result: undefined })]}
        messageState="complete"
      />,
    );

    const step = row(/Outcome unknown/);
    expect(step).not.toHaveTextContent("Running");
    // An unknown outcome opens itself: the operator should not have to hunt for it.
    expect(
      screen.getByText(/No result was recorded\. The step may have run\./),
    ).toBeInTheDocument();
  });

  it("opens the outcome inline from the row and moves Inspect into it as an icon button", () => {
    render(
      <InspectorProvider threadId="thread-1">
        <ActivityGroup
          tools={[
            tool({
              args: { query: "Q3" },
              result: { files: [{ id: "doc-1" }], rawMime: "PRIVATE_RAW_PAYLOAD" },
              timing: { startedAt: 1_000_000, completedAt: 1_002_000 },
            }),
          ]}
          messageState="complete"
        />
        <Inspector />
      </InspectorProvider>,
    );

    expect(
      screen.queryByRole("button", { name: "Inspect Searched Drive" }),
    ).not.toBeInTheDocument();
    fireEvent.click(row(/Searched Drive for “Q3”/));

    expect(screen.getByText("Took 2s")).toBeInTheDocument();
    // Raw output stays in the Inspector, not in the thread.
    expect(document.body).not.toHaveTextContent("PRIVATE_RAW_PAYLOAD");
    const inspect = screen.getByRole("button", { name: "Inspect Searched Drive" });
    expect(inspect).toHaveTextContent("");
    expect(inspect.querySelector("svg")).not.toBeNull();

    fireEvent.click(inspect);
    expect(screen.getByRole("complementary", { name: "Inspector" })).toHaveTextContent(
      "PRIVATE_RAW_PAYLOAD",
    );
  });

  it("keeps a failed step's summary and recovery actions reachable without a second click", () => {
    const act = vi.fn();
    render(
      <RecoveryProvider onAction={act}>
        <ActivityGroup
          tools={[
            tool({
              result: { error: "PRIVATE_TRANSPORT_ERROR" },
              isError: true,
              providerMetadata: {
                sourcecado: {
                  failure: {
                    class: "timeout_network",
                    connector_id: "drive",
                    source: "Drive",
                    retry_safe: true,
                    idempotent: true,
                    summary: "Drive could not be reached. Retry is safe.",
                    repair_route: null,
                    detail: "PRIVATE_TRANSPORT_ERROR",
                    call_id: "call-drive",
                    run_id: "run-1",
                    session_id: "thread-1",
                    state: "failed",
                  },
                },
              },
            }),
          ]}
          messageState="complete"
        />
      </RecoveryProvider>,
    );

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Drive could not be reached. Retry is safe.",
    );
    expect(document.body).not.toHaveTextContent("PRIVATE_TRANSPORT_ERROR");
    fireEvent.click(screen.getByRole("button", { name: "Retry failed step" }));
    expect(act).toHaveBeenCalledWith("retry", expect.objectContaining({ source: "Drive" }));
  });

  it("shows a live ticking elapsed time on a running step, then the real duration after it ends", () => {
    vi.stubGlobal("matchMedia", (query: string) => ({
      matches: false,
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
    }));
    vi.useFakeTimers();
    const startedAt = Date.now();

    const { rerender } = render(
      <ActivityGroup
        tools={[tool({ result: undefined, timing: { startedAt } })]}
        messageState="running"
      />,
    );
    expect(row(/Searching Drive/)).toHaveTextContent("0s");

    act(() => {
      vi.advanceTimersByTime(3000);
    });
    expect(row(/Searching Drive/)).toHaveTextContent("3s");

    rerender(
      <ActivityGroup
        tools={[tool({ timing: { startedAt, completedAt: startedAt + 3000 } })]}
        messageState="complete"
      />,
    );
    fireEvent.click(row(/Searched Drive/));
    expect(screen.getByText("Took 3s")).toBeInTheDocument();

    act(() => {
      vi.advanceTimersByTime(10000);
    });
    expect(screen.getByText("Took 3s")).toBeInTheDocument();
  });

  it("never reads 0s for a step that finished in under a second", () => {
    const startedAt = 1_000_000;
    render(
      <ActivityGroup
        tools={[tool({ timing: { startedAt, completedAt: startedAt + 380 } })]}
        messageState="complete"
      />,
    );

    fireEvent.click(row(/Searched Drive/));
    expect(screen.getByText("Took <1s")).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent("0s");
  });

  it("does not invent a duration for a step that stopped without a recorded end", () => {
    vi.useFakeTimers();
    const startedAt = Date.now();
    render(
      <ActivityGroup
        tools={[tool({ result: undefined, timing: { startedAt } })]}
        messageState="stopped"
      />,
    );

    fireEvent.click(row(/Interrupted/));
    act(() => {
      vi.advanceTimersByTime(60_000);
    });
    expect(document.body).not.toHaveTextContent(/Took/);
    expect(document.body).not.toHaveTextContent(/\d+s/);
  });
});

describe("ActivityGroup process layer", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("shows one step as one row with no receipt line", () => {
    const { container } = render(
      <ActivityGroup tools={sources(1)} messageState="complete" />,
    );

    expect(container.querySelector(".sourcecado-activity-receipt")).toBeNull();
    expect(container.querySelectorAll(".sourcecado-step")).toHaveLength(1);
  });

  it("groups several steps under one receipt line with a count and elapsed time", () => {
    const startedAt = 1_000_000;
    const tools = sources(3).map((part, index) => ({
      ...part,
      timing: { startedAt: startedAt + index * 1000, completedAt: startedAt + 4000 },
    }));
    render(<ActivityGroup tools={tools} messageState="complete" />);

    const receipt = row(/Used 3 sources in 4s/);
    expect(receipt).toHaveTextContent("Completed");
    expect(receipt).toHaveAttribute("aria-expanded", "false");
    expect(document.querySelectorAll(".sourcecado-step")).toHaveLength(0);
    fireEvent.click(receipt);
    expect(document.querySelectorAll(".sourcecado-step")).toHaveLength(3);
  });

  it("counts sources and actions separately", () => {
    render(
      <ActivityGroup
        tools={[
          tool({ toolCallId: "a" }),
          tool({ toolCallId: "b", toolName: "gmail_draft" }),
        ]}
        messageState="complete"
      />,
    );

    expect(row(/Used 1 source, ran 1 action/)).toBeInTheDocument();
  });

  it("lets a thinking row join the receipt as its first segment", () => {
    render(
      <ActivityGroup
        tools={sources(3)}
        messageState="complete"
        lead={{ summary: "Thought for 12s", node: <p>Model reasoning</p> }}
      />,
    );

    const receipt = row(/Thought for 12s · Used 3 sources/);
    fireEvent.click(receipt);
    const steps = screen.getByRole("group", { name: "Steps" });
    const rows = within(steps).getAllByRole("listitem");
    expect(rows).toHaveLength(4);
    expect(rows[0]).toHaveTextContent("Model reasoning");
  });

  it("stays open while the turn works and closes when the answer starts", () => {
    const { rerender } = render(
      <ActivityGroup
        tools={[...sources(2).slice(0, 1), tool({ toolCallId: "b", result: undefined })]}
        messageState="running"
      />,
    );
    expect(row(/Using 2 sources/)).toHaveAttribute("aria-expanded", "true");
    expect(document.querySelectorAll(".sourcecado-step")).toHaveLength(2);

    rerender(
      <ActivityGroup tools={sources(2)} messageState="running" answerStarted />,
    );
    expect(row(/Used 2 sources/)).toHaveAttribute("aria-expanded", "false");
  });

  it("opens a step added while the turn is still working, then collapses at the end", () => {
    const { rerender } = render(
      <ActivityGroup tools={sources(2)} messageState="running" />,
    );
    rerender(<ActivityGroup tools={sources(3)} messageState="running" />);
    expect(document.querySelectorAll(".sourcecado-step")).toHaveLength(3);

    rerender(<ActivityGroup tools={sources(3)} messageState="complete" />);
    expect(row(/Used 3 sources/)).toHaveAttribute("aria-expanded", "false");
  });

  it("keeps the layer closed through new steps when the operator closed it", () => {
    const { rerender } = render(
      <ActivityGroup tools={sources(2)} messageState="running" />,
    );
    fireEvent.click(row(/Used 2 sources/));
    expect(row(/Used 2 sources/)).toHaveAttribute("aria-expanded", "false");

    rerender(<ActivityGroup tools={sources(3)} messageState="running" />);
    expect(row(/Used 3 sources/)).toHaveAttribute("aria-expanded", "false");
  });

  it("keeps the layer open through completion when the operator opened it", () => {
    const { rerender } = render(
      <ActivityGroup tools={sources(2)} messageState="running" answerStarted />,
    );
    expect(row(/Used 2 sources/)).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(row(/Used 2 sources/));
    expect(row(/Used 2 sources/)).toHaveAttribute("aria-expanded", "true");

    rerender(<ActivityGroup tools={sources(2)} messageState="complete" answerStarted />);
    expect(row(/Used 2 sources/)).toHaveAttribute("aria-expanded", "true");
  });

  it("bounds the open layer and makes it a keyboard-scrollable region", () => {
    render(<ActivityGroup tools={sources(12)} messageState="running" />);

    const body = screen.getByRole("group", { name: "Steps" });
    expect(body).toHaveAttribute("tabindex", "0");
    expect(body).toHaveClass("sourcecado-activity-body");
  });

  it("follows the newest step while the turn works, unless the operator scrolled up", () => {
    Object.defineProperty(HTMLElement.prototype, "scrollHeight", {
      configurable: true,
      get: () => 480,
    });
    try {
      const { rerender } = render(
        <ActivityGroup tools={sources(3)} messageState="running" />,
      );
      const body = () => screen.getByRole("group", { name: "Steps" });
      expect(body().scrollTop).toBe(480);

      rerender(<ActivityGroup tools={sources(4)} messageState="running" />);
      expect(body().scrollTop).toBe(480);

      body().scrollTop = 0;
      fireEvent.scroll(body());
      rerender(<ActivityGroup tools={sources(5)} messageState="running" />);
      expect(body().scrollTop).toBe(0);
    } finally {
      Reflect.deleteProperty(HTMLElement.prototype, "scrollHeight");
    }
  });

  it.each([
    ["partial", [tool(), tool({ toolCallId: "b", result: { error: "x" }, isError: true })], "Partial"],
    ["unknown", [tool(), tool({ toolCallId: "b", result: undefined })], "Outcome unknown"],
    [
      "failed",
      [
        tool({ result: { error: "x" }, isError: true }),
        tool({ toolCallId: "b", result: { error: "y" }, isError: true }),
      ],
      "Failed",
    ],
    [
      "denied",
      [
        tool({ result: undefined, approval: { id: "a1", approved: false } }),
        tool({
          toolCallId: "b",
          result: undefined,
          approval: { id: "a2", approved: false },
        }),
      ],
      "Denied",
    ],
    [
      "unknown",
      [tool({ result: undefined }), tool({ toolCallId: "b", result: undefined })],
      "Outcome unknown",
    ],
  ] as const)("names the layer outcome %s on the receipt line", (state, tools, word) => {
    const { container } = render(
      <ActivityGroup tools={tools} messageState="complete" />,
    );

    expect(container.querySelector(".sourcecado-activity-receipt")).toHaveTextContent(word);
    expect(container.querySelector(`.sourcecado-status-icon-${state}`)).not.toBeNull();
  });

  it("marks the layer interrupted when the turn stopped mid-step, and never as success", () => {
    const { container } = render(
      <ActivityGroup
        tools={[tool(), tool({ toolCallId: "b", result: undefined })]}
        messageState="interrupted"
      />,
    );

    const receipt = container.querySelector(".sourcecado-activity-receipt");
    expect(receipt).toHaveTextContent("Interrupted");
    expect(receipt?.querySelector(".sourcecado-status-icon-done")).toBeNull();
  });

  it("shows no result cards: those render in the answer area", () => {
    render(
      <ActivityGroup
        tools={[
          tool({
            toolName: "apollo_search_people",
            result: {
              people: [
                {
                  apolloId: "person-tim",
                  firstName: "Tim",
                  lastNameObfuscated: "Zh***g",
                  title: "CEO",
                  organizationName: "Apollo.io",
                  hasEmail: true,
                },
              ],
            },
          }),
        ]}
        messageState="complete"
      />,
    );

    expect(screen.queryByText("Tim")).not.toBeInTheDocument();
  });
});
