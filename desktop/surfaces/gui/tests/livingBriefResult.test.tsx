import { render, screen, within } from "@testing-library/react";
import type { ToolCallMessagePart } from "@assistant-ui/react";
import { describe, expect, it } from "vitest";

import { ActivityGroup } from "../src/chat/ActivityGroup";
import { InspectorProvider } from "../src/chat/Inspector";
import { livingBrief } from "./livingBrief";

function renderBoardResult(result: unknown, isError = false) {
  const tool = {
    type: "tool-call",
    toolCallId: "board-brief-1",
    toolName: "board_get",
    args: {},
    argsText: "{}",
    result,
    isError,
  } as ToolCallMessagePart;
  return render(
    <InspectorProvider threadId="thread-brief">
      <ActivityGroup tools={[tool]} messageState="complete" />
    </InspectorProvider>,
  );
}

describe("living brief result", () => {
  it("renders one complete brief with all four successor handoff fields", () => {
    const brief = livingBrief();
    renderBoardResult({
      status: "complete",
      partial: false,
      person_id: "person one",
      brief,
    });

    const result = screen.getByRole("region", { name: "Living brief for Alyssa Lee" });
    expect(result).toHaveTextContent("Sequence · Open");
    expect(result).toHaveTextContent("TargetStrong sourcing fit.");
    expect(result).toHaveTextContent("What this person wants is not recorded.");
    expect(within(result).getByRole("region", { name: "Learned" })).toHaveTextContent(
      "Led university sourcing.",
    );
    expect(within(result).getByRole("region", { name: "Knowledge gaps" })).toHaveTextContent(
      "No email address is recorded for this person.",
    );
    expect(result).toHaveTextContent("SourcesApollo");
    const handoff = within(result).getByRole("region", { name: "Successor handoff" });
    expect(handoff).toHaveTextContent("Who this is");
    expect(handoff).toHaveTextContent("What we wanted");
    expect(handoff).toHaveTextContent("What happened");
    expect(handoff).toHaveTextContent("What they want");
    expect(handoff).toHaveTextContent("Draft for review. Nothing was saved.");
  });

  it("renders a bounded structured partial result for a genuine Board failure", () => {
    renderBoardResult(
      {
        status: "partial",
        partial: true,
        code: "board_read_failed",
        error: "The Board person-file read is unavailable.",
        partial_sources: ["board"],
        unavailable_sources: [{ source: "board", code: "board_read_failed" }],
      },
      true,
    );

    const result = screen.getByRole("region", { name: "Partial living brief" });
    expect(result).toHaveTextContent("Board is unavailable");
    expect(result).toHaveTextContent(
      "Evidence already gathered remains available in this conversation.",
    );
    expect(result).not.toHaveTextContent("private");
  });
});
