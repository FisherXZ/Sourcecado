import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { UndoToast, useUndoToast } from "../src/crm/UndoToast";

function Host({ name, onUndo }: { name: string; onUndo?: () => Promise<void> }) {
  const { toast, show, dismiss } = useUndoToast();
  return (
    <div data-testid={name}>
      <button
        type="button"
        onClick={() => show({ message: `${name} saved`, onUndo, undoLabel: `Undo ${name}` })}
      >
        Show {name}
      </button>
      <UndoToast toast={toast} onDismiss={dismiss} />
    </div>
  );
}

describe("Undo toast", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("announces the result and closes itself after six seconds", () => {
    render(<Host name="first" onUndo={async () => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Show first" }));
    expect(screen.getByText("first saved").closest('[role="status"]')).toBeTruthy();
    expect(screen.getByRole("button", { name: "Undo first" })).toBeTruthy();

    act(() => vi.advanceTimersByTime(5900));
    expect(screen.getByText("first saved")).toBeTruthy();
    act(() => vi.advanceTimersByTime(200));
    expect(screen.queryByText("first saved")).toBeNull();
  });

  it("waits while the pointer is on it", () => {
    render(<Host name="first" onUndo={async () => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Show first" }));
    fireEvent.mouseEnter(screen.getByText("first saved").parentElement!);
    act(() => vi.advanceTimersByTime(10000));
    expect(screen.getByText("first saved")).toBeTruthy();
    fireEvent.mouseLeave(screen.getByText("first saved").parentElement!);
    act(() => vi.advanceTimersByTime(6000));
    expect(screen.queryByText("first saved")).toBeNull();
  });

  it("shows one toast at a time across the app", () => {
    render(
      <>
        <Host name="first" />
        <Host name="second" />
      </>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Show first" }));
    fireEvent.click(screen.getByRole("button", { name: "Show second" }));
    expect(screen.queryByText("first saved")).toBeNull();
    expect(screen.getByText("second saved")).toBeTruthy();
    // A toast without onUndo only confirms.
    expect(screen.queryByRole("button", { name: "Undo second" })).toBeNull();
  });

  it("runs Undo once and closes, or reports a failed undo in place", async () => {
    const undo = vi.fn(async () => {});
    const { unmount } = render(<Host name="first" onUndo={undo} />);
    fireEvent.click(screen.getByRole("button", { name: "Show first" }));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Undo first" }));
    });
    expect(undo).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("first saved")).toBeNull();
    unmount();

    render(<Host name="other" onUndo={async () => { throw new Error("Couldn’t undo. The task changed again."); }} />);
    fireEvent.click(screen.getByRole("button", { name: "Show other" }));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Undo other" }));
    });
    expect(screen.getByText("Couldn’t undo. The task changed again.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Undo other" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByText("Couldn’t undo. The task changed again.")).toBeNull();
  });
});
