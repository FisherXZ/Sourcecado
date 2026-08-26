import { act, fireEvent, render, screen } from "@testing-library/react";
import type { ToolCallMessagePart } from "@assistant-ui/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ActivityGroup } from "../src/chat/ActivityGroup";

const tool = (
  overrides: Partial<ToolCallMessagePart> = {},
): ToolCallMessagePart => ({
  type: "tool-call",
  toolCallId: "call-drive",
  toolName: "drive_search",
  args: {},
  argsText: "{}",
  result: { rows: 2 },
  ...overrides,
});

describe("ActivityGroup", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("shows a live ticking elapsed time while a tool is running, then stops on completion", () => {
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
    expect(
      screen.getByRole("button", { name: /0s · Running$/ }),
    ).toBeInTheDocument();

    act(() => {
      vi.advanceTimersByTime(3000);
    });
    expect(
      screen.getByRole("button", { name: /3s · Running$/ }),
    ).toBeInTheDocument();

    rerender(
      <ActivityGroup
        tools={[tool({ timing: { startedAt, completedAt: startedAt + 3000 } })]}
        messageState="complete"
      />,
    );
    const receipt = screen.getByRole("button", { name: /Completed$/ });
    expect(receipt).toHaveTextContent("3s");
    const textAfterCompletion = receipt.textContent;

    act(() => {
      vi.advanceTimersByTime(10000);
    });
    expect(receipt.textContent).toBe(textAfterCompletion);
  });


  it("uses the specific human action for a single known tool", () => {
    render(<ActivityGroup tools={[tool()]} messageState="complete" />);

    const disclosure = screen.getByRole("button", {
      name: "Searched Drive · Completed",
    });
    expect(disclosure).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(disclosure);
    expect(screen.getByText("Searched Drive")).toBeInTheDocument();
  });

  it.each([
    ["board_get", "Read Board record"],
    ["board_query", "Queried Board"],
    ["board_upsert", "Created Board record"],
    ["board_mutate", "Updated Board record"],
    ["board_delete", "Deleted Board record"],
  ])("labels %s receipts with a reviewable Board action", (toolName, label) => {
    render(
      <ActivityGroup
        tools={[tool({ toolName, result: { board_changed: true } })]}
        messageState="complete"
      />,
    );

    expect(screen.getByRole("button", { name: `${label} · Completed` })).toBeInTheDocument();
  });

  it.each([
    ["Running", [tool({ toolName: "private_internal_tool", result: undefined })], "running"],
    ["Failed", [tool({ toolName: "private_internal_tool", result: undefined, isError: true })], "complete"],
    [
      "Denied",
      [
        tool({
          toolName: "private_internal_tool",
          result: undefined,
          approval: { id: "approval-1", approved: false },
        }),
      ],
      "complete",
    ],
    [
      "Partial",
      [
        tool(),
        tool({
          toolCallId: "call-failed",
          toolName: "private_internal_tool",
          result: undefined,
          isError: true,
        }),
      ],
      "complete",
    ],
    ["Interrupted", [tool()], "interrupted"],
  ] as const)("summarizes %s activity without exposing unknown tool names", (state, tools, messageState) => {
    const { container } = render(
      <ActivityGroup tools={tools} messageState={messageState} />,
    );

    expect(
      screen.getByRole("button", { name: new RegExp(`${state}$`) }),
    ).toHaveTextContent(state);
    expect(container).not.toHaveTextContent("private_internal_tool");
  });

  it("shows the domain answer result without requiring the activity trace to be expanded", () => {
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

    const disclosure = screen.getByRole("button", {
      name: "Searched Apollo · Completed",
    });
    expect(disclosure).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByText("Tim Zh***g")).toBeInTheDocument();
  });

  it("does not duplicate the domain result when the activity trace is also expanded", () => {
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

    fireEvent.click(
      screen.getByRole("button", { name: "Searched Apollo · Completed" }),
    );
    expect(screen.getAllByText("Tim Zh***g")).toHaveLength(1);
  });

  it("auto-collapses the activity trace to a quiet receipt once a running turn completes", () => {
    const { rerender } = render(
      <ActivityGroup tools={[tool({ result: undefined })]} messageState="running" />,
    );
    expect(screen.getByRole("button", { name: /Running$/ })).toHaveAttribute(
      "aria-expanded",
      "true",
    );

    rerender(<ActivityGroup tools={[tool()]} messageState="complete" />);
    expect(screen.getByRole("button", { name: /Completed$/ })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
  });

  it("keeps an explicitly reopened activity trace open through completion", () => {
    const { rerender } = render(
      <ActivityGroup tools={[tool({ result: undefined })]} messageState="running" />,
    );
    const disclosure = screen.getByRole("button", { name: /Running$/ });
    expect(disclosure).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(disclosure);
    expect(disclosure).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(disclosure);
    expect(disclosure).toHaveAttribute("aria-expanded", "true");

    rerender(<ActivityGroup tools={[tool()]} messageState="complete" />);
    expect(screen.getByRole("button", { name: /Completed$/ })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
  });

  it("does not offer another write retry when recovery outcome is unknown", () => {
    render(
      <ActivityGroup
        tools={[
          tool({
            result: { error: "connection dropped" },
            isError: true,
            providerMetadata: {
              sourcecado: {
                failure: {
                  class: "unknown",
                  connector_id: "gmail",
                  source: "Gmail",
                  retry_safe: false,
                  idempotent: false,
                  summary: "The Gmail draft outcome needs review.",
                  repair_route: null,
                  detail: "Connection dropped.",
                  call_id: "call-drive",
                  run_id: "run-1",
                  session_id: "thread-1",
                  state: "failed",
                },
                recovery: {
                  commandId: "retry-1",
                  action: "retry",
                  status: "interrupted",
                  outcome:
                    "Outcome is unknown after Sourcecado restarted. Verify the external resource before retrying.",
                },
              },
            },
          }),
        ]}
        messageState="partial"
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /Failed$/ }));
    expect(
      screen.getByText(/outcome is unknown after Sourcecado restarted/i),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Retry failed step" }),
    ).not.toBeInTheDocument();
  });
});
