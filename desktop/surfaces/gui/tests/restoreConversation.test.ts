import { describe, expect, it } from "vitest";

import {
  restoreConversation,
  restoreConversationMessages,
} from "../src/chat/restoreConversation";

describe("restoreConversation", () => {
  it("restores legacy-only histories into the production structured store shape", () => {
    expect(
      restoreConversation({
        id: "thread-production",
        title: "Recruiting",
        messages: [
          { role: "user", content: "Find recruiting leads." },
          { role: "assistant", content: "I found three leads." },
        ],
        events: [],
      }),
    ).toEqual([
      {
        id: "thread-production:legacy:0",
        role: "user",
        state: "complete",
        parts: [
          {
            type: "text",
            id: "thread-production:legacy:0:text:0",
            text: "Find recruiting leads.",
            state: "complete",
          },
        ],
      },
      {
        id: "thread-production:legacy:1",
        role: "assistant",
        state: "complete",
        parts: [
          {
            type: "text",
            id: "thread-production:legacy:1:text:0",
            text: "I found three leads.",
            state: "complete",
          },
        ],
      },
    ]);
  });

  it("replaces a legacy assistant with its stable v2 event projection", () => {
    const envelope = {
      version: 2,
      session_id: "thread-production",
      run_id: "run-1",
      message_id: "message-answer-1",
      part_id: "part-answer-1",
    } as const;

    const restored = restoreConversation({
      id: "thread-production",
      title: "Recruiting",
      messages: [
        { role: "user", content: "Find recruiting leads." },
        { role: "assistant", content: "I found three leads." },
      ],
      events: [
        {
          ...envelope,
          event_id: "event-1",
          type: "turn_start",
          state: "running",
        },
        {
          ...envelope,
          event_id: "event-2",
          type: "assistant_delta",
          delta: "I found three leads.",
        },
        {
          ...envelope,
          event_id: "event-3",
          type: "turn_end",
          state: "complete",
          text: "I found three leads.",
        },
      ],
    });

    expect(restored[0]?.id).toBe("thread-production:legacy:0");
    expect(restored[1]).toEqual({
      id: "message-answer-1",
      role: "assistant",
      state: "complete",
      parts: [
        {
          type: "text",
          id: "part-answer-1",
          text: "I found three leads.",
          state: "complete",
        },
      ],
    });
  });
});

describe("restoreConversationMessages", () => {
  it("uses the legacy adapter unchanged when no presentation event log exists", () => {
    const messages = [
      { role: "user", content: "Find recruiting leads." },
      { role: "assistant", content: "I found three leads." },
    ];
    const before = JSON.stringify(messages);

    const restored = restoreConversationMessages({
      id: "thread-alpha",
      title: "Recruiting",
      messages,
      events: [],
    });

    expect(restored).toEqual([
      {
        id: "thread-alpha:legacy:0",
        role: "user",
        content: [
          {
            type: "text",
            text: "Find recruiting leads.",
            parentId: "thread-alpha:legacy:0:text:0",
          },
        ],
      },
      {
        id: "thread-alpha:legacy:1",
        role: "assistant",
        content: [
          {
            type: "text",
            text: "I found three leads.",
            parentId: "thread-alpha:legacy:1:text:0",
          },
        ],
        status: { type: "complete", reason: "stop" },
      },
    ]);
    expect(JSON.stringify(messages)).toBe(before);
  });

  it("replaces the matching model assistant record with its stable v2 projection", () => {
    const envelope = {
      version: 2,
      session_id: "thread-alpha",
      run_id: "run-1",
      message_id: "message-answer-1",
      part_id: "part-answer-1",
    } as const;

    const restored = restoreConversationMessages({
      id: "thread-alpha",
      title: "Recruiting",
      messages: [
        { role: "user", content: "Find recruiting leads." },
        { role: "assistant", content: "I found three leads." },
      ],
      events: [
        {
          ...envelope,
          event_id: "event-1",
          type: "turn_start",
          state: "running",
        },
        {
          ...envelope,
          event_id: "event-2",
          type: "assistant_delta",
          delta: "I found three ",
        },
        {
          ...envelope,
          event_id: "event-3",
          type: "assistant_delta",
          delta: "leads.",
        },
        {
          ...envelope,
          event_id: "event-4",
          type: "turn_end",
          state: "complete",
          text: "I found three leads.",
        },
      ],
    });

    expect(restored[0]).toMatchObject({
      id: "thread-alpha:legacy:0",
      role: "user",
    });
    expect(restored[1]).toMatchObject({
      id: "message-answer-1",
      role: "assistant",
      content: [
        {
          type: "text",
          text: "I found three leads.",
          parentId: "part-answer-1",
          status: { type: "complete" },
        },
      ],
      status: { type: "complete", reason: "stop" },
    });
  });
});
