import { describe, expect, it } from "vitest";

import { convertStructuredMessage } from "../src/chat/messageAdapter";
import { SourcecadoChatStore } from "../src/chat/store";

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

describe("SourcecadoChatStore tool callbacks", () => {
  it("inserts a result into only the addressed message and tool call", () => {
    const tool = (id: string) => ({
      type: "tool" as const,
      id,
      name: "drive_search",
      arguments: { query: id },
      state: "running" as const,
    });
    const store = new SourcecadoChatStore(
      [
        {
          id: "thread-alpha",
          messages: [
            {
              id: "message-tools-1",
              role: "assistant",
              state: "running",
              parts: [tool("call-keep-1"), tool("call-target")],
            },
            {
              id: "message-tools-2",
              role: "assistant",
              state: "running",
              parts: [tool("call-keep-2")],
            },
          ],
        },
      ],
      "thread-alpha",
    );

    store.addToolResult("thread-alpha", {
      messageId: "message-tools-1",
      toolCallId: "call-target",
      toolName: "drive_search",
      result: { documents: 4 },
      isError: false,
    });

    const [first, second] = store.messagesFor("thread-alpha");
    expect(first?.parts).toEqual([
      tool("call-keep-1"),
      {
        ...tool("call-target"),
        state: "complete",
        result: { documents: 4 },
      },
    ]);
    expect(second?.parts).toEqual([tool("call-keep-2")]);
  });

  it("resolves only the tool carrying the addressed approval id", () => {
    const pendingTool = (toolId: string, approvalId: string) => ({
      type: "tool" as const,
      id: toolId,
      name: "gmail_create_draft",
      arguments: { to: `${toolId}@example.com` },
      state: "running" as const,
      approval: {
        id: approvalId,
        state: "pending" as const,
        reason: "Draft creation requires permission.",
      },
    });
    const store = new SourcecadoChatStore(
      [
        {
          id: "thread-alpha",
          messages: [
            {
              id: "message-approvals-1",
              role: "assistant",
              state: "waiting-approval",
              parts: [
                pendingTool("call-keep", "approval-keep"),
                pendingTool("call-target", "approval-target"),
              ],
            },
          ],
        },
      ],
      "thread-alpha",
    );

    store.respondToApproval("thread-alpha", {
      approvalId: "approval-target",
      approved: true,
      reason: "Allowed once by the operator.",
    });

    const parts = store.messagesFor("thread-alpha")[0]?.parts;
    expect(parts?.[0]).toEqual(pendingTool("call-keep", "approval-keep"));
    expect(parts?.[1]).toMatchObject({
      id: "call-target",
      approval: {
        id: "approval-target",
        state: "allowed",
        reason: "Allowed once by the operator.",
      },
    });
    const converted = adapted(store, "thread-alpha")[0]?.content;
    expect(converted?.[0]).toMatchObject({
      toolCallId: "call-keep",
      approval: { id: "approval-keep" },
    });
    const untouchedApproval =
      converted?.[0]?.type === "tool-call" ? converted[0].approval : undefined;
    expect(untouchedApproval).not.toHaveProperty("approved");
    expect(converted?.[1]).toMatchObject({
      toolCallId: "call-target",
      approval: { id: "approval-target", approved: true },
    });
  });
});
