// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

const { runChatMock } = vi.hoisted(() => ({ runChatMock: vi.fn() }));
vi.mock("@/app/chat/stream", () => ({ runChat: runChatMock }));

import { ChatClient } from "@/app/chat/ChatClient";

describe("ChatClient", () => {
  beforeEach(() => runChatMock.mockReset());

  it("shows an empty state before any question", () => {
    render(<ChatClient />);
    expect(screen.getByRole("heading", { name: /ask your team's memory/i })).toBeInTheDocument();
  });

  it("streams steps live, then renders the cited answer and re-enables the composer", async () => {
    let resolveRun: (turn: unknown) => void = () => {};
    runChatMock.mockImplementation((_q: string, _h: unknown, onUpdate?: (t: unknown) => void) => {
      if (!onUpdate) return Promise.resolve({ steps: [], answer: "" });
      onUpdate({ steps: [{ index: 1, tool: "search_memory", ok: true, detail: "2 facts, 1 chunk" }], answer: "" });
      return new Promise((resolve) => {
        resolveRun = resolve;
      });
    });

    render(<ChatClient />);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "tell me about acme" } });
    fireEvent.submit(screen.getByRole("textbox"));

    // user message + live reasoning step + busy composer
    await waitFor(() => expect(screen.getByText("tell me about acme")).toBeInTheDocument());
    expect(screen.getByText("search_memory")).toBeInTheDocument();
    expect(screen.getByRole("listitem", { busy: true })).toBeInTheDocument();
    expect(screen.getByRole("textbox")).toBeDisabled();

    resolveRun({
      steps: [{ index: 1, tool: "search_memory", ok: true, detail: "2 facts, 1 chunk" }],
      answer: "Acme Robotics is Series B [acme-md#chunk-1].",
      meta: { runId: 42, status: "succeeded", steps: 1, invalidCitations: [] },
    });

    await waitFor(() => expect(screen.getByText(/Acme Robotics is Series B/)).toBeInTheDocument());
    expect(screen.queryByRole("listitem", { busy: true })).not.toBeInTheDocument(); // settled
    expect(screen.getByRole("textbox")).not.toBeDisabled();
    expect(screen.getByRole("link", { name: /view trace/i })).toHaveAttribute("href", "/runs/42");
    // P1-4: streaming content lives in a polite live region for screen readers.
    expect(screen.getByText(/Acme Robotics is Series B/).closest("[aria-live]")).toHaveAttribute(
      "aria-live",
      "polite"
    );
  });

  it("settles to an error state (no live row, composer re-enabled) when the stream fails", async () => {
    // Guard the teardown no-arg call (vitest cleanup) so only the real run rejects.
    runChatMock.mockImplementation((_q: string, _h: unknown, onUpdate?: (t: unknown) => void) =>
      onUpdate ? Promise.reject(new Error("stream dropped")) : Promise.resolve({ steps: [], answer: "" })
    );
    render(<ChatClient />);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "boom" } });
    fireEvent.submit(screen.getByRole("textbox"));

    // P0-1: a failed/stalled run must SETTLE — the avocado "live" row is gone,
    // the failure is surfaced as an alert, and the composer is usable again.
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/stream dropped/i);
    expect(screen.queryByRole("listitem", { busy: true })).not.toBeInTheDocument();
    expect(screen.getByRole("textbox")).not.toBeDisabled();
  });

  it("sends prior turns as history on a follow-up question", async () => {
    runChatMock.mockResolvedValue({
      steps: [],
      answer: "First answer.",
      meta: { runId: 1, status: "succeeded", steps: 0, invalidCitations: [] },
    });
    render(<ChatClient />);

    fireEvent.change(screen.getByRole("textbox"), { target: { value: "first question" } });
    fireEvent.submit(screen.getByRole("textbox"));
    await waitFor(() => expect(screen.getByText("First answer.")).toBeInTheDocument());

    runChatMock.mockResolvedValue({
      steps: [],
      answer: "Second answer.",
      meta: { runId: 2, status: "succeeded", steps: 0, invalidCitations: [] },
    });
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "follow up" } });
    fireEvent.submit(screen.getByRole("textbox"));
    await waitFor(() => expect(screen.getByText("Second answer.")).toBeInTheDocument());

    // the second call's history carries the first exchange (user + assistant)
    const secondCallHistory = runChatMock.mock.calls[1][1];
    expect(secondCallHistory).toEqual([
      { role: "user", content: "first question" },
      { role: "assistant", content: "First answer." },
    ]);
  });

  it("excludes an errored exchange's message from the next turn's history", async () => {
    runChatMock
      .mockImplementationOnce((_q: string, _h: unknown, onUpdate?: (t: unknown) => void) =>
        onUpdate ? Promise.reject(new Error("stream dropped")) : Promise.resolve({ steps: [], answer: "" })
      )
      .mockResolvedValueOnce({
        steps: [],
        answer: "Second answer.",
        meta: { runId: 2, status: "succeeded", steps: 0, invalidCitations: [] },
      });

    render(<ChatClient />);

    fireEvent.change(screen.getByRole("textbox"), { target: { value: "first question" } });
    fireEvent.submit(screen.getByRole("textbox"));
    await screen.findByRole("alert");

    fireEvent.change(screen.getByRole("textbox"), { target: { value: "follow up" } });
    fireEvent.submit(screen.getByRole("textbox"));
    await waitFor(() => expect(screen.getByText("Second answer.")).toBeInTheDocument());

    const secondCallHistory = runChatMock.mock.calls[1][1];
    expect(secondCallHistory).toEqual([]);
  });
});
