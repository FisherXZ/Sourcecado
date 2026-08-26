import { fireEvent, render, screen } from "@testing-library/react";
import type { ToolCallMessagePart } from "@assistant-ui/react";
import { describe, expect, it } from "vitest";

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
