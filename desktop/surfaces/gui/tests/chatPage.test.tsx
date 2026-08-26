import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ChatPage } from "../src/routes/ChatPage";

const api = vi.hoisted(() => ({
  getHealth: vi.fn(),
  getSessions: vi.fn(),
  getPersona: vi.fn(),
  getSchedule: vi.fn(),
  getGmail: vi.fn(),
  getConnectors: vi.fn(),
  getSettings: vi.fn(),
  getInbox: vi.fn(),
  getSession: vi.fn(),
  openChat: vi.fn(),
}));
const writeText = vi.fn();
const chatSend = vi.fn();
const chatApprove = vi.fn();
const chatCancel = vi.fn();
const chatQueue = vi.fn();
const chatRecover = vi.fn();
let onChatEvent: ((event: Record<string, unknown>) => void) | undefined;

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((next) => {
    resolve = next;
  });
  return { promise, resolve };
}

vi.mock("../src/api", () => ({
  connectCalendar: vi.fn(),
  connectDrive: vi.fn(),
  connectGmail: vi.fn(),
  connectGranola: vi.fn(),
  disconnectGmail: vi.fn(),
  getHealth: api.getHealth,
  getSessions: api.getSessions,
  getPersona: api.getPersona,
  getSchedule: api.getSchedule,
  getGmail: api.getGmail,
  getConnectors: api.getConnectors,
  getSettings: api.getSettings,
  getInbox: api.getInbox,
  getSession: api.getSession,
  hasToken: () => true,
  openChat: api.openChat,
  resolveInbox: vi.fn(),
  runScheduleJob: vi.fn(),
  setPersona: vi.fn(),
}));

describe("ChatPage Warm Operator thread", () => {
  beforeEach(() => {
    window.localStorage.clear();
    writeText.mockReset();
    chatSend.mockReset();
    chatApprove.mockReset();
    chatCancel.mockReset();
    chatQueue.mockReset();
    chatRecover.mockReset();
    onChatEvent = undefined;
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
      configurable: true,
      value: vi.fn(),
    });
    api.getHealth.mockResolvedValue({
      status: "ok",
      piece: "desktop",
      slice: 1,
      model: "operator-model",
    });
    api.getSessions.mockResolvedValue({
      sessions: [],
      open_id: "thread-alpha",
    });
    api.getPersona.mockResolvedValue({
      id: "sourcing",
      name: "Sourcing Operator",
      tools: [],
    });
    api.getSchedule.mockResolvedValue({ jobs: [], runs: [] });
    api.getGmail.mockResolvedValue({ connected: false, email: null });
    api.getConnectors.mockResolvedValue({ connectors: [] });
    api.getSettings.mockResolvedValue({
      persona: { id: "sourcing", name: "Sourcing Operator" },
      model: "operator-model",
      gmail: { connected: false, email: null },
      apollo: { configured: false },
    });
    api.getInbox.mockResolvedValue({ items: [] });
    api.openChat.mockImplementation((callback) => {
      onChatEvent = callback;
      return {
      send: chatSend,
      approve: chatApprove,
      cancel: chatCancel,
      queue: chatQueue,
      recover: chatRecover,
      close: vi.fn(),
      };
    });
  });

  it("renders restored assistant GFM as semantic content", async () => {
    api.getSession.mockResolvedValue({
      id: "thread-alpha",
      title: "Recruiting",
      messages: [
        {
          role: "assistant",
          content: [
            "## Shortlist",
            "",
            "- [Alyssa](https://example.com/alyssa)",
            "- Mateo",
            "",
            "| Name | Fit |",
            "| --- | --- |",
            "| Alyssa | High |",
            "",
            "Use `apollo_people_search`.",
            "",
            "```text",
            "Review-ready draft",
            "```",
          ].join("\n"),
        },
      ],
      events: [],
    });

    render(<ChatPage sessionId="thread-alpha" />);

    expect(
      await screen.findByRole("heading", { level: 2, name: "Shortlist" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("list")).toBeInTheDocument();
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Alyssa" })).toHaveAttribute(
      "href",
      "https://example.com/alyssa",
    );
    expect(screen.getByText("apollo_people_search").tagName).toBe("CODE");
    expect(screen.getByText("Review-ready draft").tagName).toBe("CODE");
  });

  it("renders stable assistant-ui message identities in a focusable non-live log", async () => {
    api.getSession.mockResolvedValue({
      id: "thread-alpha",
      title: "Recruiting",
      messages: [
        { role: "user", content: "Find recruiting leads." },
        { role: "assistant", content: "I found three leads." },
      ],
      events: [],
    });

    render(<ChatPage sessionId="thread-alpha" />);

    const log = await screen.findByRole("log", { name: "Conversation" });
    expect(log).toHaveAttribute("tabindex", "0");
    expect(log).not.toHaveAttribute("aria-live");
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
      "Recruiting",
    );
    await waitFor(() => {
      expect(
        log.querySelector('[data-message-id="thread-alpha:legacy:0"]'),
      ).toHaveClass("sourcecado-user-message");
      expect(
        log.querySelector('[data-message-id="thread-alpha:legacy:1"]'),
      ).toHaveClass("sourcecado-assistant-message");
    });
  });

  it("copies completed assistant prose without tool arguments or results", async () => {
    api.getSession.mockResolvedValue({
      id: "thread-alpha",
      title: "Recruiting",
      messages: [
        {
          role: "assistant",
          content: "I prepared a review-ready draft.",
          tool_calls: [
            {
              id: "call-draft-1",
              function: {
                name: "gmail_create_draft",
                arguments: JSON.stringify({ raw_secret: "PRIVATE_ARGUMENT" }),
              },
            },
          ],
        },
        {
          role: "tool",
          tool_call_id: "call-draft-1",
          content: JSON.stringify({ raw_secret: "PRIVATE_RESULT" }),
        },
      ],
      events: [],
    });

    const { container } = render(<ChatPage sessionId="thread-alpha" />);

    const copy = await screen.findByRole("button", { name: "Copy response" });
    expect(container).not.toHaveTextContent("PRIVATE_ARGUMENT");
    expect(container).not.toHaveTextContent("PRIVATE_RESULT");
    copy.click();
    expect(writeText).toHaveBeenCalledWith(
      "I prepared a review-ready draft.",
    );
  });

  it("moves from a transcript skeleton to sourcing-specific starters", async () => {
    const conversation = deferred<{
      id: string;
      title: string | null;
      messages: never[];
      events: never[];
    }>();
    api.getSession.mockReturnValue(conversation.promise);

    render(<ChatPage sessionId="thread-alpha" />);

    expect(screen.getByRole("log", { name: "Conversation" })).toHaveAttribute(
      "aria-busy",
      "true",
    );
    expect(screen.getByLabelText("Loading conversation")).toBeInTheDocument();

    await act(async () => {
      conversation.resolve({
        id: "thread-alpha",
        title: null,
        messages: [],
        events: [],
      });
    });

    const starter = await screen.findByRole("button", {
      name: "Build a candidate shortlist",
    });
    fireEvent.click(starter);
    expect(screen.getByRole("textbox", { name: "Message Sourcecado" })).toHaveValue(
      "Build a candidate shortlist for this week’s highest-priority role.",
    );
  });

  it("keeps the draft through a contextual load failure and retry", async () => {
    api.getSession
      .mockRejectedValueOnce(new Error("session 500 PRIVATE_PATH"))
      .mockResolvedValueOnce({
        id: "thread-alpha",
        title: "Recovered thread",
        messages: [
          { role: "assistant", content: "Restored after retry." },
        ],
        events: [],
      });

    const { container } = render(<ChatPage sessionId="thread-alpha" />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("We couldn’t load this conversation");
    expect(alert).toHaveTextContent("Your draft is still here");
    expect(container).not.toHaveTextContent("PRIVATE_PATH");
    const composer = screen.getByRole("textbox", { name: "Message Sourcecado" });
    fireEvent.change(composer, { target: { value: "Keep this sourcing draft" } });
    fireEvent.click(
      screen.getByRole("button", { name: "Retry loading conversation" }),
    );

    expect(await screen.findByText("Restored after retry.")).toBeInTheDocument();
    expect(composer).toHaveValue("Keep this sourcing draft");
    expect(api.getSession).toHaveBeenCalledTimes(2);
  });

  it("keeps available history visible beside a recoverable history notice", async () => {
    api.getSession.mockResolvedValue({
      id: "thread-alpha",
      title: "Partially restored",
      messages: [{ role: "user", content: "Keep this visible." }],
      events: [
        {
          type: "error",
          message: "Unsupported chat event version 99.",
          notice: { code: "unsupported_version", recoverable: true },
          session_id: "thread-alpha",
        },
      ],
    });

    render(<ChatPage sessionId="thread-alpha" />);

    expect(await screen.findByText("Keep this visible.")).toBeInTheDocument();
    const notice = screen.getByRole("note");
    expect(notice).toHaveTextContent("Some conversation history is unavailable");
    expect(notice).toHaveTextContent("Unsupported chat event version 99");
    expect(notice).toHaveTextContent("Available messages are shown");
  });

  it("keeps isolated drafts when switching away from and back to a thread", async () => {
    api.getSession.mockImplementation(async (id: string) => ({
      id,
      title: id === "thread-alpha" ? "Alpha" : "Beta",
      messages: [],
      events: [],
    }));

    const view = render(<ChatPage sessionId="thread-alpha" />);
    const alphaComposer = await screen.findByRole("textbox", {
      name: "Message Sourcecado",
    });
    await screen.findByRole("heading", { level: 1, name: "Alpha" });
    fireEvent.change(alphaComposer, { target: { value: "Alpha sourcing draft" } });

    view.rerender(<ChatPage sessionId="thread-beta" />);
    await screen.findByRole("heading", { level: 1, name: "Beta" });
    const betaComposer = screen.getByRole("textbox", { name: "Message Sourcecado" });
    expect(betaComposer).toHaveValue("");
    fireEvent.change(betaComposer, { target: { value: "Beta sourcing draft" } });

    view.rerender(<ChatPage sessionId="thread-alpha" />);
    await screen.findByRole("heading", { level: 1, name: "Alpha" });
    await waitFor(() =>
      expect(
        screen.getByRole("textbox", { name: "Message Sourcecado" }),
      ).toHaveValue("Alpha sourcing draft"),
    );
  });

  it("sends when idle and keeps queued submission available while running", async () => {
    api.getSession.mockResolvedValue({
      id: "thread-alpha",
      title: "Alpha",
      messages: [],
      events: [],
    });
    render(<ChatPage sessionId="thread-alpha" />);
    await screen.findByRole("heading", { level: 1, name: "Alpha" });
    const composer = screen.getByRole("textbox", { name: "Message Sourcecado" });
    fireEvent.change(composer, { target: { value: "Find five candidates" } });
    const send = screen.getByRole("button", { name: "Send message" });
    expect(send).toBeEnabled();
    fireEvent.click(send);

    await waitFor(() =>
    expect(chatSend).toHaveBeenCalledWith("Find five candidates", "thread-alpha"),
    );
    expect(screen.getByRole("status")).toHaveTextContent(
      "Run started",
    );
    expect(composer).not.toBeDisabled();
    fireEvent.change(composer, {
      target: { value: "Preserve this next instruction" },
    });
    expect(composer).toHaveValue("Preserve this next instruction");
    expect(screen.getByRole("button", { name: "Send message" })).toBeEnabled();
    expect(screen.getByText("You can keep drafting while Sourcecado works.")).toBeInTheDocument();

    act(() => {
      onChatEvent?.({
        version: 2,
        type: "turn_start",
        session_id: "thread-alpha",
        run_id: "run-1",
        event_id: "event-1",
        message_id: "assistant-1",
        part_id: "assistant-part-1",
        state: "running",
      });
      onChatEvent?.({
        version: 2,
        type: "assistant_delta",
        session_id: "thread-alpha",
        run_id: "run-1",
        event_id: "event-2",
        message_id: "assistant-1",
        part_id: "assistant-part-1",
        delta: "Working on it.",
      });
    });

    expect(await screen.findByText("Working on it.")).toBeInTheDocument();
    expect(composer).toHaveValue("Preserve this next instruction");
    expect(
      Array.from(
        screen
          .getByRole("log", { name: "Conversation" })
          .querySelectorAll<HTMLElement>("[data-message-id]"),
      ).map((element) => element.dataset.messageId),
    ).toEqual(["thread-alpha:legacy:0", "assistant-1"]);
  });

  it("stops the addressed active run without losing the composer draft", async () => {
    api.getSession.mockResolvedValue({
      id: "thread-alpha",
      title: "Alpha",
      messages: [],
      events: [],
    });
    render(<ChatPage sessionId="thread-alpha" />);
    await screen.findByRole("heading", { level: 1, name: "Alpha" });
    const composer = screen.getByRole("textbox", { name: "Message Sourcecado" });
    fireEvent.change(composer, { target: { value: "Keep this next prompt" } });
    act(() => {
      onChatEvent?.({
        version: 2,
        type: "turn_start",
        session_id: "thread-alpha",
        run_id: "run-cancel-me",
        event_id: "cancel-event-start",
        message_id: "assistant-cancel",
        part_id: "assistant-cancel-part",
        state: "running",
      });
    });

    fireEvent.click(await screen.findByRole("button", { name: "Stop run" }));
    expect(chatCancel).toHaveBeenCalledWith("thread-alpha", "run-cancel-me");
    expect(composer).not.toBeDisabled();
    expect(composer).toHaveValue("Keep this next prompt");

    act(() => {
      onChatEvent?.({
        version: 2,
        type: "turn_stopping",
        session_id: "thread-alpha",
        run_id: "run-cancel-me",
        event_id: "cancel-event-stopping",
        message_id: "assistant-cancel",
        part_id: "assistant-cancel-part",
        state: "stopping",
        message: "Stopping after the current action finishes.",
      });
    });
    expect(screen.getByRole("status")).toHaveTextContent(
      "Stopping after the current action finishes",
    );

    act(() => {
      onChatEvent?.({
        version: 2,
        type: "turn_stopped",
        session_id: "thread-alpha",
        run_id: "run-cancel-me",
        event_id: "cancel-event-stopped",
        message_id: "assistant-cancel",
        part_id: "assistant-cancel-part",
        state: "stopped",
        text: "",
        message: "Run cancelled.",
      });
    });
    expect(screen.getByRole("status")).toHaveTextContent("Run cancelled.");
    expect(screen.queryByRole("button", { name: "Stop run" })).not.toBeInTheDocument();
    expect(composer).toHaveValue("Keep this next prompt");
  });

  it("restores a cancelled run as a durable transcript receipt", async () => {
    const envelope = {
      version: 2,
      session_id: "thread-alpha",
      run_id: "run-cancelled",
      message_id: "assistant-cancelled",
      part_id: "assistant-cancelled-part",
    } as const;
    api.getSession.mockResolvedValue({
      id: "thread-alpha",
      title: "Cancelled history",
      messages: [],
      events: [
        {
          ...envelope,
          type: "turn_start",
          event_id: "restore-cancel-1",
          state: "running",
        },
        {
          ...envelope,
          type: "assistant_delta",
          event_id: "restore-cancel-2",
          delta: "Partial shortlist",
        },
        {
          ...envelope,
          type: "turn_stopping",
          event_id: "restore-cancel-3",
          state: "stopping",
          message: "Stopping the current run.",
        },
        {
          ...envelope,
          type: "turn_stopped",
          event_id: "restore-cancel-4",
          state: "stopped",
          text: "Partial shortlist",
          message: "Run cancelled.",
        },
      ],
    });

    render(<ChatPage sessionId="thread-alpha" />);

    expect(await screen.findByText("Partial shortlist")).toBeInTheDocument();
    expect(screen.getByText("Run cancelled.")).toHaveClass(
      "sourcecado-terminal-receipt",
    );
    expect(screen.queryByRole("button", { name: "Stop run" })).not.toBeInTheDocument();
  });

  it.each([
    [
      "completion",
      {
        version: 2,
        type: "turn_end",
        session_id: "thread-alpha",
        run_id: "run-1",
        event_id: "terminal-complete",
        message_id: "assistant-1",
        part_id: "assistant-part-1",
        state: "complete",
        text: "Finished.",
      },
      "Run complete.",
    ],
    [
      "failure",
      {
        version: 2,
        type: "error",
        session_id: "thread-alpha",
        run_id: "run-1",
        event_id: "terminal-failed",
        message_id: "assistant-1",
        part_id: "assistant-part-1",
        state: "failed",
        message: "Provider failed",
      },
      "Run failed.",
    ],
    [
      "cancellation",
      {
        version: 2,
        type: "turn_end",
        session_id: "thread-alpha",
        run_id: "run-1",
        event_id: "terminal-stopped",
        message_id: "assistant-1",
        part_id: "assistant-part-1",
        state: "stopped",
        text: "Stopped.",
      },
      "Run cancelled.",
    ],
  ] as const)("announces run %s outside the transcript", async (_label, terminal, expected) => {
    api.getSession.mockResolvedValue({
      id: "thread-alpha",
      title: "Alpha",
      messages: [],
      events: [],
    });
    render(<ChatPage sessionId="thread-alpha" />);
    await screen.findByRole("heading", { level: 1, name: "Alpha" });

    act(() => {
      onChatEvent?.({
        version: 2,
        type: "turn_start",
        session_id: "thread-alpha",
        run_id: "run-1",
        event_id: "event-start",
        message_id: "assistant-1",
        part_id: "assistant-part-1",
        state: "running",
      });
    });
    expect(screen.getByRole("status")).toHaveTextContent("Run started.");
    act(() => onChatEvent?.(terminal));
    expect(screen.getByRole("status")).toHaveTextContent(expected);
    expect(screen.getByRole("log", { name: "Conversation" })).not.toHaveAttribute(
      "aria-live",
    );
  });

  it("keeps background deltas isolated and restores them when returning to the thread", async () => {
    api.getSession.mockImplementation(async (id: string) => ({
      id,
      title: id === "thread-alpha" ? "Alpha" : "Beta",
      messages: [
        {
          role: "assistant",
          content: id === "thread-alpha" ? "Alpha seed" : "Beta seed",
        },
      ],
      events: [],
    }));
    const view = render(<ChatPage sessionId="thread-alpha" />);
    await screen.findByText("Alpha seed");

    view.rerender(<ChatPage sessionId="thread-beta" />);
    await screen.findByText("Beta seed");
    act(() => {
      onChatEvent?.({
        version: 2,
        type: "turn_start",
        session_id: "thread-alpha",
        run_id: "run-background",
        event_id: "background-start",
        message_id: "background-assistant",
        part_id: "background-part",
        state: "running",
      });
      onChatEvent?.({
        version: 2,
        type: "assistant_delta",
        session_id: "thread-alpha",
        run_id: "run-background",
        event_id: "background-delta",
        message_id: "background-assistant",
        part_id: "background-part",
        delta: "Background alpha delta",
      });
    });

    expect(screen.getByRole("log", { name: "Conversation" })).toHaveTextContent(
      "Beta seed",
    );
    expect(screen.queryByText("Background alpha delta")).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("");

    view.rerender(<ChatPage sessionId="thread-alpha" />);
    expect(await screen.findByText("Background alpha delta")).toBeInTheDocument();
    expect(api.getSession.mock.calls.filter(([id]) => id === "thread-alpha")).toHaveLength(1);
  });

  it("renders restored canonical tool activity without exposing raw JSON", async () => {
    const envelope = {
      version: 2,
      session_id: "thread-alpha",
      run_id: "run-tool",
      message_id: "assistant-tool",
      part_id: "assistant-tool-part",
    } as const;
    api.getSession.mockResolvedValue({
      id: "thread-alpha",
      title: "Tool history",
      messages: [],
      events: [
        {
          ...envelope,
          type: "turn_start",
          event_id: "tool-event-1",
          state: "running",
        },
        {
          ...envelope,
          type: "tool_started",
          event_id: "tool-event-2",
          id: "call-drive",
          name: "drive_search",
          arguments: { private_query: "DO_NOT_RENDER_ARGUMENT" },
        },
        {
          ...envelope,
          type: "tool_finished",
          event_id: "tool-event-3",
          id: "call-drive",
          name: "drive_search",
          ok: true,
          result: { private_rows: "DO_NOT_RENDER_RESULT" },
        },
        {
          ...envelope,
          type: "turn_end",
          event_id: "tool-event-4",
          state: "complete",
          text: "",
        },
      ],
    });

    const { container } = render(<ChatPage sessionId="thread-alpha" />);

    const activity = await screen.findByRole("button", {
      name: "Searched Drive · Completed",
    });
    expect(activity).toHaveAttribute("aria-expanded", "false");
    expect(container).not.toHaveTextContent("DO_NOT_RENDER_ARGUMENT");
    expect(container).not.toHaveTextContent("DO_NOT_RENDER_RESULT");
  });

  it("renders a safe progressive approval and routes its decision", async () => {
    const envelope = {
      version: 2,
      session_id: "thread-alpha",
      run_id: "run-approval",
      message_id: "assistant-approval",
      part_id: "assistant-approval-part",
    } as const;
    api.getSession.mockResolvedValue({
      id: "thread-alpha",
      title: "Approval history",
      messages: [],
      events: [
        {
          ...envelope,
          type: "turn_start",
          event_id: "approval-event-1",
          state: "running",
        },
        {
          ...envelope,
          type: "permission_required",
          event_id: "approval-event-2",
          id: "approval-draft",
          name: "gmail_create_draft",
          arguments: {
            to: "legacy@example.com",
            subject: "Legacy draft",
            body: "Safe legacy body preview",
          },
          reason: "Creating a draft changes Gmail.",
        },
      ],
    });

    const { container } = render(<ChatPage sessionId="thread-alpha" />);

    expect(
      await screen.findByRole("heading", {
        level: 2,
        name: "Prepared Gmail draft",
      }),
    ).toBeInTheDocument();
    expect(screen.getByText("Creating a draft changes Gmail.")).toBeInTheDocument();
    expect(screen.getByText("legacy@example.com")).toBeInTheDocument();
    expect(screen.getByText("Legacy draft")).toBeInTheDocument();
    expect(screen.getByText("Safe legacy body preview")).toBeInTheDocument();
    expect(screen.getByText("Not sent")).toBeInTheDocument();
    expect(container).not.toHaveTextContent("Sent successfully");
    fireEvent.click(screen.getByRole("button", { name: "Allow once" }));
    await waitFor(() =>
      expect(chatApprove).toHaveBeenCalledWith("approval-draft", "allow"),
    );
  });

  it("ignores a stale load response after the route selects another thread", async () => {
    const alpha = deferred<{
      id: string;
      title: string;
      messages: Array<{ role: string; content: string }>;
      events: never[];
    }>();
    api.getSession.mockImplementation((id: string) =>
      id === "thread-alpha"
        ? alpha.promise
        : Promise.resolve({
            id: "thread-beta",
            title: "Beta",
            messages: [{ role: "assistant", content: "Beta response" }],
            events: [],
          }),
    );

    const view = render(<ChatPage sessionId="thread-alpha" />);
    view.rerender(<ChatPage sessionId="thread-beta" />);
    expect(await screen.findByText("Beta response")).toBeInTheDocument();
    await act(async () => {
      alpha.resolve({
        id: "thread-alpha",
        title: "Alpha",
        messages: [{ role: "assistant", content: "Late alpha response" }],
        events: [],
      });
    });

    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Beta");
    expect(screen.getByText("Beta response")).toBeInTheDocument();
    expect(screen.queryByText("Late alpha response")).not.toBeInTheDocument();
  });

  it("restores the sidecar-authoritative queue with stable item state", async () => {
    api.getSession.mockResolvedValue({
      id: "thread-alpha",
      title: "Queued work",
      messages: [],
      events: [],
      queue_paused: false,
      queue: [
        {
          id: "queue-restored-1",
          session_id: "thread-alpha",
          text: "Follow up with the shortlist",
          position: 0,
          state: "waiting",
          error: null,
          created_at: "2026-08-25T12:00:00Z",
          updated_at: "2026-08-25T12:00:00Z",
        },
      ],
    });

    render(<ChatPage sessionId="thread-alpha" />);

    const queue = await screen.findByRole("region", { name: "Queued messages" });
    expect(queue).toHaveTextContent("Follow up with the shortlist");
    expect(queue).toHaveTextContent("Waiting");
    expect(queue.querySelector('[data-queue-item-id="queue-restored-1"]')).toBeInTheDocument();
  });

  it("submits into the persisted queue while a run is active", async () => {
    api.getSession.mockResolvedValue({
      id: "thread-alpha",
      title: "Active queue",
      messages: [],
      events: [],
      queue: [],
      queue_paused: false,
    });
    render(<ChatPage sessionId="thread-alpha" />);
    await screen.findByRole("heading", { level: 1, name: "Active queue" });
    act(() => {
      onChatEvent?.({
        version: 2,
        type: "turn_start",
        session_id: "thread-alpha",
        run_id: "run-active-queue",
        event_id: "active-queue-start",
        message_id: "active-queue-message",
        part_id: "active-queue-part",
        state: "running",
      });
    });
    const composer = screen.getByRole("textbox", { name: "Message Sourcecado" });
    fireEvent.change(composer, { target: { value: "Queue this follow-up" } });
    const send = screen.getByRole("button", { name: "Send message" });

    expect(send).toBeEnabled();
    fireEvent.click(send);
    await waitFor(() => expect(chatQueue).toHaveBeenCalledOnce());
    expect(chatQueue.mock.calls[0]?.[0]).toMatchObject({
      type: "queue_add",
      session_id: "thread-alpha",
      text: "Queue this follow-up",
    });
    expect(chatQueue.mock.calls[0]?.[0].command_id).toEqual(expect.any(String));
    expect(chatQueue.mock.calls[0]?.[0].item_id).toEqual(expect.any(String));
    expect(chatSend).not.toHaveBeenCalled();

    act(() => {
      onChatEvent?.({
        version: 2,
        type: "queue_snapshot",
        session_id: "thread-alpha",
        command_id: chatQueue.mock.calls[0]?.[0].command_id,
        status: "accepted",
        paused: false,
        items: [
          {
            id: chatQueue.mock.calls[0]?.[0].item_id,
            session_id: "thread-alpha",
            text: "Queue this follow-up",
            position: 0,
            state: "waiting",
            error: null,
            created_at: "2026-08-25T12:00:00Z",
            updated_at: "2026-08-25T12:00:00Z",
          },
        ],
      });
    });
    expect(await screen.findByText("Queue this follow-up")).toBeInTheDocument();
  });

  it("offers persisted retry and resume recovery for a paused failed item", async () => {
    api.getSession.mockResolvedValue({
      id: "thread-alpha",
      title: "Queue recovery",
      messages: [],
      events: [],
      queue_paused: true,
      queue: [
        {
          id: "failed-queue-item",
          session_id: "thread-alpha",
          text: "Retry this sourcing prompt",
          position: 0,
          state: "failed",
          error: "PRIVATE_PROVIDER_ERROR",
          created_at: "2026-08-25T12:00:00Z",
          updated_at: "2026-08-25T12:01:00Z",
        },
      ],
    });

    const { container } = render(<ChatPage sessionId="thread-alpha" />);
    await screen.findByText("Retry this sourcing prompt");
    expect(container).not.toHaveTextContent("PRIVATE_PROVIDER_ERROR");
    fireEvent.click(screen.getByRole("button", { name: "Retry queued message" }));
    expect(chatQueue.mock.calls[0]?.[0]).toMatchObject({
      type: "queue_retry",
      session_id: "thread-alpha",
      item_id: "failed-queue-item",
    });
    fireEvent.click(screen.getByRole("button", { name: "Resume queue" }));
    expect(chatQueue.mock.calls[1]?.[0]).toMatchObject({
      type: "queue_resume",
      session_id: "thread-alpha",
    });
  });

  it("edits, reorders, and removes queued messages with labeled controls", async () => {
    const queued = (id: string, text: string, position: number) => ({
      id,
      session_id: "thread-alpha",
      text,
      position,
      state: "waiting" as const,
      error: null,
      created_at: `2026-08-25T12:0${position}:00Z`,
      updated_at: `2026-08-25T12:0${position}:00Z`,
    });
    api.getSession.mockResolvedValue({
      id: "thread-alpha",
      title: "Queue controls",
      messages: [],
      events: [],
      queue_paused: false,
      queue: [queued("queue-first", "First prompt", 0), queued("queue-second", "Second prompt", 1)],
    });
    render(<ChatPage sessionId="thread-alpha" />);
    await screen.findByText("First prompt");

    fireEvent.click(screen.getByRole("button", { name: "Move Second prompt up" }));
    expect(chatQueue.mock.calls[0]?.[0]).toMatchObject({
      type: "queue_move",
      item_id: "queue-second",
      before_id: "queue-first",
    });

    fireEvent.click(screen.getByRole("button", { name: "Edit First prompt" }));
    const editor = screen.getByRole("textbox", { name: "Edit queued message" });
    fireEvent.change(editor, { target: { value: "First prompt revised" } });
    fireEvent.click(screen.getByRole("button", { name: "Save queued message" }));
    expect(chatQueue.mock.calls[1]?.[0]).toMatchObject({
      type: "queue_edit",
      item_id: "queue-first",
      text: "First prompt revised",
    });

    fireEvent.click(screen.getByRole("button", { name: "Remove First prompt" }));
    expect(chatQueue.mock.calls[2]?.[0]).toMatchObject({
      type: "queue_remove",
      item_id: "queue-first",
    });
  });

  it("groups restored tool activity into one collapsed humanized receipt", async () => {
    const envelope = {
      version: 2,
      session_id: "thread-alpha",
      run_id: "run-activity",
      message_id: "assistant-activity",
      part_id: "assistant-activity-part",
    } as const;
    api.getSession.mockResolvedValue({
      id: "thread-alpha",
      title: "Activity receipt",
      messages: [],
      queue: [],
      queue_paused: false,
      events: [
        { ...envelope, type: "turn_start", event_id: "activity-1", state: "running" },
        {
          ...envelope,
          type: "tool_started",
          event_id: "activity-2",
          id: "drive-call",
          name: "drive_search",
          arguments: { private_query: "DO_NOT_RENDER" },
          started_at: "2026-08-25T12:00:00.000Z",
        },
        {
          ...envelope,
          type: "tool_finished",
          event_id: "activity-3",
          id: "drive-call",
          name: "drive_search",
          ok: true,
          result: { private_rows: "DO_NOT_RENDER" },
          finished_at: "2026-08-25T12:00:01.000Z",
        },
        {
          ...envelope,
          type: "tool_started",
          event_id: "activity-4",
          id: "apollo-call",
          name: "apollo_search_people",
          arguments: { private_query: "DO_NOT_RENDER" },
          started_at: "2026-08-25T12:00:01.000Z",
        },
        {
          ...envelope,
          type: "tool_finished",
          event_id: "activity-5",
          id: "apollo-call",
          name: "apollo_search_people",
          ok: true,
          result: { private_rows: "DO_NOT_RENDER" },
          finished_at: "2026-08-25T12:00:02.400Z",
        },
        {
          ...envelope,
          type: "turn_end",
          event_id: "activity-6",
          state: "complete",
          text: "Two sources checked.",
        },
      ],
    });

    const { container } = render(<ChatPage sessionId="thread-alpha" />);

    const disclosure = await screen.findByRole("button", {
      name: /Checked 2 sources · 2s · Completed/,
    });
    expect(disclosure).toHaveAttribute("aria-expanded", "false");
    expect(container).not.toHaveTextContent("DO_NOT_RENDER");
    expect(screen.queryByText("Searched Drive")).not.toBeInTheDocument();
    fireEvent.click(disclosure);
    expect(disclosure).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("Searched Drive")).toBeInTheDocument();
    expect(screen.getByText("Searched Apollo")).toBeInTheDocument();
  });

  it("restores a resolved approval as a collapsed durable audit receipt", async () => {
    const envelope = {
      version: 2,
      session_id: "thread-alpha",
      run_id: "run-approved",
      message_id: "assistant-approved",
      part_id: "assistant-approved-part",
    } as const;
    api.getSession.mockResolvedValue({
      id: "thread-alpha",
      title: "Approval receipt",
      messages: [],
      queue: [],
      queue_paused: false,
      events: [
        { ...envelope, type: "turn_start", event_id: "approved-1", state: "running" },
        {
          ...envelope,
          type: "permission_required",
          event_id: "approved-2",
          id: "approval-draft",
          name: "gmail_draft",
          arguments: { to: "alyssa@example.com", subject: "Hello", body: "PRIVATE_BODY" },
          reason: "Creating a Gmail draft changes an external account.",
          requested_at: "2026-08-25T12:00:00.000Z",
          scope: "once",
        },
        {
          ...envelope,
          type: "tool_started",
          event_id: "approved-3",
          id: "approval-draft",
          name: "gmail_draft",
          arguments: { to: "alyssa@example.com", subject: "Hello", body: "PRIVATE_BODY" },
        },
        {
          ...envelope,
          type: "tool_finished",
          event_id: "approved-4",
          id: "approval-draft",
          name: "gmail_draft",
          ok: true,
          result: { draft_id: "draft-private" },
        },
        {
          ...envelope,
          type: "approval_resolved",
          event_id: "approved-5",
          id: "approval-draft",
          name: "gmail_draft",
          resolution: "allowed",
          decision: "allow",
          actor: "Fisher",
          requested_at: "2026-08-25T12:00:00.000Z",
          resolved_at: "2026-08-25T12:00:03.000Z",
          scope: "once",
          execution_status: "succeeded",
          execution_error: null,
        },
        {
          ...envelope,
          type: "turn_end",
          event_id: "approved-6",
          state: "complete",
          text: "Draft ready for review.",
        },
      ],
    });

    const { container } = render(<ChatPage sessionId="thread-alpha" />);

    const receipt = await screen.findByRole("button", {
      name: "Prepared Gmail draft · Allowed",
    });
    expect(receipt).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("button", { name: "Allow once" })).not.toBeInTheDocument();
    expect(container).not.toHaveTextContent("PRIVATE_BODY");
    fireEvent.click(receipt);
    expect(screen.getByText("Allowed by Fisher")).toBeInTheDocument();
    expect(screen.getByText("Scope: once")).toBeInTheDocument();
    expect(screen.getByText("Execution succeeded")).toBeInTheDocument();
  });

  it("shows a progressive pending approval and waits for durable resolution", async () => {
    const envelope = {
      version: 2,
      session_id: "thread-alpha",
      run_id: "run-pending",
      message_id: "assistant-pending",
      part_id: "assistant-pending-part",
    } as const;
    api.getSession.mockResolvedValue({
      id: "thread-alpha",
      title: "Pending approval",
      messages: [],
      queue: [],
      queue_paused: false,
      events: [
        { ...envelope, type: "turn_start", event_id: "pending-1", state: "running" },
        {
          ...envelope,
          type: "permission_required",
          event_id: "pending-2",
          id: "pending-draft",
          name: "gmail_draft",
          arguments: {
            to: "alyssa@example.com",
            subject: "Hello",
            body: "Hi Alyssa,\n\nHere is the sourcing update.",
          },
          reason: "Creating a Gmail draft changes an external account.",
          requested_at: "2026-08-25T12:00:00.000Z",
          scope: "once",
        },
      ],
    });

    const { container } = render(<ChatPage sessionId="thread-alpha" />);

    const title = await screen.findByRole("heading", {
      level: 2,
      name: "Prepared Gmail draft",
    });
    const composer = screen.getByRole("textbox", { name: "Message Sourcecado" });
    await waitFor(() => expect(title).toHaveFocus());
    expect(screen.getByText("alyssa@example.com")).toBeInTheDocument();
    expect(screen.getByText("Hello")).toBeInTheDocument();
    expect(screen.getByText(/Here is the sourcing update/)).toBeInTheDocument();
    expect(screen.getByText("Not sent")).toBeInTheDocument();
    expect(screen.getByText(/changes an external account/)).toBeInTheDocument();
    const approval = title.closest("section");
    expect(approval).not.toBeNull();
    expect(
      within(approval as HTMLElement).queryByRole("button", { name: /^Send$/i }),
    ).not.toBeInTheDocument();
    const details = screen.getByRole("button", {
      name: "Review full request and policy",
    });
    expect(details).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(details);
    expect(container).toHaveTextContent("Sourcecado will not send email");

    fireEvent.click(screen.getByRole("button", { name: "Allow once" }));
    expect(chatApprove).toHaveBeenCalledWith("pending-draft", "allow");
    expect(screen.getByText("Submitting decision…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Allow once" })).toBeDisabled();

    act(() => {
      onChatEvent?.({
        ...envelope,
        type: "approval_resolved",
        event_id: "pending-3",
        id: "pending-draft",
        name: "gmail_draft",
        resolution: "allowed",
        decision: "allow",
        actor: "operator",
        requested_at: "2026-08-25T12:00:00.000Z",
        resolved_at: "2026-08-25T12:00:02.000Z",
        scope: "once",
        execution_status: "succeeded",
        execution_error: null,
      });
    });
    expect(
      await screen.findByRole("button", {
        name: "Prepared Gmail draft · Allowed",
      }),
    ).toBeInTheDocument();
    await waitFor(() => expect(composer).toHaveFocus());
  });

  it("offers retry repair and continue on a failed activity row without exposing raw detail", async () => {
    const envelope = {
      version: 2,
      session_id: "thread-alpha",
      run_id: "run-recovery-ui",
      message_id: "message-recovery-ui",
      part_id: "part-recovery-ui",
    } as const;
    const failure = {
      class: "connector_auth",
      connector_id: "drive",
      source: "Google Drive",
      retry_safe: true,
      idempotent: true,
      summary: "Google Drive needs to be repaired before this source can be checked.",
      repair_route: "#/connections/drive",
      detail: "PRIVATE_RAW_TRANSPORT_ERROR",
      call_id: "call-drive-ui",
      run_id: "run-recovery-ui",
      session_id: "thread-alpha",
      state: "failed",
    } as const;
    api.getSession.mockResolvedValue({
      id: "thread-alpha",
      title: "Recovery UI",
      messages: [],
      queue: [],
      queue_paused: false,
      events: [
        { ...envelope, type: "turn_start", event_id: "recover-ui-1", state: "running" },
        {
          ...envelope,
          type: "tool_started",
          event_id: "recover-ui-2",
          id: "call-success-ui",
          name: "now",
          arguments: {},
        },
        {
          ...envelope,
          type: "tool_finished",
          event_id: "recover-ui-3",
          id: "call-success-ui",
          name: "now",
          ok: true,
          result: { iso: "noon" },
        },
        {
          ...envelope,
          type: "tool_started",
          event_id: "recover-ui-4",
          id: "call-drive-ui",
          name: "drive_search",
          arguments: { query: "Codeology" },
        },
        {
          ...envelope,
          type: "tool_finished",
          event_id: "recover-ui-5",
          id: "call-drive-ui",
          name: "drive_search",
          ok: false,
          result: { error: "PRIVATE_RAW_TRANSPORT_ERROR" },
          failure,
        },
        {
          ...envelope,
          type: "turn_end",
          event_id: "recover-ui-6",
          state: "partial",
          text: "Available result remains visible.",
        },
      ],
    });

    const { container } = render(<ChatPage sessionId="thread-alpha" />);
    const activity = await screen.findByRole("button", {
      name: /Checked 2 sources · Partial/,
    });
    fireEvent.click(activity);

    expect(screen.getByText("Checked current time")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Google Drive needs to be repaired before this source can be checked.",
      ),
    ).toBeInTheDocument();
    expect(container).not.toHaveTextContent("PRIVATE_RAW_TRANSPORT_ERROR");
    expect(screen.getByRole("link", { name: "Repair Google Drive" })).toHaveAttribute(
      "href",
      "#/connections/drive",
    );
    fireEvent.click(screen.getByRole("button", { name: "Retry failed step" }));
    expect(chatRecover.mock.calls[0]?.[0]).toMatchObject({
      type: "retry_failed_step",
      session_id: "thread-alpha",
      run_id: "run-recovery-ui",
      call_id: "call-drive-ui",
    });
    chatRecover.mockClear();
    fireEvent.click(screen.getByRole("button", { name: "Continue without Google Drive" }));
    expect(chatRecover.mock.calls[0]?.[0]).toMatchObject({
      type: "continue_without_source",
      session_id: "thread-alpha",
      run_id: "run-recovery-ui",
      call_id: "call-drive-ui",
    });
    fireEvent.click(screen.getByRole("button", { name: "Failure details" }));
    expect(container).toHaveTextContent("PRIVATE_RAW_TRANSPORT_ERROR");
  });

  it("restores a stable inline source target and inspects its provenance on demand", async () => {
    const envelope = {
      version: 2,
      session_id: "thread-alpha",
      run_id: "run-provenance",
      message_id: "message-provenance",
      part_id: "part-provenance",
    } as const;
    api.getSession.mockResolvedValue({
      id: "thread-alpha",
      title: "Provenance",
      messages: [],
      queue: [],
      queue_paused: false,
      events: [
        { ...envelope, type: "turn_start", event_id: "provenance-1", state: "running" },
        {
          ...envelope,
          type: "tool_started",
          event_id: "provenance-2",
          id: "call-provenance",
          name: "drive_search",
          arguments: { query: "Codeology" },
        },
        {
          ...envelope,
          type: "tool_finished",
          event_id: "provenance-3",
          id: "call-provenance",
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
          artifacts: [
            {
              id: "artifact-research-summary",
              artifact_type: "text",
              title: "Research summary",
              preview: "One matching brief",
              external_url: "https://example.com/artifacts/summary",
              stale: false,
              truncated: true,
            },
          ],
        },
        {
          ...envelope,
          type: "turn_end",
          event_id: "provenance-4",
          state: "complete",
          text: "I found the brief.",
        },
      ],
    });

    render(<ChatPage sessionId="thread-alpha" />);

    const source = await screen.findByRole("button", {
      name: "Source: Codeology brief",
    });
    const transcript = screen.getByRole("log");
    transcript.scrollTop = 96;
    fireEvent.click(source);
    const inspector = screen.getByRole("complementary", { name: "Inspector" });
    expect(transcript.scrollTop).toBe(96);
    expect(within(inspector).getByRole("heading", { level: 2 })).toHaveTextContent(
      "Codeology brief",
    );
    expect(within(inspector).getByText("Google Drive")).toBeInTheDocument();
    expect(within(inspector).getByRole("link", { name: "Open externally" })).toHaveAttribute(
      "href",
      "https://drive.google.com/file/codeology",
    );
    fireEvent.click(within(inspector).getByRole("button", { name: "Close inspector" }));
    await waitFor(() => expect(source).toHaveFocus());
    expect(transcript.scrollTop).toBe(96);

    fireEvent.click(source);
    const reopenedInspector = screen.getByRole("complementary", { name: "Inspector" });

    fireEvent.click(screen.getByRole("button", { name: "Artifact: Research summary" }));
    expect(within(reopenedInspector).getByRole("heading", { level: 2 })).toHaveTextContent(
      "Research summary",
    );
    expect(within(reopenedInspector).queryByText("Google Drive")).not.toBeInTheDocument();
    expect(within(reopenedInspector).getByText("Truncated")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Searched Drive · Completed" }));
    fireEvent.click(screen.getByRole("button", { name: "Inspect Searched Drive" }));
    expect(within(reopenedInspector).getByRole("heading", { level: 2 })).toHaveTextContent(
      "Searched Drive",
    );
    expect(within(reopenedInspector).getByText("Arguments")).toBeInTheDocument();
    expect(within(reopenedInspector).getByText("Result")).toBeInTheDocument();
  });

  it("restores an empty Apollo search and retains its query when adjusting criteria", async () => {
    const envelope = {
      version: 2,
      session_id: "thread-alpha",
      run_id: "run-apollo-empty",
      message_id: "message-apollo-empty",
      part_id: "part-apollo-empty",
    } as const;
    api.getSession.mockResolvedValue({
      id: "thread-alpha",
      title: "Apollo search",
      messages: [],
      queue: [],
      queue_paused: false,
      events: [
        { ...envelope, type: "turn_start", event_id: "apollo-empty-1", state: "running" },
        {
          ...envelope,
          type: "tool_started",
          event_id: "apollo-empty-2",
          id: "apollo-empty-call",
          name: "apollo_search_people",
          arguments: {
            organizationName: "Apollo",
            personTitles: ["CEO"],
          },
        },
        {
          ...envelope,
          type: "tool_finished",
          event_id: "apollo-empty-3",
          id: "apollo-empty-call",
          name: "apollo_search_people",
          ok: true,
          result: { people: [] },
        },
        {
          ...envelope,
          type: "turn_end",
          event_id: "apollo-empty-4",
          state: "complete",
          text: "",
        },
      ],
    });

    render(<ChatPage sessionId="thread-alpha" />);

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Searched Apollo · Completed",
      }),
    );
    expect(
      screen.getByRole("heading", { name: "No Apollo matches" }),
    ).toBeInTheDocument();
    expect(screen.getByText("CEO at Apollo", { exact: false })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Adjust criteria" }));
    expect(screen.getByRole("textbox", { name: "Message Sourcecado" })).toHaveValue(
      "Adjust the Apollo people search for CEO at Apollo.",
    );
  });

  it("renders equivalent Apollo shortlist states from restored and live events", async () => {
    const restored = {
      version: 2,
      session_id: "thread-alpha",
      run_id: "run-apollo-restored",
      message_id: "message-apollo-restored",
      part_id: "part-apollo-restored",
    } as const;
    api.getSession.mockResolvedValue({
      id: "thread-alpha",
      title: "Apollo parity",
      messages: [],
      queue: [],
      queue_paused: false,
      events: [
        { ...restored, type: "turn_start", event_id: "apollo-restored-1", state: "running" },
        {
          ...restored,
          type: "tool_started",
          event_id: "apollo-restored-2",
          id: "apollo-restored-call",
          name: "apollo_search_people",
          arguments: { organizationName: "Abridge", personTitles: ["Director"] },
        },
        {
          ...restored,
          type: "tool_finished",
          event_id: "apollo-restored-3",
          id: "apollo-restored-call",
          name: "apollo_search_people",
          ok: true,
          result: {
            people: [
              {
                apolloId: "restored-lee",
                firstName: "Restored",
                lastNameObfuscated: "Le***",
                title: "Director",
                organizationName: "Abridge",
                hasEmail: true,
                directPhoneStatus: null,
              },
            ],
          },
        },
        {
          ...restored,
          type: "turn_end",
          event_id: "apollo-restored-4",
          state: "complete",
          text: "",
        },
      ],
    });

    render(<ChatPage sessionId="thread-alpha" />);

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Searched Apollo · Completed",
      }),
    );
    expect(screen.getByText("Restored Le***")).toBeInTheDocument();
    expect(screen.getByText("Director at Abridge")).toBeInTheDocument();

    const live = {
      version: 2,
      session_id: "thread-alpha",
      run_id: "run-apollo-live",
      message_id: "message-apollo-live",
      part_id: "part-apollo-live",
    } as const;
    act(() => {
      onChatEvent?.({
        ...live,
        type: "turn_start",
        event_id: "apollo-live-1",
        state: "running",
      });
      onChatEvent?.({
        ...live,
        type: "tool_started",
        event_id: "apollo-live-2",
        id: "apollo-live-call",
        name: "apollo_search_people",
        arguments: { organizationName: "Acme", personTitles: ["VP Talent"] },
      });
    });
    expect(
      await screen.findByRole("list", { name: "Loading Apollo candidates" }),
    ).toBeInTheDocument();

    act(() => {
      onChatEvent?.({
        ...live,
        type: "tool_finished",
        event_id: "apollo-live-3",
        id: "apollo-live-call",
        name: "apollo_search_people",
        ok: true,
        result: {
          people: [
            {
              apolloId: "live-kim",
              firstName: "Live",
              lastNameObfuscated: "Ki***",
              title: "VP Talent",
              organizationName: "Acme",
              hasEmail: false,
              directPhoneStatus: "No",
            },
          ],
        },
      });
      onChatEvent?.({
        ...live,
        type: "turn_end",
        event_id: "apollo-live-4",
        state: "complete",
        text: "",
      });
    });

    expect(await screen.findByText("Live Ki***")).toBeInTheDocument();
    expect(screen.getByText("VP Talent at Acme")).toBeInTheDocument();
    expect(
      screen.queryByRole("list", { name: "Loading Apollo candidates" }),
    ).not.toBeInTheDocument();
  });

  it("renders equivalent not-sent Gmail draft artifacts from restored and live events", async () => {
    const restored = {
      version: 2,
      session_id: "thread-alpha",
      run_id: "run-gmail-restored",
      message_id: "message-gmail-restored",
      part_id: "part-gmail-restored",
    } as const;
    api.getSession.mockResolvedValue({
      id: "thread-alpha",
      title: "Gmail draft parity",
      messages: [],
      queue: [],
      queue_paused: false,
      events: [
        { ...restored, type: "turn_start", event_id: "gmail-restored-1", state: "running" },
        {
          ...restored,
          type: "tool_started",
          event_id: "gmail-restored-2",
          id: "gmail-restored-call",
          name: "gmail_draft",
          arguments: {
            to: "restored@example.com",
            subject: "Restored draft",
            body: "Restored body",
          },
        },
        {
          ...restored,
          type: "tool_finished",
          event_id: "gmail-restored-3",
          id: "gmail-restored-call",
          name: "gmail_draft",
          ok: true,
          result: { draft_id: "draft-restored", sent: false },
        },
        {
          ...restored,
          type: "turn_end",
          event_id: "gmail-restored-4",
          state: "complete",
          text: "",
        },
      ],
    });

    render(<ChatPage sessionId="thread-alpha" />);
    fireEvent.click(
      await screen.findByRole("button", {
        name: "Prepared Gmail draft · Completed",
      }),
    );
    expect(screen.getByText("Draft ID: draft-restored")).toBeInTheDocument();
    expect(screen.getByText("Restored body")).toBeInTheDocument();

    const live = {
      version: 2,
      session_id: "thread-alpha",
      run_id: "run-gmail-live",
      message_id: "message-gmail-live",
      part_id: "part-gmail-live",
    } as const;
    act(() => {
      onChatEvent?.({
        ...live,
        type: "turn_start",
        event_id: "gmail-live-1",
        state: "running",
      });
      onChatEvent?.({
        ...live,
        type: "tool_started",
        event_id: "gmail-live-2",
        id: "gmail-live-call",
        name: "gmail_draft",
        arguments: {
          to: "live@example.com",
          subject: "Live draft",
          body: "Live body",
        },
      });
    });
    expect(
      await screen.findByRole("heading", { name: "Creating Gmail draft" }),
    ).toBeInTheDocument();

    act(() => {
      onChatEvent?.({
        ...live,
        type: "tool_finished",
        event_id: "gmail-live-3",
        id: "gmail-live-call",
        name: "gmail_draft",
        ok: true,
        result: {
          id: "draft-live",
          to: "live@example.com",
          subject: "Live draft",
          drafted: true,
          sent: false,
        },
      });
      onChatEvent?.({
        ...live,
        type: "turn_end",
        event_id: "gmail-live-4",
        state: "complete",
        text: "",
      });
    });

    expect(await screen.findByText("Draft ID: draft-live")).toBeInTheDocument();
    expect(screen.getByText("Live body")).toBeInTheDocument();
    const artifacts = screen.getAllByLabelText("Gmail draft artifact");
    expect(artifacts).toHaveLength(2);
    for (const artifact of artifacts) {
      expect(within(artifact).getByText("Not sent")).toBeInTheDocument();
      expect(
        within(artifact).queryByRole("button", { name: /^Send$/i }),
      ).not.toBeInTheDocument();
    }
  });

  it("renders equivalent Calendar list states from restored and live events", async () => {
    const restored = {
      version: 2,
      session_id: "thread-alpha",
      run_id: "run-calendar-restored",
      message_id: "message-calendar-restored",
      part_id: "part-calendar-restored",
    } as const;
    api.getSession.mockResolvedValue({
      id: "thread-alpha",
      title: "Calendar parity",
      messages: [],
      queue: [],
      queue_paused: false,
      events: [
        { ...restored, type: "turn_start", event_id: "calendar-restored-1", state: "running" },
        {
          ...restored,
          type: "tool_started",
          event_id: "calendar-restored-2",
          id: "calendar-restored-call",
          name: "calendar_list",
          arguments: {},
        },
        {
          ...restored,
          type: "tool_finished",
          event_id: "calendar-restored-3",
          id: "calendar-restored-call",
          name: "calendar_list",
          ok: true,
          result: {
            events: [
              {
                id: "restored-event",
                summary: "Restored calendar event",
                start: { dateTime: "2026-08-25T09:00:00-07:00" },
                end: { dateTime: "2026-08-25T09:30:00-07:00" },
              },
            ],
          },
        },
        {
          ...restored,
          type: "turn_end",
          event_id: "calendar-restored-4",
          state: "complete",
          text: "",
        },
      ],
    });

    render(<ChatPage sessionId="thread-alpha" />);
    fireEvent.click(
      await screen.findByRole("button", { name: "Checked calendar · Completed" }),
    );
    expect(screen.getByText("Restored calendar event")).toBeInTheDocument();

    const live = {
      version: 2,
      session_id: "thread-alpha",
      run_id: "run-calendar-live",
      message_id: "message-calendar-live",
      part_id: "part-calendar-live",
    } as const;
    act(() => {
      onChatEvent?.({
        ...live,
        type: "turn_start",
        event_id: "calendar-live-1",
        state: "running",
      });
      onChatEvent?.({
        ...live,
        type: "tool_started",
        event_id: "calendar-live-2",
        id: "calendar-live-call",
        name: "calendar_list",
        arguments: {},
      });
    });
    expect(
      await screen.findByRole("heading", { name: "Loading Calendar events" }),
    ).toBeInTheDocument();

    act(() => {
      onChatEvent?.({
        ...live,
        type: "tool_finished",
        event_id: "calendar-live-3",
        id: "calendar-live-call",
        name: "calendar_list",
        ok: true,
        result: {
          events: [
            {
              id: "live-event",
              summary: "Live calendar event",
              start: {
                dateTime: "2026-08-25T11:00:00",
                timeZone: "America/Los_Angeles",
              },
              end: {
                dateTime: "2026-08-25T11:30:00",
                timeZone: "America/Los_Angeles",
              },
            },
          ],
        },
      });
      onChatEvent?.({
        ...live,
        type: "turn_end",
        event_id: "calendar-live-4",
        state: "complete",
        text: "",
      });
    });
    expect(await screen.findByText("Live calendar event")).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "Loading Calendar events" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /delete/i })).not.toBeInTheDocument();
  });

  it("renders equivalent Drive and Granola evidence from restored and live events", async () => {
    const restored = {
      version: 2,
      session_id: "thread-alpha",
      run_id: "run-evidence-restored",
      message_id: "message-evidence-restored",
      part_id: "part-evidence-restored",
    } as const;
    api.getSession.mockResolvedValue({
      id: "thread-alpha",
      title: "Evidence parity",
      messages: [],
      queue: [],
      queue_paused: false,
      events: [
        { ...restored, type: "turn_start", event_id: "evidence-restored-1", state: "running" },
        {
          ...restored,
          type: "tool_started",
          event_id: "evidence-restored-2",
          id: "evidence-restored-call",
          name: "drive_read",
          arguments: { file_id: "drive-restored" },
        },
        {
          ...restored,
          type: "tool_finished",
          event_id: "evidence-restored-3",
          id: "evidence-restored-call",
          name: "drive_read",
          ok: true,
          result: {
            id: "drive-restored",
            name: "Restored Drive evidence",
            content: "Restored evidence excerpt",
            truncated: false,
          },
        },
        {
          ...restored,
          type: "turn_end",
          event_id: "evidence-restored-4",
          state: "complete",
          text: "",
        },
      ],
    });

    render(<ChatPage sessionId="thread-alpha" />);
    fireEvent.click(
      await screen.findByRole("button", { name: "Read Drive evidence · Completed" }),
    );
    expect(screen.getByText("Restored Drive evidence")).toBeInTheDocument();
    expect(screen.getByText("Restored evidence excerpt")).toBeInTheDocument();

    const live = {
      version: 2,
      session_id: "thread-alpha",
      run_id: "run-evidence-live",
      message_id: "message-evidence-live",
      part_id: "part-evidence-live",
    } as const;
    act(() => {
      onChatEvent?.({
        ...live,
        type: "turn_start",
        event_id: "evidence-live-1",
        state: "running",
      });
      onChatEvent?.({
        ...live,
        type: "tool_started",
        event_id: "evidence-live-2",
        id: "evidence-live-call",
        name: "mcp__granola__list_meetings",
        arguments: {},
      });
    });
    expect(
      await screen.findByRole("heading", { name: "Loading evidence" }),
    ).toBeInTheDocument();

    act(() => {
      onChatEvent?.({
        ...live,
        type: "tool_finished",
        event_id: "evidence-live-3",
        id: "evidence-live-call",
        name: "mcp__granola__list_meetings",
        ok: true,
        result: { ok: true, result: "Live Granola meeting evidence excerpt" },
      });
      onChatEvent?.({
        ...live,
        type: "turn_end",
        event_id: "evidence-live-4",
        state: "complete",
        text: "",
      });
    });
    expect(await screen.findByText("Granola meeting context")).toBeInTheDocument();
    expect(screen.getByText("Live Granola meeting evidence excerpt")).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "Loading evidence" }),
    ).not.toBeInTheDocument();
  });
});
