import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ToolCallMessagePartProps } from "@assistant-ui/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApprovalCard } from "../src/chat/ApprovalCard";
import { ChatPage } from "../src/routes/ChatPage";

const api = vi.hoisted(() => ({
  getSessions: vi.fn(),
  getPersona: vi.fn(),
  getSession: vi.fn(),
  getCurrentRunTelemetry: vi.fn(),
  getSkills: vi.fn(),
  getBoard: vi.fn(),
  openChat: vi.fn(),
}));

vi.mock("../src/api", () => ({
  getSessions: api.getSessions,
  getPersona: api.getPersona,
  getSession: api.getSession,
  getCurrentRunTelemetry: api.getCurrentRunTelemetry,
  getSkills: api.getSkills,
  getBoard: api.getBoard,
  hasToken: () => true,
  openChat: api.openChat,
}));

let onChatEvent: ((event: Record<string, unknown>) => void) | undefined;

const envelope = {
  version: 2,
  session_id: "thread-alpha",
  run_id: "run-scroll",
  message_id: "message-scroll",
  part_id: "part-scroll",
} as const;

// One finished turn with a source chip, so the thread has messages to scroll
// and the inspector has something to open.
const CONVERSATION = {
  id: "thread-alpha",
  title: "Scroll",
  messages: [],
  queue: [],
  queue_paused: false,
  events: [
    { ...envelope, type: "turn_start", event_id: "scroll-1", state: "running" },
    {
      ...envelope,
      type: "tool_started",
      event_id: "scroll-2",
      id: "call-scroll",
      name: "drive_search",
      arguments: { query: "Codeology" },
    },
    {
      ...envelope,
      type: "tool_finished",
      event_id: "scroll-3",
      id: "call-scroll",
      name: "drive_search",
      ok: true,
      result: { files: 1 },
      sources: [
        {
          id: "source-codeology-brief",
          title: "Codeology brief",
          url: "https://drive.google.com/file/codeology",
          provider: "Google Drive",
          stale: false,
          truncated: false,
        },
      ],
      artifacts: [],
    },
    { ...envelope, type: "turn_end", event_id: "scroll-4", state: "complete", text: "I found the brief." },
  ],
};

const SOURCE_CHIP = "Source: Codeology brief (drive.google.com)";

type Metrics = { scrollHeight: number; clientHeight: number; scrollTop: number };

/** jsdom does no layout. Give the viewport the numbers a browser would. */
function setMetrics(el: HTMLElement, metrics: Metrics) {
  Object.defineProperty(el, "scrollHeight", { configurable: true, writable: true, value: metrics.scrollHeight });
  Object.defineProperty(el, "clientHeight", { configurable: true, writable: true, value: metrics.clientHeight });
  Object.defineProperty(el, "scrollTop", { configurable: true, writable: true, value: metrics.scrollTop });
}

function scrollTo(el: HTMLElement, top: number) {
  act(() => {
    (el as unknown as { scrollTop: number }).scrollTop = top;
    fireEvent.scroll(el);
  });
}

const APPROVAL_CONVERSATION = {
  ...CONVERSATION,
  events: [
    { ...envelope, type: "turn_start", event_id: "wait-1", state: "running" },
    {
      ...envelope,
      type: "permission_required",
      event_id: "wait-2",
      id: "approval-draft",
      name: "gmail_draft",
      arguments: { to: "alyssa@example.com", subject: "Hello", body: "Full body" },
      reason: "Creating a Gmail draft changes an external account.",
      requested_at: "2026-08-25T12:00:00.000Z",
      scope: "once",
    },
  ],
};

async function openThread(conversation = CONVERSATION) {
  api.getSession.mockResolvedValue(conversation);
  render(<ChatPage sessionId="thread-alpha" />);
  const transcript = await screen.findByRole("log", { name: "Conversation" });
  await screen.findByRole(
    conversation === CONVERSATION ? "button" : "heading",
    conversation === CONVERSATION ? { name: SOURCE_CHIP } : { level: 2 },
  );
  // The thread library schedules its first scroll to the bottom on a frame.
  // Let that frame pass so the test starts from a settled viewport.
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 60));
  });
  const calls = vi.fn();
  Object.defineProperty(transcript, "scrollTo", {
    configurable: true,
    value: (options: ScrollToOptions) => {
      calls(options);
      // A browser clamps the target to the end of the content.
      scrollTo(
        transcript,
        Math.min(Number(options.top), transcript.scrollHeight - transcript.clientHeight),
      );
    },
  });
  setMetrics(transcript, { scrollHeight: 2000, clientHeight: 500, scrollTop: 1500 });
  scrollTo(transcript, 1500);
  return { transcript, scrollCalls: calls };
}

const jumpButton = () => screen.queryByRole("button", { name: "Jump to latest" });

describe("Scroll follow and Jump to latest", () => {
  beforeEach(() => {
    window.localStorage.clear();
    api.getSessions.mockResolvedValue({ sessions: [], open_id: "thread-alpha" });
    api.getPersona.mockResolvedValue({ id: "sourcing", name: "Sourcing Operator", tools: [] });
    api.getCurrentRunTelemetry.mockResolvedValue({
      version: 1,
      session_id: "thread-alpha",
      current_run: null,
    });
    api.getSkills.mockResolvedValue({ skills: [] });
    api.getBoard.mockResolvedValue({ backlog: [], open: [], in_conversation: [], done: [] });
    onChatEvent = undefined;
    api.openChat.mockImplementation((callback) => {
      onChatEvent = callback;
      return {
      send: vi.fn(),
      approve: vi.fn(),
      cancel: vi.fn(),
      queue: vi.fn(),
      recover: vi.fn(),
      close: vi.fn(),
      };
    });
  });

  it("shows no Jump to latest while the thread is at the bottom", async () => {
    await openThread();
    expect(jumpButton()).not.toBeInTheDocument();
  });

  it("shows Jump to latest as a named icon button once the operator scrolls up", async () => {
    const { transcript } = await openThread();
    scrollTo(transcript, 600);

    const button = await screen.findByRole("button", { name: "Jump to latest" });
    expect(button.querySelector("svg")).not.toBeNull();
    expect(button).toHaveTextContent("");
    expect(button).toHaveAttribute("title", "Jump to latest");
    // It sits in the thread, not inside the scrolling transcript.
    expect(transcript).not.toContainElement(button);
    expect(button.closest(".sourcecado-thread")).not.toBeNull();
  });

  it("hides again when the operator scrolls back to the bottom by hand", async () => {
    const { transcript } = await openThread();
    scrollTo(transcript, 600);
    await screen.findByRole("button", { name: "Jump to latest" });

    scrollTo(transcript, 1500);
    await waitFor(() => expect(jumpButton()).not.toBeInTheDocument());
  });

  it("returns to the bottom, hands focus to the transcript and hides", async () => {
    const { transcript, scrollCalls } = await openThread();
    scrollTo(transcript, 600);

    fireEvent.click(await screen.findByRole("button", { name: "Jump to latest" }));

    expect(scrollCalls).toHaveBeenLastCalledWith(expect.objectContaining({ top: 2000 }));
    await waitFor(() => expect(jumpButton()).not.toBeInTheDocument());
    expect(transcript).toHaveFocus();
    expect(transcript.scrollTop).toBe(1500);
  });

  it("jumps instantly, with no scroll animation to reduce", async () => {
    const { transcript, scrollCalls } = await openThread();
    scrollTo(transcript, 600);
    fireEvent.click(await screen.findByRole("button", { name: "Jump to latest" }));

    const options = scrollCalls.mock.calls.at(-1)?.[0] as ScrollToOptions;
    expect(options.behavior === undefined || options.behavior === "auto" || options.behavior === "instant").toBe(true);
  });

  it("holds the position when new output arrives while scrolled up, and follows again after the jump", async () => {
    const { transcript, scrollCalls } = await openThread();
    scrollTo(transcript, 600);
    await screen.findByRole("button", { name: "Jump to latest" });
    scrollCalls.mockClear();

    // New output arrives below the reading position.
    const grow = (height: number) => {
      setMetrics(transcript, { scrollHeight: height, clientHeight: 500, scrollTop: transcript.scrollTop });
      const chunk = document.createElement("p");
      chunk.textContent = `more output ${height}`;
      act(() => {
        transcript.appendChild(chunk);
      });
    };
    grow(2400);
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 60));
    });
    expect(scrollCalls).not.toHaveBeenCalled();
    expect(transcript.scrollTop).toBe(600);
    expect(jumpButton()).toBeInTheDocument();

    // After the jump the thread follows new output again.
    fireEvent.click(screen.getByRole("button", { name: "Jump to latest" }));
    await waitFor(() => expect(transcript.scrollTop).toBe(1900));
    scrollCalls.mockClear();
    grow(2900);
    await waitFor(() => expect(scrollCalls).toHaveBeenCalledWith(expect.objectContaining({ top: 2900 })));
    expect(jumpButton()).not.toBeInTheDocument();
  });

  it("does not scroll when a run starts that the operator did not send, such as a queued run", async () => {
    const { transcript, scrollCalls } = await openThread();
    scrollTo(transcript, 600);
    await screen.findByRole("button", { name: "Jump to latest" });
    scrollCalls.mockClear();

    act(() => {
      onChatEvent?.({
        ...envelope,
        run_id: "run-queued",
        message_id: "message-queued",
        part_id: "part-queued",
        type: "turn_start",
        event_id: "queued-1",
        state: "running",
      });
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 60));
    });

    expect(await screen.findByRole("button", { name: "Stop run" })).toBeInTheDocument();
    expect(scrollCalls).not.toHaveBeenCalled();
    expect(transcript.scrollTop).toBe(600);
    expect(jumpButton()).toBeInTheDocument();
  });

  it("scrolls to the newest output when the operator clicks the Send button", async () => {
    const { transcript, scrollCalls } = await openThread();
    scrollTo(transcript, 600);
    await screen.findByRole("button", { name: "Jump to latest" });
    scrollCalls.mockClear();

    const draft = screen.getByRole("textbox", { name: "Message Sourcecado" });
    fireEvent.change(draft, { target: { value: "Find the founders" } });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));

    // The library raises the send event after its own async step.
    await waitFor(() => expect(scrollCalls).toHaveBeenCalledTimes(1));
    expect(scrollCalls).toHaveBeenCalledWith(expect.objectContaining({ top: 2000 }));
    await waitFor(() => expect(jumpButton()).not.toBeInTheDocument());
  });

  it("scrolls to the newest output when the operator presses Enter in the box", async () => {
    const { transcript, scrollCalls } = await openThread();
    scrollTo(transcript, 600);
    await screen.findByRole("button", { name: "Jump to latest" });
    scrollCalls.mockClear();

    const draft = screen.getByRole("textbox", { name: "Message Sourcecado" });
    fireEvent.change(draft, { target: { value: "Find the founders" } });
    fireEvent.keyDown(draft, { key: "Enter" });

    // The library raises the send event after its own async step.
    await waitFor(() => expect(scrollCalls).toHaveBeenCalledTimes(1));
    expect(scrollCalls).toHaveBeenCalledWith(expect.objectContaining({ top: 2000 }));
  });

  it("does not scroll when the operator presses Enter in an empty box", async () => {
    const { transcript, scrollCalls } = await openThread();
    scrollTo(transcript, 600);
    await screen.findByRole("button", { name: "Jump to latest" });
    scrollCalls.mockClear();

    const draft = screen.getByRole("textbox", { name: "Message Sourcecado" });
    fireEvent.keyDown(draft, { key: "Enter" });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 60));
    });

    expect(scrollCalls).not.toHaveBeenCalled();
    expect(transcript.scrollTop).toBe(600);
  });

  it("does not scroll when the operator queues a message during a run", async () => {
    const { transcript, scrollCalls } = await openThread();
    act(() => {
      onChatEvent?.({
        ...envelope,
        run_id: "run-active",
        message_id: "message-active",
        part_id: "part-active",
        type: "turn_start",
        event_id: "active-1",
        state: "running",
      });
    });
    await screen.findByRole("button", { name: "Stop run" });
    scrollTo(transcript, 600);
    await screen.findByRole("button", { name: "Jump to latest" });
    scrollCalls.mockClear();

    const draft = screen.getByRole("textbox", { name: "Message Sourcecado" });
    fireEvent.change(draft, { target: { value: "queue this one" } });
    fireEvent.keyDown(draft, { key: "Enter" });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 60));
    });

    expect(draft).toHaveValue("");
    expect(scrollCalls).not.toHaveBeenCalled();
    expect(transcript.scrollTop).toBe(600);
    expect(jumpButton()).toBeInTheDocument();
  });

  it("puts focus on a waiting approval after Jump to latest, so Allow and Deny are a few Tabs away", async () => {
    const { transcript } = await openThread(APPROVAL_CONVERSATION);
    const heading = screen.getByRole("heading", { level: 2 });
    transcript.focus();
    scrollTo(transcript, 600);

    fireEvent.click(await screen.findByRole("button", { name: "Jump to latest" }));

    expect(heading).toHaveFocus();
  });

  it("keeps the thread scroll and the draft when the inspector opens and closes", async () => {
    const { transcript } = await openThread();
    scrollTo(transcript, 600);
    const draft = screen.getByRole("textbox", { name: "Message Sourcecado" });
    fireEvent.change(draft, { target: { value: "keep this draft" } });

    const source = screen.getByRole("button", { name: SOURCE_CHIP });
    fireEvent.click(source);
    const inspector = screen.getByRole("complementary", { name: "Inspector" });
    expect(screen.getByRole("textbox", { name: "Message Sourcecado" })).toBe(draft);
    expect(draft).toHaveValue("keep this draft");
    expect(transcript.scrollTop).toBe(600);

    // The operator reads on while the panel is open.
    scrollTo(transcript, 900);
    fireEvent.click(within(inspector).getByRole("button", { name: "Close inspector" }));
    await waitFor(() => expect(source).toHaveFocus());
    expect(screen.getByRole("textbox", { name: "Message Sourcecado" })).toBe(draft);
    expect(draft).toHaveValue("keep this draft");
    expect(transcript.scrollTop).toBe(900);
  });

  it("returns focus to the inspector trigger without scrolling the thread to it", async () => {
    await openThread();
    const focus = vi.spyOn(HTMLElement.prototype, "focus");
    const source = screen.getByRole("button", { name: SOURCE_CHIP });
    fireEvent.click(source);
    focus.mockClear();

    fireEvent.click(
      within(screen.getByRole("complementary", { name: "Inspector" })).getByRole("button", {
        name: "Close inspector",
      }),
    );
    await waitFor(() => expect(source).toHaveFocus());
    const refocus = focus.mock.instances.findIndex((node) => node === source);
    expect(refocus).toBeGreaterThanOrEqual(0);
    expect(focus.mock.calls[refocus]?.[0]).toEqual({ preventScroll: true });
    focus.mockRestore();
  });
});

function pendingApproval(): ToolCallMessagePartProps {
  return {
    type: "tool-call",
    toolCallId: "call-approval",
    toolName: "gmail_draft",
    args: { to: "alyssa@example.com", subject: "Hello", body: "Full body" },
    argsText: "{}",
    approval: { id: "approval-1", reason: "Draft creation needs approval." },
    providerMetadata: {
      sourcecado: {
        approvalState: "pending",
        actor: null,
        requestedAt: "2026-08-25T12:00:00Z",
        resolvedAt: null,
        scope: "once",
        executionStatus: "not_run",
        executionError: null,
      },
    },
    addResult: vi.fn(),
    resume: vi.fn(),
    respondToApproval: vi.fn(),
    status: { type: "requires-action", reason: "tool-calls" },
  } as ToolCallMessagePartProps;
}

describe("Approval card mount", () => {
  it("takes focus without scrolling, so it cannot pull a reader down the thread", () => {
    const focus = vi.spyOn(HTMLElement.prototype, "focus");
    render(<ApprovalCard part={pendingApproval()} onDecision={vi.fn()} />);

    const heading = focus.mock.instances.findIndex(
      (node) => (node as HTMLElement).tagName === "H2",
    );
    expect(heading).toBeGreaterThanOrEqual(0);
    expect(focus.mock.calls[heading]?.[0]).toEqual({ preventScroll: true });
    focus.mockRestore();
  });
});
