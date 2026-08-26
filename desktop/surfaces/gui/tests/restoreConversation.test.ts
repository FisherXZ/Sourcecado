import { describe, expect, it } from "vitest";

import { restoreConversation } from "../src/chat/restoreConversation";

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

describe("restoreConversation merge fidelity", () => {
  const envelope = {
    version: 2,
    session_id: "thread-alpha",
    run_id: "run-1",
    message_id: "message-answer-1",
    part_id: "part-answer-1",
  } as const;

  it("does not duplicate an assistant message whose text differs only by whitespace", () => {
    const restored = restoreConversation({
      id: "thread-alpha",
      title: "Recruiting",
      messages: [
        { role: "user", content: "Find recruiting leads." },
        { role: "assistant", content: "I found three leads." },
      ],
      events: [
        { ...envelope, event_id: "event-1", type: "turn_start", state: "running" },
        {
          ...envelope,
          event_id: "event-2",
          type: "assistant_delta",
          delta: "I found three leads. ",
        },
        {
          ...envelope,
          event_id: "event-3",
          type: "turn_end",
          state: "complete",
          text: "I found three leads. ",
        },
      ],
    });

    expect(restored.map((message) => message.role)).toEqual([
      "user",
      "assistant",
    ]);
    expect(restored[1]?.id).toBe("message-answer-1");
  });

  it("replaces the whole legacy block of a multi-step tool run instead of appending a concatenated copy", () => {
    const restored = restoreConversation({
      id: "thread-alpha",
      title: "Recruiting",
      messages: [
        { role: "user", content: "Find recruiting leads." },
        {
          role: "assistant",
          content: "Let me search.",
          tool_calls: [
            {
              id: "call-1",
              function: { name: "apollo_search", arguments: "{}" },
            },
          ],
        },
        { role: "tool", tool_call_id: "call-1", content: '{"people": 3}' },
        { role: "assistant", content: "Found three leads." },
      ],
      events: [
        { ...envelope, event_id: "event-1", type: "turn_start", state: "running" },
        {
          ...envelope,
          event_id: "event-2",
          type: "assistant_delta",
          delta: "Let me search.",
        },
        {
          ...envelope,
          event_id: "event-3",
          type: "tool_started",
          id: "call-1",
          name: "apollo_search",
          arguments: {},
        },
        {
          ...envelope,
          event_id: "event-4",
          type: "tool_finished",
          id: "call-1",
          name: "apollo_search",
          ok: true,
          result: { people: 3 },
        },
        {
          ...envelope,
          event_id: "event-5",
          type: "assistant_delta",
          delta: "Found three leads.",
        },
        {
          ...envelope,
          event_id: "event-6",
          type: "turn_end",
          state: "complete",
          text: "Found three leads.",
        },
      ],
    });

    expect(restored.map((message) => message.role)).toEqual([
      "user",
      "assistant",
    ]);
    expect(restored[1]?.id).toBe("message-answer-1");
    expect(
      restored[1]?.parts.filter((part) => part.type === "tool"),
    ).toHaveLength(1);
  });

  it("replaces legacy records by persisted message identity even when the text diverges", () => {
    const restored = restoreConversation({
      id: "thread-alpha",
      title: "Recruiting",
      messages: [
        { role: "user", content: "Find recruiting leads." },
        {
          role: "assistant",
          content: "A truncated legacy copy",
          message_id: "message-answer-1",
        },
      ],
      events: [
        { ...envelope, event_id: "event-1", type: "turn_start", state: "running" },
        {
          ...envelope,
          event_id: "event-2",
          type: "assistant_delta",
          delta: "The full projected answer.",
        },
        {
          ...envelope,
          event_id: "event-3",
          type: "turn_end",
          state: "complete",
          text: "The full projected answer.",
        },
      ],
    });

    expect(restored.map((message) => message.role)).toEqual([
      "user",
      "assistant",
    ]);
    expect(restored[1]?.id).toBe("message-answer-1");
    expect(restored[1]?.parts).toEqual([
      {
        type: "text",
        id: "part-answer-1",
        text: "The full projected answer.",
        state: "complete",
      },
    ]);
  });
});
