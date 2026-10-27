import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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
  });

  it("shows no time on an idle chip, even if the backend still reports one", () => {
    render(<ThreadHeader title="Alpha" status="idle" elapsedMs={12_900} />);
    expect(screen.getByRole("banner")).not.toHaveTextContent("12s");
  });

  it("puts the person chip before the status chip, after the title", () => {
    render(
      <ThreadHeader
        title="Alpha"
        activePerson={{ person_id: "p1", version: 1, label: "Maya Chen, Founder" }}
      />,
    );
    const title = screen.getByRole("heading", { level: 1 });
    const person = screen.getByRole("link");
    const status = screen.getByRole("banner").querySelector(".sourcecado-run-status")!;
    expect(title.compareDocumentPosition(person) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(person.compareDocumentPosition(status) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("keeps the full title and person label in tooltips for when they are cut short", () => {
    const long = "Find the founders of every Berkeley alumni startup that raised in 2026";
    render(
      <ThreadHeader
        title={long}
        activePerson={{ person_id: "p1", version: 1, label: "Maya Chen, Founder at Acme" }}
      />,
    );
    expect(screen.getByRole("heading", { level: 1, name: long })).toHaveAttribute("title", long);
    expect(screen.getByRole("link")).toHaveAttribute("title", "Maya Chen, Founder at Acme");
  });

  it("shows no chip while the thread loads", () => {
    render(<ThreadHeader title="Alpha" status={null} />);
    expect(screen.getByRole("banner")).not.toHaveTextContent("Idle");
    expect(screen.getByRole("banner").querySelector(".sourcecado-run-status")).toBeNull();
  });
});

describe("ThreadHeader status hold", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  const chip = () =>
    screen.getByRole("banner").querySelector(".sourcecado-run-status")?.textContent ?? "";

  it("shows the first state after loading at once", () => {
    const { rerender } = render(<ThreadHeader title="Alpha" status={null} />);
    rerender(<ThreadHeader title="Alpha" status="approval" />);
    expect(chip()).toContain("Needs approval");
  });

  it("holds a live state for 400 ms before another state replaces it", () => {
    const { rerender } = render(<ThreadHeader title="Alpha" status="thinking" />);
    rerender(<ThreadHeader title="Alpha" status="working" />);
    expect(chip()).toContain("Thinking");

    act(() => void vi.advanceTimersByTime(399));
    expect(chip()).toContain("Thinking");
    act(() => void vi.advanceTimersByTime(1));
    expect(chip()).toContain("Working");
  });

  it("does not flash a state that is gone before the hold ends", () => {
    const seen: string[] = [];
    const { rerender } = render(<ThreadHeader title="Alpha" status="thinking" />);
    for (const next of ["working", "thinking", "working", "thinking"] as const) {
      act(() => void vi.advanceTimersByTime(50));
      rerender(<ThreadHeader title="Alpha" status={next} />);
      seen.push(chip());
    }
    act(() => void vi.advanceTimersByTime(1000));
    seen.push(chip());
    expect(seen.every((text) => text.includes("Thinking"))).toBe(true);
  });

  it("ends on the real state once the run is over", () => {
    const { rerender } = render(<ThreadHeader title="Alpha" status="working" />);
    rerender(<ThreadHeader title="Alpha" status="idle" />);
    act(() => void vi.advanceTimersByTime(400));
    expect(chip()).toContain("Idle");
  });

  it("shows Needs approval at once, and starts a run at once from Idle", () => {
    const { rerender } = render(<ThreadHeader title="Alpha" status="idle" />);
    rerender(<ThreadHeader title="Alpha" status="thinking" />);
    expect(chip()).toContain("Thinking");
    rerender(<ThreadHeader title="Alpha" status="approval" />);
    expect(chip()).toContain("Needs approval");
  });
});
