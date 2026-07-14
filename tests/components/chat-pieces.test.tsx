// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { StepRow, PendingRow } from "@/app/chat/StepRow";
import { ReasoningTrace } from "@/app/chat/ReasoningTrace";
import { MessageBubble } from "@/app/chat/MessageBubble";
import { Composer } from "@/app/chat/Composer";
import type { ChatStep } from "@/app/chat/stream";

const step = (over: Partial<ChatStep> = {}): ChatStep => ({
  index: 1,
  tool: "search_memory",
  ok: true,
  detail: "2 facts, 1 chunk",
  ...over,
});

describe("StepRow", () => {
  it("renders the tool and detail and marks a successful step done", () => {
    render(
      <ul>
        <StepRow step={step()} />
      </ul>
    );
    expect(screen.getByText("search_memory")).toBeInTheDocument();
    expect(screen.getByText(/2 facts, 1 chunk/)).toBeInTheDocument();
    expect(screen.getByRole("listitem")).toHaveAttribute("data-state", "done");
  });

  it("marks a failed step with the error state", () => {
    render(
      <ul>
        <StepRow step={step({ ok: false, detail: "source not allowed" })} />
      </ul>
    );
    expect(screen.getByRole("listitem")).toHaveAttribute("data-state", "error");
  });
});

describe("PendingRow", () => {
  it("is busy and shows a working label", () => {
    render(
      <ul>
        <PendingRow label="Searching memory" />
      </ul>
    );
    const li = screen.getByRole("listitem");
    expect(li).toHaveAttribute("aria-busy", "true");
    expect(li).toHaveAttribute("data-state", "working");
    expect(screen.getByText(/Searching memory/)).toBeInTheDocument();
  });
});

describe("ReasoningTrace", () => {
  it("shows the step count and reflects open state via aria-expanded", () => {
    render(<ReasoningTrace steps={[step(), step({ index: 2 })]} running={false} open={true} onToggle={() => {}} />);
    const toggle = screen.getByRole("button", { name: /reasoning/i });
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText(/2 steps/)).toBeInTheDocument();
  });

  it("calls onToggle when the header is clicked", () => {
    const onToggle = vi.fn();
    render(<ReasoningTrace steps={[step()]} running={false} open={false} onToggle={onToggle} />);
    fireEvent.click(screen.getByRole("button", { name: /reasoning/i }));
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it("shows a live pending row while the agent is running", () => {
    render(<ReasoningTrace steps={[step()]} running={true} open={true} onToggle={() => {}} />);
    expect(screen.getByRole("listitem", { busy: true })).toBeInTheDocument();
  });

  it("renders no pending row once the run has settled", () => {
    render(<ReasoningTrace steps={[step()]} running={false} open={true} onToggle={() => {}} />);
    expect(screen.queryByRole("listitem", { busy: true })).not.toBeInTheDocument();
  });
});

describe("MessageBubble", () => {
  it("tags the role and renders its content", () => {
    const { rerender } = render(<MessageBubble role="user">Hello there</MessageBubble>);
    expect(screen.getByText("Hello there").closest("[data-role]")).toHaveAttribute("data-role", "user");
    rerender(<MessageBubble role="assistant">Reply</MessageBubble>);
    expect(screen.getByText("Reply").closest("[data-role]")).toHaveAttribute("data-role", "assistant");
  });
});

describe("Composer", () => {
  it("submits the typed value and disables while busy", () => {
    const onSubmit = vi.fn();
    const onChange = vi.fn();
    const { rerender } = render(
      <Composer value="who is acme" onChange={onChange} onSubmit={onSubmit} disabled={false} />
    );
    fireEvent.submit(screen.getByRole("textbox"));
    expect(onSubmit).toHaveBeenCalledTimes(1);

    rerender(<Composer value="" onChange={onChange} onSubmit={onSubmit} disabled={true} />);
    expect(screen.getByRole("textbox")).toBeDisabled();
    expect(screen.getByRole("button", { name: /send|running/i })).toBeDisabled();
  });

  it("submits on Enter but not on Shift+Enter", () => {
    const onSubmit = vi.fn();
    render(<Composer value="hi" onChange={() => {}} onSubmit={onSubmit} disabled={false} />);
    const box = screen.getByRole("textbox");
    fireEvent.keyDown(box, { key: "Enter", shiftKey: true });
    expect(onSubmit).not.toHaveBeenCalled();
    fireEvent.keyDown(box, { key: "Enter", shiftKey: false });
    expect(onSubmit).toHaveBeenCalledTimes(1);
  });
});
