import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { SourcecadoStructuredMessage } from "../src/chat/messageAdapter";
import { ThreadHeader, runStatusOf } from "../src/chat/ThreadHeader";

function assistant(
  state: SourcecadoStructuredMessage["state"],
  parts: SourcecadoStructuredMessage["parts"] = [],
): SourcecadoStructuredMessage {
  return { id: "a1", role: "assistant", state, parts };
}

function tool(
  state: "running" | "complete" | "error",
  approval?: "pending" | "allowed",
): SourcecadoStructuredMessage["parts"][number] {
  return {
    type: "tool",
    id: "t1",
    name: "gmail_draft",
    arguments: {},
    state,
    ...(approval ? { approval: { id: "t1", state: approval } } : {}),
  };
}

describe("runStatusOf", () => {
  it("is idle with no run and no parked approval", () => {
    expect(runStatusOf([], false)).toBe("idle");
    expect(runStatusOf([assistant("complete")], false)).toBe("idle");
  });

  it("is thinking while the run is live and no tool is running", () => {
    expect(runStatusOf([], true)).toBe("thinking");
    expect(runStatusOf([assistant("running")], true)).toBe("thinking");
    expect(runStatusOf([assistant("running", [tool("complete")])], true)).toBe(
      "thinking",
    );
  });

  it("is working while a tool call is in flight", () => {
    expect(runStatusOf([assistant("running", [tool("running")])], true)).toBe(
      "working",
    );
  });

  it("needs approval while an approval is pending, even with no live run", () => {
    const parked = [assistant("waiting-approval", [tool("running", "pending")])];
    expect(runStatusOf(parked, true)).toBe("approval");
    expect(runStatusOf(parked, false)).toBe("approval");
  });

  it("stops asking once the approval is answered or the turn is over", () => {
    expect(
      runStatusOf(
        [assistant("waiting-approval", [tool("running", "allowed")])],
        true,
      ),
    ).toBe("working");
    expect(
      runStatusOf([assistant("complete", [tool("running", "pending")])], false),
    ).toBe("idle");
  });
});

describe("ThreadHeader", () => {
  it("renders the title, a status chip and no eyebrow, persona or statistics", () => {
    render(<ThreadHeader title="Find Maya Chen at Acme" />);

    const header = screen.getByRole("banner");
    expect(
      screen.getByRole("heading", { level: 1, name: "Find Maya Chen at Acme" }),
    ).toBeInTheDocument();
    expect(header).toHaveTextContent("Idle");
    expect(header).not.toHaveTextContent("Sourcing workspace");
    expect(header.querySelector(".eyebrow")).toBeNull();
    expect(header.querySelector(".sourcecado-persona-badge")).toBeNull();
    expect(header.querySelector(".sourcecado-run-metrics")).toBeNull();
  });

  it("falls back to a plain title for a thread with no name yet", () => {
    render(<ThreadHeader title="   " />);
    expect(
      screen.getByRole("heading", { level: 1, name: "New sourcing conversation" }),
    ).toBeInTheDocument();
  });

  it("puts an icon in every chip", () => {
    render(
      <ThreadHeader
        title="Alpha"
        status="working"
        elapsedMs={65_000}
        activePerson={{ person_id: "p 1", version: 1, label: "Maya Chen, Founder" }}
      />,
    );
    const chips = screen
      .getByRole("banner")
      .querySelectorAll(".sourcecado-run-status, .sourcecado-active-person");
    expect(chips).toHaveLength(2);
    for (const chip of chips) {
      expect(chip.querySelector("svg[aria-hidden='true']")).not.toBeNull();
    }
  });

  it("links the person chip to the person file", () => {
    render(
      <ThreadHeader
        title="Alpha"
        activePerson={{ person_id: "p 1", version: 1, label: "Maya Chen, Founder" }}
      />,
    );
    const link = screen.getByRole("link", { name: "Active person: Maya Chen, Founder" });
    expect(link).toHaveAttribute("href", "#/people/p%201");
    expect(link).toHaveTextContent("Maya Chen, Founder");
  });

  it("shows no person chip when the thread has no active person", () => {
    render(<ThreadHeader title="Alpha" />);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it.each([
    ["thinking", "Thinking"],
    ["working", "Working"],
    ["approval", "Needs approval"],
  ] as const)("shows %s with the elapsed time while a run is active", (status, label) => {
    render(<ThreadHeader title="Alpha" status={status} elapsedMs={65_400} />);
    expect(screen.getByRole("banner")).toHaveTextContent(`${label}1m 05s`);
  });

  it("shows seconds under a minute, and no time when it is unknown or the run is idle", () => {
    const { rerender } = render(
      <ThreadHeader title="Alpha" status="thinking" elapsedMs={12_900} />,
    );
    expect(screen.getByRole("banner")).toHaveTextContent("Thinking12s");

    rerender(<ThreadHeader title="Alpha" status="thinking" elapsedMs={null} />);
    expect(screen.getByRole("banner")).toHaveTextContent(/^Alpha\s*Thinking$/);

    rerender(<ThreadHeader title="Alpha" status="idle" elapsedMs={12_900} />);
    expect(screen.getByRole("banner")).not.toHaveTextContent("12s");
  });
});
