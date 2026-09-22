import { describe, expect, it } from "vitest";

import { convertStructuredMessage } from "../src/chat/messageAdapter";
import { SourcecadoChatStore } from "../src/chat/store";
import { restoreConversation } from "../src/chat/restoreConversation";

const adapted = (store: SourcecadoChatStore, threadId: string) =>
  store.messagesFor(threadId).map(convertStructuredMessage);

const textTurnEvents = () => [
  {
    version: 2,
    type: "turn_start",
    session_id: "thread-alpha",
    run_id: "run-1",
    event_id: "event-1",
    message_id: "message-answer-1",
    part_id: "part-answer-1",
    state: "running",
  },
  {
    version: 2,
    type: "assistant_delta",
    session_id: "thread-alpha",
    run_id: "run-1",
    event_id: "event-2",
    message_id: "message-answer-1",
    part_id: "part-answer-1",
    delta: "Three leads",
  },
  {
    version: 2,
    type: "assistant_delta",
    session_id: "thread-alpha",
    run_id: "run-1",
    event_id: "event-3",
    message_id: "message-answer-1",
    part_id: "part-answer-1",
    delta: " match the brief.",
  },
  {
    version: 2,
    type: "turn_end",
    session_id: "thread-alpha",
    run_id: "run-1",
    event_id: "event-4",
    message_id: "message-answer-1",
    part_id: "part-answer-1",
    state: "complete",
    text: "Three leads match the brief.",
  },
] as const;

describe("SourcecadoChatStore v2 text event spine", () => {
  it("uses the same reducer for live application and HTTP replay", () => {
    const live = new SourcecadoChatStore(
      [{ id: "thread-alpha", messages: [] }],
      "thread-alpha",
    );
    for (const event of textTurnEvents()) live.applyChatEvent(event);

    const restored = new SourcecadoChatStore(
      [{ id: "thread-alpha", messages: [] }],
      "thread-alpha",
    );
    restored.replayChatEvents(textTurnEvents());

    expect(adapted(live, "thread-alpha")).toEqual(
      adapted(restored, "thread-alpha"),
    );
    expect(restored.messagesFor("thread-alpha")).toEqual([
      {
        id: "message-answer-1",
        role: "assistant",
        state: "complete",
        parts: [
          {
            type: "text",
            id: "part-answer-1",
            text: "Three leads match the brief.",
            state: "complete",
          },
        ],
      },
    ]);
  });

  it("applies a replayed event_id at most once", () => {
    const store = new SourcecadoChatStore(
      [{ id: "thread-alpha", messages: [] }],
      "thread-alpha",
    );
    const events = textTurnEvents();

    store.replayChatEvents([...events, events[1], events[2]]);

    expect(store.messagesFor("thread-alpha")[0]?.parts[0]).toEqual({
      type: "text",
      id: "part-answer-1",
      text: "Three leads match the brief.",
      state: "complete",
    });
  });

  it.each([
    [
      "failed",
      {
        version: 2,
        type: "error",
        session_id: "thread-alpha",
        run_id: "run-1",
        event_id: "event-terminal",
        message_id: "message-answer-1",
        part_id: "part-answer-1",
        state: "failed",
        message: "Provider failed",
      },
    ],
    [
      "stopped",
      {
        version: 2,
        type: "turn_end",
        session_id: "thread-alpha",
        run_id: "run-1",
        event_id: "event-terminal",
        message_id: "message-answer-1",
        part_id: "part-answer-1",
        state: "stopped",
        text: "Three leads",
      },
    ],
    [
      "interrupted",
      {
        version: 2,
        type: "turn_end",
        session_id: "thread-alpha",
        run_id: "run-1",
        event_id: "event-terminal",
        message_id: "message-answer-1",
        part_id: "part-answer-1",
        state: "interrupted",
        text: "Three leads",
      },
    ],
  ] as const)("restores an explicit %s terminal state", (state, terminal) => {
    const store = new SourcecadoChatStore(
      [{ id: "thread-alpha", messages: [] }],
      "thread-alpha",
    );
    store.replayChatEvents([...textTurnEvents().slice(0, 2), terminal]);

    expect(store.messagesFor("thread-alpha")[0]).toMatchObject({
      id: "message-answer-1",
      state,
      parts: [{ id: "part-answer-1", text: "Three leads", state }],
    });
  });

  it("marks a persisted turn without a terminal event as interrupted", () => {
    const store = new SourcecadoChatStore(
      [{ id: "thread-alpha", messages: [] }],
      "thread-alpha",
    );

    store.replayChatEvents(textTurnEvents().slice(0, 2));

    expect(store.messagesFor("thread-alpha")[0]).toMatchObject({
      id: "message-answer-1",
      state: "interrupted",
      parts: [
        {
          id: "part-answer-1",
          text: "Three leads",
          state: "interrupted",
        },
      ],
    });
  });

  it("projects an unknown-version event as a recoverable notice", () => {
    const store = new SourcecadoChatStore(
      [
        { id: "thread-alpha", messages: [] },
        { id: "thread-beta", messages: [] },
      ],
      "thread-alpha",
    );

    store.applyChatEvent({
      version: 99,
      type: "assistant_delta",
      session_id: "thread-beta",
    });

    expect(store.activeMessages()).toEqual([]);
    expect(store.messagesFor("thread-beta")).toEqual([
      {
        id: "thread-beta:notice:1",
        role: "assistant",
        state: "partial",
        parts: [
          {
            type: "notice",
            id: "thread-beta:notice:1:part",
            code: "unsupported_version",
            message: "Unsupported chat event version 99.",
            recoverable: true,
          },
        ],
      },
    ]);
  });

  it("preserves a recoverable notice already parsed at the API boundary", () => {
    const store = new SourcecadoChatStore(
      [{ id: "thread-alpha", messages: [] }],
      "thread-alpha",
    );

    store.applyChatEvent({
      type: "error",
      message: "Unsupported chat event version 99.",
      notice: { code: "unsupported_version", recoverable: true },
      session_id: "thread-alpha",
    });

    expect(store.messagesFor("thread-alpha")[0]?.parts[0]).toMatchObject({
      type: "notice",
      code: "unsupported_version",
      message: "Unsupported chat event version 99.",
    });
  });

  it("routes a background session event only to its envelope session_id", () => {
    const store = new SourcecadoChatStore(
      [
        { id: "thread-alpha", messages: [] },
        { id: "thread-beta", messages: [] },
      ],
      "thread-alpha",
    );
    const backgroundEvents = textTurnEvents().map((event) => ({
      ...event,
      session_id: "thread-beta",
    }));

    for (const event of backgroundEvents) store.applyChatEvent(event);

    expect(store.activeMessages()).toEqual([]);
    expect(store.messagesFor("thread-beta")[0]).toMatchObject({
      id: "message-answer-1",
      state: "complete",
      parts: [
        {
          id: "part-answer-1",
          text: "Three leads match the brief.",
        },
      ],
    });
  });
});

describe("SourcecadoChatStore live/restored parity", () => {
  it("replays live text deltas into the same snapshot identities as restore", () => {
    const live = new SourcecadoChatStore(
      [{ id: "thread-alpha", messages: [] }],
      "thread-alpha",
    );
    live.apply({
      type: "message_started",
      threadId: "thread-alpha",
      messageId: "message-answer-1",
      role: "assistant",
    });
    live.apply({
      type: "text_delta",
      threadId: "thread-alpha",
      messageId: "message-answer-1",
      partId: "part-answer-1",
      delta: "Three leads",
    });
    live.apply({
      type: "text_delta",
      threadId: "thread-alpha",
      messageId: "message-answer-1",
      partId: "part-answer-1",
      delta: " match the brief.",
    });
    live.apply({
      type: "message_finished",
      threadId: "thread-alpha",
      messageId: "message-answer-1",
      state: "complete",
    });

    const restored = new SourcecadoChatStore(
      [
        {
          id: "thread-alpha",
          messages: [
            {
              id: "message-answer-1",
              role: "assistant",
              state: "complete",
              parts: [
                {
                  type: "text",
                  id: "part-answer-1",
                  text: "Three leads match the brief.",
                  state: "complete",
                },
              ],
            },
          ],
        },
      ],
      "thread-alpha",
    );

    expect(adapted(live, "thread-alpha")).toEqual(
      adapted(restored, "thread-alpha"),
    );
  });

  it("replays a live tool result into the same part identity as restore", () => {
    const live = new SourcecadoChatStore(
      [{ id: "thread-alpha", messages: [] }],
      "thread-alpha",
    );
    live.apply({
      type: "message_started",
      threadId: "thread-alpha",
      messageId: "message-tools-1",
      role: "assistant",
    });
    live.apply({
      type: "tool_started",
      threadId: "thread-alpha",
      messageId: "message-tools-1",
      toolCallId: "call-drive-1",
      name: "drive_search",
      arguments: { query: "Codeology" },
    });
    live.apply({
      type: "tool_result",
      threadId: "thread-alpha",
      messageId: "message-tools-1",
      toolCallId: "call-drive-1",
      result: { documents: 2 },
      isError: false,
    });
    live.apply({
      type: "message_finished",
      threadId: "thread-alpha",
      messageId: "message-tools-1",
      state: "complete",
    });

    const restored = new SourcecadoChatStore(
      [
        {
          id: "thread-alpha",
          messages: [
            {
              id: "message-tools-1",
              role: "assistant",
              state: "complete",
              parts: [
                {
                  type: "tool",
                  id: "call-drive-1",
                  name: "drive_search",
                  arguments: { query: "Codeology" },
                  state: "complete",
                  result: { documents: 2 },
                },
              ],
            },
          ],
        },
      ],
      "thread-alpha",
    );

    expect(adapted(live, "thread-alpha")).toEqual(
      adapted(restored, "thread-alpha"),
    );
  });

  it("replays a live approval gate into the same pending tool state as restore", () => {
    const live = new SourcecadoChatStore(
      [{ id: "thread-alpha", messages: [] }],
      "thread-alpha",
    );
    live.apply({
      type: "message_started",
      threadId: "thread-alpha",
      messageId: "message-approval-1",
      role: "assistant",
    });
    live.apply({
      type: "tool_started",
      threadId: "thread-alpha",
      messageId: "message-approval-1",
      toolCallId: "call-gmail-1",
      name: "gmail_create_draft",
      arguments: { to: "alyssa@example.com" },
    });
    live.apply({
      type: "approval_required",
      threadId: "thread-alpha",
      messageId: "message-approval-1",
      toolCallId: "call-gmail-1",
      approvalId: "approval-gmail-1",
      reason: "Creating a draft is a write action.",
    });
    live.apply({
      type: "message_state_changed",
      threadId: "thread-alpha",
      messageId: "message-approval-1",
      state: "waiting-approval",
    });

    const restored = new SourcecadoChatStore(
      [
        {
          id: "thread-alpha",
          messages: [
            {
              id: "message-approval-1",
              role: "assistant",
              state: "waiting-approval",
              parts: [
                {
                  type: "tool",
                  id: "call-gmail-1",
                  name: "gmail_create_draft",
                  arguments: { to: "alyssa@example.com" },
                  state: "running",
                  approval: {
                    id: "approval-gmail-1",
                    state: "pending",
                    reason: "Creating a draft is a write action.",
                  },
                },
              ],
            },
          ],
        },
      ],
      "thread-alpha",
    );

    expect(adapted(live, "thread-alpha")).toEqual(
      adapted(restored, "thread-alpha"),
    );
  });

  it("replays a partial multi-tool result without losing successful parts", () => {
    const live = new SourcecadoChatStore(
      [{ id: "thread-alpha", messages: [] }],
      "thread-alpha",
    );
    live.apply({
      type: "message_started",
      threadId: "thread-alpha",
      messageId: "message-partial-1",
      role: "assistant",
    });
    for (const tool of [
      { id: "call-drive-ok", name: "drive_search" },
      { id: "call-notion-failed", name: "notion_search" },
    ]) {
      live.apply({
        type: "tool_started",
        threadId: "thread-alpha",
        messageId: "message-partial-1",
        toolCallId: tool.id,
        name: tool.name,
        arguments: { query: "Codeology" },
      });
    }
    live.apply({
      type: "tool_result",
      threadId: "thread-alpha",
      messageId: "message-partial-1",
      toolCallId: "call-drive-ok",
      result: { documents: 2 },
      isError: false,
    });
    live.apply({
      type: "tool_result",
      threadId: "thread-alpha",
      messageId: "message-partial-1",
      toolCallId: "call-notion-failed",
      result: { error: "connection expired" },
      isError: true,
    });
    live.apply({
      type: "message_state_changed",
      threadId: "thread-alpha",
      messageId: "message-partial-1",
      state: "partial",
    });

    const restored = new SourcecadoChatStore(
      [
        {
          id: "thread-alpha",
          messages: [
            {
              id: "message-partial-1",
              role: "assistant",
              state: "partial",
              parts: [
                {
                  type: "tool",
                  id: "call-drive-ok",
                  name: "drive_search",
                  arguments: { query: "Codeology" },
                  state: "complete",
                  result: { documents: 2 },
                },
                {
                  type: "tool",
                  id: "call-notion-failed",
                  name: "notion_search",
                  arguments: { query: "Codeology" },
                  state: "error",
                  result: { error: "connection expired" },
                },
              ],
            },
          ],
        },
      ],
      "thread-alpha",
    );

    expect(adapted(live, "thread-alpha")).toEqual(
      adapted(restored, "thread-alpha"),
    );
  });

  it("replays cancellation without discarding the interrupted text part", () => {
    const live = new SourcecadoChatStore(
      [{ id: "thread-alpha", messages: [] }],
      "thread-alpha",
    );
    live.apply({
      type: "message_started",
      threadId: "thread-alpha",
      messageId: "message-cancelled-1",
      role: "assistant",
    });
    live.apply({
      type: "text_delta",
      threadId: "thread-alpha",
      messageId: "message-cancelled-1",
      partId: "part-cancelled-1",
      delta: "I found two likely",
    });
    live.apply({
      type: "message_state_changed",
      threadId: "thread-alpha",
      messageId: "message-cancelled-1",
      state: "cancelled",
    });

    const restored = new SourcecadoChatStore(
      [
        {
          id: "thread-alpha",
          messages: [
            {
              id: "message-cancelled-1",
              role: "assistant",
              state: "cancelled",
              parts: [
                {
                  type: "text",
                  id: "part-cancelled-1",
                  text: "I found two likely",
                  state: "cancelled",
                },
              ],
            },
          ],
        },
      ],
      "thread-alpha",
    );

    expect(adapted(live, "thread-alpha")).toEqual(
      adapted(restored, "thread-alpha"),
    );
  });
});

describe("SourcecadoChatStore thread routing", () => {
  it("routes a delta to the selected fixture and never the inactive thread", () => {
    const message = (text: string) => ({
      id: "shared-message-id",
      role: "assistant" as const,
      state: "complete" as const,
      parts: [
        {
          type: "text" as const,
          id: "shared-part-id",
          text,
          state: "complete" as const,
        },
      ],
    });
    const store = new SourcecadoChatStore(
      [
        { id: "thread-alpha", messages: [message("Alpha")] },
        { id: "thread-beta", messages: [message("Beta")] },
      ],
      "thread-alpha",
    );

    store.selectThread("thread-beta");
    store.apply({
      type: "text_delta",
      threadId: "thread-beta",
      messageId: "shared-message-id",
      partId: "shared-part-id",
      delta: " selected",
    });

    expect(store.activeMessages()[0]?.parts[0]).toMatchObject({
      text: "Beta selected",
    });
    expect(store.messagesFor("thread-alpha")[0]?.parts[0]).toMatchObject({
      text: "Alpha",
    });
  });
});

describe("SourcecadoChatStore restore-while-streaming", () => {
  const raceEnvelope = {
    version: 2,
    session_id: "thread-alpha",
    run_id: "run-live",
    message_id: "message-live-1",
    part_id: "part-live-1",
  } as const;
  const liveStart = {
    ...raceEnvelope,
    event_id: "event-live-1",
    type: "turn_start",
    state: "running",
  } as const;
  const liveHello = {
    ...raceEnvelope,
    event_id: "event-live-2",
    type: "assistant_delta",
    delta: "Hello",
  } as const;
  const liveWorld = {
    ...raceEnvelope,
    event_id: "event-live-3",
    type: "assistant_delta",
    delta: " world",
  } as const;

  it("keeps live deltas that arrived while the restore snapshot was in flight", () => {
    const store = new SourcecadoChatStore(
      [{ id: "thread-alpha", messages: [] }],
      "thread-alpha",
    );
    store.applyChatEvent(liveStart);
    store.applyChatEvent(liveHello);
    store.applyChatEvent(liveWorld);

    // The HTTP snapshot was taken before the last delta reached the client.
    store.replaceThread(
      "thread-alpha",
      restoreConversation({
        id: "thread-alpha",
        title: null,
        messages: [{ role: "user", content: "Say hello." }],
        events: [liveStart, liveHello],
      }),
    );

    const messages = store.messagesFor("thread-alpha");
    const assistants = messages.filter((message) => message.role === "assistant");
    expect(assistants).toHaveLength(1);
    expect(assistants[0]?.parts).toEqual([
      {
        type: "text",
        id: "part-live-1",
        text: "Hello world",
        state: "running",
      },
    ]);
    expect(assistants[0]?.state).toBe("running");
  });

  it("completes a frozen running message when the reconnect re-sync replays the missed tail", () => {
    // T1/B1 composed seam: the transport's reconnect re-sync replays the
    // events a run emitted while the socket was down. Pins that the store
    // turns that replay into a finished message with the full text, and that
    // a backend over-replay of an already-delivered delta cannot double it.
    const store = new SourcecadoChatStore(
      [{ id: "thread-alpha", messages: [] }],
      "thread-alpha",
    );
    store.replaceThread("thread-alpha", []);
    store.applyChatEvent(liveStart);
    store.applyChatEvent(liveHello);

    // Socket gap: the run streams on and completes server-side. The re-sync
    // replays the undelivered tail through the same applyChatEvent path.
    store.applyChatEvent(liveWorld);
    store.applyChatEvent({
      ...raceEnvelope,
      event_id: "event-live-4",
      type: "turn_end",
      text: "Hello world",
      state: "complete",
    });
    // The backend may over-replay an already-delivered event; identity wins.
    store.applyChatEvent(liveHello);

    const assistant = store
      .messagesFor("thread-alpha")
      .find((message) => message.role === "assistant");
    expect(assistant?.state).toBe("complete");
    expect(assistant?.parts).toEqual([
      {
        type: "text",
        id: "part-live-1",
        text: "Hello world",
        state: "complete",
      },
    ]);
  });

  it("does not re-apply a snapshot event that is later replayed over the socket", () => {
    const store = new SourcecadoChatStore(
      [{ id: "thread-alpha", messages: [] }],
      "thread-alpha",
    );
    store.applyChatEvent(liveStart);
    store.applyChatEvent(liveHello);
    store.applyChatEvent(liveWorld);
    store.replaceThread(
      "thread-alpha",
      restoreConversation({
        id: "thread-alpha",
        title: null,
        messages: [{ role: "user", content: "Say hello." }],
        events: [liveStart, liveHello],
      }),
    );

    store.applyChatEvent(liveHello);

    const assistant = store
      .messagesFor("thread-alpha")
      .find((message) => message.role === "assistant");
    expect(assistant?.parts[0]).toMatchObject({ text: "Hello world" });
  });

  it("revives an interrupted restore when the backend re-announces the run", () => {
    const store = new SourcecadoChatStore(
      [{ id: "thread-alpha", messages: [] }],
      "thread-alpha",
    );
    store.replaceThread(
      "thread-alpha",
      restoreConversation({
        id: "thread-alpha",
        title: null,
        messages: [{ role: "user", content: "Say hello." }],
        events: [liveStart, liveHello],
      }),
    );
    const before = store
      .messagesFor("thread-alpha")
      .find((message) => message.role === "assistant");
    expect(before?.state).toBe("interrupted");

    const applied = store.applyChatEvent(liveStart);

    expect(applied).toEqual(liveStart);
    const after = store
      .messagesFor("thread-alpha")
      .find((message) => message.role === "assistant");
    expect(after?.state).toBe("running");
    expect(after?.parts[0]).toMatchObject({ text: "Hello", state: "running" });
    expect(
      store
        .messagesFor("thread-alpha")
        .filter((message) => message.role === "assistant"),
    ).toHaveLength(1);
  });
});

describe("SourcecadoChatStore transport events", () => {
  it("passes a connection_change through without adding a transcript message", () => {
    const store = new SourcecadoChatStore(
      [{ id: "thread-alpha", messages: [] }],
      "thread-alpha",
    );
    const change = {
      type: "connection_change",
      status: "reconnecting",
      attempt: 1,
      reason: "The backend connection closed (code 1006). Reconnecting.",
    } as const;

    expect(store.applyChatEvent(change)).toEqual(change);
    expect(store.messagesFor("thread-alpha")).toEqual([]);
  });

  it("surfaces a transport error with its own message, not a malformed-event notice", () => {
    const store = new SourcecadoChatStore(
      [{ id: "thread-alpha", messages: [] }],
      "thread-alpha",
    );

    store.applyChatEvent({
      type: "error",
      message:
        "The backend connection is down and the retry buffer is full; the command was dropped.",
    });

    const [message] = store.messagesFor("thread-alpha");
    expect(message?.parts[0]).toMatchObject({
      type: "notice",
      code: "transport",
      message:
        "The backend connection is down and the retry buffer is full; the command was dropped.",
    });
  });
});

describe("SourcecadoChatStore approval resource", () => {
  it("carries a sanitized gmail_draft resource onto the pending approval", () => {
    const store = new SourcecadoChatStore(
      [{ id: "thread-alpha", messages: [] }],
      "thread-alpha",
    );
    const envelope = {
      version: 2,
      session_id: "thread-alpha",
      run_id: "run-1",
      message_id: "message-1",
      part_id: "call-1",
    } as const;
    store.applyChatEvent({
      ...envelope,
      event_id: "event-1",
      type: "turn_start",
      state: "running",
    });
    store.applyChatEvent({
      ...envelope,
      event_id: "event-2",
      type: "permission_required",
      id: "call-1",
      name: "gmail_send",
      arguments: { draft_id: "draft-1" },
      reason: "Sending requires permission.",
      resource: {
        kind: "gmail_draft",
        draft_id: "draft-1",
        to: "a@example.com",
        subject: "Hello",
        account: "me@example.com",
      },
    });

    const [message] = store.messagesFor("thread-alpha");
    const tool = message?.parts.find((part) => part.type === "tool");
    expect(tool?.type === "tool" ? tool.approval?.resource : undefined).toEqual({
      kind: "gmail_draft",
      draft_id: "draft-1",
      to: "a@example.com",
      subject: "Hello",
      account: "me@example.com",
    });
  });
});

describe("live wire-to-part approval resource path", () => {
  it("delivers a wire permission_required resource to the rendered tool-call part", () => {
    const store = new SourcecadoChatStore(
      [{ id: "thread-alpha", messages: [] }],
      "thread-alpha",
    );
    const envelope = {
      version: 2,
      session_id: "thread-alpha",
      run_id: "run-1",
      message_id: "message-1",
      part_id: "call-1",
    } as const;
    store.applyChatEvent({
      ...envelope,
      event_id: "event-1",
      type: "turn_start",
      state: "running",
    });
    // Wire-shaped payload, including keys the UI must never see.
    store.applyChatEvent(
      JSON.parse(
        JSON.stringify({
          ...envelope,
          event_id: "event-2",
          type: "permission_required",
          id: "call-1",
          name: "gmail_send",
          arguments: { draft_id: "draft-1" },
          reason: "Sending requires permission.",
          resource: {
            kind: "gmail_draft",
            draft_id: "draft-1",
            to: "a@example.com",
            subject: "Quarterly intro",
            account: "me@example.com",
            body: "SECRET BODY",
          },
        }),
      ),
    );

    const [rendered] = adapted(store, "thread-alpha");
    const toolPart = (
      rendered?.content as ReadonlyArray<{
        type: string;
        providerMetadata?: { sourcecado?: Record<string, unknown> };
      }>
    ).find((part) => part.type === "tool-call");
    expect(toolPart?.providerMetadata?.sourcecado?.resource).toEqual({
      kind: "gmail_draft",
      draft_id: "draft-1",
      to: "a@example.com",
      subject: "Quarterly intro",
      account: "me@example.com",
    });
  });
});

describe("issue #136 - a failed turn must say why", () => {
  const failureEvent = (overrides: Record<string, unknown> = {}) => ({
    version: 2,
    type: "error",
    session_id: "thread-alpha",
    run_id: "run-1",
    event_id: "event-terminal",
    message_id: "message-answer-1",
    part_id: "part-answer-1",
    state: "failed",
    error_kind: "rate_limit",
    message: "The model provider remained rate limited after bounded retries.",
    ...overrides,
  });

  it("surfaces the failure text instead of discarding it", () => {
    const store = new SourcecadoChatStore(
      [{ id: "thread-alpha", messages: [] }],
      "thread-alpha",
    );

    store.replayChatEvents([...textTurnEvents().slice(0, 2), failureEvent()]);

    const notices = store
      .messagesFor("thread-alpha")
      .flatMap((message) => message.parts)
      .filter((part) => part.type === "notice");
    expect(notices).toHaveLength(1);
    expect(notices[0]).toMatchObject({
      type: "notice",
      code: "rate_limit",
      message: "The model provider remained rate limited after bounded retries.",
      // A terminal provider failure is not something the client can recover.
      recoverable: false,
    });
  });

  it("renders that text so the operator can actually read it", () => {
    const store = new SourcecadoChatStore(
      [{ id: "thread-alpha", messages: [] }],
      "thread-alpha",
    );

    store.replayChatEvents([...textTurnEvents().slice(0, 2), failureEvent()]);

    const rendered = JSON.stringify(adapted(store, "thread-alpha"));
    expect(rendered).toContain("rate limited after bounded retries");
  });

  it("falls back to a generic code when the backend sends no kind", () => {
    const store = new SourcecadoChatStore(
      [{ id: "thread-alpha", messages: [] }],
      "thread-alpha",
    );

    store.replayChatEvents([
      ...textTurnEvents().slice(0, 2),
      failureEvent({ error_kind: undefined }),
    ]);

    const notice = store
      .messagesFor("thread-alpha")
      .flatMap((message) => message.parts)
      .find((part) => part.type === "notice");
    expect(notice).toMatchObject({ code: "error", recoverable: false });
  });

  it("collapses consecutive identical provider failures into one actionable card", () => {
    const store = new SourcecadoChatStore(
      [{ id: "thread-alpha", messages: [] }],
      "thread-alpha",
    );
    const repeated = Array.from({ length: 5 }, (_value, index) => {
      const number = index + 1;
      return [
        {
          ...textTurnEvents()[0],
          run_id: `run-${number}`,
          event_id: `event-start-${number}`,
          message_id: `message-answer-${number}`,
          part_id: `part-answer-${number}`,
        },
        failureEvent({
          run_id: `run-${number}`,
          event_id: `event-terminal-${number}`,
          message_id: `message-answer-${number}`,
          part_id: `part-answer-${number}`,
          error_kind: "provider",
          message: "The model provider failed after bounded recovery attempts.",
          failure: {
            code: "provider_runtime_error",
            provider: "openai",
            model: "gpt-4o-mini",
            attempts: 1,
            recovery_count: 1,
            exhausted: true,
          },
        }),
      ];
    }).flat();

    store.replayChatEvents(repeated);

    const notices = store
      .messagesFor("thread-alpha")
      .flatMap((message) => message.parts)
      .filter((part) => part.type === "notice");
    expect(notices).toHaveLength(1);
    expect(notices[0]).toMatchObject({
      code: "provider_runtime_error",
      recoverable: false,
      occurrences: 5,
    });
    expect(notices[0]?.message).toContain("5 consecutive failed turns");
  });

  it("leaves a held turn alone, since held is not a failure", () => {
    const store = new SourcecadoChatStore(
      [{ id: "thread-alpha", messages: [] }],
      "thread-alpha",
    );

    store.replayChatEvents([
      ...textTurnEvents().slice(0, 2),
      failureEvent({
        state: "held",
        code: "outcome_unknown",
        effect_id: "effect-1",
      }),
    ]);

    const held = store.messagesFor("thread-alpha")[0];
    expect(held?.state).toBe("held");
  });
});
